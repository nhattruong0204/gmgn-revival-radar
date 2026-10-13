"""Bounded SQLite reads, each connection owned by the calling request thread."""

import json
import math
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from pydantic import ValidationError

from revival_radar.chains import CHAINS
from revival_radar.models.token import Candle, TokenSnapshot
from revival_radar.storage.database import SCHEMA_VERSION
from revival_radar.storage.repository import Repository
from revival_radar.web.config import WebConfig
from revival_radar.web.models import (
    Alert,
    AlertDetail,
    AlertOutcome,
    AlertsPage,
    MarketReport,
    Observation,
    Outcomes,
    PublicSignal,
    PublicToken,
    RadarPage,
    ScorePoint,
    SnapshotPoint,
    TokenDetail,
    TokenHistory,
)

LOOKBACK_SECONDS = 7 * 86400


@contextmanager
def public_reader(config: WebConfig) -> Iterator["PublicQueries"]:
    # mode=ro refuses absent databases and never invokes scanner migrations.
    uri = config.database_path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=config.web_read_timeout_ms / 1000)
    try:
        db.row_factory = sqlite3.Row
        # Keep the same indexed projections in fixture-mode load tests. Opt-in
        # fixtures bypass exclusion through a reserved, never-recorded origin.
        excluded = (
            "'__preview_excluded__'" if config.web_allow_synthetic_data else "'synthetic','test'"
        )
        # Only connection-local read projections change; originals stay intact.
        for table, kind in (
            ("scan_runs", "scan"),
            ("token_snapshots", "snapshot"),
            ("evaluations", "evaluation"),
            ("alerts", "alert"),
        ):
            db.execute(f"""CREATE TEMP VIEW {table} AS SELECT r.* FROM main.{table} r
                WHERE NOT EXISTS(SELECT 1 FROM main.state p
                    WHERE p.key='origin:{kind}:' || r.id
                    AND p.value IN ({excluded}))""")
        db.execute(f"""CREATE TEMP VIEW enrichment_cache AS
            SELECT r.* FROM main.enrichment_cache r
            WHERE NOT EXISTS(SELECT 1 FROM main.state p
                WHERE p.key='origin:cache:' || r.chain || ':' ||
                    r.contract_address || ':' || r.kind
                AND p.value IN ({excluded}))""")
        db.execute("""CREATE TEMP VIEW signal_outcomes AS
            SELECT o.rowid AS rowid,o.* FROM main.signal_outcomes o
            JOIN alerts a ON a.id=o.alert_id""")
        db.execute("PRAGMA query_only=ON")
        db.execute(f"PRAGMA busy_timeout={config.web_read_timeout_ms}")
        deadline = time.monotonic() + config.web_read_timeout_ms / 1000
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("Unsupported public storage schema")
        yield PublicQueries(db, allow_synthetic=config.web_allow_synthetic_data)
    finally:
        db.close()


def finite(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _label(context: Mapping) -> dict:
    preset, revision = context.get("preset"), context.get("revision")
    return {
        "preset": preset if preset in ("Strict", "Balanced", "Broad", "Custom") else None,
        "revision": revision if type(revision) is int and revision >= 0 else None,
    }


def observation(value: Mapping, identity: Mapping, origin: str = "unknown") -> Observation:
    """Sanitize evidence before it reaches response serialization or caching."""
    token = PublicToken.model_validate(value["token"]) if value.get("token") else None
    signal = PublicSignal.model_validate(value["signal"]) if value.get("signal") else None
    context = value.get("configuration") or {}
    fields = {key: value.get(key) for key in ("id", "timestamp", "score", "status", "eligible")}
    fields.update(
        origin=origin,
        origin_confidence="unknown" if origin == "unknown" else "recorded",
        chain=identity["chain"],
        contract_address=identity["contract_address"],
        symbol=token.symbol if token else identity.get("symbol", "?"),
        name=token.name if token else "",
        blockers=value.get("rejection_reasons") or [],
        missing_fields=value.get("missing_fields") or [],
        warnings=value.get("warnings") or [],
        token=token,
        signal=signal,
        **_label(context),
    )
    if token:
        for name in (
            "price",
            "market_cap",
            "liquidity",
            "drawdown_from_ath",
            "volume_5m",
            "volume_1h",
            "tx_5m",
            "holders",
        ):
            fields[name] = getattr(token, name)
    if signal:
        for name in ("setup_score", "trigger_score", "confirmation_score"):
            fields[name] = getattr(signal, name)
    return Observation.model_validate(fields)


def public_field(payload, chain, address, timestamp, score, status, eligible, name):
    """Search/sort use the same trusted evidence as public serialization."""
    detail = Repository._market_detail(
        {
            "chain": chain,
            "contract_address": address,
            "timestamp": timestamp,
            "score": score,
            "status": status,
            "eligible": eligible,
        },
        payload,
    )
    return detail["token"].get(name) if detail else None


class PublicQueries:
    def __init__(self, db: sqlite3.Connection, *, allow_synthetic: bool = False):
        self.db = db
        self.allow_synthetic = allow_synthetic
        self.repo = Repository(db)
        db.create_function("public_field", 8, public_field, deterministic=True)

    def last_scan_at(self, until: float) -> float | None:
        row = self.db.execute(
            "SELECT MAX(finished) FROM scan_runs WHERE finished<=?", (until,)
        ).fetchone()
        return finite(row[0])

    def version(self, until: float, route: str = "") -> tuple:
        origin_version = self.db.execute(
            "SELECT COUNT(*),MAX(updated_at) FROM state "
            "WHERE key GLOB 'origin:*' OR key GLOB 'provenance:audit:*'"
        ).fetchone()
        if route in {"meta", "radar", "reports"}:
            # These routes use only frozen evaluations from completed scans.
            # New snapshots during an unfinished scan do not change their data.
            return tuple(origin_version) + tuple(
                self.db.execute(
                    "SELECT MAX(finished),MAX(id),COUNT(*) FROM scan_runs WHERE finished<=?",
                    (until,),
                ).fetchone()
            )
        # A lower-ID pending alert can finish after a newer alert was sent. Its
        # receipt must invalidate cached lists/outcomes even when MAX(id) is unchanged.
        # Outcome rows can also arrive without a new snapshot or completed scan.
        row = self.db.execute(
            "SELECT (SELECT MAX(id) FROM token_snapshots),"
            "(SELECT MAX(id) FROM alerts WHERE delivery_status='sent'),"
            "(SELECT COUNT(*) FROM alerts WHERE delivery_status='sent'),"
            "(SELECT MAX(rowid) FROM signal_outcomes),"
            "(SELECT COUNT(*) FROM signal_outcomes),"
            "(SELECT MAX(fetched_at) FROM enrichment_cache)"
        ).fetchone()
        return (self.last_scan_at(until), *tuple(origin_version), *tuple(row))

    def _evaluation(self, row: sqlite3.Row) -> Observation:
        payload = self.repo.get_state(f"telegram:evaluation:{row['id']}")
        return observation(
            self.repo._market_observation(row, payload),
            dict(row),
            self.repo.observation_origin("evaluation", row["id"]),
        )

    def _latest_row(self, chain: str, address: str, until: float) -> sqlite3.Row | None:
        return self.db.execute(
            """SELECT e.* FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id
            WHERE e.chain=? AND e.contract_address=? AND e.timestamp>? AND e.timestamp<=?
            AND s.finished IS NOT NULL AND s.finished<=?
            ORDER BY e.timestamp DESC,e.id DESC LIMIT 1""",
            (chain, address, until - LOOKBACK_SECONDS, until, until),
        ).fetchone()

    def radar(
        self,
        until: float,
        *,
        chain: str | None,
        status: str | None,
        search: str,
        min_score: int,
        include_ignore: bool,
        sort: str,
        order: str,
        page: int,
        page_size: int,
        age_hours: int = 24,
    ) -> RadarPage:
        filters, arguments = ["e.score>=?"], [min_score]
        if status:
            filters.append("e.status=?")
            arguments.append(status)
        elif not include_ignore:
            filters.append("e.status!='IGNORE'")
        if chain:
            filters.append("e.chain=?")
            arguments.append(chain)
        if search:
            # LIKE escapes treat user input literally; mint address case remains significant.
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            filters.append(
                "(e.symbol LIKE ? ESCAPE '\\' OR "
                "public_field(st.value,e.chain,e.contract_address,e.timestamp,e.score,"
                "e.status,e.eligible,'name') LIKE ? ESCAPE '\\' OR instr(e.contract_address,?)>0)"
            )
            arguments.extend((f"%{escaped}%", f"%{escaped}%", search))
        cte = """WITH identities AS (
            SELECT DISTINCT chain,contract_address FROM evaluations
            WHERE timestamp>? AND timestamp<=?
        ), latest AS (
            SELECT e.* FROM identities i JOIN evaluations e ON e.id=(
                SELECT candidate.id FROM evaluations candidate
                JOIN scan_runs s ON s.id=candidate.scan_id
                WHERE candidate.chain=i.chain AND candidate.contract_address=i.contract_address
                AND candidate.timestamp>? AND candidate.timestamp<=?
                AND s.finished IS NOT NULL AND s.finished<=?
                ORDER BY candidate.timestamp DESC,candidate.id DESC LIMIT 1
            )
        ) """
        selection = (
            "FROM latest e LEFT JOIN state st ON st.key='telegram:evaluation:' || e.id WHERE "
            + " AND ".join(filters)
        )
        parameters = (
            until - age_hours * 3600,
            until,
            until - age_hours * 3600,
            until,
            until,
            *arguments,
        )
        total = self.db.execute(cte + "SELECT COUNT(*) " + selection, parameters).fetchone()[0]
        if sort in ("score", "timestamp"):
            sorting = f"e.{sort}"
        else:
            # sort is an enum at the HTTP boundary and additionally checked here.
            if sort not in ("market_cap", "liquidity", "volume_5m"):
                raise ValueError("Unsupported sort")
            sorting = (
                "public_field(st.value,e.chain,e.contract_address,e.timestamp,e.score,"
                f"e.status,e.eligible,'{sort}')"
            )
        if order not in ("asc", "desc"):
            raise ValueError("Unsupported order")
        rows = self.db.execute(
            cte
            + "SELECT e.* "
            + selection
            + f" ORDER BY ({sorting}) IS NULL ASC,({sorting}) {order.upper()},"
            "e.timestamp DESC,e.chain,e.contract_address LIMIT ? OFFSET ?",
            (*parameters, page_size, (page - 1) * page_size),
        ).fetchall()
        return RadarPage(
            items=[self._evaluation(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            as_of=until,
            last_scan_at=self.last_scan_at(until),
        )

    def token(self, chain: str, address: str, until: float) -> TokenDetail | None:
        row = self._latest_row(chain, address, until)
        if row is None:
            return None
        latest = self.db.execute(
            """SELECT payload FROM token_snapshots WHERE chain=? AND contract_address=?
            AND timestamp<=? ORDER BY timestamp DESC,id DESC LIMIT 1""",
            (chain, address, until),
        ).fetchone()
        market = None
        if latest:
            try:
                token = TokenSnapshot.model_validate_json(latest["payload"])
                if token.key == (chain, address) and token.timestamp <= until:
                    market = PublicToken.model_validate(token.model_dump(mode="json"))
            except (ValueError, TypeError, ValidationError):
                pass
        return TokenDetail(
            observation=self._evaluation(row),
            latest_market=market,
            as_of=until,
            last_scan_at=self.last_scan_at(until),
            links=CHAINS[chain].links(address),
        )

    def briefs(self, chain: str, address: str, until: float, page: int, page_size: int):
        from revival_radar.models.brief import ResearchBrief
        from revival_radar.web.models import BriefPage

        where = """b.chain=? AND b.contract_address=? AND b.as_of<=? AND b.saved_at<=?
            AND p.published_at<=?"""
        where += (
            " AND b.origin != 'test'"
            if self.allow_synthetic
            else " AND b.origin NOT IN ('synthetic','test')"
        )
        excluded = "'test'" if self.allow_synthetic else "'synthetic','test'"
        where += f""" AND NOT EXISTS(SELECT 1 FROM state visibility
            WHERE visibility.key IN ('origin:brief:scan:' || b.id,
                'origin:brief:evaluation:' || b.id) AND visibility.value IN ({excluded}))"""
        params = (chain, address, until, until, until)
        base = "FROM research_briefs b JOIN research_brief_publications p ON p.brief_id=b.id"
        total = self.db.execute(f"SELECT COUNT(*) {base} WHERE {where}", params).fetchone()[0]
        rows = self.db.execute(
            f"""SELECT b.payload {base} WHERE {where}
            ORDER BY b.saved_at DESC,b.id DESC LIMIT ? OFFSET ?""",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
        return BriefPage(
            items=[ResearchBrief.model_validate_json(row[0]) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            as_of=until,
        )

    def history(
        self,
        chain: str,
        address: str,
        until: float,
        hours: int,
        max_points: int,
    ) -> TokenHistory:
        since = until - hours * 3600
        snapshots = self.db.execute(
            """SELECT id,timestamp,price,market_cap FROM token_snapshots
            WHERE chain=? AND contract_address=? AND timestamp>? AND timestamp<=?
            ORDER BY timestamp DESC,id DESC LIMIT ?""",
            (chain, address, since, until, max_points + 1),
        ).fetchall()
        evaluations = self.db.execute(
            """SELECT e.* FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id
            WHERE e.chain=? AND e.contract_address=? AND e.timestamp>? AND e.timestamp<=?
            AND s.finished IS NOT NULL AND s.finished<=?
            ORDER BY e.timestamp DESC,e.id DESC LIMIT ?""",
            (chain, address, since, until, until, max_points + 1),
        ).fetchall()
        scores = []
        for row in reversed(evaluations[:max_points]):
            item = self._evaluation(row)
            scores.append(
                ScorePoint(
                    **{
                        key: getattr(item, key)
                        for key in (
                            "origin",
                            "timestamp",
                            "score",
                            "setup_score",
                            "trigger_score",
                            "confirmation_score",
                        )
                    }
                )
            )
        entry = self.db.execute(
            """SELECT * FROM enrichment_cache WHERE chain=? AND contract_address=?
            AND kind LIKE 'kline:1h:%' AND fetched_at<=?
            ORDER BY fetched_at DESC,kind DESC LIMIT 1""",
            (chain, address, until),
        ).fetchone()
        bars, fetched_at, expires_at = [], None, None
        if entry:
            try:
                payload = json.loads(entry["payload"])
                raw_bars = payload["candles"]
                if not isinstance(raw_bars, list) or len(raw_bars) > 1000:
                    raise ValueError("Invalid candle cache")
                validated = [Candle.model_validate(bar) for bar in raw_bars]
                bars = sorted(
                    (
                        bar
                        for bar in validated
                        if since < bar.timestamp and bar.timestamp + 3600 <= until
                    ),
                    key=lambda bar: bar.timestamp,
                )
                fetched_at, expires_at = finite(entry["fetched_at"]), finite(entry["expires_at"])
            except (ValueError, KeyError, TypeError, RecursionError, ValidationError):
                pass
        cache_origin = (
            self.repo.get_state(f"origin:cache:{chain}:{address}:{entry['kind']}")
            if entry
            else None
        )
        return TokenHistory(
            candles_origin=cache_origin
            if cache_origin in {"live", "synthetic", "test"}
            else "unknown",
            snapshots=[
                SnapshotPoint(
                    origin=self.repo.observation_origin("snapshot", row["id"]),
                    timestamp=row["timestamp"],
                    price=finite(row["price"]),
                    market_cap=finite(row["market_cap"]),
                )
                for row in reversed(snapshots[:max_points])
            ],
            scores=scores,
            candles=bars[-max_points:],
            candles_fetched_at=fetched_at,
            candles_expires_at=expires_at,
            as_of=until,
            since=since,
            truncated=(
                len(snapshots) > max_points
                or len(evaluations) > max_points
                or len(bars) > max_points
            ),
        )

    def _alert(self, row: sqlite3.Row) -> Alert:
        base = dict(row)
        # Validate saved alert-time payload against the immutable alert identity/score.
        payload = self.repo.get_state(f"telegram:alert:{row['id']}")
        status = row["stage"] or "UNKNOWN"
        evidence = self.repo._market_detail(
            {**base, "status": status, "eligible": True},
            payload,
        )
        public = observation(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "score": row["score"],
                "status": status,
                "eligible": True if evidence else None,
                "warnings": [] if evidence else ["Saved alert evidence unavailable"],
                **(evidence or {}),
            },
            {**base, "symbol": "?"},
            self.repo.observation_origin("alert", row["id"]),
        )
        if not evidence:
            # alert_signals is trustworthy alert-time data even when presentation is absent.
            public = public.model_copy(
                update={
                    **_label(base),
                    "price": finite(row["price"]),
                    "market_cap": finite(row["market_cap"]),
                    **{
                        key: row[key]
                        for key in (
                            "setup_score",
                            "trigger_score",
                            "confirmation_score",
                        )
                    },
                }
            )
        return Alert(
            id=row["id"],
            timestamp=row["timestamp"],
            chain=row["chain"],
            contract_address=row["contract_address"],
            symbol=public.symbol,
            score=row["score"],
            preset=public.preset,
            revision=public.revision,
            observation=public,
        )

    @staticmethod
    def _alerts_base() -> str:
        return """SELECT a.*,sg.price,sg.market_cap,sg.setup_score,sg.trigger_score,
            sg.confirmation_score,sg.stage,sg.preset,sg.revision FROM alerts a
            LEFT JOIN alert_signals sg ON sg.alert_id=a.id """

    def alerts(
        self,
        until: float,
        chain: str | None,
        page: int,
        page_size: int,
    ) -> AlertsPage:
        where = "WHERE a.delivery_status='sent' AND a.timestamp<=?"
        parameters = [until]
        if chain:
            where += " AND a.chain=?"
            parameters.append(chain)
        count = self.db.execute(
            "SELECT COUNT(*) FROM alerts a " + where,
            parameters,
        ).fetchone()[0]
        rows = self.db.execute(
            self._alerts_base() + where + " ORDER BY a.timestamp DESC,a.id DESC LIMIT ? OFFSET ?",
            (*parameters, page_size, (page - 1) * page_size),
        ).fetchall()
        return AlertsPage(
            items=[self._alert(row) for row in rows],
            total=count,
            page=page,
            page_size=page_size,
            as_of=until,
            last_scan_at=self.last_scan_at(until),
        )

    def alert(self, identity: int, until: float) -> AlertDetail | None:
        row = self.db.execute(
            self._alerts_base() + "WHERE a.id=? AND a.delivery_status='sent' AND a.timestamp<=?",
            (identity, until),
        ).fetchone()
        if row is None:
            return None
        saved = self._alert(row)
        current = self._latest_row(row["chain"], row["contract_address"], until)
        checkpoints = self.db.execute(
            """SELECT horizon_hours,due_at,observed_at,price,market_cap,return_pct
            FROM signal_outcomes WHERE alert_id=? AND observed_at<=?
            ORDER BY horizon_hours""",
            (identity, until),
        ).fetchall()
        return AlertDetail(
            **saved.model_dump(),
            latest_observation=self._evaluation(current) if current else None,
            outcomes=[AlertOutcome(**dict(item)) for item in checkpoints],
            as_of=until,
        )

    def report(self, until: float, hours: int) -> MarketReport:
        report = self.repo.market_report(until - hours * 3600, until, timezone="Asia/Ho_Chi_Minh")
        entries = []
        for entry in report["entries"]:
            entries.append(
                {
                    **{
                        name: entry[name]
                        for name in (
                            "chain",
                            "contract_address",
                            "symbol",
                            "observations",
                        )
                    },
                    **{
                        name: observation(
                            entry[name],
                            entry,
                            self.repo.observation_origin("evaluation", entry[name]["id"]),
                        )
                        for name in ("peak", "latest")
                    },
                }
            )
        return MarketReport.model_validate({**report, "entries": entries})

    def outcomes(self, until: float, days: int) -> Outcomes:
        since = until - days * 86400
        return Outcomes(
            since=since,
            until=until,
            horizons=self.repo.outcome_summary(since, until),
        )

"""Bounded SQLite reads, each connection owned by the calling request thread."""

import json
import math
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Timer

from pydantic import ValidationError

from revival_radar.chains import CHAINS
from revival_radar.models.token import Candle, TokenSnapshot
from revival_radar.storage.database import SCHEMA_VERSION
from revival_radar.storage.repository import Repository
from revival_radar.web.config import WebConfig
from revival_radar.web.models import (
    Alert,
    AlertDetail,
    AlertGroup,
    AlertOutcome,
    AlertsPage,
    CallPerformance,
    MarketReport,
    Observation,
    Outcomes,
    PublicSignal,
    PublicToken,
    RadarPage,
    RegimeContext,
    ScorePoint,
    SnapshotPoint,
    TokenDetail,
    TokenHistory,
)

LOOKBACK_SECONDS = 7 * 86400
FRESH_SECONDS = 30 * 60


@contextmanager
def public_reader(config: WebConfig) -> Iterator["PublicQueries"]:
    # mode=ro refuses absent databases and never invokes scanner migrations.
    uri = config.database_path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=config.web_read_timeout_ms / 1000)
    interruption = None
    try:
        db.row_factory = sqlite3.Row
        # Connection-local page-cache targets avoid repeated spill/read churn on
        # week-long saved cohorts. Keep temporary storage file-backed; neither
        # these settings nor the read-only projections change persisted data.
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp.cache_size=-8192")
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
        # Native interrupt is thread-safe and avoids Python callbacks throughout
        # SQL execution. Keep the same elapsed deadline and check it again on
        # exit for model work/cache waits without an active SQL statement.
        interruption = Timer(max(0, deadline - time.monotonic()), db.interrupt)
        interruption.daemon = True
        interruption.start()
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("Unsupported public storage schema")
        yield PublicQueries(db, allow_synthetic=config.web_allow_synthetic_data, deadline=deadline)
        if time.monotonic() > deadline:
            raise sqlite3.OperationalError("Saved read deadline exceeded")
    finally:
        if interruption is not None:
            interruption.cancel()
            interruption.join()
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


def _alert_warnings(evidence: Mapping) -> list[str]:
    """Union of warnings saved with an alert presentation, first-seen order, no duplicates."""
    groups = (
        evidence.get("warnings"),
        (evidence.get("signal") or {}).get("warnings"),
        (evidence.get("token") or {}).get("data_warnings"),
    )
    seen: dict[str, None] = {}
    for group in groups:
        if isinstance(group, list):
            seen.update((w, None) for w in group if isinstance(w, str))
    return list(seen)


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
    def __init__(
        self,
        db: sqlite3.Connection,
        *,
        allow_synthetic: bool = False,
        deadline: float | None = None,
    ):
        self.db = db
        self.deadline = deadline
        self.allow_synthetic = allow_synthetic
        self.repo = Repository(db)
        db.create_function("public_field", 8, public_field, deterministic=True)

    def check_deadline(self):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise sqlite3.OperationalError("Saved read deadline exceeded")

    def last_scan_at(self, until: float) -> float | None:
        row = self.db.execute(
            "SELECT MAX(finished) FROM scan_runs WHERE finished<=?", (until,)
        ).fetchone()
        return finite(row[0])

    def version(self, until: float, route: str = "") -> tuple:
        if route in {"track-record", "outcomes"}:
            # Outcome evidence changes independently of scanner snapshots/briefs.
            # Current operator attestations still invalidate quarantine immediately.
            origin = self.db.execute("""SELECT COUNT(*),MAX(updated_at) FROM state
                WHERE key GLOB 'origin:alert:*' OR key GLOB 'provenance:audit:*'""").fetchone()
            data = self.db.execute("""SELECT
                (SELECT MAX(id) FROM alerts WHERE delivery_status='sent'),
                (SELECT COUNT(*) FROM alerts WHERE delivery_status='sent'),
                (SELECT MAX(rowid) FROM signal_outcomes),(SELECT COUNT(*) FROM signal_outcomes),
                (SELECT MAX(saved_at) FROM research_alert_facts),
                (SELECT COUNT(*) FROM research_alert_facts),
                (SELECT MAX(sent_at) FROM research_delivery_receipts),
                (SELECT COUNT(*) FROM research_delivery_receipts),
                (SELECT MAX(recorded_at) FROM research_checkpoint_facts),
                (SELECT COUNT(*) FROM research_checkpoint_facts)""").fetchone()
            return (*origin, *data)
        if route in {"meta", "radar", "reports"}:
            # These routes use only frozen evaluations from completed scans.
            # Origins for unrelated snapshots/briefs or unfinished evaluations
            # cannot change this view. Operator audit changes still invalidate
            # immediately, including quarantine of previously completed rows.
            origin_version = self.db.execute(
                """SELECT COUNT(*),MAX(updated_at) FROM (
                SELECT p.updated_at FROM state p JOIN main.scan_runs s
                ON s.id=CAST(substr(p.key,13) AS INTEGER)
                WHERE p.key GLOB 'origin:scan:*' AND s.finished<=?
                UNION ALL
                SELECT p.updated_at FROM state p JOIN main.evaluations e
                ON e.id=CAST(substr(p.key,19) AS INTEGER)
                JOIN main.scan_runs s ON s.id=e.scan_id
                WHERE p.key GLOB 'origin:evaluation:*' AND s.finished<=?
                UNION ALL
                SELECT updated_at FROM state WHERE key GLOB 'provenance:audit:*'
            )""",
                (until, until),
            ).fetchone()
            return tuple(origin_version) + tuple(
                self.db.execute(
                    "SELECT MAX(finished),MAX(id),COUNT(*) FROM scan_runs WHERE finished<=?",
                    (until,),
                ).fetchone()
            )
        origin_version = self.db.execute(
            "SELECT COUNT(*),MAX(updated_at) FROM state "
            "WHERE key GLOB 'origin:*' OR key GLOB 'provenance:audit:*'"
        ).fetchone()
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

    def visible_brief(self, identity, until):
        excluded = "'test'" if self.allow_synthetic else "'synthetic','test'"
        return (
            self.db.execute(
                f"""SELECT 1 FROM research_briefs b JOIN research_brief_publications p
            ON p.brief_id=b.id WHERE b.id=? AND b.as_of<=? AND b.saved_at<=? AND p.published_at<=?
            AND b.origin NOT IN ({excluded}) AND NOT EXISTS(SELECT 1 FROM state s
                WHERE s.key IN ('origin:brief:scan:' || b.id,'origin:brief:evaluation:' || b.id)
                AND s.value IN ({excluded}))""",
                (identity, until, until, until),
            ).fetchone()
            is not None
        )

    def briefs(
        self,
        chain: str,
        address: str,
        until: float,
        page: int,
        page_size: int,
        version_id: str | None = None,
    ):
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
        if version_id:
            where += " AND b.id=?"
            params += (version_id,)
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

    def _performance(self, row: sqlite3.Row, until: float) -> CallPerformance:
        """Compare the immutable call with saved snapshots in (call, until]; read-only."""
        called = row["timestamp"]
        call_mc, call_price = finite(row["market_cap"]), finite(row["price"])
        call_mc = call_mc if call_mc is not None and call_mc > 0 else None
        chain, address = row["chain"], row["contract_address"]
        # Alert-time market-cap source: the latest snapshot at or before the call (indexed
        # LIMIT 1), accepted only if its market cap is the one the alert froze. Legacy
        # payloads carry no source and stay unknown; nothing is inferred or rewritten.
        call_source = None
        if call_mc is not None:
            at_call = self.db.execute(
                """SELECT market_cap,json_extract(payload,'$.market_cap_source')
                FROM token_snapshots WHERE chain=? AND contract_address=? AND timestamp<=?
                ORDER BY timestamp DESC LIMIT 1""",
                (chain, address, called),
            ).fetchone()
            if (
                at_call
                and at_call[1] in ("rank", "circulating_enriched")
                and finite(at_call[0]) is not None
                and abs(at_call[0] - call_mc) <= 1e-9 * call_mc
            ):
                call_source = at_call[1]
        # Finite, positive market caps only; 1e300 excludes +inf without Python callbacks.
        # One indexed range read (chain, contract_address, timestamp) bounded by the cutoff.
        source_filter, source_args = "", ()
        basis, other_basis = "unknown", 0
        if call_source:
            total, same = self.db.execute(
                """SELECT COUNT(*),COALESCE(SUM(json_extract(payload,'$.market_cap_source')=?),0)
                FROM token_snapshots
                WHERE chain=? AND contract_address=? AND timestamp>? AND timestamp<=?
                AND market_cap>0 AND market_cap<1e300""",
                (call_source, chain, address, called, until),
            ).fetchone()
            if same:
                # Prefer like-with-like; the excluded count is reported, never dropped silently.
                basis, other_basis = "same", total - same
                if other_basis:
                    source_filter = " AND json_extract(payload,'$.market_cap_source')=?"
                    source_args = (call_source,)
            elif total:
                basis = "mixed"  # only other-basis or unlabelled snapshots exist to compare
        agg = self.db.execute(
            f"""WITH r AS (SELECT timestamp t,market_cap mc FROM token_snapshots
                WHERE chain=? AND contract_address=? AND timestamp>? AND timestamp<=?
                AND market_cap>0 AND market_cap<1e300{source_filter})
            SELECT COUNT(*),MAX(mc),MIN(mc),MAX(t),
                (SELECT MAX(g) FROM (SELECT t-LAG(t,1,?) OVER (ORDER BY t) g FROM r)) FROM r""",
            (chain, address, called, until, *source_args, called),
        ).fetchone()
        count, high, low, latest_at, gap = agg
        latest_mc = latest_price = None
        if count:
            latest = self.db.execute(
                """SELECT market_cap,price FROM token_snapshots
                WHERE chain=? AND contract_address=? AND timestamp=?""",
                (row["chain"], row["contract_address"], latest_at),
            ).fetchone()
            latest_mc = finite(latest[0])
            price = finite(latest[1])
            latest_price = price if price is not None and price > 0 else None
        position = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(a.timestamp<? OR (a.timestamp=? AND a.id<=?)),0) "
            "FROM alerts a WHERE a.chain=? AND a.contract_address=? AND " + self.alert_visibility(),
            (called, called, row["id"], row["chain"], row["contract_address"], until, until, until),
        ).fetchone()
        change = mfe = mae = None
        reason = None
        if call_mc is None:
            reason = "no_call_mc"
        elif not count:
            reason = "no_later_observation"
        else:
            change = (latest_mc / call_mc - 1) * 100
            mfe = (high / call_mc - 1) * 100
            mae = (low / call_mc - 1) * 100
        return CallPerformance(
            call_market_cap=call_mc,
            call_price=call_price if call_price is not None and call_price > 0 else None,
            latest_market_cap=latest_mc,
            latest_price=latest_price,
            latest_observed_at=latest_at if count else None,
            freshness="none"
            if not count
            else "fresh"
            if until - latest_at <= FRESH_SECONDS
            else "stale",
            change_since_call_pct=change,
            change_reason=reason,
            mfe_pct=mfe,
            mae_pct=mae,
            observations=count,
            max_gap_seconds=gap if count else None,
            elapsed_seconds=max(0.0, until - called),
            token_call_index=position[1] or None,
            token_call_count=position[0] or None,
            basis=basis,
            call_market_cap_source=call_source,
            other_basis_observations=other_basis if basis == "same" else None,
        )

    def _alert(self, row: sqlite3.Row, until: float) -> Alert:
        base = dict(row)
        # Validate saved alert-time payload against the immutable alert identity/score.
        frozen = self.db.execute(
            "SELECT payload,saved_at FROM research_alert_facts WHERE alert_id=?", (row["id"],)
        ).fetchone()
        payload = frozen[0] if frozen else self.repo.get_state(f"telegram:alert:{row['id']}")
        status = row["stage"] or "UNKNOWN"
        if frozen:
            status = json.loads(payload)["signal"]["status"]
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
                **({"warnings": _alert_warnings(evidence)} if evidence else {}),
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
        delivery = self.db.execute(
            "SELECT sent_at FROM research_delivery_receipts WHERE alert_id=?", (row["id"],)
        ).fetchone()
        return Alert(
            recorded_at=frozen[1] if frozen else None,
            confirmed_at=delivery[0] if delivery else None,
            id=row["id"],
            timestamp=row["timestamp"],
            chain=row["chain"],
            contract_address=row["contract_address"],
            symbol=public.symbol,
            score=row["score"],
            preset=public.preset,
            revision=public.revision,
            observation=public,
            performance=self._performance(row, until),
        )

    @staticmethod
    def _alerts_base() -> str:
        return """SELECT a.*,sg.price,sg.market_cap,sg.setup_score,sg.trigger_score,
            sg.confirmation_score,sg.stage,sg.preset,sg.revision FROM alerts a
            LEFT JOIN alert_signals sg ON sg.alert_id=a.id """

    @staticmethod
    def alert_visibility():
        return """a.timestamp<=? AND (
            (EXISTS(SELECT 1 FROM research_delivery_receipts d WHERE d.alert_id=a.id
                AND d.sent_at<=?)
             AND NOT EXISTS(SELECT 1 FROM research_alert_facts f WHERE f.alert_id=a.id
                AND f.saved_at>?))
            OR (a.delivery_status='sent'
                AND NOT EXISTS(SELECT 1 FROM research_delivery_receipts d WHERE d.alert_id=a.id)
                AND NOT EXISTS(SELECT 1 FROM research_alert_facts f WHERE f.alert_id=a.id)))"""

    def alerts(
        self,
        until: float,
        chain: str | None,
        page: int,
        page_size: int,
    ) -> AlertsPage:
        where = "WHERE " + self.alert_visibility()
        parameters = [until, until, until]
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
            items=[self._alert(row, until) for row in rows],
            total=count,
            page=page,
            page_size=page_size,
            as_of=until,
            last_scan_at=self.last_scan_at(until),
        )

    GROUP_CALL_CAP = 25
    GROUP_PAGE_MAX = 20

    def alerts_by_token(
        self,
        until: float,
        chain: str | None,
        page: int,
        page_size: int,
    ) -> AlertsPage:
        """Page over tokens (most recently called first); list every call of each.

        `total` counts tokens. Each token lists at most GROUP_CALL_CAP calls, newest
        first; `truncated` and `call_count` state when older calls are not listed.
        Uses the (chain, contract_address, timestamp) index; bounded by the read deadline.
        """
        where = "WHERE " + self.alert_visibility()
        parameters: list = [until, until, until]
        if chain:
            where += " AND a.chain=?"
            parameters.append(chain)
        total = self.db.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM alerts a "
            + where
            + " GROUP BY a.chain,a.contract_address)",
            parameters,
        ).fetchone()[0]
        tokens = self.db.execute(
            "SELECT a.chain,a.contract_address,COUNT(*) n,MAX(a.timestamp) latest,MAX(a.id) lid "
            "FROM alerts a "
            + where
            + " GROUP BY a.chain,a.contract_address ORDER BY latest DESC,lid DESC LIMIT ? OFFSET ?",
            (*parameters, page_size, (page - 1) * page_size),
        ).fetchall()
        items: list[Alert] = []
        groups: list[AlertGroup] = []
        for token in tokens:
            self.check_deadline()
            rows = self.db.execute(
                self._alerts_base()
                + "WHERE a.chain=? AND a.contract_address=? AND "
                + self.alert_visibility()
                + " ORDER BY a.timestamp DESC,a.id DESC LIMIT ?",
                (
                    token["chain"],
                    token["contract_address"],
                    until,
                    until,
                    until,
                    self.GROUP_CALL_CAP,
                ),
            ).fetchall()
            calls = [self._alert(row, until) for row in rows]
            items.extend(calls)
            groups.append(
                AlertGroup(
                    chain=token["chain"],
                    contract_address=token["contract_address"],
                    symbol=calls[0].symbol if calls else "",
                    call_count=token["n"],
                    latest_call_at=token["latest"],
                    alert_ids=[c.id for c in calls],
                    truncated=token["n"] > len(calls),
                )
            )
        return AlertsPage(
            items=items,
            groups=groups,
            total=total,
            page=page,
            page_size=page_size,
            as_of=until,
            last_scan_at=self.last_scan_at(until),
        )

    def alert(self, identity: int, until: float) -> AlertDetail | None:
        row = self.db.execute(
            self._alerts_base() + "WHERE a.id=? AND " + self.alert_visibility(),
            (identity, until, until, until),
        ).fetchone()
        if row is None:
            return None
        saved = self._alert(row, until)
        current = self._latest_row(row["chain"], row["contract_address"], until)
        from revival_radar.analysis.track_record import checkpoint_rows

        research = [
            item
            for h in (1, 6, 24, 72)
            for item in checkpoint_rows(
                self.repo,
                row["timestamp"],
                until,
                h,
                synthetic=self.allow_synthetic,
                alert_id=identity,
            )
        ]
        original_brief = research[0]["brief_id"] if research else None
        if original_brief and not self.visible_brief(original_brief, until):
            original_brief = None
        return AlertDetail(
            **saved.model_dump(),
            checkpoint_research=research,
            original_brief_id=original_brief,
            latest_observation=self._evaluation(current) if current else None,
            outcomes=[
                AlertOutcome(
                    horizon_hours=item["horizon_hours"],
                    due_at=item["due_at"],
                    observed_at=item["observed_at"],
                    price=item["checkpoint_price"],
                    market_cap=item["checkpoint_market_cap"],
                    return_pct=item["return_pct"],
                )
                for item in research
                if item["observed_at"] is not None
            ],
            as_of=until,
        )

    def regime_context(self, until: float, chain: str = "sol") -> dict | None:
        """Latest completed scan's saved regime context; saved data only."""
        rows = self.db.execute(
            """SELECT s.finished, json_extract(m.value,'$.market_context') AS context
            FROM (SELECT id,finished FROM scan_runs WHERE finished IS NOT NULL
                AND finished<=? ORDER BY finished DESC LIMIT 5) s
            JOIN state m ON m.key='scan:metrics:' || s.id
            ORDER BY s.finished DESC""",
            (until,),
        ).fetchall()
        for row in rows:
            try:
                context = json.loads(row["context"]) if row["context"] else None
                value = context.get(chain) if isinstance(context, dict) else None
                if value:
                    return RegimeContext.model_validate(
                        {**value, "scan_finished": row["finished"], "chain": chain}
                    ).model_dump()
            except (ValueError, TypeError, ValidationError):
                continue
        return None

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
        return MarketReport.model_validate(
            {**report, "entries": entries, "market_context": self.regime_context(until)}
        )

    def outcomes(self, until: float, days: int) -> Outcomes:
        since = until - days * 86400
        return Outcomes(
            since=since,
            until=until,
            horizons=self.repo.outcome_summary(since, until),
        )

    def track_record(self, until, days, horizon, filters, page, page_size):
        from revival_radar.analysis.track_record import accounting, checkpoint_rows
        from revival_radar.web.models import TrackRecord

        rows = checkpoint_rows(
            self.repo, until - days * 86400, until, horizon, synthetic=self.allow_synthetic
        )
        allowed = len(rows)
        count_where = "a.timestamp BETWEEN ? AND ? AND " + self.alert_visibility()
        count_params = (until - days * 86400, until, until, until, until)
        confirmed = self.db.execute(
            "SELECT COUNT(*) FROM main.alerts a WHERE " + count_where, count_params
        ).fetchone()[0]
        excluded_origins = "'test'" if self.allow_synthetic else "'synthetic','test'"
        excluded_fixtures = self.db.execute(
            f"""SELECT COUNT(*) FROM main.alerts a WHERE {count_where}
            AND EXISTS(SELECT 1 FROM state s WHERE s.key='origin:alert:' || a.id
                AND s.value IN ({excluded_origins}))""",
            count_params,
        ).fetchone()[0]
        for key in ("chain", "preset", "revision", "score_version", "origin", "knowledge"):
            wanted = filters.get(key)
            if wanted is not None:
                rows = [
                    r for r in rows if ("unknown" if r[key] is None else str(r[key])) == str(wanted)
                ]
        groups = {}
        for row in rows:
            groups.setdefault(row["cohort_id"], []).append(row)
        cohorts = [
            {
                "id": identity,
                **{
                    key: values[0][key]
                    for key in (
                        "chain",
                        "preset",
                        "revision",
                        "score_version",
                        "origin",
                        "knowledge",
                    )
                },
                "counts": accounting(values),
            }
            for identity, values in sorted(groups.items())
        ]
        if filters.get("cohort"):
            rows = groups.get(filters["cohort"], [])
        if filters.get("checkpoint"):
            rows = [row for row in rows if row["state"] == filters["checkpoint"]]
        if filters.get("bucket") is not None:
            bucket = filters["bucket"]

            def bucket_for(value):
                return (
                    0
                    if value < -50
                    else 1
                    if value < 0
                    else 2
                    if value == 0
                    else 3
                    if value <= 50
                    else 4
                )

            rows = [
                row
                for row in rows
                if row["return_pct"] is not None and bucket_for(row["return_pct"]) == bucket
            ]
        comparable = len({r["cohort_id"] for r in rows}) <= 1
        offset = (page - 1) * page_size
        return TrackRecord(
            since=until - days * 86400,
            as_of=until,
            filters={"days": days, "horizon": horizon, **filters},
            reconciliation={
                "confirmed_or_legacy_sent_before_origin_policy": confirmed,
                "known_fixture_alerts_excluded": excluded_fixtures,
                "allowed_before_cohort_filters": allowed,
                "cohort_filters_excluded": allowed - len(rows),
            },
            definition="One confirmed delivered alert per row, alert timestamps in inclusive "
            "[since, as_of]. First usable saved checkpoint in [due, due+1h], inclusive. "
            "Alert-time strategy/version only; no retrospective peak-ranked reports. "
            "Horizons anchor to the original alert observation, not delivery time.",
            exclusions=[
                "Non-confirmed delivery",
                "Evidence recorded after the cutoff",
                "Known synthetic/test origins under the default saved-data policy",
                "Missing, nonfinite, inconsistent or out-of-window returns from distribution/rates",
            ],
            limitations=[
                "Observed checkpoint price changes are not executed trades or realized P&L.",
                "Fees, slippage, liquidity, execution and quote-currency identity are unknown.",
                "Legacy first-recorded/delivery times are unknown; "
                "legacy replay is an event-time view.",
                "Rates/medians are withheld for pooled incompatible cohorts; "
                "select an exact cohort.",
                "Repeated alerts are not independent trades; "
                "no confidence interval or predictive success is inferred.",
                "Only retained alerts are included; "
                "deleted/missing historic data is not reconstructed.",
            ],
            summary=accounting(rows, comparable=comparable),
            cohorts=cohorts,
            items=rows[offset : offset + page_size],
            total=len(rows),
            page=page,
            page_size=page_size,
        )

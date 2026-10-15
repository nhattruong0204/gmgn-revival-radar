"""Bounded SQLite reads, each connection owned by the calling request thread."""

import json
import math
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Timer

from pydantic import ValidationError

from revival_radar.analysis import call_projection as projection
from revival_radar.analysis.scoring import STAGE_ORDER
from revival_radar.chains import CHAINS
from revival_radar.models.token import Candle, TokenSnapshot
from revival_radar.storage.database import PUBLIC_SCHEMA_VERSIONS
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
        if db.execute("PRAGMA user_version").fetchone()[0] not in PUBLIC_SCHEMA_VERSIONS:
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

    def publishing_policy(self, until: float) -> dict | None:
        """Policy from the latest completed scan's saved configuration, or None if unknown."""
        run = self.db.execute(
            "SELECT id FROM scan_runs WHERE finished IS NOT NULL AND finished<=? "
            "ORDER BY finished DESC, id DESC LIMIT 1",
            (until,),
        ).fetchone()
        saved = self.repo.get_state(f"telegram:scan:{run['id']}") if run else None
        try:
            context = json.loads(saved) if saved else None
            settings = context["settings"]
            threshold = settings["alert_score_threshold"]
        except (ValueError, TypeError, KeyError):
            return None
        # Scans saved before the policy existed had no stage gate or re-alert rule.
        stage = settings.get("alert_min_stage", "WATCH")
        flag = settings.get("realert_requires_new_evidence", False)
        revision = context.get("revision")
        if type(threshold) is not int or stage not in STAGE_ORDER[1:] or type(flag) is not bool:
            return None
        return {
            "min_stage": stage,
            "score_threshold": threshold,
            "realert_requires_new_evidence": flag,
            "revision": revision if type(revision) is int and revision >= 0 else None,
        }

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

    def _performance_on_read(self, chain, address, called, call_mc, until):
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
        return call_source, basis, other_basis, agg

    @property
    def has_projection(self) -> bool:
        """Schema-8 projection present and applicable; schema-7 databases compute on read."""
        if not hasattr(self, "_has_projection"):
            self._has_projection = not self.allow_synthetic and (
                self.db.execute(
                    "SELECT 1 FROM main.sqlite_master "
                    "WHERE type='table' AND name='call_performance'"
                ).fetchone()
                is not None
            )
        return self._has_projection

    def _projected(self, row: sqlite3.Row, until: float, call_mc: float | None):
        """Projection folded up to `until`, or None to compute on read.

        Never serves aggregates that include data after the cutoff (updated_at > until),
        and folds in snapshots newer than updated_at itself, so the result is identical to
        the on-read computation. A changed call market cap or basis transition falls back.
        """
        if not self.has_projection:
            return None
        stored = self.db.execute(
            "SELECT * FROM call_performance WHERE alert_id=?", (row["id"],)
        ).fetchone()
        if stored is None or stored["updated_at"] > until:
            return None
        if stored["call_market_cap"] != call_mc:
            return None
        state = projection.row_to_state(stored, row["timestamp"])
        newer = projection.snapshot_rows(
            self.db,
            row["chain"],
            row["contract_address"],
            max(stored["updated_at"], row["timestamp"]),
            until,
            hide_origins=False,
        )
        return projection.fold(state, newer) if newer else state

    def _performance(self, row: sqlite3.Row, until: float) -> CallPerformance:
        """Compare the immutable call with saved snapshots in (call, until]; read-only."""
        called = row["timestamp"]
        call_mc, call_price = finite(row["market_cap"]), finite(row["price"])
        call_mc = call_mc if call_mc is not None and call_mc > 0 else None
        chain, address = row["chain"], row["contract_address"]
        state = self._projected(row, until, call_mc)
        if state is not None:
            # Scanner-maintained aggregates (same arithmetic as below; see call_projection).
            call_source, basis = state.call_source, state.basis
            other_basis = state.other_basis
            count, high, low, latest_at, gap = (
                state.count,
                state.high,
                state.low,
                state.latest_at,
                state.max_gap,
            )
            latest_mc = finite(state.latest_market_cap)
            latest_price = state.latest_price
        else:
            call_source, basis, other_basis, agg = self._performance_on_read(
                chain, address, called, call_mc, until
            )
            count, high, low, latest_at, gap = agg
            latest_mc = latest_price = None
            if count:
                latest = self.db.execute(
                    """SELECT market_cap,price FROM token_snapshots
                    WHERE chain=? AND contract_address=? AND timestamp=?""",
                    (chain, address, latest_at),
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

    def _alert(
        self, row: sqlite3.Row, until: float, performance: CallPerformance | None = None
    ) -> Alert:
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
            performance=performance or self._performance(row, until),
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
        address: str | None = None,
    ) -> AlertsPage:
        where = "WHERE " + self.alert_visibility()
        parameters = [until, until, until]
        if chain:
            where += " AND a.chain=?"
            parameters.append(chain)
        if chain and address:
            where += " AND a.contract_address=?"
            parameters.append(address)
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
        address: str | None = None,
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
        if chain and address:
            where += " AND a.contract_address=?"
            parameters.append(address)
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

    JOURNAL_MAX_CALLS = 2000
    JOURNAL_SORTS = ("newest", "age", "best", "worst", "mfe", "mae", "score")

    def journal_cohort(self, until, days, filters):
        """Filtered call cohort with performance for every call; saved data only.

        Window, chain, stage, score, revision and age narrow in SQL (indexed alert
        visibility rules shared with /alerts); outcome sign is applied after the one
        `_performance` evaluation per call, never a duplicate of its arithmetic.
        """
        since = until - days * 86400
        base = ["a.timestamp BETWEEN ? AND ?"]
        params = [since, until]
        visibility = self.alert_visibility()
        visible = self.db.execute(
            "SELECT COUNT(*) FROM alerts a WHERE a.timestamp BETWEEN ? AND ? AND " + visibility,
            (since, until, until, until, until),
        ).fetchone()[0]
        stage_sql = (
            "COALESCE(CASE WHEN json_valid(f.payload) THEN "
            "json_extract(f.payload,'$.signal.status') END, sg.stage, 'UNKNOWN')"
        )
        if filters.get("chain"):
            base.append("a.chain=?")
            params.append(filters["chain"])
        if filters.get("stage"):
            base.append(stage_sql + "=?")
            params.append(filters["stage"])
        if filters.get("min_score") is not None:
            base.append("a.score>=?")
            params.append(filters["min_score"])
        if filters.get("max_score") is not None:
            base.append("a.score<=?")
            params.append(filters["max_score"])
        revision = filters.get("revision")
        if revision == "unknown":
            base.append("sg.revision IS NULL")
        elif revision is not None:
            base.append("sg.revision=?")
            params.append(int(revision))
        if filters.get("min_age_hours") is not None:
            base.append("a.timestamp<=?")
            params.append(until - filters["min_age_hours"] * 3600)
        if filters.get("max_age_hours") is not None:
            base.append("a.timestamp>=?")
            params.append(until - filters["max_age_hours"] * 3600)
        rows = self.db.execute(
            f"""SELECT a.*,sg.price,sg.market_cap,sg.setup_score,sg.trigger_score,
            sg.confirmation_score,sg.stage,sg.preset,sg.revision,{stage_sql} AS stage_effective
            FROM alerts a LEFT JOIN alert_signals sg ON sg.alert_id=a.id
            LEFT JOIN research_alert_facts f ON f.alert_id=a.id
            WHERE {" AND ".join(base)} AND {visibility}
            ORDER BY a.timestamp DESC,a.id DESC LIMIT ?""",
            (*params, until, until, until, self.JOURNAL_MAX_CALLS + 1),
        ).fetchall()
        if len(rows) > self.JOURNAL_MAX_CALLS:
            raise sqlite3.DataError("Call cohort exceeds the exact accounting limit; narrow it")
        from revival_radar.analysis.call_journal import sign

        entries = []
        for row in rows:
            self.check_deadline()
            entries.append(
                {
                    "row": row,
                    "perf": self._performance(row, until),
                    "stage": row["stage_effective"],
                    "revision": _label(dict(row))["revision"],
                }
            )
        outcome = filters.get("outcome")
        if outcome:
            entries = [e for e in entries if sign(e["perf"].change_since_call_pct) == outcome]
        return since, visible, entries

    @staticmethod
    def journal_order(entries, sort):
        """Unknown values always sort last; ties fall back to newest."""
        newest = sorted(entries, key=lambda e: (-e["row"]["timestamp"], -e["row"]["id"]))
        metric = {
            "best": lambda e: (
                -e["perf"].change_since_call_pct
                if e["perf"].change_since_call_pct is not None
                else None
            ),
            "worst": lambda e: e["perf"].change_since_call_pct,
            "mfe": lambda e: -e["perf"].mfe_pct if e["perf"].mfe_pct is not None else None,
            "mae": lambda e: e["perf"].mae_pct,
            "score": lambda e: -e["row"]["score"],
        }
        if sort == "newest":
            return newest
        if sort == "age":
            return list(reversed(newest))
        known = [e for e in newest if metric[sort](e) is not None]
        unknown = [e for e in newest if metric[sort](e) is None]
        return sorted(known, key=metric[sort]) + unknown

    def call_journal(self, until, days, filters, sort, page, page_size, *, everything=False):
        from revival_radar.analysis.call_journal import summarize
        from revival_radar.analysis.track_record import accounting, checkpoint_rows
        from revival_radar.web.models import CallJournal

        since, visible, entries = self.journal_cohort(until, days, filters)
        ids = {e["row"]["id"] for e in entries}
        checkpoints = (
            [
                row
                for row in checkpoint_rows(
                    self.repo, since, until, 24, synthetic=self.allow_synthetic
                )
                if row["alert_id"] in ids
            ]
            if ids
            else []
        )
        self.check_deadline()
        ordered = self.journal_order(entries, sort)
        offset = 0 if everything else (page - 1) * page_size
        chosen = ordered if everything else ordered[offset : offset + page_size]
        items = [self._alert(e["row"], until, e["perf"]) for e in chosen]
        return CallJournal(
            since=since,
            as_of=until,
            last_scan_at=self.last_scan_at(until),
            filters={"days": days, **filters},
            sort=sort,
            definition=(
                "One confirmed delivered alert per row, alert timestamps in inclusive "
                "[since, as_of]. Change since call compares the market cap frozen with "
                "the alert with the latest saved snapshot at or before the cutoff; "
                "MFE/MAE are the highest/lowest saved market caps in between. "
                "The 24h price change comes from the first saved checkpoint in its window."
            ),
            limitations=[
                "Saved snapshot observations, not executed trades or realized P&L.",
                "Fees, slippage, liquidity depth and latency are ignored.",
                "Snapshots arrive about every five minutes; MFE/MAE can miss intra-scan moves.",
                "Repeated alerts on one token are separate calls, not independent trades.",
                "Calls from different strategy revisions are described together when mixed; "
                "this is not a comparison or a confidence interval.",
                "Market-cap change and price change are different metrics; see the glossary.",
            ],
            reconciliation={
                "visible_alerts_in_window": visible,
                "excluded_by_filters": visible - len(entries),
                "after_filters": len(entries),
            },
            summary=summarize(entries, accounting(checkpoints)),
            items=items,
            total=len(entries),
            page=page,
            page_size=page_size,
        )

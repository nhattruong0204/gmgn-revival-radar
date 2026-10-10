import json
import sqlite3
import time
from collections import Counter
from math import isfinite
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from revival_radar.analysis.acceleration import fresh_history
from revival_radar.analysis.filters import first_pass
from revival_radar.config import Settings
from revival_radar.metrics import sqlite_timed
from revival_radar.models.signal import RevivalResult, has_returning_activity
from revival_radar.models.token import TokenSnapshot
from revival_radar.storage.database import SNAPSHOT_FIELDS


class Repository:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        # Connection-local and read-only: a report never trusts unvalidated JSON
        # sub-scores for ranking. Re-registering on a reused connection is safe.
        db.create_function(
            "market_evidence_rank", 7, self._market_evidence_rank, deterministic=True
        )

    @sqlite_timed
    def save_snapshot(self, token: TokenSnapshot) -> None:
        data = token.model_dump(mode="json")
        data["discovery_source"] = ",".join(sorted(token.discovery_source))
        names = ",".join(SNAPSHOT_FIELDS) + ",payload"
        values = [data[f] for f in SNAPSHOT_FIELDS] + [token.model_dump_json()]
        placeholders = ",".join("?" for _ in values)
        with self.db:
            self.db.execute(
                f"INSERT OR IGNORE INTO token_snapshots ({names}) VALUES ({placeholders})", values
            )

    @sqlite_timed
    def history(self, token: TokenSnapshot, limit: int = 24) -> list[TokenSnapshot]:
        rows = self.db.execute(
            """SELECT payload FROM token_snapshots
            WHERE chain=? AND contract_address=? AND timestamp<? ORDER BY timestamp DESC LIMIT ?""",
            (*token.key, token.timestamp, limit),
        ).fetchall()
        return [TokenSnapshot.model_validate_json(r["payload"]) for r in rows]

    @sqlite_timed
    def watchlist(self, chain: str, since: float, limit: int) -> list[TokenSnapshot]:
        rows = self.db.execute(
            """SELECT payload FROM (
            SELECT payload, timestamp, ROW_NUMBER() OVER (
                PARTITION BY contract_address ORDER BY timestamp DESC) AS position
            FROM token_snapshots WHERE chain=? AND timestamp>=? AND discovery_source != ''
            ) WHERE position=1 ORDER BY timestamp DESC LIMIT ?""",
            (chain, since, limit),
        )
        return [TokenSnapshot.model_validate_json(r["payload"]) for r in rows]

    @sqlite_timed
    def reserve_alert(
        self,
        token: TokenSnapshot,
        result: RevivalResult,
        config: Settings,
        configuration: dict | None = None,
    ) -> int | None:
        if config.dry_run or not result.eligible or result.score < config.alert_score_threshold:
            return None
        # Serialize cooldown check + reservation across scanner processes.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            recent = self.db.execute(
                """SELECT * FROM alerts WHERE chain=? AND contract_address=?
                AND delivery_status IN ('sent','pending','unknown') AND timestamp>?
                ORDER BY timestamp DESC, id DESC""",
                (*token.key, token.timestamp - config.alert_cooldown_hours * 3600),
            ).fetchall()
            if any(r["delivery_status"] != "sent" for r in recent):
                self.db.commit()
                return None
            if (
                recent
                and result.score < max(r["score"] for r in recent) + config.realert_score_increase
            ):
                self.db.commit()
                return None
            cursor = self.db.execute(
                """INSERT INTO alerts
                (timestamp,chain,contract_address,score,reason) VALUES (?,?,?,?,?)""",
                (token.timestamp, *token.key, result.score, json.dumps(result.reasons)),
            )
            self.db.execute(
                "INSERT INTO state (key,value,updated_at) VALUES (?,?,?)",
                (
                    f"telegram:alert:{cursor.lastrowid}",
                    self._presentation(token, result, configuration),
                    token.timestamp,
                ),
            )
            context = configuration or {}
            self.db.execute(
                "INSERT INTO alert_signals VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    cursor.lastrowid,
                    token.price,
                    token.market_cap,
                    result.score,
                    result.setup_score,
                    result.trigger_score,
                    result.confirmation_score,
                    result.status,
                    context.get("preset"),
                    context.get("revision"),
                ),
            )
            self.db.commit()
            return cursor.lastrowid
        except sqlite3.Error:
            self.db.rollback()
            raise

    @staticmethod
    def _presentation(
        token: TokenSnapshot, result: RevivalResult, configuration: dict | None
    ) -> str:
        # The caller supplies an allowlisted nonsecret configuration snapshot.
        return json.dumps(
            {
                "token": token.model_dump(mode="json"),
                "signal": result.model_dump(mode="json"),
                "configuration": configuration or {},
            }
        )

    def presentation_detail(self, kind: str, identity: int) -> dict | None:
        if kind not in {"a", "e"} or not 0 < identity < 2**63:
            return None
        key = "alert" if kind == "a" else "evaluation"
        value = self.get_state(f"telegram:{key}:{identity}")
        if kind == "a":
            return json.loads(value) if value else None
        row = self.db.execute("SELECT * FROM evaluations WHERE id=?", (identity,)).fetchone()
        if row is None:
            return None
        detail = (
            json.loads(value)
            if self._market_detail(row, value) is not None
            else {"token": None, "signal": None, "configuration": {}}
        )
        detail["evaluation"] = {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "eligible": bool(row["eligible"]),
            **{
                name: self._saved_text_list(row[name])
                for name in ("rejection_reasons", "missing_fields", "warnings")
            },
        }
        return detail

    @sqlite_timed
    def finish_alert(self, alert_id: int, status: str, message_id: int | None = None) -> None:
        if status not in {"sent", "failed", "unknown"}:
            raise ValueError("Invalid alert delivery state")
        with self.db:
            self.db.execute(
                "UPDATE alerts SET delivery_status=?, telegram_message_id=? WHERE id=?",
                (status, message_id, alert_id),
            )

    @sqlite_timed
    def begin_scan(self, started: float, configuration: dict | None = None) -> int:
        with self.db:
            cursor = self.db.execute("INSERT INTO scan_runs (started) VALUES (?)", (started,))
            if configuration:
                self.db.execute(
                    "INSERT INTO state (key,value,updated_at) VALUES (?,?,?)",
                    (f"telegram:scan:{cursor.lastrowid}", json.dumps(configuration), started),
                )
        return cursor.lastrowid

    @sqlite_timed
    def record_evaluation(
        self,
        scan_id: int,
        token: TokenSnapshot,
        result: RevivalResult,
        config: Settings,
        configuration: dict | None = None,
    ) -> None:
        """Retain every scored token, including those capped by the initial filter."""
        gate = first_pass(token, config)
        blockers = list(gate.reasons)
        if not result.structure.available or not result.structure.base_detected:
            blockers.append("no_base")
        if (
            result.structure.available
            and 0 < result.structure.base_duration_hours < config.base_min_hours
        ):
            blockers.append("base_too_short")
        if not result.eligible and any("deferred" in w.lower() for w in result.warnings):
            blockers.append("enrichment_deferred")
        if not has_returning_activity(result):
            blockers.append("no_returning_activity")
        if token.security.dangerous is True:
            blockers.append("security_dangerous")
        if result.score < config.alert_score_threshold:
            blockers.append("score_below_threshold")
        missing = [reason.split(":", 1)[0] for reason in gate.reasons if ": unavailable" in reason]
        for field in ("ath_market_cap", "volume_5m", "tx_5m", "tx_1h"):
            if getattr(token, field) is None:
                missing.append(field)
        for field in (
            "top10_ratio",
            "dev_ratio",
            "sniper_ratio",
            "bundler_ratio",
            "insider_ratio",
            "dangerous",
        ):
            if getattr(token.security, field) is None:
                missing.append(f"security.{field}")
        recent = fresh_history(token, self.history(token, config.history_observations * 3), config)
        if len([entry for entry in recent if entry.volume_5m is not None]) < (
            config.minimum_history_observations
        ):
            missing.append("baseline_history")
        if token.volume_5m is not None and result.acceleration.volume_ratio_5m is None:
            missing.append("volume_5m_baseline_unavailable_or_zero")
        # A failed first pass skips candles; do not misreport that as an API/data failure.
        if gate.passed and not result.structure.available:
            missing.append("candles")
        with self.db:
            self.db.execute(
                """INSERT INTO evaluations (
                    scan_id,timestamp,chain,contract_address,symbol,score,status,eligible,
                    rejection_reasons,missing_fields,warnings,asset_type,discovery_sources
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scan_id,chain,contract_address) DO UPDATE SET
                    timestamp=excluded.timestamp,symbol=excluded.symbol,score=excluded.score,
                    status=excluded.status,eligible=excluded.eligible,
                    rejection_reasons=excluded.rejection_reasons,
                    missing_fields=excluded.missing_fields,warnings=excluded.warnings,
                    asset_type=excluded.asset_type,discovery_sources=excluded.discovery_sources""",
                (
                    scan_id,
                    token.timestamp,
                    *token.key,
                    token.symbol,
                    result.score,
                    result.status,
                    int(result.eligible),
                    json.dumps(list(dict.fromkeys(blockers))),
                    json.dumps(sorted(set(missing))),
                    json.dumps(result.warnings),
                    getattr(token, "asset_type", None) or "unknown",
                    json.dumps(sorted(token.discovery_source)),
                ),
            )
            identity = self.db.execute(
                "SELECT id FROM evaluations WHERE scan_id=? AND chain=? AND contract_address=?",
                (scan_id, *token.key),
            ).fetchone()["id"]
            self.db.execute(
                """INSERT INTO state (key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,updated_at=excluded.updated_at""",
                (
                    f"telegram:evaluation:{identity}",
                    self._presentation(token, result, configuration),
                    token.timestamp,
                ),
            )
            self.db.execute(
                """UPDATE scan_runs SET processed=(SELECT COUNT(*) FROM evaluations WHERE scan_id=?)
                WHERE id=? AND finished IS NULL""",
                (scan_id, scan_id),
            )

    @sqlite_timed
    def finish_scan(self, scan_id: int, report: object, finished: float) -> None:
        metrics = [
            int(getattr(report, name, 0))
            for name in ("processed", "errors", "sent", "potential_alerts", "sources_ok")
        ]
        counts = [
            json.dumps(getattr(report, name, {}) or {}, sort_keys=True)
            for name in ("discovered_by_chain", "discovered_by_source", "source_errors")
        ]
        keys = json.dumps(sorted(getattr(report, "discovered_keys", ()) or ()))
        with self.db:
            self.db.execute(
                """UPDATE scan_runs SET finished=?, duration_seconds=MAX(0,?-started),
                processed=?,errors=?,sent=?,potential_alerts=?,sources_ok=?,
                discovered_by_chain=?,discovered_by_source=?,source_errors=?,discovered_keys=?
                WHERE id=? AND finished IS NULL""",
                (finished, finished, *metrics, *counts, keys, scan_id),
            )

    def persist_scan_metrics(self, scan_id: int, report: object) -> None:
        """Final metric persistence is excluded from the measured scan's operations."""
        payload = json.dumps({"performance": report.performance, "funnel": report.funnel})
        with self.db:
            if report.finished_at:
                self.db.execute(
                    "UPDATE scan_runs SET finished=?,duration_seconds=? WHERE id=?",
                    (report.finished_at, report.duration_seconds, scan_id),
                )
            self.db.execute(
                "INSERT INTO state (key,value,updated_at) VALUES (?,?,?)",
                (f"scan:metrics:{scan_id}", payload, report.finished_at or time.time()),
            )

    def get_state(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str, timestamp: float | None = None) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO state (key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,updated_at=excluded.updated_at""",
                (key, value, time.time() if timestamp is None else timestamp),
            )

    def claim_daily_summary(self, day_key: str, timestamp: float) -> bool:
        """Claim an attempt durably before sending; a crash cannot cause a duplicate attempt."""
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO state (key,value,updated_at) VALUES (?,?,?)",
                (f"daily_summary:{day_key}", "attempted", timestamp),
            )
        return cursor.rowcount == 1

    def claim_market_report(self, period_key: str, timestamp: float) -> bool:
        """Reserve one scheduled attempt per period across workers and restarts."""
        if not period_key or len(period_key) > 150 or not isfinite(timestamp):
            raise ValueError("Invalid market report period")
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO state (key,value,updated_at) VALUES (?,?,?)",
                (
                    f"market_report:{period_key}",
                    json.dumps({"status": "pending", "claimed_at": timestamp}),
                    timestamp,
                ),
            )
        return cursor.rowcount == 1

    def finish_market_report(
        self,
        period_key: str,
        status: str,
        message_id: int | None = None,
        timestamp: float | None = None,
    ) -> None:
        if status not in {"sent", "failed", "unknown"}:
            raise ValueError("Invalid market report delivery status")
        now = time.time() if timestamp is None else timestamp
        with self.db:
            row = self.db.execute(
                "SELECT value FROM state WHERE key=?", (f"market_report:{period_key}",)
            ).fetchone()
            if row is None:
                raise ValueError("Market report was not reserved")
            delivery = json.loads(row["value"])
            delivery.update(status=status, finished_at=now, message_id=message_id)
            payload = json.dumps(delivery)
            self.db.execute(
                "UPDATE state SET value=?,updated_at=? WHERE key=?",
                (payload, now, f"market_report:{period_key}"),
            )
            self.db.execute(
                """INSERT INTO state (key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,updated_at=excluded.updated_at""",
                ("market_report_delivery", json.dumps({"period": period_key, **delivery}), now),
            )

    @staticmethod
    def _saved_text_list(value: str | None) -> list[str]:
        try:
            values = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return []
        return (
            [item for item in values if isinstance(item, str)] if isinstance(values, list) else []
        )

    @staticmethod
    def _market_detail(row: sqlite3.Row | dict, payload: str | None) -> dict | None:
        """One evidence policy for both SQL tie-breaks and reconstructed details."""
        try:
            detail = json.loads(payload) if payload else None
            if not isinstance(detail, dict):
                raise ValueError("Missing saved details")
            token = TokenSnapshot.model_validate(detail["token"])
            signal = RevivalResult.model_validate(detail["signal"])
            if (
                token.key != (row["chain"], row["contract_address"])
                or token.timestamp != row["timestamp"]
                or signal.score != row["score"]
                or signal.status != row["status"]
                or signal.eligible != bool(row["eligible"])
            ):
                raise ValueError("Saved details disagree with evaluation")
            # Sub-scores are persisted integers. Do not promote coerced strings
            # or booleans into trusted evidence when reading a damaged payload.
            for name in ("setup_score", "confirmation_score", "trigger_score"):
                value = detail["signal"].get(name)
                if value is not None and type(value) is not int:
                    raise ValueError("Invalid saved dimension score")
            configuration = detail.get("configuration")
            context = {}
            if isinstance(configuration, dict):
                # Keep report context nonsecret, including old or malformed saved payloads.
                settings = configuration.get("settings")
                preset, revision = configuration.get("preset"), configuration.get("revision")
                context = {
                    "preset": preset
                    if preset in ("Strict", "Balanced", "Broad", "Custom")
                    else None,
                    "revision": revision if type(revision) is int and revision >= 0 else None,
                    "settings": {},
                }
                if isinstance(settings, dict):
                    gap = settings.get("history_max_gap_seconds")
                    threshold = settings.get("alert_score_threshold")
                    if type(gap) in {int, float} and isfinite(gap) and gap > 0:
                        context["settings"]["history_max_gap_seconds"] = gap
                    if type(threshold) is int and 0 <= threshold <= 100:
                        context["settings"]["alert_score_threshold"] = threshold
            evidence = {
                "token": token.model_dump(mode="json"),
                "signal": signal.model_dump(mode="json"),
                "configuration": context,
            }
            # Older signal models allow nonfinite floats and can coerce strings
            # such as "Infinity". They must not poison a frozen report snapshot.
            json.dumps(evidence, allow_nan=False)
            return evidence
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError, ValidationError):
            return None

    @staticmethod
    def _market_evidence_rank(payload, chain, address, timestamp, score, status, eligible) -> int:
        evidence = Repository._market_detail(
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
        if evidence is None:
            return 0
        signal = evidence["signal"]
        confirmation, trigger = signal["confirmation_score"], signal["trigger_score"]
        return ((confirmation if confirmation is not None else -1) + 1) * 102 + (
            (trigger if trigger is not None else -1) + 1
        )

    @staticmethod
    def _market_observation(row: sqlite3.Row, payload: str | None) -> dict:
        observation = {
            name: row[name] for name in ("id", "timestamp", "score", "status", "eligible")
        }
        observation["eligible"] = bool(observation["eligible"])
        for name in ("rejection_reasons", "missing_fields", "warnings"):
            observation[name] = Repository._saved_text_list(row[name])
        observation.update(token=None, signal=None, configuration={})
        evidence = Repository._market_detail(row, payload)
        if evidence is None:
            observation["warnings"].append("Saved detail evidence unavailable")
        else:
            observation.update(evidence)
            observation["warnings"] = list(
                dict.fromkeys(
                    observation["warnings"]
                    + evidence["token"]["data_warnings"]
                    + evidence["signal"]["warnings"]
                )
            )
        return observation

    def market_report(
        self,
        since: float,
        now: float | None = None,
        timezone: str = "Asia/Bangkok",
        limit: int = 10,
    ) -> dict:
        # Hold one read snapshot across ranking, latest evidence and coverage queries.
        # Reuse a caller's transaction without committing or discarding its writes.
        if self.db.in_transaction:
            return self._market_report(since, now, timezone, limit)
        self.db.execute("BEGIN")
        try:
            return self._market_report(since, now, timezone, limit)
        finally:
            self.db.rollback()

    def _market_report(self, since: float, now: float | None, timezone: str, limit: int) -> dict:
        """Peak and latest saved Solana evaluations in (since, until], regardless of eligibility."""
        until = time.time() if now is None else now
        if (
            not isfinite(since)
            or not isfinite(until)
            or not 0 < until - since <= 7 * 86400
            or type(limit) is not int
            or not 1 <= limit <= 10
        ):
            raise ValueError("Invalid market report window")
        ZoneInfo(timezone)
        # Invalid/missing legacy JSON contributes its saved score, with unknown sub-scores.
        base = """FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id
            LEFT JOIN state st ON st.key='telegram:evaluation:' || e.id
            WHERE e.chain='sol' AND e.timestamp>? AND e.timestamp<=?
            AND s.finished IS NOT NULL AND s.finished<=?"""
        params = (since, until, until)
        valid = "CASE WHEN json_valid(st.value) THEN st.value ELSE '{}' END"
        query = f"""WITH observations AS MATERIALIZED (
            SELECT e.* {base}
        ), maxima AS (
            SELECT contract_address,MAX(score) AS peak_score FROM observations
            GROUP BY contract_address
        ), candidates AS MATERIALIZED (
            SELECT o.*,market_evidence_rank(st.value,o.chain,o.contract_address,o.timestamp,
                o.score,o.status,o.eligible) AS evidence_rank
            FROM observations o JOIN maxima m ON m.contract_address=o.contract_address
                AND m.peak_score=o.score
            LEFT JOIN state st ON st.key='telegram:evaluation:' || o.id
        ), peaks AS (
            SELECT *,ROW_NUMBER() OVER (
                PARTITION BY contract_address ORDER BY evidence_rank DESC,timestamp DESC,id DESC
            ) AS peak_position FROM candidates
        ), latest AS (
            SELECT *,ROW_NUMBER() OVER (
                PARTITION BY contract_address ORDER BY timestamp DESC,id DESC
            ) AS latest_position, COUNT(*) OVER (PARTITION BY contract_address) AS observations
            FROM observations
        ) SELECT p.id AS peak_id,l.id AS latest_id,l.observations
            FROM peaks p JOIN latest l ON l.contract_address=p.contract_address
            WHERE p.peak_position=1 AND l.latest_position=1
            ORDER BY p.score DESC,p.evidence_rank DESC,
                p.timestamp DESC,p.contract_address ASC LIMIT ?"""
        ranked = self.db.execute(query, (*params, limit)).fetchall()
        totals = self.db.execute(
            "SELECT COUNT(*) AS evaluations,COUNT(DISTINCT e.contract_address) AS unique_tokens,"
            "MIN(e.timestamp) AS first_observation,MAX(s.finished) AS last_finished,"
            f"COUNT(DISTINCT COALESCE(json_extract({valid},'$.configuration'),'legacy') || ':' || "
            f"COALESCE(json_extract({valid},'$.signal.score_version'),'legacy')) AS configurations "
            + base,
            params,
        ).fetchone()
        runs = self.db.execute(
            """SELECT SUM(CASE WHEN finished IS NOT NULL AND finished<=? THEN 1 ELSE 0 END)
                AS completed_scans,SUM(CASE WHEN finished IS NULL OR finished>? THEN 1 ELSE 0 END)
                AS incomplete_scans FROM scan_runs WHERE started<=?
                AND (finished>? OR finished IS NULL)""",
            (until, until, until, since),
        ).fetchone()
        entries = []
        for item in ranked:
            evidence = {}
            for name in ("peak", "latest"):
                identity = item[f"{name}_id"]
                row = self.db.execute(
                    "SELECT * FROM evaluations WHERE id=?", (identity,)
                ).fetchone()
                payload = self.get_state(f"telegram:evaluation:{identity}")
                evidence[name] = self._market_observation(row, payload)
            entries.append(
                {
                    "chain": row["chain"],
                    "contract_address": row["contract_address"],
                    "symbol": row["symbol"],
                    "observations": item["observations"],
                    **evidence,
                }
            )
        return {
            "since": since,
            "until": until,
            "timezone": timezone,
            "hours": (until - since) / 3600,
            "chain": "sol",
            "evaluations": totals["evaluations"],
            "unique_tokens": totals["unique_tokens"],
            "completed_scans": runs["completed_scans"] or 0,
            "incomplete_scans": runs["incomplete_scans"] or 0,
            "first_observation": totals["first_observation"],
            "last_finished": totals["last_finished"],
            "multiple_configurations": totals["configurations"] > 1,
            "entries": entries,
        }

    def prune_diagnostics(self, before: float) -> None:
        with self.db:
            for table, kind in (("evaluations", "evaluation"), ("scan_runs", "scan")):
                self.db.execute(
                    f"DELETE FROM state WHERE key IN (SELECT 'telegram:{kind}:' || id FROM {table} "
                    + (
                        "WHERE scan_id IN (SELECT id FROM scan_runs WHERE started<?))"
                        if table == "evaluations"
                        else "WHERE started<?)"
                    ),
                    (before,),
                )
            self.db.execute(
                "DELETE FROM evaluations WHERE scan_id IN "
                "(SELECT id FROM scan_runs WHERE started<?)",
                (before,),
            )
            self.db.execute(
                "DELETE FROM state WHERE key IN "
                "(SELECT 'scan:metrics:' || id FROM scan_runs WHERE started<?)",
                (before,),
            )
            self.db.execute("DELETE FROM scan_runs WHERE started<?", (before,))
            self.db.execute("DELETE FROM enrichment_cache WHERE expires_at<?", (before,))
            self.db.execute("DELETE FROM watch_state WHERE last_seen<?", (before,))
            self.db.execute(
                "DELETE FROM state WHERE key LIKE 'daily_summary:%' AND updated_at<?", (before,)
            )
            self.db.execute(
                "DELETE FROM state WHERE key LIKE 'market_report:%' AND updated_at<?", (before,)
            )

    def health(self, since: float, now: float | None = None) -> dict[str, Any]:
        """Aggregate scans that started in the window; token identity always includes chain."""
        until = time.time() if now is None else now
        runs = self.db.execute(
            "SELECT * FROM scan_runs WHERE started>=? AND started<=? ORDER BY started",
            (since, until),
        ).fetchall()
        completed = [run for run in runs if run["finished"] is not None]
        durations = [run["duration_seconds"] for run in completed]
        scans = {
            "started": len(runs),
            "completed": len(completed),
            "interrupted": len(runs) - len(completed),
            **{
                name: sum(run[name] for run in runs)
                for name in ("processed", "errors", "sent", "potential_alerts", "sources_ok")
            },
            "duration_seconds": {
                "total": sum(durations),
                "min": min(durations, default=0),
                "max": max(durations, default=0),
                "mean": sum(durations) / len(durations) if durations else 0,
            },
            "last_started": runs[-1]["started"] if runs else None,
            "last_finished": max((run["finished"] for run in completed), default=None),
        }
        discovered_by_chain: Counter = Counter()
        sources: dict[str, Counter] = {}
        source_errors: dict[str, Counter] = {}
        discovered_keys: set[tuple[str, str]] = set()
        for run in runs:
            discovered_by_chain.update(json.loads(run["discovered_by_chain"]))
            discovered_keys.update(tuple(key) for key in json.loads(run["discovered_keys"]))
            for field, target in (
                ("discovered_by_source", sources),
                ("source_errors", source_errors),
            ):
                for chain, counts in json.loads(run[field]).items():
                    target.setdefault(chain, Counter()).update(counts)
        discovery = {
            "observations_by_chain": dict(discovered_by_chain),
            "snapshots_by_source": {chain: dict(counts) for chain, counts in sources.items()},
            "source_errors": {chain: dict(counts) for chain, counts in source_errors.items()},
            "unique_tokens": len(discovered_keys),
            "unique_tokens_by_chain": dict(Counter(chain for chain, _ in discovered_keys)),
            "counting_note": "Chain observations deduplicate within each scan; source snapshots "
            "may overlap. Unique tokens deduplicate chain/address across scans. "
            "Watchlist excluded.",
        }
        window = "FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id WHERE s.started>=? "
        window += "AND s.started<=?"
        params = (since, until)
        totals = self.db.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(eligible),0) AS eligible " + window, params
        ).fetchone()
        unique = self.db.execute(
            "SELECT COUNT(*) FROM (SELECT e.chain,e.contract_address "
            + window
            + " GROUP BY e.chain,e.contract_address)",
            params,
        ).fetchone()[0]
        buckets = {"0-39": 0, "40-49": 0, "50-59": 0, "60-74": 0, "75-84": 0, "85-100": 0}
        for row in self.db.execute(
            "SELECT score,COUNT(*) AS n " + window + " GROUP BY score", params
        ):
            name = next(
                label
                for ceiling, label in (
                    (39, "0-39"),
                    (49, "40-49"),
                    (59, "50-59"),
                    (74, "60-74"),
                    (84, "75-84"),
                    (100, "85-100"),
                )
                if row["score"] <= ceiling
            )
            buckets[name] += row["n"]
        counts = {}
        for field in ("rejection_reasons", "missing_fields"):
            counts[field] = {
                row["value"]: row["n"]
                for row in self.db.execute(
                    f"SELECT j.value,COUNT(*) AS n FROM evaluations e "
                    f"JOIN scan_runs s ON s.id=e.scan_id, json_each(e.{field}) j "
                    "WHERE s.started>=? AND s.started<=? GROUP BY j.value ORDER BY n DESC,j.value",
                    params,
                )
            }
        assets = {
            row["asset_type"]: row["n"]
            for row in self.db.execute(
                "SELECT asset_type,COUNT(*) AS n " + window + " GROUP BY asset_type", params
            )
        }
        evaluations = {
            **dict(totals),
            "unique_tokens": unique,
            "score_buckets": buckets,
            "rejection_counts": counts["rejection_reasons"],
            "missing_field_counts": counts["missing_fields"],
            "asset_type_counts": assets,
            "counting_note": "Counts describe evaluations, not unique tokens. Each evaluation "
            "can have multiple rejection reasons and missing fields; counts are not exclusive.",
        }
        coverage = self.db.execute(
            """SELECT COUNT(*) AS known,
                SUM(json_extract(st.value,'$.signal.structure.available')=1) AS candles,
                SUM(json_extract(st.value,'$.token.security.top10_ratio') IS NOT NULL)
                    AS "security.top10_ratio",
                SUM(json_extract(st.value,'$.token.security.dev_ratio') IS NOT NULL)
                    AS "security.dev_ratio",
                SUM(json_extract(st.value,'$.token.security.sniper_ratio') IS NOT NULL)
                    AS "security.sniper_ratio",
                SUM(json_extract(st.value,'$.token.security.bundler_ratio') IS NOT NULL)
                    AS "security.bundler_ratio",
                SUM(json_extract(st.value,'$.token.security.insider_ratio') IS NOT NULL)
                    AS "security.insider_ratio",
                SUM(json_extract(st.value,'$.token.security.dangerous') IS NOT NULL
                    OR json_extract(st.value,'$.token.security.top10_ratio') IS NOT NULL
                    OR json_extract(st.value,'$.token.security.dev_ratio') IS NOT NULL
                    OR json_extract(st.value,'$.token.security.sniper_ratio') IS NOT NULL
                    OR json_extract(st.value,'$.token.security.bundler_ratio') IS NOT NULL
                    OR json_extract(st.value,'$.token.security.insider_ratio') IS NOT NULL
                ) AS security
            FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id
            JOIN state st ON st.key='telegram:evaluation:' || e.id AND json_valid(st.value)
            WHERE s.started>=? AND s.started<=?""",
            params,
        ).fetchone()
        evaluations["coverage"] = {key: value or 0 for key, value in dict(coverage).items()}
        candidates = []
        # Bound the report to ten distinct chain/address pairs, including low-score near misses.
        query = (
            """SELECT * FROM (
            SELECT e.*,ROW_NUMBER() OVER (
                PARTITION BY e.chain,e.contract_address ORDER BY e.score DESC,e.timestamp DESC
            ) AS position """
            + window
        )
        query += " AND e.score>=50) WHERE position=1 ORDER BY score DESC LIMIT 10"
        for row in self.db.execute(query, params):
            item = {
                name: row[name]
                for name in (
                    "id",
                    "timestamp",
                    "chain",
                    "contract_address",
                    "symbol",
                    "score",
                    "status",
                    "asset_type",
                )
            }
            item["eligible"] = bool(row["eligible"])
            for field in ("rejection_reasons", "missing_fields", "warnings"):
                item[field] = json.loads(row[field])
            detail = self.presentation_detail("e", item["id"])
            item["configuration"] = detail.get("configuration", {}) if detail else {}
            signal = (detail.get("signal") or {}) if detail else {}
            for dimension in ("setup_score", "trigger_score", "confirmation_score"):
                item[dimension] = signal.get(dimension)
            candidates.append(item)
        alerts = {"sent": 0, "failed": 0, "pending": 0, "unknown": 0}
        for row in self.db.execute(
            "SELECT delivery_status,COUNT(*) AS n FROM alerts WHERE timestamp>=? AND timestamp<=? "
            "GROUP BY delivery_status",
            params,
        ):
            alerts[row["delivery_status"]] = row["n"]
        alerts["total"] = sum(alerts.values())
        configurations = []
        for run in runs:
            value = self.get_state(f"telegram:scan:{run['id']}")
            context = json.loads(value) if value else {}
            if context not in configurations:
                configurations.append(context)
        latest = dict(runs[-1]) if runs else {}
        if latest:
            value = self.get_state(f"telegram:scan:{latest['id']}")
            latest["configuration"] = json.loads(value) if value else {}
            metrics = self.get_state(f"scan:metrics:{latest['id']}")
            if metrics:
                latest.update(json.loads(metrics))
        return {
            "since": since,
            "until": until,
            "latest_scan": latest,
            "configurations": configurations,
            "scans": scans,
            "discovery": discovery,
            "evaluations": evaluations,
            "alerts": alerts,
            "best_candidates": candidates,
            "outcomes": self.outcome_summary(until - 7 * 86400, until),
        }

    @sqlite_timed
    def cache_entry(self, kind: str, token: TokenSnapshot) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM enrichment_cache WHERE kind=? AND chain=? AND contract_address=?",
            (kind, *token.key),
        ).fetchone()
        return dict(row) if row else None

    @sqlite_timed
    def cache_save(self, kind: str, token: TokenSnapshot, payload: str, expires: float) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO enrichment_cache VALUES (?,?,?,?,?,?)
                ON CONFLICT(kind,chain,contract_address) DO UPDATE SET
                fetched_at=excluded.fetched_at,expires_at=excluded.expires_at,payload=excluded.payload""",
                (kind, *token.key, token.timestamp, expires, payload),
            )

    @sqlite_timed
    def watch_discovered(self, token: TokenSnapshot) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO watch_state(chain,contract_address,first_seen,last_seen,payload)
                VALUES(?,?,?,?,?) ON CONFLICT(chain,contract_address) DO UPDATE SET
                last_seen=MAX(watch_state.last_seen,excluded.last_seen),payload=excluded.payload""",
                (*token.key, token.timestamp, token.timestamp, token.model_dump_json()),
            )

    @sqlite_timed
    def watch_due(self, chain: str, now: float, config: Settings) -> list[dict]:
        since = now - (config.watchlist_expire_hours or config.watchlist_hours) * 3600
        # Seed older installations once, without rewriting any historical snapshot.
        marker = f"watch:seeded:{chain}"
        if self.get_state(marker) is None:
            with self.db:
                self.db.execute(
                    """INSERT OR IGNORE INTO watch_state
                    (chain,contract_address,first_seen,last_seen,last_score,tier,payload)
                    SELECT chain,contract_address,timestamp,timestamp,observed_score,
                        CASE WHEN observed_score>=60 OR ?-timestamp<=? THEN 'high'
                             WHEN observed_score>=40 THEN 'medium' ELSE 'low' END,payload
                    FROM (
                        SELECT s.*,COALESCE((SELECT score FROM evaluations e
                            WHERE e.chain=s.chain AND e.contract_address=s.contract_address
                            ORDER BY e.timestamp DESC,e.id DESC LIMIT 1),0) AS observed_score,
                            ROW_NUMBER() OVER (
                                PARTITION BY contract_address ORDER BY timestamp DESC,id DESC
                            ) AS position FROM token_snapshots s
                        WHERE chain=? AND timestamp>=? AND discovery_source!=''
                    ) WHERE position=1""",
                    (
                        now,
                        config.minimum_history_observations * config.scan_interval_seconds * 2,
                        chain,
                        since,
                    ),
                )
                self.db.execute("INSERT INTO state VALUES(?,?,?)", (marker, "1", now))
        return [
            dict(row)
            for row in self.db.execute(
                """SELECT * FROM watch_state WHERE chain=? AND last_seen>=? AND next_due<=?
            ORDER BY CASE tier WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
            deferred DESC,last_score DESC,last_polled,last_seen DESC,contract_address LIMIT ?""",
                (chain, since, now, config.watchlist_limit),
            )
        ]

    @sqlite_timed
    def watch_polled(self, token: TokenSnapshot, result: RevivalResult, config: Settings) -> None:
        row = self.db.execute(
            "SELECT first_seen FROM watch_state WHERE chain=? AND contract_address=?", token.key
        ).fetchone()
        recent = row is not None and token.timestamp - row[0] <= (
            config.minimum_history_observations * config.scan_interval_seconds * 2
        )
        warming = (
            recent
            and first_pass(token, config).passed
            and (result.acceleration.volume_ratio_5m is None)
        )
        tier = (
            "high" if result.score >= 60 or warming else "medium" if result.score >= 40 else "low"
        )
        interval = (
            config.watchlist_high_score_interval_seconds
            if tier == "high"
            else (config.watchlist_normal_interval_seconds * (3 if tier == "low" else 1))
        )
        if tier != "low":
            interval = min(interval, config.history_max_gap_seconds * 0.75)
        with self.db:
            self.db.execute(
                """UPDATE watch_state SET last_polled=?,last_score=?,next_due=?,
                deferred=0,tier=?,payload=? WHERE chain=? AND contract_address=?""",
                (
                    token.timestamp,
                    result.score,
                    token.timestamp + interval,
                    tier,
                    token.model_dump_json(),
                    *token.key,
                ),
            )

    @sqlite_timed
    def watch_defer(self, token: TokenSnapshot) -> None:
        with self.db:
            self.db.execute(
                "UPDATE watch_state SET deferred=deferred+1,next_due=0 "
                "WHERE chain=? AND contract_address=?",
                token.key,
            )

    @sqlite_timed
    def watch_priority(self, token: TokenSnapshot) -> dict:
        row = self.db.execute(
            "SELECT tier,deferred,last_score,last_polled,last_seen FROM watch_state "
            "WHERE chain=? AND contract_address=?",
            token.key,
        ).fetchone()
        return dict(row) if row else {}

    @sqlite_timed
    def observe_outcomes(self, token: TokenSnapshot) -> int:
        """First usable snapshot within one hour after each horizon, without interpolation."""
        if token.price is None and token.market_cap is None:
            return 0
        rows = self.db.execute(
            """WITH horizons(hours) AS (VALUES(1),(6),(24),(72))
            SELECT a.id, s.price, h.hours, a.timestamp+h.hours*3600 AS due
            FROM alerts a JOIN alert_signals s ON s.alert_id=a.id CROSS JOIN horizons h
            WHERE a.chain=? AND a.contract_address=? AND a.delivery_status='sent'
            AND ? BETWEEN a.timestamp+h.hours*3600 AND a.timestamp+(h.hours+1)*3600""",
            (*token.key, token.timestamp),
        ).fetchall()
        inserted = 0
        with self.db:
            for row in rows:
                initial = row["price"]
                change = (
                    (token.price / initial - 1) * 100
                    if token.price is not None and initial is not None and initial > 0
                    else None
                )
                if change is not None and not isfinite(change):
                    change = None
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO signal_outcomes VALUES (?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        row["hours"],
                        row["due"],
                        token.timestamp,
                        token.price,
                        token.market_cap,
                        change,
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    @sqlite_timed
    def watch_measurement(self, token: TokenSnapshot) -> dict:
        row = self.db.execute(
            "SELECT first_seen,last_seen,last_polled,deferred,tier FROM watch_state "
            "WHERE chain=? AND contract_address=?",
            token.key,
        ).fetchone()
        return dict(row) if row else {}

    @sqlite_timed
    def due_outcome_tokens(self, now: float, chains: list[str], limit: int) -> list[TokenSnapshot]:
        """Poll sent alerts independently of watchlist expiry; share the market budget."""
        if not chains or limit <= 0:
            return []
        placeholders = ",".join("?" for _ in chains)
        rows = self.db.execute(
            """WITH horizons(hours) AS (VALUES(1),(6),(24),(72))
            SELECT a.chain,a.contract_address,MIN(a.timestamp+h.hours*3600) AS due
            FROM alerts a CROSS JOIN horizons h
            LEFT JOIN signal_outcomes o ON o.alert_id=a.id AND o.horizon_hours=h.hours
            WHERE a.delivery_status='sent' AND o.alert_id IS NULL
            AND a.timestamp BETWEEN ? AND ?
            AND ? BETWEEN a.timestamp+h.hours*3600 AND a.timestamp+(h.hours+1)*3600
            AND a.chain IN ("""
            + placeholders
            + """ )
            GROUP BY a.chain,a.contract_address ORDER BY due,a.chain,a.contract_address LIMIT ?""",
            (now - 73 * 3600, now - 3600, now, *chains, limit),
        ).fetchall()
        return [
            TokenSnapshot(
                timestamp=now, chain=row["chain"], contract_address=row["contract_address"]
            )
            for row in rows
        ]

    def outcome_summary(self, since: float, until: float) -> dict:
        result = {}
        for hours in (1, 6, 24, 72):
            rows = self.db.execute(
                """SELECT a.timestamp,o.observed_at,o.return_pct FROM alerts a
                LEFT JOIN signal_outcomes o ON o.alert_id=a.id AND o.horizon_hours=?
                    AND o.observed_at<=?
                WHERE a.delivery_status='sent' AND a.timestamp BETWEEN ? AND ?""",
                (hours, until, since, until),
            ).fetchall()
            values = [row["return_pct"] for row in rows if row["return_pct"] is not None]
            result[str(hours)] = {
                "observed": sum(row["observed_at"] is not None for row in rows),
                "returns_available": len(values),
                "positive": sum(value > 0 for value in values),
                "median_return_pct": median(values) if values else None,
                "pending": sum(
                    row["observed_at"] is None and until <= row["timestamp"] + (hours + 1) * 3600
                    for row in rows
                ),
                "missed": sum(
                    row["observed_at"] is None and until > row["timestamp"] + (hours + 1) * 3600
                    for row in rows
                ),
            }
        return result

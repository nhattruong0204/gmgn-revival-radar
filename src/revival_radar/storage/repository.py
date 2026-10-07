import json
import sqlite3
import time
from collections import Counter
from typing import Any

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
        return json.loads(value) if value else None

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
        if not result.structure.base_detected:
            blockers.append("no_base")
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
        for field in ("top10_ratio", "insider_ratio", "dangerous"):
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
            self.db.execute(
                "UPDATE scan_runs SET finished=?,duration_seconds=? WHERE id=?",
                (report.finished_at, report.duration_seconds, scan_id),
            )
            self.db.execute(
                "INSERT INTO state (key,value,updated_at) VALUES (?,?,?)",
                (f"scan:metrics:{scan_id}", payload, report.finished_at),
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

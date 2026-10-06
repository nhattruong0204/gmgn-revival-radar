import json
import sqlite3

from revival_radar.config import Settings
from revival_radar.models.signal import RevivalResult
from revival_radar.models.token import TokenSnapshot
from revival_radar.storage.database import SNAPSHOT_FIELDS


class Repository:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

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

    def history(self, token: TokenSnapshot, limit: int = 24) -> list[TokenSnapshot]:
        rows = self.db.execute(
            """SELECT payload FROM token_snapshots
            WHERE chain=? AND contract_address=? AND timestamp<? ORDER BY timestamp DESC LIMIT ?""",
            (*token.key, token.timestamp, limit),
        ).fetchall()
        return [TokenSnapshot.model_validate_json(r["payload"]) for r in rows]

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

    def reserve_alert(
        self, token: TokenSnapshot, result: RevivalResult, config: Settings
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
            self.db.commit()
            return cursor.lastrowid
        except sqlite3.Error:
            self.db.rollback()
            raise

    def finish_alert(self, alert_id: int, status: str, message_id: int | None = None) -> None:
        if status not in {"sent", "failed", "unknown"}:
            raise ValueError("Invalid alert delivery state")
        with self.db:
            self.db.execute(
                "UPDATE alerts SET delivery_status=?, telegram_message_id=? WHERE id=?",
                (status, message_id, alert_id),
            )

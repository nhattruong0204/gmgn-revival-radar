import sqlite3
from pathlib import Path

from revival_radar.models.token import TokenSnapshot

# Flat snapshot columns are queryable in SQLite; additional security fields are JSON.
TEXT_FIELDS = {"chain", "contract_address", "symbol", "name", "discovery_source"}
SNAPSHOT_FIELDS = [f for f in TokenSnapshot.model_fields if f not in {"security", "data_warnings"}]


def connect(path: Path, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=busy_timeout_ms / 1000)
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    db.execute("PRAGMA journal_mode=WAL")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > 1:
        db.close()
        raise RuntimeError("Database schema is newer than this application")
    if version == 0:
        fields = ", ".join(
            f'"{f}" {"TEXT" if f in TEXT_FIELDS else "REAL"}' for f in SNAPSHOT_FIELDS
        )
        db.executescript(f"""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS token_snapshots (
                id INTEGER PRIMARY KEY, {fields}, payload TEXT NOT NULL,
                UNIQUE(chain, contract_address, timestamp)
            );
            CREATE INDEX IF NOT EXISTS snapshots_token_time
                ON token_snapshots(chain, contract_address, timestamp DESC);
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, chain TEXT NOT NULL,
                contract_address TEXT NOT NULL, score INTEGER NOT NULL,
                reason TEXT NOT NULL, telegram_message_id INTEGER,
                delivery_status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS alerts_token_time
                ON alerts(chain, contract_address, timestamp DESC);
            PRAGMA user_version=1;
            COMMIT;
        """)
    return db

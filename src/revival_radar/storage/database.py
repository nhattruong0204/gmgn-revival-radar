import sqlite3
from pathlib import Path

from revival_radar.models.token import TokenSnapshot

# Flat snapshot columns are queryable in SQLite; additional security fields are JSON.
TEXT_FIELDS = {
    "chain",
    "contract_address",
    "symbol",
    "name",
    "discovery_source",
    "asset_type",
    "asset_classification_reason",
}
SNAPSHOT_FIELDS = [f for f in TokenSnapshot.model_fields if f not in {"security", "data_warnings"}]
SCHEMA_VERSION = 2


def connect(path: Path, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=busy_timeout_ms / 1000)
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        # Recheck the version under the lock, so concurrent startups migrate only once.
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this application")
        if version == 0:
            fields = ", ".join(
                f'"{f}" {"TEXT" if f in TEXT_FIELDS else "REAL"}' for f in SNAPSHOT_FIELDS
            )
            db.execute(f"""CREATE TABLE IF NOT EXISTS token_snapshots (
                id INTEGER PRIMARY KEY, {fields}, payload TEXT NOT NULL,
                UNIQUE(chain, contract_address, timestamp)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS snapshots_token_time
                ON token_snapshots(chain, contract_address, timestamp DESC)""")
            db.execute("""CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, chain TEXT NOT NULL,
                contract_address TEXT NOT NULL, score INTEGER NOT NULL,
                reason TEXT NOT NULL, telegram_message_id INTEGER,
                delivery_status TEXT NOT NULL DEFAULT 'pending'
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS alerts_token_time
                ON alerts(chain, contract_address, timestamp DESC)""")
        if version < 2:
            _migrate_diagnostics(db)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
    except Exception:
        db.rollback()
        db.close()
        raise
    return db


def _migrate_diagnostics(db: sqlite3.Connection) -> None:
    existing = {row["name"] for row in db.execute("PRAGMA table_info(token_snapshots)")}
    for field in ("asset_type", "asset_classification_reason"):
        if field not in existing:
            db.execute(f'ALTER TABLE token_snapshots ADD COLUMN "{field}" TEXT')
    db.execute("""CREATE TABLE scan_runs (
        id INTEGER PRIMARY KEY, started REAL NOT NULL, finished REAL, duration_seconds REAL,
        processed INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
        sent INTEGER NOT NULL DEFAULT 0, potential_alerts INTEGER NOT NULL DEFAULT 0,
        sources_ok INTEGER NOT NULL DEFAULT 0,
        discovered_by_chain TEXT NOT NULL DEFAULT '{}',
        discovered_by_source TEXT NOT NULL DEFAULT '{}',
        discovered_keys TEXT NOT NULL DEFAULT '[]',
        source_errors TEXT NOT NULL DEFAULT '{}'
    )""")
    db.execute("CREATE INDEX scan_runs_started ON scan_runs(started DESC)")
    db.execute("""CREATE TABLE evaluations (
        id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL REFERENCES scan_runs(id),
        timestamp REAL NOT NULL, chain TEXT NOT NULL, contract_address TEXT NOT NULL,
        symbol TEXT NOT NULL, score INTEGER NOT NULL, status TEXT NOT NULL,
        eligible INTEGER NOT NULL, rejection_reasons TEXT NOT NULL,
        missing_fields TEXT NOT NULL, warnings TEXT NOT NULL, asset_type TEXT NOT NULL,
        discovery_sources TEXT NOT NULL, UNIQUE(scan_id, chain, contract_address)
    )""")
    db.execute("CREATE INDEX evaluations_scan ON evaluations(scan_id)")
    db.execute("""CREATE TABLE state (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL
    )""")

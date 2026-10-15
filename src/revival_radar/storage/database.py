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
# market_cap_source and circulating_supply live only in the payload JSON (no migration).
PAYLOAD_ONLY_FIELDS = {"security", "data_warnings", "market_cap_source", "circulating_supply"}
SNAPSHOT_FIELDS = [f for f in TokenSnapshot.model_fields if f not in PAYLOAD_ONLY_FIELDS]
SCHEMA_VERSION = 10
# The read-only website serves schema 7 (projections absent: computed on read), 8, 9 and 10.
PUBLIC_SCHEMA_VERSIONS = (7, 8, 9, 10)


def connect(path: Path, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=busy_timeout_ms / 1000)
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    db.execute("PRAGMA journal_mode=WAL")
    # REPLACE must obey immutable archive delete guards as well as explicit DELETE.
    db.execute("PRAGMA recursive_triggers=ON")
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
        if version < 3:
            _migrate_enrichment(db)
        if version < 4:
            _migrate_outcomes(db)
        if version < 5:
            _migrate_web_controls(db)
        if version < 6:
            _migrate_research_briefs(db)
        if version < 7:
            _migrate_research_outcomes(db)
        if version < 8:
            _migrate_call_performance(db)
        if version < 9:
            _migrate_replay_archive(db)
        if version < 10:
            _migrate_report_configuration(db)
        # An additive query index is compatible with existing schema-4/rollback readers.
        db.execute("""CREATE INDEX IF NOT EXISTS evaluations_chain_time
            ON evaluations(chain,timestamp DESC,id DESC)""")
        db.execute("""CREATE INDEX IF NOT EXISTS evaluations_token_time
            ON evaluations(chain,contract_address,timestamp DESC,id DESC)""")
        db.execute("CREATE INDEX IF NOT EXISTS scan_runs_finished ON scan_runs(finished DESC)")
        db.execute("""CREATE INDEX IF NOT EXISTS evaluations_report_window ON evaluations(
            chain,timestamp DESC,id DESC,scan_id,contract_address,score,status,eligible)""")
        db.execute(
            "CREATE INDEX IF NOT EXISTS enrichment_fetched ON enrichment_cache(fetched_at DESC)"
        )
        # Compatible additive index, including existing schema-seven databases.
        # Reports inspect every saved configuration when all observations agree;
        # index nullable extracted fields instead of reparsing large presentations.
        db.execute("""CREATE INDEX IF NOT EXISTS state_report_configuration ON state (
            key,
            CAST(json_extract(CASE WHEN json_valid(value) THEN value ELSE '{}' END,
                '$.configuration') AS TEXT),
            CAST(json_extract(CASE WHEN json_valid(value) THEN value ELSE '{}' END,
                '$.signal.score_version') AS TEXT)
        )""")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
    except Exception:
        db.rollback()
        db.close()
        raise
    return db


def _migrate_call_performance(db: sqlite3.Connection) -> None:
    # Additive, derived and fully rebuildable from token_snapshots (see
    # analysis/call_projection.py). Existing tables and rows are never touched; the
    # scanner fills it incrementally after each scan. Idempotent.
    db.execute("""CREATE TABLE IF NOT EXISTS call_performance (
        alert_id INTEGER PRIMARY KEY REFERENCES alerts(id),
        basis TEXT NOT NULL CHECK(basis IN ('same','mixed','unknown')),
        call_source TEXT, call_market_cap REAL,
        latest_market_cap REAL, latest_price REAL, latest_observed_at REAL,
        max_market_cap REAL, min_market_cap REAL,
        observations INTEGER NOT NULL, max_gap_seconds REAL,
        valid_observations INTEGER NOT NULL, other_basis_observations INTEGER NOT NULL,
        updated_at REAL NOT NULL, frozen INTEGER NOT NULL DEFAULT 0 CHECK(frozen IN (0,1))
    )""")


def _migrate_report_configuration(db: sqlite3.Connection) -> None:
    # Additive, derived and fully rebuildable from the saved presentations (see
    # analysis/report_projection.py). Existing tables and rows are never touched; the
    # scanner fills it after each scan (backfilling history in bounded batches). Idempotent.
    db.execute("""CREATE TABLE IF NOT EXISTS evaluation_configuration (
        evaluation_id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, digest BLOB NOT NULL
    )""")


def _migrate_research_outcomes(db: sqlite3.Connection) -> None:
    # Additive receipts only: legacy knowledge/brief associations stay unknown.
    db.execute("""CREATE TABLE IF NOT EXISTS research_alert_facts (
        alert_id INTEGER PRIMARY KEY, saved_at REAL NOT NULL, payload TEXT NOT NULL,
        score_version TEXT, brief_id TEXT, initial_price REAL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS research_delivery_receipts (
        alert_id INTEGER PRIMARY KEY, sent_at REAL NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS research_checkpoint_facts (
        alert_id INTEGER NOT NULL, horizon_hours INTEGER NOT NULL,
        recorded_at REAL NOT NULL, due_at REAL NOT NULL, observed_at REAL NOT NULL,
        price REAL, market_cap REAL, return_pct REAL, origin TEXT NOT NULL,
        PRIMARY KEY(alert_id,horizon_hours)
    )""")
    for table in (
        "research_alert_facts",
        "research_delivery_receipts",
        "research_checkpoint_facts",
    ):
        for operation in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Research evidence is immutable'); END""")


def _migrate_replay_archive(db: sqlite3.Connection) -> None:
    # Additive, append-only replay evidence (issue #74). No backfill: pruned history
    # cannot be recovered, and existing rows are never rewritten.
    db.execute("""CREATE TABLE IF NOT EXISTS replay_evaluations (
        id INTEGER PRIMARY KEY, evaluation_id INTEGER, scan_id INTEGER,
        timestamp REAL NOT NULL, chain TEXT NOT NULL, contract_address TEXT NOT NULL,
        symbol TEXT, score INTEGER, status TEXT, eligible INTEGER,
        rejection_reasons TEXT, missing_fields TEXT, warnings TEXT, asset_type TEXT,
        discovery_sources TEXT, presentation BLOB NOT NULL, reason TEXT NOT NULL,
        archived_at REAL NOT NULL, UNIQUE(chain,contract_address,timestamp)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS replay_evaluations_time
        ON replay_evaluations(timestamp)""")
    db.execute("""CREATE TABLE IF NOT EXISTS replay_candles (
        id INTEGER PRIMARY KEY, chain TEXT NOT NULL, contract_address TEXT NOT NULL,
        kind TEXT NOT NULL, fetched_at REAL NOT NULL, series_hash TEXT NOT NULL,
        payload BLOB NOT NULL, archived_at REAL NOT NULL,
        UNIQUE(chain,contract_address,kind,series_hash)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS replay_candles_token
        ON replay_candles(chain,contract_address,fetched_at)""")
    for table in ("replay_evaluations", "replay_candles"):
        for operation in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Replay evidence is immutable'); END""")


def _migrate_research_briefs(db: sqlite3.Connection) -> None:
    # No historical backfill: mutable legacy caches cannot establish old versions.
    db.execute("""CREATE TABLE IF NOT EXISTS research_briefs (
        id TEXT PRIMARY KEY, scan_id INTEGER NOT NULL, evaluation_id INTEGER NOT NULL,
        chain TEXT NOT NULL, contract_address TEXT NOT NULL, as_of REAL NOT NULL,
        saved_at REAL NOT NULL, origin TEXT NOT NULL, payload TEXT NOT NULL,
        scan_started_at REAL NOT NULL, UNIQUE(scan_id,evaluation_id,scan_started_at)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS research_briefs_token
        ON research_briefs(chain,contract_address,saved_at DESC,id)""")
    db.execute("""CREATE TABLE IF NOT EXISTS research_brief_publications (
        brief_id TEXT PRIMARY KEY, published_at REAL NOT NULL
    )""")
    for table in ("research_briefs", "research_brief_publications"):
        for operation in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Research versions are immutable'); END""")


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


def _migrate_enrichment(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE enrichment_cache (
        kind TEXT NOT NULL, chain TEXT NOT NULL, contract_address TEXT NOT NULL,
        fetched_at REAL NOT NULL, expires_at REAL NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(kind,chain,contract_address)
    )""")
    db.execute("""CREATE TABLE watch_state (
        chain TEXT NOT NULL, contract_address TEXT NOT NULL,
        first_seen REAL NOT NULL, last_seen REAL NOT NULL,
        last_polled REAL NOT NULL DEFAULT 0, last_score INTEGER NOT NULL DEFAULT 0,
        next_due REAL NOT NULL DEFAULT 0, deferred INTEGER NOT NULL DEFAULT 0,
        tier TEXT NOT NULL DEFAULT 'high', payload TEXT NOT NULL,
        PRIMARY KEY(chain,contract_address)
    )""")
    db.execute("CREATE INDEX watch_due ON watch_state(chain,next_due,last_seen)")


def _migrate_outcomes(db: sqlite3.Connection) -> None:
    # Additive migration: original alerts, snapshots and cooldown records are untouched.
    db.execute("""CREATE TABLE alert_signals (
        alert_id INTEGER PRIMARY KEY REFERENCES alerts(id),
        price REAL, market_cap REAL, score INTEGER NOT NULL,
        setup_score INTEGER, trigger_score INTEGER, confirmation_score INTEGER,
        stage TEXT, preset TEXT, revision INTEGER
    )""")
    db.execute("""CREATE TABLE signal_outcomes (
        alert_id INTEGER NOT NULL REFERENCES alerts(id),
        horizon_hours INTEGER NOT NULL CHECK(horizon_hours IN (1,6,24,72)),
        due_at REAL NOT NULL, observed_at REAL NOT NULL,
        price REAL, market_cap REAL, return_pct REAL,
        PRIMARY KEY(alert_id,horizon_hours)
    )""")
    db.execute("CREATE INDEX outcomes_observed ON signal_outcomes(observed_at)")
    db.execute("""CREATE INDEX IF NOT EXISTS outcome_alerts_due
        ON alerts(timestamp,chain,contract_address)
        WHERE delivery_status='sent'""")
    # Existing saved presentation is the only trustworthy source for alert-time data.
    # Legacy alerts without it keep unknown fields; never infer from newer snapshots.
    db.execute("""INSERT INTO alert_signals
        SELECT a.id,
            json_extract(s.value,'$.token.price'),
            json_extract(s.value,'$.token.market_cap'), a.score,
            json_extract(s.value,'$.signal.setup_score'),
            json_extract(s.value,'$.signal.trigger_score'),
            json_extract(s.value,'$.signal.confirmation_score'),
            json_extract(s.value,'$.signal.status'),
            json_extract(s.value,'$.configuration.preset'),
            json_extract(s.value,'$.configuration.revision')
        FROM alerts a LEFT JOIN state s
        ON s.key='telegram:alert:' || a.id AND json_valid(s.value)""")


def _migrate_web_controls(db: sqlite3.Connection) -> None:
    """Scanner-owned, additive storage; the separate web API never migrates."""
    db.execute("""CREATE TABLE web_commands (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, action TEXT NOT NULL,
        payload TEXT NOT NULL, expected_revision INTEGER, target_revision INTEGER,
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK(status IN ('queued','running','succeeded','failed','unknown')),
        result TEXT, created_at REAL NOT NULL, started_at REAL, finished_at REAL
    )""")
    db.execute("CREATE INDEX web_commands_pending ON web_commands(status,created_at,id)")
    db.execute("CREATE INDEX web_commands_owner ON web_commands(owner,created_at DESC)")
    db.execute("""CREATE TABLE web_previews (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL,
        revision INTEGER NOT NULL, expires_at REAL NOT NULL,
        used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0,1))
    )""")
    db.execute("CREATE INDEX web_previews_expiry ON web_previews(expires_at)")
    db.execute("""CREATE TABLE web_sessions (
        token_hash TEXT PRIMARY KEY, csrf_hash TEXT NOT NULL,
        expires_at REAL NOT NULL, created_at REAL NOT NULL
    )""")
    db.execute("CREATE INDEX web_sessions_expiry ON web_sessions(expires_at)")

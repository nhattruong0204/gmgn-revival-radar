import json
import sqlite3

import pytest

from revival_radar.storage.database import SNAPSHOT_FIELDS, TEXT_FIELDS, connect
from revival_radar.storage.repository import Repository


def test_schema_one_migration_preserves_snapshot_payload_and_alert(tmp_path, token):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    old_fields = [
        field
        for field in SNAPSHOT_FIELDS
        if field not in {"asset_type", "asset_classification_reason"}
    ]
    definitions = ",".join(
        f'"{field}" {"TEXT" if field in TEXT_FIELDS else "REAL"}' for field in old_fields
    )
    legacy.execute(f"""CREATE TABLE token_snapshots (
        id INTEGER PRIMARY KEY,{definitions},payload TEXT NOT NULL,
        UNIQUE(chain,contract_address,timestamp))""")
    legacy.execute("""CREATE TABLE alerts (
        id INTEGER PRIMARY KEY,timestamp REAL NOT NULL,chain TEXT NOT NULL,
        contract_address TEXT NOT NULL,score INTEGER NOT NULL,reason TEXT NOT NULL,
        telegram_message_id INTEGER,delivery_status TEXT NOT NULL DEFAULT 'pending')""")
    payload_data = token.model_dump(
        mode="json", exclude={"asset_type", "asset_classification_reason"}
    )
    payload = json.dumps(payload_data)
    payload_data["discovery_source"] = ",".join(sorted(token.discovery_source))
    values = [payload_data[field] for field in old_fields] + [payload]
    legacy.execute(
        f"INSERT INTO token_snapshots ({','.join(old_fields)},payload) "
        f"VALUES ({','.join('?' for _ in values)})",
        values,
    )
    legacy.execute(
        "INSERT INTO alerts VALUES (1,?,?,?,?,?,?,?)",
        (token.timestamp, *token.key, 90, '["existing reason"]', 42, "sent"),
    )
    legacy.execute("PRAGMA user_version=1")
    legacy.commit()
    old_alert = legacy.execute("SELECT * FROM alerts").fetchone()
    legacy.close()

    migrated = connect(path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 3
    assert migrated.execute("SELECT payload FROM token_snapshots").fetchone()[0] == payload
    assert tuple(migrated.execute("SELECT * FROM alerts").fetchone()) == old_alert
    columns = {
        row["name"]: row["type"] for row in migrated.execute("PRAGMA table_info(token_snapshots)")
    }
    assert columns["asset_type"] == "TEXT"
    assert columns["asset_classification_reason"] == "TEXT"
    assert len(Repository(migrated).watchlist(token.chain, 0, 10)) == 1
    next_token = token.model_copy(update={"timestamp": token.timestamp + 300})
    Repository(migrated).save_snapshot(next_token)
    newest = migrated.execute(
        "SELECT asset_type,asset_classification_reason FROM token_snapshots "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    assert tuple(newest) == (next_token.asset_type, next_token.asset_classification_reason)
    migrated.close()
    # Opening an already migrated file must not duplicate tables or alter old records.
    reopened = connect(path)
    assert reopened.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    assert reopened.execute("SELECT payload FROM token_snapshots").fetchone()[0] == payload
    reopened.close()


def test_unknown_newer_schema_is_rejected_without_mutation(tmp_path):
    path = tmp_path / "future.db"
    future = sqlite3.connect(path)
    future.execute("PRAGMA user_version=4")
    future.close()
    with pytest.raises(RuntimeError, match="newer"):
        connect(path)
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 4
    assert check.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    check.close()

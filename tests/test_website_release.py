"""Cross-feature acceptance against saved observations, with no live providers."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.web_commands import CommandStore

from .conftest import changed
from .test_web_public import NOW, SECRET, observe
from .test_web_public import web as web


async def test_saved_bot_public_parity_across_cutoffs_identity_and_unknowns(
    web, repo, token, config
):
    client, _ = web
    live = config.model_copy(update={"dry_run": False})
    addresses = [token.contract_address, "B" * 32, "C" * 32]
    alerts = []
    for index, address in enumerate(addresses):
        snapshot, signal, context = observe(
            repo,
            changed(token, contract_address=address, symbol="SAME"),
            live,
            timestamp=NOW - 7200,
            score=95 - index * 5,
        )
        # The third alert represents a genuine pre-seven record, not a mutated new receipt.
        repo.research_outcomes_enabled = index != 2
        alert = repo.reserve_alert(snapshot, signal, live, context)
        repo.finish_alert(alert, "sent", message_id=987654321)
        repo.research_outcomes_enabled = True
        alerts.append(alert)
        if index < 2:
            repo.observe_outcomes(
                changed(
                    snapshot,
                    timestamp=NOW - 3600,
                    price=snapshot.price * (2 if index == 0 else 0.5),
                )
            )
        observe(repo, changed(snapshot, symbol="SAME"), live, timestamp=NOW - 100, score=50 + index)
    # An old evaluation with no saved details and a legacy sent signal remain unknown.
    with repo.db:
        repo.db.execute("DELETE FROM state WHERE key='telegram:evaluation:5'")
        repo.db.execute("DELETE FROM state WHERE key=?", (f"telegram:alert:{alerts[2]}",))
        repo.db.execute(
            "UPDATE alert_signals SET price=NULL,stage=NULL,revision=NULL WHERE alert_id=?",
            (alerts[2],),
        )
    observe(repo, token, live, timestamp=NOW - 20, score=100, finished=False)
    # Completed now, but unfinished at the earlier requested cutoff.
    observe(repo, token, live, timestamp=NOW - 200, score=99)
    with repo.db:
        repo.db.execute("UPDATE scan_runs SET finished=? WHERE id=8", (NOW - 10,))
    for cutoff in (NOW - 150, NOW):
        bot = repo.market_report(cutoff - 86400, cutoff, timezone="Asia/Ho_Chi_Minh")
        public = (await client.get("/api/v1/reports", params={"hours": 24, "as_of": cutoff})).json()
        assert [(entry["chain"], entry["contract_address"]) for entry in public["entries"]] == [
            (entry["chain"], entry["contract_address"]) for entry in bot["entries"]
        ]
        assert len(public["entries"]) == 3
        for exposed, saved in zip(public["entries"], bot["entries"], strict=True):
            for role in ("peak", "latest"):
                for key in ("id", "score", "timestamp", "status", "eligible"):
                    assert exposed[role][key] == saved[role][key]
        if cutoff == NOW - 150:
            assert [entry["peak"]["score"] for entry in public["entries"]] == [95, 90, 85]
        outcomes = (await client.get("/api/v1/outcomes", params={"as_of": cutoff})).json()
        assert outcomes["horizons"] == repo.outcome_summary(cutoff - 7 * 86400, cutoff)
        assert outcomes["horizons"]["1"]["observed"] == 2
        assert outcomes["horizons"]["1"]["median_return_pct"] == 25  # median(+100, -50)
        assert SECRET not in json.dumps([public, outcomes])
        assert "987654321" not in json.dumps([public, outcomes])
    legacy = (await client.get(f"/api/v1/alerts/{alerts[2]}")).json()
    assert legacy["observation"]["status"] == "UNKNOWN" and legacy["revision"] is None
    assert legacy["outcomes"] == []
    history = (await client.get(f"/api/v1/tokens/sol/{addresses[2]}/history")).json()
    assert history["candles"] == []
    assert len(history["scores"]) == 2
    for path in ("/api/v1/state", "/api/v1/commands", "/api/v1/alerts/999999"):
        response = await client.get(path)
        assert response.status_code == 404
        assert SECRET not in response.text


def test_backup_restore_saved_evidence_controls_and_pending_queue(repo, token, config, tmp_path):
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    config.database_path = path
    snapshot, signal, context = observe(repo, token, config.model_copy(update={"dry_run": False}))
    alert = repo.reserve_alert(
        snapshot, signal, config.model_copy(update={"dry_run": False}), context
    )
    repo.finish_alert(alert, "unknown")  # Retains cooldown and uncertain-delivery evidence.
    runtime = RuntimeSettings(config)
    runtime.apply({"min_holders": 123})
    runtime.set_offset(987)
    CommandStore(repo.db).enqueue(
        "owner:fixture", "pending", "preview", {"desired_action": "pause"}, 1
    )
    tables = [
        row[0] for row in repo.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    saved = {
        table: [tuple(row) for row in repo.db.execute(f'SELECT * FROM "{table}"')]
        for table in tables
    }
    script = Path(__file__).resolve().parents[1] / "scripts/backup_database.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=os.environ | {"DATABASE_PATH": str(path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    backup = next(tmp_path.glob("test.before-upgrade-*.db"))
    assert backup.stat().st_mode & 0o777 == 0o600
    restored = tmp_path / "restored" / "radar.db"
    restored.parent.mkdir()
    with sqlite3.connect(backup) as original, sqlite3.connect(restored) as target:
        original.backup(target)
    restored_config = config.model_copy(update={"database_path": restored})
    restored_runtime = RuntimeSettings(restored_config)
    shutil.copyfile(runtime.path, restored_runtime.path)
    restored_runtime = RuntimeSettings(restored_config)
    assert restored_runtime.offset == 987 and restored_runtime.revision == 1
    assert restored_runtime.effective().min_holders == 123
    assert restored_runtime.path.read_bytes() == runtime.path.read_bytes()
    db = connect(restored)
    try:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table, rows in saved.items():
            assert [tuple(row) for row in db.execute(f'SELECT * FROM "{table}"')] == rows
        restored_repo = Repository(db)
        assert CommandStore(db).get("owner:fixture", "pending")["status"] == "queued"
        assert (
            restored_repo.reserve_alert(
                snapshot, signal, restored_config.model_copy(update={"dry_run": False}), context
            )
            is None
        )
    finally:
        db.close()


async def test_hostile_token_text_is_data_and_nested_private_fields_are_not_public(
    web, repo, token, config
):
    client, _ = web
    text = '<img src=x onerror="alert(1)">'
    observe(repo, changed(token, symbol=text, name=text), config)
    row = repo.db.execute(
        "SELECT key,value FROM state WHERE key LIKE 'telegram:evaluation:%'"
    ).fetchone()
    payload = json.loads(row["value"])
    payload["token"]["telegram_chat_id"] = SECRET
    payload["token"]["security"]["owner_id"] = SECRET
    payload["signal"]["internal_error"] = SECRET
    payload["provider_credentials"] = SECRET
    repo.set_state(row["key"], json.dumps(payload))
    for route in ("/radar", "/reports", f"/tokens/sol/{token.contract_address}"):
        response = await client.get("/api/v1" + route)
        assert response.status_code == 200
        assert text in json.dumps(response.json(), ensure_ascii=False).replace('\\"', '"')
        assert SECRET not in response.text
        for name in ("telegram_chat_id", "owner_id", "internal_error", "provider_credentials"):
            assert name not in response.text

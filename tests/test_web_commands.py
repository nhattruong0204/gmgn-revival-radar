import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from revival_radar import service
from revival_radar.control_service import SharedController
from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.storage.database import SCHEMA_VERSION, connect
from revival_radar.storage.repository import Repository
from revival_radar.web_commands import (
    CommandError,
    CommandStore,
    DuplicateCommandError,
    QueueFullError,
    WebCommandWorker,
)

OWNER = "owner:session-a"
OTHER_OWNER = "owner:session-b"


@pytest.fixture
async def worker(config, tmp_path, repo):
    config.database_path = tmp_path / "controls.db"
    config.web_controls_enabled = True
    config.telegram_bot_token = SecretStr("fixture-telegram-secret")
    config.telegram_chat_id = "-9876"  # Live alerts may use a group; tests must use the owner.
    config.telegram_owner_id = 1234
    runtime = RuntimeSettings(config)
    runtime.set_offset(501)
    runtime.active_revision = 0
    sent = []

    def handler(request):
        assert request.url.host == "api.telegram.org"
        assert request.url.path.endswith("/sendMessage")
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = WebCommandWorker(SharedController(runtime, http), repo)
        result.sent = sent
        yield result


async def make_preview(worker, action="settings", changes=None, *, owner=OWNER, now=1000):
    payload = {"desired_action": action}
    if changes is not None:
        payload["changes"] = changes
    identity = (
        f"preview-{worker.store.db.execute('SELECT count(*) FROM web_commands').fetchone()[0]}"
    )
    worker.store.enqueue(
        owner, identity, "preview", payload, worker.controller.runtime.revision, now
    )
    assert await worker.process_one(now)
    result = worker.store.get(owner, identity)
    assert result["status"] == "succeeded"
    return result["result"]


async def confirm_preview(worker, preview, *, owner=OWNER, identity="confirm-1", now=1001):
    worker.store.enqueue(
        owner, identity, "confirm", {"preview_id": preview["preview_id"]}, preview["revision"], now
    )
    assert await worker.process_one(now)
    return worker.store.get(owner, identity)


def test_schema_four_copy_migration_preserves_legacy_rows_and_runtime_offset(
    config, tmp_path, token
):
    original = tmp_path / "schema4.db"
    db = connect(original)
    repo = Repository(db)
    repo.save_snapshot(token)
    repo.set_state("legacy:unknown", '{"old":"unchanged"}', token.timestamp)
    db.execute(
        "INSERT INTO alerts VALUES(1,?,?,?,?,?,?,?)",
        (token.timestamp, *token.key, 90, '["legacy"]', 77, "sent"),
    )
    db.execute(
        "INSERT INTO alert_signals(alert_id,score) VALUES(1,90)"
    )  # Unknown signal fields must remain unknown.
    repo.set_state("telegram:alert:1", '{"evidence":"preserved"}', token.timestamp)
    db.execute("INSERT INTO signal_outcomes VALUES(1,1,1000,1001,NULL,NULL,NULL)")
    for table in ("web_commands", "web_previews", "web_sessions"):
        db.execute(f"DROP TABLE {table}")
    db.execute("PRAGMA user_version=4")
    db.commit()
    tables = ("alerts", "alert_signals", "signal_outcomes", "token_snapshots", "state")
    saved = {
        table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables
    }
    copied = tmp_path / "migrated-copy.db"
    with sqlite3.connect(copied) as target:
        db.backup(target)
    db.close()
    config.database_path = copied
    runtime = RuntimeSettings(config)
    runtime.apply({"alert_score_threshold": 65})
    runtime.set_offset(987)
    original_controls = runtime.path.read_bytes()

    migrated = connect(copied)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    for table, rows in saved.items():
        assert [tuple(row) for row in migrated.execute(f"SELECT * FROM {table}")] == rows
    assert migrated.execute("SELECT price,stage,revision FROM alert_signals").fetchone()[:] == (
        None,
        None,
        None,
    )
    assert migrated.execute("SELECT count(*) FROM web_sessions").fetchone()[0] == 0
    migrated.close()
    resumed = RuntimeSettings(config)
    assert resumed.offset == 987 and resumed.revision == 1
    assert resumed.path.read_bytes() == original_controls


def test_duplicate_ids_are_bound_to_owner_payload_action_and_revision(repo):
    store = CommandStore(repo.db)
    payload = {"desired_action": "settings", "changes": {"min_holders": 100}}
    first = store.enqueue(OWNER, "a", "preview", payload, 0, 1000)
    assert store.enqueue(OWNER, "a", "preview", dict(reversed(list(payload.items()))), 0) == first
    for owner, value, revision in (
        (OTHER_OWNER, payload, 0),
        (OWNER, payload | {"changes": {"min_holders": 101}}, 0),
        (OWNER, payload, 1),
    ):
        with pytest.raises(DuplicateCommandError):
            store.enqueue(owner, "a", "preview", value, revision)
    assert store.get(OTHER_OWNER, "a") is None
    assert store.list(OTHER_OWNER) == []
    assert store.list(OWNER) == [first]


@pytest.mark.parametrize(
    "action,payload",
    [
        ("settings", {"min_holders": 100}),
        ("test", {}),
        ("preview", {"desired_action": "settings", "changes": {"telegram_bot_token": "secret"}}),
        ("preview", {"desired_action": "scan", "changes": {"min_holders": 100}}),
        ("preview", {"desired_action": "settings", "changes": {"min_holders": float("nan")}}),
        ("confirm", {"preview_id": "abc", "changes": {"min_holders": 1}}),
        ("confirm", {"preview_id": "abc", "desired_action": "test"}),
    ],
)
def test_queue_rejects_direct_effects_secrets_nonfinite_and_changed_confirm_payload(
    repo, action, payload
):
    with pytest.raises(CommandError) as error:
        CommandStore(repo.db).enqueue(OWNER, "a", action, payload)
    assert "secret" not in str(error.value)
    assert repo.db.execute("SELECT count(*) FROM web_commands").fetchone()[0] == 0


def test_queue_is_bounded_when_worker_offline_and_duplicate_retry_still_works(repo):
    store = CommandStore(repo.db, max_outstanding=1)
    payload = {"desired_action": "scan"}
    queued = store.enqueue(OWNER, "a", "preview", payload)
    with pytest.raises(QueueFullError):
        store.enqueue(OWNER, "b", "preview", payload)
    assert store.enqueue(OWNER, "a", "preview", payload) == queued


async def test_preview_normalizes_once_confirm_saves_revision_and_preserves_offset(worker):
    before = worker.controller.runtime.path.read_bytes()
    preview = await make_preview(worker, changes={"min_holders": "120"})
    assert preview["changes"] == {"min_holders": 120}
    assert worker.controller.runtime.path.read_bytes() == before
    result = await confirm_preview(worker, preview)
    assert result["status"] == "succeeded" and result["target_revision"] == 1
    assert result["result"]["revision"] == 1 and result["result"]["active_revision"] == 0
    runtime = worker.controller.runtime
    assert runtime.offset == 501 and runtime.effective().min_holders == 120
    assert "fixture-" not in runtime.path.read_text()
    resumed = RuntimeSettings(runtime.base)
    assert resumed.offset == 501 and resumed.revision == 1
    assert resumed.effective().min_holders == 120
    assert await worker.process_one(1002) is False


async def test_foreign_expired_and_used_previews_cannot_apply(worker):
    preview = await make_preview(worker, changes={"min_holders": 120})
    foreign = await confirm_preview(worker, preview, owner=OTHER_OWNER, identity="foreign")
    assert foreign["status"] == "failed"
    assert worker.controller.runtime.revision == 0
    valid = await confirm_preview(worker, preview, identity="valid")
    assert valid["status"] == "succeeded"
    used = await confirm_preview(worker, preview, identity="used")
    assert used["status"] == "failed"
    assert worker.controller.runtime.revision == 1
    expired = await make_preview(worker, changes={"min_holders": 130}, now=2000)
    result = await confirm_preview(worker, expired, identity="expired", now=2600)
    assert result["status"] == "failed" and "expired" in result["result"]["message"]
    assert worker.controller.runtime.revision == 1


async def test_altered_stored_preview_fails_canonical_revalidation(worker):
    preview = await make_preview(worker, changes={"min_holders": 120})
    with worker.store.db:
        worker.store.db.execute(
            "UPDATE web_previews SET payload=? WHERE id=?",
            (
                json.dumps({"action": "settings", "changes": {"min_holders": "120"}}),
                preview["preview_id"],
            ),
        )
    result = await confirm_preview(worker, preview)
    assert result["status"] == "failed"
    assert worker.controller.runtime.revision == 0


async def test_telegram_change_invalidates_web_preview_and_expected_revision(worker):
    preview = await make_preview(worker, changes={"min_holders": 120})
    runtime = worker.controller.runtime
    runtime.apply({"alert_score_threshold": 65}, expected_revision=0)
    result = await confirm_preview(worker, preview)
    assert result["status"] == "failed" and result["result"]["code"] == "stale_revision"
    assert runtime.revision == 1 and runtime.effective().min_holders != 120
    worker.store.enqueue(
        OWNER, "stale", "preview", {"desired_action": "pause"}, expected_revision=0, now=1100
    )
    assert await worker.process_one(1100)
    assert worker.store.get(OWNER, "stale")["status"] == "failed"


async def test_pause_resume_reset_use_shared_runtime_and_keep_active_revision(worker):
    paused = await make_preview(worker, "pause")
    assert not worker.controller.runtime.effective().alerts_paused
    assert (await confirm_preview(worker, paused))["status"] == "succeeded"
    assert worker.controller.runtime.effective().alerts_paused
    resumed = await make_preview(worker, "resume")
    assert (await confirm_preview(worker, resumed, identity="resume"))["status"] == "succeeded"
    worker.controller.runtime.apply({"min_holders": 999})
    reset = await make_preview(worker, "reset")
    assert (await confirm_preview(worker, reset, identity="reset"))["status"] == "succeeded"
    runtime = worker.controller.runtime
    assert runtime.effective().min_holders == runtime.base.min_holders
    assert runtime.offset == 501 and runtime.revision == 4 and runtime.active_revision == 0


async def test_reset_preview_pins_reviewed_defaults_across_restart(worker):
    worker.controller.runtime.apply({"min_holders": 999})
    preview = await make_preview(worker, "reset")
    base = worker.controller.runtime.base.model_copy(update={"min_holders": 123})
    worker.controller.runtime = RuntimeSettings(base)
    result = await confirm_preview(worker, preview)
    assert result["status"] == "failed"
    assert worker.controller.runtime.revision == 1
    assert worker.controller.runtime.effective().min_holders == 999


async def test_test_alert_is_synthetic_private_even_when_dry_run_and_no_public_records(worker):
    preview = await make_preview(worker, "test")
    assert worker.sent == []
    result = await confirm_preview(worker, preview)
    assert result["status"] == "succeeded"
    assert len(worker.sent) == 1 and worker.sent[0]["chat_id"] == "1234"
    assert "TEST · Synthetic example" in worker.sent[0]["text"]
    assert worker.controller.runtime.revision == 0
    for table in ("alerts", "alert_signals", "signal_outcomes", "scan_runs"):
        assert worker.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    duplicate = worker.store.enqueue(
        OWNER, "confirm-1", "confirm", {"preview_id": preview["preview_id"]}, 0
    )
    assert duplicate == result
    assert await worker.process_one() is False and len(worker.sent) == 1


async def test_uncertain_test_delivery_is_unknown_and_never_replayed(worker):
    attempts = []

    def uncertain(request):
        attempts.append(True)
        raise httpx.ReadTimeout("fixture-telegram-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(uncertain)) as http:
        worker.controller.http = http
        preview = await make_preview(worker, "test")
        result = await confirm_preview(worker, preview)
        assert result["status"] == "unknown"
        assert result["result"]["delivery_status"] == "unknown"
        assert "fixture-" not in json.dumps(result)
        worker.recover(1002)
        assert await worker.process_one(1002) is False
    assert len(attempts) == 1


async def test_crash_after_settings_file_commit_recovers_from_atomic_receipt(worker, monkeypatch):
    preview = await make_preview(worker, changes={"alert_score_threshold": 65})
    finish = worker.store.finish

    def crash(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker.store, "finish", crash)
    with pytest.raises(asyncio.CancelledError):
        await confirm_preview(worker, preview)
    command = worker.store.get(OWNER, "confirm-1")
    assert command["status"] == "running" and command["target_revision"] == 1
    original = worker.controller.runtime.path.read_bytes()
    resumed = RuntimeSettings(worker.controller.runtime.base)
    assert resumed.revision == 1 and resumed.offset == 501
    worker.controller.runtime = resumed
    monkeypatch.setattr(worker.store, "finish", finish)
    worker.recover(1002)
    result = worker.store.get(OWNER, "confirm-1")
    assert result["status"] == "succeeded" and result["result"]["applied_revision"] == 1
    assert resumed.revision == 1 and resumed.path.read_bytes() == original
    assert await worker.process_one(1003) is False


async def test_transient_ack_failure_records_proven_settings_success(worker, monkeypatch):
    preview = await make_preview(worker, changes={"min_holders": 120})
    finish = worker.store.finish
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("temporarily busy")
        return finish(*args, **kwargs)

    monkeypatch.setattr(worker.store, "finish", fail_once)
    result = await confirm_preview(worker, preview)
    assert result["status"] == "succeeded" and result["result"]["applied_revision"] == 1
    assert worker.controller.runtime.revision == 1 and calls == 2


async def test_recovery_does_not_infer_success_from_matching_settings(worker):
    preview = await make_preview(worker, changes={"alert_score_threshold": 65})
    command = worker.store.enqueue(
        OWNER, "interrupted", "confirm", {"preview_id": preview["preview_id"]}, 0, 1001
    )
    command = worker.store.claim(1001)
    stored = worker.store.preview(OWNER, preview["preview_id"], 1001)
    worker.store.prepare_confirmation(command, stored)
    worker.controller.runtime.apply(
        {"alert_score_threshold": 65}
    )  # A Telegram edit, no web receipt.
    worker.recover(1002)
    assert worker.store.get(OWNER, "interrupted")["status"] == "unknown"
    assert await worker.process_one(1002) is False


async def test_telegram_offset_and_subsequent_edit_preserve_recovery_evidence(worker):
    preview = await make_preview(worker, changes={"min_holders": 120})
    command = worker.store.enqueue(
        OWNER, "interrupted", "confirm", {"preview_id": preview["preview_id"]}, 0, 1001
    )
    command = worker.store.claim(1001)
    stored = worker.store.preview(OWNER, preview["preview_id"], 1001)
    worker.store.prepare_confirmation(command, stored)
    await worker.controller.execute(
        "settings", stored["payload"]["changes"], expected_revision=0, command_id="interrupted"
    )
    worker.controller.runtime.set_offset(502)
    worker.controller.runtime.apply({"min_holders": 130}, expected_revision=1)
    resumed = RuntimeSettings(worker.controller.runtime.base)
    worker.controller.runtime = resumed
    worker.recover(1002)
    result = worker.store.get(OWNER, "interrupted")
    assert result["status"] == "succeeded" and result["result"]["applied_revision"] == 1
    assert (
        resumed.revision == 2 and resumed.offset == 502 and resumed.effective().min_holders == 130
    )


@pytest.mark.parametrize("action", ["scan", "test"])
async def test_restart_marks_prepared_nonsettings_action_unknown_without_replay(worker, action):
    preview = await make_preview(worker, action)
    worker.store.enqueue(
        OWNER, "interrupted", "confirm", {"preview_id": preview["preview_id"]}, 0, 1001
    )
    command = worker.store.claim(1001)
    stored = worker.store.preview(OWNER, preview["preview_id"], 1001)
    worker.store.prepare_confirmation(command, stored)
    worker.recover(1002)
    assert worker.store.get(OWNER, "interrupted")["status"] == "unknown"
    assert worker.sent == [] and await worker.process_one(1002) is False


async def test_recovery_rejects_mismatched_command_receipt(worker):
    preview = await make_preview(worker, changes={"min_holders": 120})
    worker.store.enqueue(
        OWNER, "interrupted", "confirm", {"preview_id": preview["preview_id"]}, 0, 1001
    )
    command = worker.store.claim(1001)
    stored = worker.store.preview(OWNER, preview["preview_id"], 1001)
    worker.store.prepare_confirmation(command, stored)
    await worker.controller.execute(
        "settings", stored["payload"]["changes"], expected_revision=0, command_id="interrupted"
    )
    with worker.store.db:
        worker.store.db.execute(
            "UPDATE web_commands SET result=? WHERE id='interrupted'",
            (json.dumps({"execution": {"action": "settings", "changes": {"min_holders": 130}}}),),
        )
    worker.recover(1002)
    assert worker.store.get(OWNER, "interrupted")["status"] == "unknown"
    assert worker.controller.runtime.effective().min_holders == 120


def test_two_connections_claim_each_command_only_once(repo):
    store = CommandStore(repo.db)
    store.enqueue(OWNER, "a", "preview", {"desired_action": "scan"})
    other = connect(Path(repo.db.execute("PRAGMA database_list").fetchone()["file"]))
    try:
        assert store.claim(1000)["id"] == "a"
        assert CommandStore(other).claim(1000) is None
    finally:
        other.close()


async def test_worker_publishes_only_allowlisted_settings_and_current_schedule(worker):
    worker.controller.schedule_provider = lambda: {"running": True, "next_due": None}
    worker.publish(1100)
    snapshot = json.loads(worker.repo.get_state("web:worker"))
    assert snapshot["heartbeat_at"] == 1100 and snapshot["enabled"] is True
    assert snapshot["schedule"]["running"] is True
    assert snapshot["revision"] == 0 and snapshot["active_revision"] == 0
    assert set(snapshot["presets"]) == {"strict", "balanced", "broad"}
    assert "fixture-" not in json.dumps(snapshot)
    assert "telegram_bot_token" not in snapshot["settings"]


async def test_service_web_worker_runs_while_scan_awaits_and_changes_next_scan(
    config, repo, tmp_path, monkeypatch
):
    config.database_path = tmp_path / "service.db"
    config.web_controls_enabled = True
    config.market_report_enabled = False
    config.scan_interval_seconds = 0.001
    runtime = RuntimeSettings(config)
    scan_started, settings_saved = asyncio.Event(), asyncio.Event()
    observed = []
    store = CommandStore(repo.db)

    class Finished(Exception):
        pass

    class FakeScanner:
        def __init__(self, config, *args, **kwargs):
            self.config = config

        async def scan_once(self):
            observed.append((self.config.alert_score_threshold, runtime.active_revision))
            if len(observed) == 1:
                scan_started.set()
                await settings_saved.wait()
                assert self.config.alert_score_threshold == 75
            else:
                raise Finished

    class FakeControls:
        enabled = False

        def __init__(self, *args, **kwargs):
            pass

        def stop(self):
            pass

    async def produce():
        await scan_started.wait()
        store.enqueue(
            OWNER,
            "preview",
            "preview",
            {"desired_action": "settings", "changes": {"alert_score_threshold": 65}},
            0,
        )
        while store.get(OWNER, "preview")["status"] != "succeeded":
            await asyncio.sleep(0.01)
        preview = store.get(OWNER, "preview")["result"]
        store.enqueue(OWNER, "confirm", "confirm", {"preview_id": preview["preview_id"]}, 0)
        while store.get(OWNER, "confirm")["status"] != "succeeded":
            await asyncio.sleep(0.01)
        snapshot = json.loads(repo.get_state("web:worker"))
        assert snapshot["schedule"]["running"] is True
        settings_saved.set()

    monkeypatch.setattr(service, "Scanner", FakeScanner)
    monkeypatch.setattr(service, "TelegramControls", FakeControls)
    producer = asyncio.create_task(produce())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No providers"))
    ) as http:
        try:
            with pytest.raises(Finished):
                await asyncio.wait_for(service.run_service(runtime, repo, http), timeout=3)
            await producer
        finally:
            producer.cancel()
    assert observed == [(75, 0), (65, 1)]


async def test_web_scan_commands_refuse_overlap_and_wake_existing_idle_event(
    config, repo, tmp_path, monkeypatch
):
    config.database_path = tmp_path / "scan-service.db"
    config.web_controls_enabled = True
    config.market_report_enabled = False
    config.scan_interval_seconds = 300
    runtime = RuntimeSettings(config)
    running, released = asyncio.Event(), asyncio.Event()
    store, scans, answers, captured = CommandStore(repo.db), [], [], []

    class Finished(Exception):
        pass

    class FakeScanner:
        def __init__(self, *args, **kwargs):
            pass

        async def scan_once(self):
            scans.append(True)
            if len(scans) == 1:
                running.set()
                await released.wait()
            else:
                raise Finished

    class CapturedWorker(WebCommandWorker):
        def __init__(self, *args):
            super().__init__(*args)
            captured.append(self)

    async def request(identity):
        store.enqueue(OWNER, identity, "preview", {"desired_action": "scan"}, 0)
        while store.get(OWNER, identity)["status"] != "succeeded":
            await asyncio.sleep(0.01)
        preview = store.get(OWNER, identity)["result"]
        confirmed = f"confirm-{identity}"
        store.enqueue(OWNER, confirmed, "confirm", {"preview_id": preview["preview_id"]}, 0)
        while store.get(OWNER, confirmed)["status"] != "succeeded":
            await asyncio.sleep(0.01)
        answers.append(store.get(OWNER, confirmed)["result"]["message"])

    async def produce():
        await running.wait()
        await request("running")
        released.set()
        while captured[0].controller.schedule_provider()["running"]:
            await asyncio.sleep(0.01)
        await request("idle")

    monkeypatch.setattr(service, "Scanner", FakeScanner)
    monkeypatch.setattr(service, "WebCommandWorker", CapturedWorker)
    producer = asyncio.create_task(produce())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No provider requests"))
    ) as http:
        try:
            with pytest.raises(Finished):
                await asyncio.wait_for(service.run_service(runtime, repo, http), timeout=5)
            await producer
        finally:
            producer.cancel()
    assert len(scans) == 2
    assert "already running" in answers[0] and "Scan requested" in answers[1]
    assert runtime.revision == 0


async def test_web_controls_disabled_by_default_do_not_consume_queued_actions(
    config, repo, tmp_path, monkeypatch
):
    assert config.web_controls_enabled is False
    config.database_path = tmp_path / "disabled.db"
    config.market_report_enabled = False
    runtime = RuntimeSettings(config)
    store = CommandStore(repo.db)
    store.enqueue(OWNER, "disabled", "preview", {"desired_action": "pause"}, 0)

    class Finished(Exception):
        pass

    class FakeScanner:
        def __init__(self, *args, **kwargs):
            pass

        async def scan_once(self):
            await asyncio.sleep(0.02)
            raise Finished

    monkeypatch.setattr(service, "Scanner", FakeScanner)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No provider requests"))
    ) as http:
        with pytest.raises(Finished):
            await service.run_service(runtime, repo, http)
    assert store.get(OWNER, "disabled")["status"] == "queued"
    assert repo.get_state("web:worker") is None and runtime.revision == 0


async def test_restart_before_claim_keeps_queued_work_and_after_claim_never_replays(worker):
    worker.store.enqueue(OWNER, "queued", "preview", {"desired_action": "pause"}, 0, 1000)
    worker.recover(1001)
    assert worker.store.get(OWNER, "queued")["status"] == "queued"
    assert await worker.process_one(1001)
    preview = worker.store.get(OWNER, "queued")["result"]
    worker.store.enqueue(OWNER, "claimed", "confirm", {"preview_id": preview["preview_id"]}, 0)
    worker.store.claim(1002)
    worker.recover(1003)
    assert worker.store.get(OWNER, "claimed")["status"] == "unknown"
    assert worker.controller.runtime.revision == 0
    assert await worker.process_one(1004) is False


async def test_crash_after_test_send_before_ack_does_not_duplicate_message(worker, monkeypatch):
    preview = await make_preview(worker, "test")
    finish = worker.store.finish

    def crash(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker.store, "finish", crash)
    with pytest.raises(asyncio.CancelledError):
        await confirm_preview(worker, preview)
    assert len(worker.sent) == 1
    monkeypatch.setattr(worker.store, "finish", finish)
    worker.recover(1002)
    assert worker.store.get(OWNER, "confirm-1")["status"] == "unknown"
    assert await worker.process_one(1003) is False
    assert len(worker.sent) == 1 and worker.controller.runtime.offset == 501


async def test_command_completion_timestamp_follows_slow_execution(worker, monkeypatch):
    preview = await make_preview(worker, "test")
    clock = [1001.0]
    execute = worker.controller.execute

    async def slow_execute(*args, **kwargs):
        result = await execute(*args, **kwargs)
        clock[0] = 1010.0
        return result

    monkeypatch.setattr("revival_radar.web_commands.time.time", lambda: clock[0])
    monkeypatch.setattr(worker.controller, "execute", slow_execute)
    worker.store.enqueue(OWNER, "slow", "confirm", {"preview_id": preview["preview_id"]}, 0)
    assert await worker.process_one()
    result = worker.store.get(OWNER, "slow")
    assert result["started_at"] == 1001 and result["finished_at"] == 1010


async def test_worker_shutdown_publishes_stopped_state_and_restart_clears_it(worker):
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(100):
            if worker.repo.get_state("web:worker") is not None:
                break
            await asyncio.sleep(0.001)
        assert json.loads(worker.repo.get_state("web:worker"))["stopped_at"] is None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    snapshot = json.loads(worker.repo.get_state("web:worker"))
    assert snapshot["stopped_at"] == snapshot["heartbeat_at"]
    worker.publish()
    assert json.loads(worker.repo.get_state("web:worker"))["stopped_at"] is None

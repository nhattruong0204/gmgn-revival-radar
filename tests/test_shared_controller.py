import json

import httpx
import pytest

from revival_radar.control_service import SharedController
from revival_radar.runtime_settings import PRESETS, RuntimeSettings, SettingsChangeError
from revival_radar.telegram_controls import TelegramControls
from revival_radar.web_commands import CommandStore, WebCommandWorker

from .test_telegram_controls import bot as bot
from .test_telegram_controls import confirm, last_message
from .test_telegram_controls import controls_config as controls_config
from .test_telegram_controls import runtime as runtime


async def test_injected_controller_must_share_the_exact_bot_runtime(runtime, controls_config):
    async with httpx.AsyncClient() as http:
        other = RuntimeSettings(controls_config)
        with pytest.raises(SettingsChangeError, match="same runtime"):
            TelegramControls(runtime, http, controller=SharedController(other, http))
        controller = SharedController(runtime, http)
        assert TelegramControls(runtime, http, controller=controller).controller is controller


async def test_published_snapshots_cannot_mutate_global_presets(runtime):
    async with httpx.AsyncClient() as http:
        controller = SharedController(runtime, http)
        original = PRESETS["balanced"]["min_holders"]
        snapshot = controller.snapshot()
        snapshot["presets"]["balanced"]["min_holders"] = 99999
        snapshot["settings"]["min_holders"] = 99999
        assert PRESETS["balanced"]["min_holders"] == original
        assert (
            controller.preview("settings", preset="balanced")["changes"]["min_holders"] == original
        )
        assert runtime.effective().min_holders != 99999


async def test_web_and_telegram_share_revisions_and_reject_stale_confirmation(bot, repo):
    controls, requests = bot
    runtime = controls.runtime
    runtime.set_offset(501)
    runtime.active_revision = 0
    before_scan = runtime.effective()
    await controls._preview({"min_holders": 400})
    worker = WebCommandWorker(controls.controller, repo)
    store = CommandStore(repo.db)
    store.enqueue(
        "owner:fixture",
        "preview",
        "preview",
        {
            "desired_action": "settings",
            "changes": {"min_holders": 500},
        },
        0,
        1000,
    )
    assert await worker.process_one(1000)
    preview = store.get("owner:fixture", "preview")["result"]
    store.enqueue(
        "owner:fixture", "confirm", "confirm", {"preview_id": preview["preview_id"]}, 0, 1001
    )
    assert await worker.process_one(1001)
    assert runtime.revision == 1 and runtime.effective().min_holders == 500
    assert before_scan.min_holders != 500 and runtime.active_revision == 0
    await confirm(bot)
    assert runtime.revision == 1 and runtime.effective().min_holders == 500
    assert "Settings changed" in last_message(requests)["text"]
    assert runtime.offset == 501
    assert json.loads(runtime.path.read_text())["offset"] == 501


async def test_shared_pause_resume_does_not_change_report_schedule_or_scan_snapshot(runtime):
    runtime.active_revision = 0
    scan_config = runtime.effective()
    async with httpx.AsyncClient() as http:
        controller = SharedController(runtime, http)
        result = await controller.execute("pause", expected_revision=0)
        assert runtime.effective().alerts_paused and not scan_config.alerts_paused
        assert runtime.effective().market_report_enabled == scan_config.market_report_enabled
        assert result["revision"] == 1 and result["active_revision"] == 0
        await controller.execute("resume", expected_revision=1)
        assert not runtime.effective().alerts_paused and runtime.active_revision == 0


async def test_shared_scan_action_uses_existing_scheduler_without_settings_mutation(runtime):
    requests = []

    def request_scan():
        requests.append(True)
        return "already running" if len(requests) > 1 else "queued"

    async with httpx.AsyncClient() as http:
        controller = SharedController(runtime, http, request_scan, lambda: {"running": True})
        for expected in ("queued", "already running"):
            assert (await controller.execute("scan", expected_revision=0))["message"] == expected
        assert runtime.revision == 0 and len(requests) == 2


async def test_telegram_test_uses_shared_synthetic_message_and_no_public_records(bot, repo):
    controls, requests = bot
    await controls._action_preview("test")
    await confirm(bot)
    sent = [
        payload
        for method, payload in requests
        if "TEST · Synthetic example" in payload.get("text", "")
    ]
    assert len(sent) == 1 and sent[0]["chat_id"] == controls.owner_id
    assert not controls.runtime.base.database_path.exists()
    assert all(method in {"sendMessage", "answerCallbackQuery"} for method, _ in requests)
    for table in ("alerts", "signal_outcomes", "scan_runs"):
        assert repo.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0

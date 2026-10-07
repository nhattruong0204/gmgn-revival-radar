import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from revival_radar import service
from revival_radar.clients.telegram import TelegramClient
from revival_radar.demo import DemoSource
from revival_radar.runtime_settings import RuntimeSettings, SettingsChangeError
from revival_radar.scanner import Scanner


def test_runtime_request_pacing_is_validated_and_persistent(config, tmp_path):
    path = tmp_path / "controls.json"
    runtime = RuntimeSettings(config, path)
    with pytest.raises(SettingsChangeError):
        runtime.apply({"request_spacing_seconds": 0})
    runtime.apply({"request_spacing_seconds": 2})
    assert RuntimeSettings(config, path).effective().request_spacing_seconds == 2


def live_config(config):
    return config.model_copy(
        update={
            "dry_run": False,
            "telegram_chat_id": "123456",
            "telegram_bot_token": SecretStr("fixture-token"),
            "daily_summary_enabled": True,
        }
    )


async def test_daily_summary_local_time_once_even_after_failed_send(config, repo):
    config = live_config(config)
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(403, json={"ok": False})

    before = datetime(2026, 10, 7, 1, 59, tzinfo=UTC).timestamp()  # 08:59 Bangkok
    after = before + 60
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = TelegramClient(config, http)
        assert await service.daily_summary(repo, config, client, before) is None
        assert await service.daily_summary(repo, config, client, after) == "failed"
        assert await service.daily_summary(repo, config, client, after + 3600) is None
        assert len(calls) == 1
        assert "Asia/Bangkok" in calls[0]["text"]
        assert await service.daily_summary(repo, config, client, after + 86400) == "failed"
    assert len(calls) == 2
    saved = json.loads(repo.get_state("daily_summary_delivery"))
    assert saved["status"] == "failed"


@pytest.mark.parametrize("field", ["dry_run", "alerts_paused"])
async def test_daily_summary_muted_without_claiming_day(field, config, repo):
    config = live_config(config)
    setattr(config, field, True)

    def forbidden(request):
        pytest.fail("Muted summaries must not send")

    now = datetime(2026, 10, 7, 3, tzinfo=UTC).timestamp()
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        assert await service.daily_summary(repo, config, TelegramClient(config, http), now) is None
    assert repo.claim_daily_summary("Asia/Bangkok:2026-10-07", now)


async def test_runtime_pause_suppresses_inflight_alert_without_reservation(config, repo):
    config = live_config(config)
    source = DemoSource()
    for token in source.histories():
        repo.save_snapshot(token)

    def forbidden(request):
        pytest.fail("Paused alerts must not send")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        report = await Scanner(
            config, source, repo, TelegramClient(config, http), delivery_muted=lambda: True
        ).scan_once()
    assert report.potential_alerts == 1 and report.sent == 0 and report.errors == 0
    assert repo.db.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0
    assert repo.db.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 3


async def test_service_applies_changes_next_scan_and_cancels_controls(
    config, repo, tmp_path, monkeypatch
):
    config.scan_interval_seconds = 0.001
    config.database_path = tmp_path / "service.db"
    runtime = RuntimeSettings(config)
    first_started, edited, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []

    class FakeControls:
        enabled = True

        def __init__(self, runtime, http, health_provider):
            self.runtime = runtime

        async def run(self):
            try:
                await first_started.wait()
                self.runtime.apply({"alert_score_threshold": 65})
                edited.set()
                await asyncio.Event().wait()
            finally:
                stopped.set()

        def stop(self):
            pass

    class Finished(Exception):
        pass

    class FakeScanner:
        def __init__(self, config, source, repo, telegram, delivery_muted):
            self.config = config

        async def scan_once(self):
            observed.append((self.config.alert_score_threshold, runtime.active_revision))
            if len(observed) == 1:
                first_started.set()
                await edited.wait()
                assert self.config.alert_score_threshold == 75  # Immutable current scan.
            else:
                raise Finished()

    monkeypatch.setattr(service, "TelegramControls", FakeControls)
    monkeypatch.setattr(service, "Scanner", FakeScanner)
    async with httpx.AsyncClient() as http:
        with pytest.raises(Finished):
            await asyncio.wait_for(service.run_service(runtime, repo, http), timeout=2)
    assert observed == [(75, 0), (65, 1)]
    assert stopped.is_set()

import asyncio
import json
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr

from revival_radar import service
from revival_radar.clients.telegram import TelegramClient
from revival_radar.runtime_settings import RuntimeSettings


def instant(value):
    return datetime.fromisoformat(value).timestamp()


@pytest.fixture
def report_config(config):
    return config.model_copy(
        update={
            "dry_run": False,
            "telegram_bot_token": SecretStr("fixture-report-token"),
            "telegram_chat_id": "123456",
            "market_report_enabled": True,
            "daily_summary_enabled": False,
            "alerts_paused": True,
            "alert_score_threshold": 100,
        }
    )


class MemoryReportRepo:
    def __init__(self):
        self.claims = {}
        self.states = {}
        self.queries = []
        self.finishes = []

    def claim_market_report(self, period_key, timestamp):
        if period_key in self.claims:
            return False
        self.claims[period_key] = {"status": "pending", "claimed_at": timestamp}
        return True

    def market_report(self, since, now=None, timezone="Asia/Bangkok", limit=10):
        self.queries.append((since, now, timezone, limit))
        return {
            "since": since,
            "until": now,
            "timezone": timezone,
            "hours": (now - since) / 3600,
            "chain": "sol",
            "evaluations": 0,
            "unique_tokens": 0,
            "completed_scans": 0,
            "incomplete_scans": 0,
            "last_finished": None,
            "first_observation": None,
            "multiple_configurations": False,
            "entries": [],
        }

    def set_state(self, key, value, timestamp=None):
        self.states[key] = value

    def finish_market_report(self, period_key, status, message_id=None, timestamp=None):
        self.claims[period_key].update(status=status, message_id=message_id)
        self.finishes.append((period_key, status, message_id, timestamp))


@pytest.fixture
def report_repo():
    return MemoryReportRepo()


@pytest.mark.parametrize(
    ("now", "timezone", "until"),
    [
        ("2026-10-09T16:59:59+00:00", "Asia/Bangkok", "2026-10-09T13:00:00+00:00"),
        ("2026-10-09T17:00:00+00:00", "Asia/Bangkok", "2026-10-09T17:00:00+00:00"),
        ("2026-10-09T20:59:59+00:00", "Asia/Bangkok", "2026-10-09T17:00:00+00:00"),
        ("2026-10-09T21:00:00+00:00", "Asia/Bangkok", "2026-10-09T21:00:00+00:00"),
        ("2026-10-10T03:59:59+00:00", "UTC", "2026-10-10T00:00:00+00:00"),
        ("2026-10-10T04:00:00+00:00", "UTC", "2026-10-10T04:00:00+00:00"),
        ("2026-03-08T08:00:00+00:00", "America/New_York", "2026-03-08T08:00:00+00:00"),
        ("2026-11-01T09:00:00+00:00", "America/New_York", "2026-11-01T09:00:00+00:00"),
        ("2018-11-04T03:00:00+00:00", "America/Sao_Paulo", "2018-11-03T23:00:00+00:00"),
        ("2026-11-01T04:30:00+00:00", "America/Havana", "2026-11-01T04:00:00+00:00"),
        ("2026-11-01T05:30:00+00:00", "America/Havana", "2026-11-01T04:00:00+00:00"),
    ],
)
def test_local_report_boundaries_use_actual_four_hour_window(now, timezone, until):
    period_key, since, end = service.market_report_period(instant(now), timezone)
    assert end == instant(until)
    assert end - since == 4 * 3600
    assert period_key == f"{timezone}:{int(end)}"


async def test_startup_latest_period_only_and_immutable_snapshot(report_config, report_repo):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 91}})

    now = instant("2026-10-09T23:35:00+00:00")  # Bangkok 06:35: report ends 04:00.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        telegram = TelegramClient(report_config, http)
        assert (
            await service.four_hour_market_report(report_repo, report_config, telegram, now)
            == "sent"
        )
        assert (
            await service.four_hour_market_report(report_repo, report_config, telegram, now + 60)
            is None
        )
    period_key, since, until = service.market_report_period(now, report_config.report_timezone)
    assert report_repo.queries == [(since, until, "Asia/Bangkok", 10)]
    saved = json.loads(report_repo.states[f"market_report:snapshot:{period_key}"])
    assert saved["since"] == since and saved["until"] == until and saved["entries"] == []
    assert saved["generated_at"] == now
    assert report_repo.claims[period_key]["status"] == "sent"
    assert report_repo.claims[period_key]["message_id"] == 91
    assert len(calls) == 1
    assert "Asia/Bangkok" in calls[0]["text"]


@pytest.mark.parametrize(
    "changes",
    [
        {"dry_run": True},
        {"market_report_enabled": False},
        {"telegram_bot_token": SecretStr("")},
        {"telegram_chat_id": ""},
        {"telegram_chat_id": "  "},
    ],
)
async def test_suppressed_reporting_does_not_claim_or_query(changes, report_config, report_repo):
    config = report_config.model_copy(update=changes)

    class ForbiddenTelegram:
        async def send_text(self, *args):
            pytest.fail("Suppressed reporting must not send")

    assert (
        await service.four_hour_market_report(report_repo, config, ForbiddenTelegram(), 1791560000)
        is None
    )
    assert report_repo.claims == {} and report_repo.queries == []


@pytest.mark.parametrize("status", ["sent", "failed", "unknown", "pending"])
async def test_previous_claims_suppress_all_delivery_states(status, report_config, report_repo):
    now = instant("2026-10-10T01:00:00+00:00")
    period_key, _, _ = service.market_report_period(now, report_config.report_timezone)
    report_repo.claims[period_key] = {"status": status}

    class ForbiddenTelegram:
        async def send_text(self, *args):
            pytest.fail("Any previous claim must prevent duplicate delivery")

    assert (
        await service.four_hour_market_report(report_repo, report_config, ForbiddenTelegram(), now)
        is None
    )
    assert report_repo.queries == []


@pytest.mark.parametrize("failure", ["rejected", "timeout", "unexpected"])
async def test_failed_or_uncertain_sends_are_persisted_without_retry(
    failure, report_config, report_repo
):
    calls = []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-url-must-not-be-logged", request=request)
        if failure == "unexpected":
            raise RuntimeError("private-url-must-not-be-logged")
        return httpx.Response(403, json={"ok": False})

    now = instant("2026-10-10T01:00:00+00:00")
    expected = "failed" if failure == "rejected" else "unknown"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        telegram = TelegramClient(report_config, http)
        assert (
            await service.four_hour_market_report(report_repo, report_config, telegram, now)
            == expected
        )
        assert (
            await service.four_hour_market_report(report_repo, report_config, telegram, now + 30)
            is None
        )
        assert (
            await service.four_hour_market_report(
                report_repo, report_config, telegram, now + 4 * 3600
            )
            == expected
        )
    assert len(calls) == 2
    assert [row[1] for row in report_repo.finishes] == [expected, expected]


async def test_snapshot_failure_is_failed_and_does_not_send(
    report_config, report_repo, monkeypatch, caplog
):
    def broken(*args, **kwargs):
        raise RuntimeError("private-query-details")

    monkeypatch.setattr(report_repo, "market_report", broken)

    class ForbiddenTelegram:
        async def send_text(self, *args):
            pytest.fail("Failed snapshots must not send")

    now = instant("2026-10-10T01:00:00+00:00")
    assert (
        await service.four_hour_market_report(report_repo, report_config, ForbiddenTelegram(), now)
        == "failed"
    )
    assert report_repo.finishes[0][1] == "failed"
    assert "private-query-details" not in caplog.text


async def test_cancellation_marks_uncertain_and_propagates(report_config, report_repo):
    class CancelledTelegram:
        async def send_text(self, *args):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await service.four_hour_market_report(
            report_repo, report_config, CancelledTelegram(), 1791560000
        )
    assert report_repo.finishes[0][1] == "unknown"


async def test_background_recovers_from_claim_exception(
    report_config, report_repo, monkeypatch, caplog
):
    runtime = RuntimeSettings(report_config)
    original_claim = report_repo.claim_market_report
    attempts = []
    delivered = asyncio.Event()
    sleeps = []

    def claim(period_key, timestamp):
        attempts.append(period_key)
        if len(attempts) == 1:
            raise RuntimeError("private-claim-details")
        return original_claim(period_key, timestamp)

    async def sleep(delay):
        sleeps.append(delay)
        if delivered.is_set():
            raise asyncio.CancelledError()

    def handle(request):
        delivered.set()
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(report_repo, "claim_market_report", claim)
    monkeypatch.setattr(service.asyncio, "sleep", sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(asyncio.CancelledError):
            await service.market_reports_loop(runtime, report_repo, http)
    assert len(attempts) == 2 and delivered.is_set()
    assert sleeps == [30, 30]
    assert "private-claim-details" not in caplog.text


async def test_real_repository_restart_deduplication(report_config, repo, tmp_path):
    from revival_radar.storage.database import connect
    from revival_radar.storage.repository import Repository

    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    now = instant("2026-10-10T01:00:00+00:00")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = TelegramClient(report_config, http)
        assert await service.four_hour_market_report(repo, report_config, client, now) == "sent"
        connection = connect(tmp_path / "test.db")
        try:
            restarted = Repository(connection)
            assert (
                await service.four_hour_market_report(restarted, report_config, client, now + 30)
                is None
            )
        finally:
            connection.close()
    assert len(calls) == 1


async def test_service_report_task_is_independent_and_controls_query_is_local(
    report_config, repo, tmp_path, monkeypatch
):
    report_config.database_path = tmp_path / "report-service.db"
    runtime = RuntimeSettings(report_config)
    report_finished = asyncio.Event()
    observations = {}
    requests = []

    class Finished(Exception):
        pass

    class FakeControls:
        enabled = False

        def __init__(self, runtime, http, market_report_provider, **kwargs):
            observations["manual_report"] = market_report_provider(12)

        def stop(self):
            observations["controls_stopped"] = True

    class FakeScanner:
        def __init__(self, config, source, repo, telegram, **kwargs):
            observations["scan_config"] = config

        async def scan_once(self):
            await report_finished.wait()
            raise Finished()

    async def report_task(runtime, repo, http):
        config = runtime.effective()
        assert config.alerts_paused and config.alert_score_threshold == 100
        assert not config.daily_summary_enabled
        await service.four_hour_market_report(repo, config, TelegramClient(config, http))
        report_finished.set()
        await asyncio.Event().wait()

    def handle(request):
        requests.append(request)
        assert request.url.host == "api.telegram.org"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(service, "TelegramControls", FakeControls)
    monkeypatch.setattr(service, "Scanner", FakeScanner)
    monkeypatch.setattr(service, "market_reports_loop", report_task)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(Finished):
            await asyncio.wait_for(service.run_service(runtime, repo, http), timeout=2)
    assert len(requests) == 1
    assert observations["manual_report"]["hours"] == 12
    assert observations["controls_stopped"]

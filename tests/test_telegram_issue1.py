import json
import time
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from revival_radar.bot_profile import configure_profile
from revival_radar.clients.telegram import alert_buttons, format_alert, format_why
from revival_radar.config_context import configuration_context
from revival_radar.models.signal import Acceleration, RevivalResult, Structure
from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.telegram_controls import TelegramControls
from revival_radar.telegram_views import health_page

from .conftest import changed
from .test_alert_presentation import TelegramHTML
from .test_telegram_controls import callback, message


def live_owner(config, tmp_path):
    return config.model_copy(
        update={
            "telegram_owner_id": 1234,
            "telegram_chat_id": "1234",
            "telegram_bot_token": SecretStr("fixture-bot-secret"),
            "database_path": tmp_path / "controls.db",
            "dry_run": False,
        }
    )


def assert_html(text):
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    parser = TelegramHTML()
    parser.feed(text)
    parser.close()
    assert not parser.tags


def test_compact_alert_includes_required_summary_and_secondary_buttons(token, config):
    result = RevivalResult(
        score=85,
        status="STRONG_REVIVAL",
        eligible=True,
        acceleration=Acceleration(volume_ratio_5m=1.8, tx_acceleration_5m=2),
        structure=Structure(
            available=True, base_detected=True, base_duration_hours=97, higher_high_detected=True
        ),
        components={"volume_5m": 15, "concentration_penalty": -10},
    )
    text = format_alert(token, result, configuration_context(config, 7))
    assert f"${token.symbol} · Solana" in text
    for value in (
        "85/100",
        "Strong revival",
        "Cap",
        "ATH",
        "Liquidity",
        "Holders",
        "Trigger",
        "+80% vs baseline",
        "Base 97h",
        "Higher high ✓",
        "r7",
    ):
        assert value in text
    assert len(text.splitlines()) <= 23
    assert "Setup" not in text  # Current scorer has no category scores; do not invent them.
    assert "weighted contributions" not in text
    buttons = alert_buttons(token, 5)
    assert {button["text"] for row in buttons for button in row} == {
        "📈 GMGN",
        "🔎 Explorer",
        "🧠 Why this alert",
        "📊 Full details",
    }
    assert "High holder concentration -10" in format_why(token, result, {})
    assert_html(text)


def test_compact_alert_bounds_escapes_and_keeps_missing_data_visible(token):
    token = changed(token, symbol="<&🦊" * 30, volume_5m=None, tx_5m=None, security={})
    result = RevivalResult(
        score=60,
        status="<REVIVING>" * 100,
        eligible=False,
        components={"risk_<&" + str(i): -5 for i in range(80)},
        reasons=["<&" * 1000] * 30,
    )
    text = format_alert(token, result, {"preset": "<&" * 100, "revision": "<bad>"})
    assert "Security assessment unavailable" in text
    assert "Structure unavailable" in text and "unavailable" in text
    assert "&lt;" in text and "True" not in text and "False" not in text
    assert_html(text)
    assert_html(format_why(token, result, {}))


def test_saved_alert_detail_is_immutable_survives_reopen_and_contains_no_secrets(
    config,
    repo,
    token,
    tmp_path,
):
    config = live_owner(config, tmp_path)
    context = configuration_context(config, 2)
    result = RevivalResult(score=85, status="REVIVING", eligible=True)
    identity = repo.reserve_alert(token, result, config, context)
    repo.finish_alert(identity, "sent", 42)
    context["revision"] = 99
    token.market_cap = 1
    detail = repo.presentation_detail("a", identity)
    assert detail["configuration"]["revision"] == 2
    assert detail["token"]["market_cap"] != 1
    assert "fixture-" not in json.dumps(detail)
    path = repo.db.execute("PRAGMA database_list").fetchone()["file"]
    other = connect(Path(path))
    try:
        assert Repository(other).presentation_detail("a", identity) == detail
        assert other.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        other.close()
    assert repo.presentation_detail("a", 2**63) is None
    assert repo.presentation_detail("unknown", identity) is None


def test_presentation_pruning_preserves_alerts_but_removes_old_scan_and_evaluation(
    config,
    repo,
    token,
):
    context = configuration_context(config, 3)
    result = RevivalResult(score=80, status="REVIVING", eligible=True)
    config.dry_run = False
    scan = repo.begin_scan(token.timestamp, context)
    repo.record_evaluation(scan, token, result, config, context)
    identity = repo.db.execute("SELECT id FROM evaluations").fetchone()["id"]
    alert = repo.reserve_alert(token, result, config, context)
    assert repo.presentation_detail("e", identity)
    repo.prune_diagnostics(token.timestamp + 1)
    assert repo.presentation_detail("e", identity) is None
    assert repo.get_state(f"telegram:scan:{scan}") is None
    assert repo.presentation_detail("a", alert)


@pytest.mark.parametrize(
    "view", ["overview", "performance", "funnel", "quality", "config", "near", "full"]
)
def test_health_pages_are_escaped_bounded_and_use_saved_context(config, repo, token, view):
    scan = repo.begin_scan(token.timestamp, configuration_context(config, 5))
    repo.record_evaluation(
        scan,
        changed(token, symbol="<bad>&"),
        RevivalResult(score=63, status="EARLY_WATCH", eligible=False),
        config,
        configuration_context(config, 5),
    )
    report = repo.health(token.timestamp - 1, now=token.timestamp + 1)
    text = health_page(report | {"schedule": {"running": True}}, view)
    if view != "full":
        assert "r5" in text
    assert "fixture-key" not in text
    assert_html(text)
    if view == "overview":
        assert "Scanning now" in text and "Data coverage" in text
        assert len(text.splitlines()) <= 28
    if view == "near":
        assert "&lt;bad&gt;&amp;" in text


def test_legacy_health_never_invents_configuration(config, repo):
    repo.begin_scan(time.time() - 1)
    text = health_page(repo.health(0))
    assert "legacy observation" in text and "No observations" in text
    assert "r0" not in text


async def test_old_alert_detail_callbacks_remain_readable_and_owner_only(config, tmp_path, token):
    config = live_owner(config, tmp_path)
    requests, loaded = [], []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    def details(kind, identity):
        loaded.append((kind, identity))
        return {
            "token": token.model_dump(mode="json"),
            "signal": RevivalResult(score=85, status="REVIVING", eligible=True).model_dump(),
            "configuration": configuration_context(config, 5),
        }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(RuntimeSettings(config), http, detail_provider=details)
        for sender in (99, 1234):
            update = callback("unused", sender=sender, date=1)
            update["callback_query"]["data"] = "a:5:why"
            await controls.handle_update(update)
            if sender == 99:
                assert not requests and not loaded
        assert loaded == [("a", 5)] and "r5" in requests[-1]["text"]
        for value in ("a:9999999999999999999999:why", "a:-1:full", "a:5:trade"):
            update["callback_query"]["data"] = value
            await controls.handle_update(update)
        assert loaded == [("a", 5)]


@pytest.mark.parametrize("kind", ["provider_error", "corrupt", "legacy"])
async def test_unavailable_saved_details_do_not_crash_controls(config, tmp_path, kind):
    config = live_owner(config, tmp_path)
    responses = []

    def handler(request):
        responses.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    def provider(*args):
        if kind == "provider_error":
            raise RuntimeError("fixture-bot-secret")
        return {} if kind == "legacy" else {"token": {}, "signal": {}}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(RuntimeSettings(config), http, detail_provider=provider)
        update = callback("unused", date=1)
        update["callback_query"]["data"] = "a:5:full"
        await controls.handle_update(update)
    assert "fixture-" not in responses[-1]["text"]
    assert any(word in responses[-1]["text"] for word in ("unavailable", "incomplete", "could not"))


async def test_home_settings_and_actions_require_owner_and_confirmation(config, tmp_path):
    config = live_owner(config, tmp_path)
    calls, requested = [], []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        runtime = RuntimeSettings(config)
        controls = TelegramControls(
            runtime, http, scan_request=lambda: requested.append(True) or "Queued"
        )
        await controls.handle_update(message())
        home = calls[-1]
        labels = [b["text"] for row in home["reply_markup"]["inline_keyboard"] for b in row]
        for label in (
            "📊 Status",
            "🩺 Health",
            "🎯 Strategy presets",
            "⚙️ Settings",
            "🔍 Near misses",
            "🔄 Scan now",
            "⏸ Pause alerts",
            "🧪 Test alert",
        ):
            assert label in labels
        assert "Solana" in home["text"] and "Robinhood" not in home["text"]
        await controls.handle_update(message("/settings"))
        settings = json.dumps(calls[-1]["reply_markup"])
        for category in ("Thresholds", "Structure", "Activity", "Chains", "Alerts", "Reset"):
            assert category in settings
        await controls.handle_update(callback("act:scan", sender=99))
        assert not requested
        await controls.handle_update(callback("act:scan"))
        assert not requested
        data = calls[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        update = callback("unused")
        update["callback_query"]["data"] = data
        await controls.handle_update(update)
        await controls.handle_update(update)
        assert requested == [True] and runtime.revision == 0
        await controls.handle_update(callback("act:test"))
        data = calls[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        update["callback_query"]["data"] = data
        await controls.handle_update(update)
        assert "TEST · Synthetic example" in calls[-1]["text"]
        assert len(requested) == 1 and runtime.effective().chains == ["sol"]


async def test_profile_uses_official_methods_and_multipart_photo(config, tmp_path):
    config.telegram_bot_token = SecretStr("fixture-bot")
    photo = tmp_path / "icon.png"
    photo.write_bytes(b"fixture-image")
    methods = []

    def handler(request):
        methods.append(request.url.path.rsplit("/", 1)[-1])
        if methods[-1] == "setMyProfilePhoto":
            assert b"attach://avatar" in request.content
            assert b"fixture-image" in request.content
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        results = await configure_profile(config, http, photo)
    assert methods == [
        "setMyName",
        "setMyDescription",
        "setMyShortDescription",
        "setMyCommands",
        "setMyProfilePhoto",
    ]
    assert set(results.values()) == {"updated"}


@pytest.mark.parametrize("payload", [[], None, {"ok": False}])
async def test_profile_failure_is_sanitized_and_stops_remaining_updates(config, payload):
    config.telegram_bot_token = SecretStr("fixture-bot")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await configure_profile(config, http)
    assert len(calls) == 1 and result["setMyName"] != "updated"
    assert "fixture-" not in json.dumps(result)


async def test_health_submenus_and_near_miss_inspection_use_saved_observations(
    config,
    repo,
    token,
    tmp_path,
):
    config = live_owner(config, tmp_path)
    context = configuration_context(config, 4)
    scan = repo.begin_scan(time.time(), context)
    repo.record_evaluation(
        scan,
        token,
        RevivalResult(score=63, status="EARLY_WATCH", eligible=False),
        config,
        context,
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(
            RuntimeSettings(config),
            http,
            health_provider=lambda: repo.health(0),
            detail_provider=repo.presentation_detail,
        )
        for view, heading in (
            ("overview", "Radar health"),
            ("performance", "Scan timing"),
            ("funnel", "Discovery &amp; delivery"),
            ("quality", "Data coverage"),
            ("outcomes", "Post-alert outcomes"),
            ("config", "Latest scan configuration"),
            ("near", "Near misses"),
            ("full", "Radar health"),
        ):
            await controls.handle_update(callback(f"health:{view}"))
            assert heading in requests[-1]["text"]
            assert_html(requests[-1]["text"])
            if view == "near":
                data = requests[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
                assert data.startswith("e:")
                update = callback("unused")
                update["callback_query"]["data"] = data
                await controls.handle_update(update)
                assert "63/100" in requests[-1]["text"] and "r4" in requests[-1]["text"]

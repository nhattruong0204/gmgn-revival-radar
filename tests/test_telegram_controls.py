import asyncio
import json
import stat
import time

import httpx
import pytest
from pydantic import SecretStr

from revival_radar.config import Settings
from revival_radar.runtime_settings import (
    ALLOWED_SETTINGS,
    BALANCED_PRESET,
    PRESETS,
    RuntimeSettings,
    SettingsChangeError,
    StaleRevisionError,
)
from revival_radar.telegram_controls import ControlsError, TelegramControls


@pytest.fixture
def controls_config(tmp_path):
    return Settings(
        _env_file=None,
        gmgn_api_key="fixture-gmgn-secret",
        telegram_bot_token="fixture-bot-secret",
        telegram_chat_id="1234",
        telegram_owner_id=1234,
        enabled_chains="sol,base",
        database_path=tmp_path / "radar.db",
    )


@pytest.fixture
def runtime(controls_config):
    return RuntimeSettings(controls_config)


@pytest.fixture
async def bot(runtime):
    requests = []

    def handler(request):
        requests.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        yield TelegramControls(runtime, http), requests


def message(text="/menu", *, sender=1234, chat=1234, kind="private", date=None):
    return {
        "update_id": 1,
        "message": {
            "date": int(time.time()) if date is None else date,
            "text": text,
            "from": {"id": sender},
            "chat": {"id": chat, "type": kind},
        },
    }


def callback(action, revision=0, **kwargs):
    source = message(**kwargs)["message"]
    return {
        "update_id": 2,
        "callback_query": {
            "id": "callback-id",
            "from": source["from"],
            "message": source,
            "data": f"r:{revision}:{action}",
        },
    }


def last_message(requests):
    return [payload for method, payload in requests if method == "sendMessage"][-1]


def confirm_data(requests):
    return last_message(requests)["reply_markup"]["inline_keyboard"][0][0]["callback_data"]


async def confirm(bot):
    controls, requests = bot
    data = confirm_data(requests)
    update = callback("unused")
    update["callback_query"]["data"] = data
    await controls.handle_update(update)


def test_runtime_roundtrip_only_nonsecret_overrides(runtime, controls_config):
    runtime.apply(BALANCED_PRESET)
    runtime.set_offset(501)
    raw = runtime.path.read_text()
    data = json.loads(raw)
    assert set(data) == {"revision", "offset", "overrides"}
    assert set(data["overrides"]) <= ALLOWED_SETTINGS
    assert "fixture-" not in raw and "telegram_bot_token" not in raw and "gmgn_api_key" not in raw
    assert stat.S_IMODE(runtime.path.stat().st_mode) == 0o600
    resumed = RuntimeSettings(controls_config)
    assert resumed.revision == 1 and resumed.offset == 501
    assert resumed.effective().alert_score_threshold == 65
    assert isinstance(resumed.effective().gmgn_api_key, SecretStr)
    assert resumed.effective().gmgn_api_key.get_secret_value() == "fixture-gmgn-secret"
    resumed.reset(expected_revision=1)
    assert resumed.revision == 2 and resumed.offset == 501
    assert resumed.effective().alert_score_threshold == controls_config.alert_score_threshold
    assert json.loads(resumed.path.read_text())["overrides"] == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"telegram_owner_id": 9},
        {"telegram_bot_token": "evil"},
        {"database_path": "/tmp/evil"},
        {"min_market_cap": 90_000_000},
        {"alert_score_threshold": 101},
        {"min_liquidity": -1},
        {"min_volume_1h": float("nan")},
        {"enabled_chains": "unknown"},
        {"enabled_chains": ""},
        {"scan_interval_seconds": 1000},
        {"daily_summary_hour": 24},
        {"weights": {"telegram_bot_token": "bad-secret"}},
    ],
)
def test_runtime_rejects_invalid_or_sensitive_changes(runtime, changes):
    with pytest.raises(SettingsChangeError) as exc:
        runtime.apply(changes)
    assert "fixture-" not in str(exc.value) and "bad-secret" not in str(exc.value)
    assert runtime.revision == 0 and not runtime.path.exists()


def test_runtime_stale_and_atomic_write_failure(runtime, monkeypatch):
    runtime.apply({"alert_score_threshold": 64}, expected_revision=0)
    with pytest.raises(StaleRevisionError):
        runtime.apply({"alert_score_threshold": 61}, expected_revision=0)
    previous = runtime.path.read_text()

    def failure(*args):
        raise OSError("fixture-gmgn-secret")

    monkeypatch.setattr("revival_radar.runtime_settings.os.replace", failure)
    with pytest.raises(SettingsChangeError, match="could not be saved"):
        runtime.apply({"alert_score_threshold": 61})
    assert runtime.effective().alert_score_threshold == 64
    assert runtime.revision == 1 and runtime.path.read_text() == previous
    assert not list(runtime.path.parent.glob(".radar-controls-*"))


def test_runtime_live_alerts_need_delivery_credentials(controls_config):
    controls_config.telegram_chat_id = ""
    runtime = RuntimeSettings(controls_config)
    with pytest.raises(SettingsChangeError, match="credentials"):
        runtime.preview({"dry_run": False})


def test_runtime_corrupt_file_safe_error(runtime, controls_config):
    runtime.path.write_text('{"telegram_bot_token": "fixture-bot-secret"}')
    with pytest.raises(SettingsChangeError) as exc:
        RuntimeSettings(controls_config)
    assert "fixture-bot-secret" not in str(exc.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sender": 2},
        {"chat": 2},
        {"chat": -1234, "kind": "group"},
        {"kind": "supergroup"},
        {"sender": "1234"},
        {"chat": True},
    ],
)
async def test_owner_and_private_chat_required(bot, kwargs):
    controls, requests = bot
    await controls.handle_update(message(**kwargs))
    await controls.handle_update(callback("t:dry_run", **kwargs))
    assert requests == [] and controls.runtime.revision == 0


async def test_owner_menu_and_callback_sizes(bot):
    controls, requests = bot
    await controls.handle_update(message())
    assert last_message(requests)["chat_id"] == 1234
    for menu in ("filters", "structure", "alerts", "discovery", "chains", "assets", "presets"):
        await controls.handle_update(callback(f"m:{menu}"))
    for _, payload in requests:
        for row in payload.get("reply_markup", {}).get("inline_keyboard", []):
            assert all(len(button["callback_data"].encode()) <= 64 for button in row)
    assert "fixture-" not in json.dumps([payload for _, payload in requests])


@pytest.mark.parametrize("name", list(PRESETS))
async def test_preset_requires_confirmation(bot, name):
    controls, requests = bot
    controls.runtime.apply({"alert_score_threshold": 59})
    await controls.handle_update(callback(f"p:{name}", revision=1))
    assert controls.runtime.revision == 1
    assert "Confirm within" in last_message(requests)["text"]
    await confirm(bot)
    assert controls.runtime.revision == 2
    effective = controls.runtime.effective()
    for key, value in PRESETS[name].items():
        assert getattr(effective, key) == value
    assert effective.discovery_limit == 20 and effective.watchlist_limit == 100


async def test_stale_confirmation_and_one_use(bot):
    controls, requests = bot
    await controls.handle_update(callback("p:balanced"))
    old = confirm_data(requests)
    controls.runtime.apply({"min_holders": 99})
    update = callback("unused")
    update["callback_query"]["data"] = old
    await controls.handle_update(update)
    assert controls.runtime.revision == 1
    assert "Settings changed" in last_message(requests)["text"]
    await controls.handle_update(callback("t:alerts_paused", revision=1))
    old = confirm_data(requests)
    await confirm(bot)
    update["callback_query"]["data"] = old
    await controls.handle_update(update)
    assert controls.runtime.revision == 2 and controls.runtime.effective().alerts_paused


async def test_custom_numeric_preview_validation_and_confirm(bot):
    controls, requests = bot
    await controls.handle_update(callback("n:alert_score_threshold"))
    await controls.handle_update(message("101"))
    assert "Invalid number" in last_message(requests)["text"]
    assert controls.runtime.revision == 0
    await controls.handle_update(message("66.5"))
    assert "Invalid number" in last_message(requests)["text"]
    await controls.handle_update(message("66"))
    assert controls.runtime.revision == 0 and "66" in last_message(requests)["text"]
    await confirm(bot)
    assert controls.runtime.effective().alert_score_threshold == 66


async def test_new_input_invalidates_old_confirmation(bot):
    controls, requests = bot
    await controls.handle_update(callback("p:balanced"))
    old = confirm_data(requests)
    await controls.handle_update(callback("n:min_holders"))
    update = callback("unused")
    update["callback_query"]["data"] = old
    await controls.handle_update(update)
    assert controls.runtime.revision == 0
    assert "unavailable" in last_message(requests)["text"]


async def test_malformed_nonce_is_rejected(bot):
    controls, requests = bot
    await controls.handle_update(callback("p:balanced"))
    await controls.handle_update(callback("yes:é"))
    assert "unavailable" in last_message(requests)["text"]
    assert controls.runtime.revision == 0


async def test_expired_confirmation_never_applies(bot):
    controls, requests = bot
    await controls.handle_update(callback("p:balanced"))
    controls._proposal.expires = time.monotonic() - 1
    await confirm(bot)
    assert controls.runtime.revision == 0
    assert "expired" in last_message(requests)["text"]


async def test_preview_splits_all_changes_before_confirmation(bot, monkeypatch):
    from revival_radar.telegram_controls import LABELS

    controls, requests = bot
    changes = PRESETS["broad"]
    for key in changes:
        monkeypatch.setitem(LABELS, key, key + "x" * 350)
    before = controls.runtime.public_values()
    await controls.handle_update(callback("p:broad"))
    messages = [payload for method, payload in requests if method == "sendMessage"]
    assert len(messages) > 1
    assert all(len(payload["text"].encode("utf-16-le")) // 2 <= 4096 for payload in messages)
    assert all("reply_markup" not in payload for payload in messages[:-1])
    complete = "\n".join(payload["text"] for payload in messages)
    for key, value in changes.items():
        if before[key] != value:
            assert LABELS[key] in complete
    await confirm(bot)
    assert controls.runtime.revision == 1


async def test_last_chain_cannot_be_disabled(bot):
    controls, requests = bot
    controls.runtime.apply({"enabled_chains": "sol"})
    await controls.handle_update(callback("c:sol", revision=1))
    assert controls.runtime.effective().chains == ["sol"]
    assert controls._proposal is None
    assert "Invalid setting" in last_message(requests)["text"]


async def test_stale_backlog_and_missing_timestamp_never_mutate(bot):
    controls, requests = bot
    old = controls._boot_time - 5
    await controls.handle_update(message("/menu", date=old))
    assert requests == []
    await controls.handle_update(callback("p:balanced", date=old))
    assert "earlier session" in last_message(requests)["text"]
    update = callback("p:balanced")
    del update["callback_query"]["message"]["date"]
    await controls.handle_update(update)
    assert controls._proposal is None and controls.runtime.revision == 0


async def test_pause_chains_assets_summary_and_reset_require_confirm(bot):
    controls, _ = bot
    for action, key in [
        ("t:alerts_paused", "alerts_paused"),
        ("t:daily_summary_enabled", "daily_summary_enabled"),
        ("t:exclude_stablecoins", "exclude_stablecoins"),
        ("t:dry_run", "dry_run"),
        ("c:base", "enabled_chains"),
    ]:
        before = getattr(controls.runtime.effective(), key)
        revision = controls.runtime.revision
        await controls.handle_update(callback(action, revision=revision))
        assert getattr(controls.runtime.effective(), key) == before
        await confirm(bot)
        assert getattr(controls.runtime.effective(), key) != before
    await controls.handle_update(callback("reset", revision=controls.runtime.revision))
    await confirm(bot)
    assert controls.runtime.effective().enabled_chains == "sol,base"
    assert not controls.runtime.effective().alerts_paused


async def test_health_html_escaping_and_secret_redaction(bot):
    controls, requests = bot
    controls.health_provider = lambda: "<token>& fixture-gmgn-secret fixture-bot-secret"
    await controls.handle_update(message("/health"))
    text = last_message(requests)["text"]
    assert "&lt;token&gt;&amp;" in text and "fixture-" not in text


async def test_health_mapping_uses_current_report_timezone(bot, monkeypatch):
    controls, requests = bot
    health = {"some": "metrics", "timezone": "UTC"}
    controls.health_provider = lambda: health

    def formatter(report):
        assert report["timezone"] == "Asia/Bangkok"
        return "<b>Health</b>"

    monkeypatch.setattr("revival_radar.diagnostics.format_health", formatter)
    await controls.handle_update(message("/health"))
    assert health["timezone"] == "UTC"
    assert last_message(requests)["text"] == "<b>Health</b>"


@pytest.mark.parametrize("status", [401, 403, 409])
async def test_fatal_polling_errors_are_safe(runtime, status, caplog):
    methods = []

    def handler(request):
        methods.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            status,
            json={
                "ok": False,
                "error_code": status,
                "description": "fixture-bot-secret",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(runtime, http)
        with pytest.raises(ControlsError) as exc:
            await controls.run()
    assert "fixture-" not in str(exc.value) and "fixture-" not in caplog.text
    assert methods == ["getUpdates"]
    if status == 409:
        assert "webhook" in controls.status


async def test_polling_persists_offsets_before_processing(runtime):
    bodies = []
    controls = None

    def handler(request):
        payload = json.loads(request.content)
        if request.url.path.endswith("getUpdates"):
            bodies.append(payload)
            if len(bodies) == 1:
                update = message()
                update["update_id"] = 51
                return httpx.Response(200, json={"ok": True, "result": [update]})
            controls.stop()
            return httpx.Response(200, json={"ok": True, "result": []})
        assert json.loads(runtime.path.read_text())["offset"] == 52
        return httpx.Response(200, json={"ok": True, "result": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(runtime, http)
        await controls.run()
    assert bodies[1]["offset"] == 52 and runtime.offset == 52


async def test_failed_offset_persistence_stops_before_processing(runtime, monkeypatch):
    methods = []

    def handler(request):
        methods.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"ok": True, "result": [message()]})

    def fail(*args):
        raise SettingsChangeError("Runtime settings could not be saved.")

    monkeypatch.setattr(runtime, "set_offset", fail)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(runtime, http)
        with pytest.raises(ControlsError, match="could not be saved"):
            await controls.run()
    assert "could not be saved" in controls.status
    assert methods == ["getUpdates"]


@pytest.mark.parametrize("cancel", [True, False])
async def test_stop_and_cancellation_interrupt_long_poll(runtime, cancel):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(runtime, http)
        task = asyncio.create_task(controls.run())
        await asyncio.wait_for(started.wait(), timeout=1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            controls.stop()
            await asyncio.wait_for(task, timeout=1)
    assert cancelled.is_set()


async def test_transient_errors_backoff_without_leaking(runtime, monkeypatch, caplog):
    delays = []
    controls = None

    async def no_wait(delay):
        delays.append(delay)
        controls.stop()

    monkeypatch.setattr("revival_radar.telegram_controls.asyncio.sleep", no_wait)

    def handler(request):
        raise httpx.ReadTimeout("fixture-bot-secret in request URL")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        controls = TelegramControls(runtime, http)
        await controls.run()
    assert delays == [1] and "fixture-" not in caplog.text


@pytest.mark.parametrize(
    "owner,chat,expected", [(0, "1234", 1234), (0, "-99", 0), (0, "@channel", 0), (55, "-99", 55)]
)
async def test_owner_resolution(controls_config, owner, chat, expected):
    controls_config.telegram_owner_id = owner
    controls_config.telegram_chat_id = chat
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as http:
        controls = TelegramControls(RuntimeSettings(controls_config), http)
    assert controls.owner_id == expected
    assert controls.enabled == bool(expected)

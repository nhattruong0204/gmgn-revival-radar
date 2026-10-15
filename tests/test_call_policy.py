"""Published-call policy (issue #70): stage gate and evidence-based re-alerts."""

import json

import httpx
import pytest
from pydantic import SecretStr

from revival_radar.analysis.scoring import STAGE_ORDER, below_min_stage
from revival_radar.clients.telegram import TelegramClient
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.demo import DemoSource
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.runtime_settings import ALLOWED_SETTINGS, RuntimeSettings, SettingsChangeError
from revival_radar.scanner import Scanner
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig
from revival_radar.web_commands import CommandStore, WebCommandWorker

from .conftest import changed
from .test_telegram_controls import bot as bot  # noqa: F401
from .test_telegram_controls import callback, confirm, last_message
from .test_telegram_controls import controls_config as controls_config  # noqa: F401
from .test_telegram_controls import runtime as runtime  # noqa: F401
from .test_web_public import NOW

STAGES = STAGE_ORDER[1:]


def call(score=80, status="REVIVING", **structure):
    return RevivalResult(
        score=score,
        status=status,
        eligible=True,
        structure=Structure(available=True, base_detected=True, **structure),
    )


def live(config):
    config.dry_run = False
    config.realert_requires_new_evidence = True
    config.alert_cooldown_hours = 1
    return config


def publish(repo, token, result, config, *, hours=0, price=None):
    values = {"timestamp": token.timestamp + hours * 3600}
    if price is not None:
        values["price"] = price
    alert = repo.reserve_alert(changed(token, **values), result, config)
    if alert:
        repo.finish_alert(alert, "sent", 1)
    return alert


# --- settings -------------------------------------------------------------


def test_defaults_preserve_current_behaviour():
    config = Settings(_env_file=None)
    assert config.alert_min_stage == "WATCH"
    assert config.realert_requires_new_evidence is False
    assert {"alert_min_stage", "realert_requires_new_evidence"} <= ALLOWED_SETTINGS


@pytest.mark.parametrize("value", ["", "watch", "IGNORE", "MOON", 3, None])
def test_invalid_stage_is_rejected(runtime, value):
    with pytest.raises(SettingsChangeError):
        runtime.apply({"alert_min_stage": value})
    assert runtime.revision == 0


def test_invalid_flag_is_rejected(runtime):
    with pytest.raises(SettingsChangeError):
        runtime.apply({"realert_requires_new_evidence": "maybe"})


def test_settings_persist_bump_revision_and_enter_snapshot(runtime, controls_config):
    runtime.apply({"alert_min_stage": "EARLY_REVIVAL", "realert_requires_new_evidence": True})
    assert runtime.revision == 1
    reloaded = RuntimeSettings(controls_config, runtime.path)
    effective = reloaded.effective()
    assert effective.alert_min_stage == "EARLY_REVIVAL"
    assert effective.realert_requires_new_evidence is True
    settings = configuration_context(effective, reloaded.revision)["settings"]
    assert settings["alert_min_stage"] == "EARLY_REVIVAL"
    assert settings["realert_requires_new_evidence"] is True
    assert runtime.public_values()["alert_min_stage"] == "EARLY_REVIVAL"


async def test_call_record_saves_policy_in_force(repo, token, config):
    config.dry_run = False
    config.alert_min_stage = "EARLY_REVIVAL"
    context = configuration_context(config, 7)
    alert = repo.reserve_alert(token, call(status="REVIVING"), config, context)
    detail = repo.presentation_detail("a", alert)
    saved = detail["configuration"]["settings"]
    assert saved["alert_min_stage"] == "EARLY_REVIVAL"
    assert saved["realert_requires_new_evidence"] is False


# --- stage gate -----------------------------------------------------------


@pytest.mark.parametrize("stage", STAGE_ORDER)
@pytest.mark.parametrize("score", [0, 39, 40, 74, 75, 100])
@pytest.mark.parametrize("eligible", [True, False])
def test_default_stage_gate_never_changes_a_decision(stage, score, eligible):
    # Default WATCH is "no gate": the decision is exactly the pre-policy rule.
    assert not below_min_stage(stage, "WATCH")
    before = eligible and score >= 75
    config = Settings(_env_file=None)
    after = (
        eligible
        and score >= config.alert_score_threshold
        and not below_min_stage(stage, config.alert_min_stage)
    )
    assert after == before


@pytest.mark.parametrize(
    ("stage", "blocked"),
    [
        ("IGNORE", True),
        ("WATCH", True),
        ("EARLY_REVIVAL", False),
        ("REVIVING", False),
        ("STRONG_REVIVAL", False),
        ("CONFIRMED_REVIVAL", False),
    ],
)
def test_early_revival_minimum_blocks_watch_only(stage, blocked):
    assert below_min_stage(stage, "EARLY_REVIVAL") is blocked


def test_unknown_status_is_below_any_real_gate():
    assert below_min_stage(None, "EARLY_REVIVAL")
    assert not below_min_stage(None, "WATCH")


def test_reserve_alert_enforces_stage_gate(repo, token, config):
    config.dry_run = False
    config.alert_min_stage = "EARLY_REVIVAL"
    assert repo.reserve_alert(token, call(status="WATCH"), config) is None
    assert repo.last_block_reason == "stage_below_minimum"
    assert repo.reserve_alert(token, call(status="EARLY_REVIVAL"), config)
    assert repo.last_block_reason is None


async def run_scan(config, repo, monkeypatch, status, score=80):
    result = RevivalResult(score=score, status=status, eligible=True)
    monkeypatch.setattr("revival_radar.scanner.score_token", lambda *a, **k: result)
    source = DemoSource()
    for snapshot in source.histories():
        repo.save_snapshot(snapshot)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        )
    ) as http:
        return await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()


@pytest.mark.parametrize("status", ["WATCH", "EARLY_REVIVAL", "CONFIRMED_REVIVAL"])
async def test_scanner_defaults_still_publish_watch(config, repo, monkeypatch, status):
    config.dry_run = False
    config.telegram_bot_token = SecretStr("fixture-token")
    config.telegram_chat_id = "fixture-chat"
    report = await run_scan(config, repo, monkeypatch, status)
    assert report.potential_alerts >= 1 and report.sent >= 1
    assert all("stage_below_minimum" not in counts for counts in report.funnel.values())


async def test_scanner_counts_below_stage_as_distinct_skip(config, repo, monkeypatch):
    config.alert_min_stage = "EARLY_REVIVAL"
    report = await run_scan(config, repo, monkeypatch, "WATCH")
    assert report.processed >= 1 and report.potential_alerts == 0 and report.sent == 0
    assert report.funnel["sol"]["stage_below_minimum"] == report.processed
    allowed = await run_scan(config, repo, monkeypatch, "EARLY_REVIVAL")
    assert allowed.potential_alerts == allowed.processed
    assert "stage_below_minimum" not in allowed.funnel["sol"]


# --- re-alert evidence ----------------------------------------------------


def first_call(repo, token, config, result=None, price=1.0):
    alert = publish(repo, token, result or call(), config, price=price)
    assert alert
    return alert


def test_flag_off_keeps_old_behaviour(repo, token, config):
    config.dry_run = False
    config.alert_cooldown_hours = 1
    first_call(repo, token, config)
    # Lower price, same stage, no new structure: still re-alerts after cooldown.
    assert publish(repo, token, call(), config, hours=2, price=0.5)
    assert repo.last_block_reason is None


def test_blocked_without_new_evidence(repo, token, config):
    live(config)
    first_call(repo, token, config)
    assert publish(repo, token, call(), config, hours=2, price=2.0) is None
    assert repo.last_block_reason == "realert_blocked"


def test_allowed_on_stage_upgrade_with_higher_price(repo, token, config):
    live(config)
    first_call(repo, token, config)
    assert publish(repo, token, call(status="STRONG_REVIVAL"), config, hours=2, price=1.5)


def test_allowed_on_new_breakout_with_higher_price(repo, token, config):
    live(config)
    first_call(repo, token, config)
    assert publish(repo, token, call(breakout_detected=True), config, hours=2, price=1.5)


@pytest.mark.parametrize("event", ["higher_high_detected", "retest_detected"])
def test_other_structural_events_count(repo, token, config, event):
    live(config)
    first_call(repo, token, config)
    assert publish(repo, token, call(**{event: True}), config, hours=2, price=1.5)


def test_structure_already_true_at_previous_call_is_not_new(repo, token, config):
    live(config)
    first_call(repo, token, config, call(breakout_detected=True))
    assert publish(repo, token, call(breakout_detected=True), config, hours=2, price=1.5) is None


@pytest.mark.parametrize("price", [1.0, 0.9])
def test_blocked_when_price_not_higher(repo, token, config, price):
    live(config)
    first_call(repo, token, config)
    assert (
        publish(repo, token, call(status="CONFIRMED_REVIVAL"), config, hours=2, price=price) is None
    )
    assert repo.last_block_reason == "realert_blocked"


def test_blocked_when_current_price_unknown(repo, token, config):
    live(config)
    first_call(repo, token, config)
    unknown = changed(token, timestamp=token.timestamp + 7200, price=None)
    assert repo.reserve_alert(unknown, call(status="CONFIRMED_REVIVAL"), config) is None
    assert repo.last_block_reason == "realert_blocked"


def test_blocked_when_previous_price_unknown(repo, token, config):
    live(config)
    alert = repo.reserve_alert(changed(token, price=None), call(), config)
    repo.finish_alert(alert, "sent", 1)
    assert (
        publish(repo, token, call(status="CONFIRMED_REVIVAL"), config, hours=2, price=5.0) is None
    )


def test_score_increase_and_cooldown_still_apply(repo, token, config):
    live(config)
    config.alert_cooldown_hours = 6
    first_call(repo, token, config, call(score=80))
    # Within cooldown the +10 score rule still blocks even with new evidence.
    better = call(score=85, status="STRONG_REVIVAL")
    assert publish(repo, token, better, config, hours=1, price=2.0) is None
    assert publish(repo, token, call(score=95, status="STRONG_REVIVAL"), config, hours=1, price=2.0)


def test_first_call_for_a_token_is_unaffected(repo, token, config):
    live(config)
    assert publish(repo, token, call(), config, price=None)


# --- owner controls -------------------------------------------------------


async def test_telegram_round_trip(bot):
    controls, requests = bot
    await controls.handle_update(callback("m:alerts"))
    keyboard = last_message(requests)["reply_markup"]["inline_keyboard"]
    data = [button["callback_data"] for row in keyboard for button in row]
    assert "r:0:g:EARLY_REVIVAL" in data and "r:0:t:realert_requires_new_evidence" in data
    await controls.handle_update(callback("g:EARLY_REVIVAL"))
    assert "Min. call stage" in last_message(requests)["text"]
    await confirm(bot)
    assert controls.runtime.effective().alert_min_stage == "EARLY_REVIVAL"
    await controls.handle_update(callback("t:realert_requires_new_evidence", revision=1))
    await confirm(bot)
    runtime = controls.runtime
    assert runtime.effective().realert_requires_new_evidence is True and runtime.revision == 2


async def test_telegram_rejects_unknown_stage(bot):
    controls, _ = bot
    await controls.handle_update(callback("g:MOON"))
    assert controls.runtime.revision == 0


async def test_owner_web_settings_round_trip(bot, repo):
    controls, _ = bot
    runtime = controls.runtime
    worker = WebCommandWorker(controls.controller, repo)
    store = CommandStore(repo.db)
    changes = {"alert_min_stage": "REVIVING", "realert_requires_new_evidence": True}
    store.enqueue(
        "owner:x", "p", "preview", {"desired_action": "settings", "changes": changes}, 0, 1000
    )
    assert await worker.process_one(1000)
    preview = store.get("owner:x", "p")["result"]
    store.enqueue("owner:x", "c", "confirm", {"preview_id": preview["preview_id"]}, 0, 1001)
    assert await worker.process_one(1001)
    effective = runtime.effective()
    assert effective.alert_min_stage == "REVIVING" and runtime.revision == 1
    assert effective.realert_requires_new_evidence is True
    store.enqueue(
        "owner:x",
        "bad",
        "preview",
        {"desired_action": "settings", "changes": {"alert_min_stage": "MOON"}},
        0,
        1002,
    )
    assert await worker.process_one(1002)
    assert "preview_id" not in (store.get("owner:x", "bad")["result"] or {})


# --- public policy summary ------------------------------------------------


@pytest.fixture
async def web(repo):
    database = repo.db.execute("PRAGMA database_list").fetchone()[2]
    app = create_app(WebConfig(database_path=database, web_cache_seconds=0), clock=lambda: NOW)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def finished_scan(repo, context, at=NOW - 100):
    scan = repo.begin_scan(at - 10, context)
    repo.db.execute("UPDATE scan_runs SET finished=? WHERE id=?", (at, scan))
    repo.db.commit()


async def test_meta_publishing_policy_from_saved_scan(web, repo, config):
    config.alert_min_stage = "EARLY_REVIVAL"
    config.realert_requires_new_evidence = True
    finished_scan(repo, configuration_context(config, 9))
    body = (await web.get("/api/v1/meta")).json()
    assert body["publishing_policy"] == {
        "min_stage": "EARLY_REVIVAL",
        "score_threshold": 75,
        "realert_requires_new_evidence": True,
        "revision": 9,
    }
    assert "gmgn_api_key" not in json.dumps(body) and "settings" not in body


async def test_meta_policy_for_scan_saved_before_policy_existed(web, repo):
    finished_scan(repo, {"revision": 3, "settings": {"alert_score_threshold": 40}})
    policy = (await web.get("/api/v1/meta")).json()["publishing_policy"]
    assert policy["min_stage"] == "WATCH" and policy["score_threshold"] == 40
    assert policy["realert_requires_new_evidence"] is False


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"settings": {}},
        {"settings": {"alert_score_threshold": "x"}},
        {"settings": {"alert_score_threshold": 75, "alert_min_stage": "MOON"}},
    ],
)
async def test_meta_policy_unknown_when_not_saved(web, repo, context):
    if context is not None:
        finished_scan(repo, context)
    assert (await web.get("/api/v1/meta")).json()["publishing_policy"] is None

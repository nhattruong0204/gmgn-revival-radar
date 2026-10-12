import html
import json
import sys
import time
from types import ModuleType

import httpx
import pytest

from revival_radar.config import Settings
from revival_radar.models.signal import RevivalResult
from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.telegram_controls import MARKET_REPORT_TIMEFRAMES, TelegramControls

OWNER = 1234


@pytest.fixture
def market_runtime(tmp_path):
    return RuntimeSettings(
        Settings(
            _env_file=None,
            telegram_bot_token="private-telegram-secret",
            telegram_chat_id=str(OWNER),
            telegram_owner_id=OWNER,
            gmgn_api_key="private-gmgn-secret",
            helius_api_token="private-helius<&secret",
            enabled_chains="sol",
            database_path=tmp_path / "radar.db",
        )
    )


@pytest.fixture
def report_renderer(monkeypatch):
    # Isolate control routing from repository calculation and HTML presentation;
    # those components have their own source-data and rendering tests.
    module = ModuleType("revival_radar.market_report")
    calls = []

    def page(report):
        calls.append(("page", report))
        return "<b>Recorded market report</b>\n" + html.escape(report.get("title", ""))

    def buttons(report):
        calls.append(("buttons", report))
        return [[{"text": "Saved snapshot", "callback_data": "e:42:full"}]]

    module.market_report_page = page
    module.market_report_buttons = buttons
    monkeypatch.setitem(sys.modules, "revival_radar.market_report", module)
    return calls


@pytest.fixture
async def market_bot(market_runtime):
    requests = []

    def handler(request):
        requests.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        yield TelegramControls(market_runtime, http), requests


def message(text="/market", *, sender=OWNER, chat=OWNER, kind="private", date=None):
    return {
        "update_id": 1,
        "message": {
            "date": int(time.time()) if date is None else date,
            "text": text,
            "from": {"id": sender},
            "chat": {"id": chat, "type": kind},
        },
    }


def callback(action, *, revision=0, **kwargs):
    source = message(**kwargs)["message"]
    source["message_id"] = 99
    return {
        "update_id": 2,
        "callback_query": {
            "id": "market-callback-id",
            "from": source["from"],
            "message": source,
            "data": f"r:{revision}:{action}",
        },
    }


def sent_messages(requests):
    return [payload for method, payload in requests if method in {"sendMessage", "editMessageText"}]


def last_message(requests):
    return sent_messages(requests)[-1]


def flat_buttons(payload):
    return [button for row in payload["reply_markup"]["inline_keyboard"] for button in row]


async def test_main_menu_has_one_market_report_button(market_bot):
    controls, requests = market_bot
    await controls.handle_update(message("/menu"))
    buttons = flat_buttons(last_message(requests))
    report_buttons = [button for button in buttons if "Market report" in button["text"]]
    assert len(report_buttons) == 1
    assert report_buttons[0]["callback_data"] == "r:0:market"
    assert any(button["callback_data"] == "r:0:health" for button in buttons)
    assert any(button["callback_data"] == "r:0:act:scan" for button in buttons)


@pytest.mark.parametrize("trigger", ["button", "/market", "/report", "/market@radar_bot"])
async def test_market_report_asks_for_timeframe_before_reading_data(market_bot, trigger):
    controls, requests = market_bot
    report_calls = []
    controls.market_report_provider = lambda hours: report_calls.append(hours)
    update = callback("market") if trigger == "button" else message(trigger)
    await controls.handle_update(update)
    payload = last_message(requests)
    buttons = flat_buttons(payload)
    assert "Choose a timeframe" in payload["text"]
    assert [(button["text"], button["callback_data"]) for button in buttons[:-1]] == [
        (label, f"r:0:market:{hours}") for label, hours in MARKET_REPORT_TIMEFRAMES
    ]
    assert buttons[-1]["callback_data"] == "r:0:m:home"
    assert report_calls == []
    assert controls.runtime.revision == 0
    assert controls._proposal is None
    assert not controls.runtime.path.exists()


@pytest.mark.parametrize("hours", ["1", "4", "12", "24", "72", "168"])
async def test_allowed_timeframes_read_provider_and_edit_existing_report(
    market_bot, report_renderer, hours
):
    controls, requests = market_bot
    report_calls = []
    report = {"title": "JEANPHIL & <tracked>", "window_hours": float(hours)}

    def provider(duration):
        report_calls.append(duration)
        return report

    controls.market_report_provider = provider
    controls.scan_request = lambda: pytest.fail("Market report requested a scan")
    await controls.handle_update(callback(f"market:{hours}"))
    assert report_calls == [float(hours)]
    assert report_renderer == [("page", report), ("buttons", report)]
    assert requests[-1][0] == "editMessageText"
    payload = last_message(requests)
    assert payload["message_id"] == 99
    assert "JEANPHIL &amp; &lt;tracked&gt;" in payload["text"]
    buttons = flat_buttons(payload)
    assert buttons[0]["callback_data"] == "e:42:full"
    assert buttons[-2]["callback_data"] == "r:0:market"
    assert buttons[-1]["callback_data"] == "r:0:m:home"
    assert controls.runtime.revision == 0 and not controls.runtime.path.exists()
    assert controls._proposal is None


async def test_async_market_report_provider_works_while_alerts_are_paused(
    market_bot, report_renderer
):
    controls, requests = market_bot
    controls.runtime.apply({"alerts_paused": True})
    previous_revision = controls.runtime.revision
    previous_settings = controls.runtime.path.read_bytes()
    calls = []

    async def provider(hours):
        calls.append(hours)
        return {"title": "Paused alerts; recorded observations"}

    controls.market_report_provider = provider
    controls.scan_request = lambda: pytest.fail("Manual report requested a scan")
    await controls.handle_update(callback("market:24", revision=previous_revision))
    assert calls == [24.0]
    assert "Recorded market report" in last_message(requests)["text"]
    assert controls.runtime.effective().alerts_paused is True
    assert controls.runtime.revision == previous_revision
    assert controls.runtime.path.read_bytes() == previous_settings
    assert controls._proposal is None


@pytest.mark.parametrize(
    "duration", ["", "0", "2", "169", "99999999", "24.0", "-1", "nan", "1e2", "٢٤", "24:0"]
)
async def test_invalid_timeframes_are_rejected_without_reading_or_changing_settings(
    market_bot, duration
):
    controls, requests = market_bot
    controls.market_report_provider = lambda hours: pytest.fail("Invalid duration queried data")
    await controls.handle_update(callback(f"market:{duration}"))
    assert "supported timeframe" in last_message(requests)["text"]
    assert controls.runtime.revision == 0 and not controls.runtime.path.exists()
    assert controls._proposal is None


@pytest.mark.parametrize(
    "identity",
    [
        {"sender": 999},
        {"chat": 999},
        {"kind": "group"},
        {"sender": "1234"},
        {"chat": "1234"},
    ],
)
async def test_only_owner_private_chat_can_request_reports(market_bot, identity):
    controls, requests = market_bot
    controls.market_report_provider = lambda hours: pytest.fail("Unauthorized provider call")
    await controls.handle_update(message("/market", **identity))
    await controls.handle_update(callback("market:24", **identity))
    assert requests == []


async def test_stale_report_callbacks_require_a_new_menu(market_bot):
    controls, requests = market_bot
    controls.market_report_provider = lambda hours: pytest.fail("Stale callback queried data")
    await controls.handle_update(callback("market:24", date=controls._boot_time - 1))
    assert "earlier session" in last_message(requests)["text"]
    await controls.handle_update(callback("market:24", revision=99))
    assert "Settings changed" in last_message(requests)["text"]
    assert controls.runtime.revision == 0 and not controls.runtime.path.exists()


@pytest.mark.parametrize("kind", ["sync", "async", "invalid_result"])
async def test_provider_failure_has_fixed_safe_error(market_bot, kind):
    controls, requests = market_bot

    def failure(hours):
        raise RuntimeError("private-gmgn-secret private-helius<&secret raw database payload")

    async def async_failure(hours):
        return failure(hours)

    controls.market_report_provider = (
        failure
        if kind == "sync"
        else (async_failure if kind == "async" else lambda hours: "raw private result")
    )
    await controls.handle_update(callback("market:24"))
    payload = last_message(requests)
    assert payload["text"] == "The market report could not be loaded. Try again later."
    assert "private" not in json.dumps(payload)
    assert controls.runtime.revision == 0 and not controls.runtime.path.exists()


async def test_report_unavailable_without_provider(market_bot):
    controls, requests = market_bot
    await controls.handle_update(callback("market:4"))
    assert "not available" in last_message(requests)["text"]
    assert controls.runtime.revision == 0


async def test_report_redacts_helius_gmgn_and_telegram_tokens(market_bot, report_renderer):
    controls, requests = market_bot
    controls.market_report_provider = lambda hours: {
        "title": "private-helius<&secret private-gmgn-secret private-telegram-secret"
    }
    await controls.handle_update(callback("market:1"))
    text = last_message(requests)["text"]
    assert "private" not in text
    assert text.count("[redacted]") == 3


async def test_market_command_clears_pending_numeric_input_without_saving(market_bot):
    controls, requests = market_bot
    await controls.handle_update(callback("n:alert_score_threshold"))
    assert controls._input is not None
    await controls.handle_update(message("/market"))
    assert "Choose a timeframe" in last_message(requests)["text"]
    assert controls._input is None
    assert controls._proposal is None
    assert controls.runtime.revision == 0 and not controls.runtime.path.exists()


def saved_evaluation_callback(data="e:42:report", **kwargs):
    update = callback("unused", **kwargs)
    update["callback_query"]["data"] = data
    return update


def saved_evaluation(token, **evaluation):
    return {
        "token": token.model_dump(mode="json"),
        "signal": RevivalResult(score=65, status="WATCH", eligible=False).model_dump(mode="json"),
        "configuration": {},
        "evaluation": {
            "id": 42,
            "timestamp": token.timestamp,
            "eligible": False,
            "rejection_reasons": ["no_base", "score_below_threshold"],
            "missing_fields": ["baseline_history"],
            "warnings": [],
            **evaluation,
        },
    }


async def test_saved_evidence_is_persistent_owner_only_and_does_not_request_scans(
    market_bot, token
):
    controls, requests = market_bot
    calls = []
    detail = saved_evaluation(token)

    def provider(kind, identity):
        calls.append((kind, identity))
        return detail

    controls.detail_provider = provider
    controls.scan_request = lambda: pytest.fail("Saved evidence requested a scan")
    controls.runtime.apply({"alerts_paused": True})
    revision = controls.runtime.revision
    settings = controls.runtime.path.read_bytes()
    await controls.handle_update(
        saved_evaluation_callback(sender=999, date=controls._boot_time - 1)
    )
    assert requests == [] and calls == []
    await controls.handle_update(saved_evaluation_callback(date=controls._boot_time - 1))
    assert calls == [("e", 42)]
    payload = last_message(requests)
    assert "Eligible at observation: no" in payload["text"]
    assert "Blocker 1/2: no_base" in payload["text"]
    assert "Missing field 1/1: baseline_history" in payload["text"]
    assert any(button.get("callback_data") == "e:42:full" for button in flat_buttons(payload))
    assert controls.runtime.revision == revision
    assert controls.runtime.path.read_bytes() == settings


async def test_legacy_evidence_works_without_token_signal_models(market_bot, token):
    controls, requests = market_bot
    detail = saved_evaluation(token, rejection_reasons=["security_dangerous"])
    detail.update(token=None, signal=None)
    controls.detail_provider = lambda kind, identity: detail
    await controls.handle_update(saved_evaluation_callback())
    payload = last_message(requests)
    assert "Saved token/signal details unavailable (legacy)" in payload["text"]
    assert "Blocker 1/1: security_dangerous" in payload["text"]
    assert "KNOWN RISK" in payload["text"]
    assert not any(button.get("callback_data") == "e:42:full" for button in flat_buttons(payload))


async def test_long_saved_evidence_has_bounded_previous_next_navigation(market_bot, token):
    controls, requests = market_bot
    detail = saved_evaluation(
        token,
        rejection_reasons=[f"BLOCK_{index}_" + "<&💥" * 100 for index in range(30)],
        missing_fields=[f"MISSING_{index}" for index in range(30)],
    )
    controls.detail_provider = lambda kind, identity: detail
    await controls.handle_update(saved_evaluation_callback())
    first = last_message(requests)
    assert "Evidence page 1/" in first["text"]
    assert len(first["text"].encode("utf-16-le")) // 2 <= 4096
    next_button = next(button for button in flat_buttons(first) if button["text"] == "Next ▶")
    assert next_button["callback_data"] == "e:42:report:1"
    await controls.handle_update(saved_evaluation_callback(next_button["callback_data"]))
    second = last_message(requests)
    assert "Evidence page 2/" in second["text"]
    previous = next(button for button in flat_buttons(second) if button["text"] == "◀ Previous")
    assert previous["callback_data"] == "e:42:report:0"
    assert len(second["text"].encode("utf-16-le")) // 2 <= 4096
    assert all(
        len(button.get("callback_data", "").encode()) <= 64 for button in flat_buttons(second)
    )
    await controls.handle_update(saved_evaluation_callback(previous["callback_data"]))
    assert last_message(requests)["text"] == first["text"]
    assert controls.runtime.revision == 0 and not controls.runtime.path.exists()


@pytest.mark.parametrize(
    "data",
    ["e:42:report:-1", "e:42:report:٢", "e:42:report:12345678901", "a:42:report", "e:42:full:1"],
)
async def test_malformed_evidence_callbacks_do_not_read_data(market_bot, data):
    controls, requests = market_bot
    controls.detail_provider = lambda *args: pytest.fail("Malformed callback read saved data")
    await controls.handle_update(saved_evaluation_callback(data))
    assert [method for method, _ in requests] == ["answerCallbackQuery"]


async def test_out_of_range_evidence_page_has_a_safe_start_over_button(market_bot, token):
    controls, requests = market_bot
    controls.detail_provider = lambda kind, identity: saved_evaluation(token)
    await controls.handle_update(saved_evaluation_callback("e:42:report:999"))
    payload = last_message(requests)
    assert payload["text"] == "This saved evidence page is unavailable."
    assert flat_buttons(payload)[0]["callback_data"] == "e:42:report"


async def test_evidence_metadata_identity_mismatch_is_rejected_locally(market_bot, token):
    controls, requests = market_bot
    controls.detail_provider = lambda kind, identity: saved_evaluation(token, id=99)
    await controls.handle_update(saved_evaluation_callback())
    assert last_message(requests)["text"] == (
        "Saved evaluation evidence could not be loaded. Try again later."
    )


async def test_evidence_redacts_secrets_and_supports_async_provider(market_bot, token):
    controls, requests = market_bot

    async def provider(kind, identity):
        return saved_evaluation(
            token,
            rejection_reasons=[
                "private-helius<&secret private-gmgn-secret private-telegram-secret"
            ],
        )

    controls.detail_provider = provider
    await controls.handle_update(saved_evaluation_callback())
    text = last_message(requests)["text"]
    assert "private" not in text and text.count("[redacted]") == 3


async def test_full_evaluation_details_include_escaped_summary_and_complete_evidence_button(
    market_bot, token
):
    controls, requests = market_bot
    controls.detail_provider = lambda kind, identity: saved_evaluation(
        token, rejection_reasons=["<blocked>&reason"], missing_fields=["baseline_history"]
    )
    await controls.handle_update(saved_evaluation_callback("e:42:full"))
    payload = last_message(requests)
    assert "Saved evaluation evidence" in payload["text"]
    assert "Eligible at observation: no · Blockers 1 · Missing 1" in payload["text"]
    assert "&lt;blocked&gt;&amp;reason" in payload["text"]
    assert any(button.get("callback_data") == "e:42:report" for button in flat_buttons(payload))
    assert len(payload["text"].encode("utf-16-le")) // 2 <= 4096


async def test_nearly_full_evaluation_details_keep_evidence_button_without_exceeding_limit(
    market_bot, token, monkeypatch
):
    from revival_radar.clients import telegram
    from revival_radar.telegram_views import config_footer

    controls, requests = market_bot
    footer_size = len(config_footer({}).encode("utf-16-le")) // 2
    monkeypatch.setattr(telegram, "format_full_alert", lambda *args: "A" * (4090 - footer_size - 1))
    controls.detail_provider = lambda kind, identity: saved_evaluation(token)
    await controls.handle_update(saved_evaluation_callback("e:42:full"))
    payload = last_message(requests)
    assert len(payload["text"].encode("utf-16-le")) // 2 == 4090
    assert "Status is too large" not in payload["text"]
    assert any(button.get("callback_data") == "e:42:report" for button in flat_buttons(payload))


async def test_alert_details_preserve_existing_format_without_evaluation_supplement(
    market_bot, token
):
    from revival_radar.clients.telegram import format_full_alert
    from revival_radar.telegram_views import config_footer

    controls, requests = market_bot
    detail = saved_evaluation(token)
    controls.detail_provider = lambda kind, identity: detail
    await controls.handle_update(saved_evaluation_callback("a:42:full"))
    payload = last_message(requests)
    expected = format_full_alert(token, RevivalResult.model_validate(detail["signal"]))
    assert payload["text"] == expected + "\n" + config_footer({})
    assert not any(
        button.get("callback_data", "").endswith(":report") for button in flat_buttons(payload)
    )

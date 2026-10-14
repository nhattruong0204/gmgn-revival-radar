import json

import httpx
import pytest
from pydantic import SecretStr

from revival_radar.clients.telegram import TelegramClient
from revival_radar.demo import DemoSource
from revival_radar.market_regime import regime_context
from revival_radar.scanner import Scanner

from .conftest import changed
from .test_outcomes import sent_alert
from .test_pipeline_optimization import SplitSource, run_scan


def universe(token, h1=(), h24=()):
    size = max(len(h1), len(h24))
    h1, h24 = list(h1) + [None] * (size - len(h1)), list(h24) + [None] * (size - len(h24))
    return [
        changed(
            token,
            contract_address=f"Addr{i + 1}".ljust(43, "1"),
            price_change_1h=a,
            price_change_24h=b,
        )
        for i, (a, b) in enumerate(zip(h1, h24, strict=True))
    ]


def test_medians_share_positive_and_coverage(token):
    context = regime_context(
        universe(token, [-10, -4, -2, 3, 8, 20], [-30, -20, -10, 0, 5, 10]),
        ["hot_search"],
        eligible_count=2,
    )
    assert context["universe_size"] == 6
    assert context["median_price_change_1h"] == pytest.approx(0.5)
    assert context["median_price_change_24h"] == pytest.approx(-5)
    assert context["share_positive_1h"] == pytest.approx(3 / 6)
    assert context["coverage"] == {"price_change_1h": 6, "price_change_24h": 6}
    assert context["eligible_count"] == 2 and context["sources"] == ["hot_search"]
    assert context["role"] == "context_only"


def test_unknown_values_are_excluded_not_zero(token):
    tokens = universe(token, [-5, -4, -3, -2, -1, None, None], [1, 2, 3, 4, None, None, None])
    tokens.append(
        changed(
            token,
            contract_address="Empty".ljust(43, "1"),
            price_change_1h=None,
            price_change_24h=None,
        )
    )
    context = regime_context(tokens)
    assert context["universe_size"] == 5  # tokens with no usable value are outside it
    assert context["median_price_change_1h"] == -3
    assert context["coverage"] == {"price_change_1h": 5, "price_change_24h": 4}
    assert context["share_positive_1h"] == 0
    # Four known values stay below the minimum: unknown, never a median of zeros.
    assert context["median_price_change_24h"] is None
    assert context["median_price_change_24h_reason"] == "insufficient_sample"


def test_insufficient_sample_and_empty_universe(token):
    few = regime_context(universe(token, [1, 2, 3, 4]))
    assert few["median_price_change_1h"] is None and few["share_positive_1h"] is None
    assert few["median_price_change_1h_reason"] == "insufficient_sample"
    assert few["share_positive_1h_reason"] == "insufficient_sample"
    empty = regime_context([])
    assert empty["universe_size"] == 0 and empty["eligible_count"] is None
    assert empty["median_price_change_1h"] is None
    assert empty["coverage"] == {"price_change_1h": 0, "price_change_24h": 0}
    boundary = regime_context(universe(token, [1, 2, 3, 4, 5]))
    assert boundary["median_price_change_1h"] == 3


async def test_scan_records_context_in_saved_scan_metrics(config, repo, token):
    report = await run_scan(config, repo, SplitSource(token))
    saved = json.loads(repo.get_state(f"scan:metrics:{report.scan_id}"))
    context = saved["market_context"]["sol"]
    assert context["universe_size"] == 1 and context["eligible_count"] == 0
    assert context["median_price_change_1h"] is None  # one token is below the minimum
    assert context["coverage"]["price_change_1h"] == 1
    assert report.market_context["sol"] == context


async def test_alert_presentation_carries_context_and_old_alerts_are_untouched(config, repo, token):
    config.dry_run = False
    config.trending_discovery_enabled = True
    config.telegram_bot_token = SecretStr("fixture-token")
    config.telegram_chat_id = "fixture-chat"
    old = sent_alert(config, repo, changed(token, contract_address="Radar2".ljust(43, "1")))
    old_key = f"telegram:alert:{old}"
    before = repo.get_state(old_key)
    assert "market_context" not in json.loads(before)
    source = DemoSource()
    for snapshot in source.histories():
        repo.save_snapshot(snapshot)

    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()
    assert report.sent == 1
    newest = repo.db.execute("SELECT MAX(id) FROM alerts").fetchone()[0]
    assert newest != old
    value = json.loads(repo.get_state(f"telegram:alert:{newest}"))
    assert value["market_context"]["universe_size"] >= 1
    assert value["market_context"]["eligible_count"] is None  # scan was still running
    assert repo.get_state(old_key) == before

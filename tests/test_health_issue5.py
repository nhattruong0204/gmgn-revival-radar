import copy

import pytest

from revival_radar.clients.normalization import security_from
from revival_radar.clients.telegram import format_full_alert
from revival_radar.config_context import configuration_context
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.models.token import Security
from revival_radar.scanner import ScanReport
from revival_radar.telegram_views import health_page

from .conftest import changed
from .test_telegram_issue1 import assert_html


def report_with_evaluations(config, repo, token):
    context = configuration_context(config, 7)
    scan = repo.begin_scan(token.timestamp, context)
    first = changed(token, security=Security(top10_ratio=0, dev_ratio=0.1, sniper_ratio=0.2))
    signal = RevivalResult(
        score=63,
        status="EARLY_REVIVAL",
        eligible=False,
        setup_score=70,
        trigger_score=65,
        confirmation_score=25,
        structure=Structure(available=True, base_detected=True, base_duration_hours=12),
    )
    repo.record_evaluation(scan, first, signal, config, context)
    second = changed(
        token,
        timestamp=token.timestamp + 10,
        contract_address="Dther111111111111111111111111111111111111111",
        security=Security(bundler_ratio=0.4, insider_ratio=None),
    )
    repo.record_evaluation(
        scan, second, signal.model_copy(update={"structure": Structure()}), config, context
    )
    stats = ScanReport(
        processed=2,
        duration_seconds=2,
        finished_at=token.timestamp + 2,
        performance={
            "seconds": {"market": 0.2},
            "calls": {"/v1/token/info": 2},
            "cache": {"security_hit": 3, "security_fetch": 1, "kline_fetch": 2},
        },
        funnel={
            "sol": {
                "discovered": 2,
                "prefilter_pass": 2,
                "market_pass": 2,
                "baseline_ready": 1,
                "activity_trigger": 1,
                "base_detected": 1,
                "eligible": 0,
                "alerted": 0,
            }
        },
    )
    repo.finish_scan(scan, stats, token.timestamp + 2)
    repo.persist_scan_metrics(scan, stats)
    return repo.health(0, token.timestamp + 20)


def test_coverage_counts_observed_zero_as_available_and_missing_as_unknown(config, repo, token):
    report = report_with_evaluations(config, repo, token)
    quality = health_page(report, "quality")
    for label in ("Top10", "Dev", "Sniper", "Bundler"):
        assert f"{label}  50%" in quality
    assert "Insider  0%" in quality
    assert "Candle structure available  50%" in quality
    assert "Any security data available  100%" in quality
    assert "not insider trading volume" in quality
    assert "coverage" in quality.lower()
    assert_html(quality)


def test_compact_overview_performance_funnel_and_near_miss_dimensions(config, repo, token):
    report = report_with_evaluations(config, repo, token)
    overview = health_page(report | {"schedule": {"running": True}})
    assert "Chains  sol" in overview and "r7" in overview
    assert "Last 2s" in overview and "0 eligible observations" in overview
    assert "Candle structure available" in overview and len(overview.splitlines()) <= 28
    performance = health_page(report, "performance")
    assert "Security  75% · 3 hits / 1 fetches" in performance
    assert "Candles  0% · 0 hits / 2 fetches" in performance
    assert "Token info  2" in performance and "Baseline  1" in performance
    funnel = health_page(report, "funnel")
    assert "Latest SOL scan" in funnel and "Baseline ready  1" in funnel
    near = health_page(report, "near")
    assert "Setup 70/100 · Trigger 65/100 · Confirm 25/100" in near
    assert "Base too short" in near
    assert "Missing:" in near
    for view in ("overview", "performance", "funnel", "quality", "near", "outcomes"):
        assert_html(health_page(report, view))


def test_legacy_coverage_does_not_invent_available_security(config, repo, token):
    report = report_with_evaluations(config, repo, token)
    with repo.db:
        repo.db.execute("DELETE FROM state WHERE key LIKE 'telegram:evaluation:%'")
    report = repo.health(0, token.timestamp + 20)
    assert report["evaluations"]["coverage"]["known"] == 0
    assert all(candidate["setup_score"] is None for candidate in report["best_candidates"])
    assert "Setup unknown" in health_page(report, "near")
    assert "Any security data available  No observations" in health_page(report, "quality")


def test_many_chain_funnel_remains_valid_telegram_html(config, repo, token):
    report = report_with_evaluations(config, repo, token)
    latest = report["latest_scan"]
    latest["funnel"] = {
        chain: copy.deepcopy(latest["funnel"]["sol"])
        for chain in ("sol", "bsc", "base", "robinhood", "arc")
    }
    for view in ("overview", "performance", "funnel", "quality", "near", "outcomes"):
        text = health_page(report, view)
        assert len(text.encode("utf-16-le")) // 2 <= 4096
        assert_html(text)


def test_repeated_missing_insider_is_not_an_alert_watchout_but_risks_remain(config, token):
    token = changed(token, security=Security(dangerous=True, insider_ratio=None))
    signal = RevivalResult(
        score=70,
        status="WATCH",
        eligible=False,
        warnings=["Insider holdings unavailable", "Liquidity dropped sharply"],
        components={"liquidity_penalty": -25},
    )
    text = format_full_alert(token, signal)
    assert "Insider holdings unavailable" not in text
    assert "Liquidity dropped sharply" in text and "dangerous" in text.lower()


@pytest.mark.parametrize("volume_field", ["rat_trader_amount_rate", "top_rat_trader_percentage"])
def test_insider_volume_never_becomes_holdings(volume_field):
    result = security_from({"stat": {volume_field: 0.8}}, {volume_field: 0.8}, "sol", Security())
    assert result.insider_ratio is None
    actual = security_from({}, {"suspected_insider_hold_rate": 0.2}, "sol", Security())
    assert actual.insider_ratio == 0.2

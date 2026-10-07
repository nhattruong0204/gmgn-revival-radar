import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from revival_radar.analysis.acceleration import calculate_acceleration
from revival_radar.analysis.scoring import score_token, status_for
from revival_radar.clients.telegram import format_alert, format_full_alert, format_why
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.models.signal import RevivalResult, Structure, has_returning_activity
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository

from .conftest import changed
from .test_alert_presentation import TelegramHTML
from .test_pipeline_optimization import SplitSource, run_scan


def base(hours=96, **kwargs):
    return Structure(available=True, base_detected=True, base_duration_hours=hours, **kwargs)


def full_base(hours=96):
    return base(
        hours,
        higher_low_detected=True,
        higher_high_detected=True,
        breakout_detected=True,
        retest_detected=True,
        volatility_compression_score=0.5,
    )


def test_false_percentage_surge_does_not_earn_full_trigger_or_strong_stage(token, history, config):
    tiny = changed(token, liquidity=700000, volume_5m=2800, volume_1h=50000, tx_5m=4, tx_1h=4)
    old = [changed(h, volume_5m=500, volume_1h=50000, tx_5m=2, tx_1h=2) for h in history]
    signal = score_token(tiny, old, full_base(), config)
    assert signal.acceleration.tx_acceleration_5m == 2
    assert signal.acceleration.volume_ratio_5m > 5
    assert signal.acceleration.volume_5m_to_liquidity == pytest.approx(0.004)
    assert not any(name in signal.components for name in ("volume_5m", "volume_1h", "transactions"))
    assert not signal.returning_activity and not signal.eligible
    assert signal.status in {"IGNORE", "WATCH"}


@pytest.mark.parametrize("higher_low,expected", [(False, "EARLY_REVIVAL"), (True, "REVIVING")])
def test_real_early_revival(token, history, config, higher_low, expected):
    signal = score_token(token, history, base(24, higher_low_detected=higher_low), config)
    assert signal.status == expected and signal.eligible
    assert signal.components["volume_5m"] > 0 and signal.components["transactions"] > 0
    assert signal.setup_score > 0 and signal.trigger_score == 100
    assert signal.confirmation_score == (25 if higher_low else 0)


def test_confirmed_revival_requires_mature_base_and_chart_evidence(token, history, config):
    signal = score_token(token, history, full_base(), config)
    assert signal.score == 100 and signal.status == "CONFIRMED_REVIVAL"
    assert (signal.setup_score, signal.trigger_score, signal.confirmation_score) == (100, 100, 100)
    assert signal.eligible
    short = score_token(token, history, full_base(24), config)
    assert short.status == "REVIVING"  # High total and real chart evidence cannot bypass maturity.
    unconfirmed = score_token(token, history, base(), config)
    assert unconfirmed.status == "EARLY_REVIVAL" and unconfirmed.confirmation_score == 0


def test_pure_pump_and_dead_token_remain_ignore(token, history, config):
    pump = score_token(changed(token, price_change_1h=250), history, Structure(), config)
    assert pump.status == "IGNORE" and not pump.eligible and pump.score <= 39
    dead = changed(token, liquidity=1000, volume_5m=10, volume_1h=100, tx_5m=1, tx_1h=1)
    signal = score_token(dead, history, Structure(), config)
    assert signal.status == "IGNORE" and not signal.eligible
    assert signal.components["liquidity_penalty"] < 0


@pytest.mark.parametrize("hours,points", [(6, 5), (8, 5), (12, 8), (24, 12), (48, 15), (72, 15)])
def test_progressive_base_boundaries(token, history, config, hours, points):
    config.base_min_hours = 6
    signal = score_token(token, history, base(hours), config)
    assert signal.components["base"] == points
    assert ("base_duration" in signal.components) == (hours >= 72)


def test_base_thresholds_fractions_compression_and_dimension_weights_are_configurable(
    token, history, config
):
    config.base_min_hours = 6
    config.base_maturity_hours = (4, 10, 20, 40, 60)
    config.base_maturity_fractions = (0.2, 0.4, 0.7, 1)
    config.base_sufficient_hours = 60
    config.weights.base = 10
    signal = score_token(token, history, base(10), config)
    assert signal.components["base"] == 4
    compressed = score_token(token, history, base(10, volatility_compression_score=0.5), config)
    assert compressed.components["compression"] == 5 and compressed.setup_score > signal.setup_score
    config.volatility_compression_threshold = 0.6
    assert (
        "compression"
        not in score_token(
            token, history, base(10, volatility_compression_score=0.5), config
        ).components
    )
    config.weights.setup_dimension = config.weights.confirmation_dimension = 0
    assert (
        score_token(token, history, base(10), config).score == 100
    )  # Trigger-only overall allocation.
    assert (
        score_token(token, history, Structure(), config).status == "WATCH"
    )  # Still no chart evidence.


@pytest.mark.parametrize("liquidity,expected", [(None, None), (0, None), (100, 20), (1e-308, None)])
def test_liquidity_normalization_is_uncapped_nullable_and_finite(
    token, history, config, liquidity, expected
):
    signal = calculate_acceleration(
        changed(token, volume_5m=2000, liquidity=liquidity), history, config
    )
    assert signal.volume_5m_to_liquidity == expected


def test_hourly_activity_uses_hourly_floors_not_a_tiny_5m_count(token, history, config):
    # Quiet short window does not erase a genuinely meaningful hourly revival.
    current = changed(token, volume_5m=100, tx_5m=4, tx_1h=600)
    old = [changed(h, volume_5m=100, tx_5m=2, tx_1h=200) for h in history]
    signal = score_token(current, old, full_base(), config)
    assert "volume_5m" not in signal.components
    assert signal.components["volume_1h"] == 10 and signal.components["transactions"] == 10
    # An unrelated high 5m count must not rescue a 2 -> 4 hourly percentage surge.
    current = changed(current, tx_5m=100, tx_1h=4, volume_1h=50000)
    old = [changed(h, tx_5m=100, tx_1h=2, volume_1h=50000, volume_5m=100) for h in history]
    signal = score_token(current, old, full_base(), config)
    assert "transactions" not in signal.components and not signal.returning_activity


def test_five_minute_absolute_and_liquidity_floors_are_independent_and_inclusive(
    token, history, config
):
    old = [changed(h, volume_5m=1000, volume_1h=50000, tx_5m=5, tx_1h=10) for h in history]
    current = changed(token, liquidity=200000, volume_5m=2000, volume_1h=50000, tx_5m=10, tx_1h=10)
    signal = score_token(current, old, base(), config)
    assert signal.components["volume_5m"] == 15 and signal.components["transactions"] == 10
    current = changed(current, volume_5m=1999, tx_5m=9)
    signal = score_token(current, old, base(), config)
    assert "volume_5m" not in signal.components and "transactions" not in signal.components
    assert not signal.eligible


def test_off_ranking_alert_requires_stronger_evidence_for_top_stage(token, history, config):
    watched = changed(token, discovery_source=set(), hot_search_rank=None, trending_rank=None)
    signal = score_token(watched, history, full_base(), config)
    assert signal.score == 88 and signal.status == "CONFIRMED_REVIVAL" and signal.eligible
    assert signal.discovery_context == "WATCHLIST_REVIVAL"
    weak = score_token(
        watched, history, base(higher_low_detected=True, higher_high_detected=True), config
    )
    assert weak.status not in {"STRONG_REVIVAL", "CONFIRMED_REVIVAL"}
    steady_tx = [changed(h, tx_5m=token.tx_5m, tx_1h=token.tx_1h) for h in history]
    assert score_token(watched, steady_tx, full_base(), config).status not in {
        "STRONG_REVIVAL",
        "CONFIRMED_REVIVAL",
    }
    unknown = changed(watched, data_warnings=["Current ranking coverage incomplete"])
    assert (
        score_token(unknown, history, full_base(), config).discovery_context
        == "RANKING_UNAVAILABLE"
    )


@pytest.mark.parametrize("score", [0, 40, 60, 80, 100])
def test_total_without_evidence_never_establishes_top_stage(config, score):
    assert status_for(score, config=config) == "IGNORE"
    assert status_for(
        score, config=config, qualifies=True, activity=True, structure=Structure()
    ) in {"IGNORE", "WATCH"}


def test_stage_thresholds_do_not_override_chart_guards(config):
    config.strong_revival_score_threshold = 70
    config.confirmed_revival_score_threshold = 75
    assert (
        status_for(
            75, config=config, qualifies=True, activity=True, meaningful=True, structure=full_base()
        )
        == "CONFIRMED_REVIVAL"
    )
    assert (
        status_for(
            100, config=config, qualifies=True, activity=True, meaningful=True, structure=base()
        )
        == "EARLY_REVIVAL"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"base_maturity_hours": (6, 12, 12, 48, 72)},
        {"base_maturity_fractions": (0.5, 0.2, 0.8, 1)},
        {"strong_base_min_hours": 96, "confirmed_base_min_hours": 72},
        {"strong_revival_score_threshold": 90, "confirmed_revival_score_threshold": 85},
        {"min_volume_5m_liquidity_ratio": float("nan")},
        {"min_tx_5m_for_acceleration": 0},
    ],
)
def test_new_config_rejects_invalid_ranges(changes):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **changes)


def test_zero_weights_are_safe_and_do_not_invent_activity_confidence(token, history, config):
    for name in type(config.weights).model_fields:
        setattr(config.weights, name, 0)
    signal = score_token(token, history, full_base(), config)
    assert (signal.setup_score, signal.trigger_score, signal.confirmation_score, signal.score) == (
        0,
        0,
        0,
        0,
    )
    assert signal.status == "IGNORE"
    assert has_returning_activity(
        signal
    )  # Actual evidence is independent of configured zero points.


def assert_html(text):
    assert len(text.encode("utf-16-le")) // 2 < 4096
    parser = TelegramHTML()
    parser.feed(text)
    parser.close()
    assert not parser.tags


def test_new_dimensions_render_and_legacy_records_stay_unknown(token, history, config):
    signal = score_token(token, history, full_base(), config)
    for formatter, args in (
        (format_alert, (token, signal, {})),
        (format_why, (token, signal, {})),
        (format_full_alert, (token, signal)),
    ):
        text = formatter(*args)
        assert "Setup 100/100" in text and "Confirm 100/100" in text
        assert_html(text)
    assert "not overall points" in format_why(token, signal, {})
    legacy = RevivalResult.model_validate(
        {"score": 91, "status": "HIGH_CONVICTION_REVIVAL", "eligible": True}
    )
    assert legacy.setup_score is None and legacy.returning_activity is None
    assert "High conviction revival" in format_alert(token, legacy)
    assert "Setup" not in format_alert(token, legacy)


def test_dimensions_persist_without_changing_old_alert_payloads(config, repo, token, history):
    legacy = RevivalResult(score=91, status="HIGH_CONVICTION_REVIVAL", eligible=True)
    config.dry_run = False
    identity = repo.reserve_alert(token, legacy, config)
    prior = repo.get_state(f"telegram:alert:{identity}")
    signal = score_token(token, history, full_base(), config)
    scan_id = repo.begin_scan(token.timestamp + 300, configuration_context(config))
    repo.record_evaluation(scan_id, token, signal, config, configuration_context(config))
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    other = connect(path)
    try:
        restored = Repository(other)
        evaluation = restored.presentation_detail("e", 1)
        assert evaluation["signal"]["setup_score"] == 100
        assert evaluation["configuration"]["settings"]["base_maturity_hours"] == [6, 12, 24, 48, 72]
        assert restored.get_state(f"telegram:alert:{identity}") == prior
        assert other.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "fixture-key" not in json.dumps(evaluation)
    finally:
        other.close()


async def test_tiny_surge_skips_candles_and_security_in_scanner(token, history, config, repo):
    for old in history:
        repo.save_snapshot(changed(old, volume_5m=1, volume_1h=50000, tx_5m=2, tx_1h=2))
    tiny = changed(token, volume_5m=10, volume_1h=50000, tx_5m=4, tx_1h=4)
    source = SplitSource(tiny)
    report = await run_scan(config, repo, source)
    assert source.calls["kline"] == source.calls["security"] == 0
    assert report.potential_alerts == 0 and not report.signals[0][1].returning_activity
    blockers = json.loads(
        repo.db.execute("SELECT rejection_reasons FROM evaluations").fetchone()[0]
    )
    assert "no_returning_activity" in blockers


def test_stale_rank_fields_do_not_create_current_attention_scores(token, history, config):
    watched = changed(token, discovery_source=set())  # Deliberately retain old rank values.
    signal = score_token(watched, history, full_base(), config)
    assert not any(name in signal.components for name in ("hot_rank", "trending", "both_sources"))
    assert signal.discovery_context == "WATCHLIST_REVIVAL"


def test_env_example_loads_json_scoring_thresholds_without_credentials():
    config = Settings(_env_file=Path(__file__).parents[1] / ".env.example")
    assert config.base_maturity_hours == (6, 12, 24, 48, 72)
    assert config.base_maturity_fractions == pytest.approx((1 / 3, 8 / 15, 0.8, 1))
    assert config.min_tx_5m_for_acceleration == 10 and config.chains == ["sol"]


async def test_partial_rank_failure_is_unknown_coverage_for_watchlist(
    config, repo, token, history, candles, monkeypatch
):
    monkeypatch.setattr("time.time", lambda: token.timestamp)
    for old in history:
        repo.save_snapshot(old)

    class PartialSource(SplitSource):
        async def discover(self, chain, kind):
            if kind == "trending":
                raise ValueError("ranking unavailable")
            return []

    source = PartialSource(token, candles=candles)
    report = await run_scan(config, repo, source)
    assert report.errors == 1 and report.processed == 1
    assert report.signals[0][1].discovery_context == "RANKING_UNAVAILABLE"
    assert not report.signals[0][0].discovery_source

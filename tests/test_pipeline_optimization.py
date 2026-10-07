import asyncio
import json
from collections import Counter
from pathlib import Path

import httpx
import pytest

from revival_radar.analysis.filters import discovery_prefilter
from revival_radar.analysis.scoring import score_token
from revival_radar.clients.gmgn import DataSourceError, GMGNClient
from revival_radar.clients.normalization import ranked_token
from revival_radar.clients.telegram import TelegramClient
from revival_radar.metrics import CURRENT_SCAN, ENDPOINTS, STAGES, ScanMetrics, sqlite_timed
from revival_radar.models.signal import Structure
from revival_radar.models.token import Security, TokenSnapshot
from revival_radar.scanner import Scanner
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.telegram_views import health_page

from .conftest import changed
from .test_alert_presentation import TelegramHTML


class SplitSource:
    def __init__(self, discovery, market=None, candles=(), security=None):
        self.discovery = discovery
        self.market = market or discovery
        self.bars = candles
        self.security = security
        self.calls = Counter()

    async def discover(self, chain, source):
        self.calls[source] += 1
        return [changed(self.discovery, discovery_source={source})] if chain == "sol" else []

    async def enrich_market(self, seed):
        self.calls["market"] += 1
        return changed(self.market, discovery_source=seed.discovery_source)

    async def enrich_security(self, token):
        self.calls["security"] += 1
        if isinstance(self.security, Exception):
            raise self.security
        return changed(token, security=self.security) if self.security is not None else token

    async def candles(self, token):
        self.calls["kline"] += 1
        return self.bars


@pytest.mark.parametrize(
    "field",
    [
        "market_cap",
        "ath_market_cap",
        "liquidity",
        "holders",
        "token_age_seconds",
        "volume_1h",
        "price_change_5m",
        "price_change_1h",
    ],
)
def test_prefilter_defers_unknown_discovery_values_to_market(token, config, field):
    assert discovery_prefilter(changed(token, **{field: None}), config).passed


@pytest.mark.parametrize(
    "field,value",
    [
        ("market_cap", 1000),
        ("market_cap", 20_000_000),
        ("liquidity", 0),
        ("holders", 0),
        ("token_age_seconds", 0),
        ("volume_1h", 0),
        ("ath_market_cap", 1_000_000),
        ("ath_market_cap", 100_000_000),
        ("price_change_5m", 31),
        ("price_change_1h", 76),
    ],
)
def test_prefilter_rejects_known_threshold_failures(token, config, field, value):
    gate = discovery_prefilter(changed(token, **{field: value}), config)
    assert not gate.passed
    assert all(not reason.endswith(": unavailable") for reason in gate.reasons)


@pytest.mark.parametrize("interval,passed", [("1h", False), ("5m", True), ("24h", True)])
def test_prefilter_checks_volume_only_in_the_matching_window(token, config, interval, passed):
    raw = {
        "address": token.contract_address,
        "market_cap": token.market_cap,
        "history_highest_market_cap": token.ath_market_cap,
        "volume": 0,
    }
    normalized = ranked_token(raw, "sol", "trending", 1, interval, token.timestamp)
    assert discovery_prefilter(normalized, config).passed is passed


async def run_scan(config, repo, source):
    def forbidden(request):
        pytest.fail("Synthetic dry-run must not make network requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        return await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()


async def test_known_bad_discovery_skips_all_expensive_calls_but_retains_diagnostics(
    token, config, repo
):
    source = SplitSource(changed(token, market_cap=1000))
    report = await run_scan(config, repo, source)
    assert source.calls == {"hot_search": 1, "trending": 1}
    assert report.processed == 1 and report.errors == 0
    assert report.funnel["sol"]["prefilter_rejected"] == 1
    assert repo.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 1
    assert repo.db.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0] == 0
    assert report.signals[0][1].score <= 39 and not report.signals[0][1].eligible


async def test_missing_discovery_is_enriched_before_any_decision(token, config, repo):
    seed = TokenSnapshot(chain="sol", contract_address=token.contract_address)
    source = SplitSource(seed, market=token)
    report = await run_scan(config, repo, source)
    assert source.calls["market"] == 1
    assert report.funnel["sol"]["prefilter_pass"] == report.funnel["sol"]["market_pass"] == 1
    assert report.signals[0][0].market_cap == token.market_cap
    assert repo.db.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0] == 1


async def test_market_failure_skips_security_and_candles_even_when_discovery_passes(
    token, config, repo
):
    source = SplitSource(token, market=changed(token, liquidity=100))
    report = await run_scan(config, repo, source)
    assert source.calls["market"] == 1
    assert source.calls["security"] == source.calls["kline"] == 0
    assert report.funnel["sol"]["market_pass"] == 0 and report.errors == 0


async def test_no_activity_collects_market_history_without_security_or_kline(token, config, repo):
    source = SplitSource(token)
    report = await run_scan(config, repo, source)
    assert source.calls["security"] == source.calls["kline"] == 0
    assert report.funnel["sol"]["market_enriched"] == 1
    assert not report.signals[0][1].eligible


@pytest.mark.parametrize("with_base", [False, True])
async def test_security_requires_serious_candidate_and_preserves_scoring(
    token,
    history,
    candles,
    config,
    repo,
    with_base,
):
    for snapshot in history:
        repo.save_snapshot(snapshot)
    source = SplitSource(token, candles=candles if with_base else [])
    report = await run_scan(config, repo, source)
    assert source.calls["kline"] == 1
    assert source.calls["security"] == int(with_base)
    signal = report.signals[0][1]
    expected_structure = signal.structure if with_base else Structure()
    expected = score_token(report.signals[0][0], history, expected_structure, config)
    assert signal.model_dump() == expected.model_dump()
    assert report.potential_alerts == int(with_base)


async def test_security_refresh_can_remove_discovery_penalties_and_add_new_risks(
    token,
    history,
    candles,
    config,
    repo,
):
    for snapshot in history:
        repo.save_snapshot(snapshot)
    config.weights.concentration_penalty = 40
    concentrated = changed(token, security=token.security.model_dump() | {"top10_ratio": 0.9})
    source = SplitSource(concentrated, candles=candles, security=token.security)
    report = await run_scan(config, repo, source)
    assert source.calls["security"] == 1  # A low provisional score must not prevent refresh.
    assert report.signals[0][1].score >= config.alert_score_threshold
    source.security = Security(dangerous=True)
    report = await run_scan(config, repo, source)
    assert source.calls["security"] == 2 and not report.signals[0][1].eligible
    assert report.potential_alerts == 0


async def test_security_soft_failure_keeps_existing_nullable_behavior_and_is_measured(
    token,
    history,
    candles,
    config,
    repo,
):
    for snapshot in history:
        repo.save_snapshot(snapshot)
    source = SplitSource(token, candles=candles, security=DataSourceError("unavailable"))
    report = await run_scan(config, repo, source)
    assert report.performance["failures"]["security"] == 1
    assert "Security enrichment unavailable" in report.signals[0][1].warnings
    assert report.signals[0][1].eligible  # Existing scorer policy is unchanged in issue #2.


async def test_split_client_only_calls_requested_endpoint_and_preserves_info_security(
    token,
    responses,
    config,
):
    calls = []
    responses["info"]["data"]["address"] = token.contract_address
    responses["security"]["data"]["creator_balance_rate"] = 0.9

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=responses[request.url.path.rsplit("/", 1)[-1]])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = GMGNClient(config, http)
        enriched = await source.enrich_market(token)
        assert calls == ["/v1/token/info"]
        assert enriched.security.dev_ratio == 0.03 and enriched.security.sniper_ratio == 0.05
        secured = await source.enrich_security(enriched)
        assert calls == ["/v1/token/info", "/v1/token/security"]
        assert secured.security.insider_ratio == 0.08 and secured.security.dev_ratio == 0.03
        assert secured.price == enriched.price and secured.timestamp == enriched.timestamp
        assert "_market_security_info" not in secured.model_dump()
        assert "_market_security_info" not in TokenSnapshot.model_fields


async def test_api_attempt_counters_include_retries_failures_and_respect_shared_cooldown(
    config, monkeypatch
):
    async def no_wait(delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"code": 503})
        if len(requests) == 2:
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(429, headers={"Retry-After": "300"}, json={"code": 429})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = GMGNClient(config, http)
        await source.request("GET", "/v1/token/info")
        assert source.metrics.calls["/v1/token/info"] == 2
        for _ in range(2):
            with pytest.raises(DataSourceError):
                await source.request("GET", "/v1/token/security")
        assert source.metrics.calls["/v1/token/security"] == 1
        assert len(requests) == 3


async def test_timing_counts_persist_restart_reset_and_leave_schema_compatible(config, repo, token):
    source = SplitSource(changed(token, market_cap=1000))
    first = await run_scan(config, repo, source)
    second = await run_scan(config, repo, source)
    for report in (first, second):
        assert set(report.performance["seconds"]) == set(STAGES) | {"total"}
        assert set(report.performance["calls"]) == set(ENDPOINTS)
        assert report.performance["seconds"]["sqlite"] > 0
        assert report.performance["seconds"]["total"] == pytest.approx(
            report.duration_seconds, abs=1e-6
        )
        assert report.funnel["sol"]["prefilter_checked"] == 1
        assert "fixture-" not in json.dumps(report.performance)
    assert CURRENT_SCAN.get() is None
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    reopened = connect(path)
    try:
        restored = Repository(reopened).health(0)
        assert restored["latest_scan"]["performance"] == second.performance
        assert restored["latest_scan"]["funnel"] == second.funnel
        assert reopened.execute("PRAGMA user_version").fetchone()[0] == 2
        for view in ("performance", "funnel"):
            text = health_page(restored, view)
            assert len(text.encode("utf-16-le")) // 2 <= 4096
            parser = TelegramHTML()
            parser.feed(text)
            parser.close()
            assert not parser.tags
        repo.prune_diagnostics(second.started_at + 1)
        assert repo.get_state(f"scan:metrics:{second.scan_id}") is None
    finally:
        reopened.close()


def test_sqlite_timing_counts_nested_operations_once_and_cleans_up_on_failure(monkeypatch):
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr("revival_radar.metrics.time.monotonic", lambda: next(ticks))

    @sqlite_timed
    def nested():
        raise ValueError("fixture")

    @sqlite_timed
    def outer():
        nested()

    metrics = ScanMetrics()
    scope = CURRENT_SCAN.set(metrics)
    try:
        with pytest.raises(ValueError):
            outer()
        assert metrics.seconds["sqlite"] == 3
    finally:
        CURRENT_SCAN.reset(scope)
    with pytest.raises(ValueError):
        nested()  # No clock reads outside the scan context.


async def test_real_client_pipeline_skips_security_after_market_filter_failure(
    config,
    repo,
    token,
    responses,
    monkeypatch,
):
    monkeypatch.setattr("time.time", lambda: token.timestamp)
    responses["rank"]["data"]["rank"][0]["address"] = token.contract_address
    responses["hot_searches"]["data"][0]["tokens"][0]["address"] = token.contract_address
    responses["info"]["data"]["address"] = token.contract_address
    responses["info"]["data"]["liquidity"] = 0
    requests = []

    def handler(request):
        path = request.url.path
        requests.append(path)
        assert path != "/v1/token/security" and path != "/v1/market/token_kline"
        return httpx.Response(200, json=responses[path.rsplit("/", 1)[-1]])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GMGNClient(config, http)
        report = await Scanner(config, client, repo, TelegramClient(config, http)).scan_once()
    assert requests == ["/v1/market/hot_searches", "/v1/market/rank", "/v1/token/info"]
    assert report.performance["calls"]["/v1/token/info"] == 1
    assert report.performance["calls"]["/v1/token/security"] == 0
    assert report.funnel["sol"]["prefilter_pass"] == 1
    assert report.funnel["sol"]["market_pass"] == 0
    assert report.errors == 0 and report.signals[0][1].score <= 39
    assert "fixture-key" not in json.dumps(report.performance)


async def test_background_sqlite_work_is_excluded_from_active_scan(config, repo, token):
    # A task created before the scanner starts has no scan metric context.
    release, finished = asyncio.Event(), asyncio.Event()

    async def background():
        await release.wait()
        assert CURRENT_SCAN.get() is None
        repo.save_snapshot(changed(token, timestamp=token.timestamp - 300))
        finished.set()

    task = asyncio.create_task(background())

    class Source(SplitSource):
        async def enrich_market(self, seed):
            assert CURRENT_SCAN.get() is not None
            release.set()
            await finished.wait()
            return await super().enrich_market(seed)

    try:
        report = await run_scan(config, repo, Source(token))
        assert report.performance["seconds"]["sqlite"] > 0
        assert CURRENT_SCAN.get() is None
    finally:
        await task

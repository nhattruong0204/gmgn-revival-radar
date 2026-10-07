import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

from revival_radar import service
from revival_radar.analysis.scoring import score_token
from revival_radar.clients.gmgn import DataSourceError, GMGNClient
from revival_radar.enrichment import Enrichment, EnrichmentDeferred
from revival_radar.metrics import ScanMetrics
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.models.token import Candle, Security, TokenSnapshot
from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.scanner import Scanner
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.telegram_views import health_page

from .conftest import changed
from .test_pipeline_optimization import SplitSource, run_scan


def manager(config, source, repo):
    return Enrichment(config, source, repo, ScanMetrics())


async def test_security_cache_preserves_all_fields_restarts_expires_and_honors_new_ttl(
    config, repo, token
):
    security = Security(
        top10_ratio=0.4,
        dev_ratio=0.03,
        insider_ratio=0.08,
        sniper_ratio=0.1,
        bundler_ratio=0.2,
        dangerous=True,
        mint_renounced=False,
        freeze_renounced=False,
        flags=["mint_renounced: false"],
    )
    source = SplitSource(token, security=security)
    first = await manager(config, source, repo).security(token)
    assert first.security == security
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    db = connect(path)
    try:
        cached = manager(config, source, Repository(db))
        result = await cached.security(changed(token, timestamp=token.timestamp + 300))
        assert result.security == security and source.calls["security"] == 1
        assert cached.metrics.cache["security_hit"] == 1
        await manager(config, source, repo).security(
            changed(token, timestamp=token.timestamp + 1800)
        )
        assert source.calls["security"] == 2
        config.security_cache_ttl_seconds = 100
        await manager(config, source, repo).security(
            changed(token, timestamp=token.timestamp + 1900)
        )
        assert source.calls["security"] == 3
    finally:
        db.close()


async def test_security_hit_keeps_fresh_info_precedence_and_known_danger(config, repo, token):
    source = SplitSource(token, security=Security(dev_ratio=0.9, dangerous=False))
    await manager(config, source, repo).security(token)
    fresh = changed(
        token, timestamp=token.timestamp + 60, security=Security(dangerous=True, flags=["new risk"])
    )
    fresh._market_security_info = {"stat": {"creator_hold_rate": 0.07}}
    result = await manager(config, source, repo).security(fresh)
    assert result.security.dev_ratio == 0.07
    assert result.security.dangerous and "new risk" in result.security.flags


async def test_expired_and_corrupt_security_never_bypass_zero_budget(config, repo, token):
    config.max_security_enrich_per_scan = 0
    repo.cache_save("security", token, Security(dangerous=False).model_dump_json(), token.timestamp)
    with pytest.raises(EnrichmentDeferred):
        await manager(config, SplitSource(token), repo).security(token)
    repo.cache_save("security", token, "not-json", token.timestamp + 1800)
    with pytest.raises(EnrichmentDeferred):
        await manager(config, SplitSource(token), repo).security(token)


async def test_cache_identity_keeps_chain_and_case_and_lookback_separate(
    config, repo, token, candles
):
    source = SplitSource(token, candles=candles)
    await manager(config, source, repo).security(token)
    other = changed(token, contract_address="a" + token.contract_address[1:])
    await manager(config, source, repo).security(other)
    await manager(config, source, repo).security(
        TokenSnapshot(chain="base", contract_address="0x" + "a" * 40)
    )
    assert source.calls["security"] == 3
    await manager(config, source, repo).candles(token)
    config.kline_lookback_hours = 48
    await manager(config, source, repo).candles(token)
    assert source.calls["kline"] == 2


async def test_closed_candle_cache_reuses_past_ttl_and_refreshes_exact_hour_incrementally(
    config, repo, token
):
    start = int(token.timestamp // 3600) * 3600 + 10
    bar = Candle(timestamp=start - 10 - 3600, open=1, high=1, low=1, close=1)
    new = Candle(timestamp=start - 10, open=1, high=1, low=1, close=1)

    class Source(SplitSource):
        async def candles_since(self, token, since):
            self.calls["incremental"] += 1
            assert since == bar.timestamp
            return [bar, new]  # Overlap must deduplicate.

    source = Source(token, candles=[bar])
    first = await manager(config, source, repo).candles(changed(token, timestamp=start))
    later = manager(config, source, repo)
    assert await later.candles(changed(token, timestamp=start + 1000)) == first
    assert later.metrics.cache["kline_hit"] == 1 and source.calls["kline"] == 1
    updated = await manager(config, source, repo).candles(
        changed(token, timestamp=start - 10 + 3600)
    )
    assert [c.timestamp for c in updated] == [bar.timestamp, new.timestamp]
    assert source.calls["incremental"] == 1 and source.calls["kline"] == 1
    await manager(config, source, repo).candles(changed(token, timestamp=start + 3 * 3600))
    assert source.calls["kline"] == 2  # Missing multiple hours uses the full lookback.


async def test_empty_candles_retry_ttl_and_open_bars_are_not_cached(config, repo, token):
    now = int(token.timestamp // 3600) * 3600 + 10
    source = SplitSource(token, candles=[Candle(timestamp=now, open=1, high=1, low=1, close=1)])
    assert await manager(config, source, repo).candles(changed(token, timestamp=now)) == []
    await manager(config, source, repo).candles(changed(token, timestamp=now + 100))
    assert source.calls["kline"] == 1
    await manager(config, source, repo).candles(changed(token, timestamp=now + 900))
    assert source.calls["kline"] == 2


@pytest.mark.parametrize("stage", ["security", "kline"])
async def test_hits_do_not_consume_budgets_and_failures_do_consume_them(
    config, repo, token, candles, stage
):
    source = SplitSource(token, candles=candles)
    config.max_security_enrich_per_scan = config.max_kline_fetch_per_scan = 1
    enrichment = manager(config, source, repo)
    method = enrichment.security if stage == "security" else enrichment.candles
    await method(token)
    await method(changed(token, timestamp=token.timestamp + 1))
    assert enrichment.used[stage] == 1 and enrichment.metrics.cache[f"{stage}_hit"] == 1
    with pytest.raises(EnrichmentDeferred):
        await method(changed(token, contract_address="A" * 32))
    # A fresh scan gets a new budget, including after an adapter failure.
    source.security = DataSourceError("failed")
    if stage == "security":
        next_scan = manager(config, source, repo)
        with pytest.raises(DataSourceError):
            await next_scan.security(changed(token, contract_address="A" * 32))
        with pytest.raises(EnrichmentDeferred):
            await next_scan.security(changed(token, contract_address="B" * 32))
        assert repo.cache_entry("security", changed(token, contract_address="A" * 32)) is None


async def test_budget_deferral_cannot_send_unchecked_alert_and_is_due_next_scan(
    config, repo, token, history, candles
):
    for old in history:
        repo.save_snapshot(old)
    config.max_security_enrich_per_scan = 0
    source = SplitSource(token, candles=candles)
    report = await run_scan(config, repo, source)
    assert report.potential_alerts == 0 and source.calls["security"] == 0
    assert report.performance["deferred"]["security"] == 1
    assert report.funnel["sol"]["security_requested"] == 0
    assert repo.watch_due("sol", token.timestamp + 1, config)[0]["deferred"] == 1
    assert not report.signals[0][1].eligible
    config.max_security_enrich_per_scan = 1
    source.discovery = changed(token, timestamp=token.timestamp + 1)
    source.market = source.discovery
    second = await run_scan(config, repo, source)
    assert source.calls["security"] == 1 and second.potential_alerts == 1
    assert second.performance["cache"]["kline_hit"] == 1
    assert "Cache hits" in health_page(repo.health(0), "performance")


async def test_market_budget_is_global_priority_deterministic_and_deferred_rotate(
    config, repo, token
):
    config.enabled_chains = "sol,base"
    config.max_market_enrich_per_scan = 1
    a = changed(token, contract_address="A" * 32, discovery_source={"trending"})
    b = changed(token, contract_address="B" * 32, discovery_source={"trending"})
    evm = changed(
        token, chain="base", contract_address="0x" + "a" * 40, discovery_source={"hot_search"}
    )

    class Source(SplitSource):
        def __init__(self):
            super().__init__(token)
            self.order = []

        async def discover(self, chain, source):
            if source == "trending" and chain == "sol":
                return [b, a]  # Arrival order cannot determine priority.
            return [evm] if chain == "base" and source == "hot_search" else []

        async def enrich_market(self, seed):
            self.order.append(seed.key)
            return seed

    source = Source()
    first = await run_scan(config, repo, source)
    assert source.order == [a.key]
    assert first.performance["deferred"]["market"] == 2 and first.errors == 0
    assert first.processed == 1
    await run_scan(config, repo, source)
    assert source.order == [a.key, b.key]
    assert repo.watch_priority(evm)["deferred"] == 2


def test_all_priority_tiers_are_explicit_and_stable(config, repo, token):
    scanner = Scanner(config, SplitSource(token), repo, None)
    pairs = [({"trending"}, 0), ({"hot_search"}, 1), ({"hot_search", "trending"}, 2)]
    for sources, tier in pairs:
        assert scanner.priority(changed(token, discovery_source=sources))[0] == tier
    repo.watch_discovered(token)
    for tier, expected in [("high", 3), ("medium", 4), ("low", 5)]:
        with repo.db:
            repo.db.execute("UPDATE watch_state SET tier=?", (tier,))
        assert scanner.priority(changed(token, discovery_source=set()))[0] == expected


@pytest.mark.parametrize(
    "score,tier,delay", [(70, "high", 150), (50, "medium", 600), (30, "low", 1800)]
)
def test_watch_tiers_due_times_expiration_and_discovery_refresh(
    config, repo, token, score, tier, delay
):
    token = changed(token, timestamp=token.timestamp - 10000)
    repo.watch_discovered(token)
    current = changed(token, timestamp=token.timestamp + 10000, discovery_source=set())
    repo.watch_polled(current, RevivalResult(score=score, status="WATCH", eligible=False), config)
    row = repo.watch_priority(current)
    assert row["tier"] == tier
    assert not repo.watch_due("sol", current.timestamp + delay - 1, config)
    assert (
        repo.watch_due("sol", current.timestamp + delay, config)[0]["last_seen"] == token.timestamp
    )
    config.watchlist_expire_hours = 1
    assert not repo.watch_due("sol", current.timestamp + delay, config)
    repo.watch_discovered(current)
    assert repo.watch_due("sol", current.timestamp + delay, config)


def test_watch_bootstrap_preserves_discovery_lifetime_and_warms_recent_candidates(
    config, repo, token
):
    repo.save_snapshot(token)
    recent = changed(token, timestamp=token.timestamp + 300, discovery_source=set())
    repo.save_snapshot(recent)
    due = repo.watch_due("sol", recent.timestamp, config)
    assert due[0]["last_seen"] == token.timestamp  # Polling never renews discovery lifetime.
    repo.watch_polled(recent, score_token(recent, [], Structure(), config), config)
    assert repo.watch_priority(token)["tier"] == "high"
    assert not repo.watch_due("sol", recent.timestamp + 149, config)
    assert repo.watch_due("sol", recent.timestamp + 150, config)


async def test_incremental_gmgn_uses_documented_from_and_returns_only_closed_bars(config, token):
    requests = []

    def handler(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await GMGNClient(config, http).candles_since(token, token.timestamp - 7200)
    assert requests[0]["from"] == str(int((token.timestamp - 7200) * 1000))
    assert requests[0]["resolution"] == "1h"


def test_schema_two_migration_preserves_all_existing_content(config, tmp_path, token):
    # Make a real previous-version database, including diagnostics and presentation state.
    # Tests must run on GitHub too: create v2 with the production v2 migration helpers.
    from revival_radar.storage.database import SNAPSHOT_FIELDS, TEXT_FIELDS, _migrate_diagnostics

    path = tmp_path / "v2.db"
    old = sqlite3.connect(path)
    old.row_factory = sqlite3.Row
    fields = ", ".join(f'"{f}" {"TEXT" if f in TEXT_FIELDS else "REAL"}' for f in SNAPSHOT_FIELDS)
    old.execute(
        f"CREATE TABLE token_snapshots (id INTEGER PRIMARY KEY,{fields},"
        "payload TEXT NOT NULL,UNIQUE(chain,contract_address,timestamp))"
    )
    old.execute(
        "CREATE TABLE alerts (id INTEGER PRIMARY KEY,timestamp REAL,chain TEXT,"
        "contract_address TEXT,score INTEGER,reason TEXT,telegram_message_id INTEGER,"
        "delivery_status TEXT)"
    )
    _migrate_diagnostics(old)
    old.execute("PRAGMA user_version=2")
    old.commit()
    legacy = Repository(old)
    legacy.save_snapshot(token)
    identity = legacy.begin_scan(token.timestamp, {"preset": "Strict"})
    legacy.record_evaluation(identity, token, score_token(token, [], Structure(), config), config)
    tables = ("token_snapshots", "alerts", "scan_runs", "evaluations", "state")
    saved = {name: [tuple(r) for r in old.execute(f"SELECT * FROM {name}")] for name in tables}
    old.close()
    migrated = connect(path)
    for name, records in saved.items():
        assert [tuple(r) for r in migrated.execute(f"SELECT * FROM {name}")] == records
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 4
    migrated.close()
    reopened = connect(path)
    assert reopened.execute("SELECT count(*) FROM token_snapshots").fetchone()[0] == 1
    reopened.close()


async def test_service_overrun_waits_for_completion_before_next_scan(
    config, repo, tmp_path, monkeypatch
):
    config.scan_interval_seconds = 0.001
    config.database_path = tmp_path / "overrun.db"
    runtime = RuntimeSettings(config)
    running = 0
    completed = []

    class Finished(Exception):
        pass

    class Controls:
        enabled = False

        def __init__(self, *args, **kwargs):
            pass

        def stop(self):
            pass

    class SlowScanner:
        def __init__(self, *args, **kwargs):
            pass

        async def scan_once(self):
            nonlocal running
            assert running == 0
            running += 1
            try:
                if completed:
                    raise Finished()
                await asyncio.sleep(0.02)  # Twenty times the cadence.
                completed.append(True)
            finally:
                running -= 1

    monkeypatch.setattr(service, "TelegramControls", Controls)
    monkeypatch.setattr(service, "Scanner", SlowScanner)
    async with httpx.AsyncClient() as http:
        with pytest.raises(Finished):
            await asyncio.wait_for(service.run_service(runtime, repo, http), timeout=2)
    assert completed == [True] and running == 0


async def test_security_cache_avoids_actual_http_and_ttl_zero_disables_reuse(
    config, repo, token, responses
):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=responses["security"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = GMGNClient(config, http)
        await manager(config, source, repo).security(token)
        await manager(config, source, repo).security(
            changed(token, timestamp=token.timestamp + 300)
        )
        assert requests == ["/v1/token/security"]
        config.security_cache_ttl_seconds = 0
        await manager(config, source, repo).security(
            changed(token, timestamp=token.timestamp + 301)
        )
    assert requests == ["/v1/token/security", "/v1/token/security"]


def test_legacy_watch_priority_uses_saved_scores(config, repo, token):
    old = changed(token, timestamp=token.timestamp - 10000)
    repo.save_snapshot(old)
    identity = repo.begin_scan(old.timestamp)
    repo.record_evaluation(
        identity, old, RevivalResult(score=70, status="REVIVING", eligible=False), config
    )
    due = repo.watch_due("sol", token.timestamp, config)
    assert due[0]["tier"] == "high" and due[0]["last_score"] == 70


async def test_kline_failure_is_not_cached_and_budget_retries_next_scan(config, repo, token):
    config.max_kline_fetch_per_scan = 1

    class Source(SplitSource):
        async def candles(self, token):
            self.calls["kline"] += 1
            raise DataSourceError("failed")

    source = Source(token)
    first = manager(config, source, repo)
    with pytest.raises(DataSourceError):
        await first.candles(token)
    assert repo.cache_entry(f"kline:1h:{config.kline_lookback_hours}", token) is None
    with pytest.raises(EnrichmentDeferred):
        await first.candles(token)
    with pytest.raises(DataSourceError):
        await manager(config, source, repo).candles(token)
    assert source.calls["kline"] == 2


async def test_scanner_polls_only_due_watchlist_tiers(config, repo, token, monkeypatch):
    now = token.timestamp
    monkeypatch.setattr("time.time", lambda: now)
    low = changed(token, timestamp=now - 10000, contract_address="A" * 32)
    high = changed(token, timestamp=now - 10000, contract_address="B" * 32)
    for seed, score in ((low, 30), (high, 70)):
        repo.watch_discovered(seed)
        repo.watch_polled(
            changed(seed, timestamp=now - 200),
            RevivalResult(score=score, status="WATCH", eligible=False),
            config,
        )

    class Source(SplitSource):
        def __init__(self):
            super().__init__(token)
            self.polled = []

        async def discover(self, *args):
            return []

        async def enrich_market(self, seed):
            self.polled.append(seed.key)
            return changed(token, contract_address=seed.contract_address, discovery_source=set())

    source = Source()
    report = await run_scan(config, repo, source)
    assert source.polled == [high.key]
    assert report.funnel["sol"]["watchlist_added"] == 1 and report.processed == 1
    assert repo.watch_priority(low)["last_polled"] == now - 200

import httpx
import pytest

from revival_radar.clients.gmgn import DataSourceError
from revival_radar.clients.telegram import TelegramClient
from revival_radar.demo import DemoSource
from revival_radar.scanner import Scanner, merge_tokens

from .conftest import changed


def test_dedup_chain_address_and_rank_merge(token):
    hot = changed(token, discovery_source={"hot_search"}, trending_rank=None)
    trend = changed(token, discovery_source={"trending"}, hot_search_rank=None)
    merged = merge_tokens([hot, trend])
    assert len(merged) == 1
    assert merged[0].discovery_source == {"hot_search", "trending"}
    assert merged[0].hot_search_rank == 8 and merged[0].trending_rank == 14
    evm = changed(token, chain="bsc", contract_address="0x" + "A" * 40)
    assert len(merge_tokens([evm, changed(evm, contract_address="0x" + "a" * 40)])) == 1
    assert len(merge_tokens([evm, changed(evm, chain="base")])) == 2


def test_merge_preserves_dangerous_flag(token):
    dangerous = changed(token, security={"dangerous": True, "flags": ["honeypot"]})
    assert merge_tokens([dangerous, token])[0].security.dangerous is True


async def test_stale_candles_do_not_trigger_alert(config, repo, token, candles, history):
    class StaleSource(DemoSource):
        async def candles(self, snapshot):
            return [c.model_copy(update={"timestamp": c.timestamp - 86400}) for c in candles]

    for snapshot in history:
        repo.save_snapshot(snapshot)
    async with httpx.AsyncClient() as http:
        scanner = Scanner(config, StaleSource(), repo, TelegramClient(config, http))
        _, result = await scanner.inspect(token)
    assert not result.eligible
    assert any("stale" in warning for warning in result.warnings)


async def test_full_dry_run_pipeline(config, repo):
    config.trending_discovery_enabled = True  # Preserve the two-source synthetic score fixture.
    source = DemoSource()
    for snapshot in source.histories():
        repo.save_snapshot(snapshot)

    def forbidden(request):
        pytest.fail("Offline scan must not use network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        scanner = Scanner(config, source, repo, TelegramClient(config, http))
        report = await scanner.scan_once()
    assert report.processed == 3 and report.errors == 0
    assert report.potential_alerts == 1 and report.sent == 0
    assert repo.db.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0
    assert repo.db.execute("SELECT count(*) FROM token_snapshots").fetchone()[0] == 13


async def test_chain_source_and_token_failure_isolation(config, repo):
    class BrokenSource(DemoSource):
        async def discover(self, chain, source):
            assert source == "hot_search"
            if chain == "bsc":
                raise DataSourceError("fixture failure")
            return await super().discover(chain, source)

        async def enrich(self, token):
            if token.symbol == "REVIVE":
                raise ValueError("bad token")
            return token

    config.enabled_chains = "sol,bsc"
    async with httpx.AsyncClient() as http:
        report = await Scanner(
            config, BrokenSource(), repo, TelegramClient(config, http)
        ).scan_once()
    assert report.processed == 2 and report.errors == 2 and report.sources_ok == 1
    assert report.discovered_by_source == {"sol": {"hot_search": 3}, "bsc": {}}
    assert report.source_errors == {"sol": {}, "bsc": {"hot_search": 1}}


@pytest.mark.parametrize("historical_source", ["hot_search", "trending"])
async def test_disappearing_token_refreshed_without_stale_metrics(
    config, repo, token, historical_source
):
    seen = []

    class MissingRankSource:
        async def discover(self, chain, source):
            return []

        async def enrich(self, seed):
            seen.append(seed)
            return seed

        async def candles(self, seed):
            return []

    repo.save_snapshot(changed(token, discovery_source={historical_source}))
    async with httpx.AsyncClient() as http:
        report = await Scanner(
            config, MissingRankSource(), repo, TelegramClient(config, http)
        ).scan_once()
    assert report.processed == 1
    assert seen[0].volume_5m is None and seen[0].hot_search_rank is None
    assert seen[0].trending_rank is None and not seen[0].discovery_source
    assert seen[0].ath_market_cap == token.ath_market_cap
    assert report.signals[0][1].discovery_context == "WATCHLIST_REVIVAL"
    assert not report.signals[0][1].eligible


async def test_live_mock_delivery_and_cooldown(config, repo):
    from pydantic import SecretStr

    config.dry_run = False
    config.trending_discovery_enabled = True  # Offline fixture also covers legacy source scores.
    config.telegram_bot_token = SecretStr("fixture-token")
    config.telegram_chat_id = "fixture-chat"
    source = DemoSource()
    for snapshot in source.histories():
        repo.save_snapshot(snapshot)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        scanner = Scanner(config, source, repo, TelegramClient(config, http))
        first = await scanner.scan_once()
        second = await scanner.scan_once()
    assert first.sent == 1 and second.sent == 0 and len(calls) == 1
    assert repo.db.execute("SELECT telegram_message_id FROM alerts").fetchone()[0] == 42

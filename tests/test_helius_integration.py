import asyncio
import json
import time

import httpx
import pytest
from pydantic import SecretStr

from revival_radar.clients.gmgn import DataSourceError
from revival_radar.clients.helius import ROUTE
from revival_radar.clients.providers import HybridDataSource
from revival_radar.clients.telegram import TelegramClient
from revival_radar.enrichment import Enrichment, EnrichmentDeferred
from revival_radar.metrics import ScanMetrics
from revival_radar.models.token import Security, SolanaMintVerification
from revival_radar.scanner import Scanner

from .conftest import changed
from .test_pipeline_optimization import SplitSource, run_scan

CACHE_KIND = "helius:mint:confirmed:v1"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def mint_evidence(token, **overrides):
    return {
        "provider": "helius",
        "contract_address": token.contract_address,
        "slot": 123456,
        "observed_at": token.timestamp,
        "commitment": "confirmed",
        "token_program": TOKEN_PROGRAM,
        "supply_raw": "1000000000000000000",
        "decimals": 9,
        "mint_renounced": True,
        "freeze_renounced": True,
        "extensions": [],
    } | overrides


class MintSource(SplitSource):
    helius_enabled = True

    def __init__(self, token, *, evidence=None, failure=None, candles=(), security=None):
        super().__init__(token, candles=candles, security=security)
        self.evidence = evidence or {}
        self.failure = failure

    async def verify_mint(self, token):
        self.calls["helius"] += 1
        if self.failure is not None:
            raise self.failure
        return mint_evidence(token, **({"observed_at": time.time()} | self.evidence))


@pytest.fixture
def helius_config(config, token, monkeypatch):
    config.helius_api_token = SecretStr("fixture-helius-token")
    monkeypatch.setattr("time.time", lambda: token.timestamp)
    return config


def manager(config, source, repo):
    return Enrichment(config, source, repo, ScanMetrics())


async def test_hybrid_activation_requires_key_and_runtime_settings_and_metrics_propagate(
    config, repo, token
):
    def forbidden(request):
        pytest.fail("Disabled Helius must make no HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        source = HybridDataSource(config, http)
        assert not source.helius_enabled
        current = await manager(config, source, repo).onchain(token)
        assert current.security.solana_mint is None
        enabled = config.model_copy(update={"helius_api_token": SecretStr("fixture-key")})
        source.config = enabled
        assert source.helius_enabled
        assert source.gmgn.config is source.helius.config is enabled
        metrics = ScanMetrics()
        source.metrics = metrics
        assert source.gmgn.metrics is source.helius.metrics is metrics
        disabled = enabled.model_copy(update={"helius_enabled": False})
        source.config = disabled
        assert not source.helius_enabled
        assert (await manager(disabled, source, repo).onchain(token)).security.solana_mint is None


@pytest.mark.parametrize("chain,enabled", [("sol", True), ("sol", False), ("base", True)])
async def test_mint_verification_is_sol_only_and_honors_disabled_source(
    helius_config, repo, token, chain, enabled
):
    current = (
        token if chain == "sol" else changed(token, chain=chain, contract_address="0x" + "a" * 40)
    )
    source = MintSource(current)
    source.helius_enabled = enabled
    enrichment = manager(helius_config, source, repo)
    result = await enrichment.onchain(current)
    assert source.calls["helius"] == int(chain == "sol" and enabled)
    assert (result.security.solana_mint is not None) == (chain == "sol" and enabled)
    assert enrichment.used["market"] == enrichment.used["security"] == 0


async def test_market_failure_skips_helius_and_no_activity_still_verifies_mint(
    helius_config, repo, token
):
    source = MintSource(changed(token, liquidity=0))
    first = await run_scan(helius_config, repo, source)
    assert source.calls["helius"] == 0 and first.funnel["sol"]["market_pass"] == 0
    source.discovery = source.market = changed(token, timestamp=token.timestamp + 300)
    second = await run_scan(helius_config, repo, source)
    assert source.calls["helius"] == 1
    assert second.signals[0][0].security.solana_mint is not None
    assert source.calls["security"] == source.calls["kline"] == 0
    assert not second.signals[0][1].returning_activity


async def test_helius_cache_survives_new_manager_and_expires_at_ttl(
    helius_config, repo, token, monkeypatch
):
    helius_config.helius_cache_ttl_seconds = 900
    source = MintSource(token)
    first = await manager(helius_config, source, repo).onchain(token)
    assert first.security.solana_mint.observed_at == token.timestamp
    monkeypatch.setattr("time.time", lambda: token.timestamp + 899)
    cached_manager = manager(helius_config, source, repo)
    cached = await cached_manager.onchain(changed(token, timestamp=token.timestamp + 899))
    assert source.calls["helius"] == 1
    assert cached.security.solana_mint.observed_at == token.timestamp
    assert cached_manager.metrics.cache["helius_hit"] == 1
    assert cached_manager.used["helius"] == 0
    monkeypatch.setattr("time.time", lambda: token.timestamp + 900)
    refreshed = await manager(helius_config, source, repo).onchain(
        changed(token, timestamp=token.timestamp + 900)
    )
    assert source.calls["helius"] == 2
    assert refreshed.security.solana_mint.observed_at == token.timestamp + 900


@pytest.mark.parametrize("poison", ["wrong_address", "future_evidence", "bad_slot", "json"])
async def test_poisoned_helius_cache_never_becomes_verified_evidence(
    helius_config, repo, token, poison
):
    payload = mint_evidence(token)
    if poison == "wrong_address":
        payload["contract_address"] = "1" * 32
    elif poison == "future_evidence":
        payload["observed_at"] += 60
    elif poison == "bad_slot":
        payload["slot"] = "123456"
    repo.cache_save(
        CACHE_KIND,
        token,
        "not-json" if poison == "json" else json.dumps(payload),
        token.timestamp + 900,
    )
    source = MintSource(token)
    result = await manager(helius_config, source, repo).onchain(token)
    assert source.calls["helius"] == 1
    assert result.security.solana_mint.contract_address == token.contract_address
    assert result.security.solana_mint.observed_at == token.timestamp
    assert result.security.solana_mint.slot == 123456


async def test_future_cache_timestamp_and_shorter_configured_ttl_require_refresh(
    helius_config, repo, token, monkeypatch
):
    source = MintSource(token)
    await manager(helius_config, source, repo).onchain(token)
    with repo.db:
        repo.db.execute(
            "UPDATE enrichment_cache SET fetched_at=? WHERE kind=?",
            (
                token.timestamp + 60,
                CACHE_KIND,
            ),
        )
    await manager(helius_config, source, repo).onchain(token)
    assert source.calls["helius"] == 2
    helius_config.helius_cache_ttl_seconds = 100
    monkeypatch.setattr("time.time", lambda: token.timestamp + 100)
    await manager(helius_config, source, repo).onchain(
        changed(token, timestamp=token.timestamp + 100)
    )
    assert source.calls["helius"] == 3


async def test_helius_budget_is_independent_and_cache_hit_needs_no_slot(helius_config, repo, token):
    helius_config.max_helius_verify_per_scan = 1
    helius_config.max_market_enrich_per_scan = 0
    helius_config.max_security_enrich_per_scan = 0
    source = MintSource(token)
    enrichment = manager(helius_config, source, repo)
    await enrichment.onchain(token)
    assert enrichment.used["helius"] == 1
    await enrichment.onchain(changed(token, timestamp=token.timestamp + 1))
    assert source.calls["helius"] == 1
    other = changed(token, contract_address="1" * 32)
    with pytest.raises(EnrichmentDeferred):
        await enrichment.onchain(other)
    assert enrichment.metrics.deferred["helius"] == 1
    assert enrichment.used["market"] == enrichment.used["security"] == 0
    helius_config.max_helius_verify_per_scan = 0
    fresh_manager = manager(helius_config, source, repo)
    await fresh_manager.onchain(changed(token, timestamp=token.timestamp + 2))
    assert fresh_manager.used["helius"] == 0


async def test_disabled_cache_and_failed_attempts_do_not_bypass_helius_budget(
    helius_config, repo, token
):
    helius_config.helius_cache_ttl_seconds = 0
    helius_config.max_helius_verify_per_scan = 1
    source = MintSource(token, failure=DataSourceError("fixture unavailable"))
    enrichment = manager(helius_config, source, repo)
    with pytest.raises(DataSourceError):
        await enrichment.onchain(token)
    assert enrichment.used["helius"] == 1
    assert repo.cache_entry(CACHE_KIND, token) is None
    with pytest.raises(EnrichmentDeferred):
        await enrichment.onchain(token)
    source.failure = None
    await manager(helius_config, source, repo).onchain(token)
    await manager(helius_config, source, repo).onchain(token)
    assert source.calls["helius"] == 3
    assert repo.cache_entry(CACHE_KIND, token) is None


@pytest.mark.parametrize("failure_kind", ["request", "budget"])
async def test_unknown_helius_preserves_existing_gmgn_eligibility_and_market_data(
    helius_config, repo, token, history, candles, failure_kind
):
    for old in history:
        repo.save_snapshot(old)
    source = MintSource(
        token,
        candles=candles,
        failure=DataSourceError("fixture-helius-secret") if failure_kind == "request" else None,
    )
    if failure_kind == "budget":
        helius_config.max_helius_verify_per_scan = 0
    report = await run_scan(helius_config, repo, source)
    current, signal = report.signals[0]
    assert report.errors == 0 and report.potential_alerts == 1
    assert signal.eligible and current.security.solana_mint is None
    assert source.calls["market"] == source.calls["security"] == source.calls["kline"] == 1
    assert any("helius" in warning.lower() for warning in signal.warnings)
    assert "fixture-helius-secret" not in json.dumps(report.performance)
    assert current.price == token.price and current.volume_5m == token.volume_5m


@pytest.mark.parametrize("malformed", ["wrong_address", "future", "string_boolean"])
async def test_malformed_fresh_evidence_is_unknown_and_never_cached_as_verification(
    helius_config, repo, token, history, candles, malformed
):
    for old in history:
        repo.save_snapshot(old)
    override = {
        "wrong_address": {"contract_address": "1" * 32},
        "future": {"observed_at": token.timestamp + 1},
        "string_boolean": {"mint_renounced": "false"},
    }[malformed]
    source = MintSource(token, evidence=override, candles=candles)
    report = await run_scan(helius_config, repo, source)
    current, signal = report.signals[0]
    assert report.errors == 0 and signal.eligible
    assert current.security.solana_mint is None
    assert repo.cache_entry(CACHE_KIND, token) is None
    assert source.calls["helius"] == source.calls["security"] == 1
    assert any("helius" in warning.lower() for warning in signal.warnings)


@pytest.mark.parametrize("failure_kind", ["request", "budget"])
async def test_stale_source_evidence_is_not_claimed_verified_after_helius_failure(
    helius_config, repo, token, failure_kind
):
    stale = SolanaMintVerification.model_validate(
        mint_evidence(
            token, observed_at=token.timestamp - helius_config.helius_cache_ttl_seconds - 1
        )
    )
    prior = changed(token, security=token.security.model_copy(update={"solana_mint": stale}))
    source = MintSource(
        prior, failure=DataSourceError("fixture unavailable") if failure_kind == "request" else None
    )
    if failure_kind == "budget":
        helius_config.max_helius_verify_per_scan = 0
    report = await run_scan(helius_config, repo, source)
    current, _ = report.signals[0]
    assert current.security.solana_mint is None
    assert current.security.mint_renounced == token.security.mint_renounced
    assert current.security.freeze_renounced == token.security.freeze_renounced
    trace = next(
        t
        for t in report.performance["candidate_traces"]
        if t["contract_address"] == token.contract_address
    )
    assert trace["helius_verified"] is False
    assert repo.cache_entry(CACHE_KIND, token) is None


async def test_market_rejection_clears_stale_evidence_but_preserves_known_adverse_flags(
    helius_config, repo, token
):
    stale = SolanaMintVerification.model_validate(
        mint_evidence(
            token,
            observed_at=token.timestamp - helius_config.helius_cache_ttl_seconds - 1,
            mint_renounced=False,
        )
    )
    prior = changed(
        token,
        security=token.security.model_copy(
            update={
                "solana_mint": stale,
                "mint_renounced": False,
                "dangerous": True,
                "flags": ["previous-known-adverse-authority"],
            }
        ),
    )
    # Discovery passes; the new market response fails liquidity after stale evidence is cleared.
    source = MintSource(prior)
    source.market = changed(prior, liquidity=0)
    report = await run_scan(helius_config, repo, source)
    current, signal = report.signals[0]
    assert source.calls["market"] == 1 and source.calls["helius"] == 0
    assert current.security.solana_mint is None
    assert current.security.mint_renounced is False and current.security.dangerous is True
    assert "previous-known-adverse-authority" in current.security.flags
    assert not signal.eligible
    assert not report.performance["candidate_traces"][0].get("helius_verified", False)


async def test_real_hybrid_scanner_helius_429_has_one_terminal_candidate_error(
    helius_config, repo, responses
):
    address = "GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump"
    responses["hot_searches"]["data"][0]["tokens"][0]["address"] = address
    responses["info"]["data"]["address"] = address
    calls = []

    def handler(request):
        calls.append((request.url.host, request.url.path))
        if request.url.host == "mainnet.helius-rpc.com":
            return httpx.Response(
                429,
                headers={"Retry-After": "60"},
                json={"error": "fixture-provider-body-never-retain"},
            )
        assert request.url.path in {"/v1/market/hot_searches", "/v1/token/info"}
        return httpx.Response(200, json=responses[request.url.path.rsplit("/", 1)[-1]])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = HybridDataSource(helius_config, http)
        report = await Scanner(
            helius_config, source, repo, TelegramClient(helius_config, http)
        ).scan_once()
    assert len(calls) == 3
    assert report.processed == 1 and report.errors == 0
    performance = report.performance
    assert performance["calls"][ROUTE] == 1
    assert performance["http_errors"][f"{ROUTE}:helius_429"] == 1
    errors = performance["error_events"]
    assert len(errors) == 1
    assert errors[0]["stage"] == "helius" and errors[0]["category"] == "helius_429"
    assert errors[0]["chain"] == "sol" and errors[0]["contract_address"] == address
    assert "fixture-provider-body-never-retain" not in json.dumps(performance)
    assert report.signals[0][0].security.solana_mint is None


@pytest.mark.parametrize("authority", ["mint_renounced", "freeze_renounced"])
async def test_active_authority_blocks_before_later_gmgn_safe_enrichment(
    helius_config, repo, token, history, candles, authority
):
    for old in history:
        repo.save_snapshot(old)
    repo.cache_save(
        "security", token, Security(dangerous=False).model_dump_json(), token.timestamp + 1800
    )
    source = MintSource(
        token, evidence={authority: False}, candles=candles, security=Security(dangerous=False)
    )
    report = await run_scan(helius_config, repo, source)
    current, signal = report.signals[0]
    assert current.security.dangerous is True and not signal.eligible
    assert getattr(current.security, authority) is False
    assert getattr(current.security.solana_mint, authority) is False
    assert current.security.flags
    assert source.calls["helius"] == 1
    assert source.calls["security"] == source.calls["kline"] == 0
    assert report.potential_alerts == 0


async def test_safe_helius_evidence_never_clears_existing_gmgn_danger(helius_config, repo, token):
    risky = changed(
        token,
        security=token.security.model_dump()
        | {
            "dangerous": True,
            "flags": ["gmgn-existing-risk"],
        },
    )
    result = await manager(helius_config, MintSource(risky), repo).onchain(risky)
    assert result.security.mint_renounced and result.security.freeze_renounced
    assert result.security.dangerous is True
    assert "gmgn-existing-risk" in result.security.flags


@pytest.mark.parametrize("security_cached", [False, True])
async def test_verified_evidence_is_reapplied_after_gmgn_security_without_cache_contamination(
    helius_config, repo, token, history, candles, security_cached
):
    for old in history:
        repo.save_snapshot(old)
    gmgn = token.security.model_copy(
        update={
            "mint_renounced": False,
            "freeze_renounced": False,
            "dangerous": True,
            "flags": ["gmgn-later-risk"],
        }
    )
    if security_cached:
        repo.cache_save("security", token, gmgn.model_dump_json(), token.timestamp + 1800)
    source = MintSource(token, candles=candles, security=gmgn)
    report = await run_scan(helius_config, repo, source)
    current, signal = report.signals[0]
    assert source.calls["security"] == int(not security_cached)
    assert current.security.solana_mint is not None
    assert current.security.mint_renounced and current.security.freeze_renounced
    assert current.security.dangerous and "gmgn-later-risk" in current.security.flags
    assert not signal.eligible
    assert any("disagree" in warning.lower() for warning in current.data_warnings)
    cached = json.loads(repo.cache_entry("security", token)["payload"])
    assert cached["solana_mint"] is None
    assert cached["mint_renounced"] is False and cached["freeze_renounced"] is False


async def test_token2022_extensions_and_conflicts_remain_explicitly_unassessed(
    helius_config, repo, token
):
    current = changed(token, security=token.security.model_dump() | {"mint_renounced": False})
    source = MintSource(
        current,
        evidence={
            "token_program": TOKEN_2022_PROGRAM,
            "extensions": ["transferFeeConfig"],
        },
    )
    verified = await manager(helius_config, source, repo).onchain(current)
    assert verified.security.solana_mint.extensions == ["transferFeeConfig"]
    assert verified.security.solana_mint.token_program == TOKEN_2022_PROGRAM
    assert any("disagree" in warning.lower() for warning in verified.data_warnings)
    assert any(
        "extension" in warning.lower() and "assess" in warning.lower()
        for warning in verified.data_warnings
    )


async def test_helius_wall_time_budget_cancels_slow_call_and_defers_remaining_verification(
    helius_config, repo, token
):
    helius_config.helius_scan_budget_seconds = 0.002

    class SlowSource(MintSource):
        async def verify_mint(self, current):
            self.calls["helius"] += 1
            await asyncio.sleep(0.05)
            return mint_evidence(current)

    source = SlowSource(token)
    enrichment = manager(helius_config, source, repo)
    with pytest.raises(DataSourceError):
        await enrichment.onchain(token)
    assert repo.cache_entry(CACHE_KIND, token) is None
    with pytest.raises(EnrichmentDeferred):
        await enrichment.onchain(token)
    assert source.calls["helius"] == 1
    assert enrichment.used["market"] == enrichment.used["security"] == 0


async def test_helius_evidence_persists_inside_payloads_without_schema_or_market_rewrite(
    helius_config, repo, token, history, candles
):
    for old in history:
        repo.save_snapshot(old)
    source = MintSource(token, candles=candles)
    report = await run_scan(helius_config, repo, source)
    current, signal = report.signals[0]
    assert signal.eligible and source.calls["security"] == 1
    for field in (
        "price",
        "market_cap",
        "ath_market_cap",
        "liquidity",
        "volume_5m",
        "volume_1h",
        "tx_5m",
        "tx_1h",
        "holders",
        "token_age_seconds",
    ):
        assert getattr(current, field) == getattr(token, field)
    assert repo.db.execute("PRAGMA user_version").fetchone()[0] == 4
    stored = json.loads(
        repo.db.execute(
            "SELECT payload FROM token_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert stored["security"]["solana_mint"] == current.security.solana_mint.model_dump()
    identity = repo.db.execute(
        "SELECT id FROM evaluations WHERE scan_id=?", (report.scan_id,)
    ).fetchone()[0]
    presentation = repo.presentation_detail("e", identity)
    assert presentation["token"]["security"]["solana_mint"] == stored["security"]["solana_mint"]
    assert "fixture-helius-token" not in json.dumps(presentation)
    assert "_gmgn_security" not in stored and "_market_security_info" not in stored

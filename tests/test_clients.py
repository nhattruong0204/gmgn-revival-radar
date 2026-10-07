import asyncio
import json
import time

import httpx
import pytest

from revival_radar.clients.gmgn import DataSourceError, GMGNClient
from revival_radar.clients.normalization import boolean, enriched_token, number, parse_candles
from revival_radar.clients.telegram import TelegramClient, format_alert, format_why
from revival_radar.models.signal import RevivalResult
from revival_radar.models.token import Security

from .conftest import changed


async def test_documented_routes_and_normalization(config, responses, token):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["X-APIKEY"] == "fixture-key"
        assert abs(int(request.url.params["timestamp"]) - time.time()) < 5
        assert request.url.params["client_id"]
        suffix = request.url.path.rsplit("/", 1)[-1]
        if suffix == "hot_searches":
            assert request.method == "POST"
            assert json.loads(request.content)["params"][0]["chain"] == "sol"
        elif suffix == "token_kline":
            assert request.url.params["resolution"] == "1h"
            assert int(request.url.params["to"]) == int(token.timestamp * 1000)
            suffix = "kline"
        return httpx.Response(200, json=responses[suffix])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GMGNClient(config, http)
        hot = await client.discover("sol", "hot_search")
        trending = await client.discover("sol", "trending")
        assert hot[0].hot_search_rank == 8
        assert trending[0].trending_rank == 8
        enriched = await client.enrich(changed(hot[0], timestamp=token.timestamp))
        assert enriched.market_cap == 820000
        assert enriched.volume_5m == 61000
        assert enriched.price_change_5m == pytest.approx(10)
        assert enriched.price_change_1h == pytest.approx(25)
        assert enriched.token_age_seconds == 11 * 86400
        candles = await client.candles(token)
        assert len(candles) == 100
        assert candles[0].volume_usd == 1000  # amount, NOT token-unit volume
    assert len({r.url.params["client_id"] for r in requests}) == len(requests)


@pytest.mark.parametrize("chain", ["sol", "bsc", "base", "robinhood", "arc"])
async def test_all_chain_mappings(chain, config):
    def handler(request):
        assert request.url.params["chain"] == chain
        return httpx.Response(200, json={"code": 0, "data": {"rank": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await GMGNClient(config, http).discover(chain, "trending") == []


async def test_malformed_token_is_skipped(config, responses):
    payload = responses["rank"]
    payload["data"]["rank"].extend([{"address": "invalid"}, None, {"address": "0x123"}])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        assert len(await GMGNClient(config, http).discover("sol", "trending")) == 1


async def test_mismatched_token_info_rejected(config, token, responses):
    payload = responses["info"]
    payload["data"]["address"] = "different-address"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        with pytest.raises(DataSourceError, match="address mismatch"):
            await GMGNClient(config, http).enrich(token)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_retry_transient_http(config, monkeypatch, status):
    calls = []

    async def no_wait(delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status if len(calls) == 1 else 200, json={"code": 0, "data": {"rank": []}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await GMGNClient(config, http).discover("sol", "trending") == []
    assert len(calls) == 2
    assert calls[0].url.params["client_id"] != calls[1].url.params["client_id"]


async def test_shared_rate_limit_cooldown(config):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, headers={"Retry-After": "300"}, json={"code": 429, "reset_at": time.time() + 300}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GMGNClient(config, http)
        for chain in ["sol", "bsc"]:
            with pytest.raises(DataSourceError):
                await client.discover(chain, "trending")
    assert len(calls) == 1


async def test_timeout_and_auth_errors_sanitized(config, monkeypatch):
    async def no_wait(delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture-key must not leak")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DataSourceError) as error:
            await GMGNClient(config, http).discover("sol", "trending")
        assert "fixture-key" not in str(error.value)
    assert len(calls) == config.http_attempts


@pytest.mark.parametrize(
    "payload", [{"code": 0, "data": None}, {"code": 0, "data": {}}, {"code": 401}]
)
async def test_bad_envelopes(config, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        with pytest.raises(DataSourceError):
            await GMGNClient(config, http).discover("sol", "trending")


def test_unknown_values_and_chain_specific_security(token, responses):
    assert number("nan") is None and number("") is None and number(-1) is None
    assert boolean("unknown") is None and boolean(-1) is None
    info = responses["info"]["data"]
    info["creation_timestamp"] = 0
    info["price"]["volume_1h"] = ""
    info["price"]["price_1h"] = "0"
    seed = changed(
        token,
        token_age_seconds=None,
        volume_1h=None,
        price_change_1h=None,
        ath_market_cap=None,
        security={},
    )
    parsed = enriched_token(
        seed,
        info,
        {"renounced_mint": False},
    )
    assert parsed.token_age_seconds is None
    assert parsed.volume_1h is None
    assert parsed.price_change_1h is None
    assert parsed.ath_market_cap is None
    assert parsed.security.dangerous is True
    evm = changed(seed, chain="base", contract_address="0x" + "A" * 40)
    parsed = enriched_token(evm, info, {"renounced_mint": False, "renounced_freeze_account": False})
    assert parsed.security.dangerous is None
    assert parsed.security.mint_renounced is None
    assert parsed.contract_address == "0x" + "a" * 40
    assert Security().top10_ratio is None


def test_incomplete_or_invalid_candles_excluded(responses, token):
    payload = responses["kline"]["data"]
    payload["list"].extend(
        [
            {"time": token.timestamp, "open": 1, "high": 2, "low": 1, "close": 2},
            {"time": token.timestamp - 3600, "open": 2, "high": 1, "low": 1, "close": 2},
        ]
    )
    assert len(parse_candles(payload, token.timestamp)) == 100


def test_live_millisecond_candles(responses, token):
    payload = responses["kline"]["data"]
    expected = parse_candles(payload, token.timestamp)
    for candle in payload["list"]:
        candle["time"] *= 1000
    assert parse_candles(payload, token.timestamp) == expected


@pytest.mark.parametrize("inner_code", [0, 401])
async def test_live_nested_trending_envelope(config, responses, inner_code):
    payload = {"code": 0, "data": {"code": inner_code, "data": responses["rank"]["data"]}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        client = GMGNClient(config, http)
        if inner_code == 0:
            assert len(await client.discover("sol", "trending")) == 1
        else:
            with pytest.raises(DataSourceError, match="upstream error"):
                await client.discover("sol", "trending")


async def test_telegram_html_and_dry_run(config, token):
    result = RevivalResult(score=80, status="REVIVING", eligible=True, reasons=["x < y & z"])
    message = format_alert(changed(token, symbol="<b>& coin"), result)
    assert "&lt;b&gt;&amp;" in message
    assert "x &lt; y &amp; z" in format_why(token, result, {})
    assert len(message) < 4096

    def forbidden(request):
        pytest.fail("Dry-run must never send HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        assert (await TelegramClient(config, http).send_text(message)).status == "dry_run"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("ok", "sent"),
        ("timeout", "unknown"),
        ("500", "unknown"),
        ("403", "failed"),
        ("429", "failed"),
    ],
)
async def test_telegram_failures(config, kind, expected, caplog):
    config.dry_run = False
    config.telegram_bot_token = "not-a-real-secret"
    # Settings assignment is not validated; use the declared SecretStr type.
    from pydantic import SecretStr

    config.telegram_bot_token = SecretStr("not-a-real-secret")
    config.telegram_chat_id = "fixture-chat"

    def handler(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("secret request URL")
        if kind == "ok":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
        return httpx.Response(int(kind), json={"ok": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await TelegramClient(config, http).send_text("hello")
    assert result.status == expected
    assert "not-a-real-secret" not in caplog.text

import asyncio
import copy
import json
from datetime import UTC, datetime
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from revival_radar.clients.helius import (
    ROUTE,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    HeliusClient,
    HeliusDataError,
    is_pubkey,
    parse_mint_response,
)

MINT = "GTBxUiw6wJdmmkCGZgRHLyYxqu1vG4KtRpeox6yDpump"
AUTHORITY = "11111111111111111111111111111111"
REQUEST_ID = "fixture-request-id"


def configuration(**updates):
    return SimpleNamespace(
        **(
            {
                "helius_api_token": SecretStr("private-helius-fixture-key"),
                "helius_request_spacing_seconds": 0.00001,
                "helius_http_timeout_seconds": 10,
                "helius_http_attempts": 2,
                "helius_retry_max_wait_seconds": 2,
            }
            | updates
        )
    )


def response_payload(request_id=REQUEST_ID, *, program=TOKEN_PROGRAM):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "context": {"slot": 12345},
            "value": {
                "owner": program,
                "executable": False,
                "data": {
                    "program": "spl-token" if program == TOKEN_PROGRAM else "spl-token-2022",
                    "parsed": {
                        "type": "mint",
                        "info": {
                            "mintAuthority": None,
                            "freezeAuthority": None,
                            "isInitialized": True,
                            "supply": "969518021000000",
                            "decimals": 6,
                        },
                    },
                },
            },
        },
    }


def info(payload):
    return payload["result"]["value"]["data"]["parsed"]["info"]


def test_parsed_mint_retains_only_documented_evidence():
    payload = response_payload()
    payload["untrusted_provider_message"] = "private-provider-body"
    result = parse_mint_response(payload, MINT, REQUEST_ID, 123.5)
    assert result == {
        "contract_address": MINT,
        "provider": "helius",
        "observed_at": 123.5,
        "slot": 12345,
        "commitment": "confirmed",
        "token_program": TOKEN_PROGRAM,
        "supply_raw": "969518021000000",
        "decimals": 6,
        "mint_renounced": True,
        "freeze_renounced": True,
        "extensions": [],
    }
    assert "private-provider-body" not in json.dumps(result)


def test_token2022_extensions_are_names_only_without_safety_claim():
    # Official token-2022 interface declare_id; avoid a fixture that only mirrors
    # the client's constant and could accept an incorrect program address.
    official_program = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    assert official_program == TOKEN_2022_PROGRAM
    payload = response_payload(program=official_program)
    info(payload)["extensions"] = [
        {"extension": "transferHook", "state": {"authority": AUTHORITY}},
        {"extension": "permanentDelegate", "state": {"delegate": AUTHORITY}},
    ]
    result = parse_mint_response(payload, MINT, REQUEST_ID, 123.5)
    assert result["token_program"] == TOKEN_2022_PROGRAM
    assert result["extensions"] == ["transferHook", "permanentDelegate"]
    assert "dangerous" not in result
    assert "state" not in json.dumps(result)


@pytest.mark.parametrize("field", ["mintAuthority", "freezeAuthority"])
def test_existing_authority_is_not_renounced(field):
    payload = response_payload()
    info(payload)[field] = AUTHORITY
    result = parse_mint_response(payload, MINT, REQUEST_ID, 123.5)
    assert result["mint_renounced" if field == "mintAuthority" else "freeze_renounced"] is False


@pytest.mark.parametrize("field", ["mintAuthority", "freezeAuthority"])
def test_missing_authority_does_not_mean_renounced(field):
    payload = response_payload()
    info(payload).pop(field)
    with pytest.raises(HeliusDataError, match="authority evidence unavailable"):
        parse_mint_response(payload, MINT, REQUEST_ID, 123.5)


@pytest.mark.parametrize("authority", [False, 0, "", "0" * 44, "2" * 32, {}, []])
def test_malformed_authority_rejected(authority):
    payload = response_payload()
    info(payload)["mintAuthority"] = authority
    with pytest.raises(HeliusDataError):
        parse_mint_response(payload, MINT, REQUEST_ID, 123.5)


@pytest.mark.parametrize("supply", [0, "", "-1", "1.0", "1e9", "１２", str(2**64), "9" * 100])
def test_malformed_supply_rejected(supply):
    payload = response_payload()
    info(payload)["supply"] = supply
    with pytest.raises(HeliusDataError, match="supply or decimals invalid"):
        parse_mint_response(payload, MINT, REQUEST_ID, 123.5)


@pytest.mark.parametrize("decimals", [True, None, "6", -1, 256, 6.0])
def test_malformed_decimals_rejected(decimals):
    payload = response_payload()
    info(payload)["decimals"] = decimals
    with pytest.raises(HeliusDataError, match="supply or decimals invalid"):
        parse_mint_response(payload, MINT, REQUEST_ID, 123.5)


@pytest.mark.parametrize("slot", [True, None, "123", -1, 3.0])
def test_malformed_context_slot_rejected(slot):
    payload = response_payload()
    payload["result"]["context"]["slot"] = slot
    with pytest.raises(HeliusDataError, match="context unavailable"):
        parse_mint_response(payload, MINT, REQUEST_ID, 123.5)


@pytest.mark.parametrize("extensions", [None, {}, ["transferHook"], [{}], [{"extension": "bad\n"}]])
def test_malformed_extensions_rejected(extensions):
    payload = response_payload(program=TOKEN_2022_PROGRAM)
    info(payload)["extensions"] = extensions
    with pytest.raises(HeliusDataError):
        parse_mint_response(payload, MINT, REQUEST_ID, 123.5)


def test_owner_program_type_and_initialization_are_bound():
    valid = response_payload()
    invalid = []
    payload = copy.deepcopy(valid)
    payload["id"] = "other-request"
    invalid.append(payload)
    payload = copy.deepcopy(valid)
    payload["result"]["value"]["owner"] = AUTHORITY
    invalid.append(payload)
    payload = copy.deepcopy(valid)
    payload["result"]["value"]["data"]["program"] = "spl-token-2022"
    invalid.append(payload)
    payload = copy.deepcopy(valid)
    payload["result"]["value"]["data"]["parsed"]["type"] = "account"
    invalid.append(payload)
    payload = copy.deepcopy(valid)
    payload["result"]["value"]["data"] = ["binary-data", "base64"]
    invalid.append(payload)
    payload = copy.deepcopy(valid)
    payload["result"]["value"]["executable"] = 0
    invalid.append(payload)
    payload = copy.deepcopy(valid)
    info(payload)["isInitialized"] = False
    invalid.append(payload)
    for payload in invalid:
        with pytest.raises(HeliusDataError):
            parse_mint_response(payload, MINT, REQUEST_ID, 123.5)


def test_account_absence_is_distinct_from_authority_renunciation():
    payload = response_payload()
    payload["result"]["value"] = None
    with pytest.raises(HeliusDataError) as error:
        parse_mint_response(payload, MINT, REQUEST_ID, 123.5)
    assert error.value.category == "helius_missing_account"


@pytest.mark.parametrize("address", [None, "", "0x123", "2" * 32, "1" * 31, "1" * 33, "0" * 44])
def test_base58_byte_length_is_checked(address):
    assert not is_pubkey(address)


async def test_documented_rpc_request_and_attempt_metrics():
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.url.host == "mainnet.helius-rpc.com"
        assert request.url.params["api-key"] == "private-helius-fixture-key"
        assert body["method"] == "getAccountInfo"
        assert body["params"] == [MINT, {"encoding": "jsonParsed", "commitment": "confirmed"}]
        return httpx.Response(200, json=response_payload(body["id"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        result = await client.fetch_mint(MINT)
        assert result["observed_at"] > 0
        assert client.metrics.calls[ROUTE] == 1
        assert client.metrics.http_statuses[f"{ROUTE}:200"] == 1
        assert not client.metrics.http_errors
    assert len(requests) == 1


async def test_invalid_address_or_missing_api_token_make_no_requests():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected HTTP request"))
    ) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint("2" * 32)
        assert error.value.category == "helius_invalid_address"
        client.config = configuration(helius_api_token=SecretStr(""))
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == "helius_auth"
        assert not client.metrics.calls


@pytest.mark.parametrize("status", [401, 403, 400, 404])
async def test_http_rejections_do_not_expose_url_key_or_response_body(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="private-provider-body")
        )
    ) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        rendered = str(error.value) + json.dumps(client.metrics.http_errors)
        assert "private" not in rendered
        assert "https://" not in rendered
        assert "api-key" not in rendered
        assert client.metrics.calls[ROUTE] == 1


async def test_redirects_are_not_followed_by_shared_client():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://unexpected-provider.example/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == "helius_http"
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["timeout", "transport", "5xx"])
async def test_transient_failure_retry_recovers_and_counts_attempts(monkeypatch, failure):
    calls = []

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("private-key https://private", request=request)
            if failure == "transport":
                raise httpx.ConnectError("private-key https://private", request=request)
            return httpx.Response(503, text="private-provider-body")
        return httpx.Response(200, json=response_payload(json.loads(request.content)["id"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        assert (await client.fetch_mint(MINT))["mint_renounced"] is True
        assert client.metrics.calls[ROUTE] == 2
        assert client.metrics.recovered_requests[ROUTE] == 1
        assert client.metrics.http_errors[f"{ROUTE}:helius_{failure}"] == 1
        assert "private" not in json.dumps(client.metrics.http_errors)
        # A recovered HTTP attempt is not a terminal candidate error. Terminal
        # events are the scanner's responsibility so they carry token identity.
        assert client.metrics.errors == []
    assert json.loads(calls[0].content)["id"] != json.loads(calls[1].content)["id"]


@pytest.mark.parametrize("retry_after", [None, "NaN", "300"])
async def test_429_stops_immediately_and_cooldown_survives_config_refresh(retry_after):
    calls = []

    def handler(request):
        calls.append(request)
        headers = {"Retry-After": retry_after} if retry_after else {}
        return httpx.Response(429, headers=headers, text="private-provider-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(helius_http_attempts=3), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == "helius_429"
        client.config = configuration(helius_http_attempts=4)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == "helius_cooldown"
        assert client.metrics.calls[ROUTE] == 1
        assert client.metrics.http_errors[f"{ROUTE}:helius_429"] == 1
        assert client.metrics.http_errors[f"{ROUTE}:helius_cooldown"] == 1
    assert len(calls) == 1


async def test_http_date_retry_after_is_respected(monkeypatch):
    import revival_radar.clients.helius as helius

    clock = 1_700_000_000
    monkeypatch.setattr(helius.time, "time", lambda: clock)
    monkeypatch.setattr(helius.time, "monotonic", lambda: 1000)
    date = format_datetime(datetime.fromtimestamp(clock + 120, UTC), usegmt=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": date})
        )
    ) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError):
            await client.fetch_mint(MINT)
        assert client._blocked_until == 1120


async def test_long_server_retry_delay_blocks_without_wait_or_flood():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, headers={"Retry-After": "120"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        for _ in range(2):
            with pytest.raises(HeliusDataError):
                await client.fetch_mint(MINT)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["bad_json", "wrong_id", "rpc_error"])
async def test_malformed_rpc_does_not_retry_or_expose_provider_text(kind):
    def handler(request):
        if kind == "bad_json":
            return httpx.Response(200, text="private-provider-body")
        payload = response_payload(
            "other-id" if kind == "wrong_id" else json.loads(request.content)["id"]
        )
        if kind == "rpc_error":
            payload["error"] = {"code": -32000, "message": "private-provider-body"}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert "private" not in str(error.value)
        assert client.metrics.calls[ROUTE] == 1


async def test_cancellation_releases_shared_request_lock():
    entered = asyncio.Event()
    block = asyncio.Event()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await block.wait()
        return httpx.Response(200, json=response_payload(json.loads(request.content)["id"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        task = asyncio.create_task(client.fetch_mint(MINT))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await asyncio.wait_for(client.fetch_mint(MINT), timeout=1))["mint_renounced"]


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_opens_shared_300_second_circuit(monkeypatch, status):
    import revival_radar.clients.helius as helius

    monkeypatch.setattr(helius.time, "monotonic", lambda: 1000)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="private-provider-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == "helius_auth"
        assert client._blocked_until == 1300
        client.config = configuration()
        for _ in range(20):
            with pytest.raises(HeliusDataError) as error:
                await client.fetch_mint(MINT)
            assert error.value.category == "helius_cooldown"
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["timeout", "transport", "5xx"])
async def test_exhausted_transient_attempts_open_30_second_circuit(monkeypatch, failure):
    import revival_radar.clients.helius as helius

    monkeypatch.setattr(helius.time, "monotonic", lambda: 1000)
    requests = []

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    def handler(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-provider-body", request=request)
        if failure == "transport":
            raise httpx.ConnectError("private-provider-body", request=request)
        return httpx.Response(503, text="private-provider-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == f"helius_{failure}"
        assert client._blocked_until == 1030
        client.config = configuration()
        for _ in range(20):
            with pytest.raises(HeliusDataError) as error:
                await client.fetch_mint(MINT)
            assert error.value.category == "helius_cooldown"
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("code", "category", "delay"),
    [
        (429, "helius_429", 60),
        (-32005, "helius_rpc_unavailable", 30),
        (-32002, "helius_rpc_error", 30),
        (-32003, "helius_rpc_error", 30),
    ],
)
async def test_matched_rpc_error_opens_shared_circuit(monkeypatch, code, category, delay):
    import revival_radar.clients.helius as helius

    monkeypatch.setattr(helius.time, "monotonic", lambda: 1000)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": json.loads(request.content)["id"],
                "error": {"code": code, "message": "private-provider-body rate limited timeout"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == category
        assert "private" not in str(error.value)
        assert client._blocked_until == 1000 + delay
        client.config = configuration()
        for _ in range(20):
            with pytest.raises(HeliusDataError) as error:
                await client.fetch_mint(MINT)
            assert error.value.category == "helius_cooldown"
    assert len(requests) == 1


@pytest.mark.parametrize("code", [-32602, -32000, None, "429"])
async def test_other_rpc_codes_and_untrusted_messages_do_not_set_cooldown(code):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": json.loads(request.content)["id"],
                "error": {"code": code, "message": "rate limited timeout private-provider-body"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(), http)
        for _ in range(2):
            with pytest.raises(HeliusDataError) as error:
                await client.fetch_mint(MINT)
            assert error.value.category in {"helius_rpc_error", "helius_malformed_response"}
            assert "private" not in str(error.value)
        assert client._blocked_until == 0
        assert client.metrics.calls[ROUTE] == 2


async def test_rpc_error_identity_mismatch_never_opens_circuit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": "wrong-request-id", "error": {"code": 429}},
            )
        )
    ) as http:
        client = HeliusClient(configuration(), http)
        with pytest.raises(HeliusDataError) as error:
            await client.fetch_mint(MINT)
        assert error.value.category == "helius_malformed_response"
        assert client._blocked_until == 0


@pytest.mark.parametrize("failure", ["auth", "5xx", "rpc_unavailable"])
async def test_outage_circuit_expires_and_allows_a_fresh_request(monkeypatch, failure):
    import revival_radar.clients.helius as helius

    clock = [1000.0]
    monkeypatch.setattr(helius.time, "monotonic", lambda: clock[0])
    requests = []

    def handler(request):
        requests.append(request)
        request_id = json.loads(request.content)["id"]
        if len(requests) == 1:
            if failure == "auth":
                return httpx.Response(403)
            if failure == "5xx":
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32005}},
            )
        return httpx.Response(200, json=response_payload(request_id))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HeliusClient(configuration(helius_http_attempts=1), http)
        with pytest.raises(HeliusDataError):
            await client.fetch_mint(MINT)
        clock[0] = client._blocked_until + 1
        client.config = configuration(helius_http_attempts=1)
        assert (await client.fetch_mint(MINT))["mint_renounced"] is True
    assert len(requests) == 2

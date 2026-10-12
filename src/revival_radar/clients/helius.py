"""Read-only Solana mint evidence, with independent Helius pacing and cooldown.

The RPC request ID binds the response to the requested account. Parsed mint
authorities establish those specific facts only; extensions are retained by name
and this client never declares a token safe.
"""

import asyncio
import math
import re
import time
import uuid
from contextlib import suppress
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from revival_radar.clients.gmgn import DataSourceError
from revival_radar.config import Settings
from revival_radar.metrics import ScanMetrics

API_BASE = "https://mainnet.helius-rpc.com/"
ROUTE = "helius:getAccountInfo"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
PROGRAM_NAMES = {TOKEN_PROGRAM: "spl-token", TOKEN_2022_PROGRAM: "spl-token-2022"}
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_EXTENSION_NAME = re.compile(r"[a-z][A-Za-z0-9]{0,63}\Z")
_SUPPLY = re.compile(r"[0-9]{1,20}\Z")


class HeliusDataError(DataSourceError):
    """An error carrying only a fixed category and locally authored message."""

    def __init__(self, message: str, *, category: str = "helius_malformed_response"):
        super().__init__(message)
        self.category = category


def is_pubkey(value: Any) -> bool:
    """Require a base58 encoding of exactly 32 bytes, without another dependency."""
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return False
    decoded = 0
    for char in value:
        index = _BASE58.find(char)
        if index < 0:
            return False
        decoded = decoded * 58 + index
    leading_zero_bytes = len(value) - len(value.lstrip("1"))
    nonzero_bytes = (decoded.bit_length() + 7) // 8
    return leading_zero_bytes + nonzero_bytes == 32


def parse_mint_response(payload: Any, address: str, request_id: str, observed_at: float) -> dict:
    """Validate jsonParsed account evidence without returning raw provider data."""
    if not is_pubkey(address):
        raise HeliusDataError("Helius mint address invalid", category="helius_invalid_address")
    if (
        not isinstance(payload, dict)
        or payload.get("jsonrpc") != "2.0"
        or payload.get("id") != request_id
        or not isinstance(payload.get("id"), str)
    ):
        raise HeliusDataError("Helius RPC response identity mismatch")
    if "error" in payload:
        error = payload["error"]
        code = error.get("code") if isinstance(error, dict) else None
        if type(code) is not int:
            raise HeliusDataError("Helius RPC error response shape invalid")
        if code == 429:
            raise HeliusDataError("Helius RPC rate limited", category="helius_429")
        if code == -32005:
            raise HeliusDataError("Helius RPC node unavailable", category="helius_rpc_unavailable")
        raise HeliusDataError("Helius RPC request failed", category="helius_rpc_error")
    result = payload.get("result")
    context = result.get("context") if isinstance(result, dict) else None
    slot = context.get("slot") if isinstance(context, dict) else None
    if type(slot) is not int or slot < 0:
        raise HeliusDataError("Helius RPC context unavailable")
    if "value" not in result or result["value"] is None:
        raise HeliusDataError("Helius mint account unavailable", category="helius_missing_account")
    account = result["value"]
    if not isinstance(account, dict) or account.get("executable") is not False:
        raise HeliusDataError("Helius mint account response shape invalid")
    owner = account.get("owner")
    if not isinstance(owner, str) or owner not in PROGRAM_NAMES:
        raise HeliusDataError("Helius account is not owned by a supported token program")
    data = account.get("data")
    if not isinstance(data, dict) or data.get("program") != PROGRAM_NAMES[owner]:
        raise HeliusDataError("Helius parsed token program mismatch")
    parsed = data.get("parsed")
    if not isinstance(parsed, dict) or parsed.get("type") != "mint":
        raise HeliusDataError("Helius account is not a parsed mint")
    info = parsed.get("info")
    if not isinstance(info, dict) or info.get("isInitialized") is not True:
        raise HeliusDataError("Helius initialized mint evidence unavailable")
    for field in ("mintAuthority", "freezeAuthority"):
        if field not in info or (info[field] is not None and not is_pubkey(info[field])):
            raise HeliusDataError("Helius mint authority evidence unavailable")
    supply, decimals = info.get("supply"), info.get("decimals")
    if (
        not isinstance(supply, str)
        or not _SUPPLY.fullmatch(supply)
        or int(supply) > 2**64 - 1
        or type(decimals) is not int
        or not 0 <= decimals <= 255
    ):
        raise HeliusDataError("Helius mint supply or decimals invalid")
    extensions = info.get("extensions", [])
    if not isinstance(extensions, list):
        raise HeliusDataError("Helius mint extensions response shape invalid")
    extension_names = []
    for extension in extensions:
        name = extension.get("extension") if isinstance(extension, dict) else None
        if not isinstance(name, str) or not _EXTENSION_NAME.fullmatch(name):
            raise HeliusDataError("Helius mint extension name invalid")
        extension_names.append(name)
    return {
        "contract_address": address,
        "provider": "helius",
        "observed_at": observed_at,
        "slot": slot,
        "commitment": "confirmed",
        "token_program": owner,
        "supply_raw": supply,
        "decimals": decimals,
        "mint_renounced": info["mintAuthority"] is None,
        "freeze_renounced": info["freezeAuthority"] is None,
        "extensions": extension_names,
    }


def _retry_after(response: httpx.Response, *, default: float) -> float:
    """Honor a finite Retry-After delay/date; never retain provider header text."""
    now = time.time()
    delays = []
    retry = response.headers.get("retry-after")
    if retry:
        with suppress(ValueError, TypeError, OverflowError):
            seconds = float(retry)
            if math.isfinite(seconds) and seconds >= 0:
                delays.append(seconds)
        if not delays:
            with suppress(ValueError, TypeError, OverflowError):
                seconds = parsedate_to_datetime(retry).timestamp() - now
                if math.isfinite(seconds):
                    delays.append(max(0, seconds))
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        with suppress(ValueError, TypeError, OverflowError):
            seconds = float(reset) - now
            if math.isfinite(seconds) and seconds > 0:
                delays.append(seconds)
    return max(delays) if delays else default


class HeliusClient:
    def __init__(self, config: Settings, http: httpx.AsyncClient):
        self.config, self.http = config, http
        self.metrics = ScanMetrics()
        self._lock = asyncio.Lock()
        self._next_request = 0.0
        self._blocked_until = 0.0

    def _failure(self, category: str, message: str) -> HeliusDataError:
        error = HeliusDataError(message, category=category)
        self.metrics.http_errors[f"{ROUTE}:{category}"] += 1
        # Attempts and cooldowns belong to HTTP counters. The scanner records a
        # terminal candidate error once, with the affected token identity.
        return error

    def _cooldown(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    async def fetch_mint(self, address: str) -> dict:
        """Fetch a confirmed mint observation; no transactions or wallet operations."""
        with self.metrics.measure("helius"):
            return await self._fetch_mint(address)

    async def _fetch_mint(self, address: str) -> dict:
        if not is_pubkey(address):
            raise self._failure("helius_invalid_address", "Helius mint address invalid")
        key = self.config.helius_api_token.get_secret_value()
        if not key:
            raise self._failure("helius_auth", "HELIUS_API_TOKEN is missing")
        async with self._lock:
            for attempt in range(self.config.helius_http_attempts):
                if time.monotonic() < self._blocked_until:
                    raise self._failure("helius_cooldown", "Helius shared cooldown active")
                await asyncio.sleep(max(0, self._next_request - time.monotonic()))
                # A pending request can outlive a configuration refresh; the client
                # retains its own lock, pacing and cooldown across scans.
                self._next_request = time.monotonic() + self.config.helius_request_spacing_seconds
                request_id = str(uuid.uuid4())
                try:
                    self.metrics.calls[ROUTE] += 1
                    response = await self.http.request(
                        "POST",
                        API_BASE,
                        params={"api-key": key},
                        json={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": "getAccountInfo",
                            "params": [
                                address,
                                {"encoding": "jsonParsed", "commitment": "confirmed"},
                            ],
                        },
                        timeout=self.config.helius_http_timeout_seconds,
                        follow_redirects=False,
                    )
                except httpx.TransportError as exc:
                    category = (
                        "helius_timeout"
                        if isinstance(exc, httpx.TimeoutException)
                        else "helius_transport"
                    )
                    error = self._failure(category, "Helius transport request failed")
                    if attempt + 1 == self.config.helius_http_attempts:
                        self._cooldown(30.0)
                        raise error from None
                    await asyncio.sleep(min(2**attempt, self.config.helius_retry_max_wait_seconds))
                    continue
                self.metrics.http_statuses[f"{ROUTE}:{response.status_code}"] += 1
                if response.status_code == 429:
                    self._cooldown(max(1.0, _retry_after(response, default=60.0)))
                    raise self._failure("helius_429", "Helius rate limited; shared cooldown active")
                if response.status_code in (401, 403):
                    self._cooldown(300.0)
                    raise self._failure("helius_auth", "Helius authentication rejected")
                if 500 <= response.status_code <= 599:
                    error = self._failure("helius_5xx", "Helius server request failed")
                    delay = max(2**attempt, _retry_after(response, default=0.0))
                    if delay > self.config.helius_retry_max_wait_seconds:
                        self._cooldown(max(30.0, delay))
                        raise error
                    if attempt + 1 < self.config.helius_http_attempts:
                        await asyncio.sleep(delay)
                        continue
                    self._cooldown(30.0)
                    raise error
                if not response.is_success:
                    raise self._failure("helius_http", "Helius HTTP request rejected")
                try:
                    payload = response.json()
                    evidence = parse_mint_response(payload, address, request_id, time.time())
                except ValueError:
                    raise self._failure(
                        "helius_malformed_response", "Helius response is not valid JSON"
                    ) from None
                except HeliusDataError as exc:
                    if exc.category == "helius_429":
                        self._cooldown(max(1.0, _retry_after(response, default=60.0)))
                    elif exc.category == "helius_rpc_unavailable":
                        self._cooldown(30.0)
                    elif exc.category == "helius_rpc_error":
                        # Solana defines these as transaction failures. On this
                        # mint-read method, suspend repeated mismatched RPC work;
                        # do not relabel them using untrusted provider messages.
                        code = payload["error"]["code"]
                        if code in (-32002, -32003):
                            self._cooldown(30.0)
                    raise self._failure(exc.category, str(exc)) from None
                if attempt:
                    self.metrics.recovered_requests[ROUTE] += 1
                return evidence
        raise self._failure("helius_retry_exhausted", "Helius retry attempts exhausted")

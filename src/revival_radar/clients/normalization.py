"""Only mappings documented by GMGNAI/gmgn-skills; unknown values stay nullable."""

import math
from typing import Any

from pydantic import ValidationError

from revival_radar.models.token import Candle, Security, TokenSnapshot

WINDOWS = ("1m", "5m", "1h", "6h", "24h")


def number(value: Any, *, negative: bool = False) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and (negative or result >= 0) else None


def count(value: Any) -> int | None:
    n = number(value)
    return int(n) if n is not None and n.is_integer() else None


def fraction(value: Any) -> float | None:
    n = number(value)
    return n if n is not None and n <= 1 else None


def boolean(value: Any) -> bool | None:
    if value in (True, 1, "1", "true", "yes"):
        return True
    if value in (False, 0, "0", "false", "no"):
        return False
    return None


def object_value(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def security_from(info: dict, raw: dict, chain: str, prior: Security) -> Security:
    stat, tags = object_value(info.get("stat")), object_value(info.get("wallet_tags_stat"))
    values = prior.model_dump()
    mappings = {
        "top10_ratio": raw.get("top_10_holder_rate", stat.get("top_10_holder_rate")),
        "dev_ratio": stat.get("creator_hold_rate", raw.get("creator_balance_rate")),
        "insider_ratio": raw.get("suspected_insider_hold_rate"),
        "sniper_ratio": stat.get("top70_sniper_hold_rate"),
        "bundler_ratio": raw.get("bundler_trader_amount_rate"),
        "liquidity_locked_ratio": object_value(raw.get("lock_summary")).get("lock_percent"),
    }
    values.update({k: fraction(v) for k, v in mappings.items() if fraction(v) is not None})
    smart = count(tags.get("smart_wallets"))
    if smart is not None:
        values["smart_money_count"] = smart
    flags = list(prior.flags)
    assessments = []
    for field in ("is_honeypot", "is_wash_trading", "can_not_sell"):
        b = boolean(raw.get(field))
        if b is not None:
            assessments.append(b)
        if b is True:
            flags.append(field)
    if chain == "sol":
        for field, dest in (
            ("renounced_mint", "mint_renounced"),
            ("renounced_freeze_account", "freeze_renounced"),
        ):
            b = boolean(raw.get(field))
            if b is not None:
                values[dest] = b
                assessments.append(not b)
            if b is False:
                flags.append(f"{dest}: false")
    if raw.get("burn_status") == "burn":
        values["liquidity_burned"] = True
    values["flags"] = list(dict.fromkeys(flags))
    if assessments:
        values["dangerous"] = any(assessments) or prior.dangerous is True
    return Security(**values)


def ranked_token(
    raw: dict, chain: str, source: str, rank: int, interval: str, now: float
) -> TokenSnapshot:
    created = number(raw.get("creation_timestamp"))
    sec = Security(
        top10_ratio=fraction(raw.get("top_10_holder_rate")),
        dev_ratio=fraction(raw.get("creator_balance_rate")),
        insider_ratio=fraction(raw.get("suspected_insider_hold_rate")),
        sniper_ratio=fraction(raw.get("top70_sniper_hold_rate")),
        bundler_ratio=fraction(raw.get("bundler_rate")),
        smart_money_count=count(raw.get("smart_degen_count")),
    )
    values = {
        "timestamp": now,
        "chain": chain,
        "contract_address": raw.get("address", ""),
        "symbol": raw.get("symbol") or "?",
        "name": raw.get("name") or "",
        "discovery_source": {source},
        f"{source}_rank": count(raw.get("rank")) or rank,
        "price": number(raw.get("price")),
        "market_cap": number(raw.get("market_cap")),
        "ath_market_cap": number(raw.get("history_highest_market_cap")),
        "liquidity": number(raw.get("liquidity")),
        "holders": count(raw.get("holder_count")),
        "token_age_seconds": now - created if created and created <= now else None,
        f"volume_{interval}": number(raw.get("volume")),
        f"tx_{interval}": count(raw.get("swaps")),
        "security": security_from({}, raw, chain, sec),
    }
    if interval in ("5m", "1h"):
        values[f"buys_{interval}"] = count(raw.get("buys"))
        values[f"sells_{interval}"] = count(raw.get("sells"))
    # Rank change fields have ambiguous % vs ratio documentation. Obtain exact
    # percentage points from token-info start prices instead of guessing units.
    return TokenSnapshot(**values)


def enriched_token(seed: TokenSnapshot, info: dict, raw_security: dict) -> TokenSnapshot:
    values = seed.model_dump()
    prices = object_value(info.get("price"))
    current, supply = number(prices.get("price")), number(info.get("circulating_supply"))
    if current is not None:
        values["price"] = current
    if current is not None and supply is not None:
        values["market_cap"] = current * supply
    # Do not multiply ATH price by today's supply and claim an observed ATH cap.
    for key, raw in (("liquidity", info.get("liquidity")), ("holders", info.get("holder_count"))):
        parsed = count(raw) if key == "holders" else number(raw)
        if parsed is not None:
            values[key] = parsed
    created = number(info.get("creation_timestamp"))
    if created and created <= seed.timestamp:
        values["token_age_seconds"] = seed.timestamp - created
    for window in WINDOWS:
        for target, origin, parser in (
            ("volume", "volume", number),
            ("tx", "swaps", count),
            ("buys", "buys", count),
            ("sells", "sells", count),
        ):
            key = f"{target}_{window}"
            if key in TokenSnapshot.model_fields:
                n = parser(prices.get(f"{origin}_{window}"))
                if n is not None:
                    values[key] = n
        before = number(prices.get(f"price_{window}"))
        if window != "1m" and before and current is not None:
            values[f"price_change_{window}"] = (current / before - 1) * 100
    values["security"] = security_from(info, raw_security, seed.chain, seed.security)
    return TokenSnapshot(**values)


def parse_candles(data: Any, now: float) -> list[Candle]:
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise ValueError("Unexpected K-line response shape")
    result = []
    for row in data["list"]:
        if not isinstance(row, dict):
            continue
        timestamp = number(row.get("time"))
        if timestamp is None or timestamp + 3600 > now:
            continue  # Only complete hourly candles; unfinished bars can reverse.
        try:
            result.append(
                Candle(
                    timestamp=timestamp,
                    open=row.get("open"),
                    high=row.get("high"),
                    low=row.get("low"),
                    close=row.get("close"),
                    volume_usd=number(row.get("amount")),
                )
            )
        except ValidationError:
            continue
    return result

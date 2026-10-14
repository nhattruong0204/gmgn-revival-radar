"""Bounded private sample of raw rank/token-info pairs, and an offline basis report.

The sample reuses data already fetched during a scan (no extra provider requests) and is
kept in the existing ``state`` table under one capped key. It exists so the supply basis
of the GMGN rank ``market_cap`` can be verified once from real observations.
"""

import json
import math
import statistics
from typing import Any

SAMPLE_KEY = "market_cap_basis:sample"
SAMPLE_CAP = 50
_FIELDS = ("price", "market_cap", "circulating_supply", "total_supply", "timestamp")


def _clean(block: Any) -> dict:
    if not isinstance(block, dict):
        return {}
    out = {}
    for key in _FIELDS:
        value = block.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value if math.isfinite(value) else None
        else:
            out[key] = None
    return out


def build_pair(chain: str, address: str, sample: dict | None) -> dict | None:
    """One bounded pair, or None unless both a rank row and a token-info row were seen."""
    if not sample or "rank" not in sample or "info" not in sample:
        return None
    return {
        "chain": chain,
        "address": address,
        "rank": _clean(sample["rank"]),
        "info": _clean(sample["info"]),
    }


def append_pair(existing: str | None, pair: dict, cap: int = SAMPLE_CAP) -> str:
    """Return the new JSON value holding at most the newest ``cap`` pairs."""
    try:
        pairs = json.loads(existing) if existing else []
    except ValueError:
        pairs = []
    if not isinstance(pairs, list):
        pairs = []
    pairs.append(pair)
    return json.dumps(pairs[-cap:], separators=(",", ":"))


def basis_report(raw: str | None) -> dict:
    """Compare rank market_cap with price x circulating and price x total supply."""
    try:
        pairs = json.loads(raw) if raw else []
    except ValueError:
        pairs = []
    if not isinstance(pairs, list):
        pairs = []
    circ, total = [], []
    for pair in pairs:
        rank, info = pair.get("rank") or {}, pair.get("info") or {}
        mc = rank.get("market_cap")
        price = info.get("price") or rank.get("price")
        if not mc or not price or mc <= 0 or price <= 0:
            continue
        supply = info.get("circulating_supply") or rank.get("circulating_supply")
        if supply and supply > 0:
            circ.append(mc / (price * supply))
        supply = info.get("total_supply") or rank.get("total_supply")
        if supply and supply > 0:
            total.append(mc / (price * supply))

    def summary(ratios: list[float]) -> dict:
        if not ratios:
            return {"n": 0, "within_5pct": 0}
        ratios = sorted(ratios)
        return {
            "n": len(ratios),
            "min": ratios[0],
            "median": statistics.median(ratios),
            "max": ratios[-1],
            "within_5pct": sum(abs(r - 1) <= 0.05 for r in ratios),
        }

    c, t = summary(circ), summary(total)
    near_c = c["n"] > 0 and c["within_5pct"] / c["n"] >= 0.8
    near_t = t["n"] > 0 and t["within_5pct"] / t["n"] >= 0.8
    if c["n"] < 5 and t["n"] < 5:
        verdict = "pending: fewer than 5 comparable pairs"
    elif near_c and near_t:
        verdict = "indistinguishable: circulating and total supply are about equal in this sample"
    elif near_c:
        verdict = "rank market_cap approximates price x circulating_supply"
    elif near_t:
        verdict = "rank market_cap approximates price x total_supply"
    else:
        verdict = "inconclusive: ratios do not cluster near 1 for either supply"
    return {
        "pairs": len(pairs),
        "rank_over_price_x_circulating": c,
        "rank_over_price_x_total": t,
        "verdict": verdict,
    }

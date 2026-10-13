"""Deterministic saved-evidence briefs. No prediction, provider I/O or backfill."""

from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from revival_radar.models.research_identity import research_digest
from revival_radar.models.token import Candle


class BriefModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class BriefSource(BriefModel):
    id: str
    kind: Literal["evaluation", "closed_hourly_candles"]
    observed_at: float
    fetched_at: float | None = None
    expires_at: float | None = None
    origin: Literal["live", "synthetic", "test", "unknown"]
    coverage: str
    values: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    candles: list[Candle] = Field(default_factory=list)


class BriefClaim(BriefModel):
    key: str
    text: str
    source_id: str
    field: str
    value: float | bool | str | None = None


class BriefScenario(BriefModel):
    kind: Literal["bull", "bear", "watch"]
    text: str
    source_id: str
    field: str
    level: float | None = None


class ResearchBrief(BriefModel):
    id: str
    evidence_digest: str | None = None
    brief_version: Literal["brief-v1"] = "brief-v1"
    evaluation_id: int
    scan_id: int
    scan_started_at: float
    chain: str
    contract_address: str
    as_of: float
    saved_at: float
    origin: Literal["live", "synthetic", "test", "unknown"]
    strategy: str | None
    strategy_revision: int | None
    score_version: str | None
    outcome: Literal["bull", "bear", "watch"]
    support: list[BriefClaim]
    counter_evidence: list[BriefClaim]
    unresolved: list[BriefClaim]
    scenarios: list[BriefScenario]
    sources: list[BriefSource]


def build_brief(
    *,
    identity,
    scan_id,
    scan_started_at,
    token,
    result,
    blockers,
    missing,
    configuration,
    origin,
    cache,
    cache_origin,
    saved_at,
    brief_id,
) -> ResearchBrief:
    """Freeze only evidence available at evaluation time, including its numeric inputs."""
    source_id = f"evaluation:{identity}"
    values = {
        "price": token.price,
        "volume_5m": token.volume_5m,
        "tx_5m": token.tx_5m,
        "returning_activity": result.returning_activity,
    }
    sources = [
        BriefSource(
            id=source_id,
            kind="evaluation",
            observed_at=token.timestamp,
            origin=origin,
            coverage="Saved evaluation; field completeness below",
            values=values,
        )
    ]
    support, counter, unresolved = [], [], []

    def claim(target, key, text, value=None, source=source_id, field=None):
        target.append(
            BriefClaim(key=key, text=text, source_id=source, field=field or key, value=value)
        )

    if origin != "live":
        claim(unresolved, "origin", f"Evaluation origin: {origin}", origin)
    if result.returning_activity is True:
        claim(
            support if origin == "live" else unresolved,
            "returning_activity",
            "Returning activity was observed",
            True,
        )
    elif result.returning_activity is False:
        claim(counter, "returning_activity", "Returning activity was not observed", False)
    else:
        claim(unresolved, "returning_activity", "Returning activity is unknown")
    for field in ("price", "volume_5m", "tx_5m"):
        if values[field] is None:
            claim(unresolved, field, f"{field} is unavailable")
    for category, entries, target in (
        ("blockers", blockers, counter),
        ("missing_fields", missing, unresolved),
        ("warnings", result.warnings, counter),
    ):
        for index, entry in enumerate(dict.fromkeys(entries)):
            key = f"{category}.{index}"
            sources[0].values[key] = entry
            claim(target, key, entry, entry)
    context = configuration or {}
    revision = context.get("revision")
    revision = revision if type(revision) is int and revision >= 0 else None
    sources[0].values.update(origin=origin, strategy_revision=revision)
    strategy = context.get("preset")
    strategy = (
        strategy
        if isinstance(strategy, str) and strategy in {"Strict", "Balanced", "Broad", "Custom"}
        else None
    )
    if revision is None:
        claim(unresolved, "strategy_revision", "Strategy revision is unknown")
    bars = []
    if cache and cache["fetched_at"] <= token.timestamp:
        import json

        try:
            raw = json.loads(cache["payload"])["candles"]
            if not isinstance(raw, list) or len(raw) > 1000:
                raise ValueError("Invalid candle cache")
            bars = sorted(
                [Candle.model_validate(b) for b in raw if b["timestamp"] + 3600 <= token.timestamp],
                key=lambda b: b.timestamp,
            )[-25:]
        except (ValueError, KeyError, TypeError):
            bars = []
    complete = (
        len(bars) == 25
        and all(b.timestamp - a.timestamp == 3600 for a, b in zip(bars, bars[1:], strict=False))
        and 0 <= token.timestamp - (bars[-1].timestamp + 3600) < 3600
        and cache["expires_at"] > token.timestamp
    )
    candle_id = f"{brief_id}:candles"
    candle_source = BriefSource(
        id=candle_id,
        kind="closed_hourly_candles",
        observed_at=bars[-1].timestamp + 3600 if bars else token.timestamp,
        fetched_at=cache["fetched_at"] if cache else None,
        expires_at=cache["expires_at"] if cache else None,
        origin=cache_origin,
        coverage="25 consecutive fresh closed 1h bars"
        if complete
        else "Missing, gapped, stale or incomplete 25-bar coverage",
        candles=bars,
    )
    sources.append(candle_source)
    scenarios = [
        BriefScenario(
            kind="watch",
            text="Wait for complete fresh evidence and review "
            "blockers before reassessing; this is not an entry instruction.",
            source_id=source_id,
            field="returning_activity",
        )
    ]
    outcome = "watch"
    if complete and cache_origin == "live":
        low, high = min(b.low for b in bars[:-1]), max(b.high for b in bars[:-1])
        last = bars[-1]
        candle_source.values.update(range_low=low, range_high=high, close=last.close)
        if last.close > high:
            claim(
                support,
                "close",
                "Last closed hour finished above the preceding 24-hour high",
                last.close,
                candle_id,
            )
            outcome = "bull"
        elif last.close < low:
            claim(
                counter,
                "close",
                "Last closed hour finished below the preceding 24-hour low",
                last.close,
                candle_id,
            )
            outcome = "bear"
        else:
            claim(
                unresolved,
                "close",
                "Last close remains inside the preceding 24-hour range",
                last.close,
                candle_id,
            )
        volumes = [b.volume_usd for b in bars]
        mean = sum(v / 24 for v in volumes[:-1]) if all(v is not None for v in volumes) else 0
        ratio = volumes[-1] / mean if mean > 0 else None
        if ratio is not None and isfinite(ratio):
            candle_source.values["relative_volume"] = ratio
            claim(
                support if ratio > 1 else counter,
                "relative_volume",
                "Hourly USD volume relative to the preceding 24 closed hours",
                ratio,
                candle_id,
            )
        else:
            claim(
                unresolved,
                "relative_volume",
                "Hourly USD volume baseline is unavailable or zero",
                source=candle_id,
            )
        scenarios.extend(
            [
                BriefScenario(
                    kind="bull",
                    text="If a later closed hour holds above the observed "
                    "range high with renewed activity, reassess the supporting case. "
                    "A close back inside that range would weaken it.",
                    source_id=candle_id,
                    field="range_high",
                    level=high,
                ),
                BriefScenario(
                    kind="bear",
                    text="If a later closed hour falls below the observed "
                    "range low, reassess downside evidence. A recovery inside the range "
                    "would weaken that case.",
                    source_id=candle_id,
                    field="range_low",
                    level=low,
                ),
            ]
        )
    else:
        claim(
            unresolved,
            "candle_coverage",
            candle_source.coverage + f"; candle origin: {cache_origin}",
            source=candle_id,
            field="coverage",
        )
    if unresolved or blockers or origin != "live":
        outcome = "watch"
    return ResearchBrief(
        id=brief_id,
        evidence_digest=research_digest(token, result, configuration),
        evaluation_id=identity,
        scan_id=scan_id,
        scan_started_at=scan_started_at,
        chain=token.chain,
        contract_address=token.contract_address,
        as_of=token.timestamp,
        saved_at=saved_at,
        origin=origin,
        strategy=strategy,
        strategy_revision=revision,
        score_version=result.score_version,
        outcome=outcome,
        support=support,
        counter_evidence=counter,
        unresolved=unresolved,
        scenarios=scenarios,
        sources=sources,
    )

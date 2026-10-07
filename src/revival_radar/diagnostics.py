"""Readable Telegram health summaries built only from persisted scanner metrics."""

from datetime import UTC, datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CHAIN_NAMES = {
    "sol": "Solana",
    "bsc": "BNB Chain",
    "base": "Base",
    "robinhood": "Robinhood",
    "arc": "Arc",
}
_LABELS = {
    "no_base": "No price base detected",
    "no_returning_activity": "Activity has not returned",
    "score_below_threshold": "Score below alert threshold",
    "security_dangerous": "Known security risk",
    "dangerous": "Known security risk",
    "token_age_seconds": "Token age",
    "market_cap": "Market cap",
    "liquidity": "Liquidity",
    "drawdown_from_ath": "ATH drawdown",
    "holders": "Holders",
    "volume_1h": "Hourly volume",
    "volume_5m": "5m volume",
    "price_change_5m": "5m price increase",
    "price_change_1h": "Hourly price increase",
    "baseline_history": "Not enough recent scan history",
    "ath_market_cap": "ATH market cap",
    "tx_5m": "5m transactions",
    "tx_1h": "Hourly transactions",
    "security.top10_ratio": "Top 10 holder concentration",
    "security.insider_ratio": "Insider holdings",
    "security.dangerous": "Security assessment",
    "volume_5m_baseline_unavailable_or_zero": "5m volume baseline missing or zero",
    "candles": "Price candles",
    "hot_search": "Hot Search",
    "trending": "Trending",
    "watchlist": "Watchlist",
    "tokenized_stock": "Tokenized stock",
    "stablecoin": "Stablecoin",
    "wrapped_asset": "Wrapped asset",
}


def _units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text(value: object, limit: int = 100) -> str:
    """Escape one value, shortening whole characters/entities within a UTF-16 budget."""
    parts: list[str] = []
    used = 0
    for character in str(value):
        encoded = escape(character)
        size = _units(encoded)
        if used + size > limit - 1:
            return "".join(parts) + "…"
        parts.append(encoded)
        used += size
    return "".join(parts)


def _label(value: str) -> str:
    if value in _LABELS:
        return _LABELS[value]
    if value.startswith("asset_type: excluded "):
        asset = value.removeprefix("asset_type: excluded ")
        return f"Excluded asset: {_LABELS.get(asset, asset.replace('_', ' '))}"
    if ": " in value:
        field, detail = value.split(": ", 1)
        detail = "missing" if detail == "unavailable" else detail
        return f"{_LABELS.get(field, field.replace('_', ' '))}: {detail}"
    return value.replace("_", " ")


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder}s" if remainder else f"{minutes}m"


def _counted(values: dict[str, int], limit: int = 3) -> str:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    lines = [f"• {_text(_label(key), 72)} — {count:,}" for key, count in ordered[:limit]]
    if len(ordered) > limit:
        lines.append(f"+{len(ordered) - limit} other categories")
    return "\n".join(lines) or "None recorded."


def _reasons(values: list[str], limit: int = 2) -> str:
    rendered = " · ".join(_text(_label(value), 68) for value in values[:limit])
    if len(values) > limit:
        rendered += f" · +{len(values) - limit} more"
    return rendered


def format_health(report: dict[str, Any]) -> str:
    """Return balanced, escaped HTML within Telegram's 4,096 UTF-16-unit budget.

    Essential scan and delivery totals always come first. Detailed sections have
    bounded values; if the entire report cannot fit, omission is made explicit.
    """
    timezone_name = str(report.get("timezone", "UTC"))
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        timezone, timezone_name = UTC, "UTC"
    since = datetime.fromtimestamp(report["since"], timezone).strftime("%d %b %H:%M")
    until = datetime.fromtimestamp(report["until"], timezone).strftime("%d %b %H:%M")
    scans, discovery = report["scans"], report["discovery"]
    evaluations, alerts = report["evaluations"], report["alerts"]
    sections = [
        f"<b>🩺 Radar health</b>\n{since} → {until}\n{_text(timezone_name)}",
        f"<b>📬 {alerts['sent']:,} alerts sent</b>\n"
        f"{evaluations['unique_tokens']:,} unique tokens evaluated\n"
        f"{evaluations['count']:,} evaluations · {evaluations['eligible']:,} passed core checks",
    ]
    if any(alerts[status] for status in ("failed", "pending", "unknown")):
        sections.append(
            "<b>⚠️ Delivery needs attention</b>\n"
            f"{alerts['failed']:,} failed · {alerts['pending']:,} pending · "
            f"{alerts['unknown']:,} unconfirmed"
        )
    duration = scans["duration_seconds"]
    scan_lines = [f"<b>🔄 {scans['completed']:,} scans completed</b>"]
    if scans["completed"]:
        scan_lines.append(
            f"Average {_duration(duration['mean'])} · Longest {_duration(duration['max'])}"
        )
    if scans["errors"]:
        scan_lines.append(f"⚠️ {scans['errors']:,} scan errors; coverage may be reduced.")
    else:
        scan_lines.append("No scan errors recorded.")
    if scans["interrupted"]:
        scan_lines.append(f"{scans['interrupted']:,} unfinished — may be running or interrupted.")
    if not scans["started"]:
        scan_lines.append("No scans recorded in this window yet.")
    sections.append("\n".join(scan_lines))

    sections.append(
        "<b>🚧 Most common blockers</b>\n"
        + _counted(evaluations["rejection_counts"])
        + "\n<i>Counts are evaluations; multiple reasons can apply.</i>"
    )
    if evaluations["missing_field_counts"]:
        sections.append(
            "<b>🧩 Missing data</b>\n" + _counted(evaluations["missing_field_counts"], 2)
        )

    candidates = report["best_candidates"]
    candidate_lines = ["<b>💎 Best observed · score ≥50</b>"]
    if not candidates:
        candidate_lines.append("None in this window. Check the blockers above.")
    for candidate in candidates[:3]:
        symbol = _text(candidate["symbol"], 36)
        chain = _text(_CHAIN_NAMES.get(candidate["chain"], candidate["chain"]), 24)
        candidate_lines.append(f"\n<b>{symbol} · {candidate['score']}/100</b> · {chain}")
        if candidate["rejection_reasons"]:
            candidate_lines.append("Needs: " + _reasons(candidate["rejection_reasons"]))
        elif candidate["eligible"]:
            candidate_lines.append("Core checks passed; alert rules still apply.")
        else:
            candidate_lines.append("Core checks not passed.")
        if candidate["missing_fields"]:
            candidate_lines.append("Missing: " + _reasons(candidate["missing_fields"], 1))
        candidate_lines.append(f"<code>{_text(candidate['contract_address'], 80)}</code>")
    if len(candidates) > 3:
        candidate_lines.append(f"Showing 3 of {len(candidates)} recorded candidates.")
    sections.append("\n".join(candidate_lines))

    source_counts = {
        f"{_CHAIN_NAMES.get(chain, chain)} / {_LABELS.get(source, source)}": count
        for chain, sources in discovery["snapshots_by_source"].items()
        for source, count in sources.items()
    }
    source_errors = {
        f"{_CHAIN_NAMES.get(chain, chain)} / {_LABELS.get(source, source)}": count
        for chain, sources in discovery["source_errors"].items()
        for source, count in sources.items()
    }
    observations = {
        _CHAIN_NAMES.get(chain, chain): count
        for chain, count in discovery["observations_by_chain"].items()
    }
    scores = " · ".join(
        f"{_text(bucket, 16)}: {count:,}" for bucket, count in evaluations["score_buckets"].items()
    )
    detail_lines = [
        "<b>🔎 Scan details</b>",
        f"Discovery: {discovery['unique_tokens']:,} unique tokens",
        f"Processed: {scans['processed']:,} · Successful sources: {scans['sources_ok']:,}",
        f"Potential alerts: {scans['potential_alerts']:,}",
        "\n<b>Discovery observations</b>\n" + _counted(observations, 5),
        "\n<b>Source snapshots</b>\n" + _counted(source_counts),
        "\n<b>Source errors</b>\n" + _counted(source_errors),
        "\n<b>Scores / evaluation count</b>\n" + scores,
        "\nUnique tokens use chain + address. Discovery excludes the watchlist. "
        "Observations deduplicate within scans; source snapshots can overlap.",
        "Core checks alone do not send alerts; score, cooldown and delivery settings still apply.",
    ]
    sections.append("<blockquote expandable>" + "\n".join(detail_lines) + "</blockquote>")

    omission = "<i>Some details omitted to fit Telegram.</i>"
    selected: list[str] = []
    omitted = False
    # Count the HTML itself conservatively, including tags and escaped entities.
    # Never cut an HTML section, a surrogate pair or an escaped entity in half.
    budget = 4096 - _units(omission) - 2
    used = 0
    for section in sections:
        cost = _units(section) + (2 if selected else 0)
        if used + cost <= budget:
            selected.append(section)
            used += cost
        else:
            omitted = True
    if omitted:
        selected.append(omission)
    return "\n\n".join(selected)

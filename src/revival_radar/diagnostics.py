"""Compact Telegram health reports built exclusively from persisted scanner metrics."""

from datetime import UTC, datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def format_health(report: dict[str, Any]) -> str:
    """Return escaped Telegram HTML, always below its 4,096-character message limit."""
    lines: list[str] = []

    def add(line: str) -> None:
        if sum(map(len, lines)) + len(lines) + len(line) < 4000:
            lines.append(line)

    def text(value: object, limit: int = 160) -> str:
        raw = str(value)
        return escape(raw if len(raw) <= limit else raw[: limit - 1] + "…")

    def counted(values: dict[str, int], limit: int = 6) -> str:
        ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return "; ".join(f"{text(key, 70)}: {count}" for key, count in ordered) or "none"

    timezone_name = str(report.get("timezone", "UTC"))
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        timezone, timezone_name = UTC, "UTC"
    since = datetime.fromtimestamp(report["since"], timezone).strftime("%m-%d %H:%M")
    until = datetime.fromtimestamp(report["until"], timezone).strftime("%m-%d %H:%M")
    scans, discovery = report["scans"], report["discovery"]
    evaluations, alerts = report["evaluations"], report["alerts"]
    add("<b>Revival Radar health</b>")
    add(f"{since} → {until} {text(timezone_name)}")
    add(
        f"Scans: {scans['completed']} completed; {scans['interrupted']} unfinished "
        "(interrupted/in progress)"
    )
    duration = scans["duration_seconds"]
    add(f"Duration: mean {duration['mean']:.1f}s; max {duration['max']:.1f}s")
    add(
        f"Processed: {scans['processed']}; source successes: {scans['sources_ok']}; "
        f"errors: {scans['errors']}; potential alerts: {scans['potential_alerts']}"
    )
    add(f"Discovered unique tokens: {discovery['unique_tokens']} (chain + address)")
    add("Discovery observations by chain: " + counted(discovery["observations_by_chain"]))
    source_counts = {
        f"{chain}/{source}": count
        for chain, sources in discovery["snapshots_by_source"].items()
        for source, count in sources.items()
    }
    add("Source snapshots: " + counted(source_counts, 10))
    source_errors = {
        f"{chain}/{source}": count
        for chain, sources in discovery["source_errors"].items()
        for source, count in sources.items()
    }
    add("Source errors: " + counted(source_errors, 10))
    add("Observations deduplicate within scans; snapshots can overlap; watchlist excluded.")
    add(
        f"Evaluations: {evaluations['count']}; unique tokens: {evaluations['unique_tokens']}; "
        f"eligible: {evaluations['eligible']}"
    )
    add("Scores (evaluations): " + counted(evaluations["score_buckets"]))
    add("Rejections: " + counted(evaluations["rejection_counts"]))
    add("Missing data: " + counted(evaluations["missing_field_counts"]))
    add("Rejection/missing counts are per evaluation; multiple reasons can apply.")
    add(
        f"Deliveries: {alerts['sent']} sent; {alerts['failed']} failed; "
        f"{alerts['unknown']} unknown; {alerts['pending']} pending"
    )
    add("<b>Best candidates / near misses (score ≥50)</b>")
    if not report["best_candidates"]:
        add("None in this window; check the low-score rejection counts above.")
    for candidate in report["best_candidates"][:3]:
        reasons = ", ".join(candidate["rejection_reasons"]) or "none"
        missing = ", ".join(candidate["missing_fields"]) or "none"
        add(
            f"{text(candidate['symbol'], 40)} · {text(candidate['chain'], 20)} · "
            f"{candidate['score']}/100\n"
            f"<code>{text(candidate['contract_address'], 64)}</code>\n"
            f"Blocked: {text(reasons, 180)}\nMissing: {text(missing, 180)}"
        )
    return "\n".join(lines)

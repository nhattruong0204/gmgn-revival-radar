"""Bounded Telegram summaries of saved Solana evaluations, without live API calls."""

import html
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from revival_radar.chains import CHAINS
from revival_radar.clients.telegram import money

_MESSAGE_LIMIT = 4096
_DEFAULT_STALE_SECONDS = 900


def _size(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text(value: object, limit: int) -> str:
    """Bound HTML source units without cutting an escape sequence or HTML tag."""
    plain = " ".join(str(value).split())
    plain = "".join(character for character in plain if character.isprintable())
    escaped = html.escape(plain)
    if _size(escaped) <= limit:
        return escaped
    result = []
    used = 1
    for character in plain:
        part = html.escape(character)
        if used + _size(part) > limit:
            break
        result.append(part)
        used += _size(part)
    return "".join(result) + "…"


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _count(value: object) -> str:
    number = _number(value)
    return str(int(number)) if number is not None and 0 <= number <= 10**12 else "?"


def _score(value: object) -> str:
    number = _number(value)
    return f"{number:g}" if number is not None and 0 <= number <= 100 else "?"


def _items(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def _zone(report: Mapping) -> tuple[ZoneInfo, str]:
    name = report.get("timezone", "Asia/Bangkok")
    try:
        zone = ZoneInfo(name) if isinstance(name, str) else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC"), "UTC (report timezone unavailable)"
    end = _date(report.get("until"), zone)
    offset = end.strftime("%z") if end else ""
    suffix = f"UTC{offset[:3]}:{offset[3:]}" if offset else "offset unavailable"
    return zone, f"{_text(zone.key, 40)} ({suffix})"


def _date(value: object, zone: ZoneInfo) -> datetime | None:
    number = _number(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, UTC).astimezone(zone)
    except (OverflowError, OSError, ValueError):
        return None


def _when(value: object, zone: ZoneInfo, *, full: bool = False) -> str:
    date = _date(value, zone)
    return date.strftime("%d %b %H:%M" if full else "%m/%d %H:%M") if date else "unknown"


def _age(observation: Mapping, until: object) -> str:
    end, timestamp = _number(until), _number(observation.get("timestamp"))
    if end is None or timestamp is None:
        return "age unknown"
    seconds = end - timestamp
    if not math.isfinite(seconds) or seconds > 315_576_000_000:
        return "age unknown"
    if seconds < 0:
        return "future timestamp"
    if seconds < 60:
        age = f"{int(seconds)}s ago"
    elif seconds < 3600:
        age = f"{int(seconds / 60)}m ago"
    elif seconds < 86400:
        age = f"{seconds / 3600:.1f}h ago"
    else:
        age = f"{seconds / 86400:.1f}d ago"
    settings = _mapping(_mapping(observation.get("configuration")).get("settings"))
    cutoff = _number(settings.get("history_max_gap_seconds"))
    cutoff = cutoff if cutoff is not None and cutoff > 0 else _DEFAULT_STALE_SECONDS
    return f"STALE {age}" if seconds > cutoff else age


def _risk(observation: Mapping) -> str | None:
    security = _mapping(_mapping(observation.get("token")).get("security"))
    if security.get("mint_renounced") is False:
        return "mint authority active"
    if security.get("freeze_renounced") is False:
        return "freeze authority active"
    flags = _items(security.get("flags"))
    if security.get("dangerous") is True or flags:
        return flags[0] if flags else "dangerous-token flag"
    if "security_dangerous" in _items(observation.get("rejection_reasons")):
        return "dangerous-token flag"
    return None


def _entries(report: Mapping) -> list[Mapping]:
    """Preserve repository order, while defensively enforcing Solana/distinct/top ten."""
    entries, seen = [], set()
    for raw in report.get("entries", []):
        entry = _mapping(raw)
        if entry.get("chain", report.get("chain", "sol")) != "sol":
            continue
        address = entry.get("contract_address")
        if not isinstance(address, str) or not address or address in seen:
            continue
        seen.add(address)
        entries.append(entry)
        if len(entries) == 10:
            break
    return entries


def _money(value: object) -> str:
    number = _number(value)
    return money(number) if number is not None and number >= 0 else "?"


def _row(entry: Mapping, rank: int, report: Mapping, zone: ZoneInfo, budget: int) -> str:
    peak, latest = _mapping(entry.get("peak")), _mapping(entry.get("latest"))
    signal, token = _mapping(latest.get("signal")), _mapping(latest.get("token"))
    blocks, missing = _items(latest.get("rejection_reasons")), _items(latest.get("missing_fields"))
    peak_blocks = _items(peak.get("rejection_reasons"))
    peak_missing = _items(peak.get("missing_fields"))
    distinct = peak.get("id") != latest.get("id") or (peak_blocks, peak_missing) != (
        blocks,
        missing,
    )
    peak_legacy = not _mapping(peak.get("token")) or not _mapping(peak.get("signal"))
    latest_legacy = not token or not signal
    latest_risk, peak_risk = _risk(latest), _risk(peak)
    risk = ""
    if latest_risk:
        risk = f"⚠ KNOWN RISK latest: {_text(latest_risk, 45)}"
        if peak_risk:
            risk += " (risk also at peak)"
    elif peak_risk:
        risk = f"⚠ KNOWN RISK at peak: {_text(peak_risk, 45)}"
    elif _mapping(token.get("security")).get("dangerous") is None:
        risk = "Risk unknown / checks incomplete"
    legacy = []
    if peak_legacy:
        legacy.append("Peak details unavailable (legacy)")
    if latest_legacy:
        legacy.append("Saved details unavailable (legacy)")
    dimensions = "/".join(
        _score(signal.get(f"{name}_score")) for name in ("setup", "trigger", "confirmation")
    )
    lines = [
        f"<b>{rank}. ${_text(entry.get('symbol', '?'), 30)}</b> · "
        f"{_score(peak.get('score'))} → {_score(latest.get('score'))} · "
        f"{_text(latest.get('status', 'status unknown'), 27)}",
        f"Peak {_when(peak.get('timestamp'), zone)} · Latest "
        f"{_age(latest, report.get('generated_at', report.get('until')))}"
        f" · {_count(entry.get('observations'))} obs",
        f"S/T/C {dimensions} · MC {_money(token.get('market_cap'))}"
        f" · Liq {_money(token.get('liquidity'))}",
    ]
    if risk:
        lines.append(risk)
    if legacy:
        lines.append(" · ".join(legacy))
    if distinct:
        # Both observations remain inspectable even when their verbose lists cannot fit.
        lines.append(
            f"Blocked peak/latest {_count(len(peak_blocks))}/{_count(len(blocks))} · "
            f"Missing peak/latest {_count(len(peak_missing))}/{_count(len(missing))}"
        )
    elif blocks or missing:
        lines.append(f"Blocked {_count(len(blocks))} · Missing {_count(len(missing))}")
    base = "\n".join(lines)
    if _size(base) > budget:
        # Risk/unknown/legacy notices have priority over optional market figures.
        lines[2] = f"S/T/C {dimensions}"
        base = "\n".join(lines)
    if _size(base) > budget and risk:
        if latest_risk:
            lines[3] = "⚠ KNOWN RISK latest" + (" and peak" if peak_risk else "")
        elif peak_risk:
            lines[3] = "⚠ KNOWN RISK at peak"
        else:
            lines[3] = "Risk unknown"
        base = "\n".join(lines)
    if _size(base) > budget and legacy:
        legacy_index = 4 if risk else 3
        lines[legacy_index] = (
            "Peak + latest details unavailable"
            if peak_legacy and latest_legacy
            else ("Peak details unavailable" if peak_legacy else "Latest details unavailable")
        )
        base = "\n".join(lines)
    if _size(base) > budget:
        lines[0] = (
            f"<b>{rank}. ${_text(entry.get('symbol', '?'), 12)}</b> · "
            f"{_score(peak.get('score'))} → {_score(latest.get('score'))} · "
            f"{_text(latest.get('status', 'unknown'), 16)}"
        )
        base = "\n".join(lines)
    extras = []
    if blocks:
        extras.append("Latest block: " + _text(blocks[0].replace("_", " "), 52))
    if missing:
        extras.append("Latest missing: " + _text(missing[0].replace("_", " "), 38))
    if distinct and peak_blocks:
        extras.append("Peak block: " + _text(peak_blocks[0].replace("_", " "), 52))
    if distinct and peak_missing:
        extras.append("Peak missing: " + _text(peak_missing[0].replace("_", " "), 38))
    warnings = _items(latest.get("warnings")) + _items(token.get("data_warnings"))
    if warnings:
        extras.append("Note: " + _text(warnings[0], 42))
    for extra in extras:
        if _size(base + "\n" + extra) <= budget:
            base += "\n" + extra
    return base


def market_report_page(report: dict) -> str:
    """Summarize the supplied historical cohort; ranking and windowing belong to storage."""
    zone, zone_label = _zone(report)
    entries = _entries(report)
    hours = _number(report.get("hours"))
    window = f"{hours:g}h" if hours is not None and 0 < hours <= 8760 else "selected window"
    lines = [
        f"📊 <b>Solana top {len(entries)} · {window}</b>",
        f"{_when(report.get('since'), zone, full=True)} → "
        f"{_when(report.get('until'), zone, full=True)} · {zone_label}",
        f"{_count(report.get('evaluations'))} evaluations · "
        f"{_count(report.get('unique_tokens'))} tracked tickers · "
        f"{_count(report.get('completed_scans'))} completed scans",
        "Ranked by peak score; latest = last saved in window; age at report creation. "
        "Below-threshold rows included.",
        "S/T/C = latest setup / trigger / confirmation scores. ? = unavailable.",
    ]
    incomplete = _number(report.get("incomplete_scans"))
    if incomplete is not None and incomplete > 0:
        lines.append(f"⚠ {_count(incomplete)} incomplete scans at cutoff; coverage may have gaps.")
    if not report.get("completed_scans"):
        lines.append("⚠ No completed scans in this window; coverage is limited.")
    first, start = _number(report.get("first_observation")), _number(report.get("since"))
    if first is not None and start is not None and first > start + 300:
        lines.append("⚠ Partial window: first saved observation " + _when(first, zone))
    if entries and any((_number(entry.get("observations")) or 0) < 3 for entry in entries):
        lines.append("⚠ Sparse samples: some tickers have fewer than 3 observations.")
    if report.get("multiple_configurations"):
        lines.append("⚠ Multiple configurations in window; scores may not be comparable.")
    last = report.get("last_finished")
    if last is not None:
        lines.append("Last completed scan " + _when(last, zone))
    footer = (
        "Heuristic scores, not return probabilities. Narrative/catalysts unassessed. "
        "Tracked observations only, not the whole market or live quotes."
    )
    header = "\n".join(lines)
    if not entries:
        return header + "\n\nNo saved Solana evaluations in this window.\n\n" + footer
    row_budget = (_MESSAGE_LIMIT - _size(header) - _size(footer) - 2 * (len(entries) + 1)) // len(
        entries
    )
    rows = [_row(entry, rank, report, zone, row_budget) for rank, entry in enumerate(entries, 1)]
    return "\n\n".join([header, *rows, footer])


def _detail_id(observation: Mapping) -> int | None:
    identity = observation.get("id")
    if type(identity) is int and 0 < identity < 2**63 and len(str(identity)) <= 18:
        return identity
    return None


def market_report_buttons(report: dict) -> list[list[dict[str, str]]]:
    """Durable saved-detail callbacks; the controls handler enforces owner authorization."""
    rows = []
    for rank, entry in enumerate(_entries(report), 1):
        peak, latest = _mapping(entry.get("peak")), _mapping(entry.get("latest"))
        peak_id, latest_id = _detail_id(peak), _detail_id(latest)
        buttons = []
        if peak_id is not None:
            buttons.append(
                {"text": f"{rank}. Peak evidence", "callback_data": f"e:{peak_id}:report"}
            )
        if latest_id is not None and latest_id != peak_id:
            buttons.append(
                {"text": f"{rank}. Latest evidence", "callback_data": f"e:{latest_id}:report"}
            )
        buttons.append(
            {"text": f"{rank}. GMGN", "url": CHAINS["sol"].links(entry["contract_address"])["GMGN"]}
        )
        rows.append(buttons)
    return rows


def _evidence_items(value: object) -> list[str] | None:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        return None
    return list(value)


def _eligibility(value: object) -> str:
    return "yes" if value is True else ("no" if value is False else "unknown")


def market_evidence_summary(evaluation: Mapping, budget: int = 600) -> str:
    """A compact supplement; the evidence view keeps the complete recorded lists."""
    blockers = _evidence_items(evaluation.get("rejection_reasons"))
    missing = _evidence_items(evaluation.get("missing_fields"))
    lines = [
        "📋 <b>Saved evaluation evidence</b>",
        f"Eligible at observation: {_eligibility(evaluation.get('eligible'))} · "
        f"Blockers {_count(len(blockers)) if blockers is not None else '?'} · "
        f"Missing {_count(len(missing)) if missing is not None else '?'}",
    ]
    footer = "Complete recorded lists: Saved evidence button."
    mandatory = "\n".join([*lines, footer])
    if _size(mandatory) > budget:
        return ""
    for label, values in (("Block", blockers), ("Missing", missing)):
        for value in (values or [])[:3]:
            line = label + ": " + _text(value, 80)
            if _size("\n".join([*lines, line, footer])) <= budget:
                lines.append(line)
    return "\n".join([*lines, footer])


def _evidence_line(label: str, text: str, budget: int):
    """Split a complete recorded value into independently escaped continuation lines."""
    plain = "".join(character for character in " ".join(text.split()) if character.isprintable())
    plain = plain or "(empty recorded value)"
    prefix, continuation = f"• {label}: ", f"• {label} (continued): "
    line, used = prefix, _size(prefix)
    for character in plain:
        part = html.escape(character)
        size = _size(part)
        if used + size > budget:
            yield line
            line, used = continuation, _size(continuation)
        line += part
        used += size
    yield line


def market_evidence_pages(detail: Mapping, timezone: str = "Asia/Bangkok") -> list[str]:
    """Render every recorded blocker/missing field without truncating long reasons."""
    evaluation = _mapping(detail.get("evaluation"))
    identity = _detail_id(evaluation)
    if identity is None:
        raise ValueError("Saved evaluation metadata unavailable")
    token, signal = _mapping(detail.get("token")), _mapping(detail.get("signal"))
    zone, zone_label = _zone({"timezone": timezone, "until": evaluation.get("timestamp")})
    blockers = _evidence_items(evaluation.get("rejection_reasons"))
    missing = _evidence_items(evaluation.get("missing_fields"))
    recorded_warnings = _evidence_items(evaluation.get("warnings"))
    warnings = (
        list(
            dict.fromkeys(
                recorded_warnings
                + (_evidence_items(token.get("data_warnings")) or [])
                + (_evidence_items(signal.get("warnings")) or [])
            )
        )
        if recorded_warnings is not None
        else None
    )
    security = _mapping(token.get("security"))
    flags = _evidence_items(security.get("flags"))
    lines = [
        f"📋 <b>Saved evaluation #{identity}</b> · ${_text(token.get('symbol', '?'), 40)}",
        f"Observed {_when(evaluation.get('timestamp'), zone, full=True)} · {zone_label}",
        f"Eligible at observation: {_eligibility(evaluation.get('eligible'))}",
    ]
    risk = _risk({"token": token, "rejection_reasons": blockers})
    if risk:
        lines.append("⚠ KNOWN RISK: " + _text(risk, 80))
    elif security.get("dangerous") is None:
        lines.append("Risk unknown / checks incomplete")
    if not token or not signal:
        lines.append("Saved token/signal details unavailable (legacy)")
    header = "\n".join(lines)
    footer = (
        "Saved observation; no live fetch. Historical eligibility; other risks may be unassessed."
    )
    # Reserve more than the page-counter markup ever needs for an inspectable SQLite payload.
    capacity = _MESSAGE_LIMIT - _size(header) - _size(footer) - 80
    bodies, current = [], []

    def append(line: str) -> None:
        nonlocal current
        if current and _size("\n".join([*current, line])) > capacity:
            bodies.append("\n".join(current))
            current = []
        current.append(line)

    groups = (
        ("Blocker", "Blockers", blockers),
        ("Missing field", "Missing fields", missing),
        ("Warning", "Warnings", warnings),
        ("Security flag", "Security flags", flags),
    )
    for singular, plural, values in groups:
        if values is None:
            append(plural + ": unavailable")
        elif not values:
            append(plural + ": none recorded")
        else:
            for index, value in enumerate(values, 1):
                for line in _evidence_line(f"{singular} {index}/{len(values)}", value, capacity):
                    append(line)
    if current:
        bodies.append("\n".join(current))
    return [
        f"{header}\n<b>Evidence page {index}/{len(bodies)}</b>\n\n{body}\n\n{footer}"
        for index, body in enumerate(bodies, 1)
    ]

"""Compact native Telegram pages built from existing persisted diagnostics."""

from revival_radar.diagnostics import _counted, _duration, _label, _text, format_health
from revival_radar.telegram_presentation import setting_value


def config_footer(context: dict) -> str:
    if not context:
        return "Configuration unavailable · legacy observation"
    revision = context.get("revision")
    label = f"r{revision}" if type(revision) is int else "revision unknown"
    return f"Preset: {_text(context.get('preset', 'Custom'), 30)} · {_text(label, 24)}"


def _quality(report: dict) -> list[str]:
    evaluations = report["evaluations"]
    count, missing = evaluations["count"], evaluations["missing_field_counts"]
    fields = {
        "baseline_history": "Recent history ready",
        "volume_5m": "5m volume available",
        "tx_5m": "5m transactions available",
        "security.insider_ratio": "Insider data available",
    }
    return [
        f"{label}  {100 * max(0, count - missing.get(key, 0)) / count:.0f}%"
        if count
        else f"{label}  No observations"
        for key, label in fields.items()
    ]


def health_page(report: dict, view: str = "overview") -> str:
    scans, evaluations, alerts = report["scans"], report["evaluations"], report["alerts"]
    latest = report.get("latest_scan") or {}
    context = latest.get("configuration") or {}
    settings = context.get("settings", {})
    footer = config_footer(context)
    if len(report.get("configurations", [])) > 1:
        footer += "\nWindow includes multiple configurations; footer describes the latest scan."
    header = (
        "🩺 <b>Radar health</b>\n"
        f"Last {(report['until'] - report['since']) / 3600:g}h · "
        f"{_text(report.get('timezone', 'UTC'), 48)}"
    )
    if view == "full":
        return format_health(report)
    if view == "performance":
        duration = scans["duration_seconds"]
        lines = [
            "⚡ <b>Scan timing</b>",
            f"Completed  {scans['completed']}",
            f"Average  {_duration(duration['mean'])}",
            f"Fastest / slowest  {_duration(duration['min'])} / {_duration(duration['max'])}",
        ]
        elapsed = latest.get("duration_seconds")
        target = settings.get("scan_interval_seconds")
        lines.append(
            f"Latest  {_duration(elapsed)}"
            if elapsed is not None
            else "Latest  Unfinished / no data"
        )
        if target is not None:
            lines.append(f"Target  {_duration(target)}")
            if elapsed is not None and elapsed > target:
                lines.append("⚠️ Latest scan exceeded the interval target")
        lines += ["", "Timing covers whole scans; per-route API metrics are not recorded."]
    elif view == "funnel":
        lines = [
            "🔻 <b>Discovery &amp; delivery</b>",
            f"Discovered  {report['discovery']['unique_tokens']} unique tokens",
            f"Evaluated  {evaluations['count']} observations",
            f"Core checks passed  {evaluations['eligible']} observations",
            f"Potential alerts  {scans['potential_alerts']}",
            f"Sent  {alerts['sent']} · Failed  {alerts['failed']}",
            f"Pending  {alerts['pending']} · Unconfirmed  {alerts['unknown']}",
            "",
            "Evaluations include watchlist observations; discovery counts exclude watchlist. "
            "Potential alerts can be suppressed by dry run, pause, or cooldown.",
        ]
    elif view == "quality":
        lines = [
            "🧪 <b>Data coverage</b>",
            f"Based on {evaluations['count']} saved evaluations.",
            "",
            *_quality(report),
            "",
            "Recent history ready means enough recent samples, not necessarily a nonzero baseline.",
            "Missing data never means zero or safe. Coverage is not API success rate.",
            "",
            "<b>Most common missing fields</b>",
            _counted(evaluations["missing_field_counts"], 5),
        ]
    elif view == "config":
        lines = ["⚙️ <b>Latest scan configuration</b>", footer]
        for key in (
            "enabled_chains",
            "alert_score_threshold",
            "base_min_hours",
            "min_volume_1h",
            "min_ath_drawdown",
            "max_ath_drawdown",
            "scan_interval_seconds",
        ):
            if key in settings:
                lines.append(
                    f"{key.replace('_', ' ').capitalize()}  "
                    f"{_text(setting_value(key, settings[key]), 100)}"
                )
        lines.append("Saved scan context; current edits apply at the next scan.")
    elif view == "near":
        lines = [
            "🔍 <b>Near misses / best observed</b>",
            "Saved observations, not fresh inspections.",
        ]
        for item in report["best_candidates"][:3]:
            reasons = item["rejection_reasons"]
            missing = item["missing_fields"]
            lines += [
                "",
                f"<b>{_text(item['symbol'], 30)} · {item['score']}/100</b>",
                f"{_text(item['chain'].upper(), 20)} · "
                f"{_text(item['status'].replace('_', ' ').capitalize(), 50)}",
                "Blocked: "
                + (" · ".join(_text(_label(v), 60) for v in reasons[:3]) or "Core checks passed"),
                "Missing: "
                + (" · ".join(_text(_label(v), 60) for v in missing[:2]) or "None recorded"),
                config_footer(item.get("configuration", {})),
            ]
        if not report["best_candidates"]:
            lines.append("No candidates scored 50+ yet. Check blockers and history coverage.")
    else:
        schedule = report.get("schedule", {})
        state = (
            "🔄 Scanning now"
            if schedule.get("running")
            else (
                "🟢 Waiting for next scan"
                if schedule
                else "Scanner status unavailable in this process"
            )
        )
        lines = [
            header,
            state,
            "",
            f"📬 <b>{alerts['sent']} alerts sent</b> · {alerts['failed']} failed · "
            f"{alerts['unknown']} unconfirmed",
            f"Pending delivery  {alerts['pending']}",
            f"🔄 {scans['completed']} scans completed · {scans['interrupted']} unfinished",
            f"Average {_duration(scans['duration_seconds']['mean'])} · {scans['errors']} errors",
            f"🔎 {report['discovery']['unique_tokens']} discovered · "
            f"{evaluations['count']} evaluated",
            f"{evaluations['eligible']} passed core checks",
            "",
            "<b>Data coverage</b>",
            *_quality(report)[:3],
            "",
            "<b>Biggest blockers</b>",
            _counted(evaluations["rejection_counts"]),
            "Blockers can overlap; unfinished includes running or interrupted scans.",
        ]
    return "\n".join(lines) + f"\n\n<i>{footer}</i>"

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
        "security.top10_ratio": "Top10",
        "security.dev_ratio": "Dev",
        "security.sniper_ratio": "Sniper",
        "security.bundler_ratio": "Bundler",
        "security.insider_ratio": "Insider",
    }
    coverage = evaluations.get("coverage", {})
    lines = [
        f"{label}  {
            100
            * (
                coverage.get(key, 0)
                if key.startswith('security.')
                else max(0, count - missing.get(key, 0))
            )
            / count:.0f}%"
        if count and (not key.startswith("security.") or coverage.get("known"))
        else f"{label}  No observations"
        for key, label in fields.items()
    ]
    for field, label in (
        ("candles", "Candle structure available"),
        ("security", "Any security data available"),
    ):
        lines.append(
            f"{label}  {100 * coverage.get(field, 0) / count:.0f}%"
            if count and coverage.get("known")
            else f"{label}  No observations"
        )
    return lines


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
        metrics = latest.get("performance")
        if metrics:
            lines += ["", "<b>Time by operation</b>"]
            for stage, label in (
                ("discovery", "Discovery"),
                ("market", "Market info"),
                ("security", "Security"),
                ("kline", "Candles"),
                ("helius", "Solana mint checks"),
                ("sqlite", "SQLite"),
                ("telegram", "Telegram"),
            ):
                lines.append(f"{label}  {metrics['seconds'].get(stage, 0):.2f}s")
            lines += ["", "<b>API attempts · retries included</b>"]
            routes = {
                "/v1/market/hot_searches": "Hot Search",
                "/v1/market/rank": "Trending",
                "/v1/token/info": "Token info",
                "/v1/token/security": "Security",
                "/v1/market/token_kline": "Candles",
                "helius:getAccountInfo": "Helius mint RPC",
            }
            for route, label in routes.items():
                lines.append(f"{label}  {metrics['calls'].get(route, 0)}")
            cache = metrics.get("cache", {})
            lines += ["", "<b>Cache hits / fetches</b>"]
            for kind, label in (
                ("security", "Security"),
                ("kline", "Candles"),
                ("helius", "Solana mint"),
            ):
                hits, fetches = cache.get(f"{kind}_hit", 0), cache.get(f"{kind}_fetch", 0)
                attempts = hits + fetches
                rate = f"{100 * hits / attempts:.0f}%" if attempts else "No requests"
                lines.append(f"{label}  {rate} · {hits} hits / {fetches} fetches")
            lines += ["", "<b>Latest pipeline · all chains</b>"]
            for key, label in (
                ("discovered", "Discovered"),
                ("prefilter_pass", "Prefilter"),
                ("market_pass", "Market"),
                ("baseline_ready", "Baseline"),
                ("activity_trigger", "Activity"),
                ("eligible", "Eligible"),
                ("alerted", "Sent"),
                ("outcome_polled", "Outcome polls"),
            ):
                lines.append(
                    f"{label}  {sum(c.get(key, 0) for c in latest.get('funnel', {}).values())}"
                )
            if metrics.get("deferred"):
                lines += ["", "<b>Budget deferrals</b>", _counted(metrics["deferred"])]
            if metrics.get("failures"):
                lines += ["", "<b>Operation failures</b>", _counted(metrics["failures"])]
            lines.append("Operation times include API waiting; concurrent stages can overlap.")
        else:
            lines += ["", "No per-stage metrics saved for this scan (legacy / unfinished)."]
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
        for chain, counts in latest.get("funnel", {}).items():
            lines += ["", f"<b>Latest {_text(chain.upper(), 20)} scan</b>"]
            for key, label in (
                ("discovered", "Discovered"),
                ("prefilter_pass", "Prefilter passed"),
                ("market_enriched", "Market enriched"),
                ("market_pass", "Market passed"),
                ("baseline_ready", "Baseline ready"),
                ("activity_trigger", "Returning activity"),
                ("kline_requested", "Candle requests"),
                ("base_detected", "Base detected"),
                ("serious_candidates", "Serious candidates"),
                ("security_requested", "Security requests"),
                ("eligible", "Eligible"),
                ("alerted", "Sent"),
            ):
                lines.append(f"{label}  {counts.get(key, 0)}")
            lines.append(
                "Stage counts describe operations, not a disjoint population; watchlist included."
            )
    elif view == "quality":
        lines = [
            "🧪 <b>Data coverage</b>",
            f"Based on {evaluations['count']} saved evaluations.",
            "",
            *_quality(report),
            "",
            "Recent history ready means enough recent samples, not necessarily a nonzero baseline.",
            "Missing data never means zero or safe. Coverage is not API success rate.",
            "Denominator: all saved evaluations, including skipped enrichment. "
            "Legacy diagnostics without field records have unknown coverage.",
            "Insider means suspected holdings, not insider trading volume.",
            "",
            "<b>Most common missing fields</b>",
            _counted(evaluations["missing_field_counts"], 5),
        ]
    elif view == "outcomes":
        lines = [
            "📈 <b>Post-alert outcomes</b>",
            "Sent alerts from last 7 days · first observation within +1h of each checkpoint.",
        ]
        for hours, item in report.get("outcomes", {}).items():
            value = item["median_return_pct"]
            change = f"{value:+.1f}%" if value is not None else "unknown"
            lines += [
                "",
                f"<b>+{hours}h</b> · {item['observed']} observations",
                f"Returns available {item['returns_available']} · Median {change}",
                f"Pending {item['pending']} · Missed {item['missed']}",
            ]
        lines.append(
            "Observed sample only; missing prices are not zero returns. No trades simulated."
        )
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
                "Setup "
                + _dimension(item.get("setup_score"))
                + " · Trigger "
                + _dimension(item.get("trigger_score"))
                + " · Confirm "
                + _dimension(item.get("confirmation_score")),
                "Blocked: "
                + (
                    " · ".join(_text(_label(v), 60) for v in reasons[:3])
                    or "No scoring blocker; delivery may be dry run, paused, or on cooldown"
                ),
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
        last_duration = (
            _duration(latest["duration_seconds"])
            if latest.get("duration_seconds") is not None
            else "unfinished / no data"
        )
        lines = [
            header,
            state,
            "Chains  " + _text(settings.get("enabled_chains", "unavailable"), 80),
            "",
            f"📬 <b>{alerts['sent']} alerts sent</b> · {alerts['failed']} failed · "
            f"{alerts['unknown']} unconfirmed",
            f"Pending delivery  {alerts['pending']}",
            f"🔄 {scans['completed']} scans completed · {scans['interrupted']} unfinished",
            f"Average {_duration(scans['duration_seconds']['mean'])} · "
            f"Last {last_duration} · {scans['errors']} errors",
            f"🔎 {report['discovery']['unique_tokens']} discovered · "
            f"{evaluations['count']} evaluated",
            f"{evaluations['eligible']} eligible observations",
            "",
            "<b>Data coverage</b>",
            _quality(report)[0],
            *_quality(report)[-2:],
            "",
            "<b>Biggest blockers</b>",
            _counted(evaluations["rejection_counts"]),
            "Blockers can overlap; unfinished includes running or interrupted scans.",
        ]
    suffix = f"\n\n<i>{footer}</i>"
    omitted = ""
    while len(("\n".join(lines) + omitted + suffix).encode("utf-16-le")) // 2 > 4000:
        lines.pop()
        omitted = "\nAdditional detail omitted; use the other health pages."
    return "\n".join(lines) + omitted + suffix


def _dimension(value) -> str:
    return f"{value}/100" if type(value) is int else "unknown"

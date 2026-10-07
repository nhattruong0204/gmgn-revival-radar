import html
import logging
from dataclasses import dataclass

import httpx

from revival_radar.chains import CHAINS
from revival_radar.config import Settings
from revival_radar.models.signal import RevivalResult
from revival_radar.models.token import TokenSnapshot

log = logging.getLogger(__name__)


def money(value: float | None) -> str:
    if value is None:
        return "unavailable"
    if abs(value) >= 1_000_000_000_000_000:
        return f"${value:.2e}"
    if abs(value) >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1000:
        return f"${value / 1000:.1f}K"
    return f"${value:.2f}"


def ratio_text(value: float | None) -> str:
    return "unavailable" if value is None else f"{(value - 1) * 100:+.0f}%"


def _utf16_size(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text(value: object, budget: int = 100) -> str:
    """Bound escaped HTML as well as visible text, without splitting an entity."""
    plain = " ".join(str(value).split())
    escaped = html.escape(plain)
    if _utf16_size(escaped) <= budget:
        return escaped
    result: list[str] = []
    size = 1  # Reserve one UTF-16 unit for the ellipsis.
    for character in plain:
        part = html.escape(character)
        size += _utf16_size(part)
        if size > budget:
            break
        result.append(part)
    return "".join(result) + "…"


_COMPONENT_LABELS = {
    "drawdown": "Drawdown in range",
    "liquidity": "Liquidity meets minimum",
    "holders": "Holder retention",
    "base": "Base formed",
    "base_duration": "Established base",
    "volume_5m": "5m volume above baseline",
    "volume_1h": "Hourly volume rising",
    "transactions": "Transactions rising",
    "hot_rank": "Hot Search rank improving",
    "both_sources": "On both discovery lists",
    "higher_low": "Higher low",
    "higher_high": "Higher high",
    "breakout": "Breakout",
    "concentration_penalty": "High holder concentration",
    "danger_penalty": "Known dangerous-token flag",
    "sniper_penalty": "High sniper holdings",
    "bundler_penalty": "High bundled holdings",
    "liquidity_penalty": "Liquidity dropped sharply",
    "dev_penalty": "Developer holdings dropped sharply",
    "insider_penalty": "Insider holdings dropped sharply",
}


def _reason(value: str) -> str:
    name, separator, points = value.partition(":")
    label = _COMPONENT_LABELS.get(name)
    return f"{label} {points.strip()}" if label and separator else value.replace("_", " ")


def _warning(value: str) -> str:
    return _COMPONENT_LABELS.get(value.replace(" ", "_"), value)


def format_alert(token: TokenSnapshot, result: RevivalResult) -> str:
    a, s = result.acceleration, result.structure
    drawdown = (
        f"{token.drawdown_from_ath:.1%}" if token.drawdown_from_ath is not None else "unavailable"
    )
    age = (
        f"{token.token_age_seconds / 86400:.1f}d"
        if token.token_age_seconds is not None
        else "unavailable"
    )
    rank = f"#{token.hot_search_rank}" if token.hot_search_rank is not None else "unavailable"
    if a.previous_hot_rank is not None:
        rank = f"#{a.previous_hot_rank} → {rank}"
    trending = f"#{token.trending_rank}" if token.trending_rank is not None else "unavailable"
    holders = f"{token.holders:,}" if token.holders is not None else "unavailable"
    transactions = str(token.tx_5m) if token.tx_5m is not None else "unavailable"
    concentration = (
        f"{token.security.top10_ratio:.1%}"
        if token.security.top10_ratio is not None
        else "unavailable"
    )
    status = result.status.replace("_", " ").capitalize()
    lines = [
        f"♻️ <b>${_text(token.symbol, 80)} · {_text(result.score, 12)}/100</b>",
        f"{CHAINS[token.chain].display_name} · <b>{_text(status, 60)}</b>",
        "",
        "💰 <b>Market</b>",
        f"Cap <b>{money(token.market_cap)}</b> · Liquidity <b>{money(token.liquidity)}</b>",
        f"ATH drawdown {_text(drawdown, 24)} · Holders {_text(holders, 24)}",
        "",
        "📈 <b>Activity</b>",
        f"Volume 5m <b>{money(token.volume_5m)}</b> · "
        f"{_text(ratio_text(a.volume_acceleration_5m), 24)} vs prior",
        f"Vs baseline {_text(ratio_text(a.volume_ratio_5m), 24)} · 1h {money(token.volume_1h)}",
        f"Transactions 5m <b>{_text(transactions, 24)}</b>"
        f" · {_text(ratio_text(a.tx_acceleration_5m), 24)} vs prior",
        "",
    ]
    details = [
        "<b>Signal details</b>",
        f"Age {_text(age, 24)} · ATH cap {money(token.ath_market_cap)}",
        f"Hot Search {_text(rank, 48)} · Trending {_text(trending, 24)}",
    ]
    if not s.available:
        lines.append("📊 <b>Structure</b> · unavailable")
        details.append("Unavailable · insufficient candle history")
    else:
        lines.append(
            f"📊 <b>Base confirmed · {_text(f'{s.base_duration_hours:.0f}', 12)}h</b>"
            if s.base_detected
            else "📊 <b>Base unconfirmed</b>"
        )
        patterns = []
        if s.higher_low_detected:
            patterns.append("Higher low")
        if s.higher_high_detected:
            patterns.append("Higher high")
        if s.breakout_detected:
            patterns.append("Breakout")
        if s.retest_detected:
            patterns.append("Retest")
        if patterns:
            lines.append(" · ".join(patterns))
        details.append(
            f"{'Higher low confirmed' if s.higher_low_detected else 'Higher low unconfirmed'} · "
            f"{'Higher high confirmed' if s.higher_high_detected else 'Higher high unconfirmed'}"
        )
        details.append(
            f"{'Breakout detected' if s.breakout_detected else 'Breakout unconfirmed'} · "
            f"{'Retest confirmed' if s.retest_detected else 'Retest unconfirmed'}"
        )

    if result.components:
        # Show the largest positive drivers. Risk deductions remain visible below.
        positives = sorted(
            ((key, value) for key, value in result.components.items() if value > 0),
            key=lambda item: item[1],
            reverse=True,
        )
        reasons = [
            f"{_COMPONENT_LABELS.get(key, key.replace('_', ' '))} +{value}"
            for key, value in positives
        ]
    else:
        reasons = [_reason(reason) for reason in result.reasons]
    if reasons:
        details.extend(["", "🎯 <b>Top score drivers</b>"])
        details.extend(f"• {_text(reason, 120)}" for reason in reasons[:3])
        if len(reasons) > 3:
            details.append(f"+{len(reasons) - 3} other scoring factors")

    warnings = list(dict.fromkeys(_warning(warning) for warning in result.warnings))
    risk_findings = []
    for key, value in result.components.items():
        if value < 0:
            warning = _COMPONENT_LABELS.get(key, key.replace("_", " "))
            if warning in warnings:
                warnings.remove(warning)
            risk_findings.append(f"{warning} ({value})")
    if token.security.dangerous is True and result.components.get("danger_penalty", 0) >= 0:
        risk_findings.insert(0, "Known dangerous-token flag")
    if token.security.dangerous is None:
        risk_findings.insert(0, "Security assessment unavailable; absence is not safety")
    # Keep safety findings ahead of verbose upstream diagnostic text.
    warnings.sort(
        key=lambda warning: (
            not any(
                word in warning.lower()
                for word in ("danger", "security", "concentration", "sniper", "bundl", "dropped")
            )
        )
    )
    warnings = list(dict.fromkeys(risk_findings + warnings))
    lines.extend(["", "⚠️ <b>Watchouts</b>", f"Top 10 holders {_text(concentration, 24)}"])
    lines.extend(f"• {_text(warning, 160)}" for warning in warnings[:8])
    if len(warnings) > 8:
        lines.append(f"+{len(warnings) - 8} additional data notes")

    lines.extend(["", "<blockquote expandable>" + "\n".join(details) + "</blockquote>"])
    lines.extend(["", f"<code>{html.escape(token.contract_address)}</code>"])
    links = [
        f'<a href="{html.escape(url, quote=True)}">{label}</a>'
        for label, url in CHAINS[token.chain].links(token.contract_address).items()
    ]
    lines.append(" · ".join(links))
    lines.append("<i>Heuristic signal, not certainty. No trades executed.</i>")
    return "\n".join(lines)


@dataclass(frozen=True)
class Delivery:
    status: str
    message_id: int | None = None


class TelegramClient:
    def __init__(self, config: Settings, http: httpx.AsyncClient):
        self.config, self.http = config, http

    async def send_text(self, message: str) -> Delivery:
        if self.config.dry_run:
            log.info("dry_run telegram suppressed")
            return Delivery("dry_run")
        token = self.config.telegram_bot_token.get_secret_value()
        if not token or not self.config.telegram_chat_id:
            log.error("Telegram credentials missing")
            return Delivery("failed")
        try:
            response = await self.http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=self.config.http_timeout_seconds,
            )
        except httpx.TransportError as exc:
            # Telegram has no idempotency key. A timeout might have delivered;
            # do not immediately resend and produce duplicates.
            log.warning("telegram delivery uncertain error=%s", type(exc).__name__)
            return Delivery("unknown")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.is_success and data.get("ok") is True:
            result = data.get("result")
            message_id = result.get("message_id") if isinstance(result, dict) else None
            return Delivery("sent", message_id if isinstance(message_id, int) else None)
        log.warning("telegram rejected HTTP=%d", response.status_code)
        return Delivery("unknown" if response.status_code >= 500 else "failed")

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
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1000:
        return f"${value / 1000:.1f}K"
    return f"${value:.2f}"


def ratio_text(value: float | None) -> str:
    return "unavailable" if value is None else f"{(value - 1) * 100:+.0f}%"


def format_alert(token: TokenSnapshot, result: RevivalResult) -> str:
    escape = html.escape
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
    holders = f"{token.holders:,}" if token.holders is not None else "unavailable"
    concentration = (
        f"{token.security.top10_ratio:.1%}"
        if token.security.top10_ratio is not None
        else "unavailable"
    )
    lines = [
        f"♻️ <b>REVIVAL RADAR — {result.score}/100</b>",
        f"${escape(token.symbol)} | {escape(CHAINS[token.chain].display_name)}",
        "",
        f"MC: {money(token.market_cap)} | ATH MC: {money(token.ath_market_cap)}",
        f"ATH drawdown: {drawdown} | Liquidity: {money(token.liquidity)}",
        f"Age: {age} | Holders: {holders}",
        "",
        f"🔥 Hot Search: {rank} | Trending: {token.trending_rank or 'unavailable'}",
        f"📈 Volume 5m: {money(token.volume_5m)} ({ratio_text(a.volume_acceleration_5m)} vs prior)",
        f"Volume 5m vs baseline: {ratio_text(a.volume_ratio_5m)} | 1h: {money(token.volume_1h)}",
        f"🔄 TX 5m: {token.tx_5m if token.tx_5m is not None else 'unavailable'} "
        f"({ratio_text(a.tx_acceleration_5m)})",
        "",
        f"📊 Base: {'yes' if s.base_detected else 'unconfirmed'} ({s.base_duration_hours:.0f}h)",
        f"Higher low: {s.higher_low_detected} | Higher high: {s.higher_high_detected}",
        f"Breakout: {s.breakout_detected} | Retest: {s.retest_detected}",
        f"Top 10 concentration: {concentration}",
        f"Status: <b>{escape(result.status)}</b>",
        "Reasons: " + escape(", ".join(result.reasons)[:750]),
    ]
    if result.warnings:
        lines.append("⚠️ " + escape("; ".join(result.warnings)[:750]))
    lines.extend(["", f"CA: <code>{escape(token.contract_address)}</code>"])
    for label, url in CHAINS[token.chain].links(token.contract_address).items():
        lines.append(f'<a href="{escape(url, quote=True)}">{label}</a>')
    lines.append("Heuristic signal, not financial certainty. No trades executed.")
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

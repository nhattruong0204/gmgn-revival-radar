"""Owner-only Telegram menus; settings changes require a fresh confirmation."""

import asyncio
import html
import inspect
import logging
import math
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from revival_radar.chains import CHAINS
from revival_radar.runtime_settings import PRESETS, RuntimeSettings, SettingsChangeError

log = logging.getLogger(__name__)

# Each setting has a short, stable callback identifier and a readable label.
GROUPS = {
    "filters": [
        ("token_min_age_hours", "Minimum age (hours)"),
        ("min_market_cap", "Minimum market cap ($)"),
        ("max_market_cap", "Maximum market cap ($)"),
        ("min_liquidity", "Minimum liquidity ($)"),
        ("min_holders", "Minimum holders"),
        ("min_ath_drawdown", "Minimum ATH drawdown (0–1)"),
        ("max_ath_drawdown", "Maximum ATH drawdown (0–1)"),
        ("min_volume_1h", "Minimum 1h volume ($)"),
        ("max_price_change_5m", "Maximum 5m price change (%)"),
        ("max_price_change_1h", "Maximum 1h price change (%)"),
    ],
    "structure": [
        ("base_min_hours", "Minimum base (hours)"),
        ("base_sufficient_hours", "Sufficient base (hours)"),
        ("volume_acceleration_threshold", "Volume acceleration ratio"),
        ("tx_acceleration_threshold", "Transaction acceleration ratio"),
        ("base_max_range_ratio", "Maximum base range ratio"),
        ("holder_retention_ratio", "Holder retention ratio"),
        ("top10_max_ratio", "Maximum top-10 holder ratio"),
    ],
    "alerts": [
        ("alert_score_threshold", "Minimum alert score"),
        ("alert_cooldown_hours", "Alert cooldown (hours)"),
        ("realert_score_increase", "Re-alert score increase"),
        ("daily_summary_hour", "Daily summary hour (0–23)"),
    ],
    "discovery": [
        ("discovery_limit", "Discovery limit per source"),
        ("watchlist_limit", "Watchlist limit"),
        ("watchlist_hours", "Watchlist lifetime (hours)"),
        ("scan_interval_seconds", "Scan interval (seconds)"),
        ("request_spacing_seconds", "API request spacing (seconds)"),
        ("history_observations", "History observations"),
        ("minimum_history_observations", "Minimum history observations"),
        ("history_max_gap_seconds", "Maximum history gap (seconds)"),
    ],
}
LABELS = dict(item for group in GROUPS.values() for item in group) | {
    "alerts_paused": "Alerts paused",
    "dry_run": "Dry-run alerts",
    "daily_summary_enabled": "Daily summary",
    "enabled_chains": "Chains",
    "discovery_interval": "Discovery interval",
    "exclude_tokenized_stocks": "Exclude stocks",
    "exclude_stablecoins": "Exclude stablecoins",
    "exclude_wrapped_assets": "Exclude wrapped assets",
}
NUMERIC_FIELDS = {field for group in GROUPS.values() for field, _ in group}
TOGGLES = {
    "alerts_paused",
    "dry_run",
    "daily_summary_enabled",
    "exclude_tokenized_stocks",
    "exclude_stablecoins",
    "exclude_wrapped_assets",
}


class ControlsError(RuntimeError):
    """Sanitized Telegram failure, safe to display or log."""

    def __init__(self, message: str, *, fatal: bool = False, retry_after: float = 0):
        super().__init__(message)
        self.fatal, self.retry_after = fatal, retry_after


@dataclass
class Proposal:
    nonce: str
    revision: int
    changes: dict[str, Any]
    expires: float
    reset: bool = False


class TelegramControls:
    def __init__(
        self,
        runtime: RuntimeSettings,
        http: httpx.AsyncClient,
        health_provider: Callable[[], Any] | None = None,
    ):
        self.runtime, self.http, self.health_provider = runtime, http, health_provider
        config = runtime.effective()
        configured_owner = config.telegram_owner_id
        chat_id = config.telegram_chat_id.strip()
        self.owner_id = (
            configured_owner
            if configured_owner > 0
            else (
                int(chat_id)
                if chat_id.isascii() and chat_id.isdecimal() and int(chat_id) > 0
                else 0
            )
        )
        self.enabled = bool(
            config.telegram_controls_enabled
            and self.owner_id > 0
            and config.telegram_bot_token.get_secret_value()
        )
        self.status = "ready" if self.enabled else "disabled (owner or credentials unavailable)"
        self._boot_time = int(time.time())
        self._stop = asyncio.Event()
        self._proposal: Proposal | None = None
        self._input: tuple[str, int, float] | None = None

    def stop(self) -> None:
        self._stop.set()

    def _safe(self, value: str) -> str:
        for secret in (
            self.runtime.base.gmgn_api_key,
            self.runtime.base.telegram_bot_token,
        ):
            raw = secret.get_secret_value()
            if raw:
                value = value.replace(raw, "[redacted]").replace(html.escape(raw), "[redacted]")
        return value

    async def _request(self, method: str, payload: dict[str, Any]) -> Any:
        token = self.runtime.base.telegram_bot_token.get_secret_value()
        timeout = 40 if method == "getUpdates" else 15
        try:
            response = await self.http.post(
                f"https://api.telegram.org/bot{token}/{method}",
                json=payload,
                timeout=httpx.Timeout(timeout, connect=10),
            )
        except httpx.TransportError:
            raise ControlsError("Telegram connection failed; retrying.") from None
        try:
            data = response.json()
        except ValueError:
            data = None
        code = (
            data.get("error_code", response.status_code)
            if isinstance(data, dict)
            else (response.status_code)
        )
        if code == 409:
            raise ControlsError(
                "Telegram polling conflict: another poller or webhook is active. "
                "Stop the other poller or review the webhook configuration.",
                fatal=True,
            )
        if code in (401, 403):
            raise ControlsError("Telegram rejected control access; check bot access.", fatal=True)
        if code == 429:
            parameters = data.get("parameters", {}) if isinstance(data, dict) else {}
            retry = parameters.get("retry_after", 5) if isinstance(parameters, dict) else 5
            retry = float(retry) if type(retry) in (int, float) else 5.0
            retry = min(max(retry, 1), 300) if math.isfinite(retry) else 5.0
            raise ControlsError("Telegram rate limited controls; retrying.", retry_after=retry)
        if not response.is_success or not isinstance(data, dict) or data.get("ok") is not True:
            raise ControlsError("Telegram returned an unsuccessful control response.")
        return data.get("result")

    async def _interruptible(self, awaitable: Any) -> Any:
        operation = asyncio.ensure_future(awaitable)
        stopper = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait({operation, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                return await operation
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            return None
        finally:
            operation.cancel()
            stopper.cancel()
            await asyncio.gather(operation, stopper, return_exceptions=True)

    async def run(self) -> None:
        if not self.enabled:
            log.warning("telegram controls disabled: configure a positive owner ID and bot token")
            return
        delay = 1.0
        self.status = "polling"
        try:
            while not self._stop.is_set():
                try:
                    updates = await self._interruptible(
                        self._request(
                            "getUpdates",
                            {
                                "offset": self.runtime.offset,
                                "timeout": 25,
                                "limit": 100,
                                "allowed_updates": ["message", "callback_query"],
                            },
                        )
                    )
                    if self._stop.is_set():
                        break
                    if not isinstance(updates, list):
                        raise ControlsError("Telegram returned an invalid update list.")
                    for update in updates:
                        if self._stop.is_set():
                            break
                        update_id = update.get("update_id") if isinstance(update, dict) else None
                        if type(update_id) is not int or update_id < self.runtime.offset:
                            continue
                        # Acknowledge durably before handling. Lost replies are safe; replayed
                        # confirmations after a crash must never repeat a mutation.
                        self.runtime.set_offset(update_id + 1)
                        await self._interruptible(self.handle_update(update))
                    delay = 1.0
                    self.status = "polling"
                except ControlsError as exc:
                    self.status = str(exc)
                    log.warning("telegram controls: %s", exc)
                    if exc.fatal:
                        raise
                    await self._interruptible(asyncio.sleep(max(delay, exc.retry_after)))
                    delay = min(delay * 2, 30)
        except SettingsChangeError as exc:
            self.status = str(exc)
            log.error("telegram controls: %s", exc)
            raise ControlsError(str(exc), fatal=True) from None
        finally:
            if self.status == "polling":
                self.status = "stopped"

    def _authorized(self, sender: Any, chat: Any) -> bool:
        return bool(
            self.enabled
            and isinstance(sender, dict)
            and isinstance(chat, dict)
            and type(sender.get("id")) is int
            and type(chat.get("id")) is int
            and sender["id"] == self.owner_id
            and chat["id"] == self.owner_id
            and chat.get("type") == "private"
            and not sender.get("is_bot", False)
        )

    def _button(self, label: str, action: str) -> dict[str, str]:
        data = f"r:{self.runtime.revision}:{action}"
        if len(data.encode()) > 64:
            raise ControlsError("Control callback is too long.")
        return {"text": label, "callback_data": data}

    async def _send(self, text: str, rows: list[list[dict[str, str]]] | None = None) -> None:
        # All text is created internally with escaped values. Keep headroom for Telegram.
        text = self._safe(text)
        if len(text.encode("utf-16-le")) // 2 > 4096:
            text = "Status is too large to display. Open a specific settings page."
            rows = None
        payload: dict[str, Any] = {
            "chat_id": self.owner_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if rows:
            payload["reply_markup"] = {"inline_keyboard": rows}
        await self._request("sendMessage", payload)

    async def handle_update(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            message = callback.get("message", {})
            if not isinstance(message, dict) or not self._authorized(
                callback.get("from"), message.get("chat")
            ):
                return
            callback_id = callback.get("id")
            if isinstance(callback_id, str):
                await self._request("answerCallbackQuery", {"callback_query_id": callback_id})
            if not self._fresh(message):
                await self._send("This menu is from an earlier session. Open /menu again.")
                return
            data = callback.get("data")
            if not isinstance(data, str) or len(data.encode()) > 64:
                return
            await self._callback(data)
            return
        message = update.get("message")
        if (
            not isinstance(message, dict)
            or message.get("sender_chat")
            or not self._authorized(message.get("from"), message.get("chat"))
        ):
            return
        if not self._fresh(message):
            return
        value = message.get("text")
        if not isinstance(value, str):
            return
        command = value.split(maxsplit=1)[0].split("@")[0] if value.strip() else ""
        if command in {"/start", "/menu", "/settings"}:
            self._input = None
            await self._menu("settings" if command == "/settings" else "home")
        elif command in {"/status", "/health"}:
            await self._health() if command == "/health" else await self._status()
        elif command == "/cancel":
            self._input, self._proposal = None, None
            await self._menu("home")
        elif self._input is not None:
            await self._custom(value)
        else:
            await self._send("Use /menu to review settings, or /health for scanner diagnostics.")

    def _fresh(self, message: dict[str, Any]) -> bool:
        date = message.get("date")
        return type(date) is int and date >= self._boot_time

    async def _callback(self, data: str) -> None:
        parts = data.split(":", 3)
        if len(parts) < 3 or parts[0] != "r" or not parts[1].isdecimal():
            return
        if int(parts[1]) != self.runtime.revision:
            await self._send("Settings changed. Open /menu and review a new preview.")
            return
        action, value = parts[2], parts[3] if len(parts) == 4 else ""
        if action != "yes":
            self._proposal = None
        try:
            if action == "m":
                self._input = None
                await self._menu(value)
            elif action == "status":
                await self._status()
            elif action == "health":
                await self._health()
            elif action == "p" and value in PRESETS:
                await self._preview(PRESETS[value], title=f"{value.title()} preset")
            elif action == "t" and value in TOGGLES:
                await self._preview({value: not getattr(self.runtime.effective(), value)})
            elif action == "c" and value in CHAINS:
                chains = self.runtime.effective().chains
                chains = (
                    [chain for chain in chains if chain != value]
                    if value in chains
                    else (chains + [value])
                )
                await self._preview({"enabled_chains": ",".join(chains)})
            elif action == "d" and value in {"1m", "5m", "1h", "6h", "24h"}:
                await self._preview({"discovery_interval": value})
            elif action == "n" and value in NUMERIC_FIELDS:
                self._input = (value, self.runtime.revision, time.monotonic() + 600)
                current = getattr(self.runtime.effective(), value)
                await self._send(
                    f"Send a number for <b>{html.escape(LABELS[value])}</b>.\n"
                    f"Current: {html.escape(str(current))}\n"
                    "You will review a confirmation before applying. Use /cancel to cancel."
                )
            elif action == "reset":
                await self._preview({}, title="Reset to startup configuration", reset=True)
            elif action == "yes":
                await self._confirm(value)
            elif action == "cancel":
                self._proposal, self._input = None, None
                await self._menu("home")
        except SettingsChangeError as exc:
            await self._send(str(exc))

    async def _menu(self, name: str) -> None:
        config = self.runtime.effective()
        rows: list[list[dict[str, str]]] = []
        if name in {"home", "settings"}:
            title = "Revival Radar controls"
            rows = [
                [self._button("Status", "status"), self._button("Health", "health")],
                [self._button("Strategy presets", "m:presets")],
                [self._button("Advanced configuration", "m:advanced")],
                [
                    self._button("Token filters", "m:filters"),
                    self._button("Base / ratios", "m:structure"),
                ],
                [self._button("Alerts", "m:alerts"), self._button("Discovery", "m:discovery")],
                [self._button("Chains", "m:chains"), self._button("Asset exclusions", "m:assets")],
                [
                    self._button(
                        "Resume alerts" if config.alerts_paused else "Pause alerts",
                        "t:alerts_paused",
                    )
                ],
                [self._button("Reset overrides", "reset")],
            ]
        elif name == "presets":
            title = "Strategy presets — choose one to review all threshold changes"
            rows = [[self._button(p.title(), f"p:{p}")] for p in PRESETS]
            rows.append([self._button("Advanced configuration", "m:advanced")])
        elif name == "advanced":
            title = (
                "Advanced configuration\n"
                "Choose a setting, send its new value, then confirm. "
                "Use full numbers (15000), drawdown ratios (0.60), "
                "and price-change percentages (40).\n"
                "Edits are saved across restarts. Selecting a preset later replaces "
                "the values controlled by that preset."
            )
            fields = dict.fromkeys(key for preset in PRESETS.values() for key in preset)
            fields["scan_interval_seconds"] = None
            rows = [
                [self._button(f"{LABELS[key]}: {getattr(config, key)}", f"n:{key}")]
                for key in fields
            ]
        elif name in GROUPS:
            title = name.title()
            rows = [
                [self._button(f"{label}: {getattr(config, key)}", f"n:{key}")]
                for key, label in GROUPS[name]
            ]
            if name == "alerts":
                rows.extend(
                    [
                        [
                            self._button(
                                f"{LABELS[key]}: {'on' if getattr(config, key) else 'off'}",
                                f"t:{key}",
                            )
                        ]
                        for key in ("alerts_paused", "dry_run", "daily_summary_enabled")
                    ]
                )
                title += f"\nSummary timezone: {html.escape(config.report_timezone)}"
            if name == "discovery":
                title += f"\nCurrent interval: {html.escape(config.discovery_interval)}"
                rows.append(
                    [
                        self._button(interval, f"d:{interval}")
                        for interval in ("1m", "5m", "1h", "6h", "24h")
                    ]
                )
        elif name == "chains":
            title = "Enabled chains — at least one is required"
            rows = [
                [
                    self._button(
                        f"{'✓' if key in config.chains else '○'} {chain.display_name}", f"c:{key}"
                    )
                ]
                for key, chain in CHAINS.items()
            ]
        elif name == "assets":
            title = "Asset exclusions"
            rows = [
                [
                    self._button(
                        f"{LABELS[key]}: {'on' if getattr(config, key) else 'off'}", f"t:{key}"
                    )
                ]
                for key in (
                    "exclude_tokenized_stocks",
                    "exclude_stablecoins",
                    "exclude_wrapped_assets",
                )
            ]
        else:
            return
        if name not in {"home", "settings"}:
            rows.append([self._button("Back to menu", "m:home")])
        await self._send(
            f"<b>{title}</b>\nRevision {self.runtime.revision}. "
            "Changes apply to the next scan; scans continue while alerts are paused.",
            rows,
        )

    async def _preview(
        self, changes: dict[str, Any], title: str = "Review settings change", reset: bool = False
    ) -> None:
        self._proposal = None
        before = self.runtime.public_values()
        candidate = self.runtime.base if reset else self.runtime.preview(changes)
        after = candidate.model_dump(mode="json")
        keys = before.keys() if reset else changes.keys()
        lines = [f"<b>{html.escape(title)}</b>"]
        for key in sorted(keys):
            if before[key] != after[key]:
                label = LABELS.get(key, key.replace("_", " ").title())
                lines.append(
                    f"{html.escape(label)}: {html.escape(str(before[key]))} → "
                    f"<b>{html.escape(str(after[key]))}</b>"
                )
        if len(lines) == 1:
            await self._send("These settings already match. Open /menu for other changes.")
            return
        if changes.get("dry_run") is False or (
            reset and before.get("dry_run") and not after["dry_run"]
        ):
            lines.append("Live alert delivery will be enabled.")
        if any(key in changes for key in ("discovery_limit", "watchlist_limit", "enabled_chains")):
            lines.append("A larger discovery universe can increase API usage.")
        if "request_spacing_seconds" in changes:
            lines.append("Shorter request spacing can trigger API rate limits.")
        if changes.get("alerts_paused") is True or changes.get("dry_run") is True:
            lines.append("Alert suppression takes effect immediately, including the current scan.")
        lines.append("Confirm within 10 minutes. Strategy changes apply at the next scan.")
        proposal = Proposal(
            secrets.token_hex(6),
            self.runtime.revision,
            dict(changes),
            time.monotonic() + 600,
            reset,
        )
        chunks: list[str] = []
        chunk = ""
        for line in lines:
            if len(line.encode("utf-16-le")) // 2 > 3900:
                raise SettingsChangeError(
                    "This change is too large to preview. Edit fewer settings."
                )
            combined = f"{chunk}\n{line}" if chunk else line
            if len(combined.encode("utf-16-le")) // 2 > 3900:
                chunks.append(chunk)
                chunk = line
            else:
                chunk = combined
        chunks.append(chunk)
        for part in chunks[:-1]:
            await self._send(part)
        self._proposal = proposal
        try:
            await self._send(
                chunks[-1],
                [
                    [
                        self._button("Confirm", f"yes:{proposal.nonce}"),
                        self._button("Cancel", "cancel"),
                    ]
                ],
            )
        except ControlsError:
            self._proposal = None
            raise

    async def _confirm(self, nonce: str) -> None:
        proposal = self._proposal
        if proposal is None or not secrets.compare_digest(proposal.nonce.encode(), nonce.encode()):
            await self._send("This confirmation is unavailable. Open /menu for a new preview.")
            return
        self._proposal = None
        if proposal.expires < time.monotonic():
            await self._send("This preview expired. Open /menu and review it again.")
            return
        if proposal.reset:
            self.runtime.reset(expected_revision=proposal.revision)
        else:
            self.runtime.apply(proposal.changes, expected_revision=proposal.revision)
        self._input = None
        await self._send(
            f"Saved revision {self.runtime.revision}. New settings take effect on the next scan.",
            [
                [self._button("Advanced configuration", "m:advanced")],
                [self._button("Open menu", "m:home")],
            ],
        )

    async def _custom(self, text: str) -> None:
        assert self._input is not None
        key, revision, expires = self._input
        if revision != self.runtime.revision or expires < time.monotonic():
            self._input = None
            await self._send("This input request expired or settings changed. Open /menu again.")
            return
        try:
            if len(text) > 64:
                raise ValueError
            value: int | float = float(text.strip())
            if not math.isfinite(value):
                raise ValueError
            if type(getattr(self.runtime.effective(), key)) is int:
                if not value.is_integer():
                    raise ValueError
                value = int(value)
            await self._preview({key: value})
            self._input = None
        except (ValueError, SettingsChangeError):
            await self._send("Invalid number or conflicting ranges. Try again, or use /cancel.")

    async def _status(self) -> None:
        config = self.runtime.effective()
        await self._send(
            "<b>Radar status</b>\n"
            f"Controls: {html.escape(self.status)}\nRevision: {self.runtime.revision}\n"
            f"Chains: {html.escape(', '.join(config.chains))}\n"
            f"Scan interval: {config.scan_interval_seconds:g}s\n"
            f"Alerts: {'paused' if config.alerts_paused else 'running'}\n"
            f"Dry-run alerts: {'on' if config.dry_run else 'off'}\n"
            f"Alert score: {config.alert_score_threshold}/100\n"
            f"Active scan revision: {self.runtime.active_revision}\n"
            f"Daily summary: {'on' if config.daily_summary_enabled else 'off'} "
            f"at {config.daily_summary_hour:02}:00 {html.escape(config.report_timezone)}\n"
            "Strategy changes apply at the next scan.\n"
            "Use /health for completed scans, filtering, and delivery results.",
            [[self._button("Open menu", "m:home")]],
        )

    async def _health(self) -> None:
        if self.health_provider is None:
            await self._send("Scanner diagnostics are not available yet.")
            return
        try:
            result = self.health_provider()
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, Mapping):
                from revival_radar.diagnostics import format_health

                text = format_health(
                    dict(result)
                    | {
                        "timezone": self.runtime.effective().report_timezone,
                    }
                )
            else:
                text = html.escape(str(result))
        except Exception:
            # Internal diagnostics can contain upstream failures: never echo their exceptions.
            await self._send("Scanner diagnostics could not be loaded. Try again later.")
            return
        await self._send(text, [[self._button("Open menu", "m:home")]])

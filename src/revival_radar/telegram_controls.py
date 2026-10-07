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
from revival_radar.telegram_presentation import (
    SHORT_LABELS,
    parse_setting_number,
    setting_hint,
    setting_value,
)

log = logging.getLogger(__name__)

# Each setting has a short, stable callback identifier and a readable label.
GROUPS = {
    "activity": [
        ("volume_acceleration_threshold", "Volume multiplier"),
        ("tx_acceleration_threshold", "Transaction multiplier"),
    ],
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
ADVANCED_PAGES = (
    (
        "Market & eligibility",
        (
            "alert_score_threshold",
            "token_min_age_hours",
            "min_market_cap",
            "max_market_cap",
            "min_liquidity",
            "min_holders",
            "min_ath_drawdown",
            "max_ath_drawdown",
        ),
    ),
    (
        "Activity & chart structure",
        (
            "min_volume_1h",
            "max_price_change_5m",
            "max_price_change_1h",
            "base_min_hours",
            "base_sufficient_hours",
            "volume_acceleration_threshold",
            "tx_acceleration_threshold",
        ),
    ),
    (
        "Delivery & discovery",
        (
            "alert_cooldown_hours",
            "realert_score_increase",
            "discovery_limit",
            "watchlist_limit",
            "watchlist_hours",
            "scan_interval_seconds",
        ),
    ),
)


class ControlsError(RuntimeError):
    """Sanitized Telegram failure, safe to display or log."""

    def __init__(
        self,
        message: str,
        *,
        fatal: bool = False,
        retry_after: float = 0,
        edit_unavailable: bool = False,
    ):
        super().__init__(message)
        self.fatal, self.retry_after = fatal, retry_after
        self.edit_unavailable = edit_unavailable


@dataclass
class Proposal:
    nonce: str
    revision: int
    changes: dict[str, Any]
    expires: float
    reset: bool = False
    action: str | None = None


class TelegramControls:
    def __init__(
        self,
        runtime: RuntimeSettings,
        http: httpx.AsyncClient,
        health_provider: Callable[[], Any] | None = None,
        detail_provider: Callable[[str, int], Any] | None = None,
        scan_request: Callable[[], str] | None = None,
        schedule_provider: Callable[[], dict] | None = None,
    ):
        self.runtime, self.http, self.health_provider = runtime, http, health_provider
        self.detail_provider, self.scan_request = detail_provider, scan_request
        self.schedule_provider = schedule_provider
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
        self._message_to_edit: int | None = None
        self._current_menu = "advanced"

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
        if method == "editMessageText" and code == 400 and isinstance(data, dict):
            description = str(data.get("description", "")).lower()
            if "message is not modified" in description:
                return None
            if (
                "message to edit not found" in description
                or "message can't be edited" in description
            ):
                raise ControlsError("This menu can no longer be edited.", edit_unavailable=True)
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

    async def _send(
        self, text: str, rows: list[list[dict[str, str]]] | None = None, *, edit: bool = False
    ) -> None:
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
        if edit and self._message_to_edit is not None:
            try:
                await self._request(
                    "editMessageText", payload | {"message_id": self._message_to_edit}
                )
                return
            except ControlsError as exc:
                if not exc.edit_unavailable:
                    raise
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
            detail_data = callback.get("data")
            if (
                isinstance(detail_data, str)
                and len(detail_data) <= 64
                and detail_data.startswith(("a:", "e:"))
            ):
                await self._detail(detail_data)
                return
            if not self._fresh(message):
                await self._send("This menu is from an earlier session. Open /menu again.")
                return
            data = callback.get("data")
            if not isinstance(data, str) or len(data.encode()) > 64:
                return
            message_id = message.get("message_id")
            self._message_to_edit = (
                message_id if type(message_id) is int and message_id > 0 else None
            )
            try:
                await self._callback(data)
            finally:
                self._message_to_edit = None
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
            await self._menu("settings_hub" if command == "/settings" else "home")
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
                await self._health(value or "overview")
            elif action == "act" and value in {"scan", "test"}:
                await self._action_preview(value)
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
                    f"✏️ <b>{html.escape(SHORT_LABELS.get(value, LABELS[value]))}</b>\n\n"
                    f"Current  <b>{html.escape(setting_value(value, current))}</b>\n\n"
                    f"{html.escape(setting_hint(value))}\n\n"
                    "Send the new value in this chat. Nothing changes until you confirm.",
                    [[self._button("✖ Cancel", f"m:{self._current_menu}")]],
                    edit=True,
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

    def _strategy_name(self) -> str:
        config = self.runtime.effective()
        for name, preset in PRESETS.items():
            if all(getattr(config, key) == value for key, value in preset.items()):
                return name.title()
        return "Custom"

    def _delivery_label(self) -> str:
        config = self.runtime.effective()
        if config.alerts_paused:
            return "⏸ Alerts paused"
        if config.dry_run:
            return "🟡 Dry run · alerts off"
        if not config.telegram_bot_token.get_secret_value() or not config.telegram_chat_id:
            return "⚠️ Alert destination missing"
        return "🟢 Live alerts enabled"

    def _setting_button(self, key: str) -> dict[str, str]:
        label = SHORT_LABELS.get(key, LABELS[key])
        value = setting_value(key, getattr(self.runtime.effective(), key))
        return self._button(f"✏️ {label} · {value}", f"n:{key}")

    def _toggle_button(self, key: str) -> dict[str, str]:
        value = getattr(self.runtime.effective(), key)
        icon = "⚪"
        if value:
            icon = {"alerts_paused": "⏸", "dry_run": "🟡"}.get(key, "🟢")
        return self._button(
            f"{icon} {SHORT_LABELS.get(key, LABELS[key])} · {setting_value(key, value)}", f"t:{key}"
        )

    async def _menu(self, name: str) -> None:
        config = self.runtime.effective()
        rows: list[list[dict[str, str]]] = []
        note = ""
        if name in {"home", "settings"}:
            title = "📡 Revival Radar"
            body = (
                f"{self._delivery_label()}\n"
                f"{html.escape(setting_value('enabled_chains', config.enabled_chains))}\n\n"
                f"Strategy  <b>{self._strategy_name()}</b> · "
                f"Score <b>{config.alert_score_threshold}+</b>\n"
                "Scan target  "
                f"<b>{setting_value('scan_interval_seconds', config.scan_interval_seconds)}</b>\n\n"
                f"{self._schedule_text()}\n\nChoose an action below."
            )
            rows = [
                [self._button("📊 Status", "status"), self._button("🩺 Health", "health")],
                [self._button("🎯 Strategy presets", "m:presets")],
                [
                    self._button("🔍 Near misses", "health:near"),
                    self._button("🔄 Scan now", "act:scan"),
                ],
                [self._button("⚙️ Settings", "m:settings_hub")],
                [
                    self._button(
                        "▶️ Resume alerts" if config.alerts_paused else "⏸ Pause alerts",
                        "t:alerts_paused",
                    )
                ],
            ]
            rows.append([self._button("🧪 Test alert", "act:test")])
        elif name == "settings_hub":
            title, body = "⚙️ Settings", "Choose a category. All changes require confirmation."
            rows = [
                [self._button(label, f"m:{target}")]
                for label, target in (
                    ("🌐 Chains", "chains"),
                    ("🎯 Thresholds", "filters"),
                    ("📊 Structure", "structure"),
                    ("⚡ Activity", "activity"),
                    ("🔔 Alerts", "alerts"),
                    ("🔎 Discovery & timing", "discovery"),
                )
            ]
            rows.extend(
                [
                    [self._button("⚙️ Advanced configuration", "m:advanced")],
                    [self._button("🛡 Asset exclusions", "m:assets")],
                    [self._button("↩️ Reset custom settings", "reset")],
                ]
            )
        elif name == "presets":
            title = "🎯 Strategy presets"
            body = (
                f"Current strategy  <b>{self._strategy_name()}</b>\n\n"
                "🛡 <b>Strict</b> · Selective filters · score 75+\n"
                "⚖️ <b>Balanced</b> · Moderate filters · score 65+\n"
                "🔭 <b>Broad</b> · Wider filters · score 60+\n\n"
                "Choose a preset to review its changes before saving.\n"
                "A preset replaces your custom values for the settings it controls."
            )
            rows = [
                [self._button(f"{icon} {preset.title()}", f"p:{preset}")]
                for preset, icon in (("strict", "🛡"), ("balanced", "⚖️"), ("broad", "🔭"))
            ]
            rows.append([self._button("⚙️ Advanced configuration", "m:advanced")])
        elif name in {"advanced", "advanced_1", "advanced_2"}:
            page = 0 if name == "advanced" else int(name.rsplit("_", 1)[1])
            group, fields = ADVANCED_PAGES[page]
            title = "⚙️ Advanced configuration"
            body = (
                f"<b>{html.escape(group)}</b> · {page + 1}/{len(ADVANCED_PAGES)}\n"
                "Tap a value to edit it. You review every change before saving."
            )
            rows = [[self._setting_button(key)] for key in fields]
            navigation = []
            if page > 0:
                target = "advanced" if page == 1 else "advanced_1"
                navigation.append(self._button("‹ Previous", f"m:{target}"))
            if page < len(ADVANCED_PAGES) - 1:
                navigation.append(self._button("Next ›", f"m:advanced_{page + 1}"))
            rows.append(navigation)
            if page == 1:
                rows.append([self._button("📈 More chart filters", "m:structure")])
            if page == 2:
                rows.append([self._button("↩️ Reset custom settings", "reset")])
            note = "Saved edits persist across restarts."
        elif name in GROUPS:
            titles = {
                "filters": "💰 Token filters",
                "structure": "📊 Price structure",
                "alerts": "🔔 Alerts & reports",
                "activity": "⚡ Returning activity",
                "discovery": "🔎 Discovery & timing",
            }
            title = titles[name]
            body = "Tap a value to edit it."
            rows = [[self._setting_button(key)] for key, _ in GROUPS[name]]
            if name == "alerts":
                body = (
                    f"{self._delivery_label()}\n"
                    f"Report timezone  {html.escape(config.report_timezone)}\n\n"
                    "Tap a value or switch to review a change."
                )
                rows.extend(
                    [
                        [self._toggle_button(key)]
                        for key in ("alerts_paused", "dry_run", "daily_summary_enabled")
                    ]
                )
            if name == "discovery":
                body = (
                    "Scan interval is a target; a scan can take longer.\n"
                    "Request spacing controls how quickly API calls are made.\n\n"
                    f"Ranking window  <b>{html.escape(config.discovery_interval)}</b>"
                )
                for intervals in (("1m", "5m", "1h"), ("6h", "24h")):
                    rows.append(
                        [
                            self._button(
                                f"{'✓ ' if interval == config.discovery_interval else ''}"
                                f"{interval}",
                                f"d:{interval}",
                            )
                            for interval in intervals
                        ]
                    )
        elif name == "chains":
            title = "🌐 Chains"
            body = (
                "✓ Enabled · ○ Disabled\nTap a chain to review a change. Keep at least one enabled."
            )
            rows = [
                [
                    self._button(
                        f"{'✓' if key in config.chains else '○'} {chain.display_name}", f"c:{key}"
                    )
                ]
                for key, chain in CHAINS.items()
            ]
        elif name == "assets":
            title = "🛡 Asset exclusions"
            body = (
                "On means these assets are filtered out.\n"
                "Classification uses available metadata; unknown assets may still appear."
            )
            rows = [
                [
                    self._button(
                        f"{'🟢' if getattr(config, key) else '⚪'} "
                        f"{SHORT_LABELS.get(key, LABELS[key])} · "
                        f"{setting_value(key, getattr(config, key))}",
                        f"t:{key}",
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
        self._current_menu = name
        if name not in {"home", "settings"}:
            rows.append([self._button("🏠 Main menu", "m:home")])
        text = f"<b>{title}</b>\n\n{body}"
        if note:
            text += f"\n\n<i>{note}</i>"
        await self._send(text, rows, edit=True)

    async def _preview(
        self, changes: dict[str, Any], title: str = "Review settings change", reset: bool = False
    ) -> None:
        self._proposal = None
        before = self.runtime.public_values()
        candidate = self.runtime.base if reset else self.runtime.preview(changes)
        after = candidate.model_dump(mode="json")
        keys = before.keys() if reset else changes.keys()
        lines = [f"📝 <b>{html.escape(title)}</b>"]
        for key in sorted(keys):
            if before[key] != after[key]:
                label = SHORT_LABELS.get(key, LABELS.get(key, key.replace("_", " ").title()))
                lines.append(
                    f"\n{html.escape(label)}\n"
                    f"{html.escape(setting_value(key, before[key]))} → "
                    f"<b>{html.escape(setting_value(key, after[key]))}</b>"
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
        if "scan_interval_seconds" in changes:
            lines.append("Scan interval is a target. A full scan may take longer.")
        if changes.get("alerts_paused") is True or changes.get("dry_run") is True:
            lines.append("Alert suppression takes effect immediately, including the current scan.")
        lines.append("\nConfirm within 10 minutes. Strategy changes apply at the next scan.")
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
                        self._button("✅ Confirm", f"yes:{proposal.nonce}"),
                        self._button("✖ Cancel", "cancel"),
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
        if proposal.action:
            if proposal.action == "scan":
                answer = (
                    self.scan_request()
                    if self.scan_request
                    else "Manual scans are unavailable in this process."
                )
                await self._send(html.escape(answer), [[self._button("🏠 Main menu", "m:home")]])
            else:
                from revival_radar.analysis.market_structure import analyze_structure
                from revival_radar.analysis.scoring import score_token
                from revival_radar.clients.telegram import format_alert
                from revival_radar.demo import DemoSource
                from revival_radar.models.token import Candle, TokenSnapshot

                demo = DemoSource().data[0]
                token = TokenSnapshot(**demo["token"])
                config = self.runtime.effective()
                signal = score_token(
                    token,
                    [TokenSnapshot(**h) for h in demo["history"]],
                    analyze_structure([Candle(**c) for c in demo["candles"]], config),
                    config,
                )
                await self._send(
                    "🧪 <b>TEST · Synthetic example, not a live signal</b>\n\n"
                    + format_alert(token, signal)
                )
            return
        if proposal.reset:
            self.runtime.reset(expected_revision=proposal.revision)
        else:
            self.runtime.apply(proposal.changes, expected_revision=proposal.revision)
        self._input = None
        immediate = (
            proposal.changes.get("alerts_paused") is True or proposal.changes.get("dry_run") is True
        )
        timing = (
            "Alert suppression is active now. A send already in progress may finish."
            if immediate
            else "Your changes apply at the next scan."
        )
        await self._send(
            f"✅ <b>Settings saved</b>\n\n{timing}\nYour settings are kept across restarts.",
            [
                [self._button("✏️ Continue editing", f"m:{self._current_menu}")],
                [self._button("🏠 Main menu", "m:home")],
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
            value: int | float = parse_setting_number(key, text)
            if not math.isfinite(value):
                raise ValueError
            if type(getattr(self.runtime.effective(), key)) is int:
                if not value.is_integer():
                    raise ValueError
                value = int(value)
            await self._preview({key: value})
            self._input = None
        except (ValueError, SettingsChangeError):
            await self._send(
                "⚠️ <b>Invalid number or conflicting ranges</b>\n\n"
                f"{html.escape(setting_hint(key))}\n"
                "Minimum values must not exceed their maximum. Nothing was changed.\n"
                "Try again, or use /cancel."
            )

    async def _status(self) -> None:
        config = self.runtime.effective()
        active = self.runtime.active_revision
        application = (
            "Waiting for the first scan"
            if active is None
            else (
                "Latest settings loaded by the scanner"
                if active == self.runtime.revision
                else "Changes waiting for the next scan"
            )
        )
        summary = (
            f"{config.daily_summary_hour:02}:00 · {html.escape(config.report_timezone)}"
            if config.daily_summary_enabled
            else "Off"
        )
        await self._send(
            "📊 <b>Radar status</b>\n\n"
            f"{self._delivery_label()}\n"
            f"{html.escape(setting_value('enabled_chains', config.enabled_chains))}\n\n"
            "<b>Strategy</b>\n"
            f"{self._strategy_name()} · Alert score <b>{config.alert_score_threshold}+</b>\n"
            f"{application}\n\n"
            "<b>Timing</b>\n"
            f"Scan target  {setting_value('scan_interval_seconds', config.scan_interval_seconds)}\n"
            "API spacing  "
            f"{setting_value('request_spacing_seconds', config.request_spacing_seconds)}\n"
            f"Daily summary  {summary}\n"
            f"{self._schedule_text()}\n\n"
            f"Controls  {html.escape(self.status)}\n"
            "Health shows actual scan times, filtering and delivery results.",
            [
                [self._button("🩺 View health", "health"), self._button("🔄 Refresh", "status")],
                [self._button("🏠 Main menu", "m:home")],
            ],
            edit=True,
        )

    async def _health(self, view: str = "overview") -> None:
        if self.health_provider is None:
            await self._send("Scanner diagnostics are not available yet.")
            return
        try:
            result = self.health_provider()
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, Mapping):
                from revival_radar.telegram_views import health_page

                text = health_page(
                    dict(result)
                    | {
                        "timezone": self.runtime.effective().report_timezone,
                    },
                    view,
                )
            else:
                text = html.escape(str(result))
        except Exception:
            # Internal diagnostics can contain upstream failures: never echo their exceptions.
            await self._send("Scanner diagnostics could not be loaded. Try again later.")
            return
        rows = [
            [
                self._button("⚡ Performance", "health:performance"),
                self._button("🔻 Funnel", "health:funnel"),
            ],
            [
                self._button("🧪 Data quality", "health:quality"),
                self._button("🎯 Near misses", "health:near"),
            ],
            [self._button("⚙️ Scan configuration", "health:config")],
            [self._button("📋 Full diagnostics", "health:full")],
            [self._button("🔄 Overview", "health"), self._button("🏠 Main menu", "m:home")],
        ]
        if view == "near" and isinstance(result, Mapping):
            for item in result.get("best_candidates", [])[:3]:
                if item.get("id"):
                    rows.insert(
                        0,
                        [
                            {
                                "text": f"🔎 Inspect {str(item['symbol'])[:16]} snapshot",
                                "callback_data": f"e:{item['id']}:full",
                            }
                        ],
                    )
        await self._send(text, rows, edit=True)

    def _schedule_text(self) -> str:
        schedule = self.schedule_provider() if self.schedule_provider else {}
        if not schedule:
            return "Scan timing unavailable here; see Health."
        if schedule.get("running"):
            return "🔄 Scan in progress"
        last = schedule.get("last_finished")
        ago = f"{max(0, int(time.time() - last))}s ago" if last else "not completed yet"
        due = schedule.get("next_due")
        nxt = f"~{max(0, int(due - time.time()))}s" if due else "pending"
        return f"Last scan {ago} · Next scan {nxt}"

    async def _action_preview(self, action: str) -> None:
        self._input = None
        self._proposal = Proposal(
            secrets.token_hex(6), self.runtime.revision, {}, time.monotonic() + 600, action=action
        )
        text = (
            "🔄 <b>Request a scan now?</b>\n"
            "The existing rate limits still apply. Scans never overlap."
            if action == "scan"
            else "🧪 <b>Send a test alert here?</b>\n"
            "Uses synthetic data and makes no GMGN requests."
        )
        await self._send(
            text,
            [
                [
                    self._button("✅ Confirm", f"yes:{self._proposal.nonce}"),
                    self._button("✖ Cancel", "cancel"),
                ]
            ],
        )

    async def _detail(self, data: str) -> None:
        parts = data.split(":")
        if (
            len(parts) != 3
            or parts[0] not in {"a", "e"}
            or len(parts[1]) > 18
            or not parts[1].isascii()
            or not parts[1].isdigit()
            or parts[2] not in {"why", "full"}
        ):
            return
        if not self.detail_provider:
            await self._send("Details are unavailable in this process.")
            return
        try:
            detail = self.detail_provider(parts[0], int(parts[1]))
            if inspect.isawaitable(detail):
                detail = await detail
        except Exception:
            await self._send("Saved details could not be loaded. Try again later.")
            return
        if not detail:
            await self._send(
                "This saved observation is unavailable or predates detailed recording."
            )
            return
        from revival_radar.clients.telegram import format_full_alert, format_why
        from revival_radar.models.signal import RevivalResult
        from revival_radar.models.token import TokenSnapshot
        from revival_radar.telegram_views import config_footer

        try:
            token = TokenSnapshot(**detail["token"])
            signal = RevivalResult(**detail["signal"])
        except (KeyError, TypeError, ValueError):
            await self._send("This saved observation is incomplete and cannot be displayed.")
            return
        context = detail.get("configuration", {})
        text = (
            format_why(token, signal, context)
            if parts[2] == "why"
            else format_full_alert(token, signal) + "\n" + config_footer(context)
        )
        await self._send(text, [[self._button("🏠 Main menu", "m:home")]])

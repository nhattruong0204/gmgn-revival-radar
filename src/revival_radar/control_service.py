"""Transport-independent controls for the one scanner-owned runtime instance."""

from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

import httpx

from revival_radar.clients.telegram import TelegramClient, format_alert
from revival_radar.config_context import configuration_context
from revival_radar.runtime_settings import PRESETS, RuntimeSettings, SettingsChangeError

CONTROL_ACTIONS = frozenset({"settings", "reset", "pause", "resume", "scan", "test"})
SETTINGS_ACTIONS = frozenset({"settings", "reset", "pause", "resume"})


class SharedController:
    def __init__(
        self,
        runtime: RuntimeSettings,
        http: httpx.AsyncClient,
        scan_request: Callable[[], str] | None = None,
        schedule_provider: Callable[[], dict] | None = None,
    ):
        self.runtime, self.http = runtime, http
        self.scan_request, self.schedule_provider = scan_request, schedule_provider

    def check_revision(self, expected_revision: int | None) -> None:
        self.runtime._check_revision(expected_revision)

    def preview(
        self,
        action: str,
        changes: dict[str, Any] | None = None,
        preset: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self.check_revision(expected_revision)
        if action not in CONTROL_ACTIONS:
            raise SettingsChangeError("This control action is unavailable.")
        if changes is not None and not isinstance(changes, dict):
            raise SettingsChangeError("Settings changes must be an object.")
        if action != "settings" and (changes or preset is not None):
            raise SettingsChangeError("This action does not accept settings changes.")
        proposed = dict(changes or {})
        if preset is not None:
            if not isinstance(preset, str) or preset not in PRESETS:
                raise SettingsChangeError("This preset is unavailable.")
            proposed = PRESETS[preset] | proposed
        before = self.runtime.public_values()
        if action == "settings":
            if not proposed:
                raise SettingsChangeError("Choose at least one setting to preview.")
            candidate = self.runtime.preview(proposed)
        elif action == "reset":
            candidate = self.runtime.preview_reset()
        elif action in {"pause", "resume"}:
            proposed = {"alerts_paused": action == "pause"}
            candidate = self.runtime.preview(proposed)
        else:
            candidate = self.runtime.effective()
            if action == "test":
                self._private_owner()
        values = candidate.model_dump(mode="json")
        normalized = {key: values[key] for key in (before if action == "reset" else proposed)}
        after = {key: values[key] for key in before}
        return {
            "action": action,
            "changes": normalized,
            "before": before,
            "after": after,
            "revision": self.runtime.revision,
        }

    async def execute(
        self,
        action: str,
        changes: dict[str, Any] | None = None,
        *,
        expected_revision: int | None = None,
        command_id: str | None = None,
        test_sender: Callable[[str], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        # All state-changing transports use this validation and the same runtime.
        # Pause/resume previews already contain their canonical setting change.
        supplied = None if action in {"reset", "pause", "resume"} else changes
        preview = self.preview(action, supplied, expected_revision=expected_revision)
        normalized = {"action": action, "changes": preview["changes"]}
        receipt = {"command_id": command_id, "command_payload": normalized}
        if action == "reset":
            self.runtime.reset(expected_revision, **receipt)
            message = "Settings reset. Strategy changes apply at the next scan."
        elif action in {"settings", "pause", "resume"}:
            self.runtime.apply(preview["changes"], expected_revision, **receipt)
            message = (
                "Alert suppression is active immediately. A send in progress may finish."
                if preview["changes"].get("alerts_paused") is True
                or preview["changes"].get("dry_run") is True
                else "Settings saved. Strategy changes apply at the next scan."
            )
        elif action == "scan":
            if self.scan_request is None:
                raise SettingsChangeError("Manual scans are unavailable in this process.")
            message = self.scan_request()
        else:
            message = self.test_message()
            if test_sender is not None:
                await test_sender(message)
                delivery_status = "sent"
            else:
                owner = self._private_owner()
                config = self.runtime.effective().model_copy(
                    update={"dry_run": False, "telegram_chat_id": str(owner)}
                )
                delivery = await TelegramClient(config, self.http).send_text(message)
                delivery_status = delivery.status
            message = {
                "sent": "Synthetic test sent to the owner's private Telegram.",
                "failed": "Telegram rejected the synthetic test.",
                "unknown": (
                    "Synthetic test delivery is uncertain. It will not be resent automatically."
                ),
            }.get(delivery_status, "Synthetic test delivery could not be confirmed.")
            return self._result(action, message) | {
                "delivery_status": delivery_status,
            }
        result = self._result(action, message)
        if action == "scan":
            result["schedule"] = self.schedule_provider() if self.schedule_provider else {}
        return result

    def _result(self, action: str, message: str) -> dict[str, Any]:
        return {
            "action": action,
            "revision": self.runtime.revision,
            "active_revision": self.runtime.active_revision,
            "message": message,
        }

    def _private_owner(self) -> int:
        config = self.runtime.effective()
        chat_id = config.telegram_chat_id.strip()
        owner = config.telegram_owner_id
        if owner <= 0 and chat_id.isascii() and chat_id.isdecimal():
            owner = int(chat_id)
        if owner <= 0 or not config.telegram_bot_token.get_secret_value():
            raise SettingsChangeError("Configure a private Telegram owner before sending a test.")
        return owner

    def test_message(self) -> str:
        from revival_radar.analysis.market_structure import analyze_structure
        from revival_radar.analysis.scoring import score_token
        from revival_radar.demo import DemoSource
        from revival_radar.models.token import Candle, TokenSnapshot

        demo = DemoSource().data[0]
        token = TokenSnapshot(**demo["token"])
        config = self.runtime.effective()
        signal = score_token(
            token,
            [TokenSnapshot(**item) for item in demo["history"]],
            analyze_structure([Candle(**item) for item in demo["candles"]], config),
            config,
        )
        return "🧪 <b>TEST · Synthetic example, not a live signal</b>\n\n" + format_alert(
            token, signal
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "settings": self.runtime.public_values(),
            "revision": self.runtime.revision,
            "active_revision": self.runtime.active_revision,
            "presets": deepcopy(PRESETS),
            "preset": configuration_context(self.runtime.effective())["preset"],
        }

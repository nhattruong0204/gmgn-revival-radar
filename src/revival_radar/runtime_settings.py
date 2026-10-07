"""Validated, nonsecret runtime overrides kept beside the scanner database."""

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import ValidationError

from revival_radar.config import Settings

ALLOWED_SETTINGS = frozenset(
    {
        "enabled_chains",
        "scan_interval_seconds",
        "request_spacing_seconds",
        "token_min_age_hours",
        "min_market_cap",
        "max_market_cap",
        "min_liquidity",
        "min_ath_drawdown",
        "max_ath_drawdown",
        "min_holders",
        "min_volume_1h",
        "max_price_change_5m",
        "max_price_change_1h",
        "alert_score_threshold",
        "alert_cooldown_hours",
        "realert_score_increase",
        "dry_run",
        "discovery_interval",
        "discovery_limit",
        "watchlist_hours",
        "watchlist_limit",
        "history_observations",
        "minimum_history_observations",
        "history_max_gap_seconds",
        "acceleration_cap",
        "volume_acceleration_threshold",
        "tx_acceleration_threshold",
        "hot_rank_improvement",
        "holder_retention_ratio",
        "base_min_hours",
        "base_sufficient_hours",
        "base_max_range_ratio",
        "major_low_tolerance",
        "breakout_margin",
        "retest_tolerance",
        "kline_lookback_hours",
        "top10_max_ratio",
        "insider_drop_threshold",
        "dev_drop_threshold",
        "liquidity_drop_threshold",
        "sniper_max_ratio",
        "bundler_max_ratio",
        "weights",
        "alerts_paused",
        "exclude_tokenized_stocks",
        "exclude_stablecoins",
        "exclude_wrapped_assets",
        "daily_summary_enabled",
        "daily_summary_hour",
        "report_timezone",
    }
)

STRICT_PRESET = {
    "token_min_age_hours": 48,
    "min_market_cap": 100_000,
    "max_market_cap": 10_000_000,
    "min_liquidity": 30_000,
    "min_holders": 300,
    "min_ath_drawdown": 0.65,
    "max_ath_drawdown": 0.95,
    "min_volume_1h": 50_000,
    "max_price_change_5m": 30,
    "max_price_change_1h": 75,
    "base_min_hours": 24,
    "base_sufficient_hours": 72,
    "volume_acceleration_threshold": 1.5,
    "tx_acceleration_threshold": 1.5,
    "alert_score_threshold": 75,
    "alert_cooldown_hours": 6,
    "realert_score_increase": 10,
    "discovery_limit": 20,
    "watchlist_limit": 100,
    "watchlist_hours": 24,
}
BALANCED_PRESET = STRICT_PRESET | {
    "token_min_age_hours": 24,
    "min_market_cap": 50_000,
    "max_market_cap": 15_000_000,
    "min_liquidity": 15_000,
    "min_holders": 100,
    "min_ath_drawdown": 0.60,
    "min_volume_1h": 20_000,
    "max_price_change_5m": 40,
    "max_price_change_1h": 100,
    "base_min_hours": 12,
    "base_sufficient_hours": 48,
    "volume_acceleration_threshold": 1.4,
    "tx_acceleration_threshold": 1.4,
    "alert_score_threshold": 65,
}
BROAD_PRESET = BALANCED_PRESET | {
    "alert_score_threshold": 60,
    "token_min_age_hours": 12,
    "min_liquidity": 10_000,
    "min_holders": 75,
    "min_volume_1h": 10_000,
    "base_min_hours": 8,
}
PRESETS = {"strict": STRICT_PRESET, "balanced": BALANCED_PRESET, "broad": BROAD_PRESET}


class SettingsChangeError(ValueError):
    """A safe error that never includes rejected values or credentials."""


class StaleRevisionError(SettingsChangeError):
    pass


class RuntimeSettings:
    def __init__(self, base: Settings, path: Path | None = None):
        self.base = base.model_copy(deep=True)
        self.path = (
            Path(path) if path is not None else base.database_path.parent / "radar-controls.json"
        )
        self._lock = RLock()
        self._overrides: dict[str, Any] = {}
        self._revision = 0
        self._offset = 0
        if self.path.exists():
            self._load()
        self.active_revision: int | None = None

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def offset(self) -> int:
        return self._offset

    def _validate(self, overrides: dict[str, Any]) -> Settings:
        if not isinstance(overrides, dict) or set(overrides) - ALLOWED_SETTINGS:
            raise SettingsChangeError("That setting cannot be changed through Telegram.")
        if set(overrides) - Settings.model_fields.keys():
            raise SettingsChangeError("This setting is unavailable in this application version.")
        try:
            # Python-mode dumps preserve SecretStr. JSON dumps would replace real secrets.
            effective = Settings(_env_file=None, **(self.base.model_dump() | overrides))
        except (ValidationError, ValueError, TypeError):
            raise SettingsChangeError(
                "Invalid setting value or conflicting ranges. No settings were changed."
            ) from None
        if (
            "dry_run" in overrides
            and not effective.dry_run
            and (
                not effective.telegram_bot_token.get_secret_value()
                or not effective.telegram_chat_id
            )
        ):
            raise SettingsChangeError("Configure Telegram credentials before enabling live alerts.")
        return effective

    def effective(self) -> Settings:
        with self._lock:
            return self._validate(self._overrides)

    def public_values(self) -> dict[str, Any]:
        values = self.effective().model_dump(mode="json")
        return {key: values[key] for key in sorted(ALLOWED_SETTINGS) if key in values}

    def preview(self, changes: dict[str, Any]) -> Settings:
        with self._lock:
            if not isinstance(changes, dict) or set(changes) - ALLOWED_SETTINGS:
                raise SettingsChangeError("That setting cannot be changed through Telegram.")
            return self._validate(self._overrides | changes)

    def _check_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self._revision:
            raise StaleRevisionError("Settings changed. Open /menu and review a new preview.")

    def apply(self, changes: dict[str, Any], expected_revision: int | None = None) -> Settings:
        with self._lock:
            self._check_revision(expected_revision)
            effective = self.preview(changes)
            normalized = effective.model_dump(mode="json")
            overrides = {key: normalized[key] for key in self._overrides.keys() | changes.keys()}
            self._persist(overrides, self._revision + 1, self._offset)
            self._overrides, self._revision = overrides, self._revision + 1
            return effective

    def reset(self, expected_revision: int | None = None) -> Settings:
        with self._lock:
            self._check_revision(expected_revision)
            effective = self._validate({})
            self._persist({}, self._revision + 1, self._offset)
            self._overrides, self._revision = {}, self._revision + 1
            return effective

    def set_offset(self, offset: int) -> None:
        with self._lock:
            if type(offset) is not int or offset < 0:
                raise SettingsChangeError("Invalid Telegram update offset.")
            if offset > self._offset:
                self._persist(self._overrides, self._revision, offset)
                self._offset = offset

    def _load(self) -> None:
        try:
            if self.path.stat().st_size > 65_536:
                raise ValueError
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or set(data) != {"revision", "offset", "overrides"}:
                raise ValueError
            revision, offset = data["revision"], data["offset"]
            if any(type(value) is not int or value < 0 for value in (revision, offset)):
                raise ValueError
            effective = self._validate(data["overrides"])
            normalized = effective.model_dump(mode="json")
            self._overrides = {key: normalized[key] for key in data["overrides"]}
            self._revision, self._offset = revision, offset
            # Repair overly permissive modes from copies/restores without rewriting values.
            self.path.chmod(0o600)
        except (OSError, ValueError, TypeError, KeyError):
            raise SettingsChangeError(
                "Runtime settings file could not be loaded; repair it before starting controls."
            ) from None

    def _persist(self, overrides: dict[str, Any], revision: int, offset: int) -> None:
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".radar-controls-", dir=self.path.parent)
            with os.fdopen(fd, "w") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(
                    {"revision": revision, "offset": offset, "overrides": overrides},
                    output,
                    allow_nan=False,
                    sort_keys=True,
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
            try:
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                # The rename has committed. Some filesystems do not support directory
                # fsync; reporting failure now would disagree with the persisted state.
                pass
        except (OSError, ValueError, TypeError):
            raise SettingsChangeError("Runtime settings could not be saved.") from None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    os.unlink(temporary)

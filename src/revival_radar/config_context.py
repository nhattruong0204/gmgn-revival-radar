"""Nonsecret immutable configuration context attached to historical observations."""

from revival_radar.config import Settings
from revival_radar.runtime_settings import ALLOWED_SETTINGS, PRESETS


def configuration_context(config: Settings, revision: int = 0, overrides: int = 0) -> dict:
    preset = next(
        (
            name.title()
            for name, values in PRESETS.items()
            if all(getattr(config, key) == value for key, value in values.items())
        ),
        "Custom",
    )
    values = config.model_dump(mode="json")
    return {
        "preset": preset,
        "revision": revision,
        "overrides": overrides,
        "settings": {key: values[key] for key in sorted(ALLOWED_SETTINGS)},
    }

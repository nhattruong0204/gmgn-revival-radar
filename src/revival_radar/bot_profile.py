"""Explicit official Bot API profile setup; never run automatically at startup."""

import json
from pathlib import Path

import httpx

from revival_radar.config import Settings


async def configure_profile(
    config: Settings, http: httpx.AsyncClient, photo: Path | None = None
) -> dict:
    token = config.telegram_bot_token.get_secret_value()
    if not token:
        return {"status": "missing_telegram_credentials"}
    operations = {
        "setMyName": {"name": "Revival Radar"},
        "setMyDescription": {
            "description": "Read-only revival monitoring: surviving liquidity, "
            "returning activity and price structure. "
            "Private controls with /menu. Heuristic signals, no automated trades."
        },
        "setMyShortDescription": {
            "short_description": "♻️ Revival Radar · setup, activity and structure. "
            "Read-only alerts. No trading."
        },
        "setMyCommands": {
            "commands": [
                {"command": "start", "description": "Open Revival Radar"},
                {"command": "menu", "description": "Strategy and settings"},
                {"command": "status", "description": "Scanner status and timing"},
                {"command": "health", "description": "Performance, coverage and near misses"},
                {"command": "cancel", "description": "Cancel a pending edit"},
            ]
        },
    }
    results = {}

    async def record(method, **kwargs):
        try:
            response = await http.post(
                f"https://api.telegram.org/bot{token}/{method}", timeout=30, **kwargs
            )
            payload = response.json()
            results[method] = (
                "updated"
                if response.is_success and isinstance(payload, dict) and payload.get("ok") is True
                else f"failed_http_{response.status_code}"
            )
        except (httpx.HTTPError, ValueError):
            results[method] = "connection_or_response_error"

    for method, payload in operations.items():
        await record(method, json=payload)
        if results[method] != "updated":
            break
    if photo and all(value == "updated" for value in results.values()):
        if not photo.is_file():
            results["setMyProfilePhoto"] = "photo_file_missing"
        else:
            with photo.open("rb") as image:
                await record(
                    "setMyProfilePhoto",
                    data={"photo": json.dumps({"type": "static", "photo": "attach://avatar"})},
                    files={"avatar": (photo.name, image, "image/png")},
                )
    return results

"""Nonsecret, deterministic matching of an evaluated case to alert-time facts."""

import hashlib
import json


def research_context(configuration):
    context = configuration or {}
    preset, revision = context.get("preset"), context.get("revision")
    return {
        "preset": preset
        if isinstance(preset, str) and preset in {"Strict", "Balanced", "Broad", "Custom"}
        else None,
        "revision": revision if type(revision) is int and revision >= 0 else None,
    }


def research_digest(token, result, configuration):
    value = {
        "token": token.model_dump(mode="json"),
        "signal": result.model_dump(mode="json"),
        "configuration": research_context(configuration),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

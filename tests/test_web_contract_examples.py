import json
from pathlib import Path

from revival_radar.web.admin import CommandRequest
from revival_radar.web.models import CallPerformance, RadarPage


def test_tracked_synthetic_examples_follow_current_contracts():
    examples = Path(__file__).resolve().parents[1] / "docs" / "examples"
    public = RadarPage.model_validate_json((examples / "website-public.json").read_text())
    assert public.items[0].token is None and public.items[0].signal is None
    assert public.items[0].blockers and public.items[0].liquidity is None
    owner = json.loads((examples / "website-admin.json").read_text())
    for key in ("preview_request", "confirm_request"):
        CommandRequest.model_validate(owner[key])
    assert "owner" not in owner["preview_result"]
    assert "payload" not in owner["preview_result"]


def test_call_performance_contract_keeps_unknowns_null_with_reason():
    legacy = CallPerformance(change_reason="no_call_mc", observations=0)
    dump = legacy.model_dump()
    assert set(dump) == {
        "call_market_cap",
        "call_price",
        "latest_market_cap",
        "latest_price",
        "latest_observed_at",
        "freshness",
        "change_since_call_pct",
        "change_reason",
        "mfe_pct",
        "mae_pct",
        "observations",
        "max_gap_seconds",
        "elapsed_seconds",
        "token_call_index",
        "token_call_count",
    }
    assert dump["change_since_call_pct"] is None and dump["freshness"] == "none"

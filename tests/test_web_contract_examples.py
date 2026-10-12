import json
from pathlib import Path

from revival_radar.web.admin import CommandRequest
from revival_radar.web.models import RadarPage


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

import importlib.util
import stat
from pathlib import Path

import pytest

from revival_radar.web.auth import verify_password

spec = importlib.util.spec_from_file_location(
    "configure_web_owner", Path(__file__).parents[1] / "scripts/configure_web_owner.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_owner_setup_hash_matches_server_and_preserves_other_settings(tmp_path):
    path = tmp_path / ".env.web"
    path.write_text("WEB_ADMIN_ENABLED=false\nWEB_ADMIN_PASSWORD_HASH=old\nWEB_CACHE_SECONDS=10\n")
    setup.configure(path, "private test password")
    content = path.read_text()
    encoded = next(
        line.split("=", 1)[1]
        for line in content.splitlines()
        if line.startswith("WEB_ADMIN_PASSWORD_HASH=")
    )
    assert verify_password("private test password", encoded)
    assert "private test password" not in content
    assert "WEB_ADMIN_ENABLED=true" in content
    assert "WEB_CACHE_SECONDS=10" in content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".web-owner-*"))


def test_invalid_owner_password_leaves_configuration_unchanged(tmp_path):
    path = tmp_path / ".env.web"
    path.write_text("WEB_ADMIN_ENABLED=false\n")
    with pytest.raises(ValueError):
        setup.configure(path, "short")
    assert path.read_text() == "WEB_ADMIN_ENABLED=false\n"

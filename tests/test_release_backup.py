import importlib.util
import sqlite3
from pathlib import Path

import pytest

from revival_radar.runtime_settings import RuntimeSettings
from revival_radar.storage.database import SCHEMA_VERSION

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release_backup.py"
spec = importlib.util.spec_from_file_location("release_backup", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
REVISION = "a" * 40


def test_matched_backup_and_staging_preserve_schema_controls_and_private_permissions(
    repo, config, tmp_path
):
    database = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    config.database_path = database
    runtime = RuntimeSettings(config)
    runtime.set_offset(987)
    repo.set_state("fixture:evidence", "preserved")
    bundle, staged = tmp_path / "bundle", tmp_path / "stage"
    release.create(database, runtime.path, bundle, REVISION, True)
    summary = release.stage(bundle, staged, REVISION)
    assert summary["integrity"] == "ok" and summary["schema"] == SCHEMA_VERSION
    with sqlite3.connect(staged / "database.db") as restored:
        assert (
            restored.execute("SELECT value FROM state WHERE key='fixture:evidence'").fetchone()[0]
            == "preserved"
        )
    assert (staged / "radar-controls.json").read_bytes() == runtime.path.read_bytes()
    assert bundle.stat().st_mode & 0o777 == staged.stat().st_mode & 0o777 == 0o700
    for path in bundle.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        release.stage(bundle, staged, REVISION)


def test_backup_requires_stopped_services_and_staging_rejects_wrong_code_or_corruption(
    repo, config, tmp_path
):
    database = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    config.database_path = database
    runtime = RuntimeSettings(config)
    runtime.set_offset(12)
    bundle, staged = tmp_path / "bundle", tmp_path / "stage"
    with pytest.raises(ValueError, match="Stop scanner"):
        release.create(database, runtime.path, bundle, REVISION, False)
    assert not bundle.exists()
    release.create(database, runtime.path, bundle, REVISION, True)
    with pytest.raises(ValueError, match="revision"):
        release.stage(bundle, staged, "b" * 40)
    (bundle / "radar-controls.json").write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        release.stage(bundle, staged, REVISION)
    assert not staged.exists()

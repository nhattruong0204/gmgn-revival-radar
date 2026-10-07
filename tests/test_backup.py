import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_backup_preserves_existing_schema_and_wal(tmp_path):
    path = tmp_path / "original.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA user_version=1")
    db.execute("CREATE TABLE existing_data (value TEXT)")
    db.execute("INSERT INTO existing_data VALUES ('preserved')")
    db.commit()
    script = Path(__file__).resolve().parents[1] / "scripts/backup_database.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=os.environ | {"DATABASE_PATH": str(path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    backups = list(tmp_path.glob("original.before-upgrade-*.db"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    backup = sqlite3.connect(backups[0])
    assert backup.execute("SELECT value FROM existing_data").fetchone()[0] == "preserved"
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    backup.close()
    db.close()

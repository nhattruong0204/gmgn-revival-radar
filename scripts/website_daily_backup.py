"""Create an online database-only snapshot; release bundles remain separate."""

import argparse
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def backup(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise ValueError("Existing source database required")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = destination / f"daily-{stamp}.db"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with (
            sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original,
            sqlite3.connect(target) as copied,
        ):
            original.backup(copied)
            if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    # Only this script's completed snapshots; matched release bundles are untouched.
    for old in sorted(destination.glob("daily-*.db"), reverse=True)[7:]:
        old.unlink()
    print("Online database snapshot verified; seven most recent snapshots retained.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    backup(args.database, args.destination)

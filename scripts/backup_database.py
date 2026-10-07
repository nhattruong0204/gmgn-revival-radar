"""Run inside the existing container before an upgrade; no schema migration or API calls."""

import os
import sqlite3
from datetime import UTC, datetime

from pydantic import ValidationError

from revival_radar.config import Settings


def main() -> None:
    try:
        source = Settings().database_path.resolve()
    except ValidationError:
        raise SystemExit(
            "Invalid application configuration; repair .env before backing up."
        ) from None
    if not source.is_file():
        print("No existing database to back up.")
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = source.with_name(f"{source.stem}.before-upgrade-{stamp}.db")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            backup = sqlite3.connect(destination)
            try:
                original.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Database backup integrity check failed")
            finally:
                backup.close()
        finally:
            original.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    print(f"Database backup created: {destination.name}")


if __name__ == "__main__":
    main()

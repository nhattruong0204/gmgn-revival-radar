"""Create a stopped-service release backup, or stage one without overwriting live files."""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def copy_database(source, destination):
    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(destination)) as target,
    ):
        original.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
    destination.chmod(0o600)


def create(database, controls, destination, revision, services_stopped):
    if not services_stopped:
        raise ValueError("Stop scanner and website before backing up matching state")
    if re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
        raise ValueError("Record the full deployed Git commit SHA")
    if not database.is_file() or not controls.is_file():
        raise ValueError("Database and matching runtime controls must exist")
    destination.mkdir(mode=0o700)  # Never overwrite an existing backup.
    try:
        before = controls.read_bytes()
        copy_database(database, destination / "database.db")
        (destination / "radar-controls.json").write_bytes(before)
        (destination / "radar-controls.json").chmod(0o600)
        if controls.read_bytes() != before:
            raise ValueError("Runtime controls changed during backup; stop services and retry")
        manifest = {
            "format": 1,
            "revision": revision.lower(),
            "sha256": {
                name: digest(destination / name) for name in ("database.db", "radar-controls.json")
            },
        }
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (destination / "manifest.json").chmod(0o600)
    except BaseException:
        shutil.rmtree(destination)
        raise


def stage(bundle, destination, revision):
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("format") != 1 or manifest.get("revision") != revision.lower():
        raise ValueError("Backup format or expected code revision does not match")
    for name in ("database.db", "radar-controls.json"):
        if digest(bundle / name) != manifest["sha256"].get(name):
            raise ValueError("Backup checksum mismatch")
    destination.mkdir(mode=0o700)  # Staging always targets a new directory.
    try:
        copy_database(bundle / "database.db", destination / "database.db")
        shutil.copyfile(bundle / "radar-controls.json", destination / "radar-controls.json")
        (destination / "radar-controls.json").chmod(0o600)
        # SQLite backup preserves schema; never invoke scanner migrations here.
        with closing(sqlite3.connect(destination / "database.db")) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        return {"revision": revision.lower(), "schema": version, "integrity": "ok"}
    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    backup = sub.add_parser("create")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--controls", type=Path, required=True)
    backup.add_argument("--services-stopped", action="store_true")
    restore = sub.add_parser("stage")
    restore.add_argument("--bundle", type=Path, required=True)
    for command in (backup, restore):
        command.add_argument("--destination", type=Path, required=True)
        command.add_argument("--revision", required=True)
    args = parser.parse_args()
    if args.action == "create":
        create(args.database, args.controls, args.destination, args.revision, args.services_stopped)
        print("Matched release backup created; no credentials or runtime values printed.")
    else:
        print(json.dumps(stage(args.bundle, args.destination, args.revision)))

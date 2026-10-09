#!/usr/bin/env python3
"""Analyze an already exported, private SQLite copy without migrations or API calls."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from revival_radar.audit import analyze, correlate_logs, rows  # noqa: E402


def safe_metadata(value):
    """Do not trust a filename saying 'sanitized' to allow credential fields."""
    if isinstance(value, dict):
        return {
            key: safe_metadata(item)
            for key, item in value.items()
            if not re.search(
                r"password|secret|credential|api[_-]?key|bot[_-]?token|private[_-]?key|"
                r"ssh[_-]?key|authorization|telegram[_-]?(chat|owner)[_-]?id",
                key,
                re.I,
            )
        }
    if isinstance(value, list):
        return [safe_metadata(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/audit"))
    parser.add_argument("--source", choices=("production", "demo", "research"), required=True)
    parser.add_argument("--since", type=float)
    parser.add_argument(
        "--until", type=float, help="UTC epoch as-of time; defaults to last stored event"
    )
    parser.add_argument("--timezone", default="Asia/Saigon")
    parser.add_argument(
        "--manifest", type=Path, help="Export manifest linking snapshot and production version"
    )
    args = parser.parse_args()
    os.umask(0o077)
    try:
        manifest = json.loads(args.manifest.read_text()) if args.manifest else None
        if args.source == "production" and manifest is None:
            raise ValueError("Production analysis requires an export manifest")
        if manifest:
            with args.database.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != manifest.get("snapshot_sha256"):
                raise ValueError("Snapshot checksum differs from export manifest")
        until = args.until
        if until is None and manifest:
            until = manifest["backup"]["backup_finished_at"]
        report = analyze(
            args.database,
            since=args.since,
            until=until,
            source=args.source,
            timezone=args.timezone,
        )
        report["export_manifest"] = safe_metadata(manifest)
        if args.manifest:
            import sqlite3

            bundled = {}
            for filename in (
                "production_config_sanitized.json",
                "container_inventory.json",
                "vps_inventory.json",
                "production_logs_summary.json",
            ):
                path = args.manifest.parent / filename
                if path.is_file():
                    bundled[filename] = safe_metadata(json.loads(path.read_text()))
            log_summary = bundled.get("production_logs_summary.json", {})
            db = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                report["log_database_consistency"] = correlate_logs(
                    rows(db, "scan_runs"), log_summary
                )
            finally:
                db.close()
            report["operational_evidence"] = bundled
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
        report["analysis_git_sha"] = revision.stdout.strip() if revision.returncode == 0 else None
        args.output_dir.mkdir(parents=True, exist_ok=True)
        destination = args.output_dir / "latest_audit.json"
        # Avoid following a pre-existing symlink, and write owner-readable private artifacts.
        descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        os.chmod(destination, 0o600)
        print(f"Offline audit written: {destination}")
    except Exception as exc:
        parser.exit(
            1, f"Audit failed ({type(exc).__name__}); source unchanged, no secret values printed.\n"
        )


if __name__ == "__main__":
    main()

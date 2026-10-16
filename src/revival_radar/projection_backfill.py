"""Offline projection backfill for deployments: ``python -m revival_radar.projection_backfill DB``.

Migrates the database (scanner-owned, additive), then runs every derived projection to
completion with no time budget: ``call_performance`` (#90), report-configuration digests
(#101) and radar fields (#100). It then verifies full coverage of the last seven days and
exits non-zero if anything is missing. It needs no network and stops no service by itself:
run it while the scanner and website are stopped, so the first scans and the first public
reads after a deploy never pay for the backfill. Re-running it is a cheap no-op.
"""

import json
import sys
import time
from pathlib import Path

from revival_radar.analysis import radar_projection, report_projection
from revival_radar.storage.database import SCHEMA_VERSION, connect
from revival_radar.storage.repository import Repository

WEEK = 7 * 86400


def run(path: Path, active_days: float = 30.0) -> dict:
    db = connect(path)
    try:
        repo = Repository(db)
        out: dict = {"schema": db.execute("PRAGMA user_version").fetchone()[0]}
        started = time.monotonic()
        calls = 0
        while True:
            stats = repo.refresh_call_performance(time.time(), active_days)
            built = stats.get("built", 0) + stats.get("rebuilt", 0)
            calls += built
            if not built:
                break
        out["call_performance"] = {"built": calls, "seconds": round(time.monotonic() - started, 2)}
        for name, module in (
            ("report_digests", report_projection),
            ("radar_fields", radar_projection),
        ):
            started = time.monotonic()
            written = 0
            while True:
                result = module.refresh(db, budget_seconds=3600)
                written += result.get("written", 0)
                if result.get("complete"):
                    break
            out[name] = {"written": written, "seconds": round(time.monotonic() - started, 2)}
        latest = db.execute("SELECT MAX(timestamp) FROM evaluations").fetchone()[0] or 0
        since = latest - WEEK
        out["coverage"] = {
            "sent_calls_without_projection": db.execute(
                """SELECT COUNT(*) FROM alerts a LEFT JOIN call_performance c ON c.alert_id=a.id
                WHERE a.delivery_status='sent' AND c.alert_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM state p WHERE p.key='origin:alert:' || a.id
                    AND p.value IN ('synthetic','test'))"""
            ).fetchone()[0],
            "evaluations_7d": db.execute(
                "SELECT COUNT(*) FROM evaluations WHERE timestamp>?", (since,)
            ).fetchone()[0],
            "report_digests_missing_7d": db.execute(
                """SELECT COUNT(*) FROM evaluations e LEFT JOIN evaluation_configuration c
                ON c.evaluation_id=e.id AND c.timestamp=e.timestamp
                WHERE e.timestamp>? AND c.evaluation_id IS NULL""",
                (since,),
            ).fetchone()[0],
            "radar_fields_missing_7d": db.execute(
                """SELECT COUNT(*) FROM evaluations e LEFT JOIN evaluation_radar p
                ON p.evaluation_id=e.id AND p.timestamp=e.timestamp AND p.score IS e.score
                AND p.status IS e.status AND p.eligible IS e.eligible
                WHERE e.timestamp>? AND p.evaluation_id IS NULL""",
                (since,),
            ).fetchone()[0],
        }
        coverage = out["coverage"]
        out["complete"] = (
            out["schema"] == SCHEMA_VERSION
            and coverage["sent_calls_without_projection"] == 0
            and coverage["report_digests_missing_7d"] == 0
            and coverage["radar_fields_missing_7d"] == 0
        )
        return out
    finally:
        db.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m revival_radar.projection_backfill DATABASE", file=sys.stderr)
        return 2
    result = run(Path(sys.argv[1]))
    print(json.dumps(result, indent=1))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

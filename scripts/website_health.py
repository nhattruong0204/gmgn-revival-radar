"""Host health probe: HTTPS readiness, observation freshness and bot heartbeat."""

import argparse
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--origin", default="https://onchainflow.app")
    args = parser.parse_args()
    with urllib.request.urlopen(args.origin + "/api/v1/meta", timeout=15) as response:
        meta = json.load(response)
    now = time.time()
    finished = meta.get("last_scan_at")
    if not isinstance(finished, (int, float)) or not 0 <= now - finished <= 1200:
        raise SystemExit("Public observations are stale; investigate the scanner.")
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT value FROM state WHERE key='web:worker'").fetchone()
    worker = json.loads(row[0]) if row else {}
    heartbeat = worker.get("heartbeat_at")
    if (
        not isinstance(heartbeat, (int, float))
        or not 0 <= now - heartbeat <= 30
        or worker.get("enabled") is not True
        or worker.get("stopped_at") is not None
    ):
        raise SystemExit("Bot control worker is unavailable; investigate its process.")
    print("Verified HTTPS readiness, fresh saved observations and live bot control worker.")

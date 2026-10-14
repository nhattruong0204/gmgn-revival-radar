"""Owner/offline: report whether rank market_cap matches price x circulating or x total.

Usage: python scripts/market_cap_basis_report.py path/to/radar.db
Opens the database read-only; makes no provider requests.
"""

import json
import sqlite3
import sys
from pathlib import Path

from revival_radar.analysis.market_cap_basis import SAMPLE_KEY, basis_report


def main(path: str) -> int:
    db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    row = db.execute("SELECT value FROM state WHERE key=?", (SAMPLE_KEY,)).fetchone()
    print(json.dumps(basis_report(row[0] if row else None), indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))

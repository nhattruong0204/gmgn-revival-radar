"""Apply a reviewed provenance attestation to an existing database; no migrations."""

import argparse
import json
import sqlite3
from pathlib import Path

from revival_radar.storage.provenance import classify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "evidence", type=Path, help="Private JSON attestation; see docs/data-provenance.md"
    )
    args = parser.parse_args()
    # mode=rw cannot silently create a new database. No scanner or provider imports.
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=rw", uri=True) as db:
        print(json.dumps(classify(db, args.evidence.read_bytes())))


if __name__ == "__main__":
    main()

"""``python -m revival_radar.research`` command line."""

import argparse
import sys
from pathlib import Path

from revival_radar.research import data, export, report

DESCRIPTION = """\
Reproducible research export and signal-quality report.

Runs ONLY on a database COPY, opened read-only (mode=ro). Make a consistent copy with
SQLite's VACUUM INTO, which never blocks or modifies the live scanner file:

    sqlite3 data/revival_radar.db "VACUUM INTO '/tmp/research-copy.db'"

The tool refuses to run when the path is the configured live database (DATABASE_PATH or
data/revival_radar.db) unless --i-know-this-is-a-copy is given. Times are ISO-8601
(2026-10-07T13:36:00Z) or epoch seconds; --until is also the observation cutoff.
"""


def add_common(parser):
    parser.add_argument("--db", required=True, type=Path, help="path to a database COPY")
    parser.add_argument("--since", required=True, help="window start (ISO-8601 or epoch)")
    parser.add_argument("--until", required=True, help="window end and observation cutoff")
    parser.add_argument(
        "--baseline-since",
        help="start of the cohort baseline and revival-universe sample (default: --since)",
    )
    parser.add_argument(
        "--i-know-this-is-a-copy",
        action="store_true",
        help="allow a path that resolves to the configured live database",
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m revival_radar.research",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="write calls/checkpoints/funnel/cohorts CSV + manifest")
    add_common(exp)
    exp.add_argument("--out", required=True, type=Path, help="output directory")
    exp.add_argument("--no-hash", action="store_true", help="skip hashing the database file")
    rep = sub.add_parser("report", help="write a markdown signal-quality report")
    add_common(rep)
    rep.add_argument("--out", type=Path, help="markdown file (default: print to stdout)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        data.refuse_live(args.db, i_know_this_is_a_copy=args.i_know_this_is_a_copy)
        since, until = data.parse_time(args.since), data.parse_time(args.until)
        if until <= since:
            raise data.ResearchError("--until must be later than --since")
        baseline = data.parse_time(args.baseline_since) if args.baseline_since else since
        window = data.Window(since, until, baseline)
        db = data.open_copy(args.db)
        try:
            if args.command == "export":
                manifest = export.export(db, args.db, window, args.out, hash_input=not args.no_hash)
                print(f"Wrote {len(manifest['row_counts'])} CSV files and manifest.json "
                      f"to {args.out}: {manifest['row_counts']}")  # fmt: skip
            else:
                text = report.build_report(db, window)
                if args.out:
                    args.out.write_text(text, encoding="utf-8")
                    print(f"Wrote {args.out}")
                else:
                    sys.stdout.write(text)
        finally:
            db.close()
    except data.ResearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0

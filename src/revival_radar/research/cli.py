"""``python -m revival_radar.research`` command line."""

import argparse
import json
import sys
from pathlib import Path

from revival_radar.research import data, export, replay, report

DESCRIPTION = """\
Reproducible research export and signal-quality report.

Runs ONLY on a database COPY, opened read-only (mode=ro). Make a consistent copy with
SQLite's VACUUM INTO, which never blocks or modifies the live scanner file:

    sqlite3 data/revival_radar.db "VACUUM INTO '/tmp/research-copy.db'"

The tool refuses to run when the path is the configured live database (DATABASE_PATH or
data/revival_radar.db) unless --i-know-this-is-a-copy is given. Times are ISO-8601
(2026-10-07T13:36:00Z) or epoch seconds; --until is also the observation cutoff.
"""

REPLAY_DESCRIPTION = """\
Point-in-time replay. Re-scores saved evaluation inputs with the unchanged scorer under a
candidate configuration, using only data timestamped at or before each evaluation, then
simulates the publish decision (threshold, alert_min_stage, cooldown, new-evidence re-alert)
and reports the calls with their observed forward outcomes beside the baseline. Results are
labelled faithful or partial; the two are never merged. With --split the design and holdout
periods are reported separately: do not tune live settings on the holdout.
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
    rp = sub.add_parser(
        "replay",
        help="re-score saved evaluations under a candidate configuration (point-in-time)",
        description=REPLAY_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rp.add_argument("--db", required=True, type=Path, help="path to a database COPY")
    rp.add_argument("--since", required=True, help="window start (ISO-8601 or epoch)")
    rp.add_argument("--until", required=True, help="window end and observation cutoff")
    rp.add_argument(
        "--config",
        required=True,
        help="candidate settings: inline JSON object or a JSON file; applied over the "
        "settings recorded in each evaluation",
    )
    rp.add_argument(
        "--baseline-config",
        default="current",
        help="'current' (settings recorded in each evaluation, default) or JSON overrides",
    )
    rp.add_argument(
        "--split",
        help="walk-forward boundary: [since, split) is the design period, "
        "[split, until] the holdout. Never tune live settings on the holdout.",
    )
    rp.add_argument("--out", type=Path, help="markdown file (default: print to stdout)")
    rp.add_argument("--json-out", type=Path, help="also write the structured result as JSON")
    rp.add_argument(
        "--i-know-this-is-a-copy",
        action="store_true",
        help="allow a path that resolves to the configured live database",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        data.refuse_live(args.db, i_know_this_is_a_copy=args.i_know_this_is_a_copy)
        since, until = data.parse_time(args.since), data.parse_time(args.until)
        if until <= since:
            raise data.ResearchError("--until must be later than --since")
        if args.command == "replay":
            return run_replay(args, since, until)
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


def run_replay(args, since, until):
    candidate = replay.load_config_arg(args.config, "--config")
    baseline = replay.load_config_arg(args.baseline_config, "--baseline-config")
    split = data.parse_time(args.split) if args.split else None
    db = data.open_copy(args.db)
    try:
        data.require_schema(db)
        result = replay.run(db, since, until, candidate=candidate, baseline=baseline, split=split)
    finally:
        db.close()
    text = replay.render(result)
    if args.json_out:
        args.json_out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0

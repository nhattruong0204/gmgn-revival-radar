"""Report configuration projection (schema 10): derived, rebuildable, never authoritative.

Market reports must say whether the evaluations in a window were scored under more than
one saved configuration. Answering that from the saved presentations reads multi-KB JSON
per evaluation, which is slow when the OS page cache is cold. ``evaluation_configuration``
keeps one 32-byte digest per evaluation of exactly the text the on-read query compares
(``configuration`` JSON and ``signal.score_version``, ``legacy`` for each missing part and
``legacy:legacy`` for a missing presentation). The scanner maintains it after each scan;
the website uses it only when it covers every evaluation of the requested window and
otherwise computes the same answer on read. Nothing here calls a provider or rewrites a
historical row.
"""

import hashlib
import sqlite3
import time

# One expression, shared by the projection and the on-read fallback, so both compare the
# same text. ``{valid}`` tolerates damaged legacy JSON by contributing no fields.
_VALID = "CASE WHEN json_valid(st.value) THEN st.value ELSE '{}' END"
CONFIGURATION_TEXT = f"""COALESCE((SELECT
    COALESCE(CAST(json_extract({_VALID},'$.configuration') AS TEXT),'legacy') || ':' ||
    COALESCE(CAST(json_extract({_VALID},'$.signal.score_version') AS TEXT),'legacy')
    FROM state st WHERE st.key='telegram:evaluation:' || e.id),'legacy:legacy')"""
BATCH = 2000


def digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()


def available(db: sqlite3.Connection) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM main.sqlite_master "
            "WHERE type='table' AND name='evaluation_configuration'"
        ).fetchone()
        is not None
    )


def refresh(db: sqlite3.Connection, budget_seconds: float = 15.0) -> dict:
    """Digest evaluations that have no current projection row, newest first.

    Idempotent and incremental: rows whose evaluation timestamp changed (reused ids) are
    replaced, everything else is left alone. Each batch commits on its own so the single
    scanner writer never holds the lock for the whole backfill; a spent budget simply
    resumes on the next scan.
    """
    if not available(db):
        return {}
    started = time.monotonic()
    written = 0
    while True:
        rows = db.execute(
            f"""SELECT e.id,e.timestamp,{CONFIGURATION_TEXT} AS configuration
            FROM main.evaluations e LEFT JOIN main.evaluation_configuration c
                ON c.evaluation_id=e.id AND c.timestamp=e.timestamp
            WHERE c.evaluation_id IS NULL ORDER BY e.id DESC LIMIT ?""",
            (BATCH,),
        ).fetchall()
        if not rows:
            return {"written": written, "complete": True}
        with db:
            db.executemany(
                "INSERT OR REPLACE INTO evaluation_configuration (evaluation_id,timestamp,digest)"
                " VALUES (?,?,?)",
                [(r["id"], r["timestamp"], digest(r["configuration"])) for r in rows],
            )
        written += len(rows)
        if time.monotonic() - started >= budget_seconds:
            return {"written": written, "complete": False}

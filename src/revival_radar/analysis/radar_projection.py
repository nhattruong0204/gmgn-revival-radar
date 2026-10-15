"""Radar field projection: derived, rebuildable, never authoritative.

Radar sorts and searches by fields that live only inside each evaluation's saved
presentation (``name``, ``market_cap``, ``liquidity``, ``volume_5m``), and search also reads the
evaluation's ``symbol``. Reading them means
loading and fully validating a multi-KB payload per candidate token, which is slow on a
cold page cache. ``evaluation_radar`` keeps exactly the values ``web.queries.public_field``
would return for each evaluation (``NULL`` when the saved evidence fails validation),
computed with the same ``Repository._market_detail`` policy.

The scanner maintains it after every scan, newest evaluations first and in bounded
batches. The website trusts a row only while the evaluation's id, timestamp, score,
status and eligibility still match it, and computes any other row on read with the
original function, so a response never depends on how far the backfill has got. The table
is created without a schema-version bump (``CREATE TABLE IF NOT EXISTS``), is ignored by
older code and can be dropped and rebuilt at any time. Nothing here calls a provider or
rewrites a historical row.
"""

import sqlite3
import time

from revival_radar.storage.repository import Repository

FIELDS = ("name", "market_cap", "liquidity", "volume_5m")
BATCH = 1000
_SCALARS = (str, int, float, type(None))


def available(db: sqlite3.Connection) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='evaluation_radar'"
        ).fetchone()
        is not None
    )


def extract(row, payload) -> tuple:
    """The four values ``public_field`` returns for this evaluation and saved payload."""
    detail = Repository._market_detail(row, payload)
    token = detail["token"] if detail else {}
    return tuple(
        value if isinstance(value := token.get(name), _SCALARS) else None for name in FIELDS
    )


def refresh(db: sqlite3.Connection, budget_seconds: float = 15.0) -> dict:
    """Project evaluations that have no current row, newest first; idempotent and resumable."""
    if not available(db):
        return {}
    started = time.monotonic()
    written = 0
    while True:
        rows = db.execute(
            """SELECT e.id,e.chain,e.contract_address,e.timestamp,e.score,e.status,e.eligible,
                e.symbol,st.value AS payload
            FROM main.evaluations e
            LEFT JOIN main.evaluation_radar p ON p.evaluation_id=e.id AND p.timestamp=e.timestamp
                AND p.score IS e.score AND p.status IS e.status AND p.eligible IS e.eligible
            LEFT JOIN main.state st ON st.key='telegram:evaluation:' || e.id
            WHERE p.evaluation_id IS NULL ORDER BY e.id DESC LIMIT ?""",
            (BATCH,),
        ).fetchall()
        if not rows:
            return {"written": written, "complete": True}
        with db:
            db.executemany(
                "INSERT OR REPLACE INTO evaluation_radar (evaluation_id,timestamp,score,status,"
                "eligible,symbol,name,market_cap,liquidity,volume_5m) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        r["id"],
                        r["timestamp"],
                        r["score"],
                        r["status"],
                        r["eligible"],
                        r["symbol"],
                        *extract(r, r["payload"]),
                    )
                    for r in rows
                ],
            )
        written += len(rows)
        if time.monotonic() - started >= budget_seconds:
            return {"written": written, "complete": False}

"""Append-only replay-grade evidence archive (issue #74).

Pruning is unchanged. Before expiring evaluations are deleted, the ones worth replaying
are copied into ``replay_evaluations``: every evaluation of a token that was ever called
(within +/- N days of a call) and a deterministic sample of other tokens kept as a
comparison baseline. 1h candle series used by evaluations are copied, deduplicated by
content hash, into ``replay_candles``. Both tables reject UPDATE and DELETE.
"""

import hashlib
import logging
import sqlite3
import time
import zlib

log = logging.getLogger(__name__)


def sampled(chain: str, address: str, fraction: float) -> bool:
    """Stable, reproducible membership of a token in the comparison sample."""
    if fraction <= 0:
        return False
    if fraction >= 1:
        return True
    digest = hashlib.sha256(f"{chain}:{address}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < fraction


def pack(text: str) -> bytes:
    return zlib.compress(text.encode(), 6)


def unpack(blob: bytes | str | None) -> str | None:
    if blob is None:
        return None
    return blob if isinstance(blob, str) else zlib.decompress(blob).decode()


def archive_candles(
    db: sqlite3.Connection, chain: str, address: str, kind: str, fetched_at: float, payload: str
) -> bool:
    """Copy one cached candle series; identical content is stored once. True if new."""
    digest = hashlib.sha256(payload.encode()).hexdigest()
    cursor = db.execute(
        "INSERT OR IGNORE INTO replay_candles "
        "(chain,contract_address,kind,fetched_at,series_hash,payload,archived_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (chain, address, kind, fetched_at, digest, pack(payload), time.time()),
    )
    return bool(cursor.rowcount)


def archive_expiring(
    db: sqlite3.Connection,
    before: float,
    *,
    window_days: int,
    sample_fraction: float,
    sample_max_bytes: int,
) -> dict[str, int]:
    """Copy qualifying evaluations that scans started before ``before`` are about to lose.

    Runs inside the caller's transaction so archive and prune commit together. Returns
    counts; ``skipped_sample_cap`` is non-zero when the sample budget is exhausted.
    """
    counts = {"called_window": 0, "sample": 0, "skipped_sample_cap": 0}
    window = window_days * 86400
    sample_bytes = db.execute(
        "SELECT COALESCE(SUM(LENGTH(presentation)),0) FROM replay_evaluations WHERE reason='sample'"
    ).fetchone()[0]
    verdict: dict[tuple[str, str], bool] = {}
    rows = db.execute(
        """SELECT e.id,e.scan_id,e.timestamp,e.chain,e.contract_address,e.symbol,e.score,
        e.status,e.eligible,e.rejection_reasons,e.missing_fields,e.warnings,e.asset_type,
        e.discovery_sources,
        EXISTS (SELECT 1 FROM alerts a WHERE a.delivery_status='sent'
            AND a.chain=e.chain AND a.contract_address=e.contract_address
            AND ABS(a.timestamp-e.timestamp)<=?) AS called
        FROM evaluations e JOIN scan_runs r ON r.id=e.scan_id
        WHERE r.started<?""",
        (window, before),
    ).fetchall()
    for row in rows:
        key = (row["chain"], row["contract_address"])
        if key not in verdict:
            verdict[key] = sampled(*key, sample_fraction)
        reason = "called_window" if row["called"] else "sample" if verdict[key] else None
        if reason is None:
            continue
        if reason == "sample" and sample_bytes >= sample_max_bytes:
            counts["skipped_sample_cap"] += 1
            continue
        state = db.execute(
            "SELECT value FROM state WHERE key=?", (f"telegram:evaluation:{row['id']}",)
        ).fetchone()
        if state is None:
            continue  # nothing replayable was ever saved for this evaluation
        packed = pack(state[0])
        cursor = db.execute(
            """INSERT OR IGNORE INTO replay_evaluations (evaluation_id,scan_id,timestamp,chain,
            contract_address,symbol,score,status,eligible,rejection_reasons,missing_fields,
            warnings,asset_type,discovery_sources,presentation,reason,archived_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["id"], row["scan_id"], row["timestamp"], row["chain"],
                row["contract_address"], row["symbol"], row["score"], row["status"],
                row["eligible"], row["rejection_reasons"], row["missing_fields"],
                row["warnings"], row["asset_type"], row["discovery_sources"],
                packed, reason, time.time(),
            ),
        )  # fmt: skip
        if cursor.rowcount:
            counts[reason] += 1
            if reason == "sample":
                sample_bytes += len(packed)
    if counts["skipped_sample_cap"]:
        log.warning("replay sample budget reached; skipped %d", counts["skipped_sample_cap"])
    return counts

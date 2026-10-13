"""Explicit, reversible operator attestations; never infer origins from market data."""

import hashlib
import json
import sqlite3
import time
import uuid

TABLES = {
    "scan": "scan_runs",
    "snapshot": "token_snapshots",
    "evaluation": "evaluations",
    "alert": "alerts",
}
ORIGINS = {"live", "synthetic", "test", "unknown"}


def classify(db: sqlite3.Connection, evidence: bytes) -> dict:
    """Apply one reviewed JSON attestation atomically, keeping original evidence intact.

    Validation proves scope and records the operator's evidence, not market truth.
    Reversal requires another attestation with origin=unknown (or the prior origin).
    """
    manifest = json.loads(evidence)
    if not isinstance(manifest, dict):
        raise ValueError("Require a reviewed JSON attestation object")
    kind, identity, origin = (manifest.get(key) for key in ("kind", "id", "origin"))
    reason = manifest.get("evidence")
    if (
        not isinstance(kind, str)
        or kind not in TABLES
        or type(identity) is not int
        or identity < 1
        or not isinstance(origin, str)
        or origin not in ORIGINS
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("Require kind, positive integer id, allowed origin and reviewed evidence")
    key = f"origin:{kind}:{identity}"
    audit = f"provenance:audit:{uuid.uuid4().hex}"
    now = time.time()
    db.execute("BEGIN IMMEDIATE")
    try:
        record = db.execute(f"SELECT * FROM {TABLES[kind]} WHERE id=?", (identity,)).fetchone()
        if record is None:
            raise ValueError("Observation does not exist")
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        previous = row[0] if row else "unknown"
        result = {"kind": kind, "id": identity, "previous": previous, "origin": origin, "at": now}
        db.execute(
            "INSERT INTO state(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value,updated_at=excluded.updated_at",
            (key, origin, now),
        )
        db.execute(
            "INSERT INTO state(key,value,updated_at) VALUES(?,?,?)",
            (
                audit,
                json.dumps(
                    result
                    | {
                        "evidence": reason,
                        "sha256": hashlib.sha256(evidence).hexdigest(),
                        "record_sha256": hashlib.sha256(
                            json.dumps(list(record)).encode()
                        ).hexdigest(),
                    }
                ),
                now,
            ),
        )
        db.commit()
        return result | {"audit": audit}
    except Exception:
        db.rollback()
        raise

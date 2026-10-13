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
        cursor = db.execute(f"SELECT * FROM {TABLES[kind]} WHERE id=?", (identity,))
        record = cursor.fetchone()
        if record is None:
            raise ValueError("Observation does not exist")
        fields = dict(zip((column[0] for column in cursor.description), record, strict=True))
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        previous = row[0] if row else "unknown"
        result = {"kind": kind, "id": identity, "previous": previous, "origin": origin, "at": now}
        db.execute(
            "INSERT INTO state(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value,updated_at=excluded.updated_at",
            (key, origin, now),
        )
        # Apply read visibility to durable copies without rewriting their saved facts.
        # UUID markers survive diagnostic pruning and cannot attach to reused row IDs.
        if (
            kind in {"scan", "evaluation"}
            and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_briefs'"
            ).fetchone()
        ):
            if kind == "scan":
                copies = db.execute(
                    "SELECT id FROM research_briefs WHERE scan_id=? AND scan_started_at=?",
                    (identity, fields["started"]),
                ).fetchall()
            else:
                copies = db.execute(
                    """SELECT b.id FROM research_briefs b JOIN scan_runs s ON s.id=b.scan_id
                    AND s.started=b.scan_started_at WHERE b.evaluation_id=? AND b.scan_id=?
                    AND b.chain=? AND b.contract_address=? AND b.as_of=?""",
                    (
                        identity,
                        fields["scan_id"],
                        fields["chain"],
                        fields["contract_address"],
                        fields["timestamp"],
                    ),
                ).fetchall()
            for copy in copies:
                db.execute(
                    "INSERT INTO state(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                    "updated_at=excluded.updated_at",
                    (f"origin:brief:{kind}:{copy[0]}", origin, now),
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

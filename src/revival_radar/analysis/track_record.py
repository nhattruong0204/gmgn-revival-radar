"""Saved alert cohorts and checkpoint accounting shared by website and bot.

No peak selection, interpolation, zero filling or inference of executed performance.
"""

import hashlib
import json
import math
from collections import Counter
from statistics import median

from revival_radar.models.research_identity import research_context


def finite(value):
    return value if type(value) in {int, float} and math.isfinite(value) else None


def checkpoint_rows(repo, since, until, horizon, *, synthetic=None, alert_id=None):
    modern = repo.research_outcomes_enabled
    joins = (
        """LEFT JOIN research_alert_facts f ON f.alert_id=a.id
        LEFT JOIN research_delivery_receipts d ON d.alert_id=a.id
        LEFT JOIN research_checkpoint_facts c ON c.alert_id=a.id AND c.horizon_hours=?"""
        if modern
        else ""
    )
    extra = (
        "f.payload AS frozen_payload,f.saved_at,f.initial_price AS captured_initial,"
        "f.score_version,f.brief_id,d.sent_at,"
        "c.recorded_at,c.due_at AS frozen_due,c.observed_at AS frozen_observed,"
        "c.price AS frozen_price,c.market_cap AS frozen_cap,"
        "c.return_pct AS frozen_return,c.origin AS checkpoint_origin"
        if modern
        else "NULL AS frozen_payload,NULL AS saved_at,NULL AS captured_initial,"
        "NULL AS score_version,"
        "NULL AS brief_id,NULL AS sent_at,NULL AS recorded_at,NULL AS checkpoint_origin"
    )
    predicate = (
        "((d.sent_at<=? AND (f.saved_at IS NULL OR f.saved_at<=?)) "
        "OR (d.alert_id IS NULL AND f.alert_id IS NULL AND a.delivery_status='sent'))"
        if modern
        else "a.delivery_status='sent'"
    )
    params = (
        ([horizon] if modern else [])
        + [horizon]
        + ([until, until] if modern else [])
        + [since, until]
    )
    if alert_id is not None:
        predicate += " AND a.id=?"
        params = params[:-2] + [alert_id] + params[-2:]
    rows = repo.db.execute(
        f"""SELECT a.id,a.timestamp,a.chain,a.contract_address,a.score,
        s.price AS initial_price,s.preset,s.revision,s.stage,{extra},
        origin.value AS alert_origin,legacy.value AS legacy_payload,
        o.due_at,o.observed_at,o.price AS checkpoint_price,
        o.market_cap AS checkpoint_cap,o.return_pct
        FROM alerts a LEFT JOIN alert_signals s ON s.alert_id=a.id {joins}
        LEFT JOIN state origin ON origin.key='origin:alert:' || a.id
        LEFT JOIN state legacy ON legacy.key='telegram:alert:' || a.id
        LEFT JOIN signal_outcomes o ON o.alert_id=a.id AND o.horizon_hours=?
        WHERE {predicate} AND a.timestamp BETWEEN ? AND ? ORDER BY a.timestamp DESC,a.id DESC
        LIMIT 10001""",
        params,
    ).fetchall()
    if len(rows) > 10000:
        import sqlite3

        raise sqlite3.DataError("Cohort exceeds exact accounting limit; narrow the window")
    result = []
    for raw in rows:
        row = dict(raw)
        origin = (
            row["alert_origin"]
            if row["alert_origin"] in {"live", "synthetic", "test"}
            else "unknown"
        )
        if synthetic is not None and (
            origin == "test" or (origin == "synthetic" and not synthetic)
        ):
            continue
        frozen = {}
        try:
            frozen = json.loads(row["frozen_payload"] or "{}")
        except (ValueError, TypeError):
            frozen = {}
        if not isinstance(frozen, dict):
            frozen = {}
        captured = row["saved_at"] is not None
        token, signal = frozen.get("token") or {}, frozen.get("signal") or {}
        token = token if isinstance(token, dict) else {}
        signal = signal if isinstance(signal, dict) else {}
        legacy_symbol = "?"
        if not captured:
            try:
                legacy = json.loads(row["legacy_payload"] or "{}")
                old_token, old_signal = legacy["token"], legacy["signal"]
                if (
                    old_token["chain"] == row["chain"]
                    and old_token["contract_address"] == row["contract_address"]
                    and old_token["timestamp"] == row["timestamp"]
                    and old_signal["score"] == row["score"]
                ):
                    symbol = old_token.get("symbol", "?")
                    if isinstance(symbol, str):
                        legacy_symbol = "".join(c for c in symbol if c.isprintable())[:100]
            except (ValueError, KeyError, TypeError):
                legacy_symbol = "?"
        context = (
            research_context(frozen.get("configuration")) if captured else research_context(row)
        )
        initial = finite(row["captured_initial"]) if captured else finite(row["initial_price"])
        due = row["timestamp"] + horizon * 3600
        observed = row["frozen_observed"] if row["recorded_at"] is not None else row["observed_at"]
        price = row["frozen_price"] if row["recorded_at"] is not None else row["checkpoint_price"]
        cap = row["frozen_cap"] if row["recorded_at"] is not None else row["checkpoint_cap"]
        change = row["frozen_return"] if row["recorded_at"] is not None else row["return_pct"]
        actual_due = row["frozen_due"] if row["recorded_at"] is not None else row["due_at"]
        receipt_visible = row["recorded_at"] is None or row["recorded_at"] <= until
        checkpoint_origin = row["checkpoint_origin"] or "unknown"
        source_visible = synthetic is None or checkpoint_origin not in (
            {"test"} if synthetic else {"test", "synthetic"}
        )
        visible = observed is not None and observed <= until and receipt_visible and source_visible
        state = "pending" if until <= due + 3600 else "missed"
        reason = (
            "Awaiting horizon / one-hour collection window"
            if state == "pending"
            else "No eligible checkpoint was saved in the one-hour collection window"
        )
        value = None
        if visible:
            if actual_due != due or not due <= observed <= due + 3600:
                state, reason = "unresolved", "Stored checkpoint falls outside its declared window"
            elif finite(change) is None:
                state = (
                    "missing_price"
                    if initial is None or initial <= 0 or finite(price) is None
                    else "unresolved"
                )
                reason = "Observed checkpoint; price-change arithmetic unavailable"
            elif initial is None or initial <= 0 or finite(price) is None or price < 0:
                state, reason = (
                    "unresolved",
                    "Stored return cannot be verified from saved price inputs",
                )
            else:
                calculated = (price / initial - 1) * 100
                if not math.isfinite(calculated) or not math.isclose(
                    change, calculated, rel_tol=1e-9, abs_tol=1e-8
                ):
                    state, reason = "unresolved", "Stored return disagrees with saved price inputs"
                else:
                    state, reason, value = "return", "Observed checkpoint price change", change
        knowledge = (
            "recorded"
            if row["saved_at"] is not None and row["sent_at"] is not None
            else "legacy_unknown"
        )
        version = row["score_version"] if captured else None
        fields = [row["chain"], context["preset"], context["revision"], version, origin, knowledge]
        cohort_id = hashlib.sha256(json.dumps(fields).encode()).hexdigest()[:16]
        result.append(
            {
                "alert_id": row["id"],
                "timestamp": row["timestamp"],
                "chain": row["chain"],
                "contract_address": row["contract_address"],
                "symbol": token.get("symbol", legacy_symbol),
                "score": row["score"],
                "stage": signal.get("status") or row["stage"] or "UNKNOWN",
                "preset": context["preset"],
                "revision": context["revision"],
                "score_version": version,
                "origin": origin,
                "knowledge": knowledge,
                "saved_at": row["saved_at"],
                "sent_at": row["sent_at"],
                "cohort_id": cohort_id,
                "horizon_hours": horizon,
                "due_at": due,
                "window_end": due + 3600,
                "observed_at": observed if visible else None,
                "recorded_at": row["recorded_at"] if visible else None,
                "checkpoint_origin": checkpoint_origin if visible else "unknown",
                "initial_price": initial,
                "checkpoint_price": finite(price) if visible else None,
                "checkpoint_market_cap": finite(cap) if visible else None,
                "reported_return_pct": finite(change) if visible else None,
                "return_pct": value,
                "state": state,
                "reason": reason,
                "brief_id": row["brief_id"],
                "source": f"alert:{row['id']}/checkpoint:{horizon}",
            }
        )
    return result


def accounting(rows, *, comparable=True):
    counts = Counter(row["state"] for row in rows)
    values = [row["return_pct"] for row in rows if row["state"] == "return"]
    positive, negative, zero = (
        sum(v > 0 for v in values),
        sum(v < 0 for v in values),
        sum(v == 0 for v in values),
    )
    return {
        "total": len(rows),
        "observed": sum(row["observed_at"] is not None for row in rows),
        "returns_available": len(values),
        "positive": positive,
        "negative": negative,
        "zero": zero,
        "missing_price": counts["missing_price"],
        "unresolved": counts["unresolved"],
        "pending": counts["pending"],
        "missed": counts["missed"],
        "median_return_pct": median(values) if values and comparable else None,
        "positive_rate_pct": positive / len(values) * 100 if values and comparable else None,
        "small_sample": len(values) < 20,
        "comparable": comparable,
        "distribution": [
            {"label": label, "count": count}
            for label, count in (
                ("Below -50%", sum(v < -50 for v in values)),
                ("-50% to below 0%", sum(-50 <= v < 0 for v in values)),
                ("Exactly 0%", zero),
                ("Above 0% through 50%", sum(0 < v <= 50 for v in values)),
                ("Above 50%", sum(v > 50 for v in values)),
            )
        ],
    }

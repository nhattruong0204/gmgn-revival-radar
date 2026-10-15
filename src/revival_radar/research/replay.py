"""Point-in-time replay of saved evaluations under a candidate configuration (issue #74).

Read-only and causal. Every evaluation is re-scored with the unchanged ``score_token`` from
inputs timestamped at or before that evaluation: the saved evaluation payload (the enriched
token as scored), the token's earlier snapshots (strictly before the evaluation, through the
same ``fresh_history`` windows) and, when archived, the 1h candle series fetched at or before
it. The publish decision (threshold, ``alert_min_stage``, cooldown, ``realert_requires_new_
evidence``) is simulated per token. Forward outcomes come from saved snapshots through the
shared ``market_performance`` and are only attached to the finished simulated calls; they
never feed a decision.

Input labels, never mixed silently:

* ``faithful``: payload and configuration retained, and whenever the original evaluation used
  candles the exact series is archived, so structure is re-derived under the candidate config.
* ``partial_saved_structure``: payload retained but the candles are not; the saved structure
  result is reused (exact unless the candidate changes a structure setting).
* ``partial_legacy``: no replayable payload or recorded configuration; only the recorded
  score, status and eligibility can be gated, nothing is re-scored.
"""

import heapq
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from revival_radar.analysis.call_metrics import finite, market_performance
from revival_radar.analysis.call_policy import new_evidence_block
from revival_radar.analysis.market_structure import analyze_structure
from revival_radar.analysis.scoring import below_min_stage, score_token
from revival_radar.config import Settings
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.models.token import Candle, TokenSnapshot
from revival_radar.research import stats
from revival_radar.research.data import ResearchError, iso, tables
from revival_radar.storage.replay_archive import unpack

FAITHFUL = "faithful"
PARTIAL_STRUCTURE = "partial_saved_structure"
PARTIAL_LEGACY = "partial_legacy"
LABELS = (FAITHFUL, PARTIAL_STRUCTURE, PARTIAL_LEGACY)
LABEL_TEXT = {
    FAITHFUL: "faithful",
    PARTIAL_STRUCTURE: "partial (candle-only/legacy: saved structure reused, candles not retained)",
    PARTIAL_LEGACY: "partial (candle-only/legacy: no replayable payload, recorded values only)",
}
MATCH_SECONDS = 5.0
DEFERRED_SECURITY = "Security request deferred by per-scan budget"
STALE_CANDLE_SECONDS = 7200


@dataclass
class Evaluation:
    id: int
    source: str  # live | archive
    timestamp: float
    chain: str
    address: str
    symbol: str | None
    score: int | None
    status: str | None
    eligible: bool
    raw: object  # presentation text or compressed blob, decoded lazily

    @property
    def key(self):
        return (self.chain, self.address)


@dataclass
class Prepared:
    evaluation: Evaluation
    label: str
    token: TokenSnapshot | None
    saved: RevivalResult | None
    recorded_settings: dict
    price: float | None
    market_cap: float | None
    structure_flags: dict | None


@dataclass
class SimCall:
    timestamp: float
    score: int
    price: float | None
    stage: str | None
    structure: dict | None


@dataclass
class Policy:
    """Simulated publish decisions for one configuration; every accepted call counts as sent."""

    calls: dict = field(default_factory=lambda: defaultdict(list))

    def decide(self, key, timestamp, price, result, config, structure_flags):
        if not result.eligible:
            return False, "not_eligible"
        if result.score < config.alert_score_threshold:
            return False, "below_threshold"
        if below_min_stage(result.status, config.alert_min_stage):
            return False, "stage_below_minimum"
        previous = self.calls[key]
        recent = [c for c in previous if c.timestamp > timestamp - config.alert_cooldown_hours * 3600]
        if recent and result.score < max(c.score for c in recent) + config.realert_score_increase:
            return False, "cooldown"
        if config.realert_requires_new_evidence and previous:
            last = previous[-1]
            reason = new_evidence_block(last.price, last.stage, last.structure, price, result)
            if reason is not None:
                return False, f"realert_blocked:{reason}"
        previous.append(SimCall(timestamp, result.score, price, result.status, structure_flags))
        return True, None


def _loads(text):
    try:
        value = json.loads(text) if text else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_config_arg(value: str | None, name: str) -> dict:
    """A JSON object given inline or as a file path; ``None``/``current`` means no overrides."""
    if value is None or value.strip().lower() == "current":
        return {}
    text = value
    if not value.lstrip().startswith("{"):
        path = Path(value)
        if not path.is_file():
            raise ResearchError(f"{name}: file not found: {value}")
        text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ResearchError(f"{name}: invalid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise ResearchError(f"{name}: expected a JSON object of settings")
    parsed = parsed.get("settings", parsed) if isinstance(parsed.get("settings"), dict) else parsed
    unknown = sorted(set(parsed) - Settings.model_fields.keys())
    if unknown:
        raise ResearchError(f"{name}: unknown settings {unknown}")
    try:
        Settings(_env_file=None, **parsed)
    except (ValidationError, ValueError) as exc:
        raise ResearchError(f"{name}: invalid settings ({exc})") from exc
    return parsed


class ConfigFactory:
    """Effective per-evaluation settings: the recorded in-force settings plus overrides."""

    def __init__(self):
        self.cache: dict[str, Settings] = {}

    def make(self, recorded: dict, overrides: dict) -> Settings:
        values = {k: v for k, v in recorded.items() if k in Settings.model_fields}
        values.update(overrides)
        key = json.dumps(values, sort_keys=True, default=str)
        if key not in self.cache:
            try:
                self.cache[key] = Settings(_env_file=None, **values)
            except (ValidationError, ValueError) as exc:
                raise ResearchError(
                    f"Cannot build settings for an evaluation: {str(exc)[:200]}"
                ) from exc
        return self.cache[key]


class Inputs:
    """Causal readers over the read-only copy: nothing at or after the cutoff is returned."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.have = tables(db)
        self.snapshot_cache: dict = {}
        self.candle_cache: dict = {}

    def history(self, token: TokenSnapshot, cutoff: float, limit: int) -> list[TokenSnapshot]:
        rows = self.db.execute(
            """SELECT timestamp,payload FROM token_snapshots
            WHERE chain=? AND contract_address=? AND timestamp<? ORDER BY timestamp DESC LIMIT ?""",
            (*token.key, cutoff, limit),
        ).fetchall()
        result = []
        for row in rows:
            key = (*token.key, row["timestamp"])
            snapshot = self.snapshot_cache.get(key)
            if snapshot is None:
                if len(self.snapshot_cache) > 50_000:
                    self.snapshot_cache.clear()
                snapshot = self.snapshot_cache[key] = TokenSnapshot.model_validate_json(
                    row["payload"]
                )
            result.append(snapshot)
        return result

    def snapshot_at(self, key, timestamp):
        return self.db.execute(
            "SELECT price,market_cap FROM token_snapshots "
            "WHERE chain=? AND contract_address=? AND timestamp=?",
            (*key, timestamp),
        ).fetchone()

    def candles(self, token: TokenSnapshot, cutoff: float, kind: str):
        """Latest archived series fetched at or before ``cutoff`` in the cutoff's UTC hour."""
        if "replay_candles" not in self.have:
            return None
        if token.key not in self.candle_cache:
            self.candle_cache[token.key] = self.db.execute(
                "SELECT fetched_at,payload FROM replay_candles "
                "WHERE chain=? AND contract_address=? AND kind=? ORDER BY fetched_at,id",
                (*token.key, kind),
            ).fetchall()
        best = None
        for row in self.candle_cache[token.key]:
            if row["fetched_at"] > cutoff:
                break
            payload = _loads(unpack(row["payload"]))
            if payload.get("hour") == int(cutoff // 3600):
                best = payload
        if best is None:
            return None
        try:
            return [Candle.model_validate(c) for c in best["candles"]]
        except (ValueError, KeyError, TypeError, ValidationError):
            return None


def iter_evaluations(db: sqlite3.Connection, since: float, until: float):
    """Live and archived evaluations in [since, until], ordered by time, deduplicated."""
    live = db.execute(
        """SELECT e.id,e.timestamp,e.chain,e.contract_address,e.symbol,e.score,e.status,
        e.eligible,s.value AS presentation FROM evaluations e
        LEFT JOIN state s ON s.key='telegram:evaluation:' || e.id
        WHERE e.timestamp>=? AND e.timestamp<=? ORDER BY e.timestamp,e.id""",
        (since, until),
    )

    def live_rows():
        for r in live:
            yield (r["timestamp"], 0, r["id"]), Evaluation(
                r["id"], "live", r["timestamp"], r["chain"], r["contract_address"],
                r["symbol"], r["score"], r["status"], bool(r["eligible"]), r["presentation"],
            )  # fmt: skip

    archived = []
    if "replay_evaluations" in tables(db):
        for r in db.execute(
            """SELECT id,timestamp,chain,contract_address,symbol,score,status,eligible,
            presentation FROM replay_evaluations WHERE timestamp>=? AND timestamp<=?
            ORDER BY timestamp,id""",
            (since, until),
        ):
            duplicate = db.execute(
                "SELECT 1 FROM evaluations WHERE chain=? AND contract_address=? AND timestamp=?",
                (r["chain"], r["contract_address"], r["timestamp"]),
            ).fetchone()
            if duplicate:
                continue
            archived.append(
                (
                    (r["timestamp"], 1, r["id"]),
                    Evaluation(
                        r["id"], "archive", r["timestamp"], r["chain"], r["contract_address"],
                        r["symbol"], r["score"], r["status"], bool(r["eligible"]),
                        r["presentation"],
                    ),
                )  # fmt: skip
            )
    for _, evaluation in heapq.merge(live_rows(), archived, key=lambda item: item[0]):
        yield evaluation


def prepare(inputs: Inputs, evaluation: Evaluation) -> Prepared:
    payload = _loads(unpack(evaluation.raw) if evaluation.raw else None)
    settings = (payload.get("configuration") or {}).get("settings")
    token = saved = None
    try:
        if isinstance(payload.get("token"), dict) and isinstance(payload.get("signal"), dict):
            token = TokenSnapshot.model_validate(payload["token"])
            saved = RevivalResult.model_validate(payload["signal"])
    except (ValidationError, ValueError):
        token = saved = None
    if token is None or saved is None or not isinstance(settings, dict) or not settings:
        row = inputs.snapshot_at(evaluation.key, evaluation.timestamp)
        price = finite(row["price"]) if row else None
        return Prepared(
            evaluation, PARTIAL_LEGACY, None, None, {},
            price if price and price > 0 else None,
            finite(row["market_cap"]) if row else None, None,
        )  # fmt: skip
    price = finite(token.price)
    return Prepared(
        evaluation,
        FAITHFUL,  # refined per run once the candidate settings (lookback) are known
        token,
        saved,
        settings,
        price if price and price > 0 else None,
        finite(token.market_cap),
        saved.structure.model_dump(),
    )


def structure_for(inputs: Inputs, prepared: Prepared, config: Settings):
    """(structure, label): re-derive from archived candles when the original used candles."""
    saved = prepared.saved.structure
    if not saved.available:
        return saved, FAITHFUL
    timestamp = prepared.evaluation.timestamp
    candles = inputs.candles(prepared.token, timestamp, f"kline:1h:{config.kline_lookback_hours}")
    if candles is None:
        return saved, PARTIAL_STRUCTURE
    bars = sorted(
        (
            c
            for c in candles
            if timestamp - config.kline_lookback_hours * 3600 <= c.timestamp
            and c.timestamp + 3600 <= timestamp
        ),
        key=lambda c: c.timestamp,
    )
    if bars and timestamp - max(c.timestamp for c in bars) > STALE_CANDLE_SECONDS:
        return Structure(), FAITHFUL
    return analyze_structure(bars, config), FAITHFUL


def rescore(inputs: Inputs, prepared: Prepared, config: Settings):
    """Re-score under ``config`` from inputs strictly before the evaluation time."""
    token = prepared.token
    cutoff = prepared.evaluation.timestamp
    history = inputs.history(token, cutoff, config.history_observations * 3)
    structure, label = structure_for(inputs, prepared, config)
    result = score_token(token, history, structure, config)
    if any(w.startswith(DEFERRED_SECURITY) for w in prepared.saved.warnings):
        result.eligible = False  # the scanner overrode eligibility after scoring
    return result, label


def _seed_prior_calls(db, since):
    """Sent calls before the window seed cooldown/evidence state for every configuration."""
    seeds = defaultdict(list)
    for r in db.execute(
        """SELECT a.id,a.timestamp,a.chain,a.contract_address,a.score,s.price,s.stage,
        (SELECT value FROM state WHERE key='telegram:alert:' || a.id) AS saved
        FROM alerts a LEFT JOIN alert_signals s ON s.alert_id=a.id
        WHERE a.delivery_status='sent' AND a.timestamp<? ORDER BY a.timestamp,a.id""",
        (since,),
    ):
        structure = (_loads(r["saved"]).get("signal") or {}).get("structure")
        price = finite(r["price"])
        seeds[(r["chain"], r["contract_address"])].append(
            SimCall(r["timestamp"], r["score"], price, r["stage"],
                    structure if isinstance(structure, dict) else None)  # fmt: skip
        )
    return seeds


def _outcome(db, call, cutoff):
    perf = market_performance(
        db, call["chain"], call["contract_address"], call["timestamp"], call["market_cap"], cutoff
    )
    return {
        "change_since_call_pct": perf.change_since_call_pct,
        "mfe_pct": perf.mfe_pct,
        "mae_pct": perf.mae_pct,
        "observations": perf.count,
        "latest_observed_at": iso(perf.latest_at),
        "change_reason": perf.change_reason,
        "basis": perf.basis,
    }


def summarize(calls: list[dict]) -> dict:
    changes = [c["outcome"]["change_since_call_pct"] for c in calls]
    mfes = [c["outcome"]["mfe_pct"] for c in calls]
    known = stats.known(changes)
    return {
        "calls": len(calls),
        "tokens": len({(c["chain"], c["contract_address"]) for c in calls}),
        "with_outcome": len(known),
        "unknown_outcome": len(calls) - len(known),
        "positive": sum(v > 0 for v in known),
        "median_change_pct": stats.med(changes),
        "mean_change_pct": stats.avg(changes),
        "mfe_ge_25": sum(v >= 25 for v in stats.known(mfes)),
        "mfe_ge_50": sum(v >= 50 for v in stats.known(mfes)),
        "median_mfe_pct": stats.med(mfes),
    }


def by_label(calls: list[dict]) -> dict:
    return {label: summarize([c for c in calls if c["label"] == label]) for label in LABELS}


def _public(call, cutoff, db):
    row = {
        "chain": call["chain"],
        "contract_address": call["contract_address"],
        "symbol": call["symbol"],
        "called_at": iso(call["timestamp"]),
        "timestamp": call["timestamp"],
        "score": call["score"],
        "stage": call["stage"],
        "market_cap": call["market_cap"],
        "label": call["label"],
        "outcome_cutoff": iso(cutoff),
        "outcome": _outcome(db, call, cutoff),
    }
    return row


def run(
    db: sqlite3.Connection,
    since: float,
    until: float,
    *,
    candidate: dict,
    baseline: dict | None = None,
    split: float | None = None,
) -> dict:
    """Replay [since, until] and return the structured result (see ``render``)."""
    baseline = baseline or {}
    if split is not None and not since < split < until:
        raise ResearchError("--split must lie strictly inside --since and --until")
    inputs = Inputs(db)
    factory = ConfigFactory()
    seeds = _seed_prior_calls(db, since)
    policies = {
        "baseline": Policy(defaultdict(list, {k: list(v) for k, v in seeds.items()})),
        "candidate": Policy(defaultdict(list, {k: list(v) for k, v in seeds.items()})),
    }
    overrides = {"baseline": baseline, "candidate": candidate}
    same = baseline == candidate
    sim_calls = {"baseline": [], "candidate": []}
    decisions = {}  # (key, timestamp) -> per-run reason and label
    labels = Counter()
    agreement = Counter()
    evaluations = 0
    last_settings: dict = {}
    for evaluation in iter_evaluations(db, since, until):
        evaluations += 1
        prepared = prepare(inputs, evaluation)
        if prepared.recorded_settings:
            last_settings = prepared.recorded_settings
        outcomes = {}
        for name in ("baseline", "candidate"):
            if name == "candidate" and same:
                outcomes[name] = outcomes["baseline"]
                continue
            if prepared.token is None:
                result = RevivalResult(
                    score=evaluation.score if evaluation.score is not None else 0,
                    status=evaluation.status or "IGNORE",
                    eligible=evaluation.eligible,
                )
                config = factory.make(last_settings, overrides[name])
                run_label = PARTIAL_LEGACY
            else:
                config = factory.make(prepared.recorded_settings, overrides[name])
                result, run_label = rescore(inputs, prepared, config)
            outcomes[name] = (config, result, run_label)
        # One label per evaluation: the weakest across the two runs.
        run_labels = {o[2] for o in outcomes.values()}
        label = next((x for x in (PARTIAL_LEGACY, PARTIAL_STRUCTURE) if x in run_labels), FAITHFUL)
        labels[label] += 1
        if overrides["baseline"] == {} and prepared.token is not None:
            result = outcomes["baseline"][1]
            agreement[label + ":compared"] += 1
            agreement[label + ":agree"] += int(
                result.score == evaluation.score
                and result.status == evaluation.status
                and result.eligible == evaluation.eligible
            )
        for name in ("baseline", "candidate"):
            config, result, _ = outcomes[name]
            called, reason = policies[name].decide(
                evaluation.key, evaluation.timestamp, prepared.price, result, config,
                prepared.structure_flags if prepared.token is not None else None,
            )  # fmt: skip
            decisions[(evaluation.key, evaluation.timestamp, name)] = (called, reason, label)
            if called:
                sim_calls[name].append(
                    {
                        "chain": evaluation.chain,
                        "contract_address": evaluation.address,
                        "symbol": evaluation.symbol
                        or (prepared.token.symbol if prepared.token else None),
                        "timestamp": evaluation.timestamp,
                        "score": result.score,
                        "stage": result.status,
                        "market_cap": prepared.market_cap,
                        "label": label,
                    }
                )
    periods = (
        [("window", since, until, until)]
        if split is None
        else [("design", since, split, split), ("holdout", split, until, until)]
    )
    report = {
        "window": {"since": iso(since), "until": iso(until), "split": iso(split)},
        "baseline_config": baseline or "current (settings recorded in each evaluation)",
        "candidate_config": candidate,
        "evaluations": evaluations,
        "evaluation_labels": {label: labels[label] for label in LABELS},
        "baseline_rescore_agreement": {
            label: {
                "compared": agreement[label + ":compared"],
                "agree": agreement[label + ":agree"],
            }
            for label in LABELS
        },
        "periods": {},
        "actual": actual_comparison(db, since, until, sim_calls["baseline"], decisions),
    }
    for period, lo, hi, cutoff in periods:
        base = [c for c in sim_calls["baseline"] if lo <= c["timestamp"] < hi or (
            hi == until and c["timestamp"] == until)]  # fmt: skip
        cand = [c for c in sim_calls["candidate"] if lo <= c["timestamp"] < hi or (
            hi == until and c["timestamp"] == until)]  # fmt: skip
        base_rows = [_public(c, cutoff, db) for c in base]
        cand_rows = [_public(c, cutoff, db) for c in cand]
        base_keys = {(c["chain"], c["contract_address"], c["timestamp"]) for c in base_rows}
        cand_keys = {(c["chain"], c["contract_address"], c["timestamp"]) for c in cand_rows}

        def pick(rows, keys, invert=False):
            return [
                r
                for r in rows
                if ((r["chain"], r["contract_address"], r["timestamp"]) in keys) != invert
            ]

        removed = pick(base_rows, cand_keys, invert=True)
        added = pick(cand_rows, base_keys, invert=True)
        report["periods"][period] = {
            "from": iso(lo),
            "to": iso(hi),
            "outcome_cutoff": iso(cutoff),
            "baseline": {"summary": summarize(base_rows), "by_label": by_label(base_rows)},
            "candidate": {"summary": summarize(cand_rows), "by_label": by_label(cand_rows)},
            "removed_by_candidate": {"summary": summarize(removed), "by_label": by_label(removed)},
            "added_by_candidate": {"summary": summarize(added), "by_label": by_label(added)},
            "baseline_calls": base_rows,
            "candidate_calls": cand_rows,
            "removed_calls": removed,
            "added_calls": added,
        }
    return report


def actual_comparison(db, since, until, baseline_calls, decisions) -> dict:
    """Simulated baseline calls versus the calls actually published in the window."""
    actual = db.execute(
        """SELECT id,timestamp,chain,contract_address,score,delivery_status FROM alerts
        WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp,id""",
        (since, until),
    ).fetchall()
    sent = [a for a in actual if a["delivery_status"] == "sent"]
    simulated = defaultdict(list)
    for c in baseline_calls:
        simulated[(c["chain"], c["contract_address"])].append(c["timestamp"])
    matched, missed, matched_sim = [], [], set()
    for a in sent:
        key = (a["chain"], a["contract_address"])
        near = [t for t in simulated[key] if abs(t - a["timestamp"]) <= MATCH_SECONDS]
        row = {"alert_id": a["id"], "called_at": iso(a["timestamp"]), **{
            "chain": a["chain"], "contract_address": a["contract_address"]}}  # fmt: skip
        if near:
            matched_sim.add((key, near[0]))
            matched.append(row)
            continue
        evaluated = [
            v
            for (k, t, name), v in decisions.items()
            if name == "baseline" and k == key and abs(t - a["timestamp"]) <= MATCH_SECONDS
        ]
        if not evaluated:
            row["reason"] = "no_evaluation_retained"
        else:
            _, why, label = evaluated[0]
            row["reason"] = f"{why or 'simulated_call_not_matched'} ({label})"
        missed.append(row)
    other_status = defaultdict(list)
    for a in actual:
        if a["delivery_status"] != "sent":
            other_status[(a["chain"], a["contract_address"])].append(
                (a["timestamp"], a["delivery_status"])
            )
    extra = []
    for c in baseline_calls:
        key = (c["chain"], c["contract_address"])
        if (key, c["timestamp"]) in matched_sim:
            continue
        status = next(
            (s for t, s in other_status.get(key, ()) if abs(t - c["timestamp"]) <= MATCH_SECONDS),
            None,
        )
        extra.append(
            {
                "chain": key[0],
                "contract_address": key[1],
                "called_at": iso(c["timestamp"]),
                "label": c["label"],
                "reason": f"actual_delivery_{status}"
                if status
                else "not_published_live (paused, muted, dry-run, cap or state difference)",
            }
        )
    return {
        "actual_sent_calls": len(sent),
        "simulated_baseline_calls": len(baseline_calls),
        "matched": len(matched),
        "missed": missed,
        "extra": extra,
    }


def _fmt(value, spec="{:+.1f}%"):
    return "unknown" if value is None else spec.format(value)


def _summary_row(name, s):
    return (
        f"| {name} | {s['calls']} | {s['tokens']} | {s['with_outcome']} | "
        f"{s['unknown_outcome']} | {_fmt(s['median_change_pct'])} | "
        f"{_fmt(s['mean_change_pct'])} | {s['mfe_ge_25']} | {s['mfe_ge_50']} |"
    )


HEADER = (
    "| Set | Calls | Tokens | With outcome | Outcome unknown | Median change | Mean change "
    "| MFE >= 25% | MFE >= 50% |\n|---|---|---|---|---|---|---|---|---|"
)


def render(report: dict) -> str:
    w = report["window"]
    lines = [
        "# Point-in-time replay",
        "",
        f"Window {w['since']} to {w['until']}"
        + (f"; walk-forward split at {w['split']}" if w["split"] else "")
        + ". Research output on saved data only; not a recommendation to change live settings.",
        "",
        f"- Baseline configuration: `{json.dumps(report['baseline_config'])}`",
        f"- Candidate overrides (applied over each evaluation's recorded settings): "
        f"`{json.dumps(report['candidate_config'])}`",
        f"- Evaluations replayed: {report['evaluations']}",
        "",
        "## Input fidelity",
        "",
        "| Label | Evaluations |\n|---|---|",
    ]
    lines += [f"| {LABEL_TEXT[x]} | {report['evaluation_labels'][x]} |" for x in LABELS]
    lines += [
        "",
        "Baseline re-score versus the score, stage and eligibility recorded at the time "
        "(only meaningful when the baseline is the in-force configuration):",
        "",
        "| Label | Compared | Identical |\n|---|---|---|",
    ]
    for x in LABELS:
        a = report["baseline_rescore_agreement"][x]
        lines.append(f"| {LABEL_TEXT[x]} | {a['compared']} | {a['agree']} |")
    actual = report["actual"]
    lines += [
        "",
        "## Baseline versus calls actually published",
        "",
        f"Actual sent calls {actual['actual_sent_calls']}; simulated baseline calls "
        f"{actual['simulated_baseline_calls']}; matched {actual['matched']}; "
        f"actual calls not reproduced {len(actual['missed'])}; simulated calls never "
        f"published {len(actual['extra'])}.",
    ]
    for title, rows in (("Not reproduced", actual["missed"]), ("Never published", actual["extra"])):
        if rows:
            lines += ["", f"### {title}", "", "| Time | Token | Reason |\n|---|---|---|"]
            lines += [
                f"| {r['called_at']} | {r['chain']}:{r['contract_address'][:8]} | {r['reason']} |"
                for r in rows
            ]
    for period, data in report["periods"].items():
        lines += [
            "",
            f"## {period.title()} period {data['from']} to {data['to']}",
            "",
            f"Outcomes use saved snapshots observed up to {data['outcome_cutoff']} only.",
            "",
            HEADER,
        ]
        for name, key in (
            ("Baseline", "baseline"),
            ("Candidate", "candidate"),
            ("Removed by candidate", "removed_by_candidate"),
            ("Added by candidate", "added_by_candidate"),
        ):
            lines.append(_summary_row(name, data[key]["summary"]))
        for name, key in (("Baseline", "baseline"), ("Candidate", "candidate")):
            lines += ["", f"### {name} by input fidelity", "", HEADER]
            lines += [
                _summary_row(LABEL_TEXT[x], data[key]["by_label"][x]) for x in LABELS
            ]
        for title, rows in (
            ("Calls removed by the candidate", data["removed_calls"]),
            ("Calls added by the candidate", data["added_calls"]),
        ):
            if rows:
                lines += ["", f"### {title}", "", "| Time | Token | Stage | Score | Change | Fidelity |"]
                lines.append("|---|---|---|---|---|---|")
                lines += [
                    f"| {r['called_at']} | {r['symbol'] or r['contract_address'][:8]} | "
                    f"{r['stage']} | {r['score']} | "
                    f"{_fmt(r['outcome']['change_since_call_pct'])} | {r['label']} |"
                    for r in rows
                ]
    lines += [
        "",
        "## Notes",
        "",
        "- Unknown values are excluded from aggregates and counted, never treated as 0.",
        "- Replay is a conditional re-score of saved evaluations. It does not re-run discovery,"
        " provider budgets, deferral queues or watchlist timing, so a candidate that would have"
        " admitted tokens the scanner never evaluated cannot show them.",
        "- Do NOT tune live settings on the holdout period. Choose settings on the design period,"
        " read the holdout once, and treat small samples as descriptive only.",
        "",
    ]
    return "\n".join(lines)

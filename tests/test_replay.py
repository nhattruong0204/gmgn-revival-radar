"""Replay-grade evidence retention and the point-in-time replay harness (issue #74)."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from revival_radar.analysis.market_structure import analyze_structure
from revival_radar.analysis.scoring import score_token
from revival_radar.config import Settings
from revival_radar.config_context import configuration_context
from revival_radar.demo import DemoSource
from revival_radar.models.signal import RevivalResult
from revival_radar.models.token import Candle, TokenSnapshot
from revival_radar.research import cli, data, replay
from revival_radar.storage.database import SCHEMA_VERSION, connect
from revival_radar.storage.replay_archive import archive_candles, sampled
from revival_radar.storage.repository import Repository

from .conftest import changed

T0 = 1_800_000_000
STEP = 300
DAY = 86400
SCENARIO = DemoSource().data[0]
ALPHA = "abcdefghijkmnopqrstuvwxyz"


def make_addr(i, tag="A"):
    return f"{tag}{ALPHA[i // 25 % 25]}{ALPHA[i % 25]}" + "2" * 40


OTHER = make_addr(999, "Z")


def live_config(**values):
    base = {
        "_env_file": None,
        "enabled_chains": "sol",
        "gmgn_api_key": "fixture-key",
        "request_spacing_seconds": 0.00001,
        "dry_run": False,
        "alert_score_threshold": 75,
        "alert_cooldown_hours": 1,
    }
    return Settings(**(base | values))


def base_token(address=None, **values):
    token = TokenSnapshot(**SCENARIO["token"])
    updates = dict(values)
    if address:
        updates["contract_address"] = address
    return changed(token, **updates)


def demo_candles():
    return [Candle(**c) for c in SCENARIO["candles"]]


def seed_history(repo, address=None, shift=0):
    for raw in SCENARIO["history"]:
        snap = TokenSnapshot(**raw)
        snap = changed(snap, timestamp=snap.timestamp + shift)
        if address:
            snap = changed(snap, contract_address=address)
        repo.save_snapshot(snap)


def cache_candles(repo, token, config):
    payload = {
        "hour": int(token.timestamp // 3600),
        "candles": [c.model_dump() for c in demo_candles()],
    }
    repo.cache_save(
        f"kline:1h:{config.kline_lookback_hours}",
        token,
        json.dumps(payload),
        token.timestamp + 3600,
        origin="test",
    )


def step(repo, config, ts, *, address=None, call=True, candles=True, price_bump=0.0):
    """One scanner-like evaluation of the demo token at ``ts``."""
    token = base_token(address, timestamp=ts)
    token = changed(token, price=token.price * (1 + price_bump))
    context = configuration_context(config)
    if candles:
        cache_candles(repo, token, config)
    structure = analyze_structure(demo_candles(), config) if candles else None
    from revival_radar.models.signal import Structure

    result = score_token(
        token,
        repo.history(token, config.history_observations * 3),
        structure or Structure(),
        config,
    )
    scan_id = repo.begin_scan(ts, context)
    repo.record_evaluation(scan_id, token, result, config, context)
    alert_id = repo.reserve_alert(token, result, config, context) if call else None
    if alert_id:
        repo.finish_alert(alert_id, "sent")
    repo.save_snapshot(token)
    return token, result, alert_id


def build(tmp_path, config=None, *, steps=3, candles=True, name="live.db"):
    config = config or live_config()
    repo = Repository(connect(tmp_path / name))
    seed_history(repo)
    for k in range(steps):
        step(repo, config, T0 + k * STEP, candles=candles)
    # Later observations give the call forward outcomes.
    last = T0 + (steps - 1) * STEP
    for k in range(1, 5):
        later = base_token(
            timestamp=last + k * 600, market_cap=base_token().market_cap * (1 + k / 10)
        )
        repo.save_snapshot(later)
    return repo, config


def copy_of(repo, tmp_path, name="copy.db"):
    target = tmp_path / name
    repo.db.execute("VACUUM INTO ?", (str(target),))
    return target


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def replay_args(copy, **kw):
    return {"since": T0 - 10, "until": T0 + 3 * DAY, **kw}


def run(copy, candidate=None, baseline=None, split=None, since=T0 - 10, until=T0 + 3600):
    db = data.open_copy(copy)
    try:
        return replay.run(
            db, since, until, candidate=candidate or {}, baseline=baseline or {}, split=split
        )
    finally:
        db.close()


# ---------------------------------------------------------------- retention


def test_migration_is_additive_idempotent_and_keeps_rows(tmp_path):
    repo, _ = build(tmp_path)
    for table in ("replay_evaluations", "replay_candles"):
        repo.db.execute(f"DROP TABLE {table}")
    repo.db.execute("PRAGMA user_version=8")
    repo.db.commit()
    before = {
        t: [tuple(r) for r in repo.db.execute(f"SELECT * FROM {t} ORDER BY 1")]
        for t in ("token_snapshots", "alerts", "alert_signals", "evaluations", "state")
    }
    path = tmp_path / "live.db"
    repo.db.close()
    for _ in range(2):
        db = connect(path)
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 12
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"replay_evaluations", "replay_candles"} <= tables
        for t, rows in before.items():
            assert [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1")] == rows
        db.close()


def archive_world(tmp_path, **settings):
    """A called token (evaluated at T0.., one late evaluation 4 days on) plus an uncalled one."""
    config = live_config(replay_sample_fraction=0, **settings)
    repo, _ = build(tmp_path, config, steps=3)
    step(repo, config, T0 + 4 * DAY, call=False)  # outside +/- 3 days of the call
    seed_history(repo, OTHER)
    step(repo, config, T0 + 600, address=OTHER, call=False)
    return repo, config


def test_called_token_window_is_retained_and_other_rows_still_pruned(tmp_path):
    repo, config = archive_world(tmp_path)
    assert repo.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    repo.prune_diagnostics(T0 + 30 * DAY, config)
    assert repo.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0
    assert repo.db.execute("SELECT COUNT(*) FROM state WHERE key LIKE 'telegram:evaluation:%'")
    assert (
        repo.db.execute(
            "SELECT COUNT(*) FROM state WHERE key LIKE 'telegram:evaluation:%'"
        ).fetchone()[0]
        == 0
    )
    rows = repo.db.execute(
        "SELECT chain,contract_address,timestamp,reason FROM replay_evaluations ORDER BY timestamp"
    ).fetchall()
    assert [r["timestamp"] for r in rows] == [T0, T0 + STEP, T0 + 2 * STEP]
    assert {r["reason"] for r in rows} == {"called_window"}
    assert all(r["contract_address"] != OTHER for r in rows)
    # The archived presentation is the full saved one.
    blob = repo.db.execute("SELECT presentation FROM replay_evaluations").fetchone()[0]
    from revival_radar.storage.replay_archive import unpack

    saved = json.loads(unpack(blob))
    assert set(saved) >= {"token", "signal", "configuration"}


def test_window_width_is_configurable(tmp_path):
    repo, config = archive_world(tmp_path, replay_called_window_days=5)
    repo.prune_diagnostics(T0 + 30 * DAY, config)
    stamps = [r[0] for r in repo.db.execute("SELECT timestamp FROM replay_evaluations")]
    assert T0 + 4 * DAY in stamps


def test_retention_can_be_disabled_and_is_off_without_config(tmp_path):
    repo, config = archive_world(tmp_path)
    repo.prune_diagnostics(T0 + 30 * DAY, None)
    assert repo.db.execute("SELECT COUNT(*) FROM replay_evaluations").fetchone()[0] == 0
    (tmp_path / "x").mkdir()
    repo2, config2 = archive_world(tmp_path / "x")
    off = config2.model_copy(update={"replay_retention_enabled": False})
    repo2.prune_diagnostics(T0 + 30 * DAY, off)
    assert repo2.db.execute("SELECT COUNT(*) FROM replay_evaluations").fetchone()[0] == 0


def test_sample_is_deterministic_and_proportional():
    addresses = [f"addr{i:05d}" for i in range(4000)]
    first = [sampled("sol", a, 0.05) for a in addresses]
    assert first == [sampled("sol", a, 0.05) for a in addresses]
    assert 0.03 < sum(first) / len(first) < 0.07
    assert not any(sampled("sol", a, 0) for a in addresses)
    assert all(sampled("sol", a, 1) for a in addresses)
    # Raising the fraction only ever adds tokens: the sample is nested.
    wider = [sampled("sol", a, 0.2) for a in addresses]
    assert all(w for f, w in zip(first, wider, strict=True) if f)


def test_sampled_noncalled_token_is_kept_for_comparison(tmp_path):
    config = live_config(replay_sample_fraction=0.5)
    address = next(a for a in map(make_addr, range(300)) if sampled("sol", a, 0.5))
    repo = Repository(connect(tmp_path / "s.db"))
    seed_history(repo, address)
    step(repo, config, T0, address=address, call=False)
    repo.prune_diagnostics(T0 + 30 * DAY, config)
    row = repo.db.execute("SELECT reason FROM replay_evaluations").fetchone()
    assert row["reason"] == "sample"
    # A token outside the sample is dropped as before.
    miss = next(a for a in (make_addr(i, "M") for i in range(300)) if not sampled("sol", a, 0.5))
    seed_history(repo, miss)
    step(repo, config, T0 + 10, address=miss, call=False)
    repo.prune_diagnostics(T0 + 30 * DAY, config)
    assert repo.db.execute("SELECT COUNT(*) FROM replay_evaluations").fetchone()[0] == 1


def test_sample_budget_is_bounded(tmp_path):
    config = live_config(replay_sample_fraction=1, replay_sample_max_megabytes=0)
    repo = Repository(connect(tmp_path / "b.db"))
    seed_history(repo)
    step(repo, config, T0, call=False)
    repo.prune_diagnostics(T0 + 30 * DAY, config)
    assert repo.db.execute("SELECT COUNT(*) FROM replay_evaluations").fetchone()[0] == 0


def test_archive_is_append_only_and_existing_rows_are_not_rewritten(tmp_path):
    repo, config = archive_world(tmp_path)

    def digest():
        h = hashlib.sha256()
        for t in ("token_snapshots", "alerts", "alert_signals"):
            for row in repo.db.execute(f"SELECT * FROM {t} ORDER BY 1"):
                h.update(repr(tuple(row)).encode())
        return h.hexdigest()

    before = digest()
    repo.prune_diagnostics(T0 + 30 * DAY, config)
    assert digest() == before  # snapshots, alerts and signals are untouched
    archived = [tuple(r) for r in repo.db.execute("SELECT * FROM replay_evaluations ORDER BY id")]
    repo.prune_diagnostics(T0 + 30 * DAY, config)  # idempotent: nothing duplicated or rewritten
    assert [tuple(r) for r in repo.db.execute("SELECT * FROM replay_evaluations ORDER BY id")] == (
        archived
    )
    for statement in (
        "UPDATE replay_evaluations SET score=0",
        "DELETE FROM replay_evaluations",
        "UPDATE replay_candles SET payload=x''",
        "DELETE FROM replay_candles",
    ):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            repo.db.execute(statement)


def test_candles_used_are_archived_once(tmp_path):
    repo, config = build(tmp_path, steps=3)
    rows = repo.db.execute("SELECT COUNT(*) FROM replay_candles").fetchone()[0]
    assert rows == 1  # three evaluations, one identical series
    kind = f"kline:1h:{config.kline_lookback_hours}"
    entry = repo.cache_entry(kind, base_token(timestamp=T0))
    assert (
        archive_candles(repo.db, "sol", base_token().contract_address, kind, 1.0, entry["payload"])
        is False
    )
    # A candle-less evaluation archives nothing.
    repo2, _ = build(tmp_path, steps=2, candles=False, name="nocandle.db")
    assert repo2.db.execute("SELECT COUNT(*) FROM replay_candles").fetchone()[0] == 0


# ------------------------------------------------------------------- replay


def test_replay_reproduces_actual_calls_with_actual_config(tmp_path):
    repo, config = build(tmp_path, steps=3)
    out = run(copy_of(repo, tmp_path))
    actual = out["actual"]
    assert actual["actual_sent_calls"] == 1
    assert actual["matched"] == 1 and actual["missed"] == [] and actual["extra"] == []
    assert out["evaluations"] == 3
    assert out["evaluation_labels"] == {
        "faithful": 3,
        "partial_saved_structure": 0,
        "partial_legacy": 0,
    }
    agree = out["baseline_rescore_agreement"]["faithful"]
    assert agree == {"compared": 3, "agree": 3}
    call = out["periods"]["window"]["baseline_calls"][0]
    assert call["timestamp"] == T0 and call["label"] == "faithful"
    assert call["outcome"]["change_since_call_pct"] is not None
    assert out["periods"]["window"]["candidate"]["summary"]["calls"] == 1


def test_candidate_threshold_changes_calls(tmp_path):
    repo, _ = build(tmp_path, steps=3)
    out = run(copy_of(repo, tmp_path), candidate={"alert_score_threshold": 99})
    window = out["periods"]["window"]
    assert window["baseline"]["summary"]["calls"] == 1
    assert window["candidate"]["summary"]["calls"] == 0
    assert window["removed_by_candidate"]["summary"]["calls"] == 1


def test_candidate_stage_gate_filters_watch(tmp_path):
    # Stage thresholds above the demo score make the actual call a WATCH-stage call.
    config = live_config(
        early_revival_score_threshold=95,
        reviving_score_threshold=96,
        strong_revival_score_threshold=97,
        confirmed_revival_score_threshold=98,
    )
    repo, _ = build(tmp_path, config, steps=2)
    assert repo.db.execute("SELECT stage FROM alert_signals").fetchone()[0] == "WATCH"
    out = run(copy_of(repo, tmp_path), candidate={"alert_min_stage": "EARLY_REVIVAL"})
    assert out["actual"]["matched"] == 1
    window = out["periods"]["window"]
    assert window["baseline"]["summary"]["calls"] == 1
    assert window["candidate"]["summary"]["calls"] == 0
    assert window["removed_calls"][0]["stage"] == "WATCH"


def test_policy_gate_cooldown_and_new_evidence_units():
    config = live_config(alert_min_stage="EARLY_REVIVAL", realert_requires_new_evidence=True)
    policy = replay.Policy()
    key = ("sol", "X")
    watch = RevivalResult(score=80, status="WATCH", eligible=True)
    assert policy.decide(key, 0, 1.0, watch, config, None) == (False, "stage_below_minimum")
    early = RevivalResult(score=80, status="EARLY_REVIVAL", eligible=True)
    assert policy.decide(key, 0, 1.0, early, config, None) == (True, None)
    assert policy.decide(key, 60, 2.0, early, config, None)[1] == "cooldown"
    # Past the cooldown a repeat needs a higher price and new evidence.
    later = 2 * 3600
    assert (
        policy.decide(key, later, 1.0, early, config, None)[1] == "realert_blocked:price_not_higher"
    )
    assert (
        policy.decide(key, later, 2.0, early, config, None)[1] == "realert_blocked:no_new_evidence"
    )
    higher = RevivalResult(score=80, status="REVIVING", eligible=True)
    assert policy.decide(key, later, 2.0, higher, config, None) == (True, None)


def test_candidate_realert_gate_uses_new_evidence(tmp_path):
    config = live_config(alert_cooldown_hours=0.01, realert_score_increase=1)
    repo = Repository(connect(tmp_path / "live.db"))
    seed_history(repo)
    # The same score at a lower price: the cooldown lets a repeat through only if score rises.
    step(repo, config, T0)
    out = run(copy_of(repo, tmp_path), candidate={"realert_requires_new_evidence": True})
    assert out["periods"]["window"]["candidate"]["summary"]["calls"] == 1


def test_causality_future_snapshots_do_not_change_decisions(tmp_path):
    repo, _ = build(tmp_path, steps=3)
    before = run(copy_of(repo, tmp_path, "a.db"))
    decisions = [
        (c["timestamp"], c["score"], c["stage"])
        for c in before["periods"]["window"]["baseline_calls"]
    ]
    inputs_before = before["baseline_rescore_agreement"]
    # A wild snapshot after the last evaluation, outside the replayed window's evaluations.
    future = base_token(timestamp=T0 + 3 * DAY, volume_5m=1e12, volume_1h=1e12, tx_5m=10**9)
    repo.save_snapshot(future)
    repo.db.commit()
    after = run(copy_of(repo, tmp_path, "b.db"))
    assert decisions == [
        (c["timestamp"], c["score"], c["stage"])
        for c in after["periods"]["window"]["baseline_calls"]
    ]
    assert after["baseline_rescore_agreement"] == inputs_before


def test_history_reader_never_returns_snapshots_at_or_after_cutoff(tmp_path):
    repo, _ = build(tmp_path, steps=3)
    db = data.open_copy(copy_of(repo, tmp_path))
    inputs = replay.Inputs(db)
    token = base_token(timestamp=T0 + STEP)
    cutoff = T0 + STEP
    history = inputs.history(token, cutoff, 100)
    assert history and all(h.timestamp < cutoff for h in history)
    db.close()


def test_outcome_cutoff_bounds_forward_observations(tmp_path):
    repo, _ = build(tmp_path, steps=2)
    out = run(copy_of(repo, tmp_path), until=T0 + 700)
    call = out["periods"]["window"]["baseline_calls"][0]
    assert call["outcome_cutoff"] == data.iso(T0 + 700)
    full = run(copy_of(repo, tmp_path, "full.db"), until=T0 + 3600)
    assert (
        call["outcome"]["observations"]
        < full["periods"]["window"]["baseline_calls"][0]["outcome"]["observations"]
    )


def test_labels_faithful_partial_structure_and_legacy_are_separate(tmp_path):
    faithful, _ = build(tmp_path, steps=2)
    out = run(copy_of(faithful, tmp_path, "f.db"))
    assert out["evaluation_labels"]["faithful"] == 2
    assert out["periods"]["window"]["baseline"]["by_label"]["faithful"]["calls"] == 1
    # Candles not retained: the saved structure is reused and labelled partial.
    partial, _ = build(tmp_path, steps=2, name="p.db")
    partial.db.execute("DROP TRIGGER replay_candles_no_delete")
    partial.db.execute("DELETE FROM replay_candles")
    partial.db.commit()
    out = run(copy_of(partial, tmp_path, "p2.db"))
    assert out["evaluation_labels"] == {
        "faithful": 0,
        "partial_saved_structure": 2,
        "partial_legacy": 0,
    }
    by = out["periods"]["window"]["baseline"]["by_label"]
    assert by["partial_saved_structure"]["calls"] == 1 and by["faithful"]["calls"] == 0
    assert out["baseline_rescore_agreement"]["partial_saved_structure"]["agree"] == 2
    # Payload pruned: only recorded values remain.
    legacy, _ = build(tmp_path, steps=2, name="l.db")
    legacy.db.execute("DELETE FROM state WHERE key LIKE 'telegram:evaluation:%'")
    legacy.db.commit()
    out = run(copy_of(legacy, tmp_path, "l2.db"))
    assert out["evaluation_labels"]["partial_legacy"] == 2
    assert out["periods"]["window"]["baseline"]["by_label"]["partial_legacy"]["calls"] <= 1


def test_replay_reads_archived_evaluations_after_pruning(tmp_path):
    repo, config = build(tmp_path, steps=3)
    repo.prune_diagnostics(T0 + 30 * DAY, config)
    assert repo.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0
    out = run(copy_of(repo, tmp_path))
    assert out["evaluations"] == 3
    assert out["actual"]["matched"] == 1
    assert out["evaluation_labels"]["faithful"] == 3


def test_walk_forward_split_reports_periods_separately(tmp_path):
    repo, config = build(tmp_path, steps=3)
    seed_history(repo, OTHER, shift=3600)
    step(repo, config, T0 + 3600, address=OTHER)
    out = run(copy_of(repo, tmp_path), split=T0 + 3600, until=T0 + 4 * 3600)
    assert set(out["periods"]) == {"design", "holdout"}
    design, holdout = out["periods"]["design"], out["periods"]["holdout"]
    assert design["baseline"]["summary"]["calls"] == 1
    assert holdout["baseline"]["summary"]["calls"] == 1
    assert design["outcome_cutoff"] == data.iso(
        T0 + 3600
    )  # design outcomes are purged at the split
    assert holdout["outcome_cutoff"] == data.iso(T0 + 4 * 3600)
    text = replay.render(out)
    assert "Do NOT tune live settings on the holdout" in text
    with pytest.raises(data.ResearchError):
        run(copy_of(repo, tmp_path, "z.db"), split=T0 - 100)


def test_render_labels_every_fidelity_class(tmp_path):
    repo, _ = build(tmp_path, steps=2)
    text = replay.render(run(copy_of(repo, tmp_path)))
    for label in replay.LABEL_TEXT.values():
        assert label in text


# ---------------------------------------------------------------------- cli


def test_cli_refuses_live_database_and_leaves_copy_untouched(tmp_path, monkeypatch, capsys):
    repo, _ = build(tmp_path, steps=2)
    copy = copy_of(repo, tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(copy))
    args = ["replay", "--db", str(copy), "--since", str(T0 - 10), "--until", str(T0 + 3600)]
    assert cli.main([*args, "--config", "{}"]) == 2
    assert "live database" in capsys.readouterr().err
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "other-live.db"))
    before = file_hash(copy)
    out = tmp_path / "out.md"
    assert cli.main([*args, "--config", '{"alert_score_threshold": 90}', "--out", str(out)]) == 0
    assert file_hash(copy) == before
    assert not list(tmp_path.glob("copy.db-*"))
    assert "Point-in-time replay" in out.read_text()


def test_cli_rejects_bad_candidate_config(tmp_path, monkeypatch, capsys):
    repo, _ = build(tmp_path, steps=1)
    copy = copy_of(repo, tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "other.db"))
    base = ["replay", "--db", str(copy), "--since", str(T0 - 10), "--until", str(T0 + 60)]
    assert cli.main([*base, "--config", '{"no_such_setting": 1}']) == 2
    assert cli.main([*base, "--config", '{"alert_min_stage": "NOPE"}']) == 2
    assert cli.main([*base, "--config", "not json"]) == 2
    capsys.readouterr()


def test_connection_is_read_only(tmp_path):
    repo, _ = build(tmp_path, steps=1)
    db = data.open_copy(copy_of(repo, tmp_path))
    with pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM alerts")
    db.close()

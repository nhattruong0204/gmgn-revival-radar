"""Research export and report (#71) on a fixture database with hand-checkable values."""

import hashlib
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

from revival_radar.research import cli, data, export, report
from revival_radar.research.stats import ranks, spearman
from revival_radar.storage.database import SCHEMA_VERSION, connect

GOLDEN = Path(__file__).parent / "fixtures" / "research"
B0 = 1_799_971_200  # a 6h bucket boundary: 83332 * 21600
H = 3600
UNTIL = B0 + 48 * H
CHAIN = "sol"
TOKENS = {name: name * 4 + "0" * 28 for name in ("TA", "TB", "TC", "TD", "TE")}


def at(hours):
    return B0 + hours * H


def window(baseline=B0):
    return data.Window(B0, UNTIL, baseline)


def series(spec, last=48):
    """{hour: market cap}: a step function; a spec of (from_hour, value) pairs."""
    out, value = {}, None
    steps = dict(spec)
    for h in range(last + 1):
        value = steps.get(h, value)
        out[h] = value
    return out


# TA: called at h1 (1,000,000) and h8 (800,000)
TA = {0: 1_000_000, 1: 1_000_000, 2: 1_100_000, 3: 900_000, 4: 750_000, 5: 800_000, 9: 880_000}
TC = {0: 250_000, 2: 300_000, 3: 400_000, 4: 500_000, 5: 750_000}  # a run-up, then called at h4


def state_payload(call_id, token, ts, score, status, symbol, **features):
    return json.dumps(
        {
            "token": {
                "chain": CHAIN,
                "contract_address": token,
                "timestamp": ts,
                "symbol": symbol,
                "price_change_1h": features.get("pc1h"),
                "security": {"bundler_ratio": features.get("bundler")},
            },
            "signal": {
                "score": score,
                "status": status,
                "score_version": "setup-trigger-confirmation-v1",
                "structure": {"base_duration_hours": features.get("base")},
                "acceleration": {"volume_acceleration_5m": features.get("accel")},
            },
        }
    )


def build(path: Path) -> Path:
    connect(path).close()
    db = sqlite3.connect(path)

    def snapshots(token, steps, last, drawdown=None, age=None):
        for h, mc in series(steps, last).items():
            db.execute(
                "INSERT INTO token_snapshots(chain,contract_address,timestamp,symbol,market_cap,"
                "price,drawdown_from_ath,token_age_seconds,payload) VALUES(?,?,?,?,?,?,?,?,'{}')",
                (CHAIN, token, at(h), token[:2], mc, mc / 1e9, drawdown, age),
            )

    snapshots(TOKENS["TA"], TA, 48, 0.6, 5 * 86400)
    snapshots(TOKENS["TB"], {0: 2_000_000}, 48, None, 5 * 86400)  # unknown drawdown
    snapshots(TOKENS["TC"], TC, 48, 0.6, 5 * 86400)  # starts under the $300K floor
    snapshots(TOKENS["TD"], {0: 400_000}, 3, 0.6, 5 * 86400)  # tracked only 3 hours
    snapshots(TOKENS["TE"], {0: 1_000_000, 10: 1_600_000, 11: 1_200_000}, 30, 0.5, 4 * 86400)
    for h in range(48):
        db.execute(
            "INSERT INTO scan_runs(id,started,finished,potential_alerts,errors,source_errors)"
            " VALUES(?,?,?,?,?,'{}')",
            (h + 1, at(h), at(h) + 30, 1 if h in (1, 4, 8) else 0, 1 if h == 20 else 0),
        )

    def evaluate(token, hours, score, eligible, reasons="[]"):
        for h in hours:
            db.execute(
                "INSERT INTO evaluations(scan_id,timestamp,chain,contract_address,symbol,score,"
                "status,eligible,rejection_reasons,missing_fields,warnings,asset_type,"
                "discovery_sources) VALUES(?,?,?,?,?,?,?,?,?,'[]','[]','token','[]')",
                (h + 1, at(h), CHAIN, token, token[:2], score, "WATCH", eligible, reasons),
            )

    evaluate(TOKENS["TA"], range(48), 45, 1)
    evaluate(TOKENS["TB"], range(12), 30, 1)
    evaluate(TOKENS["TC"], range(12), 62, 1)
    evaluate(TOKENS["TD"], range(12), 10, 0)
    evaluate(TOKENS["TD"], [30], 10, 0)  # no snapshot near it
    evaluate(TOKENS["TE"], range(12), 12, 0, '["no_base: short", "no_returning_activity"]')

    calls = [
        (1, "TA", 1, 45, 1_000_000, 0.001, "WATCH", (60, 40, 30), 9, "AAA",
         dict(base=10, bundler=0.1, pc1h=5.0, accel=1.2)),
        (2, "TA", 8, 55, 800_000, 0.0008, "EARLY_REVIVAL", (70, 50, 40), 9, "AAA",
         dict(base=12, bundler=0.2, pc1h=-3.0, accel=0.8)),
        (3, "TB", 2, 35, None, None, "WATCH", (None, None, None), None, "BBB", {}),
        (4, "TC", 4, 65, 500_000, 0.0005, "EARLY_REVIVAL", (50, 70, 20), 10, "CCC",
         dict(base=6, bundler=0.05, pc1h=20.0, accel=2.5)),
    ]  # fmt: skip
    for cid, tok, hour, score, mc, price, stage, parts, rev, sym, feat in calls:
        token, ts = TOKENS[tok], at(hour)
        db.execute(
            "INSERT INTO alerts(id,timestamp,chain,contract_address,score,reason,delivery_status)"
            " VALUES(?,?,?,?,?,'[]','sent')",
            (cid, ts, CHAIN, token, score),
        )
        db.execute(
            "INSERT INTO alert_signals VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, price, mc, score, *parts, stage, "Custom" if rev else None, rev),
        )
        db.execute(
            "INSERT INTO state(key,value,updated_at) VALUES(?,?,?)",
            (f"telegram:alert:{cid}", state_payload(cid, token, ts, score, stage, sym, **feat), ts),
        )
    # one saved +1h checkpoint for call 1; others stay missed or pending
    db.execute(
        "INSERT INTO signal_outcomes VALUES(1,1,?,?,0.0011,1100000,10.0)",
        (at(1) + H, at(1) + H + 120),
    )
    db.commit()
    db.close()
    return path


@pytest.fixture
def copy(tmp_path):
    """A copy made the recommended way (VACUUM INTO), in a directory of its own."""
    source = build(tmp_path / "source.db")
    target = tmp_path / "copies" / "copy.db"
    target.parent.mkdir()
    db = sqlite3.connect(source)
    db.execute("VACUUM INTO ?", (str(target),))
    db.close()
    return target


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_ro(path):
    return data.open_copy(path)


def golden(name, text):
    target = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    assert text == target.read_text(encoding="utf-8"), f"{name} differs from the golden file"


# --- golden files -------------------------------------------------------------------------


def test_export_matches_golden_files(copy, tmp_path):
    db = read_ro(copy)
    manifest = export.export(db, copy, window(), tmp_path / "out", hash_input=True)
    db.close()
    for name in ("calls.csv", "checkpoints.csv", "funnel.csv", "cohorts.csv"):
        golden(name, (tmp_path / "out" / name).read_text(encoding="utf-8"))
    assert manifest["row_counts"] == {
        "calls.csv": 4,
        "checkpoints.csv": 16,
        "funnel.csv": 2,
        "cohorts.csv": manifest["row_counts"]["cohorts.csv"],
    }
    saved = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert saved["quick_check"] == "ok" and saved["schema_version"] == SCHEMA_VERSION
    assert saved["inputs"]["database_sha256"] == file_hash(copy)
    assert saved["cutoffs"]["until"] == UNTIL and saved["cutoffs"]["since"] == B0
    assert set(saved["definitions"]) >= {"unknown", "cohorts", "change_since_call_pct"}
    assert saved["outputs"]["calls.csv"] == file_hash(tmp_path / "out" / "calls.csv")
    assert "code_version" in saved


def test_report_matches_golden_file(copy):
    db = read_ro(copy)
    text = report.build_report(db, window())
    db.close()
    golden("report.md", text)


# --- hand-checked numbers ---------------------------------------------------------------


def by_id(calls):
    return {c["call_id"]: c for c in calls}


def test_call_values_are_exact(copy):
    db = read_ro(copy)
    calls = by_id(data.call_table(db, window()))
    db.close()
    one, two, three, four = (calls[i] for i in (1, 2, 3, 4))
    assert one["change_since_call_pct"] == pytest.approx(-12.0)
    assert (one["mfe_pct"], one["mae_pct"]) == (pytest.approx(10.0), pytest.approx(-25.0))
    assert one["observations"] == 47 and one["token_call_index"] == 1
    assert two["change_since_call_pct"] == pytest.approx(10.0)
    assert two["mfe_pct"] == pytest.approx(10.0) and two["mae_pct"] == pytest.approx(10.0)
    assert (two["token_call_index"], two["token_call_count"]) == (2, 2)
    assert four["change_since_call_pct"] == pytest.approx(50.0)
    assert four["rise_24h_before_call_pct"] == pytest.approx(100.0)
    assert four["runup_24h_before_call_pct"] == pytest.approx(100.0)
    assert two["runup_24h_before_call_pct"] == pytest.approx(10.0)
    assert (one["symbol"], one["stage"], one["preset"], one["revision"]) == (
        "AAA",
        "WATCH",
        "Custom",
        9,
    )
    assert one["score_version"] == "setup-trigger-confirmation-v1"
    assert (one["base_duration_hours"], one["bundler_ratio"]) == (10, 0.1)
    assert three["symbol"] == "BBB"


def test_unknown_is_never_zero(copy, tmp_path):
    db = read_ro(copy)
    calls = by_id(data.call_table(db, window()))
    three = calls[3]
    assert three["change_since_call_pct"] is None and three["mfe_pct"] is None
    assert three["change_reason"] == "no_call_mc"
    assert three["call_market_cap"] is None and three["bundler_ratio"] is None
    text = report.build_report(db, window())
    db.close()
    # 3 of 4 calls have a known change; the unknown one is excluded from every rate
    assert (
        "| Calls with a known change since call | 3 (unknown, excluded: 1; no_call_mc: 1) |" in text
    )
    assert "| Positive since call | 2 of 3 (66.7%) |" in text
    assert "| Negative since call | 1 of 3 (33.3%) |" in text
    assert "| Median change since call | +10.0% |" in text
    assert "| Mean change since call | +16.0% |" in text
    assert "| Calls with MFE >= +25% | 1 of 3 (33.3%) |" in text
    assert "| Calls with MFE >= +50% | 1 of 3 (33.3%) |" in text
    assert "| Median observed MFE | +10.0% (n=3) |" in text
    # CSV: unknown is an empty cell with its reason, not 0
    out = tmp_path / "o"
    db = read_ro(copy)
    export.export(db, copy, window(), out)
    db.close()
    import csv

    row = next(r for r in csv.DictReader((out / "calls.csv").open()) if r["call_id"] == "3")
    assert row["change_since_call_pct"] == "" and row["call_market_cap"] == ""
    assert row["change_reason"] == "no_call_mc" and row["mfe_pct"] == ""
    # rank correlation drops the unknown pair and shows n
    assert "| Overall score | 3 | +1.00 |" in text
    assert "| Bundler ratio | 3 | -0.50 |" in text
    assert "| Setup score | 3 | " in text
    assert "1 of 3 (33.3%) of calls with saved pre-call snapshots" in text
    assert "(1 without pre-call snapshots, excluded)" in text


def test_universe_excludes_unknown_and_short_tracking(copy):
    db = read_ro(copy)
    universe = {u["contract_address"][:2]: u for u in data.revival_universe(db, window())}
    db.close()
    assert set(universe) == {"TA", "TE"}  # TB unknown drawdown, TC < $300K, TD tracked 3h
    te = universe["TE"]
    assert te["mfe_24h_pct"] == pytest.approx(60.0) and te["hours_to_peak"] == 10.0
    assert te["called"] is False and universe["TA"]["called"] is True
    assert te["max_score_to_peak"] == 12 and te["eligible_by_peak"] is False
    assert te["top_rejection_reasons"] == [("no_base", 11), ("no_returning_activity", 11)]


def test_funnel_counts(copy):
    db = read_ro(copy)
    days, summary = data.funnel(db, window())
    db.close()
    assert summary == {
        "scans_completed": 48,
        "scans_with_source_error": 1,
        "evaluations": 48 + 12 + 12 + 13 + 12,
        "distinct_tokens": 5,
        "eligible": 48 + 12 + 12,
        "score_ge_60": 12,
        "score_ge_60_eligible": 12,
        "potential_alerts": 3,
        "sent_calls": 4,
        "distinct_tokens_called": 3,
    }
    assert sum(d["evaluations"] for d in days) == summary["evaluations"]
    assert sum(d["sent_calls"] for d in days) == 4


# --- cohort sampling rule -----------------------------------------------------------------


def test_cohort_one_sample_per_token_cohort_bucket(copy):
    db = read_ro(copy)
    samples = data.cohort_samples(db, window())
    db.close()
    ta = [s for s in samples if s["contract_address"][:2] == "TA" and s["horizon_hours"] == 6]
    # TA is evaluated hourly for 48h (8 buckets). Bucket 0 holds a call at h1, bucket 1 a
    # call at h8; each bucket has exactly one "B" sample (its first evaluation) and, where
    # called, exactly one "A" sample at the call. Nothing is sampled twice.
    keys = [(s["cohort"], s["bucket_start"]) for s in ta]
    assert len(keys) == len(set(keys))
    assert sum(s["cohort"] == "B" for s in ta) == 8
    a = [s for s in ta if s["cohort"] == "A"]
    assert [s["sampled_at"] for s in a] == [data.iso(at(1)), data.iso(at(8))]
    first_b = next(s for s in ta if s["cohort"] == "B")
    assert first_b["sampled_at"] == data.iso(at(0))  # the first evaluation, not a later one
    # forward arithmetic on known snapshots (bucket 0, first evaluation, base 1,000,000)
    assert first_b["coverage"] == "ok" and first_b["change_pct"] == pytest.approx(-20.0)
    assert first_b["mfe_pct"] == pytest.approx(10.0) and first_b["mae_pct"] == pytest.approx(-25.0)
    a1 = a[0]
    assert a1["change_pct"] == pytest.approx(-20.0)  # h1 -> h7: 800,000 / 1,000,000
    day = [s for s in samples if s["contract_address"][:2] == "TA" and s["horizon_hours"] == 24]
    assert next(s for s in day if s["cohort"] == "B")["change_pct"] == pytest.approx(-12.0)
    # eligible, score >= 60, not called -> E, never mixed into A
    assert {s["cohort"] for s in samples if s["contract_address"][:2] == "TC"} == {"A", "E"}
    assert {s["cohort"] for s in samples if s["contract_address"][:2] == "TB"} == {"A", "C"}


def test_cohort_coverage_statuses(copy):
    db = read_ro(copy)
    samples = data.cohort_samples(db, window())
    db.close()
    td = [s for s in samples if s["contract_address"][:2] == "TD"]
    status = {(s["sampled_at"], s["horizon_hours"]): s["coverage"] for s in td}
    # h0: base exists but tracking stopped at h3 -> no later snapshot: missing, not 0
    assert status[(data.iso(at(0)), 6)] == "missing"
    assert status[(data.iso(at(6)), 6)] == "no_base"  # nothing within 600s of h6
    assert status[(data.iso(at(30)), 24)] == "no_base"
    assert all(s["change_pct"] is None for s in td)
    # a horizon past the cutoff is pending
    ta = [s for s in samples if s["contract_address"][:2] == "TA" and s["cohort"] == "B"]
    late = next(s for s in ta if s["sampled_at"] == data.iso(at(36)) and s["horizon_hours"] == 24)
    assert late["coverage"] == "pending" and late["change_pct"] is None


# --- Spearman -----------------------------------------------------------------------------


def test_spearman_known_examples():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == (pytest.approx(1.0), 4)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == (pytest.approx(-1.0), 4)
    # ties get average ranks: rx = 1, 2.5, 2.5, 4 -> rho = 4.5 / sqrt(4.5 * 5)
    assert ranks([1, 2, 2, 3]) == [1.0, 2.5, 2.5, 4.0]
    rho, n = spearman([1, 2, 2, 3], [1, 2, 3, 4])
    assert n == 4 and rho == pytest.approx(0.9486832980505138)
    # rank (not value) based: a monotone outlier does not change it
    assert spearman([1, 2, 3, 4], [1, 2, 3, 1000])[0] == pytest.approx(1.0)
    # unknown pairs are dropped and n counts only the pairs used
    assert spearman([1, None, 3, 4, float("nan")], [2, 5, 6, None, 1]) == (None, 2)
    rho, n = spearman([1, None, 2, 3, 4], [1, 9, 2, 3, None])
    assert (round(rho, 6), n) == (1.0, 3)
    assert spearman([1, 1, 1], [1, 2, 3]) == (None, 3)  # constant: undefined, not 0
    assert spearman([1, 2], [1, 2]) == (None, 2)


# --- read-only and live-path safety --------------------------------------------------------


def test_database_file_is_unchanged_after_running(copy, tmp_path, capsys):
    before = file_hash(copy)
    siblings = set(os.listdir(copy.parent))
    args = ["--db", str(copy), "--since", str(B0), "--until", str(UNTIL)]
    assert cli.main(["report", *args, "--out", str(tmp_path / "r.md")]) == 0
    assert cli.main(["export", *args, "--out", str(tmp_path / "x")]) == 0
    assert file_hash(copy) == before
    assert set(os.listdir(copy.parent)) == siblings == {"copy.db"}  # no -wal/-shm/-journal


def test_wal_mode_copy_file_is_unchanged_too(tmp_path):
    wal = build(tmp_path / "wal.db")  # a plain file copy of a WAL database
    before = file_hash(wal)
    args = ["--db", str(wal), "--since", str(B0), "--until", str(UNTIL)]
    assert cli.main(["report", *args, "--out", str(tmp_path / "r.md")]) == 0
    assert file_hash(wal) == before


def test_connection_cannot_write(copy):
    db = data.open_copy(copy)
    with pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM alerts")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("CREATE TABLE x(a)")
    db.close()


def test_refuses_the_configured_live_database(copy, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", str(copy))
    args = ["--db", str(copy), "--since", str(B0), "--until", str(UNTIL)]
    assert cli.main(["report", *args]) == 2
    err = capsys.readouterr().err
    assert "Refusing to run" in err and "VACUUM INTO" in err
    assert cli.main(["export", *args, "--out", str(tmp_path / "x")]) == 2
    assert not (tmp_path / "x").exists()
    # a symlink or relative spelling of the same file is refused too
    link = tmp_path / "alias.db"
    link.symlink_to(copy)
    assert cli.main(["report", "--db", str(link), *args[2:]]) == 2
    # the explicit acknowledgement is required, and then it runs
    assert cli.main(["report", *args, "--i-know-this-is-a-copy", "--out", str(tmp_path / "r")]) == 0


def test_default_live_path_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    live = tmp_path / "data" / "revival_radar.db"
    live.parent.mkdir()
    build(live)
    assert data.live_database_path().resolve() == live.resolve()
    with pytest.raises(data.ResearchError, match="live database"):
        data.refuse_live(live, i_know_this_is_a_copy=False)
    data.refuse_live(live, i_know_this_is_a_copy=True)


def test_help_recommends_vacuum_into():
    assert "VACUUM INTO" in cli.build_parser().format_help()


def test_missing_and_foreign_databases_are_clear_errors(tmp_path, capsys):
    assert (
        cli.main(["report", "--db", str(tmp_path / "no.db"), "--since", "1", "--until", "2"]) == 2
    )
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t(a)").connection.close()
    assert cli.main(["report", "--db", str(other), "--since", "1", "--until", "2"]) == 2
    assert "missing tables" in capsys.readouterr().err
    assert cli.main(["report", "--db", str(other), "--since", "5", "--until", "2"]) == 2


def test_time_parsing():
    assert data.parse_time("2026-10-07T13:36:00Z") == 1791380160.0
    assert data.parse_time("2026-10-07 13:36:00") == 1791380160.0
    assert data.parse_time("2026-10-07T20:36:00+07:00") == 1791380160.0
    assert data.parse_time("1791380160") == 1791380160.0
    with pytest.raises(data.ResearchError):
        data.parse_time("yesterday")


# --- agreement with the independent parity script -----------------------------------------


def test_matches_the_independent_parity_recomputation(copy):
    spec = importlib.util.spec_from_file_location(
        "verify_call_parity", Path(__file__).parents[1] / "scripts/verify_call_parity.py"
    )
    parity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parity)
    check = parity.open_readonly(copy)
    expected = parity.expected_calls(check, UNTIL)
    check.close()
    db = read_ro(copy)
    calls = by_id(data.call_table(db, window()))
    db.close()
    assert set(calls) == set(expected)
    for call_id, mine in calls.items():
        theirs = expected[call_id]
        for ours, other in (
            ("change_since_call_pct", "change_since_call_pct"),
            ("mfe_pct", "mfe_pct"),
            ("mae_pct", "mae_pct"),
            ("latest_market_cap", "latest_market_cap"),
            ("observations", "observations"),
            ("max_gap_seconds", "max_gap_seconds"),
            ("token_call_index", "token_call_index"),
            ("token_call_count", "token_call_count"),
        ):
            if theirs[other] is None:
                assert mine[ours] is None
            else:
                assert mine[ours] == pytest.approx(theirs[other])


def test_checkpoints_reuse_shared_accounting(copy):
    db = read_ro(copy)
    rows = data.checkpoint_table(db, window())
    db.close()
    one = [r for r in rows if r["call_id"] == 1]
    states = {r["horizon_hours"]: r["state"] for r in one}
    assert states == {1: "return", 6: "missed", 24: "missed", 72: "pending"}
    assert next(r for r in one if r["horizon_hours"] == 1)["price_change_pct"] == 10.0
    assert all(r["price_change_pct"] is None for r in one if r["horizon_hours"] != 1)

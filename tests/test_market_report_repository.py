import json
from types import SimpleNamespace

import pytest

from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository

from .conftest import changed


def observe(repo, token, config, timestamp, score, *, finished=True, finish_at=None, **values):
    configuration = values.pop("configuration", {"revision": 9, "settings": {}})
    scan = repo.begin_scan(timestamp - 1, configuration)
    signal = RevivalResult(
        score=score,
        status="WATCH",
        eligible=False,
        returning_activity=True,
        structure=Structure(available=True, base_detected=True),
        **values,
    )
    repo.record_evaluation(scan, changed(token, timestamp=timestamp), signal, config, configuration)
    if finished:
        repo.finish_scan(scan, SimpleNamespace(processed=1), finish_at or timestamp + 1)
    return repo.db.execute("SELECT id FROM evaluations WHERE scan_id=?", (scan,)).fetchone()[0]


def test_market_report_peaks_latest_distinct_identity_without_threshold(repo, token, config):
    config.alert_score_threshold = 100
    peak = observe(repo, token, config, 110, 70, confirmation_score=25)
    latest = observe(repo, token, config, 150, 40, confirmation_score=5)
    second = changed(token, contract_address="B" * 32)
    observe(repo, second, config, 160, 60)
    report = repo.market_report(100, now=200)
    assert report["evaluations"] == 3 and report["unique_tokens"] == 2
    assert report["entries"][0]["contract_address"] == token.contract_address
    entry = report["entries"][0]
    assert entry["observations"] == 2
    assert entry["peak"]["score"] == 70 and entry["peak"]["id"] == peak
    assert entry["latest"]["score"] == 40 and entry["latest"]["id"] == latest
    assert "score_below_threshold" in entry["latest"]["rejection_reasons"]
    assert entry["peak"]["eligible"] is False
    assert report["completed_scans"] == 3


def test_report_excludes_other_chains_future_incomplete_and_before_boundary(repo, token, config):
    observe(repo, token, config, 100, 100)  # Lower boundary is exclusive.
    observe(repo, token, config, 120, 95, finished=False)
    observe(repo, token, config, 130, 90, finish_at=210)  # Unfinished at cutoff.
    observe(repo, token, config, 201, 100)
    bsc = changed(token, chain="bsc", contract_address="0x" + "a" * 40)
    observe(repo, bsc, config, 140, 99)
    valid = observe(repo, token, config, 190, 50)
    report = repo.market_report(100, now=200)
    assert report["evaluations"] == 1
    assert report["entries"][0]["peak"]["id"] == valid
    # The scan starting at exactly the cutoff also had not completed by then.
    assert report["incomplete_scans"] == 3
    assert report["first_observation"] == 190


def test_report_scan_started_before_window_with_observation_inside(repo, token, config):
    identity = observe(repo, token, config, 120, 55)
    with repo.db:
        repo.db.execute("UPDATE scan_runs SET started=90 WHERE id=1")
    report = repo.market_report(100, now=200)
    assert report["entries"][0]["peak"]["id"] == identity
    assert report["completed_scans"] == 1


def test_report_ties_use_confirmation_trigger_then_time_and_cap_ten(repo, token, config):
    addresses = [character * 32 for character in "ABCDEFGHJKLM"]
    for index, address in enumerate(addresses):
        observe(
            repo,
            changed(token, contract_address=address),
            config,
            110 + index,
            70,
            confirmation_score=40 if index < 2 else 10,
            trigger_score=70 if index == 0 else 60,
        )
    peak = observe(
        repo,
        changed(token, contract_address=addresses[0]),
        config,
        140,
        70,
        confirmation_score=40,
        trigger_score=70,
    )
    report = repo.market_report(100, now=200)
    assert len(report["entries"]) == 10 and report["unique_tokens"] == 12
    assert report["entries"][0]["contract_address"] == addresses[0]
    assert report["entries"][0]["peak"]["id"] == peak
    assert report["entries"][1]["contract_address"] == addresses[1]
    assert report["entries"][2]["contract_address"] == addresses[-1]


@pytest.mark.parametrize("replacement", [None, "broken", "[]", '{"token":{},"signal":{}}'])
def test_report_missing_or_invalid_legacy_detail_remains_visible(repo, token, config, replacement):
    identity = observe(repo, token, config, 120, 70)
    if replacement is None:
        with repo.db:
            repo.db.execute("DELETE FROM state WHERE key=?", (f"telegram:evaluation:{identity}",))
    else:
        repo.set_state(f"telegram:evaluation:{identity}", replacement)
    report = repo.market_report(100, now=200)
    saved = report["entries"][0]["peak"]
    assert saved["score"] == 70
    assert saved["token"] is None and saved["signal"] is None
    assert "Saved detail evidence unavailable" in saved["warnings"]


def test_report_detail_identity_and_score_mismatch_not_used(repo, token, config):
    identity = observe(repo, token, config, 120, 70)
    detail = json.loads(repo.get_state(f"telegram:evaluation:{identity}"))
    detail["token"]["contract_address"] = "B" * 32
    detail["signal"]["score"] = 99
    repo.set_state(f"telegram:evaluation:{identity}", json.dumps(detail))
    evidence = repo.market_report(100, now=200)["entries"][0]["peak"]
    assert evidence["score"] == 70 and evidence["token"] is None


def test_report_latest_known_danger_preserved_even_if_peak_clean(repo, token, config):
    observe(repo, token, config, 110, 90)
    security = token.security.model_copy(update={"dangerous": True, "flags": ["known risk"]})
    observe(repo, changed(token, security=security), config, 150, 50)
    latest = repo.market_report(100, now=200)["entries"][0]["latest"]
    assert latest["token"]["security"]["dangerous"] is True
    assert "security_dangerous" in latest["rejection_reasons"]
    assert "known risk" in latest["token"]["security"]["flags"]


def test_report_mixed_config_and_legacy_unknown_without_secret_context(repo, token, config):
    observe(repo, token, config, 110, 70, configuration={"revision": 8, "settings": {}})
    identity = observe(
        repo,
        token,
        config,
        150,
        60,
        configuration={"revision": 9, "secret": "hidden", "settings": {"gmgn_api_key": "hidden"}},
    )
    report = repo.market_report(100, now=200)
    assert report["multiple_configurations"]
    assert "hidden" not in json.dumps(report)
    with repo.db:
        repo.db.execute("DELETE FROM state WHERE key=?", (f"telegram:evaluation:{identity}",))
    assert repo.market_report(100, now=200)["multiple_configurations"]


@pytest.mark.parametrize(
    "since,until,limit",
    [
        (200, 200, 10),
        (300, 200, 10),
        (float("nan"), 200, 10),
        (0, float("inf"), 10),
        (0, 7 * 86400 + 1, 10),
        (100, 200, 11),
        (100, 200, True),
    ],
)
def test_report_window_and_limit_validation(repo, since, until, limit):
    with pytest.raises(ValueError):
        repo.market_report(since, now=until, limit=limit)
    assert not repo.db.in_transaction


def test_empty_market_report_is_explicit_and_read_only(repo):
    before = repo.db.total_changes
    report = repo.market_report(100, now=200)
    assert report["entries"] == [] and report["evaluations"] == report["unique_tokens"] == 0
    assert report["first_observation"] is report["last_finished"] is None
    assert repo.db.total_changes == before
    assert not repo.db.in_transaction


def test_market_report_claim_delivery_durable_across_connections_and_restart(tmp_path):
    path = tmp_path / "report.db"
    first, second = connect(path), connect(path)
    a, b = Repository(first), Repository(second)
    assert a.claim_market_report("Asia/Bangkok:14400", 15000)
    assert not b.claim_market_report("Asia/Bangkok:14400", 15001)
    a.finish_market_report("Asia/Bangkok:14400", "sent", 123, 15002)
    assert json.loads(b.get_state("market_report:Asia/Bangkok:14400"))["message_id"] == 123
    assert json.loads(b.get_state("market_report_delivery"))["status"] == "sent"
    first.close()
    second.close()
    reopened = connect(path)
    repo = Repository(reopened)
    assert not repo.claim_market_report("Asia/Bangkok:14400", 15003)
    assert repo.claim_market_report("Asia/Bangkok:28800", 28801)
    assert repo.db.execute("PRAGMA user_version").fetchone()[0] == 4
    reopened.close()


def test_prune_market_report_snapshots_and_claims_without_removing_latest_status(repo):
    repo.claim_market_report("old", 90)
    repo.finish_market_report("old", "unknown", timestamp=90)
    repo.set_state("market_report:snapshot:old", "{}", 90)
    repo.claim_market_report("new", 110)
    repo.prune_diagnostics(100)
    assert repo.get_state("market_report:old") is None
    assert repo.get_state("market_report:snapshot:old") is None
    assert repo.get_state("market_report:new") is not None
    assert repo.get_state("market_report_delivery") is not None


@pytest.mark.parametrize("corruption", ["address", "timestamp", "score", "status", "eligible"])
def test_invalid_details_cannot_win_dimension_ties(repo, token, config, corruption):
    good = observe(repo, token, config, 110, 70, confirmation_score=50, trigger_score=20)
    bad = observe(repo, token, config, 120, 70, confirmation_score=100, trigger_score=100)
    other = changed(token, contract_address="B" * 32)
    competing = observe(repo, other, config, 130, 70, confirmation_score=40, trigger_score=90)
    detail = json.loads(repo.get_state(f"telegram:evaluation:{bad}"))
    if corruption == "address":
        detail["token"]["contract_address"] = other.contract_address
    elif corruption == "timestamp":
        detail["token"]["timestamp"] = 121
    elif corruption == "score":
        detail["signal"]["score"] = 99
    elif corruption == "status":
        detail["signal"]["status"] = "STRONG_REVIVAL"
    else:
        detail["signal"]["eligible"] = True
    repo.set_state(f"telegram:evaluation:{bad}", json.dumps(detail))
    report = repo.market_report(100, now=200)
    assert report["entries"][0]["peak"]["id"] == good
    assert report["entries"][0]["latest"]["id"] == bad
    assert report["entries"][0]["latest"]["signal"] is None
    assert report["entries"][1]["peak"]["id"] == competing


@pytest.mark.parametrize("value", [True, "100", [], {"value": 100}])
def test_invalid_raw_dimensions_are_unknown_for_ties(repo, token, config, value):
    good = observe(repo, token, config, 110, 70, confirmation_score=1)
    bad = observe(repo, token, config, 120, 70, confirmation_score=100)
    detail = json.loads(repo.get_state(f"telegram:evaluation:{bad}"))
    detail["signal"]["confirmation_score"] = value
    repo.set_state(f"telegram:evaluation:{bad}", json.dumps(detail))
    entry = repo.market_report(100, now=200)["entries"][0]
    assert entry["peak"]["id"] == good and entry["latest"]["signal"] is None


@pytest.mark.parametrize(
    "section,field", [("acceleration", "volume_ratio_5m"), ("structure", "base_duration_hours")]
)
@pytest.mark.parametrize("value", ["Infinity", "NaN", float("nan"), float("inf")])
def test_nonfinite_saved_signals_become_unknown_without_poisoning_snapshot(
    repo, token, config, section, field, value
):
    identity = observe(repo, token, config, 120, 70, confirmation_score=100)
    detail = json.loads(repo.get_state(f"telegram:evaluation:{identity}"))
    detail["signal"][section][field] = value
    repo.set_state(f"telegram:evaluation:{identity}", json.dumps(detail))
    report = repo.market_report(100, now=200)
    observation = report["entries"][0]["peak"]
    assert observation["score"] == 70 and observation["signal"] is None
    assert "Saved detail evidence unavailable" in observation["warnings"]
    json.dumps(report, allow_nan=False)


def test_malformed_context_is_omitted_and_frozen_report_remains_finite(repo, token, config):
    observe(
        repo,
        token,
        config,
        120,
        70,
        configuration={
            "preset": {"secret": "private"},
            "revision": float("inf"),
            "settings": {
                "history_max_gap_seconds": float("nan"),
                "alert_score_threshold": {"secret": "private"},
                "helius_api_token": "private",
            },
        },
    )
    report = repo.market_report(100, now=200)
    observation = report["entries"][0]["peak"]
    assert observation["signal"] is not None
    assert observation["configuration"] == {"preset": None, "revision": None, "settings": {}}
    frozen = json.dumps(report, allow_nan=False)
    assert "private" not in frozen


def test_open_scan_started_before_window_is_counted_as_incomplete(repo, token, config):
    identity = observe(repo, token, config, 120, 90, finished=False)
    with repo.db:
        repo.db.execute("UPDATE scan_runs SET started=90 WHERE id=1")
    observe(repo, changed(token, contract_address="B" * 32), config, 150, 50)
    report = repo.market_report(100, now=200)
    assert report["incomplete_scans"] == report["completed_scans"] == 1
    assert report["evaluations"] == 1
    assert all(entry["peak"]["id"] != identity for entry in report["entries"])


def test_dimension_validation_runs_once_per_maximum_score_candidate(repo, token, config):
    calls = []
    for timestamp, score in ((110, 60), (120, 70), (130, 70), (140, 65)):
        observe(repo, token, config, timestamp, score, confirmation_score=20)

    def tracked(*arguments):
        calls.append(arguments[3])
        return Repository._market_evidence_rank(*arguments)

    repo.db.create_function("market_evidence_rank", 7, tracked, deterministic=True)
    report = repo.market_report(100, now=200)
    assert report["entries"][0]["observations"] == 4
    assert sorted(calls) == [120, 130]


def test_peak_latest_and_counts_without_automatic_cte_indexes(repo, token, config):
    # SQLite 3.40 on the production image does not reliably index materialized
    # CTE joins. Reporting must remain correct without those planner optimizations.
    repo.db.execute("PRAGMA automatic_index=OFF")
    expected = []
    for index, character in enumerate("ABCDEFGHJKLMNPQRSTUVWXYZ"):
        candidate = changed(token, contract_address=character * 32)
        peak = observe(repo, candidate, config, 110 + index, 50 + index, confirmation_score=20)
        latest = observe(repo, candidate, config, 150 + index, 40, confirmation_score=10)
        expected.append((candidate.contract_address, peak, latest))
    report = repo.market_report(100, now=200)
    actual = [
        (entry["contract_address"], entry["peak"]["id"], entry["latest"]["id"])
        for entry in report["entries"]
    ]
    assert actual == list(reversed(expected))[:10]
    assert report["unique_tokens"] == len(expected)
    assert report["evaluations"] == 2 * len(expected)
    assert all(entry["observations"] == 2 for entry in report["entries"])


def test_report_validation_works_on_read_only_connection(repo, token, config, tmp_path):
    import sqlite3

    identity = observe(repo, token, config, 120, 70, confirmation_score=30)
    connection = sqlite3.connect(f"file:{tmp_path / 'test.db'}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        report = Repository(connection).market_report(100, now=200)
        assert report["entries"][0]["peak"]["id"] == identity
        assert connection.total_changes == 0 and not connection.in_transaction
    finally:
        connection.close()


@pytest.mark.parametrize("replacement", [None, "broken", "[]", '{"token":{},"signal":{}}'])
def test_saved_evaluation_metadata_remains_available_with_legacy_details(
    repo, token, config, replacement
):
    identity = observe(repo, token, config, 120, 70)
    key = f"telegram:evaluation:{identity}"
    if replacement is None:
        with repo.db:
            repo.db.execute("DELETE FROM state WHERE key=?", (key,))
    else:
        repo.set_state(key, replacement)
    detail = repo.presentation_detail("e", identity)
    assert detail["token"] is detail["signal"] is None
    assert detail["evaluation"]["id"] == identity
    assert detail["evaluation"]["timestamp"] == 120
    assert detail["evaluation"]["eligible"] is False
    assert "score_below_threshold" in detail["evaluation"]["rejection_reasons"]
    assert set(detail["evaluation"]) == {
        "id",
        "timestamp",
        "eligible",
        "rejection_reasons",
        "missing_fields",
        "warnings",
    }


def test_saved_evaluation_metadata_uses_safe_lists_and_alert_detail_is_unchanged(
    repo, token, config
):
    identity = observe(repo, token, config, 120, 70)
    with repo.db:
        repo.db.execute(
            "UPDATE evaluations SET rejection_reasons=?,missing_fields=?,warnings=? WHERE id=?",
            ('["core_rejected",42,{"private":"value"}]', "broken", '{"private":"value"}', identity),
        )
    detail = repo.presentation_detail("e", identity)
    assert detail["token"] is not None
    assert detail["evaluation"]["rejection_reasons"] == ["core_rejected"]
    assert detail["evaluation"]["missing_fields"] == detail["evaluation"]["warnings"] == []
    assert repo.presentation_detail("e", identity + 1) is None
    original = {"legacy": "unchanged"}
    repo.set_state("telegram:alert:123", json.dumps(original))
    assert repo.presentation_detail("a", 123) == original

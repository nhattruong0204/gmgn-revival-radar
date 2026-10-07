import json
from types import SimpleNamespace

from revival_radar.analysis.scoring import score_token
from revival_radar.diagnostics import format_health
from revival_radar.models.signal import Acceleration, RevivalResult, Structure
from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository

from .conftest import changed


def scan_report(**overrides):
    return SimpleNamespace(
        **(
            {
                "processed": 1,
                "errors": 0,
                "sent": 0,
                "potential_alerts": 0,
                "sources_ok": 2,
                "discovered_by_chain": {},
                "discovered_by_source": {},
                "source_errors": {},
                "discovered_keys": set(),
            }
            | overrides
        )
    )


def candidate(score=60):
    return RevivalResult(
        score=score,
        status="BASE_FORMING",
        eligible=False,
        structure=Structure(available=True, base_detected=True),
        acceleration=Acceleration(volume_ratio_5m=1.2),
    )


def test_all_scores_retained_with_distinct_missing_data_and_blockers(repo, token, config):
    scan_id = repo.begin_scan(100)
    missing = changed(token, market_cap=None, volume_5m=None)
    result = score_token(missing, [], Structure(), config)
    repo.record_evaluation(scan_id, missing, result, config)
    row = repo.db.execute("SELECT * FROM evaluations").fetchone()
    assert row["score"] < 40
    blockers = json.loads(row["rejection_reasons"])
    assert "market_cap: unavailable" in blockers
    assert "no_base" in blockers
    assert "no_returning_activity" in blockers
    assert "score_below_threshold" in blockers
    fields = json.loads(row["missing_fields"])
    assert "market_cap" in fields
    assert "baseline_history" in fields
    assert "score_below_threshold" not in fields
    assert "candles" not in fields  # First-pass rejection means candles were never requested.
    report = repo.health(0, 200)
    assert report["evaluations"]["score_buckets"]["0-39"] == 1
    assert report["evaluations"]["rejection_counts"]["market_cap: unavailable"] == 1


def test_missing_candles_and_warmup_are_recorded_when_initial_filter_passes(repo, token, config):
    token = changed(
        token, contract_address="3" * 32, asset_type=None, asset_classification_reason=None
    )
    result = score_token(token, [], Structure(), config)
    scan_id = repo.begin_scan(100)
    repo.record_evaluation(scan_id, token, result, config)
    missing = repo.health(0, 200)["evaluations"]["missing_field_counts"]
    assert missing["candles"] == 1
    assert missing["baseline_history"] == 1
    assert missing["volume_5m_baseline_unavailable_or_zero"] == 1


def test_evaluations_are_per_scan_and_chain_address_is_identity(repo, token, config):
    address = "0x" + "1" * 40
    base, bsc = (changed(token, chain=chain, contract_address=address) for chain in ("base", "bsc"))
    first = repo.begin_scan(100)
    for item in (base, base, bsc):
        repo.record_evaluation(first, item, candidate(), config)
    repo.finish_scan(first, scan_report(processed=2), 110)
    second = repo.begin_scan(200)
    repo.record_evaluation(second, base, candidate(65), config)
    repo.finish_scan(second, scan_report(), 220)
    report = repo.health(0, 300)
    assert report["evaluations"]["count"] == 3
    assert report["evaluations"]["unique_tokens"] == 2
    assert len(report["best_candidates"]) == 2
    assert report["best_candidates"][0]["score"] == 65
    assert report["best_candidates"][0]["contract_address"] == address
    assert "score_below_threshold" in report["best_candidates"][0]["rejection_reasons"]
    assert report["scans"]["duration_seconds"]["mean"] == 15
    json.dumps(report, allow_nan=False)


def test_discovery_counts_do_not_count_watchlist_or_overlap_as_unique(repo, token, config):
    for started in (100, 200):
        scan_id = repo.begin_scan(started)
        repo.record_evaluation(scan_id, token, candidate(), config)
        watched = changed(token, contract_address="1" * 32, discovery_source=set())
        repo.record_evaluation(scan_id, watched, candidate(), config)
        # An additional discovery fails inspection and therefore has no evaluation.
        repo.finish_scan(
            scan_id,
            scan_report(
                processed=2,
                errors=1,
                discovered_by_chain={"sol": 2},
                discovered_by_source={"sol": {"hot_search": 2, "trending": 1}},
                discovered_keys={token.key, ("sol", "2" * 32)},
            ),
            started + 1,
        )
    report = repo.health(0, 300)
    assert report["discovery"]["unique_tokens"] == 2
    assert report["discovery"]["observations_by_chain"] == {"sol": 4}
    assert report["discovery"]["snapshots_by_source"]["sol"] == {"hot_search": 4, "trending": 2}
    assert report["evaluations"]["count"] == 4
    assert report["evaluations"]["unique_tokens"] == 2


def test_failed_and_interrupted_scans_and_alert_delivery_states_survive_restart(
    tmp_path, token, config
):
    path = tmp_path / "diagnostics.db"
    db = connect(path)
    repo = Repository(db)
    first = repo.begin_scan(100)
    repo.finish_scan(
        first,
        scan_report(
            processed=0,
            errors=2,
            sources_ok=0,
            source_errors={"sol": {"trending": 1, "hot_search": 1}},
        ),
        105,
    )
    second = repo.begin_scan(200)
    repo.record_evaluation(second, token, candidate(), config)
    config.dry_run = False
    sent_result = candidate(90).model_copy(update={"eligible": True})
    failed = repo.reserve_alert(changed(token, timestamp=210), sent_result, config)
    repo.finish_alert(failed, "failed")
    pending = repo.reserve_alert(changed(token, timestamp=211), sent_result, config)
    assert pending is not None
    db.close()
    reopened = connect(path)
    report = Repository(reopened).health(0, 300)
    assert report["scans"]["completed"] == 1
    assert report["scans"]["interrupted"] == 1
    assert report["scans"]["errors"] == 2
    assert report["scans"]["processed"] == 1
    assert report["discovery"]["source_errors"]["sol"]["trending"] == 1
    assert report["evaluations"]["count"] == 1
    assert report["alerts"] == {"sent": 0, "failed": 1, "pending": 1, "unknown": 0, "total": 2}
    reopened.close()


def test_daily_report_claim_and_state_are_durable(tmp_path):
    path = tmp_path / "state.db"
    first, second = connect(path), connect(path)
    assert Repository(first).claim_daily_summary("2026-10-07", 100)
    assert not Repository(second).claim_daily_summary("2026-10-07", 200)
    Repository(first).set_state("next_scan", "123", 100)
    assert Repository(second).get_state("next_scan") == "123"
    first.close()
    second.close()
    reopened = connect(path)
    assert not Repository(reopened).claim_daily_summary("2026-10-07", 300)
    assert Repository(reopened).claim_daily_summary("2026-10-08", 400)
    reopened.close()


def test_health_time_window_and_diagnostic_retention_preserve_snapshots(repo, token, config):
    for started in (100, 200, 300):
        scan_id = repo.begin_scan(started)
        repo.record_evaluation(scan_id, token, candidate(), config)
        repo.finish_scan(scan_id, scan_report(), started + 10)
    repo.save_snapshot(token)
    assert repo.health(150, 250)["evaluations"]["count"] == 1
    repo.prune_diagnostics(150)
    assert repo.db.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 2
    assert repo.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 2
    assert repo.db.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0] == 1


def test_report_escapes_html_and_includes_near_miss_identifiers(repo, token, config):
    scan_id = repo.begin_scan(100)
    unsafe = changed(token, symbol="<script>&boom")
    repo.record_evaluation(scan_id, unsafe, candidate(), config)
    report = repo.health(0, 200)
    text = format_health(report)
    assert "&lt;script&gt;&amp;boom" in text
    assert "<script>" not in text
    assert unsafe.contract_address in text
    assert "60/100" in text
    assert "score_below_threshold" in text
    assert "baseline_history" in text
    assert "unique tokens" in text
    assert "multiple reasons" in text
    report["evaluations"]["rejection_counts"] = {"<&" * 1000: 500}
    report["best_candidates"] *= 100
    assert len(format_health(report)) < 4000


def test_empty_health_report_explains_no_candidates(repo):
    report = repo.health(0, 200)
    assert report["scans"]["started"] == 0
    assert report["evaluations"]["unique_tokens"] == 0
    assert "None in this window" in format_health(report)


def test_report_uses_configured_timezone_and_safely_falls_back(repo):
    report = repo.health(0, 200)
    report["timezone"] = "Europe/Moscow"
    formatted = format_health(report)
    assert "01-01 03:00" in formatted
    assert "Europe/Moscow" in formatted
    report["timezone"] = "<invalid>"
    formatted = format_health(report)
    assert "01-01 00:00" in formatted
    assert "UTC" in formatted
    assert "<invalid>" not in formatted

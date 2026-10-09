import asyncio
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from revival_radar.audit import (
    analyze,
    budget_analysis,
    cohort,
    performance_summary,
    sampled_outcome,
    sequential_funnel,
)
from revival_radar.clients.gmgn import GMGNClient
from revival_radar.config_context import configuration_context
from revival_radar.metrics import ScanMetrics, error_category
from revival_radar.models.signal import RevivalResult
from revival_radar.scanner import Scanner

from .conftest import changed
from .test_outcomes import sent_alert
from .test_pipeline_optimization import SplitSource, run_scan


def exporter():
    path = Path(__file__).resolve().parents[1] / "scripts/export_audit_bundle.py"
    spec = importlib.util.spec_from_file_location("export_audit_bundle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_outcome_summary_excludes_future_observations(config, repo, token):
    sent_alert(config, repo, token)
    repo.observe_outcomes(changed(token, timestamp=token.timestamp + 4000, price=token.price * 2))
    before = repo.outcome_summary(0, token.timestamp + 3900)["1"]
    after = repo.outcome_summary(0, token.timestamp + 4000)["1"]
    assert before["observed"] == 0 and before["median_return_pct"] is None
    assert before["pending"] == 1 and after["median_return_pct"] == 100


def test_funnel_intersects_sets_and_preserves_conditional_branch():
    traces = [
        {
            "prefilter_pass": True,
            "market_enriched": True,
            "market_pass": True,
            "activity_trigger": True,
            "baseline_ready": False,
        },
        {
            "prefilter_pass": True,
            "market_enriched": True,
            "market_pass": True,
            "baseline_ready": True,
            "activity_trigger": False,
        },
        {"prefilter_pass": False, "baseline_ready": True, "activity_trigger": True},
    ]
    result = sequential_funnel(traces)
    counts = {r["stage"]: r for r in result["stages"]}
    assert counts["candidate"]["count"] == 3
    assert counts["baseline_ready"]["count"] == 1
    assert counts["activity_trigger"]["count"] == 0
    assert counts["baseline_ready"]["from_previous"]["pct"] == 50
    assert counts["baseline_ready"]["from_universe"]["pct"] == pytest.approx(100 / 3)
    assert result["activity_without_baseline"] == 1
    assert result["security"]["pct"] is None


@pytest.mark.parametrize("initial", [None, 0, float("nan")])
def test_missing_initial_price_never_becomes_zero_return(initial):
    result = sampled_outcome([(3600, 2), (86400, 3)], 0, initial, 90000)
    assert result["1"]["return_pct"] is None
    assert result["24"]["sampled_mfe_pct"] is None


def test_sampled_paths_horizons_censoring_and_asof():
    points = [(100, 1.5), (3000, 0.5), (3600, 1.2), (86400, 2), (86500, 100)]
    result = sampled_outcome(points, 0, 1, 86400)
    assert result["1"]["return_pct"] == pytest.approx(20)
    assert result["24"]["return_pct"] == 100
    assert result["24"]["sampled_mfe_pct"] == 100
    assert result["24"]["sampled_mae_pct"] == -50
    assert result["24"]["time_to_25pct_seconds"] == 100
    assert result["72"]["horizon_mature"] is False
    assert result["72"]["sampled_mfe_pct"] is None
    # A late observation must not stand in for a missed checkpoint.
    assert sampled_outcome([(8000, 2)], 0, 1, 10000)["1"]["return_pct"] is None
    assert sampled_outcome([(3601, 2)], 0, 1, 3600)["1"]["return_pct"] is None


def test_sampled_excursions_include_entry_and_reveal_edge_gaps():
    falling = sampled_outcome([(1800, 0.8)], 0, 1, 3600)["1"]
    assert falling["sampled_mfe_pct"] == 0
    assert falling["sampled_mae_pct"] == pytest.approx(-20)
    assert falling["entry_to_first_sample_seconds"] == 1800
    assert falling["last_sample_to_horizon_seconds"] == 1800
    rising = sampled_outcome([(3600, 1.2)], 0, 1, 3600)["1"]
    assert rising["sampled_mae_pct"] == 0
    empty = sampled_outcome([], 0, 1, 3600)["1"]
    assert empty["sampled_mfe_pct"] is None and empty["sampled_mae_pct"] is None


def test_empty_report_explicitly_insufficient():
    report = performance_summary([])
    assert report["evidence"] == "INSUFFICIENT SAMPLE"
    assert report["24"]["returns"]["n"] == 0
    assert report["24"]["positive_return"]["pct"] is None


def test_configuration_revision_weights_and_legacy_are_isolated():
    signal = {
        "score_version": "v1",
        "setup_score": 40,
        "trigger_score": 50,
        "confirmation_score": 0,
    }
    context = {"preset": "Balanced", "revision": 6, "settings": {"weights": {"base": 15}}}
    assert cohort(context, signal) != cohort(context | {"revision": 7}, signal)
    assert cohort(context, signal) != cohort(
        context | {"settings": {"weights": {"base": 20}}}, signal
    )
    assert cohort(context, {})[2] == "LEGACY"
    assert cohort(context, signal | {"score_version": "v2"}) != cohort(context, signal)


async def test_discovery_flag_is_recorded_and_separates_same_revision_cohorts(config, repo, token):
    source = SplitSource(changed(token, market_cap=1000))
    first = await run_scan(config, repo, source)
    config.trending_discovery_enabled = True
    source.discovery = changed(source.discovery, timestamp=token.timestamp + 300)
    second = await run_scan(config, repo, source)
    contexts = [
        json.loads(repo.get_state(f"telegram:scan:{report.scan_id}")) for report in (first, second)
    ]
    assert [c["settings"]["trending_discovery_enabled"] for c in contexts] == [False, True]
    assert contexts[0]["revision"] == contexts[1]["revision"]
    signals = []
    for report in (first, second):
        identity = repo.db.execute(
            "SELECT id FROM evaluations WHERE scan_id=?", (report.scan_id,)
        ).fetchone()[0]
        detail = repo.presentation_detail("e", identity)
        assert detail["configuration"] == contexts[len(signals)]
        signals.append(detail["signal"])
    assert cohort(contexts[0], signals[0]) != cohort(contexts[1], signals[1])
    assert first.discovered_by_source == {"sol": {"hot_search": 1}}
    assert second.discovered_by_source == {"sol": {"hot_search": 1, "trending": 1}}
    assert source.calls["hot_search"] == 2 and source.calls["trending"] == 1


def test_deferral_age_delay_and_left_censoring():
    traces = [
        {
            "chain": "sol",
            "contract_address": "A",
            "first_seen": 100,
            "timestamp": 100,
            "market_deferred": True,
        },
        {
            "chain": "sol",
            "contract_address": "A",
            "first_seen": 100,
            "timestamp": 400,
            "market_enriched": True,
            "market_observed_at": 400,
        },
        {
            "chain": "sol",
            "contract_address": "B",
            "first_seen": 0,
            "timestamp": 100,
            "market_deferred": True,
        },
        {
            "chain": "sol",
            "contract_address": "B",
            "first_seen": 0,
            "timestamp": 400,
            "market_deferred": True,
        },
    ]
    result = budget_analysis(traces, 2000)
    assert result["first_enrichment_delay_seconds"]["median"] == 300
    assert result["oldest_deferred_seconds_observed_lower_bound"] == 1900
    assert result["market_backlog_at_last_observation"] == 1
    assert result["starved_candidates_30m_observed"] == 1
    assert result["consecutive_observed_market_deferrals"]["max"] == 2


async def test_deferred_candidates_and_stage_errors_survive_as_telemetry(config, repo, token):
    config.max_market_enrich_per_scan = 0
    result = await run_scan(config, repo, SplitSource(token))
    perf = result.performance
    assert perf["telemetry_version"] == 2
    assert perf["candidate_traces"][0]["market_deferred"] is True
    assert perf["candidate_traces"][0]["prefilter_pass"] is True
    assert "market_enriched" not in perf["candidate_traces"][0]
    saved = json.loads(repo.get_state(f"scan:metrics:{result.scan_id}"))
    assert saved["performance"]["candidate_traces"] == perf["candidate_traces"]
    assert "fixture-key" not in json.dumps(saved)


async def test_interrupted_scan_keeps_metrics_and_remains_unfinished(config, repo, token):
    class Cancelled(SplitSource):
        async def enrich_market(self, seed):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_scan(config, repo, Cancelled(token))
    scan = repo.db.execute("SELECT * FROM scan_runs").fetchone()
    assert scan["finished"] is None
    saved = json.loads(repo.get_state(f"scan:metrics:{scan['id']}"))
    assert saved["performance"]["error_events"][-1]["category"] == "interrupted_scan"
    assert saved["performance"]["candidate_traces"]


@pytest.mark.parametrize(
    "error,category",
    [
        (sqlite3.OperationalError("credential database is locked"), "sqlite"),
        (httpx.ReadTimeout("credential"), "gmgn_timeout"),
        (RuntimeError("GMGN request failed HTTP=503 credential"), "gmgn_5xx"),
        (RuntimeError("GMGN rate limited credential"), "gmgn_429"),
        (RuntimeError("GMGN cooldown credential"), "gmgn_cooldown"),
        (ValueError("Unexpected token info response shape credential"), "malformed_response"),
    ],
)
def test_error_labels_never_export_exception_text(error, category):
    assert error_category(error) == category
    metrics = ScanMetrics()
    metrics.error("market", error)
    assert "credential" not in json.dumps(metrics.snapshot(1))


async def test_retry_telemetry_distinguishes_http_attempts_from_failed_operations(
    config, monkeypatch
):
    calls = 0

    async def no_wait(delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={"code": 0, "data": {"rank": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = GMGNClient(config, http)
        assert await source.discover("sol", "trending") == []
    perf = source.metrics.snapshot(1)
    assert perf["http_errors"]["/v1/market/rank:gmgn_5xx"] == 1
    assert perf["recovered_requests"]["/v1/market/rank"] == 1
    assert perf["http_statuses"]["/v1/market/rank:200"] == 1
    assert perf["failures"] == {}


def test_audit_readonly_integrity_cohort_isolation_and_no_secrets(config, repo, token):
    for revision in (5, 6):
        context = configuration_context(config, revision)
        context["gmgn_api_key"] = "NEVER_EXPORT_THIS"
        scan = repo.begin_scan(token.timestamp, context)
        signal = RevivalResult(
            score=65,
            status="WATCH",
            eligible=False,
            setup_score=80,
            trigger_score=60,
            confirmation_score=0,
            score_version="v1",
        )
        repo.record_evaluation(scan, token, signal, config, context)
    sent_alert(config, repo, token)
    repo.save_snapshot(changed(token, timestamp=token.timestamp + 3600, price=token.price * 1.5))
    repo.save_snapshot(changed(token, timestamp=token.timestamp + 86400, price=token.price * 2))
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    before = {
        t: [tuple(r) for r in repo.db.execute(f"SELECT * FROM {t}")]
        for t in ("token_snapshots", "alerts", "evaluations", "state")
    }
    report = analyze(path, until=token.timestamp + 90000, source="demo")
    assert report["database"]["integrity"] == ["ok"]
    assert len(report["cohorts"]) == 2
    assert {c["key"][1] for c in report["cohorts"]} == {5, 6}
    assert report["near_misses"]["sampled_24h_mfe_above_50pct"] == 2
    assert report["funnel"]["telemetry_v2"] is None
    assert "NEVER_EXPORT_THIS" not in json.dumps(report)
    for table, values in before.items():
        assert [tuple(r) for r in repo.db.execute(f"SELECT * FROM {table}")] == values
    assert report["signal_quality"]["evidence"] == "INSUFFICIENT SAMPLE"


def test_score_band_entries_capture_later_signal_once_per_token(config, repo, token):
    context = configuration_context(config, 7)
    for offset, score in ((0, 10), (100, 65), (200, 66)):
        observation = changed(token, timestamp=token.timestamp + offset)
        scan = repo.begin_scan(observation.timestamp, context)
        repo.record_evaluation(
            scan,
            observation,
            RevivalResult(
                score=score,
                status="WATCH",
                eligible=False,
                setup_score=50,
                trigger_score=50,
                confirmation_score=50,
                score_version="v1",
            ),
            config,
            context,
        )
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()["file"])
    report = analyze(path, until=token.timestamp + 1000)
    universe = next(iter(report["score_calibration"].values()))["score"]
    bands = next(iter(report["score_band_entry_calibration"].values()))["score"]
    assert universe["60-69"]["n"] == 0
    assert bands["60-69"]["n"] == 1
    assert bands["0-39"]["n"] == 1
    assert report["ui_reconciliation_24h"]["core_checks_passed_evaluations"] == 0


def test_audit_no_implicit_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(value INTEGER)")
        db.execute("PRAGMA user_version=1")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = analyze(path)
    assert report["database"]["schema_version"] == 1
    assert report["database"]["tables"]["unrelated"]["rows"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_export_backup_includes_live_wal_without_schema_changes(tmp_path):
    path = tmp_path / "source.db"
    original = sqlite3.connect(path)
    original.execute("PRAGMA journal_mode=WAL")
    original.execute("PRAGMA user_version=1")
    original.execute("CREATE TABLE valuable_history(value TEXT)")
    original.execute("INSERT INTO valuable_history VALUES('retained WAL observation')")
    original.commit()
    assert Path(str(path) + "-wal").stat().st_size > 0
    proc = subprocess.run(
        [sys.executable, "-", str(path)],
        input=exporter().BACKUP_PROBE,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    metadata = json.loads(proc.stdout)
    target = Path(metadata["audit_copy"])
    try:
        with sqlite3.connect(target) as copy:
            assert (
                copy.execute("SELECT value FROM valuable_history").fetchone()[0]
                == "retained WAL observation"
            )
            assert copy.execute("PRAGMA user_version").fetchone()[0] == 1
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert original.execute("SELECT COUNT(*) FROM valuable_history").fetchone()[0] == 1
    finally:
        original.close()
        target.unlink()


def test_export_log_allowlist_does_not_retain_tokens_or_urls():
    module = exporter()
    event = module.log_event(
        "2026-10-08T10:00:00Z WARNING token failed chain=sol error=DataSourceError "
        "GMGN_API_KEY=SECRET https://api.telegram.org/botSECRET"
    )
    assert event["category"] == "other_unclassified"
    assert "SECRET" not in json.dumps(event)
    assert module.log_event("2026-10-08T10:00:00Z INFO SECRET TOKEN") is None
    clean = module.log_event("2026-10-08T10:00:00Z INFO scan complete processed=40 errors=0 sent=0")
    assert clean["category"] is None and clean["errors"] == 0
    failure = module.log_event(
        "2026-10-08T10:00:00Z WARNING discovery failed error=DataSourceError"
    )
    assert failure["category"] == "other_unclassified"
    assert (
        module.log_event("2026-10-08T10:00:00Z INFO alert cooldown chain=sol")["category"]
        == "alert_cooldown"
    )
    assert (
        module.log_event("2026-10-08T10:00:00Z WARNING GMGN cooldown")["category"]
        == "gmgn_cooldown"
    )
    done = module.log_event(
        "2026-10-08T10:00:00Z INFO scan complete processed=4 errors=2 duration_seconds=75.1"
    )
    assert done["processed"] == 4 and done["duration_seconds"] == 75.1


def test_export_refuses_known_credentials_including_chunk_boundary(tmp_path):
    path = tmp_path / "sensitive"
    path.write_bytes(b"a" * (1024 * 1024 - 4) + b"secret12345")
    with pytest.raises(RuntimeError, match="credential"):
        exporter().assert_no_secrets(path, ["secret12345"])


def test_export_inventory_omits_env_values_and_command_arguments(monkeypatch):
    module = exporter()
    inspect = {
        "Id": "fake",
        "Config": {
            "Env": ["GMGN_API_KEY=secret-key-12345", "DRY_RUN=false"],
            "Cmd": ["revival-radar", "secret-key-12345"],
        },
        "State": {"Error": "secret-key-12345"},
        "HostConfig": {},
    }
    monkeypatch.setattr(
        module, "run", lambda *a, **kw: {"returncode": 0, "stdout": json.dumps([inspect])}
    )
    info, secrets = module.inspect_container("radar")
    assert "secret-key-12345" not in json.dumps(info)
    assert "GMGN_API_KEY" in info["environment_names"]
    assert secrets == ["secret-key-12345"]


async def test_cross_tier_starvation_exists_without_strategy_changes(config, repo, token):
    config.max_market_enrich_per_scan = 1
    low = changed(token, contract_address="A" * 32, discovery_source={"hot_search"})
    high = changed(token, contract_address="B" * 32, discovery_source={"trending"})
    repo.watch_discovered(low)
    for _ in range(20):
        repo.watch_defer(low)
    scanner = Scanner(config, SplitSource(token), repo, None)
    assert scanner.priority(high) < scanner.priority(low)


def test_full_export_archive_has_only_private_audit_artifacts(tmp_path, monkeypatch):
    import shutil
    import tarfile

    module = exporter()
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE historical_observations(value TEXT)")
        db.execute("INSERT INTO historical_observations VALUES('valuable')")
    monkeypatch.setattr(module, "select_container", lambda name: "radar")
    monkeypatch.setattr(module, "inspect_container", lambda container: ({}, ["do-not-export-key"]))
    monkeypatch.setattr(module, "summarize_logs", lambda *a: {"categories": {}, "events": []})

    def commands(args, *, input_text=None, timeout=30):
        if input_text == module.CONFIG_PROBE:
            return {
                "returncode": 0,
                "stdout": json.dumps({"database_path": str(path), "context": {}}),
            }
        if input_text == module.BACKUP_PROBE:
            proc = subprocess.run(
                [sys.executable, "-", str(path)],
                input=input_text,
                capture_output=True,
                text=True,
                check=False,
            )
            return {"returncode": proc.returncode, "stdout": proc.stdout}
        if args[:2] == ["docker", "cp"]:
            remote = Path(args[2].split(":", 1)[1])
            shutil.copyfile(remote, args[3])
            remote.unlink()  # Only test-created temporary copy.
            return {"returncode": 0, "stdout": ""}
        return {"returncode": 0, "stdout": "bounded fixture"}

    monkeypatch.setattr(module, "run", commands)
    artifact = module.export(tmp_path / "exports")
    assert artifact.stat().st_mode & 0o777 == 0o600
    with tarfile.open(artifact) as archive:
        assert set(archive.getnames()) == {
            "production_snapshot.db",
            "manifest.json",
            "production_config_sanitized.json",
            "container_inventory.json",
            "vps_inventory.json",
            "production_logs_summary.json",
        }
        manifest = json.load(archive.extractfile("manifest.json"))
        snapshot = archive.extractfile("production_snapshot.db").read()
        assert hashlib.sha256(snapshot).hexdigest() == manifest["snapshot_sha256"]
        for member in archive.getmembers():
            assert b"do-not-export-key" not in archive.extractfile(member).read()
    with sqlite3.connect(path) as original:
        assert original.execute("SELECT COUNT(*) FROM historical_observations").fetchone()[0] == 1


def test_short_detected_range_keeps_specific_blocker_without_changing_eligibility(
    config, repo, token
):
    from revival_radar.models.signal import Structure

    scan = repo.begin_scan(token.timestamp)
    result = RevivalResult(
        score=63,
        status="WATCH",
        eligible=False,
        structure=Structure(available=True, base_detected=False, base_duration_hours=12),
    )
    repo.record_evaluation(scan, token, result, config)
    row = repo.db.execute("SELECT * FROM evaluations").fetchone()
    blockers = json.loads(row["rejection_reasons"])
    assert "no_base" in blockers and "base_too_short" in blockers
    assert not row["eligible"] and row["score"] == 63


def test_log_database_correlation_preserves_unknown_attribution():
    from revival_radar.audit import correlate_logs

    scans = [
        {
            "id": 1,
            "started": 1791453600,
            "finished": 1791453675,
            "processed": 4,
            "errors": 1,
            "sent": 0,
            "potential_alerts": 0,
            "sources_ok": 2,
        }
    ]
    events = {
        "events": [
            {
                "timestamp_text": "2026-10-08T10:01:15Z",
                "event": "scan_complete",
                "errors": 1,
                "processed": 3,
                "category": None,
            },
            {
                "timestamp_text": "2026-10-08T10:00:10Z",
                "event": "diagnostic",
                "category": "gmgn_429",
            },
            {"timestamp_text": "2026-10-08 10:00:10", "category": "gmgn_429"},
        ]
    }
    result = correlate_logs(scans, events)
    assert result["categories"]["gmgn_429"]["temporally_affected_scans"] == 1
    assert result["completion_checks"][0]["counter_differences"]["processed"] == {"log": 3, "db": 4}
    assert result["unmatched_or_naive_timestamps"] == 1


def test_audit_cli_filters_untrusted_metadata_credentials():
    path = Path(__file__).resolve().parents[1] / "scripts/audit_database.py"
    spec = importlib.util.spec_from_file_location("audit_database_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.safe_metadata(
        {
            "git": {"sha": "abc"},
            "gmgn_api_key": "do-not-export",
            "context": {"telegram_bot_token": "do-not-export", "revision": 5},
        }
    )
    assert result == {"git": {"sha": "abc"}, "context": {"revision": 5}}

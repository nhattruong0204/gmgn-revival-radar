"""Reports read-latency (#101): schema-10 configuration projection, new window query, parity."""

import json
import random
import sqlite3
import time
from types import SimpleNamespace

import pytest

from revival_radar.analysis import report_projection as projection
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.storage.database import PUBLIC_SCHEMA_VERSIONS, SCHEMA_VERSION, connect
from revival_radar.storage.repository import Repository
from revival_radar.web.config import WebConfig
from revival_radar.web.queries import public_reader

from .conftest import changed

# Verbatim copy of the pre-#101 ranking query (observations joined to scan rows per row,
# configurations from the expression index, latest evaluation scanned from observations).
LEGACY_QUERY = """WITH observations AS MATERIALIZED (
            SELECT e.id,e.chain,e.contract_address,e.timestamp,e.score,e.status,e.eligible,
                s.finished AS scan_finished FROM evaluations e JOIN scan_runs s ON s.id=e.scan_id
                WHERE e.chain='sol' AND e.timestamp>? AND e.timestamp<=?
            AND s.finished IS NOT NULL AND s.finished<=?
        ), token_counts AS MATERIALIZED (
            SELECT contract_address,MAX(score) AS peak_score,COUNT(*) AS observations
            FROM observations GROUP BY contract_address
        ), competitive_score AS (
            SELECT COALESCE((SELECT peak_score FROM token_counts
                ORDER BY peak_score DESC LIMIT 1 OFFSET ?),-1)
                AS minimum
        ), candidates AS MATERIALIZED (
            SELECT o.*,t.observations,
                market_evidence_rank(st.value,o.chain,o.contract_address,o.timestamp,
                o.score,o.status,o.eligible) AS evidence_rank
            FROM observations o JOIN token_counts t ON t.contract_address=o.contract_address
            LEFT JOIN state st ON st.key='telegram:evaluation:' || o.id
            WHERE o.score=t.peak_score AND o.score>=(SELECT minimum FROM competitive_score)
        ), peaks AS (
            SELECT *,ROW_NUMBER() OVER (
                PARTITION BY contract_address ORDER BY evidence_rank DESC,timestamp DESC,id DESC
            ) AS peak_position FROM candidates
        ), top_peaks AS MATERIALIZED (
            SELECT * FROM peaks WHERE peak_position=1
            ORDER BY score DESC,evidence_rank DESC,timestamp DESC,contract_address ASC LIMIT ?
        ), coverage AS (
            SELECT COUNT(*) AS evaluations,
                (SELECT COUNT(*) FROM token_counts) AS unique_tokens,
                MIN(timestamp) AS first_observation,MAX(scan_finished) AS last_finished
            FROM observations
        ), configurations AS MATERIALIZED (
            SELECT DISTINCT COALESCE((SELECT
                COALESCE(CAST(json_extract(CASE WHEN json_valid(st.value) THEN st.value
                    ELSE '{}' END,'$.configuration') AS TEXT),'legacy') || ':' ||
                COALESCE(CAST(json_extract(CASE WHEN json_valid(st.value) THEN st.value
                    ELSE '{}' END,'$.signal.score_version') AS TEXT),'legacy')
                FROM state st INDEXED BY state_report_configuration
                WHERE st.key='telegram:evaluation:' || e.id),'legacy:legacy')
            FROM observations e LIMIT 2
        ) SELECT p.id AS peak_id,(
                SELECT o.id FROM observations o
                WHERE o.contract_address=p.contract_address
                ORDER BY o.timestamp DESC,o.id DESC LIMIT 1
            ) AS latest_id,p.observations,c.*,
                (SELECT COUNT(*) FROM configurations) AS configurations
            FROM coverage c LEFT JOIN top_peaks p ON 1
            ORDER BY p.score DESC,p.evidence_rank DESC,
                p.timestamp DESC,p.contract_address ASC"""


def legacy_summary(repo, since, until, limit=10):
    rows = repo.db.execute(LEGACY_QUERY, (since, until, until, limit - 1, limit)).fetchall()
    total = rows[0]
    return {
        "evaluations": total["evaluations"],
        "unique_tokens": total["unique_tokens"],
        "first_observation": total["first_observation"],
        "last_finished": total["last_finished"],
        "multiple_configurations": total["configurations"] > 1,
        "entries": [
            (r["peak_id"], r["latest_id"], r["observations"]) for r in rows if r["peak_id"]
        ],
    }


def new_summary(repo, since, until, limit=10):
    report = repo.market_report(since, now=until, limit=limit)
    return {
        "evaluations": report["evaluations"],
        "unique_tokens": report["unique_tokens"],
        "first_observation": report["first_observation"],
        "last_finished": report["last_finished"],
        "multiple_configurations": report["multiple_configurations"],
        "entries": [
            (e["peak"]["id"], e["latest"]["id"], e["observations"]) for e in report["entries"]
        ],
    }


def observe(repo, token, config, timestamp, score, *, finished=True, configuration=None, **values):
    configuration = configuration or {"revision": 1, "settings": {}}
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
        repo.finish_scan(scan, SimpleNamespace(processed=1), timestamp + 1)
    return repo.db.execute("SELECT id FROM evaluations WHERE scan_id=?", (scan,)).fetchone()[0]


def bulk_world(db, seed, evaluations, tokens=40, start=1_000_000.0, step=60.0, texts=2):
    """Direct bulk rows: finished scans, varied scores, ties, damaged and missing presentations."""
    rng = random.Random(seed)
    scans = evaluations // 5
    with db:
        for s in range(scans):
            finished = None if s % 37 == 36 else start + s * step * 5 + 30
            db.execute(
                "INSERT INTO scan_runs(id,started,finished,processed) VALUES(?,?,?,5)",
                (10_000 + s, start + s * step * 5, finished),
            )
        for n in range(evaluations):
            scan = 10_000 + n // 5
            if n % 5 == 0:
                picks = rng.sample(range(tokens), 5)
            token = f"BULK{picks[n % 5]:04d}"
            stamp = start + (n // 5) * step * 5 + n % 5
            score = rng.choice([10, 40, 40, 55, 70, 70, 90])
            db.execute(
                "INSERT INTO evaluations(scan_id,timestamp,chain,contract_address,symbol,score,"
                "status,eligible,rejection_reasons,missing_fields,warnings,asset_type,"
                "discovery_sources) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (scan, stamp, "sol", token, "S", score, "WATCH", 0, "[]", "[]", "[]", "x", "[]"),
            )
            identity = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            kind = rng.randrange(12)
            if kind == 0:
                continue  # Missing presentation: legacy configuration.
            if kind == 1:
                value = "{not json"
            else:
                value = json.dumps(
                    {
                        "configuration": {"revision": rng.randrange(texts), "padding": "x" * 400},
                        "signal": {"score_version": rng.choice(["v1", "v2"][:texts])},
                    }
                )
            db.execute(
                "INSERT INTO state(key,value,updated_at) VALUES(?,?,?)",
                (f"telegram:evaluation:{identity}", value, stamp),
            )


@pytest.fixture
def mixed(repo, token, config):
    for index in range(30):
        observe(repo, token, config, 110 + index, 40 + index % 7, confirmation_score=index % 5)
    second = changed(token, contract_address="B" * 32)
    for index in range(10):
        observe(repo, second, config, 130 + index, 46, trigger_score=index)
    observe(repo, second, config, 150, 99, finished=False)
    observe(repo, token, config, 160, 80, configuration={"revision": 2, "settings": {}})
    return repo


def test_report_matches_legacy_query_before_and_after_projection(mixed):
    windows = [(100, 200), (110, 150), (125, 135), (159, 161), (0, 100), (140, 1000)]
    for since, until in windows:
        # Projection table present but empty: the on-read fallback.
        assert new_summary(mixed, since, until) == legacy_summary(mixed, since, until)
    assert projection.refresh(mixed.db)["complete"]
    for since, until in windows:
        assert new_summary(mixed, since, until) == legacy_summary(mixed, since, until)
    assert new_summary(mixed, 100, 200)["multiple_configurations"] is True
    assert new_summary(mixed, 110, 150)["multiple_configurations"] is False


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("texts", [1, 2])
def test_randomised_histories_are_identical(repo, seed, texts):
    bulk_world(repo.db, seed, 600, texts=texts)
    horizon = 1_000_000.0 + 600 // 5 * 300
    for fraction in (1.0, 0.5, 0.1):
        until = horizon + 100
        since = until - (until - 1_000_000.0) * fraction
        expected = legacy_summary(repo, since, until)
        assert new_summary(repo, since, until) == expected  # unprojected
        projection.refresh(repo.db)
        assert new_summary(repo, since, until) == expected  # projected
        repo.db.execute("DELETE FROM evaluation_configuration")
        repo.db.commit()


def test_refresh_is_idempotent_incremental_and_budgeted(repo):
    bulk_world(repo.db, 5, 500)
    assert projection.refresh(repo.db, budget_seconds=0)["written"] == 500
    rows = repo.db.execute(
        "SELECT evaluation_id,timestamp,digest FROM evaluation_configuration ORDER BY 1"
    ).fetchall()
    assert len(rows) == 500
    assert projection.refresh(repo.db) == {"written": 0, "complete": True}
    again = repo.db.execute(
        "SELECT evaluation_id,timestamp,digest FROM evaluation_configuration ORDER BY 1"
    ).fetchall()
    assert [tuple(r) for r in again] == [tuple(r) for r in rows]
    # A new evaluation is picked up incrementally; nothing else is rewritten.
    repo.db.execute(
        "INSERT INTO evaluations(scan_id,timestamp,chain,contract_address,symbol,score,status,"
        "eligible,rejection_reasons,missing_fields,warnings,asset_type,discovery_sources)"
        " VALUES(10000,5,'sol','NEW','S',1,'WATCH',0,'[]','[]','[]','x','[]')"
    )
    repo.db.commit()
    assert projection.refresh(repo.db) == {"written": 1, "complete": True}


def test_budget_spent_resumes_on_next_run(repo, monkeypatch):
    monkeypatch.setattr(projection, "BATCH", 100)
    bulk_world(repo.db, 6, 500)
    first = projection.refresh(repo.db, budget_seconds=0)
    assert first == {"written": 100, "complete": False}
    assert projection.refresh(repo.db, budget_seconds=0)["written"] == 100
    while not projection.refresh(repo.db, budget_seconds=0)["complete"]:
        pass
    assert repo.db.execute("SELECT COUNT(*) FROM evaluation_configuration").fetchone()[0] == 500


def test_stale_digest_is_replaced_and_never_trusted(repo):
    bulk_world(repo.db, 7, 100, texts=1)
    projection.refresh(repo.db)
    (identity,) = repo.db.execute(
        "SELECT id FROM evaluations WHERE json_valid("
        "(SELECT value FROM state WHERE key='telegram:evaluation:'||evaluations.id)) LIMIT 1"
    ).fetchone()
    # A reused evaluation id: same id, different evaluation (timestamp) and configuration.
    repo.db.execute("UPDATE evaluations SET timestamp=timestamp+0.5 WHERE id=?", (identity,))
    repo.db.execute(
        "UPDATE state SET value=? WHERE key=?",
        (
            json.dumps({"configuration": {"revision": 99}, "signal": {"score_version": "z"}}),
            f"telegram:evaluation:{identity}",
        ),
    )
    repo.db.commit()
    since, until = 900_000.0, 1_400_000.0
    expected = legacy_summary(repo, since, until)
    assert expected["multiple_configurations"] is True
    # The mismatching digest counts as not covering the window: exact answer via fallback.
    assert new_summary(repo, since, until) == expected
    assert projection.refresh(repo.db) == {"written": 1, "complete": True}
    assert new_summary(repo, since, until) == expected


def test_projected_report_does_not_traverse_the_presentation_index(repo):
    bulk_world(repo.db, 8, 400, texts=1)
    projection.refresh(repo.db)
    statements = []
    repo.db.set_trace_callback(statements.append)
    report = repo.market_report(900_000.0, now=1_400_000.0)
    assert report["evaluations"] > 300 and report["multiple_configurations"]
    assert not any("state_report_configuration" in text for text in statements)
    repo.db.execute("DELETE FROM evaluation_configuration")
    statements.clear()
    assert repo.market_report(900_000.0, now=1_400_000.0) == report
    # Unprojected: the same answer is computed on read from the saved presentations.
    assert any("state_report_configuration" in text for text in statements)


def test_additive_migration_from_schema_nine_is_idempotent(tmp_path):
    path = tmp_path / "nine.db"
    db = connect(path)
    bulk_world(db, 9, 200)
    db.execute("DROP TABLE evaluation_configuration")
    db.execute("PRAGMA user_version=9")
    db.commit()
    before = {
        t: [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1")]
        for t in ("scan_runs", "evaluations", "state", "alerts", "token_snapshots")
    }
    db.close()
    for _ in range(2):
        db = connect(path)
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 12
        after = {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in before}
        assert after == before
        assert db.execute("SELECT COUNT(*) FROM evaluation_configuration").fetchone()[0] == 0
        db.close()
    assert {7, 8, 9, 10, 11, 12} == set(PUBLIC_SCHEMA_VERSIONS)


@pytest.mark.parametrize("version", [7, 8, 9])
def test_website_reads_older_schemas_without_projection_and_without_writing(tmp_path, version):
    path = tmp_path / f"v{version}.db"
    db = connect(path)
    bulk_world(db, 10, 150)
    projection.refresh(db)
    expected = legacy_summary(Repository(db), 900_000.0, 1_400_000.0)
    db.execute("DROP TABLE evaluation_configuration")
    db.execute(f"PRAGMA user_version={version}")
    db.commit()
    db.close()
    web = WebConfig(database_path=path, web_cache_seconds=0, web_cookie_secure=False)
    with public_reader(web) as reader:
        report = reader.repo.market_report(900_000.0, now=1_400_000.0)
        assert report["multiple_configurations"] == expected["multiple_configurations"]
        assert report["evaluations"] == expected["evaluations"]
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == version
    assert not check.execute(
        "SELECT 1 FROM sqlite_master WHERE name='evaluation_configuration'"
    ).fetchall()


def test_public_reader_hides_synthetic_rows_from_projected_report(tmp_path):
    path = tmp_path / "hidden.db"
    db = connect(path)
    bulk_world(db, 11, 100, texts=1)
    (identity,) = db.execute("SELECT id FROM evaluations ORDER BY id LIMIT 1").fetchone()
    db.execute(
        "INSERT INTO state(key,value,updated_at) VALUES(?,'synthetic',1)",
        (f"origin:evaluation:{identity}",),
    )
    projection.refresh(db)
    db.commit()
    db.close()
    web = WebConfig(database_path=path, web_cache_seconds=0, web_cookie_secure=False)
    with public_reader(web) as reader:
        report = reader.repo.market_report(900_000.0, now=1_400_000.0)
    plain = connect(path)
    total = Repository(plain).market_report(900_000.0, now=1_400_000.0)["evaluations"]
    assert report["evaluations"] == total - 1


def test_prune_removes_digests_of_pruned_evaluations(repo):
    bulk_world(repo.db, 12, 100)
    projection.refresh(repo.db)
    repo.prune_diagnostics(1_000_000.0 + 10_000)
    left = repo.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    assert repo.db.execute("SELECT COUNT(*) FROM evaluation_configuration").fetchone()[0] == left


def test_scanner_hook_never_fatal(monkeypatch):
    from revival_radar.scanner import Scanner

    scanner = Scanner.__new__(Scanner)
    scanner.config = SimpleNamespace(report_projection_budget_seconds=1)
    scanner.repository = SimpleNamespace(
        refresh_report_projection=lambda budget: (_ for _ in ()).throw(sqlite3.OperationalError()),
        refresh_radar_projection=lambda budget: {},
    )
    scanner.maintain_report_projection()  # logs a warning and returns


def test_large_history_reports_stay_inside_the_read_deadline(tmp_path):
    """30k evaluations / ~25k tokens of multi-KB presentations: the 1.5 s deadline holds."""
    path = tmp_path / "large.db"
    db = connect(path)
    bulk_world(db, 13, 30_000, tokens=2500, texts=1)
    projection.refresh(db, budget_seconds=60)
    db.close()
    web = WebConfig(database_path=path, web_cache_seconds=0, web_cookie_secure=False)
    start = time.perf_counter()
    for hours in (1, 24, 168):
        until = 1_000_000.0 + 6000 * 300 + 1
        with public_reader(web) as reader:
            reader.report(until, hours)
    assert time.perf_counter() - start < 1.5

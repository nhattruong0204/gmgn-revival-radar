"""Radar cold-read latency (#100): schema-11 evaluation_radar projection and parity."""

import itertools
import json
import random
import sqlite3
import time
from types import SimpleNamespace

import pytest

from revival_radar.analysis import radar_projection as projection
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.storage.database import PUBLIC_SCHEMA_VERSIONS, SCHEMA_VERSION, connect
from revival_radar.storage.repository import Repository
from revival_radar.web.config import WebConfig
from revival_radar.web.queries import PublicQueries, public_reader

from .conftest import changed
from .legacy_radar import legacy_radar

UNTIL = 10_000.0
COMBOS = [
    dict(
        chain=None,
        status=status,
        search=search,
        min_score=min_score,
        include_ignore=ignore,
        sort=sort,
        order=order,
        page=page,
        page_size=7,
        age_hours=age,
    )
    for age, (min_score, ignore, status), sort, order, search, page in itertools.product(
        (1, 24),
        ((0, True, None), (40, False, None), (0, False, "WATCH")),
        ("score", "timestamp", "market_cap", "liquidity", "volume_5m"),
        ("desc", "asc"),
        ("", "alp", "TKN1", "zzzz"),
        (1, 3),
    )
]


def world(repo, token, config, count=45, seed=3):
    """Real presentations (varied fields, ties, nulls), unfinished scans and damaged rows."""
    rng = random.Random(seed)
    for n in range(count):
        stamp = UNTIL - 3000 + n * 40 + rng.random()
        configuration = {"revision": 1, "settings": {}}
        scan = repo.begin_scan(stamp - 1, configuration)
        item = changed(
            token,
            contract_address="ABCDEFGHJKLMNPQRS"[n % 17] + "A" * 31,
            timestamp=stamp,
            symbol=rng.choice(["ALPHA", "beta", "TKN1", "x_y%"]),
            name=rng.choice(["Alpha Coin", "Beta", "Gamma", "100% real"]),
            market_cap=rng.choice([None, 1e5, 5e5, 5e5, 2e6]),
            liquidity=rng.choice([None, 3e4, 9e4]),
            volume_5m=rng.choice([None, 10.0, 10.0, 5e3]),
        )
        signal = RevivalResult(
            score=rng.choice([10, 40, 40, 70]),
            status=rng.choice(["IGNORE", "WATCH", "WATCH", "REVIVING"]),
            eligible=False,
            returning_activity=True,
            structure=Structure(available=True, base_detected=True),
        )
        repo.record_evaluation(scan, item, signal, config, configuration)
        if n % 11 != 10:
            repo.finish_scan(scan, SimpleNamespace(processed=1), stamp + 1)
    # A damaged and a missing presentation must stay unknown in both implementations.
    ids = [r[0] for r in repo.db.execute("SELECT id FROM evaluations ORDER BY id LIMIT 3")]
    repo.db.execute(
        "UPDATE state SET value='{broken' WHERE key=?", (f"telegram:evaluation:{ids[0]}",)
    )
    repo.db.execute("DELETE FROM state WHERE key=?", (f"telegram:evaluation:{ids[1]}",))
    repo.db.commit()


def dumps(page):
    return json.dumps(page.model_dump(), sort_keys=True, default=str)


def assert_parity(db, combos=COMBOS):
    queries = PublicQueries(db)
    for combo in combos:
        assert dumps(queries.radar(UNTIL, **combo)) == dumps(legacy_radar(queries, UNTIL, **combo))


@pytest.fixture
def seeded(repo, token, config):
    world(repo, token, config)
    return repo


def test_radar_matches_legacy_query_unprojected_partial_and_complete(seeded):
    assert_parity(seeded.db)  # table present but empty: everything computed on read
    assert projection.refresh(seeded.db, budget_seconds=0)["written"] > 0
    seeded.db.execute("DELETE FROM evaluation_radar WHERE evaluation_id % 2 = 0")
    seeded.db.commit()
    assert_parity(seeded.db)  # partial: mixed projected and computed rows
    assert projection.refresh(seeded.db)["complete"]
    assert_parity(seeded.db)  # complete
    assert (
        seeded.db.execute("SELECT COUNT(*) FROM evaluation_radar").fetchone()[0]
        == (seeded.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0])
    )


def test_projected_values_equal_public_field(seeded):
    from revival_radar.web.queries import public_field

    projection.refresh(seeded.db)
    rows = seeded.db.execute(
        "SELECT e.*,s.value AS payload FROM evaluations e LEFT JOIN state s "
        "ON s.key='telegram:evaluation:'||e.id"
    ).fetchall()
    for r in rows:
        stored = seeded.db.execute(
            "SELECT * FROM evaluation_radar WHERE evaluation_id=?", (r["id"],)
        ).fetchone()
        assert stored["symbol"] == r["symbol"]
        for name in projection.FIELDS:
            args = (r["payload"], r["chain"], r["contract_address"], r["timestamp"], r["score"])
            expected = public_field(*args, r["status"], r["eligible"], name)
            assert stored[name] == expected and type(stored[name]) is type(expected)


def test_refresh_is_idempotent_resumable_and_incremental(seeded, monkeypatch):
    monkeypatch.setattr(projection, "BATCH", 10)
    total = seeded.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    assert projection.refresh(seeded.db, budget_seconds=0) == {"written": 10, "complete": False}
    while not projection.refresh(seeded.db, budget_seconds=0)["complete"]:
        pass
    rows = [tuple(r) for r in seeded.db.execute("SELECT * FROM evaluation_radar ORDER BY 1")]
    assert len(rows) == total
    assert projection.refresh(seeded.db) == {"written": 0, "complete": True}
    assert [
        tuple(r) for r in seeded.db.execute("SELECT * FROM evaluation_radar ORDER BY 1")
    ] == rows


def test_stale_row_is_never_trusted(seeded):
    projection.refresh(seeded.db)
    (identity,) = seeded.db.execute(
        "SELECT evaluation_id FROM evaluation_radar WHERE market_cap IS NOT NULL LIMIT 1"
    ).fetchone()
    # A reused id: same id, different evaluation (score), and a changed saved payload.
    seeded.db.execute("UPDATE evaluations SET score=score+1 WHERE id=?", (identity,))
    seeded.db.execute(
        "UPDATE evaluation_radar SET market_cap=-1,name='stale' WHERE evaluation_id=?",
        (identity,),
    )
    seeded.db.commit()
    assert_parity(seeded.db, COMBOS[::7])
    assert projection.refresh(seeded.db)["written"] == 1
    assert_parity(seeded.db, COMBOS[::7])


def test_projected_radar_does_not_load_payloads_for_sorting(seeded):
    projection.refresh(seeded.db)
    calls = []
    queries = PublicQueries(seeded.db)
    seeded.db.create_function("public_field", 8, lambda *a: calls.append(a), deterministic=True)
    combo = {**COMBOS[0], "sort": "market_cap", "age_hours": 24}
    queries.radar(UNTIL, **combo)
    assert calls == []


def test_additive_migration_from_schema_ten_is_idempotent(tmp_path, token, config):
    path = tmp_path / "ten.db"
    db = connect(path)
    world(Repository(db), token, config, count=20)
    db.execute("DROP TABLE evaluation_radar")
    db.execute("PRAGMA user_version=10")
    db.commit()
    before = {
        t: [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1")]
        for t in (
            "scan_runs",
            "evaluations",
            "state",
            "token_snapshots",
            "evaluation_configuration",
        )
    }
    db.close()
    for _ in range(2):
        db = connect(path)
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 12
        assert {
            t: [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in before
        } == before
        assert db.execute("SELECT COUNT(*) FROM evaluation_radar").fetchone()[0] == 0
        db.close()
    assert {7, 8, 9, 10, 11, 12} == set(PUBLIC_SCHEMA_VERSIONS)


@pytest.mark.parametrize("version", [7, 8, 9, 10, 11, 12])
def test_website_reads_every_supported_schema_read_only(tmp_path, token, config, version):
    path = tmp_path / f"v{version}.db"
    db = connect(path)
    world(Repository(db), token, config, count=25)
    if version >= 11:
        projection.refresh(db)
    else:
        db.execute("DROP TABLE evaluation_radar")
    if version < 12:
        db.execute("DROP TABLE origin_version")  # its triggers go with the counter table
        for name in ("insert", "update", "delete"):
            db.execute(f"DROP TRIGGER origin_version_{name}")
        db.execute(f"PRAGMA user_version={version}")
    db.commit()
    expected = {
        i: dumps(legacy_radar(PublicQueries(db), UNTIL, **c)) for i, c in enumerate(COMBOS[::31])
    }
    db.close()
    web = WebConfig(database_path=path, web_cache_seconds=0, web_cookie_secure=False)
    with public_reader(web) as reader:
        for i, combo in enumerate(COMBOS[::31]):
            assert dumps(reader.radar(UNTIL, **combo)) == expected[i]
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == version
    has_table = bool(
        check.execute("SELECT 1 FROM sqlite_master WHERE name='evaluation_radar'").fetchall()
    )
    assert has_table == (version >= 11)


def test_prune_removes_projected_rows_of_pruned_evaluations(seeded):
    projection.refresh(seeded.db)
    seeded.prune_diagnostics(UNTIL - 1500)
    left = seeded.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    assert left > 0
    assert seeded.db.execute("SELECT COUNT(*) FROM evaluation_radar").fetchone()[0] == left


def test_scanner_shares_one_budget_and_is_never_fatal():
    from revival_radar.scanner import Scanner

    scanner = Scanner.__new__(Scanner)
    scanner.config = SimpleNamespace(report_projection_budget_seconds=10)
    budgets = []

    def report(budget):
        budgets.append(("report", budget))
        raise sqlite3.OperationalError

    scanner.repository = SimpleNamespace(
        refresh_report_projection=report,
        refresh_radar_projection=lambda budget: budgets.append(("radar", budget)) or {},
    )
    scanner.maintain_report_projection()  # report failure is logged; radar still runs
    assert budgets[0] == ("report", 5) and budgets[1][0] == "radar"
    assert 4 < budgets[1][1] <= 10 and budgets[0][1] + 0 <= 5


def test_large_history_radar_stays_inside_the_read_deadline(tmp_path):
    path = tmp_path / "large.db"
    db = connect(path)
    rng = random.Random(1)
    with db:
        for s in range(2000):
            db.execute(
                "INSERT INTO scan_runs(id,started,finished,processed) VALUES(?,?,?,5)",
                (s + 1, s * 5.0, s * 5.0 + 3),
            )
        for n in range(10_000):
            token = f"BIG{(n // 5 * 13 + n % 5) % 3000:04d}"
            db.execute(
                "INSERT INTO evaluations(scan_id,timestamp,chain,contract_address,"
                "symbol,score,status,eligible,rejection_reasons,missing_fields,warnings,"
                "asset_type,discovery_sources) VALUES(?,?,?,?,?,?,?,0,'[]','[]','[]','x','[]')",
                (
                    n // 5 + 1,
                    n // 5 * 5.0 + 1,
                    "sol",
                    token,
                    "S",
                    rng.choice([10, 40, 70]),
                    "WATCH",
                ),
            )
            identity = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute(
                "INSERT INTO state(key,value,updated_at) VALUES(?,?,0)",
                (f"telegram:evaluation:{identity}", json.dumps({"pad": "x" * 3000})),
            )
    projection.refresh(db, budget_seconds=60)
    db.close()
    web = WebConfig(database_path=path, web_cache_seconds=0, web_cookie_secure=False)
    combo = {**COMBOS[0], "min_score": 0, "include_ignore": True, "age_hours": 168, "page_size": 25}
    start = time.perf_counter()
    for sort in ("score", "market_cap"):
        with public_reader(web) as reader:
            reader.radar(10_100.0, **{**combo, "sort": sort})
    assert time.perf_counter() - start < 1.5


def test_offline_backfill_completes_every_projection_and_is_rerunnable(tmp_path, token, config):
    from revival_radar import projection_backfill

    path = tmp_path / "backfill.db"
    db = connect(path)
    world(Repository(db), token, config, count=30)
    db.close()
    first = projection_backfill.run(path)
    assert first["complete"] and first["schema"] == SCHEMA_VERSION
    assert first["radar_fields"]["written"] == first["report_digests"]["written"] > 0
    assert first["coverage"]["radar_fields_missing_7d"] == 0
    second = projection_backfill.run(path)
    assert second["complete"] and second["radar_fields"]["written"] == 0

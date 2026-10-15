"""call_performance projection (#90, schema 8): migration, maintenance and read parity."""

import hashlib
import json
import math
import random
import sqlite3
import time

import pytest

from revival_radar.analysis import call_projection as projection
from revival_radar.storage.database import SCHEMA_VERSION, connect
from revival_radar.storage.repository import Repository
from revival_radar.web.queries import PublicQueries, public_reader

NOW = 1_800_000_000.0
DAY = 86400


def add_call(db, identity, token, timestamp, call_mc, source=None):
    db.execute(
        "INSERT INTO alerts(id,timestamp,chain,contract_address,score,reason,delivery_status)"
        " VALUES(?,?,?,?,80,'r','sent')",
        (identity, timestamp, "sol", token),
    )
    db.execute(
        "INSERT INTO alert_signals(alert_id,price,market_cap,score) VALUES(?,?,?,80)",
        (identity, 0.5, call_mc),
    )
    # The frozen alert-time snapshot, labelled with its market-cap source.
    add_snapshot(db, token, timestamp, call_mc, source)


def add_snapshot(db, token, timestamp, market_cap, source=None, price=0.5):
    payload = json.dumps({"market_cap_source": source} if source else {})
    db.execute(
        "INSERT OR IGNORE INTO token_snapshots(chain,contract_address,timestamp,market_cap,"
        "price,payload) VALUES('sol',?,?,?,?,?)",
        (token, timestamp, market_cap, price, payload),
    )


def random_world(db, seed, calls=6, until=NOW):
    rng = random.Random(seed)
    tokens = [f"T{seed}x{i}" for i in range(3)]
    identity = db.execute("SELECT COALESCE(MAX(id),0) FROM alerts").fetchone()[0]
    for _ in range(calls):
        identity += 1
        token = rng.choice(tokens)
        called = until - rng.uniform(1, 40) * DAY
        call_mc = rng.choice([1e6, 2.5e5, None, 0, -3.0])
        source = rng.choice([None, "rank", "circulating_enriched"])
        add_call(db, identity, token, called, call_mc, source)
    for token in tokens:
        for _ in range(rng.randint(0, 60)):
            t = until - rng.uniform(0, 41) * DAY
            mc = rng.choice([rng.uniform(1e5, 5e6)] * 6 + [0, -1, None, float("inf")])
            source = rng.choice([None, "rank", "circulating_enriched"])
            add_snapshot(db, token, t, mc, source, price=rng.choice([0.5, 0, None]))
    db.commit()


def calls_of(db):
    return db.execute(
        PublicQueries._alerts_base() + "WHERE a.delivery_status='sent' ORDER BY a.id"
    ).fetchall()


def perf_pair(db, until):
    """(projection-assisted, forced on-read) performance for every call."""
    queries = PublicQueries(db)
    assert queries.has_projection
    fast = [queries._performance(r, until).model_dump() for r in calls_of(db)]
    slow_queries = PublicQueries(db)
    slow_queries._has_projection = False
    slow = [slow_queries._performance(r, until).model_dump() for r in calls_of(db)]
    return fast, slow


def table_hashes(db):
    result = {}
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name!='call_performance'"
    ):
        digest = hashlib.sha256()
        for row in db.execute(f"SELECT * FROM {name} ORDER BY 1,2"):
            digest.update(repr(tuple(row)).encode())
        result[name] = digest.hexdigest()
    return result


@pytest.fixture
def db(repo):
    return repo.db


def test_migration_is_additive_idempotent_and_schema_seven_upgrades(tmp_path):
    path = tmp_path / "seven.db"
    old = connect(path)
    random_world(old, 1)
    old.execute("DROP TABLE call_performance")
    old.execute("PRAGMA user_version=7")
    old.commit()
    before = table_hashes(old)
    old.close()
    for _ in range(2):  # a second start is a no-op
        upgraded = connect(path)
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 8
        assert upgraded.execute("SELECT COUNT(*) FROM call_performance").fetchone()[0] == 0
        assert table_hashes(upgraded) == before
        upgraded.close()


def test_incremental_equals_full_recompute(db):
    for seed in range(8):
        random_world(db, seed, calls=5, until=NOW - 3 * DAY)
    cursor = NOW - 3 * DAY
    for step in range(6):
        cursor += 0.5 * DAY
        rng = random.Random(step)
        for row in db.execute("SELECT DISTINCT contract_address FROM alerts").fetchall():
            for _ in range(rng.randint(0, 4)):
                source = rng.choice([None, "rank", "circulating_enriched"])
                add_snapshot(
                    db,
                    row[0],
                    cursor - rng.uniform(0, 0.49) * DAY,
                    rng.uniform(1e5, 5e6),
                    source,
                )
        db.commit()
        projection.refresh(db, cursor, 30)
        for stored in db.execute("SELECT * FROM call_performance WHERE frozen=0"):
            alert = db.execute("SELECT * FROM alerts WHERE id=?", (stored["alert_id"],)).fetchone()
            mc = db.execute(
                "SELECT market_cap FROM alert_signals WHERE alert_id=?", (alert["id"],)
            ).fetchone()[0]
            full = projection.build(
                db,
                alert["chain"],
                alert["contract_address"],
                alert["timestamp"],
                projection.positive(mc),
                cursor,
                hide_origins=True,
            )
            assert projection.row_to_state(stored, alert["timestamp"]) == full, stored["alert_id"]
            assert stored["basis"] == full.basis


@pytest.mark.parametrize("basis", ["same", "mixed", "unknown"])
def test_basis_cases_match_on_read(db, basis):
    call_source = None if basis == "unknown" else "rank"
    add_call(db, 1, "TKN", NOW - 5 * DAY, 1e6, call_source)
    other = "circulating_enriched"
    series = {
        "same": [("rank", 1.5e6), (other, 9e6), ("rank", 7e5), (None, 3e5)],
        "mixed": [(other, 1.1e6), (None, 9e5), (other, 2e6)],
        "unknown": [("rank", 1.1e6), (None, 9e5)],
    }[basis]
    for n, (source, mc) in enumerate(series):
        add_snapshot(db, "TKN", NOW - 4 * DAY + n * 3600, mc, source)
    db.commit()
    projection.refresh(db, NOW - 3600, 30)
    fast, slow = perf_pair(db, NOW)
    assert fast == slow and fast[0]["basis"] == basis
    stored = db.execute("SELECT * FROM call_performance").fetchone()
    assert stored["basis"] == basis
    # A first like-with-like row arriving after only mixed ones changes the basis rule.
    if basis == "mixed":
        add_snapshot(db, "TKN", NOW - 100, 4e5, "rank")
        db.commit()
        projection.refresh(db, NOW, 30)
        fast, slow = perf_pair(db, NOW)
        assert fast == slow and fast[0]["basis"] == "same"
        assert fast[0]["other_basis_observations"] == 3


def test_frozen_after_window_and_reads_stay_exact(db):
    add_call(db, 1, "OLD", NOW - 40 * DAY, 1e6, "rank")
    add_call(db, 2, "NEW", NOW - 2 * DAY, 1e6, "rank")
    for token, start in (("OLD", NOW - 39 * DAY), ("NEW", NOW - DAY)):
        add_snapshot(db, token, start, 1.2e6, "rank")
    db.commit()
    projection.refresh(db, NOW - 3600, 30)
    rows = {r["alert_id"]: r for r in db.execute("SELECT * FROM call_performance")}
    assert rows[1]["frozen"] == 1 and rows[2]["frozen"] == 0
    frozen_before = tuple(rows[1])
    add_snapshot(db, "OLD", NOW - 60, 5e5, "rank")
    add_snapshot(db, "NEW", NOW - 60, 5e5, "rank")
    db.commit()
    stats = projection.refresh(db, NOW, 30)
    assert stats["updated"] == 1  # only the active call is maintained
    assert tuple(db.execute("SELECT * FROM call_performance WHERE alert_id=1").fetchone()) == (
        frozen_before
    )
    fast, slow = perf_pair(db, NOW)
    assert fast == slow and fast[0]["latest_market_cap"] == 5e5  # newer data folded on read


def test_as_of_before_coverage_falls_back_without_future_data(db):
    add_call(db, 1, "TKN", NOW - 5 * DAY, 1e6, "rank")
    add_snapshot(db, "TKN", NOW - 4 * DAY, 2e6, "rank")
    add_snapshot(db, "TKN", NOW - 1 * DAY, 9e6, "rank")
    db.commit()
    projection.refresh(db, NOW, 30)
    queries = PublicQueries(db)
    row = calls_of(db)[0]
    early = NOW - 3 * DAY
    assert queries._projected(row, early, 1e6) is None
    result = queries._performance(row, early)
    assert result.latest_market_cap == 2e6 and result.mfe_pct == pytest.approx(100.0)
    assert queries._projected(row, NOW, 1e6) is not None


def test_changed_call_context_never_served(db):
    add_call(db, 1, "TKN", NOW - 5 * DAY, 1e6, "rank")
    add_snapshot(db, "TKN", NOW - 4 * DAY, 2e6, "rank")
    db.commit()
    projection.refresh(db, NOW - 10, 30)
    db.execute("UPDATE alert_signals SET market_cap=5e5 WHERE alert_id=1")
    db.commit()
    fast, slow = perf_pair(db, NOW)
    assert fast == slow
    projection.refresh(db, NOW, 30)  # a re-run rebuilds the row against the new context
    assert db.execute("SELECT call_market_cap FROM call_performance").fetchone()[0] == 5e5


def test_backfill_is_bounded_resumable_and_rebuildable(db):
    for i in range(1, 8):
        add_call(db, i, f"T{i}", NOW - i * DAY, 1e6, "rank")
        add_snapshot(db, f"T{i}", NOW - 60, 1.1e6, "rank")
    db.commit()
    assert projection.refresh(db, NOW, 30, limit=3)["built"] == 3
    assert projection.refresh(db, NOW, 30, limit=3)["built"] == 3
    assert projection.refresh(db, NOW, 30, limit=3)["built"] == 1
    assert db.execute("SELECT COUNT(*) FROM call_performance").fetchone()[0] == 7
    fast, slow = perf_pair(db, NOW)
    assert fast == slow
    saved = [tuple(r) for r in db.execute("SELECT * FROM call_performance ORDER BY alert_id")]
    db.execute("DELETE FROM call_performance")
    projection.refresh(db, NOW, 30)
    again = [tuple(r) for r in db.execute("SELECT * FROM call_performance ORDER BY alert_id")]
    assert again == saved


@pytest.mark.parametrize("seed", range(40))
def test_random_fixture_parity(db, seed):
    random_world(db, seed, calls=8, until=NOW - DAY)
    rng = random.Random(seed)
    # Refresh at an intermediate time; later snapshots must be folded in on read.
    mid = NOW - DAY * rng.uniform(0, 20)
    projection.refresh(db, mid, 30)
    for until in (NOW, mid, NOW - 35 * DAY, mid + 3600, mid - 3600):
        fast, slow = perf_pair(db, until)
        assert fast == slow, (seed, until)
    for row in db.execute("SELECT * FROM call_performance"):
        for value in (row["latest_market_cap"], row["max_market_cap"]):
            assert value is None or math.isfinite(value)


def test_schema_seven_database_is_read_without_projection(tmp_path):
    from revival_radar.web.config import WebConfig

    path = tmp_path / "seven.db"
    old = connect(path)
    add_call(old, 1, "TKN", NOW - 5 * DAY, 1e6, "rank")
    add_snapshot(old, "TKN", NOW - 60, 2e6, "rank")
    old.commit()
    expected = PublicQueries(old)._performance(calls_of(old)[0], NOW).model_dump()
    old.execute("DROP TABLE call_performance")
    old.execute("PRAGMA user_version=7")
    old.commit()
    old.close()
    config = WebConfig(database_path=path, web_cache_seconds=0, web_cookie_secure=False)
    with public_reader(config) as reader:
        assert not reader.has_projection
        row = calls_of(reader.db)[0]
        assert reader._performance(row, NOW).model_dump() == expected
        journal = reader.call_journal(NOW, 30, {}, "newest", 1, 25)
        assert journal.total == 1
    # The reader stayed read-only: still schema 7, no table created.
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 7
    assert not check.execute("SELECT 1 FROM sqlite_master WHERE name='call_performance'").fetchall()


def big_world(db, calls=2000, tokens=200, snapshots=1500):
    rng = random.Random(7)
    start = NOW - 80 * DAY
    for t in range(tokens):
        token = f"BIG{t}"
        db.executemany(
            "INSERT INTO token_snapshots(chain,contract_address,timestamp,market_cap,price,payload)"
            " VALUES('sol',?,?,?,0.5,?)",
            [
                (
                    token,
                    start + n * (79 * DAY / snapshots) + 1,
                    1e6 * rng.uniform(0.5, 2),
                    '{"market_cap_source":"rank"}',
                )
                for n in range(snapshots)
            ],
        )
    for i in range(calls):
        called = start + 3600 + rng.uniform(0, 77 * DAY)
        token = f"BIG{i % tokens}"
        row = db.execute(
            "SELECT market_cap FROM token_snapshots WHERE contract_address=? AND timestamp<=?"
            " ORDER BY timestamp DESC LIMIT 1",
            (token, called),
        ).fetchone()
        db.execute(
            "INSERT INTO alerts(id,timestamp,chain,contract_address,score,reason,delivery_status)"
            " VALUES(?,?,'sol',?,80,'r','sent')",
            (i + 1, called, token),
        )
        db.execute(
            "INSERT INTO alert_signals(alert_id,price,market_cap,score) VALUES(?,0.5,?,80)",
            (i + 1, row[0] if row else 1e6),
        )
    db.commit()


def test_cold_journal_summary_for_2000_calls_is_under_300ms(db, capsys):
    big_world(db)
    snaps = db.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0]
    projection.refresh(db, NOW - 60, 365, limit=5000)
    measured, summaries = {}, {}
    for mode in ("projection", "on_read"):
        queries = PublicQueries(db)
        if mode == "on_read":
            queries._has_projection = False
        start = time.perf_counter()
        journal = queries.call_journal(NOW, 90, {}, "newest", 1, 25)
        measured[mode] = time.perf_counter() - start
        assert journal.total == 2000
        summaries[mode] = journal.summary
    assert summaries["projection"] == summaries["on_read"]
    with capsys.disabled():
        print(
            f"\nJOURNAL 2000 calls / {snaps} snapshots: projection "
            f"{measured['projection'] * 1000:.0f} ms, on-read {measured['on_read'] * 1000:.0f} ms"
        )
    assert measured["projection"] < 0.3


def test_scanner_refresh_overhead_is_small_and_incremental(db, capsys):
    # ~6 calls/day over the 30-day window, five-minute snapshots on each token.
    rng = random.Random(3)
    for i in range(180):
        add_call(db, i + 1, f"A{i % 60}", NOW - rng.uniform(0, 29) * DAY, 1e6, "rank")
    for t in range(60):
        for n in range(2000):
            add_snapshot(db, f"A{t}", NOW - 30 * DAY + n * 300 + t, rng.uniform(5e5, 2e6), "rank")
    db.commit()
    first = projection.refresh(db, NOW - 600, 30)
    assert first["built"] == 180
    timings = []
    for scan in range(1, 6):
        now = NOW - 600 + scan * 300
        for t in range(60):
            add_snapshot(db, f"A{t}", now - 1, rng.uniform(5e5, 2e6), "rank")
        db.commit()
        stats = projection.refresh(db, now, 30)
        assert stats["updated"] == 180 and stats["built"] == 0
        timings.append(stats["seconds"])
    with capsys.disabled():
        print(
            f"\nSCANNER refresh: backfill 180 calls {first['seconds'] * 1000:.0f} ms; "
            f"steady state {[round(t * 1000, 1) for t in timings]} ms per scan"
        )
    assert max(timings) < 0.25
    fast, slow = perf_pair(db, NOW)
    assert fast == slow


def test_scanner_hook_never_fails_a_scan(repo, config):
    from revival_radar.scanner import Scanner

    class Boom(Repository):
        def refresh_call_performance(self, now, active_days):
            raise sqlite3.OperationalError("locked")

    Scanner(
        config, source=object(), repository=Boom(repo.db), telegram=object()
    ).maintain_call_performance()  # logged, not raised
    good = Scanner(config, source=object(), repository=repo, telegram=object())
    add_call(repo.db, 1, "TKN", time.time() - 100, 1e6, "rank")
    repo.db.commit()
    good.maintain_call_performance()
    assert repo.db.execute("SELECT COUNT(*) FROM call_performance").fetchone()[0] == 1

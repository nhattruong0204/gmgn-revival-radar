import hashlib
import sqlite3
import time

import httpx
import pytest

from revival_radar.storage.database import SCHEMA_VERSION, connect
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig
from revival_radar.web.queries import PublicQueries

from .test_track_record import AT, checkpoint, clock, sent  # noqa: F401

ROUTES = ("meta", "radar", "reports", "alerts", "track-record")


def versions(db):
    return {route: PublicQueries(db).version(AT, route) for route in ROUTES}


def put(db, key, value="live", at=1.0):
    db.execute("INSERT INTO state(key,value,updated_at) VALUES(?,?,?)", (key, value, at))


def to_schema_eleven(db):
    for name in ("insert", "update", "delete"):
        db.execute(f"DROP TRIGGER origin_version_{name}")
    db.execute("DROP TABLE origin_version")
    db.execute("PRAGMA user_version=11")
    db.commit()


def test_new_database_has_schema_twelve_and_an_empty_counter(tmp_path):
    db = connect(tmp_path / "s.db")
    assert SCHEMA_VERSION == 12
    assert tuple(db.execute("SELECT * FROM origin_version").fetchone()) == (1, 0, 0, 0, None)
    db.close()


def test_migration_is_additive_idempotent_and_seeds_from_existing_rows(tmp_path):
    path = tmp_path / "m.db"
    db = connect(path)
    for i in range(5):
        put(db, f"origin:alert:{i}", at=10 + i)
    put(db, "origin:scan:1", at=20)
    put(db, "origin:evaluation:1", at=21)
    put(db, "origin:snapshot:1", at=22)
    put(db, "provenance:audit:a", at=23)
    put(db, "telegram:evaluation:1", "{}", 24)
    to_schema_eleven(db)

    def digest():
        tables = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name!='origin_version'"
                " ORDER BY name"
            )
        ]
        return {
            t: hashlib.sha256(
                repr([tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1")]).encode()
            ).hexdigest()
            for t in tables
        }

    before = digest()
    db.close()
    for _ in range(3):
        db = connect(path)
        assert digest() == before
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
        assert tuple(db.execute("SELECT * FROM origin_version").fetchone()) == (1, 9, 6, 3, 23.0)
        objects = db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'origin_version%'"
        ).fetchone()[0]
        assert objects == 4  # one table and three triggers, never duplicated
        db.close()


MUTATIONS = {
    "insert": lambda db: put(db, "origin:alert:9"),
    "update": lambda db: db.execute(
        "UPDATE state SET value='synthetic' WHERE key='origin:alert:1'"
    ),
    "delete": lambda db: db.execute("DELETE FROM state WHERE key='origin:alert:1'"),
    "rename_into": lambda db: db.execute("UPDATE state SET key='origin:alert:7' WHERE key='plain'"),
    "rename_out_of": lambda db: db.execute(
        "UPDATE state SET key='plain2' WHERE key='origin:alert:1'"
    ),
    "rename_within": lambda db: db.execute(
        "UPDATE state SET key='origin:alert:8' WHERE key='origin:alert:1'"
    ),
    "replace": lambda db: db.execute(
        "INSERT OR REPLACE INTO state(key,value,updated_at) VALUES('origin:alert:1','x',5)"
    ),
    "upsert": lambda db: db.execute(
        "INSERT INTO state(key,value,updated_at) VALUES('origin:alert:1','x',5)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    ),
    "attestation": lambda db: put(db, "provenance:audit:review", "reviewed"),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_every_relevant_state_write_bumps_the_version(tmp_path, name):
    db = connect(tmp_path / "v.db")
    put(db, "origin:alert:1")
    put(db, "plain", "1")
    db.commit()
    before = versions(db)
    MUTATIONS[name](db)
    db.commit()
    after = versions(db)
    assert after["alerts"] != before["alerts"]  # the full counter moves on every case
    assert after["track-record"] != before["track-record"]  # all of these touch alert keys
    if name == "attestation":
        assert all(after[route] != before[route] for route in ROUTES)


def test_unrelated_state_writes_do_not_bump(tmp_path):
    db = connect(tmp_path / "u.db")
    put(db, "origin:alert:1")
    db.commit()
    before = versions(db)
    put(db, "telegram:evaluation:1", "{}")
    db.execute("UPDATE state SET value='y' WHERE key='telegram:evaluation:1'")
    db.execute("DELETE FROM state WHERE key='telegram:evaluation:1'")
    db.commit()
    assert versions(db) == before


def test_ignored_duplicate_origin_is_not_a_change(tmp_path):
    db = connect(tmp_path / "d.db")
    put(db, "origin:alert:1")
    db.commit()
    before = versions(db)
    db.execute("INSERT OR IGNORE INTO state(key,value,updated_at) VALUES('origin:alert:1','x',2)")
    db.commit()
    assert versions(db) == before


def test_per_route_counters_keep_unrelated_origins_quiet(tmp_path):
    db = connect(tmp_path / "r.db")
    base = versions(db)
    put(db, "origin:snapshot:1")
    db.commit()
    after = versions(db)
    assert after["meta"] == base["meta"] and after["track-record"] == base["track-record"]
    assert after["alerts"] != base["alerts"]
    put(db, "origin:scan:1")
    db.commit()
    assert versions(db)["meta"] != after["meta"]
    assert versions(db)["track-record"] == after["track-record"]
    put(db, "origin:alert:1")
    db.commit()
    assert versions(db)["track-record"] != after["track-record"]


def test_schema_eleven_falls_back_to_the_exact_legacy_queries(tmp_path):
    db = connect(tmp_path / "f.db")
    put(db, "origin:alert:1", at=3)
    put(db, "origin:scan:1", at=4)
    put(db, "provenance:audit:a", at=5)
    to_schema_eleven(db)
    queries = PublicQueries(db)
    assert queries._origin_counter("counter") is None
    legacy_all = tuple(
        db.execute(
            "SELECT COUNT(*),MAX(updated_at) FROM state "
            "WHERE key GLOB 'origin:*' OR key GLOB 'provenance:audit:*'"
        ).fetchone()
    )
    assert legacy_all == (3, 5.0)
    assert tuple(queries.version(AT, "alerts"))[1:3] == legacy_all
    legacy_alert = (2, 5.0)  # origin:alert + provenance:audit only
    assert tuple(queries.version(AT, "track-record"))[:2] == legacy_alert
    before = queries.version(AT, "track-record")
    put(db, "provenance:audit:b", at=6)
    assert queries.version(AT, "track-record") != before


def test_version_stays_fast_with_a_large_state_table(tmp_path):
    path = tmp_path / "big.db"
    db = connect(path)
    db.executemany(
        "INSERT INTO state(key,value,updated_at) VALUES(?,?,?)",
        ((f"origin:snapshot:{i}", "live", float(i)) for i in range(300_000)),
    )
    db.commit()
    db.close()
    ro = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    queries = PublicQueries(ro)
    for route in ROUTES:
        queries.version(AT, route)
        start = time.perf_counter()
        for _ in range(20):
            queries.version(AT, route)
        assert (time.perf_counter() - start) / 20 < 0.005, route
    ro.close()


async def test_cached_response_is_never_served_after_an_origin_change(
    repo,
    token,
    config,
    clock,  # noqa: F811
):
    alert_id, snapshot = sent(repo, token, config, clock)
    checkpoint(repo, snapshot, clock, 2)
    path = repo.db.execute("PRAGMA database_list").fetchone()[2]
    app = create_app(WebConfig(database_path=path, web_cache_seconds=60), clock=lambda: AT)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        url = "/api/v1/track-record?horizon=1"
        first = (await client.get(url)).json()
        assert first["total"] == 1
        assert (await client.get(url)).json() == first  # cached
        # Reclassify out of band: a new row, then an in-place update of the same key.
        repo.db.execute("DELETE FROM state WHERE key=?", (f"origin:alert:{alert_id}",))
        repo.db.execute(
            "INSERT INTO state(key,value,updated_at) VALUES(?,?,?)",
            (f"origin:alert:{alert_id}", "synthetic", AT),
        )
        repo.db.commit()
        assert (await client.get(url)).json()["total"] == 0
        repo.db.execute("UPDATE state SET value='live' WHERE key=?", (f"origin:alert:{alert_id}",))
        repo.db.commit()
        assert (await client.get(url)).json() == first

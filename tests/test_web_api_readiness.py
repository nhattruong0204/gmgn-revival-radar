import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from revival_radar.storage.database import SCHEMA_VERSION
from revival_radar.web.app import create_app
from revival_radar.web.queries import PublicQueries, public_reader

from .conftest import changed
from .test_web_public import NOW, observe
from .test_web_public import web as web


async def test_radar_cache_detects_late_completion_with_same_finished_time(
    web, repo, token, config
):
    client, app = web
    app.state.config.web_cache_seconds = 60
    observe(repo, token, config, finished=False)
    pending = repo.db.execute("SELECT MAX(id) FROM scan_runs").fetchone()[0]
    observe(repo, changed(token, contract_address="B" * 32), config)
    assert (await client.get("/api/v1/radar")).json()["total"] == 1
    # MAX(id) and MAX(finished) stay identical; the completed count changes.
    repo.finish_scan(pending, SimpleNamespace(processed=1), NOW - 99)
    assert (await client.get("/api/v1/radar")).json()["total"] == 2


@pytest.mark.parametrize("version", [SCHEMA_VERSION - 1, SCHEMA_VERSION + 1])
async def test_unsupported_schema_is_rejected_without_migration(web, repo, token, config, version):
    client, _ = web
    observe(repo, token, config)
    repo.db.execute(f"PRAGMA user_version={version}")
    before = list(repo.db.iterdump())
    for route in ("/radar", "/reports", "/outcomes", "/meta"):
        response = await client.get("/api/v1" + route)
        assert response.status_code == 503
        assert "schema" not in response.text.lower()
    assert repo.db.execute("PRAGMA user_version").fetchone()[0] == version
    assert list(repo.db.iterdump()) == before


async def test_cache_invalidates_late_lower_id_delivery_and_new_outcome(web, repo, token, config):
    client, app = web
    app.state.config.web_cache_seconds = 60
    live = config.model_copy(update={"dry_run": False})
    snapshot, signal, context = observe(repo, token, live, timestamp=NOW - 7200)
    first = repo.reserve_alert(snapshot, signal, live, context)
    other, other_signal, other_context = observe(
        repo, changed(token, contract_address="B" * 32), live, timestamp=NOW - 7200
    )
    second = repo.reserve_alert(other, other_signal, live, other_context)
    repo.finish_alert(second, "sent")
    assert (await client.get("/api/v1/alerts")).json()["total"] == 1
    assert (await client.get("/api/v1/outcomes")).json()["horizons"]["1"]["observed"] == 0
    repo.finish_alert(first, "sent")
    assert (await client.get("/api/v1/alerts")).json()["total"] == 2
    assert (await client.get(f"/api/v1/alerts/{first}")).json()["outcomes"] == []
    assert (await client.get("/api/v1/outcomes")).json()["horizons"]["1"]["pending"] == 2
    # observe_outcomes writes a checkpoint but does not insert a market snapshot.
    repo.observe_outcomes(changed(snapshot, timestamp=NOW - 3600, price=snapshot.price * 2))
    assert (await client.get("/api/v1/outcomes")).json()["horizons"]["1"][
        "median_return_pct"
    ] == 100
    assert len((await client.get(f"/api/v1/alerts/{first}")).json()["outcomes"]) == 1


async def test_candle_cache_arrival_invalidates_empty_history(web, repo, token, config):
    client, app = web
    app.state.config.web_cache_seconds = 60
    observe(repo, token, config)
    route = f"/api/v1/tokens/sol/{token.contract_address}/history"
    assert (await client.get(route)).json()["candles"] == []
    repo.cache_save(
        "kline:1h:168",
        changed(token, timestamp=NOW - 10),
        json.dumps(
            {"candles": [{"timestamp": NOW - 3600, "open": 1, "high": 2, "low": 1, "close": 2}]}
        ),
        NOW + 100,
    )
    result = (await client.get(route)).json()
    assert len(result["candles"]) == 1
    assert result["candles_fetched_at"] == NOW - 10


async def test_duplicate_symbols_case_sensitive_identity_and_literal_search(
    web, repo, token, config
):
    client, _ = web
    for address in ("A" * 32, "a" * 32, "B" * 32):
        observe(repo, changed(token, contract_address=address, symbol="SAME"), config)
    addresses = []
    for page in (1, 2, 3):
        result = (await client.get(f"/api/v1/radar?page_size=1&page={page}")).json()
        assert result["total"] == 3
        addresses.append(result["items"][0]["contract_address"])
    assert len(set(addresses)) == 3
    for address in addresses:
        detail = (await client.get(f"/api/v1/tokens/sol/{address}")).json()
        assert detail["observation"]["contract_address"] == address
    result = await client.get("/api/v1/radar", params={"search": "%' OR 1=1 --"})
    assert result.status_code == 200 and result.json()["total"] == 0
    assert repo.db.execute("SELECT count(*) FROM evaluations").fetchone()[0] == 3


async def test_invalid_saved_identity_cannot_supply_search_or_sort_metrics(
    web, repo, token, config
):
    client, _ = web
    observe(repo, token, config, score=90)
    other = changed(token, contract_address="B" * 32, market_cap=100)
    observe(repo, other, config, score=70)
    row = repo.db.execute(
        "SELECT key,value FROM state WHERE key='telegram:evaluation:1'"
    ).fetchone()
    value = json.loads(row["value"])
    value["token"].update(contract_address="C" * 32, name="Poisoned evidence", market_cap=1e12)
    repo.set_state(row["key"], json.dumps(value))
    assert (await client.get("/api/v1/radar?search=Poisoned")).json()["total"] == 0
    result = (await client.get("/api/v1/radar?sort=market_cap")).json()
    assert result["items"][0]["contract_address"] == other.contract_address
    assert result["items"][1]["market_cap"] is None


@pytest.mark.parametrize("hours", [1, 4, 12, 24, 72, 168])
async def test_all_report_windows_include_below_threshold_and_match_bot(
    web, repo, token, config, hours
):
    client, _ = web
    for index, score in enumerate((20, 39, 60, 90)):
        observe(
            repo,
            changed(token, contract_address="ABCD"[index] * 32),
            config,
            timestamp=NOW - 100 - index * 7200,
            score=score,
            status="IGNORE" if score < 40 else "WATCH",
        )
    expected = repo.market_report(NOW - hours * 3600, NOW, timezone="Asia/Ho_Chi_Minh")
    result = (await client.get(f"/api/v1/reports?hours={hours}")).json()
    assert [entry["peak"]["id"] for entry in result["entries"]] == [
        entry["peak"]["id"] for entry in expected["entries"]
    ]
    assert any(entry["peak"]["score"] < config.alert_score_threshold for entry in result["entries"])


async def test_all_public_reads_are_inert_and_coexist_with_scan_writes(
    web, repo, token, config, monkeypatch
):
    client, app = web
    observe(repo, token, config)
    live = config.model_copy(update={"dry_run": False})
    snapshot, signal, context = observe(repo, token, live, timestamp=NOW - 7200)
    identity = repo.reserve_alert(snapshot, signal, live, context)
    repo.finish_alert(identity, "sent")

    def forbidden(*args, **kwargs):
        raise AssertionError("Public reads must not migrate, scan or call the network")

    monkeypatch.setattr("revival_radar.storage.database.connect", forbidden)
    monkeypatch.setattr("revival_radar.scanner.Scanner.scan_once", forbidden)
    monkeypatch.setattr("revival_radar.clients.gmgn.GMGNClient.__init__", forbidden)
    monkeypatch.setattr("revival_radar.clients.helius.HeliusClient.__init__", forbidden)
    monkeypatch.setattr("revival_radar.clients.telegram.TelegramClient.__init__", forbidden)
    create_app(app.state.config, clock=lambda: NOW)
    # Network clients are never constructed by the public API. Request-owned ASGI
    # transport is the only HTTP transport used in this test.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)

    routes = [
        "/radar",
        f"/tokens/sol/{token.contract_address}",
        f"/tokens/sol/{token.contract_address}/history",
        "/alerts",
        f"/alerts/{identity}",
        "/reports",
        "/outcomes",
        "/meta",
    ]
    readers = asyncio.gather(*[client.get("/api/v1" + route) for route in routes for _ in range(3)])
    for index in range(5):
        observe(repo, token, config, timestamp=NOW - 50 + index, score=70 + index)
        await asyncio.sleep(0)
    responses = await readers
    assert all(response.status_code == 200 for response in responses)
    assert (await client.get("/api/v1/radar")).json()["items"][0]["score"] == 74

    # A held WAL read snapshot remains internally consistent while the writer commits.
    def held_read():
        with public_reader(app.state.config) as queries:
            before = queries.db.execute("SELECT count(*) FROM evaluations").fetchone()[0]
            ready.set()
            assert released.wait(5)
            return before, queries.db.execute("SELECT count(*) FROM evaluations").fetchone()[0]

    ready, released = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(held_read)
        assert ready.wait(5)
        observe(repo, token, config, timestamp=NOW - 10, score=99)
        released.set()
        before, after = future.result(timeout=5)
    assert before == after
    assert repo.db.execute("SELECT count(*) FROM evaluations").fetchone()[0] == after + 1


async def test_query_deadline_returns_safe_service_error(web, monkeypatch):
    client, app = web
    app.state.config.web_read_timeout_ms = 100

    def expensive(self, until, hours):
        self.db.execute(
            "WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL "
            "SELECT n+1 FROM numbers WHERE n<10000000) SELECT sum(n) FROM numbers"
        ).fetchone()

    monkeypatch.setattr(PublicQueries, "report", expensive)
    response = await client.get("/api/v1/reports")
    assert response.status_code == 503
    assert response.json() == {"detail": "Saved observations temporarily unavailable"}


async def test_outcomes_keep_missing_prices_out_of_returns_and_match_bot(web, repo, token, config):
    client, _ = web
    live = config.model_copy(update={"dry_run": False})
    missing, signal, context = observe(
        repo, changed(token, price=None), live, timestamp=NOW - 10800
    )
    first = repo.reserve_alert(missing, signal, live, context)
    repo.finish_alert(first, "sent")
    repo.observe_outcomes(changed(missing, timestamp=NOW - 7200, price=1))
    fresh, signal, context = observe(
        repo, changed(token, contract_address="B" * 32), live, timestamp=NOW - 100
    )
    second = repo.reserve_alert(fresh, signal, live, context)
    repo.finish_alert(second, "sent")
    result = (await client.get("/api/v1/outcomes")).json()
    assert result["horizons"] == repo.outcome_summary(NOW - 7 * 86400, NOW)
    assert result["horizons"]["1"] == {
        "observed": 1,
        "returns_available": 0,
        "positive": 0,
        "median_return_pct": None,
        "pending": 1,
        "missed": 0,
    }


def test_concurrent_cache_misses_share_one_fill_without_blocking_other_keys():
    from threading import Barrier, Event

    from pydantic import BaseModel

    from revival_radar.web.public import ResponseCache

    class Value(BaseModel):
        value: int

    cache = ResponseCache()
    started, release, barrier = Event(), Event(), Barrier(6)
    calls = []

    def fill():
        calls.append(True)
        started.set()
        assert release.wait(2)
        return Value(value=7)

    def read():
        barrier.wait(timeout=2)
        return cache.get_or_create(("report", 1), fill, 10)

    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(read) for _ in range(6)]
        try:
            assert started.wait(2)
            unrelated = pool.submit(cache.get_or_create, ("meta", 1), lambda: Value(value=9), 10)
            assert unrelated.result(timeout=1).value == 9
        finally:
            release.set()
        assert [future.result(timeout=2).value for future in futures] == [7] * 6
    assert len(calls) == 1 and not cache.filling
    assert cache.get_or_create(("report", 2), lambda: Value(value=8), 10).value == 8


def test_failed_cache_fill_releases_waiters_and_does_not_cache_errors():
    from threading import Event

    from pydantic import BaseModel

    from revival_radar.web.public import ResponseCache

    class Value(BaseModel):
        value: int

    cache, started, release = ResponseCache(), Event(), Event()

    def fail():
        started.set()
        assert release.wait(2)
        raise sqlite3.OperationalError("synthetic interruption")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_create, ("report", 1), fail, 10)
        assert started.wait(2)
        second = pool.submit(cache.get_or_create, ("report", 1), lambda: Value(value=8), 10)
        release.set()
        with pytest.raises(sqlite3.OperationalError):
            first.result(timeout=2)
        assert second.result(timeout=2).value == 8
    assert not cache.filling

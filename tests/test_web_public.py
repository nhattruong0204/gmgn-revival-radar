import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig
from revival_radar.web.queries import public_reader

from .conftest import changed

NOW = 1_800_000_000
SECRET = "private-owner-credential-marker"


async def test_brief_api_cutoff_bounds_and_saved_only_reads(web, repo, token, config):
    client, _ = web
    observe(repo, token, config, timestamp=NOW - 100)
    path = f"/api/v1/tokens/sol/{token.contract_address}/briefs"
    before = repo.db.total_changes
    result = await client.get(path)
    assert result.status_code == 200 and result.json()["total"] == 1
    brief = result.json()["items"][0]
    assert brief["strategy_revision"] == 4 and brief["outcome"] == "watch"
    assert SECRET not in result.text
    assert (await client.get(path + f"?as_of={NOW - 101}")).json()["items"] == []
    for query in ("as_of=nan", f"as_of={NOW + 1}", "page=0", "page_size=101"):
        assert (await client.get(path + "?" + query)).status_code == 422
    assert repo.db.total_changes == before


def observe(repo, token, config, *, timestamp=NOW - 100, score=88, finished=True, **values):
    snapshot = changed(token, timestamp=timestamp)
    context = {
        "preset": "Balanced",
        "revision": 4,
        "telegram_chat_id": SECRET,
        "settings": {"gmgn_api_key": SECRET},
    }
    signal = RevivalResult(
        score=score,
        status=values.pop("status", "CONFIRMED_REVIVAL"),
        eligible=True,
        setup_score=70,
        trigger_score=60,
        confirmation_score=80,
        returning_activity=True,
        structure=Structure(available=True, base_detected=True, base_duration_hours=48),
        **values,
    )
    scan = repo.begin_scan(timestamp - 10, context)
    repo.save_snapshot(snapshot)
    repo.record_evaluation(scan, snapshot, signal, config, context)
    if finished:
        repo.finish_scan(scan, SimpleNamespace(processed=1), timestamp + 1)
    return snapshot, signal, context


@pytest.fixture
async def web(repo):
    database = repo.db.execute("PRAGMA database_list").fetchone()[2]
    config = WebConfig(database_path=database, web_cache_seconds=0, web_cookie_secure=False)
    app = create_app(config, clock=lambda: NOW)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, app


async def test_radar_latest_completed_evidence_not_newer_market(web, repo, token, config):
    client, _ = web
    observe(repo, token, config, timestamp=NOW - 300, score=95)
    original, _, _ = observe(repo, token, config, timestamp=NOW - 200, score=80)
    observe(repo, token, config, timestamp=NOW - 50, score=99, finished=False)
    observe(repo, token, config, timestamp=NOW + 5, score=100)
    latest = changed(token, timestamp=NOW - 10, market_cap=123456, price=0.123)
    repo.save_snapshot(latest)
    response = await client.get("/api/v1/radar")
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    saved = result["items"][0]
    assert saved["score"] == 80 and saved["timestamp"] == NOW - 200
    assert saved["market_cap"] == original.market_cap
    detail = (await client.get(f"/api/v1/tokens/sol/{token.contract_address}")).json()
    assert detail["observation"]["score"] == 80
    assert detail["latest_market"]["market_cap"] == 123456
    assert detail["latest_market"]["timestamp"] == NOW - 10
    assert result["last_scan_at"] == NOW - 199


async def test_filter_after_latest_selection_and_strict_identity(web, repo, token, config):
    client, _ = web
    observe(repo, token, config, timestamp=NOW - 200, score=95)
    observe(repo, token, config, timestamp=NOW - 100, score=20, status="IGNORE")
    second = changed(token, contract_address="B" * 32, symbol="Second", name="Signal Alpha")
    observe(repo, second, config, score=82)
    bsc = changed(token, chain="bsc", contract_address="0x" + "a" * 40, symbol="EVM")
    observe(repo, bsc, config, score=75)
    base = changed(bsc, chain="base")
    observe(repo, base, config, score=74)
    result = (await client.get("/api/v1/radar?min_score=40&page_size=2")).json()
    assert result["total"] == 3 and len(result["items"]) == 2
    assert [item["score"] for item in result["items"]] == [82, 75]
    result = (await client.get("/api/v1/radar?include_ignore=true&sort=score&order=asc")).json()
    assert result["total"] == 4 and result["items"][0]["score"] == 20
    assert (await client.get("/api/v1/radar?search=alpha")).json()["total"] == 1
    assert (await client.get("/api/v1/radar?search=%25")).json()["total"] == 0
    assert (await client.get("/api/v1/radar?search=" + "b" * 32)).json()["total"] == 0
    assert (await client.get("/api/v1/radar?chain=base")).json()["total"] == 1
    uppercase = "0x" + "A" * 40
    assert (await client.get(f"/api/v1/tokens/bsc/{uppercase}")).json()["observation"][
        "contract_address"
    ] == bsc.contract_address


async def test_legacy_corrupt_details_remain_unknown(web, repo, token, config):
    client, _ = web
    observe(repo, token, config)
    repo.db.execute("UPDATE state SET value='invalid JSON' WHERE key LIKE 'telegram:evaluation:%'")
    repo.db.commit()
    row = (await client.get("/api/v1/radar")).json()["items"][0]
    assert row["token"] is None and row["signal"] is None
    assert row["setup_score"] is None and row["market_cap"] is None
    assert row["score"] == 88 and row["missing_fields"]
    assert "Saved detail evidence unavailable" in row["warnings"]


async def test_history_completed_scores_cached_closed_candles_and_bounds(
    web,
    repo,
    token,
    config,
):
    client, _ = web
    observe(repo, token, config, timestamp=NOW - 7200, score=70)
    observe(repo, token, config, timestamp=NOW - 3600, score=80)
    observe(repo, token, config, timestamp=NOW - 100, score=99, finished=False)
    repo.cache_save(
        "kline:1h:168",
        changed(token, timestamp=NOW - 50),
        json.dumps(
            {
                "hour": int(NOW // 3600),
                "candles": [
                    {"timestamp": timestamp, "open": 1, "high": 3, "low": 1, "close": 2}
                    for timestamp in (NOW - 7200, NOW - 3600, NOW - 100)
                ],
            }
        ),
        NOW + 3600,
    )
    route = f"/api/v1/tokens/sol/{token.contract_address}/history"
    result = (await client.get(route)).json()
    assert [point["score"] for point in result["scores"]] == [70, 80]
    assert len(result["snapshots"]) == 3
    assert [bar["timestamp"] for bar in result["candles"]] == [NOW - 7200, NOW - 3600]
    assert result["candles_fetched_at"] == NOW - 50
    limited = (await client.get(route + "?max_points=1")).json()
    assert limited["truncated"] and len(limited["scores"]) == 1
    assert limited["scores"][0]["score"] == 80
    historical = (await client.get(route, params={"as_of": NOW - 3000})).json()
    assert historical["candles"] == []  # Current cache was unavailable at that cutoff.


async def test_sent_alerts_preserve_original_and_exclude_other_deliveries(web, repo, token, config):
    client, _ = web
    live_config = config.model_copy(update={"dry_run": False})
    ids = {}
    for index, state in enumerate(("sent", "pending", "failed", "unknown")):
        candidate = changed(token, contract_address="ABCDEFGH"[index] * 32)
        snapshot, signal, context = observe(repo, candidate, live_config, timestamp=NOW - 7200)
        identity = repo.reserve_alert(snapshot, signal, live_config, context)
        if state != "pending":
            repo.finish_alert(identity, state, message_id=123456789)
        ids[state] = identity
        if state == "sent":
            observe(repo, candidate, live_config, score=95)
            repo.observe_outcomes(changed(snapshot, timestamp=NOW - 3600, price=snapshot.price * 2))
    result = (await client.get("/api/v1/alerts")).json()
    assert result["total"] == 1 and result["items"][0]["id"] == ids["sent"]
    assert result["items"][0]["observation"]["score"] == 88
    detail = (await client.get(f"/api/v1/alerts/{ids['sent']}")).json()
    assert detail["observation"]["score"] == 88
    assert detail["latest_observation"]["score"] == 95
    assert detail["outcomes"][0]["return_pct"] == 100
    for state in ("pending", "failed", "unknown"):
        assert (await client.get(f"/api/v1/alerts/{ids[state]}")).status_code == 404
    assert SECRET not in json.dumps(result) and "telegram_message_id" not in json.dumps(detail)


async def test_reports_and_outcomes_agree_with_bot_and_do_not_leak_state(web, repo, token, config):
    client, _ = web
    live = config.model_copy(update={"dry_run": False})
    snapshot, signal, context = observe(repo, token, live, timestamp=NOW - 7200, score=95)
    identity = repo.reserve_alert(snapshot, signal, live, context)
    repo.finish_alert(identity, "sent", message_id=987654321)
    repo.observe_outcomes(changed(snapshot, timestamp=NOW - 3600, price=snapshot.price * 0.5))
    observe(repo, token, live, score=70)
    observe(repo, token, live, timestamp=NOW - 20, score=100, finished=False)
    repo.set_state("secret-config", json.dumps({"chat_id": SECRET, "token": SECRET}))
    raw = repo.market_report(NOW - 86400, NOW, timezone="Asia/Ho_Chi_Minh")
    report = await client.get("/api/v1/reports?hours=24")
    assert report.status_code == 200
    public = report.json()
    for name in (
        "evaluations",
        "unique_tokens",
        "completed_scans",
        "incomplete_scans",
        "first_observation",
        "last_finished",
        "multiple_configurations",
    ):
        assert public[name] == raw[name]
    assert public["entries"][0]["peak"]["id"] == raw["entries"][0]["peak"]["id"]
    assert public["entries"][0]["latest"]["score"] == 70
    assert '"configuration"' not in json.dumps(public)
    outcomes = (await client.get("/api/v1/outcomes")).json()
    assert outcomes["horizons"] == repo.outcome_summary(NOW - 7 * 86400, NOW)
    assert outcomes["horizons"]["1"]["median_return_pct"] == -50
    for response in (public, outcomes):
        assert SECRET not in json.dumps(response)
    meta = (await client.get("/api/v1/meta")).json()
    assert meta["chains"] == [{"id": "sol", "name": "Solana"}]


@pytest.mark.parametrize(
    "route",
    [
        "/radar?page_size=101",
        "/radar?page=10001",
        "/radar?min_score=101",
        "/radar?sort=telegram_chat_id",
        "/radar?chain=eth",
        "/radar?status=private",
        "/radar?search=" + "a" * 101,
        "/reports?hours=2",
        "/outcomes?days=8",
        "/tokens/sol/invalid",
        "/tokens/sol/" + "A" * 32 + "/history?hours=169",
        "/tokens/sol/" + "A" * 32 + "/history?max_points=1001",
        "/alerts/0",
        "/radar?as_of=nan",
        "/radar?as_of=" + str(NOW + 1),
    ],
)
async def test_http_query_bounds(web, route):
    client, _ = web
    assert (await client.get("/api/v1" + route)).status_code == 422


async def test_all_public_reads_leave_database_unchanged_and_parallel_reads_succeed(
    web,
    repo,
    token,
    config,
):
    client, app = web
    observe(repo, token, config)
    before = list(repo.db.iterdump())
    routes = (
        "/radar",
        f"/tokens/sol/{token.contract_address}",
        f"/tokens/sol/{token.contract_address}/history",
        "/alerts",
        "/reports",
        "/outcomes",
    )
    responses = await asyncio.gather(
        *[client.get("/api/v1" + route) for route in routes for _ in range(3)]
    )
    assert all(response.status_code == 200 for response in responses)
    assert list(repo.db.iterdump()) == before
    with public_reader(app.state.config) as queries, pytest.raises(sqlite3.OperationalError):
        queries.db.execute("INSERT INTO state VALUES ('oops','private',1)")


async def test_public_response_cache_invalidates_after_completed_scan(web, repo, token, config):
    client, app = web
    app.state.config.web_cache_seconds = 60
    observe(repo, token, config, timestamp=NOW - 200, score=70)
    assert (await client.get("/api/v1/radar")).json()["items"][0]["score"] == 70
    observe(repo, token, config, timestamp=NOW - 100, score=90)
    assert (await client.get("/api/v1/radar")).json()["items"][0]["score"] == 90


async def test_missing_database_startup_is_inert_and_errors_are_safe(tmp_path, monkeypatch):
    database = tmp_path / "absent" / "missing.db"

    def forbidden(*args, **kwargs):
        raise AssertionError("Web server must never migrate or start a scanner")

    monkeypatch.setattr("revival_radar.storage.database.connect", forbidden)
    app = create_app(WebConfig(database_path=database), clock=lambda: NOW)
    assert not database.parent.exists()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/radar")
    assert response.status_code == 503 and str(database) not in response.text
    assert not database.parent.exists()


async def test_rate_limit_and_no_request_values_in_validation_error(web):
    client, app = web
    app.state.config.web_public_rate_limit = 10
    for _ in range(10):
        assert (await client.get("/api/v1/meta")).status_code == 200
    limited = await client.get("/api/v1/meta")
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
    error = await client.post("/api/v1/admin/session", json={"password": {"secret": SECRET}})
    assert error.status_code in (422, 503)
    assert SECRET not in error.text
    assert error.headers["cache-control"] == "no-store"
    too_big = await client.post("/api/v1/admin/session", content="x" * 16385)
    assert too_big.status_code == 413


async def test_candle_history_retains_usd_volume_and_unknown_without_open_bars(
    web, repo, token, config
):
    client, _ = web
    observe(repo, token, config, timestamp=NOW - 7200, score=70)
    bars = [
        {
            "timestamp": NOW - hours * 3600,
            "open": 1,
            "high": 3,
            "low": 1,
            "close": 2,
            "volume_usd": volume,
        }
        for hours, volume in ((3, None), (2, 0), (1, 125.75), (0, 999))
    ]
    repo.cache_save(
        "kline:1h:168",
        changed(token, timestamp=NOW - 50),
        json.dumps({"candles": bars}),
        NOW + 3600,
    )
    result = (await client.get(f"/api/v1/tokens/sol/{token.contract_address}/history")).json()
    assert [bar["volume_usd"] for bar in result["candles"]] == [None, 0, 125.75]
    assert [bar["close"] for bar in result["candles"]] == [2, 2, 2]
    assert all(bar["timestamp"] + 3600 <= result["as_of"] for bar in result["candles"])
    assert result["candles_origin"] == "unknown"

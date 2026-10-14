import hashlib
import json
import sqlite3
import time

import pytest

from .conftest import changed
from .test_web_public import NOW, observe
from .test_web_public import web as web

CALL_MC = 1_000_000.0


def make_call(repo, token, config, *, timestamp, call_mc=CALL_MC, score=88, source=None):
    live = config.model_copy(update={"dry_run": False})
    snapshot, signal, context = observe(
        repo,
        changed(token, market_cap=call_mc, market_cap_source=source),
        live,
        timestamp=timestamp,
        score=score,
    )
    identity = repo.reserve_alert(snapshot, signal, live, context)
    repo.finish_alert(identity, "sent", message_id=123456789)
    return identity


def snap(repo, token, timestamp, market_cap, price=0.5, source=None):
    repo.save_snapshot(
        changed(
            token,
            timestamp=timestamp,
            market_cap=market_cap,
            price=price,
            market_cap_source=source,
        )
    )


def set_call_mc(repo, identity, value):
    repo.db.execute("UPDATE alert_signals SET market_cap=? WHERE alert_id=?", (value, identity))
    repo.db.commit()


async def perf(client, identity, suffix=""):
    return (await client.get(f"/api/v1/alerts/{identity}{suffix}")).json()["performance"]


@pytest.mark.parametrize(
    ("latest", "expected"), [(1_250_000, 25.0), (800_000, -20.0), (1_000_000, 0.0)]
)
async def test_change_arithmetic(web, repo, token, config, latest, expected):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 60, latest)
    p = await perf(client, identity)
    assert p["change_since_call_pct"] == pytest.approx(expected, abs=1e-9)
    assert p["change_reason"] is None
    assert p["call_market_cap"] == CALL_MC and p["latest_market_cap"] == latest
    assert p["latest_observed_at"] == NOW - 60 and p["latest_price"] == 0.5
    assert p["elapsed_seconds"] == 7200 and p["freshness"] == "fresh"


@pytest.mark.parametrize("bad", [None, 0, -5, float("nan")])
async def test_unusable_call_market_cap_is_unknown_with_reason(web, repo, token, config, bad):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 60, 2_000_000)
    set_call_mc(repo, identity, bad)
    p = await perf(client, identity)
    assert p["change_since_call_pct"] is None and p["change_reason"] == "no_call_mc"
    assert p["mfe_pct"] is None and p["mae_pct"] is None and p["call_market_cap"] is None
    # The later observation itself is still reported.
    assert p["latest_market_cap"] == 2_000_000


async def test_no_later_snapshot_and_nonpositive_latest(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 60, 0)  # market_cap 0 is not an observation
    p = await perf(client, identity)
    assert p["change_reason"] == "no_later_observation" and p["change_since_call_pct"] is None
    assert p["freshness"] == "none" and p["observations"] == 0
    assert p["latest_market_cap"] is None and p["latest_observed_at"] is None
    assert p["max_gap_seconds"] is None and p["mfe_pct"] is None
    assert p["token_call_index"] == 1 and p["token_call_count"] == 1


async def test_stale_latest_after_thirty_minutes(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 1801, 1_100_000)
    assert (await perf(client, identity))["freshness"] == "stale"
    snap(repo, token, NOW - 1800, 1_200_000)
    assert (await perf(client, identity))["freshness"] == "fresh"


async def test_two_calls_share_latest_with_own_baselines(web, repo, token, config):
    client, _ = web
    first = make_call(repo, token, config, timestamp=NOW - 7200, call_mc=1_000_000)
    second = make_call(repo, token, config, timestamp=NOW - 3000, call_mc=2_000_000, score=99)
    snap(repo, token, NOW - 60, 1_500_000)
    a, b = await perf(client, first), await perf(client, second)
    assert (a["latest_market_cap"], a["latest_observed_at"]) == (1_500_000, NOW - 60)
    assert (b["latest_market_cap"], b["latest_observed_at"]) == (1_500_000, NOW - 60)
    assert a["change_since_call_pct"] == pytest.approx(50.0)
    assert b["change_since_call_pct"] == pytest.approx(-25.0)
    assert (a["token_call_index"], a["token_call_count"]) == (1, 2)
    assert (b["token_call_index"], b["token_call_count"]) == (2, 2)
    listed = (await client.get("/api/v1/alerts")).json()["items"]
    assert [i["performance"]["token_call_index"] for i in listed] == [2, 1]


async def test_cutoff_excludes_later_snapshots(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 3000, 1_100_000)
    snap(repo, token, NOW - 100, 5_000_000)
    p = await perf(client, identity, f"?as_of={NOW - 1000}")
    assert p["latest_market_cap"] == 1_100_000 and p["observations"] == 1
    assert p["mfe_pct"] == pytest.approx(10.0)
    assert p["elapsed_seconds"] == 6200 and p["freshness"] == "stale"


async def test_mfe_mae_and_max_gap(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    for offset, value in ((6900, 1_500_000), (6000, 700_000), (3000, 2_000_000), (60, 900_000)):
        snap(repo, token, NOW - offset, value)
    snap(repo, token, NOW - 7300, 9_000_000)  # before the call: excluded
    p = await perf(client, identity)
    assert p["observations"] == 4
    assert p["mfe_pct"] == pytest.approx(100.0) and p["mae_pct"] == pytest.approx(-30.0)
    # gaps: 300 (call -> first), 900, 3000, 2940
    assert p["max_gap_seconds"] == 3000
    assert p["change_since_call_pct"] == pytest.approx(-10.0)


async def test_legacy_call_without_signal_values(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 60, 2_000_000)
    repo.db.execute("UPDATE alert_signals SET market_cap=NULL,price=NULL")
    repo.db.commit()
    p = await perf(client, identity)
    assert p["call_market_cap"] is None and p["call_price"] is None
    assert p["change_reason"] == "no_call_mc"


async def test_new_snapshot_invalidates_cached_page(web, repo, token, config):
    client, app = web
    app.state.config.web_cache_seconds = 60
    make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 600, 1_100_000)
    first = (await client.get("/api/v1/alerts")).json()["items"][0]["performance"]
    snap(repo, token, NOW - 60, 1_300_000)
    second = (await client.get("/api/v1/alerts")).json()["items"][0]["performance"]
    assert first["latest_market_cap"] == 1_100_000
    assert second["latest_market_cap"] == 1_300_000


def table_hashes(path):
    db = sqlite3.connect(path)
    names = ["alerts", "alert_signals", "state"]
    names += [
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'research_%'"
        )
    ]
    result = {}
    for name in names:
        digest = hashlib.sha256()
        for row in db.execute(f"SELECT * FROM {name} ORDER BY 1,2"):
            digest.update(json.dumps(row, default=str).encode())
        result[name] = digest.hexdigest()
    db.close()
    return result


async def test_performance_reads_leave_saved_rows_byte_identical(web, repo, token, config):
    client, app = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 60, 1_100_000)
    path = str(app.state.config.database_path)
    before = table_hashes(path)
    assert any(name.startswith("research_") for name in before)
    changes = repo.db.total_changes
    for url in ("/api/v1/alerts", f"/api/v1/alerts/{identity}"):
        assert (await client.get(url)).status_code == 200
    assert table_hashes(path) == before and repo.db.total_changes == changes


async def test_large_history_page_stays_within_deadline(web, repo, token, config):
    client, app = web
    app.state.config.web_read_timeout_ms = 1500
    tokens = [
        changed(token, contract_address="123456789ABCDEFGHJKLMNPQRSTUVWXYZ"[i] * 32)
        for i in range(25)
    ]
    start = NOW - 86_400 * 3
    for t in tokens:
        make_call(repo, t, config, timestamp=start)
        # 5,000 saved snapshots per token (125,000 rows) after the call.
        repo.db.executemany(
            "INSERT INTO token_snapshots(chain,contract_address,timestamp,market_cap,price,payload)"
            " VALUES(?,?,?,?,?,'{}')",
            [
                (t.chain, t.contract_address, start + 30 * (n + 1), 1e6 + n, 0.5)
                for n in range(5000)
            ],
        )
    repo.db.commit()
    started = time.perf_counter()
    response = await client.get("/api/v1/alerts?page_size=25")
    elapsed = time.perf_counter() - started
    print(f"cold 25-call page, 5000 snapshots each: {elapsed:.3f}s")
    assert response.status_code == 200 and len(response.json()["items"]) == 25
    item = response.json()["items"][0]["performance"]
    assert item["observations"] == 5000 and item["max_gap_seconds"] == 30
    assert elapsed < 1.5


ENR, RANK = "circulating_enriched", "rank"


async def test_legacy_rows_yield_unknown_basis(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200)
    snap(repo, token, NOW - 60, 1_250_000)
    p = await perf(client, identity)
    assert p["basis"] == "unknown" and p["call_market_cap_source"] is None
    assert p["change_since_call_pct"] == pytest.approx(25.0)  # legacy arithmetic unchanged


async def test_same_source_history_is_same(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200, source=ENR)
    snap(repo, token, NOW - 3000, 1_500_000, source=ENR)
    snap(repo, token, NOW - 60, 1_250_000, source=ENR)
    p = await perf(client, identity)
    assert p["basis"] == "same" and p["call_market_cap_source"] == ENR
    assert p["other_basis_observations"] == 0 and p["observations"] == 2
    assert p["mfe_pct"] == pytest.approx(50.0) and p["change_since_call_pct"] == pytest.approx(25.0)


async def test_same_basis_preferred_and_excluded_rows_are_counted(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200, source=ENR)
    snap(repo, token, NOW - 5000, 9_000_000, source=RANK)  # different basis spike
    snap(repo, token, NOW - 4000, 1_100_000, source=ENR)
    snap(repo, token, NOW - 3000, 100_000)  # unlabelled legacy-style row
    snap(repo, token, NOW - 60, 1_250_000, source=RANK)
    p = await perf(client, identity)
    assert p["basis"] == "same" and p["observations"] == 1
    assert p["other_basis_observations"] == 3
    assert p["mfe_pct"] == pytest.approx(10.0) and p["mae_pct"] == pytest.approx(10.0)
    assert p["latest_market_cap"] == 1_100_000


async def test_mixed_source_history_is_mixed_and_keeps_data(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200, source=ENR)
    snap(repo, token, NOW - 3000, 1_500_000, source=RANK)
    snap(repo, token, NOW - 60, 1_250_000)
    p = await perf(client, identity)
    assert p["basis"] == "mixed" and p["observations"] == 2
    assert p["mfe_pct"] == pytest.approx(50.0) and p["change_since_call_pct"] == pytest.approx(25.0)
    assert p["other_basis_observations"] is None


async def test_no_later_observation_has_unknown_basis(web, repo, token, config):
    client, _ = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200, source=ENR)
    p = await perf(client, identity)
    assert p["basis"] == "unknown" and p["call_market_cap_source"] == ENR


async def test_basis_reads_leave_rows_unchanged(web, repo, token, config):
    client, app = web
    identity = make_call(repo, token, config, timestamp=NOW - 7200, source=ENR)
    snap(repo, token, NOW - 3000, 1_500_000, source=RANK)
    snap(repo, token, NOW - 60, 1_250_000, source=ENR)
    before = table_hashes(str(app.state.config.database_path))
    assert (await perf(client, identity))["basis"] == "same"
    assert table_hashes(str(app.state.config.database_path)) == before


async def test_large_labelled_history_stays_within_deadline(web, repo, token, config):
    client, app = web
    app.state.config.web_read_timeout_ms = 1500
    tokens = [
        changed(token, contract_address="123456789ABCDEFGHJKLMNPQRSTUVWXYZ"[i] * 32)
        for i in range(25)
    ]
    start = NOW - 86_400 * 3
    padding = "x" * 1500  # realistic payload size so json_extract is not trivially cheap
    for t in tokens:
        make_call(repo, t, config, timestamp=start, call_mc=1e6, source=ENR)
        repo.db.executemany(
            "INSERT INTO token_snapshots(chain,contract_address,timestamp,market_cap,price,payload)"
            " VALUES(?,?,?,?,?,?)",
            [
                (
                    t.chain,
                    t.contract_address,
                    start + 30 * (n + 1),
                    1e6 + n,
                    0.5,
                    json.dumps({"market_cap_source": ENR if n % 2 else RANK, "pad": padding}),
                )
                for n in range(5000)
            ],
        )
    repo.db.commit()
    started = time.perf_counter()
    response = await client.get("/api/v1/alerts?page_size=25")
    elapsed = time.perf_counter() - started
    print(f"cold 25-call page with source filtering, 5000 snapshots each: {elapsed:.3f}s")
    assert response.status_code == 200 and len(response.json()["items"]) == 25
    item = response.json()["items"][0]["performance"]
    assert item["basis"] == "same" and item["observations"] == 2500
    assert item["other_basis_observations"] == 2500
    assert elapsed < 1.5

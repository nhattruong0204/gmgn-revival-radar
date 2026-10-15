import csv
import io
import time

import pytest

from revival_radar.web.queries import PublicQueries

from .conftest import changed
from .test_call_performance import CALL_MC, set_call_mc, snap
from .test_web_public import NOW, observe
from .test_web_public import web as web

URL = "/api/v1/track-record/calls"


def address(i):
    return "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"[i] * 32


def call(repo, token, config, i, *, timestamp, call_mc=CALL_MC, score=88, status=None, **values):
    live = config.model_copy(
        update={"dry_run": False, "alert_score_threshold": 0, "alert_cooldown_hours": 0.001}
    )
    extra = {"status": status} if status else {}
    snapshot, signal, context = observe(
        repo,
        changed(token, contract_address=address(i), market_cap=call_mc, **values),
        live,
        timestamp=timestamp,
        score=score,
        **extra,
    )
    identity = repo.reserve_alert(snapshot, signal, live, context)
    repo.finish_alert(identity, "sent", message_id=123456789)
    return identity, snapshot


def later(repo, token, i, at, market_cap):
    snap(repo, changed(token, contract_address=address(i)), at, market_cap)


@pytest.fixture
def journal(repo, token, config):
    """Seven calls over five tokens; token 0 is called twice."""
    ids = {}
    ids["win"], _ = call(repo, token, config, 0, timestamp=NOW - 7200, score=90)
    later(repo, token, 0, NOW - 3500, 1_400_000)
    ids["repeat"], _ = call(
        repo, token, config, 0, timestamp=NOW - 3000, call_mc=2_000_000, score=82
    )
    later(repo, token, 0, NOW - 60, 1_250_000)
    ids["loss"], _ = call(
        repo, token, config, 1, timestamp=NOW - 5 * 86400, score=70, status="WATCH"
    )
    later(repo, token, 1, NOW - 60, 600_000)
    ids["flat"], _ = call(repo, token, config, 2, timestamp=NOW - 6000, score=75)
    later(repo, token, 2, NOW - 60, CALL_MC)
    ids["nomc"], _ = call(repo, token, config, 3, timestamp=NOW - 4000, score=60)
    later(repo, token, 3, NOW - 60, 3_000_000)
    set_call_mc(repo, ids["nomc"], None)
    ids["nolater"], _ = call(repo, token, config, 4, timestamp=NOW - 2000, score=95)
    ids["old"], _ = call(repo, token, config, 5, timestamp=NOW - 20 * 86400, score=85)
    later(repo, token, 5, NOW - 60, 1_500_000)
    for name in ("loss", "old"):
        repo.db.execute("UPDATE alert_signals SET revision=3 WHERE alert_id=?", (ids[name],))
    repo.db.commit()
    return ids


async def get(client, query=""):
    response = await client.get(f"{URL}?{query}")
    assert response.status_code == 200, response.text
    return response.json()


def order(data):
    return [item["id"] for item in data["items"]]


async def test_summary_denominators_and_unknown_reasons(web, journal):
    client, _ = web
    data = await get(client, "days=7")
    s = data["summary"]
    assert data["methodology_version"] == "call-journal-v1"
    # Six calls inside seven days; the 20-day-old call is outside the window.
    assert (s["calls"], s["measurable"], s["unknown"]) == (6, 4, 2)
    assert (s["positive"], s["negative"], s["flat"]) == (1, 2, 1)
    assert s["positive"] + s["negative"] + s["flat"] == s["measurable"]
    assert s["unknown_reasons"] == {"no_call_mc": 1, "no_later_observation": 1, "unspecified": 0}
    assert s["median_change_pct"] == pytest.approx(-18.75)
    assert s["mean_change_pct"] == pytest.approx(-13.125)
    assert s["positive_rate_pct"] == pytest.approx(25.0)
    assert sum(b["count"] for b in s["distribution"]) == s["measurable"]
    assert [b["count"] for b in s["distribution"]] == [0, 2, 1, 1, 0]
    assert s["mfe_known"] == 4 and s["mae_known"] == 4
    assert s["mfe_median_pct"] is not None
    assert s["small_sample"] is True and s["small_sample_threshold"] == 20
    assert data["total"] == 6 and len(data["items"]) == 6
    # Unknowns stay null with a reason, never zero.
    unknown = {i["id"]: i["performance"] for i in data["items"]}
    assert unknown[journal["nomc"]]["change_since_call_pct"] is None
    assert unknown[journal["nomc"]]["change_reason"] == "no_call_mc"
    assert unknown[journal["nolater"]]["change_reason"] == "no_later_observation"
    assert all(g["calls"] >= g["measurable"] for g in s["by_stage"] + s["by_revision"])
    assert sum(g["calls"] for g in s["by_stage"]) == s["calls"]
    stages = {g["key"]: g for g in s["by_stage"]}
    assert stages["WATCH"]["calls"] == 1 and stages["WATCH"]["negative"] == 1


async def test_pooled_revisions_are_described_not_withheld(web, journal):
    client, _ = web
    s = (await get(client, "days=30"))["summary"]
    assert s["mixed_configurations"] is True and s["revisions"] == ["4", "3"]
    assert s["median_change_pct"] is not None
    assert {g["key"]: g["calls"] for g in s["by_revision"]} == {"4": 5, "3": 2}
    exact = (await get(client, "days=30&revision=3"))["summary"]
    assert exact["mixed_configurations"] is False and exact["revisions"] == ["3"]
    assert exact["calls"] == 2
    none = (await get(client, "days=30&revision=unknown"))["summary"]
    assert none["calls"] == 0 and none["median_change_pct"] is None


async def test_checkpoint_24h_summary_uses_cohort_only(web, journal):
    client, _ = web
    s = (await get(client, "days=7&chain=sol"))["summary"]["checkpoint_24h"]
    assert s["total"] == 6
    # Calls under 24 hours old are pending; none of them is counted as a zero.
    assert s["pending"] >= 4 and s["returns_available"] == 0
    assert (await get(client, "days=7&stage=WATCH"))["summary"]["checkpoint_24h"]["total"] == 1


async def test_totals_reconcile_with_alerts_and_performance_matches(web, journal):
    client, _ = web
    alerts = (await client.get("/api/v1/alerts?page_size=100")).json()
    data = await get(client, "days=365&page_size=100")
    assert data["total"] == alerts["total"] == data["summary"]["calls"] == 7
    assert data["reconciliation"] == {
        "visible_alerts_in_window": 7,
        "excluded_by_filters": 0,
        "after_filters": 7,
    }
    by_id = {a["id"]: a for a in alerts["items"]}
    for item in data["items"]:
        assert item["performance"] == by_id[item["id"]]["performance"]
        assert item["symbol"] == by_id[item["id"]]["symbol"]
    filtered = await get(client, "days=365&chain=sol&min_score=80")
    assert filtered["reconciliation"]["excluded_by_filters"] == 7 - filtered["total"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("chain=sol", {"win", "repeat", "loss", "flat", "nomc", "nolater"}),
        ("chain=bsc", set()),
        ("stage=WATCH", {"loss"}),
        ("stage=CONFIRMED_REVIVAL", {"win", "repeat", "flat", "nomc", "nolater"}),
        ("min_score=85", {"win", "nolater"}),
        ("max_score=70", {"loss", "nomc"}),
        ("min_score=70&max_score=82", {"repeat", "loss", "flat"}),
        ("outcome=positive", {"win"}),
        ("outcome=negative", {"repeat", "loss"}),
        ("outcome=flat", {"flat"}),
        ("outcome=unknown", {"nomc", "nolater"}),
        ("revision=3", {"loss"}),
        ("revision=4", {"win", "repeat", "flat", "nomc", "nolater"}),
        ("min_age_hours=24", {"loss"}),
        ("max_age_hours=1.5", {"nolater", "repeat", "nomc"}),
        ("min_age_hours=1.2&max_age_hours=1.8", {"flat"}),
    ],
)
async def test_each_filter(web, journal, query, expected):
    client, _ = web
    data = await get(client, f"days=7&{query}&page_size=100")
    assert set(order(data)) == {journal[name] for name in expected}
    assert data["total"] == data["summary"]["calls"] == len(expected)


async def test_window_filter(web, journal):
    client, _ = web
    assert (await get(client, "days=1"))["total"] == 5
    assert (await get(client, "days=30"))["total"] == 7


async def test_each_sort_puts_unknown_last_and_keeps_failures_visible(web, journal):
    client, _ = web
    j = journal

    async def sorted_ids(sort):
        return order(await get(client, f"days=7&sort={sort}&page_size=100"))

    newest = await sorted_ids("newest")
    assert newest[0] == j["nolater"] and newest[-1] == j["loss"]
    assert (await sorted_ids("age")) == list(reversed(newest))
    best = await sorted_ids("best")
    assert best[:4] == [j["win"], j["flat"], j["repeat"], j["loss"]]
    assert set(best[4:]) == {j["nomc"], j["nolater"]}
    worst = await sorted_ids("worst")
    assert worst[:4] == [j["loss"], j["repeat"], j["flat"], j["win"]]
    assert set(worst[4:]) == {j["nomc"], j["nolater"]}
    assert (await sorted_ids("score"))[:2] == [j["nolater"], j["win"]]
    mfe = await sorted_ids("mfe")
    assert mfe[0] == j["win"] and set(mfe[4:]) == {j["nomc"], j["nolater"]}
    mae = await sorted_ids("mae")
    assert mae[0] == j["loss"] and set(mae[4:]) == {j["nomc"], j["nolater"]}


async def test_pagination_is_over_the_whole_cohort(web, journal):
    client, _ = web
    first = await get(client, "days=7&sort=worst&page_size=4&page=1")
    second = await get(client, "days=7&sort=worst&page_size=4&page=2")
    assert first["total"] == second["total"] == first["summary"]["calls"] == 6
    assert len(first["items"]) == 4 and len(second["items"]) == 2
    assert first["summary"] == second["summary"]
    assert not set(order(first)) & set(order(second))


async def test_rejects_inverted_ranges(web, journal):
    client, _ = web
    assert (await client.get(f"{URL}?min_score=90&max_score=10")).status_code == 422
    assert (await client.get(f"{URL}?min_age_hours=9&max_age_hours=1")).status_code == 422
    assert (await client.get(f"{URL}?sort=random")).status_code == 422


async def test_empty_history(web, repo):
    client, _ = web
    data = await get(client)
    s = data["summary"]
    assert (s["calls"], s["measurable"], s["unknown"]) == (0, 0, 0)
    assert s["median_change_pct"] is None and s["mean_change_pct"] is None
    assert s["mixed_configurations"] is False and s["small_sample"] is True
    assert data["items"] == []


async def test_csv_export_matches_selection_and_escapes_formulas(web, repo, token, config):
    client, _ = web
    call(repo, token, config, 0, timestamp=NOW - 7200, symbol="=HYPERLINK(1)")
    later(repo, token, 0, NOW - 60, 800_000)
    call(repo, token, config, 1, timestamp=NOW - 3000, symbol="@cmd")
    call(repo, token, config, 2, timestamp=NOW - 1000, symbol="OK")
    later(repo, token, 2, NOW - 60, 1_100_000)
    response = await client.get(f"{URL}/export?days=7&sort=worst")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "saved-calls.csv" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.reader(io.StringIO(response.text)))
    header, body = rows[0], rows[1:]
    assert len(body) == 3
    column = {name: index for index, name in enumerate(header)}
    assert body[0][column["symbol"]] == "'=HYPERLINK(1)"
    assert body[-1][column["change_reason"]] == "no_later_observation"
    assert body[-1][column["change_since_call_pct"]] == ""  # unknown stays empty, never 0
    assert float(body[0][column["change_since_call_pct"]]) == pytest.approx(-20.0)
    assert body[1][column["symbol"]] == "'@cmd" or body[2][column["symbol"]] == "'@cmd"
    assert body[0][column["methodology"]] == "call-journal-v1"
    assert body[0][column["calls"]] == "3"
    assert "mixed_configurations" in header
    # Exports every selected row, not one page.
    limited = await client.get(f"{URL}/export?days=7&page_size=1")
    assert len(list(csv.reader(io.StringIO(limited.text)))) == 4


async def test_deadline_returns_safe_service_error(web, journal, monkeypatch):
    client, app = web
    app.state.config.web_read_timeout_ms = 100
    original = PublicQueries._performance

    def slow(self, row, until):
        time.sleep(0.06)
        return original(self, row, until)

    monkeypatch.setattr(PublicQueries, "_performance", slow)
    response = await client.get(URL)
    assert response.status_code == 503
    assert response.json() == {"detail": "Saved observations temporarily unavailable"}
    assert (await client.get(f"{URL}/export")).status_code == 503


async def test_oversized_cohort_asks_to_narrow(web, journal, monkeypatch):
    client, _ = web
    monkeypatch.setattr(PublicQueries, "JOURNAL_MAX_CALLS", 3)
    assert (await client.get(f"{URL}?days=7")).status_code == 503
    assert (await client.get(f"{URL}?days=7&max_age_hours=1.5")).status_code == 200


async def test_cold_page_on_large_history_stays_within_deadline(web, repo, token, config):
    client, app = web
    app.state.config.web_read_timeout_ms = 1500
    start = NOW - 86_400 * 3
    for i in range(25):
        t = changed(token, contract_address=address(i))
        call(repo, t, config, i, timestamp=start)
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
    response = await client.get(f"{URL}?days=7&page_size=25&sort=worst")
    elapsed = time.perf_counter() - started
    print(f"cold 25-call journal, 5000 snapshots each: {elapsed:.3f}s")
    assert response.status_code == 200 and response.json()["summary"]["measurable"] == 25
    assert elapsed < 1.5

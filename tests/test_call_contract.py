"""End-to-end call contract (#64): scanner -> alert -> delivery -> API/Track Record/CSV.

One world is built through the real scanner and repository with fake providers; every
test asserts one published-call invariant against the public API, so a regression
anywhere between the scanner and the website fails here.
"""

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass

import httpx
import pytest
from pydantic import SecretStr

from revival_radar.clients.telegram import TelegramClient, alert_buttons
from revival_radar.demo import DemoSource
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.scanner import Scanner, merge_tokens
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig

from .conftest import changed

T0 = 1_800_000_000  # the demo scenario's observation time: call A
NOW = T0 + 86_400  # the website clock
HOUR = 3600
CALL_B_AT = T0 + 2 * HOUR
CALL_B_MC = 2_000_000.0


class FakeProvider(DemoSource):
    """Deterministic offline provider that is recorded as live so public reads include it."""

    observation_origin = "live"


@dataclass
class World:
    repo: object
    config: object
    token: object
    other: object
    quiet: object
    a: int
    b: int
    c: int
    d: int
    telegram: list
    a_mc: float
    a_price: float
    database: str


def result(score, version="score-v2"):
    return RevivalResult(
        score=score,
        status="CONFIRMED_REVIVAL",
        eligible=True,
        score_version=version,
        returning_activity=True,
        structure=Structure(available=True, base_detected=True, base_duration_hours=48),
    )


def call(repo, token, config, *, at, score, preset, revision, version, market_cap, price=1.0):
    """Reserve and confirm one more call through the repository, as the scanner does."""
    snapshot = changed(token, timestamp=at, market_cap=market_cap, price=price)
    context = {"preset": preset, "revision": revision}
    repo.save_snapshot(snapshot)
    identity = repo.reserve_alert(snapshot, result(score, version), config, context)
    assert identity is not None
    repo.finish_alert(identity, "sent", 1000 + identity)
    return identity, alert_buttons(snapshot, identity)


@pytest.fixture
def clock(monkeypatch):
    value = [T0]
    monkeypatch.setattr("revival_radar.storage.repository.time.time", lambda: value[0])
    return value


@pytest.fixture
async def world(repo, config, token, clock):
    config.dry_run = False
    config.trending_discovery_enabled = True
    config.telegram_bot_token = SecretStr("fixture-token")
    config.telegram_chat_id = "fixture-chat"
    source = FakeProvider()
    for snapshot in source.histories():
        repo.save_snapshot(snapshot)
    telegram = []

    def handler(request):
        telegram.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 4242}})

    # Call A: evaluation -> reservation -> confirmed delivery through the real scanner.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await Scanner(config, source, repo, TelegramClient(config, http)).scan_once()
    assert report.sent == 1 and len(telegram) == 1
    a = repo.db.execute("SELECT id FROM alerts").fetchone()[0]
    a_mc, a_price = repo.db.execute(
        "SELECT market_cap,price FROM alert_signals WHERE alert_id=?", (a,)
    ).fetchone()
    assert a_mc and a_price
    called = repo.db.execute("SELECT chain,contract_address FROM alerts").fetchone()
    token = changed(token, chain=called[0], contract_address=called[1])

    config.alert_cooldown_hours = 1  # let a second call on the token through
    # Call B: the same token again under a different strategy revision and score version.
    clock[0] = CALL_B_AT
    b, buttons_b = call(
        repo, token, config, at=CALL_B_AT, score=99, preset="Balanced", revision=5,
        version="score-v3", market_cap=CALL_B_MC, price=2.0,
    )  # fmt: skip
    telegram.append({"reply_markup": {"inline_keyboard": buttons_b}})
    # The operator later changes runtime settings; stored calls must not follow.
    config.alert_score_threshold = 10
    config.alert_cooldown_hours = 2
    # Call C: another token, observed once long ago. Call D: unusable alert-time market cap.
    other = changed(token, contract_address="C" * 32, symbol="LONELY")
    quiet = changed(token, contract_address="D" * 32, symbol="NOMC")
    clock[0] = T0 + 3 * HOUR
    c, buttons_c = call(
        repo, other, config, at=T0 + 3 * HOUR, score=95, preset="Broad", revision=6,
        version="score-v3", market_cap=500_000.0,
    )  # fmt: skip
    telegram.append({"reply_markup": {"inline_keyboard": buttons_c}})
    d, _ = call(
        repo, quiet, config, at=T0 + 3 * HOUR, score=95, preset="Balanced", revision=6,
        version="score-v3", market_cap=800_000.0,
    )  # fmt: skip
    repo.db.execute("UPDATE alert_signals SET market_cap=NULL WHERE alert_id=?", (d,))
    repo.db.commit()
    snap = repo.save_snapshot
    # Later observations of the main token, including a decoy at B's exact call time.
    snap(changed(token, timestamp=CALL_B_AT, market_cap=9e9, price=9.0))  # not strictly later
    snap(changed(token, timestamp=T0 + 3600, market_cap=a_mc * 0.5, price=a_price * 0.5))
    snap(changed(token, timestamp=T0 + 3 * HOUR, market_cap=3_000_000.0, price=3.0))
    snap(changed(token, timestamp=NOW - 60, market_cap=1_500_000.0, price=1.5))
    snap(changed(other, timestamp=NOW - 7200, market_cap=250_000.0, price=0.5))  # stale
    snap(changed(quiet, timestamp=NOW - 60, market_cap=900_000.0, price=1.0))
    # First-hour checkpoints: A negative (price halved), B positive; the rest stay pending.
    clock[0] = T0 + HOUR + 60
    repo.observe_outcomes(changed(token, timestamp=T0 + HOUR + 60, price=a_price * 0.5))
    clock[0] = CALL_B_AT + HOUR + 60
    repo.observe_outcomes(changed(token, timestamp=CALL_B_AT + HOUR + 60, price=3.0))
    clock[0] = NOW
    return World(
        repo, config, token, other, quiet, a, b, c, d, telegram, a_mc, a_price,
        repo.db.execute("PRAGMA database_list").fetchone()[2],
    )  # fmt: skip


@pytest.fixture
async def client(world):
    app = create_app(
        WebConfig(database_path=world.database, web_cache_seconds=0, web_cookie_secure=False),
        clock=lambda: NOW,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def detail(client, identity, query=""):
    response = await client.get(f"/api/v1/alerts/{identity}{query}")
    assert response.status_code == 200
    return response.json()


async def listing(client, query=""):
    response = await client.get("/api/v1/alerts?page_size=100" + query)
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["items"]}


TRACK = "/api/v1/track-record?days=7&horizon=1&page_size=100"
EXPORT = TRACK.replace("track-record?", "track-record/export?")


def parse_csv(text):
    rows = list(csv.reader(io.StringIO(text)))
    return [dict(zip(rows[0], row, strict=False)) for row in rows[1:]]


def digest(world, sql):
    h = hashlib.sha256()
    for row in world.repo.db.execute(sql):
        h.update(json.dumps(tuple(row), default=str).encode())
    return h.hexdigest()


PROTECTED = {
    "alerts": "SELECT * FROM alerts ORDER BY id",
    "alert_signals": "SELECT * FROM alert_signals ORDER BY alert_id",
    "alert_state": "SELECT * FROM state WHERE key LIKE 'telegram:alert:%' ORDER BY key",
    "outcomes": "SELECT * FROM signal_outcomes ORDER BY alert_id,horizon_hours",
    "facts": "SELECT * FROM research_alert_facts ORDER BY alert_id",
    "checkpoint_facts": "SELECT * FROM research_checkpoint_facts ORDER BY 1,2",
    "receipts": "SELECT * FROM research_delivery_receipts ORDER BY alert_id",
}


# 1. Original timestamp / market cap / price survive later snapshots and enrichment.
async def test_call_keeps_original_values_after_snapshots_and_enrichment(client, world):
    before = await detail(client, world.a)
    assert before["timestamp"] == T0
    assert before["performance"]["call_market_cap"] == world.a_mc
    assert before["performance"]["call_price"] == world.a_price
    # Enrichment: the same token arrives again from two discovery sources with new values.
    hot = changed(world.token, timestamp=NOW - 30, market_cap=7e6, price=7.0, trending_rank=None)
    trend = changed(hot, discovery_source={"trending"}, hot_search_rank=None, liquidity=1.0)
    for merged in merge_tokens([hot, trend]):
        world.repo.save_snapshot(merged)
    world.repo.save_snapshot(
        changed(world.token, timestamp=T0, market_cap=1.0, price=0.0001)  # same key: ignored
    )
    after = await detail(client, world.a)
    for key in ("timestamp", "score", "preset", "revision", "observation"):
        assert after[key] == before[key], key
    assert after["performance"]["call_market_cap"] == world.a_mc
    assert after["performance"]["call_price"] == world.a_price
    assert after["performance"]["latest_market_cap"] == 7e6  # only the latest moved


# 2. Latest market cap comes from a strictly later, timestamped observation.
async def test_latest_market_cap_is_strictly_later_observation(client, world):
    b = (await detail(client, world.b))["performance"]
    # A decoy at exactly B's call time (9e9) and earlier snapshots must be ignored.
    assert b["latest_market_cap"] == 1_500_000.0 and b["latest_observed_at"] == NOW - 60
    assert b["observations"] == 2  # T0+3h and NOW-60 only; the same-time decoy is excluded
    assert b["mfe_pct"] == pytest.approx((3_000_000 / CALL_B_MC - 1) * 100)
    a = (await detail(client, world.a))["performance"]
    assert a["latest_observed_at"] == NOW - 60 and a["observations"] >= 4
    cut = (await detail(client, world.a, f"?as_of={T0 + HOUR}"))["performance"]
    assert cut["latest_observed_at"] == T0 + HOUR  # nothing after the cutoff is used
    assert cut["latest_market_cap"] == world.a_mc * 0.5


# 3. Displayed change equals independently recomputed arithmetic.
async def test_change_since_call_matches_recomputed_arithmetic(client, world):
    items = await listing(client)
    for identity, call_mc in ((world.a, world.a_mc), (world.b, CALL_B_MC), (world.c, 500_000.0)):
        perf = items[identity]["performance"]
        expected = (perf["latest_market_cap"] / call_mc - 1) * 100
        assert perf["change_since_call_pct"] == pytest.approx(expected, abs=1e-9)
        assert perf["change_reason"] is None
    assert items[world.b]["performance"]["change_since_call_pct"] == pytest.approx(-25.0)
    assert items[world.c]["performance"]["change_since_call_pct"] == pytest.approx(-50.0)
    # The detail endpoint agrees with the list.
    assert (await detail(client, world.b))["performance"] == items[world.b]["performance"]


# 4. Missing observations are unknown/null with a reason, never zero.
async def test_missing_observation_is_unknown_not_zero(client, world):
    perf = (await detail(client, world.d))["performance"]
    assert perf["change_since_call_pct"] is None and perf["change_reason"] == "no_call_mc"
    assert perf["call_market_cap"] is None and perf["mfe_pct"] is None
    # A call whose token has no observation after the call (here: at the cutoff) is unknown.
    early = (await detail(client, world.c, f"?as_of={T0 + 3 * HOUR}"))["performance"]
    assert early["change_since_call_pct"] is None
    assert early["change_reason"] == "no_later_observation"
    assert early["latest_market_cap"] is None and early["freshness"] == "none"
    assert early["observations"] == 0 and early["mfe_pct"] is None and early["mae_pct"] is None
    # Unknown values stay unknown in the Track Record and CSV.
    long = TRACK.replace("horizon=1", "horizon=72")
    rows = (await client.get(long)).json()["items"]
    assert rows and all(r["state"] == "pending" and r["return_pct"] is None for r in rows)
    exported = parse_csv((await client.get(EXPORT.replace("horizon=1", "horizon=72"))).text)
    assert exported and all(r["return_pct"] == "" for r in exported)
    missed = [r for r in (await client.get(TRACK)).json()["items"] if r["state"] != "return"]
    assert missed and all(r["return_pct"] is None for r in missed)


# 5. Several calls on one token stay distinct with stable ids and correct counts.
async def test_multiple_calls_one_token_distinct_and_stable(client, world):
    items = await listing(client)
    assert (world.a, world.b) == (1, 2) and len(items) == 4
    a, b = items[world.a], items[world.b]
    assert a["contract_address"] == b["contract_address"]
    assert (a["timestamp"], b["timestamp"]) == (T0, CALL_B_AT)
    assert [x["performance"]["token_call_index"] for x in (a, b)] == [1, 2]
    assert [x["performance"]["token_call_count"] for x in (a, b)] == [2, 2]
    assert items[world.c]["performance"]["token_call_count"] == 1
    again = await listing(client)  # ids and content do not change between reads
    assert again == items
    page = (await client.get("/api/v1/alerts")).json()["items"]
    assert sorted(r["id"] for r in page) == [1, 2, 3, 4]


# 6. One call id appears in the Telegram buttons, the API, Track Record rows and the CSV.
async def test_same_call_id_across_telegram_api_track_record_and_csv(client, world):
    callbacks = [
        button["callback_data"]
        for message in world.telegram
        for row in message["reply_markup"]["inline_keyboard"]
        for button in row
        if "callback_data" in button
    ]
    telegram_ids = {
        int(m.group(1)) for item in callbacks if (m := re.fullmatch(r"a:(\d+):(?:why|full)", item))
    }
    assert telegram_ids == {world.a, world.b, world.c}
    api = await listing(client)
    track = (await client.get(TRACK)).json()["items"]
    csv_rows = parse_csv((await client.get(EXPORT)).text)
    track_ids = {row["alert_id"] for row in track}
    assert track_ids == {world.a, world.b, world.c, world.d}
    assert {int(row["alert_id"]) for row in csv_rows} == track_ids
    assert telegram_ids <= set(api) and track_ids == set(api)
    for row in track:  # same call, same time and contract on every surface
        assert api[row["alert_id"]]["timestamp"] == row["timestamp"]
        assert api[row["alert_id"]]["contract_address"] == row["contract_address"]
    for row in csv_rows:
        assert api[int(row["alert_id"])]["contract_address"] == row["contract_address"]
    # The scanner's own Telegram message carries the id the repository reserved.
    keyboard = world.telegram[0]["reply_markup"]["inline_keyboard"]
    assert keyboard[1][0]["callback_data"] == f"a:{world.a}:why"
    assert keyboard[2][0]["callback_data"] == f"a:{world.a}:full"


# 7. Preset / revision / score_version stay on the original call after settings change.
async def test_strategy_attached_to_original_call(client, world):
    assert world.config.alert_score_threshold == 10  # settings changed after the calls
    items = await listing(client)
    assert (items[world.a]["preset"], items[world.a]["revision"]) == ("Strict", 0)
    assert (items[world.b]["preset"], items[world.b]["revision"]) == ("Balanced", 5)
    assert (items[world.c]["preset"], items[world.c]["revision"]) == ("Broad", 6)
    track = {r["alert_id"]: r for r in (await client.get(TRACK)).json()["items"]}
    exported = {r["alert_id"]: r for r in parse_csv((await client.get(EXPORT)).text)}
    versions = {i: track[i]["score_version"] for i in (world.a, world.b, world.c)}
    assert versions[world.b] == versions[world.c] == "score-v3"
    assert versions[world.a] != "score-v3" and versions[world.a]
    assert (track[world.b]["preset"], track[world.b]["revision"]) == ("Balanced", 5)
    csv_b = exported[str(world.b)]
    assert (csv_b["preset"], csv_b["revision"], csv_b["score_version"]) == (
        "Balanced",
        "5",
        "score-v3",
    )


# 8. Enrichment never overwrites historical snapshot or state rows.
async def test_enrichment_never_rewrites_history(client, world):
    snapshots = "SELECT * FROM token_snapshots ORDER BY id"
    evaluations = "SELECT * FROM evaluations ORDER BY id"
    before = {name: digest(world, sql) for name, sql in PROTECTED.items()}
    before_snapshots, before_evaluations = digest(world, snapshots), digest(world, evaluations)
    count = world.repo.db.execute("SELECT count(*) FROM token_snapshots").fetchone()[0]
    # Re-enrich: identical keys with different values (ignored) plus a genuinely newer row.
    for stamp, value in ((T0, 1.0), (T0 + 3600, 2.0), (T0 - 1200, 3.0)):
        world.repo.save_snapshot(
            changed(world.token, timestamp=stamp, market_cap=value, price=value)
        )
    world.repo.save_snapshot(changed(world.token, timestamp=NOW - 10, market_cap=6e6, price=6.0))
    world.repo.observe_outcomes(changed(world.token, timestamp=T0 + HOUR + 120, price=99.0))
    now_count = world.repo.db.execute("SELECT count(*) FROM token_snapshots").fetchone()[0]
    assert now_count == count + 1
    for name, sql in PROTECTED.items():
        assert digest(world, sql) == before[name], name
    old = f"SELECT * FROM token_snapshots WHERE id<={count} ORDER BY id"
    assert digest(world, old) == before_snapshots
    assert digest(world, evaluations) == before_evaluations
    assert (await detail(client, world.a))["performance"]["call_market_cap"] == world.a_mc


# 9. Positive / negative / pending / stale states come out correctly.
async def test_positive_negative_pending_and_stale_states(client, world):
    items = await listing(client)
    assert items[world.b]["performance"]["change_since_call_pct"] < 0  # 1.5M vs 2.0M
    assert items[world.b]["performance"]["freshness"] == "fresh"
    assert items[world.c]["performance"]["freshness"] == "stale"  # last seen 2h earlier
    assert items[world.d]["performance"]["change_since_call_pct"] is None
    body = (await client.get(TRACK)).json()
    rows = {r["alert_id"]: r for r in body["items"]}
    assert rows[world.a]["state"] == "return" and rows[world.a]["return_pct"] == pytest.approx(-50)
    assert rows[world.b]["state"] == "return" and rows[world.b]["return_pct"] == pytest.approx(50)
    assert rows[world.c]["state"] != "return" and rows[world.c]["return_pct"] is None
    assert body["summary"]["positive"] == 1 and body["summary"]["negative"] == 1
    far = (await client.get(TRACK.replace("horizon=1", "horizon=72"))).json()["items"]
    assert far and all(r["state"] == "pending" and r["return_pct"] is None for r in far)


# 10. A historical cohort at a fixed as_of is reproducible, even after new data arrives.
async def test_cohort_reproducible_at_fixed_as_of(client, world):
    fixed = f"&as_of={T0 + 4 * HOUR}"
    first = (await client.get(TRACK + fixed)).json()
    assert first["items"] and (await client.get(TRACK + fixed)).json() == first
    csv_first = (await client.get(EXPORT + fixed)).text
    api_first = await listing(client, fixed)
    # New call, snapshots and checkpoints arrive after the cutoff.
    late = changed(world.token, timestamp=NOW - 20, market_cap=5.5e6, price=5.5)
    world.repo.save_snapshot(late)
    world.repo.observe_outcomes(late)
    world.config.alert_cooldown_hours = 0.001
    call(
        world.repo, world.other, world.config, at=NOW - 10, score=100, preset="Strict",
        revision=7, version="score-v4", market_cap=1e6,
    )  # fmt: skip
    assert (await client.get(TRACK + fixed)).json() == first
    assert (await client.get(EXPORT + fixed)).text == csv_first
    assert await listing(client, fixed) == api_first
    assert (await client.get(TRACK)).json() != first  # the present-day view did change

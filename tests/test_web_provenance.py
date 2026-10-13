import json

import httpx
import pytest

from revival_radar.storage.provenance import classify
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig

from .conftest import changed
from .test_web_public import NOW, SECRET, observe


@pytest.fixture
async def web(repo):
    database = repo.db.execute("PRAGMA database_list").fetchone()[2]
    app = create_app(WebConfig(database_path=database, web_cache_seconds=0), clock=lambda: NOW)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, app


def attest(repo, kind, identity, origin):
    return classify(
        repo.db,
        json.dumps(
            {
                "kind": kind,
                "id": identity,
                "origin": origin,
                "evidence": "Reviewed isolated fixture capture; private-owner-credential-marker",
            }
        ).encode(),
    )


async def test_recent_window_history_pagination_and_cutoffs(web, repo, token, config):
    client, _ = web
    observe(repo, token, config, timestamp=NOW - 2 * 86400)
    observe(repo, changed(token, contract_address="B" * 32), config, timestamp=NOW - 200)
    recent = (await client.get("/api/v1/radar")).json()
    assert recent["total"] == 1 and recent["items"][0]["contract_address"] == "B" * 32
    historical = (await client.get("/api/v1/radar?age_hours=168&page_size=1&page=2")).json()
    assert historical["total"] == 2 and len(historical["items"]) == 1
    assert (await client.get("/api/v1/radar?age_hours=168&as_of=" + str(NOW - 1000))).json()[
        "total"
    ] == 1
    assert (await client.get("/api/v1/radar?age_hours=25")).status_code == 422
    # Old detail remains intentionally accessible despite shortlist exclusion.
    detail = (await client.get(f"/api/v1/tokens/sol/{token.contract_address}")).json()
    assert detail["observation"]["timestamp"] == NOW - 2 * 86400
    assert detail["observation"]["origin_confidence"] == "unknown"


@pytest.mark.parametrize(
    "heartbeat,collection,stopped,expected,active",
    [
        (NOW - 1, {"active_chains": ["base", "sol"]}, None, "current", ["base", "sol"]),
        (NOW - 31, {"active_chains": ["sol"]}, None, "stale", []),
        (NOW + 1, {"active_chains": ["sol"]}, None, "stale", []),
        (True, {"active_chains": ["sol"]}, None, "stale", []),
        (float("nan"), {"active_chains": ["sol"]}, None, "stale", []),
        (NOW, {"active_chains": []}, None, "disabled", []),
        (NOW, None, None, "unknown", []),
        (NOW, {"active_chains": "sol"}, None, "unknown", []),
        (NOW, {"active_chains": [[]]}, None, "unknown", []),
        (NOW, {"active_chains": ["unsupported"]}, None, "unknown", []),
        (NOW, {"active_chains": ["sol"]}, NOW, "stale", []),
    ],
)
async def test_collection_uses_applied_chains_not_owner_controls_or_history(
    web,
    repo,
    token,
    config,
    heartbeat,
    collection,
    stopped,
    expected,
    active,
):
    client, _ = web
    observe(repo, changed(token, chain="bsc", contract_address="0x" + "a" * 40), config)
    repo.set_state(
        "web:worker",
        json.dumps(
            {
                "heartbeat_at": heartbeat,
                "enabled": False,
                "stopped_at": stopped,
                "collection": collection,
                "settings": {"enabled_chains": "bsc", "gmgn_api_key": SECRET},
            }
        ),
    )
    response = await client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["collection_status"] == expected
    assert [c["id"] for c in body["active_chains"]] == active
    assert [c["id"] for c in body["chains"]] == ["bsc"]
    assert SECRET not in response.text and "settings" not in body
    if heartbeat == NOW + 1 or type(heartbeat) is bool:
        assert body["worker_heartbeat_at"] is None


async def test_exclusion_is_read_only_reversible_and_keeps_original_evidence(
    web, repo, token, config
):
    client, _ = web
    snapshot, signal, context = observe(repo, token, config)
    identity = repo.db.execute("SELECT id FROM evaluations").fetchone()[0]
    snapshot_id = repo.db.execute("SELECT id FROM token_snapshots").fetchone()[0]
    scan = repo.db.execute("SELECT id FROM scan_runs").fetchone()[0]
    alert = repo.reserve_alert(
        snapshot, signal, config.model_copy(update={"dry_run": False}), context
    )
    repo.finish_alert(alert, "sent")
    repo.cache_save(
        "kline:1h:168",
        snapshot,
        json.dumps(
            {"candles": [{"timestamp": NOW - 7200, "open": 1, "high": 2, "low": 1, "close": 2}]}
        ),
        NOW + 100,
        origin="synthetic",
    )
    originals = {
        t: repo.db.execute(f"SELECT * FROM {t}").fetchall()
        for t in (
            "evaluations",
            "token_snapshots",
            "scan_runs",
            "alerts",
            "alert_signals",
            "signal_outcomes",
        )
    }
    for kind, value in (
        ("scan", scan),
        ("snapshot", snapshot_id),
        ("evaluation", identity),
        ("alert", alert),
    ):
        attest(repo, kind, value, "synthetic")
    before = list(repo.db.iterdump())
    assert (await client.get("/api/v1/radar")).json()["total"] == 0
    assert (await client.get("/api/v1/alerts")).json()["total"] == 0
    assert (await client.get(f"/api/v1/alerts/{alert}")).status_code == 404
    assert (await client.get(f"/api/v1/tokens/sol/{token.contract_address}")).status_code == 404
    history = (await client.get(f"/api/v1/tokens/sol/{token.contract_address}/history")).json()
    assert not history["scores"] and not history["snapshots"] and not history["candles"]
    assert (await client.get("/api/v1/reports")).json()["entries"] == []
    assert (await client.get("/api/v1/meta")).json()["chains"] == []
    assert list(repo.db.iterdump()) == before
    for kind, value in (
        ("scan", scan),
        ("snapshot", snapshot_id),
        ("evaluation", identity),
        ("alert", alert),
    ):
        attest(repo, kind, value, "unknown")
    assert (await client.get("/api/v1/radar")).json()["total"] == 1
    assert (await client.get("/api/v1/alerts")).json()["total"] == 1
    for table, rows in originals.items():
        assert repo.db.execute(f"SELECT * FROM {table}").fetchall() == rows
    assert (
        repo.db.execute(
            "SELECT count(*) FROM state WHERE key LIKE 'provenance:audit:%'"
        ).fetchone()[0]
        == 8
    )
    assert SECRET not in (await client.get("/api/v1/radar")).text


async def test_classification_invalidates_cached_reads_and_fixture_opt_in(repo, token, config):
    observe(repo, token, config)
    identity = repo.db.execute("SELECT id FROM evaluations").fetchone()[0]
    database = repo.db.execute("PRAGMA database_list").fetchone()[2]
    app = create_app(WebConfig(database_path=database, web_cache_seconds=60), clock=lambda: NOW)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/radar")).json()["total"] == 1
        attest(repo, "evaluation", identity, "test")
        assert (await client.get("/api/v1/radar")).json()["total"] == 0
        attest(repo, "evaluation", identity, "live")
        row = (await client.get("/api/v1/radar")).json()["items"][0]
        assert row["origin"] == "live" and row["origin_confidence"] == "recorded"
        attest(repo, "evaluation", identity, "synthetic")
    stage = create_app(
        WebConfig(database_path=database, web_allow_synthetic_data=True), clock=lambda: NOW
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stage), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/meta")).json()["data_mode"] == "synthetic"
        assert (await client.get("/api/v1/radar")).json()["items"][0]["origin"] == "synthetic"


@pytest.mark.parametrize(
    "manifest",
    [
        {
            "kind": "evaluations; DROP TABLE alerts",
            "id": 1,
            "origin": "synthetic",
            "evidence": "fixture",
        },
        {"kind": "evaluation", "id": True, "origin": "test", "evidence": "fixture"},
        {"kind": "evaluation", "id": 99999, "origin": "test", "evidence": "fixture"},
        {"kind": "evaluation", "id": 1, "origin": "guessed", "evidence": "fixture"},
        {"kind": "evaluation", "id": 1, "origin": "synthetic", "evidence": ""},
    ],
)
def test_invalid_attestation_has_no_writes(repo, manifest):
    before = list(repo.db.iterdump())
    with pytest.raises(ValueError):
        classify(repo.db, json.dumps(manifest).encode())
    assert list(repo.db.iterdump()) == before


async def test_coverage_heartbeat_expires_even_when_public_cache_is_enabled(repo):
    now = [NOW]
    repo.set_state(
        "web:worker",
        json.dumps(
            {
                "heartbeat_at": NOW,
                "collection": {"active_chains": ["sol"]},
            }
        ),
    )
    database = repo.db.execute("PRAGMA database_list").fetchone()[2]
    app = create_app(WebConfig(database_path=database, web_cache_seconds=60), clock=lambda: now[0])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/meta")).json()["collection_status"] == "current"
        now[0] += 31
        body = (await client.get("/api/v1/meta")).json()
        assert body["collection_status"] == "stale" and body["active_chains"] == []


def test_duplicate_snapshot_keeps_original_classification(repo, token):
    repo.save_snapshot(token, origin="live")
    identity = repo.db.execute("SELECT id FROM token_snapshots").fetchone()[0]
    repo.save_snapshot(token, origin="synthetic")
    assert repo.observation_origin("snapshot", identity) == "live"
    assert repo.db.execute("SELECT count(*) FROM token_snapshots").fetchone()[0] == 1


def test_retention_cannot_attach_old_origin_to_reused_id(repo, token, config):
    observe(repo, token, config)
    identity = repo.db.execute("SELECT id FROM evaluations").fetchone()[0]
    attest(repo, "evaluation", identity, "synthetic")
    repo.prune_diagnostics(NOW)
    observe(repo, token, config, timestamp=NOW)
    reused = repo.db.execute("SELECT id FROM evaluations").fetchone()[0]
    assert reused == identity
    assert repo.observation_origin("evaluation", reused) == "unknown"
    assert (
        repo.db.execute(
            "SELECT count(*) FROM state WHERE key LIKE 'provenance:audit:%'"
        ).fetchone()[0]
        == 1
    )

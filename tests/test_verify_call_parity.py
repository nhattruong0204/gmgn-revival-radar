# ruff: noqa: F811
"""The operator parity script, run against the same fixture world as the call contract."""

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from .test_call_contract import NOW, clock, world  # noqa: F401
from .test_call_contract import client as client  # noqa: F401

SPEC = importlib.util.spec_from_file_location(
    "verify_call_parity", Path(__file__).parents[1] / "scripts/verify_call_parity.py"
)
parity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parity)


async def served(client):
    """The public API body, handed back by a fake GET-only fetcher."""
    body = (await client.get("/api/v1/alerts?page=1&page_size=100")).json()
    assert body["as_of"] == NOW
    urls = []

    def fetch(url):
        urls.append(url)
        return body

    return body, fetch, urls


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def test_matches_api_and_recomputes_independently(world, client, capsys):
    _, fetch, urls = await served(client)
    before = file_hash(world.database)
    mismatches = parity.run(world.database, "https://example.test", fetch=fetch)
    assert mismatches == []
    assert file_hash(world.database) == before
    assert urls and all(u.startswith("https://example.test/api/v1/alerts?") for u in urls)
    out = capsys.readouterr().out
    assert "mismatches=0" in out and "no_call_mc" in out and "stale" in out
    assert f"as_of={NOW}" in out
    db = parity.open_readonly(world.database)
    expected = parity.expected_calls(db, NOW)
    db.close()
    assert expected[world.b]["change_since_call_pct"] == pytest.approx(-25.0)
    assert expected[world.d]["change_since_call_pct"] is None
    assert expected[world.d]["change_reason"] == "no_call_mc"
    assert [expected[i]["token_call_index"] for i in (world.a, world.b)] == [1, 2]


async def test_detects_api_disagreement(world, client, capsys):
    body, fetch, _ = await served(client)
    body["items"][0]["performance"]["change_since_call_pct"] = 0.0  # unknown shown as zero
    body["items"][1]["performance"]["latest_market_cap"] += 1
    mismatches = parity.run(world.database, "https://example.test", fetch=fetch)
    assert len(mismatches) >= 2
    assert "MISMATCH" in capsys.readouterr().out
    # A call hidden from the API is reported as a presence mismatch.
    hidden, fetch, _ = await served(client)
    hidden["items"] = hidden["items"][:-1]
    hidden["total"] -= 1
    assert any(m[1] == "presence" for m in parity.run(world.database, "x://", fetch=fetch))


async def test_database_change_after_publication_is_caught(world, client):  # noqa: F811
    _, fetch, _ = await served(client)
    world.repo.db.execute(
        "UPDATE alert_signals SET market_cap=market_cap*2 WHERE alert_id=?", (world.b,)
    )
    world.repo.db.commit()
    mismatches = parity.run(world.database, "https://example.test", fetch=fetch)
    assert {m[1] for m in mismatches if m[0] == world.b} >= {
        "call_market_cap",
        "change_since_call_pct",
    }


def test_database_only_mode_and_exit_codes(world, capsys, monkeypatch):  # noqa: F811
    assert parity.main(["--db", str(world.database), "--as-of", str(NOW)]) == 0
    assert "compared=0 mismatches=0" in capsys.readouterr().out
    assert parity.main(["--db", str(world.database) + ".missing"]) == 2
    assert "Cannot verify" in capsys.readouterr().err
    monkeypatch.setattr(parity, "get_json", lambda url: {"as_of": NOW, "items": [], "total": 0})
    assert parity.main(["--db", str(world.database), "--api", "https://example.test"]) == 1
    assert "absent from API" in capsys.readouterr().out


def test_database_is_opened_read_only(world):  # noqa: F811
    db = parity.open_readonly(world.database)
    with pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM alerts")
    db.close()
    assert world.repo.db.execute("SELECT count(*) FROM alerts").fetchone()[0] == 4


def test_projection_mode_matches_and_detects_a_tampered_row(tmp_path, capsys):
    from revival_radar.analysis import call_projection
    from revival_radar.storage.database import connect

    from .test_call_projection import NOW as T0
    from .test_call_projection import add_call, add_snapshot, random_world

    path = tmp_path / "p.db"
    db = connect(path)
    random_world(db, 5, calls=10, until=T0)
    add_call(db, 999, "BASIS", T0 - 86400, 1e6, "rank")
    for n, source in enumerate(["rank", "circulating_enriched", "rank", None]):
        add_snapshot(db, "BASIS", T0 - 80000 + n * 600, 1e6 + n * 1e5, source)
    db.commit()
    call_projection.refresh(db, T0, 30)
    db.close()
    assert parity.run_projection(path, out=lambda *_: None) == []
    assert parity.main(["--db", str(path), "--projection"]) == 0
    db = connect(path)
    db.execute("UPDATE call_performance SET max_market_cap=max_market_cap*2 WHERE alert_id=999")
    db.commit()
    db.close()
    mismatches = parity.run_projection(path, out=lambda *_: None)
    assert [(m[0], m[1]) for m in mismatches] == [(999, "max_market_cap")]
    assert parity.main(["--db", str(path), "--projection"]) == 1

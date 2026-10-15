import csv
import io
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from revival_radar.analysis.track_record import accounting, checkpoint_rows
from revival_radar.models.signal import RevivalResult, Structure
from revival_radar.storage.database import connect
from revival_radar.storage.provenance import classify
from revival_radar.web.app import create_app
from revival_radar.web.config import WebConfig
from revival_radar.web.queries import PublicQueries

from .conftest import changed

AT = 1_800_000_000


@pytest.fixture
def clock(monkeypatch):
    value = [AT]
    monkeypatch.setattr("revival_radar.storage.repository.time.time", lambda: value[0])
    return value


def sent(
    repo,
    token,
    config,
    clock,
    *,
    index=0,
    timestamp=AT - 7200,
    revision=4,
    initial=1,
    symbol="RESEARCH",
    legacy=False,
    origin="live",
    version="score-v2",
    address=None,
):
    config.dry_run = False
    clock[0] = timestamp
    snapshot = changed(
        token,
        contract_address=address or "ABCDEFGHJKLMNPQRSTUVWXYZ"[index] * 32,
        timestamp=timestamp,
        price=initial,
        symbol=symbol,
    )
    result = RevivalResult(
        score=90,
        status="CONFIRMED_REVIVAL",
        eligible=True,
        returning_activity=True,
        score_version=version,
        structure=Structure(available=True, base_detected=True, base_duration_hours=48),
    )
    context = {"preset": "Balanced", "revision": revision, "gmgn_api_key": "DO-NOT-EXPOSE"}
    scan = repo.begin_scan(timestamp - 10, context, origin=origin)
    repo.record_evaluation(scan, snapshot, result, config, context, origin=origin)
    if legacy:
        repo.research_outcomes_enabled = False
    identity = repo.reserve_alert(snapshot, result, config, context, origin=origin)
    repo.finish_alert(identity, "sent", 42)
    repo.research_outcomes_enabled = True
    repo.finish_scan(scan, SimpleNamespace(processed=1), timestamp + 1)
    clock[0] = AT
    return identity, snapshot


def checkpoint(repo, token, clock, price, *, recorded=AT - 3500, observed=AT - 3600, origin="live"):
    clock[0] = recorded
    repo.observe_outcomes(changed(token, timestamp=observed, price=price), origin=origin)
    clock[0] = AT


def test_accounting_never_drops_negative_missing_pending_or_missed(repo, token, config, clock):
    for i, value in enumerate([2, 0.5, 1, None]):
        _, snapshot = sent(repo, token, config, clock, index=i)
        checkpoint(repo, snapshot, clock, value)
    sent(repo, token, config, clock, index=4, timestamp=AT - 100)
    sent(repo, token, config, clock, index=5, timestamp=AT - 8000)
    rows = checkpoint_rows(repo, AT - 86400, AT, 1)
    stats = accounting(rows)
    assert stats["total"] == 6
    assert stats["observed"] == 4 and stats["returns_available"] == 3
    assert (stats["positive"], stats["negative"], stats["zero"]) == (1, 1, 1)
    assert stats["missing_price"] == 1 and stats["pending"] == 1 and stats["missed"] == 1
    assert stats["median_return_pct"] == 0 and stats["positive_rate_pct"] == pytest.approx(100 / 3)
    assert sum(b["count"] for b in stats["distribution"]) == 3
    assert stats["small_sample"]
    bot = repo.outcome_summary(AT - 86400, AT)["1"]
    assert all(bot[key] == stats[key] for key in bot)


def test_causal_receipts_and_original_evidence_cannot_be_rewritten(repo, token, config, clock):
    identity, snapshot = sent(repo, token, config, clock)
    checkpoint(repo, snapshot, clock, 0.5, recorded=AT - 100)
    before = checkpoint_rows(repo, AT - 86400, AT - 200, 1)
    assert before[0]["state"] == "pending" and before[0]["observed_at"] is None
    current = checkpoint_rows(repo, AT - 86400, AT, 1)[0]
    assert current["return_pct"] == -50 and current["recorded_at"] == AT - 100
    with repo.db:
        repo.db.execute(
            "UPDATE signal_outcomes SET price=99,return_pct=9800 WHERE alert_id=?", (identity,)
        )
        repo.db.execute(
            "UPDATE alert_signals SET price=100,preset='Broad',revision=99 WHERE alert_id=?",
            (identity,),
        )
        repo.db.execute("UPDATE state SET value='{}' WHERE key=?", (f"telegram:alert:{identity}",))
    assert checkpoint_rows(repo, AT - 86400, AT, 1)[0] == current
    detail = PublicQueries(repo.db).alert(identity, AT)
    assert detail.observation.price == 1 and detail.revision == 4
    assert detail.outcomes[0].return_pct == -50
    assert detail.original_brief_id == current["brief_id"]
    for table in (
        "research_alert_facts",
        "research_delivery_receipts",
        "research_checkpoint_facts",
    ):
        for sql in (
            f"DELETE FROM {table}",
            f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                repo.db.execute(sql)
    repo.db.rollback()


def test_delivery_confirmation_availability_is_not_backdated(repo, token, config, clock):
    identity, _ = sent(repo, token, config, clock)
    assert PublicQueries(repo.db).alert(identity, AT - 7300) is None
    # Existing legacy alert may gain a new recorded delivery without inventing old capture time.
    with repo.db:
        repo.db.execute(
            "INSERT INTO alerts VALUES(999,?,'sol',?,90,'[]',NULL,'pending')", (AT - 7200, "Z" * 32)
        )
    clock[0] = AT - 100
    repo.finish_alert(999, "sent", 44)
    assert PublicQueries(repo.db).alert(999, AT - 200) is None
    assert any(
        r["alert_id"] == 999 and r["knowledge"] == "legacy_unknown"
        for r in checkpoint_rows(repo, AT - 86400, AT, 1)
    )


def test_cohort_filters_are_alert_time_and_incompatible_rates_withheld(repo, token, config, clock):
    for i, revision in enumerate([4, 5]):
        _, snapshot = sent(repo, token, config, clock, index=i, revision=revision)
        checkpoint(repo, snapshot, clock, 2 if i == 0 else 0.5)
    query = PublicQueries(repo.db)
    pooled = query.track_record(AT, 7, 1, {}, 1, 25)
    assert len(pooled.cohorts) == 2 and not pooled.summary.comparable
    assert pooled.summary.median_return_pct is None and pooled.summary.positive_rate_pct is None
    selected = query.track_record(AT, 7, 1, {"revision": "4"}, 1, 25)
    assert selected.total == 1 and selected.summary.median_return_pct == 100
    exact = query.track_record(AT, 7, 1, {"cohort": selected.cohorts[0].id}, 1, 25)
    assert exact.items == selected.items
    negative = query.track_record(AT, 7, 1, {"bucket": 1}, 1, 25)
    assert negative.total == 1 and negative.items[0].return_pct == -50


def test_unresolved_legacy_and_fixture_inputs_never_become_returns(repo, token, config, clock):
    identity, snapshot = sent(repo, token, config, clock, legacy=True)
    repo.research_outcomes_enabled = False
    checkpoint(repo, snapshot, clock, 2)
    repo.research_outcomes_enabled = True
    with repo.db:
        repo.db.execute("UPDATE signal_outcomes SET due_at=0 WHERE alert_id=?", (identity,))
    row = checkpoint_rows(repo, AT - 86400, AT, 1)[0]
    assert row["knowledge"] == "legacy_unknown" and row["recorded_at"] is None
    assert row["state"] == "unresolved" and row["return_pct"] is None
    _, second = sent(repo, token, config, clock, index=1)
    checkpoint(repo, second, clock, 2, origin="test")
    public = checkpoint_rows(repo, AT - 86400, AT, 1, synthetic=False)
    assert public[0]["return_pct"] is None and public[0]["checkpoint_price"] is None
    _, third = sent(repo, token, config, clock, index=2, origin="synthetic")
    assert not any(
        r["contract_address"] == third.contract_address
        for r in checkpoint_rows(repo, AT - 86400, AT, 1, synthetic=False)
    )


async def test_api_csv_all_rows_parity_bounds_and_formula_safety(repo, token, config, clock):
    for i in range(3):
        _, snapshot = sent(repo, token, config, clock, index=i, symbol='=HYPERLINK("hostile")')
        checkpoint(repo, snapshot, clock, [2, 0.5, 1][i])
    path = repo.db.execute("PRAGMA database_list").fetchone()[2]
    app = create_app(WebConfig(database_path=path, web_cache_seconds=0), clock=lambda: AT)
    before = repo.db.total_changes
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get("/api/v1/track-record?days=1&horizon=1&page_size=1")
        assert result.status_code == 200
        body = result.json()
        assert body["total"] == 3 and len(body["items"]) == 1
        export = await client.get("/api/v1/track-record/export?days=1&horizon=1&page_size=1")
        records = list(csv.DictReader(io.StringIO(export.text)))
        assert len(records) == 3 and {float(r["return_pct"]) for r in records} == {100, -50, 0}
        assert all(r["symbol"].startswith("'=HYPERLINK") for r in records)
        assert all(r["timestamp"].endswith("+00:00") for r in records)
        assert "DO-NOT-EXPOSE" not in result.text + export.text
        assert all(r["methodology"] == body["methodology_version"] for r in records)
        for query in (
            "days=366",
            "horizon=2",
            "as_of=nan",
            f"as_of={AT + 1}",
            "cohort=bad",
            "bucket=5",
        ):
            assert (await client.get("/api/v1/track-record?" + query)).status_code == 422
    assert repo.db.total_changes == before


def test_six_to_seven_migration_preserves_legacy_without_backfill(repo, token, config, clock):
    identity, snapshot = sent(repo, token, config, clock, legacy=True)
    for table in (
        "research_alert_facts",
        "research_delivery_receipts",
        "research_checkpoint_facts",
    ):
        repo.db.execute(f"DROP TABLE {table}")
    repo.db.execute("PRAGMA user_version=6")
    repo.db.commit()
    saved = [tuple(r) for r in repo.db.execute("SELECT * FROM alerts")]
    path = repo.db.execute("PRAGMA database_list").fetchone()[2]
    upgraded = connect(__import__("pathlib").Path(path))
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 8
    assert [tuple(r) for r in upgraded.execute("SELECT * FROM alerts")] == saved
    assert upgraded.execute("SELECT COUNT(*) FROM research_alert_facts").fetchone()[0] == 0
    assert upgraded.execute("SELECT COUNT(*) FROM research_briefs").fetchone()[0] == 1
    upgraded.close()


async def test_cohort_cache_ignores_scanner_churn_but_invalidates_review_and_receipts(
    repo, token, config, clock
):
    identity, snapshot = sent(repo, token, config, clock)
    query = PublicQueries(repo.db)
    original_version = query.version(AT, "track-record")
    repo.save_snapshot(changed(token, timestamp=AT, price=99), origin="live")
    repo.begin_scan(AT, {}, origin="live")
    assert query.version(AT, "track-record") == original_version
    checkpoint(repo, snapshot, clock, 0.5)
    assert query.version(AT, "track-record") != original_version
    path = repo.db.execute("PRAGMA database_list").fetchone()[2]
    app = create_app(WebConfig(database_path=path, web_cache_seconds=60), clock=lambda: AT)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        before = (await client.get("/api/v1/track-record?horizon=1")).json()
        assert before["total"] == 1
        classify(
            repo.db,
            json.dumps(
                {
                    "kind": "alert",
                    "id": identity,
                    "origin": "synthetic",
                    "evidence": "Private reviewed fixture",
                }
            ).encode(),
        )
        after = (await client.get("/api/v1/track-record?horizon=1")).json()
        assert after["total"] == 0
        assert after["reconciliation"]["known_fixture_alerts_excluded"] == 1
        empty_csv = await client.get("/api/v1/track-record/export?horizon=1")
        assert list(csv.DictReader(io.StringIO(empty_csv.text))) == []
        assert "cohort_metadata_json=" in empty_csv.text


def test_repeated_tokens_count_as_alerts_and_deadline_boundaries_are_explicit(
    repo, token, config, clock
):
    sent(repo, token, config, clock, timestamp=AT - 2 * 86400)
    _, snapshot = sent(repo, token, config, clock, timestamp=AT - 7200)
    assert len(checkpoint_rows(repo, AT - 7 * 86400, AT, 1)) == 2
    assert len({r["contract_address"] for r in checkpoint_rows(repo, AT - 7 * 86400, AT, 1)}) == 1
    checkpoint(repo, snapshot, clock, 1, recorded=AT, observed=AT)
    rows = checkpoint_rows(repo, AT - 7 * 86400, AT, 1)
    assert rows[0]["return_pct"] == 0  # due+1h is inclusive, unlike missingness.

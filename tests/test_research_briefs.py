import json
import sqlite3
from types import SimpleNamespace

import pytest

from revival_radar.models.brief import ResearchBrief, build_brief
from revival_radar.models.signal import RevivalResult
from revival_radar.storage.provenance import classify
from revival_radar.web.queries import PublicQueries

from .conftest import changed

AT = 1_800_000_000


def inputs(token, **overrides):
    bars = [
        dict(timestamp=AT - (25 - i) * 3600, open=10, high=11, low=9, close=10, volume_usd=100)
        for i in range(25)
    ]
    bars[-1].update(high=13, close=12, volume_usd=200)
    data = dict(
        identity=1,
        scan_id=1,
        scan_started_at=AT - 10,
        token=changed(token, timestamp=AT),
        result=RevivalResult(score=88, status="WATCH", eligible=True, returning_activity=True),
        blockers=[],
        missing=[],
        configuration={"preset": "Balanced", "revision": 4},
        origin="live",
        cache=dict(fetched_at=AT - 1, expires_at=AT + 3600, payload=json.dumps({"candles": bars})),
        cache_origin="live",
        saved_at=AT,
        brief_id="saved-version",
    )
    return data | overrides


def test_grounded_bull_and_bear_with_source_round_trip(token):
    data = inputs(token)
    brief = build_brief(**data)
    assert brief.outcome == "bull"
    assert ResearchBrief.model_validate_json(brief.model_dump_json()) == brief
    for claim in brief.support + brief.counter_evidence + brief.unresolved:
        source = next(s for s in brief.sources if s.id == claim.source_id)
        assert claim.field in source.values or claim.field == "coverage"
        if claim.value is not None:
            assert claim.value == source.values[claim.field]
    assert brief.sources[-1].values["relative_volume"] == 2
    assert [s.level for s in brief.scenarios] == [None, 11, 9]
    bars = json.loads(data["cache"]["payload"])["candles"]
    bars[-1].update(low=7, close=8)
    data["cache"]["payload"] = json.dumps({"candles": bars})
    assert build_brief(**data).outcome == "bear"


@pytest.mark.parametrize(
    "failure",
    ["gap", "missing", "zero", "stale", "future", "duplicate", "unknown", "blocker", "revision"],
)
def test_unknown_or_incomplete_evidence_is_watch(token, failure):
    data = inputs(token)
    bars = json.loads(data["cache"]["payload"])["candles"]
    if failure == "gap":
        bars.pop(5)
    elif failure == "missing":
        bars[5]["volume_usd"] = None
    elif failure == "zero":
        for bar in bars[:-1]:
            bar["volume_usd"] = 0
    elif failure == "stale":
        data["cache"]["expires_at"] = AT - 1
    elif failure == "future":
        data["cache"]["fetched_at"] = AT + 1
    elif failure == "duplicate":
        bars[-10] = bars[-11]
    elif failure == "unknown":
        data["origin"] = "unknown"
    elif failure == "blocker":
        data["blockers"] = ["security_dangerous"]
    else:
        data["configuration"] = None
    data["cache"]["payload"] = json.dumps({"candles": bars})
    brief = build_brief(**data)
    assert brief.outcome == "watch"
    assert brief.unresolved or brief.counter_evidence


def test_persistence_causal_replay_and_cache_replacement(repo, token, config, monkeypatch):
    clock = AT
    monkeypatch.setattr("revival_radar.storage.repository.time.time", lambda: clock)
    data = inputs(token)
    token = data["token"]
    repo.cache_save("kline:1h:168", token, data["cache"]["payload"], AT + 3600, origin="live")
    scan = repo.begin_scan(AT - 10, {})
    repo.record_evaluation(
        scan, token, data["result"], config, data["configuration"], origin="live"
    )
    query = PublicQueries(repo.db)
    assert query.briefs(*token.key, AT + 100, 1, 10).total == 0
    repo.finish_scan(scan, SimpleNamespace(processed=1), AT + 2)
    original = query.briefs(*token.key, AT + 2, 1, 10).items[0]
    assert query.briefs(*token.key, AT + 1, 1, 10).items == []
    clock = AT + 100
    # Newly saved evidence has an old observation time: it must not enter past replay.
    next_scan = repo.begin_scan(AT + 90, {})
    repo.cache_save("kline:1h:168", token, '{"candles":[]}', AT + 3600, origin="live")
    repo.record_evaluation(
        next_scan, token, data["result"], config, {"preset": "Broad", "revision": 5}, origin="live"
    )
    repo.finish_scan(next_scan, SimpleNamespace(processed=1), AT + 101)
    assert query.briefs(*token.key, AT + 2, 1, 10).items == [original]
    versions = query.briefs(*token.key, AT + 101, 1, 10)
    assert versions.total == 2 and versions.items[-1] == original
    assert versions.items[0].strategy_revision == 5
    for sql in (
        "UPDATE research_briefs SET payload='{}'",
        "DELETE FROM research_briefs",
        "UPDATE research_brief_publications SET published_at=0",
        "DELETE FROM research_brief_publications",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            repo.db.execute(sql)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        repo.db.execute("INSERT OR REPLACE INTO research_briefs SELECT * FROM research_briefs")
    repo.db.rollback()
    # Archive independence: removing rotating evaluation/cache data cannot rewrite versions.
    with repo.db:
        repo.db.execute("DELETE FROM evaluations")
        repo.db.execute("DELETE FROM scan_runs")
        repo.db.execute("DELETE FROM enrichment_cache")
    assert query.briefs(*token.key, AT + 101, 1, 10).items[-1] == original


def test_no_backfill_origin_isolation_and_idempotent_retry(repo, token, config, monkeypatch):
    monkeypatch.setattr("revival_radar.storage.repository.time.time", lambda: AT)
    assert PublicQueries(repo.db).briefs(*token.key, AT, 1, 10).items == []
    data = inputs(token)
    for origin in ("synthetic", "test", "unknown"):
        scan = repo.begin_scan(AT - 10, {})
        repo.record_evaluation(scan, data["token"], data["result"], config, origin=origin)
        repo.record_evaluation(scan, data["token"], data["result"], config, origin=origin)
        repo.finish_scan(scan, SimpleNamespace(processed=1), AT + 1)
    assert repo.db.execute("SELECT COUNT(*) FROM research_briefs").fetchone()[0] == 3
    assert PublicQueries(repo.db).briefs(*token.key, AT + 1, 1, 10).total == 1
    assert PublicQueries(repo.db, allow_synthetic=True).briefs(*token.key, AT + 1, 1, 10).total == 2


def test_reviewed_quarantine_follows_archived_uuid_without_rewriting_history(
    repo, token, config, monkeypatch
):
    monkeypatch.setattr("revival_radar.storage.repository.time.time", lambda: AT)
    data = inputs(token)
    scan = repo.begin_scan(AT - 10, {})
    repo.record_evaluation(scan, data["token"], data["result"], config, origin="live")
    repo.finish_scan(scan, SimpleNamespace(processed=1), AT + 1)
    query = PublicQueries(repo.db)
    original = query.briefs(*token.key, AT + 1, 1, 10).items[0]

    def attest(origin):
        classify(
            repo.db,
            json.dumps(
                {
                    "kind": "evaluation",
                    "id": original.evaluation_id,
                    "origin": origin,
                    "evidence": "Private reviewed fixture attestation",
                }
            ).encode(),
        )

    classify(
        repo.db,
        json.dumps(
            {"kind": "scan", "id": scan, "origin": "synthetic", "evidence": "Reviewed scan fixture"}
        ).encode(),
    )
    attest("unknown")
    assert query.briefs(*token.key, AT + 1, 1, 10).total == 0
    classify(
        repo.db,
        json.dumps(
            {"kind": "scan", "id": scan, "origin": "unknown", "evidence": "Reviewed reversal"}
        ).encode(),
    )
    attest("synthetic")
    assert query.briefs(*token.key, AT + 1, 1, 10).total == 0
    attest("unknown")
    assert query.briefs(*token.key, AT + 1, 1, 10).items == [original]
    attest("test")
    repo.prune_diagnostics(AT)
    assert query.briefs(*token.key, AT + 1, 1, 10).total == 0
    archived = repo.db.execute(
        "SELECT payload FROM research_briefs WHERE id=?", (original.id,)
    ).fetchone()[0]
    assert ResearchBrief.model_validate_json(archived) == original
    next_scan = repo.begin_scan(AT + 10, {})
    repo.record_evaluation(
        next_scan, changed(token, timestamp=AT + 10), data["result"], config, origin="live"
    )
    repo.finish_scan(next_scan, SimpleNamespace(processed=1), AT + 11)
    public = query.briefs(*token.key, AT + 11, 1, 10)
    assert public.total == 1 and public.items[0].id != original.id

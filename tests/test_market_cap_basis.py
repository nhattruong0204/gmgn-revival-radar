import hashlib
import json

from revival_radar.analysis.market_cap_basis import SAMPLE_CAP, SAMPLE_KEY, basis_report
from revival_radar.clients.normalization import enriched_token, ranked_token
from revival_radar.models.token import TokenSnapshot

from .conftest import changed

ADDRESS = "So11111111111111111111111111111111111111112"
RANK_ROW = {"address": ADDRESS, "symbol": "X", "price": "0.001", "market_cap": "900000"}
INFO = {"circulating_supply": "1000000000", "price": {"price": "0.001"}, "total_supply": "2e9"}


def ranked(raw=None, now=1_800_000_000.0):
    return ranked_token(raw or RANK_ROW, "sol", "trending", 1, "1h", now)


def test_rank_snapshot_records_rank_source():
    token = ranked()
    assert token.market_cap == 900000 and token.market_cap_source == "rank"
    assert token.circulating_supply is None
    assert json.loads(token.model_dump_json())["market_cap_source"] == "rank"


def test_rank_row_without_market_cap_has_unknown_source():
    token = ranked({k: v for k, v in RANK_ROW.items() if k != "market_cap"})
    assert token.market_cap is None and token.market_cap_source is None


def test_enriched_snapshot_records_circulating_source_and_supply():
    token = enriched_token(ranked(), INFO, {})
    assert token.market_cap == 1_000_000 and token.market_cap_source == "circulating_enriched"
    assert token.circulating_supply == 1_000_000_000


def test_enrichment_without_supply_keeps_rank_source():
    token = enriched_token(ranked(), {"price": {"price": "0.002"}}, {})
    assert token.market_cap == 900000 and token.market_cap_source == "rank"
    assert token.circulating_supply is None


def test_snapshot_persistence_includes_source_in_payload_only(repo):
    token = enriched_token(ranked(), INFO, {})
    repo.save_snapshot(token)
    columns = {r["name"] for r in repo.db.execute("PRAGMA table_info(token_snapshots)")}
    assert "market_cap_source" not in columns  # payload JSON only, no schema change
    row = repo.db.execute("SELECT payload FROM token_snapshots").fetchone()
    stored = TokenSnapshot.model_validate_json(row["payload"])
    assert stored.market_cap_source == "circulating_enriched"
    assert stored.circulating_supply == 1_000_000_000


def test_legacy_payload_loads_with_unknown_source(token):
    legacy = json.loads(token.model_dump_json())
    legacy.pop("market_cap_source")
    legacy.pop("circulating_supply")
    loaded = TokenSnapshot.model_validate(legacy)
    assert loaded.market_cap_source is None and loaded.circulating_supply is None


def sample_pairs(repo):
    return json.loads(repo.get_state(SAMPLE_KEY))


def test_sample_stores_pair_and_cap_is_enforced(repo):
    for n in range(SAMPLE_CAP + 7):
        repo.save_snapshot(enriched_token(ranked(now=1_800_000_000.0 + n), INFO, {}))
    pairs = sample_pairs(repo)
    assert len(pairs) == SAMPLE_CAP
    assert pairs[-1]["rank"]["timestamp"] == 1_800_000_000.0 + SAMPLE_CAP + 6
    assert pairs[0]["rank"]["timestamp"] == 1_800_000_000.0 + 7  # oldest dropped
    pair = pairs[-1]
    assert pair["chain"] == "sol" and pair["address"] == ADDRESS
    assert pair["rank"]["market_cap"] == 900000 and pair["info"]["circulating_supply"] == 1e9
    assert pair["info"]["total_supply"] == 2e9
    assert (
        set(pair["rank"])
        == set(pair["info"])
        == {
            "price",
            "market_cap",
            "circulating_supply",
            "total_supply",
            "timestamp",
        }
    )


def test_rank_only_snapshots_do_not_create_a_sample(repo):
    repo.save_snapshot(ranked())
    assert repo.get_state(SAMPLE_KEY) is None


def pair(mc, price, circ, total):
    return {
        "rank": {"market_cap": mc, "price": price},
        "info": {"price": price, "circulating_supply": circ, "total_supply": total},
    }


def test_report_identifies_circulating_basis():
    pairs = [pair(1e6 * (1 + i / 1000), 0.001, 1e9, 2e9) for i in range(10)]
    report = basis_report(json.dumps(pairs))
    assert report["pairs"] == 10
    assert report["rank_over_price_x_circulating"]["n"] == 10
    assert report["rank_over_price_x_circulating"]["median"] == 1.0045
    assert report["rank_over_price_x_total"]["median"] < 0.6
    assert "circulating" in report["verdict"]


def test_report_identifies_total_basis():
    report = basis_report(json.dumps([pair(2e6, 0.001, 1e9, 2e9)] * 6))
    assert report["rank_over_price_x_total"]["within_5pct"] == 6
    assert "total_supply" in report["verdict"]


def test_report_is_pending_for_small_or_missing_samples():
    assert basis_report(None)["verdict"].startswith("pending")
    assert basis_report("not json")["pairs"] == 0
    assert basis_report(json.dumps([pair(1e6, 0.001, 1e9, 2e9)]))["verdict"].startswith("pending")


def test_report_ignores_unusable_pairs():
    report = basis_report(json.dumps([pair(0, 0.001, 1e9, None), pair(1e6, None, 1e9, None)]))
    assert report["rank_over_price_x_circulating"]["n"] == 0


def row_hash(db, row_id):
    row = db.execute("SELECT * FROM token_snapshots WHERE id=?", (row_id,)).fetchone()
    return hashlib.sha256(repr(tuple(row)).encode()).hexdigest()


def test_new_saves_never_rewrite_existing_snapshot_rows(repo, token):
    repo.save_snapshot(changed(token, timestamp=1_700_000_000.0, market_cap=5.0))
    repo.db.execute("UPDATE token_snapshots SET payload=json_remove(payload,'$.market_cap_source')")
    repo.db.commit()
    legacy_id = repo.db.execute("SELECT id FROM token_snapshots").fetchone()["id"]
    before = row_hash(repo.db, legacy_id)
    repo.save_snapshot(enriched_token(ranked(now=1_800_000_000.0), INFO, {}))
    # Same natural key again: INSERT OR IGNORE must leave the legacy row untouched.
    repo.save_snapshot(
        changed(token, timestamp=1_700_000_000.0, market_cap=5.0, market_cap_source="rank")
    )
    assert row_hash(repo.db, legacy_id) == before
    assert repo.db.execute("SELECT COUNT(*) FROM token_snapshots").fetchone()[0] == 2
    payload = repo.db.execute(
        "SELECT payload FROM token_snapshots WHERE id=?", (legacy_id,)
    ).fetchone()[0]
    assert "market_cap_source" not in json.loads(payload)

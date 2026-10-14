import json
from types import SimpleNamespace

from revival_radar.market_regime import regime_context

from .test_web_public import NOW, observe, web  # noqa: F401


async def test_reports_expose_latest_saved_regime_context_read_only(web, repo, token, config):  # noqa: F811
    client, _ = web
    observe(repo, token, config, timestamp=NOW - 100)
    assert (await client.get("/api/v1/reports?hours=24")).json()["market_context"] is None

    def save(scan_id, finished, universe):
        context = {
            "sol": {**regime_context([]), "universe_size": universe, "sources": ["hot_search"]}
        }
        repo.set_state(f"scan:metrics:{scan_id}", json.dumps({"market_context": context}))
        repo.db.execute("UPDATE scan_runs SET finished=? WHERE id=?", (finished, scan_id))
        repo.db.commit()

    save(1, NOW - 500, 12)
    scan = repo.begin_scan(NOW - 60, {})
    repo.finish_scan(scan, SimpleNamespace(processed=0), NOW - 50)
    save(scan, NOW - 50, 20)
    before = repo.db.total_changes
    context = (await client.get("/api/v1/reports?hours=24")).json()["market_context"]
    assert context["universe_size"] == 20 and context["scan_finished"] == NOW - 50
    assert context["sources"] == ["hot_search"] and context["chain"] == "sol"
    assert context["median_price_change_1h"] is None
    assert context["median_price_change_1h_reason"] == "insufficient_sample"
    assert "role" not in context and repo.db.total_changes == before
    earlier = (await client.get(f"/api/v1/reports?hours=24&as_of={NOW - 100}")).json()
    assert earlier["market_context"]["universe_size"] == 12

import pytest

from revival_radar.web.queries import PublicQueries

from .conftest import changed
from .test_web_public import NOW, observe
from .test_web_public import web as web


async def test_alerts_filter_by_token_is_validated_bounded_and_scoped(web, repo, token, config):
    client, _ = web
    live = config.model_copy(update={"dry_run": False})
    sent = []
    for index, address in enumerate((token.contract_address, "B" * 32, token.contract_address)):
        candidate = changed(token, contract_address=address)
        snapshot, signal, context = observe(
            repo, candidate, live, timestamp=NOW - 80000 + index * 25200
        )
        identity = repo.reserve_alert(snapshot, signal, live, context)
        repo.finish_alert(identity, "sent", message_id=123456789)
        sent.append(identity)
    snapshot, signal, context = observe(repo, token, live, timestamp=NOW - 500)
    repo.reserve_alert(snapshot, signal, live, context)  # pending: never public

    params = {"chain": "sol", "address": token.contract_address}
    result = (await client.get("/api/v1/alerts", params=params)).json()
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [sent[2], sent[0]]
    assert all(item["performance"] is not None for item in result["items"])
    assert all(
        item["observation"]["contract_address"] == token.contract_address
        for item in result["items"]
    )
    page = {**params, "page_size": 1, "page": 2}
    paged = (await client.get("/api/v1/alerts", params=page)).json()
    assert paged["total"] == 2 and [item["id"] for item in paged["items"]] == [sent[0]]
    scoped = (await client.get("/api/v1/alerts", params={**params, "as_of": NOW - 79000})).json()
    assert [item["id"] for item in scoped["items"]] == [sent[0]]
    other = {"chain": "sol", "address": "C" * 32}
    absent = (await client.get("/api/v1/alerts", params=other)).json()
    assert absent["items"] == [] and absent["total"] == 0
    assert (await client.get("/api/v1/alerts")).json()["total"] == 3
    evm = {"chain": "bsc", "address": "0x" + "AB" * 20}
    assert (await client.get("/api/v1/alerts", params=evm)).status_code == 200


@pytest.mark.parametrize(
    "query",
    [
        "address=" + "A" * 32,
        "chain=sol&address=invalid",
        "chain=bsc&address=" + "A" * 32,
        "chain=sol&address=0x" + "a" * 40,
        "chain=sol&address=" + "A" * 32 + "&page_size=101",
        "chain=sol&address=" + "A" * 32 + "&page=0",
    ],
)
async def test_alerts_token_filter_validation(web, query):
    client, _ = web
    assert (await client.get("/api/v1/alerts?" + query)).status_code == 422


async def test_token_alert_filter_respects_query_deadline(web, monkeypatch):
    client, app = web
    app.state.config.web_read_timeout_ms = 100

    def expensive(self, until, chain, page, page_size, address=None):
        self.db.execute(
            "WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL "
            "SELECT n+1 FROM numbers WHERE n<10000000) SELECT sum(n) FROM numbers"
        ).fetchone()

    monkeypatch.setattr(PublicQueries, "alerts", expensive)
    response = await client.get("/api/v1/alerts", params={"chain": "sol", "address": "A" * 32})
    assert response.status_code == 503
    assert response.json() == {"detail": "Saved observations temporarily unavailable"}

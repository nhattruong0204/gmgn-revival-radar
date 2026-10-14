"""Signals feed `group=token` mode (#67): paginated by token, bounded, deadline-safe."""

from .conftest import changed
from .test_call_performance import make_call
from .test_web_public import NOW, observe
from .test_web_public import web as web  # noqa: F401


def _tokens(token, count):
    return [
        changed(token, contract_address="123456789ABCDEFGHJKLMNPQRSTUVWXYZ"[i] * 32)
        for i in range(count)
    ]


async def test_group_token_pages_over_tokens_and_lists_every_call(web, repo, token, config):  # noqa: F811
    client, _ = web
    a, b, c = _tokens(token, 3)
    ids = {
        "a": [
            make_call(repo, a, config, timestamp=NOW - 40 * 3600 + 7 * 3600 * n) for n in range(3)
        ],
        "b": [make_call(repo, b, config, timestamp=NOW - 20 * 3600)],
        "c": [make_call(repo, c, config, timestamp=NOW - 500)],
    }
    ids["a"].append(make_call(repo, a, config, timestamp=NOW - 100))
    first = (await client.get("/api/v1/alerts?group=token&page_size=2")).json()
    assert first["total"] == 3 and first["page"] == 1
    assert [g["contract_address"] for g in first["groups"]] == [
        a.contract_address,
        c.contract_address,
    ]
    ga = first["groups"][0]
    assert ga["call_count"] == 4 and ga["truncated"] is False
    by_id = {i["id"]: i for i in first["items"]}
    assert set(by_id) == set(ids["a"]) | set(ids["c"])
    stamps = [by_id[i]["timestamp"] for i in ga["alert_ids"]]
    assert stamps == sorted(stamps, reverse=True) and len(stamps) == 4
    assert ga["latest_call_at"] == NOW - 100
    second = (await client.get("/api/v1/alerts?group=token&page_size=2&page=2")).json()
    assert [g["contract_address"] for g in second["groups"]] == [b.contract_address]
    assert [i["id"] for i in second["items"]] == ids["b"]
    assert not set(by_id) & {i["id"] for i in second["items"]}


async def test_group_token_is_bounded_and_flat_mode_has_no_groups(web, repo, token, config):  # noqa: F811
    client, _ = web
    t = _tokens(token, 1)[0]
    for n in range(27):
        make_call(repo, t, config, timestamp=NOW - 10 * 86400 + n * 7 * 3600)
    group = (await client.get("/api/v1/alerts?group=token")).json()
    g = group["groups"][0]
    assert g["call_count"] == 27 and len(g["alert_ids"]) == 25 and g["truncated"] is True
    assert len(group["items"]) == 25
    assert (await client.get("/api/v1/alerts?group=token&page_size=21")).status_code == 422
    assert (await client.get("/api/v1/alerts?group=bogus")).status_code == 422
    flat = (await client.get("/api/v1/alerts?page_size=100")).json()
    assert flat["groups"] is None and flat["total"] == 27


async def test_group_token_excludes_unsent_and_respects_deadline(  # noqa: F811
    web, repo, token, config, monkeypatch
):
    from revival_radar.web.queries import PublicQueries

    client, app = web
    live = config.model_copy(update={"dry_run": False})
    snapshot, signal, context = observe(repo, token, live, timestamp=NOW - 300)
    identity = repo.reserve_alert(snapshot, signal, live, context)
    repo.finish_alert(identity, "failed")
    assert (await client.get("/api/v1/alerts?group=token")).json()["groups"] == []
    make_call(repo, token, config, timestamp=NOW - 200)
    assert (await client.get("/api/v1/alerts?group=token")).json()["total"] == 1

    app.state.config.web_read_timeout_ms = 100

    def slow(self, *args):
        self.db.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<10000000) "
            "SELECT sum(i) FROM n"
        ).fetchone()

    monkeypatch.setattr(PublicQueries, "alerts_by_token", slow)
    response = await client.get("/api/v1/alerts?group=token")
    assert response.status_code == 503


async def test_group_token_honours_the_address_filter(web, repo, token, config):  # noqa: F811
    client, _ = web
    a, b, _c = _tokens(token, 3)
    make_call(repo, a, config, timestamp=NOW - 40 * 3600)
    make_call(repo, a, config, timestamp=NOW - 20 * 3600)
    make_call(repo, b, config, timestamp=NOW - 500)
    page = (
        await client.get(f"/api/v1/alerts?group=token&chain=sol&address={a.contract_address}")
    ).json()
    assert page["total"] == 1 and page["groups"][0]["call_count"] == 2
    assert (
        await client.get("/api/v1/alerts?group=token&address=" + a.contract_address)
    ).status_code == 422

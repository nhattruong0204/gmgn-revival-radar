import sqlite3
import time

from revival_radar.web.app import _run_warmup, create_app
from revival_radar.web.config import WebConfig
from revival_radar.web.queries import PublicQueries

from .test_web_public import NOW, observe
from .test_web_public import web as web  # noqa: F401


async def test_warmup_uses_longer_deadline_but_visitors_keep_normal_deadline(
    web, repo, token, config, monkeypatch
):
    client, app = web
    app.state.config.web_read_timeout_ms = 1500
    app.state.config.web_warmup_timeout_ms = 5000
    app.state.config.web_cache_seconds = 60
    observe(repo, token, config)
    original = PublicQueries.report

    def slow(queries, until, hours):
        time.sleep(1.6)
        return original(queries, until, hours)

    monkeypatch.setattr(PublicQueries, "report", slow)
    # A visitor whose query takes 1.6 s is refused at the unchanged deadline.
    assert (await client.get("/api/v1/reports")).status_code == 503
    async with app.router.lifespan_context(app):
        assert app.state.warmup_complete
        assert app.state.report_warmup_complete
        assert app.state.warmup_status["failed"] == 0
        # Warm-up succeeded through the normal cache path.
        assert (await client.get("/api/v1/reports")).status_code == 200
    # Without the cached warm-up result a visitor is still limited to 1.5 s.
    app.state.public_cache = type(app.state.public_cache)()
    assert (await client.get("/api/v1/reports")).status_code == 503
    assert app.state.config.web_read_timeout_ms == 1500


async def test_warmup_set_order_and_failures_not_cached(web, repo, token, config, monkeypatch):
    client, app = web
    observe(repo, token, config)
    app.state.config.web_cache_seconds = 60
    seen = []
    original = PublicQueries.radar

    def failing(queries, until, **filters):
        seen.append((filters["age_hours"], filters["min_score"], filters["include_ignore"]))
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(PublicQueries, "radar", failing)
    async with app.router.lifespan_context(app):
        assert app.state.warmup_complete
        assert app.state.warmup_status["failed"] == 6
        assert app.state.warmup_status["ok"] == 5
    # Three attempts each, in order: 24/72/168 x {default, all rows}.
    assert [seen[i] for i in range(0, 18, 3)] == [
        (24, 0, False),
        (24, 0, True),
        (72, 0, False),
        (72, 0, True),
        (168, 0, False),
        (168, 0, True),
    ]
    monkeypatch.setattr(PublicQueries, "radar", original)
    # The failure was never cached; a visitor now computes a fresh success.
    assert (await client.get("/api/v1/radar?age_hours=72")).status_code == 200


def test_warmup_budget_is_enforced(web):
    _, app = web
    app.state.config.web_warmup_budget_seconds = 1
    app.state.config.web_warmup_timeout_ms = 100
    calls = []

    def slow_failure():
        calls.append(1)
        time.sleep(0.4)
        raise sqlite3.OperationalError("slow")

    status = _run_warmup(app, [(f"x{i}", slow_failure) for i in range(5)])
    assert status["budget_exhausted"]
    assert status["skipped"] >= 1
    assert status["seconds"] < 2.5
    assert len(calls) < 15


async def test_warmup_flags_false_without_storage(tmp_path):
    app = create_app(WebConfig(database_path=tmp_path / "none.db"), clock=lambda: NOW)
    async with app.router.lifespan_context(app):
        assert app.state.warmup_complete is False
        assert app.state.report_warmup_complete is False

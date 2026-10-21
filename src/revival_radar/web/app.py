import asyncio
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from revival_radar.web.config import WebConfig
from revival_radar.web.public import ResponseCache, router

logger = logging.getLogger("uvicorn.error.warmup")


def _warmup_items(request):
    """Warm-up reads through the public route functions: same read() path, same cache keys."""
    from revival_radar.web import public

    items = [(f"reports/{h}h", lambda h=h: public.reports(request, h, None)) for h in (24, 72, 168)]
    for hours in (24, 72, 168):
        for label, min_score, ignore in (("default", 0, False), ("all", 0, True)):
            items.append(
                (
                    f"radar/{hours}h/{label}",
                    lambda h=hours, m=min_score, i=ignore: public.radar(
                        request,
                        chain=None,
                        status=None,
                        search="",
                        min_score=m,
                        include_ignore=i,
                        sort="score",
                        order="desc",
                        page=1,
                        page_size=25,
                        as_of=None,
                        age_hours=h,
                    ),
                )
            )
    items.append(("meta", lambda: public.metadata(request, None)))
    items.append(
        (
            "alerts/1",
            lambda: public.alerts(
                request,
                chain=None,
                address=None,
                page=1,
                page_size=None,
                as_of=None,
                group=None,
            ),
        )
    )
    return items


def _run_warmup(app, items) -> dict:
    """Blocking warm-up with the startup-only deadline and total budget (monotonic clock)."""
    from revival_radar.web.queries import READ_TIMEOUT_OVERRIDE_MS

    config = app.state.config
    started = time.perf_counter()
    budget = float(config.web_warmup_budget_seconds)
    results = []
    for name, call in items:
        status, attempts, began = "skipped", 0, time.perf_counter()
        for attempt in range(1, 4):
            remaining_ms = int((budget - (time.perf_counter() - started)) * 1000)
            if remaining_ms < 100:
                break
            attempts = attempt
            token = READ_TIMEOUT_OVERRIDE_MS.set(min(config.web_warmup_timeout_ms, remaining_ms))
            try:
                call()
                status = "ok"
                break
            except HTTPException as exc:
                status = f"failed:{exc.status_code}"
            except Exception as exc:  # noqa: BLE001 - startup must never crash on a warm-up read
                status = f"failed:{type(exc).__name__}"
            finally:
                READ_TIMEOUT_OVERRIDE_MS.reset(token)
        seconds = time.perf_counter() - began
        logger.info(
            "warmup item=%s status=%s attempts=%d seconds=%.3f", name, status, attempts, seconds
        )
        results.append({"item": name, "status": status, "seconds": round(seconds, 3)})
    total = time.perf_counter() - started
    summary = {
        "complete": True,
        "ok": sum(r["status"] == "ok" for r in results),
        "failed": sum(r["status"].startswith("failed") for r in results),
        "skipped": sum(r["status"] == "skipped" for r in results),
        "budget_exhausted": any(r["status"] == "skipped" for r in results),
        "seconds": round(total, 3),
    }
    logger.info("warmup finished %s", summary)
    return {**summary, "items": results}


@asynccontextmanager
async def research_startup(app):
    # Construction stays inert. At server startup, before accepting traffic, warm the
    # saved public views through the normal read() path with a startup-only deadline
    # and total budget. Visitor requests keep web_read_timeout_ms; failed/over-budget
    # results never enter the response cache.
    app.state.warmup_complete = False
    app.state.report_warmup_complete = False
    app.state.warmup_status = None
    if app.state.config.database_path.is_file():
        request = SimpleNamespace(app=app)
        status = await asyncio.to_thread(_run_warmup, app, _warmup_items(request))
        app.state.warmup_status = {k: v for k, v in status.items() if k != "items"}
        app.state.warmup_complete = True
        app.state.report_warmup_complete = all(
            r["status"] == "ok"
            for r in status["items"]
            if r["item"] in ("reports/24h", "reports/168h")
        )
    yield


class WebsiteFiles(StaticFiles):
    """Serve the built single-page application and preserve API error boundaries."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            # Missing assets are real 404s, while browser routes receive the app shell.
            if Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def create_app(
    config: WebConfig | None = None, *, clock: Callable[[], float] = time.time
) -> FastAPI:
    """Construct routes without database access, migrations, scans or provider clients."""
    config = config if config is not None else WebConfig()
    app = FastAPI(title="Revival Radar website API", version="1.0.0", lifespan=research_startup)
    app.state.config = config
    app.state.clock = clock
    app.state.public_cache = ResponseCache()
    from revival_radar.web.auth import LoginLimiter

    app.state.login_limiter = LoginLimiter()
    clients: OrderedDict[str, deque[float]] = OrderedDict()
    limit_lock = Lock()

    @app.middleware("http")
    async def public_limits(request: Request, call_next):
        path = request.url.path
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            maximum = 16 * 1024 if path.startswith("/api/v1/admin") else 256 * 1024
            raw_length = request.headers.get("content-length")
            if raw_length:
                try:
                    declared_length = int(raw_length)
                    if declared_length < 0:
                        raise ValueError
                except ValueError:
                    return JSONResponse(
                        {"detail": "Invalid request length"},
                        status_code=400,
                        headers={"Cache-Control": "no-store"},
                    )
                if declared_length > maximum:
                    return JSONResponse(
                        {"detail": "Request body too large"},
                        status_code=413,
                        headers={"Cache-Control": "no-store"},
                    )
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > maximum:
                    return JSONResponse(
                        {"detail": "Request body too large"},
                        status_code=413,
                        headers={"Cache-Control": "no-store"},
                    )
            # Starlette's cached Request replays this bounded body to downstream handlers.
            request._body = bytes(body)
        if path.startswith("/api/v1/") and not path.startswith("/api/v1/admin"):
            client = request.client.host if request.client else "unknown"
            now = time.monotonic()
            with limit_lock:
                timestamps = clients.setdefault(client, deque())
                clients.move_to_end(client)
                while timestamps and timestamps[0] <= now - 60:
                    timestamps.popleft()
                if len(timestamps) >= config.web_public_rate_limit:
                    return JSONResponse(
                        {"detail": "Too many requests; retry shortly"},
                        status_code=429,
                        headers={"Retry-After": "60", "Cache-Control": "no-store"},
                    )
                timestamps.append(now)
                while len(clients) > 4096:
                    clients.popitem(last=False)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Passwords and other request values must never appear in validation errors.
        return JSONResponse(
            {
                "detail": [
                    {name: value for name, value in item.items() if name in {"loc", "msg", "type"}}
                    for item in error.errors()
                ]
            },
            status_code=422,
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(router)
    # Owner routes are always registered and enforce their own configuration/access checks.
    from revival_radar.web.admin import router as admin_router

    app.include_router(admin_router)
    if config.web_static_directory is not None:
        app.mount("/", WebsiteFiles(directory=config.web_static_directory, html=True), name="web")
    return app

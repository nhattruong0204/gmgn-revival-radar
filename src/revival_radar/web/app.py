import asyncio
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
from revival_radar.web.public import ResponseCache, read, router


@asynccontextmanager
async def research_startup(app):
    # Construction stays inert. At server startup, warm only saved public reports
    # before accepting traffic. Each attempt retains its normal read deadline;
    # failed/over-budget results never enter the response cache.
    completed = []
    if app.state.config.database_path.is_file():
        request = SimpleNamespace(app=app)
        for hours in (24, 168):
            for _ in range(3):
                try:
                    await asyncio.to_thread(
                        read,
                        request,
                        None,
                        ("reports", hours),
                        lambda queries, until, h=hours: queries.report(until, h),
                    )
                except HTTPException as exc:
                    if exc.status_code != 503:
                        raise
                else:
                    completed.append(hours)
                    break
    app.state.report_warmup_complete = completed == [24, 168]
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

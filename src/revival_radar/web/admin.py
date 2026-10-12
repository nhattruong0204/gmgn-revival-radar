"""Authenticated administrative routes; bot commands execute in the scanner."""

import json
import secrets
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from revival_radar.storage.repository import Repository
from revival_radar.web.auth import (
    COOKIE_NAME,
    COOKIE_PATH,
    LoginLimiter,
    admin_database,
    csrf_token,
    owner_session,
    require_enabled,
    require_origin,
    token_hash,
    verify_password,
)
from revival_radar.web_commands import (
    CommandError,
    CommandStore,
    DuplicateCommandError,
    QueueFullError,
)

router = APIRouter(prefix="/api/v1/admin", tags=["owner"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=1024)


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    action: Literal["preview", "confirm"]
    expected_revision: int | None = Field(default=None, ge=0)
    desired_action: Literal["settings", "reset", "pause", "resume", "scan", "test"] | None = None
    changes: dict | None = None
    preset: Literal["strict", "balanced", "broad"] | None = None
    preview_id: str | None = Field(default=None, max_length=128)


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/session")
def session(request: Request, response: Response) -> dict:
    _private(response)
    if not request.app.state.config.web_admin_enabled:
        return {"authenticated": False, "enabled": False, "csrf_token": None}
    try:
        current = owner_session(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return {"authenticated": False, "enabled": True, "csrf_token": None}
        raise
    return {
        "authenticated": True,
        "enabled": True,
        **{k: current[k] for k in ("csrf_token", "expires_at")},
    }


@router.post("/session")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    _private(response)
    config = require_enabled(request)
    require_origin(request, config)
    limiter = getattr(request.app.state, "login_limiter", None)
    if limiter is None:
        # Initialized once in create_app; this branch supports alternate embeddings.
        limiter = request.app.state.login_limiter = LoginLimiter()
    limiter.check(request.client.host if request.client else "unknown")
    with limiter.password_lock:
        valid = verify_password(payload.password, config.web_admin_password_hash.get_secret_value())
    if not valid:
        raise HTTPException(401, "Invalid owner credentials.")
    cookie = secrets.token_urlsafe(32)
    csrf = csrf_token(cookie, config.web_admin_password_hash.get_secret_value())
    now = time.time()
    expires = now + config.web_session_hours * 3600
    with admin_database(config, writable=True) as db, db:
        db.execute("DELETE FROM web_sessions WHERE expires_at<=?", (now,))
        db.execute(
            "INSERT INTO web_sessions(token_hash,csrf_hash,created_at,expires_at) VALUES (?,?,?,?)",
            (token_hash(cookie), token_hash(csrf), now, expires),
        )
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        max_age=config.web_session_hours * 3600,
        httponly=True,
        secure=config.web_cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )
    return {"authenticated": True, "enabled": True, "csrf_token": csrf, "expires_at": expires}


@router.delete("/session")
def logout(request: Request, response: Response) -> dict:
    _private(response)
    current = owner_session(request, mutation=True)
    with admin_database(request.app.state.config, writable=True) as db, db:
        db.execute("DELETE FROM web_sessions WHERE token_hash=?", (current["token_hash"],))
    response.delete_cookie(
        COOKIE_NAME,
        path=COOKIE_PATH,
        secure=request.app.state.config.web_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return {"authenticated": False, "csrf_token": None}


def _worker(db) -> dict:
    row = db.execute("SELECT value FROM state WHERE key='web:worker'").fetchone()
    try:
        value = json.loads(row["value"]) if row else {}
        if not isinstance(value, dict):
            value = {}
    except (ValueError, TypeError):
        value = {}
    heartbeat = value.get("heartbeat_at")
    value["available"] = (
        isinstance(heartbeat, (int, float))
        and 0 <= time.time() - heartbeat <= 30
        and value.get("enabled") is True
        and value.get("stopped_at") is None
    )
    return value


@router.get("/status")
def status(request: Request, response: Response) -> dict:
    _private(response)
    owner_session(request)
    with admin_database(request.app.state.config) as db:
        db.execute("BEGIN")
        worker = _worker(db)
        now = time.time()
        health = Repository(db).health(now - 86400, now=now)
        history = [
            dict(row)
            for row in db.execute(
                "SELECT id,started,finished,duration_seconds,processed,errors,sent "
                "FROM scan_runs WHERE started>=? AND started<=? "
                "ORDER BY started DESC,id DESC LIMIT 20",
                (now - 86400, now),
            )
        ]
    return {"worker": worker, "health": health, "scan_history": history}


@router.get("/settings")
def settings(request: Request, response: Response) -> dict:
    _private(response)
    owner_session(request)
    with admin_database(request.app.state.config) as db:
        worker = _worker(db)
    if not worker.get("settings"):
        raise HTTPException(503, "Bot settings are unavailable. Enable the bot control service.")
    return {
        key: worker.get(key)
        for key in ("settings", "revision", "active_revision", "presets", "available")
    }


def _command_view(value: dict) -> dict:
    # Session principals and raw request payloads are internal even within the UI.
    return {key: val for key, val in value.items() if key not in {"owner", "payload"}}


@router.post("/commands", status_code=202)
def submit(payload: CommandRequest, request: Request, response: Response) -> dict:
    _private(response)
    current = owner_session(request, mutation=True)
    if payload.action == "preview":
        if payload.desired_action is None or payload.preview_id is not None:
            raise HTTPException(422, "A preview requires its intended action.")
        value = {"desired_action": payload.desired_action}
        if payload.changes is not None:
            value["changes"] = payload.changes
        if payload.preset is not None:
            value["preset"] = payload.preset
    else:
        if not payload.preview_id or any(
            x is not None for x in (payload.desired_action, payload.changes, payload.preset)
        ):
            raise HTTPException(422, "Confirm the saved preview without replacement changes.")
        value = {"preview_id": payload.preview_id}
    with admin_database(request.app.state.config, writable=True) as db:
        try:
            result = CommandStore(db).enqueue(
                current["owner"], str(payload.id), payload.action, value, payload.expected_revision
            )
        except DuplicateCommandError:
            raise HTTPException(409, "Command ID already belongs to another request.") from None
        except QueueFullError:
            raise HTTPException(429, "The command queue is full. Wait for the bot.") from None
        except CommandError:
            raise HTTPException(422, "Invalid command.") from None
    return _command_view(result)


@router.get("/commands")
def commands(request: Request, response: Response, limit: int = Query(50, ge=1, le=50)) -> dict:
    _private(response)
    current = owner_session(request)
    with admin_database(request.app.state.config) as db:
        values = CommandStore(db).list(current["owner"], limit=limit)
    return {"items": [_command_view(value) for value in values]}


@router.get("/commands/{command_id}")
def command(command_id: UUID, request: Request, response: Response) -> dict:
    _private(response)
    current = owner_session(request)
    with admin_database(request.app.state.config) as db:
        result = CommandStore(db).get(current["owner"], str(command_id))
    if result is None:
        raise HTTPException(404, "Command not found.")
    return _command_view(result)

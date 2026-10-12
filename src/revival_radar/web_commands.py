"""Durable owner-bound requests executed exclusively by the existing bot process."""

import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from typing import Any

from revival_radar.control_service import CONTROL_ACTIONS, SETTINGS_ACTIONS, SharedController
from revival_radar.runtime_settings import (
    ALLOWED_SETTINGS,
    SettingsChangeError,
    StaleRevisionError,
    command_fingerprint,
)
from revival_radar.storage.repository import Repository

log = logging.getLogger(__name__)
PREVIEW_TTL_SECONDS = 600
WORKER_POLL_SECONDS = 0.5
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class CommandError(ValueError):
    """Fixed-message validation failures safe to expose to an authenticated owner."""


class DuplicateCommandError(CommandError):
    pass


class QueueFullError(CommandError):
    pass


def _json(value: Any) -> str:
    try:
        result = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise CommandError("Invalid command payload.") from None
    if len(result.encode()) > 16_384:
        raise CommandError("This request is too large.")
    return result


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in ("payload", "result"):
        result[field] = json.loads(result[field]) if result[field] is not None else None
    return result


class CommandStore:
    def __init__(self, db: sqlite3.Connection, max_outstanding: int = 100):
        self.db, self.max_outstanding = db, max_outstanding

    def enqueue(
        self,
        owner: str,
        command_id: str,
        action: str,
        payload: dict[str, Any],
        expected_revision: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(owner, str) or not 0 < len(owner) <= 128:
            raise CommandError("Invalid owner principal.")
        if not isinstance(command_id, str) or _IDENTIFIER.fullmatch(command_id) is None:
            raise CommandError("Invalid command ID.")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise CommandError("Invalid settings revision.")
        if not isinstance(payload, dict):
            raise CommandError("Invalid command payload.")
        if action == "preview":
            if set(payload) - {"desired_action", "changes", "preset"}:
                raise CommandError("Invalid preview payload.")
            desired = payload.get("desired_action")
            if not isinstance(desired, str) or desired not in CONTROL_ACTIONS:
                raise CommandError("Invalid control action.")
            changes = payload.get("changes", {})
            if not isinstance(changes, dict) or set(changes) - ALLOWED_SETTINGS:
                raise CommandError("This setting cannot be changed through web controls.")
            if desired != "settings" and (changes or "preset" in payload):
                raise CommandError("This action does not accept settings changes.")
            if "preset" in payload and not isinstance(payload["preset"], str):
                raise CommandError("Invalid preset.")
        elif action == "confirm":
            if set(payload) != {"preview_id"} or not isinstance(payload["preview_id"], str):
                raise CommandError("Confirm an existing preview without changing its payload.")
            if _IDENTIFIER.fullmatch(payload["preview_id"]) is None:
                raise CommandError("Invalid preview ID.")
        else:
            raise CommandError("Create a preview and confirm it before performing an action.")
        encoded = _json(payload)
        now = time.time() if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT * FROM web_commands WHERE id=?", (command_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["owner"] != owner
                    or existing["action"] != action
                    or existing["payload"] != encoded
                    or existing["expected_revision"] != expected_revision
                ):
                    raise DuplicateCommandError("This command ID is already bound to a request.")
                self.db.commit()
                return _row(existing)
            pending = self.db.execute(
                "SELECT count(*) FROM web_commands WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if pending >= self.max_outstanding:
                raise QueueFullError("The command queue is full. Check the worker status.")
            self.db.execute(
                "INSERT INTO web_commands(id,owner,action,payload,expected_revision,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (command_id, owner, action, encoded, expected_revision, now),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.get(owner, command_id)

    def get(self, owner: str, command_id: str) -> dict[str, Any] | None:
        return _row(
            self.db.execute(
                "SELECT * FROM web_commands WHERE owner=? AND id=?", (owner, command_id)
            ).fetchone()
        )

    def list(self, owner: str, limit: int = 50) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise CommandError("Command list limit must be between 1 and 100.")
        return [
            _row(row)
            for row in self.db.execute(
                "SELECT * FROM web_commands WHERE owner=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (owner, limit),
            )
        ]

    def claim(self, now: float) -> dict[str, Any] | None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM web_commands WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is not None:
                self.db.execute(
                    "UPDATE web_commands SET status='running',started_at=? WHERE id=?",
                    (now, row["id"]),
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        if row is None:
            return None
        return self.get(row["owner"], row["id"])

    def finish(self, command: dict, status: str, result: dict, now: float) -> None:
        with self.db:
            self.db.execute(
                "UPDATE web_commands SET status=?,result=?,finished_at=? "
                "WHERE id=? AND owner=? AND status='running'",
                (status, _json(result), now, command["id"], command["owner"]),
            )

    def add_preview(self, command: dict, preview: dict, now: float) -> dict:
        identity = uuid.uuid4().hex
        normalized = {"action": preview["action"], "changes": preview["changes"]}
        result = preview | {"preview_id": identity, "expires_at": now + PREVIEW_TTL_SECONDS}
        with self.db:
            self.db.execute(
                "INSERT INTO web_previews(id,owner,payload,revision,expires_at) VALUES(?,?,?,?,?)",
                (
                    identity,
                    command["owner"],
                    _json(normalized),
                    preview["revision"],
                    result["expires_at"],
                ),
            )
            self.db.execute(
                "UPDATE web_commands SET status='succeeded',result=?,finished_at=? WHERE id=?",
                (_json(result), now, command["id"]),
            )
        return result

    def preview(self, owner: str, preview_id: str, now: float) -> dict:
        row = self.db.execute(
            "SELECT * FROM web_previews WHERE id=? AND owner=?", (preview_id, owner)
        ).fetchone()
        if row is None:
            raise CommandError("This preview is unavailable for this owner session.")
        if row["used"]:
            raise CommandError("This preview has already been confirmed.")
        if row["expires_at"] <= now:
            raise CommandError("This preview expired. Request a new preview.")
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value

    def prepare_confirmation(self, command: dict, preview: dict) -> None:
        """Consume once and persist the exact intended effect before an external write."""
        target = (
            preview["revision"] + 1 if preview["payload"]["action"] in SETTINGS_ACTIONS else None
        )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.db.execute(
                "UPDATE web_previews SET used=1 WHERE id=? AND owner=? AND used=0",
                (preview["id"], command["owner"]),
            )
            if cursor.rowcount != 1:
                raise CommandError("This preview has already been confirmed.")
            self.db.execute(
                "UPDATE web_commands SET target_revision=?,result=? "
                "WHERE id=? AND status='running'",
                (
                    target,
                    _json({"execution": preview["payload"], "revision": preview["revision"]}),
                    command["id"],
                ),
            )
            self.db.commit()
            command["target_revision"] = target
            command["result"] = {"execution": preview["payload"], "revision": preview["revision"]}
        except Exception:
            self.db.rollback()
            raise


class WebCommandWorker:
    def __init__(self, controller: SharedController, repo: Repository):
        self.controller, self.repo = controller, repo
        self.store = CommandStore(repo.db)

    def publish(self, now: float | None = None, *, stopped: bool = False) -> None:
        now = time.time() if now is None else now
        schedule = self.controller.schedule_provider() if self.controller.schedule_provider else {}
        snapshot = self.controller.snapshot() | {
            "heartbeat_at": now,
            "stopped_at": now if stopped else None,
            "enabled": bool(self.controller.runtime.base.web_controls_enabled),
            "schedule": schedule,
        }
        self.repo.set_state("web:worker", json.dumps(snapshot, allow_nan=False), now)

    def recover(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        rows = self.store.db.execute("SELECT * FROM web_commands WHERE status='running'").fetchall()
        for row in rows:
            command = _row(row)
            receipt = self.controller.runtime.web_receipt
            execution = (command["result"] or {}).get("execution")
            # A matching atomically persisted receipt is positive evidence. Equal
            # settings alone never prove this request was applied after a crash.
            applied = (
                isinstance(execution, dict)
                and execution.get("action") in SETTINGS_ACTIONS
                and receipt is not None
                and receipt["id"] == command["id"]
                and receipt["revision"] == command["target_revision"]
                and receipt["payload_hash"] == command_fingerprint(execution)
            )
            if applied:
                status = "succeeded"
                result = self.controller._result(
                    execution["action"], "Saved settings recovered after restart."
                )
                result["applied_revision"] = receipt["revision"]
            else:
                status = "failed" if command["action"] == "preview" else "unknown"
                result = {
                    "code": "interrupted",
                    "message": (
                        "Worker restarted before the result was recorded. Request a new preview; "
                        "this action will not be replayed."
                    ),
                }
            self.store.finish(command, status, result, now)

    async def process_one(self, now: float | None = None) -> bool:
        # Explicit time keeps deterministic tests; production records actual completion.
        clock = time.time if now is None else lambda: now
        now = clock()
        command = self.store.claim(now)
        if command is None:
            return False
        prepared = False
        try:
            payload = command["payload"]
            if command["action"] == "preview":
                preview = self.controller.preview(
                    payload["desired_action"],
                    payload.get("changes"),
                    payload.get("preset"),
                    command["expected_revision"],
                )
                self.store.add_preview(command, preview, now)
            else:
                preview = self.store.preview(command["owner"], payload["preview_id"], now)
                expected = command["expected_revision"]
                if expected is not None and expected != preview["revision"]:
                    raise StaleRevisionError("Settings changed. Request a new preview.")
                self.controller.check_revision(preview["revision"])
                execution = preview["payload"]
                # Revalidate the saved canonical payload without allowing the
                # confirm request to supply replacement changes or actions.
                action, changes = execution["action"], execution["changes"]
                checked = self.controller.preview(
                    action,
                    None if action in {"reset", "pause", "resume"} else changes,
                    expected_revision=preview["revision"],
                )
                if execution != {"action": checked["action"], "changes": checked["changes"]}:
                    raise CommandError("This preview is invalid. Request a new preview.")
                self.store.prepare_confirmation(command, preview)
                prepared = True
                result = await self.controller.execute(
                    action, changes, expected_revision=preview["revision"], command_id=command["id"]
                )
                delivery = result.get("delivery_status")
                status = (
                    "unknown"
                    if delivery == "unknown"
                    else "failed"
                    if delivery == "failed"
                    else "succeeded"
                )
                self.store.finish(command, status, result, clock())
        except (CommandError, SettingsChangeError) as exc:
            code = "stale_revision" if isinstance(exc, StaleRevisionError) else "invalid_command"
            self.store.finish(command, "failed", {"code": code, "message": str(exc)}, clock())
        except asyncio.CancelledError:
            # The durable running row is reconciled by recover(), including an
            # uncertain Telegram send, rather than being blindly retried.
            raise
        except Exception as exc:
            log.error("web command failed error=%s", type(exc).__name__)
            receipt = self.controller.runtime.web_receipt
            execution = (command["result"] or {}).get("execution")
            if (
                prepared
                and execution is not None
                and execution["action"] in SETTINGS_ACTIONS
                and receipt is not None
                and receipt["id"] == command["id"]
                and receipt["revision"] == command["target_revision"]
                and receipt["payload_hash"] == command_fingerprint(execution)
            ):
                result = self.controller._result(execution["action"], "Settings were saved.")
                result["applied_revision"] = receipt["revision"]
                self.store.finish(command, "succeeded", result, clock())
                return True
            self.store.finish(
                command,
                "unknown" if prepared else "failed",
                {
                    "code": "execution_error",
                    "message": (
                        "The command result could not be confirmed. "
                        "Check worker status before retrying."
                    ),
                },
                clock(),
            )
        return True

    async def run(self) -> None:
        try:
            recovered = False
            while True:
                try:
                    if not recovered:
                        self.recover()
                        recovered = True
                    self.publish()
                    pending = await self.process_one()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("web controls background error=%s", type(exc).__name__)
                    # An acknowledgement failure can leave a running row and its
                    # atomic receipt. Reconcile before another settings mutation
                    # could replace that evidence, including transient SQLite errors.
                    recovered = False
                    pending = False
                await asyncio.sleep(0 if pending else WORKER_POLL_SECONDS)
        finally:
            # Graceful shutdown is immediately visible. Abrupt exits still age out
            # through the API heartbeat deadline; running commands remain recoverable.
            try:
                self.publish(stopped=True)
            except Exception as exc:
                log.error("web controls shutdown status error=%s", type(exc).__name__)

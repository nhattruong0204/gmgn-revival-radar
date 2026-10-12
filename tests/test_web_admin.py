import json
import time
from uuid import uuid4

import httpx
import pytest

from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.web.app import create_app
from revival_radar.web.auth import COOKIE_NAME, password_hash, verify_password
from revival_radar.web.config import WebConfig

TEST_PASSWORD = "owner-password-for-tests"
ORIGIN = "http://radar.test"


@pytest.fixture(scope="module")
def encoded_password():
    return password_hash(TEST_PASSWORD)


@pytest.fixture
def web_database(tmp_path):
    path = tmp_path / "admin.db"
    db = connect(path)
    db.close()
    return path


@pytest.fixture
def web_config(web_database, encoded_password):
    return WebConfig(
        database_path=web_database,
        web_admin_enabled=True,
        web_admin_password_hash=encoded_password,
        web_cookie_secure=False,
        web_trusted_origin=ORIGIN,
    )


def client_for(config):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(config)),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
    )


async def sign_in(client):
    response = await client.post("/api/v1/admin/session", json={"password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def test_password_hash_is_salted_and_malformed_cost_is_rejected(encoded_password):
    assert verify_password(TEST_PASSWORD, encoded_password)
    assert not verify_password("incorrect", encoded_password)
    assert not verify_password(TEST_PASSWORD, encoded_password.replace("32768", "2147483648"))
    assert not verify_password(TEST_PASSWORD, "not-a-hash")
    assert encoded_password != password_hash(TEST_PASSWORD)
    with pytest.raises(ValueError):
        password_hash("short")


@pytest.mark.parametrize("path", ["status", "settings", "commands", f"commands/{uuid4()}"])
async def test_anonymous_admin_reads_rejected(web_config, path):
    async with client_for(web_config) as client:
        response = await client.get(f"/api/v1/admin/{path}")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"


async def test_login_origin_cookie_csrf_logout_and_storage(web_config):
    async with client_for(web_config) as client:
        response = await client.post(
            "/api/v1/admin/session",
            json={"password": TEST_PASSWORD},
            headers={"Origin": "https://foreign.example"},
        )
        assert response.status_code == 403
        login = await sign_in(client)
        cookie = login.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        session = await client.get("/api/v1/admin/session")
        assert session.json()["authenticated"] is True
        assert session.json()["csrf_token"] == client.headers["X-CSRF-Token"]
        with connect(web_config.database_path) as db:
            saved = db.execute("SELECT * FROM web_sessions").fetchone()
            assert saved["token_hash"] != client.cookies[COOKIE_NAME]
            assert saved["csrf_hash"] != client.headers["X-CSRF-Token"]
        no_csrf = await client.delete("/api/v1/admin/session", headers={"X-CSRF-Token": ""})
        assert no_csrf.status_code == 403
        logged_out = await client.delete("/api/v1/admin/session")
        assert logged_out.status_code == 200
        assert (await client.get("/api/v1/admin/status")).status_code == 401


async def test_session_expiry_and_secure_cookie(web_config):
    async with client_for(web_config) as client:
        await sign_in(client)
        with connect(web_config.database_path) as db:
            db.execute("UPDATE web_sessions SET expires_at=?", (time.time() - 1,))
        assert (await client.get("/api/v1/admin/status")).status_code == 401
    secure = web_config.model_copy(update={"web_cookie_secure": True})
    async with client_for(secure) as client:
        response = await sign_in(client)
        assert "Secure" in response.headers["set-cookie"]


async def test_login_throttle_and_password_not_echoed(web_config):
    async with client_for(web_config) as client:
        invalid = await client.post(
            "/api/v1/admin/session", json={"password": "private-value", "extra": 1}
        )
        assert invalid.status_code == 422
        assert "private-value" not in invalid.text
        for _ in range(10):
            response = await client.post("/api/v1/admin/session", json={"password": "wrong"})
            assert response.status_code == 401
        response = await client.post("/api/v1/admin/session", json={"password": TEST_PASSWORD})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "900"


async def test_admin_disabled_or_unmigrated_never_creates_database(tmp_path, encoded_password):
    path = tmp_path / "absent.db"
    config = WebConfig(database_path=path)
    async with client_for(config) as client:
        assert (await client.get("/api/v1/admin/session")).json()["enabled"] is False
        assert (await client.get("/api/v1/admin/status")).status_code == 503
    enabled = config.model_copy(
        update={
            "web_admin_enabled": True,
            "web_admin_password_hash": encoded_password,
            "web_trusted_origin": ORIGIN,
            "web_cookie_secure": False,
        }
    )
    # Pydantic model_copy does not validate SecretStr; use a validated config.
    enabled = WebConfig(**enabled.model_dump())
    async with client_for(enabled) as client:
        response = await client.post("/api/v1/admin/session", json={"password": TEST_PASSWORD})
        assert response.status_code == 503
    assert not path.exists()


async def test_commands_idempotency_csrf_and_session_ownership(web_config):
    command_id = str(uuid4())
    payload = {"id": command_id, "action": "preview", "desired_action": "scan"}
    async with client_for(web_config) as first, client_for(web_config) as second:
        await sign_in(first)
        await sign_in(second)
        assert (
            await first.post("/api/v1/admin/commands", json=payload, headers={"X-CSRF-Token": ""})
        ).status_code == 403
        created = await first.post("/api/v1/admin/commands", json=payload)
        assert created.status_code == 202, created.text
        assert created.json()["status"] == "queued"
        assert "owner" not in created.json() and "payload" not in created.json()
        assert (await first.post("/api/v1/admin/commands", json=payload)).status_code == 202
        changed = payload | {"desired_action": "test"}
        assert (await first.post("/api/v1/admin/commands", json=changed)).status_code == 409
        assert (await second.get(f"/api/v1/admin/commands/{command_id}")).status_code == 404
        assert (await second.post("/api/v1/admin/commands", json=payload)).status_code == 409
        assert len((await first.get("/api/v1/admin/commands")).json()["items"]) == 1
        assert (await second.get("/api/v1/admin/commands")).json()["items"] == []
    with connect(web_config.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM web_commands").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


async def test_confirm_does_not_accept_replacement_payload(web_config):
    async with client_for(web_config) as client:
        await sign_in(client)
        payload = {
            "id": str(uuid4()),
            "action": "confirm",
            "preview_id": "saved-preview",
            "changes": {"alerts_paused": True},
        }
        assert (await client.post("/api/v1/admin/commands", json=payload)).status_code == 422


async def test_worker_status_staleness_and_settings(web_config):
    with connect(web_config.database_path) as db:
        Repository(db).set_state(
            "web:worker",
            json.dumps(
                {
                    "heartbeat_at": time.time() - 100,
                    "enabled": True,
                    "settings": {"alerts_paused": False},
                    "revision": 5,
                    "active_revision": 4,
                    "presets": {},
                    "schedule": {"running": True},
                }
            ),
        )
    async with client_for(web_config) as client:
        await sign_in(client)
        response = await client.get("/api/v1/admin/status")
        assert response.status_code == 200
        assert response.json()["worker"]["available"] is False
        settings = (await client.get("/api/v1/admin/settings")).json()
        assert settings["revision"] == 5 and settings["active_revision"] == 4
        assert settings["available"] is False

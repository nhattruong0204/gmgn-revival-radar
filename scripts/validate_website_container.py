"""Synthetic HTTPS container smoke; requires built website and Caddy images, no scanner.

Run from the repository root with the project virtual environment.
"""

import json
import os
import secrets
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from revival_radar.storage.database import connect
from revival_radar.storage.repository import Repository
from revival_radar.web.auth import password_hash

root = Path(__file__).resolve().parents[1]
nonce = uuid.uuid4().hex[:8]
web = "radar26-web-" + nonce
proxy = "radar26-proxy-" + nonce
network = "radar26-net-" + nonce


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.DEVNULL).strip()


MINIMUM_SQLITE = (3, 45, 0)
SQLITE_CHECK = (
    "import sqlite3;"
    "print(sqlite3.sqlite_version);"
    f"raise SystemExit(0 if sqlite3.sqlite_version_info >= {MINIMUM_SQLITE!r} else 1)"
)


def assert_modern_sqlite(image, label):
    """The reports/radar query plans depend on SQLite >= 3.45 (issue #104)."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", image, "-c", SQLITE_CHECK],
        capture_output=True,
        text=True,
    )
    version = result.stdout.strip()
    print(f"{label} image SQLite {version}")
    assert result.returncode == 0, f"{label} image SQLite {version} is older than 3.45.0"


def main():
    web_image = os.environ.get("RADAR_STAGE_WEB_IMAGE", "revival-radar-web:staging")
    # CI builds revival-radar:<tag> beside revival-radar-web:<tag>; derive it unless overridden.
    scanner_image = os.environ.get("RADAR_STAGE_SCANNER_IMAGE") or web_image.replace(
        "revival-radar-web:", "revival-radar:", 1
    )
    assert_modern_sqlite(web_image, "website")
    assert_modern_sqlite(scanner_image, "scanner")
    with tempfile.TemporaryDirectory(prefix="radar26-https-") as directory:
        stage = Path(directory)
        database = stage / "staged.db"
        subprocess.run(
            [
                sys.executable,
                str(root / "scripts/seed_website_demo.py"),
                str(database),
            ],
            cwd=root,
            check=True,
        )
        password = secrets.token_urlsafe(24)
        with connect(database) as db:
            Repository(db).set_state(
                "web:worker",
                json.dumps(
                    {
                        "heartbeat_at": time.time() - 100,
                        "enabled": True,
                        "revision": 0,
                        "active_revision": 0,
                        "settings": {"min_holders": 100, "alerts_paused": False},
                        "presets": {},
                        "schedule": {"running": True},
                    }
                ),
            )
        (stage / "web.env").write_text(
            "DATABASE_PATH=/app/data/staged.db\nWEB_ALLOW_SYNTHETIC_DATA=true\nWEB_ADMIN_ENABLED=true\nWEB_ADMIN_PASSWORD_HASH="
            + password_hash(password)
            + "\nWEB_TRUSTED_ORIGIN=https://localhost:18443\nWEB_COOKIE_SECURE=true\n"
        )
        (stage / "web.env").chmod(0o600)
        (stage / "Caddyfile").write_text(
            "https://localhost {\n tls internal\n reverse_proxy " + web + ":8000\n}\n"
        )
        made = []
        try:
            docker("network", "create", network)
            docker(
                "create",
                "--name",
                web,
                "--network",
                network,
                "--env-file",
                str(stage / "web.env"),
                os.environ.get("RADAR_STAGE_WEB_IMAGE", "revival-radar-web:staging"),
            )
            made.append(web)
            docker("cp", str(database), web + ":/app/data/staged.db")
            docker("start", web)
            docker("exec", "--user", "root", web, "chown", "10001:10001", "/app/data/staged.db")
            # The cloud daemon may inject outbound proxy settings. Only the local backend
            # address is exempted so internal container traffic reaches the staged API.
            address = docker(
                "inspect",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                web,
            )
            (stage / "Caddyfile").write_text(
                "https://localhost {\n tls internal\n reverse_proxy " + address + ":8000\n}\n"
            )
            docker(
                "create",
                "--name",
                proxy,
                "--network",
                network,
                "--env",
                "NO_PROXY=" + address,
                "--env",
                "no_proxy=" + address,
                "-p",
                "127.0.0.1:18443:443",
                os.environ.get("RADAR_STAGE_PROXY_IMAGE", "caddy:2-alpine"),
            )
            made.append(proxy)
            docker("cp", str(stage / "Caddyfile"), proxy + ":/etc/caddy/Caddyfile")
            docker("start", proxy)
            time.sleep(1)
            for _ in range(100):
                try:
                    docker(
                        "cp",
                        proxy + ":/data/caddy/pki/authorities/local/root.crt",
                        str(stage / "ca.crt"),
                    )
                    break
                except subprocess.CalledProcessError:
                    time.sleep(0.1)
            context = ssl.create_default_context(cafile=str(stage / "ca.crt"))
            with httpx.Client(
                base_url="https://localhost:18443", verify=context, trust_env=False, timeout=5
            ) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/v1/meta").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                for page in ("/", "/reports", "/outcomes", "/admin/settings"):
                    response = client.get(page)
                    assert (
                        response.status_code == 200
                        and "text/html" in response.headers["content-type"]
                    )
                    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
                for path in ("status", "settings", "commands"):
                    assert client.get("/api/v1/admin/" + path).status_code == 401
                response = client.post(
                    "/api/v1/admin/session",
                    headers={"Origin": "https://localhost:18443"},
                    json={"password": password},
                )
                assert response.status_code == 200
                cookie = response.headers["set-cookie"]
                assert all(word in cookie for word in ("Secure", "HttpOnly", "SameSite=strict"))
                csrf = response.json()["csrf_token"]
                headers = {"Origin": "https://localhost:18443", "X-CSRF-Token": csrf}
                status = client.get("/api/v1/admin/status").json()
                assert status["worker"]["available"] is False
                payload = {
                    "id": str(uuid.uuid4()),
                    "action": "preview",
                    "desired_action": "pause",
                    "expected_revision": 0,
                }
                assert (
                    client.post(
                        "/api/v1/admin/commands",
                        json=payload,
                        headers={"Origin": "https://localhost:18443"},
                    ).status_code
                    == 403
                )
                response = client.post("/api/v1/admin/commands", json=payload, headers=headers)
                assert response.status_code == 202 and response.json()["status"] == "queued"
                assert client.get("/api/v1/alerts").json()["items"] == []
                assert client.delete("/api/v1/admin/session", headers=headers).status_code == 200
                assert client.get("/api/v1/admin/status").status_code == 401
            docker("exec", proxy, "caddy", "validate", "--config", "/etc/caddy/Caddyfile")
            print(
                "Container staging passed: verified HTTPS, same-origin routing, CSP, "
                "secure cookies, "
                "anonymous denial, CSRF, queued command, stale worker, logout, "
                "and no public alerts. "
                "Website image starts no scanner."
            )
        finally:
            for name in reversed(made):
                subprocess.run(
                    ["docker", "rm", "-fv", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            subprocess.run(
                ["docker", "network", "rm", network],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    main()

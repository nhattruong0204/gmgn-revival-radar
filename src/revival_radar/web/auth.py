"""Owner sessions for the website, independent of provider/Telegram credentials."""

import getpass
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager

from fastapi import HTTPException, Request

from revival_radar.storage.database import SCHEMA_VERSION
from revival_radar.web.config import WebConfig

COOKIE_NAME = "radar_session"
COOKIE_PATH = "/api/v1/admin"
_SCRYPT_N = 32768


def password_hash(password: str) -> str:
    """Create a salted scrypt hash; callers must never persist the plaintext."""
    if not 12 <= len(password) <= 1024:
        raise ValueError("Use a password between 12 and 1024 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${_SCRYPT_N}$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", str(_SCRYPT_N), "8", "1"):
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != 32 or len(password) > 1024:
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=_SCRYPT_N,
            r=8,
            p=1,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(cookie: str, credential_hash: str = "") -> str:
    # Bind persisted sessions to the current owner credential without a schema
    # change. Replacing the password hash invalidates old sessions on every route.
    message = b"revival-radar:csrf:v2:" + hashlib.sha256(credential_hash.encode()).digest()
    return hmac.new(cookie.encode(), message, hashlib.sha256).hexdigest()


@contextmanager
def admin_database(config: WebConfig, *, writable: bool = False):
    """Open an existing database in the caller's thread; never create or migrate."""
    db = None
    try:
        mode = "rw" if writable else "ro"
        db = sqlite3.connect(
            config.database_path.resolve().as_uri() + f"?mode={mode}",
            uri=True,
            timeout=config.web_read_timeout_ms / 1000,
        )
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={config.web_read_timeout_ms}")
        if not writable:
            db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise HTTPException(503, "Admin storage requires a scanner-owned upgrade.")
        yield db
    except sqlite3.Error:
        raise HTTPException(503, "Admin storage is unavailable.") from None
    finally:
        if db is not None:
            db.close()


def require_enabled(request: Request) -> WebConfig:
    config = request.app.state.config
    if not config.web_admin_enabled or not config.web_admin_password_hash.get_secret_value():
        raise HTTPException(503, "Owner access is not configured.")
    return config


def require_origin(request: Request, config: WebConfig) -> None:
    expected = config.web_trusted_origin.rstrip("/") or str(request.base_url).rstrip("/")
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or not hmac.compare_digest(origin.encode(), expected.encode()):
        raise HTTPException(403, "Request origin is not allowed.")


def owner_session(request: Request, *, mutation: bool = False) -> dict:
    config = require_enabled(request)
    cookie = request.cookies.get(COOKIE_NAME, "")
    if not cookie or len(cookie) > 128:
        raise HTTPException(401, "Owner sign-in required.")
    identity = token_hash(cookie)
    with admin_database(config) as db:
        row = db.execute(
            "SELECT csrf_hash,expires_at FROM web_sessions WHERE token_hash=?", (identity,)
        ).fetchone()
    current_csrf = csrf_token(cookie, config.web_admin_password_hash.get_secret_value())
    if (
        row is None
        or row["expires_at"] <= time.time()
        or not hmac.compare_digest(token_hash(current_csrf), row["csrf_hash"])
    ):
        raise HTTPException(401, "Owner session expired. Sign in again.")
    if mutation:
        require_origin(request, config)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(token_hash(supplied), row["csrf_hash"]):
            raise HTTPException(403, "Invalid CSRF token.")
    return {
        "owner": "owner:" + identity,
        "token_hash": identity,
        "csrf_token": current_csrf,
        "expires_at": row["expires_at"],
    }


class LoginLimiter:
    """Bounded per-address attempts; trusted proxy must not forward arbitrary IPs."""

    def __init__(self):
        self._attempts: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()
        self.password_lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            while attempts and attempts[0] < now - 900:
                attempts.popleft()
            self._attempts.move_to_end(key)
            while len(self._attempts) > 1024:
                self._attempts.popitem(last=False)
            if len(attempts) >= 10:
                raise HTTPException(
                    429,
                    "Too many sign-in attempts. Try again later.",
                    headers={"Retry-After": "900"},
                )
            attempts.append(now)


def main() -> None:
    password = getpass.getpass("Owner password (minimum 12 characters): ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords differ.")
    try:
        print(password_hash(password))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()

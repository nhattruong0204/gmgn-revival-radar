from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebConfig(BaseSettings):
    """Website settings intentionally do not load the scanner's .env or credentials."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_path: Path = Path("data/revival_radar.db")
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8000, ge=1, le=65535)
    web_read_timeout_ms: int = Field(default=1500, ge=100, le=10000)
    # Startup-only warm-up limits; visitor requests keep web_read_timeout_ms.
    web_warmup_timeout_ms: int = Field(default=20000, ge=100, le=60000)
    web_warmup_budget_seconds: int = Field(default=90, ge=1, le=300)
    web_allow_synthetic_data: bool = False
    web_cache_seconds: int = Field(default=10, ge=0, le=60)
    web_admin_password_hash: SecretStr = SecretStr("")
    web_cookie_secure: bool = True
    web_session_hours: int = Field(default=12, ge=1, le=168)
    web_trusted_origin: str = ""
    web_admin_enabled: bool = False
    web_public_rate_limit: int = Field(default=120, ge=10, le=10000)
    web_telegram_url: str = ""
    web_static_directory: Path | None = None

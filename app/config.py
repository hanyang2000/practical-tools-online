from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(os.getenv("PRACTICAL_ENV_FILE")) if os.getenv("PRACTICAL_ENV_FILE") else Path.cwd() / ".env", override=False)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    host: str
    port: int
    database_url: str
    data_root: Path
    cookie_secure: bool
    session_days: int
    auto_create_tables: bool
    max_upload_bytes: int
    update_manifest_url: str
    allow_remote_update: bool
    agent_min_protocol_version: int
    agent_update_manifest_url: str

    @property
    def storage_root(self) -> Path: return self.data_root / "storage"
    @property
    def cache_root(self) -> Path: return self.data_root / "cache"
    @property
    def logs_root(self) -> Path: return self.data_root / "logs"

    def ensure_directories(self) -> None:
        for path in (self.data_root, self.storage_root, self.cache_root, self.logs_root):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("PRACTICAL_APP_ENV", "development"),
        host=os.getenv("PRACTICAL_HOST", "127.0.0.1"),
        # Same port as 主图一条龙服务协同版; the two center services are
        # intentionally mutually exclusive on the limited host.
        port=int(os.getenv("PRACTICAL_PORT", "18180")),
        database_url=os.getenv("PRACTICAL_DATABASE_URL", "sqlite:///./practical_tools.db"),
        data_root=Path(os.getenv("PRACTICAL_DATA_ROOT", str(Path.home() / ".practical_tools_online"))).expanduser().resolve(),
        cookie_secure=_bool("PRACTICAL_COOKIE_SECURE", False),
        session_days=max(1, int(os.getenv("PRACTICAL_SESSION_DAYS", "7"))),
        # Production always runs Alembic explicitly.  Development/test users
        # can opt in with PRACTICAL_AUTO_CREATE_TABLES=true.
        auto_create_tables=_bool("PRACTICAL_AUTO_CREATE_TABLES", False),
        max_upload_bytes=max(1, int(os.getenv("PRACTICAL_MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))),
        update_manifest_url=os.getenv("PRACTICAL_UPDATE_MANIFEST_URL", "").strip(),
        allow_remote_update=_bool("PRACTICAL_ALLOW_REMOTE_UPDATE", False),
        agent_min_protocol_version=max(1, int(os.getenv("PRACTICAL_AGENT_MIN_PROTOCOL_VERSION", "1"))),
        agent_update_manifest_url=os.getenv("PRACTICAL_AGENT_UPDATE_MANIFEST_URL", "").strip(),
    )


def reset_settings_cache() -> None:
    get_settings.cache_clear()

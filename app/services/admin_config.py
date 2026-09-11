"""Small center-local configuration store for values managed by /admin."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.config import Settings


def config_path(settings: Settings) -> Path:
    # Storage unit tests use a tiny settings double with only storage_root;
    # keep this helper independent of the concrete Settings dataclass.
    root = getattr(settings, "data_root", None)
    if root is None:
        root = Path(getattr(settings, "storage_root")).parent
    return Path(root) / "admin" / "storage.json"


def load_storage_config(settings: Settings) -> dict:
    path = config_path(settings)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_storage_config(settings: Settings, values: dict) -> None:
    path = config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="storage-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def effective_storage_config(settings: Settings) -> dict:
    saved = load_storage_config(settings)
    return {
        "backend": saved.get("backend") or os.getenv("PRACTICAL_STORAGE_BACKEND", "local"),
        "bucket": saved.get("bucket") or os.getenv("PRACTICAL_COS_BUCKET", ""),
        "region": saved.get("region") or os.getenv("PRACTICAL_COS_REGION", ""),
        "prefix": saved.get("prefix") or os.getenv("PRACTICAL_COS_PREFIX", "practical-tools"),
        "secret_id": saved.get("secret_id") or os.getenv("PRACTICAL_COS_SECRET_ID", ""),
        "secret_key": saved.get("secret_key") or os.getenv("PRACTICAL_COS_SECRET_KEY", ""),
    }

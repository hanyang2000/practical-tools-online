"""Verified .ptmigration.zip handling shared by the CLI and admin console."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = {"browser_state", "cookies", "cookie", ".env", "agent.json", "state.json", "secrets", "tokens"}
MAX_MIGRATION_BYTES = 1024 * 1024 * 1024


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"迁移包包含非法路径：{name}")
    if any(part.lower() in FORBIDDEN_PARTS or "token" in part.lower() or "secret" in part.lower() for part in path.parts):
        raise ValueError(f"迁移包包含禁止迁移内容：{name}")
    return path


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_bundle(bundle: Path) -> dict:
    """Validate a bundle without extracting it and return its manifest."""
    if bundle.stat().st_size <= 0 or bundle.stat().st_size > MAX_MIGRATION_BYTES:
        raise ValueError("迁移包不能为空，且不能超过 1GB")
    with zipfile.ZipFile(bundle) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("迁移包缺少有效 manifest.json") from exc
        if manifest.get("schema_version") != 1:
            raise ValueError("不支持的迁移包版本")
        expected = {str(item.get("path")): item for item in manifest.get("files", [])}
        if not expected:
            raise ValueError("迁移包没有可导入文件")
        actual_names = set()
        for info in archive.infolist():
            if info.filename == "manifest.json" or info.is_dir():
                continue
            path = _safe_member(info.filename)
            # Unix mode 0120000 denotes a symlink; symlinks are never followed.
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError(f"迁移包不允许符号链接：{info.filename}")
            actual_names.add(path.as_posix())
        if actual_names != set(expected):
            raise ValueError("迁移包文件清单与 manifest 不一致")
        total_bytes = 0
        for name, item in expected.items():
            size = 0
            digest = hashlib.sha256()
            with archive.open(name) as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            if int(item.get("size", -1)) != size or item.get("sha256") != digest.hexdigest():
                raise ValueError(f"迁移包校验失败：{name}")
            total_bytes += size
        totals = manifest.get("totals") or {}
        if int(totals.get("files", -1)) != len(expected):
            raise ValueError("迁移包文件数校验失败")
        if int(totals.get("bytes", -1)) != total_bytes:
            raise ValueError("迁移包总大小校验失败")
        for name, item in expected.items():
            kind = item.get("kind")
            path = PurePosixPath(name)
            allowed = (
                (kind == "analysis-cache" and name == "payload/legacy/analysis/cache.json")
                or (kind in {"analysis-db", "analysis-db-sidecar"} and name in {"payload/legacy/db.sqlite3", "payload/legacy/db.sqlite3-wal", "payload/legacy/db.sqlite3-shm"})
                or (kind == "analysis-source" and len(path.parts) == 5 and path.parts[:4] == ("payload", "legacy", "analysis", "source") and path.suffix.lower() == ".xlsx")
                or (kind == "capture-config" and name == "payload/screenshot/config.yaml")
                or (kind == "screenshot" and len(path.parts) >= 6 and path.parts[:3] == ("payload", "screenshot", "screenshots") and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
            )
            if not allowed:
                raise ValueError(f"迁移包文件类型或路径不允许：{name}")
        return manifest


def extract_verified(bundle: Path, destination: Path | None = None) -> tuple[Path, dict]:
    """Extract a verified bundle to a temporary/dedicated directory."""
    manifest = inspect_bundle(bundle)
    root = destination or Path(tempfile.mkdtemp(prefix="practical-migration-"))
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            if info.filename == "manifest.json" or info.is_dir():
                continue
            rel = _safe_member(info.filename)
            target = (root / rel.as_posix()).resolve()
            if root.resolve() != target and root.resolve() not in target.parents:
                raise ValueError("迁移包路径越界")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info.filename) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
    return root, manifest

"""Self-contained center migration bundles.

The center is intentionally migrated as data, not as a copy of its running
directory.  This keeps secrets, sessions and temporary workers out of the
archive while preserving the business tables, Agent bindings and local files
referenced by the center.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import DateTime, select, func
from sqlalchemy.orm import Session

from app import __version__
from app.config import Settings
from app.models import (
    CaptureAgent,
    CaptureJob,
    CaptureSchedule,
    CaptureShop,
    PracticalAnalysisCache,
    PracticalAnalysisDaily,
    PracticalAnalysisFix,
    PracticalAnalysisSource,
    PracticalAuthAccount,
    PracticalScreenshot,
)
from app.services.admin_config import effective_storage_config


CENTER_MIGRATION_FORMAT = "practical-tools-center-migration-v1"
CENTER_MIGRATION_SCHEMA_VERSION = 1
MAX_CENTER_MIGRATION_BYTES = 4 * 1024 * 1024 * 1024
MAX_CENTER_MIGRATION_FILE_BYTES = 2 * 1024 * 1024 * 1024

# Durable application data only.  Sessions, one-time pairing codes, upload
# staging records and old migration task records must not be replayed on a new
# center.  CaptureAgent.token_hash is deliberately retained: it is a one-way
# verifier and is what lets already installed Agents keep working.
EXPORT_MODELS = (
    PracticalAuthAccount,
    PracticalAnalysisSource,
    PracticalAnalysisCache,
    PracticalAnalysisDaily,
    PracticalAnalysisFix,
    CaptureAgent,
    CaptureShop,
    CaptureSchedule,
    CaptureJob,
    PracticalScreenshot,
)
MODEL_BY_TABLE = {model.__tablename__: model for model in EXPORT_MODELS}

# A clean-center migration mode for cases where accounts, Agents, schedules
# and in-flight jobs are intentionally recreated on the new host.  Screenshot
# metadata is retained, but its old owner/Agent/job links are cleared so the
# rows remain valid with newly created users and Agents.
DATA_ONLY_MODELS = (
    PracticalAnalysisSource,
    PracticalAnalysisCache,
    PracticalAnalysisDaily,
    PracticalAnalysisFix,
    CaptureShop,
    PracticalScreenshot,
)
DATA_ONLY_NULL_FIELDS = {
    PracticalAnalysisSource: {"owner_id"},
    PracticalAnalysisDaily: {"owner_id"},
    PracticalAnalysisFix: {"owner_id"},
    CaptureShop: {"owner_id"},
    PracticalScreenshot: {"owner_id", "agent_id", "job_id"},
}


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _decode_value(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _rows_for_model(db: Session, model) -> list[dict[str, Any]]:
    columns = list(model.__table__.columns)
    rows = []
    for row in db.scalars(select(model)).all():
        rows.append({column.name: _json_value(getattr(row, column.name)) for column in columns})
    return rows


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"中心迁移包包含非法路径：{name}")
    forbidden = {".env", "secrets", "tokens", "passwords", "browser_state", "cookies", "logs", "update-staging"}
    if any(part.casefold() in forbidden for part in path.parts):
        raise ValueError(f"中心迁移包包含禁止迁移内容：{name}")
    return path


def _file_kind(path: PurePosixPath) -> str:
    if path.parts[:2] == ("payload", "database"):
        return "database"
    if path.parts[:3] == ("payload", "config", "center-config.json"):
        return "config"
    if path.parts[:2] == ("payload", "program"):
        return "program"
    if path.parts[:3] == ("payload", "files", "analysis"):
        return "analysis-file"
    if path.parts[:3] == ("payload", "files", "storage"):
        return "storage-file"
    raise ValueError(f"中心迁移包文件类型或路径不允许：{path.as_posix()}")


def _config_summary(settings: Settings) -> dict[str, Any]:
    storage = effective_storage_config(settings)
    return {
        "app_env": settings.app_env,
        "port": settings.port,
        "database_backend": str(settings.database_url).split(":", 1)[0],
        "storage": {
            "backend": storage.get("backend", "local"),
            "bucket": storage.get("bucket", ""),
            "region": storage.get("region", ""),
            "prefix": storage.get("prefix", ""),
            "secret_id_configured": bool(storage.get("secret_id")),
            "secret_key_configured": bool(storage.get("secret_key")),
        },
        "required_environment": [
            "PRACTICAL_DATABASE_URL",
            "PRACTICAL_DATA_ROOT",
            "PRACTICAL_COS_SECRET_ID (仅使用 COS 时)",
            "PRACTICAL_COS_SECRET_KEY (仅使用 COS 时)",
        ],
        "secrets_included": False,
        "sessions_included": False,
        "agent_browser_state_included": False,
    }


def _persistent_files(settings: Settings, include_storage: bool) -> list[tuple[Path, str, str]]:
    roots = [(settings.data_root / "analysis", "payload/files/analysis", "analysis-file")]
    if include_storage:
        roots.append((settings.storage_root, "payload/files/storage", "storage-file"))
    result: list[tuple[Path, str, str]] = []
    for root, archive_root, kind in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink() or path.name == ".DS_Store":
                continue
            size = path.stat().st_size
            if size > MAX_CENTER_MIGRATION_FILE_BYTES:
                raise ValueError(f"中心迁移包单个文件不能超过 2GB：{path.name}")
            relative = path.relative_to(root).as_posix()
            result.append((path, f"{archive_root}/{relative}", kind))
    return result


def _program_files(program_root: Path | None) -> list[tuple[Path, str, str]]:
    """Collect the runnable center tree without runtime state or secrets."""
    if program_root is None:
        return []
    root = program_root.expanduser().resolve()
    if not root.is_dir():
        return []
    allowed_roots = {"app", "frontend", "migrations", "packaging", "scripts", "portable", "runtime"}
    allowed_files = {"pyproject.toml", "alembic.ini"}
    excluded_dirs = {"__pycache__", "data", "storage", "cache", "logs", "browser_state", "update-staging", ".git", ".venv"}
    excluded_names = {".env", "agent.json", "state.json"}
    result: list[tuple[Path, str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name == ".DS_Store":
            continue
        relative = path.relative_to(root)
        if not (relative.parts and (relative.parts[0] in allowed_roots or relative.as_posix() in allowed_files)):
            continue
        if any(part in excluded_dirs for part in relative.parts) or path.name in excluded_names or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        size = path.stat().st_size
        if size > MAX_CENTER_MIGRATION_FILE_BYTES:
            raise ValueError(f"中心迁移包单个程序文件不能超过 2GB：{path.name}")
        result.append((path, f"payload/program/{relative.as_posix()}", "program"))
    return result


def build_center_migration_bundle(
    db: Session,
    settings: Settings,
    output: Path,
    *,
    include_storage: bool = True,
    program_root: Path | None = None,
) -> dict[str, Any]:
    """Write a verified center migration archive and return its summary."""
    settings.ensure_directories()
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    table_summary: list[dict[str, Any]] = []
    table_payloads: dict[str, bytes] = {}
    for model in EXPORT_MODELS:
        data = json.dumps(_rows_for_model(db, model), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        archive_name = f"payload/database/{model.__tablename__}.json"
        table_payloads[model.__tablename__] = data
        rows.append({"path": archive_name, "kind": "database", "size": len(data), "sha256": _sha256_bytes(data)})
        table_summary.append({"table": model.__tablename__, "rows": len(json.loads(data)), "path": archive_name})

    config_data = json.dumps(_config_summary(settings), ensure_ascii=False, indent=2).encode("utf-8")
    rows.append({"path": "payload/config/center-config.json", "kind": "config", "size": len(config_data), "sha256": _sha256_bytes(config_data)})
    files = _program_files(program_root) + _persistent_files(settings, include_storage)
    for source, archive_name, kind in files:
        rows.append({"path": archive_name, "kind": kind, "size": source.stat().st_size, "sha256": _sha256_file(source)})

    manifest = {
        "format": CENTER_MIGRATION_FORMAT,
        "schema_version": CENTER_MIGRATION_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_app_version": __version__,
        "database_scope": "practical_* business tables; shared users table is intentionally not copied",
        "program_included": bool(program_root and any(row["kind"] == "program" for row in rows)),
        "tables": table_summary,
        "storage_files": sum(1 for row in rows if row["kind"] in {"analysis-file", "storage-file"}),
        "excluded": ["users", "user_sessions", "practical_auth_sessions", "practical_capture_pair_codes", "practical_capture_uploads", "practical_upload_sessions", "practical_migration_tasks", ".env", "COS secrets", "browser state/cookies", "logs", "update staging"],
        "config": _config_summary(settings),
        "files": rows,
        "totals": {"files": len(rows), "bytes": sum(int(row["size"]) for row in rows)},
    }
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("payload/config/center-config.json", config_data)
        for model in EXPORT_MODELS:
            archive.writestr(f"payload/database/{model.__tablename__}.json", table_payloads[model.__tablename__])
        for source, archive_name, _kind in files:
            archive.write(source, archive_name)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    inspect_center_bundle(output)
    return {"output": str(output), "format": CENTER_MIGRATION_FORMAT, "source_app_version": __version__, "tables": table_summary, "files": len(rows), "bytes": sum(int(row["size"]) for row in rows), "sha256": _sha256_file(output)}


def inspect_center_bundle(bundle: Path, *, verify_program: bool = True) -> dict[str, Any]:
    """Validate paths, hashes, schema and sensitive-file exclusions."""
    bundle = bundle.expanduser().resolve()
    if not bundle.is_file() or bundle.stat().st_size <= 0 or bundle.stat().st_size > MAX_CENTER_MIGRATION_BYTES:
        raise ValueError("中心迁移包不能为空，且不能超过 4GB")
    with zipfile.ZipFile(bundle) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("中心迁移包缺少有效 manifest.json") from exc
        if manifest.get("format") != CENTER_MIGRATION_FORMAT or manifest.get("schema_version") != CENTER_MIGRATION_SCHEMA_VERSION:
            raise ValueError("不支持的中心迁移包版本")
        file_rows = manifest.get("files")
        if not isinstance(file_rows, list) or not file_rows:
            raise ValueError("中心迁移包没有可导入文件")
        expected: dict[str, dict[str, Any]] = {}
        for item in file_rows:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or item["path"] in expected:
                raise ValueError("中心迁移包文件清单无效")
            path = _safe_member(item["path"])
            kind = _file_kind(path)
            if item.get("kind") != kind or int(item.get("size", -1)) < 0 or len(str(item.get("sha256", ""))) != 64:
                raise ValueError(f"中心迁移包清单无效：{path.as_posix()}")
            expected[path.as_posix()] = item
        actual = set()
        total = 0
        for info in archive.infolist():
            if info.filename == "manifest.json" or info.is_dir():
                continue
            path = _safe_member(info.filename).as_posix()
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError(f"中心迁移包不允许符号链接：{info.filename}")
            actual.add(path)
            if info.file_size > MAX_CENTER_MIGRATION_FILE_BYTES:
                raise ValueError(f"中心迁移包单个文件不能超过 2GB：{path}")
        if actual != set(expected):
            raise ValueError("中心迁移包文件清单与 manifest 不一致")
        for path, item in expected.items():
            if item["kind"] == "program" and not verify_program:
                # Data-only import deliberately does not hash the runnable
                # program tree, but those members still count toward the
                # manifest totals and their declared archive size must remain
                # trustworthy.
                try:
                    info = archive.getinfo(path)
                except KeyError as exc:
                    raise ValueError(f"中心迁移包缺少清单文件：{path}") from exc
                if info.file_size != int(item["size"]):
                    raise ValueError(f"中心迁移包文件大小校验失败：{path}")
                total += int(item["size"])
                continue
            size = 0
            digest = hashlib.sha256()
            with archive.open(path) as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            if int(item["size"]) != size or item["sha256"] != digest.hexdigest():
                raise ValueError(f"中心迁移包校验失败：{path}")
            total += size
            if item["kind"] == "database":
                table = PurePosixPath(path).stem
                if table not in MODEL_BY_TABLE:
                    raise ValueError(f"中心迁移包包含不支持的数据表：{table}")
                try:
                    value = json.loads(archive.read(path))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError(f"中心迁移包数据表不是有效 JSON：{table}") from exc
                if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                    raise ValueError(f"中心迁移包数据表格式无效：{table}")
                if table == CaptureAgent.__tablename__:
                    for row in value:
                        token_hash = str(row.get("token_hash", ""))
                        if len(token_hash) != 64 or any(char not in "0123456789abcdefABCDEF" for char in token_hash):
                            raise ValueError("中心迁移包包含无效 Agent 凭据摘要")
        totals = manifest.get("totals") or {}
        if int(totals.get("files", -1)) != len(expected) or int(totals.get("bytes", -1)) != total:
            raise ValueError("中心迁移包总数校验失败")
        return manifest


def extract_center_bundle(bundle: Path, destination: Path | None = None, *, include_program: bool = True) -> tuple[Path, dict[str, Any]]:
    manifest = inspect_center_bundle(bundle, verify_program=include_program)
    root = destination or Path(tempfile.mkdtemp(prefix="practical-center-migration-"))
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            if info.filename == "manifest.json" or info.is_dir():
                continue
            relative = _safe_member(info.filename)
            if not include_program and _file_kind(relative) == "program":
                continue
            target = (root / relative.as_posix()).resolve()
            if target != root.resolve() and root.resolve() not in target.parents:
                raise ValueError("中心迁移包路径越界")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info.filename) as source, target.open("wb") as destination_file:
                shutil.copyfileobj(source, destination_file, length=1024 * 1024)
    return root, manifest


def import_center_migration_bundle(
    bundle: Path,
    db: Session,
    settings: Settings,
    *,
    allow_nonempty: bool = False,
    overwrite_storage: bool = False,
    data_only: bool = False,
) -> dict[str, Any]:
    """Import a bundle into a new center.

    The default is intentionally conservative: an existing business table
    must be empty. ``--allow-nonempty`` is an explicit operator choice for a
    controlled merge, and conflicting local files still fail unless
    ``--overwrite-storage`` is also supplied. ``data_only`` omits credentials,
    Agents, schedules and jobs, and clears old ownership links from retained
    content so the new center can create its own accounts and Agents.
    """
    root, manifest = extract_center_bundle(bundle, include_program=not data_only)
    created_files: list[Path] = []
    try:
        settings.ensure_directories()
        selected_models = DATA_ONLY_MODELS if data_only else EXPORT_MODELS
        selected_model_set = set(selected_models)
        counts: dict[str, int] = {}
        for model in selected_models:
            count = int(db.scalar(select(func.count()).select_from(model)) or 0)
            counts[model.__tablename__] = count
        if not allow_nonempty and any(counts.values()):
            populated = ", ".join(f"{name}={count}" for name, count in counts.items() if count)
            raise ValueError(f"目标中心已有业务数据，未执行导入：{populated}；如确认合并请显式使用 --allow-nonempty")

        file_entries = [item for item in manifest["files"] if item["kind"] in {"analysis-file", "storage-file"}]
        for item in file_entries:
            relative = PurePosixPath(item["path"]).relative_to(PurePosixPath("payload/files"))
            target = (settings.data_root / relative.as_posix()).resolve()
            data_source = (root / item["path"]).resolve()
            if target != settings.data_root.resolve() and settings.data_root.resolve() not in target.parents:
                raise ValueError(f"中心迁移目标路径越界：{item['path']}")
            if target.exists():
                if _sha256_file(target) == item["sha256"]:
                    continue
                if not overwrite_storage:
                    raise ValueError(f"目标文件已存在且内容不同：{relative.as_posix()}；如确认覆盖请显式使用 --overwrite-storage")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.migration-{os.getpid()}")
            shutil.copy2(data_source, temporary)
            os.replace(temporary, target)
            created_files.append(target)

        imported_rows: dict[str, int] = {}
        for item in manifest["files"]:
            if item["kind"] != "database":
                continue
            table_name = PurePosixPath(item["path"]).stem
            model = MODEL_BY_TABLE[table_name]
            if model not in selected_model_set:
                continue
            columns = {column.name: column for column in model.__table__.columns}
            values = json.loads((root / item["path"]).read_text(encoding="utf-8"))
            for payload in values:
                row_values = {name: _decode_value(columns[name], value) for name, value in payload.items() if name in columns}
                if data_only:
                    for name in DATA_ONLY_NULL_FIELDS.get(model, set()):
                        if name in row_values:
                            row_values[name] = None
                db.merge(model(**row_values))
            imported_rows[table_name] = len(values)
        db.commit()
        return {"ok": True, "format": CENTER_MIGRATION_FORMAT, "source_app_version": manifest.get("source_app_version", ""), "tables": imported_rows, "files": len(file_entries), "storage_bytes": sum(int(item["size"]) for item in file_entries), "allow_nonempty": allow_nonempty, "data_only": data_only}
    except Exception:
        db.rollback()
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(root, ignore_errors=True)

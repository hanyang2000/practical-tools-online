from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.parse
import urllib.request
import uuid
import zipfile
import subprocess
import sys
import threading
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.auth import CurrentAdmin
from app.config import get_settings
from app.db import get_db
from app.models import CaptureAgent, PracticalAuthAccount, PracticalAuthSession, PracticalMigrationTask, PracticalUploadSession, User, new_id, utcnow
from app.services.storage import TencentCosStorage, sha256_file
from app.security import hash_password, normalize_username
from app.services.admin_config import effective_storage_config, save_storage_config
from app.services.migration_bundle import MAX_MIGRATION_BYTES, extract_verified, inspect_bundle
from app.services.center_migration import build_center_migration_bundle
from app.services.storage import TencentCosStorage
from app.services.update_bundle import MAX_UPDATE_BYTES, inspect_update_bundle

router = APIRouter(prefix="/admin", tags=["admin"])


def _windowless_runtime(runtime: Path) -> Path:
    """Prefer pythonw for the detached online-update worker on Windows."""
    if os.name == "nt" and runtime.name.lower() == "python.exe":
        candidate = runtime.with_name("pythonw.exe")
        if candidate.is_file():
            return candidate
    return runtime


def _hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is None:
        return None
    startupinfo = startupinfo_type()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return startupinfo


@router.get("/migration/center/export", name="export_center_migration")
def export_center_migration(user: CurrentAdmin, db: Session = Depends(get_db)):
    """Export the running center as one transportable program+data archive.

    The archive is built outside the database transaction and removed after
    the response is sent.  It contains the current runnable center tree plus
    durable ``practical_*`` data and local business files, but never the
    center's .env, sessions, COS secrets or browser login state.
    """
    settings = get_settings()
    settings.ensure_directories()
    directory = settings.data_root / "admin" / "center-exports"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = directory / f"PracticalToolsCenterMigration-{stamp}-{uuid.uuid4().hex[:8]}.ptcenter.zip"
    try:
        report = build_center_migration_bundle(
            db,
            settings,
            path,
            include_storage=True,
            program_root=Path(__file__).resolve().parents[2],
        )
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"中心迁移包导出失败：{exc}") from exc

    def cleanup() -> None:
        path.unlink(missing_ok=True)

    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={"X-Center-Migration-SHA256": report["sha256"]},
        background=BackgroundTask(cleanup),
    )

@router.post("/migration/from-cos")
def migration_from_cos(upload_id: str | None = None, body: dict | None = None, user: CurrentAdmin = None, db: Session = Depends(get_db)):
    upload_id = upload_id or (body or {}).get("upload_id")
    row = db.get(PracticalUploadSession, upload_id)
    if not row or row.owner_id != user.id or row.purpose != "migration_bundle" or row.status != "completed": raise HTTPException(409, "迁移上传会话无效或尚未完成")
    settings = get_settings(); settings.ensure_directories(); directory = settings.data_root / "admin" / "migration-inbox"; directory.mkdir(parents=True, exist_ok=True)
    temp_context = None
    extracted = None
    try:
        with TencentCosStorage().temporary_download(row.storage_key) as path:
            if path.stat().st_size != row.size or sha256_file(path) != row.sha256: raise HTTPException(400, "COS 文件校验失败")
            manifest = inspect_bundle(path)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(400, f"迁移包校验失败：{exc}") from exc
    task = PracticalMigrationTask(filename=row.filename, bundle_path=f"cos://{row.storage_key}", status="uploaded", report={"manifest": manifest, "storage_key": row.storage_key})
    db.add(task); row.status = "consumed"; db.commit(); db.refresh(task)
    return {"ok": True, "task": _task_view(task)}

@router.post("/updates/from-cos")
def update_from_cos(upload_id: str | None = None, body: dict | None = None, user: CurrentAdmin = None, db: Session = Depends(get_db)):
    upload_id = upload_id or (body or {}).get("upload_id")
    row = db.get(PracticalUploadSession, upload_id)
    if not row or row.owner_id != user.id or row.purpose != "update_bundle" or row.status != "completed": raise HTTPException(409, "更新上传会话无效或尚未完成")
    if row.size > MAX_UPDATE_BYTES: raise HTTPException(413, "更新包不能超过 1GB")
    settings = get_settings(); root = _update_root(settings); target = root / f"{uuid.uuid4().hex}-{Path(row.filename).name}.staged.zip"
    try:
        with TencentCosStorage().temporary_download(row.storage_key) as path:
            if path.stat().st_size != row.size or sha256_file(path) != row.sha256: raise HTTPException(400, "COS 文件校验失败")
            manifest = inspect_update_bundle(path); shutil.copyfile(path, target)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(400, f"更新包校验失败：{exc}") from exc
    _rotate_completed_update_marker(root)
    backup = _backup_before_update(settings); pending = {"version": manifest["version"], "package": str(target), "sha256": row.sha256, "size": row.size, "manifest": manifest, "backup": backup, "restart_required": True, "remote_execution": False}
    (root / "pending.json").write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"); row.status = "consumed"; db.commit()
    return {"ok": True, "pending": pending}


def _safe_update_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value or "")
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(400, "更新地址必须是 HTTPS URL")
    return value


def _storage_public(settings) -> dict:
    values = effective_storage_config(settings)
    return {"backend": values["backend"], "bucket": values["bucket"], "region": values["region"], "prefix": values["prefix"], "secret_id_configured": bool(values["secret_id"]), "secret_key_configured": bool(values["secret_key"])}


@router.get("/status")
def status(user: CurrentAdmin, db: Session = Depends(get_db)):
    settings = get_settings()
    agents = db.scalars(select(CaptureAgent).order_by(CaptureAgent.name, CaptureAgent.device_id)).all()
    return {"ok": True, "version": __version__, "environment": settings.app_env, "port": settings.port, "storage": _storage_public(settings), "agents": [{"device_id": a.device_id, "name": a.name, "platform": a.platform, "version": a.version, "status": a.status, "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None, "owner_id": a.owner_id} for a in agents], "updates": _update_status(settings)}


@router.get("/users")
def users(user: CurrentAdmin, db: Session = Depends(get_db)):
    return {"users": [{"id": row.id, "username": row.username, "display_name": row.display_name, "role": row.role, "active": row.active} for row in db.scalars(select(User).where(User.active.is_(True)).order_by(User.username)).all()]}


@router.get("/accounts")
def accounts(user: CurrentAdmin, db: Session = Depends(get_db)):
    return {"accounts": [{"id": a.id, "username": a.username, "role": a.role, "active": a.active, "owner_id": a.owner_id} for a in db.scalars(select(PracticalAuthAccount).order_by(PracticalAuthAccount.username)).all()]}


@router.post("/accounts")
def create_account(body: dict, user: CurrentAdmin, db: Session = Depends(get_db)):
    username, password = normalize_username(str(body.get("username", ""))), str(body.get("password", ""))
    if not username or not password: raise HTTPException(400, "用户名和密码不能为空")
    if db.scalar(select(PracticalAuthAccount).where(PracticalAuthAccount.username == username)): raise HTTPException(409, "用户名已存在")
    owner = db.get(User, body.get("owner_id")) if body.get("owner_id") else user.owner
    if not owner or not owner.active: raise HTTPException(400, "绑定的共享用户不存在或未启用")
    try: password_hash = hash_password(password)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    account = PracticalAuthAccount(id=new_id(), username=username, password_hash=password_hash, role="admin" if body.get("role") == "admin" else "planner", active=bool(body.get("active", True)), owner_id=owner.id)
    db.add(account); db.commit(); db.refresh(account)
    return {"ok": True, "account": {"id": account.id, "username": account.username, "role": account.role, "active": account.active, "owner_id": account.owner_id}}


@router.patch("/accounts/{account_id}")
def update_account(account_id: str, body: dict, user: CurrentAdmin, db: Session = Depends(get_db)):
    account = db.get(PracticalAuthAccount, account_id)
    if not account: raise HTTPException(404, "账号不存在")
    if "username" in body:
        value = normalize_username(str(body["username"]))
        if not value: raise HTTPException(400, "用户名不能为空")
        duplicate = db.scalar(select(PracticalAuthAccount).where(PracticalAuthAccount.username == value, PracticalAuthAccount.id != account_id))
        if duplicate: raise HTTPException(409, "用户名已存在")
        account.username = value
    if "password" in body and body["password"]:
        try: account.password_hash = hash_password(str(body["password"]))
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    if "role" in body and body["role"] in {"admin", "planner"}:
        if account.role == "admin" and body["role"] != "admin" and not _has_other_admin(db, account_id): raise HTTPException(400, "至少保留一个管理员账号")
        account.role = body["role"]
    if "active" in body and not body["active"] and account.role == "admin" and not _has_other_admin(db, account_id): raise HTTPException(400, "至少保留一个启用的管理员账号")
    if "active" in body: account.active = bool(body["active"])
    db.commit(); return {"ok": True}


def _has_other_admin(db, exclude_id):
    return db.scalar(select(PracticalAuthAccount.id).where(PracticalAuthAccount.role == "admin", PracticalAuthAccount.active.is_(True), PracticalAuthAccount.id != exclude_id).limit(1)) is not None


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str, user: CurrentAdmin, db: Session = Depends(get_db)):
    account = db.get(PracticalAuthAccount, account_id)
    if not account: raise HTTPException(404, "账号不存在")
    if account.role == "admin" and account.active and not _has_other_admin(db, account_id): raise HTTPException(400, "至少保留一个启用的管理员账号")
    db.delete(account); db.commit(); return {"ok": True}


@router.get("/settings/storage")
def storage_settings(user: CurrentAdmin):
    return {"ok": True, "storage": _storage_public(get_settings())}


@router.put("/settings/storage")
def update_storage_settings(body: dict, user: CurrentAdmin):
    backend = str(body.get("backend", "local")).strip().lower()
    if backend not in {"local", "tencent_cos"}:
        raise HTTPException(400, "存储类型只支持 local 或 tencent_cos")
    current = effective_storage_config(get_settings())
    values = {"backend": backend, "bucket": str(body.get("bucket", current["bucket"])).strip(), "region": str(body.get("region", current["region"])).strip(), "prefix": str(body.get("prefix", current["prefix"])).strip().strip("/")}
    # Blank secrets mean "keep existing". Secrets are accepted only over the
    # authenticated admin request and are never echoed in the response.
    values["secret_id"] = str(body.get("secret_id") or current["secret_id"])
    values["secret_key"] = str(body.get("secret_key") or current["secret_key"])
    if backend == "tencent_cos" and (not values["bucket"] or not values["region"]):
        raise HTTPException(400, "COS Bucket 和地域不能为空")
    save_storage_config(get_settings(), values)
    return {"ok": True, "storage": _storage_public(get_settings())}


@router.post("/settings/storage/test")
def test_storage(user: CurrentAdmin):
    values = effective_storage_config(get_settings())
    if values["backend"] != "tencent_cos":
        return {"ok": True, "backend": "local", "message": "本地存储已就绪"}
    try:
        TencentCosStorage().ping()
    except Exception as exc:
        raise HTTPException(400, f"COS 连接失败：{exc}") from exc
    return {"ok": True, "backend": "tencent_cos", "message": "COS 连接成功"}


@router.post("/pair-code")
def admin_pair_code(user: CurrentAdmin, db: Session = Depends(get_db)):
    # Keep one canonical implementation and expose it in the admin console.
    from app.api.screenshot import create_pair_code
    return create_pair_code(user, db)


def _task_view(task: PracticalMigrationTask) -> dict:
    return {"id": task.id, "filename": task.filename, "status": task.status, "owner_id": task.owner_id, "report": task.report or {}, "error": task.error, "created_at": task.created_at.isoformat(), "started_at": task.started_at.isoformat() if task.started_at else None, "finished_at": task.finished_at.isoformat() if task.finished_at else None}


@router.post("/migration/bundles/inspect")
async def inspect_migration_bundle(user: CurrentAdmin, file: UploadFile = File(...)):
    settings = get_settings(); settings.ensure_directories()
    path = settings.data_root / "admin" / "inspect" / f"{uuid.uuid4().hex}.ptmigration.zip"; path.parent.mkdir(parents=True, exist_ok=True)
    try:
        size = 0
        with path.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_MIGRATION_BYTES:
                    raise HTTPException(413, "迁移包不能超过 1GB")
                target.write(chunk)
        manifest = inspect_bundle(path)
        return {"ok": True, "filename": file.filename or path.name, "manifest": manifest}
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(400, f"迁移包无效：{exc}") from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("/migration/bundles/import")
async def upload_migration_bundle(user: CurrentAdmin, file: UploadFile = File(...), db: Session = Depends(get_db)):
    settings = get_settings(); settings.ensure_directories()
    directory = settings.data_root / "admin" / "migration-inbox"; directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.ptmigration.zip"
    try:
        size = 0
        with path.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_MIGRATION_BYTES:
                    raise HTTPException(413, "迁移包不能超过 1GB")
                target.write(chunk)
        manifest = inspect_bundle(path)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"迁移包无效：{exc}") from exc
    task = PracticalMigrationTask(filename=Path(file.filename or path.name).name, bundle_path=str(path), status="uploaded", report={"manifest": manifest})
    db.add(task); db.commit(); db.refresh(task)
    return {"ok": True, "task": _task_view(task)}


@router.get("/migration/bundles")
@router.get("/migration/tasks")
def migration_tasks(user: CurrentAdmin, db: Session = Depends(get_db)):
    return {"tasks": [_task_view(task) for task in db.scalars(select(PracticalMigrationTask).order_by(PracticalMigrationTask.created_at.desc())).all()]}

@router.delete("/migration/tasks/{task_id}")
@router.delete("/migration/bundles/{task_id}")
def delete_migration_task(task_id: str, user: CurrentAdmin, db: Session = Depends(get_db)):
    task = db.get(PracticalMigrationTask, task_id)
    if not task:
        raise HTTPException(404, "迁移任务不存在")
    if task.status == "running":
        raise HTTPException(409, "迁移任务正在执行，不能删除")
    bundle = task.bundle_path or ""
    try:
        if bundle.startswith("cos://"):
            TencentCosStorage().delete(bundle[6:])
        elif bundle:
            Path(bundle).unlink(missing_ok=True)
    except Exception:
        # Removing a stale COS object must not prevent deleting the local task
        # record; the object may already have expired or been removed manually.
        pass
    db.delete(task); db.commit()
    return {"ok": True, "deleted": task_id}


@router.post("/migration/bundles/{task_id}/start")
@router.post("/migration/tasks/{task_id}/start")
def start_migration(task_id: str, user: CurrentAdmin, body: dict | None = None, db: Session = Depends(get_db)):
    task = db.get(PracticalMigrationTask, task_id)
    if not task or task.status in {"running", "completed"}: raise HTTPException(404, "迁移任务不存在或已完成")
    owner_id = (body or {}).get("owner_id")
    target_user = db.get(User, owner_id)
    if not owner_id or not target_user or not target_user.active: raise HTTPException(400, "请选择有效的目标账号")
    temp_context = None
    extracted = None
    try:
        from scripts.migrate_legacy_data import apply_plan, build_plan
        task.owner_id = owner_id; task.status = "running"; task.started_at = utcnow(); db.commit()
        temp_context = TencentCosStorage().temporary_download(task.bundle_path[6:]) if task.bundle_path.startswith("cos://") else None
        if temp_context:
            source = temp_context.__enter__()
        else: source = Path(task.bundle_path)
        extracted, manifest = extract_verified(source)
        plan = build_plan(extracted / "payload" / "legacy", extracted / "payload" / "screenshot", extracted / "payload" / "legacy" / "db.sqlite3")
        # Historical imports are shared center data, visible to every account.
        # Keep owner_id on the task for audit, but do not scope imported rows.
        report = apply_plan(plan, None)
        task.report = {"manifest": manifest, "result": report}; task.status = "completed"; task.finished_at = utcnow(); db.commit()
    except Exception as exc:
        db.rollback()
        try:
            failed = db.get(PracticalMigrationTask, task_id)
            if failed:
                failed.status = "failed"; failed.error = str(exc)[:4000]; failed.finished_at = utcnow(); db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(400, f"迁移失败：{exc}") from exc
    finally:
        if temp_context: temp_context.__exit__(None, None, None)
        if extracted:
            shutil.rmtree(extracted, ignore_errors=True)
    return {"ok": True, "task": _task_view(task)}


def _update_root(settings) -> Path:
    root = settings.data_root / "update-staging"; root.mkdir(parents=True, exist_ok=True); return root


def _rotate_completed_update_marker(root: Path) -> Path | None:
    """Move an old completion marker out of the fixed updater filename.

    Older green-package workers used ``Path.rename`` for
    ``pending.json -> pending.completed.json`` and failed on Windows when the
    destination already existed. Rotate that reserved marker before a new
    install so a one-time retry can pass through the old worker too; newer
    workers still use their own idempotent archive logic as a second guard.
    """
    marker = root / "pending.completed.json"
    if not marker.exists():
        return None
    target = root / f"pending.completed.previous-{uuid.uuid4().hex}.json"
    try:
        os.replace(marker, target)
        return target
    except OSError:
        return None


def _read_update_result(root: Path) -> dict | None:
    try:
        value = json.loads((root / "result.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _is_newer_version(value: str) -> bool:
    """Reject re-installing the running version or an older package."""
    from app.services.update_bundle import version_tuple
    try:
        return version_tuple(value) > version_tuple(__version__)
    except ValueError:
        return False


def _update_status(settings) -> dict:
    root = _update_root(settings); pending = root / "pending.json"
    value = {}
    try: value = json.loads(pending.read_text(encoding="utf-8"))
    except (OSError, ValueError): pass
    return {"current_version": __version__, "manifest_url_configured": bool(settings.update_manifest_url), "remote_enabled": settings.allow_remote_update, "pending": value or None, "last_result": _read_update_result(root), "staging_dir": str(root)}


@router.get("/updates/status")
def update_status(user: CurrentAdmin): return {"ok": True, **_update_status(get_settings())}


@router.post("/updates/check")
def check_update(user: CurrentAdmin):
    settings = get_settings()
    if not settings.update_manifest_url: return {"ok": True, "available": False, "reason": "未配置更新地址", "current_version": __version__}
    if not settings.allow_remote_update: return {"ok": True, "available": False, "reason": "远程更新默认关闭，请先在中心配置中显式开启", "current_version": __version__}
    try:
        with urllib.request.urlopen(_safe_update_url(settings.update_manifest_url), timeout=10) as response:
            value = json.loads(response.read(1024 * 1024).decode("utf-8"))
        if not isinstance(value, dict) or not value.get("version") or len(str(value.get("sha256", ""))) != 64:
            raise ValueError("更新清单缺少 version/sha256")
        value["available"] = _is_newer_version(str(value["version"])); value["current_version"] = __version__
        return {"ok": True, **value}
    except Exception as exc: raise HTTPException(400, f"检查更新失败：{exc}") from exc


def _backup_before_update(settings) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = _update_root(settings) / "previous" / stamp; backup.mkdir(parents=True, exist_ok=True)
    env_file = Path(__import__("os").environ.get("PRACTICAL_ENV_FILE", str(Path.cwd() / ".env"))).expanduser()
    if env_file.is_file(): shutil.copy2(env_file, backup / ".env")
    for name in ("storage", "analysis", "cache"):
        source = settings.data_root / name
        if source.is_dir(): shutil.copytree(source, backup / name, dirs_exist_ok=True)
    return str(backup)


@router.post("/updates/upload")
async def upload_update(user: CurrentAdmin, file: UploadFile = File(...), sha256: str | None = None):
    settings = get_settings(); settings.ensure_directories(); root = _update_root(settings)
    target = root / f"{uuid.uuid4().hex}-{Path(file.filename or 'update.zip').name}.staged.zip"
    digest = hashlib.sha256(); size = 0
    with target.open("wb") as stream:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk); digest.update(chunk); stream.write(chunk)
            if size > MAX_UPDATE_BYTES:
                target.unlink(missing_ok=True)
                raise HTTPException(413, "更新包不能超过 1GB")
    actual = digest.hexdigest()
    if sha256 and sha256.lower() != actual: target.unlink(missing_ok=True); raise HTTPException(400, "更新包 SHA-256 校验失败")
    try:
        manifest = inspect_update_bundle(target)
    except Exception as exc:
        target.unlink(missing_ok=True); raise HTTPException(400, f"更新包清单校验失败：{exc}") from exc
    if not _is_newer_version(str(manifest["version"])):
        target.unlink(missing_ok=True)
        raise HTTPException(400, "更新版本必须高于当前版本")
    pending_path = root / "pending.json"
    if pending_path.is_file():
        try:
            existing = json.loads(pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if existing.get("install_requested"):
            raise HTTPException(409, "已有更新正在安装，请等待当前任务结束")
    _rotate_completed_update_marker(root)
    backup = _backup_before_update(settings)
    pending = {"version": manifest["version"], "package": str(target), "sha256": actual, "size": size, "manifest": manifest, "backup": backup, "restart_required": True, "remote_execution": False, "instructions": "请点击绿色版更新器或运行 packaging/apply_center_update.cmd；中心不会执行未校验程序"}
    (root / "pending.json").write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "pending": pending}


@router.post("/updates/apply")
def apply_update(body: dict, user: CurrentAdmin):
    settings = get_settings()
    if body.get("package"):
        return {"ok": False, "reason": "请使用 /updates/upload 上传更新包；中心不会执行远程程序"}
    if not settings.allow_remote_update: raise HTTPException(403, "远程更新默认关闭")
    url = _safe_update_url(str(body.get("url") or "")); expected = str(body.get("sha256") or "").lower()
    if len(expected) != 64: raise HTTPException(400, "必须提供 64 位 SHA-256")
    root = _update_root(settings); target = root / "remote-update.staged"; digest = hashlib.sha256(); size = 0
    try:
        with urllib.request.urlopen(url, timeout=30) as response, target.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                size += len(chunk); digest.update(chunk); stream.write(chunk)
                if size > MAX_UPDATE_BYTES:
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, "更新包不能超过 1GB")
    except Exception as exc: target.unlink(missing_ok=True); raise HTTPException(400, f"下载更新失败：{exc}") from exc
    if digest.hexdigest() != expected: target.unlink(missing_ok=True); raise HTTPException(400, "更新包 SHA-256 校验失败")
    try:
        manifest = inspect_update_bundle(target)
    except Exception as exc:
        target.unlink(missing_ok=True); raise HTTPException(400, f"更新包清单校验失败：{exc}") from exc
    if body.get("version") and str(body["version"]) != str(manifest["version"]):
        target.unlink(missing_ok=True); raise HTTPException(400, "更新版本与清单不一致")
    if not _is_newer_version(str(manifest["version"])):
        target.unlink(missing_ok=True); raise HTTPException(400, "更新版本必须高于当前版本")
    _rotate_completed_update_marker(root)
    backup = _backup_before_update(settings); pending = {"version": manifest["version"], "package": str(target), "sha256": expected, "size": size, "manifest": manifest, "backup": backup, "restart_required": True, "remote_execution": False, "instructions": "请运行绿色版更新脚本并重新启动中心服务"}
    (root / "pending.json").write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "pending": pending}


@router.post("/updates/rollback")
def rollback_update(user: CurrentAdmin):
    settings = get_settings(); root = _update_root(settings); marker = root / "rollback.json"; marker.write_text(json.dumps({"requested_at": utcnow().isoformat(), "remote_execution": False}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "restart_required": True, "instructions": "请运行绿色版回滚脚本，再重新启动中心服务"}


@router.post("/updates/install")
def request_update_install(user: CurrentAdmin):
    """Arm the fixed green-package updater without executing package code."""
    settings = get_settings(); root = _update_root(settings); pending_file = root / "pending.json"
    try:
        pending = json.loads(pending_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(404, "没有待安装更新") from exc
    package = Path(str(pending.get("package", "")))
    if not package.is_file():
        raise HTTPException(404, "待更新包不存在")
    (root / "result.json").unlink(missing_ok=True)
    _rotate_completed_update_marker(root)
    pending["install_requested"] = True; pending["requested_at"] = utcnow().isoformat(); pending["remote_execution"] = False
    pending_file.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    # In the Windows green package, run a fixed trusted worker copied outside
    # the update payload. The archive can never choose the executable.
    app_root = Path(__file__).resolve().parents[2]
    bundled_runtime = app_root / "runtime" / "python.exe"
    runtime = _windowless_runtime(
        Path(os.environ.get("PRACTICAL_RUNTIME_PYTHON", str(bundled_runtime if bundled_runtime.is_file() else Path(sys.executable))))
    )
    worker_source = Path(__file__).resolve().parents[2] / "packaging" / "update_worker.py"
    supervisor = settings.data_root / "update-supervisor"; supervisor.mkdir(parents=True, exist_ok=True)
    worker = supervisor / "update_worker.py"; shutil.copy2(worker_source, worker)
    parent_pid = os.environ.get("PRACTICAL_PORTABLE_PID")
    launcher = Path(os.environ.get("PRACTICAL_PORTABLE_LAUNCHER", str(app_root / "portable" / "portable_center.py")))
    worker_env = os.environ.copy(); worker_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = _hidden_startupinfo()
    log_path = settings.data_root / "logs" / "update-worker.log"; log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen([str(runtime), str(worker), str(pending_file), "--app-root", str(app_root), "--wait-pid", str(os.getpid()), "--wait-parent-pid", parent_pid or "0", "--launcher", str(launcher), "--runtime", str(runtime), "--health-url", f"http://127.0.0.1:{settings.port}/api/health", "--result-file", str(root / "result.json")], cwd=str(worker_source.parent), env=worker_env, close_fds=True, creationflags=flags, startupinfo=startupinfo, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    def stop_current_center():
        # In the green Windows launcher this is the child Uvicorn process.
        # os._exit avoids Windows' signal emulation leaving the child alive
        # while the worker is already trying to replace locked files.
        if os.name == "nt" and (parent_pid or os.environ.get("PRACTICAL_RUNTIME_PYTHON")):
            os._exit(0)
        os.kill(os.getpid(), signal.SIGTERM)
    if parent_pid or os.environ.get("PRACTICAL_RUNTIME_PYTHON"):
        threading.Timer(1.0, stop_current_center).start()
    return {"ok": True, "restart_required": True, "pending": pending, "instructions": "绿色版更新器会读取此标记，校验通过后替换 payload；.env 和 data 保持不变"}

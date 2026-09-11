from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth import CurrentUser, optional_current_user
from app.config import get_settings
from app.db import get_db
from app.models import CaptureAgent, CaptureJob, CapturePairCode, CaptureSchedule, CaptureShop, CaptureUpload, PracticalScreenshot, utcnow
from app.schemas import AgentHeartbeat, AgentPairRequest, JobRequest, ShopRequest
from app.services.storage import TencentCosStorage, enforce_upload_policy, find_compatible_storage_file, inspect_image, mobile_preview, resolve_storage_key, safe_name, sha256_file, stage_upload, storage_backend, store_file, thumbnail
from app.security import hash_session_token, issue_session_token

router = APIRouter(prefix="/screenshot", tags=["screenshot"])
agent_router = APIRouter(prefix="/agent", tags=["agent"])

# This is deliberately separate from the display/build version.  A center
# release can therefore accept an older Agent that still speaks the same
# protocol, while a future protocol break can request a signed package.
AGENT_PROTOCOL_VERSION = 1


def _agent(db: Session, token: str | None) -> CaptureAgent:
    if not token: raise HTTPException(401, "采集端未配对")
    token = token.removeprefix("Bearer ")
    agent = db.scalar(select(CaptureAgent).where(CaptureAgent.token_hash == hash_session_token(token), CaptureAgent.status != "revoked"))
    if not agent: raise HTTPException(401, "采集端令牌无效")
    agent.last_seen_at = utcnow(); db.commit(); return agent

def _owned(model, user):
    return (model.owner_id == user.id) | (model.owner_id.is_(None))


def _scheduled_capture_snapshot(db: Session, user: CurrentUser) -> dict | None:
    """Return the active local scheduled capture reported by the Agent."""
    agent = db.scalar(select(CaptureAgent).where(
        CaptureAgent.owner_id == user.id,
        CaptureAgent.status == "busy",
    ).order_by(CaptureAgent.last_seen_at.desc()))
    if not agent or not isinstance(agent.meta, dict) or not agent.meta.get("capture_running"):
        return None
    progress = agent.meta.get("capture_progress")
    return {
        "agent": agent,
        "progress": progress if isinstance(progress, dict) else {},
        "source": agent.meta.get("capture_source") or "scheduled",
    }


def _agent_update_descriptor(settings, platform_name: str) -> dict | None:
    """Return only a pointer to a platform-specific, externally hosted manifest.

    The center never downloads or executes Agent packages.  The Agent fetches
    the manifest itself and applies the package only after validating its
    HTTPS URL, platform, version and SHA-256.
    """
    value = str(settings.agent_update_manifest_url or "").strip()
    if not value:
        return None
    if "{platform}" in value:
        value = value.replace("{platform}", platform_name.casefold())
    return {"manifest_url": value, "platform": platform_name}


@router.get("/list")
def list_images(user: CurrentUser, db: Session = Depends(get_db)):
    # Screenshots are readable across accounts.  Deletion remains scoped to
    # the owner and is exposed as a capability so the UI can render shared
    # images as read-only without relying on client-side enforcement.
    images = db.scalars(select(PracticalScreenshot).order_by(PracticalScreenshot.brand, PracticalScreenshot.captured_at.desc())).all(); grouped = {}
    for image in images:
        date = image.captured_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        grouped.setdefault(image.brand, {}).setdefault(date, []).append({"id": image.id, "brand": image.brand, "date": date, "file": image.original_name, "path": image.storage_key, "size": image.file_size, "width": image.width, "height": image.height, "capture_id": image.capture_id, "captured_at": image.captured_at.isoformat(), "can_delete": user.role == "admin" or image.owner_id == user.id})
    brands = [{"brand": brand, "dates": [{"date": date, "images": imgs} for date, imgs in sorted(dates.items(), reverse=True)], "count": sum(map(len, dates.values()))} for brand, dates in sorted(grouped.items())]
    return {"brands": brands, "total": len(images), "revision": _screenshot_revision(db)}


def _screenshot_revision(db: Session) -> str:
    """Cheap change marker used to avoid rebuilding the full list repeatedly."""
    count, latest = db.execute(select(func.count(PracticalScreenshot.id), func.max(PracticalScreenshot.updated_at))).one()
    return f"{int(count or 0)}:{latest.isoformat() if latest else ''}"


@router.get("/revision")
def screenshot_revision(user: CurrentUser, db: Session = Depends(get_db)):
    return {"revision": _screenshot_revision(db)}


def _image(db, path: str):
    image = db.scalar(select(PracticalScreenshot).where((PracticalScreenshot.id == path) | (PracticalScreenshot.storage_key == path) | (PracticalScreenshot.capture_id == path)))
    if not image: raise HTTPException(404, "文件不存在")
    return image


def _mirror_cos_object(storage_key: str) -> Path | None:
    """Materialize a COS object in the center's local storage.

    COS remains the upload ingress for large files, but normal reads should
    use the center host.  The temporary file and atomic replace prevent a
    partially downloaded image from being served after an interrupted copy.
    """
    settings = get_settings()
    target = resolve_storage_key(settings, storage_key)
    if target.is_file():
        return target
    temporary = target.with_name(f".{target.name}.download-{secrets.token_hex(8)}")
    try:
        cos = TencentCosStorage()
        target.parent.mkdir(parents=True, exist_ok=True)
        with cos.temporary_download(storage_key) as source, temporary.open("wb") as destination:
            with source.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, destination, length=1024 * 1024)
        os.replace(temporary, target)
        return target
    except Exception:
        temporary.unlink(missing_ok=True)
        return None


def _local_image_path(image: PracticalScreenshot) -> Path | None:
    """Resolve an image, including objects copied by older installers."""
    settings = get_settings()
    try:
        local = resolve_storage_key(settings, image.storage_key)
        if local.is_file():
            return local
    except ValueError:
        pass
    return find_compatible_storage_file(
        settings,
        image.storage_key,
        expected_size=image.file_size,
        expected_sha256=image.sha256,
    )


@router.get("/file")
def get_file(user: CurrentUser, path: str, db: Session = Depends(get_db)):
    image = _image(db, path)
    settings = get_settings()
    try:
        local = _local_image_path(image)
        if local is not None or (storage_backend(settings) == "tencent_cos" and _mirror_cos_object(image.storage_key)):
            local = local or resolve_storage_key(settings, image.storage_key)
            # Screenshot storage keys are immutable (a new capture gets a new
            # key). Let the browser reuse an already-open original when the
            # lightbox falls back from the session Cache API.
            return FileResponse(local, filename=image.original_name, headers={
                "Cache-Control": "private, max-age=86400, immutable",
                "ETag": f'"{image.id}-{image.file_size}-{int(image.captured_at.timestamp())}"',
            })
    except ValueError:
        pass
    if storage_backend(settings) == "tencent_cos":
        try: return RedirectResponse(TencentCosStorage().presign_get(image.storage_key))
        except RuntimeError: pass
    raise HTTPException(404, "文件不存在")


@router.get("/thumb")
def get_thumb(user: CurrentUser, path: str, size: int = 320, db: Session = Depends(get_db)):
    image = _image(db, path)
    settings = get_settings()
    try:
        local = _local_image_path(image)
        if local is None and storage_backend(settings) == "tencent_cos":
            local = _mirror_cos_object(image.storage_key)
        if not local or not local.is_file():
            raise OSError("中心主机没有本地原图副本")
        # The grid never needs the original's dimensions. Keep the generated
        # file small, and include the size in the disk-cache key so this can be
        # tuned independently from the lightbox/original image.
        edge = min(max(int(size or 320), 160), 640)
        cached, media_type = thumbnail(settings, local, image.id, edge=edge)
        return FileResponse(cached, media_type=media_type, headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "ETag": f'"{image.id}-{edge}"',
        })
    except (ValueError, OSError):
        if storage_backend(settings) == "tencent_cos":
            raise HTTPException(503, "原图尚未同步到中心主机，暂时无法生成缩略图")
        raise HTTPException(404, "文件不存在")


@router.get("/preview")
def get_mobile_preview(user: CurrentUser, path: str, size: int = 1440, db: Session = Depends(get_db)):
    """Serve a cached high-resolution derivative for native mobile viewing.

    The original remains available at ``/file`` for desktop/lightbox use. A
    bounded lossless WebP is much faster and safer for phone memory than a
    raw original while preserving the small Chinese text in tall screenshots.
    """
    image = _image(db, path)
    settings = get_settings()
    try:
        local = _local_image_path(image)
        if local is None and storage_backend(settings) == "tencent_cos":
            local = _mirror_cos_object(image.storage_key)
        if not local or not local.is_file():
            raise OSError("中心主机没有本地原图副本")
        edge = min(max(int(size or 1440), 640), 4096)
        cached, media_type = mobile_preview(settings, local, image.id, edge=edge)
        return FileResponse(cached, media_type=media_type, headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "ETag": f'"{image.id}-preview-{edge}"',
        })
    except (ValueError, OSError):
        if storage_backend(settings) == "tencent_cos":
            raise HTTPException(503, "原图尚未同步到中心主机，暂时无法生成手机预览")
        raise HTTPException(404, "文件不存在")


@router.post("/delete")
def delete_images(user: CurrentUser, body: dict, db: Session = Depends(get_db)):
    deleted = 0; skipped = 0
    for path in (body or {}).get("paths", []):
        image = db.scalar(select(PracticalScreenshot).where((PracticalScreenshot.id == path) | (PracticalScreenshot.storage_key == path) | (PracticalScreenshot.capture_id == path)))
        if image and (user.role == "admin" or image.owner_id == user.id):
            try:
                if storage_backend(get_settings()) == "tencent_cos": TencentCosStorage().delete(image.storage_key)
                else: resolve_storage_key(get_settings(), image.storage_key).unlink(missing_ok=True)
            except (ValueError, OSError, RuntimeError): pass
            if storage_backend(get_settings()) == "tencent_cos":
                try: resolve_storage_key(get_settings(), image.storage_key).unlink(missing_ok=True)
                except (ValueError, OSError): pass
            db.delete(image); deleted += 1
        elif image:
            # Shared/read-only images must not be deleted by another account.
            skipped += 1
    db.commit()
    result = {"deleted": deleted}
    if skipped:
        result["skipped"] = skipped
    return result


@router.get("/shops")
def list_shops(user: CurrentUser, db: Session = Depends(get_db)): return {"shops": [{"name": s.name, "url": s.url, "enabled": s.enabled} for s in db.scalars(select(CaptureShop).where(_owned(CaptureShop, user)).order_by(CaptureShop.sort_order, CaptureShop.name)).all()]}


@router.post("/shops")
def add_shop(user: CurrentUser, body: ShopRequest, db: Session = Depends(get_db)):
    if db.scalar(select(CaptureShop).where(_owned(CaptureShop, user), CaptureShop.name == body.name.strip())): return {"error": "店铺已存在"}
    db.add(CaptureShop(name=body.name.strip(), url=body.url.strip(), owner_id=user.id if user else None)); db.commit(); return {"ok": True}


@router.delete("/shops/{name}")
def remove_shop(user: CurrentUser, name: str, db: Session = Depends(get_db)):
    row = db.scalar(select(CaptureShop).where(_owned(CaptureShop, user), CaptureShop.name == name));
    if row: db.delete(row); db.commit()
    return {"ok": True}


def _new_job(db, kind, payload, user):
    job = CaptureJob(kind=kind, payload=payload or {}, owner_id=user.id if user else None); db.add(job); db.commit(); db.refresh(job); return job


def _queue_scheduler_sync(db: Session, user: CurrentUser, schedule: dict | None, enabled: bool) -> None:
    """Push a schedule change to the user's active Agent immediately.

    The Agent still treats ``/api/agent/config`` as the durable source of
    truth.  This auxiliary job removes the old 30-second polling dependency
    after a user clicks save and also makes a failed local launchd install
    visible through the normal Agent job result.
    """
    agent = db.scalar(select(CaptureAgent).where(
        CaptureAgent.owner_id == user.id,
        CaptureAgent.status != "revoked",
    ).order_by(CaptureAgent.last_seen_at.desc()))
    if not agent:
        return
    queued = db.scalars(select(CaptureJob).where(
        CaptureJob.owner_id == user.id,
        CaptureJob.agent_id == agent.device_id,
        CaptureJob.kind.in_(("scheduler_install", "scheduler_uninstall")),
        CaptureJob.status == "queued",
    )).all()
    for old in queued:
        old.status = "cancelled"
        old.finished_at = utcnow()
    command = "scheduler_install" if enabled else "scheduler_uninstall"
    payload = {"command": command}
    if enabled and schedule:
        payload["schedule"] = schedule
    db.add(CaptureJob(kind=command, payload=payload, owner_id=user.id, agent_id=agent.device_id))


@router.post("/capture")
def start_capture(user: CurrentUser, body: dict | None = None, db: Session = Depends(get_db)):
    running = db.scalar(select(CaptureJob).where(_owned(CaptureJob, user), CaptureJob.kind == "capture", CaptureJob.status.in_(["queued", "running", "paused"])))
    if running:
        raise HTTPException(409, "已有截图任务在运行")
    scheduled = _scheduled_capture_snapshot(db, user)
    if scheduled:
        progress = scheduled["progress"]
        detail = f"定时截图正在执行（{progress.get('current', 0)}/{progress.get('total', 0)}，当前：{progress.get('shop') or '准备中'}）"
        raise HTTPException(409, detail)
    job = _new_job(db, "capture", body or {}, user); return {"ok": True, "job_id": job.id}


@router.get("/capture/status")
def capture_status(user: CurrentUser, db: Session = Depends(get_db)):
    job = db.scalar(select(CaptureJob).where(_owned(CaptureJob, user), CaptureJob.kind == "capture", CaptureJob.status.in_(["queued", "running", "paused"])).order_by(CaptureJob.created_at.desc()))
    if job:
        return {"running": True, "paused": job.status == "paused", "job_id": job.id, "status": job.status, "source": "manual"}
    scheduled = _scheduled_capture_snapshot(db, user)
    if scheduled:
        return {"running": True, "paused": False, "job_id": None, "status": "running", "source": scheduled["source"]}
    return {"running": False, "paused": False, "job_id": None, "status": "idle", "source": None}


def _job_action(action, db, user):
    job = db.scalar(select(CaptureJob).where(_owned(CaptureJob, user), CaptureJob.kind == "capture", CaptureJob.status.in_(["queued", "running", "paused"])).order_by(CaptureJob.created_at.desc()))
    if not job: return {"ok": True, "paused": False}
    if action in {"pause", "resume"}:
        job.status = "paused" if action == "pause" else ("queued" if job.status == "paused" else job.status)
        if job.agent_id:
            db.add(CaptureJob(kind=action, payload={"target_job_id": job.id}, owner_id=user.id, agent_id=job.agent_id))
    db.commit(); return {"ok": True, "paused": job.status == "paused"}


@router.post("/capture/pause")
def pause_capture(user: CurrentUser, db: Session = Depends(get_db)): return _job_action("pause", db, user)


@router.post("/capture/resume")
def resume_capture(user: CurrentUser, db: Session = Depends(get_db)): return _job_action("resume", db, user)


@router.post("/capture/stop")
def stop_capture(user: CurrentUser, db: Session = Depends(get_db)):
    job = db.scalar(select(CaptureJob).where(_owned(CaptureJob, user), CaptureJob.kind == "capture", CaptureJob.status.in_(["queued", "running", "paused"])).order_by(CaptureJob.created_at.desc()))
    if job: job.status = "cancelled"; job.finished_at = utcnow(); db.commit()
    return {"ok": True, "running": False}


@router.get("/progress")
def progress(user: CurrentUser, db: Session = Depends(get_db)):
    active_job = db.scalar(select(CaptureJob).where(
        _owned(CaptureJob, user), CaptureJob.kind == "capture",
        CaptureJob.status.in_(["queued", "running", "paused"]),
    ).order_by(CaptureJob.created_at.desc()))
    scheduled = _scheduled_capture_snapshot(db, user)
    if active_job:
        value = active_job.progress
    elif scheduled:
        value = scheduled["progress"]
    else:
        job = db.scalar(select(CaptureJob).where(_owned(CaptureJob, user)).order_by(CaptureJob.created_at.desc()))
        value = job.progress if job else {}
    # Defensive normalization keeps old jobs created before the storage fix
    # readable while new jobs are stored in the flat shape below.
    if isinstance(value, dict) and isinstance(value.get("progress"), dict):
        value = value["progress"]
    return value if isinstance(value, dict) else {"current": 0, "total": 0, "shop": ""}


def _latest_schedule(db: Session, owner_id: str | None):
    if not owner_id:
        return None
    return db.scalar(select(CaptureSchedule).where(CaptureSchedule.owner_id == owner_id).order_by(CaptureSchedule.updated_at.desc(), CaptureSchedule.created_at.desc()))


@router.get("/scheduler/status")
def scheduler_status(user: CurrentUser, db: Session = Depends(get_db)):
    row = _latest_schedule(db, user.id)
    schedule = {"weekdays": row.weekdays if row else [], "hour": row.hour if row else 9, "minute": row.minute if row else 0}
    enabled = bool(row and row.enabled)
    return {"installed": enabled, "enabled": enabled, "schedule": schedule,
            "weekdays": schedule["weekdays"], "hour": schedule["hour"], "minute": schedule["minute"],
            "running": False, "last_code": ""}


@router.post("/scheduler/install")
def scheduler_install(user: CurrentUser, db: Session = Depends(get_db)):
    row = _latest_schedule(db, user.id) or CaptureSchedule(owner_id=user.id)
    row.enabled = True; db.add(row); _queue_scheduler_sync(db, user, {"weekdays": row.weekdays, "hour": row.hour, "minute": row.minute}, True); db.commit(); return {"ok": True, "installed": True}


@router.post("/scheduler/config")
def scheduler_config(user: CurrentUser, body: dict, db: Session = Depends(get_db)):
    """Save the schedule and its enabled state in one database transaction.

    The legacy ``/schedule`` and ``/install`` endpoints remain available for
    older clients, but the current page uses this endpoint so a network error
    cannot leave a newly edited schedule disabled halfway through setup.
    """
    raw_weekdays = body.get("weekdays", [])
    try:
        if not isinstance(raw_weekdays, (list, tuple)):
            raise ValueError("星期配置格式不对")
        weekdays = sorted(set(int(day) for day in raw_weekdays))
        hour = int(body.get("hour", 9))
        minute = int(body.get("minute", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc) or "参数格式不对") from exc
    if not weekdays or any(day not in range(1, 8) for day in weekdays):
        raise HTTPException(400, "请至少选择一个星期")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise HTTPException(400, "时间格式不对")

    row = _latest_schedule(db, user.id)
    if row is None:
        row = CaptureSchedule(owner_id=user.id)
    row.weekdays = weekdays
    row.hour = hour
    row.minute = minute
    row.enabled = bool(body.get("enabled", True))
    db.add(row)
    _queue_scheduler_sync(db, user, {"weekdays": weekdays, "hour": hour, "minute": minute}, row.enabled)
    db.commit()
    return {"ok": True, "installed": row.enabled, "enabled": row.enabled,
            "schedule": {"weekdays": weekdays, "hour": hour, "minute": minute}}


@router.post("/scheduler/uninstall")
def scheduler_uninstall(user: CurrentUser, db: Session = Depends(get_db)):
    for row in db.scalars(select(CaptureSchedule).where(_owned(CaptureSchedule, user))): row.enabled = False
    _queue_scheduler_sync(db, user, None, False)
    db.commit(); return {"ok": True, "installed": False}


@router.post("/scheduler/schedule")
def scheduler_schedule(user: CurrentUser, body: dict, db: Session = Depends(get_db)):
    row = _latest_schedule(db, user.id) or CaptureSchedule(owner_id=user.id)
    try: row.weekdays = [int(x) for x in body.get("weekdays", [])]; row.hour = int(body.get("hour", 9)); row.minute = int(body.get("minute", 0))
    except (TypeError, ValueError): raise HTTPException(400, "参数格式不对")
    row.hour = min(max(row.hour, 0), 23); row.minute = min(max(row.minute, 0), 59); db.add(row); db.commit(); return {"ok": True, "weekdays": row.weekdays, "hour": row.hour, "minute": row.minute}


@router.post("/login")
def start_login(user: CurrentUser, db: Session = Depends(get_db)):
    job = _new_job(db, "login", {}, user)
    return {"ok": True, "job_id": job.id, "hint": "已向采集端下发登录指令"}
@router.post("/login/done")
def done_login(user: CurrentUser, db: Session = Depends(get_db)):
    job = db.scalar(select(CaptureJob).where(_owned(CaptureJob, user), CaptureJob.kind == "login", CaptureJob.status.in_(["queued", "running"])).order_by(CaptureJob.created_at.desc()))
    if job: job.status = "completed"; job.finished_at = utcnow(); db.commit()
    return {"ok": True, "job_id": job.id if job else None}
@router.get("/login/status")
def login_status(user: CurrentUser, db: Session = Depends(get_db)):
    job = db.scalar(select(CaptureJob).where(_owned(CaptureJob, user), CaptureJob.kind == "login").order_by(CaptureJob.created_at.desc()))
    result = {"running": bool(job and job.status in {"queued", "running"}), "logged_in": bool(job and job.status == "completed"), "job_id": job.id if job else None}
    if job and job.status == "failed" and job.error:
        result["error"] = job.error
    return result
@router.post("/open-folder")
def open_folder(user: CurrentUser, db: Session = Depends(get_db)):
    job = _new_job(db, "open_folder", {}, user)
    return {"ok": True, "job_id": job.id, "hint": "已向采集端下发打开目录指令"}


@agent_router.post("/pair-code")
def create_pair_code(user: CurrentUser, db: Session = Depends(get_db)):
    """Issue a short-lived, single-use code for a local Agent."""
    raw_code = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(minutes=10)
    db.add(CapturePairCode(code_hash=hash_session_token(raw_code), created_by=user.id, expires_at=expires_at))
    db.commit()
    return {"ok": True, "code": raw_code, "expires_at": expires_at.isoformat()}


@agent_router.post("/pair")
async def pair(request: Request, user=Depends(optional_current_user), db: Session = Depends(get_db)):
    # Do the access check before Pydantic body validation.  Pairing is the one
    # intentionally anonymous endpoint, so an empty/malformed request from an
    # unauthenticated caller must be 401 (not an information-leaking 422).
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if user is None:
        supplied_code = payload.get("code") if isinstance(payload, dict) else None
        pair_code = db.scalar(select(CapturePairCode).where(CapturePairCode.code_hash == hash_session_token(str(supplied_code or "")), CapturePairCode.consumed_at.is_(None), CapturePairCode.expires_at > utcnow()))
        if not supplied_code or not pair_code:
            raise HTTPException(401, "需要登录或有效的一次性配对码")
    if not isinstance(payload, dict):
        raise HTTPException(422, "配对请求体必须是 JSON 对象")
    try:
        body = AgentPairRequest.model_validate(payload)
    except Exception as exc:  # Pydantic ValidationError; keep an HTTP 422 contract.
        detail = getattr(exc, "errors", lambda: "配对参数不合法")()
        raise HTTPException(422, detail=detail) from exc
    if user is None:
        # Conditional update makes code consumption single-use even when two
        # Agents race to exchange it.
        consumed = db.execute(update(CapturePairCode).where(CapturePairCode.id == pair_code.id, CapturePairCode.consumed_at.is_(None), CapturePairCode.expires_at > utcnow()).values(consumed_at=utcnow()).execution_options(synchronize_session=False))
        if consumed.rowcount != 1:
            db.rollback()
            raise HTTPException(401, "一次性配对码已失效")
    token = issue_session_token(); existing = db.get(CaptureAgent, body.device_id)
    if existing and user and existing.owner_id not in (None, user.id): raise HTTPException(403, "设备已绑定其他账号")
    if user is not None:
        owner_id = user.id
    else:
        # Anonymous exchange must inherit the account that issued the code;
        # otherwise the Agent would remain ownerless and could not see that
        # account's shops/jobs or persist uploads into its namespace.
        owner_id = pair_code.created_by
        if existing and existing.owner_id not in (None, owner_id):
            raise HTTPException(403, "设备已绑定其他账号")
    if existing: existing.token_hash = hash_session_token(token); existing.name = body.name; existing.platform = body.platform; existing.version = body.version; existing.status = "paired"; existing.owner_id = owner_id; existing.meta = {"protocol_version": body.protocol_version, "capabilities": body.capabilities}
    else: db.add(CaptureAgent(device_id=body.device_id, token_hash=hash_session_token(token), name=body.name, platform=body.platform, version=body.version, owner_id=owner_id,
                              meta={"protocol_version": body.protocol_version, "capabilities": body.capabilities}))
    db.commit(); return {"ok": True, "device_id": body.device_id, "agent_token": token}


@agent_router.post("/heartbeat")
def heartbeat(body: AgentHeartbeat, x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)): 
    agent = _agent(db, x_agent_token)
    agent.status = body.status
    agent.version = body.version
    agent.platform = body.platform or agent.platform
    agent.meta = {**(body.meta or {}), "protocol_version": body.protocol_version,
                  "capabilities": body.capabilities}
    db.commit()
    settings = get_settings()
    compatible = settings.agent_min_protocol_version <= body.protocol_version <= AGENT_PROTOCOL_VERSION
    return {"ok": True, "device_id": agent.device_id, "compatible": compatible,
            "compatibility": "compatible" if compatible else "incompatible",
            "protocol_version": AGENT_PROTOCOL_VERSION,
            "min_protocol_version": settings.agent_min_protocol_version,
            "agent_update_required": not compatible,
            "agent_update": None if compatible else _agent_update_descriptor(settings, body.platform)}


@agent_router.get("/config")
def agent_config(x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    agent = _agent(db, x_agent_token)
    # The page deliberately exposes ownerless legacy/shared shops to every
    # account. The Agent must use the same visibility rule, otherwise the
    # page can show shops while /agent/config returns an empty list and a
    # "capture all" job has nothing to execute.
    owner_filter = ((CaptureShop.owner_id == agent.owner_id) | CaptureShop.owner_id.is_(None)) if agent.owner_id else CaptureShop.owner_id.is_(None)
    shops = db.scalars(select(CaptureShop).where(CaptureShop.enabled == True, owner_filter).order_by(CaptureShop.sort_order)).all()
    row = _latest_schedule(db, agent.owner_id)
    schedule = row if row and row.enabled else None
    return {"agent_id": agent.device_id, "owner_id": agent.owner_id,
            "shops": [{"name": s.name, "url": s.url, **(s.config or {})} for s in shops],
            "schedule": {"weekdays": schedule.weekdays, "hour": schedule.hour, "minute": schedule.minute} if schedule else None}


@agent_router.get("/jobs/next")
def next_job(x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    agent = _agent(db, x_agent_token)
    owner_filter = ((CaptureJob.owner_id == agent.owner_id) | CaptureJob.owner_id.is_(None)) if agent.owner_id else CaptureJob.owner_id.is_(None)
    job = db.scalar(select(CaptureJob).where(CaptureJob.status == "queued", owner_filter, (CaptureJob.agent_id == None) | (CaptureJob.agent_id == agent.device_id)).order_by(CaptureJob.created_at))
    if not job: return {"job": None}
    job.agent_id = agent.device_id; job.status = "running"; job.started_at = utcnow(); db.commit(); return {"job": {"id": job.id, "kind": job.kind, "payload": job.payload}}


@agent_router.post("/jobs/{job_id}/progress")
def agent_progress(job_id: str, body: dict, x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    agent = _agent(db, x_agent_token); job = db.get(CaptureJob, job_id)
    if not job or job.agent_id != agent.device_id: raise HTTPException(404, "任务不存在")
    # Agent progress reports also carry a transport-level status. Store only
    # the progress object because the web page reads current/total/shop from
    # this column; keeping the wrapper made every live update look like 0/0.
    progress = body.get("progress") if isinstance(body, dict) and isinstance(body.get("progress"), dict) else body
    job.progress = progress or {}; db.commit(); return {"ok": True}


@agent_router.post("/jobs/{job_id}/complete")
def agent_complete(job_id: str, body: dict | None = None, x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    agent = _agent(db, x_agent_token); job = db.get(CaptureJob, job_id)
    if not job or job.agent_id != agent.device_id: raise HTTPException(404, "任务不存在")
    # Completion payloads normally contain {status, result}; do not replace
    # the final visible progress with that wrapper. Accept nested or flat
    # progress for compatibility with older Agents.
    payload = body or {}
    progress = payload.get("progress") if isinstance(payload, dict) and isinstance(payload.get("progress"), dict) else payload
    if isinstance(progress, dict) and any(key in progress for key in ("current", "total", "shop")):
        job.progress = progress
    job.status = "completed"; job.finished_at = utcnow(); db.commit(); return {"ok": True}


@agent_router.post("/jobs/{job_id}/fail")
def agent_fail(job_id: str, body: dict | None = None, x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    agent = _agent(db, x_agent_token); job = db.get(CaptureJob, job_id)
    if not job or job.agent_id != agent.device_id: raise HTTPException(404, "任务不存在")
    job.status = "failed"; job.error = str((body or {}).get("error", "采集失败")); job.finished_at = utcnow(); db.commit(); return {"ok": True}


@agent_router.post("/screenshots")
async def upload_screenshot(capture_id: str, brand: str, captured_at: str, file: UploadFile = File(...), job_id: str|None = None, x_agent_token: Annotated[str | None, Header()] = None, background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    agent = _agent(db, x_agent_token); duplicate = db.scalar(select(PracticalScreenshot).where(PracticalScreenshot.capture_id == capture_id))
    if duplicate:
        if duplicate.owner_id != agent.owner_id or (duplicate.agent_id and duplicate.agent_id != agent.device_id):
            raise HTTPException(403, "截图属于其他账号或设备")
        return {"ok": True, "duplicate": True, "id": duplicate.id}
    staged, size, digest = stage_upload(file.file, get_settings())
    try:
        try: enforce_upload_policy(get_settings(), size)
        except RuntimeError as exc: raise HTTPException(413, str(exc)) from exc
        filename = safe_name(file.filename or "capture.png")
        width, height = inspect_image(staged); key = f"screenshots/{brand.strip()}/{captured_at[:10]}/{capture_id}-{filename}"; stored = store_file(get_settings(), key, staged, sha256=digest, file_size=size, overwrite=False)
        timestamp = datetime.fromisoformat(captured_at.replace("Z", "+00:00")); timestamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        image = PracticalScreenshot(capture_id=capture_id, brand=brand.strip(), captured_at=timestamp, original_name=filename, storage_key=stored.storage_key, sha256=digest, file_size=size, width=width, height=height, agent_id=agent.device_id, job_id=job_id, owner_id=agent.owner_id); db.add(image); db.commit(); db.refresh(image)
        if background_tasks is not None and stored.path is not None:
            background_tasks.add_task(thumbnail, get_settings(), stored.path, image.id, 320)
        return {"ok": True, "id": image.id, "storage_key": key, "sha256": digest}
    finally: staged.unlink(missing_ok=True)


@agent_router.post("/screenshots/initiate")
def initiate_upload(capture_id: str, filename: str, size: int, sha256: str | None = None, x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)):
    if size <= 0 or size > get_settings().max_upload_bytes: raise HTTPException(400, "文件大小不合法")
    agent = _agent(db, x_agent_token); existing = db.get(CaptureUpload, capture_id)
    if existing and (existing.owner_id != agent.owner_id or (existing.agent_id and existing.agent_id != agent.device_id)):
        raise HTTPException(403, "上传批次属于其他账号或设备")
    if existing and existing.status == "completed": return {"ok": True, "capture_id": capture_id, "status": "completed", "upload_url": None}
    filename = __import__('pathlib').Path(filename or f"{capture_id}.png").name
    key = f"screenshots/pending/{agent.device_id}/{capture_id}/{filename}"; upload_url = None
    # Production may still run local storage on the constrained center host;
    # opt into COS explicitly so an absent SDK/credential never breaks the
    # local fallback.
    upload_headers = {}
    if storage_backend(get_settings()) == "tencent_cos":
        upload_headers = {"x-cos-meta-sha256": str(sha256 or "").lower()}
        cos = TencentCosStorage()
        try:
            upload_url = cos.presign_put(key, metadata=upload_headers)
        except TypeError:  # backwards-compatible test doubles/older SDK adapter
            upload_url = cos.presign_put(key)
    row = existing or CaptureUpload(capture_id=capture_id)
    row.owner_id = agent.owner_id; row.agent_id = agent.device_id; row.status = "initiated"; row.storage_key = key; row.upload_url = upload_url; row.sha256 = sha256
    db.add(row); db.commit(); return {"ok": True, "capture_id": capture_id, "storage_key": key, "upload_url": upload_url, "upload_headers": upload_headers, "backend": "tencent_cos" if upload_url else "local"}


@agent_router.post("/screenshots/complete")
def complete_upload(capture_id: str, body: dict | None = None, x_agent_token: Annotated[str | None, Header()] = None, background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    agent = _agent(db, x_agent_token); body = body or {}; row = db.get(CaptureUpload, capture_id)
    if not row:
        existing_image = db.scalar(select(PracticalScreenshot).where(PracticalScreenshot.capture_id == capture_id))
        if existing_image:
            if existing_image.owner_id != agent.owner_id or (existing_image.agent_id and existing_image.agent_id != agent.device_id):
                raise HTTPException(403, "截图属于其他账号或设备")
            return {"ok": True, "duplicate": True, "capture_id": capture_id, "id": existing_image.id, "status": "completed", "storage_key": existing_image.storage_key}
        raise HTTPException(404, "上传批次不存在")
    if row.owner_id != agent.owner_id or (row.agent_id and row.agent_id != agent.device_id):
        raise HTTPException(403, "上传批次属于其他账号或设备")
    expected_sha = body.get("sha256") or row.sha256
    expected_size = int(body.get("size") or 0)
    backend = storage_backend(get_settings())
    actual_size = None; actual_sha = None
    if backend == "tencent_cos":
        try:
            info = TencentCosStorage().head(row.storage_key)
            actual_size = int(info.get("ContentLength") or info.get("Content-Length") or info.get("content-length") or 0)
            etag = str(info.get("ETag") or info.get("etag") or "").strip('"')
            actual_sha = str(info.get("x-cos-meta-sha256") or info.get("X-Cos-Meta-Sha256") or "") or None
            if expected_size and actual_size != expected_size: raise HTTPException(400, "COS 对象大小校验失败")
            if not actual_sha: raise HTTPException(400, "COS 对象缺少服务端 SHA-256 元数据，拒绝确认")
            if expected_sha and actual_sha != expected_sha: raise HTTPException(400, "COS 对象 SHA-256 校验失败")
            if expected_sha and len(expected_sha) != 64: raise HTTPException(400, "SHA-256 格式不合法")
        except HTTPException: raise
        except Exception as exc: raise HTTPException(400, f"COS 对象尚未上传或无法校验：{exc}") from exc
    else:
        try:
            local = resolve_storage_key(get_settings(), row.storage_key)
            actual_size = local.stat().st_size
            actual_sha = sha256_file(local)
        except (ValueError, OSError) as exc: raise HTTPException(400, "本地对象尚未上传") from exc
        if expected_size and actual_size != expected_size: raise HTTPException(400, "对象大小校验失败")
        if expected_sha and actual_sha != expected_sha: raise HTTPException(400, "对象 SHA-256 校验失败")
        # COS is the upload ingress only. Persist a verified local mirror before
        # committing screenshot metadata so normal reads do not use COS egress.
        if not _mirror_cos_object(row.storage_key):
            raise HTTPException(503, "截图已上传 COS，但下载到中心主机失败，请稍后重试")
    row.status = "completed"; row.sha256 = actual_sha or expected_sha; db.add(row)
    # Confirm is also the metadata commit. Repeating it returns the existing
    # row, making retries safe after a network timeout.
    image = db.scalar(select(PracticalScreenshot).where(PracticalScreenshot.capture_id == capture_id))
    if image:
        db.commit(); return {"ok": True, "duplicate": True, "capture_id": capture_id, "id": image.id, "status": row.status, "storage_key": row.storage_key}
    if body.get("brand") and body.get("captured_at"):
        try:
            timestamp = datetime.fromisoformat(str(body["captured_at"]).replace("Z", "+00:00")); timestamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        except ValueError as exc: raise HTTPException(400, "captured_at 格式不合法") from exc
        image = PracticalScreenshot(capture_id=capture_id, brand=str(body["brand"]).strip(), captured_at=timestamp, original_name=str(body.get("filename") or capture_id), storage_key=row.storage_key, sha256=actual_sha or expected_sha or "", file_size=actual_size or expected_size, width=int(body.get("width") or 0), height=int(body.get("height") or 0), agent_id=agent.device_id, job_id=body.get("job_id"), owner_id=agent.owner_id)
        db.add(image)
    db.commit()
    if background_tasks is not None and image is not None:
        try:
            local_image = resolve_storage_key(get_settings(), image.storage_key)
        except ValueError:
            local_image = None
        if local_image is not None and local_image.is_file():
            background_tasks.add_task(thumbnail, get_settings(), local_image, image.id, 320)
    return {"ok": True, "capture_id": capture_id, "status": row.status, "storage_key": row.storage_key, "id": image.id if image else None}


@agent_router.post("/screenshots/confirm")
def confirm_upload(capture_id: str, body: dict, x_agent_token: Annotated[str | None, Header()] = None, db: Session = Depends(get_db)): return complete_upload(capture_id, body, x_agent_token, None, db)

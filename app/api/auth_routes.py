from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pathlib import Path
import hashlib
import re
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentUser, SESSION_COOKIE, load_user_from_token, session_expiry, _principal
from app.config import get_settings
from app.db import get_db
from app.models import PracticalAuthAccount, PracticalAuthSession, PracticalUploadSession, User, new_id
from app.services.storage import TencentCosStorage, UPLOAD_COS_THRESHOLD
from app.services.update_bundle import MAX_UPDATE_BYTES
from app.services.admin_config import effective_storage_config
from app.schemas import AuthStatus, LoginRequest, UserView
from app.security import hash_password, hash_session_token, issue_session_token, normalize_username, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/uploads/initiate")
def initiate_upload(purpose: str, filename: str, size: int, sha256: str, user: CurrentUser, db: Session = Depends(get_db)):
    if size <= UPLOAD_COS_THRESHOLD: raise HTTPException(400, "仅大于 1MiB 的文件使用此接口")
    if purpose == "update_bundle" and size > MAX_UPDATE_BYTES: raise HTTPException(413, "更新包不能超过 1GB")
    if purpose not in {"analysis_excel", "migration_bundle", "update_bundle"}: raise HTTPException(400, "不支持的上传用途")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256 or ""): raise HTTPException(400, "SHA-256 无效")
    settings = get_settings()
    if effective_storage_config(settings).get("backend") != "tencent_cos": raise HTTPException(413, "大文件必须先在管理员后台启用腾讯云 COS")
    safe = Path(filename or "upload.bin").name
    key = f"uploads/{user.id}/{purpose}/{new_id()}-{safe}"
    headers = {"x-cos-meta-sha256": sha256.lower()}
    try: url = TencentCosStorage().presign_put(key, metadata=headers)
    except Exception as exc: raise HTTPException(400, f"COS 预签名失败：{exc}") from exc
    row = PracticalUploadSession(owner_id=user.id, purpose=purpose, filename=safe, size=size, sha256=sha256.lower(), storage_key=key, upload_url=url)
    db.add(row); db.commit(); db.refresh(row)
    return {"upload_id": row.id, "upload_url": url, "upload_headers": headers, "storage_key": key}

@router.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    row = db.get(PracticalUploadSession, upload_id)
    if not row or row.owner_id != user.id: raise HTTPException(404, "上传会话不存在")
    if row.status == "consumed": raise HTTPException(409, "上传会话已消费")
    if row.status == "completed": return {"ok": True, "upload_id": row.id, "status": row.status, "storage_key": row.storage_key}
    try: info = TencentCosStorage().head(row.storage_key)
    except Exception as exc: raise HTTPException(400, f"COS 对象不存在：{exc}") from exc
    size = int(info.get("Content-Length") or info.get("content-length") or 0)
    metadata = str(info.get("x-cos-meta-sha256") or info.get("X-Cos-Meta-Sha256") or info.get("x-cos-meta-sha256".lower()) or "").lower()
    if size != row.size or metadata != row.sha256: raise HTTPException(400, "COS 文件大小或 SHA-256 校验失败")
    row.status = "completed"; db.commit()
    return {"ok": True, "upload_id": row.id, "status": row.status, "storage_key": row.storage_key, "purpose": row.purpose}


def _view(user):
    return UserView(id=user.id, username=user.username, display_name=user.display_name, role=user.role, active=user.active, avatar_custom=user.avatar_custom)


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)):
    return {"setup_required": db.scalar(select(PracticalAuthAccount.id).limit(1)) is None}


@router.post("/setup", response_model=UserView)
def setup(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(PracticalAuthAccount.id).limit(1)):
        raise HTTPException(409, "账号初始化已完成，请直接登录")
    owner = db.scalar(select(User).where(User.active.is_(True), User.role == "admin").order_by(User.username)) or db.scalar(select(User).where(User.active.is_(True)).order_by(User.username))
    try: password_hash = hash_password(body.password)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    # A standalone center may have a freshly created local users table rather
    # than the collaboration service's pre-existing shared users. In that
    # case the first center setup creates the local owner and its admin
    # account together. Existing shared users remain the preferred owner.
    if not owner:
        owner = User(
            id=new_id(),
            username=normalize_username(body.username),
            display_name=body.username.strip() or normalize_username(body.username),
            password_hash=password_hash,
            role="admin",
            active=True,
        )
        db.add(owner)
        db.flush()
    account = PracticalAuthAccount(id=new_id(), username=normalize_username(body.username), password_hash=password_hash, role="admin", active=True, owner_id=owner.id, setup_marker="initial-admin")
    db.add(account)
    try: db.commit()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409, "账号初始化已被其他请求完成，请直接登录") from exc
    db.refresh(account)
    return _view(_principal(db, account))


@router.post("/login", response_model=UserView)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    account = db.scalar(select(PracticalAuthAccount).where(PracticalAuthAccount.username == body.username))
    user = _principal(db, account)
    if not user or not verify_password(body.password, account.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = issue_session_token()
    db.add(PracticalAuthSession(account_id=account.id, token_hash=hash_session_token(token), expires_at=session_expiry()))
    db.commit()
    settings = get_settings()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_days * 86400, path="/")
    return _view(user)


@router.post("/logout")
def logout(response: Response, user: CurrentUser, db: Session = Depends(get_db)):
    db.execute(delete(PracticalAuthSession).where(PracticalAuthSession.account_id == user.account.id)); db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserView)
def me(user: CurrentUser): return _view(user)


@router.get("/status", response_model=AuthStatus)
def auth_status(db: Session = Depends(get_db), session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None):
    user = load_user_from_token(db, session_token)
    return AuthStatus(authenticated=user is not None, user=_view(user) if user else None)

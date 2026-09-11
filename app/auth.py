from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import PracticalAuthAccount, PracticalAuthSession, User, utcnow
from app.security import hash_session_token

SESSION_COOKIE = "practical_tools_session"
DbSession = Annotated[Session, Depends(get_db)]


class AuthPrincipal:
    def __init__(self, account, owner): self.account, self.owner = account, owner
    @property
    def id(self): return self.owner.id
    @property
    def username(self): return self.account.username
    @property
    def display_name(self): return self.account.username
    @property
    def role(self): return self.account.role
    @property
    def active(self): return self.account.active and self.owner.active
    @property
    def avatar_storage_key(self): return self.account.avatar_storage_key
    @avatar_storage_key.setter
    def avatar_storage_key(self, value): self.account.avatar_storage_key = value
    @property
    def avatar_custom(self): return bool(self.account.avatar_storage_key)


def _principal(db, account):
    owner = db.get(User, account.owner_id) if account else None
    return AuthPrincipal(account, owner) if account and account.active and owner and owner.active else None


def load_user_from_token(db: Session, token: str | None) -> AuthPrincipal | None:
    if not token: return None
    record = db.scalar(select(PracticalAuthSession).where(PracticalAuthSession.token_hash == hash_session_token(token), PracticalAuthSession.expires_at > datetime.now(timezone.utc)))
    if not record: return None
    user = _principal(db, db.get(PracticalAuthAccount, record.account_id))
    if not user: return None
    record.last_seen_at = utcnow(); db.commit()
    return user


def current_user(db: DbSession, session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None) -> User:
    user = load_user_from_token(db, session_token)
    if not user: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


CurrentUser = Annotated[AuthPrincipal, Depends(current_user)]


def admin_user(user: CurrentUser) -> AuthPrincipal:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


CurrentAdmin = Annotated[AuthPrincipal, Depends(admin_user)]


def optional_current_user(
    db: DbSession,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User | None:
    """Return the logged-in user when present, without making auth mandatory.

    This is used only by the one-time Agent pairing endpoint: normal business
    APIs continue to depend on ``CurrentUser`` and therefore return 401.
    """
    return load_user_from_token(db, session_token)


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=get_settings().session_days)

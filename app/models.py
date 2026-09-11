from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id() -> str: return str(uuid.uuid4())
def utcnow() -> datetime: return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# These two models intentionally retain the exact table names used by the
# main-image collaboration service. Production points both applications at
# the same PostgreSQL users/session tables.
class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="planner", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    avatar_storage_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    @property
    def avatar_custom(self) -> bool: return bool(self.avatar_storage_key)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PracticalAuthAccount(Base, TimestampMixin):
    """Credentials owned by this application; ``owner_id`` points to the
    shared collaboration user whose business data remains in scope."""
    __tablename__ = "practical_auth_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="planner", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    avatar_storage_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False)
    setup_marker: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)


class PracticalAuthSession(Base):
    __tablename__ = "practical_auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("practical_auth_accounts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PracticalAnalysisSource(Base, TimestampMixin):
    __tablename__ = "practical_analysis_source_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    year: Mapped[str] = mapped_column(String(12), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(30), default="ready", nullable=False)
    cache_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class PracticalAnalysisCache(Base, TimestampMixin):
    __tablename__ = "practical_analysis_cache_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    year: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class PracticalAnalysisDaily(Base):
    __tablename__ = "practical_analysis_daily"
    __table_args__ = (UniqueConstraint("owner_id", "year", "date", name="uq_practical_analysis_daily_owner"), Index("ix_practical_analysis_daily_date", "date"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    activity: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    new_visitors: Mapped[Optional[float]] = mapped_column(nullable=True)
    new_clicks_people: Mapped[Optional[float]] = mapped_column(nullable=True)
    new_avg_stay: Mapped[Optional[float]] = mapped_column(nullable=True)
    new_pay_buyers: Mapped[Optional[float]] = mapped_column(nullable=True)
    old_visitors: Mapped[Optional[float]] = mapped_column(nullable=True)
    old_clicks_people: Mapped[Optional[float]] = mapped_column(nullable=True)
    old_avg_stay: Mapped[Optional[float]] = mapped_column(nullable=True)
    old_pay_buyers: Mapped[Optional[float]] = mapped_column(nullable=True)


class PracticalAnalysisFix(Base):
    __tablename__ = "practical_analysis_fix"
    __table_args__ = (UniqueConstraint("owner_id", "year", "date", "field", name="uq_practical_analysis_fix_owner"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    delta: Mapped[float] = mapped_column(nullable=False)


class CaptureAgent(Base, TimestampMixin):
    __tablename__ = "practical_capture_agents"
    device_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    platform: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    version: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="paired", nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class CaptureShop(Base, TimestampMixin):
    __tablename__ = "practical_capture_shops"
    __table_args__ = (UniqueConstraint("name", name="uq_practical_capture_shop_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class CaptureSchedule(Base, TimestampMixin):
    __tablename__ = "practical_capture_schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    weekdays: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    hour: Mapped[int] = mapped_column(Integer, default=9, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class CaptureJob(Base, TimestampMixin):
    __tablename__ = "practical_capture_jobs"
    __table_args__ = (Index("ix_practical_capture_jobs_claim", "status", "owner_id", "agent_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False, index=True)
    progress: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("practical_capture_agents.device_id", ondelete="SET NULL"), index=True)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PracticalScreenshot(Base, TimestampMixin):
    __tablename__ = "practical_screenshots"
    __table_args__ = (UniqueConstraint("capture_id", name="uq_practical_screenshot_capture_id"), Index("ix_practical_screenshot_brand_date", "brand", "captured_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capture_id: Mapped[str] = mapped_column(String(120), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(700), nullable=False)
    thumbnail_key: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("practical_capture_agents.device_id", ondelete="SET NULL"), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("practical_capture_jobs.id", ondelete="SET NULL"), index=True)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)


class CapturePairCode(Base):
    __tablename__ = "practical_capture_pair_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CaptureUpload(Base, TimestampMixin):
    __tablename__ = "practical_capture_uploads"
    capture_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="initiated", nullable=False)
    storage_key: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    upload_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("practical_capture_agents.device_id", ondelete="SET NULL"), index=True)

class PracticalUploadSession(Base, TimestampMixin):
    __tablename__ = "practical_upload_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(700), nullable=False, unique=True)
    upload_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="initiated", nullable=False)


class PracticalMigrationTask(Base, TimestampMixin):
    """Persisted history-import task created by the administrator console."""

    __tablename__ = "practical_migration_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    bundle_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="uploaded", nullable=False, index=True)
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

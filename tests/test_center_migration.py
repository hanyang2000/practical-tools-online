from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import Base, SessionLocal
from app.models import CaptureAgent, CaptureSchedule, CaptureShop, PracticalAuthAccount, PracticalAuthSession, PracticalScreenshot, User
from app.services.center_migration import build_center_migration_bundle, import_center_migration_bundle, inspect_center_bundle


def test_center_export_includes_program_business_data_and_storage_but_not_sessions(tmp_path: Path):
    settings = replace(get_settings(), data_root=tmp_path / "source-data")
    settings.ensure_directories()
    source_file = settings.data_root / "analysis" / "users" / "u-1" / "source" / "2026.xlsx"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"analysis")
    image = settings.storage_root / "screenshots" / "brand" / "2026-09-03" / "capture.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    config_file = settings.data_root / "admin" / "storage.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({"backend": "tencent_cos", "bucket": "bucket", "region": "ap-shanghai", "secret_id": "id", "secret_key": "secret-value"}), encoding="utf-8")

    with SessionLocal() as db:
        user = User(id="u-1", username="migration-user", display_name="Migration User", password_hash="hash", role="admin", active=True)
        db.add(user)
        db.flush()
        db.add(PracticalAuthAccount(id="account-1", username="migration-user", password_hash="hash", role="admin", active=True, owner_id=user.id))
        db.add(PracticalAuthSession(id="session-1", account_id="account-1", token_hash="s" * 64, expires_at=datetime.now(timezone.utc)))
        db.add(CaptureAgent(device_id="agent-1", token_hash="a" * 64, name="Mac", platform="Darwin", version="0.5.1", owner_id=user.id))
        db.add(CaptureShop(id="shop-1", name="店铺", url="https://example.com", owner_id=user.id))
        db.add(CaptureSchedule(id="schedule-1", weekdays=[4], hour=15, minute=10, enabled=True, owner_id=user.id))
        db.add(PracticalScreenshot(id="image-1", capture_id="capture-1", brand="店铺", captured_at=datetime.now(timezone.utc), original_name="capture.png", storage_key="screenshots/brand/2026-09-03/capture.png", sha256="i" * 64, file_size=5, width=1, height=1, agent_id="agent-1", owner_id=user.id))
        db.commit()

        output = tmp_path / "center.ptcenter.zip"
        report = build_center_migration_bundle(db, settings, output, program_root=Path(__file__).parents[1])

    manifest = inspect_center_bundle(output)
    data_only_manifest = inspect_center_bundle(output, verify_program=False)
    assert report["sha256"]
    assert manifest["format"] == "practical-tools-center-migration-v1"
    assert data_only_manifest["totals"] == manifest["totals"]
    assert manifest["program_included"] is True
    assert any(item["path"].startswith("payload/program/app/") for item in manifest["files"])
    assert any(item["path"].endswith("capture.png") for item in manifest["files"])
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert not any("session" in name.casefold() for name in names if "database" in name)
        assert "payload/database/practical_auth_accounts.json" in names
        assert b'"token_hash":"' in archive.read("payload/database/practical_capture_agents.json")
        assert b"secret-value" not in b"".join(archive.read(name) for name in names if name != "manifest.json")


def test_center_import_requires_empty_target_and_roundtrips_agent_and_files(tmp_path: Path):
    source_settings = replace(get_settings(), data_root=tmp_path / "source-data")
    source_settings.ensure_directories()
    source_image = source_settings.storage_root / "screenshots" / "a.png"
    source_image.parent.mkdir(parents=True)
    source_image.write_bytes(b"center-image")
    with SessionLocal() as source_db:
        owner = User(id="owner-1", username="owner", display_name="Owner", password_hash="hash", role="admin", active=True)
        source_db.add(owner)
        source_db.flush()
        source_db.add(PracticalAuthAccount(id="account-1", username="owner", password_hash="hash", role="admin", active=True, owner_id=owner.id))
        source_db.add(CaptureAgent(device_id="agent-1", token_hash="b" * 64, owner_id=owner.id))
        source_db.commit()
        bundle = tmp_path / "center.ptcenter.zip"
        build_center_migration_bundle(source_db, source_settings, bundle, program_root=None)

    target_engine = create_engine(f"sqlite:///{tmp_path / 'target.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(target_engine)
    TargetSession = sessionmaker(bind=target_engine, autoflush=False, expire_on_commit=False)
    target_settings = replace(get_settings(), data_root=tmp_path / "target-data")
    target_settings.ensure_directories()
    with TargetSession() as target_db:
        target_db.add(User(id="owner-1", username="owner", display_name="Owner", password_hash="hash", role="admin", active=True))
        target_db.add(PracticalAuthAccount(id="account-1", username="owner", password_hash="hash", role="admin", active=True, owner_id="owner-1"))
        target_db.commit()
        try:
            import_center_migration_bundle(bundle, target_db, target_settings)
        except ValueError as exc:
            assert "已有业务数据" in str(exc)
        result = import_center_migration_bundle(bundle, target_db, target_settings, allow_nonempty=True)
        assert result["tables"]["practical_capture_agents"] == 1
        assert target_db.get(CaptureAgent, "agent-1").token_hash == "b" * 64
        assert (target_settings.storage_root / "screenshots" / "a.png").read_bytes() == b"center-image"
    target_engine.dispose()


def test_data_only_import_keeps_content_but_allows_new_accounts_and_agents(tmp_path: Path):
    source_settings = replace(get_settings(), data_root=tmp_path / "source-data")
    source_settings.ensure_directories()
    image = source_settings.storage_root / "screenshots" / "old.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"old-image")
    with SessionLocal() as source_db:
        owner = User(id="old-owner", username="old-owner", display_name="Old", password_hash="hash", role="admin", active=True)
        source_db.add(owner)
        source_db.flush()
        source_db.add(CaptureAgent(device_id="old-agent", token_hash="c" * 64, owner_id=owner.id))
        source_db.add(PracticalScreenshot(id="old-shot", capture_id="old-capture", brand="店铺", captured_at=datetime.now(timezone.utc), original_name="old.png", storage_key="screenshots/old.png", sha256="d" * 64, file_size=9, width=1, height=1, agent_id="old-agent", owner_id=owner.id))
        source_db.commit()
        bundle = tmp_path / "data-only.ptcenter.zip"
        build_center_migration_bundle(source_db, source_settings, bundle, program_root=None)

    target_engine = create_engine(f"sqlite:///{tmp_path / 'data-only-target.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(target_engine)
    TargetSession = sessionmaker(bind=target_engine, autoflush=False, expire_on_commit=False)
    target_settings = replace(get_settings(), data_root=tmp_path / "target-data")
    target_settings.ensure_directories()
    with TargetSession() as target_db:
        target_db.add(User(id="new-owner", username="new-owner", display_name="New", password_hash="hash", role="admin", active=True))
        target_db.commit()
        result = import_center_migration_bundle(bundle, target_db, target_settings, data_only=True)
        shot = target_db.scalar(select(PracticalScreenshot).where(PracticalScreenshot.id == "old-shot"))
        assert result["data_only"] is True
        assert "practical_capture_agents" not in result["tables"]
        assert shot is not None
        assert shot.owner_id is None and shot.agent_id is None and shot.job_id is None
        assert (target_settings.storage_root / "screenshots" / "old.png").read_bytes() == b"old-image"
    target_engine.dispose()

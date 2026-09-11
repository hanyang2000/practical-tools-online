from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.config import get_settings
from scripts.export_legacy_bundle import build_bundle
from app.api.admin import _rotate_completed_update_marker


def _login(client: TestClient, make_user, username: str, *, role: str, active: bool = True) -> None:
    make_user(username=username, password="secret-password", role=role, active=active)
    response = client.post("/api/auth/login", json={"username": username, "password": "secret-password"})
    assert response.status_code == 200, response.text


def test_admin_api_and_page_require_exact_admin_role(client, make_user):
    assert client.get("/api/admin/status").status_code == 401
    assert client.get("/admin/").status_code == 401

    _login(client, make_user, "planner-user", role="planner")
    assert client.get("/api/admin/status").status_code == 403
    assert client.get("/admin/").status_code == 403
    client.post("/api/auth/logout")

    # The shared main-image service defines `admin` as the administrative
    # role. Unknown role values must not silently gain center-level authority.
    _login(client, make_user, "unknown-super", role="superadmin")
    assert client.get("/api/admin/status").status_code == 403
    client.post("/api/auth/logout")

    _login(client, make_user, "admin-user", role="admin")
    assert client.get("/api/admin/status").status_code == 200
    page = client.get("/admin/")
    assert page.status_code == 200
    assert "管理后台" in page.text


def test_admin_storage_secrets_are_masked_and_saved_values_reach_cos_adapter(client, make_user, monkeypatch):
    _login(client, make_user, "admin-user", role="admin")
    for name in (
        "PRACTICAL_COS_BUCKET", "PRACTICAL_COS_REGION", "PRACTICAL_COS_PREFIX",
        "PRACTICAL_COS_SECRET_ID", "PRACTICAL_COS_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    secret_id = "AKID-ADMIN-PAGE-ONLY"
    secret_key = "COS-SECRET-MUST-NEVER-ECHO"
    saved = client.put(
        "/api/admin/settings/storage",
        json={
            "backend": "tencent_cos",
            "bucket": "private-1250000000",
            "region": "ap-shanghai",
            "prefix": "practical-tools",
            "secret_id": secret_id,
            "secret_key": secret_key,
        },
    )
    assert saved.status_code == 200, saved.text
    assert secret_id not in saved.text
    assert secret_key not in saved.text
    public = client.get("/api/admin/settings/storage")
    assert public.status_code == 200
    assert public.json()["storage"]["secret_id_configured"] is True
    assert public.json()["storage"]["secret_key_configured"] is True
    assert secret_id not in public.text
    assert secret_key not in public.text

    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured["config"] = kwargs

    class FakeClient:
        def __init__(self, config):
            captured["client_config"] = config

        def head_bucket(self, **kwargs):
            captured["head_bucket"] = kwargs
            return {"ok": True}

    monkeypatch.setitem(sys.modules, "qcloud_cos", types.SimpleNamespace(CosConfig=FakeConfig, CosS3Client=FakeClient))
    tested = client.post("/api/admin/settings/storage/test")
    assert tested.status_code == 200, tested.text
    assert captured["config"] == {
        "Region": "ap-shanghai", "SecretId": secret_id, "SecretKey": secret_key
    }
    assert captured["head_bucket"] == {"Bucket": "private-1250000000"}
    assert secret_id not in tested.text
    assert secret_key not in tested.text


def test_admin_storage_blank_secrets_keep_existing_values(client, make_user):
    _login(client, make_user, "admin-user", role="admin")
    first = client.put(
        "/api/admin/settings/storage",
        json={
            "backend": "tencent_cos", "bucket": "first-1250000000", "region": "ap-shanghai",
            "secret_id": "AKID-KEEP", "secret_key": "SECRET-KEEP",
        },
    )
    assert first.status_code == 200
    second = client.put(
        "/api/admin/settings/storage",
        json={
            "backend": "tencent_cos", "bucket": "second-1250000000", "region": "ap-guangzhou",
            "secret_id": "", "secret_key": "",
        },
    )
    assert second.status_code == 200
    from app.services.admin_config import effective_storage_config
    values = effective_storage_config(get_settings())
    assert values["secret_id"] == "AKID-KEEP"
    assert values["secret_key"] == "SECRET-KEEP"
    assert values["bucket"] == "second-1250000000"


def test_migration_bundle_cannot_start_for_inactive_target_account(client, make_user, tmp_path: Path):
    _login(client, make_user, "admin-user", role="admin")
    inactive = make_user(username="disabled-owner", role="planner", active=False)
    legacy = tmp_path / "legacy"
    (legacy / "analysis").mkdir(parents=True)
    (legacy / "analysis" / "cache.json").write_text("{}", encoding="utf-8")
    bundle = tmp_path / "history.ptmigration.zip"
    build_bundle(bundle, legacy, tmp_path / "capture")
    with bundle.open("rb") as stream:
        uploaded = client.post(
            "/api/admin/migration/bundles/import",
            files={"file": (bundle.name, stream, "application/zip")},
        )
    assert uploaded.status_code == 200, uploaded.text
    task_id = uploaded.json()["task"]["id"]
    started = client.post(
        f"/api/admin/migration/tasks/{task_id}/start",
        json={"owner_id": inactive.id},
    )
    assert started.status_code == 400


def _write_update(path: Path, *, unsafe_path: str | None = None, version: str = "9.0.0", second_file: bool = False) -> Path:
    payload_name = unsafe_path or "app/__init__.py"
    payloads = {payload_name: f'__version__ = "{version}"\n'.encode(), "app/main.py": b"# test entrypoint\n"}
    if second_file:
        payloads["frontend/shell/update-test.js"] = b"window.updated = true;\n"
    manifest = {
        "format": "practical-tools-online-update-v1",
        "version": version,
        "notes": "test",
        "files": [
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in payloads.items()
        ],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in payloads.items():
            archive.writestr(f"payload/{name}", data)
    return path


def test_online_update_rejects_non_archive_and_path_escape_before_staging(client, make_user, tmp_path: Path):
    _login(client, make_user, "admin-user", role="admin")
    invalid = tmp_path / "not-update.zip"
    invalid.write_bytes(b"this is not an update archive")
    with invalid.open("rb") as stream:
        response = client.post("/api/admin/updates/upload", files={"file": (invalid.name, stream, "application/zip")})
    assert response.status_code == 400

    escaped = _write_update(tmp_path / "escaped.zip", unsafe_path="../data/overwrite.txt")
    with escaped.open("rb") as stream:
        response = client.post("/api/admin/updates/upload", files={"file": (escaped.name, stream, "application/zip")})
    assert response.status_code == 400


def test_online_update_requires_newer_version_and_complete_manifest(client, make_user, tmp_path: Path):
    _login(client, make_user, "admin-user", role="admin")
    incomplete = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "practical-tools-online-update-v1", "version": "9.0.0", "files": []}))
    with incomplete.open("rb") as stream:
        response = client.post("/api/admin/updates/upload", files={"file": (incomplete.name, stream, "application/zip")})
    assert response.status_code == 400

    same_version = _write_update(tmp_path / "same-version.zip", version=__version__)
    with same_version.open("rb") as stream:
        response = client.post("/api/admin/updates/upload", files={"file": (same_version.name, stream, "application/zip")})
    assert response.status_code == 400


def test_update_worker_restores_every_replaced_file_when_apply_fails(tmp_path: Path, monkeypatch):
    """A partial file replacement must never leave a mixed-version center."""
    worker_path = Path(__file__).parents[1] / "packaging" / "update_worker.py"
    spec = importlib.util.spec_from_file_location("practical_update_worker", worker_path)
    assert spec and spec.loader
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)

    app_root = tmp_path / "green-center"
    first = app_root / "app" / "app" / "__init__.py"
    second = app_root / "frontend" / "shell" / "update-test.js"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("old app", encoding="utf-8")
    second.write_text("old frontend", encoding="utf-8")
    (app_root / ".env").write_text("KEEP_ENV=1", encoding="utf-8")
    (app_root / "data").mkdir()
    (app_root / "data" / "keep.txt").write_text("keep data", encoding="utf-8")
    package = _write_update(tmp_path / "update.zip", second_file=True)

    original_replace = worker.os.replace
    replacements = 0

    def fail_during_second_replace(source, destination):
        nonlocal replacements
        replacements += 1
        if replacements >= 2:
            raise OSError("simulated disk failure")
        return original_replace(source, destination)

    monkeypatch.setattr(worker.os, "replace", fail_during_second_replace)
    monkeypatch.setattr(worker, "RETRY_COUNT", 2)
    monkeypatch.setattr(worker, "RETRY_DELAY", 0)
    with pytest.raises(OSError, match="simulated disk failure"):
        worker.apply_update(package, app_root)

    assert first.read_text(encoding="utf-8") == "old app"
    assert second.read_text(encoding="utf-8") == "old frontend"
    assert (app_root / ".env").read_text(encoding="utf-8") == "KEEP_ENV=1"
    assert (app_root / "data" / "keep.txt").read_text(encoding="utf-8") == "keep data"


def test_new_update_rotates_stale_completed_marker(tmp_path: Path):
    marker = tmp_path / "pending.completed.json"
    marker.write_text("old result", encoding="utf-8")

    rotated = _rotate_completed_update_marker(tmp_path)

    assert rotated is not None and rotated.name.startswith("pending.completed.previous-")
    assert rotated.read_text(encoding="utf-8") == "old result"
    assert not marker.exists()

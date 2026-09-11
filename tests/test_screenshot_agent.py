from __future__ import annotations

import hashlib
import io
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from app.db import SessionLocal
from app.models import CaptureAgent, CaptureShop, CaptureUpload, PracticalScreenshot
from app.services.storage import resolve_storage_key
from app.services.admin_config import save_storage_config
from app.config import get_settings, reset_settings_cache


def _png_bytes(color=(20, 90, 180), size=(64, 120)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


def _pair(client, device="device-1") -> str:
    response = client.post(
        "/api/agent/pair",
        json={"device_id": device, "name": device, "platform": "Darwin", "version": "1.0.0"},
    )
    assert response.status_code == 200, response.text
    return response.json()["agent_token"]


def _agent_headers(token: str) -> dict[str, str]:
    return {"X-Agent-Token": token}


def test_shop_capture_job_and_agent_lifecycle(authenticated_client):
    client = authenticated_client
    assert client.post("/api/screenshot/shops", json={"name": "测试店铺", "url": "https://example.test/shop"}).json() == {"ok": True}
    shops = client.get("/api/screenshot/shops").json()["shops"]
    assert [(shop["name"], shop["url"]) for shop in shops] == [("测试店铺", "https://example.test/shop")]

    token = _pair(client)
    created = client.post("/api/screenshot/capture", json={"shop": "测试店铺"})
    assert created.status_code == 200 and created.json()["ok"] is True
    job_id = created.json()["job_id"]

    claimed = client.get("/api/agent/jobs/next", headers=_agent_headers(token))
    assert claimed.status_code == 200
    assert claimed.json()["job"] == {"id": job_id, "kind": "capture", "payload": {"shop": "测试店铺"}}

    progress = {"current": 1, "total": 2, "shop": "测试店铺"}
    assert client.post(
        f"/api/agent/jobs/{job_id}/progress",
        json={"progress": progress, "status": "running"},
        headers=_agent_headers(token),
    ).json() == {"ok": True}
    assert client.get("/api/screenshot/progress").json() == progress
    assert client.get("/api/screenshot/capture/status").json()["running"] is True

    assert client.post(
        f"/api/agent/jobs/{job_id}/complete",
        json={"status": "completed", "result": {"success": []}},
        headers=_agent_headers(token),
    ).json() == {"ok": True}
    assert client.get("/api/screenshot/progress").json() == progress
    status = client.get("/api/screenshot/capture/status").json()
    assert status["running"] is False and status["status"] == "idle"


def test_scheduled_capture_progress_is_visible_and_manual_capture_is_blocked(authenticated_client):
    client = authenticated_client
    _pair(client, device="scheduled-agent")
    with SessionLocal() as db:
        agent = db.get(CaptureAgent, "scheduled-agent")
        agent.status = "busy"
        agent.meta = {
            "capture_running": True,
            "capture_source": "scheduled",
            "capture_progress": {"current": 5, "total": 12, "shop": "理肤泉", "stage": "加载整页内容"},
        }
        db.commit()

    status = client.get("/api/screenshot/capture/status").json()
    assert status["running"] is True and status["source"] == "scheduled"
    assert client.get("/api/screenshot/progress").json()["current"] == 5
    blocked = client.post("/api/screenshot/capture", json={})
    assert blocked.status_code == 409
    assert "定时截图正在执行" in blocked.json()["detail"]


def test_login_open_folder_and_schedule_commands_are_visible_to_agent(authenticated_client):
    client = authenticated_client
    token = _pair(client)

    login = client.post("/api/screenshot/login").json()
    login_job = client.get("/api/agent/jobs/next", headers=_agent_headers(token)).json()["job"]
    assert login_job["id"] == login["job_id"] and login_job["kind"] == "login"
    client.post(f"/api/agent/jobs/{login_job['id']}/complete", json={}, headers=_agent_headers(token))

    opened = client.post("/api/screenshot/open-folder").json()
    folder_job = client.get("/api/agent/jobs/next", headers=_agent_headers(token)).json()["job"]
    assert folder_job["id"] == opened["job_id"] and folder_job["kind"] == "open_folder"
    client.post(f"/api/agent/jobs/{folder_job['id']}/complete", json={}, headers=_agent_headers(token))

    configured = client.post(
        "/api/screenshot/scheduler/schedule",
        json={"weekdays": [1, 3, 5], "hour": 8, "minute": 35},
    )
    assert configured.json() == {"ok": True, "weekdays": [1, 3, 5], "hour": 8, "minute": 35}
    assert client.post("/api/screenshot/scheduler/install").json()["installed"] is True
    agent_config = client.get("/api/agent/config", headers=_agent_headers(token)).json()
    assert agent_config["schedule"] == {"weekdays": [1, 3, 5], "hour": 8, "minute": 35}
    assert client.post("/api/screenshot/scheduler/uninstall").json()["installed"] is False


def test_schedule_config_saves_time_and_enabled_state_atomically(authenticated_client):
    client = authenticated_client
    response = client.post(
        "/api/screenshot/scheduler/config",
        json={"weekdays": [4], "hour": 10, "minute": 0, "enabled": True},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "installed": True,
        "enabled": True,
        "schedule": {"weekdays": [4], "hour": 10, "minute": 0},
    }
    assert client.get("/api/screenshot/scheduler/status").json()["enabled"] is True


def test_schedule_config_pushes_immediate_sync_job_to_agent(authenticated_client):
    client = authenticated_client
    token = _pair(client, device="scheduler-agent")
    response = client.post(
        "/api/screenshot/scheduler/config",
        json={"weekdays": [4], "hour": 11, "minute": 0, "enabled": True},
    )
    assert response.status_code == 200
    queued = client.get("/api/agent/jobs/next", headers=_agent_headers(token))
    assert queued.status_code == 200
    assert queued.json()["job"]["kind"] == "scheduler_install"
    assert queued.json()["job"]["payload"] == {
        "command": "scheduler_install",
        "schedule": {"weekdays": [4], "hour": 11, "minute": 0},
    }


def test_invalid_schedule_config_does_not_change_existing_enabled_task(authenticated_client):
    client = authenticated_client
    client.post("/api/screenshot/scheduler/config", json={"weekdays": [4], "hour": 10, "minute": 0})
    invalid = client.post("/api/screenshot/scheduler/config", json={"weekdays": [], "hour": 10, "minute": 0})
    assert invalid.status_code == 400
    current = client.get("/api/screenshot/scheduler/status").json()
    assert current["enabled"] is True
    assert current["schedule"] == {"weekdays": [4], "hour": 10, "minute": 0}


def test_local_multipart_upload_list_file_thumb_and_delete(authenticated_client):
    client = authenticated_client
    token = _pair(client)
    content = _png_bytes()
    digest = hashlib.sha256(content).hexdigest()
    captured_at = "2026-08-31T09:10:11+00:00"
    response = client.post(
        "/api/agent/screenshots",
        params={"capture_id": "capture-local-1", "brand": "品牌A", "captured_at": captured_at},
        files={"file": ("09-10-11.png", content, "image/png")},
        headers=_agent_headers(token),
    )
    assert response.status_code == 200, response.text
    uploaded = response.json()
    assert uploaded["ok"] is True and uploaded["sha256"] == digest

    duplicate = client.post(
        "/api/agent/screenshots",
        params={"capture_id": "capture-local-1", "brand": "品牌A", "captured_at": captured_at},
        files={"file": ("different.png", _png_bytes((255, 0, 0)), "image/png")},
        headers=_agent_headers(token),
    )
    assert duplicate.json()["duplicate"] is True

    listing = client.get("/api/screenshot/list").json()
    assert listing["total"] == 1
    image = listing["brands"][0]["dates"][0]["images"][0]
    assert image["brand"] == "品牌A" and image["date"] == "2026-08-31"
    original = client.get("/api/screenshot/file", params={"path": image["path"]})
    assert original.status_code == 200 and original.content == content
    thumb = client.get("/api/screenshot/thumb", params={"path": image["path"]})
    assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/jpeg")
    with Image.open(io.BytesIO(thumb.content)) as thumb_image:
        assert thumb_image.width <= 480
    preview = client.get("/api/screenshot/preview", params={"path": image["path"], "size": 1440})
    assert preview.status_code == 200 and preview.headers["content-type"].startswith("image/webp")
    with Image.open(io.BytesIO(preview.content)) as preview_image:
        assert max(preview_image.size) <= 1440

    large_preview = client.get("/api/screenshot/preview", params={"path": image["path"], "size": 4096})
    assert large_preview.status_code == 200
    with Image.open(io.BytesIO(large_preview.content)) as large_preview_image:
        assert max(large_preview_image.size) <= 4096
    assert client.post("/api/screenshot/delete", json={"paths": [image["path"]]}).json() == {"deleted": 1}
    assert client.get("/api/screenshot/list").json()["total"] == 0


def test_windows_client_filename_is_stored_as_a_safe_relative_name(authenticated_client):
    client = authenticated_client
    token = _pair(client, "windows-filename-agent")
    response = client.post(
        "/api/agent/screenshots",
        params={"capture_id": "windows-filename-capture", "brand": "品牌A", "captured_at": "2026-08-26T09:10:11+00:00"},
        files={"file": (r"C:\\Users\\ThinkPad\\Pictures\\capture.png", _png_bytes(), "image/png")},
        headers=_agent_headers(token),
    )
    assert response.status_code == 200, response.text
    stored = response.json()["storage_key"]
    assert stored.endswith("-capture.png")
    assert "\\" not in stored and ":" not in stored
    assert resolve_storage_key(get_settings(), stored).is_file()


def test_screenshot_routes_read_a_legacy_migrated_data_layout(authenticated_client):
    client = authenticated_client
    owner_id = client.get("/api/auth/me").json()["id"]
    content = _png_bytes(size=(120, 80))
    key = r"screenshots\品牌A\2026-08-26\capture.png"
    legacy = get_settings().data_root / "screenshots" / "品牌A" / "2026-08-26" / "capture.png"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(content)
    with SessionLocal() as db:
        db.add(PracticalScreenshot(
            capture_id="legacy-layout-capture",
            brand="品牌A",
            captured_at=datetime(2026, 8, 26, 9, 10, 11, tzinfo=timezone.utc),
            original_name="capture.png",
            storage_key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            file_size=len(content),
            width=120,
            height=80,
            owner_id=owner_id,
        ))
        db.commit()
    assert client.get("/api/screenshot/file", params={"path": key}).status_code == 200
    thumb = client.get("/api/screenshot/thumb", params={"path": key})
    assert thumb.status_code == 200 and thumb.headers["content-type"].startswith("image/jpeg")


def test_screenshots_are_readable_across_users_but_delete_is_owner_only(client, make_user):
    make_user("image-owner")
    make_user("image-reader")
    assert client.post("/api/auth/login", json={"username": "image-owner", "password": "correct-password"}).status_code == 200
    token = _pair(client, "image-owner-agent")
    content = _png_bytes()
    uploaded = client.post(
        "/api/agent/screenshots",
        params={"capture_id": "cross-account-image", "brand": "共享品牌", "captured_at": "2026-08-31T09:10:11+00:00"},
        files={"file": ("shared.png", content, "image/png")},
        headers=_agent_headers(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    owner_image = client.get("/api/screenshot/list").json()["brands"][0]["dates"][0]["images"][0]
    assert owner_image["can_delete"] is True

    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "image-reader", "password": "correct-password"}).status_code == 200
    reader_image = client.get("/api/screenshot/list").json()["brands"][0]["dates"][0]["images"][0]
    assert reader_image["can_delete"] is False
    assert client.get("/api/screenshot/file", params={"path": reader_image["path"]}).status_code == 200
    assert client.get("/api/screenshot/thumb", params={"path": reader_image["path"]}).status_code == 200
    assert client.post("/api/screenshot/delete", json={"paths": [reader_image["path"]]}).json() == {"deleted": 0, "skipped": 1}
    assert client.get("/api/screenshot/list").json()["total"] == 1


def test_admin_can_delete_screenshots_owned_by_another_account(client, make_user):
    make_user("image-owner")
    make_user("image-admin", role="admin")
    assert client.post("/api/auth/login", json={"username": "image-owner", "password": "correct-password"}).status_code == 200
    token = _pair(client, "admin-delete-agent")
    uploaded = client.post(
        "/api/agent/screenshots",
        params={"capture_id": "admin-delete-image", "brand": "管理员可见", "captured_at": "2026-08-31T09:10:11+00:00"},
        files={"file": ("shared.png", _png_bytes(), "image/png")},
        headers=_agent_headers(token),
    )
    assert uploaded.status_code == 200, uploaded.text

    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "image-admin", "password": "correct-password"}).status_code == 200
    image = client.get("/api/screenshot/list").json()["brands"][0]["dates"][0]["images"][0]
    assert image["can_delete"] is True
    assert client.post("/api/screenshot/delete", json={"paths": [image["path"]]}).json() == {"deleted": 1}
    assert client.get("/api/screenshot/list").json()["total"] == 0


def test_local_initiate_complete_is_sha256_checked_and_idempotent(authenticated_client):
    client = authenticated_client
    token = _pair(client)
    content = _png_bytes()
    digest = hashlib.sha256(content).hexdigest()
    initiated = client.post(
        "/api/agent/screenshots/initiate",
        params={"capture_id": "batch-local", "filename": "capture.png", "size": len(content)},
        headers=_agent_headers(token),
    )
    assert initiated.status_code == 200
    batch = initiated.json()
    target = resolve_storage_key(get_settings(), batch["storage_key"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    body = {
        "sha256": digest,
        "size": len(content),
        "brand": "品牌B",
        "captured_at": "2026-08-31T10:00:00Z",
        "filename": "capture.png",
        "width": 64,
        "height": 120,
    }
    completed = client.post(
        "/api/agent/screenshots/complete",
        params={"capture_id": "batch-local"},
        json=body,
        headers=_agent_headers(token),
    )
    assert completed.status_code == 200 and completed.json()["id"]
    repeated = client.post(
        "/api/agent/screenshots/complete",
        params={"capture_id": "batch-local"},
        json=body,
        headers=_agent_headers(token),
    )
    assert repeated.status_code == 200 and repeated.json()["duplicate"] is True
    conflict = client.post(
        "/api/agent/screenshots/complete",
        params={"capture_id": "batch-local"},
        json={**body, "sha256": "0" * 64},
        headers=_agent_headers(token),
    )
    assert conflict.status_code == 400


class FakeCos:
    def __init__(self, *, size: int, sha256: str | None):
        self.size = size
        self.sha256 = sha256
        self.deleted = []

    def presign_put(self, key, expires=900):
        return f"https://cos.test/upload/{key}?expires={expires}"

    def presign_get(self, key, expires=300):
        return f"https://cos.test/download/{key}?expires={expires}"

    @contextmanager
    def temporary_download(self, key):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        path.write_bytes(_png_bytes())
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    def put_file(self, key, path, content_type="application/octet-stream"):
        self.thumbnail_key = key

    def head(self, key):
        result = {"ContentLength": self.size, "ETag": "not-a-sha256"}
        if self.sha256 is not None:
            result["x-cos-meta-sha256"] = self.sha256
        return result

    def delete(self, key):
        self.deleted.append(key)


def test_cos_mock_initiate_complete_idempotency_and_conflict(authenticated_client, monkeypatch):
    client = authenticated_client
    token = _pair(client)
    digest = hashlib.sha256(b"cos-object").hexdigest()
    fake = FakeCos(size=10, sha256=digest)
    monkeypatch.setenv("PRACTICAL_STORAGE_BACKEND", "tencent_cos")
    monkeypatch.setattr("app.api.screenshot.TencentCosStorage", lambda: fake)

    initiated = client.post(
        "/api/agent/screenshots/initiate",
        params={"capture_id": "cos-1", "filename": "capture.png", "size": 10},
        headers=_agent_headers(token),
    )
    assert initiated.status_code == 200
    assert initiated.json()["backend"] == "tencent_cos"
    assert initiated.json()["upload_url"].startswith("https://cos.test/upload/")

    body = {
        "sha256": digest,
        "size": 10,
        "brand": "COS品牌",
        "captured_at": "2026-08-31T11:00:00Z",
        "filename": "capture.png",
    }
    first = client.post("/api/agent/screenshots/complete", params={"capture_id": "cos-1"}, json=body, headers=_agent_headers(token))
    assert first.status_code == 200 and first.json()["id"]
    second = client.post("/api/agent/screenshots/complete", params={"capture_id": "cos-1"}, json=body, headers=_agent_headers(token))
    assert second.status_code == 200 and second.json()["duplicate"] is True
    conflict = client.post(
        "/api/agent/screenshots/complete",
        params={"capture_id": "cos-1"},
        json={**body, "sha256": "f" * 64},
        headers=_agent_headers(token),
    )
    assert conflict.status_code == 400


def test_cos_complete_rejects_missing_server_side_sha256(authenticated_client, monkeypatch):
    client = authenticated_client
    token = _pair(client)
    digest = hashlib.sha256(b"claimed-only").hexdigest()
    fake = FakeCos(size=12, sha256=None)
    monkeypatch.setenv("PRACTICAL_STORAGE_BACKEND", "tencent_cos")
    monkeypatch.setattr("app.api.screenshot.TencentCosStorage", lambda: fake)
    client.post("/api/agent/screenshots/initiate", params={"capture_id": "cos-no-sha", "filename": "x.png", "size": 12}, headers=_agent_headers(token))
    response = client.post(
        "/api/agent/screenshots/complete",
        params={"capture_id": "cos-no-sha"},
        json={"sha256": digest, "size": 12, "brand": "X", "captured_at": "2026-08-31T00:00:00Z"},
        headers=_agent_headers(token),
    )
    assert response.status_code == 400


def test_admin_storage_json_overrides_environment_for_screenshot_routes(authenticated_client, monkeypatch):
    client = authenticated_client
    token = _pair(client, "admin-config-agent")
    content = _png_bytes()
    digest = hashlib.sha256(content).hexdigest()
    fake = FakeCos(size=len(content), sha256=digest)
    # The process environment intentionally remains local; the administrator
    # JSON is the effective configuration used by every screenshot endpoint.
    monkeypatch.setenv("PRACTICAL_STORAGE_BACKEND", "local")
    save_storage_config(get_settings(), {
        "backend": "tencent_cos", "bucket": "test-bucket", "region": "ap-test",
    })
    monkeypatch.setattr("app.api.screenshot.TencentCosStorage", lambda: fake)
    initiated = client.post(
        "/api/agent/screenshots/initiate",
        params={"capture_id": "admin-cos", "filename": "capture.png", "size": len(content)},
        headers=_agent_headers(token),
    )
    assert initiated.status_code == 200 and initiated.json()["backend"] == "tencent_cos"
    completed = client.post(
        "/api/agent/screenshots/complete", params={"capture_id": "admin-cos"},
        json={"sha256": digest, "size": len(content), "brand": "管理员COS", "captured_at": "2026-08-31T00:00:00Z"},
        headers=_agent_headers(token),
    )
    assert completed.status_code == 200
    path = completed.json()["storage_key"]
    # COS is upload ingress only; after completion the center serves the local
    # mirror, so normal reads do not redirect the browser to COS.
    assert client.get("/api/screenshot/file", params={"path": path}, follow_redirects=False).status_code == 200
    assert client.get("/api/screenshot/thumb", params={"path": path}, follow_redirects=False).status_code == 200
    assert client.post("/api/screenshot/delete", json={"paths": [path]}).json() == {"deleted": 1}
    assert path in fake.deleted


def test_shops_are_isolated_by_user(client, make_user):
    make_user("user1")
    make_user("user2")
    assert client.post("/api/auth/login", json={"username": "user1", "password": "correct-password"}).status_code == 200
    client.post("/api/screenshot/shops", json={"name": "私有店铺", "url": "https://example.test/1"})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "user2", "password": "correct-password"}).status_code == 200
    assert client.get("/api/screenshot/shops").json()["shops"] == []


def test_agent_cannot_claim_another_users_job(client, make_user):
    make_user("user1")
    make_user("user2")
    client.post("/api/auth/login", json={"username": "user1", "password": "correct-password"})
    token1 = _pair(client, "agent-user1")
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "user2", "password": "correct-password"})
    client.post("/api/screenshot/capture", json={"shop": "user2-shop"})
    response = client.get("/api/agent/jobs/next", headers=_agent_headers(token1))
    assert response.json()["job"] is None


def test_pause_is_delivered_to_running_agent(authenticated_client):
    client = authenticated_client
    token = _pair(client)
    client.post("/api/screenshot/capture", json={})
    claimed = client.get("/api/agent/jobs/next", headers=_agent_headers(token)).json()["job"]
    assert claimed is not None
    client.post("/api/screenshot/capture/pause")
    command = client.get("/api/agent/jobs/next", headers=_agent_headers(token)).json()["job"]
    assert command is not None and command["kind"] == "pause"


def test_one_time_pairing_code_can_be_exchanged_without_browser_cookie(authenticated_client):
    client = authenticated_client
    owner_id = client.get("/api/auth/me").json()["id"]
    code = client.post("/api/agent/pair-code").json()["code"]
    client.post("/api/auth/logout")
    response = client.post(
        "/api/agent/pair",
        json={"code": code, "device_id": "new-device", "name": "new", "platform": "Darwin", "version": "1.0.0"},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(CaptureAgent, "new-device").owner_id == owner_id


def test_one_time_pairing_code_cannot_be_reused(authenticated_client):
    client = authenticated_client
    code = client.post("/api/agent/pair-code").json()["code"]
    client.post("/api/auth/logout")
    first = client.post(
        "/api/agent/pair",
        json={"code": code, "device_id": "first-device", "name": "first", "platform": "Darwin", "version": "1.0.0"},
    )
    assert first.status_code == 200
    reused = client.post(
        "/api/agent/pair",
        json={"code": code, "device_id": "second-device", "name": "second", "platform": "Windows", "version": "1.0.0"},
    )
    assert reused.status_code == 401


def test_anonymous_pairing_inherits_pair_code_owner(authenticated_client):
    client = authenticated_client
    client.post("/api/screenshot/shops", json={"name": "code-owner-shop", "url": "https://example.test/code-owner"})
    code = client.post("/api/agent/pair-code").json()["code"]
    client.post("/api/auth/logout")
    paired = client.post("/api/agent/pair", json={"code": code, "device_id": "code-owner-agent"})
    assert paired.status_code == 200, paired.text
    config = client.get("/api/agent/config", headers=_agent_headers(paired.json()["agent_token"])).json()
    assert [shop["name"] for shop in config["shops"]] == ["code-owner-shop"]


def test_agent_config_includes_ownerless_shared_shops(authenticated_client):
    client = authenticated_client
    with SessionLocal() as db:
        db.add(CaptureShop(name="共享历史店铺", url="https://example.test/shared", owner_id=None))
        db.commit()
    token = _pair(client, "shared-shop-agent")

    config = client.get("/api/agent/config", headers=_agent_headers(token)).json()

    assert [shop["name"] for shop in config["shops"]] == ["共享历史店铺"]


def test_agent_config_is_isolated_by_owner(client, make_user):
    make_user("user1")
    make_user("user2")
    client.post("/api/auth/login", json={"username": "user1", "password": "correct-password"})
    client.post("/api/screenshot/shops", json={"name": "user1-private-shop", "url": "https://example.test/private"})
    client.post("/api/screenshot/scheduler/schedule", json={"weekdays": [2], "hour": 7, "minute": 15})
    client.post("/api/screenshot/scheduler/install")
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "user2", "password": "correct-password"})
    token2 = _pair(client, "agent-user2-config")
    config = client.get("/api/agent/config", headers=_agent_headers(token2)).json()
    assert config["shops"] == [] and config["schedule"] is None
    assert config["agent_id"] == "agent-user2-config"


def test_agent_heartbeat_returns_protocol_compatibility_and_update_pointer(authenticated_client, monkeypatch):
    client = authenticated_client
    token = _pair(client, "protocol-agent")
    compatible = client.post(
        "/api/agent/heartbeat",
        json={"agent_id": "protocol-agent", "platform": "Darwin", "version": "0.4.3",
              "protocol_version": 1, "capabilities": ["capture"]},
        headers=_agent_headers(token),
    )
    assert compatible.status_code == 200
    assert compatible.json()["compatible"] is True

    monkeypatch.setenv("PRACTICAL_AGENT_MIN_PROTOCOL_VERSION", "2")
    monkeypatch.setenv("PRACTICAL_AGENT_UPDATE_MANIFEST_URL", "https://updates.example.test/agent-{platform}.json")
    reset_settings_cache()
    incompatible = client.post(
        "/api/agent/heartbeat",
        json={"agent_id": "protocol-agent", "platform": "Darwin", "version": "0.4.3",
              "protocol_version": 1, "capabilities": ["capture"]},
        headers=_agent_headers(token),
    )
    assert incompatible.status_code == 200
    payload = incompatible.json()
    assert payload["compatible"] is False and payload["agent_update_required"] is True
    assert payload["agent_update"] == {"manifest_url": "https://updates.example.test/agent-darwin.json", "platform": "Darwin"}
    reset_settings_cache()


def test_login_status_is_isolated_by_owner(client, make_user):
    make_user("user1")
    make_user("user2")
    client.post("/api/auth/login", json={"username": "user1", "password": "correct-password"})
    client.post("/api/screenshot/login")
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "user2", "password": "correct-password"})
    assert client.get("/api/screenshot/login/status").json() == {"running": False, "logged_in": False, "job_id": None}


def test_capture_upload_session_cannot_be_reused_by_another_owner(client, make_user):
    make_user("user1")
    make_user("user2")
    client.post("/api/auth/login", json={"username": "user1", "password": "correct-password"})
    token1 = _pair(client, "agent-user1-upload")
    created = client.post(
        "/api/agent/screenshots/initiate",
        params={"capture_id": "owner-bound-capture", "filename": "one.png", "size": 10},
        headers=_agent_headers(token1),
    )
    assert created.status_code == 200
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "user2", "password": "correct-password"})
    token2 = _pair(client, "agent-user2-upload")
    collision = client.post(
        "/api/agent/screenshots/initiate",
        params={"capture_id": "owner-bound-capture", "filename": "two.png", "size": 10},
        headers=_agent_headers(token2),
    )
    assert collision.status_code == 403

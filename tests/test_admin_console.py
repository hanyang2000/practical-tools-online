from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.config import get_settings


def _login(client, username, password="correct-password"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def test_admin_page_and_apis_are_role_gated(client, make_user):
    make_user("planner", role="planner")
    _login(client, "planner")
    assert client.get("/admin/").status_code == 403
    assert client.get("/api/admin/status").status_code == 403
    client.post("/api/auth/logout")
    make_user("admin", role="admin")
    _login(client, "admin")
    page = client.get("/admin/")
    assert page.status_code == 200 and "管理后台" in page.text and "COS" in page.text
    status = client.get("/api/admin/status")
    assert status.status_code == 200 and status.json()["version"]


def test_admin_storage_settings_never_echo_secret_and_are_used(client, make_user):
    make_user("admin", role="admin")
    _login(client, "admin")
    response = client.put("/api/admin/settings/storage", json={"backend": "tencent_cos", "bucket": "bucket-a", "region": "ap-shanghai", "secret_id": "sid", "secret_key": "skey"})
    assert response.status_code == 200
    text = response.text
    assert "skey" not in text and response.json()["storage"]["secret_key_configured"] is True
    assert client.get("/api/admin/settings/storage").json()["storage"]["secret_id_configured"] is True
    # The persisted adapter sees the admin-managed values without exposing them.
    from app.services.storage import TencentCosStorage
    adapter = TencentCosStorage(client=object())
    assert adapter.bucket == "bucket-a" and adapter.region == "ap-shanghai" and adapter.secret_key == "skey"


def test_admin_can_upload_and_start_verified_history_bundle(client, make_user, tmp_path):
    make_user("admin", role="admin")
    target = make_user("target", role="planner")
    _login(client, "admin")
    files = {"payload/legacy/analysis/cache.json": b'{"years": {}}'}
    import hashlib
    manifest = {"schema_version": 1, "created_at": "2026-01-01T00:00:00Z", "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "kind": "analysis-cache"} for name, data in files.items()], "totals": {"files": 1, "bytes": len(next(iter(files.values())))}}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in files.items(): archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest))
    response = client.post("/api/admin/migration/bundles/import", files={"file": ("history.ptmigration.zip", stream.getvalue(), "application/zip")})
    assert response.status_code == 200, response.text
    task = response.json()["task"]; started = client.post(f"/api/admin/migration/tasks/{task['id']}/start", json={"owner_id": target.id})
    assert started.status_code == 200, started.text
    assert started.json()["task"]["status"] == "completed"


def test_admin_rejects_unverified_update_archive(client, make_user):
    make_user("admin", role="admin")
    _login(client, "admin")
    response = client.post("/api/admin/updates/upload", files={"file": ("not-update.zip", b"not a zip", "application/zip")})
    assert response.status_code == 400


def test_admin_can_export_complete_center_migration_bundle(client, make_user):
    make_user("admin", role="admin")
    _login(client, "admin")
    response = client.get("/api/admin/migration/center/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    import zipfile
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert "manifest.json" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "practical-tools-center-migration-v1"
        assert manifest["program_included"] is True

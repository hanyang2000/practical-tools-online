from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from app.analysis.parser import parse_file
from app.security import hash_password, verify_password
from app.services.storage import find_compatible_storage_file, resolve_storage_key, store_bytes
from app.services.storage import TencentCosStorage


def test_password_roundtrip():
    encoded = hash_password("a-secure-password")
    assert verify_password("a-secure-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_storage_rejects_traversal(tmp_path):
    class Settings:
        storage_root = tmp_path / "storage"
        cache_root = tmp_path / "cache"
        max_upload_bytes = 100
        def ensure_directories(self): self.storage_root.mkdir(parents=True, exist_ok=True)
    settings = Settings(); settings.ensure_directories()
    assert store_bytes(settings, "x/a.txt", b"ok").path.read_bytes() == b"ok"
    try: resolve_storage_key(settings, "../outside")
    except ValueError: pass
    else: raise AssertionError("path traversal was accepted")


def test_storage_accepts_legacy_windows_separator_and_migrated_data_layout(tmp_path):
    class Settings:
        data_root = tmp_path / "data"
        storage_root = data_root / "storage"
        cache_root = data_root / "cache"

    settings = Settings()
    key = r"screenshots\品牌\2026-08-26\capture.png"
    legacy = settings.data_root / "screenshots" / "品牌" / "2026-08-26" / "capture.png"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"migrated-image")
    assert resolve_storage_key(settings, key) == settings.storage_root / "screenshots" / "品牌" / "2026-08-26" / "capture.png"
    assert find_compatible_storage_file(settings, key, expected_size=len(b"migrated-image"), expected_sha256=__import__("hashlib").sha256(b"migrated-image").hexdigest()) == legacy


def test_parser_overall(tmp_path):
    path = tmp_path / "2026.xlsx"
    workbook = __import__("openpyxl").Workbook(); sheet = workbook.active; sheet.title = "整体数据"
    sheet.append(["日期", "活动", "新客", "新客", "老客", "老客"])
    sheet.append(["", "", "访客数", "点击人数", "访客数", "点击人数"])
    sheet.append(["2026-01-01", "日常", 10, 2, 5, 1]); workbook.save(path)
    parsed = parse_file(path)
    assert parsed["sheets"]["整体数据"]["fields"]["years"] == ["2026"]


def test_cos_adapter_can_be_mocked(monkeypatch):
    class FakeClient:
        def get_presigned_url(self, **kwargs):
            return f"https://cos.test/{kwargs['Key']}"
    monkeypatch.setenv("PRACTICAL_COS_BUCKET", "bucket")
    monkeypatch.setenv("PRACTICAL_COS_REGION", "ap-shanghai")
    storage = TencentCosStorage(client=FakeClient())
    assert storage.presign_put("screenshots/a.png").endswith("screenshots/a.png")
    assert storage.presign_get("screenshots/a.png").startswith("https://cos.test/")

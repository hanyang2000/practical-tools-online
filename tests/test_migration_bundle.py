from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from app.services.migration_bundle import inspect_bundle
from scripts.export_legacy_bundle import build_bundle


def _manifest(files: dict[str, bytes], *, kinds: dict[str, str] | None = None) -> dict:
    kinds = kinds or {}
    rows = [
        {
            "path": name,
            "kind": kinds.get(name, "screenshot"),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for name, data in files.items()
    ]
    return {
        "schema_version": 1,
        "created_at": "2026-08-31T00:00:00+00:00",
        "source_platform": "macOS",
        "files": rows,
        "totals": {"files": len(rows), "bytes": sum(len(value) for value in files.values())},
    }


def _write_bundle(path: Path, files: dict[str, bytes], *, manifest: dict | None = None) -> Path:
    value = manifest or _manifest(files)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(value, ensure_ascii=False))
    return path


def test_mac_bundle_contains_only_allowlisted_business_data(tmp_path: Path):
    legacy = tmp_path / "legacy"
    (legacy / "analysis" / "source").mkdir(parents=True)
    (legacy / "analysis" / "cache.json").write_text('{"years": {"2025": {}}}', encoding="utf-8")
    (legacy / "analysis" / "source" / "2025.xlsx").write_bytes(b"excel")
    (legacy / "db.sqlite3").write_bytes(b"sqlite")
    screenshots = tmp_path / "capture"
    (screenshots / "screenshots" / "brand" / "2026-08-31").mkdir(parents=True)
    (screenshots / "screenshots" / "brand" / "2026-08-31" / "10-00-00.png").write_bytes(b"png")
    (screenshots / "config.yaml").write_text("shops: []\n", encoding="utf-8")

    # Credential-bearing files are deliberately placed beside valid source
    # data.  The exporter must never traverse or package them.
    (screenshots / "browser_state").mkdir()
    (screenshots / "browser_state" / "state.json").write_text("TAOBAO_COOKIE", encoding="utf-8")
    (legacy / ".env").write_text("PRACTICAL_COS_SECRET_KEY=secret", encoding="utf-8")

    output = tmp_path / "history.ptmigration.zip"
    report = build_bundle(output, legacy, screenshots)
    manifest = inspect_bundle(output)

    assert report["files"] == 5
    assert manifest["totals"]["files"] == 5
    assert {item["kind"] for item in manifest["files"]} == {
        "analysis-cache", "analysis-source", "analysis-db", "capture-config", "screenshot"
    }
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        joined = "\n".join(names).lower()
        assert "browser_state" not in joined
        assert "state.json" not in joined
        assert ".env" not in joined
        assert all(".." not in Path(name).parts for name in names)
        assert b"TAOBAO_COOKIE" not in b"".join(
            archive.read(name) for name in names if not name.endswith("/")
        )


def test_mac_bundle_never_follows_source_symlinks(tmp_path: Path):
    legacy = tmp_path / "legacy"
    source = legacy / "analysis" / "source"
    source.mkdir(parents=True)
    outside = tmp_path / "private.xlsx"
    outside.write_bytes(b"PRIVATE-CONTENT")
    try:
        (source / "linked.xlsx").symlink_to(outside)
    except OSError:
        pytest.skip("当前文件系统不允许创建符号链接")
    (legacy / "analysis" / "cache.json").write_text("{}", encoding="utf-8")

    output = tmp_path / "history.ptmigration.zip"
    build_bundle(output, legacy, tmp_path / "screenshots")
    with zipfile.ZipFile(output) as archive:
        assert "payload/legacy/analysis/source/linked.xlsx" not in archive.namelist()
        assert b"PRIVATE-CONTENT" not in b"".join(archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../outside.png",
        "/absolute.png",
        "payload/browser_state/state.json",
        "payload/cookies.sqlite",
        "payload/.env",
        "payload/agent.json",
    ],
)
def test_bundle_inspection_rejects_unsafe_or_credential_paths(tmp_path: Path, unsafe_name: str):
    path = tmp_path / "unsafe.zip"
    files = {unsafe_name: b"secret"}
    _write_bundle(path, files)
    with pytest.raises(ValueError):
        inspect_bundle(path)


def test_bundle_inspection_rejects_symlink_member(tmp_path: Path):
    path = tmp_path / "symlink.zip"
    name = "payload/screenshot/screenshots/brand/2026-08-31/image.png"
    manifest = _manifest({name: b"target"})
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
        archive.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="符号链接"):
        inspect_bundle(path)


def test_bundle_inspection_rejects_tampering_extra_files_and_wrong_totals(tmp_path: Path):
    name = "payload/screenshot/screenshots/brand/2026-08-31/image.png"
    expected = _manifest({name: b"original"})

    tampered = tmp_path / "tampered.zip"
    _write_bundle(tampered, {name: b"modified"}, manifest=expected)
    with pytest.raises(ValueError, match="校验失败"):
        inspect_bundle(tampered)

    extra = tmp_path / "extra.zip"
    _write_bundle(extra, {name: b"original", "payload/extra.txt": b"extra"}, manifest=expected)
    with pytest.raises(ValueError, match="清单"):
        inspect_bundle(extra)

    wrong_total = tmp_path / "wrong-total.zip"
    expected["totals"]["files"] = 99
    _write_bundle(wrong_total, {name: b"original"}, manifest=expected)
    with pytest.raises(ValueError, match="文件数"):
        inspect_bundle(wrong_total)


def test_bundle_inspection_rejects_unknown_kind_or_path_mapping(tmp_path: Path):
    name = "payload/arbitrary/run.exe"
    value = _manifest({name: b"binary"}, kinds={name: "screenshot"})
    path = _write_bundle(tmp_path / "unknown.zip", {name: b"binary"}, manifest=value)
    with pytest.raises(ValueError, match="类型|路径"):
        inspect_bundle(path)


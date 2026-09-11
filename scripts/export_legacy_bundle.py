#!/usr/bin/env python3
"""Create a safe, portable .ptmigration.zip from the Mac desktop utility."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.services.migration_bundle import FORBIDDEN_PARTS


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(legacy_root: Path, screenshot_root: Path) -> list[tuple[Path, str, str]]:
    found: list[tuple[Path, str, str]] = []
    # Packaged Mac builds keep runtime data under Chinese-named directories.
    # Accept both the source checkout layout and the frozen app layout.
    data_root = legacy_root / "数据" if (legacy_root / "数据").is_dir() else legacy_root
    analysis = data_root / "analysis"
    for path in sorted(analysis.glob("cache.json")) if analysis.is_dir() else []:
        found.append((path, f"payload/legacy/analysis/{path.name}", "analysis-cache"))
    source = analysis / "source"
    if source.is_dir():
        for path in sorted(source.glob("*.xlsx")):
            if path.is_symlink() or not path.is_file():
                continue
            found.append((path, f"payload/legacy/analysis/source/{path.name}", "analysis-source"))
    old_db = data_root / "db.sqlite3"
    if old_db.is_file() and not old_db.is_symlink():
        found.append((old_db, "payload/legacy/db.sqlite3", "analysis-db"))
        # SQLite WAL mode keeps the newest committed rows beside the main DB.
        # Include both sidecar files so the extracted snapshot is current.
        for suffix in ("-wal", "-shm"):
            sidecar = data_root / f"db.sqlite3{suffix}"
            if sidecar.is_file() and not sidecar.is_symlink():
                found.append((sidecar, f"payload/legacy/db.sqlite3{suffix}", "analysis-db-sidecar"))
    exports = data_root / "exports"
    if exports.is_dir():
        for path in sorted(exports.glob("*.xlsx")):
            if path.is_file() and not path.is_symlink():
                found.append((path, f"payload/legacy/analysis/source/{path.name}", "analysis-source"))
    if (legacy_root / "截图数据").is_dir() and not screenshot_root.is_dir():
        screenshot_root = legacy_root / "截图数据"
    config = screenshot_root / "config.yaml"
    if config.is_file() and not config.is_symlink():
        found.append((config, "payload/screenshot/config.yaml", "capture-config"))
    images = screenshot_root / "screenshots"
    if images.is_dir():
        for path in sorted(images.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            rel = path.relative_to(images).as_posix()
            found.append((path, f"payload/screenshot/screenshots/{rel}", "screenshot"))
    return found


def build_bundle(output: Path, legacy_root: Path, screenshot_root: Path) -> dict:
    files = _files(legacy_root.expanduser().resolve(), screenshot_root.expanduser().resolve())
    if not files:
        raise ValueError("没有发现可迁移的分析数据、截图或配置")
    manifest_files = []
    total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, archive_name, kind in files:
            if any(part.lower() in FORBIDDEN_PARTS for part in Path(archive_name).parts):
                continue
            size = source.stat().st_size
            digest = _hash(source)
            archive.write(source, archive_name)
            manifest_files.append({"path": archive_name, "size": size, "sha256": digest, "kind": kind})
            total += size
        manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "files": manifest_files, "totals": {"files": len(manifest_files), "bytes": total}}
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"output": str(output), **manifest["totals"], "manifest": manifest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-data-root", type=Path, default=Path.home() / ".taobao_detail_extractor")
    parser.add_argument("--screenshot-root", type=Path, default=Path.cwd() / "screenshot")
    parser.add_argument("--output", type=Path, default=Path.home() / "Desktop" / f"PracticalToolsMigration-{datetime.now():%Y%m%d-%H%M%S}.ptmigration.zip")
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.output, args.legacy_data_root, args.screenshot_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

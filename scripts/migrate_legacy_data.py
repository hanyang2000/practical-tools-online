#!/usr/bin/env python3
"""Import the desktop utility's analysis/screenshots into the online center.

Default mode is a read-only dry run. ``--apply`` is required for writes. The
script never reads or copies screenshot/browser login state and is safe to run
more than once: analysis years are replaced atomically and screenshots use a
deterministic SHA-based capture id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Allow the documented `python scripts/migrate_legacy_data.py` invocation to
# work without requiring callers to set PYTHONPATH or install the package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from app.config import get_settings
from app.db import SessionLocal, create_schema_for_development
from app.models import CaptureSchedule, CaptureShop, PracticalAnalysisDaily, PracticalScreenshot
from app.services import analysis_db
from app.services.storage import TencentCosStorage, _backend_name
from app.services.migration_bundle import extract_verified


@dataclass
class MigrationPlan:
    cache_path: Path
    source_files: list[Path] = field(default_factory=list)
    screenshot_files: list[Path] = field(default_factory=list)
    shops: list[dict] = field(default_factory=list)
    schedule: dict = field(default_factory=dict)
    old_db: Path | None = None
    report: dict = field(default_factory=dict)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_files(root: Path) -> list[Path]:
    if not root.is_dir(): return []
    # Deliberately only include the screenshots subtree. browser_state is never
    # traversed, copied, hashed, or mentioned in the output.
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})


def build_plan(legacy_data_root: Path, screenshot_root: Path, old_db: Path | None = None) -> MigrationPlan:
    analysis_root = legacy_data_root / "analysis"
    cache_path = analysis_root / "cache.json"
    source_files = sorted((analysis_root / "source").glob("*.xlsx")) if (analysis_root / "source").is_dir() else []
    config_path = screenshot_root / "config.yaml"
    config = {}
    if config_path.is_file():
        with config_path.open(encoding="utf-8") as stream: config = yaml.safe_load(stream) or {}
    db_path = old_db or (legacy_data_root / "db.sqlite3")
    return MigrationPlan(cache_path=cache_path, source_files=source_files, screenshot_files=_image_files(screenshot_root / "screenshots"), shops=config.get("shops") or [], schedule=config.get("schedule") or {}, old_db=db_path if db_path.is_file() else None)


def summarize(plan: MigrationPlan) -> dict:
    cache = {}
    if plan.cache_path.is_file():
        try: cache = json.loads(plan.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError): cache = {}
    years = sorted((cache.get("years") or {}).keys()) if isinstance(cache, dict) else []
    daily = sum(len(((value.get("sheets") or {}).get("整体数据") or {}).get("data") or []) for value in (cache.get("years") or {}).values()) if isinstance(cache, dict) else 0
    images = [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)} for path in plan.screenshot_files]
    report = {"dry_run": True, "analysis": {"cache": str(plan.cache_path), "years": years, "daily_rows_in_cache": daily, "source_files": [{"path": str(p), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in plan.source_files]}, "screenshots": {"count": len(images), "bytes": sum(x["bytes"] for x in images), "files": images}, "shops": len(plan.shops), "schedule": plan.schedule, "old_sqlite": str(plan.old_db) if plan.old_db else None, "browser_state_migrated": False}
    plan.report = report; return report


def _copy_analysis(plan: MigrationPlan, settings, db, owner_id: str | None = None) -> dict:
    cache = json.loads(plan.cache_path.read_text(encoding="utf-8")) if plan.cache_path.is_file() else {}
    target = settings.data_root / "analysis" / "users" / str(owner_id) if owner_id else settings.data_root / "analysis"
    target.mkdir(parents=True, exist_ok=True)
    if plan.cache_path.is_file(): shutil.copy2(plan.cache_path, target / "cache.json")
    years = []
    for year, payload in (cache.get("years") or {}).items():
        rows = ((payload.get("sheets") or {}).get("整体数据") or {}).get("data") or []
        analysis_db.store_overall(db, rows, owner_id=owner_id); years.append(str(year))
    for source in plan.source_files:
        destination = target / "source" / source.name; destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)
    # Older desktop releases may contain daily rows in SQLite even when the
    # JSON cache has been removed. Import that normalized table as a fallback.
    if not years and plan.old_db and plan.old_db.is_file():
        with sqlite3.connect(plan.old_db) as old:
            try:
                columns = [row[1] for row in old.execute("PRAGMA table_info(analysis_daily)")]
                for values in old.execute("SELECT * FROM analysis_daily"):
                    row = dict(zip(columns, values)); db.add(PracticalAnalysisDaily(owner_id=owner_id, year=int(row["year"]), date=str(row["date"]), activity=str(row.get("activity") or ""), new_visitors=row.get("new_visitors"), new_clicks_people=row.get("new_clicks_people"), new_avg_stay=row.get("new_avg_stay"), new_pay_buyers=row.get("new_pay_buyers"), old_visitors=row.get("old_visitors"), old_clicks_people=row.get("old_clicks_people"), old_avg_stay=row.get("old_avg_stay"), old_pay_buyers=row.get("old_pay_buyers"))); years.append(str(row["year"]))
                db.commit()
            except sqlite3.Error: pass
    fixes = 0
    if plan.old_db and plan.old_db.is_file():
        with sqlite3.connect(plan.old_db) as old:
            try: old_rows = old.execute("SELECT year,date,field,delta FROM analysis_fix").fetchall()
            except sqlite3.Error: old_rows = []
        for year, date, field, delta in old_rows:
            analysis_db.apply_fix(db, int(year), str(date), str(field), float(delta), owner_id=owner_id); fixes += 1
    return {"years": sorted(years), "daily_rows": sum(1 for year in years for row in (((cache.get("years") or {}).get(year, {}).get("sheets") or {}).get("整体数据") or {}).get("data", []) if not row.get("is_summary") and row.get("date")), "fixes": fixes}


def _copy_screenshots(plan: MigrationPlan, settings, db, owner_id: str | None = None) -> dict:
    if not plan.screenshot_files: return {"imported": 0, "skipped": 0, "bytes": 0, "sha256": []}
    cos = TencentCosStorage() if _backend_name(settings) == "tencent_cos" else None
    imported = 0; skipped = 0; checksums = []
    for source in plan.screenshot_files:
        parts = source.relative_to(plan.screenshot_files[0].parents[2]).parts if len(plan.screenshot_files[0].parents) >= 2 else source.parts[-3:]
        # Normal layout is screenshots/{brand}/{date}/{file}; find the last
        # three components so arbitrary absolute roots are harmless.
        brand, date, filename = source.parts[-3:]
        digest = sha256(source); capture_id = f"legacy-{digest[:32]}"; key = f"screenshots/{brand}/{date}/{filename}"
        existing = db.query(PracticalScreenshot).filter(PracticalScreenshot.capture_id == capture_id, PracticalScreenshot.owner_id == owner_id).first()
        checksums.append({"path": str(source), "sha256": digest, "bytes": source.stat().st_size})
        if existing: skipped += 1; continue
        if cos:
            with source.open("rb") as stream:
                cos._client().put_object(Bucket=cos.bucket, Key=cos.object_key(key), Body=stream)
        else:
            target = settings.storage_root / key; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
        try: timestamp = datetime.fromisoformat(f"{date}T{Path(filename).stem.replace('-', ':')}").replace(tzinfo=timezone.utc)
        except ValueError: timestamp = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
        db.add(PracticalScreenshot(owner_id=owner_id, capture_id=capture_id, brand=brand, captured_at=timestamp, original_name=filename, storage_key=key, sha256=digest, file_size=source.stat().st_size))
        imported += 1
    db.commit(); return {"imported": imported, "skipped": skipped, "bytes": sum(x["bytes"] for x in checksums), "sha256": checksums}


def apply_plan(plan: MigrationPlan, owner_id: str | None = None) -> dict:
    settings = get_settings(); settings.ensure_directories()
    if settings.auto_create_tables: create_schema_for_development()
    db = SessionLocal()
    try:
        analysis = _copy_analysis(plan, settings, db, owner_id)
        screenshots = _copy_screenshots(plan, settings, db, owner_id)
        seen_shop_names = set()
        for shop in plan.shops:
            name, url = str(shop.get("name") or "").strip(), str(shop.get("url") or "").strip()
            if not name or not url: continue
            normalized_name = name.casefold()
            if normalized_name in seen_shop_names:
                continue
            seen_shop_names.add(normalized_name)
            existing = db.query(CaptureShop).filter(CaptureShop.name == name).first()
            if existing:
                # Shop names are globally unique. Re-runs must be idempotent;
                # normalize an older account-scoped row to global visibility.
                if owner_id is None: existing.owner_id = None
                existing.url = url; existing.enabled = bool(shop.get("enabled", True))
                continue
            db.add(CaptureShop(owner_id=owner_id, name=name, url=url, enabled=bool(shop.get("enabled", True))))
        if plan.schedule:
            db.add(CaptureSchedule(owner_id=owner_id, weekdays=plan.schedule.get("weekdays", []), hour=int(plan.schedule.get("hour", 9)), minute=int(plan.schedule.get("minute", 0)), enabled=False))
        db.commit(); report = summarize(plan); report.update({"dry_run": False, "analysis_applied": analysis, "screenshots_applied": screenshots, "browser_state_migrated": False}); return report
    finally: db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--legacy-data-root", type=Path, default=Path.home() / ".taobao_detail_extractor"); parser.add_argument("--screenshot-root", type=Path, default=Path.cwd() / "screenshot"); parser.add_argument("--bundle", type=Path, help="Mac 端生成的 .ptmigration.zip；可直接替代两个目录参数"); parser.add_argument("--old-db", type=Path); parser.add_argument("--owner-id", help="在线版 users.id；建议迁移到指定登录账号，避免数据留在无主空间"); parser.add_argument("--apply", action="store_true", help="确认写入在线版数据库和存储"); args = parser.parse_args()
    extracted = None
    try:
        if args.bundle:
            extracted, manifest = extract_verified(args.bundle.expanduser().resolve())
            legacy_root = extracted / "payload" / "legacy"
            screenshot_root = extracted / "payload" / "screenshot"
            old_db = legacy_root / "db.sqlite3"
        else:
            legacy_root = args.legacy_data_root.expanduser().resolve()
            screenshot_root = args.screenshot_root.expanduser().resolve()
            old_db = args.old_db.expanduser().resolve() if args.old_db else None
        plan = build_plan(legacy_root, screenshot_root, old_db)
        report = apply_plan(plan, args.owner_id) if args.apply else summarize(plan)
        if args.bundle: report["bundle"] = {"schema_version": manifest["schema_version"], "files": manifest["totals"]["files"], "bytes": manifest["totals"]["bytes"]}
        print(json.dumps(report, ensure_ascii=False, indent=2)); return 0
    finally:
        if extracted:
            shutil.rmtree(extracted, ignore_errors=True)


if __name__ == "__main__": raise SystemExit(main())

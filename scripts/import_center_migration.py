#!/usr/bin/env python3
"""Safely import a .ptcenter.zip into a newly prepared center."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.db import SessionLocal
from app.services.center_migration import import_center_migration_bundle, inspect_center_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="旧中心导出的 .ptcenter.zip")
    parser.add_argument("--allow-nonempty", action="store_true", help="允许合并到已有业务数据的中心")
    parser.add_argument("--overwrite-storage", action="store_true", help="允许覆盖内容不同的本地素材；必须同时指定 --allow-nonempty")
    parser.add_argument("--data-only", action="store_true", help="只导入截图、分析数据、分析文件和店铺配置；账号、Agent、调度、任务在新机重新创建")
    parser.add_argument("--inspect-only", action="store_true", help="只校验并显示清单，不写入")
    args = parser.parse_args()
    if args.overwrite_storage and not args.allow_nonempty:
        parser.error("--overwrite-storage 必须与 --allow-nonempty 一起使用")
    bundle = args.bundle.expanduser().resolve()
    manifest = inspect_center_bundle(bundle, verify_program=not args.data_only)
    if args.inspect_only:
        print(json.dumps({"ok": True, "format": manifest["format"], "source_app_version": manifest.get("source_app_version"), "program_included": manifest.get("program_included", False), "tables": manifest.get("tables", []), "totals": manifest.get("totals", {})}, ensure_ascii=False, indent=2))
        return 0
    with SessionLocal() as db:
        report = import_center_migration_bundle(bundle, db, get_settings(), allow_nonempty=args.allow_nonempty, overwrite_storage=args.overwrite_storage, data_only=args.data_only)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

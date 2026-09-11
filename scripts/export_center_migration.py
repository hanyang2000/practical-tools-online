#!/usr/bin/env python3
"""Export the current center program and durable data into one .ptcenter.zip."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.db import SessionLocal
from app.services.center_migration import build_center_migration_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="输出 .ptcenter.zip 路径")
    parser.add_argument("--program-root", type=Path, default=PROJECT_ROOT, help="中心程序根目录；默认使用当前项目")
    parser.add_argument("--no-storage", action="store_true", help="只导出程序和数据库，不包含本地素材")
    args = parser.parse_args()
    output = args.output or Path.cwd() / f"PracticalToolsCenterMigration-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.ptcenter.zip"
    settings = get_settings()
    with SessionLocal() as db:
        report = build_center_migration_bundle(db, settings, output, include_storage=not args.no_storage, program_root=args.program_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

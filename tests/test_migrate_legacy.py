from __future__ import annotations

import json

from openpyxl import Workbook

from scripts.migrate_legacy_data import build_plan, summarize


def test_migration_plan_is_read_only_and_excludes_browser_state(tmp_path):
    legacy = tmp_path / "legacy"; (legacy / "analysis" / "source").mkdir(parents=True)
    cache = {"years": {"2026": {"sheets": {"整体数据": {"data": [{"date": "2026-01-01", "is_summary": False}]}}}}, "order": ["2026"]}
    (legacy / "analysis" / "cache.json").write_text(json.dumps(cache), encoding="utf-8")
    workbook = Workbook(); workbook.save(legacy / "analysis" / "source" / "2026.xlsx")
    screenshot = tmp_path / "screenshot"; (screenshot / "screenshots" / "品牌" / "2026-01-01").mkdir(parents=True)
    (screenshot / "screenshots" / "品牌" / "2026-01-01" / "10-00-00.png").write_bytes(b"not-an-image-for-scan")
    # This file is intentionally outside the scanned screenshots tree and must
    # never appear in the plan/report.
    (screenshot / "browser_state").mkdir(); (screenshot / "browser_state" / "state.json").write_text("SECRET", encoding="utf-8")
    plan = build_plan(legacy, screenshot); report = summarize(plan)
    assert report["browser_state_migrated"] is False
    assert report["screenshots"]["count"] == 1
    assert "state.json" not in json.dumps(report)
    assert (legacy / "analysis" / "cache.json").read_text(encoding="utf-8") == json.dumps(cache)

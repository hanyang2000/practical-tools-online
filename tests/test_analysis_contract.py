from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = Path("/Users/hanyang/策划实用小工具")
REAL_DATA_ROOT = Path("/Users/hanyang/.taobao_detail_extractor/analysis/source")
PYTHON = Path("/Users/hanyang/主图一条龙服务协同版/协同版/.venv/bin/python")

# These panels intentionally changed their activity semantics: routine-grade
# rows are daily periods, and disjoint runs of the same activity are separate
# filter/calendar entries.  They therefore need a semantic baseline instead
# of byte-for-byte parity with the pre-change legacy service.
ACTIVITY_SEMANTICS_CHANGED = {
    "panel_overview",
    "panel_anomaly",
    "panel_blocks",
    "panel_calendar",
}


def _legacy_call(function_name: str, kwargs: dict) -> dict:
    script = f"""
import asyncio, inspect, json, os, tempfile
os.environ['TAOBAO_SCRAPER_DATA'] = tempfile.mkdtemp(prefix='legacy-analysis-contract-')
from analysis import api
from fastapi import UploadFile

async def main():
    paths = {[str(REAL_DATA_ROOT / '2025天猫旗舰店页面数据监测.xlsx'), str(REAL_DATA_ROOT / '2026天猫旗舰店页面数据监测.xlsx')]!r}
    for path_text in paths:
        stream = open(path_text, 'rb')
        try:
            await api.upload(UploadFile(filename=os.path.basename(path_text), file=stream))
        finally:
            stream.close()
    value = getattr(api, {function_name!r})(**{kwargs!r})
    if inspect.isawaitable(value):
        value = await value
    print(json.dumps(value, ensure_ascii=False, default=str, sort_keys=True))

asyncio.run(main())
"""
    completed = subprocess.run(
        [str(PYTHON), "-c", script],
        cwd=LEGACY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _upload_real_years(client):
    responses = []
    for year in (2025, 2026):
        path = REAL_DATA_ROOT / f"{year}天猫旗舰店页面数据监测.xlsx"
        assert path.is_file(), f"missing real fixture: {path}"
        with path.open("rb") as stream:
            response = client.post(
                "/api/analysis/upload",
                files={"file": (path.name, stream, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert response.json()["year"] == str(year)
        responses.append(response.json())
    return responses


def _assert_json_equal(actual, expected, path="$"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert set(actual) == set(expected), f"{path}: keys differ: actual-only={set(actual)-set(expected)}, expected-only={set(expected)-set(actual)}"
        for key in expected:
            _assert_json_equal(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{path}: list length {len(actual) if isinstance(actual, list) else type(actual)} != {len(expected)}"
        for index, value in enumerate(expected):
            _assert_json_equal(actual[index], value, f"{path}[{index}]")
    elif isinstance(expected, float) and isinstance(actual, (int, float)):
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), path
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def _assert_activity_semantics_baseline(payload):
    """Validate the new activity contract without freezing source-data values."""
    assert payload.get("ok") is True
    activities = payload.get("activities")
    assert isinstance(activities, list)

    ranges_by_activity = {}
    for item in activities:
        assert item.get("group")
        start = item.get("s") or item.get("start")
        end = item.get("e") or item.get("end")
        if not start or not end:
            continue
        group_key = (item.get("group"), item.get("grade") or "")
        ranges_by_activity.setdefault(group_key, []).append((start, end))
        # Overview/anomaly/blocks expose this explicit marker.  Calendar uses
        # the same grade but keeps its historical {start, end, waves} shape.
        if item.get("grade") == "常规" and "routine" in item:
            assert item["routine"] is True

    # Two disjoint runs of one activity must not be merged into an overlapping
    # range.  The focused unit test additionally covers the internal-gap case.
    for group_key, ranges in ranges_by_activity.items():
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            assert current[0] > previous[1], (
                f"{group_key} 活动区段重叠/被合并：{previous} -> {current}"
            )


def test_real_2025_2026_upload_status_preview_and_files(authenticated_client):
    client = authenticated_client
    _upload_real_years(client)
    status = client.get("/api/analysis/status")
    assert status.status_code == 200
    expected_status = _legacy_call("status", {})
    _assert_json_equal(status.json(), expected_status)

    preview = client.get("/api/analysis/preview")
    assert preview.status_code == 200
    expected_preview = _legacy_call("preview", {})
    # saved_source is deliberately environment-specific; all user-visible and
    # calculation fields must remain identical.
    actual_preview = preview.json()
    actual_preview.pop("saved_source", None)
    expected_preview.pop("saved_source", None)
    _assert_json_equal(actual_preview, expected_preview)

    files = client.get("/api/analysis/files")
    assert files.status_code == 200
    _assert_json_equal(files.json(), _legacy_call("list_files", {}))


@pytest.mark.parametrize(
    "path,function_name,kwargs",
    [
        ("/api/analysis/metrics", "metrics_api", {}),
        ("/api/analysis/panel/overview", "panel_overview", {"year": 2026, "dim": "YTD", "period": 1}),
        ("/api/analysis/panel/newold", "panel_newold", {"year": 2026, "dim": "YTD", "period": 1}),
        ("/api/analysis/panel/anomaly", "panel_anomaly", {"year": 2026, "dim": "YTD", "period": 1, "metric": "总访客数"}),
        ("/api/analysis/panel/blocks", "panel_blocks", {"year": 2026, "dim": "YTD", "period": 1, "sheet": "日常页面数据", "metric": "点击率", "segment": "整体", "min_days": 1}),
        ("/api/analysis/panel/blocks_yoy", "panel_blocks_yoy", {"year": 2026, "dim": "YTD", "period": 1, "sheet": "日常页面数据", "metric": "点击率", "segment": "整体"}),
        ("/api/analysis/panel/calendar", "panel_calendar", {"year": 2026, "dim": "YTD", "period": 1}),
        ("/api/analysis/panel/overall_all", "panel_overall_all", {"year": 2026, "dim": "YTD", "period": 1}),
    ],
)
@pytest.mark.parametrize("analysis_year", [2025, 2026])
def test_all_analysis_panels_match_legacy_real_data(authenticated_client, path, function_name, kwargs, analysis_year):
    client = authenticated_client
    _upload_real_years(client)
    kwargs = {**kwargs, **({"year": analysis_year} if "year" in kwargs else {})}
    response = client.get(path, params=kwargs)
    assert response.status_code == 200, response.text
    if function_name in ACTIVITY_SEMANTICS_CHANGED:
        _assert_activity_semantics_baseline(response.json())
        return
    expected = _legacy_call(function_name, kwargs)
    _assert_json_equal(response.json(), expected)


def test_anomaly_fix_update_list_and_delete(authenticated_client):
    client = authenticated_client
    _upload_real_years(client)
    body = {"date": "2026-01-02", "field": "访客数", "delta": 12.5}
    created = client.post("/api/analysis/panel/anomaly/fix", json=body)
    assert created.status_code == 200 and created.json()["ok"] is True
    updated = client.post("/api/analysis/panel/anomaly/fix", json={**body, "delta": -2})
    assert updated.json()["delta"] == -2
    fixes = client.get("/api/analysis/panel/anomaly/fixes").json()["fixes"]
    assert fixes == [{"year": 2026, "date": "2026-01-02", "field": "访客数", "delta": -2.0}]
    removed = client.delete("/api/analysis/panel/anomaly/fix", params={"date": "2026-01-02", "field": "访客数"})
    assert removed.json() == {"ok": True}
    assert client.get("/api/analysis/panel/anomaly/fixes").json()["fixes"] == []


def test_delete_year_removes_daily_cache_and_fixes(authenticated_client):
    client = authenticated_client
    _upload_real_years(client)
    client.post("/api/analysis/panel/anomaly/fix", json={"date": "2025-01-02", "field": "点击人数", "delta": 1})
    deleted = client.delete("/api/analysis/file", params={"year": 2025})
    assert deleted.status_code == 200 and deleted.json() == {"ok": True, "year": "2025"}
    assert [row["year"] for row in client.get("/api/analysis/files").json()["files"]] == ["2026"]
    assert client.get("/api/analysis/panel/anomaly/fixes").json()["fixes"] == []


def test_analysis_files_are_isolated_by_user(client, make_user):
    make_user("user1")
    make_user("user2")
    client.post("/api/auth/login", json={"username": "user1", "password": "correct-password"})
    path = REAL_DATA_ROOT / "2025天猫旗舰店页面数据监测.xlsx"
    with path.open("rb") as stream:
        assert client.post("/api/analysis/upload", files={"file": (path.name, stream)}).status_code == 200
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "user2", "password": "correct-password"})
    assert client.get("/api/analysis/files").json()["files"] == []

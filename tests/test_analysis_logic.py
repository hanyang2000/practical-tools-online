from __future__ import annotations

from app.analysis import metrics


def _daily_row(date: str, activity: str):
    return {
        "date": date,
        "activity": activity,
        "new_visitors": 10,
        "old_visitors": 5,
        "new_clicks_people": 2,
        "old_clicks_people": 1,
        "new_pay_buyers": 1,
        "old_pay_buyers": 0,
        "new_avg_stay": 10,
        "old_avg_stay": 12,
    }


def _block(name: str, dates: list[str], rows: list[dict] | None = None):
    return {
        "name": name,
        "title": name,
        "wave_group": metrics.parser._activity_group_key(name),
        "dates": dates,
        "rows": rows or [{"板块": "A", "子模块": None}],
    }


def test_routine_grade_is_always_daily(monkeypatch):
    rows = [
        _daily_row("2026-07-01", "超级88（常规）"),
        _daily_row("2026-07-02", "超级88（S-）"),
    ]
    monkeypatch.setattr(metrics.adb, "rows_in_range", lambda year, start, end: rows)
    monkeypatch.setattr(metrics.adb, "get_fixes_map", lambda: {})

    series = metrics.daily_series(2026, "2026-07-01", "2026-07-02")

    assert [row["is_routine"] for row in series] == [True, False]


def test_same_activity_is_split_into_independent_discontinuous_options():
    series = [
        {"date": "2026-07-07", "activity": "超级88（S-）", "is_routine": False},
        {"date": "2026-07-08", "activity": "超级88（S-）", "is_routine": False},
        {"date": "2026-07-20", "activity": "超级88（S-）", "is_routine": False},
    ]

    activities, keymap = metrics._aggregate_activities(series)

    assert keymap == {}
    assert [(a["group"], a["date_range"]) for a in activities] == [
        ("超级88", "07.07~07.08"),
        ("超级88", "07.20~07.20"),
    ]
    assert activities[0]["key"] != activities[1]["key"]
    assert [x["date"] for x in metrics._filter_by_activity(series, activities, keymap, activities[1]["key"])] == ["2026-07-20"]


def test_block_activity_options_apply_routine_grade_and_split_segments():
    blocks = [
        _block("超级88（常规）", ["2026-07-01", "2026-07-02"]),
        _block("超级88（S-）", ["2026-07-07", "2026-07-08"]),
        _block("超级88（S-）", ["2026-07-20"]),
    ]

    activities, _ = metrics.activity_options_from_blocks(blocks)

    assert [(a["group"], a["grade"], a.get("routine", False), a["date_range"]) for a in activities] == [
        ("常规活动", "常规", True, "07.01~07.02"),
        ("超级88", "S-", False, "07.07~07.08"),
        ("超级88", "S-", False, "07.20~07.20"),
    ]


def test_block_order_inserts_activity_only_module_at_global_position():
    blocks = [
        _block("常规活动", ["2026-07-01"], [
            {"板块": "A", "子模块": None},
            {"板块": "B", "子模块": None},
            {"板块": "C", "子模块": None},
        ]),
        _block("超级88（S-）", ["2026-07-07"], [
            {"板块": "A", "子模块": None},
            {"板块": "X", "子模块": None},
            {"板块": "B", "子模块": None},
            {"板块": "C", "子模块": None},
        ]),
    ]

    assert metrics.block_source_order(blocks) == ["A", "X", "B", "C"]

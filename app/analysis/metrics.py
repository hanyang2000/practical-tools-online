"""核心 4 指标计算（用户给定公式，2026-08-20）。

- 总访客数   = 周期内新客访客数之和 + 老客访客数之和
- 点击率     = 周期内（新客点击人数 + 老客点击人数）之和 ÷ 总访客数
- 平均停留时长 = 周期内新老客平均停留时长的算术平均（忽略空值）
- 引导支付转化率 = 周期内（新客引导支付买家数 + 老客引导支付买家数）之和 ÷ 总访客数

空值处理：页面数据里的空值表示该天尚未到来（未来日期），计算时忽略，不参与求和/平均。
"""
import calendar
import datetime
import time

from app.analysis import legacy_db as adb
from app.analysis import parser  # 复用活动名归组（_activity_group_key）

METRIC_KEYS = ("总访客数", "点击率", "平均停留时长", "引导支付转化率")
ROUTINE_GROUPS = ("常规", "常规活动", "日常")   # 常规（非活动）日/块的归组名
_MONTHLY_CACHE: dict[tuple[str, tuple[int, ...]], tuple[float, dict]] = {}
_MONTHLY_CACHE_TTL = 60.0


def clear_cache() -> None:
    _MONTHLY_CACHE.clear()


def compute_metrics(year: int, start: str, end: str) -> dict:
    rows = adb.rows_in_range(year, start, end)
    total_visitors = 0.0
    clicks_people = 0.0
    pay_buyers = 0.0
    stays: list = []
    for r in rows:
        for key, val in (("new_visitors", r["new_visitors"]), ("old_visitors", r["old_visitors"])):
            if val is not None:
                total_visitors += val
        for key, val in (("new_clicks_people", r["new_clicks_people"]),
                         ("old_clicks_people", r["old_clicks_people"])):
            if val is not None:
                clicks_people += val
        for key, val in (("new_pay_buyers", r["new_pay_buyers"]),
                         ("old_pay_buyers", r["old_pay_buyers"])):
            if val is not None:
                pay_buyers += val
        for key, val in (("new_avg_stay", r["new_avg_stay"]), ("old_avg_stay", r["old_avg_stay"])):
            if val is not None:
                stays.append(val)
    return {
        "year": year,
        "days": len(rows),
        "总访客数": total_visitors if rows else None,
        "点击率": _cap01(clicks_people / total_visitors) if total_visitors else None,
        "平均停留时长": (sum(stays) / len(stays)) if stays else None,
        "引导支付转化率": _cap01(pay_buyers / total_visitors) if total_visitors else None,
    }


def month_metrics(year: int, month: int) -> dict:
    """某年某月的 4 指标（忽略空值）。"""
    last = calendar.monthrange(year, month)[1]
    return compute_metrics(year, f"{year:04d}-{month:02d}-01",
                           f"{year:04d}-{month:02d}-{last:02d}")


def full_year_metrics(year: int) -> dict:
    return compute_metrics(year, f"{year:04d}-01-01", f"{year:04d}-12-31")


def monthly_compare() -> dict:
    """所有已入库年度的逐月 + 全年指标，供月度对比面板。"""
    years = adb.available_years()
    key = (str(adb.owner_id()), tuple(years))
    cached = _MONTHLY_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _MONTHLY_CACHE_TTL:
        return cached[1]
    by_month = {}
    year_total = {}
    for y in years:
        months = {}
        for m in range(1, 13):
            mw = month_metrics(y, m)
            months[str(m)] = {k: mw[k] for k in METRIC_KEYS}
        by_month[str(y)] = months
        year_total[str(y)] = {k: full_year_metrics(y)[k] for k in METRIC_KEYS}
    result = {"years": years, "by_month": by_month, "year_total": year_total}
    _MONTHLY_CACHE[key] = (time.monotonic(), result)
    return result


# ---------------- 面板：每日序列 / 周期聚合 ----------------

def _add(a, b):
    """空值安全的求和。"""
    a = a if a is not None else 0
    b = b if b is not None else 0
    return a + b


def _cap01(x):
    """比率类指标（点击率/支付转化率）封顶 100%：源数据异常时避免出现 >100% 的不合理值。"""
    return min(x, 1.0) if x is not None else None


def _is_routine_activity(name: str, title: str = "") -> bool:
    """只要活动等级是「常规」，就归入日常期；不再只看固定活动名称。"""
    value = (name or "").strip()
    if not value or value in ROUTINE_GROUPS or value.startswith(("常规活动", "日常")):
        return True
    if parser._activity_grade(value) == "常规":
        return True
    return bool(title and parser._block_grade(title, value) == "常规")


def _is_routine_block(block: dict) -> bool:
    """判断板块块是否为日常块，兼容等级写在块标题或活动名括号内。"""
    name = block.get("name") or block.get("wave_group") or ""
    return _is_routine_activity(name, block.get("title") or "")


def resolve_period_range(dim: str, period: int, year: int):
    """维度 + 周期 → 起止日期（整周期，未来空值自动忽略）：
    MTD=指定月份、QTD=指定季度、HTD=指定半年、YTD=全年。"""
    if dim == "MTD":
        m = int(period)
        last = calendar.monthrange(year, m)[1]
        return f"{year:04d}-{m:02d}-01", f"{year:04d}-{m:02d}-{last:02d}"
    if dim == "QTD":
        q = int(period)
        em = q * 3
        sm = em - 2
        last = calendar.monthrange(year, em)[1]
        return f"{year:04d}-{sm:02d}-01", f"{year:04d}-{em:02d}-{last:02d}"
    if dim == "HTD":
        h = int(period)  # 1=上半年 2=下半年
        em = 6 if h == 1 else 12
        sm = 1 if h == 1 else 7
        last = calendar.monthrange(year, em)[1]
        return f"{year:04d}-{sm:02d}-01", f"{year:04d}-{em:02d}-{last:02d}"
    # YTD → 全年
    return f"{year:04d}-01-01", f"{year:04d}-12-31"


def newold(year: int, start: str, end: str, activity: str = None) -> dict:
    """新老客对比：分别聚合新客/老客的核心指标（访客用日均，避免活动期/日常期时长不公平）+ 转化漏斗。"""
    rows = adb.rows_in_range(year, start, end)
    if activity:
        series_all = daily_series(year, start, end)
        activities, keymap = _aggregate_activities(series_all)
        selected_dates = {x["date"] for x in _filter_by_activity(series_all, activities, keymap, activity)}
        rows = [r for r in rows if r["date"] in selected_dates]
    n_vis = o_vis = 0
    n_clicks = o_clicks = 0
    n_pay = o_pay = 0
    n_stays: list = []
    o_stays: list = []
    days = 0  # 有访客数据的天数
    for r in rows:
        if r["new_visitors"] is not None or r["old_visitors"] is not None:
            days += 1
        if r["new_visitors"] is not None:
            n_vis += r["new_visitors"]
        if r["old_visitors"] is not None:
            o_vis += r["old_visitors"]
        if r["new_clicks_people"] is not None:
            n_clicks += r["new_clicks_people"]
        if r["old_clicks_people"] is not None:
            o_clicks += r["old_clicks_people"]
        if r["new_pay_buyers"] is not None:
            n_pay += r["new_pay_buyers"]
        if r["old_pay_buyers"] is not None:
            o_pay += r["old_pay_buyers"]
        if r["new_avg_stay"] is not None:
            n_stays.append(r["new_avg_stay"])
        if r["old_avg_stay"] is not None:
            o_stays.append(r["old_avg_stay"])

    def agg(vis, clicks, pay, stays):
        return {
            "日均访客数": (vis / days) if (vis and days) else None,
            "点击率": _cap01(clicks / vis) if vis else None,
            "平均停留时长": (sum(stays) / len(stays)) if stays else None,
            "支付转化率": _cap01(pay / vis) if vis else None,
        }
    return {
        "year": year, "start": start, "end": end, "activity": activity, "days": days,
        "新客": agg(n_vis, n_clicks, n_pay, n_stays),
        "老客": agg(o_vis, o_clicks, o_pay, o_stays),
        "新客漏斗": {"访客": n_vis, "点击": n_clicks, "支付": n_pay},
        "老客漏斗": {"访客": o_vis, "点击": o_clicks, "支付": o_pay},
    }


def anomaly(year: int, start: str, end: str, metric: str = "总访客数",
            activity: str = None) -> dict:
    """趋势异常检测：Z-Score(|z|>2) + 环比偏离(>20%) 标记异常点。"""
    series_all = daily_series(year, start, end)
    activities, _keymap = _aggregate_activities(series_all)
    series = _filter_by_activity(series_all, activities, _keymap, activity)
    points = [(x["date"], x[metric], x["activity"]) for x in series if x[metric] is not None]
    if not points:
        return {"ok": False, "error": "无数据", "activities": activities}
    vals = [v for _, v, _ in points]
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    anomalies = []
    for i, (d, v, act) in enumerate(points):
        z = (v - mean) / std if std > 0 else 0
        reasons = []
        if abs(z) > 2:
            direction = "高于" if z > 0 else "低于"
            if mean and mean > 0:
                reasons.append(f"明显{direction}平时平均水平（约为平时平均的 {v / mean:.1f} 倍）")
            else:
                reasons.append(f"明显{direction}平时平均水平")
        if i > 0 and points[i-1][1]:
            prev = points[i-1][1]
            delta = (v - prev) / prev if prev else 0
            if abs(delta) > 0.2:
                if delta > 0:
                    reasons.append(f"较前一天上升 {delta * 100:.0f}%")
                else:
                    reasons.append(f"较前一天下降 {abs(delta) * 100:.0f}%")
        if reasons:
            anomalies.append({"date": d, "value": v,
                              "activity": (act or "").replace("\n", " "),
                              "group": parser._activity_group_key(act) if act else "",
                              "reasons": reasons, "z": round(z, 2)})
    return {
        "ok": True, "year": year, "start": start, "end": end,
        "metric": metric, "activity": activity,
        "mean": round(mean, 4), "std": round(std, 4),
        "trend": [{"date": d, "value": v} for d, v, _ in points],
        "anomalies": anomalies,
        "activities": activities,
    }


def daily_series(year: int, start: str, end: str) -> list:
    """每日核心 4 指标 + 活动/日常标记 + 原始分量（供周期聚合）。

    点击率/支付转化率 = 分量相除（用户表格口径），封顶 100% 兜底源表笔误。
    """
    out = []
    fixes = adb.get_fixes_map()
    for r in adb.rows_in_range(year, start, end):
        act = r["activity"] or ""
        nv = r["new_visitors"]
        ov = r["old_visitors"]
        nc = r["new_clicks_people"]
        oc = r["old_clicks_people"]
        # 应用增减修正（访客数/点击人数的增量，按比例分配到新老客，结果取整）
        vis_delta = fixes.get((r["date"], "访客数"))
        if vis_delta and (nv is not None or ov is not None):
            total = (nv or 0) + (ov or 0)
            s = (total + vis_delta) / total if total else 1
            if nv is not None:
                nv = nv * s
            if ov is not None:
                ov = ov * s
        click_delta = fixes.get((r["date"], "点击人数"))
        if click_delta and (nc is not None or oc is not None):
            total = (nc or 0) + (oc or 0)
            s = (total + click_delta) / total if total else 1
            if nc is not None:
                nc = nc * s
            if oc is not None:
                oc = oc * s
        if nv is None and ov is None:
            # 该日无数据（未来日期未到等）→ 全部指标 None，趋势图断点而非 0
            out.append({"date": r["date"], "activity": act,
                        "is_routine": _is_routine_activity(act),
                        "总访客数": None, "点击率": None,
                        "平均停留时长": None, "支付转化率": None,
                        "_tv": None, "_cp": None, "_pb": None, "_stays": []})
            continue
        tv = _add(nv, ov)
        cp = _add(nc, oc)
        pb = _add(r["new_pay_buyers"], r["old_pay_buyers"])
        stays = [x for x in (r["new_avg_stay"], r["old_avg_stay"]) if x is not None]
        out.append({
            "date": r["date"],
            "activity": act,
            "is_routine": _is_routine_activity(act),
            "总访客数": tv,
            "点击率": _cap01(cp / tv) if tv else None,
            "平均停留时长": sum(stays) / len(stays) if stays else None,
            "支付转化率": _cap01(pb / tv) if tv else None,
            "_tv": tv, "_cp": cp, "_pb": pb, "_stays": stays,
        })
    return out


def _agg(items: list) -> dict:
    """对一组日数据求周期聚合：总量相除的比率用分量和，停留取均值。"""
    tv = sum(x["_tv"] for x in items if x["_tv"])
    cp = sum(x["_cp"] for x in items if x["_cp"])
    pb = sum(x["_pb"] for x in items if x["_pb"])
    stays = [s for x in items for s in x["_stays"]]
    return {
        "总访客数": tv if items else None,
        "点击率": _cap01(cp / tv) if tv else None,
        "平均停留时长": sum(stays) / len(stays) if stays else None,
        "支付转化率": _cap01(pb / tv) if tv else None,
    }


def _daily_avg_visitors(items: list):
    """日均访客数（取整）：累计访客 / 有数据天数。"""
    days = len([x for x in items if x["_tv"] is not None])
    tv = sum(x["_tv"] for x in items if x["_tv"])
    return tv / days if days else None


def _routine_segments(dates) -> list:
    """把常规日列表按连续性切成段。dates 升序去重后，相邻日距 >1 天即断开。

    返回 [{"s": start, "e": end}, ...]，s/e 为 'YYYY-MM-DD'。
    常规日本身就是活动之间的空档，散落全年，不能合并显示成一个跨活动期的长区间。
    """
    segs = []
    prev = None
    for d in sorted(set(dates)):
        if prev is None or (datetime.date.fromisoformat(d) - datetime.date.fromisoformat(prev)).days > 1:
            segs.append({"s": d, "e": d})
        else:
            segs[-1]["e"] = d
        prev = d
    return segs


def _activity_sort_key(a: dict) -> tuple:
    """活动列表按时间排序的统一键：取段起点（常规段 s 或 date_range 前缀），
    归一成 MM.DD 再比较，避免完整日期(2026-01-01)与 MM.DD(01.04)混排错序。"""
    d = a.get("s") or (a["date_range"].split("~")[0] if a["date_range"] else "")
    if "-" in d:
        d = d[5:].replace("-", ".")
    return (d, a["group"])


_ACTIVITY_SEGMENT_SEPARATOR = "::"


def _activity_segment_key(base_key: str, start: str, end: str, count: int) -> str:
    """同名活动存在多个不连续区段时，为每段生成独立的内部筛选键。"""
    if count <= 1:
        return base_key
    return f"{base_key}{_ACTIVITY_SEGMENT_SEPARATOR}{start}~{end}"


def _split_activity_key(key: str):
    """拆出活动基础键和可选的完整日期区段。"""
    if _ACTIVITY_SEGMENT_SEPARATOR not in (key or ""):
        return key, None, None
    base, date_range = key.rsplit(_ACTIVITY_SEGMENT_SEPARATOR, 1)
    if "~" not in date_range:
        return key, None, None
    start, end = date_range.split("~", 1)
    return base, start, end


def _activity_options_from_groups(gd: dict, gbase: dict, ggrade: dict) -> list:
    """把活动键拆成连续时间段，避免同名活动跨空档合并。"""
    activities = []
    for base_key, dates in gd.items():
        segments = _routine_segments(dates)
        for seg in segments:
            start, end = seg["s"], seg["e"]
            activities.append({
                "group": gbase[base_key],
                "key": _activity_segment_key(base_key, start, end, len(segments)),
                "grade": ggrade[base_key],
                "date_range": f"{start[5:].replace('-', '.')}~{end[5:].replace('-', '.')}",
                "s": start,
                "e": end,
            })
    return activities


def _most_common_grade(grades: list) -> str:
    """取一组成员里出现最多的等级（S+/S/S-/常规…），空则返回空串。"""
    from collections import Counter
    c = Counter(g for g in grades if g)
    return c.most_common(1)[0][0] if c else ""


# ---------------- 整体数据 · 全维度聚合（总览「查看更多」） ----------------
OVERALL_METRICS = (
    "浏览量", "访客数", "点击次数", "点击人数", "点击率", "跳失率", "平均停留时长(秒)",
    "引导下单金额", "引导下单买家数", "引导下单转化率",
    "引导支付金额", "引导支付买家数", "引导支付转化率",
    "引导商品详情次数", "引导商品详情人数", "引导加购人数", "引导加购件数",
)
# 计数/金额类：周期内求和（整体 = 新客 + 老客）
_OVERALL_COUNT = {"浏览量", "访客数", "点击次数", "点击人数", "引导下单金额", "引导下单买家数",
                  "引导支付金额", "引导支付买家数", "引导商品详情次数", "引导商品详情人数",
                  "引导加购人数", "引导加购件数"}
# 比率类：用分量相除（与核心指标口径一致，封顶 100%）
_OVERALL_RATIO = {
    "点击率": ("点击人数", "访客数"),
    "引导下单转化率": ("引导下单买家数", "访客数"),
    "引导支付转化率": ("引导支付买家数", "访客数"),
}


def _has_overall_data(r) -> bool:
    """判断整体数据日行是否有实际数据（未来未更新日 segments 为空）。"""
    for grp in ("新客", "老客"):
        for v in ((r.get("segments", {}).get(grp) or {})).values():
            if v is not None:
                return True
    return False


def _overall_type(m: str) -> str:
    if m in ("引导下单金额", "引导支付金额"):
        return "money"
    if m in ("点击率", "跳失率", "引导下单转化率", "引导支付转化率"):
        return "rate"
    if m == "平均停留时长(秒)":
        return "stay"
    return "count"


def overall_all(data, start: str, end: str):
    """从「整体数据」日行聚合全维度指标（新客/老客/整体 三列）。

    计数/金额类求和；点击率/下单转化/支付转化用分量相除；跳失率、停留时长用均值。
    返回 [{metric, type, 新客, 老客, 整体}]，无数据返回 None。
    """
    rows = [r for r in data
            if not r.get("is_summary") and r.get("date") and start <= r["date"] <= end
            and _has_overall_data(r)]
    if not rows:
        return None
    acc = {"新客": {}, "老客": {}}
    stays = {"新客": [], "老客": []}
    bounce = {"新客": [], "老客": []}
    for r in rows:
        seg = r.get("segments", {})
        for grp in ("新客", "老客"):
            s = seg.get(grp, {})
            a = acc[grp]
            for m in _OVERALL_COUNT:
                a[m] = a.get(m, 0.0) + (s.get(m) or 0)
            if s.get("平均停留时长(秒)") is not None:
                stays[grp].append(s["平均停留时长(秒)"])
            if s.get("跳失率") is not None:
                bounce[grp].append(s["跳失率"])

    def _val(m, grp):
        if grp == "整体":
            base = {k: acc["新客"].get(k, 0) + acc["老客"].get(k, 0) for k in _OVERALL_COUNT}
            st = stays["新客"] + stays["老客"]
            bnc = bounce["新客"] + bounce["老客"]
        else:
            base = acc[grp]
            st = stays[grp]
            bnc = bounce[grp]
        if m in _OVERALL_RATIO:
            num_k, den_k = _OVERALL_RATIO[m]
            den = base.get(den_k)
            return min(base.get(num_k, 0) / den, 1.0) if den else None
        if m == "跳失率":
            return sum(bnc) / len(bnc) if bnc else None
        if m == "平均停留时长(秒)":
            return sum(st) / len(st) if st else None
        return base.get(m)

    return [{"metric": m, "type": _overall_type(m),
             "新客": _val(m, "新客"), "老客": _val(m, "老客"), "整体": _val(m, "整体")}
            for m in OVERALL_METRICS]


def _daily_overall(m: str, n: dict, o: dict):
    """单日「整体」值（新老客合并）：计数/金额求和；比率分量相除；跳失/停留均值。"""
    if m in _OVERALL_RATIO:
        num_k, den_k = _OVERALL_RATIO[m]
        den = (n.get(den_k) or 0) + (o.get(den_k) or 0)
        num = (n.get(num_k) or 0) + (o.get(num_k) or 0)
        return min(num / den, 1.0) if den else None
    if m == "跳失率":
        vs = [x for x in (n.get("跳失率"), o.get("跳失率")) if x is not None]
        return sum(vs) / len(vs) if vs else None
    if m == "平均停留时长(秒)":
        vs = [x for x in (n.get(m), o.get(m)) if x is not None]
        return sum(vs) / len(vs) if vs else None
    if n.get(m) is None and o.get(m) is None:
        return None
    return (n.get(m) or 0) + (o.get(m) or 0)


def overall_trend(data_cur, start, end, data_prev, ps, pe):
    """全维度每日趋势（整体口径）：今年 + 去年（去年按同月同日对齐到今年日期）。"""
    def _build(data, s, e):
        rows = sorted((r for r in data if not r.get("is_summary") and r.get("date")
                       and s <= r["date"] <= e and _has_overall_data(r)), key=lambda r: r["date"])
        dates = [r["date"] for r in rows]
        series = {}
        for r in rows:
            seg = r.get("segments", {})
            n = seg.get("新客", {})
            o = seg.get("老客", {})
            for m in OVERALL_METRICS:
                series.setdefault(m, []).append(_daily_overall(m, n, o))
        return dates, series

    cdates, cser = _build(data_cur, start, end)
    pdates, pser = _build(data_prev, ps, pe) if data_prev else ([], {})

    def _align(m):
        # 按位置对齐：对比期第 N 天 ↔ 本期第 N 天（支持不同长度周期），短于本期补 None。
        p = pser.get(m, [])
        return [p[i] if i < len(p) else None for i in range(len(cdates))]

    return [{"metric": m, "type": _overall_type(m),
             "dates": cdates, "cur": cser.get(m, []),
             "prev": _align(m) if pdates else []}
            for m in OVERALL_METRICS]


def block_means(blocks, start: str, end: str, metric: str, segment: str) -> dict:
    """板块深度：每个板块在 [start,end] 内的指标均值（子模块为 整体/None 的行）。"""
    per = {}
    for b in blocks:
        for row in b.get("rows") or []:
            if row.get("子模块") not in (None, "整体"):
                continue
            name = row.get("板块")
            if not name:
                continue
            daily = row.get("每日") or {}
            bucket = per.setdefault(name, {})
            for date, segs in daily.items():
                if not (start <= date <= end):
                    continue
                if segment == "整体":
                    vals = [(segs.get(s) or {}).get(metric) for s in ("新客", "老客")]
                    vals = [v for v in vals if isinstance(v, (int, float))]
                    v = (sum(vals) / len(vals)) if vals else None
                else:
                    v = (segs.get(segment) or {}).get(metric)
                    v = v if isinstance(v, (int, float)) else None
                if v is not None:
                    bucket[date] = v
    out = {}
    for n, bucket in per.items():
        vals = [v for v in bucket.values() if v is not None]
        if vals:
            out[n] = sum(vals) / len(vals)
    return out


def block_source_order(blocks: list) -> list:
    """返回全量板块的稳定模块顺序，供热力图和对比表共同使用。

    每个活动块都提供一条局部的上下顺序。把这些局部顺序合并成稳定拓扑序，
    可以把只在某一档活动出现的新板块插入正确位置，而不是简单地因为它首次
    出现在后一个活动块，就被错误地排到整张图最底部。
    """
    first_seen = {}
    edges = {}
    indegree = {}
    next_seen = 0
    for b in blocks:
        sequence = []
        for row in b.get("rows") or []:
            if row.get("子模块") not in (None, "整体"):
                continue
            n = row.get("板块")
            if not n or n in sequence:
                continue
            sequence.append(n)
            if n not in first_seen:
                first_seen[n] = next_seen
                next_seen += 1
                edges[n] = set()
                indegree[n] = 0
        for left, right in zip(sequence, sequence[1:]):
            if right not in edges[left]:
                edges[left].add(right)
                indegree[right] += 1

    ready = [n for n in first_seen if indegree[n] == 0]
    ready.sort(key=first_seen.__getitem__)
    order = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in sorted(edges[current], key=first_seen.__getitem__):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=first_seen.__getitem__)

    # 异常源表若形成环，仍保证所有板块出现且顺序稳定，不让热力图接口失败。
    if len(order) < len(first_seen):
        order.extend(n for n in first_seen if n not in set(order))
    return order


def blocks_yoy(cur_blocks, prev_blocks, start, end, ps, pe, metric, segment,
               order: list = None) -> list:
    """板块对比：本期 vs 对比期同名板块的均值差距。

    order：板块页面实际顺序（block_source_order）。非 None 时按页面顺序排
    （不再按变化幅度）；None 时退回按变化幅度降序（旧行为）。
    """
    cur_m = block_means(cur_blocks, start, end, metric, segment)
    prev_m = block_means(prev_blocks, ps, pe, metric, segment)
    rows = []
    for n in set(cur_m) & set(prev_m):
        c, p = cur_m[n], prev_m[n]
        diff = c - p
        pct = (diff / p * 100) if p else None
        rows.append({"板块": n, "本期": c, "对比期": p, "差距": diff, "变化": pct})
    if order:
        pos = {n: i for i, n in enumerate(order)}
        rows.sort(key=lambda x: pos.get(x["板块"], len(order)))
    else:
        rows.sort(key=lambda x: -(abs(x["变化"]) if x["变化"] is not None else 0))
    return rows


def activity_calendar(act_blocks, daily_blocks, start, end, series=None):
    """活动日历：活动分段以「整体数据（sheet1）」每日覆盖为主，板块块补充波段名。

    series：daily_series(year, start, end)（DB 整体数据口径，含 activity/is_routine）。
    板块块（act_blocks/daily_blocks）可能收集不全（某活动漏块/日期不全），
    所以活动主体用 DB 整体数据（_aggregate_activities：联乘合并、混合等级拆分、
    常规按连续性拆段），波段名/子段从板块块按 wave_group 匹配。
    若 series 为 None（无 DB 数据），退回纯板块块逻辑。
    """
    allb = (act_blocks or []) + (daily_blocks or [])
    wave_blocks = {}
    for b in allb:
        wg = b.get("wave_group") or b.get("name")
        if not wg or _is_routine_block(b):
            continue
        wave_blocks.setdefault(wg, []).append(b)
    for wg in wave_blocks:
        wave_blocks[wg].sort(key=lambda b: (b.get("dates") or ["9999-12-31"])[0])

    if not series:
        # 无 DB 数据：退回板块块为主（仅展示非空档活动块）
        return _activity_calendar_from_blocks(wave_blocks, start, end)

    segs = _series_activity_segments(series)
    out = []
    for a in segs:
        if a["routine"]:
            out.append({"group": "常规活动", "grade": "常规", "start": a["s"], "end": a["e"],
                        "waves": [{"name": "常规活动", "start": a["s"], "end": a["e"]}]})
            continue
        group = a["group"]
        s, e = a["s"], a["e"]
        # 匹配板块块波段（wave_group 与活动组一致，且块起点落在段内）
        waves = []
        for b in wave_blocks.get(group, []):
            ds = b.get("dates") or []
            if ds and s <= ds[0] <= e:
                waves.append({"name": b.get("name"), "start": ds[0], "end": ds[-1]})
        if not waves:
            waves = [{"name": group, "start": s, "end": e}]
        out.append({"group": group, "grade": a["grade"], "start": s, "end": e, "waves": waves})

    out.sort(key=lambda x: (x["start"], x["group"]))
    return out


def _series_activity_segments(series):
    """从 DB 每日序列生成活动段：联乘合并（日期级）+ 按时间连续性拆段。

    返回 [{group, grade, s, e, routine}]（常规日按连续性拆成「常规活动」段）。
    """
    _gd, _gbase, _ggrade = {}, {}, {}
    _routine_dates = []
    for x in series:
        act = x.get("activity") or ""
        if act and not x.get("is_routine"):
            k = _day_activity_key(act)
            _gd.setdefault(k, []).append(x["date"])
            _gbase.setdefault(k, parser._activity_group_key(act))
            _ggrade.setdefault(k, parser._activity_grade(act))
        else:
            _routine_dates.append(x["date"])
    _merge_collab(_gd, _gbase, _ggrade)   # 联乘并入首活动（同等级+时间连贯）
    segs = []
    for k, ds in _gd.items():
        for seg in _routine_segments(ds):   # 同活动不连续 → 拆独立段（如 超级88 各段）
            segs.append({"group": _gbase[k], "grade": _ggrade[k], "s": seg["s"], "e": seg["e"], "routine": False})
    for seg in _routine_segments(_routine_dates):
        segs.append({"group": "常规活动", "grade": "常规", "s": seg["s"], "e": seg["e"], "routine": True})
    segs.sort(key=lambda a: (a["s"], a["group"]))
    return segs


def _activity_calendar_from_blocks(wave_blocks, start, end):
    """纯板块块逻辑：按归组名+等级分组，组内按时间连续性拆段。"""
    groups = {}
    g_grade = {}
    for wg, blks in wave_blocks.items():
        for b in blks:
            name = b.get("name") or wg
            grade = parser._block_grade(b.get("title"), name)
            key = f"{wg} {grade}" if grade else wg
            groups.setdefault(key, []).append(b)
            g_grade.setdefault(key, grade)
    activities = []
    for key, blks in groups.items():
        blks.sort(key=lambda b: (b.get("dates") or ["9999-12-31"])[0])
        segments, cur, prev_end = [], [], None
        for b in blks:
            ds = b.get("dates") or []
            if not ds:
                continue
            if cur and prev_end and not parser._contiguous(prev_end, ds[0]):
                segments.append(cur)
                cur = []
            cur.append(b)
            prev_end = ds[-1]
        if cur:
            segments.append(cur)
        for seg in segments:
            seg.sort(key=lambda b: (b.get("dates") or ["9999-12-31"])[0])
            waves = [{"name": b.get("name"), "start": b["dates"][0], "end": b["dates"][-1]}
                     for b in seg if b.get("dates")]
            if not waves:
                continue
            activities.append({
                "group": seg[0].get("wave_group") or seg[0].get("name"),
                "grade": g_grade[key],
                "start": seg[0]["dates"][0], "end": seg[-1]["dates"][-1],
                "waves": waves,
            })
    activities.sort(key=lambda a: (a["start"], a["group"]))
    return activities


def _day_activity_key(act: str) -> str:
    """活动日/块的唯一筛选键 = 归组名 + 等级（如「年货节 S-」「618 S」「双11 S+」）。

    混合等级的活动（如 年货节 既有 S- 又有 常规）会拆成不同键，可单独筛选。
    无括号（无等级）的用归组名本身。
    """
    g = parser._activity_group_key(act)
    gr = parser._activity_grade(act)
    return f"{g} {gr}" if gr else g


def _merge_collab(gd: dict, gbase: dict, ggrade: dict) -> dict:
    """联乘活动并入首活动：名称含×、首段活动独立存在、同等级、且时间连贯（无>1天断档）。

    例：「七夕×超级88年中盛典 S-」(08.04~08.09) + 「七夕 S-」(08.10~08.19)
    → 并入「七夕 S-」，区间 08.04~08.19。
    就地修改 gd（key→dates）/gbase（key→展示名）/ggrade（key→等级）；
    返回 {原key: 并入key}（未并入的 key 不在映射里，沿用自身，供筛选用）。
    """
    keymap = {}
    for k in list(gd.keys()):
        if "×" not in k or k in keymap:
            continue
        head = k.split("×")[0].strip()
        gr = ggrade.get(k, "")
        base_key = f"{head} {gr}" if gr else head
        if base_key not in gd or base_key == k:
            continue
        ds = sorted(set(gd[k]) | set(gd[base_key]))
        if len(_routine_segments(ds)) == 1:   # 两段合并后仍是连续一段 → 才并入
            gd[base_key] = ds
            gd.pop(k, None)
            gbase.pop(k, None)
            ggrade.pop(k, None)
            keymap[k] = base_key
            keymap[base_key] = base_key
    return keymap


def _aggregate_activities(series_all):
    """从每日序列聚合出活动列表 + 联乘映射，供各面板复用。

    返回 (activities, keymap)：
    - activities：含联乘合并、常规拆段、混合等级拆分，按时间排序；
    - keymap：原活动键 → 联乘并入键（筛选用）。
    """
    _gd = {}
    _g_base = {}
    _g_grade = {}
    _routine_dates = []
    for x in series_all:
        act = x.get("activity") or ""
        if act and not x.get("is_routine"):
            k = _day_activity_key(act)
            _gd.setdefault(k, []).append(x["date"])
            _g_base.setdefault(k, parser._activity_group_key(act))
            _g_grade.setdefault(k, parser._activity_grade(act))
        else:
            _routine_dates.append(x["date"])
    _keymap = _merge_collab(_gd, _g_base, _g_grade)
    activities = _activity_options_from_groups(_gd, _g_base, _g_grade)
    for seg in _routine_segments(_routine_dates):
        dr = f"{seg['s'][5:].replace('-', '.')}~{seg['e'][5:].replace('-', '.')}"
        activities.append({"group": "常规活动", "key": f"常规活动（{dr}）", "grade": "常规",
                           "date_range": dr, "s": seg["s"], "e": seg["e"], "routine": True})
    activities.sort(key=_activity_sort_key)
    return activities, _keymap


def _filter_by_activity(series_all, activities, keymap, activity):
    """按 activity key 筛选每日序列（含常规段 + 联乘合并映射）。"""
    if not activity:
        return series_all
    # 只有「常规段」（routine=True，日期散落需按段筛）走区间分支；
    # 普通活动（如 618 S）有 s/e 但应按键匹配，否则会误返回空。
    seg = next((a for a in activities if a.get("key") == activity and a.get("s") and a.get("routine")), None)
    if seg:
        return [x for x in series_all if x["is_routine"] and seg["s"] <= x["date"] <= seg["e"]]

    selected = next((a for a in activities if a.get("key") == activity), None)
    parsed_base, parsed_start, parsed_end = _split_activity_key(activity)
    selected_base = parsed_base
    selected_start = selected.get("s") if selected else parsed_start
    selected_end = selected.get("e") if selected else parsed_end

    def _eff(xx):   # 每日「有效活动键」：联乘并入首活动后
        k = _day_activity_key(xx.get("activity") or "")
        return keymap.get(k, k)
    return [x for x in series_all
            if not x["is_routine"] and _eff(x) == selected_base
            and (not selected_start or selected_start <= x["date"] <= selected_end)]


def _prev_filtered(prev_all: list, activity: str, prev_year: int) -> list:
    """对比期按同一活动筛选（与本期同口径）。

    普通活动键直接复用 _filter_by_activity（键含等级、联乘映射，均按键匹配）；
    常规段键（如「常规活动（01.01~01.03）」）的日期是本期年份的 MM.DD，
    平移回对比期年份再筛常规日（对比期第 N 天 ↔ 本期第 N 天）。
    """
    if not activity:
        return prev_all
    if activity.startswith("常规活动（"):
        m = activity[len("常规活动（"):-1].split("~")
        if len(m) == 2:
            s = f"{prev_year:04d}-{m[0].replace('.', '-')}"
            e = f"{prev_year:04d}-{m[1].replace('.', '-')}"
            return [x for x in prev_all if x["is_routine"] and s <= x["date"] <= e]
        return []
    base_key, start, end = _split_activity_key(activity)
    prev_activities, _pkeymap = _aggregate_activities(prev_all)
    if start and end:
        ps = f"{prev_year:04d}{start[4:]}"
        pe = f"{prev_year:04d}{end[4:]}"
        return [x for x in prev_all
                if not x["is_routine"] and ps <= x["date"] <= pe
                and _pkeymap.get(_day_activity_key(x.get("activity") or ""),
                                 _day_activity_key(x.get("activity") or "")) == base_key]
    return _filter_by_activity(prev_all, prev_activities, _pkeymap, activity)


def activity_options_from_blocks(blocks: list):
    """从板块块生成活动筛选项，常规等级归日常且同名断档分段。"""
    groups, gbase, ggrade = {}, {}, {}
    routine_dates = []
    for block in blocks:
        group = block.get("wave_group") or block.get("name")
        if not group:
            continue
        name = block.get("name") or group
        if _is_routine_block(block):
            routine_dates.extend(block.get("dates") or [])
            continue
        key = _day_activity_key(name)
        groups.setdefault(key, []).extend(block.get("dates") or [])
        gbase.setdefault(key, parser._activity_group_key(name))
        ggrade.setdefault(key, parser._activity_grade(name) or parser._block_grade(block.get("title"), name))

    keymap = _merge_collab(groups, gbase, ggrade)
    activities = _activity_options_from_groups(groups, gbase, ggrade)
    for seg in _routine_segments(routine_dates):
        dr = f"{seg['s'][5:].replace('-', '.')}~{seg['e'][5:].replace('-', '.')}"
        activities.append({"group": "常规活动", "key": f"常规活动（{dr}）", "grade": "常规",
                           "date_range": dr, "s": seg["s"], "e": seg["e"], "routine": True})
    activities.sort(key=_activity_sort_key)
    return activities, keymap


def filter_blocks_by_activity(blocks: list, activity: str) -> list:
    """板块块按活动筛选（与 panel_blocks 同口径）：

    常规段（routine）按区间筛常规块；普通活动按活动键（归组名+等级，含联乘并入映射）筛。
    blocks 不要求按时间段过滤——后续聚合函数会按日期范围取子集。
    """
    if not activity:
        return blocks
    activities, keymap = activity_options_from_blocks(blocks)
    # 只有「常规段」（routine=True）按区间筛；普通活动按独立活动段匹配
    seg = next((a for a in activities if a.get("key") == activity and a.get("s") and a.get("routine")), None)
    if seg:
        return [b for b in blocks if _is_routine_block(b)
                and any(seg["s"] <= d <= seg["e"] for d in (b.get("dates") or []))]

    selected = next((a for a in activities if a.get("key") == activity), None)
    parsed_base, parsed_start, parsed_end = _split_activity_key(activity)
    selected_base = parsed_base
    selected_start = selected.get("s") if selected else parsed_start
    selected_end = selected.get("e") if selected else parsed_end

    def _eff(b):   # 块的有效活动键（联乘并入首活动后）
        k = _day_activity_key(b.get("name") or (b.get("wave_group") or ""))
        return keymap.get(k, k)
    return [b for b in blocks if not _is_routine_block(b)
            and _eff(b) == selected_base
            and (not selected_start or any(selected_start <= d <= selected_end for d in (b.get("dates") or [])))]


def overview(year: int, start: str, end: str, activity: str = None,
             prev_start: str = None, prev_end: str = None) -> dict:
    """总览面板数据：周期聚合 + 活动/日常拆分 + 每日趋势 + 对比期趋势。

    activity：按活动组（_activity_group_key 归组）筛选，None=全部。
    prev_start/prev_end：任意对比时间段（完整日期，可跨年）；留空则自动对齐上一年同月同日。
    对比期年份取 prev_start 的年份；对比期趋势按位置对齐本期（第 N 天 ↔ 第 N 天）。
    """
    series_all = daily_series(year, start, end)
    activities, _keymap = _aggregate_activities(series_all)
    series = _filter_by_activity(series_all, activities, _keymap, activity)
    routine = [x for x in series if x["is_routine"]]
    campaign = [x for x in series if not x["is_routine"]]
    trend = [{k: x[k] for k in ("date", "activity", "is_routine",
                                "总访客数", "点击率", "平均停留时长", "支付转化率")}
             for x in series]
    # 对比期时间段：自定义（prev_start/prev_end，可任意跨年选段）或自动对齐上一年同月同日。
    # 对比期年份：优先取 prev_start 的年份；留空则取「本期年份-1」。本期年份 = start 自带年份，
    # 与年份选择器无关（自定义模式下选择器只作用于 MTD/QTD/HTD/YTD）。
    # 自动对齐口径：本期实际有数据的最后一天（未来空值不算）决定对比期截止。
    data_dates = [x["date"] for x in series if x["_tv"] is not None]
    last_data = max(data_dates) if data_dates else None
    prev_year = int(prev_start[:4]) if (prev_start and prev_end) else (int(start[:4]) - 1)
    if prev_start and prev_end:
        sp, ep = prev_start, prev_end
    else:
        sp = f"{prev_year:04d}{start[4:]}"
        ep = f"{prev_year:04d}{end[4:]}"
        if last_data:
            ep_trim = f"{prev_year:04d}{last_data[4:]}"
            if ep_trim < ep:
                ep = ep_trim
    # 对比期按同一活动筛选（与本期同口径），趋势再按「位置」对齐：
    # 对比期第 N 天 ↔ 本期第 N 天（支持不同长度周期对比），短于本期补 None 断点。
    prev_all = daily_series(prev_year, sp, ep)
    prev_series = _prev_filtered(prev_all, activity, prev_year)
    has_prev = bool(prev_series)
    trend_prev = []
    for i, x in enumerate(series):
        p = prev_series[i] if i < len(prev_series) else None
        trend_prev.append({
            "date": x["date"],
            "总访客数": p["总访客数"] if p else None,
            "点击率": p["点击率"] if p else None,
            "平均停留时长": p["平均停留时长"] if p else None,
            "支付转化率": p["支付转化率"] if p else None,
        })
    campaign_agg = _agg(campaign)
    campaign_agg["日均访客数"] = _daily_avg_visitors(campaign)
    routine_agg = _agg(routine)
    routine_agg["日均访客数"] = _daily_avg_visitors(routine)
    # 对比期的活动期/日常期拆分（供对比）
    prev_campaign_agg = prev_routine_agg = None
    if prev_series:
        prev_campaign = [x for x in prev_series if not x["is_routine"]]
        prev_routine = [x for x in prev_series if x["is_routine"]]
        prev_campaign_agg = _agg(prev_campaign)
        prev_campaign_agg["日均访客数"] = _daily_avg_visitors(prev_campaign)
        prev_routine_agg = _agg(prev_routine)
        prev_routine_agg["日均访客数"] = _daily_avg_visitors(prev_routine)
    return {
        "year": year, "prev_year": prev_year, "has_prev": has_prev,
        "start": start, "end": end, "prev_start": sp, "prev_end": ep,
        "activities": activities, "activity": activity,
        "period": _agg(series),
        "period_prev": _agg(prev_series) if prev_series else None,
        "routine": routine_agg,
        "campaign": campaign_agg,
        "routine_prev": prev_routine_agg,
        "campaign_prev": prev_campaign_agg,
        "trend": trend,
        "trend_prev": trend_prev,
    }

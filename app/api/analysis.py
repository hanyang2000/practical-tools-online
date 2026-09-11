"""天猫首页数据分析 - API。

上传 xlsx → 解析归一化 → 缓存到数据目录 → 返回字段映射预览。
后续面板（总览/重点指标/板块/新老客/活动日常/趋势）将消费缓存的 data。
"""
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.analysis import legacy_db as adb
from app.analysis import metrics, parser
from app.auth import CurrentUser
from app.config import get_settings
from app.services.storage import enforce_upload_policy, stage_upload
from app.services.storage import TencentCosStorage, sha256_file
from app.models import PracticalUploadSession
from app.db import get_db
from sqlalchemy.orm import Session

DATA_DIR = get_settings().data_root


async def _bind_analysis_db(db: Session = Depends(get_db), user: CurrentUser = None):
    token = adb.bind(db, user.id)
    try:
        yield
    finally:
        adb.unbind(token)


router = APIRouter(dependencies=[Depends(_bind_analysis_db)])

CACHE_DIR = DATA_DIR / "analysis"
CACHE_FILE = CACHE_DIR / "cache.json"
SOURCE_DIR = CACHE_DIR / "source"

def _user_cache_file() -> Path:
    owner = adb.owner_id()
    return CACHE_DIR / "users" / str(owner) / "cache.json" if owner else CACHE_FILE

def _user_source_dir() -> Path:
    owner = adb.owner_id()
    return CACHE_DIR / "users" / str(owner) / "source" if owner else SOURCE_DIR


def _load_cache(year: str = None):
    """加载缓存。year 不传→最新年份；传 year（'2025'）→该年份。"""
    cache_file = _user_cache_file()
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    years = data.get("years") if isinstance(data, dict) else None
    if not years:
        return None
    if year is None:
        order = data.get("order", [])
        y = order[-1] if order else sorted(years.keys())[-1]
        return years.get(y)
    return years.get(str(year))


def _available_cache_years() -> list:
    """已缓存的年份列表（升序）。"""
    cache_file = _user_cache_file()
    if not cache_file.exists():
        return []
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    order = data.get("order", [])
    return order if order else sorted((data.get("years") or {}).keys())


def _save_cache(result: dict, year: str) -> None:
    """按年份分层存缓存，不覆盖其他年份。"""
    cache_file = _user_cache_file()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if not isinstance(data.get("years"), dict):
        data["years"] = {}
        data["order"] = []
    data["years"][str(year)] = result
    order = data.get("order", [])
    if str(year) not in order:
        order.append(str(year))
    data["order"] = order
    cache_file.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


def _without_data(result: dict) -> dict:
    """预览口径：去掉全量 data，保留 fields / sample / anomalies。"""
    out = {}
    for k, v in result.items():
        if k == "data":
            continue
        if k == "sheets":
            out[k] = {name: {kk: vv for kk, vv in sv.items() if kk != "data"}
                      for name, sv in v.items()}
        else:
            out[k] = v
    return out


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    source_dir = _user_source_dir(); source_dir.mkdir(parents=True, exist_ok=True)
    src_path = source_dir / (Path(file.filename or "data.xlsx").name)
    staged = None
    try:
        # Keep the upload bounded on disk. Reading the entire workbook into
        # Python memory made a large-but-valid upload compete with the parser.
        staged, size, _digest = stage_upload(file.file, get_settings())
        try:
            enforce_upload_policy(get_settings(), size)
        except RuntimeError as exc:
            raise HTTPException(413, str(exc)) from exc
        shutil.copyfile(staged, src_path)
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        status_code = 413 if "大小限制" in str(exc) else 400
        raise HTTPException(status_code, str(exc)) from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    try:
        result = parser.parse_file(src_path)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"解析失败：{e}"}, status_code=400)
    result["file"] = file.filename
    result["saved_source"] = str(src_path)
    # 把「整体数据」日行写入库，供跨年度月度对比
    db_info = {"stored": 0, "years": []}
    overall = result.get("sheets", {}).get("整体数据")
    if overall:
        try:
            db_info = adb.store_overall(overall["data"])
            metrics.clear_cache()
        except Exception as e:  # noqa: BLE001
            db_info["error"] = str(e)
    result["db"] = db_info
    # 确定文件年份（从「整体数据」日期年份，回退文件名前4位）
    years = (overall or {}).get("fields", {}).get("years", []) if overall else []
    year = years[0] if years else (
        file.filename[:4] if file.filename and file.filename[:4].isdigit() else "unknown")
    _save_cache(result, str(year))
    return {"ok": True, "year": str(year), **(_without_data(result))}

@router.post("/upload/from-cos")
async def upload_from_cos(upload_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    row = db.get(PracticalUploadSession, upload_id)
    if not row or row.owner_id != user.id or row.purpose != "analysis_excel": raise HTTPException(404, "上传会话不存在")
    if row.status != "completed": raise HTTPException(409, "上传尚未完成")
    try:
        with TencentCosStorage().temporary_download(row.storage_key) as path:
            if path.stat().st_size != row.size or sha256_file(path) != row.sha256: raise HTTPException(400, "COS 文件校验失败")
            result = parser.parse_file(path)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(400, f"COS 文件解析失败：{exc}") from exc
    result["file"] = row.filename; result["saved_source"] = row.storage_key
    overall = result.get("sheets", {}).get("整体数据"); db_info = {"stored": 0, "years": []}
    if overall:
        try: db_info = adb.store_overall(overall["data"])
        except Exception as exc: db_info["error"] = str(exc)
    result["db"] = db_info
    years = (overall or {}).get("fields", {}).get("years", []) if overall else []
    year = years[0] if years else (row.filename[:4] if row.filename[:4].isdigit() else "unknown")
    _save_cache(result, str(year)); row.status = "consumed"; db.commit()
    return {"ok": True, "year": str(year), **(_without_data(result)), "saved_source": row.storage_key}


@router.get("/status")
async def status():
    cache = _load_cache()
    if not cache:
        return {"loaded": False}
    sheets = {name: sv.get("kind") for name, sv in cache.get("sheets", {}).items()}
    anomalies = sum(len(sv.get("anomalies", []))
                    for sv in cache.get("sheets", {}).values())
    return {"loaded": True, "file": cache.get("file"),
            "sheets": sheets, "anomaly_count": anomalies,
            "cache_years": _available_cache_years(),
            "unrecognized_sheets": cache.get("unrecognized_sheets", [])}


@router.get("/preview")
async def preview():
    cache = _load_cache()
    if not cache:
        return {"ok": False, "error": "尚未上传数据"}
    return {"ok": True, **(_without_data(cache))}


@router.get("/files")
async def list_files():
    """已上传的文件（按年份），供数据管理展示/管理。"""
    cache_file = _user_cache_file()
    if not cache_file.exists():
        return {"ok": True, "files": []}
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": True, "files": []}
    years = data.get("years") or {}
    order = data.get("order") or sorted(years.keys())
    files = []
    for y in order:
        c = years.get(str(y)) or {}
        sheets = (c.get("sheets") or {})
        files.append({
            "year": str(y),
            "file": c.get("file"),
            "sheets": {name: sv.get("kind") for name, sv in sheets.items()},
            "db_rows": (c.get("db") or {}).get("stored", 0),
        })
    return {"ok": True, "files": files}


@router.delete("/file")
async def delete_file(year: int):
    """删除某年份的缓存与入库数据（数据管理「删除」用）。"""
    y = str(year)
    try:
        adb.delete_year(int(y))
        metrics.clear_cache()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"删除入库数据失败：{e}"}
    try:
        data = {}
        cache_file = _user_cache_file()
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        years = data.get("years") or {}
        years.pop(y, None)
        data["years"] = years
        data["order"] = [x for x in (data.get("order") or []) if str(x) != y]
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"删除缓存失败：{e}"}
    return {"ok": True, "year": y}


@router.get("/metrics")
async def metrics_api():
    """跨年度月度对比：所有已入库年度的逐月 + 全年 4 核心指标。"""
    return {"ok": True, **metrics.monthly_compare()}


@router.get("/panel/overview")
async def panel_overview(year: int = None, dim: str = "YTD", period: int = None,
                         end: str = None, start: str = None, activity: str = None,
                         prev_start: str = None, prev_end: str = None):
    """总览面板：某年某维度（MTD=月/QTD=季/HTD=半年/YTD=年/自定义）× 周期的指标 + 拆分 + 趋势。

    prev_start/prev_end：去年对比的自定义时间段（留空自动对齐同月同日）。
    """
    import datetime as _dt
    years = adb.available_years()
    if not years:
        return {"ok": False, "error": "尚未上传数据"}
    if year is None or year not in years:
        year = years[-1]
    if dim == "CUSTOM":
        if not start or not end:
            return {"ok": False, "error": "自定义时间段需要起止日期"}
        year = int(start[:4])   # 本期起止日期自带年份（可任意跨年），忽略年份选择器
    else:
        # 未指定周期时，默认当前周期（如今天在 Q3 → QTD 默认 Q3）
        today = _dt.date.today()
        if period is None:
            if dim == "MTD":
                period = today.month
            elif dim == "QTD":
                period = (today.month - 1) // 3 + 1
            elif dim == "HTD":
                period = 1 if today.month <= 6 else 2
            else:
                period = 1
        start, end = metrics.resolve_period_range(dim, int(period), year)
    return {"ok": True, **metrics.overview(year, start, end, activity, prev_start, prev_end), "years": years}


@router.get("/panel/newold")
async def panel_newold(year: int = None, dim: str = "YTD", period: int = None,
                       end: str = None, start: str = None, activity: str = None):
    """新老客对比：某年某维度新客/老客核心指标 + 转化漏斗。"""
    import datetime as _dt
    years = adb.available_years()
    if not years:
        return {"ok": False, "error": "尚未上传数据"}
    if year is None or year not in years:
        year = years[-1]
    if dim == "CUSTOM":
        if not start or not end:
            return {"ok": False, "error": "自定义时间段需要起止日期"}
        year = int(start[:4])   # 本期起止日期自带年份（可任意跨年），忽略年份选择器
    else:
        today = _dt.date.today()
        if period is None:
            if dim == "MTD":
                period = today.month
            elif dim == "QTD":
                period = (today.month - 1) // 3 + 1
            elif dim == "HTD":
                period = 1 if today.month <= 6 else 2
            else:
                period = 1
        start, end = metrics.resolve_period_range(dim, int(period), year)
    return {"ok": True, **metrics.newold(year, start, end, activity), "years": years}


@router.post("/panel/anomaly/fix")
async def fix_anomaly(body: dict):
    """记录/更新某天访客数/点击人数的增减量（正=增加 负=减少），可反复调整。"""
    date = body.get("date")
    field = body.get("field")
    delta = body.get("delta")
    if not date or not field or delta is None:
        return {"ok": False, "error": "参数不全（需 date/field/delta）"}
    try:
        delta = float(delta)
    except (TypeError, ValueError):
        return {"ok": False, "error": "增减量格式错误"}
    year = int(date[:4])
    return adb.apply_fix(year, date, field, delta)


@router.get("/panel/anomaly/fixes")
async def list_fixes():
    """列出所有修正记录（供调整/重置）。"""
    return {"ok": True, "fixes": adb.get_fixes()}


@router.delete("/panel/anomaly/fix")
async def reset_fix(date: str, field: str):
    """重置（删除）某天某字段的修正记录。"""
    if not date or not field:
        return {"ok": False, "error": "参数不全"}
    return adb.remove_fix(int(date[:4]), date, field)


@router.get("/panel/anomaly")
async def panel_anomaly(year: int = None, dim: str = "YTD", period: int = None,
                        end: str = None, start: str = None, activity: str = None,
                        metric: str = "总访客数"):
    """趋势异常检测：Z-Score + 环比偏离标记异常点。"""
    import datetime as _dt
    years = adb.available_years()
    if not years:
        return {"ok": False, "error": "尚未上传数据"}
    if year is None or year not in years:
        year = years[-1]
    if dim == "CUSTOM":
        if not start or not end:
            return {"ok": False, "error": "自定义时间段需要起止日期"}
        year = int(start[:4])   # 本期起止日期自带年份（可任意跨年），忽略年份选择器
    else:
        today = _dt.date.today()
        if period is None:
            if dim == "MTD":
                period = today.month
            elif dim == "QTD":
                period = (today.month - 1) // 3 + 1
            elif dim == "HTD":
                period = 1 if today.month <= 6 else 2
            else:
                period = 1
        start, end = metrics.resolve_period_range(dim, int(period), year)
    return {"ok": True, **metrics.anomaly(year, start, end, metric, activity), "years": years}


@router.get("/panel/blocks")
async def panel_blocks(year: int = None, dim: str = "YTD", period: int = None,
                       sheet: str = "日常页面数据", metric: str = "点击率",
                       segment: str = "整体", start: str = None, end: str = None,
                       activity: str = None, min_days: int = 1, include: str = None):
    """板块深度分析：板块×日期 热力矩阵 + 板块均值 + Top/Bottom 5 + 低于整体50%警示。

    时间维度与总览/重点指标统一：year + dim(MTD/QTD/HTD/YTD/自定义) + period。
    activity：按活动组（wave_group）筛选；min_days：只保留出现天数 ≥ 该值的板块（默认 1，即 0 次板块不展示）；
    include：逗号分隔的板块名白名单（用户勾选参与的板块），None=全部。
    """
    import datetime as _dt
    cache_years = _available_cache_years()
    if not cache_years:
        return {"ok": False, "error": "尚未上传数据"}
    if year is None or str(year) not in cache_years:
        year = int(cache_years[-1])

    # 时间范围：统一用 year+dim+period（CUSTOM 用 start/end，本期年份=start 自带年份，
    # 可任意跨年，忽略年份选择器）→ 先定年份再按该年份加载缓存
    if dim == "CUSTOM":
        if not start or not end:
            return {"ok": False, "error": "自定义时间段需要起止日期"}
        year = int(start[:4])   # 本期起止日期自带年份（可任意跨年），忽略年份选择器
    else:
        today = _dt.date.today()
        if period is None:
            if dim == "MTD":
                period = today.month
            elif dim == "QTD":
                period = (today.month - 1) // 3 + 1
            elif dim == "HTD":
                period = 1 if today.month <= 6 else 2
            else:
                period = 1
        start, end = metrics.resolve_period_range(dim, int(period), year)

    cache = _load_cache(str(year))
    if not cache:
        return {"ok": False, "error": f"无 {year} 年板块数据"}
    blocks = (cache.get("sheets", {}).get(sheet) or {}).get("data") or []
    if not blocks:
        return {"ok": False, "error": f"无「{sheet}」板块数据"}
    # 页面实际顺序（全量块首次出现顺序），筛选后仍按此摆，不随活动/时间/勾选漂移
    source_order = metrics.block_source_order(blocks)

    # 按时间范围过滤块（块日期与 [start,end] 有交集）→ 活动筛选只显示当前时间段内的活动
    blocks_in_range = [b for b in blocks
                       if any(start <= d <= end for d in (b.get("dates") or []))]

    # 活动列表统一由 metrics 生成：常规等级归日常，同名断档拆成独立筛选项。
    activities, _keymap = metrics.activity_options_from_blocks(blocks_in_range)

    if activity:
        # 只有「常规段」（routine=True）按区间筛；普通活动（如 618 S）有 s/e 但应按键匹配
        blocks_in_range = metrics.filter_blocks_by_activity(blocks_in_range, activity)
        if not blocks_in_range:
            return {"ok": False, "error": f"无「{activity}」活动板块数据"}

    dates = sorted({d for b in blocks_in_range for d in (b.get("dates") or [])
                    if start <= d <= end})
    if not dates:
        return {"ok": False, "error": "所选时间段无数据"}

    # 板块级行（子模块为 整体 / None）的每日值
    per_block = {}   # 板块 -> {date: value}
    for b in blocks_in_range:
        for row in b.get("rows") or []:
            if row.get("子模块") not in (None, "整体"):
                continue
            name = row.get("板块")
            if not name:
                continue
            daily = row.get("每日") or {}
            bucket = per_block.setdefault(name, {})
            for date, segs in daily.items():
                if date < start or date > end:
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

    # 出现天数（有数据的日期数）
    appear_days = {n: len(bucket) for n, bucket in per_block.items()}
    # 默认不展示出现 0 次的板块（min_days 默认 1）
    threshold = max(min_days, 1)
    per_block = {n: b for n, b in per_block.items() if appear_days[n] >= threshold}
    # 全部板块（过滤后，按页面实际顺序，供勾选面板）
    all_blocks = [n for n in source_order if n in per_block]

    # 板块勾选白名单（用户勾选参与的板块）；include 非 None 即应用（含空=清空）
    if include is not None:
        keep = {x.strip() for x in include.split(',') if x.strip()}
        per_block = {n: b for n, b in per_block.items() if n in keep}

    # 保持页面实际顺序（非字母排序），过滤后仍按源表位置摆
    names = [n for n in source_order if n in per_block]
    matrix = [[per_block[n].get(d) for d in dates] for n in names]
    avgs = []
    for n in names:
        vals = [v for v in per_block[n].values() if v is not None]
        avgs.append(round(sum(vals) / len(vals), 6) if vals else None)
    valid = [a for a in avgs if a is not None]
    overall = round(sum(valid) / len(valid), 6) if valid else None

    # 「选你所需」是页面里的用户自选推荐位，它以下的板块都是它的承接商品位
    # （非店铺固定陈列板块）。仅「支付转化」的 Top/Bottom 5 按源表行序把
    # 「选你所需」以下的板块踢出（支付转化受自选推荐位干扰大），点击率正常排序。
    # include（板块勾选面板）只决定热力图展示哪些板块；未勾选的板块本来就进不了
    # 排名，所以这里对支付转化始终踢出，不受 include 影响。
    _xn_below = set()
    if metric == "支付转化":
        for _b in blocks:
            _after_xn = False
            for _row in _b.get("rows") or []:
                if _row.get("子模块") not in (None, "整体"):
                    continue
                _nm = _row.get("板块")
                if not _nm:
                    continue
                if _nm == "选你所需":
                    _after_xn = True
                    continue
                if _after_xn:
                    _xn_below.add(_nm)

    scored = [(n, a) for n, a in zip(names, avgs) if a is not None and n not in _xn_below]
    scored.sort(key=lambda x: x[1], reverse=True)
    top5 = [{"name": n, "value": a} for n, a in scored[:5]]
    bottom5 = [{"name": n, "value": a} for n, a in scored[-5:][::-1]]
    warn = [n for n, a in scored if overall is not None and a < overall * 0.5]

    return {"ok": True, "year": year, "sheet": sheet, "metric": metric, "segment": segment,
            "activity": activity, "activities": activities,
            "cache_years": cache_years,
            "dates": dates, "板块": names, "matrix": matrix,
            "板块均值": avgs, "整体均值": overall,
            "出现天数": [appear_days[n] for n in names],
            "all_blocks": [{"name": n, "days": appear_days[n]} for n in all_blocks],
            "top5": top5, "bottom5": bottom5, "警示板块": warn}


@router.get("/panel/blocks_yoy")
async def panel_blocks_yoy(year: int = None, dim: str = "YTD", period: int = None,
                           sheet: str = "日常页面数据", metric: str = "点击率",
                           segment: str = "整体", start: str = None, end: str = None,
                           activity: str = None, prev_start: str = None, prev_end: str = None):
    """板块对比：本期 vs 任意对比期同名板块的均值差距（板块深度面板）。

    prev_start/prev_end：任意对比时间段（完整日期，可跨年）；留空则自动对齐上一年同月同日。
    """
    import datetime as _dt
    cache_years = _available_cache_years()
    if not cache_years:
        return {"ok": False, "error": "尚未上传数据"}
    if year is None or str(year) not in cache_years:
        year = int(cache_years[-1])

    # 本期时间范围（同 panel_blocks）
    if dim == "CUSTOM":
        if not start or not end:
            return {"ok": False, "error": "自定义时间段需要起止日期"}
        year = int(start[:4])   # 本期起止日期自带年份（可任意跨年），忽略年份选择器
    else:
        today = _dt.date.today()
        if period is None:
            if dim == "MTD":
                period = today.month
            elif dim == "QTD":
                period = (today.month - 1) // 3 + 1
            elif dim == "HTD":
                period = 1 if today.month <= 6 else 2
            else:
                period = 1
        start, end = metrics.resolve_period_range(dim, int(period), year)
    cache = _load_cache(str(year))
    if not cache:
        return {"ok": False, "error": f"无 {year} 年板块数据"}

    # 对比期：自定义（prev_start/prev_end，可任意跨年）或自动对齐上一年同月同日
    if prev_start and prev_end:
        prev_year = int(prev_start[:4])
        ps, pe = prev_start, prev_end
    else:
        prev_year = year - 1
        ps = f"{prev_year:04d}{start[4:]}"
        pe = f"{prev_year:04d}{end[4:]}"
    if prev_year not in [int(y) for y in cache_years]:
        return {"ok": False, "error": f"无 {prev_year} 年数据，无法对比", "rows": []}

    def _blocks_of(y):
        c = _load_cache(str(y))
        return (c.get("sheets", {}).get(sheet) or {}).get("data") or [] if c else []

    cur_blocks_all = _blocks_of(year)
    prev_blocks = _blocks_of(prev_year)
    # 页面实际顺序（全量块首次出现顺序），对比表按此排，不随活动/时间筛选漂移
    source_order = metrics.block_source_order(cur_blocks_all)
    # 活动筛选：本期按活动键（归组名+等级 / 常规段）过滤，与 panel_blocks 同口径
    cur_blocks = metrics.filter_blocks_by_activity(cur_blocks_all, activity)

    rows = metrics.blocks_yoy(cur_blocks, prev_blocks, start, end, ps, pe, metric, segment, source_order)
    return {"ok": True, "year": year, "prev_year": prev_year, "sheet": sheet,
            "metric": metric, "segment": segment, "start": start, "end": end,
            "prev_start": ps, "prev_end": pe,
            "rows": rows}


@router.get("/panel/calendar")
async def panel_calendar(year: int = None, dim: str = "YTD", period: int = None,
                         start: str = None, end: str = None):
    """活动日历：筛选时间段内的活动 + 波段（横向甘特图数据）。

    与总览/重点指标统一 year+dim(MTD/QTD/HTD/YTD/自定义)+period。
    返回 activities：每个活动含整体周期（start/end）+ 波段列表（waves）。
    """
    import datetime as _dt
    cache_years = _available_cache_years()
    if not cache_years:
        return {"ok": False, "error": "尚未上传数据"}
    if year is None or str(year) not in cache_years:
        year = int(cache_years[-1])

    # 时间范围：统一用 year+dim+period（CUSTOM 用 start/end，本期年份=start 自带年份，
    # 可任意跨年，忽略年份选择器）→ 先定年份再按该年份加载缓存
    if dim == "CUSTOM":
        if not start or not end:
            return {"ok": False, "error": "自定义时间段需要起止日期"}
        year = int(start[:4])   # 本期起止日期自带年份（可任意跨年），忽略年份选择器
    else:
        today = _dt.date.today()
        if period is None:
            if dim == "MTD":
                period = today.month
            elif dim == "QTD":
                period = (today.month - 1) // 3 + 1
            elif dim == "HTD":
                period = 1 if today.month <= 6 else 2
            else:
                period = 1
        start, end = metrics.resolve_period_range(dim, int(period), year)

    cache = _load_cache(str(year))
    if not cache:
        return {"ok": False, "error": f"无 {year} 年板块数据"}

    sheets = cache.get("sheets", {})
    act_blocks = (sheets.get("活动页面数据") or {}).get("data") or []
    daily_blocks = (sheets.get("日常页面数据") or {}).get("data") or []
    # 活动主体以 DB 整体数据（sheet1）每日覆盖为准，板块块只补充波段名
    series = metrics.daily_series(year, start, end)
    activities = metrics.activity_calendar(act_blocks, daily_blocks, start, end, series)

    return {"ok": True, "year": year, "start": start, "end": end,
            "activities": activities, "years": [int(y) for y in cache_years]}


def _overall_data(year: int) -> list:
    """某年「整体数据」日行（从缓存）。"""
    cache = _load_cache(str(year))
    if not cache:
        return []
    return (cache.get("sheets", {}).get("整体数据") or {}).get("data") or []


def _overall_for(year: int, start: str, end: str):
    """聚合某年某时段的整体数据全维度（从缓存），无数据返回 None。"""
    return metrics.overall_all(_overall_data(year), start, end)


@router.get("/panel/overall_all")
async def panel_overall_all(year: int = None, dim: str = "YTD", period: int = None,
                            start: str = None, end: str = None,
                            prev_start: str = None, prev_end: str = None):
    """整体数据全维度对比（总览「查看更多」）：当前周期全部 17 个指标（新客/老客/整体），
    另含去年对比（prev_start/prev_end 或自动对齐同月同日）。"""
    import datetime as _dt
    cache_years = _available_cache_years()
    if not cache_years:
        return {"ok": False, "error": "尚未上传数据"}
    if year is None or str(year) not in cache_years:
        year = int(cache_years[-1])

    # 当前周期时间范围（与总览/重点指标统一）
    if dim == "CUSTOM":
        if not start or not end:
            return {"ok": False, "error": "自定义时间段需要起止日期"}
        year = int(start[:4])   # 本期起止日期自带年份（可任意跨年），忽略年份选择器
    else:
        today = _dt.date.today()
        if period is None:
            if dim == "MTD":
                period = today.month
            elif dim == "QTD":
                period = (today.month - 1) // 3 + 1
            elif dim == "HTD":
                period = 1 if today.month <= 6 else 2
            else:
                period = 1
        start, end = metrics.resolve_period_range(dim, int(period), year)

    data_cur = _overall_data(year)
    # 本期实际有数据的最后一天：未来未更新日期截断，避免与长对比期对比失准
    last_data = max((r["date"] for r in data_cur
                     if not r.get("is_summary") and r.get("date") and metrics._has_overall_data(r)),
                    default=None)
    eff_end = end
    if last_data and last_data < eff_end:
        eff_end = last_data

    # 对比期：自定义（prev_start/prev_end，可任意跨年）或自动对齐上一年同月同日
    # （自动对齐截止到本期实际数据日对应的对比年日期）
    if prev_start and prev_end:
        prev_year = int(prev_start[:4])
        sp, ep = prev_start, prev_end
    else:
        prev_year = int(start[:4]) - 1
        sp = f"{prev_year:04d}{start[4:]}"
        ep = f"{prev_year:04d}{eff_end[4:]}"
    data_prev = _overall_data(prev_year)

    cur = metrics.overall_all(data_cur, start, eff_end) if data_cur else None
    if not cur:
        return {"ok": False, "error": "该时段整体数据为空", "start": start, "end": eff_end}
    prev = metrics.overall_all(data_prev, sp, ep) if data_prev else None
    trend = metrics.overall_trend(data_cur, start, eff_end, data_prev, sp, ep) if data_cur else []

    return {"ok": True, "year": year, "start": start, "end": eff_end,
            "prev_year": prev_year, "has_prev": bool(prev),
            "prev_start": sp, "prev_end": ep,
            "metrics": cur, "prev_metrics": prev, "trend": trend}

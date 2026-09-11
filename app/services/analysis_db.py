from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PracticalAnalysisDaily, PracticalAnalysisFix


def _owner_clause(model, owner_id: str | None):
    """Restrict every operation to one account.

    ``None`` is deliberately an ownerless/legacy namespace, rather than a
    wildcard.  Import scripts can continue to write/read historical rows in
    that namespace, while a logged-in account can never see another account's
    rows (or accidentally replace them).
    """
    return model.owner_id.is_(None) if owner_id is None else model.owner_id == owner_id


def store_overall(db: Session, rows: list[dict], owner_id: str | None = None) -> dict:
    years = sorted({int(r["date"][:4]) for r in rows if not r.get("is_summary") and r.get("date")})
    for year in years:
        db.execute(delete(PracticalAnalysisDaily).where(_owner_clause(PracticalAnalysisDaily, owner_id), PracticalAnalysisDaily.year == year))
    count = 0
    for row in rows:
        if row.get("is_summary") or not row.get("date"): continue
        n, o = row.get("segments", {}).get("新客", {}), row.get("segments", {}).get("老客", {})
        db.add(PracticalAnalysisDaily(owner_id=owner_id, year=int(row["date"][:4]), date=row["date"], activity=row.get("activity") or "",
            new_visitors=n.get("访客数"), new_clicks_people=n.get("点击人数"), new_avg_stay=n.get("平均停留时长(秒)"), new_pay_buyers=n.get("引导支付买家数"),
            old_visitors=o.get("访客数"), old_clicks_people=o.get("点击人数"), old_avg_stay=o.get("平均停留时长(秒)"), old_pay_buyers=o.get("引导支付买家数")))
        count += 1
    db.commit()
    return {"written": count, "years": available_years(db, owner_id)}


def available_years(db: Session, owner_id: str | None = None) -> list[int]:
    return list(db.scalars(select(PracticalAnalysisDaily.year).where(_owner_clause(PracticalAnalysisDaily, owner_id)).distinct().order_by(PracticalAnalysisDaily.year)).all())


def rows_in_range(db: Session, year: int, start: str, end: str, owner_id: str | None = None) -> list[dict]:
    rows = db.scalars(select(PracticalAnalysisDaily).where(_owner_clause(PracticalAnalysisDaily, owner_id), PracticalAnalysisDaily.year == year, PracticalAnalysisDaily.date >= start, PracticalAnalysisDaily.date <= end).order_by(PracticalAnalysisDaily.date)).all()
    return [{k: getattr(row, k) for k in ("date", "activity", "new_visitors", "new_clicks_people", "new_avg_stay", "new_pay_buyers", "old_visitors", "old_clicks_people", "old_avg_stay", "old_pay_buyers")} for row in rows]


def apply_fix(db: Session, year: int, date: str, field: str, delta: float, owner_id: str | None = None) -> dict:
    if field not in {"访客数", "点击人数"}: return {"ok": False, "error": "只支持修正 访客数/点击人数"}
    row = db.scalar(select(PracticalAnalysisFix).where(_owner_clause(PracticalAnalysisFix, owner_id), PracticalAnalysisFix.year == year, PracticalAnalysisFix.date == date, PracticalAnalysisFix.field == field))
    if row: row.delta = delta
    else: db.add(PracticalAnalysisFix(owner_id=owner_id, year=year, date=date, field=field, delta=delta))
    db.commit(); return {"ok": True, "date": date, "field": field, "delta": delta}


def get_fixes(db: Session, owner_id: str | None = None) -> list[dict]:
    return [{"year": r.year, "date": r.date, "field": r.field, "delta": r.delta} for r in db.scalars(select(PracticalAnalysisFix).where(_owner_clause(PracticalAnalysisFix, owner_id)).order_by(PracticalAnalysisFix.date)).all()]


def remove_fix(db: Session, year: int, date: str, field: str, owner_id: str | None = None) -> dict:
    db.execute(delete(PracticalAnalysisFix).where(_owner_clause(PracticalAnalysisFix, owner_id), PracticalAnalysisFix.year == year, PracticalAnalysisFix.date == date, PracticalAnalysisFix.field == field)); db.commit(); return {"ok": True}


def delete_year(db: Session, year: int, owner_id: str | None = None) -> dict:
    db.execute(delete(PracticalAnalysisDaily).where(_owner_clause(PracticalAnalysisDaily, owner_id), PracticalAnalysisDaily.year == year))
    db.execute(delete(PracticalAnalysisFix).where(_owner_clause(PracticalAnalysisFix, owner_id), PracticalAnalysisFix.year == year))
    db.commit()
    return {"ok": True}

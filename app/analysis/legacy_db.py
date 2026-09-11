"""Compatibility facade for the desktop metrics module.

The migrated parser/metrics code retains its small db API. A context-local
SQLAlchemy session lets the same calculation code run against PostgreSQL while
keeping HTTP handlers free of global connections.
"""
from __future__ import annotations

from contextvars import ContextVar
from app.services import analysis_db

_session_var: ContextVar = ContextVar("analysis_session", default=None)
_owner_var: ContextVar = ContextVar("analysis_owner", default=None)


def bind(db, owner_id=None):
    return (_session_var.set(db), _owner_var.set(owner_id))
def unbind(token):
    session_token, owner_token = token
    _session_var.reset(session_token); _owner_var.reset(owner_token)
def owner_id(): return _owner_var.get()

def _db():
    db = _session_var.get()
    if db is None: raise RuntimeError("analysis database session is not bound")
    return db

def store_overall(rows): return analysis_db.store_overall(_db(), rows, owner_id())
def apply_fix(year, date, field, delta): return analysis_db.apply_fix(_db(), year, date, field, delta, owner_id())
def remove_fix(year, date, field): return analysis_db.remove_fix(_db(), year, date, field, owner_id())
def get_fixes(): return analysis_db.get_fixes(_db(), owner_id())
def get_fixes_map(): return {(r["date"], r["field"]): r["delta"] for r in get_fixes()}
def available_years(): return analysis_db.available_years(_db(), owner_id())
def rows_in_range(year, start, end): return analysis_db.rows_in_range(_db(), year, start, end, owner_id())
def delete_year(year):
    return analysis_db.delete_year(_db(), year, owner_id())

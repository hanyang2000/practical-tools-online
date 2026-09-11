from __future__ import annotations

import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from app.auth import SESSION_COOKIE
from app.config import get_settings
from app.db import SessionLocal
from app.models import PracticalAuthAccount, PracticalAuthSession, User, UserSession, utcnow
from app.security import hash_password, hash_session_token, issue_session_token, verify_password


ROOT = Path(__file__).resolve().parents[1]


def test_default_port_and_shared_cookie_name(monkeypatch):
    monkeypatch.delenv("PRACTICAL_PORT", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().port == 18180
        assert SESSION_COOKIE == "practical_tools_session"
    finally:
        get_settings.cache_clear()


def test_reference_password_format_is_compatible():
    encoded = hash_password("a-reference-compatible-password")
    algorithm, rounds, salt, digest = encoded.split("$", 3)
    assert algorithm == "pbkdf2_sha256"
    assert rounds == "600000"
    assert salt and digest
    assert verify_password("a-reference-compatible-password", encoded)
    assert not verify_password("wrong", encoded)


def test_login_cookie_and_hashed_session(client, make_user):
    user = make_user()
    response = client.post(
        "/api/auth/login",
        json={"username": " PLANNER ", "password": "correct-password"},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "practical_tools_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    with SessionLocal() as db:
        session = db.scalar(select(PracticalAuthSession))
        assert session is not None
        assert re.fullmatch(r"[0-9a-f]{64}", session.token_hash)
        assert session.token_hash not in cookie


def test_first_setup_can_create_owner_in_standalone_center(client):
    response = client.post(
        "/api/auth/setup",
        json={"username": "new-admin", "password": "a-new-admin-password"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "new-admin"
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "new-admin"))
        account = db.scalar(select(PracticalAuthAccount).where(PracticalAuthAccount.username == "new-admin"))
        assert user is not None and user.role == "admin"
        assert account is not None and account.owner_id == user.id


def test_existing_shared_session_cookie_is_not_accepted(client, make_user):
    user = make_user()
    raw = issue_session_token()
    with SessionLocal() as db:
        db.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_session_token(raw),
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        db.commit()
    client.cookies.set(SESSION_COOKIE, raw)
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_inactive_user_with_existing_session_is_rejected(client, make_user):
    user = make_user("inactive-session", active=False)
    raw = issue_session_token()
    with SessionLocal() as db:
        db.add(UserSession(user_id=user.id, token_hash=hash_session_token(raw), expires_at=utcnow() + timedelta(days=1)))
        db.commit()
    client.cookies.set(SESSION_COOKIE, raw)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_preserves_reference_all_sessions_semantics(client, make_user):
    user = make_user()
    assert client.post("/api/auth/login", json={"username": "planner", "password": "correct-password"}).status_code == 200
    raw = issue_session_token()
    with SessionLocal() as db:
        db.add_all([
            UserSession(user_id=user.id, token_hash=hash_session_token(raw), expires_at=utcnow() + timedelta(days=1)),
            UserSession(user_id=user.id, token_hash=hash_session_token(issue_session_token()), expires_at=utcnow() + timedelta(days=1)),
        ])
        db.commit()
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert "practical_tools_session=" in response.headers["set-cookie"]
    with SessionLocal() as db:
        assert len(db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()) == 2


def test_https_configuration_sets_secure_cookie(client, make_user, monkeypatch):
    make_user()
    monkeypatch.setenv("PRACTICAL_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    try:
        response = client.post("/api/auth/login", json={"username": "planner", "password": "correct-password"})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/auth/me"),
        ("GET", "/api/analysis/status"),
        ("GET", "/api/analysis/files"),
        ("GET", "/api/screenshot/list"),
        ("GET", "/api/screenshot/shops"),
        ("GET", "/api/screenshot/capture/status"),
        ("GET", "/api/screenshot/progress"),
        ("POST", "/api/screenshot/capture"),
        ("POST", "/api/agent/pair"),
    ],
)
def test_unauthenticated_business_api_is_401(client, method, path):
    kwargs = {"json": {}} if method == "POST" else {}
    response = client.request(method, path, **kwargs)
    assert response.status_code == 401, (path, response.status_code, response.text)


def test_inactive_user_and_wrong_password_share_401(client, make_user):
    make_user("inactive", active=False)
    inactive = client.post("/api/auth/login", json={"username": "inactive", "password": "correct-password"})
    missing = client.post("/api/auth/login", json={"username": "missing", "password": "correct-password"})
    wrong = client.post("/api/auth/login", json={"username": "inactive", "password": "wrong-password"})
    assert {inactive.status_code, missing.status_code, wrong.status_code} == {401}
    assert inactive.json() == missing.json() == wrong.json()


def test_migration_uses_independent_version_table_and_never_owns_shared_tables():
    env_text = (ROOT / "migrations/env.py").read_text(encoding="utf-8")
    migration_text = (ROOT / "migrations/versions/0001_practical_schema.py").read_text(encoding="utf-8")
    ini_text = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    assert 'version_table="practical_alembic_version"' in env_text
    assert "version_table = practical_alembic_version" in ini_text
    assert 'table.name.startswith("practical_")' in migration_text
    assert "create_table('users'" not in migration_text
    assert "drop_table('users'" not in migration_text
    assert "create_table('user_sessions'" not in migration_text
    assert "drop_table('user_sessions'" not in migration_text


def test_alembic_upgrade_preserves_preexisting_shared_rows(tmp_path):
    database = tmp_path / "migration.db"
    setup = f"""
import sqlite3
c=sqlite3.connect({str(database)!r})
c.executescript('''
CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(80), display_name VARCHAR(120), password_hash VARCHAR(255), role VARCHAR(20), active BOOLEAN, avatar_storage_key VARCHAR(500), created_at DATETIME, updated_at DATETIME);
CREATE TABLE user_sessions (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36), token_hash VARCHAR(64), expires_at DATETIME, created_at DATETIME, last_seen_at DATETIME);
INSERT INTO users VALUES ('u1','shared','Shared','hash','planner',1,NULL,'2026-01-01','2026-01-01');
INSERT INTO user_sessions VALUES ('s1','u1','tokenhash','2099-01-01','2026-01-01','2026-01-01');
''')
c.commit()
"""
    env = {
        **__import__("os").environ,
        "PRACTICAL_DATABASE_URL": f"sqlite:///{database}",
        "PRACTICAL_AUTO_CREATE_TABLES": "false",
    }
    subprocess.run([sys.executable, "-c", setup], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT, env=env)
    query = f"""
import json, sqlite3
c=sqlite3.connect({str(database)!r})
print(json.dumps({{
 'users': c.execute('select id,username from users').fetchall(),
 'sessions': c.execute('select id,user_id from user_sessions').fetchall(),
 'versions': c.execute('select version_num from practical_alembic_version').fetchall(),
 'tables': [r[0] for r in c.execute("select name from sqlite_master where type='table'")],
}}))
"""
    completed = subprocess.run([sys.executable, "-c", query], check=True, cwd=ROOT, capture_output=True, text=True)
    payload = __import__("json").loads(completed.stdout)
    assert payload["users"] == [["u1", "shared"]]
    assert payload["sessions"] == [["s1", "u1"]]
    assert payload["versions"] == [["0007_capture_job_claim_index"]]
    assert "alembic_version" not in payload["tables"]
    assert any(name.startswith("practical_") for name in payload["tables"])

    # Exercise the independent chain in both directions. Shared authentication
    # rows must survive the online migration's downgrade and second upgrade.
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], check=True, cwd=ROOT, env=env)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT, env=env)
    repeated = subprocess.run([sys.executable, "-c", query], check=True, cwd=ROOT, capture_output=True, text=True)
    repeated_payload = __import__("json").loads(repeated.stdout)
    assert repeated_payload["users"] == [["u1", "shared"]]
    assert repeated_payload["sessions"] == [["s1", "u1"]]
    assert repeated_payload["versions"] == [["0007_capture_job_claim_index"]]
    assert "alembic_version" not in repeated_payload["tables"]


def test_frontend_avatar_contract(authenticated_client):
    response = authenticated_client.post(
        "/api/auth/avatar",
        files={"file": ("avatar.png", b"not-important-for-route-check", "image/png")},
    )
    assert response.status_code == 200

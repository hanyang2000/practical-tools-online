from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest


# app.db creates its engine at import time, so isolate it before any test module
# imports application code.
TEST_ROOT = Path(tempfile.mkdtemp(prefix="practical-tools-tests-"))
os.environ["PRACTICAL_DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["PRACTICAL_DATA_ROOT"] = str(TEST_ROOT / "data")
os.environ["PRACTICAL_AUTO_CREATE_TABLES"] = "true"
os.environ["PRACTICAL_COOKIE_SECURE"] = "false"
os.environ["PRACTICAL_STORAGE_BACKEND"] = "local"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import PracticalAuthAccount, User  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(autouse=True)
def clean_application_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PRACTICAL_STORAGE_BACKEND", "local")
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.commit()
    data_root = TEST_ROOT / "data"
    if data_root.exists():
        shutil.rmtree(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as value:
        yield value


@pytest.fixture
def make_user() -> Callable[..., User]:
    def factory(
        username: str = "planner",
        password: str = "correct-password",
        *,
        active: bool = True,
        role: str = "planner",
    ) -> User:
        with SessionLocal() as db:
            user = User(
                username=username,
                display_name=username.title(),
                password_hash=hash_password(password),
                role=role,
                active=active,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            db.add(PracticalAuthAccount(username=username, password_hash=hash_password(password), role=role, active=active, owner_id=user.id))
            db.commit()
            db.expunge(user)
            return user

    return factory


@pytest.fixture
def authenticated_client(client: TestClient, make_user: Callable[..., User]) -> TestClient:
    make_user()
    response = client.post(
        "/api/auth/login",
        json={"username": "planner", "password": "correct-password"},
    )
    assert response.status_code == 200, response.text
    return client


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    shutil.rmtree(TEST_ROOT, ignore_errors=True)

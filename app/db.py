from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, **({"connect_args": {"check_same_thread": False, "timeout": 30}} if settings.database_url.startswith("sqlite") else {"pool_pre_ping": True}))

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_fk(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema_for_development() -> None:
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def ensure_auth_tables() -> None:
    """Create only this app's auth tables on every green deployment start."""
    from app import models  # noqa: F401
    models.PracticalAuthAccount.__table__.create(bind=engine, checkfirst=True)
    models.PracticalAuthSession.__table__.create(bind=engine, checkfirst=True)

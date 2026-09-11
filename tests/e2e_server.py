from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import delete

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import PracticalAuthAccount, User
from app.security import hash_password


Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(delete(table))
    user = User(
        username="e2e-user",
        display_name="E2E User",
        password_hash=hash_password("e2e-correct-password"),
        role="planner",
        active=True,
    )
    db.add(user)
    db.flush()
    db.add(PracticalAuthAccount(
        username="e2e-user",
        password_hash=hash_password("e2e-correct-password"),
        role="planner",
        active=True,
        owner_id=user.id,
    ))
    db.commit()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=18181, log_level="warning")

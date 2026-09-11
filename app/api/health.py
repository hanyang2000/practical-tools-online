from fastapi import APIRouter
from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health(): return {"ok": True, "service": "practical-tools-online", "version": __version__}

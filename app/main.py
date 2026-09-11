from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from app import __version__
from app.api import admin, analysis, auth_routes, health, screenshot, shell
from app.auth import CurrentAdmin
from app.config import get_settings
from app.db import create_schema_for_development, ensure_auth_tables

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
FRONTEND_DIR = APP_DIR.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings(); settings.ensure_directories()
    ensure_auth_tables()
    if settings.auto_create_tables: create_schema_for_development()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="策划实用小工具在线版", version=__version__, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def no_stale_pages(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # Only cache assets served by our static mounts.  Never attach a
        # browser cache policy to API responses, even if an API path happens
        # to end in a static-looking suffix.
        is_static_asset = path == "/icon.png" or path.startswith(("/static/", "/screenshot/", "/analysis/"))
        if path in {"/", "/favicon.ico"} or (is_static_asset and path.endswith(".html")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        elif is_static_asset and path.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2")):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response

    app.include_router(health.router, prefix="/api")
    app.include_router(auth_routes.router, prefix="/api")
    app.include_router(analysis.router, prefix="/api/analysis")
    app.include_router(screenshot.router, prefix="/api")
    app.include_router(screenshot.agent_router, prefix="/api")
    app.include_router(shell.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    # The migrated HTML pages remain unbundled to preserve their exact DOM,
    # CSS and relative JavaScript asset paths.
    for name in ("screenshot", "analysis"):
        page_dir = FRONTEND_DIR / name
        if page_dir.is_dir():
            app.mount(f"/{name}", StaticFiles(directory=page_dir, html=True), name=f"frontend-{name}")

    @app.get("/admin/", include_in_schema=False)
    def admin_page(user: CurrentAdmin):
        page = FRONTEND_DIR / "admin" / "index.html"
        return FileResponse(page)

    @app.get("/", include_in_schema=False)
    def root():
        index = FRONTEND_DIR / "shell" / "index.html"
        if not index.is_file(): index = STATIC_DIR / "index.html"
        return FileResponse(index) if index.is_file() else JSONResponse({"service": "practical-tools-online", "login": "/api/auth/status"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        path = STATIC_DIR / "favicon.ico"
        return FileResponse(path) if path.is_file() else Response(status_code=204)

    @app.get("/icon.png", include_in_schema=False)
    def root_icon():
        path = FRONTEND_DIR / "shell" / "icon.png"
        return FileResponse(path) if path.is_file() else Response(status_code=204)

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    try:
        uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
    except OSError as exc:
        raise RuntimeError(f"端口 {settings.port} 已被占用；请先退出主图一条龙服务协同版或另一份中心服务") from exc


if __name__ == "__main__": run()

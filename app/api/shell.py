from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import socket
from urllib.parse import urljoin, urlparse

import requests
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.config import get_settings
from app.db import get_db
from app.services.storage import store_bytes, resolve_storage_key

router = APIRouter(tags=["shell"])

FAVICON_MAX_BYTES = 512 * 1024
FAVICON_TIMEOUT = (3, 8)
FAVICON_CACHE_MAX_AGE = 7 * 24 * 60 * 60


def _public_http_url(value: str):
    """Validate a favicon target before any center-host request is made."""
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "收藏网址必须是 http 或 https 地址")
    if parsed.username or parsed.password:
        raise HTTPException(400, "收藏网址不允许携带账号凭据")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        raise HTTPException(502, "收藏网址的域名无法解析")
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise HTTPException(403, "不允许获取内网或本机地址的图标")
    return parsed


def _favicon_cache_paths(url: str):
    settings = get_settings()
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    base = settings.cache_root / "favicons" / hashlib.sha256(origin.encode("utf-8")).hexdigest()[:32]
    return base.with_suffix(".bin"), base.with_suffix(".mime")


def _read_favicon_response(response):
    if response.status_code != 200:
        return None
    content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/"):
        return None
    length = response.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > FAVICON_MAX_BYTES):
        return None
    data = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if chunk:
            data.extend(chunk)
            if len(data) > FAVICON_MAX_BYTES:
                return None
    return bytes(data), content_type


def _fetch_favicon(url: str):
    """Fetch a small public favicon with bounded redirects and disk caching."""
    cache_path, mime_path = _favicon_cache_paths(url)
    try:
        if cache_path.is_file() and mime_path.is_file():
            return cache_path.read_bytes(), mime_path.read_text(encoding="ascii").strip() or "image/x-icon"
    except OSError:
        pass

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        urljoin(url, "/favicon.ico"),
        urljoin(url, "/favicon.png"),
        urljoin(url, "/apple-touch-icon.png"),
    ]
    page_html = None
    for candidate in candidates:
        current = candidate
        for _ in range(3):
            _public_http_url(current)
            try:
                response = requests.get(current, headers={"Accept": "image/*", "User-Agent": "PracticalToolsFavicon/1.0"}, timeout=FAVICON_TIMEOUT, allow_redirects=False, stream=True)
            except requests.RequestException:
                break
            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    break
                current = urljoin(current, location)
                continue
            result = _read_favicon_response(response)
            if result:
                data, mime = result
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = cache_path.with_name(f".{cache_path.name}.tmp-{secrets.token_hex(8)}")
                    temporary.write_bytes(data)
                    temporary.replace(cache_path)
                    mime_path.write_text(mime, encoding="ascii")
                except OSError:
                    pass
                return data, mime
            break

    # Some sites only publish a non-root icon in the HTML head. Fetch the page
    # only after the common favicon paths fail, and still apply the same public
    # host/redirect/size checks to every discovered URL.
    try:
        _public_http_url(url)
        response = requests.get(url, headers={"Accept": "text/html", "User-Agent": "PracticalToolsFavicon/1.0"}, timeout=FAVICON_TIMEOUT, allow_redirects=False, stream=True)
        if response.status_code == 200 and "text/html" in str(response.headers.get("content-type", "")).lower():
            chunks = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    chunks.extend(chunk)
                    if len(chunks) > 256 * 1024:
                        break
            page_html = bytes(chunks).decode("utf-8", errors="ignore")
    except (HTTPException, requests.RequestException):
        page_html = None
    if page_html:
        tags = re.findall(r"<link\b[^>]*>", page_html, flags=re.I)
        for tag in tags:
            rel = re.search(r"\brel\s*=\s*['\"]([^'\"]+)['\"]", tag, flags=re.I)
            href = re.search(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", tag, flags=re.I)
            if not rel or not href or not any(word in rel.group(1).lower().split() for word in ("icon", "shortcut", "apple-touch-icon")):
                continue
            icon_url = urljoin(url, href.group(1).strip())
            if icon_url.startswith("data:"):
                continue
            try:
                _public_http_url(icon_url)
            except HTTPException:
                continue
            try:
                response = requests.get(icon_url, headers={"Accept": "image/*", "User-Agent": "PracticalToolsFavicon/1.0"}, timeout=FAVICON_TIMEOUT, allow_redirects=False, stream=True)
                result = _read_favicon_response(response)
            except requests.RequestException:
                result = None
            if result:
                data, mime = result
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = cache_path.with_name(f".{cache_path.name}.tmp-{secrets.token_hex(8)}")
                    temporary.write_bytes(data)
                    temporary.replace(cache_path)
                    mime_path.write_text(mime, encoding="ascii")
                except OSError:
                    pass
                return data, mime
    return None


@router.get("/avatar/status")
@router.get("/auth/avatar/status")
def avatar_status(user: CurrentUser): return {"custom": bool(user.avatar_storage_key), "avatar_custom": bool(user.avatar_storage_key)}


@router.get("/avatar")
@router.get("/auth/avatar")
@router.get("/avatar/file")
@router.get("/auth/avatar/file")
def avatar_file(user: CurrentUser):
    if not user.avatar_storage_key: raise HTTPException(404, "使用默认头像")
    path = resolve_storage_key(get_settings(), user.avatar_storage_key)
    if not path.is_file(): raise HTTPException(404, "头像文件不存在")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})


@router.post("/avatar")
@router.post("/auth/avatar")
async def upload_avatar(user: CurrentUser, file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read()
    if len(data) > 1024 * 1024: raise HTTPException(400, "头像不能超过 1MB")
    suffix = ".png" if (file.filename or "").lower().endswith(".png") else ".jpg"
    key = f"avatars/{user.id}/{__import__('uuid').uuid4().hex}{suffix}"; stored = store_bytes(get_settings(), key, data, overwrite=False); user.avatar_storage_key = stored.storage_key; db.commit(); return {"ok": True, "custom": True}


@router.post("/avatar/reset")
@router.post("/auth/avatar/reset")
def reset_avatar(user: CurrentUser, db: Session = Depends(get_db)):
    user.avatar_storage_key = None; db.commit(); return {"ok": True, "custom": False}


@router.post("/shell/{name}/exit")
def exit_app(user: CurrentUser, name: str): return {"ok": True, "name": name}


@router.post("/shell/exit-all")
def exit_all(user: CurrentUser): return {"ok": True}


@router.post("/shell/quit")
def quit_app(user: CurrentUser): return {"ok": True, "hint": "在线中心服务不会被网页退出操作关闭"}


@router.get("/shell/launchd/status")
def launchd_status(user: CurrentUser): return {"installed": False, "supported": False}


@router.post("/shell/launchd/install")
def launchd_install(user: CurrentUser): return {"ok": False, "supported": False, "hint": "请在采集 Agent 中配置开机自启"}


@router.post("/shell/launchd/uninstall")
def launchd_uninstall(user: CurrentUser): return {"ok": True, "installed": False}


@router.get("/shell/favicon")
def favicon_proxy(user: CurrentUser, url: str):
    parsed = _public_http_url(url)
    result = _fetch_favicon(parsed.geturl())
    if not result:
        raise HTTPException(404, "未找到该网址的图标")
    data, content_type = result
    return Response(data, media_type=content_type, headers={
        "Cache-Control": f"private, max-age={FAVICON_CACHE_MAX_AGE}",
        "X-Content-Type-Options": "nosniff",
    })

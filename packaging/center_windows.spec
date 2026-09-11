# -*- mode: python ; coding: utf-8 -*-
"""中心服务 Windows onedir 包。

不把数据库、账号密码或 COS 密钥打进包；生产配置由同目录 .env 提供。
"""
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
version_file = str(ROOT / "packaging" / "windows" / "version_info.txt") if sys.platform == "win32" else None
app_dir = ROOT / "app"
datas = []
if (app_dir / "static").is_dir(): datas.append((str(app_dir / "static"), "app/static"))
if (ROOT / "migrations").is_dir(): datas.append((str(ROOT / "migrations"), "migrations"))
if (ROOT / "frontend").is_dir(): datas.append((str(ROOT / "frontend"), "frontend"))
hiddenimports = [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan", "uvicorn.lifespan.on",
    "multipart", "multipart.multipart", "psycopg", "psycopg.pq",
] + collect_submodules("app")

a = Analysis([str(app_dir / "main.py")], pathex=[str(ROOT)], binaries=[], datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PracticalToolsOnline", debug=False,
         bootloader_ignore_signals=False, strip=False, upx=False, console=False,
         version=version_file)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PracticalToolsOnline")

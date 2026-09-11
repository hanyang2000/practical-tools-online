# -*- mode: python ; coding: utf-8 -*-
"""无控制台窗口的中心桌面控制台。"""
from pathlib import Path
import sys


ROOT = Path(SPECPATH).resolve().parent
version_file = str(ROOT / "packaging" / "windows" / "version_info.txt") if sys.platform == "win32" else None
icon_file = str(ROOT / "packaging" / "windows" / "installer" / "CenterConsole.ico") if sys.platform == "win32" else None
script = ROOT / "packaging" / "center_console.py"

a = Analysis(
    [str(script)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PracticalToolsCenterConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    version=version_file,
    icon=icon_file,
)

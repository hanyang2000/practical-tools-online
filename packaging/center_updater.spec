# -*- mode: python ; coding: utf-8 -*-
"""Small dedicated updater shipped beside the green center package."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
a = Analysis([str(ROOT / "packaging" / "update_worker.py")], pathex=[str(ROOT)], binaries=[], datas=[],
             hiddenimports=collect_submodules("app"), hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CenterUpdater", debug=False,
         bootloader_ignore_signals=False, strip=False, upx=False, console=False)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="CenterUpdater")

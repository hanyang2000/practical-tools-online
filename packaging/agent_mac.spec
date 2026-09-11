# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for macOS local capture Agent.

Patchright's browser binary is installed separately by install_agent.command;
no cookies, storage state or COS credentials are bundled.
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
import patchright

ROOT = Path(SPECPATH).resolve().parent
agent_dir = ROOT / "agent"
patchright_driver = Path(patchright.__file__).resolve().parent / "driver"
hiddenimports = (
    collect_submodules("patchright")
    + collect_submodules("rapidocr_onnxruntime")
    + collect_submodules("onnxruntime")
    + ["yaml", "PIL", "requests", "keyring", "keyring.backends.macOS"]
)
datas = [
    (str(agent_dir / "screenshot" / "config.yaml"), "screenshot"),
    (str(agent_dir / "screenshot" / "ocr_tool"), "screenshot/ocr_tool"),
    # Patchright resolves its Node transport at runtime from
    # ``patchright/driver/node`` and loads the JS package beside it. Python
    # hidden imports alone do not include either resource in a one-file build.
    (str(patchright_driver / "package"), "patchright/driver/package"),
]
datas += collect_data_files("rapidocr_onnxruntime", includes=["models/*.onnx", "*.yaml"])
binaries = [(str(patchright_driver / "node"), "patchright/driver")]
binaries += collect_dynamic_libs("onnxruntime")

a = Analysis(
    [str(agent_dir / "agent.py")],
    pathex=[str(agent_dir)],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PracticalToolsAgent", debug=False,
         bootloader_ignore_signals=False, strip=False, upx=False, console=True)

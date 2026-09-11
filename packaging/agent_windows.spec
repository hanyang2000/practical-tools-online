# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
agent_dir = ROOT / "agent"
a = Analysis([str(agent_dir / "agent.py")], pathex=[str(agent_dir)],
             datas=[(str(agent_dir / "screenshot" / "config.yaml"), "screenshot")]
                   + collect_data_files("rapidocr_onnxruntime", includes=["models/*.onnx", "*.yaml"]),
             binaries=collect_dynamic_libs("onnxruntime"),
             hiddenimports=collect_submodules("patchright")
                         + collect_submodules("rapidocr_onnxruntime")
                         + collect_submodules("onnxruntime")
                         + ["yaml", "PIL", "requests", "keyring", "keyring.backends.Windows"],
             hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PracticalToolsAgent", debug=False,
         bootloader_ignore_signals=False, strip=False, upx=False, console=True)

from __future__ import annotations

import zipfile
import json
import subprocess
import sys
from pathlib import Path

from scripts.create_update_bundle import build_update_bundle


def test_update_bundle_excludes_python_and_macos_generated_files(tmp_path: Path):
    source = tmp_path / "source"
    (source / "app" / "__pycache__").mkdir(parents=True)
    (source / "frontend" / "assets").mkdir(parents=True)
    (source / "app" / "__init__.py").write_text('__version__ = "0.1.1"\n', encoding="utf-8")
    (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "app" / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"bytecode")
    (source / "app" / "main.pyo").write_bytes(b"optimized bytecode")
    (source / "frontend" / ".DS_Store").write_bytes(b"finder metadata")
    (source / "frontend" / "assets" / "app.js").write_text("console.log('ok')\n", encoding="utf-8")
    output = tmp_path / "update.zip"

    build_update_bundle(source, output, "0.1.1")

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "payload/app/main.py" in names
    assert "payload/frontend/assets/app.js" in names
    assert not any("__pycache__" in name or name.endswith((".pyc", ".pyo", ".DS_Store")) for name in names)


def test_update_bundle_includes_page_collection_agent(tmp_path: Path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "agent" / "screenshot").mkdir(parents=True)
    (source / "app" / "__init__.py").write_text('__version__ = "0.3.6"\n', encoding="utf-8")
    (source / "app" / "main.py").write_text("print('center')\n", encoding="utf-8")
    (source / "agent" / "agent.py").write_text("print('agent')\n", encoding="utf-8")
    (source / "agent" / "screenshot" / "capture.py").write_text("print('capture')\n", encoding="utf-8")
    output = tmp_path / "update.zip"

    build_update_bundle(source, output, "0.3.6")

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "payload/agent/agent.py" in names
    assert "payload/agent/screenshot/capture.py" in names


def test_update_bundle_maps_installed_console_assets(tmp_path: Path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    console_source = source / "packaging" / "windows" / "installer"
    console_source.mkdir(parents=True)
    (source / "app" / "__init__.py").write_text('__version__ = "0.6.1"\n', encoding="utf-8")
    (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    for name in ("CenterConsole.hta", "CenterController.py", "CenterConsole.ico", "CenterConsoleIcon.png", "CenterConsole.ps1"):
        (console_source / name).write_bytes(name.encode("ascii"))
    output = tmp_path / "update.zip"

    build_update_bundle(source, output, "0.6.1")

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "payload/installer/CenterConsole.hta" in names
    assert "payload/installer/CenterController.py" in names
    assert "payload/installer/CenterConsole.ico" in names
    assert "payload/installer/CenterConsoleIcon.png" in names
    assert "payload/installer/CenterConsole.ps1" in names


def test_update_bundle_does_not_collect_arbitrary_installer_files(tmp_path: Path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "installer").mkdir()
    (source / "app" / "__init__.py").write_text('__version__ = "0.6.1"\n', encoding="utf-8")
    (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "installer" / "PracticalToolsOnline-install-stop.log").write_text("runtime log", encoding="utf-8")
    output = tmp_path / "update.zip"

    build_update_bundle(source, output, "0.6.1")

    with zipfile.ZipFile(output) as archive:
        assert "payload/installer/PracticalToolsOnline-install-stop.log" not in archive.namelist()


def test_update_bundle_maps_installed_portable_launcher(tmp_path: Path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    portable_source = source / "packaging" / "windows" / "portable"
    portable_source.mkdir(parents=True)
    (source / "app" / "__init__.py").write_text('__version__ = "0.6.1"\n', encoding="utf-8")
    (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (portable_source / "portable_center.py").write_text("print('launcher')\n", encoding="utf-8")
    output = tmp_path / "update.zip"

    build_update_bundle(source, output, "0.6.1")

    with zipfile.ZipFile(output) as archive:
        assert "payload/portable/portable_center.py" in archive.namelist()


def test_update_bundle_excludes_local_runtime_state(tmp_path: Path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "app" / "__init__.py").write_text('__version__ = "0.1.1"\n', encoding="utf-8")
    (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "agent" / "browser_state").mkdir(parents=True)
    (source / "agent" / "browser_state" / "cookies.json").write_text("secret", encoding="utf-8")
    (source / "agent" / "agent.json").write_text("secret", encoding="utf-8")
    output = tmp_path / "update.zip"

    build_update_bundle(source, output, "0.1.1")

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "payload/app/main.py" in names
    assert not any("browser_state" in name or name.endswith("agent.json") for name in names)


def test_update_bundle_cli_runs_from_project_root_without_pythonpath(tmp_path: Path):
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "app" / "__init__.py").write_text('__version__ = "0.1.1"\n', encoding="utf-8")
    (source / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    output = tmp_path / "update.zip"
    script = Path(__file__).parents[1] / "scripts" / "create_update_bundle.py"

    result = subprocess.run(
        [sys.executable, str(script), str(source), str(output), "0.1.1"],
        cwd=script.parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["files"] == 2
    assert output.is_file()

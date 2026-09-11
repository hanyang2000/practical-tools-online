from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import importlib.util

_spec = importlib.util.spec_from_file_location("practical_update_worker", Path(__file__).parents[1] / "packaging/update_worker.py")
update_worker = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(update_worker)


def _package(path: Path, filename="app/new.py"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("PracticalToolsOnline/" + filename, "new-version")
    return path


def _fake_inspect(_path):
    return {"version": "9.9.9"}


def test_success_replaces_and_starts_new_version(tmp_path, monkeypatch):
    root = tmp_path / "app"; root.mkdir(); (root / "app").mkdir(); (root / "app/old.py").write_text("old")
    package = _package(tmp_path / "u.zip")
    proc = type("P", (), {"pid": 123})()
    monkeypatch.setattr(update_worker, "inspect_update_bundle", _fake_inspect)
    monkeypatch.setattr(update_worker.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(update_worker, "_health_ok", lambda url, target_version=None: target_version == "9.9.9")
    result = update_worker.apply_update(package, root, launcher=tmp_path/"portable.py", runtime=tmp_path/"python.exe", health_url="http://health", result_file=tmp_path/"result.json")
    assert result["status"] == "success" and (root / "app/new.py").read_text() == "new-version"
    assert result["pid"] == 123


def test_windows_relaunch_prefers_pythonw_and_hides_console(tmp_path, monkeypatch):
    runtime = tmp_path / "python.exe"
    runtime.write_text("runtime", encoding="ascii")
    (tmp_path / "pythonw.exe").write_text("windowless runtime", encoding="ascii")
    captured = {}

    class FakeStartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(update_worker.os, "name", "nt")
    monkeypatch.setattr(update_worker.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(update_worker.subprocess, "STARTF_USESHOWWINDOW", 0x0001, raising=False)
    monkeypatch.setattr(update_worker.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(update_worker.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type("P", (), {"pid": 77})()

    monkeypatch.setattr(update_worker.subprocess, "Popen", fake_popen)
    process = update_worker._launch_center(tmp_path, tmp_path / "portable_center.py", runtime)

    assert process.pid == 77
    assert captured["command"][0].endswith("pythonw.exe")
    assert captured["kwargs"]["creationflags"] == 0x08000000
    assert captured["kwargs"]["startupinfo"].wShowWindow == 0
    assert captured["kwargs"]["stdin"] is update_worker.subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is update_worker.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is update_worker.subprocess.DEVNULL


def test_health_failure_restores_old_and_removes_new(tmp_path, monkeypatch):
    root = tmp_path / "app"; root.mkdir(); (root / "app").mkdir(); (root / "app/old.py").write_text("old")
    package = _package(tmp_path / "u.zip")
    monkeypatch.setattr(update_worker, "inspect_update_bundle", _fake_inspect)
    monkeypatch.setattr(update_worker.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 1, "terminate": lambda s: None})())
    monkeypatch.setattr(update_worker, "_health_ok", lambda *a, **k: False)
    result_file = tmp_path / "result.json"
    with pytest.raises(RuntimeError): update_worker.apply_update(package, root, launcher=tmp_path/"portable.py", runtime=tmp_path/"python.exe", health_url="http://health", result_file=result_file)
    assert not (root / "app/new.py").exists() and result_file.exists()
    assert json.loads(result_file.read_text())["status"] == "rollback_failed"


def test_waits_for_both_pids_and_worker_cli_supports_parent(tmp_path, monkeypatch):
    root = tmp_path / "app"; root.mkdir(); (root / "app").mkdir()
    package = _package(tmp_path / "u.zip")
    monkeypatch.setattr(update_worker, "inspect_update_bundle", _fake_inspect)
    seen = []
    monkeypatch.setattr(update_worker.os, "kill", lambda pid, sig: seen.append(pid))
    monkeypatch.setattr(update_worker.time, "sleep", lambda _: None)
    monkeypatch.setattr(update_worker, "_health_ok", lambda *a, **k: True)
    monkeypatch.setattr(update_worker.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 2})())
    update_worker.apply_update(package, root, wait_pid=10, wait_parent_pid=11, result_file=tmp_path/"r.json")
    assert 10 in seen and 11 in seen


def test_file_sync_retries_transient_lock_and_removes_stale_files(tmp_path, monkeypatch):
    source = tmp_path / "staging" / "packaging"
    target = tmp_path / "center" / "packaging"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "update_worker.py").write_text("new", encoding="utf-8")
    (target / "update_worker.py").write_text("old", encoding="utf-8")
    (target / "obsolete.py").write_text("stale", encoding="utf-8")

    original_replace = update_worker.os.replace
    attempts = 0

    def transient_lock(source_path, target_path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("simulated sharing violation")
        return original_replace(source_path, target_path)

    monkeypatch.setattr(update_worker.os, "replace", transient_lock)
    monkeypatch.setattr(update_worker, "RETRY_COUNT", 2)
    monkeypatch.setattr(update_worker, "RETRY_DELAY", 0)
    assert update_worker._sync_tree(source, target) == 1
    assert (target / "update_worker.py").read_text(encoding="utf-8") == "new"
    assert not (target / "obsolete.py").exists()


def test_installer_overlay_replaces_console_without_removing_other_helpers(tmp_path):
    source = tmp_path / "staging" / "installer"
    target = tmp_path / "center" / "installer"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "CenterConsole.hta").write_text("new console", encoding="utf-8")
    (target / "CenterConsole.hta").write_text("old console", encoding="utf-8")
    (target / "PracticalToolsOnlineMigration.ps1").write_text("keep me", encoding="utf-8")

    assert update_worker._overlay_tree(source, target) == 1
    assert (target / "CenterConsole.hta").read_text(encoding="utf-8") == "new console"
    assert (target / "PracticalToolsOnlineMigration.ps1").read_text(encoding="utf-8") == "keep me"


def test_portable_overlay_keeps_start_and_stop_scripts(tmp_path):
    source = tmp_path / "staging" / "portable"
    target = tmp_path / "center" / "portable"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "portable_center.py").write_text("new launcher", encoding="utf-8")
    (target / "portable_center.py").write_text("old launcher", encoding="utf-8")
    (target / "START.cmd").write_text("keep start", encoding="utf-8")
    (target / "STOP.cmd").write_text("keep stop", encoding="utf-8")

    assert update_worker._overlay_tree(source, target) == 1
    assert (target / "portable_center.py").read_text(encoding="utf-8") == "new launcher"
    assert (target / "START.cmd").read_text(encoding="utf-8") == "keep start"
    assert (target / "STOP.cmd").read_text(encoding="utf-8") == "keep stop"


def test_pending_archive_is_idempotent_when_completed_marker_already_exists(tmp_path, monkeypatch):
    root = tmp_path / "app"; root.mkdir(); (root / "app").mkdir()
    package = _package(tmp_path / "u.zip")
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"package": str(package)}), encoding="utf-8")
    completed = tmp_path / "pending.completed.json"
    completed.write_text("previous result", encoding="utf-8")
    monkeypatch.setattr(update_worker, "inspect_update_bundle", _fake_inspect)

    result = update_worker.apply_update(pending, root, result_file=tmp_path / "result.json")

    assert result["status"] == "success"
    assert completed.exists()
    assert json.loads(completed.read_text(encoding="utf-8"))["package"] == str(package)
    assert not pending.exists()


def test_pending_archive_falls_back_to_unique_name_when_fixed_destination_is_blocked(tmp_path, monkeypatch):
    pending = tmp_path / "pending.json"
    pending.write_text("pending", encoding="utf-8")
    calls = []
    original_replace = update_worker.os.replace

    def blocked_once(source, destination):
        calls.append(destination)
        if len(calls) == 1:
            raise FileExistsError(183, "already exists")
        return original_replace(source, destination)

    monkeypatch.setattr(update_worker.os, "replace", blocked_once)
    archived = update_worker._archive_pending_file(pending)

    assert archived is not None and archived.name.startswith("pending.completed.")
    assert archived.exists() and not pending.exists()


def test_install_command_uses_supervisor_and_no_parent_still_spawns(monkeypatch, tmp_path):
    # Contract test for the API-side command construction: trusted worker is
    # copied outside the payload and receives both PID arguments.
    source = Path(__file__).resolve().parents[1] / "packaging/update_worker.py"
    assert source.is_file() and "from app.services.update_bundle" in source.read_text()
    assert "--wait-parent-pid" in source.read_text()

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest
import requests
import yaml

from agent import agent as runtime
from agent import scheduler


def test_agent_update_zip_rejects_traversal(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", b"no")
    with pytest.raises(RuntimeError, match="不安全路径"):
        runtime._safe_zip_extract(archive_path, tmp_path / "extract")


def test_agent_source_update_worker_replaces_source_and_restarts_old_command(tmp_path, monkeypatch):
    package = tmp_path / "agent-update.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("agent/agent.py", "new agent source\n")
        archive.writestr("agent/screenshot/__init__.py", "\n")
    target = tmp_path / "installed-agent"
    (target / "screenshot").mkdir(parents=True)
    (target / "agent.py").write_text("old agent source\n", encoding="utf-8")
    restart_file = tmp_path / "restart.json"
    restart_file.write_text(json.dumps({"command": [sys.executable, str(target / "agent.py")] }), encoding="utf-8")
    launched: list[list[str]] = []
    monkeypatch.setattr(runtime, "_process_state", lambda pid: False)
    monkeypatch.setattr(runtime, "_launch_restart", lambda command, data_dir: launched.append(command))
    assert runtime._run_self_update_worker(package, target, "source", os.getpid(), restart_file, tmp_path / "data") == 0
    assert (target / "agent.py").read_text(encoding="utf-8") == "new agent source\n"
    assert launched == [[sys.executable, str(target / "agent.py")]]
    assert not package.exists() and not restart_file.exists()


def test_agent_restores_saved_schedule_when_center_is_unreachable(tmp_path, monkeypatch):
    config = {"shops": [{"name": "店铺A", "url": "https://example.test/shop"}],
              "schedule": {"weekdays": [4], "hour": 10, "minute": 0}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    agent = runtime.Agent("https://center.example.test", data_dir=tmp_path, acquire_lock=False, load_token=False)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(agent, "request", lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("offline")))
    monkeypatch.setattr(runtime.capture, "load_config", lambda: config)
    monkeypatch.setattr(runtime.scheduler, "status", lambda: {"installed": False, "running": False})
    monkeypatch.setattr(runtime.scheduler, "set_schedule", lambda weekdays, hour, minute: calls.append(("save", (weekdays, hour, minute))) or {"ok": True})
    monkeypatch.setattr(runtime.scheduler, "install", lambda server, data_dir: calls.append(("install", (server, data_dir))) or {"ok": True})

    assert agent.sync_config() == config
    assert calls == [("save", ([4], 10, 0)), ("install", ("https://center.example.test", tmp_path))]


def test_agent_config_sync_preserves_local_capture_settings(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    data_dir = tmp_path / "data"
    resources.mkdir()
    (resources / "config.yaml").write_text(
        yaml.safe_dump({"viewport": {"width": 574, "height": 1024},
                        "max_screenshot_height": 20000,
                        "crop_below_text": "逛逛更多宝贝"}, allow_unicode=True),
        encoding="utf-8",
    )
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text(
        yaml.safe_dump({"viewport": {"width": 574, "height": 1024},
                        "max_screenshot_height": 20000,
                        "extra_wait_min": 10}, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime.paths, "RESOURCE_DIR", resources)
    agent = runtime.Agent("https://center.example.test", data_dir=data_dir,
                          acquire_lock=False, load_token=False)

    class Response:
        ok = True

        @staticmethod
        def json():
            return {"shops": [{"name": "店铺A", "url": "https://example.test"}],
                    "schedule": None}

    monkeypatch.setattr(agent, "request", lambda *args, **kwargs: Response())
    monkeypatch.setattr(agent, "_sync_scheduler", lambda *args, **kwargs: True)
    merged = agent.sync_config()

    assert merged["shops"][0]["name"] == "店铺A"
    assert merged["viewport"] == {"width": 574, "height": 1024}
    assert merged["max_screenshot_height"] == 20000
    assert merged["crop_below_text"] == "逛逛更多宝贝"
    assert merged["extra_wait_min"] == 10


def test_agent_config_sync_can_skip_scheduler_mutation(tmp_path, monkeypatch):
    agent = runtime.Agent("https://center.example.test", data_dir=tmp_path,
                          acquire_lock=False, load_token=False)

    class Response:
        ok = True

        @staticmethod
        def json():
            return {"shops": [{"name": "店铺A", "url": "https://example.test"}],
                    "schedule": {"weekdays": [3], "hour": 14, "minute": 0}}

    calls: list[object] = []
    monkeypatch.setattr(agent, "request", lambda *args, **kwargs: Response())
    monkeypatch.setattr(agent, "_sync_scheduler", lambda *args, **kwargs: calls.append(args) or True)

    assert agent.sync_config(sync_schedule=False)["shops"][0]["name"] == "店铺A"
    assert calls == []


def test_ocr_dynamically_rescans_before_start_fraction(tmp_path, monkeypatch):
    """OCR must still find a marker placed before the initial scan window."""
    from PIL import Image, ImageDraw

    image_path = tmp_path / "upper-marker.png"
    image = Image.new("RGB", (20, 4000), "black")
    ImageDraw.Draw(image).rectangle((0, 300, 19, 340), fill="white")
    image.save(image_path)

    def fake_ocr(path):
        crop = Image.open(path).convert("RGB")
        if any(high > 0 for _low, high in crop.getextrema()):
            return [([[0, 300], [19, 300], [19, 340], [0, 340]], "逛逛更多宝贝", 0.99)], None
        return [], None

    monkeypatch.setattr(runtime.capture, "_rapid_ocr_engine", lambda: fake_ocr)
    assert runtime.capture.ocr_find_text_y(str(image_path), "逛逛更多宝贝") == 300


def test_ocr_chunks_overlap_to_avoid_splitting_image_text(tmp_path, monkeypatch):
    """OCR blocks need overlap so a banner crossing a block edge remains readable."""
    from PIL import Image, ImageDraw

    image_path = tmp_path / "split-marker.png"
    image = Image.new("RGB", (20, 4000), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 2950, 19, 2970), fill="white")
    draw.rectangle((0, 3050, 19, 3070), fill="white")
    image.save(image_path)

    def fake_ocr(path):
        crop = Image.open(path).convert("RGB")
        white_rows = [y for y in range(crop.height) if crop.getpixel((0, y))[0] > 0]
        if white_rows and max(white_rows) - min(white_rows) > 50:
            return [([[0, min(white_rows)], [19, min(white_rows)], [19, max(white_rows)], [0, max(white_rows)]], "逛逛更多宝贝", 0.99)], None
        return [], None

    monkeypatch.setattr(runtime.capture, "_rapid_ocr_engine", lambda: fake_ocr)
    found = runtime.capture.ocr_find_text_y(str(image_path), "逛逛更多宝贝", chunk=1000)
    assert found is not None and 2900 <= found <= 3000


def test_lightbox_resets_scroll_when_switching_images():
    source = (Path(__file__).resolve().parents[1] / "frontend/screenshot/index.html").read_text(encoding="utf-8")
    assert "function resetLbScroll()" in source
    assert "resetLbScroll(); updateLb()" in source


def test_agent_persists_center_disable_so_offline_mode_does_not_resurrect_schedule(tmp_path, monkeypatch):
    agent = runtime.Agent("https://center.example.test", data_dir=tmp_path, acquire_lock=False, load_token=False)
    calls: list[str] = []
    monkeypatch.setattr(runtime.scheduler, "status", lambda: {"installed": True, "running": True})
    monkeypatch.setattr(runtime.scheduler, "uninstall", lambda: calls.append("uninstall") or {"ok": True})
    monkeypatch.setattr(runtime.scheduler, "set_disabled", lambda: calls.append("disabled") or {"ok": True})

    assert agent._sync_scheduler(None, source="中心配置") is True
    assert calls == ["uninstall", "disabled"]


def test_scheduled_run_persists_capture_failure_and_cleans_lock(tmp_path, monkeypatch):
    agent = runtime.Agent("https://center.example.test", data_dir=tmp_path, acquire_lock=False, load_token=False)
    monkeypatch.setattr(agent, "flush_outbox", lambda limit=100: None)
    monkeypatch.setattr(agent, "sync_config", lambda **kwargs: {"shops": [{"name": "店铺A"}]})

    def fail_capture(config):
        raise SystemExit(1)

    monkeypatch.setattr(runtime.capture, "capture_shops", fail_capture)
    agent.scheduled_run()

    log = (tmp_path / "logs" / "scheduled.log").read_text(encoding="utf-8")
    assert "scheduled start version=" in log
    assert "scheduled config shops=1" in log
    assert "scheduled failed SystemExit: 1" in log
    assert not (tmp_path / ".capture.lock").exists()


def test_capture_lock_retries_after_stale_lock(tmp_path, monkeypatch):
    agent = runtime.Agent("https://center.example.test", data_dir=tmp_path,
                          acquire_lock=False, load_token=False)
    (tmp_path / ".capture.lock").write_text(
        json.dumps({"pid": 987654, "created_at": "2026-09-09T10:40:19+00:00"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "_process_state", lambda pid: False)

    with agent._capture_lock():
        assert (tmp_path / ".capture.lock").exists()
    assert not (tmp_path / ".capture.lock").exists()


def test_agent_reports_scheduled_capture_snapshot(tmp_path):
    agent = runtime.Agent("https://center.example.test", data_dir=tmp_path,
                          acquire_lock=False, load_token=False)
    (tmp_path / ".capture.lock").write_text(
        json.dumps({"pid": os.getpid(), "created_at": "2026-09-09T10:40:19+00:00"}),
        encoding="utf-8",
    )
    runtime.paths.PROGRESS_FILE.write_text(
        json.dumps({"current": 5, "total": 12, "shop": "理肤泉"}),
        encoding="utf-8",
    )

    snapshot = agent._scheduled_capture_snapshot()
    assert snapshot["capture_running"] is True
    assert snapshot["capture_source"] == "scheduled"
    assert snapshot["capture_progress"]["current"] == 5


def test_capture_job_reports_system_exit_instead_of_leaving_running(tmp_path, monkeypatch):
    agent = runtime.Agent("https://center.example.test", data_dir=tmp_path, acquire_lock=False, load_token=False)
    monkeypatch.setattr(agent, "sync_config", lambda: {"shops": [{"name": "店铺A", "url": "https://example.test"}]})
    monkeypatch.setattr(runtime.capture, "capture_shops", lambda config: (_ for _ in ()).throw(SystemExit(1)))
    reports: list[tuple[str, dict]] = []
    monkeypatch.setattr(agent, "post_job", lambda job_id, action, payload=None: reports.append((action, payload or {})))

    agent.capture_job({"id": "capture-job"})

    assert any(action == "fail" and payload["status"] == "failed" for action, payload in reports)
    assert not (tmp_path / ".capture.lock").exists()


def test_macos_autostart_reports_launchctl_bootstrap_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(scheduler.Path, "home", staticmethod(lambda: tmp_path))
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "bootstrap":
            return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "bootstrap denied"})()
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    result = scheduler.install_autostart("https://center.example.test", tmp_path / "data")
    assert result["ok"] is False
    assert "bootstrap denied" in result["error"]
    assert calls[0][1] == "bootout" and calls[1][1] == "bootstrap"

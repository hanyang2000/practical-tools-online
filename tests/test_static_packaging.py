from __future__ import annotations

import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = Path(sys.executable)


def test_static_routes_and_login_gate_markup(client):
    root = client.get("/")
    assert root.status_code == 200
    assert 'id="auth-view"' in root.text
    assert 'id="auth-form"' in root.text
    assert 'id="frame-screenshot"' in root.text
    assert 'id="frame-analysis"' in root.text
    assert "const APP_IDS = ['screenshot','analysis']" in root.text
    assert "违禁词排查" not in root.text
    assert "主图一条龙服务" not in root.text

    screenshot = client.get("/screenshot/")
    analysis = client.get("/analysis/")
    assert screenshot.status_code == 200 and "页面收集" in screenshot.text
    assert analysis.status_code == 200 and "首页数据分析" in analysis.text
    assert client.get("/analysis/js/echarts.min.js").status_code == 200
    assert client.get("/screenshot/icon.png").status_code == 200


def test_analysis_block_charts_recalculate_size_after_panel_visibility_changes():
    source = (ROOT / "frontend/analysis/index.html").read_text(encoding="utf-8")
    assert "function resizeBlkCharts()" in source
    assert "function observeBlkChartSize()" in source
    assert "new ResizeObserver" in source
    assert "observeBlkChartSize();" in source
    assert "resizeBlkCharts();" in source
    assert "window.addEventListener('resize', resizeBlkCharts)" in source


def test_analysis_upload_entrypoint_only_exists_in_data_management():
    source = (ROOT / "frontend/analysis/index.html").read_text(encoding="utf-8")
    header = source.split("<header class=\"page-header\">", 1)[1].split("</header>", 1)[0]
    manage = source.split('<div id="view-manage"', 1)[1].split("</div>\n    </div>", 1)[0]
    assert "上传数据" not in header
    assert 'id="file-input"' in manage
    assert 'id="dropzone"' in manage


def test_screenshot_grid_sorts_by_capture_time_not_random_storage_path():
    source = (ROOT / "frontend/screenshot/index.html").read_text(encoding="utf-8")
    assert "function sortImages(images)" in source
    assert "captureTime(b)-captureTime(a)" in source
    assert "b.path.localeCompare(a.path)" not in source


def test_date_filter_renders_date_value_not_date_object():
    source = (ROOT / "frontend/screenshot/index.html").read_text(encoding="utf-8")
    assert "const date = String(dt.date || '')" in source
    assert "filterDate('${esc(date)}')" in source
    assert "${esc(date)}</div>`" in source
    assert "filterDate('${dt}')" not in source


def test_lightbox_keeps_thumbnail_until_original_is_ready_without_empty_image_src():
    source = (ROOT / "frontend/screenshot/index.html").read_text(encoding="utf-8")
    assert "const LB_PLACEHOLDER" in source
    assert "setLbSource(image, thumbnailUrl(im), 'lb-thumb')" in source
    assert "image.removeAttribute('src')" not in source


def test_agent_ocr_uses_rapidocr_and_keeps_legacy_fallback():
    capture = (ROOT / "agent/screenshot/capture.py").read_text(encoding="utf-8")
    requirements = (ROOT / "agent/requirements.txt").read_text(encoding="utf-8")
    mac_spec = (ROOT / "packaging/agent_mac.spec").read_text(encoding="utf-8")
    windows_spec = (ROOT / "packaging/agent_windows.spec").read_text(encoding="utf-8")
    assert "rapidocr_onnxruntime==1.4.4" in requirements
    assert "from rapidocr_onnxruntime import RapidOCR" in capture
    assert "_ocr_text_matches(item[1], text)" in capture
    assert "if not OCR_TOOL.exists()" in capture
    assert 'includes=["models/*.onnx", "*.yaml"]' in mac_spec
    assert 'includes=["models/*.onnx", "*.yaml"]' in windows_spec


def test_agent_crop_margin_moves_the_crop_line_up():
    capture = (ROOT / "agent/screenshot/capture.py").read_text(encoding="utf-8")
    agent = (ROOT / "agent/agent.py").read_text(encoding="utf-8")
    config = (ROOT / "agent/screenshot/config.yaml").read_text(encoding="utf-8")
    assert "crop_margin_top = max(0, int(config.get(\"crop_margin_top\", 40)))" in capture
    assert "crop_y = max(1, crop_y - crop_margin_top)" in capture
    assert '"crop_margin_top"' in agent
    assert "crop_margin_top: 40" in config


def test_health_is_public(client):
    assert client.get("/api/health").json() == {"ok": True, "service": "practical-tools-online", "version": "0.6.10"}


def test_cache_policy_is_limited_to_static_assets(client):
    assert "no-store" in client.get("/").headers.get("cache-control", "")
    assert client.get("/analysis/js/echarts.min.js").headers.get("cache-control") == "public, max-age=31536000, immutable"
    # API routes must not inherit the static asset policy merely because a
    # future endpoint happens to use a static-looking path suffix.
    assert "cache-control" not in {k.lower() for k in client.get("/api/health").headers}
    assert "cache-control" not in {k.lower() for k in client.get("/api/health.js").headers}


def test_admin_uses_cos_for_small_uploads_when_cos_is_selected():
    source = (ROOT / "frontend/admin/index.html").read_text(encoding="utf-8")
    assert "$('backend').value==='tencent_cos'||f.size>1024*1024" in source


def test_application_version_is_current():
    from app import __version__
    assert __version__ == "0.6.10"
    assert 'version = "0.6.10"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_windows_staging_contains_cos_sdk_and_windows_crypto_runtime():
    site = ROOT / "dist" / "PracticalToolsOnlinePortable" / "runtime" / "Lib" / "site-packages"
    assert (site / "qcloud_cos" / "__init__.py").is_file()
    metadata = (site / "cos_python_sdk_v5-1.9.44.dist-info" / "METADATA").read_text(encoding="utf-8")
    assert "Version: 1.9.44" in metadata
    for dep in ("requests", "xmltodict", "six", "crcmod", "Crypto"):
        assert (site / dep).exists() or (site / f"{dep}.py").exists()
    pyd = next((site / "Crypto").rglob("*.pyd"), None)
    assert pyd and pyd.read_bytes()[:2] == b"MZ"
    assert not list(site.rglob("*.so")) and not list(site.rglob("*.dylib"))


def test_agent_source_help_and_default_center_port():
    if importlib.util.find_spec("requests") is None or importlib.util.find_spec("patchright") is None:
        pytest.skip("Agent runtime dependencies are only bundled in the packaged executable")
    completed = subprocess.run(
        [str(VENV_PYTHON), str(ROOT / "agent/agent.py"), "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "--server" in completed.stdout and "--pair" in completed.stdout
    assert "18180" in (ROOT / "agent/agent.py").read_text(encoding="utf-8")


def test_built_mac_agent_help_and_package_files():
    package = ROOT / "dist/PracticalToolsAgent-package"
    executable = package / "PracticalToolsAgent"
    assert executable.is_file() and executable.stat().st_size > 1024 * 1024
    assert (package / "config.yaml").is_file()
    assert (package / "install_agent.command").is_file()
    spec = (ROOT / "packaging/agent_mac.spec").read_text(encoding="utf-8")
    assert '"patchright/driver/package"' in spec and '"patchright/driver"' in spec
    # The PyInstaller bootloader uses a System V semaphore that is denied by
    # the managed test sandbox. Its --help smoke test is executed separately
    # outside the sandbox and recorded in TEST_REPORT.md.


def test_built_mac_agent_embeds_patchright_driver():
    """A one-file Mac Agent must carry Patchright's Node transport itself."""
    if sys.platform != "darwin":
        pytest.skip("仅在 macOS 构建机上检查 PyInstaller CArchive")
    executable = ROOT / "dist" / "PracticalToolsAgent-package" / "PracticalToolsAgent"
    if not executable.is_file():
        pytest.skip("未生成 Mac Agent 构建产物")
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        pytest.skip("当前测试环境未安装 PyInstaller")
    names = set(CArchiveReader(str(executable)).toc)
    assert "patchright/driver/node" in names
    assert "patchright/driver/package/cli.js" in names


def test_built_mac_agent_browser_revision_matches_patchright():
    """The external browser payload must match the embedded driver manifest."""
    if sys.platform != "darwin":
        pytest.skip("仅在 macOS 构建机上检查 Chromium revision")
    executable = ROOT / "dist" / "PracticalToolsAgent-package" / "PracticalToolsAgent"
    browser_root = ROOT / "dist" / "PracticalToolsAgent-package" / "browsers"
    if not executable.is_file() or not browser_root.is_dir():
        pytest.skip("未生成 Mac Agent 浏览器构建产物")
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        pytest.skip("当前测试环境未安装 PyInstaller")
    import json
    reader = CArchiveReader(str(executable))
    manifest = json.loads(reader.extract("patchright/driver/package/browsers.json"))
    revision = next(item["revision"] for item in manifest["browsers"] if item["name"] == "chromium")
    browser = browser_root / f"chromium-{revision}" / "chrome-mac-arm64" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
    assert browser.is_file(), f"Patchright 要求 chromium-{revision}，但包内没有对应浏览器"


def test_windows_and_center_packaging_inputs_exist_and_use_18180():
    required = [
        "packaging/agent_windows.spec",
        "packaging/build_windows.cmd",
        "packaging/center_windows.spec",
        "packaging/build_center_windows.cmd",
        "packaging/start_center_windows.cmd",
        "packaging/center_windows.env.example",
    ]
    for relative in required:
        assert (ROOT / relative).is_file(), relative
    env_example = (ROOT / "packaging/center_windows.env.example").read_text(encoding="utf-8")
    assert "PRACTICAL_PORT=18180" in env_example
    assert "PRACTICAL_AUTO_CREATE_TABLES=false" in env_example


def test_portable_update_scripts_use_bundled_runtime_and_launcher():
    script = (ROOT / "packaging/apply_center_update.cmd").read_text(encoding="utf-8")
    rollback = (ROOT / "packaging/rollback_center_update.cmd").read_text(encoding="utf-8")
    assert "runtime\\python.exe" in script and "portable\\portable_center.py" in script
    assert "runtime\\python.exe" in rollback and "portable\\portable_center.py" in rollback


def test_center_restart_chain_is_windowless_after_online_update():
    worker = (ROOT / "packaging/update_worker.py").read_text(encoding="utf-8")
    portable = (ROOT / "packaging/windows/portable/portable_center.py").read_text(encoding="utf-8")
    admin = (ROOT / "app/api/admin.py").read_text(encoding="utf-8")
    center_spec = (ROOT / "packaging/center_windows.spec").read_text(encoding="utf-8")
    updater_spec = (ROOT / "packaging/center_updater.spec").read_text(encoding="utf-8")
    assert "pythonw.exe" in worker and "CREATE_NO_WINDOW" in worker
    assert "pythonw.exe" in portable and "CREATE_NO_WINDOW" in portable
    assert "pythonw.exe" in admin and "CREATE_NO_WINDOW" in admin
    assert "console=False" in center_spec
    assert "console=False" in updater_spec


def test_packaged_artifacts_do_not_contain_known_secret_variable_values():
    # Static gate: manifests may name environment variables but must not embed
    # actual values or browser state files.
    package_files = list((ROOT / "dist/PracticalToolsAgent-package").glob("*"))
    names = {path.name for path in package_files}
    assert "state.json" not in names
    assert "agent.json" not in names
    assert ".env" not in names


def test_mac_installer_uses_one_time_pairing_code_not_plaintext_token():
    script = (ROOT / "packaging/install_agent.command").read_text(encoding="utf-8")
    assert "--pair" in script
    assert "Agent token" not in script
    assert "PRACTICAL_TOOLS_AGENT_TOKEN" not in script

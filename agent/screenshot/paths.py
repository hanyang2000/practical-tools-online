"""截图模块路径：区分只读资源与可写数据，兼容打包（frozen）。

- 资源（只读，打包后随包分发）：capture.py / login.py / ocr_tool
- 数据（可写）：config.yaml / screenshots / browser_state / logs
"""
import os
import sys
from pathlib import Path


def _frozen() -> bool:
    return hasattr(sys, "_MEIPASS")


def resource_dir() -> Path:
    """只读资源目录：开发=本目录，打包=sys._MEIPASS/screenshot。"""
    if _frozen():
        return Path(sys._MEIPASS) / "screenshot"
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """可写数据目录：开发=本目录，打包=~/.practical_tools/screenshot。"""
    env = os.environ.get("PRACTICAL_TOOLS_DATA")
    if env:
        return Path(env).expanduser()
    if _frozen():
        return Path.home() / ".practical_tools" / "screenshot"
    return Path(__file__).resolve().parent


RESOURCE_DIR = resource_dir()
DATA_DIR = data_dir()
CONFIG_FILE = DATA_DIR / "config.yaml"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
BROWSER_STATE_DIR = DATA_DIR / "browser_state"
LOGS_DIR = DATA_DIR / "logs"
PROGRESS_FILE = LOGS_DIR / "progress.json"
CAPTURE_PY = RESOURCE_DIR / "capture.py"
LOGIN_PY = RESOURCE_DIR / "login.py"


def _resolve_ocr_tool() -> Path:
    """定位 OCR 工具，兼容两种打包布局：
    新 spec 落在 screenshot/ocr_tool/ocr；老/误打包时可能被铺平到 screenshot/ocr。
    """
    for p in (RESOURCE_DIR / "ocr_tool" / "ocr", RESOURCE_DIR / "ocr"):
        if p.exists():
            return p
    return RESOURCE_DIR / "ocr_tool" / "ocr"


OCR_TOOL = _resolve_ocr_tool()


def ensure_data():
    """确保数据目录存在；首次运行时把随包 config.yaml 复制到可写数据目录。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    src = RESOURCE_DIR / "config.yaml"
    if not CONFIG_FILE.exists() and src.exists():
        try:
            import shutil
            shutil.copy2(src, CONFIG_FILE)
        except Exception:
            pass

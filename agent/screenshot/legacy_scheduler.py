"""定时截图调度管理（macOS launchd，按「每周几 + 几点」调度）。"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import yaml

from screenshot import paths

CONFIG_FILE = paths.CONFIG_FILE
LOGS_DIR = paths.LOGS_DIR
PLIST_LABEL = "com.practicaltools.screenshot"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _launch_args() -> list:
    """launchd 运行参数：打包后运行本程序 --capture，开发时 python run.py --capture。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--capture"]
    root = Path(__file__).resolve().parent.parent
    return [sys.executable, str(root / "run.py"), "--capture"]

# 1=周一 ... 6=周六, 7=周日（launchd Weekday 约定）
WEEKDAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False)


def get_schedule() -> dict:
    sch = _load_config().get("schedule") or {}
    weekdays = sch.get("weekdays") or [1, 2, 3, 4, 5, 6, 7]
    return {
        "weekdays": sorted(weekdays),
        "hour": int(sch.get("hour", 9)),
        "minute": int(sch.get("minute", 0)),
    }


def set_schedule(weekdays: list, hour: int, minute: int) -> dict:
    if not weekdays or not all(isinstance(d, int) and 1 <= d <= 7 for d in weekdays):
        return {"error": "请至少选择一个星期"}
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return {"error": "时间格式不对"}
    cfg = _load_config()
    cfg["schedule"] = {"weekdays": sorted(set(weekdays)), "hour": hour, "minute": minute}
    _save_config(cfg)
    return {"ok": True, "schedule": get_schedule()}


def _generate_plist(schedule: dict) -> dict:
    intervals = [
        {"Weekday": d, "Hour": schedule["hour"], "Minute": schedule["minute"]}
        for d in schedule["weekdays"]
    ]
    return {
        "Label": PLIST_LABEL,
        "ProgramArguments": _launch_args(),
        "StartCalendarInterval": intervals,
        "StandardOutPath": str(LOGS_DIR / "stdout.log"),
        "StandardErrorPath": str(LOGS_DIR / "stderr.log"),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            # 便携模式下，launchd 跑 --capture 也需走同一数据目录，否则会落到 ~/.practical_tools/screenshot 默认路径
            "PRACTICAL_TOOLS_DATA": str(paths.DATA_DIR),
        },
        "WorkingDirectory": str(paths.DATA_DIR),
    }


def install() -> dict:
    schedule = get_schedule()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(_generate_plist(schedule), f)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{PLIST_LABEL}"], capture_output=True)
    r = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)],
        capture_output=True, text=True)
    return {"ok": r.returncode == 0, "schedule": schedule,
            "error": r.stderr.strip() if r.returncode else ""}


def uninstall() -> dict:
    was = PLIST_PATH.exists()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{PLIST_LABEL}"], capture_output=True)
    PLIST_PATH.unlink(missing_ok=True)
    return {"ok": True, "was_installed": was}


def status() -> dict:
    installed = PLIST_PATH.exists()
    running = False
    last_code = None
    if installed:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{PLIST_LABEL}"],
            capture_output=True, text=True)
        running = r.returncode == 0
        for line in r.stdout.split("\n"):
            if "last exit code" in line:
                try:
                    last_code = int(line.split("=")[-1].strip())
                except Exception:
                    pass
    return {"installed": installed, "running": running,
            "schedule": get_schedule(), "last_code": last_code}

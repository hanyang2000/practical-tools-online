"""本地 Agent 调度安装器：macOS 使用 launchd，Windows 使用 schtasks。

中心服务仅保存时间配置；真实定时任务由每台运行 Agent 的电脑本地安装。
"""
from __future__ import annotations

import json
import os
import plistlib
import platform
import subprocess
import sys
from pathlib import Path

from screenshot import paths

LABEL = "com.practicaltools.capture-agent"
STATE_FILE = paths.DATA_DIR / "scheduler.json"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
TASK_NAME = "PracticalToolsCaptureAgent"
AUTOSTART_TASK_NAME = "PracticalToolsCaptureAgentBackground"
AUTOSTART_LABEL = LABEL + ".background"


def _load() -> dict:
    saved = load_saved_schedule()
    if saved is not None:
        return saved
    return {"weekdays": [1, 2, 3, 4, 5, 6, 7], "hour": 9, "minute": 0}


def _read_state() -> dict | None:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def normalize_schedule(value: object) -> dict:
    """Validate and normalize a schedule received from the center or cache."""
    if not isinstance(value, dict):
        raise ValueError("定时配置必须是对象")
    weekdays = value.get("weekdays", [1, 2, 3, 4, 5, 6, 7])
    if not isinstance(weekdays, (list, tuple)):
        raise ValueError("星期配置格式不对")
    normalized_days = sorted(set(int(day) for day in weekdays))
    if not normalized_days or any(day not in range(1, 8) for day in normalized_days):
        raise ValueError("请至少选择一个星期")
    hour = int(value.get("hour", 9))
    minute = int(value.get("minute", 0))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("时间格式不对")
    return {"weekdays": normalized_days, "hour": hour, "minute": minute}


def load_saved_schedule() -> dict | None:
    """Return the last explicitly saved schedule, or None when disabled/absent.

    Older state files stored the three schedule fields at the top level.  They
    remain readable; new files additionally store ``enabled`` so a remotely
    disabled schedule is not resurrected while the center is offline.
    """
    state = _read_state()
    if not state or state.get("enabled") is False:
        return None
    try:
        return normalize_schedule(state.get("schedule", state))
    except (TypeError, ValueError):
        return None


def set_disabled() -> dict:
    """Persist an explicit disabled marker without deleting the last state."""
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write_state({"enabled": False})
    return {"ok": True, "enabled": False}


def _write_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_name(f".{STATE_FILE.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def set_schedule(weekdays: list[int], hour: int, minute: int) -> dict:
    try:
        data = normalize_schedule({"weekdays": weekdays, "hour": hour, "minute": minute})
    except (TypeError, ValueError) as exc:
        return {"error": str(exc)}
    _write_state({"enabled": True, "schedule": data})
    return {"ok": True, "schedule": data}


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parent / "agent.py")]


def install(server_url: str | None = None, data_dir: Path | None = None) -> dict:
    schedule = _load()
    server_url = server_url or os.environ.get("PRACTICAL_TOOLS_SERVER_URL")
    if not server_url: return {"ok": False, "schedule": schedule, "error": "缺少中心地址，拒绝安装定时任务"}
    data_dir = data_dir or paths.DATA_DIR
    if platform.system() == "Darwin":
        intervals = [{"Weekday": day, "Hour": schedule["hour"], "Minute": schedule["minute"]} for day in schedule["weekdays"]]
        PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"Label": LABEL, "ProgramArguments": _command() + ["--server", server_url, "--data-dir", str(data_dir), "--scheduled-run"], "StartCalendarInterval": intervals,
                   "StandardOutPath": str(data_dir / "logs" / "scheduler.stdout.log"), "StandardErrorPath": str(data_dir / "logs" / "scheduler.stderr.log"),
                   "EnvironmentVariables": {"PRACTICAL_TOOLS_DATA": str(data_dir)}}
        temporary = PLIST_PATH.with_name(f".{PLIST_PATH.name}.tmp-{os.getpid()}")
        temporary.write_bytes(plistlib.dumps(payload))
        temporary.replace(PLIST_PATH)
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)], capture_output=True, text=True)
        error = (result.stderr or result.stdout or "").strip()
        if result.returncode == 0:
            verified = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
            if verified.returncode:
                error = (verified.stderr or verified.stdout or "").strip()
                return {"ok": False, "schedule": schedule, "error": error or "launchctl bootstrap 后未找到定时服务"}
        return {"ok": result.returncode == 0, "schedule": schedule,
                "error": error if result.returncode else ""}
    if platform.system() == "Windows":
        # schtasks 的 /D 参数使用 MON...SUN；Agent 会在指定时刻执行一次 capture_all。
        names = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        days = ",".join(names[int(day) - 1] for day in schedule["weekdays"])
        time_text = f"{int(schedule['hour']):02d}:{int(schedule['minute']):02d}"
        command = _command() + ["--server", server_url, "--data-dir", str(data_dir), "--scheduled-run"]
        result = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "WEEKLY", "/D", days,
                                 "/ST", time_text, "/TR", subprocess.list2cmdline(command), "/F"], capture_output=True, text=True)
        return {"ok": result.returncode == 0, "schedule": schedule,
                "error": result.stderr.strip() or result.stdout.strip() if result.returncode else ""}
    return {"ok": False, "schedule": schedule, "error": "当前系统不支持本地定时任务"}


def uninstall() -> dict:
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        existed = PLIST_PATH.exists()
        PLIST_PATH.unlink(missing_ok=True)
        return {"ok": True, "was_installed": existed}
    if platform.system() == "Windows":
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True)
        return {"ok": True}
    return {"ok": True}

def install_autostart(server_url: str, data_dir: Path) -> dict:
    if not server_url: return {"ok": False, "error": "缺少中心地址"}
    command = _command() + ["--server", server_url, "--data-dir", str(data_dir)]
    if platform.system() == "Darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{AUTOSTART_LABEL}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"Label": AUTOSTART_LABEL, "ProgramArguments": command, "RunAtLoad": True, "KeepAlive": True,
                   "StandardOutPath": str(data_dir / "logs" / "agent.stdout.log"),
                   "StandardErrorPath": str(data_dir / "logs" / "agent.stderr.log")}
        try:
            temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
            temporary.write_bytes(plistlib.dumps(payload))
            temporary.replace(path)
        except OSError as exc:
            return {"ok": False, "error": f"写入 macOS 自启动配置失败：{exc}"}
        label = f"gui/{os.getuid()}/{AUTOSTART_LABEL}"
        subprocess.run(["launchctl", "bootout", label], capture_output=True, text=True)
        result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], capture_output=True, text=True)
        detail = (result.stderr or result.stdout or "").strip()
        if result.returncode:
            return {"ok": False, "error": detail or f"launchctl bootstrap 失败（退出码 {result.returncode}）", "path": str(path)}
        verified = subprocess.run(["launchctl", "print", label], capture_output=True, text=True)
        if verified.returncode:
            detail = (verified.stderr or verified.stdout or "").strip()
            return {"ok": False, "error": detail or "launchctl bootstrap 后未找到服务", "path": str(path)}
        return {"ok": True, "path": str(path), "label": AUTOSTART_LABEL}
    if platform.system() == "Windows":
        result=subprocess.run(["schtasks","/Create","/TN",AUTOSTART_TASK_NAME,"/SC","ONLOGON","/TR",subprocess.list2cmdline(command),"/F"],capture_output=True,text=True); return {"ok": result.returncode==0, "error": result.stderr}
    return {"ok": False, "error": "当前系统不支持"}

def uninstall_autostart() -> dict:
    if platform.system()=="Darwin":
        path=Path.home()/"Library"/"LaunchAgents"/f"{AUTOSTART_LABEL}.plist"; subprocess.run(["launchctl","bootout",f"gui/{os.getuid()}/{AUTOSTART_LABEL}"],capture_output=True); path.unlink(missing_ok=True); return {"ok":True}
    if platform.system()=="Windows": subprocess.run(["schtasks","/Delete","/TN",AUTOSTART_TASK_NAME,"/F"],capture_output=True)
    return {"ok":True}

def autostart_status() -> dict:
    if platform.system()=="Darwin": return {"installed": (Path.home()/"Library"/"LaunchAgents"/f"{AUTOSTART_LABEL}.plist").exists()}
    if platform.system()=="Windows": return {"installed": subprocess.run(["schtasks","/Query","/TN",AUTOSTART_TASK_NAME],capture_output=True).returncode==0}
    return {"installed":False}


def status() -> dict:
    schedule = _load()
    if platform.system() == "Darwin":
        installed = PLIST_PATH.exists()
        running = False
        if installed:
            running = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True).returncode == 0
        return {"installed": installed, "running": running, "configured": STATE_FILE.exists(), "schedule": schedule}
    if platform.system() == "Windows":
        result = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True)
        return {"installed": result.returncode == 0, "running": False, "configured": STATE_FILE.exists(), "schedule": schedule}
    return {"installed": False, "running": False, "configured": STATE_FILE.exists(), "schedule": schedule}

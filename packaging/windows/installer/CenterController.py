"""Headless Windows controller used by the local HTA center console.

This module deliberately uses only the bundled Python standard library and
Win32 APIs through ctypes. It is launched with pythonw.exe, so it never owns a
console window and does not depend on WMIC, WMI, PowerShell, Tk, or Tcl.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("memory_load", wintypes.DWORD),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("page_fault_count", wintypes.DWORD),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


def filetime_value(value: FILETIME) -> int:
    return (int(value.high) << 32) | int(value.low)


def atomic_write(path: Path, values: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="ascii")
    for _ in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            time.sleep(0.02)
        except OSError:
            break
    temporary.unlink(missing_ok=True)


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="ascii")


def read_pid(path: Path) -> int:
    try:
        value = int(path.read_text(encoding="ascii").strip())
        return value if value > 0 else 0
    except (OSError, ValueError):
        return 0


def open_process(pid: int, access: int):
    if pid <= 0:
        return None
    handle = ctypes.windll.kernel32.OpenProcess(access, False, pid)
    return handle or None


def process_is_running(pid: int) -> bool:
    handle = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def terminate_process(pid: int) -> None:
    if pid <= 0:
        return
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        handle = open_process(pid, PROCESS_TERMINATE)
        if handle:
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)


def process_times(pid: int) -> int | None:
    handle = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return None
    try:
        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if not ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        return filetime_value(kernel) + filetime_value(user)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def process_memory(pid: int) -> int | None:
    handle = open_process(pid, PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)
    if not handle:
        return None
    try:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.working_set_size)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def host_cpu_times() -> tuple[int, int] | None:
    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    total = filetime_value(kernel) + filetime_value(user)
    return filetime_value(idle), total


def host_memory() -> tuple[float, int] | None:
    status = MEMORYSTATUSEX()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return float(status.memory_load), int(status.total_physical)


def launch_center(root: Path, pid_path: Path) -> int:
    existing_pid = read_pid(pid_path)
    if process_is_running(existing_pid):
        return existing_pid
    native = root / "PracticalToolsOnline.exe"
    python = root / "runtime" / "python.exe"
    script = root / "portable" / "portable_center.py"
    if native.is_file():
        command = [str(native)]
    elif python.is_file() and script.is_file():
        command = [str(python), str(script)]
    else:
        raise FileNotFoundError("Center runtime was not found")
    log_root = root / "data" / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    with (log_root / "portable-center.log").open("ab", buffering=0) as stdout_file, (
        log_root / "portable-center-error.log"
    ).open("ab", buffering=0) as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    write_pid(pid_path, process.pid)
    return process.pid


def launch_tunnel(root: Path, pid_path: Path, executable: str, mode: str, name: str) -> int:
    command = [executable, "tunnel", "--no-autoupdate"]
    if mode == "named":
        command.extend(["run", name])
    elif mode == "token":
        command.append("run")
    else:
        command.extend(["--url", "http://127.0.0.1:18180"])
    log_root = root / "data" / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    with (log_root / "cloudflare-tunnel.log").open("ab", buffering=0) as stdout_file, (
        log_root / "cloudflare-tunnel-error.log"
    ).open("ab", buffering=0) as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    write_pid(pid_path, process.pid)
    return process.pid


def monitor(
    root: Path,
    status_path: Path,
    command_path: Path,
    center_pid_path: Path,
    tunnel_pid_path: Path,
    heartbeat_path: Path,
) -> None:
    previous_host: tuple[int, int] | None = None
    previous_process: tuple[float, int] | None = None
    processor_count = max(1, os.cpu_count() or 1)
    missing_heartbeat_since = time.monotonic()
    should_exit = False

    while not should_exit:
        try:
            command = command_path.read_text(encoding="ascii").strip().lower()
        except OSError:
            command = ""
        if command:
            command_path.unlink(missing_ok=True)
            if command in {"stop-center", "stop-all-exit"}:
                terminate_process(read_pid(center_pid_path))
                center_pid_path.unlink(missing_ok=True)
            if command in {"stop-tunnel", "stop-all-exit"}:
                terminate_process(read_pid(tunnel_pid_path))
                tunnel_pid_path.unlink(missing_ok=True)
            should_exit = command == "stop-all-exit"

        try:
            heartbeat_age = time.time() - heartbeat_path.stat().st_mtime
            missing_heartbeat_since = time.monotonic()
        except OSError:
            heartbeat_age = time.monotonic() - missing_heartbeat_since
        if heartbeat_age > 15:
            terminate_process(read_pid(tunnel_pid_path))
            terminate_process(read_pid(center_pid_path))
            tunnel_pid_path.unlink(missing_ok=True)
            center_pid_path.unlink(missing_ok=True)
            should_exit = True

        center_pid = read_pid(center_pid_path)
        tunnel_pid = read_pid(tunnel_pid_path)
        center_running = process_is_running(center_pid)
        tunnel_running = process_is_running(tunnel_pid)
        if not center_running:
            center_pid_path.unlink(missing_ok=True)
            center_pid = 0
            previous_process = None
        if not tunnel_running:
            tunnel_pid_path.unlink(missing_ok=True)
            tunnel_pid = 0

        current_time = time.monotonic()
        current_process_time = process_times(center_pid) if center_running else None
        center_cpu: float | str = ""
        if current_process_time is not None and previous_process is not None:
            elapsed = current_time - previous_process[0]
            if elapsed > 0:
                center_cpu = max(0.0, min(100.0, (current_process_time - previous_process[1]) / 10_000_000 / elapsed / processor_count * 100))
        if current_process_time is not None:
            previous_process = (current_time, current_process_time)

        current_host = host_cpu_times()
        host_cpu: float | str = ""
        if current_host is not None and previous_host is not None:
            idle_delta = current_host[0] - previous_host[0]
            total_delta = current_host[1] - previous_host[1]
            if total_delta > 0:
                host_cpu = max(0.0, min(100.0, (total_delta - idle_delta) / total_delta * 100))
        if current_host is not None:
            previous_host = current_host

        memory = host_memory()
        memory_percent: float | str = memory[0] if memory else ""
        memory_total: int | str = memory[1] if memory else ""
        try:
            disk = shutil.disk_usage(root)
            disk_free: float | str = disk.free / disk.total * 100 if disk.total else ""
        except OSError:
            disk_free = ""

        atomic_write(
            status_path,
            {
                "collector": "ok",
                "centerRunning": int(center_running),
                "tunnelRunning": int(tunnel_running),
                "centerPid": center_pid,
                "centerCpu": center_cpu,
                "centerMemoryBytes": process_memory(center_pid) if center_running else "",
                "hostCpu": host_cpu,
                "hostMemory": memory_percent,
                "memoryTotal": memory_total,
                "diskFree": disk_free,
            },
        )
        if not should_exit:
            time.sleep(1)

    for path in (status_path, command_path, heartbeat_path):
        path.unlink(missing_ok=True)


def record_failure(root: Path, message: str) -> None:
    log_root = root / "data" / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    with (log_root / "center-controller-error.log").open("a", encoding="utf-8") as stream:
        stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def main(argv: list[str]) -> int:
    if os.name != "nt" or len(argv) < 2:
        return 2
    mode = argv[1].lower()
    try:
        if mode == "monitor" and len(argv) == 8:
            monitor(Path(argv[2]), *(Path(value) for value in argv[3:8]))
        elif mode == "start-center" and len(argv) == 4:
            launch_center(Path(argv[2]), Path(argv[3]))
        elif mode == "start-tunnel" and len(argv) == 7:
            launch_tunnel(Path(argv[2]), Path(argv[3]), argv[4], argv[5].lower(), argv[6])
        else:
            return 2
    except Exception as exc:
        root = Path(argv[2]) if len(argv) > 2 else Path.cwd()
        record_failure(root, f"{mode}: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

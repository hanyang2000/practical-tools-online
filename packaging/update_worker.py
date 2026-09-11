#!/usr/bin/env python3
"""Apply a previously verified center update archive.

This worker is intentionally a separate process: the center can stage and
verify an update online, then a green-package launcher starts this worker
after the center exits. It only replaces allowlisted payload files and never
touches .env, data, or registry/services.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.services.update_bundle import inspect_update_bundle

RETRY_COUNT = 30
RETRY_DELAY = 0.5
OVERLAY_ROOTS = {"installer", "portable"}


def _windowless_runtime(runtime: Path) -> Path:
    """Prefer the bundled windowless Python executable on Windows.

    The green updater is normally started without a console, but its child
    center process used to be launched with ``python.exe`` and inherited the
    console startup mode on some Windows hosts.  A sibling ``pythonw.exe`` is
    the safest executable for the long-running center process.  Keep the
    supplied runtime as a fallback because source-package and test installs
    may not ship the windowless interpreter.
    """
    if os.name == "nt" and runtime.name.lower() == "python.exe":
        windowless = runtime.with_name("pythonw.exe")
        if windowless.is_file():
            return windowless
    return runtime


def _hidden_process_kwargs() -> dict[str, object]:
    """Return subprocess options that prevent a Windows console window.

    stdout/stderr are deliberately detached from the updater.  The portable
    launcher writes its own service logs, so inheriting the updater's handle
    can only make the console visible again or keep the updater log open.
    """
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name != "nt":
        return kwargs
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def _launch_center(app_root: Path, launcher: Path, runtime: Path):
    """Start the updated center without exposing a terminal to the operator."""
    command = [str(_windowless_runtime(runtime)), str(launcher)]
    return subprocess.Popen(command, cwd=str(app_root), **_hidden_process_kwargs())


def _data_root() -> Path:
    value = os.environ.get("PRACTICAL_DATA_ROOT")
    if value:
        return Path(value).expanduser()
    env_file = ROOT / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("PRACTICAL_DATA_ROOT="):
                return Path(line.split("=", 1)[1].strip().strip('"')).expanduser()
    except OSError:
        pass
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PracticalToolsOnline" / "data"


def _health_ok(url: str, target_version: str | None = None, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
            return 200 <= response.status < 300 and payload.get("service") == "practical-tools-online" and (not target_version or payload.get("version") == target_version)
    except Exception:
        return False


def _acquire_lock(lock_root: Path) -> Path:
    lock = lock_root / ".update-lock"
    try:
        lock.mkdir(parents=True, exist_ok=False)
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "started_at": time.time()}), encoding="utf-8")
        return lock
    except FileExistsError:
        try:
            owner = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
            if time.time() - float(owner.get("started_at", 0)) > 1800:
                shutil.rmtree(lock, ignore_errors=True)
                lock.mkdir(parents=True, exist_ok=False)
                return lock
        except (OSError, ValueError, TypeError):
            pass
        raise RuntimeError("已有更新任务正在执行")


def _pid_probe(pid: int) -> str:
    """Probe a process without treating Windows' invalid-handle error as dead."""
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, check=False,
            )
            output = (result.stdout or "").strip()
            if not output or "No tasks are running" in output or "没有运行的任务" in output:
                return "dead"
            # tasklist is localized, but a matching CSV row always contains
            # the exact PID. Any non-matching filtered response means dead;
            # returning unknown here used to make an already exited process
            # impossible to clear after the retry window.
            return "alive" if f'"{pid}"' in output else "dead"
        except OSError:
            return "unknown"
    try:
        os.kill(pid, 0)
        return "alive"
    except ProcessLookupError:
        return "dead"
    except (OSError, SystemError):
        return "unknown"


def _read_app_version(app_root: Path) -> str | None:
    version_file = app_root / "app" / "__init__.py"
    try:
        text = version_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"__version__\s*=\s*[\"']([^\"']+)", text)
    return match.group(1) if match else None


def _force_stop(pid: int) -> None:
    """Stop only a PID supplied by the trusted launcher, never an arbitrary process."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, 15)
        except (OSError, SystemError):
            pass


def _stop_process_tree(pid: int | None) -> None:
    if not pid or pid <= 0:
        return
    if os.name == "nt":
        _force_stop(pid)
        return
    try:
        os.kill(pid, 15)
    except (OSError, SystemError):
        pass


def _retry(label: str, operation):
    """Retry transient Windows sharing violations before declaring failure."""
    last_error = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            return operation()
        except (OSError, SystemError) as exc:
            last_error = exc
            if attempt == RETRY_COUNT:
                break
            print(f"更新阶段：{label}失败，第 {attempt}/{RETRY_COUNT} 次重试：{exc}", flush=True)
            time.sleep(RETRY_DELAY)
    raise last_error  # type: ignore[misc]


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _sync_tree(source: Path, target: Path) -> int:
    """Sync one allowlisted top-level directory file-by-file.

    Replacing the whole directory is fragile on Windows: antivirus/indexers
    can briefly hold a directory handle even after the center process exits.
    File-level replacement keeps the locked directory in place and retries
    only the exact file that is temporarily unavailable.
    """
    source_files = sorted(path for path in source.rglob("*") if path.is_file())
    source_rel = {path.relative_to(source).as_posix() for path in source_files}
    if target.exists() and not target.is_dir():
        _retry(f"移除类型冲突 {target}", lambda: _remove_path(target))
    target.mkdir(parents=True, exist_ok=True)

    stale_files = [path for path in target.rglob("*") if path.is_file() and path.relative_to(target).as_posix() not in source_rel]
    for stale in sorted(stale_files, key=lambda path: len(path.parts), reverse=True):
        _retry(f"清理旧文件 {stale}", lambda stale=stale: _remove_path(stale))

    for source_file in source_files:
        relative = source_file.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.is_dir():
            _retry(f"移除类型冲突 {destination}", lambda destination=destination: _remove_path(destination))
        _retry(f"替换文件 {destination}", lambda source_file=source_file, destination=destination: os.replace(source_file, destination))
    return len(source_files)


def _overlay_tree(source: Path, target: Path) -> int:
    """Replace selected files without deleting unrelated installed helpers."""
    source_files = sorted(path for path in source.rglob("*") if path.is_file())
    if target.exists() and not target.is_dir():
        _retry(f"移除类型冲突 {target}", lambda: _remove_path(target))
    target.mkdir(parents=True, exist_ok=True)
    for source_file in source_files:
        destination = target / source_file.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.is_dir():
            _retry(f"移除类型冲突 {destination}", lambda destination=destination: _remove_path(destination))
        _retry(f"替换文件 {destination}", lambda source_file=source_file, destination=destination: os.replace(source_file, destination))
    return len(source_files)


def _wait_for_program_exit(pids: tuple[int | None, ...]) -> None:
    targets = [pid for pid in pids if pid]
    if not targets:
        return
    uncertain_or_alive = set()
    for _ in range(40):
        uncertain_or_alive = {pid for pid in targets if _pid_probe(pid) != "dead"}
        if not uncertain_or_alive:
            return
        time.sleep(0.25)
    if os.name != "nt":
        # macOS/Linux launchers are expected to honor their graceful signal;
        # do not send a second signal from a test/mock or unrelated host.
        return
    # The green launcher passes only its own uvicorn and portable-center PIDs.
    # If Windows still reports an invalid handle or the process ignores the
    # graceful signal, terminate that process tree so files are not left locked.
    for pid in sorted(uncertain_or_alive):
        _force_stop(pid)
    for _ in range(80):
        remaining = {pid for pid in targets if _pid_probe(pid) != "dead"}
        if not remaining:
            return
        time.sleep(0.25)
    raise RuntimeError(f"中心进程未退出，无法安全替换程序文件：{sorted(remaining)}")


def _restore_entries(entries: list[tuple[Path, Path, bool]]) -> None:
    for target, saved, existed in reversed(entries):
        remove_error = None
        try:
            _retry(f"清理回滚目标 {target}", lambda target=target: _remove_path(target))
        except (OSError, SystemError) as exc:
            # A directory may still be held by a security scanner. Overlaying
            # the backup is still useful and avoids the old FileExistsError.
            remove_error = exc
        if existed:
            if saved.is_dir():
                try: shutil.copytree(saved, target, dirs_exist_ok=True)
                except (OSError, SystemError):
                    if remove_error: raise
                    raise
            else:
                target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(saved, target)
        elif remove_error:
            raise remove_error


def _archive_pending_file(pending_file: Path) -> Path | None:
    """Archive a consumed pending marker without making update success fragile.

    ``Path.rename`` maps to ``MoveFile`` on Windows and fails with WinError 183
    when the fixed destination from a previous run still exists.  ``os.replace``
    atomically overwrites an existing regular file; if a security tool or an
    unusual directory collision still prevents that, keep the update result
    successful and fall back to a unique archive name.
    """
    if not pending_file.is_file():
        return None
    canonical = pending_file.with_name("pending.completed.json")
    try:
        os.replace(pending_file, canonical)
        return canonical
    except OSError as first_error:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        fallback = pending_file.with_name(
            f"pending.completed.{stamp}-{os.getpid()}-{time.time_ns() % 1000000:06d}.json"
        )
        try:
            os.replace(pending_file, fallback)
            print(f"更新结果归档：固定文件不可替换，已使用 {fallback.name}：{first_error}", flush=True)
            return fallback
        except OSError as second_error:
            # The result.json is the authoritative status. A failed archive
            # must not turn a completed update into a rollback.
            print(f"更新结果归档失败，保留待处理标记：{second_error}", flush=True)
            return None


def _backup_entries(app_root: Path, backup: Path, staging: Path) -> list[tuple[Path, Path, bool]]:
    entries = []
    backup.mkdir(parents=True, exist_ok=True)
    roots = sorted(staging.iterdir(), key=lambda path: path.name)
    for source in roots:
        relative = source.relative_to(staging)
        target = app_root / relative
        saved = backup / relative
        existed = target.exists()
        if existed:
            saved.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir(): shutil.copytree(target, saved, dirs_exist_ok=True)
            else: shutil.copy2(target, saved)
        entries.append((target, saved, existed))
    (backup / "backup-manifest.json").write_text(json.dumps({"roots": [str(x[0].relative_to(app_root)).replace("\\", "/") for x in entries]}), encoding="utf-8")
    return entries


def apply_update(package: Path, app_root: Path, wait_pid: int | None = None, health_url: str | None = None, wait_parent_pid: int | None = None, launcher: Path | None = None, runtime: Path | None = None, result_file: Path | None = None) -> dict:
    pending_file = package if package.suffix.lower() == ".json" else None
    if package.suffix.lower() == ".json":
        pending = json.loads(package.read_text(encoding="utf-8"))
        package = Path(str(pending.get("package", "")))
        if not package.is_file():
            raise ValueError("待更新包不存在")
    inspect_update_bundle(package)
    print(f"更新开始：包={package}，等待进程={[pid for pid in (wait_pid, wait_parent_pid) if pid]}", flush=True)
    _wait_for_program_exit((wait_pid, wait_parent_pid))
    result_file = result_file or (_data_root() / "update-staging" / "result.json")
    result_file.parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire_lock(result_file.parent)
    staging = Path(tempfile.mkdtemp(prefix="practical-update-", dir=app_root.parent))
    backup = app_root / "update-backups" / (time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
    entries: list[tuple[Path, Path, bool]] = []
    old_process = None
    previous_version = _read_app_version(app_root)
    try:
        with zipfile.ZipFile(package) as archive:
            for info in archive.infolist():
                if info.filename == "manifest.json" or info.is_dir():
                    continue
                relative = Path(*Path(info.filename).parts[1:])
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info.filename))
        print("更新阶段：包已解压到临时目录", flush=True)
        entries = _backup_entries(app_root, backup, staging)
        replaced = 0
        for source, target, _existed in [(staging / Path(x[0].relative_to(app_root)), x[0], x[2]) for x in entries]:
            if source.is_dir():
                sync = _overlay_tree if source.name in OVERLAY_ROOTS else _sync_tree
                replaced += sync(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _retry(f"替换文件 {target}", lambda source=source, target=target: os.replace(source, target)); replaced += 1
        print(f"更新阶段：逐文件替换完成，共 {replaced} 个文件", flush=True)
        if launcher and runtime:
            old_process = _launch_center(app_root, launcher, runtime)
            poll = getattr(old_process, "poll", None)
            if callable(poll) and poll() is not None:
                raise RuntimeError(f"新版中心启动后立即退出，退出码：{poll()}")
        if health_url:
            deadline = time.time() + 45
            target_version = str(inspect_update_bundle(package)["version"])
            healthy = False
            while time.time() < deadline:
                if _health_ok(health_url, target_version): healthy = True; break
                time.sleep(1)
            if not healthy: raise RuntimeError("更新后健康检查失败")
        result = {"status": "success", "ok": True, "replaced": replaced, "backup": str(backup), "pid": getattr(old_process, "pid", None)}
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if pending_file:
            _archive_pending_file(pending_file)
        return result
    except Exception as exc:
        if old_process:
            _stop_process_tree(getattr(old_process, "pid", None))
        _restore_entries(entries)
        rollback_process = None
        rollback_failed = False
        if launcher and runtime:
            try: rollback_process = _launch_center(app_root, launcher, runtime)
            except OSError: rollback_failed = True
            if health_url:
                healthy = False
                for _ in range(45):
                    if _health_ok(health_url, previous_version): healthy = True; break
                    time.sleep(1)
                if not healthy: rollback_failed = True
        result = {"status": "rollback_failed" if rollback_failed else "rolled_back", "ok": False, "error": str(exc), "restored": len(entries), "rollback_failed": rollback_failed, "rollback_pid": getattr(locals().get("rollback_process"), "pid", None)}
        print(f"更新失败：{result}", flush=True)
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if pending_file and pending_file.is_file():
            try:
                pending = json.loads(pending_file.read_text(encoding="utf-8"))
                pending["install_requested"] = False
                pending["last_result"] = result
                pending_file.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError):
                pass
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(lock, ignore_errors=True)


def rollback(backup: Path, app_root: Path, launcher: Path | None = None, runtime: Path | None = None, health_url: str | None = None) -> dict:
    if not backup.is_dir():
        raise ValueError("回滚备份目录不存在")
    manifest = backup / "backup-manifest.json"
    if manifest.is_file():
        roots = json.loads(manifest.read_text(encoding="utf-8")).get("roots", [])
        for relative in roots:
            target = app_root / relative; saved = backup / relative
            if target.is_dir(): shutil.rmtree(target, ignore_errors=True)
            else: target.unlink(missing_ok=True)
            if saved.is_dir(): shutil.copytree(saved, target, dirs_exist_ok=True)
            elif saved.is_file(): target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(saved, target)
        restored = len(roots)
    else:
        restored = 0
    for source in sorted(backup.rglob("*")):
        if source.is_file() and source.name != "backup-manifest.json" and not manifest.is_file():
            target = app_root / source.relative_to(backup)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target); restored += 1
    process = None
    if launcher and runtime:
        process = _launch_center(app_root, launcher, runtime)
        if health_url:
            target_version = _read_app_version(app_root)
            deadline = time.time() + 45
            while time.time() < deadline and not _health_ok(health_url, target_version): time.sleep(1)
            if time.time() >= deadline: raise RuntimeError("回滚后健康检查失败")
    return {"ok": True, "restored": restored, "pid": getattr(process, "pid", None)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("package", type=Path, nargs="?"); parser.add_argument("--app-root", type=Path, default=ROOT); parser.add_argument("--wait-pid", type=int); parser.add_argument("--wait-parent-pid", type=int); parser.add_argument("--health-url"); parser.add_argument("--launcher", type=Path); parser.add_argument("--runtime", type=Path); parser.add_argument("--result-file", type=Path); parser.add_argument("--rollback", type=Path); args = parser.parse_args()
    root = args.app_root.resolve()
    package = args.package
    if not args.rollback and package is None:
        package = _data_root() / "update-staging" / "pending.json"
    print(rollback(args.rollback.resolve(), root, args.launcher, args.runtime, args.health_url) if args.rollback else apply_update(package.resolve(), root, args.wait_pid, args.health_url, args.wait_parent_pid, args.launcher, args.runtime, args.result_file))

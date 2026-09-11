#!/usr/bin/env python3
"""策划实用小工具本地采集 Agent。

Agent 只负责用户电脑上的淘宝浏览器、登录态与截图；账号会话和截图元数据仍由中心服务负责。
通信全部为 Agent -> 中心服务出站请求，避免网页访问 localhost 带来的混合内容/PNA 风险。

环境变量：
  PRACTICAL_TOOLS_SERVER_URL  中心地址，默认 http://127.0.0.1:18180
  PRACTICAL_TOOLS_AGENT_TOKEN  Agent token（也可由 --pair 写入本地配置）
  PRACTICAL_TOOLS_DATA         本地可写数据目录
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml
try:
    import keyring
except ImportError:  # dependency is required for packaged use; keep importable for source tests
    keyring = None

# 让复制进 Agent 包的 screenshot 模块可按原程序的导入方式运行。
AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(AGENT_DIR))
_BROWSER_ROOT = (Path(sys.executable).resolve().parent / "browsers") if getattr(sys, "frozen", False) else (AGENT_DIR.parent / "browsers")
if _BROWSER_ROOT.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_BROWSER_ROOT))
from screenshot import capture, login, paths  # noqa: E402
import scheduler  # noqa: E402

DEFAULT_SERVER = "http://127.0.0.1:18180"
POLL_SECONDS = 3
MAX_IDLE_JOB_POLL_SECONDS = 15
HEARTBEAT_SECONDS = 15
CONFIG_SYNC_SECONDS = 30
MAX_UPLOAD_ATTEMPTS = 8
AGENT_VERSION = "0.5.7"
AGENT_PROTOCOL_VERSION = 1
AGENT_CAPABILITIES = (
    "capture",
    "login",
    "open_folder",
    "scheduler",
    "outbox_upload",
    "agent_self_update",
)
AGENT_LOCK_NAME = ".agent-process.lock"
AGENT_UPDATE_MAX_BYTES = 1024 * 1024 * 1024
AGENT_UPDATE_RETRY_COUNT = 30
AGENT_UPDATE_RETRY_DELAY = 0.5
KEYCHAIN_RETRY_SECONDS = 60
KEYCHAIN_COMMAND_TIMEOUT = 8
JOB_REPORT_RETRY_COUNT = 3
JOB_REPORT_RETRY_DELAY = 1.5

# These values describe how the local browser captures pages.  They are not
# center-owned settings: /api/agent/config intentionally only returns shops
# and the online schedule.  Keep them when the center response is persisted,
# otherwise a successful config sync silently falls back to capture.py's
# desktop defaults (1920x14500) and can truncate long mobile pages.
LOCAL_CAPTURE_CONFIG_KEYS = frozenset({
    "viewport", "full_page", "extra_wait_min", "extra_wait_max",
    "max_screenshot_height", "page_timeout", "retry_interval_seconds",
    "max_retry_rounds", "crop_below_text", "ocr_start_fraction", "crop_margin_top",
})

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the directory fallback below
    fcntl = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_path() -> Path:
    """Return the actual Agent entrypoint, not the Python interpreter path."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def _runtime_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _version_key(value: str) -> tuple[int, ...]:
    """Parse a simple numeric release version without adding a dependency."""
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))(?:\.(\d+))(?:[-+].*)?", str(value or "").strip(), re.I)
    if not match:
        raise ValueError(f"版本号格式不受支持：{value}")
    return tuple(int(part) for part in match.groups())


def _platform_name(value: str | None = None) -> str:
    value = str(value or platform.system()).strip().casefold()
    return {"darwin": "Darwin", "macos": "Darwin", "mac": "Darwin",
            "windows": "Windows", "win32": "Windows"}.get(value, str(value or "unknown"))


def _is_https_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and bool(parsed.netloc)


def _safe_zip_extract(archive_path: Path, destination: Path) -> None:
    """Extract a package only after rejecting traversal and symlink entries."""
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            parts = Path(name).parts
            if (not name or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name)
                    or ".." in parts):
                raise RuntimeError(f"Agent 更新包包含不安全路径：{member.filename}")
            # ZIP symlinks can escape the extraction directory after install.
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise RuntimeError(f"Agent 更新包不允许符号链接：{member.filename}")
        archive.extractall(destination)


def _retry_replace(source: Path, target: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(AGENT_UPDATE_RETRY_COUNT):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < AGENT_UPDATE_RETRY_COUNT:
                time.sleep(AGENT_UPDATE_RETRY_DELAY)
    raise RuntimeError(f"无法替换 Agent 文件：{target}；最后错误：{last_error}") from last_error


def _write_update_log(data_dir: Path, message: str, exc: BaseException | None = None) -> None:
    line = f"{utc_now()} [pid={os.getpid()}] {message}"
    try:
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "agent-runtime.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            if exc is not None:
                stream.write(f"{type(exc).__name__}: {exc}\n")
    except OSError:
        pass


def _launch_restart(command: list[str], data_dir: Path) -> None:
    if not command:
        raise RuntimeError("Agent 自更新缺少重启命令")
    entrypoint = next((Path(item) for item in command[1:] if item.endswith(".py")), Path(command[0]))
    subprocess.Popen(command, cwd=str(entrypoint.resolve().parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _run_self_update_worker(package: Path, target: Path, mode: str, wait_pid: int,
                            restart_file: Path, data_dir: Path) -> int:
    """Install a verified package after the parent Agent releases its lock."""
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = _process_state(wait_pid)
            if state is False:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"旧 Agent 进程未退出：PID {wait_pid}")
        command_payload = json.loads(restart_file.read_text(encoding="utf-8"))
        command = command_payload.get("command") if isinstance(command_payload, dict) else None
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise RuntimeError("Agent 自更新重启命令无效")
        with tempfile.TemporaryDirectory(prefix="practical-agent-update-", dir=str(package.parent)) as temp_name:
            extracted = Path(temp_name) / "package"
            extracted.mkdir()
            _safe_zip_extract(package, extracted)
            if mode == "binary":
                candidates = [path for path in extracted.rglob(target.name) if path.is_file()]
                if len(candidates) != 1:
                    raise RuntimeError(f"更新包中未找到唯一的 Agent 可执行文件（找到 {len(candidates)} 个）")
                staged = target.with_name(f".{target.name}.update-{os.getpid()}")
                shutil.copy2(candidates[0], staged)
                try:
                    staged.chmod(staged.stat().st_mode | 0o111)
                    _retry_replace(staged, target)
                finally:
                    staged.unlink(missing_ok=True)
            elif mode == "source":
                candidates = [path.parent for path in extracted.rglob("agent.py") if path.name == "agent.py"]
                candidates = [path for path in candidates if (path / "screenshot").is_dir()]
                if len(candidates) != 1:
                    raise RuntimeError(f"更新包中未找到唯一的 Agent 源码目录（找到 {len(candidates)} 个）")
                for source in candidates[0].rglob("*"):
                    relative = source.relative_to(candidates[0])
                    destination = target / relative
                    if source.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    elif source.is_file():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        staged = destination.with_name(f".{destination.name}.update-{os.getpid()}")
                        shutil.copy2(source, staged)
                        try:
                            _retry_replace(staged, destination)
                        finally:
                            staged.unlink(missing_ok=True)
            else:
                raise RuntimeError(f"不支持的 Agent 更新模式：{mode}")
        _write_update_log(data_dir, f"Agent 自更新完成，重启：{target}")
        _launch_restart(command, data_dir)
        package.unlink(missing_ok=True)
        restart_file.unlink(missing_ok=True)
        return 0
    except Exception as exc:
        _write_update_log(data_dir, "Agent 自更新失败，保留旧版本并尝试重启", exc)
        try:
            command_payload = json.loads(restart_file.read_text(encoding="utf-8"))
            command = command_payload.get("command") if isinstance(command_payload, dict) else None
            if isinstance(command, list) and all(isinstance(item, str) for item in command):
                _launch_restart(command, data_dir)
        except Exception as restart_exc:
            _write_update_log(data_dir, "Agent 自更新失败后重启旧版本也失败", restart_exc)
        return 1


def _duplicate_runtime_name(name: str) -> bool:
    lowered = name.casefold()
    stem = Path(name).stem.casefold()
    return bool(lowered.endswith(".tmp") or re.search(r"\s*\(\d+\)$", stem) or stem.endswith("-copy"))


def _validate_runtime_path(path: Path) -> None:
    """Reject renamed/copy Agent binaries before they can register or poll."""
    expected = {"practicaltoolsagent", "practicaltoolsagent.exe"} if getattr(sys, "frozen", False) else {"agent.py"}
    if path.name.casefold() not in expected or _duplicate_runtime_name(path.name):
        raise RuntimeError(f"检测到非标准 Agent 启动文件：{path}，请从正式安装目录启动 PracticalToolsAgent")
    if not path.is_file():
        raise RuntimeError(f"Agent 启动文件不存在：{path}")
    if os.name != "nt" and not os.access(path, os.X_OK) and getattr(sys, "frozen", False):
        raise RuntimeError(f"Agent 启动文件不可执行：{path}")
    if getattr(sys, "frozen", False):
        for sibling in path.parent.iterdir():
            if sibling == path or not sibling.is_file():
                continue
            sibling_stem = sibling.stem.casefold()
            if sibling_stem.startswith("practicaltoolsagent") and _duplicate_runtime_name(sibling.name):
                raise RuntimeError(f"正式 Agent 目录存在重复副本：{sibling}，请清理后再启动")


def _process_state(pid: int) -> bool | None:
    """Return alive/dead/unknown without treating Windows handle errors as dead."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, check=False,
            )
            output = (result.stdout or "").strip()
            if not output or "No tasks are running" in output or "没有运行的任务" in output:
                return False
            return str(pid) in output
        except OSError:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return None


class _AgentProcessLock:
    """A crash-safe process lock shared by all copies using the same Agent data."""

    def __init__(self, data_dir: Path, metadata: dict[str, Any]) -> None:
        self.data_dir = data_dir
        self.path = data_dir / AGENT_LOCK_NAME
        self.metadata = metadata
        self._fd: int | None = None
        self._directory_lock = False

    def _write_metadata(self) -> None:
        if self._fd is not None:
            os.ftruncate(self._fd, 0)
            os.write(self._fd, json.dumps(self.metadata, ensure_ascii=False).encode())
            os.fsync(self._fd)
        else:
            (self.path / "owner.json").write_text(json.dumps(self.metadata, ensure_ascii=False), encoding="utf-8")

    def acquire(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if fcntl is not None:
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                owner: dict[str, Any] = {}
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    owner = json.loads(os.read(self._fd, 8192).decode() or "{}")
                except (OSError, ValueError, TypeError):
                    pass
                os.close(self._fd)
                self._fd = None
                detail = f"PID {owner.get('pid')}，路径 {owner.get('executable_path')}" if owner else "已有进程持有锁"
                raise RuntimeError(f"Agent 已在运行（{detail}），拒绝重复启动") from exc
            self._write_metadata()
            return

        # Windows fallback: an exclusive directory survives normal launch races;
        # stale directories are removed only after confirming their owner is dead.
        try:
            self.path.mkdir()
            self._directory_lock = True
            self._write_metadata()
            return
        except FileExistsError as exc:
            owner: dict[str, Any] = {}
            try:
                owner = json.loads((self.path / "owner.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                raise RuntimeError("Agent 锁文件存在但无法确认所有者，拒绝启动以避免重复运行") from exc
            state = _process_state(int(owner.get("pid", 0)))
            if state is not False:
                raise RuntimeError(f"Agent 已在运行（PID {owner.get('pid')}，路径 {owner.get('executable_path')}），拒绝重复启动") from exc
            shutil.rmtree(self.path, ignore_errors=True)
            self.path.mkdir()
            self._directory_lock = True
            self._write_metadata()

    def release(self) -> None:
        if self._fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
            return
        if self._directory_lock:
            shutil.rmtree(self.path, ignore_errors=True)
            self._directory_lock = False


class Agent:
    def __init__(self, server_url: str, token: str = "", data_dir: Path | None = None, *, acquire_lock: bool = True,
                 load_token: bool = True, allow_path_change: bool = False) -> None:
        self.server_url = server_url.rstrip("/")
        self.data_dir = data_dir or paths.DATA_DIR
        if data_dir is not None:
            # paths.py 以模块常量兼容旧 capture.py；--data-dir 时同步覆盖这些常量，
            # 确保截图、登录态、进度和 outbox 不会落到另一台机器的默认目录。
            paths.DATA_DIR = self.data_dir
            paths.CONFIG_FILE = self.data_dir / "config.yaml"
            paths.SCREENSHOTS_DIR = self.data_dir / "screenshots"
            paths.BROWSER_STATE_DIR = self.data_dir / "browser_state"
            paths.LOGS_DIR = self.data_dir / "logs"
            paths.PROGRESS_FILE = paths.LOGS_DIR / "progress.json"
            scheduler.STATE_FILE = self.data_dir / "scheduler.json"
            capture.refresh_paths()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.executable_path = _runtime_path()
        _validate_runtime_path(self.executable_path)
        self._process_lock: _AgentProcessLock | None = None
        if acquire_lock:
            self._process_lock = _AgentProcessLock(self.data_dir, {
                "pid": os.getpid(), "started_at": utc_now(),
                "executable_path": str(self.executable_path), "version": AGENT_VERSION,
                "data_dir": str(self.data_dir.resolve()),
            })
            self._process_lock.acquire()
        self.outbox_dir = self.data_dir / "outbox"
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.job_reports_dir = self.data_dir / "job-reports"
        self.job_reports_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.data_dir / "agent.json"
        self.token = token or os.environ.get("PRACTICAL_TOOLS_AGENT_TOKEN", "")
        self.agent_id = ""
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PracticalToolsAgent/1.0"})
        self.job_thread: threading.Thread | None = None
        self.current_job: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._outbox_lock = threading.Lock()
        self._last_schedule_signature: str | None = None
        self._next_config_sync = 0.0
        self._last_network_log: dict[str, float] = {}
        self._next_token_retry = 0.0
        self._last_token_log = 0.0
        self._load_token = load_token
        self._protocol_compatible = True
        self._self_update_started = False
        self._next_self_update_attempt = 0.0
        try:
            self._load_local_identity(allow_path_change=allow_path_change)
            paths.ensure_data()
            self._log(f"Agent 已启动，中心={self.server_url}，路径={self.executable_path}")
        except Exception:
            self.close()
            raise

    def _log(self, message: str, exc: BaseException | None = None) -> None:
        """Keep local diagnostics useful even when launchd captures no stdout."""
        line = f"{utc_now()} [pid={os.getpid()}] {message}"
        try:
            paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with (paths.LOGS_DIR / "agent-runtime.log").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                if exc is not None:
                    stream.write(f"{type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        print(line, file=sys.stderr, flush=True)
        if exc is not None:
            traceback.print_exception(type(exc), exc, exc.__traceback__)

    def _log_network_error(self, operation: str, exc: BaseException) -> None:
        now = time.monotonic()
        if now - self._last_network_log.get(operation, 0.0) < 30:
            return
        self._last_network_log[operation] = now
        self._log(f"中心通信失败（{operation}）：{type(exc).__name__}: {exc}", exc)

    def _load_local_identity(self, *, allow_path_change: bool = False) -> None:
        try:
            payload = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        previous_path = str(payload.get("executable_path") or "").strip()
        if previous_path and Path(previous_path).resolve() != self.executable_path and not allow_path_change:
            raise RuntimeError(f"Agent 启动路径与已登记的正式路径不一致：{self.executable_path}；正式路径为 {previous_path}")
        if previous_path and Path(previous_path).resolve() != self.executable_path and allow_path_change:
            self._log(f"Agent 正式路径已迁移：{previous_path} -> {self.executable_path}")
        self.agent_id = str(payload.get("agent_id") or uuid.uuid4())
        if self.server_url == DEFAULT_SERVER and payload.get("server_url"):
            self.server_url = str(payload["server_url"]).rstrip("/")
        if not self.token and self._load_token:
            try:
                self.token = self._load_token_from_keyring()
            except Exception as exc:
                # A locked/inaccessible macOS Keychain must not freeze the
                # background Agent before it can restore local scheduling.
                self._log("读取 Agent 凭据失败，将以离线模式启动并稍后重试", exc)
        self._save_local_identity()

    def _save_local_identity(self) -> None:
        # Agent tokens are credentials, not configuration. Keep them in the
        # OS credential manager, never in the JSON identity file.
        payload = {"agent_id": self.agent_id, "server_url": self.server_url,
                   "executable_path": str(self.executable_path),
                   "executable_sha256": _runtime_sha256(self.executable_path),
                   "version": AGENT_VERSION}
        tmp = self.config_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.config_file)

    def _keyring_name(self) -> str:
        return f"{self.server_url}|{self.agent_id}"

    def _load_token_from_keyring(self) -> str:
        if keyring is None:
            return ""
        if platform.system() == "Darwin":
            # keyring's macOS backend can block inside SecItemCopyMatching
            # when a newly replaced unsigned binary needs Keychain approval.
            # The security CLI is isolated and bounded so launchd cannot be
            # left with a permanently stuck Python process.
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "practical-tools-agent",
                 "-a", self._keyring_name(), "-w"],
                capture_output=True, text=True, timeout=KEYCHAIN_COMMAND_TIMEOUT,
                check=False,
            )
            if result.returncode != 0:
                return ""
            return result.stdout.strip()
        try:
            return str(keyring.get_password("practical-tools-agent", self._keyring_name()) or "")
        except Exception as exc:
            raise RuntimeError("无法读取系统凭据库，请修复 Keychain/Credential Manager 后重试") from exc

    def _retry_load_token(self) -> None:
        if self.token or not self._load_token or time.monotonic() < self._next_token_retry:
            return
        self._next_token_retry = time.monotonic() + KEYCHAIN_RETRY_SECONDS
        try:
            token = self._load_token_from_keyring()
            if token:
                self.token = token
                self._save_local_identity()
                self._log("Agent 凭据已恢复，退出离线模式")
        except Exception as exc:
            now = time.monotonic()
            if now - self._last_token_log >= KEYCHAIN_RETRY_SECONDS:
                self._last_token_log = now
                self._log("Agent 凭据暂时不可用，继续离线模式", exc)

    def _save_token_to_keyring(self) -> None:
        if not self.token:
            return
        if keyring is None:
            raise RuntimeError("未安装 keyring，拒绝将 Agent token 明文写入磁盘")
        try:
            keyring.set_password("practical-tools-agent", self._keyring_name(), self.token)
        except Exception as exc:
            raise RuntimeError("无法写入系统凭据库，Agent token 未保存") from exc

    def headers(self) -> dict[str, str]:
        # 兼容后端实施的 Bearer 或 X-Agent-Token 取值方式；不在日志中输出 token。
        return {"Authorization": f"Bearer {self.token}", "X-Agent-Token": self.token,
                "X-Agent-Id": self.agent_id}

    @contextmanager
    def _capture_lock(self):
        lock = self.data_dir / ".capture.lock"; lock.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "created_at": utc_now()}).encode()); os.close(fd)
                break
            except FileExistsError:
                owner: dict[str, Any] = {}
                try:
                    owner = json.loads(lock.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    owner = {}
                state = _process_state(int(owner.get("pid", 0)))
                if state is False:
                    # Retry in this generator instead of recursively returning
                    # another contextmanager, which raises "generator didn't
                    # yield" and makes a scheduled run silently skip.
                    lock.unlink(missing_ok=True)
                    continue
                detail = f"PID {owner.get('pid')}" if owner else "锁状态无法确认"
                raise RuntimeError(f"已有采集任务运行（{detail}）")
        try: yield
        finally: lock.unlink(missing_ok=True)

    def url(self, path: str) -> str:
        return path if path.startswith("http://") or path.startswith("https://") else self.server_url + "/" + path.lstrip("/")

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers = {**self.headers(), **headers}
        return self.session.request(method, self.url(path), headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs)

    def pair(self, code: str) -> dict[str, Any]:
        response = self.request("POST", "/api/agent/pair", json={"code": code, "device_id": self.agent_id,
                                                                   "agent_id": self.agent_id, "name": platform.node() or "页面收集 Agent",
                                                                   "platform": platform.system(), "version": AGENT_VERSION,
                                                                   "protocol_version": AGENT_PROTOCOL_VERSION,
                                                                   "capabilities": list(AGENT_CAPABILITIES)})
        response.raise_for_status()
        data = response.json()
        token = data.get("token") or data.get("agent_token")
        if not token:
            raise RuntimeError("配对响应没有 Agent token")
        self.token = str(token)
        self._save_token_to_keyring()
        self._save_local_identity()
        autostart = scheduler.install_autostart(self.server_url, self.data_dir)
        data["autostart"] = autostart
        if not autostart.get("ok"):
            error = RuntimeError(autostart.get("error") or "未知自启动错误")
            self._log("配对凭据已保存，但 Agent 开机自启动安装失败", error)
            raise error
        return data

    def repair_install(self) -> dict[str, Any]:
        """Reinstall launch hooks without creating a new Agent identity.

        This is intentionally local-only.  The saved server URL, device ID,
        Keychain token, browser state and schedule remain unchanged, so moving
        the center behind the same fixed domain does not require re-pairing.
        """
        if not self.agent_id:
            raise RuntimeError("本地没有 Agent 身份，请先使用配对码完成首次绑定")
        autostart = scheduler.install_autostart(self.server_url, self.data_dir)
        saved_schedule = scheduler.load_saved_schedule()
        schedule_result = {"ok": True, "configured": False}
        if saved_schedule:
            schedule_result = scheduler.install(self.server_url, self.data_dir)
            schedule_result["configured"] = True
        if not autostart.get("ok"):
            raise RuntimeError(autostart.get("error") or "Agent 自启动安装失败")
        if saved_schedule and not schedule_result.get("ok"):
            raise RuntimeError(schedule_result.get("error") or "Agent 定时任务安装失败")
        self._save_local_identity()
        return {"ok": True, "agent_id": self.agent_id, "server_url": self.server_url,
                "autostart": autostart, "schedule": schedule_result,
                "token_loaded": bool(self.token)}

    def _fetch_update_manifest(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        manifest = descriptor if descriptor.get("url") else None
        manifest_url = str(descriptor.get("manifest_url") or "").strip()
        if manifest is None:
            if not _is_https_url(manifest_url):
                raise RuntimeError("中心未提供可信的 Agent HTTPS 更新清单")
            response = self.session.get(manifest_url, timeout=20, allow_redirects=False)
            if 300 <= response.status_code < 400:
                raise RuntimeError("Agent 更新清单禁止 HTTP 重定向")
            response.raise_for_status()
            manifest = response.json()
        if not isinstance(manifest, dict):
            raise RuntimeError("Agent 更新清单必须是 JSON 对象")
        return manifest

    def _start_self_update(self, response_data: dict[str, Any]) -> None:
        if self.current_job:
            self._log("Agent 协议不兼容，但当前仍有任务运行；任务完成后再自更新")
            return
        now = time.monotonic()
        if self._self_update_started or now < self._next_self_update_attempt:
            return
        self._next_self_update_attempt = now + 300
        descriptor = response_data.get("agent_update")
        if not isinstance(descriptor, dict):
            self._log("Agent 协议不兼容，中心未配置可用的自更新清单")
            return
        try:
            manifest = self._fetch_update_manifest(descriptor)
            target_platform = _platform_name()
            manifest_platform = _platform_name(manifest.get("platform"))
            if manifest_platform != target_platform:
                raise RuntimeError(f"更新包平台不匹配：需要 {target_platform}，收到 {manifest_platform}")
            target_protocol = int(manifest.get("protocol_version", 0))
            min_protocol = int(response_data.get("min_protocol_version", AGENT_PROTOCOL_VERSION))
            center_protocol = int(response_data.get("protocol_version", AGENT_PROTOCOL_VERSION))
            if not min_protocol <= target_protocol <= center_protocol:
                raise RuntimeError(f"更新包协议版本不在中心支持范围内：{target_protocol}")
            target_version = str(manifest.get("version") or "").strip()
            if not target_version or _version_key(target_version) == _version_key(AGENT_VERSION):
                raise RuntimeError(f"更新包版本无效或未变化：{target_version or '空'}")
            if _version_key(target_version) < _version_key(AGENT_VERSION):
                raise RuntimeError(f"拒绝降级 Agent：{AGENT_VERSION} -> {target_version}")
            package_url = str(manifest.get("url") or "").strip()
            expected_sha = str(manifest.get("sha256") or "").strip().lower()
            expected_size = int(manifest.get("size", 0))
            if not _is_https_url(package_url):
                raise RuntimeError("Agent 更新包地址必须是 HTTPS URL")
            if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                raise RuntimeError("Agent 更新清单缺少有效的 64 位 SHA-256")
            if expected_size <= 0 or expected_size > AGENT_UPDATE_MAX_BYTES:
                raise RuntimeError("Agent 更新包大小不合法")
            staging = self.data_dir / "agent-update-staging"
            staging.mkdir(parents=True, exist_ok=True)
            package = staging / f"agent-{target_version}-{os.getpid()}.zip"
            temporary = package.with_suffix(".download")
            digest = hashlib.sha256()
            size = 0
            with self.session.get(package_url, stream=True, timeout=60, allow_redirects=False) as download:
                if 300 <= download.status_code < 400:
                    raise RuntimeError("Agent 更新包禁止 HTTP 重定向")
                download.raise_for_status()
                with temporary.open("wb") as stream:
                    for chunk in download.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > AGENT_UPDATE_MAX_BYTES:
                            raise RuntimeError("Agent 更新包超过大小上限")
                        digest.update(chunk)
                        stream.write(chunk)
            if size != expected_size or digest.hexdigest() != expected_sha:
                temporary.unlink(missing_ok=True)
                raise RuntimeError("Agent 更新包大小或 SHA-256 校验失败")
            temporary.replace(package)
            restart_file = staging / "restart.json"
            if getattr(sys, "frozen", False):
                command = [str(self.executable_path), *sys.argv[1:]]
                mode = "binary"
                target = self.executable_path
            else:
                command = [sys.executable, str(self.executable_path), *sys.argv[1:]]
                mode = "source"
                target = self.executable_path.parent
            restart_file.write_text(json.dumps({"command": command}, ensure_ascii=False), encoding="utf-8")
            worker_command = ([str(self.executable_path)] if getattr(sys, "frozen", False)
                              else [sys.executable, str(self.executable_path)])
            worker_command += ["--self-update-worker", "--update-package", str(package),
                               "--update-target", str(target), "--update-mode", mode,
                               "--wait-pid", str(os.getpid()), "--restart-file", str(restart_file),
                               "--data-dir", str(self.data_dir)]
            subprocess.Popen(worker_command, cwd=str(self.executable_path.parent),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self._self_update_started = True
            self._protocol_compatible = False
            self._log(f"Agent 协议不兼容，已启动自更新 worker：{target_version}")
            self.stop()
        except Exception as exc:
            self._log("Agent 协议不兼容，自更新未启动；本次继续保持空闲", exc)

    def heartbeat(self) -> None:
        if not self.token:
            return
        scheduled_capture = self._scheduled_capture_snapshot()
        busy = bool(self.current_job or scheduled_capture)
        meta = {"hostname": platform.node(), "job_id": (self.current_job or {}).get("id"),
                "python": platform.python_version(), "executable_path": str(self.executable_path),
                "executable_sha256": _runtime_sha256(self.executable_path), "agent_version": AGENT_VERSION}
        if scheduled_capture:
            meta.update(scheduled_capture)
        payload = {"agent_id": self.agent_id, "version": AGENT_VERSION, "platform": platform.system(),
                   "protocol_version": AGENT_PROTOCOL_VERSION, "capabilities": list(AGENT_CAPABILITIES),
                   "hostname": platform.node(), "job_id": (self.current_job or {}).get("id"),
                   "status": "busy" if busy else "idle", "sent_at": utc_now(), "meta": meta}
        try:
            response = self.request("POST", "/api/agent/heartbeat", json=payload, timeout=10)
            response.raise_for_status()
            data = response.json() or {}
            self._protocol_compatible = bool(data.get("compatible", True))
            if not self._protocol_compatible:
                self._start_self_update(data)
        except requests.RequestException as exc:
            self._log_network_error("heartbeat", exc)
            pass

    def _scheduled_capture_snapshot(self) -> dict[str, Any]:
        """Expose a local one-shot scheduled capture to the center UI."""
        if self.current_job:
            return {}
        lock = self.data_dir / ".capture.lock"
        if not lock.is_file():
            return {}
        try:
            owner = json.loads(lock.read_text(encoding="utf-8"))
            state = _process_state(int(owner.get("pid", 0)))
        except (OSError, ValueError, TypeError):
            state = None
        if state is False:
            return {}
        progress: dict[str, Any] = {}
        try:
            value = json.loads(paths.PROGRESS_FILE.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                progress = value
        except (OSError, ValueError, TypeError):
            pass
        return {"capture_running": True, "capture_source": "scheduled", "capture_progress": progress}

    def get_next_job(self) -> dict[str, Any] | None:
        response = self.request("GET", "/api/agent/jobs/next", timeout=15)
        if response.status_code in (204, 404):
            return None
        response.raise_for_status()
        data = response.json() or {}
        job = data.get("job", data)
        if not isinstance(job, dict) or not job.get("id"):
            return None
        return job

    def _job_report_file(self, job_id: Any, action: str) -> Path:
        safe_job_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id))
        safe_action = re.sub(r"[^A-Za-z0-9_.-]", "_", str(action))
        return self.job_reports_dir / f"{safe_job_id}.{safe_action}.json"

    def _post_job_once(self, job_id: Any, action: str, payload: dict[str, Any] | None = None) -> None:
        body = {"agent_id": self.agent_id, "action": action, **(payload or {})}
        response = self.request("POST", f"/api/agent/jobs/{job_id}/{action}", json=body, timeout=20)
        if response.status_code == 404:
            response = self.request("POST", f"/api/agent/jobs/{job_id}", json=body, timeout=20)
        response.raise_for_status()

    def _save_job_report(self, job_id: Any, action: str, payload: dict[str, Any]) -> None:
        """持久化 complete/fail 回报，避免 502 时中心任务永久 running。"""
        target = self._job_report_file(job_id, action)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"job_id": str(job_id), "action": action,
                                         "payload": payload}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def flush_job_reports(self, limit: int = 4) -> None:
        """重试之前因网络问题未送达的最终任务回报。"""
        for report_file in sorted(self.job_reports_dir.glob("*.json"))[:limit]:
            try:
                report = json.loads(report_file.read_text(encoding="utf-8"))
                self._post_job_once(report["job_id"], report["action"], report.get("payload") or {})
                report_file.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, requests.RequestException) as exc:
                self._log_network_error("job-report-retry", exc)

    def post_job(self, job_id: Any, action: str, payload: dict[str, Any] | None = None) -> None:
        # `/jobs/next` 已在中心端原子认领并置为 running；旧兼容服务没有 start 路由。
        if action == "start":
            return
        body = {"agent_id": self.agent_id, "action": action, **(payload or {})}
        last_error: requests.RequestException | None = None
        for attempt in range(JOB_REPORT_RETRY_COUNT):
            try:
                self._post_job_once(job_id, action, body)
                self._job_report_file(job_id, action).unlink(missing_ok=True)
                return
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < JOB_REPORT_RETRY_COUNT:
                    time.sleep(JOB_REPORT_RETRY_DELAY)
        # 进度可以丢失，complete/fail 不能丢失；最终状态写盘后由主循环继续重试。
        if action in {"complete", "fail"}:
            try:
                self._save_job_report(job_id, action, body)
            except OSError as exc:
                self._log("任务最终回报写入失败", exc)
        if last_error is not None:
            self._log_network_error(f"job/{action}", last_error)

    def sync_config(self, *, sync_schedule: bool = True) -> dict[str, Any]:
        """同步中心配置，可选地同步本机定时任务。

        常驻 Agent 需要把中心的定时配置镜像到本机；一次性的本地定时
        进程则已经是由该 launchd/schtasks 任务启动的，不能在自身运行时
        再次卸载并安装同一个任务，否则 macOS launchd 可能直接终止当前
        进程并留下采集锁。
        """
        try:
            response = self.request("GET", "/api/agent/config", timeout=20)
            if response.ok:
                data = response.json() or {}
                config = data.get("config", data)
                if sync_schedule and isinstance(data, dict) and "schedule" in data:
                    # The center is authoritative.  In particular, None is an
                    # explicit disable and must be persisted locally so an
                    # offline Agent cannot resurrect an old timer.
                    self._sync_scheduler(data.get("schedule"), source="中心配置")
                if isinstance(config, dict) and config.get("shops"):
                    # The center is authoritative for shops/schedule, but it
                    # does not own the local capture parameters.  Merge with
                    # both the last local config and the bundled template so
                    # an older data directory missing newly introduced keys
                    # is repaired on the next successful sync.
                    config = self._merge_center_config(config)
                    paths.CONFIG_FILE.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
                    return config
                if isinstance(data, dict):
                    if sync_schedule and "schedule" not in data:
                        self._sync_scheduler(config.get("schedule") if isinstance(config, dict) else None, source="中心配置")
                    if isinstance(config, dict):
                        return config
        except (requests.RequestException, OSError, yaml.YAMLError) as exc:
            self._log_network_error("config", exc)
            pass
        # A network failure must not erase the last known local schedule.  The
        # scheduled launchd/schtasks entry is local and can keep collecting;
        # uploads will remain in the outbox until the center is reachable.
        config = capture.load_config()
        self._restore_local_scheduler(config)
        return config

    @staticmethod
    def _read_yaml_config(path: Path) -> dict[str, Any]:
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return {}
        return value if isinstance(value, dict) else {}

    def _merge_center_config(self, center_config: dict[str, Any]) -> dict[str, Any]:
        """Merge center-owned data without erasing local capture settings."""
        bundled = self._read_yaml_config(paths.RESOURCE_DIR / "config.yaml")
        local = self._read_yaml_config(paths.CONFIG_FILE)
        merged = {**bundled, **local, **center_config}
        for key in LOCAL_CAPTURE_CONFIG_KEYS:
            if key in local:
                merged[key] = local[key]
            elif key in bundled:
                merged[key] = bundled[key]
        return merged

    def _restore_local_scheduler(self, config: dict[str, Any] | None) -> None:
        """Restore the current online Agent's timer from last-known-good data."""
        saved = scheduler.load_saved_schedule()
        if saved is not None:
            self._sync_scheduler(saved, source="本地缓存")
            return
        if not isinstance(config, dict) or not isinstance(config.get("schedule"), dict):
            return
        # A bundled config is a template, not proof that the user enabled a
        # timer.  Only a locally modified config may be used as a recovery
        # source when scheduler.json is absent (older Agent versions did this).
        local_config = paths.CONFIG_FILE
        bundled_config = paths.RESOURCE_DIR / "config.yaml"
        try:
            if bundled_config.exists() and local_config.read_bytes() == bundled_config.read_bytes():
                return
        except OSError:
            return
        self._sync_scheduler(config.get("schedule"), source="本地配置")

    def _sync_scheduler(self, schedule: dict[str, Any] | None, *, source: str = "未知来源") -> bool:
        """把中心端启用的定时配置镜像到本机；中心返回 None 表示已卸载。

        通过签名避免每次 3 秒轮询都重新调用 launchctl/schtasks。调度任务仍由
        Agent 本地执行，中心只负责保存用户在网页上设置的配置。
        """
        signature = json.dumps(schedule, ensure_ascii=False, sort_keys=True) if schedule else "disabled"
        if signature == self._last_schedule_signature:
            try:
                current = scheduler.status()
            except OSError as exc:
                self._log("读取本地定时任务状态失败，将尝试重新同步", exc)
                current = {}
            # A plist can exist while launchd failed to load it.  Retry in
            # that case instead of permanently accepting a broken install.
            if not schedule and not current.get("installed"):
                return True
            if schedule and current.get("installed") and (platform.system() != "Darwin" or current.get("running")):
                return True
        try:
            if schedule:
                normalized = scheduler.normalize_schedule(schedule)
                saved = scheduler.set_schedule(normalized["weekdays"], normalized["hour"], normalized["minute"])
                if not saved.get("ok"):
                    raise RuntimeError(saved.get("error") or "本地定时配置保存失败")
                result = scheduler.install(self.server_url, self.data_dir)
                if not result.get("ok"):
                    raise RuntimeError(result.get("error") or "本地定时任务安装失败")
            else:
                result = scheduler.uninstall()
                scheduler.set_disabled()
                if not result.get("ok"):
                    raise RuntimeError(result.get("error") or "本地定时任务卸载失败")
            self._last_schedule_signature = signature
            self._log(f"本地定时任务已同步：{source}，状态={'启用' if schedule else '停用'}")
            return True
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            # 调度安装失败不能让截图 Agent 退出，下一次中心配置变化时会重试。
            self._last_schedule_signature = None
            self._log(f"本地定时任务同步失败：{source}", exc)
            return False

    def register_outbox(self, path: str | Path, metadata: dict[str, Any]) -> dict[str, Any]:
        file_path = Path(path)
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        entry = {"capture_id": metadata.get("capture_id") or str(uuid.uuid4()), "path": str(file_path),
                 "sha256": digest, "size": file_path.stat().st_size, "metadata": metadata,
                 "status": "pending", "attempts": 0, "updated_at": utc_now()}
        outbox_file = self.outbox_dir / f"{entry['capture_id']}.json"
        outbox_file.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        return entry

    def _prepare_upload(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        metadata = {**entry.get("metadata", {}), "capture_id": entry["capture_id"], "sha256": entry["sha256"],
                    "size": entry["size"], "agent_id": self.agent_id, "filename": Path(entry["path"]).name}
        try:
            response = self.request("POST", "/api/agent/screenshots/prepare", json=metadata, timeout=20)
            if response.status_code != 404:
                response.raise_for_status()
                data = response.json() or {}
                data.setdefault("metadata", metadata)
                return data
            # 兼容当前中心服务的 initiate/complete 命名，以及将来 prepare 命名。
            response = self.request("POST", "/api/agent/screenshots/initiate", params={
                "capture_id": entry["capture_id"], "filename": Path(entry["path"]).name, "size": entry["size"], "sha256": entry["sha256"]}, timeout=20)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json() or {}
            data.setdefault("metadata", metadata)
            if data.get("upload_url"):
                data.setdefault("upload_mode", "cos_presigned")
            return data
        except requests.RequestException:
            return None

    def upload_one(self, entry: dict[str, Any]) -> bool:
        path = Path(entry["path"])
        if not path.is_file():
            entry["status"] = "missing"
            self._save_entry(entry)
            return False
        prepared = self._prepare_upload(entry)
        if prepared and prepared.get("upload_mode") == "cos_presigned":
            upload_url = prepared.get("upload_url") or prepared.get("url")
            if not upload_url:
                raise RuntimeError("COS 预签名响应缺少 upload_url")
            put_headers = prepared.get("headers") or prepared.get("upload_headers") or {}
            with path.open("rb") as stream:
                uploaded = self.session.put(upload_url, data=stream, headers=put_headers, timeout=180)
            uploaded.raise_for_status()
            confirm_url = prepared.get("confirm_url") or "/api/agent/screenshots/complete"
            if not str(confirm_url).startswith(("http://", "https://")) and "capture_id=" not in str(confirm_url):
                confirm_url = f"{confirm_url}?capture_id={entry['capture_id']}"
            confirm_body = {**prepared.get("metadata", {}), "storage_key": prepared.get("storage_key"),
                            "etag": uploaded.headers.get("ETag", "").strip('"')}
            confirmed = self.request("POST", confirm_url, json=confirm_body, timeout=30)
            confirmed.raise_for_status()
        elif prepared and prepared.get("upload_mode") == "local_multipart":
            upload_url = prepared.get("upload_url") or "/api/agent/screenshots"
            with path.open("rb") as stream:
                uploaded = self.request("POST", upload_url, params=prepared.get("fields") or {},
                                        files={"file": (path.name, stream, "image/png")}, timeout=180)
            uploaded.raise_for_status()
        else:
            metadata = {**entry.get("metadata", {}), "capture_id": entry["capture_id"], "sha256": entry["sha256"],
                        "agent_id": self.agent_id}
            with path.open("rb") as stream:
                # FastAPI 的上传路由把 capture_id/brand/captured_at 声明为 Query 参数，
                # 因此必须放在 params 而不是 multipart 表单字段中。
                uploaded = self.request("POST", "/api/agent/screenshots", params=metadata,
                                        files={"file": (path.name, stream, "image/png")}, timeout=180)
            uploaded.raise_for_status()
        entry["status"] = "uploaded"
        entry["uploaded_at"] = utc_now()
        self._save_entry(entry)
        return True

    def _save_entry(self, entry: dict[str, Any]) -> None:
        (self.outbox_dir / f"{entry['capture_id']}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")

    def flush_outbox(self, limit: int = 4) -> None:
        with self._outbox_lock:
            files = sorted(self.outbox_dir.glob("*.json"))[:limit]
            for descriptor in files:
                try:
                    entry = json.loads(descriptor.read_text(encoding="utf-8"))
                    if entry.get("status") == "uploaded":
                        descriptor.unlink(missing_ok=True)
                        continue
                    if int(entry.get("attempts", 0)) >= MAX_UPLOAD_ATTEMPTS:
                        continue
                    entry["attempts"] = int(entry.get("attempts", 0)) + 1
                    self._save_entry(entry)
                    self.upload_one(entry)
                except (OSError, ValueError, requests.RequestException, RuntimeError) as exc:
                    try:
                        entry["last_error"] = str(exc)[:500]
                        entry["updated_at"] = utc_now()
                        self._save_entry(entry)
                    except Exception:
                        pass

    def capture_job(self, job: dict[str, Any]) -> None:
        try:
            with self._capture_lock(): self._capture_job_locked(job)
        except BaseException as exc:
            # Lock acquisition and any future pre-flight code must not be able
            # to strand a claimed center job in running state either.
            self._log(f"截图任务启动失败：{job.get('id')}", exc)
            self.post_job(job.get("id"), "fail", {
                "status": "failed", "error": str(exc)[:1000] or type(exc).__name__
            })

    def _capture_job_locked(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else job
        command = str(payload.get("command") or payload.get("type") or job.get("type") or job.get("kind") or "")
        if command in ("", "capture"):
            command = "capture_url" if payload.get("url") else ("capture_shop" if payload.get("shop") else "capture_all")
        self.post_job(job_id, "start", {"status": "running", "command": command})
        capture.set_stop(False)
        capture.set_paused(False)
        capture.initialize_progress(stage="正在同步店铺配置")
        result: dict[str, Any] = {}
        monitor_stop = threading.Event()

        def monitor() -> None:
            while not monitor_stop.wait(1.5):
                try:
                    progress = json.loads(paths.PROGRESS_FILE.read_text(encoding="utf-8")) if paths.PROGRESS_FILE.exists() else {}
                    self.post_job(job_id, "progress", {"progress": progress, "status": "paused" if capture._pause_event.is_set() else "running"})
                except (OSError, ValueError):
                    pass

        watcher = threading.Thread(target=monitor, name="capture-progress", daemon=True)
        watcher.start()
        try:
            config = self.sync_config()
            shops = config.get("shops", []) if isinstance(config, dict) else []
            if not isinstance(shops, list) or not shops:
                capture.initialize_progress(stage="店铺配置为空")
                raise RuntimeError("中心未返回可截图的店铺配置，请检查 Agent 绑定账号和店铺列表")
            capture.initialize_progress(len(shops), "已同步店铺配置，准备启动浏览器")
            self._log(f"截图任务开始执行：{job_id}，店铺数={len(shops)}")
            if command in ("capture_shop", "shop"):
                name = payload.get("shop") or payload.get("shop_name")
                result = capture.capture_shops(config, shop_filter=name)
            elif command in ("capture_url", "url"):
                url = str(payload.get("url") or "")
                name = payload.get("name") or capture.derive_name_from_url(url)
                result = capture.capture_shops(config, shops=[{"name": name, "url": url}])
            else:
                result = capture.capture_shops(config)
            for item in result.get("success", []):
                file_path = Path(item["file"])
                capture_id = hashlib.sha256(f"{job_id}:{file_path}".encode()).hexdigest()[:32]
                # 兼容旧截图目录 `{品牌}/{YYYY-MM-DD}/{HH-MM-SS}.png`，中心端可直接按这些字段分组。
                rel = file_path.relative_to(paths.SCREENSHOTS_DIR) if file_path.is_relative_to(paths.SCREENSHOTS_DIR) else Path(file_path.name)
                parts = rel.parts
                self.register_outbox(file_path, {"capture_id": capture_id, "brand": item.get("name") or (parts[0] if parts else ""),
                                                  "date": parts[1] if len(parts) > 2 else datetime.now().strftime("%Y-%m-%d"),
                                                  "original_name": parts[-1], "captured_at": utc_now(), "job_id": job_id})
            self.flush_outbox(limit=100)
            self.post_job(job_id, "complete", {"status": "completed", "result": result})
        except BaseException as exc:
            # capture.py 旧版本曾用 SystemExit 处理配置错误；后台线程必须
            # 捕获 BaseException，否则线程会无声退出而中心任务永久 running。
            self._log(f"截图任务失败：{job_id} command={command}", exc)
            self.post_job(job_id, "fail", {"status": "failed", "error": str(exc)[:1000] or type(exc).__name__})
        finally:
            monitor_stop.set()
            capture.set_stop(False)
            capture.set_paused(False)

    def execute_aux(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else job
        command = str(payload.get("command") or payload.get("type") or job.get("type") or job.get("kind") or "")
        try:
            if command in ("pause", "capture_pause"):
                capture.set_paused(True)
            elif command in ("resume", "capture_resume"):
                capture.set_paused(False)
            elif command in ("stop", "capture_stop"):
                capture.set_stop(True)
            elif command in ("login", "taobao_login"):
                self._log(f"收到淘宝登录任务：{job_id}，开始启动可见浏览器")
                def launch_login() -> None:
                    try:
                        login.run_login(refresh=True)
                        self._log(f"淘宝登录浏览器任务结束：{job_id}")
                    except Exception as exc:
                        self._log(f"淘宝登录浏览器启动失败：{job_id}", exc)
                        self.post_job(job_id, "fail", {"status": "failed", "error": f"淘宝登录浏览器启动失败：{exc}"})
                # Keep the Agent polling loop free so the separate
                # login_done command can arrive while the visible browser is
                # waiting for the user.
                threading.Thread(target=launch_login, name="taobao-login", daemon=True).start()
                return
            elif command in ("login_done", "taobao_login_done"):
                self._log(f"收到淘宝登录完成指令：{job_id}")
                login.complete_login()
            elif command == "open_folder":
                target = paths.SCREENSHOTS_DIR
                target.mkdir(parents=True, exist_ok=True)
                if platform.system() == "Windows":
                    os.startfile(str(target))  # type: ignore[attr-defined]
                elif platform.system() == "Darwin":
                    os.spawnlp(os.P_NOWAIT, "open", "open", str(target))
            elif command in ("scheduler_install", "schedule_install"):
                schedule = payload.get("schedule") or payload
                saved = scheduler.set_schedule(schedule.get("weekdays", [1, 2, 3, 4, 5, 6, 7]), schedule.get("hour", 9), schedule.get("minute", 0))
                if not saved.get("ok"):
                    raise RuntimeError(saved.get("error") or "定时配置保存失败")
                result = scheduler.install(self.server_url, self.data_dir)
                if not result.get("ok"):
                    raise RuntimeError(result.get("error") or "定时任务安装失败")
            elif command in ("scheduler_uninstall", "schedule_uninstall"):
                result = scheduler.uninstall()
                if not result.get("ok"):
                    raise RuntimeError(result.get("error") or "定时任务卸载失败")
                scheduler.set_disabled()
            else:
                raise ValueError(f"未知 Agent 指令: {command}")
            self.post_job(job_id, "complete", {"status": "completed", "command": command})
        except Exception as exc:
            self._log(f"Agent 辅助任务失败：{job_id} command={command}", exc)
            self.post_job(job_id, "fail", {"status": "failed", "error": str(exc)[:1000]})

    def dispatch(self, job: dict[str, Any]) -> None:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        command = str(payload.get("command") or payload.get("type") or job.get("type") or job.get("kind") or "capture_all")
        if command == "capture":
            command = "capture_url" if payload.get("url") else ("capture_shop" if payload.get("shop") else "capture_all")
        target = self.capture_job if command.startswith("capture") or command in ("shop", "url") else self.execute_aux
        self.current_job = job
        self.job_thread = threading.Thread(target=target, args=(job,), name=f"job-{job['id']}", daemon=True)
        self.job_thread.start()

    def run(self) -> None:
        next_heartbeat = 0.0
        next_job_poll = 0.0
        idle_job_poll = POLL_SECONDS
        next_offline_log = 0.0
        offline_scheduler_restored = False
        while not self._stop.is_set():
            now = time.monotonic()
            if not self.token:
                self._retry_load_token()
                if not self.token:
                    if now >= next_offline_log:
                        self._log("Agent 未取得认证凭据，当前以离线模式运行；本地定时任务仍会执行")
                        next_offline_log = now + KEYCHAIN_RETRY_SECONDS
                    if not offline_scheduler_restored:
                        try:
                            self._restore_local_scheduler(capture.load_config())
                            offline_scheduler_restored = True
                        except Exception as exc:
                            self._log("离线恢复本地定时任务失败", exc)
                    self._stop.wait(POLL_SECONDS)
                    continue
            if now >= next_heartbeat:
                self.heartbeat()
                next_heartbeat = now + HEARTBEAT_SECONDS
            if not self._protocol_compatible:
                self._stop.wait(2)
                continue
            if now >= self._next_config_sync:
                try:
                    self.sync_config()
                except requests.RequestException:
                    pass
                self._next_config_sync = now + CONFIG_SYNC_SECONDS
            self.flush_outbox()
            self.flush_job_reports()
            if (not self.job_thread or not self.job_thread.is_alive()) and now >= next_job_poll:
                self.current_job = None
                try:
                    job = self.get_next_job()
                    if job:
                        self._log(f"从中心领取任务：{job.get('id')} command={job.get('kind')}")
                        self.dispatch(job)
                        idle_job_poll = POLL_SECONDS
                    else:
                        idle_job_poll = min(MAX_IDLE_JOB_POLL_SECONDS, max(POLL_SECONDS, idle_job_poll * 1.7))
                    next_job_poll = time.monotonic() + idle_job_poll
                except requests.RequestException as exc:
                    self._log_network_error("jobs/next", exc)
                    idle_job_poll = min(MAX_IDLE_JOB_POLL_SECONDS, max(POLL_SECONDS, idle_job_poll * 2))
                    next_job_poll = time.monotonic() + idle_job_poll
                except Exception as exc:
                    self._log("Agent 轮询或分发任务失败", exc)
                    next_job_poll = time.monotonic() + idle_job_poll
            self._stop.wait(POLL_SECONDS)

    def scheduled_run(self) -> None:
        """Run one local capture cycle without requiring a center job."""
        log = self.data_dir / "logs" / "scheduled.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._capture_lock():
                # launchd/schtasks normally have no attached terminal.  Keep
                # the whole one-shot run line-buffered in a dedicated log so
                # browser/capture failures cannot disappear with stderr.
                with log.open("a", encoding="utf-8", buffering=1) as stream, \
                        redirect_stdout(stream), redirect_stderr(stream):
                    stream.write(f"{utc_now()} scheduled start version={AGENT_VERSION} pid={os.getpid()}\n")
                    try:
                        self.flush_outbox(limit=100)
                        # This process was launched by the local scheduler.
                        # Do not let config sync bootout/bootstrap the same
                        # launchd task while it is running.
                        config = self.sync_config(sync_schedule=False)
                        shops = config.get("shops", []) if isinstance(config, dict) else []
                        stream.write(f"{utc_now()} scheduled config shops={len(shops) if isinstance(shops, list) else 0}\n")
                        result = capture.capture_shops(config)
                        success = result.get("success", []) if isinstance(result, dict) else []
                        for item in success:
                            path = Path(item["file"])
                            self.register_outbox(path, {
                                "capture_id": hashlib.sha256(f"scheduled:{path}".encode()).hexdigest()[:32],
                                "brand": item.get("name", ""),
                                "date": datetime.now().strftime("%Y-%m-%d"),
                                "original_name": path.name,
                                "captured_at": utc_now(),
                            })
                        self.flush_outbox(limit=100)
                        stream.write(
                            f"{utc_now()} scheduled completed success={len(success)} "
                            f"captcha={len(result.get('captcha', [])) if isinstance(result, dict) else 0} "
                            f"error={len(result.get('error', [])) if isinstance(result, dict) else 0}\n"
                        )
                    except BaseException as exc:
                        stream.write(f"{utc_now()} scheduled failed {type(exc).__name__}: {exc}\n")
                        traceback.print_exc(file=stream)
        except RuntimeError as exc:
            # This is the only expected failure outside the redirected log:
            # another foreground or scheduled capture owns the lock.
            with log.open("a", encoding="utf-8", buffering=1) as stream:
                stream.write(f"{utc_now()} scheduled skipped: {exc}\n")

    def stop(self) -> None:
        self._stop.set()
        capture.set_stop(True)

    def close(self) -> None:
        if self._process_lock is not None:
            self._process_lock.release()
            self._process_lock = None


def main() -> None:
    parser = argparse.ArgumentParser(description="策划实用小工具本地页面收集 Agent")
    parser.add_argument("--server", default=None)
    parser.add_argument("--token", default=os.environ.get("PRACTICAL_TOOLS_AGENT_TOKEN", ""))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--pair", metavar="CODE", help="使用中心服务显示的一次性配对码")
    parser.add_argument("--scheduled-run", action="store_true", help="执行一次本地定时采集后退出")
    parser.add_argument("--repair-install", action="store_true", help="保留现有身份，修复/迁移本机自启动和定时任务")
    parser.add_argument("--self-update-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-package", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--update-target", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--update-mode", choices=("binary", "source"), help=argparse.SUPPRESS)
    parser.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--restart-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_update_worker:
        if not all((args.update_package, args.update_target, args.update_mode, args.wait_pid, args.restart_file, args.data_dir)):
            raise SystemExit("Agent 自更新 worker 参数不完整")
        raise SystemExit(_run_self_update_worker(args.update_package.resolve(), args.update_target.resolve(),
                                                  args.update_mode, args.wait_pid, args.restart_file.resolve(),
                                                  args.data_dir.resolve()))
    server = args.server or os.environ.get("PRACTICAL_TOOLS_SERVER_URL", DEFAULT_SERVER)
    if args.scheduled_run and not args.server:
        saved = (args.data_dir or paths.DATA_DIR) / "agent.json"
        try: server = json.loads(saved.read_text(encoding="utf-8")).get("server_url") or server
        except (OSError, ValueError): pass
        if server == DEFAULT_SERVER: raise SystemExit("定时模式必须使用已配对的中心地址，请补充 --server")
    # Scheduled runs still need the paired Agent token to sync the center's
    # shop configuration and upload the outbox.  They skip only the long-lived
    # process lock; disabling credential loading here made launchd start a
    # process that could never authenticate.
    if args.repair_install and (args.pair or args.scheduled_run):
        raise SystemExit("--repair-install 不能与 --pair 或 --scheduled-run 同时使用")
    agent = Agent(server, args.token, args.data_dir, acquire_lock=not (args.scheduled_run or args.repair_install),
                  load_token=True, allow_path_change=args.repair_install)
    try:
        if args.pair:
            print(json.dumps(agent.pair(args.pair), ensure_ascii=False, indent=2))
            return
        if args.repair_install:
            print(json.dumps(agent.repair_install(), ensure_ascii=False, indent=2))
            return
        if args.scheduled_run:
            agent.scheduled_run(); return
        agent.run()
    except KeyboardInterrupt:
        agent.stop()
    finally:
        agent.close()


if __name__ == "__main__":
    main()

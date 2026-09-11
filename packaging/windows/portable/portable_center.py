"""Run the center with the bundled Windows Python runtime (no install/service)."""
from __future__ import annotations
import os, socket, subprocess, sys, time
import urllib.parse
from pathlib import Path

_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[1] if (_HERE.parents[1] / "alembic.ini").is_file() else _HERE.parents[3]

def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1); values[key.strip()] = value.strip().strip('"')
    return values

def bootstrap_env(env_path: Path) -> dict[str, str]:
    if env_path.is_file():
        current = read_env(env_path)
        database_url = current.get("PRACTICAL_DATABASE_URL", "")
        if database_url and "user:password" not in database_url:
            return current
    print("首次启动配置：请输入主图一条龙服务协同版的实际运行目录。")
    print(r"例如：D:\MainImageCollabPortable")
    reference = input("主图协同版目录（直接回车可手动填写数据库地址）：").strip().strip('"')
    values = {}
    if reference:
        root = Path(reference)
        values["PRACTICAL_SHARED_CENTER_ROOT"] = str(root.resolve())
        for candidate in (root / "config" / "app.env", root / ".env", root / "config" / ".env"):
            if candidate.is_file():
                source = read_env(candidate)
                if source.get("COLLAB_DATABASE_URL"):
                    values["PRACTICAL_DATABASE_URL"] = source["COLLAB_DATABASE_URL"]
                break
    if "PRACTICAL_DATABASE_URL" not in values:
        values["PRACTICAL_DATABASE_URL"] = input("未找到配置，请输入数据库地址：").strip()
    values.setdefault("PRACTICAL_APP_ENV", "production")
    values.setdefault("PRACTICAL_HOST", "0.0.0.0")
    values.setdefault("PRACTICAL_PORT", "18180")
    values.setdefault("PRACTICAL_AUTO_CREATE_TABLES", "false")
    values.setdefault("PRACTICAL_DATA_ROOT", str(ROOT / "data"))
    env_path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    print("已从主图协同版读取数据库配置并保存到本项目 .env。")
    return values

def _db_host_port(url: str):
    parsed = urllib.parse.urlparse(url.replace("postgresql+psycopg", "postgresql"))
    return parsed.hostname, parsed.port or 5432

def ensure_shared_postgres(root: Path, env: dict[str, str]) -> None:
    host, port = _db_host_port(env.get("PRACTICAL_DATABASE_URL", ""))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("检查远程 PostgreSQL，跳过本地 pg_ctl 启动。")
        return
    # A standard Windows installation may run PostgreSQL as its own service
    # instead of using the legacy collaboration package directory. If the
    # configured local port is already accepting connections, leave that
    # PostgreSQL instance alone and let the normal migration/connection check
    # validate its database credentials.
    try:
        with socket.create_connection((host, port), timeout=1):
            print("本地 PostgreSQL 已在运行，跳过随包 pg_ctl 启动。")
            return
    except OSError:
        pass
    # The installer initializes a standalone PostgreSQL cluster beside the
    # installed center. Start that cluster automatically on later
    # boots/restarts; only fall back to the legacy collaboration directory
    # when no standalone cluster exists.
    standalone_data = root / "postgresql" / "data"
    standalone_pgctl = root / "postgresql" / "bin" / "pg_ctl.exe"
    if standalone_pgctl.is_file() and (standalone_data / "PG_VERSION").is_file():
        print("检查安装包内置 PostgreSQL 状态……")
        status = subprocess.run([str(standalone_pgctl), "status", "-D", str(standalone_data)], capture_output=True)
        if status.returncode != 0:
            print("启动安装包内置 PostgreSQL……")
            log = root / "data" / "logs" / "postgresql-startup.log"; log.parent.mkdir(parents=True, exist_ok=True)
            # Start without pg_ctl's synchronous -w. Some Windows hosts can
            # leave that wrapper waiting on inherited child handles. The
            # bounded socket loop below provides the readiness check.
            try:
                started = subprocess.run(
                    [str(standalone_pgctl), "start", "-D", str(standalone_data), "-l", str(log), "-o", f"-p {port}"],
                    check=False,
                    timeout=30,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"安装包内置 PostgreSQL 启动命令超时，请检查日志：{log}") from exc
            if started.returncode != 0:
                raise RuntimeError(f"安装包内置 PostgreSQL 启动失败，请检查日志：{log}")
        print("等待安装包内置 PostgreSQL 连接（最多45秒）……")
        deadline = time.time()+45
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1): return
            except OSError: time.sleep(1)
        raise RuntimeError("安装包内置 PostgreSQL 45 秒内未就绪，请检查日志")
    reference = Path(env.get("PRACTICAL_SHARED_CENTER_ROOT", "")).expanduser()
    if not reference.is_dir():
        reference = Path(input("检测到本地数据库，请输入主图协同版目录：").strip().strip('"')).expanduser()
        env["PRACTICAL_SHARED_CENTER_ROOT"] = str(reference.resolve())
        (root / ".env").write_text("\n".join(f"{k}={v}" for k,v in env.items() if k.startswith("PRACTICAL_"))+"\n", encoding="utf-8")
    config = next((p for p in (reference/"config"/"app.env", reference/".env", reference/"config"/".env") if p.is_file()), None)
    pgctl, pgdata = reference/"postgresql"/"bin"/"pg_ctl.exe", reference/"data"/"postgresql"
    if not config or not pgctl.is_file() or not (pgdata/"PG_VERSION").is_file():
        raise RuntimeError("主图协同版目录无效：必须包含 config/app.env、postgresql/bin/pg_ctl.exe、data/postgresql/PG_VERSION")
    source = read_env(config)
    if source.get("COLLAB_DATABASE_URL") != env.get("PRACTICAL_DATABASE_URL"):
        raise RuntimeError("主图协同版数据库地址与本工具配置不一致，已拒绝启动")
    print("检查共享 PostgreSQL 状态……")
    status = subprocess.run([str(pgctl), "status", "-D", str(pgdata)], capture_output=True)
    if status.returncode != 0:
        print("启动共享 PostgreSQL（不初始化、不修改数据）……")
        log = root / "data" / "logs" / "postgresql-startup.log"; log.parent.mkdir(parents=True, exist_ok=True)
        started = subprocess.run([str(pgctl), "start", "-D", str(pgdata), "-l", str(log), "-w", "-t", "45"], check=False)
        if started.returncode != 0:
            raise RuntimeError(f"共享 PostgreSQL 启动失败，请检查日志：{log}")
    print("等待数据库连接（最多45秒）……")
    deadline = time.time()+45
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1): return
        except OSError: time.sleep(1)
    raise RuntimeError("共享 PostgreSQL 45 秒内未就绪，请检查主图协同版数据库日志")

def _write_center_pid(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pid.tmp")
    temporary.write_text(str(os.getpid()), encoding="ascii")
    os.replace(temporary, path)


def _remove_owned_center_pid(path: Path) -> None:
    try:
        if int(path.read_text(encoding="ascii").strip()) == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _runtime_python(root: Path) -> Path:
    """Use the windowless bundled interpreter for background center work."""
    windowless = root / "runtime" / "pythonw.exe"
    return windowless if windowless.is_file() else root / "runtime" / "python.exe"


def _hidden_process_kwargs() -> dict[str, object]:
    """Keep the long-running service detached from any visible console."""
    kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_type is not None:
            startupinfo = startupinfo_type()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def main() -> int:
    env = os.environ.copy(); env.update(bootstrap_env(ROOT / ".env"))
    env.setdefault("PRACTICAL_ENV_FILE", str(ROOT / ".env"))
    env.setdefault("PRACTICAL_DATA_ROOT", str(ROOT / "data"))
    env.setdefault("PRACTICAL_HOST", "0.0.0.0"); env.setdefault("PRACTICAL_PORT", "18180")
    python = _runtime_python(ROOT)
    env["PRACTICAL_PORTABLE_PID"] = str(os.getpid()); env["PRACTICAL_RUNTIME_PYTHON"] = str(python); env["PRACTICAL_PORTABLE_LAUNCHER"] = str(ROOT / "portable" / "portable_center.py")
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "data" / "logs" / "portable-center.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_stream = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = log_stream
    sys.stderr = log_stream
    print(f"\\n--- center start {time.strftime('%Y-%m-%d %H:%M:%S')} ---", flush=True)
    pid_path = ROOT / "data" / "logs" / "center.pid"
    _write_center_pid(pid_path)
    try:
        try:
            ensure_shared_postgres(ROOT, env)
        except (RuntimeError, OSError) as exc:
            print(f"启动失败：{exc}")
            return 1
        print("执行数据库迁移……")
        try:
            migration = subprocess.run([str(python), "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            print("数据库迁移超过 120 秒，中心服务未启动。")
            return 1
        if migration.returncode != 0:
            detail = (migration.stderr or migration.stdout or b"migration failed").decode("utf-8", "replace").strip()
            print("数据库自动迁移失败，中心服务未启动。")
            print(detail)
            return migration.returncode or 1
        print("数据库迁移完成，正在启动中心服务……")
        return subprocess.call(
            [str(python), "-m", "app.main"],
            cwd=ROOT,
            env=env,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            **_hidden_process_kwargs(),
        )
    finally:
        _remove_owned_center_pid(pid_path)

if __name__ == "__main__":
    raise SystemExit(main())

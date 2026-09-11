"""Standalone desktop controller for the center service and Cloudflare Tunnel.

This intentionally uses only the Python standard library so the same small
controller can be bundled with the Windows center package or run from source.
It never sends a shell command from the UI; it starts only the center process,
Quick Tunnel, or a named Tunnel.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


TUNNEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PUBLIC_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+\.trycloudflare\.com\b")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class CenterConsole(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("策划实用小工具 · 中心控制台")
        self.geometry("760x620")
        self.minsize(680, 520)
        self.configure(bg="#eef3f8")

        self.root_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        self.env_path = self.root_dir / ".env"
        self.env = read_env(self.env_path)
        self.data_root = self._data_root()
        self.settings_path = self.data_root / "admin" / "center-console.json"
        self.saved = load_json(self.settings_path)
        self.center_process: subprocess.Popen[Any] | None = None
        self.tunnel_process: subprocess.Popen[Any] | None = None
        self.center_log_path = self.data_root / "logs" / ("portable-center.log" if (self.root_dir / "portable" / "portable_center.py").is_file() else "center-console.log")
        self.tunnel_log_path = self.data_root / "logs" / "cloudflare-tunnel.log"
        self._closing = False

        self._build_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._minimize)
        self.after(700, self._refresh)

    def _data_root(self) -> Path:
        raw = self.env.get("PRACTICAL_DATA_ROOT", "data")
        path = Path(os.path.expandvars(raw)).expanduser()
        return path if path.is_absolute() else self.root_dir / path

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#16324f", background="#eef3f8")
        style.configure("Sub.TLabel", font=("Segoe UI", 10), foreground="#61758a", background="#eef3f8")
        style.configure("Card.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("CardTitle.TLabel", font=("Segoe UI", 12, "bold"), foreground="#16324f", background="#ffffff")
        style.configure("CardText.TLabel", font=("Segoe UI", 10), foreground="#5d7085", background="#ffffff")
        style.configure("Status.TLabel", font=("Segoe UI", 11, "bold"), foreground="#ba7b13", background="#ffffff")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="中心控制台", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="一个窗口管理中心服务和 Cloudflare Tunnel，终端只在后台记录日志。", style="Sub.TLabel").pack(anchor="w", pady=(3, 17))

        center = ttk.Frame(outer, style="Card.TFrame", padding=15)
        center.pack(fill="x", pady=(0, 12))
        ttk.Label(center, text="中心服务", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.center_status = ttk.Label(center, text="未运行", style="Status.TLabel")
        self.center_status.grid(row=0, column=1, sticky="e", padx=(10, 0))
        center.columnconfigure(0, weight=1)
        self.center_detail = ttk.Label(center, text="", style="CardText.TLabel")
        self.center_detail.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 11))
        center_actions = ttk.Frame(center, style="Card.TFrame")
        center_actions.grid(row=2, column=0, columnspan=2, sticky="w")
        self.center_start = ttk.Button(center_actions, text="启动中心", style="Primary.TButton", command=self.start_center)
        self.center_start.pack(side="left", padx=(0, 7))
        self.center_stop = ttk.Button(center_actions, text="停止中心", command=self.stop_center)
        self.center_stop.pack(side="left", padx=(0, 7))
        ttk.Button(center_actions, text="打开网页", command=self.open_web).pack(side="left")

        tunnel = ttk.Frame(outer, style="Card.TFrame", padding=15)
        tunnel.pack(fill="x", pady=(0, 12))
        ttk.Label(tunnel, text="Cloudflare Tunnel", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.tunnel_status = ttk.Label(tunnel, text="未运行", style="Status.TLabel")
        self.tunnel_status.grid(row=0, column=1, sticky="e", padx=(10, 0))
        tunnel.columnconfigure(1, weight=1)

        ttk.Label(tunnel, text="模式", style="CardText.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 4))
        self.mode = tk.StringVar(value=str(self.saved.get("mode") or self.env.get("PRACTICAL_TUNNEL_MODE", "quick")))
        self.mode_box = ttk.Combobox(tunnel, textvariable=self.mode, state="readonly", values=("quick", "named"), width=12)
        self.mode_box.grid(row=1, column=1, sticky="w", pady=(12, 4))
        self.mode_box.bind("<<ComboboxSelected>>", lambda _event: self._toggle_named())
        ttk.Label(tunnel, text="quick：临时公网地址；named：使用本机已登录的命名 Tunnel。", style="CardText.TLabel").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(12, 4))

        ttk.Label(tunnel, text="Tunnel 名称", style="CardText.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.name = tk.StringVar(value=str(self.saved.get("tunnel_name") or self.env.get("PRACTICAL_TUNNEL_NAME", "")))
        self.name_entry = ttk.Entry(tunnel, textvariable=self.name, width=25)
        self.name_entry.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(tunnel, text="仅 named 模式需要填写", style="CardText.TLabel").grid(row=2, column=2, sticky="w", padx=(12, 0), pady=4)

        ttk.Label(tunnel, text="cloudflared", style="CardText.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        self.executable = tk.StringVar(value=str(self.saved.get("cloudflared") or self.env.get("PRACTICAL_CLOUDFLARED_PATH", "cloudflared")))
        ttk.Entry(tunnel, textvariable=self.executable, width=42).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Button(tunnel, text="选择文件", command=self.choose_cloudflared).grid(row=3, column=2, sticky="w", padx=(12, 0), pady=4)

        self.public_url = ttk.Label(tunnel, text="公网地址：等待 Tunnel 启动", style="CardText.TLabel")
        self.public_url.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 4))
        self.tunnel_detail = ttk.Label(tunnel, text="", style="CardText.TLabel")
        self.tunnel_detail.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 10))
        actions = ttk.Frame(tunnel, style="Card.TFrame")
        actions.grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Button(actions, text="保存设置", command=self.save_settings).pack(side="left", padx=(0, 7))
        self.tunnel_start = ttk.Button(actions, text="启动 Tunnel", style="Primary.TButton", command=self.start_tunnel)
        self.tunnel_start.pack(side="left", padx=(0, 7))
        self.tunnel_stop = ttk.Button(actions, text="停止 Tunnel", command=self.stop_tunnel)
        self.tunnel_stop.pack(side="left", padx=(0, 7))
        ttk.Button(actions, text="一键启动全部", command=self.start_all).pack(side="left")
        tunnel.columnconfigure(1, weight=1)

        logs = ttk.Frame(outer, style="Card.TFrame", padding=12)
        logs.pack(fill="both", expand=True)
        ttk.Label(logs, text="最近日志", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 7))
        self.log_text = tk.Text(logs, height=11, bg="#101b2a", fg="#d7e6f7", insertbackground="#ffffff", relief="flat", font=("Consolas", 9), wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")
        ttk.Button(outer, text="退出控制台（服务保持运行）", command=self.exit_console).pack(anchor="e", pady=(10, 0))
        self._toggle_named()

    def _toggle_named(self) -> None:
        self.name_entry.configure(state="normal" if self.mode.get() == "named" else "disabled")

    def _runtime_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.env)
        env["PRACTICAL_ENV_FILE"] = str(self.env_path)
        env.setdefault("PRACTICAL_HOST", "127.0.0.1")
        env.setdefault("PRACTICAL_PORT", "18180")
        env.setdefault("PRACTICAL_DATA_ROOT", str(self.data_root))
        env["PYTHONPATH"] = str(self.root_dir) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def _port(self) -> int:
        try:
            return int(self._runtime_env().get("PRACTICAL_PORT", "18180"))
        except ValueError:
            return 18180

    def _command(self) -> list[str]:
        packaged = self.root_dir / "PracticalToolsOnline.exe"
        if packaged.is_file():
            return [str(packaged)]
        portable = self.root_dir / "portable" / "portable_center.py"
        runtime = self.root_dir / "runtime" / ("python.exe" if os.name == "nt" else "python3")
        if portable.is_file() and runtime.is_file():
            return [str(runtime), str(portable)]
        runner = runtime if runtime.is_file() else Path(sys.executable)
        return [str(runner), "-m", "app.main"]

    def _launch(self, command: list[str], log_path: Path, label: str) -> subprocess.Popen[Any]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = log_path.open("a", encoding="utf-8", buffering=1)
        stream.write(f"\n--- {label} start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs: dict[str, Any] = {"cwd": str(self.root_dir), "env": self._runtime_env(), "stdin": subprocess.DEVNULL, "stdout": stream, "stderr": subprocess.STDOUT, "creationflags": flags, "close_fds": os.name != "nt"}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError:
            stream.close()
            raise
        # The child owns the duplicated descriptor; close the controller copy.
        stream.close()
        return process

    def start_center(self) -> None:
        if self._running(self.center_process):
            return
        if not self.env_path.is_file() and (self.root_dir / "portable" / "portable_center.py").is_file():
            messagebox.showwarning("还没有中心配置", f"请先把 .env.example 复制为 .env 并填写数据库配置：\n{self.env_path}")
            return
        try:
            self.center_process = self._launch(self._command(), self.center_log_path, "center")
            self.center_status.configure(text="启动中")
        except OSError as exc:
            messagebox.showerror("中心启动失败", str(exc))

    def stop_center(self) -> None:
        self.center_process = self._stop(self.center_process)

    def start_tunnel(self) -> None:
        if self._running(self.tunnel_process):
            return
        if not self._running(self.center_process):
            if messagebox.askyesno("中心尚未启动", "中心服务目前没有运行，要先启动中心再启动 Tunnel 吗？"):
                self.start_center()
            else:
                return
        mode = self.mode.get()
        name = self.name.get().strip()
        if mode == "named" and not TUNNEL_NAME_RE.fullmatch(name):
            messagebox.showwarning("Tunnel 名称无效", "命名 Tunnel 只允许字母、数字、点、下划线和短横线。")
            return
        executable = self.executable.get().strip() or "cloudflared"
        if not Path(executable).is_file() and not shutil.which(executable):
            messagebox.showerror("找不到 cloudflared", "请把 cloudflared 加入 PATH，或点击“选择文件”指定它的位置。")
            return
        command = [executable, "tunnel", "--no-autoupdate"]
        command += (["run", name] if mode == "named" else ["--url", f"http://127.0.0.1:{self._port()}"])
        self.save_settings(silent=True)
        try:
            self.tunnel_process = self._launch(command, self.tunnel_log_path, "cloudflare tunnel")
        except OSError as exc:
            messagebox.showerror("Tunnel 启动失败", str(exc))

    def stop_tunnel(self) -> None:
        self.tunnel_process = self._stop(self.tunnel_process)

    def start_all(self) -> None:
        if not self._running(self.center_process):
            self.start_center()
        self.after(1000, self.start_tunnel)

    @staticmethod
    def _running(process: subprocess.Popen[Any] | None) -> bool:
        return process is not None and process.poll() is None

    @staticmethod
    def _stop(process: subprocess.Popen[Any] | None) -> None:
        if process is None or process.poll() is not None:
            return None
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return None

    def save_settings(self, silent: bool = False) -> None:
        if self.mode.get() not in {"quick", "named"}:
            self.mode.set("quick")
        if self.mode.get() == "named" and not TUNNEL_NAME_RE.fullmatch(self.name.get().strip()):
            if not silent:
                messagebox.showwarning("Tunnel 名称无效", "命名 Tunnel 只允许字母、数字、点、下划线和短横线。")
            return
        save_json(self.settings_path, {"mode": self.mode.get(), "tunnel_name": self.name.get().strip(), "cloudflared": self.executable.get().strip() or "cloudflared"})
        if not silent:
            self.tunnel_detail.configure(text=f"设置已保存：{self.settings_path}")

    def choose_cloudflared(self) -> None:
        path = filedialog.askopenfilename(title="选择 cloudflared 可执行文件")
        if path:
            self.executable.set(path)
            self.save_settings(silent=True)

    def open_web(self) -> None:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{self._port()}")

    def _tail(self, path: Path, max_bytes: int = 18000) -> str:
        try:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - max_bytes))
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _refresh(self) -> None:
        if self._closing:
            return
        center_running = self._running(self.center_process)
        tunnel_running = self._running(self.tunnel_process)
        self.center_status.configure(text="运行中" if center_running else "未运行", foreground="#16805b" if center_running else "#ba7b13")
        self.center_start.configure(state="disabled" if center_running else "normal")
        self.center_stop.configure(state="normal" if center_running else "disabled")
        self.center_detail.configure(text=f"本地地址：http://127.0.0.1:{self._port()}  ·  日志：{self.center_log_path}")
        self.tunnel_status.configure(text="运行中" if tunnel_running else "未运行", foreground="#16805b" if tunnel_running else "#ba7b13")
        self.tunnel_start.configure(state="disabled" if tunnel_running else "normal")
        self.tunnel_stop.configure(state="normal" if tunnel_running else "disabled")
        log = self._tail(self.center_log_path) + ("\n" + self._tail(self.tunnel_log_path) if self.tunnel_log_path.is_file() else "")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", log[-18000:] or "暂无日志")
        self.log_text.configure(state="disabled")
        tunnel_log = self._tail(self.tunnel_log_path)
        urls = PUBLIC_URL_RE.findall(tunnel_log)
        self.public_url.configure(text=f"公网地址：{urls[-1] if urls else '等待 Tunnel 分配地址'}")
        self.tunnel_detail.configure(text=f"本地转发：http://127.0.0.1:{self._port()}  ·  日志：{self.tunnel_log_path}")
        self.after(1000, self._refresh)

    def _minimize(self) -> None:
        self.iconify()

    def exit_console(self) -> None:
        if self._running(self.center_process) or self._running(self.tunnel_process):
            if not messagebox.askyesno("服务仍在运行", "退出控制台不会停止中心服务和 Tunnel，确定退出控制台吗？"):
                return
        self._closing = True
        self.destroy()


def main() -> int:
    CenterConsole().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

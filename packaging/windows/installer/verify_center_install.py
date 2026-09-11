#!/usr/bin/env python3
"""Smoke-test an installed Practical Tools Online center.

This intentionally uses only the standard library so it can run with the
bundled Python runtime on Windows. It checks public pages first, then checks
authenticated business APIs when credentials are supplied.
"""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass


@dataclass
class Check:
    name: str
    path: str
    status: int | None
    ok: bool
    detail: str = ""


class Client:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, payload: object | None = None):
        data = None
        headers = {"Accept": "application/json, text/html"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(512 * 1024)
                content_type = response.headers.get_content_type()
                text = raw.decode("utf-8", "replace")
                try:
                    body = json.loads(text) if "json" in content_type or text[:1] in "[{" else text
                except json.JSONDecodeError:
                    body = text
                return response.status, body
        except urllib.error.HTTPError as exc:
            raw = exc.read(64 * 1024).decode("utf-8", "replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            return exc.code, body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return None, str(exc)


def run_check(client: Client, name: str, path: str, expected=(200,)) -> Check:
    status, body = client.call("GET", path)
    ok = status in expected
    detail = "" if ok else str(body)[:240]
    return Check(name, path, status, ok, detail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18180")
    parser.add_argument("--username", help="登录验收账号；不填写则跳过受保护接口")
    parser.add_argument("--password-stdin", action="store_true", help="从标准输入读取密码，避免密码进入命令历史")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出检查结果")
    args = parser.parse_args()

    client = Client(args.base_url)
    checks: list[Check] = []
    public = [
        ("首页", "/"),
        ("页面收集页面", "/screenshot/"),
        ("首页分析页面", "/analysis/"),
        ("健康检查", "/api/health"),
        ("认证初始化状态", "/api/auth/setup-status"),
        ("认证状态", "/api/auth/status"),
    ]
    for name, path in public:
        checks.append(run_check(client, name, path))

    logged_in = False
    is_admin = False
    if args.username:
        password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass.getpass("登录验收密码：")
        status, body = client.call("POST", "/api/auth/login", {"username": args.username, "password": password})
        login_ok = status == 200 and isinstance(body, dict) and bool(body.get("id"))
        checks.append(Check("管理员/业务账号登录", "/api/auth/login", status, login_ok, "" if login_ok else str(body)[:240]))
        logged_in = login_ok
        is_admin = login_ok and body.get("role") == "admin"

    if logged_in:
        protected = [
            ("当前用户", "/api/auth/me"),
            ("截图列表", "/api/screenshot/list"),
            ("截图版本", "/api/screenshot/revision"),
            ("截图任务状态", "/api/screenshot/capture/status"),
            ("截图进度", "/api/screenshot/progress"),
            ("调度状态", "/api/screenshot/scheduler/status"),
            ("淘宝登录状态", "/api/screenshot/login/status"),
            ("分析状态", "/api/analysis/status"),
            ("分析预览", "/api/analysis/preview"),
            ("分析文件列表", "/api/analysis/files"),
            ("分析指标", "/api/analysis/metrics"),
            ("分析总览面板", "/api/analysis/panel/overview"),
            ("分析新老客面板", "/api/analysis/panel/newold"),
            ("分析异常面板", "/api/analysis/panel/anomaly"),
            ("分析区块面板", "/api/analysis/panel/blocks"),
            ("分析日历面板", "/api/analysis/panel/calendar"),
            ("分析总览汇总", "/api/analysis/panel/overall_all"),
            ("头像状态", "/api/avatar/status"),
        ]
        for name, path in protected:
            checks.append(run_check(client, name, path))

        if is_admin:
            admin = [
                ("管理员中心状态", "/api/admin/status"),
                ("管理员账号列表", "/api/admin/accounts"),
                ("共享用户列表", "/api/admin/users"),
                ("COS 配置状态", "/api/admin/settings/storage"),
                ("在线更新状态", "/api/admin/updates/status"),
            ]
            for name, path in admin:
                checks.append(run_check(client, name, path))

    result = {
        "ok": all(item.ok for item in checks),
        "base_url": args.base_url,
        "authenticated_checks": logged_in,
        "admin_checks": is_admin,
        "checks": [asdict(item) for item in checks],
        "failed": [asdict(item) for item in checks if not item.ok],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            state = "PASS" if item.ok else "FAIL"
            suffix = f" - {item.detail}" if item.detail else ""
            print(f"[{state}] {item.name}: {item.status}{suffix}")
        print(f"\n总计：{len(checks)} 项，失败 {len(result['failed'])} 项")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

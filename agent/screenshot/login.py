#!/usr/bin/env python3
"""淘宝登录：进程内打开浏览器供扫码，登录完成后由程序保存状态。

进程内模式（打包后也生效）：
    run_login(refresh=False)  打开浏览器并等待 complete_login()
    complete_login()          用户登录完成后调用，保存状态

CLI 模式（开发用，直接 python login.py）仍可用：终端输入 done 触发保存。
"""
import json
import threading

from screenshot import paths

PROJECT_DIR = paths.RESOURCE_DIR

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

_login_done = threading.Event()
_login_state = {"running": False}


def reset_login():
    _login_done.clear()


def complete_login():
    """用户已完成登录，触发状态保存。"""
    _login_done.set()


def login_status() -> dict:
    return {"running": _login_state["running"]}


def run_login(refresh: bool = False):
    """打开浏览器登录淘宝，等待 complete_login() 后保存状态。"""
    from patchright.sync_api import sync_playwright

    # Resolve the writable state path at call time. Agent --data-dir updates
    # paths.BROWSER_STATE_DIR after this module is imported.
    state_dir = paths.BROWSER_STATE_DIR
    state_file = state_dir / "state.json"
    _login_state["running"] = True
    reset_login()
    state_dir.mkdir(parents=True, exist_ok=True)

    storage_state = None
    if refresh and state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                storage_state = json.load(f)
        except Exception:
            storage_state = None

    try:
        with sync_playwright() as p:
            # Use the Patchright-managed browser so the packaged macOS Agent
            # works even when Google Chrome is not installed or its channel
            # cannot be resolved. This also honors PLAYWRIGHT_BROWSERS_PATH.
            browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                viewport={"width": 574, "height": 1024},
                user_agent=MOBILE_UA,
                storage_state=storage_state,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                is_mobile=True,
                has_touch=True,
                device_scale_factor=1,
            )
            page = context.new_page()
            try:
                page.goto("https://login.taobao.com/", timeout=30000)
            except Exception:
                page.goto("https://www.taobao.com/", timeout=30000)

            # 等待用户在浏览器完成登录后由 UI 调 complete_login()
            _login_done.wait()

            state = context.storage_state()
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            context.close()
            browser.close()
    finally:
        _login_state["running"] = False
        reset_login()


def main():
    """CLI 模式：开发时直接 python login.py 可用，终端输入 done 保存。"""
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="淘宝登录助手")
    parser.add_argument("--refresh", action="store_true", help="刷新已有登录态")
    args = parser.parse_args()

    try:
        import patchright  # noqa: F401
    except ImportError:
        print("❌ 未安装 patchright")
        sys.exit(1)

    print("即将打开浏览器，请在浏览器中完成登录。")
    print("登录完成后回到本终端输入 done 回车。")

    def _wait_done():
        while True:
            try:
                u = input("👉 登录完成后输入 'done' 并回车: ").strip().lower()
                if u == "done":
                    complete_login()
                    return
            except EOFError:
                return

    threading.Thread(target=_wait_done, daemon=True).start()
    run_login(refresh=args.refresh)
    print("✅ 登录态已保存到:", paths.BROWSER_STATE_DIR / "state.json")


if __name__ == "__main__":
    main()

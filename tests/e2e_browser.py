from __future__ import annotations

import json
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # The release test environment bundles Patchright instead.
    from patchright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "tests/artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
BASE_URL = "http://127.0.0.1:18181"

console_errors: list[str] = []
page_errors: list[str] = []
http_failures: list[dict] = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def record_response(response):
        if response.status >= 400 and response.url.startswith(BASE_URL):
            http_failures.append({"status": response.status, "url": response.url})

    page.on("response", record_response)
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")

    assert page.locator("#auth-view").is_visible()
    assert page.locator(".app").evaluate("el => getComputedStyle(el).visibility") == "hidden"
    page.fill("#auth-username", "e2e-user")
    page.fill("#auth-password", "wrong-password")
    page.click("#auth-submit")
    page.wait_for_selector("#auth-error:not(:empty)")
    assert "用户名或密码错误" in page.locator("#auth-error").inner_text()

    page.fill("#auth-password", "e2e-correct-password")
    page.click("#auth-submit")
    page.wait_for_function("document.querySelector('#auth-view').hidden === true")
    page.wait_for_selector(".dicon[data-id='screenshot']")
    assert page.locator(".dicon[data-id='screenshot']").count() == 1
    assert page.locator(".dicon[data-id='analysis']").count() == 1

    page.click(".dicon[data-id='screenshot']")
    page.wait_for_function("document.querySelector('#frame-screenshot').dataset.loaded === '1'")
    screenshot_frame = page.frame_locator("#frame-screenshot")
    screenshot_frame.locator("body").wait_for()
    assert "页面收集" in screenshot_frame.locator("body").inner_text()
    assert screenshot_frame.get_by_role("button", name="触发截图").count() == 1

    page.click("#tab-home")
    page.click(".dicon[data-id='analysis']")
    page.wait_for_function("document.querySelector('#frame-analysis').dataset.loaded === '1'")
    analysis_frame = page.frame_locator("#frame-analysis")
    analysis_frame.locator("body").wait_for()
    assert "首页数据分析" in analysis_frame.locator("body").inner_text()
    assert analysis_frame.locator("#file-input").count() == 1
    page.wait_for_timeout(1200)

    page.screenshot(path=str(ARTIFACTS / "online-shell-analysis.png"), full_page=True)
    browser.close()

known_http_failures = [item for item in http_failures if item["status"] == 404 and "/api/auth/avatar" in item["url"]]
unknown_http_failures = [item for item in http_failures if item not in known_http_failures and not (item["status"] == 401 and "/api/auth/login" in item["url"])]
known_console = [
    message
    for message in console_errors
    if "/api/auth/avatar" in message
    or "401 (Unauthorized)" in message
    or (
        "404 (Not Found)" in message
        and any(item["status"] == 404 and "/api/auth/avatar" in item["url"] for item in known_http_failures)
    )
]
unknown_console = [message for message in console_errors if message not in known_console]

report = {
    "login_gate": "pass",
    "login_success": "pass",
    "screenshot_iframe": "pass",
    "analysis_iframe": "pass",
    "page_errors": page_errors,
    "console_errors": console_errors,
    "known_http_failures": known_http_failures,
    "unknown_http_failures": unknown_http_failures,
}
(ARTIFACTS / "browser-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

assert not page_errors, page_errors
assert not unknown_console, unknown_console
assert not unknown_http_failures, unknown_http_failures
print(json.dumps(report, ensure_ascii=False))

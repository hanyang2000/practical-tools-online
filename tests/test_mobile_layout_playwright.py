"""Small-screen layout smoke test for the static pages.

The real API is not required here: the assertion is about CSS layout bounds,
not business data.  The server wrapper supplies the repository as a static
origin so relative assets still resolve exactly as they do in a WebView.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # Optional standalone visual smoke dependency.
    sync_playwright = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
PAGES = ("frontend/shell/", "frontend/screenshot/", "frontend/analysis/")
VIEWPORTS = ((360, 800), (412, 915), (800, 480))


def main() -> int:
    if sync_playwright is None:
        print("PLAYWRIGHT_NOT_INSTALLED")
        return 2
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18765"
    output_dir = Path(os.environ.get("MOBILE_SCREENSHOT_DIR", "/private/tmp"))
    output_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        failures: list[str] = []
        for page_path in PAGES:
            for width, height in VIEWPORTS:
                page.set_viewport_size({"width": width, "height": height})
                page.goto(f"{base_url}/{page_path}", wait_until="domcontentloaded")
                page.wait_for_timeout(450)
                metrics = page.evaluate(
                    """() => ({
                        innerWidth: window.innerWidth,
                        scrollWidth: document.documentElement.scrollWidth,
                        bodyScrollWidth: document.body ? document.body.scrollWidth : 0,
                        viewport: document.querySelector('meta[name=viewport]')?.content || ''
                    })"""
                )
                if metrics["scrollWidth"] > metrics["innerWidth"] + 1 or metrics["bodyScrollWidth"] > metrics["innerWidth"] + 1:
                    failures.append(f"{page_path} @ {width}x{height}: {metrics}")
                if "viewport-fit=cover" not in metrics["viewport"]:
                    failures.append(f"{page_path}: missing viewport-fit=cover")
                page.screenshot(path=str(output_dir / f"{page_path.split('/')[1]}-{width}x{height}.png"), full_page=True)
        browser.close()
    if failures:
        print("MOBILE_LAYOUT_FAILURE")
        print("\n".join(failures))
        return 1
    print(f"MOBILE_LAYOUT_OK pages={len(PAGES)} viewports={len(VIEWPORTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

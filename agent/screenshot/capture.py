#!/usr/bin/env python3
"""
淘宝店铺定时截图工具 - 截图脚本
==================================
加载已保存的淘宝登录态，对配置中列出的所有店铺页面进行截图。
截图按 店铺名/日期/时间.png 的目录结构保存。

用法:
    python capture.py                    # 对所有店铺截图
    python capture.py --shop "店铺A"      # 只对指定店铺截图
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

# 路径：兼容打包（frozen），数据目录可写、资源目录随包分发
from screenshot import paths  # noqa: E402
PROJECT_DIR = paths.RESOURCE_DIR
CONFIG_FILE = paths.CONFIG_FILE
STATE_FILE = paths.BROWSER_STATE_DIR / "state.json"
SCREENSHOTS_DIR = paths.SCREENSHOTS_DIR
LOGS_DIR = paths.LOGS_DIR
OCR_TOOL = paths.OCR_TOOL
PROGRESS_FILE = paths.PROGRESS_FILE
_RAPID_OCR = None
_RAPID_OCR_UNAVAILABLE = False


def refresh_paths() -> None:
    """Refresh legacy module aliases after Agent applies ``--data-dir``."""
    global PROJECT_DIR, CONFIG_FILE, STATE_FILE, SCREENSHOTS_DIR, LOGS_DIR, OCR_TOOL, PROGRESS_FILE
    PROJECT_DIR = paths.RESOURCE_DIR
    CONFIG_FILE = paths.CONFIG_FILE
    STATE_FILE = paths.BROWSER_STATE_DIR / "state.json"
    SCREENSHOTS_DIR = paths.SCREENSHOTS_DIR
    LOGS_DIR = paths.LOGS_DIR
    OCR_TOOL = paths.OCR_TOOL
    PROGRESS_FILE = paths.PROGRESS_FILE


def _rapid_ocr_engine():
    """按需初始化 RapidOCR，避免 Agent 启动时就加载约 15MB 模型。"""
    global _RAPID_OCR, _RAPID_OCR_UNAVAILABLE
    if _RAPID_OCR is not None or _RAPID_OCR_UNAVAILABLE:
        return _RAPID_OCR
    try:
        from rapidocr_onnxruntime import RapidOCR
        _RAPID_OCR = RapidOCR()
    except Exception:
        # 部分旧安装包没有模型，仍允许旧 OCR 工具继续尝试。
        _RAPID_OCR_UNAVAILABLE = True
    return _RAPID_OCR


def _ocr_text_matches(value: str, target: str) -> bool:
    """兼容 OCR 结果中的空格、标点和轻微排版差异。"""
    import re
    compact = re.sub(r"[\s·•|丨，。,.、:：!！?？'\"“”‘’]", "", str(value or ""))
    wanted = re.sub(r"[\s·•|丨，。,.、:：!！?？'\"“”‘’]", "", str(target or ""))
    return bool(wanted) and wanted in compact

# 暂停 / 停止控制（进程内采集用）
import threading as _threading
_pause_event = _threading.Event()
_stop_event = _threading.Event()


def set_paused(paused: bool):
    if paused:
        _pause_event.set()
    else:
        _pause_event.clear()


def set_stop(stopped: bool):
    if stopped:
        _stop_event.set()
    else:
        _stop_event.clear()


def _write_progress(current: int, total: int, shop_name: str, stage: str = ""):
    """把当前截图进度写到状态文件，供前端进度窗口读取。

    使用临时文件替换，避免 Agent 监控线程或中心同步线程读到半截 JSON。
    ``stage`` 是兼容字段，旧版页面会忽略它，新版页面可显示更准确的阶段。
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"current": current, "total": total, "shop": shop_name,
                   "stage": stage, "updated_at": datetime.now().isoformat()}
        temporary = PROGRESS_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(PROGRESS_FILE)
    except Exception:
        pass


def initialize_progress(total: int = 0, stage: str = "等待 Agent 开始") -> None:
    """初始化一次截图任务，不能沿用上一轮任务的进度。"""
    _write_progress(0, max(0, int(total)), "", stage)

# 移动端 User-Agent（天猫移动版页面）
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

# 反检测脚本 — 在页面加载前注入
STEALTH_SCRIPT = """
// 1. 隐藏 webdriver 标记
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// 2. 伪造 chrome.runtime
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};

// 3. 伪造 plugins — 真实 Safari/Chrome 至少有数个 plugin
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        plugins.item = (i) => plugins[i] || null;
        plugins.namedItem = (n) => plugins.find(p => p.name === n) || null;
        plugins.refresh = () => {};
        Object.setPrototypeOf(plugins, PluginArray.prototype);
        return plugins;
    }
});

// 4. 伪造 mimeTypes
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const types = [
            { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
            { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        ];
        types.item = (i) => types[i] || null;
        types.namedItem = (n) => types.find(t => t.type === n) || null;
        Object.setPrototypeOf(types, MimeTypeArray.prototype);
        return types;
    }
});

// 5. 语言设置
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'language', { get: () => 'zh-CN' });

// 6. platform
Object.defineProperty(navigator, 'platform', { get: () => 'iPhone' });

// 7. hardwareConcurrency
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 6 });

// 8. deviceMemory
Object.defineProperty(navigator, 'deviceMemory', { get: () => 4 });

// 9. 移除 PhantomJS 等痕迹
delete window.callPhantom;
delete window._phantom;
delete window.__phantomas;

// 10. 覆盖权限查询
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);
"""


def load_config():
    """加载 YAML 配置文件"""
    if not CONFIG_FILE.exists():
        print(f"❌ 配置文件不存在: {CONFIG_FILE}")
        raise RuntimeError(f"配置文件不存在: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_browser_state():
    """加载已保存的浏览器登录态"""
    if not STATE_FILE.exists():
        print("❌ 未找到登录态文件！")
        print(f"   请先运行登录脚本: python {PROJECT_DIR / 'login.py'}")
        raise RuntimeError("未找到淘宝登录态文件，请先完成淘宝登录")

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"❌ 登录态文件损坏: {e}")
        print(f"   请重新运行登录脚本: python {PROJECT_DIR / 'login.py'}")
        raise RuntimeError(f"登录态文件损坏: {e}") from e


def is_logged_out(page) -> bool:
    """检测当前页面是否被重定向到登录页"""
    url = page.url.lower()
    login_indicators = [
        "login.taobao.com",
        "login.tmall.com",
        "login.taobao",
        "/login",
        "passport.taobao",
        "passport.tmall",
        "oauth2",
    ]
    return any(indicator in url for indicator in login_indicators)


def is_captcha_page(page) -> bool:
    """检测是否触发了验证码/滑块认证（URL + 标题 + 文字 + DOM + iframe）"""
    # 1. URL 检测
    url = page.url.lower()
    if any(k in url for k in ["verify", "captcha", "check", "security",
                               "robot", "punish", "ncaptcha", "newlogin",
                               "identity", "validate"]):
        return True

    # 2. 标题检测
    try:
        title = page.title().lower()
        if any(t in title for t in ["验证", "captcha", "verify", "安全验证", "滑块验证"]):
            return True
    except Exception:
        pass

    # 3. 页面文字检测 — 滑块验证的特征文字
    try:
        body_text = page.inner_text("body")
        captcha_phrases = [
            "请按住滑块", "拖动滑块", "向右滑动", "验证通过",
            "请完成下方验证", "安全验证", "滑块验证",
            "请按住左边滑块", "拖动左边滑块", "请向右拖动",
            "验证码", "滑动验证", "请完成安全验证",
            "drag the slider", "slide to verify",
        ]
        if any(p in body_text for p in captcha_phrases):
            return True
    except Exception:
        pass

    # 4. DOM 选择器检测（主页面 + 所有 iframe）
    #    注意：淘宝会在所有店铺页加载 noCaptcha 风控 JS，可能注入“隐藏”的容器。
    #    所以这里必须判断元素是否“可见”，而不是仅判断“存在”，
    #    否则会把正常店铺页误判为验证码（这也是之前 5 家店“稳定触发”的主因）。
    captcha_selectors = [
        "#nc_1_n1z", ".nc_wrapper", "#nocaptcha", ".nocaptcha",
        "#baxia-dialog", ".baxia-dialog", ".sliderCaptcha",
        ".captcha-container", "[id*='nc_1_']", "#risk-dialog",
        ".slider-captcha", ".slide-verify", ".verify-captcha",
        ".secsdk-captcha", "#aliyunCaptcha",
    ]

    frames_to_check = [page]
    try:
        frames_to_check.extend(page.frames)
    except Exception:
        pass

    for frame in frames_to_check:
        try:
            for selector in captcha_selectors:
                locator = frame.locator(selector)
                if locator.count() > 0:
                    # 只有“可见”的验证码元素才判定为触发
                    try:
                        if locator.first.is_visible():
                            return True
                    except Exception:
                        pass
        except Exception:
            continue

    return False


def dismiss_popups(page):
    """
    关闭页面上可能遮挡内容的弹窗/浮层。
    策略：按 Escape → 点 CSS 关闭按钮 → 点文字按钮（"下次再说"等）。
    重复多轮直到没有新的弹窗被关掉。
    """
    # 常见的弹窗关闭/拒绝文字
    dismiss_texts = [
        "下次再说", "我知道了", "不用了", "关闭", "取消",
        "暂不", "以后再说", "跳过", "拒绝", "不要",
        "稍后再说", "残忍拒绝", "先不看", "再说", "不了",
    ]

    for _round in range(3):  # 最多 3 轮清理
        closed_any = False

        # 1. Escape 键
        try:
            page.keyboard.press("Escape")
            time.sleep(0.4)
        except Exception:
            pass

        # 2. CSS 选择器的关闭按钮
        css_selectors = [
            ".J_Close", ".close", ".close-btn", ".closeBtn",
            ".dialog-close", ".popup-close", ".modal-close",
            ".tms-popup-close", ".sk_close",
            "button[aria-label='关闭']", "[data-role='close']",
            ".mask",  # 点击遮罩有时也能关弹窗
        ]
        for selector in css_selectors:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    el.click(timeout=1000)
                    closed_any = True
                    time.sleep(0.4)
            except Exception:
                pass

        # 3. 文字匹配 — 找包含"下次再说"等文字的可点击元素
        for text in dismiss_texts:
            try:
                # getByText 会找包含该文字的可见元素
                locator = page.get_by_text(text, exact=False)
                count = locator.count()
                for i in range(min(count, 3)):
                    el = locator.nth(i)
                    if el.is_visible():
                        try:
                            el.click(timeout=1500)
                            closed_any = True
                            time.sleep(0.5)
                        except Exception:
                            pass
            except Exception:
                pass

        # 4. 找包含 ✕ × 的按钮
        for symbol in ["×", "✕", "✖", "x", "X"]:
            try:
                el = page.get_by_text(symbol, exact=True).first
                if el.is_visible():
                    try:
                        el.click(timeout=1000)
                        closed_any = True
                        time.sleep(0.4)
                    except Exception:
                        pass
            except Exception:
                pass

        if not closed_any:
            break
        time.sleep(0.5)


def ensure_dir(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)


def save_captcha_screenshot(page, shop_name: str) -> Optional[str]:
    """触发验证时保存当前页面截图，便于排查到底是不是真的滑块/验证码。
    截图存到 screenshots/_captcha/店铺名/日期/时间.png，不会和正常截图混淆。
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H-%M-%S")
    out_dir = SCREENSHOTS_DIR / "_captcha" / shop_name / date_str
    ensure_dir(out_dir)
    out_file = out_dir / f"{time_str}.png"
    try:
        # 滑块/验证码通常是居中浮层，视口截图即可完整捕获
        page.screenshot(path=str(out_file), full_page=False)
        return str(out_file)
    except Exception:
        return None


def handle_captcha(page, shop_name: str, shop_url: str, results: dict, stage: str):
    """统一的验证码处理：截图留档 + 记录 + 关闭页面 + 冷却。"""
    shot = save_captcha_screenshot(page, shop_name)
    print(f"   🤖 触发了真人验证（{stage}）！已截图留档，跳过此店铺")
    results["captcha"].append({
        "name": shop_name, "url": shop_url, "file": shot, "stage": stage,
    })
    try:
        page.close()
    except Exception:
        pass
    cooldown = random.uniform(45, 75)
    print(f"   ⏸  冷却 {cooldown:.0f} 秒后再继续...")
    time.sleep(cooldown)


def sanitize_name(name: str) -> str:
    """清理店铺名称，替换文件系统不安全字符"""
    unsafe_chars = '/\\:*?"<>|'
    for c in unsafe_chars:
        name = name.replace(c, "_")
    return name.strip()


def derive_name_from_url(url: str) -> str:
    """从链接推断一个店铺名（用于截图保存目录）。

    例：https://olay.m.tmall.com/ -> "olay"
        https://loreal.m.tmall.com/shop/view_shop.htm?shop_id=123 -> "loreal"
    """
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    parts = host.split(".")
    # 取最左段作为品牌名；若是 www/m/mobile/shop 这类无意义前缀则取下一段
    name = parts[0] if parts else "shop"
    for skip in ("www", "m", "mobile", "shop", "tmall"):
        if name == skip and len(parts) > 1:
            name = parts[1]
            break
    return sanitize_name(name) or "shop"


def _page_height(page) -> int:
    """获取页面真实滚动高度（body 与 documentElement 取较大值）。"""
    try:
        return page.evaluate(
            "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        )
    except Exception:
        return 10000


def _wait_images_loaded(page, timeout_ms: int):
    """等待当前 DOM 里所有图片都加载完成（complete）。"""
    try:
        page.wait_for_function(
            "Array.from(document.images).every(img => img.complete)",
            timeout=timeout_ms,
        )
    except Exception:
        pass


def find_text_y(page, text: str) -> Optional[int]:
    """在页面 DOM 中查找包含指定文字的最具体可见元素，返回其绝对 Y 坐标（页面顶部算起）。

    找不到返回 None。用于「截图到某文案为止」的裁剪：例如出现「逛逛更多宝贝」
    标记时，从该处往下全部裁掉，只保留店铺自营内容。
    """
    try:
        y = page.evaluate(
            """
            (text) => {
                let best = null;
                let bestArea = Infinity;
                for (const el of document.querySelectorAll('body *')) {
                    let t = '';
                    try { t = el.innerText || ''; } catch (e) { continue; }
                    if (!t.includes(text)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    const area = r.width * r.height;
                    if (area < bestArea) { bestArea = area; best = el; }
                }
                if (!best) return null;
                return Math.round(best.getBoundingClientRect().top + window.scrollY);
            }
            """,
            text,
        )
        return int(y) if y is not None else None
    except Exception:
        return None


def crop_image(png_path: str, crop_y: int):
    """把 PNG 图片从 crop_y 往下裁掉（只保留 [0, crop_y)）。"""
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        im = Image.open(png_path)
        w, h = im.size
        crop_y = max(0, min(int(crop_y), h))
        if crop_y >= h:
            return
        im.crop((0, 0, w, crop_y)).save(png_path)
    except Exception:
        pass


def ocr_find_text_y(png_path: str, text: str, chunk: int = 1400, start_fraction: float = 0.5) -> Optional[int]:
    """用 RapidOCR 查找图片文字，返回其绝对 Y 坐标。

    「逛逛更多宝贝」这类标题常被渲染成图片，DOM 里搜不到文字，只能靠 OCR。
    先从页面下半部分开始扫描；如果没有命中，再自动回扫上半部分。每个 OCR
    分块带有重叠区域，避免标记刚好落在两个分块边界时被切断。只要某一块命中，
    立即返回坐标，调用方会把该标记以下的内容全部裁掉。
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(png_path).convert("RGB")
    except Exception:
        return None

    w, h = im.size
    start = max(0, min(int(h * start_fraction), h - 1))
    overlap = min(max(chunk // 3, 200), 500)
    step = max(1, chunk - overlap)

    def scan_region(region_start: int, region_end: int) -> Optional[int]:
        top = region_start
        while top < region_end:
            bottom = min(top + chunk, h)
            crop = im.crop((0, top, w, bottom))
            tmp_path = tempfile.mktemp(suffix=".png")
            try:
                crop.save(tmp_path)
                engine = _rapid_ocr_engine()
                if engine is not None:
                    results, _ = engine(tmp_path)
                    for item in results or []:
                        if len(item) < 2 or not _ocr_text_matches(item[1], text):
                            continue
                        points = item[0] if item[0] else []
                        y_values = [float(point[1]) for point in points if len(point) >= 2]
                        if y_values:
                            return int(top + min(y_values))
                    top += step
                    continue

                # 兼容尚未带 RapidOCR 模型的旧安装包。
                if not OCR_TOOL.exists():
                    top += step
                    continue
                legacy = subprocess.run(
                    [str(OCR_TOOL), tmp_path, text],
                    capture_output=True, text=True, timeout=120,
                )
                if legacy.returncode != 0:
                    top += step
                    continue
                out = legacy.stdout
            except Exception:
                out = ""
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) < 3 or not parts[0]:
                    continue
                try:
                    y_frac = float(parts[2])
                except ValueError:
                    continue
                return int(top + y_frac * (bottom - top))
            top += step
        return None

    # 优先扫下半部分；没有命中时动态回扫前面的区域，兼容标记出现在上半页的店铺。
    found = scan_region(start, h)
    if found is not None:
        return found
    if start > 0:
        return scan_region(0, start)
    return None


def expand_viewport_to_full(page, viewport_width: int, max_height: Optional[int]) -> int:
    """
    把视口撑到整页高度，让所有懒加载内容（图片等）进入可视区并加载。

    相比「逐步滚动」有两个好处：
      1. 不产生大量滚动事件，避免触发高价值品牌店的风控滑块；
      2. 所有懒加载图片一次性进入可视区，等它们加载完再截图，不会拍到空白。

    关键点：不能只按「撑高前」测到的页面高度一次撑到位 —— 那是个偏小的旧值，
    图片加载后页面会继续变高，导致截图底部被裁掉（截不全）。所以这里反复
    「测量 → 撑高 → 等图片 → 再测量」，直到高度收敛稳定。

    返回: 最终截图高度 = min(页面真实总高度, max_height)。
    """
    prev_h = 0

    for _ in range(10):
        # 视口尚小于内容时，scrollHeight 反映的是真实内容高度
        total_h = _page_height(page)
        target_h = min(total_h, max_height) if max_height else total_h
        target_h = max(target_h, 1)

        # 高度不再增长（±2px 视为稳定）即收敛，停止撑高
        if prev_h > 0 and abs(total_h - prev_h) <= 2:
            break

        try:
            page.set_viewport_size({"width": viewport_width, "height": target_h})
        except Exception:
            pass
        prev_h = total_h

        # 撑高视口后，新进入可视区的懒加载图片会开始加载，等它们完成
        _wait_images_loaded(page, 8000)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        time.sleep(0.8)

    # 最终把视口固定到实际截图高度
    final_h = _page_height(page)
    if max_height:
        final_h = min(final_h, max_height)
    final_h = max(final_h, 1)
    try:
        page.set_viewport_size({"width": viewport_width, "height": final_h})
    except Exception:
        pass
    _wait_images_loaded(page, 8000)
    time.sleep(0.5)

    return final_h


def wait_for_content(page, timeout_ms: int):
    """
    等待页面主要内容加载完成。
    尝试等待常见的商品/内容容器出现。
    """
    content_selectors = [
        # 天猫/淘宝常见内容容器
        ".main-content",
        ".content",
        ".shop-main",
        ".J_TBox",
        # 商品列表
        ".item",
        ".product",
        ".goods",
        "[class*='item']",
        "[class*='goods']",
        # 页面框架
        ".page-content",
        "#page",
        ".skin-box",
        # 通用：有足够多的图片说明页面内容已加载
        "img",
    ]

    for selector in content_selectors:
        try:
            page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
            return True
        except Exception:
            continue

    # 如果所有选择器都没等到，至少确保 body 存在
    try:
        page.wait_for_selector("body", timeout=5000)
        return True
    except Exception:
        return False


def capture_shops(
    config: dict,
    shop_filter: Optional[str] = None,
    shops: Optional[list] = None,
):
    """对所有配置的店铺进行截图。

    shops 参数可覆盖店铺列表，用于对触发验证码的店铺自动重试。
    """
    from patchright.sync_api import sync_playwright

    if shops is None:
        shops = config.get("shops", [])
        if not shops:
            print("❌ 配置文件中没有店铺，请在 config.yaml 的 shops 列表中添加")
            _write_progress(0, 0, "", "店铺配置为空")
            raise RuntimeError("配置文件中没有可截图的店铺")

        # 过滤店铺
        if shop_filter:
            shops = [s for s in shops if s.get("name") == shop_filter]
            if not shops:
                print(f"❌ 未找到名为 '{shop_filter}' 的店铺")
                _write_progress(0, 0, "", f"未找到店铺：{shop_filter}")
                raise RuntimeError(f"未找到名为 '{shop_filter}' 的店铺")

    viewport = config.get("viewport", {"width": 1920, "height": 1080})
    page_timeout = config.get("page_timeout", 30) * 1000
    wait_min = config.get("extra_wait_min", 8)
    wait_max = config.get("extra_wait_max", 20)
    max_height = config.get("max_screenshot_height", 14500)
    crop_text = (config.get("crop_below_text") or "").strip()
    ocr_start_fraction = float(config.get("ocr_start_fraction", 0.5))
    try:
        crop_margin_top = max(0, int(config.get("crop_margin_top", 40)))
    except (TypeError, ValueError):
        crop_margin_top = 40

    print()
    print("=" * 60)
    print(f"  淘宝店铺截图 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(f"  共 {len(shops)} 个店铺需要截图")
    print(f"  视口: {viewport['width']}x{viewport['height']}")
    print()

    _write_progress(0, len(shops), "", "准备启动浏览器")

    storage_state = load_browser_state()

    results = {"success": [], "captcha": [], "login_expired": [], "error": []}
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H-%M-%S")

    with sync_playwright() as p:
        # patchright 自带的 Chromium（CDP 层已打反检测补丁，无需 channel="chrome"）
        # 关键：必须用 patchright 的浏览器，而不是系统 Chrome，否则打不上补丁。
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            viewport=viewport,
            user_agent=MOBILE_UA,
            storage_state=storage_state,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            # 关键：UA 声称是 iPhone，必须启用移动端仿真，
            # 否则 navigator 无触屏/移动特性，指纹自相矛盾，容易被风控识别。
            is_mobile=True,
            has_touch=True,
            device_scale_factor=1,
        )

        # patchright 已在 CDP 层隐藏自动化特征，无需再注入 JS 反检测脚本
        # （STEALTH_SCRIPT 保留仅为参考，注入反而可能引入不一致指纹）

        for idx, shop in enumerate(shops, 1):
            # 暂停：等待恢复
            while _pause_event.is_set() and not _stop_event.is_set():
                time.sleep(0.3)
            # 停止：收到停止信号后退出
            if _stop_event.is_set():
                print("⏹ 收到停止信号，停止截图")
                break
            shop_name = sanitize_name(shop.get("name", f"shop_{idx}"))
            shop_url = shop.get("url", "")

            if not shop_url:
                print(f"⏭ [{idx}/{len(shops)}] {shop_name}: 缺少 URL，跳过")
                results["error"].append({"name": shop_name, "reason": "缺少 URL 配置"})
                continue

            print(f"📸 [{idx}/{len(shops)}] {shop_name}")
            print(f"   URL: {shop_url}")
            _write_progress(idx, len(shops), shop.get("name", ""), "正在加载页面")

            page = None
            shop_start = time.time()
            SHOP_TIMEOUT = 300  # 单店铺最多 5 分钟

            try:
                page = context.new_page()
                page.set_default_timeout(page_timeout)

                # 宽度固定 574，仅高度微调避免指纹完全一致
                page.set_viewport_size({
                    "width": viewport["width"],
                    "height": viewport["height"] + random.randint(-5, 15),
                })

                start_time = time.time()

                # ---- 第1步：导航到页面 ----
                print("   🌐 正在加载页面...")
                page.goto(shop_url, wait_until="domcontentloaded", timeout=page_timeout)

                # ---- 第2步：等待网络基本空闲 ----
                _write_progress(idx, len(shops), shop.get("name", ""), "等待页面加载")
                try:
                    page.wait_for_load_state("networkidle", timeout=page_timeout)
                except Exception:
                    pass

                # 等待风控 JS 初始化完成，避免把页面加载中“隐藏”的 noCaptcha
                # 容器误判为验证码（给页面一点稳定时间再检测）
                time.sleep(random.uniform(3, 5))

                # ---- 第3步：检测验证码/真人认证 ----
                if is_captcha_page(page):
                    handle_captcha(page, shop_name, shop_url, results, "加载时")
                    continue

                # ---- 第4步：检测登录态过期 ----
                if is_logged_out(page):
                    print(f"   ⚠️  登录态已过期！页面被重定向到登录页")
                    results["login_expired"].append({"name": shop_name, "url": shop_url})
                    page.close()
                    continue

                # ---- 第5步：等待内容出现 ----
                if time.time() - shop_start > SHOP_TIMEOUT:
                    raise TimeoutError("店铺处理超时")
                print("   ⏳ 等待页面内容加载...")
                _write_progress(idx, len(shops), shop.get("name", ""), "等待店铺内容")
                wait_for_content(page, timeout_ms=min(page_timeout, 15000))

                # ---- 第6步：撑高视口，触发并等待所有懒加载 ----
                print("   ⏳ 加载整页内容...")
                _write_progress(idx, len(shops), shop.get("name", ""), "加载整页内容")
                total_height = expand_viewport_to_full(page, viewport["width"], max_height)

                if time.time() - shop_start > SHOP_TIMEOUT:
                    raise TimeoutError("店铺处理超时")

                # ---- 第7步：额外等待 ----
                extra_wait = random.uniform(wait_min, wait_max)
                print(f"   ⏳ 停留 {extra_wait:.1f} 秒...")
                time.sleep(extra_wait)

                # ---- 第8步：再次检查验证码 ----
                if is_captcha_page(page):
                    handle_captcha(page, shop_name, shop_url, results, "浏览中")
                    continue

                # ---- 第9步：清理页面 + 截全页图 + 高度裁剪 ----
                print("   📸 正在截图...")
                _write_progress(idx, len(shops), shop.get("name", ""), "正在保存截图")

                # 关弹窗
                dismiss_popups(page)

                # 隐藏侧边栏
                try:
                    page.evaluate(
                        "document.querySelectorAll('.tcodeSideBar, #tcodeSideBar, "
                        "[class*=\"tcodeSideBar\"], [id*=\"tcodeSideBar\"]').forEach("
                        "el => el.style.setProperty('display', 'none', 'important'))"
                    )
                except Exception:
                    pass

                # 滚回顶部
                page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
                time.sleep(0.5)

                output_dir = SCREENSHOTS_DIR / shop_name / date_str
                ensure_dir(output_dir)
                output_file = output_dir / f"{time_str}.png"

                # 视口已撑到整页高度，整页都在一个视口内，直接截视口即可
                page.screenshot(path=str(output_file), full_page=False)
                actual_height = total_height

                # 若配置了裁剪标记文案，则从该文案位置往下裁掉（只保留店铺自营内容）。
                # 先查 DOM 文字（快）；「逛逛更多宝贝」这类标题常被渲染成图片，DOM 查不到时用 OCR 兜底。
                if crop_text:
                    crop_y = find_text_y(page, crop_text)
                    if crop_y is None:
                        crop_y = ocr_find_text_y(
                            str(output_file), crop_text, start_fraction=ocr_start_fraction
                        )
                    if crop_y is not None and 0 < crop_y < actual_height:
                        original_crop_y = crop_y
                        crop_y = max(1, crop_y - crop_margin_top)
                        crop_image(str(output_file), crop_y)
                        actual_height = crop_y
                        print(f"   ✂️  在 {original_crop_y}px 处发现「{crop_text}」，向上回退 {original_crop_y - crop_y}px 后裁剪")
                    elif crop_text:
                        print(f"   ⚠️  未识别到裁剪标记「{crop_text}」，保留完整截图")

                # 截后复查 — 如果截图中出现了验证码，仍保留截图，并记录为需重试
                if is_captcha_page(page):
                    shot = save_captcha_screenshot(page, shop_name)
                    print(f"   🤖 截图中检测到验证码，截图已保留，稍后自动重试")
                    results["captcha"].append({
                        "name": shop_name, "url": shop_url,
                        "file": str(output_file), "captcha_shot": shot,
                    })
                    page.close()
                    cooldown = random.uniform(45, 75)
                    print(f"   ⏸  冷却 {cooldown:.0f} 秒...")
                    time.sleep(cooldown)
                    continue

                elapsed = time.time() - start_time
                file_size = output_file.stat().st_size / 1024

                print(f"   ✅ 已保存: {output_file}")
                print(f"   📊 图片: {viewport['width']}x{actual_height}px | 文件: {file_size:.1f} KB | 耗时: {elapsed:.1f}s")
                results["success"].append({
                    "name": shop_name, "file": str(output_file), "size_kb": file_size
                })
                _write_progress(idx, len(shops), shop.get("name", ""), "店铺完成")

            except Exception as e:
                print(f"   ❌ 截图失败: {e}")
                results["error"].append({"name": shop_name, "reason": str(e)})
            finally:
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass

            # 店铺之间至少间隔 30 秒，降低触发验证码的概率
            if idx < len(shops):
                gap = 30 + random.uniform(0, 10)
                print(f"   ⏸  间隔 {gap:.0f} 秒...\n")
                time.sleep(gap)
            else:
                print()

        context.close()
        browser.close()

    # ---- 输出汇总 ----
    print("-" * 60)
    print("📋 本轮截图汇总:")
    print(f"   ✅ 成功:      {len(results['success'])} 个")
    print(f"   🤖 触发验证码: {len(results['captcha'])} 个")
    print(f"   🔐 登录过期:  {len(results['login_expired'])} 个")
    print(f"   ❌ 失败:      {len(results['error'])} 个")

    if results["captcha"]:
        print()
        print("🤖 以下店铺触发了真人认证，建议稍后重试或减少截图频率:")
        for item in results["captcha"]:
            print(f"   - {item['name']}")

    if results["login_expired"]:
        print()
        print("⚠️  部分店铺登录态已过期，请重新登录:")
        for item in results["login_expired"]:
            print(f"   - {item['name']}")
        print(f"   运行: python {PROJECT_DIR / 'login.py'} --refresh")

    if results["error"]:
        print()
        print("❌ 以下店铺截图失败:")
        for item in results["error"]:
            print(f"   - {item['name']}: {item['reason']}")

    return results


def organize_captcha_shops(captcha_shops: list, round_label):
    """把触发真人验证的店铺整理到 logs/captcha_shops.json 并打印清单"""
    ensure_dir(LOGS_DIR)
    report_file = LOGS_DIR / "captcha_shops.json"

    records = []
    if report_file.exists():
        try:
            records = json.loads(report_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            records = []

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for s in captcha_shops:
        records.append({
            "timestamp": now,
            "round": round_label,
            "name": s.get("name"),
            "url": s.get("url"),
            "file": s.get("file"),
        })

    report_file.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print("🤖 触发真人验证的店铺（已整理到 logs/captcha_shops.json）:")
    for i, s in enumerate(captcha_shops, 1):
        print(f"   {i}. {s.get('name')}")
        if s.get("file"):
            print(f"      截图: {s['file']}")


def main():
    parser = argparse.ArgumentParser(description="淘宝店铺截图工具")
    parser.add_argument(
        "--shop",
        type=str,
        metavar="店铺名",
        help="只对配置中的指定单个店铺截图",
    )
    parser.add_argument(
        "--url",
        type=str,
        metavar="链接",
        help="直接截图某个店铺链接（无需在 config.yaml 中配置）",
    )
    parser.add_argument(
        "--name",
        type=str,
        metavar="名称",
        help="配合 --url 使用，指定截图保存目录名（默认从链接自动推断）",
    )
    args = parser.parse_args()

    try:
        import patchright  # noqa: F401
    except ImportError:
        print("❌ 未安装 patchright，请先运行: pip3 install -r requirements.txt")
        print("   然后执行: patchright install chromium")
        sys.exit(1)

    config = load_config()

    # 触发真人验证的店铺自动重试设置
    retry_interval = int(config.get("retry_interval_seconds", 60))
    max_retry_rounds = int(config.get("max_retry_rounds", 3))

    # 通过链接直接截图（优先于 --shop）
    if args.url:
        url = args.url
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        name = args.name or derive_name_from_url(url)
        print(f"🔗 直接截图链接：{url}")
        print(f"   保存目录名：{name}")
        results = capture_shops(config, shops=[{"name": name, "url": url}])
    else:
        results = capture_shops(config, shop_filter=args.shop)

    captcha_shops = results.get("captcha", [])

    for round_num in range(1, max_retry_rounds + 1):
        if not captcha_shops:
            break

        # 整理本轮触发真人验证的店铺
        organize_captcha_shops(captcha_shops, round_num)

        retry_shops = [{"name": s.get("name"), "url": s.get("url")} for s in captcha_shops]

        print()
        print("=" * 60)
        print(f"  🔄 第 {round_num} 轮自动重试：{len(retry_shops)} 个店铺")
        print(f"  ⏸  等待 {retry_interval} 秒后自动重跑...")
        print("=" * 60)
        time.sleep(retry_interval)

        results = capture_shops(config, shops=retry_shops)
        captcha_shops = results.get("captcha", [])

    if captcha_shops:
        organize_captcha_shops(captcha_shops, "最终仍未通过")
        print()
        print(f"⚠️  自动重试后仍有 {len(captcha_shops)} 个店铺触发真人验证:")
        for s in captcha_shops:
            print(f"   - {s.get('name')}")
        print(f"   详情见 logs/captcha_shops.json，可稍后手动重试或降低截图频率。")
    else:
        print()
        print("🎉 全部店铺本轮已完成截图（含自动重试）。")


if __name__ == "__main__":
    main()

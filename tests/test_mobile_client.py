from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_android_client_has_secure_session_and_adaptive_webview_contract():
    manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    activity = (ROOT / "mobile/android/app/src/main/java/com/practicaltools/mobile/MainActivity.kt").read_text(encoding="utf-8")
    gradle = (ROOT / "mobile/android/app/build.gradle.kts").read_text(encoding="utf-8")

    assert "android.permission.INTERNET" in manifest
    assert "android.permission.ACCESS_NETWORK_STATE" in manifest
    assert 'android:usesCleartextTraffic="${allowCleartextTraffic}"' in manifest
    assert 'android:screenOrientation=' not in manifest
    assert "CookieManager" in activity
    assert "Accept-Encoding" in activity
    assert "GZIPInputStream" in activity
    assert "newFixedThreadPool(4)" in activity
    assert "newFixedThreadPool(3)" in activity
    assert "decodeNativeImageWithRetry" in activity
    assert "isTransient" in activity
    assert 'setRequestProperty("Connection", "keep-alive")' in activity
    assert "showNativeFullAnalysis" in activity
    assert "renderNativeAnalysisBlocks" in activity
    assert "renderNativeAnalysisAnomaly" in activity
    assert "uploadNativeAnalysisFile" in activity
    assert "setRootContent" in activity
    assert "confirmLogout" in activity
    assert "removeAllCookies" in activity
    assert "setPositiveButton(\"退出\")" in activity
    assert "finish()" not in activity
    assert "showScreenshotNative" in activity
    assert "showAnalysisNative" in activity
    assert "/api/screenshot/list" in activity
    assert "/api/analysis/panel/overview?dim=" in activity
    assert "nativeScreenshotCard" in activity
    assert "showNativeImageViewer" in activity
    assert 'setOnClickListener { showNativeImageViewer(item) }' in activity
    assert 'currentNativeRoute == "screenshot-image"' in activity
    assert "OnBackInvokedDispatcher" in activity
    assert "handleBackNavigation" in activity
    assert "nativePreviewEdge" in activity
    assert "inScaled = false" in activity
    assert 'openPage("/screenshot/"' not in activity
    assert "showNativeShopManagement" in activity
    assert "showNativeScheduler" in activity
    assert "/api/screenshot/shops" in activity
    assert "/api/screenshot/scheduler/config" in activity
    assert "capture/$action" in activity
    assert "/api/screenshot/capture/stop" in activity
    assert "currentNativeRoute" in activity
    assert "/api/screenshot/file?path=" in activity
    assert "/api/screenshot/preview?path=" in activity
    assert "正在加载原图" in activity
    assert "decodeNativeOriginalWithRetry" in activity
    assert "nativeOriginalCache" in activity
    assert "decodeNativeImage" in activity
    assert "重试" in activity
    assert "loadNativeOriginal" in activity
    assert "screenshotListLoaded" in activity
    assert "screenshotFilter" in activity
    assert "screenshotScrollY" in activity
    assert "已恢复上次浏览状态" in activity
    assert "clearScreenshotState" in activity
    assert "nativeMetricCard" in activity
    assert "showConnectionDiagnostics" in activity
    assert "buildDiagnosticReport" in activity
    assert "DNS 解析" in activity
    assert "TLS 握手" in activity
    assert "IPv6 路径失败" in activity
    assert "IPv4：" in activity
    assert "IPv6：" in activity
    assert "复制诊断报告" in activity
    assert "ClipData.newPlainText" in activity
    assert "账号、密码、Cookie 或业务数据" in activity
    assert "currentNativeRoute = \"analysis-full\"" in activity
    assert "openPage(" not in activity
    assert "WebView" not in activity
    assert 'setRequestProperty("User-Agent"' in activity
    assert "SSLHandshakeException" in activity
    assert "UnknownHostException" in activity
    assert 'java.net.preferIPv4Stack' in activity
    assert "https://collab.wnnttzy.kdns.fr/" in gradle
    assert "127.0.0.1" not in gradle


def test_all_web_pages_declare_mobile_viewport_and_small_screen_rules():
    for relative in ("frontend/shell/index.html", "frontend/screenshot/index.html", "frontend/analysis/index.html"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert "viewport-fit=cover" in content, relative
        assert "@media (max-width: 720px)" in content, relative
        assert "env(safe-area-inset-bottom)" in content, relative


def test_mobile_business_pages_guard_real_webview_layout_regressions():
    analysis = (ROOT / "frontend/analysis/index.html").read_text(encoding="utf-8")
    screenshot = (ROOT / "frontend/screenshot/index.html").read_text(encoding="utf-8")

    assert ".nav { flex: 0 0 auto; align-items: flex-start; }" in analysis
    assert ".nav-item { align-self: flex-start; height: auto; }" in analysis
    assert ".sidebar { height: 72px !important;" in analysis
    assert ".sidebar .logo { flex: 0 0 auto !important;" in analysis
    assert "aside { width: 100% !important; height: 72px !important;" in screenshot
    assert "white-space: nowrap; min-width: max-content;" in screenshot

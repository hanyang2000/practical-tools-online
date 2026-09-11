package com.practicaltools.mobile

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.graphics.Color
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowInsets
import android.webkit.CookieManager
import android.window.OnBackInvokedDispatcher
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.GridLayout
import android.widget.HorizontalScrollView
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.Space
import android.widget.TextView
import android.widget.Toast
import android.util.LruCache
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.ConnectException
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ProxySelector
import java.net.URI
import java.net.URL
import java.net.URLEncoder
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.SocketException
import java.net.UnknownHostException
import java.security.cert.X509Certificate
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import java.util.zip.GZIPInputStream
import org.json.JSONArray
import org.json.JSONObject
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

class MainActivity : Activity() {
    // Keep API work and image work independent. A single shared queue meant
    // one slow thumbnail could block every page and action behind it.
    private val executor = Executors.newFixedThreadPool(4)
    private val imageExecutor = Executors.newFixedThreadPool(3)
    // Keep decoded originals for the current process.  This makes returning
    // from the viewer instant and avoids asking the center for the same large
    // image again after a back gesture.
    private val nativeOriginalCache = object : LruCache<String, android.graphics.Bitmap>(
        (Runtime.getRuntime().maxMemory() / 1024L / 8L).toInt().coerceAtLeast(4096)
    ) {
        override fun sizeOf(key: String, value: android.graphics.Bitmap): Int =
            (value.allocationByteCount / 1024).coerceAtLeast(1)
    }
    private val prefs by lazy { getSharedPreferences("mobile_preferences", Context.MODE_PRIVATE) }
    private var centerUrl = ""
    private var displayName = ""
    private var currentNativeRoute = "login"
    private var isShowingLogin = false
    private var logoutInProgress = false
    private var diagnosticsFromLogin = false
    // Keep the screenshot list as a small in-memory session cache. Returning
    // from the native image viewer should restore the user's filter and
    // scroll position instead of rebuilding the whole page and refetching it.
    private var screenshotItems: List<NativeScreenshot> = emptyList()
    private var screenshotFilter: String? = null
    private var screenshotListLoaded = false
    private var screenshotScrollY = 0
    private val analysisFileRequestCode = 4102

    private data class NativeScreenshot(
        val brand: String,
        val date: String,
        val path: String,
        val size: Long,
    )

    private enum class DiagnosticState { PASS, WARN, FAIL, RUNNING }

    private data class DiagnosticItem(
        val title: String,
        val detail: String,
        val state: DiagnosticState,
    )

    private data class DiagnosticReport(
        val summary: String,
        val items: List<DiagnosticItem>,
        val text: String,
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        configureMobileNetworkCompatibility()
        configureWindow(window)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) {
                handleBackNavigation()
            }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        centerUrl = prefs.getString("center_url", "").orEmpty()
        if (centerUrl.isBlank() || centerUrl == BuildConfig.DEFAULT_CENTER_URL && centerUrl.contains("your-center.example.com")) {
            showLogin()
        } else {
            checkExistingSession()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == analysisFileRequestCode && resultCode == RESULT_OK) {
            data?.data?.let { uploadNativeAnalysisFile(it) }
        }
    }

    override fun onBackPressed() {
        handleBackNavigation()
    }

    private fun handleBackNavigation() {
        if (currentNativeRoute == "screenshot-image") {
            // The image viewer is a child of the screenshot list. System
            // back and edge-swipe back must close the viewer first.
            showScreenshotNative()
        } else if (currentNativeRoute == "diagnostics") {
            if (diagnosticsFromLogin) showLogin() else showHome()
        } else if (currentNativeRoute !in setOf("home", "login", "loading")) {
            showHome()
        } else if (!isShowingLogin) {
            super.onBackPressed()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        executor.shutdownNow()
        imageExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun configureWindow(window: Window) {
        window.statusBarColor = color(R.color.pt_background)
        window.navigationBarColor = color(R.color.pt_background)
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
    }

    private fun configureMobileNetworkCompatibility() {
        // Some mobile carriers advertise IPv6 first but do not route all
        // Cloudflare IPv6 ranges reliably. Prefer IPv4 for the center API;
        // HTTPS certificate validation still uses the original hostname.
        System.setProperty("java.net.preferIPv4Stack", "true")
        System.setProperty("java.net.preferIPv6Addresses", "false")
        System.setProperty("http.keepAlive", "true")
        System.setProperty("http.maxConnections", "8")
    }

    private fun setRootContent(root: View) {
        applySystemBarInsets(root)
        setContentView(root)
        root.post {
            root.requestApplyInsets()
            installNativeMotion(root)
        }
    }

    /**
     * GSAP's transform/opacity guidance translated to native Android. Keep
     * interaction feedback compositor-friendly and skip it when the system's
     * animator scale is disabled.
     */
    private fun installNativeMotion(root: View) {
        val animationsEnabled = runCatching {
            Settings.Global.getFloat(
                contentResolver,
                Settings.Global.ANIMATOR_DURATION_SCALE,
                1f,
            ) > 0f
        }.getOrDefault(true)
        root.alpha = if (animationsEnabled) 0f else 1f
        if (animationsEnabled) {
            root.animate().alpha(1f).setDuration(160L).start()
        }
        applyPressFeedback(root, animationsEnabled)
    }

    private fun applyPressFeedback(view: View, animationsEnabled: Boolean) {
        if (view.isClickable || view is Button || view is ImageButton) {
            view.setOnTouchListener { target, event ->
                if (!animationsEnabled) return@setOnTouchListener false
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> target.animate()
                        .scaleX(0.98f)
                        .scaleY(0.98f)
                        .setDuration(120L)
                        .start()
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> target.animate()
                        .scaleX(1f)
                        .scaleY(1f)
                        .setDuration(160L)
                        .start()
                }
                false
            }
        }
        if (view is ViewGroup) {
            for (index in 0 until view.childCount) {
                applyPressFeedback(view.getChildAt(index), animationsEnabled)
            }
        }
    }

    private fun applySystemBarInsets(root: View) {
        val left = root.paddingLeft
        val top = root.paddingTop
        val right = root.paddingRight
        val bottom = root.paddingBottom
        root.setOnApplyWindowInsetsListener { view, insets ->
            val bars = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                insets.getInsets(WindowInsets.Type.systemBars())
            } else {
                android.graphics.Insets.of(
                    insets.systemWindowInsetLeft,
                    insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight,
                    insets.systemWindowInsetBottom,
                )
            }
            view.setPadding(left + bars.left, top + bars.top, right + bars.right, bottom + bars.bottom)
            insets
        }
    }

    private fun showLogin(message: String? = null) {
        isShowingLogin = true
        currentNativeRoute = "login"
        val scroll = ScrollView(this).apply {
            setBackgroundResource(R.drawable.bg_screen)
            isFillViewport = true
        }
        val page = column(18)
        page.gravity = Gravity.CENTER_HORIZONTAL
        page.setPadding(dp(24), dp(34), dp(24), dp(32))

        // Match the browser auth card: quiet light canvas, compact brand row,
        // and a single indigo accent instead of a large promotional gradient.
        val hero = column(8).apply {
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(16), dp(16), dp(16), dp(8))
            setBackgroundResource(R.drawable.bg_login_hero)
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.ic_app_logo)
            layoutParams = LinearLayout.LayoutParams(dp(48), dp(48))
            contentDescription = getString(R.string.app_name)
        }
        hero.addView(logo)
        hero.addView(text(getString(R.string.app_tagline), 24f, R.color.pt_text, true).apply {
            gravity = Gravity.CENTER
        })
        hero.addView(text(getString(R.string.login_subtitle), 14f, R.color.pt_text_muted, false).apply {
            gravity = Gravity.CENTER
        })
        page.addView(hero, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        // The card is one form module; keep its internal rhythm compact.
        val card = column(8).apply {
            setPadding(dp(20), dp(20), dp(20), dp(20))
            setBackgroundResource(R.drawable.bg_card)
        }
        val cardParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        page.addView(card, cardParams)

        val urlInput = labeledInput(getString(R.string.center_url_label), centerUrl.ifBlank { BuildConfig.DEFAULT_CENTER_URL }, false)
        val usernameInput = labeledInput(getString(R.string.username_label), prefs.getString("username", "").orEmpty(), false)
        val passwordInput = labeledInput(getString(R.string.password_label), "", true)
        card.addView(urlInput.first)
        card.addView(usernameInput.first)

        // Keep the label above the field. The previous version put the whole
        // labeled column in the same row as the action button, so the button
        // was centered against label + field and visibly drifted downward.
        card.addView(text(getString(R.string.password_label), 12f, R.color.pt_text_muted, true))
        (passwordInput.first as ViewGroup).removeView(passwordInput.second)
        val passwordRow = row()
        passwordRow.gravity = Gravity.CENTER_VERTICAL
        passwordRow.addView(passwordInput.second, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val showPassword = Button(this).apply {
            text = getString(R.string.show_password)
            textSize = 12f
            setTextColor(color(R.color.pt_primary))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(48)
            minimumHeight = dp(48)
            minWidth = dp(64)
            minimumWidth = dp(64)
            contentDescription = getString(R.string.show_password)
        }
        showPassword.setOnClickListener {
            val visible = (passwordInput.second.inputType and InputType.TYPE_MASK_VARIATION) == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            passwordInput.second.inputType = if (visible) {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            } else {
                InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            }
            passwordInput.second.setSelection(passwordInput.second.text.length)
            showPassword.text = if (visible) getString(R.string.show_password) else getString(R.string.hide_password)
            showPassword.contentDescription = showPassword.text
        }
        passwordRow.addView(showPassword, marginStart(8))
        card.addView(passwordRow)

        val error = text(message.orEmpty(), 13f, R.color.pt_error, false).apply {
            minHeight = dp(16)
            setPadding(0, 0, 0, 0)
            visibility = if (message.isNullOrBlank()) View.GONE else View.VISIBLE
            setContentDescription(message)
        }
        card.addView(error)

        val login = Button(this).apply {
            text = getString(R.string.login)
            textSize = 16f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_primary_button)
            minHeight = dp(48)
            isAllCaps = false
        }
        card.addView(login)
        val diagnose = Button(this).apply {
            text = "检测中心连接"
            textSize = 14f
            isAllCaps = false
            setTextColor(color(R.color.pt_primary))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
            setOnClickListener { showConnectionDiagnostics(urlInput.second.text.toString()) }
        }
        card.addView(diagnose)
        page.addView(text("生产环境请使用 HTTPS；账号和密码只用于本次登录，不会写入本机。", 12f, R.color.pt_text_muted, false).apply {
            gravity = Gravity.CENTER
            setPadding(dp(4), dp(18), dp(4), 0)
        })

        login.setOnClickListener {
            val service = normalizeUrl(urlInput.second.text.toString())
            val username = usernameInput.second.text.toString().trim()
            val password = passwordInput.second.text.toString()
            if (service.isBlank() || username.isBlank() || password.isBlank()) {
                error.text = "请填写中心地址、账号和密码"
                error.visibility = View.VISIBLE
                return@setOnClickListener
            }
            if (!service.startsWith("https://") && !service.startsWith("http://")) {
                error.text = "中心地址需要以 https:// 或 http:// 开头"
                error.visibility = View.VISIBLE
                return@setOnClickListener
            }
            login.isEnabled = false
            login.text = "正在登录…"
            error.visibility = View.GONE
            executor.execute {
                val result = ApiClient.login(service, username, password)
                runOnUiThread {
                    login.isEnabled = true
                    login.text = getString(R.string.login)
                    if (result.code in 200..299) {
                        acceptCookies(service, result.cookies)
                        centerUrl = service
                        displayName = jsonValue(result.body, "display_name")
                            .ifBlank { jsonValue(result.body, "username") }
                            .ifBlank { username }
                        prefs.edit().putString("center_url", service).putString("username", username).apply()
                        clearScreenshotState()
                        showHome()
                    } else {
                        error.text = result.message()
                        error.visibility = View.VISIBLE
                    }
                }
            }
        }

        scroll.addView(page)
        setRootContent(scroll)
    }

    private fun checkExistingSession() {
        showLoading("正在连接中心服务…")
        executor.execute {
            val result = ApiClient.request("GET", centerUrl, "$centerUrl/api/auth/status", null, cookieHeader(centerUrl))
            runOnUiThread {
                val authenticated = runCatching { JSONObject(result.body).optBoolean("authenticated", false) }.getOrDefault(false)
                if (result.code in 200..299 && authenticated) {
                    displayName = jsonValue(result.body, "display_name").ifBlank { prefs.getString("username", "用户").orEmpty() }
                    showHome()
                } else {
                    showLogin(if (result.code in 400..599) "会话已过期，请重新登录" else null)
                }
            }
        }
    }

    private fun showConnectionDiagnostics(initialUrl: String) {
        diagnosticsFromLogin = isShowingLogin
        currentNativeRoute = "diagnostics"
        val root = column(0).apply { setBackgroundResource(R.drawable.bg_screen) }
        val scroll = ScrollView(this)
        val page = column(16).apply { setPadding(screenPadding(), dp(16), screenPadding(), dp(28)) }
        scroll.addView(page)

        val intro = column(5).apply {
            setPadding(dp(16), dp(14), dp(16), dp(14))
            setBackgroundResource(R.drawable.bg_card)
        }
        intro.addView(text("连接诊断", 22f, R.color.pt_text, true))
        intro.addView(text("不用登录，直接检查这台手机到中心服务的网络链路。", 13f, R.color.pt_text_muted, false))
        intro.addView(text("不会发送账号、密码、Cookie 或业务数据。", 12f, R.color.pt_text_muted, false).apply {
            setPadding(0, dp(3), 0, 0)
        })
        page.addView(intro)

        val addressField = labeledInput("检测地址", initialUrl.ifBlank { BuildConfig.DEFAULT_CENTER_URL }, false)
        page.addView(addressField.first, marginTop(2))

        val start = Button(this).apply {
            text = "开始诊断"
            textSize = 15f
            typeface = Typeface.DEFAULT_BOLD
            isAllCaps = false
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_primary_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
        }
        page.addView(start, marginTop(2))

        val summaryCard = column(5).apply {
            setPadding(dp(16), dp(14), dp(16), dp(14))
            setBackgroundResource(R.drawable.bg_card)
        }
        summaryCard.addView(text("诊断结果", 16f, R.color.pt_text, true))
        val summary = text("等待检测", 14f, R.color.pt_text_muted, true)
        summaryCard.addView(summary, marginTop(3))
        page.addView(summaryCard, marginTop(2))

        page.addView(text("检查明细", 16f, R.color.pt_text, true).apply {
            setPadding(0, dp(4), 0, 0)
        })
        val results = column(8)
        page.addView(results)

        val copy = Button(this).apply {
            text = "复制诊断报告"
            textSize = 13f
            isAllCaps = false
            isEnabled = false
            setTextColor(color(R.color.pt_primary))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(44)
            minimumHeight = dp(44)
        }
        page.addView(copy, marginTop(2))
        page.addView(text("提示：如果浏览器也打不开，优先查看 DNS、IPv6、TLS、代理/VPN 和当前运营商网络。", 12f, R.color.pt_text_muted, false).apply {
            setPadding(dp(2), dp(2), dp(2), 0)
        })

        fun renderItems(items: List<DiagnosticItem>) {
            results.removeAllViews()
            items.forEach { item ->
                val card = row().apply {
                    gravity = Gravity.TOP
                    setPadding(dp(12), dp(10), dp(12), dp(10))
                    setBackgroundResource(R.drawable.bg_card)
                }
                val badge = text(diagnosticStateLabel(item.state), 11f, diagnosticStateColor(item.state), true).apply {
                    gravity = Gravity.CENTER
                    setPadding(dp(7), dp(5), dp(7), dp(5))
                    minWidth = dp(48)
                }
                val copyBox = column(2).apply { setPadding(dp(10), 0, 0, 0) }
                copyBox.addView(text(item.title, 14f, R.color.pt_text, true))
                copyBox.addView(text(item.detail, 12f, R.color.pt_text_muted, false).apply {
                    setPadding(0, dp(3), 0, 0)
                })
                card.addView(badge)
                card.addView(copyBox, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                results.addView(card)
            }
        }

        copy.setOnClickListener {
            val report = copy.tag as? String ?: return@setOnClickListener
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("连接诊断报告", report))
            Toast.makeText(this, "诊断报告已复制", Toast.LENGTH_SHORT).show()
        }

        val back = subToolbar(
            "连接诊断",
            refreshAction = { start.performClick() },
            backAction = { if (diagnosticsFromLogin) showLogin() else showHome() },
        )
        root.addView(back, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(browserModuleRail("diagnostics"), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setRootContent(root)

        start.setOnClickListener {
            val service = normalizeUrl(addressField.second.text.toString())
            if (service.isBlank()) {
                summary.text = "请输入要检测的中心地址"
                summary.setTextColor(color(R.color.pt_error))
                return@setOnClickListener
            }
            start.isEnabled = false
            start.text = "正在检测…"
            copy.isEnabled = false
            copy.tag = null
            summary.text = "正在逐项检查手机到中心服务的链路…"
            summary.setTextColor(color(R.color.pt_primary))
            results.removeAllViews()
            results.addView(text("设备环境、网络、DNS、TCP、TLS 和 HTTPS 将依次检查。", 13f, R.color.pt_text_muted, false).apply {
                setPadding(0, dp(4), 0, dp(4))
            })
            executor.execute {
                val report = buildDiagnosticReport(service)
                runOnUiThread {
                    start.isEnabled = true
                    start.text = "重新诊断"
                    summary.text = report.summary
                    summary.setTextColor(if (report.items.any { it.state == DiagnosticState.FAIL }) color(R.color.pt_error) else color(R.color.pt_success))
                    renderItems(report.items)
                    copy.isEnabled = true
                    copy.tag = report.text
                }
            }
        }

    }

    private fun buildDiagnosticReport(service: String): DiagnosticReport {
        val items = mutableListOf<DiagnosticItem>()
        val parsed = runCatching { URL(service) }.getOrNull()
        if (parsed == null || parsed.host.isNullOrBlank() || parsed.protocol.lowercase(Locale.US) !in setOf("http", "https")) {
            items.add(DiagnosticItem("中心地址", "地址格式无效，需要以 http:// 或 https:// 开头。", DiagnosticState.FAIL))
            return diagnosticReport("地址格式无效，请先修正检测地址。", items, service)
        }

        val host = parsed.host
        val scheme = parsed.protocol.lowercase(Locale.US)
        val port = if (parsed.port > 0) parsed.port else if (scheme == "https") 443 else 80
        val device = deviceEnvironmentSummary()
        items.add(DiagnosticItem("设备环境", device, DiagnosticState.PASS))

        val connectivity = connectivitySummary()
        items.add(DiagnosticItem("当前网络", connectivity.first, connectivity.second))
        val proxy = proxySummary(service)
        items.add(DiagnosticItem("代理 / VPN", proxy.first, proxy.second))

        if (scheme != "https") {
            items.add(DiagnosticItem("传输安全", "当前地址使用 HTTP，生产环境建议改为 HTTPS。", DiagnosticState.WARN))
        } else {
            items.add(DiagnosticItem("传输安全", "地址使用 HTTPS。", DiagnosticState.PASS))
        }

        val addresses = try {
            InetAddress.getAllByName(host).distinctBy { it.hostAddress.orEmpty() }
        } catch (error: Exception) {
            items.add(DiagnosticItem("DNS 解析", "无法解析 $host：${shortError(error)}。可能是私有 DNS、运营商 DNS 或域名拦截。", DiagnosticState.FAIL))
            return diagnosticReport("DNS 解析失败，手机还没有找到中心服务器的 IP 地址。", items, service)
        }
        val addressText = addresses.joinToString("、") { formatDiagnosticAddress(it) }
        items.add(DiagnosticItem("DNS 解析", "解析到 ${addresses.size} 个地址：$addressText", DiagnosticState.PASS))

        val tcpSuccesses = mutableListOf<InetAddress>()
        val tcpErrors = mutableListOf<String>()
        addresses.take(6).forEach { address ->
            try {
                Socket().use { socket ->
                    socket.connect(InetSocketAddress(address, port), 5_000)
                }
                tcpSuccesses.add(address)
            } catch (error: Exception) {
                tcpErrors.add("${formatDiagnosticAddress(address)} ${shortError(error)}")
            }
        }
        val tcpState = if (tcpSuccesses.isNotEmpty()) DiagnosticState.PASS else DiagnosticState.FAIL
        val tcpDetail = if (tcpSuccesses.isNotEmpty()) {
            "TCP $port 可达：${tcpSuccesses.joinToString("、") { formatDiagnosticAddress(it) }}"
        } else {
            "TCP $port 不可达：${tcpErrors.take(2).joinToString("；")}. 可能是路由、防火墙或运营商路径问题。"
        }
        items.add(DiagnosticItem("TCP 端口", tcpDetail, tcpState))

        var tlsOk = false
        var tlsDetail = "未执行 TLS 检查。"
        if (scheme == "https") {
            val tlsErrors = mutableListOf<String>()
            val tlsAddresses = (tcpSuccesses.ifEmpty { addresses }).take(4)
            tlsAddresses.forEach { address ->
                var socket: SSLSocket? = null
                try {
                    socket = (SSLSocketFactory.getDefault() as SSLSocketFactory).createSocket() as SSLSocket
                    socket.connect(InetSocketAddress(address, 443), 5_000)
                    val parameters = socket.sslParameters
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                        parameters.serverNames = listOf(SNIHostName(host))
                    }
                    socket.sslParameters = parameters
                    socket.startHandshake()
                    val session = socket.session
                    val certificate = runCatching { session.peerCertificates.firstOrNull() as? X509Certificate }.getOrNull()
                    val subject = certificate?.subjectX500Principal?.name?.substringAfter("CN=")?.substringBefore(',').orEmpty()
                    tlsDetail = "TLS 握手成功：${session.protocol}${if (subject.isNotBlank()) " · 证书 CN=$subject" else ""}"
                    tlsOk = true
                    return@forEach
                } catch (error: Exception) {
                    tlsErrors.add("${formatDiagnosticAddress(address)} ${shortError(error)}")
                } finally {
                    runCatching { socket?.close() }
                }
            }
            if (!tlsOk) {
                val ipv4Errors = tlsErrors.filter { it.contains("(IPv4)") }
                val ipv6Errors = tlsErrors.filter { it.contains("(IPv6)") }
                val grouped = buildList {
                    if (ipv4Errors.isNotEmpty()) add("IPv4：${ipv4Errors.joinToString("；")}")
                    if (ipv6Errors.isNotEmpty()) add("IPv6：${ipv6Errors.joinToString("；")}")
                }.joinToString(" | ")
                tlsDetail = "TLS 握手失败：$grouped。重点检查系统时间、证书链、SNI 和网络拦截。"
            }
            items.add(DiagnosticItem("TLS 握手", tlsDetail, if (tlsOk) DiagnosticState.PASS else DiagnosticState.FAIL))
        }

        val httpItem = probeHttpsResponse(service)
        items.add(httpItem)

        val hasIpv6 = addresses.any { it is Inet6Address }
        val ipv4Worked = tcpSuccesses.any { it is Inet4Address }
        val ipv6Worked = tcpSuccesses.any { it is Inet6Address }
        val advice = when {
            connectivity.second == DiagnosticState.FAIL -> "手机当前没有可用互联网连接，先检查卓易通的联网权限、Wi-Fi/移动数据和系统省流量限制。"
            addresses.isEmpty() -> "DNS 没有返回地址，检查手机的私有 DNS、运营商 DNS 或域名解析策略。"
            hasIpv6 && ipv4Worked && !ipv6Worked -> "检测到 IPv6 地址但 IPv6 路径失败、IPv4 正常；鸿蒙手机可先切换移动网络/关闭该 Wi-Fi 的 IPv6，或修复 IPv6 路由。"
            !tlsOk && tcpSuccesses.isNotEmpty() -> "TCP 已通但 TLS 失败，重点检查手机系统时间、证书链、SNI、代理/VPN 和运营商 HTTPS 拦截。"
            httpItem.state == DiagnosticState.FAIL -> "网络链路已到达主机，但 HTTPS 服务返回异常；请检查中心服务、Cloudflare 回源和反向代理日志。"
            proxy.first.contains("VPN", true) || proxy.first.contains("代理", true) -> "当前网络经过代理或 VPN；请暂时关闭后重试，排除卓易通对 HTTPS 的兼容影响。"
            else -> "基础链路检查通过；如果浏览器仍打不开，建议在同一 Wi-Fi 下分别测试域名、IPv4 和移动网络，并保留本报告。"
        }
        items.add(DiagnosticItem("定位建议", advice, if (advice.contains("通过")) DiagnosticState.PASS else DiagnosticState.WARN))
        val failCount = items.count { it.state == DiagnosticState.FAIL }
        val warnCount = items.count { it.state == DiagnosticState.WARN }
        val summary = when {
            failCount > 0 -> "发现 $failCount 项失败，已给出对应排查方向。"
            warnCount > 0 -> "链路基本可达，但有 $warnCount 项需要注意。"
            else -> "链路检查通过，中心服务当前可达。"
        }
        return diagnosticReport(summary, items, service)
    }

    private fun diagnosticReport(summary: String, items: List<DiagnosticItem>, service: String): DiagnosticReport {
        val timestamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA).format(Date())
        val report = buildString {
            appendLine("策划实用小工具 · 连接诊断报告")
            appendLine("时间：$timestamp")
            appendLine("地址：$service")
            items.forEach { appendLine("[${diagnosticStateLabel(it.state)}] ${it.title}：${it.detail}") }
            appendLine("账号、密码和 Cookie 未参与检测。")
        }
        return DiagnosticReport(summary, items, report)
    }

    private fun deviceEnvironmentSummary(): String {
        val properties = listOf(
            "ro.build.version.emui",
            "ro.build.version.ohos",
            "ro.build.version.magic",
            "hw_sc.build.platform.version",
        ).mapNotNull { key -> readSystemProperty(key).takeIf { it.isNotBlank() }?.let { "$key=$it" } }
        val marker = listOf(Build.DISPLAY, Build.VERSION.RELEASE, Build.MANUFACTURER, Build.BRAND).joinToString(" ")
        val harmony = marker.contains("Harmony", true) || marker.contains("鸿蒙", true) || properties.isNotEmpty()
        val base = "${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})"
        return if (harmony) "$base · HarmonyOS/EMUI 环境${properties.firstOrNull()?.let { " · $it" }.orEmpty()}" else base
    }

    private fun readSystemProperty(key: String): String = runCatching {
        val clazz = Class.forName("android.os.SystemProperties")
        clazz.getMethod("get", String::class.java).invoke(null, key) as? String ?: ""
    }.getOrDefault("")

    private fun connectivitySummary(): Pair<String, DiagnosticState> {
        val manager = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = manager.activeNetwork ?: return "未检测到当前网络" to DiagnosticState.FAIL
        val capabilities = manager.getNetworkCapabilities(network)
        if (capabilities == null) return "网络信息暂时不可用" to DiagnosticState.WARN
        val transports = buildList {
            if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) add("Wi-Fi")
            if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) add("移动数据")
            if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) add("以太网")
            if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) add("VPN")
        }.ifEmpty { listOf("未知网络") }
        val internet = capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        val validated = capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        val state = when {
            !internet -> DiagnosticState.FAIL
            !validated -> DiagnosticState.WARN
            else -> DiagnosticState.PASS
        }
        val detail = transports.joinToString(" / ") + when {
            !internet -> " · 系统未确认可访问互联网"
            !validated -> " · 网络尚未通过系统联网验证"
            else -> " · 已通过系统联网验证"
        }
        return detail to state
    }

    private fun proxySummary(service: String): Pair<String, DiagnosticState> {
        val systemHost = System.getProperty("http.proxyHost").orEmpty()
        val systemPort = System.getProperty("http.proxyPort").orEmpty()
        val selector = runCatching { ProxySelector.getDefault()?.select(URI(service)).orEmpty() }.getOrDefault(emptyList())
        val selected = selector.filter { it.type() != java.net.Proxy.Type.DIRECT }
        return if (systemHost.isNotBlank() || selected.isNotEmpty()) {
            val detail = if (systemHost.isNotBlank()) "系统代理 $systemHost${if (systemPort.isNotBlank()) ":$systemPort" else ""}" else "检测到网络代理"
            "$detail；如浏览器也打不开，请暂时关闭代理/VPN 重试" to DiagnosticState.WARN
        } else {
            "未发现系统代理" to DiagnosticState.PASS
        }
    }

    private fun probeHttpsResponse(service: String): DiagnosticItem {
        return try {
            val connection = (URL(service).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 8_000
                readTimeout = 12_000
                instanceFollowRedirects = false
                useCaches = false
                setRequestProperty("User-Agent", "PracticalToolsMobile-Diagnostic/${BuildConfig.VERSION_NAME} (Android)")
                setRequestProperty("Accept", "text/html,application/json;q=0.9,*/*;q=0.8")
            }
            val code = connection.responseCode
            val location = connection.getHeaderField("Location")
            val server = connection.getHeaderField("Server")
            connection.disconnect()
            when {
                code in 200..299 -> DiagnosticItem("HTTPS 响应", "中心服务返回 HTTP $code${server?.let { " · $it" }.orEmpty()}。", DiagnosticState.PASS)
                code in 300..399 -> DiagnosticItem("HTTPS 响应", "中心服务返回 HTTP $code，重定向到 ${location ?: "未提供目标"}。", DiagnosticState.WARN)
                code in 400..499 -> DiagnosticItem("HTTPS 响应", "已到达中心服务，但返回 HTTP $code；这通常是权限、登录或防护策略，不是网络不通。", DiagnosticState.WARN)
                else -> DiagnosticItem("HTTPS 响应", "中心服务返回 HTTP $code，可能是服务端或回源异常。", DiagnosticState.FAIL)
            }
        } catch (error: Exception) {
            DiagnosticItem("HTTPS 响应", "请求没有完成：${shortError(error)}。", DiagnosticState.FAIL)
        }
    }

    private fun formatDiagnosticAddress(address: InetAddress): String = when (address) {
        is Inet6Address -> "[${address.hostAddress?.substringBefore('%').orEmpty()}] (IPv6)"
        else -> "${address.hostAddress.orEmpty()} (IPv4)"
    }

    private fun shortError(error: Throwable): String {
        val message = error.message?.replace(Regex("\\s+"), " ").orEmpty().take(120)
        return if (message.isBlank()) error.javaClass.simpleName else "${error.javaClass.simpleName}: $message"
    }

    private fun diagnosticStateLabel(state: DiagnosticState): String = when (state) {
        DiagnosticState.PASS -> "通过"
        DiagnosticState.WARN -> "注意"
        DiagnosticState.FAIL -> "失败"
        DiagnosticState.RUNNING -> "检测中"
    }

    private fun diagnosticStateColor(state: DiagnosticState): Int = when (state) {
        DiagnosticState.PASS -> R.color.pt_success
        DiagnosticState.WARN -> R.color.pt_accent
        DiagnosticState.FAIL -> R.color.pt_error
        DiagnosticState.RUNNING -> R.color.pt_primary
    }

    private fun showHome() {
        isShowingLogin = false
        currentNativeRoute = "home"
        val scroll = ScrollView(this).apply { setBackgroundResource(R.drawable.bg_screen) }
        val page = column(18).apply { setPadding(screenPadding(), dp(18), screenPadding(), dp(28)) }

        val top = row().apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(4), 0, 0)
        }
        val titleBox = column(2)
        val titleLine = row().apply { gravity = Gravity.CENTER_VERTICAL }
        titleLine.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_app_logo)
            layoutParams = LinearLayout.LayoutParams(dp(40), dp(40)).apply { marginEnd = dp(16) }
            contentDescription = getString(R.string.app_name)
        })
        titleLine.addView(text(getString(R.string.home_title), 28f, R.color.pt_text, true))
        titleBox.addView(titleLine)
        titleBox.addView(text("你好，${displayName.ifBlank { "用户" }}", 14f, R.color.pt_text_muted, false))
        top.addView(titleBox, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val status = text("● 在线", 12f, R.color.pt_success, true).apply {
            setPadding(dp(10), dp(8), dp(10), dp(8))
            setBackgroundResource(R.drawable.bg_outline_button)
            contentDescription = "中心服务已连接"
        }
        top.addView(status)
        page.addView(top)

        val statusCard = column(8).apply {
            setPadding(dp(16), dp(14), dp(16), dp(14))
            setBackgroundResource(R.drawable.bg_card)
        }
        val serviceRow = row().apply { gravity = Gravity.CENTER_VERTICAL }
        serviceRow.addView(text("中心服务", 13f, R.color.pt_text_muted, true), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val transportLabel = if (centerUrl.startsWith("https://", ignoreCase = true)) "HTTPS" else "HTTP"
        val transportColor = if (transportLabel == "HTTPS") R.color.pt_success else R.color.pt_warning
        serviceRow.addView(text("$transportLabel · 已登录", 13f, transportColor, true))
        statusCard.addView(serviceRow)
        statusCard.addView(text(centerUrl.removePrefix("https://").removeSuffix("/"), 13f, R.color.pt_text, false))
        page.addView(statusCard)

        page.addView(sectionHeading("快捷入口"))
        addAdaptiveHomeCards(page, listOf(
            homeCard(R.drawable.ic_screenshot, getString(R.string.screenshot_title)) { showScreenshotNative() },
            homeCard(R.drawable.ic_chart, getString(R.string.analysis_title)) { showAnalysisNative() },
        ))

        page.addView(sectionHeading("连接与账号"))
        addAdaptiveHomeCards(page, listOf(
            homeCard(R.drawable.ic_refresh, "连接诊断") { showConnectionDiagnostics(centerUrl) },
            homeCard(R.drawable.ic_settings, getString(R.string.settings_title)) { showSettings() },
        ))

        val logout = Button(this).apply {
            text = getString(R.string.logout)
            textSize = 14f
            isAllCaps = false
            setTextColor(color(R.color.pt_error))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(48)
        }
        page.addView(logout, marginTop(2))
        logout.setOnClickListener { confirmLogout() }

        scroll.addView(page)
        setRootContent(scroll)
    }

    private fun addAdaptiveHomeCards(parent: LinearLayout, cards: List<View>) {
        // The browser launcher becomes a two-column grid on phones. Keep the
        // same compact launcher rhythm in the native workbench instead of
        // turning every module into a full-width vertical block.
        val line = row().apply { gravity = Gravity.TOP }
        cards.forEachIndexed { index, card ->
            line.addView(card, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                if (index > 0) marginStart = dp(10)
            })
        }
        if (cards.size == 1) line.addView(Space(this), LinearLayout.LayoutParams(0, 1, 1f).apply { marginStart = dp(10) })
        parent.addView(line)
    }

    private fun showSettings() {
        currentNativeRoute = "settings"
        val root = column(0).apply { setBackgroundResource(R.drawable.bg_screen) }
        val scroll = ScrollView(this).apply { setBackgroundResource(R.drawable.bg_screen) }
        val page = column(16)
        page.setPadding(screenPadding(), dp(16), screenPadding(), dp(32))
        page.addView(sectionHeading(getString(R.string.settings_title)))
        val input = labeledInput(getString(R.string.center_url_label), centerUrl, false)
        page.addView(input.first)
        val save = Button(this).apply {
            text = "保存并连接"
            textSize = 16f
            isAllCaps = false
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_primary_button)
            minHeight = dp(48)
        }
        page.addView(save)
        val back = Button(this).apply {
            text = getString(R.string.back)
            textSize = 14f
            isAllCaps = false
            setTextColor(color(R.color.pt_primary))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(48)
        }
        page.addView(back)
        save.setOnClickListener {
            val updated = normalizeUrl(input.second.text.toString())
            if (updated.isBlank()) {
                Toast.makeText(this, "请输入中心地址", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            centerUrl = updated
            prefs.edit().putString("center_url", updated).apply()
            clearScreenshotState()
            checkExistingSession()
        }
        back.setOnClickListener { showHome() }
        scroll.addView(page)
        root.addView(subToolbar(getString(R.string.settings_title), { checkExistingSession() }), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(browserModuleRail("settings"), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setRootContent(root)
    }

    private fun subToolbar(title: String, refreshAction: () -> Unit, backAction: () -> Unit = { showHome() }): View {
        val toolbar = row().apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), 0, dp(16), 0)
            setBackgroundColor(color(R.color.pt_surface))
        }
        val back = ImageButton(this).apply {
            setImageResource(R.drawable.ic_back)
            setBackgroundColor(Color.TRANSPARENT)
            minimumWidth = dp(48)
            minimumHeight = dp(48)
            setPadding(dp(8), dp(8), dp(8), dp(8))
            contentDescription = getString(R.string.back)
            setOnClickListener { backAction() }
        }
        val heading = text(title, 18f, R.color.pt_text, true).apply {
            includeFontPadding = false
        }
        val refresh = ImageButton(this).apply {
            setImageResource(R.drawable.ic_refresh)
            setBackgroundColor(Color.TRANSPARENT)
            minimumWidth = dp(48)
            minimumHeight = dp(48)
            setPadding(dp(8), dp(8), dp(8), dp(8))
            contentDescription = getString(R.string.refresh)
            setOnClickListener { refreshAction() }
        }
        toolbar.addView(back)
        toolbar.addView(heading, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        toolbar.addView(refresh)
        return toolbar
    }

    /**
     * Browser parity rail. The desktop browser uses a dark left sidebar;
     * on a phone the same hierarchy becomes a compact horizontal rail under
     * the white page toolbar, keeping navigation visually consistent in the
     * native client.
     */
    private fun browserModuleRail(active: String): View {
        val rail = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            setBackgroundColor(color(R.color.pt_nav))
        }
        val items = row().apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(8), dp(8), dp(8), dp(8))
        }
        val routes = listOf(
            "workbench" to "工作台",
            "screenshots" to "页面收集",
            "analysis" to "数据分析",
            "diagnostics" to "连接诊断",
            "settings" to "设置",
        )
        routes.forEach { (key, label) ->
            val selected = key == active || (active == "analysis-full" && key == "analysis")
            val item = TextView(this).apply {
                text = label
                textSize = 12f
                gravity = Gravity.CENTER
                includeFontPadding = false
                setTextColor(if (selected) Color.WHITE else color(R.color.pt_nav_text))
                setPadding(dp(8), dp(8), dp(8), dp(8))
                setBackgroundResource(if (selected) R.drawable.bg_chip_selected else R.drawable.bg_nav_unselected)
                isClickable = true
                isFocusable = true
            }
            item.setOnClickListener {
                when (key) {
                    "workbench" -> showHome()
                    "screenshots" -> showScreenshotNative()
                    "analysis" -> showNativeFullAnalysis()
                    "diagnostics" -> showConnectionDiagnostics(centerUrl)
                    "settings" -> showSettings()
                }
            }
            items.addView(item, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40)).apply {
                if (items.childCount > 0) marginStart = dp(8)
            })
        }
        rail.addView(items)
        return rail
    }

    private fun showScreenshotNative() {
        currentNativeRoute = "screenshots"
        val root = column(0).apply { setBackgroundResource(R.drawable.bg_screen) }
        val scroll = ScrollView(this).apply {
            setOnScrollChangeListener { _, _, scrollY, _, _ -> screenshotScrollY = scrollY }
        }
        val page = column(16).apply { setPadding(screenPadding(), dp(16), screenPadding(), dp(28)) }
        scroll.addView(page)

        val summary = column(6).apply {
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundResource(R.drawable.bg_card)
        }
        summary.addView(text("页面收集", 22f, R.color.pt_text, true))
        val status = text("正在读取截图状态…", 13f, R.color.pt_text_muted, false)
        summary.addView(status)
        page.addView(summary)

        page.addView(sectionHeading("快捷操作"))
        val actions = column(12)
        val trigger = Button(this).apply {
            text = "触发截图"
            isAllCaps = false
            textSize = 14f
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_primary_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
            setPadding(dp(10), 0, dp(10), 0)
            setOnClickListener { startNativeCapture(status) }
        }
        val actionRow = row()
        actionRow.addView(trigger, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val progress = Button(this).apply {
            text = "查看进度"
            isAllCaps = false
            textSize = 14f
            setTextColor(color(R.color.pt_primary))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
            setPadding(dp(10), 0, dp(10), 0)
            setOnClickListener { showNativeProgressDialog() }
        }
        actionRow.addView(progress, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(12) })
        actions.addView(actionRow)
        val secondaryRow = row()
        val secondary = Button(this).apply {
            text = "店铺管理"
            isAllCaps = false
            textSize = 13f
            setTextColor(color(R.color.pt_text_muted))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
            setPadding(dp(8), 0, dp(8), 0)
            setOnClickListener { showNativeShopManagement() }
        }
        secondaryRow.addView(secondary, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val schedule = Button(this).apply {
            text = "定时任务"
            isAllCaps = false
            textSize = 13f
            setTextColor(color(R.color.pt_text_muted))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
            setPadding(dp(8), 0, dp(8), 0)
            setOnClickListener { showNativeScheduler() }
        }
        secondaryRow.addView(schedule, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(12) })
        actions.addView(secondaryRow)
        page.addView(actions)

        page.addView(sectionHeading("店铺筛选"))
        val brandsScroll = HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false }
        val brands = row().apply { setPadding(0, dp(2), 0, dp(2)) }
        brandsScroll.addView(brands)
        page.addView(brandsScroll)

        page.addView(sectionHeading("最近截图"))
        val grid = column(10)
        page.addView(grid)

        fun renderGrid(filter: String?) {
            grid.removeAllViews()
            val items = if (filter.isNullOrBlank()) screenshotItems else screenshotItems.filter { it.brand == filter }
            if (items.isEmpty()) {
                grid.addView(text("暂无截图，先触发一次采集任务。", 14f, R.color.pt_text_muted, false).apply { setPadding(0, dp(18), 0, dp(18)) })
                return
            }
            val columns = when {
                resources.displayMetrics.widthPixels / resources.displayMetrics.density >= 600f -> 3
                resources.displayMetrics.widthPixels / resources.displayMetrics.density < 380f -> 1
                else -> 2
            }
            items.chunked(columns).forEach { pair ->
                val imageRow = row().apply { gravity = Gravity.TOP }
                pair.forEach { item -> imageRow.addView(nativeScreenshotCard(item), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(6) }) }
                repeat(columns - pair.size) { imageRow.addView(Space(this), LinearLayout.LayoutParams(0, 1, 1f).apply { marginStart = dp(6) }) }
                grid.addView(imageRow, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    // The grid stack supplies the single internal gap.
                    bottomMargin = 0
                })
            }
        }
        fun renderBrandFilters() {
            brands.removeAllViews()
            brands.addView(nativeFilterButton("全部 ${screenshotItems.size}", screenshotFilter == null) {
                screenshotFilter = null
                renderBrandFilters()
                renderGrid(null)
            })
            screenshotItems.map { it.brand }.distinct().take(20).forEach { brand ->
                brands.addView(nativeFilterButton(brand, screenshotFilter == brand, {
                    screenshotFilter = brand
                    renderBrandFilters()
                    renderGrid(brand)
                }), LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(8) })
            }
        }
        renderBrandFilters()

        val load = {
            status.text = if (screenshotListLoaded) "正在刷新截图状态…" else "正在读取截图状态…"
            executor.execute {
                val result = ApiClient.request("GET", centerUrl, "$centerUrl/api/screenshot/list", null, cookieHeader(centerUrl))
                runOnUiThread {
                    if (result.code !in 200..299) {
                        status.text = result.message()
                        return@runOnUiThread
                    }
                    try {
                        val rootJson = JSONObject(result.body)
                        val total = rootJson.optInt("total")
                        status.text = "共 $total 张截图 · 数据来自中心服务"
                        screenshotItems = parseNativeScreenshots(rootJson)
                        screenshotFilter = screenshotFilter?.takeIf { filter -> screenshotItems.any { it.brand == filter } }
                        screenshotListLoaded = true
                        renderBrandFilters()
                        renderGrid(screenshotFilter)
                        scroll.post { scroll.scrollTo(0, screenshotScrollY) }
                    } catch (_: Exception) {
                        status.text = "截图数据格式暂时无法读取"
                    }
                }
            }
        }
        val toolbar = subToolbar(getString(R.string.screenshot_title), load)
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(browserModuleRail("screenshots"), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setRootContent(root)
        if (screenshotListLoaded) {
            status.text = "共 ${screenshotItems.size} 张截图 · 已恢复上次浏览状态"
            renderBrandFilters()
            renderGrid(screenshotFilter)
            scroll.post { scroll.scrollTo(0, screenshotScrollY) }
        } else {
            load()
        }
    }

    private fun clearScreenshotState() {
        screenshotItems = emptyList()
        screenshotFilter = null
        screenshotListLoaded = false
        screenshotScrollY = 0
    }

    private fun nativeFilterButton(label: String, selected: Boolean, action: () -> Unit): Button = Button(this).apply {
        text = label
        isAllCaps = false
        textSize = 12f
        gravity = Gravity.CENTER
        includeFontPadding = false
        minHeight = 0
        minimumHeight = dp(40)
        minWidth = dp(64)
        minimumWidth = dp(64)
        setPadding(dp(16), 0, dp(16), 0)
        stateListAnimator = null
        setTextColor(if (selected) Color.WHITE else color(R.color.pt_text_muted))
        setBackgroundResource(if (selected) R.drawable.bg_chip_selected else R.drawable.bg_chip_outline)
        setOnClickListener { action() }
    }

    private fun nativeScreenshotCard(item: NativeScreenshot): View {
        val card = column(4).apply {
            setPadding(dp(6), dp(6), dp(6), dp(8))
            setBackgroundResource(R.drawable.bg_card)
            contentDescription = "${item.brand}，${item.date}"
            isClickable = true
            isFocusable = true
            setOnClickListener { showNativeImageViewer(item) }
        }
        val image = ImageView(this).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, if (screenWidthDp() < 380) dp(126) else dp(136))
            scaleType = ImageView.ScaleType.CENTER_CROP
            setBackgroundColor(color(R.color.pt_surface_muted))
            contentDescription = "${item.brand}截图"
        }
        card.addView(image)
        card.addView(text(item.brand, 12f, R.color.pt_text, true).apply { setPadding(dp(2), dp(4), dp(2), 0) })
        card.addView(text(item.date, 11f, R.color.pt_text_muted, false).apply { setPadding(dp(2), 0, dp(2), dp(2)) })
        loadNativeThumbnail(image, item.path)
        return card
    }

    private fun showNativeImageViewer(item: NativeScreenshot) {
        currentNativeRoute = "screenshot-image"
        val root = column(0).apply { setBackgroundColor(Color.BLACK) }
        val toolbar = row().apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(4), 0, dp(4), 0)
            setBackgroundColor(Color.BLACK)
        }
        val back = ImageButton(this).apply {
            setImageResource(R.drawable.ic_back)
            setColorFilter(Color.WHITE)
            setBackgroundColor(Color.TRANSPARENT)
            minimumWidth = dp(48)
            minimumHeight = dp(48)
            contentDescription = getString(R.string.back)
            setOnClickListener { showScreenshotNative() }
        }
        toolbar.addView(back)
        toolbar.addView(text(item.brand, 17f, R.color.pt_white, true), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        toolbar.addView(text(item.date, 12f, R.color.pt_white, false))
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))

        val scroll = ScrollView(this)
        val content = column(10).apply { setPadding(dp(10), dp(10), dp(10), dp(28)) }
        val loading = row().apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(4), dp(4), dp(4), dp(4))
        }
        val spinner = ProgressBar(this).apply {
            isIndeterminate = true
            minimumWidth = dp(24)
            minimumHeight = dp(24)
        }
        val status = text("正在加载原图…", 12f, R.color.pt_surface_muted, false)
        loading.addView(spinner, LinearLayout.LayoutParams(dp(24), dp(24)))
        loading.addView(status, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(8) })
        val retry = Button(this).apply {
            text = "重试"
            isAllCaps = false
            textSize = 12f
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_outline_button)
            visibility = View.GONE
            minHeight = dp(38)
            minimumHeight = dp(38)
            setPadding(dp(12), 0, dp(12), 0)
        }
        loading.addView(retry, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(42)).apply { marginStart = dp(6) })
        content.addView(loading)
        val image = ImageView(this).apply {
            adjustViewBounds = true
            scaleType = ImageView.ScaleType.FIT_CENTER
            setBackgroundColor(Color.BLACK)
            contentDescription = "${item.brand} ${item.date} 原图"
        }
        content.addView(image, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        content.addView(text("${item.brand} · ${item.date}", 13f, R.color.pt_white, true).apply { setPadding(dp(4), dp(8), dp(4), 0) })
        scroll.addView(content)
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setRootContent(root)
        retry.setOnClickListener { loadNativeOriginal(image, item.path, spinner, status, retry) }
        loadNativeOriginal(image, item.path, spinner, status, retry)
    }

    private fun showNativeShopManagement() {
        currentNativeRoute = "shop-management"
        val root = column(0).apply { setBackgroundResource(R.drawable.bg_screen) }
        val scroll = ScrollView(this)
        val page = column(16).apply { setPadding(screenPadding(), dp(16), screenPadding(), dp(28)) }
        scroll.addView(page)

        val form = column(8).apply {
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundResource(R.drawable.bg_card)
        }
        form.addView(text("添加店铺", 17f, R.color.pt_text, true))
        val nameInput = labeledInput("店铺名称", "", false)
        val urlInput = labeledInput("店铺链接", "", false)
        form.addView(nameInput.first)
        form.addView(urlInput.first)
        val add = Button(this).apply {
            text = "添加店铺"
            isAllCaps = false
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_primary_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
        }
        form.addView(add)
        page.addView(form)

        page.addView(sectionHeading("已配置店铺"))
        val list = column(10)
        page.addView(list)
        var reload: (() -> Unit)? = null

        fun renderShops(shops: JSONArray) {
            list.removeAllViews()
            if (shops.length() == 0) {
                list.addView(text("暂无店铺", 14f, R.color.pt_text_muted, false).apply {
                    setPadding(0, dp(14), 0, dp(14))
                })
                return
            }
            for (index in 0 until shops.length()) {
                val shop = shops.optJSONObject(index) ?: continue
                val name = shop.optString("name")
                val url = shop.optString("url")
                val card = column(5).apply {
                    setPadding(dp(14), dp(12), dp(12), dp(12))
                    setBackgroundResource(R.drawable.bg_card)
                }
                val line = row().apply { gravity = Gravity.CENTER_VERTICAL }
                val copy = column(2)
                copy.addView(text(name, 15f, R.color.pt_text, true))
                copy.addView(text(url, 12f, R.color.pt_text_muted, false))
                line.addView(copy, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                val remove = Button(this).apply {
                    text = "删除"
                    isAllCaps = false
                    textSize = 12f
                    setTextColor(color(R.color.pt_error))
                    setBackgroundResource(R.drawable.bg_outline_button)
                    minHeight = dp(40)
                    minimumHeight = dp(40)
                    minWidth = dp(64)
                    minimumWidth = dp(64)
                    setOnClickListener {
                        AlertDialog.Builder(this@MainActivity)
                            .setTitle("删除店铺")
                            .setMessage("确认删除“$name”？")
                            .setNegativeButton("取消", null)
                            .setPositiveButton("删除") { _, _ ->
                                executor.execute {
                                    val encoded = URLEncoder.encode(name, "UTF-8")
                                    val result = ApiClient.request("DELETE", centerUrl, "$centerUrl/api/screenshot/shops/$encoded", null, cookieHeader(centerUrl))
                                    runOnUiThread {
                                        if (result.code in 200..299) reload?.invoke()
                                        else Toast.makeText(this@MainActivity, result.message(), Toast.LENGTH_SHORT).show()
                                    }
                                }
                            }
                            .show()
                    }
                }
                line.addView(remove, marginStart(8))
                card.addView(line)
                list.addView(card, if (index == 0) {
                    LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                } else {
                    LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) }
                })
            }
        }

        val load: () -> Unit = {
            executor.execute {
                val result = ApiClient.request("GET", centerUrl, "$centerUrl/api/screenshot/shops", null, cookieHeader(centerUrl))
                runOnUiThread {
                    if (result.code in 200..299) {
                        renderShops(JSONObject(result.body).optJSONArray("shops") ?: JSONArray())
                    } else {
                        list.removeAllViews()
                        list.addView(text(result.message(), 14f, R.color.pt_error, false))
                    }
                }
            }
        }
        reload = load
        add.setOnClickListener {
            val name = nameInput.second.text.toString().trim()
            val url = normalizeUrl(urlInput.second.text.toString())
            if (name.isBlank() || url.isBlank()) {
                Toast.makeText(this, "名称和链接不能为空", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            add.isEnabled = false
            executor.execute {
                val body = JSONObject().put("name", name).put("url", url).toString()
                val result = ApiClient.request("POST", centerUrl, "$centerUrl/api/screenshot/shops", body, cookieHeader(centerUrl))
                runOnUiThread {
                    add.isEnabled = true
                    if (result.code in 200..299 && !JSONObject(result.body).has("error")) {
                        nameInput.second.text.clear()
                        urlInput.second.text.clear()
                        load()
                    } else {
                        Toast.makeText(this, result.message(), Toast.LENGTH_SHORT).show()
                    }
                }
            }
        }
        val toolbar = subToolbar("店铺管理", load)
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(browserModuleRail("screenshots"), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setRootContent(root)
        load()
    }

    private fun showNativeScheduler() {
        currentNativeRoute = "scheduler"
        val root = column(0).apply { setBackgroundResource(R.drawable.bg_screen) }
        val scroll = ScrollView(this)
        val page = column(16).apply { setPadding(screenPadding(), dp(16), screenPadding(), dp(28)) }
        scroll.addView(page)
        val card = column(10).apply {
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundResource(R.drawable.bg_card)
        }
        card.addView(text("定时截图", 19f, R.color.pt_text, true))
        val status = text("正在读取定时任务…", 13f, R.color.pt_text_muted, false)
        card.addView(status)
        card.addView(text("选择执行日期", 13f, R.color.pt_text_muted, true))
        val weekdays = listOf("一", "二", "三", "四", "五", "六", "日")
        val checks = weekdays.mapIndexed { index, label ->
            CheckBox(this).apply {
                text = "周$label"
                textSize = 12f
                minWidth = dp(46)
                minHeight = dp(40)
                minimumHeight = dp(40)
                isChecked = true
                tag = index + 1
            }
        }
        val weekScroll = HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false }
        val weekRow = row()
        checks.forEach { weekRow.addView(it) }
        weekScroll.addView(weekRow)
        card.addView(weekScroll)
        val timeRow = row().apply { gravity = Gravity.CENTER_VERTICAL }
        val hourField = labeledInput("小时", "09", false)
        val hourInput = hourField.second.apply { inputType = InputType.TYPE_CLASS_NUMBER; imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_NEXT }
        val minuteField = labeledInput("分钟", "00", false)
        val minuteInput = minuteField.second.apply { inputType = InputType.TYPE_CLASS_NUMBER; imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_DONE }
        val hourBox = hourField.first
        val minuteBox = minuteField.first
        timeRow.addView(hourBox, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        timeRow.addView(minuteBox, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(8) })
        card.addView(timeRow)
        val save = Button(this).apply {
            text = "保存并启用"
            isAllCaps = false
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_primary_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
        }
        val uninstall = Button(this).apply {
            text = "停用定时任务"
            isAllCaps = false
            setTextColor(color(R.color.pt_error))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(40)
            minimumHeight = dp(40)
        }
        card.addView(save)
        card.addView(uninstall)
        page.addView(card)

        val load: () -> Unit = {
            executor.execute {
                val result = ApiClient.request("GET", centerUrl, "$centerUrl/api/screenshot/scheduler/status", null, cookieHeader(centerUrl))
                runOnUiThread {
                    if (result.code !in 200..299) {
                        status.text = result.message()
                        return@runOnUiThread
                    }
                    try {
                        val json = JSONObject(result.body)
                        val schedule = json.optJSONObject("schedule") ?: JSONObject()
                        val selected = schedule.optJSONArray("weekdays") ?: JSONArray()
                        val selectedSet = (0 until selected.length()).map { selected.optInt(it) }.toSet()
                        checks.forEach { it.isChecked = selectedSet.isEmpty() || selectedSet.contains(it.tag as Int) }
                        hourInput.setText(String.format(Locale.CHINA, "%02d", schedule.optInt("hour", 9)))
                        minuteInput.setText(String.format(Locale.CHINA, "%02d", schedule.optInt("minute", 0)))
                        status.text = if (json.optBoolean("enabled")) "已启用 · 按设定时间自动执行" else "未启用"
                    } catch (_: Exception) {
                        status.text = "定时任务数据暂时无法读取"
                    }
                }
            }
        }
        save.setOnClickListener {
            val selected = checks.filter { it.isChecked }.map { it.tag as Int }
            val hour = hourInput.text.toString().toIntOrNull()
            val minute = minuteInput.text.toString().toIntOrNull()
            if (selected.isEmpty() || hour == null || minute == null || hour !in 0..23 || minute !in 0..59) {
                Toast.makeText(this, "请检查日期和时间", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            save.isEnabled = false
            executor.execute {
                val days = JSONArray(); selected.forEach { days.put(it) }
                val body = JSONObject().put("weekdays", days).put("hour", hour).put("minute", minute).put("enabled", true).toString()
                val result = ApiClient.request("POST", centerUrl, "$centerUrl/api/screenshot/scheduler/config", body, cookieHeader(centerUrl))
                runOnUiThread {
                    save.isEnabled = true
                    if (result.code in 200..299) { status.text = "已启用 · 配置已保存" } else Toast.makeText(this, result.message(), Toast.LENGTH_SHORT).show()
                }
            }
        }
        uninstall.setOnClickListener {
            executor.execute {
                val result = ApiClient.request("POST", centerUrl, "$centerUrl/api/screenshot/scheduler/uninstall", "{}", cookieHeader(centerUrl))
                runOnUiThread { if (result.code in 200..299) status.text = "未启用" else Toast.makeText(this, result.message(), Toast.LENGTH_SHORT).show() }
            }
        }
        val toolbar = subToolbar("定时任务", load)
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(browserModuleRail("screenshots"), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setRootContent(root)
        load()
    }

    private fun parseNativeScreenshots(root: JSONObject): List<NativeScreenshot> {
        val result = mutableListOf<NativeScreenshot>()
        val brands = root.optJSONArray("brands") ?: JSONArray()
        for (i in 0 until brands.length()) {
            val brand = brands.optJSONObject(i) ?: continue
            val name = brand.optString("brand")
            val dates = brand.optJSONArray("dates") ?: continue
            for (j in 0 until dates.length()) {
                val date = dates.optJSONObject(j) ?: continue
                val images = date.optJSONArray("images") ?: continue
                for (k in 0 until images.length()) {
                    val image = images.optJSONObject(k) ?: continue
                    result.add(NativeScreenshot(name, date.optString("date"), image.optString("path"), image.optLong("size")))
                }
            }
        }
        return result
    }

    private fun loadNativeThumbnail(view: ImageView, path: String) {
        imageExecutor.execute {
            try {
                val encoded = URLEncoder.encode(path, "UTF-8")
                val bitmap = decodeNativeImageWithRetry(
                    "$centerUrl/api/screenshot/thumb?path=$encoded&size=320",
                    connectTimeoutMs = 8_000,
                    readTimeoutMs = 15_000,
                )
                if (bitmap != null) runOnUiThread { view.setImageBitmap(bitmap) }
            } catch (_: Exception) {
                // The card remains a useful metadata card when a remote thumb is unavailable.
            }
        }
    }

    private fun loadNativeOriginal(view: ImageView, path: String, spinner: ProgressBar, status: TextView, retry: Button) {
        spinner.visibility = View.VISIBLE
        status.visibility = View.VISIBLE
        status.text = "正在加载原图…"
        retry.visibility = View.GONE
        val cached = synchronized(nativeOriginalCache) { nativeOriginalCache.get(path) }
        if (cached != null) {
            view.setImageBitmap(cached)
            spinner.visibility = View.GONE
            status.text = "原图 ${cached.width} × ${cached.height}"
            status.visibility = View.VISIBLE
            return
        }
        imageExecutor.execute {
            val encoded = URLEncoder.encode(path, "UTF-8")
            val originalUrl = "$centerUrl/api/screenshot/file?path=$encoded"
            val previewUrl = "$centerUrl/api/screenshot/preview?path=$encoded&size=${nativePreviewEdge()}"
            // Always try the immutable original first.  The server preview is
            // a compatibility fallback for older centers or an unavailable
            // local original; it must never silently replace the original on
            // a healthy center because text-heavy store screenshots lose
            // detail when they are resized before reaching the phone.
            val originalBitmap = try { decodeNativeOriginalWithRetry(originalUrl, 12_000, 60_000) } catch (_: Exception) { null }
            val bitmap = originalBitmap
                ?: try { decodeNativeImageWithRetry(previewUrl, 10_000, 30_000) } catch (_: Exception) { null }
            runOnUiThread {
                spinner.visibility = View.GONE
                if (bitmap != null) {
                    if (originalBitmap != null) {
                        synchronized(nativeOriginalCache) { nativeOriginalCache.put(path, originalBitmap) }
                    }
                    view.setImageBitmap(bitmap)
                    status.text = if (originalBitmap != null) {
                        "原图 ${bitmap.width} × ${bitmap.height}"
                    } else {
                        "兼容预览 ${bitmap.width} × ${bitmap.height}"
                    }
                    status.visibility = View.VISIBLE
                } else {
                    status.text = "图片加载失败，请重试"
                    retry.visibility = View.VISIBLE
                }
            }
        }
    }

    private fun decodeNativeOriginalWithRetry(url: String, connectTimeoutMs: Int, readTimeoutMs: Int): android.graphics.Bitmap? {
        var result: android.graphics.Bitmap? = null
        repeat(2) { attempt ->
            result = try { decodeNativeOriginal(url, connectTimeoutMs, readTimeoutMs) } catch (_: Exception) { null }
            if (result != null) return result
            if (attempt == 0) {
                try {
                    Thread.sleep(280L)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    return null
                }
            }
        }
        return result
    }

    private fun decodeNativeOriginal(url: String, connectTimeoutMs: Int, readTimeoutMs: Int): android.graphics.Bitmap? {
        // Download once to the app cache, then inspect and decode that same
        // byte stream.  This avoids the old bounds-request + full-download
        // double transfer while still preventing an oversized source from
        // exhausting the phone's heap.
        val temporary = File.createTempFile("pt-original-", ".image", cacheDir)
        val connection = nativeImageConnection(url, connectTimeoutMs, readTimeoutMs)
        try {
            if (connection.responseCode !in 200..299) return null
            val contentLength = connection.contentLengthLong
            if (contentLength > 128L * 1024L * 1024L) return null
            connection.inputStream.buffered().use { input ->
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var total = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > 128L * 1024L * 1024L) return null
                        output.write(buffer, 0, count)
                    }
                }
            }

            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(temporary.absolutePath, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

            val targetWidth = maxOf(resources.displayMetrics.widthPixels * 2, 2048)
            var sample = 1
            while (bounds.outWidth / sample > targetWidth * 2) sample *= 2
            val options = BitmapFactory.Options().apply {
                inSampleSize = sample
                inScaled = false
                inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888
            }
            return BitmapFactory.decodeFile(temporary.absolutePath, options)
        } finally {
            connection.disconnect()
            temporary.delete()
        }
    }

    private fun decodeNativeImage(url: String, connectTimeoutMs: Int, readTimeoutMs: Int): android.graphics.Bitmap? {
        val connection = nativeImageConnection(url, connectTimeoutMs, readTimeoutMs)
        return try {
            if (connection.responseCode !in 200..299) return null
            val options = BitmapFactory.Options().apply {
                // Do not let Android density metadata silently rescale a
                // screenshot before it reaches the ImageView.
                inScaled = false
                inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888
            }
            connection.inputStream.use { BitmapFactory.decodeStream(it, null, options) }
        } finally {
            connection.disconnect()
        }
    }

    private fun nativePreviewEdge(): Int {
        val longestDeviceEdge = maxOf(resources.displayMetrics.widthPixels, resources.displayMetrics.heightPixels)
        // A little headroom avoids upscaling tall screenshots on high-density
        // phones while the server still caps pathological source images.
        return (longestDeviceEdge * 1.35f).toInt().coerceIn(2048, 4096)
    }

    private fun decodeNativeImageWithRetry(url: String, connectTimeoutMs: Int, readTimeoutMs: Int): android.graphics.Bitmap? {
        var result: android.graphics.Bitmap? = null
        repeat(2) { attempt ->
            result = try { decodeNativeImage(url, connectTimeoutMs, readTimeoutMs) } catch (_: Exception) { null }
            if (result != null) return result
            if (attempt == 0) {
                try {
                    Thread.sleep(280L)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    return null
                }
            }
        }
        return result
    }

    private fun nativeImageConnection(url: String, connectTimeoutMs: Int = 20_000, readTimeoutMs: Int = 45_000): HttpURLConnection = (URL(url).openConnection() as HttpURLConnection).apply {
        connectTimeout = connectTimeoutMs
        readTimeout = readTimeoutMs
        useCaches = true
        setRequestProperty("Connection", "keep-alive")
        setRequestProperty("Cookie", cookieHeader(centerUrl).orEmpty())
        setRequestProperty("Accept", "image/png,image/jpeg,image/webp,image/*,*/*;q=0.8")
        setRequestProperty("User-Agent", mobileUserAgent())
    }

    private fun startNativeCapture(status: TextView) {
        status.text = "正在启动截图任务…"
        executor.execute {
            val result = ApiClient.request("POST", centerUrl, "$centerUrl/api/screenshot/capture", "{}", cookieHeader(centerUrl))
            runOnUiThread { status.text = if (result.code in 200..299) "截图任务已启动，可在进度中查看" else result.message() }
        }
    }

    private fun showNativeProgressDialog() {
        val body = column(10).apply { setPadding(dp(20), dp(8), dp(20), dp(4)) }
        val progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 100 }
        val label = text("正在读取任务状态…", 14f, R.color.pt_text_muted, false)
        body.addView(label)
        body.addView(progress)
        val controls = row().apply { gravity = Gravity.CENTER_VERTICAL }
        val pause = Button(this).apply {
            text = "暂停"
            isAllCaps = false
            textSize = 12f
            setTextColor(color(R.color.pt_primary))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(40)
        }
        val stop = Button(this).apply {
            text = "停止"
            isAllCaps = false
            textSize = 12f
            setTextColor(color(R.color.pt_error))
            setBackgroundResource(R.drawable.bg_outline_button)
            minHeight = dp(40)
        }
        controls.addView(pause, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        controls.addView(stop, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(8) })
        body.addView(controls)
        val dialog = AlertDialog.Builder(this).setTitle("截图进度").setView(body).setPositiveButton("关闭", null).create()
        dialog.show()
        fun load() {
            executor.execute {
                val statusResult = ApiClient.request("GET", centerUrl, "$centerUrl/api/screenshot/capture/status", null, cookieHeader(centerUrl))
                val progressResult = ApiClient.request("GET", centerUrl, "$centerUrl/api/screenshot/progress", null, cookieHeader(centerUrl))
                runOnUiThread {
                    try {
                        val status = JSONObject(statusResult.body)
                        val json = JSONObject(progressResult.body)
                        val current = json.optInt("current")
                        val total = json.optInt("total")
                        progress.progress = if (total > 0) (current * 100 / total) else 0
                        val running = status.optBoolean("running")
                        val scheduled = status.optString("source") == "scheduled"
                        label.text = if (running) "$current / $total · ${json.optString("shop", "准备中")}" else "当前没有截图任务"
                        pause.visibility = if (running && !scheduled) View.VISIBLE else View.GONE
                        pause.text = if (status.optBoolean("paused")) "继续" else "暂停"
                    } catch (_: Exception) {
                        label.text = progressResult.message()
                        pause.visibility = View.GONE
                    }
                }
            }
        }
        pause.setOnClickListener {
            pause.isEnabled = false
            executor.execute {
                val status = ApiClient.request("GET", centerUrl, "$centerUrl/api/screenshot/capture/status", null, cookieHeader(centerUrl))
                val paused = runCatching { JSONObject(status.body).optBoolean("paused") }.getOrDefault(false)
                val action = if (paused) "resume" else "pause"
                ApiClient.request("POST", centerUrl, "$centerUrl/api/screenshot/capture/$action", "{}", cookieHeader(centerUrl))
                runOnUiThread { pause.isEnabled = true; load() }
            }
        }
        stop.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("停止截图")
                .setMessage("确认停止当前截图任务？")
                .setNegativeButton("取消", null)
                .setPositiveButton("停止") { _, _ ->
                    executor.execute {
                        val result = ApiClient.request("POST", centerUrl, "$centerUrl/api/screenshot/capture/stop", "{}", cookieHeader(centerUrl))
                        runOnUiThread { if (result.code in 200..299) load() else Toast.makeText(this, result.message(), Toast.LENGTH_SHORT).show() }
                    }
                }
                .show()
        }
        load()
    }

    private fun showAnalysisNative() {
        currentNativeRoute = "analysis"
        val root = column(0).apply { setBackgroundResource(R.drawable.bg_screen) }
        val scroll = ScrollView(this)
        val page = column(16).apply { setPadding(screenPadding(), dp(16), screenPadding(), dp(28)) }
        scroll.addView(page)
        val hero = column(6).apply {
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundResource(R.drawable.bg_card)
        }
        hero.addView(text("首页数据分析", 22f, R.color.pt_text, true))
        page.addView(hero)
        page.addView(sectionHeading("分析周期"))
        val controls = HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false }
        val controlRow = row()
        controls.addView(controlRow)
        page.addView(controls)
        val metrics = column(10)
        page.addView(metrics)
        val open = Button(this).apply {
            text = "打开完整分析工具"
            isAllCaps = false
            textSize = 14f
            setTextColor(Color.WHITE)
            setBackgroundResource(R.drawable.bg_primary_button)
            minHeight = dp(46)
            minimumHeight = dp(46)
            setOnClickListener { showNativeFullAnalysis() }
        }
        page.addView(open, marginTop(4))

        val dimensions = listOf("MTD" to "本月", "QTD" to "本季", "HTD" to "半年", "YTD" to "全年")
        dimensions.forEachIndexed { index, pair ->
            controlRow.addView(nativeFilterButton(pair.second, pair.first == "YTD") {
                loadNativeAnalysis(metrics, pair.first)
            }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { if (index > 0) marginStart = dp(8) })
        }
        val toolbar = subToolbar(getString(R.string.analysis_title), { loadNativeAnalysis(metrics, "YTD") })
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(browserModuleRail("analysis"), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setRootContent(root)
        loadNativeAnalysis(metrics, "YTD")
    }

    private fun loadNativeAnalysis(container: LinearLayout, dimension: String) {
        container.removeAllViews()
        container.addView(text("正在读取数据…", 14f, R.color.pt_text_muted, false))
        executor.execute {
            val result = ApiClient.request("GET", centerUrl, "$centerUrl/api/analysis/panel/overview?dim=$dimension", null, cookieHeader(centerUrl))
            runOnUiThread {
                if (result.code !in 200..299) {
                    container.removeAllViews()
                    container.addView(text(result.message(), 14f, R.color.pt_error, false))
                    return@runOnUiThread
                }
                try {
                    val json = JSONObject(result.body)
                    val period = json.optJSONObject("period") ?: JSONObject()
                    val cards = listOf(
                        "总访客数" to formatNativeNumber(period.optDouble("总访客数", Double.NaN)),
                        "点击率" to formatNativeRate(period.optDouble("点击率", Double.NaN)),
                        "平均停留" to formatNativeSeconds(period.optDouble("平均停留时长", Double.NaN)),
                        "支付转化率" to formatNativeRate(period.optDouble("支付转化率", Double.NaN)),
                    )
                    container.removeAllViews()
                    cards.chunked(if (screenWidthDp() >= 600) 4 else 2).forEachIndexed { rowIndex, rowCards ->
                        val row = row()
                        rowCards.forEach { card -> row.addView(nativeMetricCard(card.first, card.second), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(6) }) }
                        if (rowCards.size == 1) row.addView(Space(this), LinearLayout.LayoutParams(0, 1, 1f).apply { marginStart = dp(6) })
                        container.addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                            bottomMargin = 0
                        })
                    }
                    val note = if (json.optBoolean("has_prev")) "已加载本期与对比期数据" else "当前没有可用对比期"
                    container.addView(text(note, 12f, R.color.pt_text_muted, false).apply { setPadding(0, dp(4), 0, 0) })
                } catch (_: Exception) {
                    container.removeAllViews()
                    container.addView(text("数据暂时无法解析，请打开完整分析工具重试。", 14f, R.color.pt_error, false))
                }
            }
        }
    }

    private fun nativeMetricCard(label: String, value: String): View = column(5).apply {
        setPadding(dp(14), dp(14), dp(12), dp(12))
        setBackgroundResource(R.drawable.bg_card)
        addView(text(label, 12f, R.color.pt_text_muted, true))
        addView(text(value, 22f, R.color.pt_text, true).apply { setPadding(0, dp(5), 0, 0) })
    }

    private fun formatNativeNumber(value: Double): String = if (value.isNaN()) "—" else String.format(Locale.CHINA, "%,.0f", value)

    private fun formatNativeRate(value: Double): String = if (value.isNaN()) "—" else String.format(Locale.CHINA, "%.2f%%", value * 100)

    private fun formatNativeSeconds(value: Double): String = if (value.isNaN()) "—" else String.format(Locale.CHINA, "%.1f s", value)

    private var analysisStatusView: TextView? = null
    private var analysisContentView: LinearLayout? = null
    private var analysisDimension = "YTD"
    private var analysisTab = "overview"
    private val analysisNavigationButtons = mutableSetOf<Button>()

    private fun showNativeFullAnalysis() {
        currentNativeRoute = "analysis-full"
        analysisTab = "overview"
        analysisNavigationButtons.clear()
        val root = column(0).apply { setBackgroundResource(R.drawable.bg_screen) }
        val status = text("正在读取分析数据…", 12f, R.color.pt_text_muted, false)
        analysisStatusView = status
        val scroll = ScrollView(this)
        val page = column(12).apply { setPadding(screenPadding(), dp(12), screenPadding(), dp(28)) }
        val intro = column(4).apply {
            setPadding(dp(16), dp(14), dp(16), dp(14))
            setBackgroundResource(R.drawable.bg_card)
        }
        intro.addView(text("完整数据分析", 21f, R.color.pt_text, true))
        intro.addView(status, marginTop(4))
        page.addView(intro)

        page.addView(sectionHeading("分析周期"))
        val dimensions = listOf("MTD" to "本月", "QTD" to "本季", "HTD" to "半年", "YTD" to "全年")
        val dimensionButtons = mutableListOf<Button>()
        val dimensionGrid = column(8)
        var dimensionRow = row()
        val dimensionColumns = if (screenWidthDp() >= 600) 4 else 2
        dimensions.forEachIndexed { index, pair ->
            val (value, label) = pair
            val button = nativeAnalysisButton(label, value == analysisDimension) {
                analysisDimension = value
                dimensionButtons.forEach { setNativeAnalysisButtonSelected(it, it.tag == value) }
                loadNativeAnalysisTab()
            }
            button.tag = value
            dimensionButtons.add(button)
            dimensionRow.addView(button, LinearLayout.LayoutParams(0, dp(40), 1f).apply {
                if (index % dimensionColumns != dimensionColumns - 1) marginEnd = dp(8)
            })
            if (index % dimensionColumns == dimensionColumns - 1 || index == dimensions.lastIndex) {
                dimensionGrid.addView(dimensionRow, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = 0
                })
                if (index != dimensions.lastIndex) dimensionRow = row()
            }
        }
        page.addView(dimensionGrid, marginTop(8))

        val tabs = listOf("overview" to "总览", "blocks" to "板块深度", "newold" to "新老客", "campaign" to "活动日常", "anomaly" to "趋势异常", "manage" to "数据管理")
        val tabButtons = mutableListOf<Button>()
        val tabBarRow = row().apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(8), dp(4), dp(8), dp(4)) }
        tabs.forEach { pair ->
            val (value, label) = pair
            val button = nativeAnalysisButton(label, value == analysisTab) {
                analysisTab = value
                tabButtons.forEach { setNativeAnalysisButtonSelected(it, it.tag == value) }
                loadNativeAnalysisTab()
            }
            button.tag = value
            tabButtons.add(button)
            analysisNavigationButtons.add(button)
            setNativeAnalysisButtonSelected(button, value == analysisTab)
            tabBarRow.addView(button, LinearLayout.LayoutParams(dp(56), dp(40)).apply { marginEnd = dp(8) })
        }
        val content = column(10)
        analysisContentView = content
        page.addView(content, marginTop(24))
        scroll.addView(page)
        val toolbar = subToolbar(getString(R.string.analysis_title), { loadNativeAnalysisTab() })
        root.addView(toolbar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(browserModuleRail("analysis-full"), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(56)))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        val tabBar = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            setBackgroundResource(R.drawable.bg_card)
            elevation = dp(4).toFloat()
            addView(tabBarRow)
        }
        root.addView(tabBar, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)))
        setRootContent(root)
        loadNativeAnalysisTab()
    }

    private fun nativeAnalysisButton(label: String, selected: Boolean, action: () -> Unit): Button = Button(this).apply {
        text = label
        isAllCaps = false
        textSize = 12f
        gravity = Gravity.CENTER
        includeFontPadding = false
        setSingleLine(true)
        ellipsize = null
        minHeight = 0
        minimumHeight = dp(40)
        minWidth = 0
        minimumWidth = 0
        setPadding(0, 0, 0, 0)
        stateListAnimator = null
        setOnClickListener { action() }
        setNativeAnalysisButtonSelected(this, selected)
    }

    private fun setNativeAnalysisButtonSelected(button: Button, selected: Boolean) {
        button.isSelected = selected
        button.setTextColor(if (selected) Color.WHITE else color(R.color.pt_text_muted))
        button.setBackgroundResource(if (selected) R.drawable.bg_chip_selected else if (analysisNavigationButtons.contains(button)) R.drawable.bg_tab_unselected else R.drawable.bg_chip_outline)
    }

    private fun loadNativeAnalysisTab() {
        val content = analysisContentView ?: return
        val status = analysisStatusView
        content.removeAllViews()
        content.addView(text("正在加载 ${analysisTabLabel()}…", 13f, R.color.pt_text_muted, false))
        status?.text = "正在读取中心数据…"
        val encodedDim = URLEncoder.encode(analysisDimension, "UTF-8")
        val endpoint = when (analysisTab) {
            "blocks" -> "/api/analysis/panel/blocks?dim=$encodedDim"
            "newold" -> "/api/analysis/panel/newold?dim=$encodedDim"
            "anomaly" -> "/api/analysis/panel/anomaly?dim=$encodedDim&metric=%E6%80%BB%E8%AE%BF%E5%AE%A2%E6%95%B0"
            "manage" -> "/api/analysis/files"
            else -> "/api/analysis/panel/overview?dim=$encodedDim"
        }
        executor.execute {
            val result = ApiClient.request("GET", centerUrl, "$centerUrl$endpoint", null, cookieHeader(centerUrl))
            runOnUiThread {
                if (result.code !in 200..299) {
                    content.removeAllViews()
                    content.addView(text(result.message(), 14f, R.color.pt_error, false))
                    status?.text = "加载失败"
                    return@runOnUiThread
                }
                try {
                    val json = JSONObject(result.body)
                    if (!json.optBoolean("ok", true)) throw IllegalStateException(json.optString("error", "暂无数据"))
                    content.removeAllViews()
                    when (analysisTab) {
                        "blocks" -> renderNativeAnalysisBlocks(content, json)
                        "newold" -> renderNativeAnalysisNewOld(content, json)
                        "anomaly" -> renderNativeAnalysisAnomaly(content, json)
                        "manage" -> renderNativeAnalysisFiles(content, json)
                        else -> renderNativeAnalysisOverview(content, json, analysisTab == "campaign")
                    }
                    status?.text = "数据已更新 · ${analysisTabLabel()}"
                } catch (error: Exception) {
                    content.removeAllViews()
                    content.addView(text(error.message ?: "数据暂时无法解析", 14f, R.color.pt_error, false))
                    status?.text = "数据解析失败"
                }
            }
        }
    }

    private fun analysisTabLabel(): String = when (analysisTab) {
        "blocks" -> "板块深度"
        "newold" -> "新老客对比"
        "campaign" -> "活动日常"
        "anomaly" -> "趋势异常"
        "manage" -> "数据管理"
        else -> "总览"
    }

    private fun analysisSection(parent: LinearLayout, title: String, subtitle: String? = null): LinearLayout {
        val card = column(6).apply {
            setPadding(dp(14), dp(12), dp(14), dp(12))
            setBackgroundResource(R.drawable.bg_card)
        }
        card.addView(text(title, 15f, R.color.pt_text, true))
        if (!subtitle.isNullOrBlank()) card.addView(text(subtitle, 12f, R.color.pt_text_muted, false))
        parent.addView(card, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            if (parent.childCount > 0) topMargin = dp(8)
        })
        return card
    }

    private fun analysisValue(value: JSONObject?, key: String): String = when {
        value == null || !value.has(key) || value.isNull(key) -> "—"
        key.contains("率") -> formatNativeRate(value.optDouble(key, Double.NaN))
        key.contains("停留") -> formatNativeSeconds(value.optDouble(key, Double.NaN))
        else -> formatNativeNumber(value.optDouble(key, Double.NaN))
    }

    private fun renderNativeAnalysisOverview(parent: LinearLayout, json: JSONObject, campaignOnly: Boolean) {
        val period = json.optJSONObject("period") ?: JSONObject()
        val previous = json.optJSONObject("period_prev")
        val cards = listOf("总访客数", "点击率", "平均停留时长", "支付转化率")
        val metrics = column(8)
        cards.chunked(if (screenWidthDp() >= 600) 4 else 2).forEachIndexed { rowIndex, group ->
            val line = row()
            group.forEach { key ->
                line.addView(nativeMetricCard(key, analysisValue(period, key)), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(6) })
            }
            if (group.size == 1) line.addView(Space(this), LinearLayout.LayoutParams(0, 1, 1f))
            metrics.addView(line, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        parent.addView(metrics)
        val compare = analysisSection(parent, "本期 vs 对比期", if (previous == null) "暂无对比数据" else "同口径对比 · ${json.optString("prev_start")} 至 ${json.optString("prev_end")}")
        cards.forEach { key ->
            compare.addView(comparisonRow(key, comparisonValue(period, previous, key)))
        }
        val trend = json.optJSONArray("trend") ?: JSONArray()
        val title = if (campaignOnly) "活动日明细" else "周期每日趋势"
        val card = analysisSection(parent, title, "本期实线 · 对比期虚线")
        card.addView(nativeTrendChart(trend, json.optJSONArray("trend_prev")))
        val rows = column(4)
        card.addView(analysisLine("日期 / 活动", "访客 · 点击率 · 支付转化", true))
        for (i in 0 until trend.length()) {
            if (i >= 20) break
            val item = trend.optJSONObject(i) ?: continue
            if (campaignOnly && item.optBoolean("is_routine", false)) continue
            rows.addView(analysisLine(
                "${item.optString("date")}  ${item.optString("activity").replace("\n", " ").take(16)}",
                "${analysisValue(item, "总访客数")} · ${analysisValue(item, "点击率")} · ${analysisValue(item, "支付转化率")}",
                false,
            ))
        }
        card.addView(rows)
        val split = analysisSection(parent, "活动期与日常期")
        val campaign = json.optJSONObject("campaign")
        val routine = json.optJSONObject("routine")
        split.addView(analysisLine("活动期", "访客 ${analysisValue(campaign, "总访客数")} · 日均 ${analysisValue(campaign, "日均访客数")} · 点击率 ${analysisValue(campaign, "点击率")}", false))
        split.addView(analysisLine("日常期", "访客 ${analysisValue(routine, "总访客数")} · 日均 ${analysisValue(routine, "日均访客数")} · 点击率 ${analysisValue(routine, "点击率")}", false))
    }

    private fun comparisonValue(current: JSONObject, previous: JSONObject?, key: String): String {
        if (previous == null) return "本期 ${analysisValue(current, key)} · 对比期 —"
        val currentValue = current.optDouble(key, Double.NaN)
        val previousValue = previous.optDouble(key, Double.NaN)
        if (currentValue.isNaN() || previousValue.isNaN()) return "本期 ${analysisValue(current, key)} · 对比期 —"
        val delta = currentValue - previousValue
        val deltaText = when {
            key.contains("率") -> formatNativeRate(delta)
            key.contains("停留") -> formatNativeSeconds(delta)
            else -> formatNativeNumber(delta)
        }
        val sign = if (delta > 0) "+" else ""
        return "本期 ${analysisValue(current, key)} · 对比期 ${analysisValue(previous, key)} · 变化 $sign$deltaText"
    }

    private fun comparisonRow(label: String, value: String): View = column(4).apply {
        addView(text(label, 12f, R.color.pt_text_muted, true))
        addView(text(value, 13f, R.color.pt_text, false), marginTop(8))
    }

    private fun nativeTrendChart(current: JSONArray, previous: JSONArray?): View = object : View(this) {
        private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = dp(2).toFloat()
            strokeCap = Paint.Cap.ROUND
            strokeJoin = Paint.Join.ROUND
        }
        private val grid = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = dp(1).toFloat()
            color = color(R.color.pt_border)
        }
        private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            textSize = dp(11).toFloat()
            color = color(R.color.pt_text_muted)
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val left = dp(8).toFloat()
            val right = width - dp(8).toFloat()
            val top = dp(18).toFloat()
            val bottom = height - dp(24).toFloat()
            val plotWidth = right - left
            val plotHeight = bottom - top
            if (plotWidth <= 0 || plotHeight <= 0) return
            for (i in 0..3) {
                val y = top + plotHeight * i / 3f
                canvas.drawLine(left, y, right, y, grid)
            }
            val currentValues = chartValues(current)
            val previousValues = chartValues(previous)
            val all = (currentValues + previousValues).filterNotNull()
            if (all.isEmpty()) {
                label.textSize = dp(13).toFloat()
                canvas.drawText("暂无趋势数据", left, top + plotHeight / 2f, label)
                return
            }
            val maxValue = maxOf(1.0, all.maxOrNull() ?: 1.0)
            drawSeries(canvas, currentValues, maxValue, left, top, plotWidth, plotHeight, color(R.color.pt_primary), false)
            drawSeries(canvas, previousValues, maxValue, left, top, plotWidth, plotHeight, color(R.color.pt_accent), true)
            label.textSize = dp(10).toFloat()
            canvas.drawText("本期", left, height - dp(8).toFloat(), label)
            label.color = color(R.color.pt_accent)
            canvas.drawText("对比期", left + dp(48).toFloat(), height - dp(8).toFloat(), label)
            label.color = color(R.color.pt_text_muted)
        }

        private fun chartValues(array: JSONArray?): List<Double?> {
            if (array == null) return emptyList()
            return (0 until array.length()).map { index ->
                val item = array.optJSONObject(index) ?: return@map null
                if (!item.has("总访客数") || item.isNull("总访客数")) null else item.optDouble("总访客数", Double.NaN).takeUnless { it.isNaN() }
            }
        }

        private fun drawSeries(canvas: Canvas, values: List<Double?>, max: Double, left: Float, top: Float, plotWidth: Float, plotHeight: Float, colorValue: Int, dashed: Boolean) {
            if (values.size < 2) return
            line.color = colorValue
            line.pathEffect = if (dashed) android.graphics.DashPathEffect(floatArrayOf(dp(6).toFloat(), dp(6).toFloat()), 0f) else null
            val path = Path()
            var started = false
            values.forEachIndexed { index, value ->
                if (value == null) {
                    started = false
                    return@forEachIndexed
                }
                val x = left + plotWidth * index / (values.size - 1).toFloat()
                val y = top + plotHeight * (1f - (value / max).toFloat())
                if (!started) {
                    path.moveTo(x, y)
                    started = true
                } else {
                    path.lineTo(x, y)
                }
            }
            canvas.drawPath(path, line)
        }
    }.apply {
        setBackgroundResource(R.drawable.bg_screen)
        minimumHeight = dp(168)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(168)).apply { topMargin = dp(8) }
        contentDescription = "趋势图"
    }

    private fun renderNativeAnalysisBlocks(parent: LinearLayout, json: JSONObject) {
        val card = analysisSection(parent, "板块表现")
        val names = json.optJSONArray("板块") ?: JSONArray()
        val avgs = json.optJSONArray("板块均值") ?: JSONArray()
        val days = json.optJSONArray("出现天数") ?: JSONArray()
        card.addView(analysisLine("板块", "平均值 · 出现天数", true))
        for (i in 0 until minOf(names.length(), 40)) {
            card.addView(analysisLine(names.optString(i), "${formatNativeRate(avgs.optDouble(i, Double.NaN))} · ${days.optInt(i)} 天", false))
        }
        val ranking = analysisSection(parent, "Top / Bottom")
        ranking.addView(analysisLine("Top 5", jsonArrayPairs(json.optJSONArray("top5")), false))
        ranking.addView(analysisLine("Bottom 5", jsonArrayPairs(json.optJSONArray("bottom5")), false))
        val warnings = json.optJSONArray("警示板块") ?: JSONArray()
        ranking.addView(analysisLine("需要关注", if (warnings.length() == 0) "暂无" else (0 until minOf(warnings.length(), 12)).joinToString("、") { warnings.optString(it) }, false))
    }

    private fun renderNativeAnalysisNewOld(parent: LinearLayout, json: JSONObject) {
        val days = json.optInt("days", 0)
        parent.addView(text("有效数据天数：$days 天", 13f, R.color.pt_text_muted, false))
        listOf("新客", "老客").forEach { group ->
            val values = json.optJSONObject(group) ?: JSONObject()
            val card = analysisSection(parent, group)
            card.addView(analysisLine("日均访客", analysisValue(values, "日均访客数"), false))
            card.addView(analysisLine("点击率", analysisValue(values, "点击率"), false))
            card.addView(analysisLine("平均停留", analysisValue(values, "平均停留时长"), false))
            card.addView(analysisLine("支付转化率", analysisValue(values, "支付转化率"), false))
            val funnel = json.optJSONObject("${group}漏斗")
            card.addView(analysisLine("转化漏斗", "访客 ${analysisValue(funnel, "访客")} → 点击 ${analysisValue(funnel, "点击")} → 支付 ${analysisValue(funnel, "支付")}", false))
        }
    }

    private fun renderNativeAnalysisAnomaly(parent: LinearLayout, json: JSONObject) {
        val summary = analysisSection(parent, "异常概览")
        summary.addView(analysisLine("检测指标", json.optString("metric", "总访客数"), false))
        summary.addView(analysisLine("平均值 / 标准差", "${formatNativeNumber(json.optDouble("mean", Double.NaN))} / ${formatNativeNumber(json.optDouble("std", Double.NaN))}", false))
        val anomalies = json.optJSONArray("anomalies") ?: JSONArray()
        summary.addView(analysisLine("异常数量", "${anomalies.length()} 个", false))
        val list = analysisSection(parent, "异常明细")
        if (anomalies.length() == 0) {
            list.addView(text("当前周期没有明显异常。", 13f, R.color.pt_success, false))
        } else {
            for (i in 0 until minOf(anomalies.length(), 50)) {
                val item = anomalies.optJSONObject(i) ?: continue
                val line = row().apply { gravity = Gravity.CENTER_VERTICAL }
                val copy = column(2)
                copy.addView(text("${item.optString("date")} · ${item.optString("group", item.optString("activity"))}", 12f, R.color.pt_text, true))
                copy.addView(text("${formatNativeNumber(item.optDouble("value", Double.NaN))} · ${jsonArrayStrings(item.optJSONArray("reasons"))}", 11f, R.color.pt_error, false))
                line.addView(copy, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                val fix = Button(this).apply {
                    text = "修正"
                    isAllCaps = false
                    textSize = 11f
                    minHeight = dp(36)
                    minimumHeight = dp(36)
                    setPadding(dp(8), 0, dp(8), 0)
                    setTextColor(color(R.color.pt_primary))
                    setBackgroundResource(R.drawable.bg_outline_button)
                    setOnClickListener { showNativeFixDialog(item.optString("date"), "访客数") }
                }
                line.addView(fix, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40)).apply { marginStart = dp(6) })
                list.addView(line)
            }
        }
        loadNativeFixes(parent)
    }

    private fun loadNativeFixes(parent: LinearLayout) {
        executor.execute {
            val result = ApiClient.request("GET", centerUrl, "$centerUrl/api/analysis/panel/anomaly/fixes", null, cookieHeader(centerUrl))
            if (result.code !in 200..299) return@execute
            runOnUiThread {
                val fixes = runCatching { JSONObject(result.body).optJSONArray("fixes") }.getOrNull() ?: return@runOnUiThread
                val card = analysisSection(parent, "已保存的修正")
                if (fixes.length() == 0) card.addView(text("暂无修正记录。", 12f, R.color.pt_text_muted, false))
                for (i in 0 until fixes.length()) {
                    val item = fixes.optJSONObject(i) ?: continue
                    val line = row().apply { gravity = Gravity.CENTER_VERTICAL }
                    line.addView(text("${item.optString("date")} · ${item.optString("field")} · ${item.optDouble("delta", 0.0)}", 12f, R.color.pt_text, false), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                    val reset = Button(this).apply {
                        text = "重置"
                        isAllCaps = false
                        textSize = 11f
                        minHeight = dp(36); minimumHeight = dp(36)
                        setTextColor(color(R.color.pt_error)); setBackgroundResource(R.drawable.bg_outline_button)
                        setOnClickListener {
                            executor.execute {
                                ApiClient.request("DELETE", centerUrl, "$centerUrl/api/analysis/panel/anomaly/fix?date=${URLEncoder.encode(item.optString("date"), "UTF-8")}&field=${URLEncoder.encode(item.optString("field"), "UTF-8")}", null, cookieHeader(centerUrl))
                                runOnUiThread { loadNativeAnalysisTab() }
                            }
                        }
                    }
                    line.addView(reset, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40)).apply { marginStart = dp(6) })
                    card.addView(line)
                }
            }
        }
    }

    private fun showNativeFixDialog(date: String, field: String) {
        val input = EditText(this).apply {
            hint = "正数增加，负数减少"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_SIGNED or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }
        AlertDialog.Builder(this).setTitle("修正 $date · $field").setView(input)
            .setNegativeButton("取消", null).setPositiveButton("保存") { _, _ ->
                val delta = input.text.toString().toDoubleOrNull() ?: return@setPositiveButton
                executor.execute {
                    val body = JSONObject().put("date", date).put("field", field).put("delta", delta).toString()
                    ApiClient.request("POST", centerUrl, "$centerUrl/api/analysis/panel/anomaly/fix", body, cookieHeader(centerUrl))
                    runOnUiThread { loadNativeAnalysisTab() }
                }
            }.show()
    }

    private fun uploadNativeAnalysisFile(uri: Uri) {
        val status = analysisStatusView
        status?.text = "正在上传并解析 Excel…"
        analysisContentView?.removeAllViews()
        analysisContentView?.addView(text("正在上传并解析，文件较大时需要一点时间…", 13f, R.color.pt_text_muted, false))
        executor.execute {
            val result = try {
                val stream = contentResolver.openInputStream(uri)
                    ?: throw IllegalStateException("无法读取所选文件")
                stream.use { ApiClient.uploadMultipart(centerUrl, "$centerUrl/api/analysis/upload", cookieHeader(centerUrl), "analysis.xlsx", it) }
            } catch (error: Exception) {
                ApiResult(0, "", networkMessage = error.message ?: "文件读取失败")
            }
            runOnUiThread {
                if (result.code in 200..299) {
                    status?.text = "上传完成 · 正在刷新分析数据"
                    loadNativeAnalysisTab()
                } else {
                    status?.text = result.message()
                    analysisContentView?.removeAllViews()
                    analysisContentView?.addView(text(result.message(), 14f, R.color.pt_error, false))
                }
            }
        }
    }

    private fun renderNativeAnalysisFiles(parent: LinearLayout, json: JSONObject) {
        val upload = Button(this).apply {
            text = "选择 XLSX 文件并上传"
            isAllCaps = false
            textSize = 13f
            minHeight = dp(44); minimumHeight = dp(44)
            setTextColor(Color.WHITE); setBackgroundResource(R.drawable.bg_primary_button)
            setOnClickListener {
                val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                    type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    addCategory(Intent.CATEGORY_OPENABLE)
                }
                startActivityForResult(intent, analysisFileRequestCode)
            }
        }
        parent.addView(upload)
        val files = json.optJSONArray("files") ?: JSONArray()
        val card = analysisSection(parent, "已上传文件")
        if (files.length() == 0) card.addView(text("尚未上传数据。", 13f, R.color.pt_text_muted, false))
        for (i in 0 until files.length()) {
            val item = files.optJSONObject(i) ?: continue
            val line = row().apply { gravity = Gravity.CENTER_VERTICAL }
            val copy = column(2)
            copy.addView(text("${item.optString("year")} 年 · ${item.optString("file", "未命名")}", 12f, R.color.pt_text, true))
            copy.addView(text("${item.optInt("db_rows", 0)} 行入库 · ${item.optJSONObject("sheets")?.length() ?: 0} 张表", 11f, R.color.pt_text_muted, false))
            line.addView(copy, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            val delete = Button(this).apply {
                text = "删除"; isAllCaps = false; textSize = 11f; minHeight = dp(36); minimumHeight = dp(36)
                setTextColor(color(R.color.pt_error)); setBackgroundResource(R.drawable.bg_outline_button)
                setOnClickListener {
                    AlertDialog.Builder(this@MainActivity).setTitle("删除 ${item.optString("year")} 年数据？").setMessage("此操作不可恢复。")
                        .setNegativeButton("取消", null).setPositiveButton("删除") { _, _ ->
                            executor.execute {
                                ApiClient.request("DELETE", centerUrl, "$centerUrl/api/analysis/file?year=${item.optString("year")}", null, cookieHeader(centerUrl))
                                runOnUiThread { loadNativeAnalysisTab() }
                            }
                        }.show()
                }
            }
            line.addView(delete, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(40)).apply { marginStart = dp(6) })
            card.addView(line)
        }
    }

    private fun analysisLine(left: String, right: String, header: Boolean): View = row().apply {
        gravity = Gravity.CENTER_VERTICAL
        setPadding(0, dp(if (header) 3 else 7), 0, dp(if (header) 3 else 7))
        addView(text(left, if (header) 11f else 12f, if (header) R.color.pt_text_muted else R.color.pt_text, header), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        addView(text(right, if (header) 11f else 12f, R.color.pt_text_muted, header))
    }

    private fun jsonArrayStrings(array: JSONArray?): String = if (array == null || array.length() == 0) "—" else (0 until minOf(array.length(), 3)).joinToString("；") { array.optString(it) }

    private fun jsonArrayPairs(array: JSONArray?): String = if (array == null || array.length() == 0) "暂无" else (0 until minOf(array.length(), 5)).joinToString("、") { val item = array.optJSONObject(it); "${item?.optString("name")} ${formatNativeRate(item?.optDouble("value", Double.NaN) ?: Double.NaN)}" }

    private fun mobileUserAgent(): String = "Mozilla/5.0 (Linux; Android ${Build.VERSION.RELEASE}; Mobile) PracticalToolsMobile/${BuildConfig.VERSION_NAME}"

    private fun confirmLogout() {
        AlertDialog.Builder(this)
            .setTitle("退出登录")
            .setMessage("退出后会回到登录页，应用不会关闭。")
            .setNegativeButton("取消", null)
            .setPositiveButton("退出") { _, _ -> logout() }
            .show()
    }

    private fun logout() {
        if (logoutInProgress) return
        logoutInProgress = true
        showLoading("正在退出登录…")
        executor.execute {
            ApiClient.request("POST", centerUrl, "$centerUrl/api/auth/logout", "{}", cookieHeader(centerUrl))
            runOnUiThread {
                CookieManager.getInstance().removeAllCookies {
                    CookieManager.getInstance().flush()
                    runOnUiThread {
                        logoutInProgress = false
                        displayName = ""
                        showLogin("已退出登录")
                    }
                }
            }
        }
    }

    private fun showLoading(message: String) {
        currentNativeRoute = "loading"
        val page = column(12).apply {
            gravity = Gravity.CENTER
            setBackgroundResource(R.drawable.bg_screen)
        }
        page.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_app_logo)
            contentDescription = getString(R.string.app_name)
            layoutParams = LinearLayout.LayoutParams(dp(64), dp(64))
        })
        page.addView(text(message, 15f, R.color.pt_text_muted, false).apply {
            gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(16)
        })
        setRootContent(page)
    }

    private fun homeCard(iconRes: Int, title: String, subtitle: String? = null, action: () -> Unit): View {
        val card = column(8).apply {
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundResource(R.drawable.bg_card)
            isClickable = true
            isFocusable = true
            minimumHeight = dp(128)
            contentDescription = title
        }
        val iconTile = FrameLayout(this).apply {
            setBackgroundResource(R.drawable.bg_icon_tile)
            layoutParams = LinearLayout.LayoutParams(dp(56), dp(56))
        }
        val icon = ImageView(this).apply {
            setImageResource(iconRes)
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            setPadding(dp(12), dp(12), dp(12), dp(12))
            setColorFilter(Color.WHITE)
            layoutParams = FrameLayout.LayoutParams(dp(56), dp(56))
        }
        iconTile.addView(icon)
        val copy = column(8).apply { gravity = Gravity.CENTER_HORIZONTAL }
        copy.addView(text(title, 15f, R.color.pt_text, true).apply {
            gravity = Gravity.CENTER
            textAlignment = View.TEXT_ALIGNMENT_CENTER
            includeFontPadding = false
        })
        if (!subtitle.isNullOrBlank()) copy.addView(text(subtitle, 12f, R.color.pt_text_muted, false).apply {
            gravity = Gravity.CENTER
            textAlignment = View.TEXT_ALIGNMENT_CENTER
            includeFontPadding = false
        })
        card.addView(iconTile)
        card.addView(copy, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        card.setOnClickListener { action() }
        return card
    }

    private fun labeledInput(label: String, value: String, password: Boolean): Pair<View, EditText> {
        val box = column(8)
        box.addView(text(label, 12f, R.color.pt_text_muted, true))
        val input = EditText(this).apply {
            setText(value)
            textSize = 16f
            setTextColor(color(R.color.pt_text))
            setHintTextColor(color(R.color.pt_text_muted))
            setBackgroundResource(R.drawable.bg_input)
            minimumHeight = dp(48)
            isSingleLine = true
            if (password) {
                inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_DONE
            } else {
                inputType = InputType.TYPE_CLASS_TEXT
            }
        }
        box.addView(input, marginTop(8))
        return box to input
    }

    private fun column(gap: Int): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        if (gap > 0) {
            // The original layout API accepted a gap value but silently
            // ignored it. Use a transparent divider so every vertical stack
            // gets real breathing room without adding border noise. The
            // shared dp() resolver keeps it on the 8dp grid.
            setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE)
            setDividerDrawable(GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                setSize(1, dp(responsiveStackGap(gap)))
            })
        }
    }

    private fun row(): LinearLayout = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }

    private fun text(value: String, size: Float, colorRes: Int, bold: Boolean): TextView = TextView(this).apply {
        text = value
        textSize = size
        setTextColor(color(colorRes))
        typeface = if (bold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
        includeFontPadding = false
    }

    private fun sectionHeading(title: String, subtitle: String? = null): View = column(8).apply {
        addView(text(title, 16f, R.color.pt_text, true))
        if (!subtitle.isNullOrBlank()) addView(text(subtitle, 11f, R.color.pt_text_muted, false))
    }

    private fun screenWidthDp(): Int = (resources.displayMetrics.widthPixels / resources.displayMetrics.density).toInt()

    private fun screenHeightDp(): Int = (resources.displayMetrics.heightPixels / resources.displayMetrics.density).toInt()

    /** Keep module separation visible while keeping controls compact. */
    private fun responsiveStackGap(requested: Int): Int {
        if (requested <= 0) return 0
        val compactSurface = screenWidthDp() < 360 || screenHeightDp() < 640
        val largeSurface = screenWidthDp() >= 600 || resources.configuration.fontScale >= 1.2f

        // 16dp is reserved for separation between modules. Everything inside
        // a module stays at one compact 8dp rhythm. This prevents a divider
        // and an explicit child margin from creating a doubled gap.
        val moduleGap = requested >= 16
        return when {
            compactSurface -> 8
            largeSurface && moduleGap -> 16
            moduleGap -> 16
            else -> 8
        }
    }

    private fun screenPadding(): Int = when {
        screenWidthDp() >= 840 -> dp(32)
        screenWidthDp() >= 600 -> dp(24)
        else -> dp(16)
    }

    private fun marginTop(value: Int): LinearLayout.LayoutParams = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    ).apply { topMargin = dp(value) }

    private fun marginStart(value: Int): LinearLayout.LayoutParams = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.WRAP_CONTENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    ).apply { marginStart = dp(value) }

    private fun color(resId: Int): Int = getColor(resId)

    /** Resolve layout dimensions to the project's 8dp grid. */
    private fun dp(value: Int): Int {
        val gridValue = if (value <= 0) 0 else ((value + 7) / 8) * 8
        return (gridValue * resources.displayMetrics.density).toInt()
    }

    private fun normalizeUrl(value: String): String = value.trim().trimEnd('/')

    private fun cookieHeader(base: String): String? = CookieManager.getInstance().getCookie(base)

    private fun acceptCookies(base: String, cookies: List<String>) {
        val manager = CookieManager.getInstance()
        cookies.forEach { cookie -> manager.setCookie(base, cookie.substringBefore(';')) }
        manager.flush()
    }

    private fun jsonValue(json: String, key: String): String {
        val match = Regex("\\\"${Regex.escape(key)}\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"").find(json)
        return match?.groupValues?.getOrNull(1).orEmpty()
    }

    private data class ApiResult(
        val code: Int,
        val body: String,
        val cookies: List<String> = emptyList(),
        val networkMessage: String = "",
    ) {
        fun message(): String {
            val detail = Regex("\\\"(?:detail|error)\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"").find(body)?.groupValues?.getOrNull(1)
            return detail ?: when (code) {
                0 -> networkMessage.ifBlank { "无法连接中心服务，请检查地址和网络" }
                401 -> "账号或密码错误"
                404 -> "中心服务地址不正确"
                else -> "请求失败（$code）"
            }
        }
    }

    private object ApiClient {
        fun login(base: String, username: String, password: String): ApiResult = request(
            "POST",
            base,
            "$base/api/auth/login",
            "{\"username\":\"${escape(username)}\",\"password\":\"${escape(password)}\"}",
            null,
        )

        fun request(method: String, base: String, url: String, body: String?, cookie: String?): ApiResult {
            val safeToRetry = method.equals("GET", ignoreCase = true) || method.equals("HEAD", ignoreCase = true)
            var result = requestOnce(method, url, body, cookie)
            if (safeToRetry && isTransient(result)) {
                try {
                    Thread.sleep(260L)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    return result
                }
                result = requestOnce(method, url, body, cookie)
            }
            return result
        }

        private fun requestOnce(method: String, url: String, body: String?, cookie: String?): ApiResult {
            var connection: HttpURLConnection? = null
            return try {
                connection = (URL(url).openConnection() as HttpURLConnection).apply {
                    requestMethod = method
                    connectTimeout = 12_000
                    readTimeout = 22_000
                    useCaches = true
                    instanceFollowRedirects = true
                    setRequestProperty("Connection", "keep-alive")
                    setRequestProperty("User-Agent", "PracticalToolsMobile/${BuildConfig.VERSION_NAME} (Android)")
                    setRequestProperty("Accept", "application/json, text/plain, */*")
                    // The center enables GZipMiddleware for larger JSON
                    // responses. Browser stacks negotiate this automatically;
                    // the native client must opt in explicitly.
                    setRequestProperty("Accept-Encoding", "gzip")
                    setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9")
                    if (!cookie.isNullOrBlank()) setRequestProperty("Cookie", cookie)
                    if (body != null) {
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                        outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
                    }
                }
                val code = connection.responseCode
                val content = readResponse(connection, code)
                val cookies = connection.headerFields.entries
                    .filter { it.key?.equals("Set-Cookie", ignoreCase = true) == true }
                    .flatMap { it.value.orEmpty() }
                ApiResult(code, content, cookies)
            } catch (error: Exception) {
                ApiResult(0, "", networkMessage = networkMessage(error))
            } finally {
                connection?.disconnect()
            }
        }

        private fun isTransient(result: ApiResult): Boolean = result.code == 0 || result.code in 502..504

        fun uploadMultipart(base: String, url: String, cookie: String?, filename: String, input: java.io.InputStream): ApiResult {
            return try {
                val boundary = "----PracticalTools${System.currentTimeMillis()}"
                val connection = (URL(url).openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 25_000
                    readTimeout = 120_000
                    doOutput = true
                    useCaches = true
                    setRequestProperty("Connection", "keep-alive")
                    setRequestProperty("User-Agent", "PracticalToolsMobile/${BuildConfig.VERSION_NAME} (Android)")
                    setRequestProperty("Accept", "application/json, text/plain, */*")
                    setRequestProperty("Accept-Encoding", "gzip")
                    setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
                    if (!cookie.isNullOrBlank()) setRequestProperty("Cookie", cookie)
                }
                connection.outputStream.use { output ->
                    output.write("--$boundary\r\n".toByteArray())
                    output.write("Content-Disposition: form-data; name=\"file\"; filename=\"$filename\"\r\n".toByteArray())
                    output.write("Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n".toByteArray())
                    input.copyTo(output, 1024 * 1024)
                    output.write("\r\n--$boundary--\r\n".toByteArray())
                }
                val code = connection.responseCode
                val content = readResponse(connection, code)
                val cookies = connection.headerFields.entries
                    .filter { it.key?.equals("Set-Cookie", ignoreCase = true) == true }
                    .flatMap { it.value.orEmpty() }
                connection.disconnect()
                ApiResult(code, content, cookies)
            } catch (error: Exception) {
                ApiResult(0, "", networkMessage = networkMessage(error))
            }
        }

        private fun readResponse(connection: HttpURLConnection, code: Int): String {
            val source = if (code in 200..399) connection.inputStream else connection.errorStream
                ?: return ""
            val decoded = if (connection.contentEncoding?.contains("gzip", ignoreCase = true) == true) {
                GZIPInputStream(source)
            } else source
            return decoded.bufferedReader(Charsets.UTF_8).use { it.readText() }
        }

        private fun networkMessage(error: Throwable): String = when (error) {
            is UnknownHostException -> "无法解析中心域名，请切换手机网络或检查 DNS"
            is SSLHandshakeException -> "HTTPS 证书握手失败，请检查手机系统时间或网络拦截"
            is SocketTimeoutException -> "连接中心服务超时，请检查手机网络或防火墙"
            is ConnectException -> "无法连接中心服务，请检查手机网络或防火墙"
            is SocketException -> "网络连接失败（${error.message ?: "SocketException"}），请检查手机网络"
            else -> "网络请求失败（${error.javaClass.simpleName}），请检查手机网络"
        }

        private fun escape(value: String): String = value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
    }
}

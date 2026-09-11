# Android 手机客户端

这是“策划实用小工具在线版”的 Android 客户端 MVP。

## 产品范围

- 原生登录页：账号、密码、服务地址、显示/隐藏密码、提交状态和错误提示。
- 默认中心服务地址：`https://collab.wnnttzy.kdns.fr/`，登录页仍允许切换到其他中心。
- 原生移动工作台：显示当前账号与中心连接状态，提供“页面收集”和“首页数据分析”入口。
- 业务页面：使用同一中心域名的 WebView 加载现有 `/screenshot/`、`/analysis/` 页面，复用现有 HttpOnly 会话 Cookie 和 API。
- 退出登录：调用中心 `/api/auth/logout`，同时清理 WebView Cookie。

## 设计依据

设计系统和页面规则位于：

- `design-system/practical-tools-mobile/MASTER.md`
- `design-system/practical-tools-mobile/pages/login.md`
- `design-system/practical-tools-mobile/pages/home.md`

实现遵循扁平、轻量、低动效的移动工具风格：青绿色主色、橙色主操作、8dp 间距节奏、48dp 最小触控区域、安全区留白、可访问的字段标签和错误反馈。

## 多尺寸适配门槛

客户端和复用页面按移动优先处理：

- 原生页面使用可滚动内容、系统安全区、48dp 触控目标，不依赖固定像素高度。
- 集合页、页面收集、首页数据分析都补充了 `viewport-fit=cover`、720px/380px 移动断点；侧栏会变成横向导航，卡片/指标会折叠，表格和活动日历保留可控的横向滚动，图片和图表限制在视口内。
- Android WebView 开启宽视口、DOM Storage、同域 Cookie、禁止混合内容和本地文件访问；不强制缩放页面，保留系统文字放大能力。
- 小手机以 360dp 宽度为门槛，大手机/平板通过流式宽度扩展；横屏仍允许内容滚动，不用固定宽度把页面撑破。
- 发布前需要在至少 360×800、412×915、600×1024、横屏 800×480 四种视口复核，并在系统大字体和减少动效设置下复核。

## 构建前置条件

需要 Android Studio 或 Android SDK、JDK 17 和 Gradle/Gradle Wrapper。项目已验证可使用 Android SDK 35、Build Tools 35 和 Gradle 8.7 构建；当前交付目录同时包含已构建的 debug APK。真机安装后的登录联调仍需实际 Android 设备或模拟器。

在有 Android 构建机的环境中：

```bash
cd mobile/android
gradle assembleDebug
```

默认要求 HTTPS 中心地址。开发时如需使用局域网 HTTP 地址，显式开启明文流量：

```bash
./gradlew assembleDebug -PallowCleartextTraffic=true
```

也可以用 Gradle 参数预置一个中心地址（用户仍可在登录页修改；默认值就是 `https://collab.wnnttzy.kdns.fr/`）：

```bash
./gradlew assembleDebug -PcenterUrl=https://tools.example.com
```

## 部署注意

手机不能使用 `127.0.0.1:18180` 访问电脑上的中心服务。生产环境应使用可从手机访问的 HTTPS 域名；局域网调试时使用电脑的局域网 IP，并确认防火墙、路由和中心服务监听地址允许访问。

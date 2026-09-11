# 调研与工程发现

## 当前工程

- Android 入口：`mobile/android/app/src/main/java/com/practicaltools/mobile/MainActivity.kt`
- Manifest：`mobile/android/app/src/main/AndroidManifest.xml`
- 当前版本：`0.3.1`，版本号 6
- 默认中心地址：`https://collab.wnnttzy.kdns.fr/`
- 最低 Android 版本：API 26；目标版本：API 35
- 已有统一系统栏 inset 处理；登录、主页、页面收集、分析等页面已有原生实现。
- 页面收集按钮最近已调整为视觉高度更紧凑，并增加了上下留白；后续改动不能回退这部分修正。

## 诊断功能需求

- 鸿蒙/卓易通问题不能只用 User-Agent 判断；需要把网络链路拆成可验证的检查项。
- 需要区分 DNS 失败、TCP 不通、TLS 握手失败、HTTPS 服务异常和代理/VPN 干扰。
- IPv4 与 IPv6 需要分别展示，便于识别“IPv6 失败、IPv4 正常”的运营商或路由问题。
- 报告应包含系统环境、网络类型、地址、检查状态和建议，但不得包含密码、Cookie 或认证响应内容。

## 当前环境对照检测

- 当前开发环境可以解析 `collab.wnnttzy.kdns.fr`，拿到 Cloudflare 的 A 与 AAAA 记录。
- 当前开发环境对 HTTPS 根地址发起 GET 返回 200；此前 HEAD 返回 405，说明服务端只允许 GET 并不代表服务不可达。
- 因此鸿蒙设备上的浏览器也打不开时，应优先检查设备侧 DNS、IPv6 路由、TLS 握手、运营商/私有 DNS、VPN/代理或网络权限；不能继续只归因于 APK 的 UA。

## 鸿蒙设备实际报告

- 设备为 HUAWEI SGT-AL10，Android 16 / API 36，检测到 `ro.build.version.emui=EmotionUI_14.0.0`。
- 使用移动数据且系统联网验证通过；未发现代理/VPN。
- 目标域名解析到 2 个 IPv4 和 2 个 IPv6；四个地址的 TCP 443 均可达。
- 四个地址的 TLS 握手均出现 `SocketException: Connection reset`，HTTPS 请求也被重置。
- 结论：失败发生在 TCP 之后、HTTP 之前，当前不能归因于 UA、WebView、登录接口或 DNS；需要优先对比 Huawei 设备网络栈/运营商链路与目标域名的 TLS/SNI 处理。
- 当前报告的 TLS 明细只展示前两个地址，后续可改成按 IPv4/IPv6 分组展示，避免隐藏地址族差异。

## UI 方向

- 采用浅色、蓝色主行动色、白色卡片和中性灰边框，保持与现有数据分析页面的可读性。
- 诊断页面使用紧凑但不拥挤的 48dp 可点击区域、8dp 以上间距、状态徽标和结果卡片。
- 依据 Android 官方 Window Size Class 资料，紧凑宽度保持单列，中等及以上宽度切换为双列卡片/指标布局；功能卡不再把内容拉伸成满屏大按钮。
- 原生客户端统一为浅色数据工作台视觉：`#536DFE` 蓝紫主色、冷灰背景、白色卡片、语义状态色；筛选项使用圆角芯片，主要动作与辅助动作分层。
- 登录密码输入和显示密码按钮使用同一 52dp 控件高度；首页卡片使用图标色块、标题/说明/箭头三段结构；页面收集与分析筛选视觉高度收紧至 38–40dp。

## 截图原图加载排查

- Android 旧实现先对 `/api/screenshot/file` 发起 `inJustDecodeBounds` 请求，再发起第二次请求解码图片；虽然第一次不创建 Bitmap，但仍会把 HTTP 响应完整读完，因此移动网络下等于重复下载原图。
- 服务端已有 `/api/screenshot/thumb`，但它按宽度生成列表缩略图；对于 1200×2608 一类的竖向截图，不能作为手机高清查看图的稳定方案。
- 新增 `/api/screenshot/preview`：按最长边生成并缓存 JPEG 高清预览，默认 1440、上限 2048，使用独立 `previews` 缓存目录，不修改原图和网页端 `/file` 行为。
- Android 0.3.4 优先单次请求预览接口，失败再单次回退原图；查看页增加进度、失败文案和重试按钮，避免一直黑屏或只弹 Toast。
- 已在模拟器安装 0.3.4 并确认登录页正常启动；中心服务需要部署新接口后才能使用预览加速，未部署时客户端仍可走原图回退。
- 实机反馈显示 1200×2608 左右的长截图被固定 1440px 最长边预览缩成约 663×1440，再放大到手机宽度，导致明显模糊；固定预览尺寸不适合不同像素密度设备。
- Android 现在按设备最长像素边的 1.35 倍请求预览，范围 2048–4096，并关闭 `BitmapFactory` 的 density 自动缩放；服务端预览上限同步提高到 4096，JPEG 质量提高到 92、关闭色度抽样并使用新缓存命名，避免命中旧的低质量文件。
- 图片页的系统返回/边缘返回手势原先命中通用分支 `showHome()`；现在 `screenshot-image` 明确返回页面收集列表，避免查看图片后跳首页。

## 原生化范围盘点

- 已原生：登录、连接诊断、工作台、页面收集、缩略图网格、图片查看、店铺管理、定时任务、采集进度、分析概览。
- 仍使用 WebView：从分析概览进入的完整分析工具，主要包含趋势图、热力图、明细表、异常修正、分析文件管理等深层能力。
- 这些深层模块已经有独立 `/api/analysis/*` 接口，可以继续按“筛选状态 → 指标卡 → 图表数据 → 修正操作”拆分，而不是再增加一个更大的 WebView 页面。
- 长内容放在 ScrollView 中，避免窄屏横向溢出；状态栏和底部导航栏使用安全区 inset。

## 原生数据加载性能排查

- `app/main.py` 已启用 Starlette `GZipMiddleware`，响应超过 1024 字节即可压缩。
- Android `ApiClient` 之前只设置 `Accept: application/json`，没有 `Accept-Encoding: gzip`，因此原生页面不会主动协商服务端压缩；WebView 会自动协商 gzip/连接复用，所以同一分析接口在 WebView 中更快。
- 原生请求层需要加入 gzip 声明并按响应头使用 `GZIPInputStream` 解压；图片请求不应把 JSON 解压逻辑混入其中。
- 完整分析页面的 API 已覆盖 `/panel/overview`、`/panel/blocks`、`/panel/blocks_yoy`、`/panel/newold`、`/panel/anomaly`、`/panel/anomaly/fix`、`/panel/anomaly/fixes`、`/files` 和 `/file`，具备原生化条件。
- Android 0.3.5 已移除完整分析页的 WebView 入口，原生工作区直接调用上述接口；数据管理页支持 SAF 选择 XLSX 上传和按年份删除，异常页支持访客数修正/重置。

## 在线更新发布

- 中心版本已从 0.6.2 提升到 0.6.3，避免覆盖已有的 0.6.2 更新包。
- `dist/updates/PracticalToolsOnline-update-v0.6.3.zip` 已由 `create_update_bundle.py` 生成并通过 `inspect_update_bundle`；114 个文件，SHA-256 为 `ce6c6a91eafbef0b55159501566134602f98881102664514c23093bb03e4a05d`。
- 在线更新包只包含 allowlist 程序树与安装器映射文件；业务数据、运行时、日志、浏览器状态、Cookie、密钥和 `.env` 不在包内。`center_windows.env.example` 是示例配置，不包含真实密钥。

## 2026-09-11 手机高清截图仍模糊排查

- 实机反馈显示中心已升级到 `0.6.5` 后，图片页仍出现明显的中文文字和细线模糊；这说明问题不只是预览边长或 Android `BitmapFactory` 密度缩放。
- 采集端保存的是 PNG 截图，服务端手机预览此前统一转换为 JPEG；即使使用质量 92 和 4:4:4 色度，界面文字周围仍会产生有损压缩伪影。
- `mobile_preview` 现改为优先生成并缓存无损 WebP，缓存名使用新的 `lossless.webp` 后缀以避开旧的 JPEG 预览；Android 原生 `BitmapFactory` 可直接解码 WebP。
- 对缺少 Pillow WebP 编码支持的部署环境，保留 q96、4:4:4 JPEG 回退，仍比旧 q92 预览更清晰；原图 `/api/screenshot/file` 行为保持不变。
- 页面采集返回状态问题来自 Android `showScreenshotNative()` 的页面级重建：截图列表、当前店铺筛选和 `ScrollView` 都是局部变量，图片页返回时必然重新请求并从顶部开始。
- Android 0.3.9 将截图列表缓存、当前筛选和滚动位置提升为 Activity 会话状态；返回图片页时直接重建轻量视图并恢复状态，手动点击刷新仍会重新请求中心服务；切换账号或中心地址时会清空该缓存，避免串用户数据。

## 2026-09-11 响应式间距与最终视觉验收

- 原有 `column(gap)` 只设置了方向，没有真正渲染 gap；这解释了多个页面按钮上下紧贴。修复为透明分隔空间，并以屏幕宽度、屏幕高度和字体缩放动态选择 8dp/16dp，避免在普通手机上把显式 margin 再叠成过大空白。
- 真实安装包验收已确认：顶部状态栏安全区正常；窄屏和大字体页面可纵向滚动；平板入口自动双列；横屏保留可滚动内容；分析底部 Tab、筛选高亮、图片页返回状态均正常。
- Android 应用图标与应用内图标已统一为蓝紫 P 标识；正式构建产物 `mobile/releases/PracticalToolsMobile-debug-v0.4.4.apk` 的 manifest 显示 `usesCleartextTraffic=false`，默认中心地址仍为 `https://collab.wnnttzy.kdns.fr/`。

## 2026-09-11 在线更新后终端窗口排查

- 更新接口 `app/api/admin.py` 原先以 `runtime/python.exe` 启动后台更新工作进程，只设置了分离进程组，没有完整的 Windows 无窗口参数；更新工作进程原先也直接以 `python.exe` 拉起 `portable_center.py`。
- `portable/portable_center.py` 原先再用 `runtime/python.exe -m app.main` 启动真正的中心服务，导致即使外层更新器隐藏，Windows 仍可能为服务子进程创建控制台窗口。
- 现已统一优先选择同目录的 `runtime/pythonw.exe`，并设置 `STARTF_USESHOWWINDOW + SW_HIDE + CREATE_NO_WINDOW`；标准输入、输出和错误输出均不继承更新器控制台，中心服务继续写入自己的日志文件。
- `packaging/center_windows.spec` 与 `packaging/center_updater.spec` 已切换为 `console=False`，后续完整 Windows 构建的中心服务和更新器也不会自带控制台窗口。
- 在线更新包只覆盖 `portable/portable_center.py` 与允许的应用代码，因此本次修复的关键路径放在可随更新包下发的更新器和便携中心代码中；`START.cmd`、停止脚本等安装级辅助文件继续按约定保留。

## 2026-09-11 UI 技能引入与首轮审查

- 官方 Impeccable 已安装到项目 `.agents/skills/impeccable`，包含 Android 原生、adapt、audit 和 craft-floor 参考；官方 GSAP AI 技能已安装 `gsap-core`、`gsap-timeline`、`gsap-performance` 三个相关子技能。
- “Premium UI Reference Director”没有发现可验证的官方独立仓库，因此不能声称已下载第三方包；当前以项目级适配器保存用户明确给出的 8px 网格、边框降噪、8/16dp 圆角、层级和精简文案约束。
- 首轮源代码审查发现 `MainActivity.kt` 原有大量 dp(10/12/14/18/22/34/38/42/46/52) 以及 XML 中 12/14/18/24dp 圆角；`dp()` 现统一向上取整到 8dp 网格，资源圆角已收敛为 8dp/16dp，后续仍需逐页确认视觉密度而不能只依赖自动取整。
- 首轮审查还发现部分说明是在解释实现方式而不是帮助用户完成任务，已删除首页长说明、图片查看页“原生图片查看”说明和完整分析页“所有分析功能已改为原生”说明；安全、错误、加载和恢复提示保留。
- Impeccable 官方上下文命令使用 `sh` 运行成功；直接执行因官方脚本从压缩包落地时缺少执行位而失败。当前版本引擎对 `audit` 返回 Unknown command，所以未将其输出当作自动评分。

## 2026-09-11 文案审查

- 登录页原先的“把工作台带到手机上”是宣传语，不是工具名；工具正式名称为“策划实用小工具”，副标题压缩为“连接中心服务”。
- 删除了工作台、页面收集、设置、店铺管理、定时任务和分析页中解释实现方式、重复描述或把明显操作写成教程的文案；保留登录安全说明、加载/错误/重试、删除确认和诊断结果等会影响决策的信息。

## 2026-09-11 逐页截图验收准备

- 用户明确要求不能只截图验收登录页；本轮需要在本地测试中心建立登录态，逐页检查工作台、页面收集、图片查看、分析、诊断、设置、店铺管理和定时任务。
- 用户提供的测试账号仅用于本地验收，密码不落盘，不进入截图和日志。
- 当前宿主机 PATH 未包含 `adb`，需要使用项目 Android 工具链下的 `platform-tools/adb`；这属于验收环境问题，不代表 APK 运行失败。
- 2026-09-11：`column(gap)` 的透明分隔与控件自身 `marginTop` 同时生效，是实机中按钮组内部显得过松的直接原因；已删除页面收集、设置、店铺、定时任务和分析页的重复内部外边距。

# 策划实用小工具在线版架构契约

## 1. 文档定位

本文是当前实现阶段的架构约束，不是远期设想。构建、迁移、打包和测试均以此为准。若代码与本文冲突，应先确认用户需求再修改本文或代码。

本次只迁移：

- 页面收集。
- 首页数据分析。
- 原集合外壳中与上述两项相关的主题、背景、收藏、头像、导航和退出行为。

明确排除：违禁词排查、主图一条龙服务本身、项目成员、审核分工、多端协同锁及主图协同状态机。

## 2. 已冻结的核心决策

| 主题 | 决策 |
|---|---|
| UI | 除新增登录验证页外，直接复用旧 HTML/CSS/JS/ECharts；不改 DOM 结构、视觉、文案和操作流程 |
| 中心服务 | FastAPI + SQLAlchemy 2 + PostgreSQL + Alembic |
| 中心地址 | 复用主图协同版中心主机、域名/入口和默认服务端口 `18180`；端口可配置，但生产值保持一致 |
| 进程关系 | 两个中心程序互斥运行，不做同端口复用、反向代理分流或同时启动 |
| 登录 | 直接复用同一 PostgreSQL 的 `users`、`user_sessions`、密码哈希算法和 Cookie 名 |
| 业务数据 | 新建独立业务表；不读写主图项目、核查、分工和锁相关表 |
| 数据迁移 | 在线版使用独立 Alembic revision 链和独立 version table |
| 页面收集 | 浏览器只访问中心服务；本地 Agent 主动向中心服务反向轮询任务 |
| 并发 | 不实现用户协同锁；只保留数据库事务、任务租约和幂等约束这些可靠性机制 |
| 截图存储 | 通过 `BlobStorage` 适配层支持本地文件与腾讯云 COS；未配置 COS 时可显式降级本地存储 |
| 客户端 | macOS、Windows 均提供本地 Agent/启动器；淘宝登录态只留在本机 |

## 3. 部署拓扑

```text
macOS/Windows 浏览器
  └─ 原样 UI + 登录页
       └─ HTTPS /api/*
            ┌──────────────────────────────────────┐
            │ 中心主机 practical-tools-online     │
            │ FastAPI :18180                      │
            │ ├─ 共享认证 users/user_sessions     │
            │ ├─ 独立 practical_* 业务表          │
            │ ├─ Agent 任务与状态                 │
            │ └─ BlobStorage                     │
            └──────────────┬───────────────────────┘
                           ├─ PostgreSQL（与协同版同库）
                           ├─ LocalBlobStorage（降级/开发）
                           └─ TencentCosBlobStorage（生产截图）
                                      ↑
Agent 主动轮询/心跳/确认 ──────────────┘
  ├─ Patchright + 本机浏览器
  ├─ 淘宝 storage state（永不上传）
  ├─ OCR/裁剪/验证码检测
  ├─ 本机调度与暂停/继续
  └─ outbox + SHA-256 + 断网重试
```

中心服务只接受入站 HTTPS；Agent 只需建立出站 HTTPS，不要求路由器端口映射，也不让网页访问 `localhost`。

## 4. 中心服务与端口互斥

生产默认端口为参照程序使用的 `18180`，配置项可以覆盖以便测试，但安装包/部署模板默认值必须为 `18180`。

由于主图协同版与本程序不会同时开启，采用运行时互斥而不是同端口共存：

1. 启动前探测 `18180`。
2. 若已占用，读取 `/api/health` 或进程标识判断占用者。
3. 不自动结束另一个程序，不抢占端口；向用户显示“另一中心服务正在运行，请先退出”的明确提示。
4. 本程序的“退出程序”只能退出当前网站会话、停止/退出本机 Agent 或关闭启动器；不得提供关闭中心主机或结束任意进程的公网 API。

## 5. 共享认证契约

### 5.1 必须原样复用的内容

在线版连接与主图协同版相同的 PostgreSQL 数据库，并把以下对象视为外部共享基础设施：

- `users`
  - `id varchar(36)` 主键。
  - `username varchar(80)` 唯一。
  - `display_name varchar(120)`。
  - `password_hash varchar(255)`。
  - `role varchar(20)`。
  - `active boolean`。
  - `avatar_storage_key varchar(500)`，若生产库已执行头像迁移。
  - `created_at`、`updated_at`。
- `user_sessions`
  - `id varchar(36)` 主键。
  - `user_id` 外键到 `users.id`，用户删除时级联。
  - `token_hash varchar(64)` 唯一。
  - `expires_at`、`created_at`、`last_seen_at`。
- 密码：`pbkdf2_sha256$600000$<salt>$<digest>`，验证必须与参照程序兼容。
- 会话明文 token：`secrets.token_urlsafe(32)`；数据库只保存 SHA-256。
- Cookie 名：`main_image_collab_session`。
- Cookie：`HttpOnly`、`Path=/`、`SameSite=Lax`；生产 HTTPS 必须 `Secure`；有效期沿用参照程序配置，默认 7 天。

这样已有账号无需复制或重置密码；已有、未过期的同域 Cookie 也能由本程序读取。角色字段只用于展示/兼容，不在本程序引入管理员、策划、设计分工逻辑；任何 `active=false` 账号均拒绝登录和会话恢复。

### 5.2 认证表所有权

- 本程序可以读取 `users`，并按参照逻辑新增/删除 `user_sessions`。
- 本程序的业务迁移不得创建、修改、重命名或删除 `users`、`user_sessions`。
- 用户头像若继续共用 `avatar_storage_key`，必须共用可解析的头像存储根或提供兼容读取；不得用本程序迁移修改共享列。
- 登录失败统一返回 401，不区分“用户不存在、停用、密码错误”。
- `/api/auth/login`、`/logout`、`/me`、`/status`、`/avatar` 的路径和主要返回形状与参照版一致。
- 参照版当前退出会删除该用户全部会话。为保持兼容，本程序第一版沿用；两个服务互斥运行使影响可控。若以后允许两程序同时在线，再单独引入按 session id 注销，不能在本期暗改共享语义。

### 5.3 SQLAlchemy 映射边界

代码可以为共享表建立只读/兼容 ORM 映射，但 Alembic 必须通过独立 metadata 或 `include_object` 明确排除共享表。业务表引用用户时可建立到 `users.id` 的真实 PostgreSQL 外键；迁移脚本只创建该外键，不拥有被引用表。

禁止在生产使用 `Base.metadata.create_all()` 代替迁移。自动建表仅允许隔离的开发/测试数据库。

## 6. 数据库与迁移隔离

### 6.1 命名和版本表

- 新业务表统一使用 `practical_` 前缀，避免与主图协同版将来新增表碰撞。
- Alembic version table 固定为 `practical_alembic_version`。
- 主图协同版继续使用其 `alembic_version`；两条 revision 链互不设置 `down_revision`。
- 在线版 `alembic upgrade head` 只能改 `practical_*`；`downgrade` 也不得触碰共享认证表和主图业务表。

建议业务表：

| 表 | 关键约束/用途 |
|---|---|
| `practical_capture_agents` | 设备 id、所属用户、token hash、平台、版本、最后心跳、撤销时间 |
| `practical_capture_shops` | 用户、名称、URL、启用、排序；用户内名称唯一 |
| `practical_capture_schedules` | 用户/Agent、时区、星期、时分、启用 |
| `practical_capture_jobs` | 类型、payload、状态、租约、进度、错误、生命周期时间 |
| `practical_screenshots` | `capture_id` 唯一、品牌、时间、storage key、大小、尺寸、SHA-256、Agent |
| `practical_capture_uploads` | 申请、目标 key、过期、状态、重试和完成确认 |
| `practical_analysis_source_files` | 用户、年份、原文件 key、SHA-256、解析状态；用户内年份唯一 |
| `practical_analysis_cache_files` | 用户、年份、parser 版本、缓存 key |
| `practical_analysis_daily` | 用户、year/date 和现有核心指标；`(user_id, year, date)` 唯一 |
| `practical_analysis_fix` | 用户、year/date/field/delta；四元组唯一 |

虽然第一版没有多端协同，业务记录仍关联 `user_id`，用于账号间数据隔离。这是访问控制，不是协同功能。

### 6.2 事务原则

- Excel 年度重传：在单事务内替换该用户该年度日数据和缓存引用；新 Blob 成功后再切换引用，失败时不破坏旧数据。
- 年度删除：数据库记录先标记/删除，Blob 回收可进入可重试清理队列；接口语义仍保持旧版同步返回。
- 截图完成：只有 Blob 校验通过后才插入/激活截图元数据。
- `capture_id`、上传 session id 和 job id 都是服务端生成的不可猜测 UUID。

## 7. 原前端与 API 兼容层

### 7.1 前端原则

- 直接复制 `frontend/shell`、`frontend/screenshot`、`frontend/analysis` 及 ECharts 静态资源。
- 仅删除本次排除的两个应用入口，并增加登录门禁；其余样式、尺寸、按钮、弹窗、键盘/鼠标行为保持不变。
- 原相对路径继续使用，不把中心地址硬编码到 HTML。
- 主题、背景、收藏、标签顺序继续用当前 origin 的 `localStorage`；不做多端同步。

### 7.2 旧 API 必须保持

兼容层必须保留旧页面实际调用的路径、HTTP 方法、查询参数和 JSON 字段：

- 页面收集：`/api/screenshot/list|file|thumb|delete|open-folder|shops|capture|capture/status|capture/pause|capture/resume|scheduler/config|scheduler/*|progress|login|login/done|login/status`。
- 数据分析：`/api/analysis/upload|status|preview|files|file|metrics|panel/overview|panel/newold|panel/anomaly|panel/anomaly/fix|panel/anomaly/fixes|panel/blocks|panel/blocks_yoy|panel/calendar|panel/overall_all`。

关键旧返回契约包括：

- 截图列表：`{"brands":[{"brand","dates":[{"date","images"}],"count"}],"total"}`；图片项保留 `brand/date/file/path/size`。在线版可令 `path` 为不透明 id，但前端传回后必须可解析，禁止暴露服务器文件路径。
- 采集状态：`{"running": bool, "paused": bool}`。
- 进度：`{"current": int, "total": int, "shop": str}`。
- 店铺：`{"shops":[{"name","url"}]}`。
- 分析接口继续使用 `ok`、`error`、`year`、`years/cache_years` 及原面板字段；数值、数组顺序和空值保持。

兼容层可以在认证缺失时返回 401；这是新增登录页带来的唯一全局协议变化。Agent 离线时旧页面应收到可展示的 `error`，不得伪造 `ok: true`。

## 8. Agent 架构

### 8.1 身份和配对

Agent 不保存用户网站密码，也不复用浏览器 Cookie。推荐流程：

1. 已登录网页创建短时、单次配对码。
2. Agent 用配对码交换长期设备 token。
3. 中心库只存设备 token 的 SHA-256；明文进入 macOS Keychain 或 Windows Credential Manager。
4. token 绑定 `agent_id + user_id`，可以单独撤销和轮换。

### 8.2 反向轮询

推荐 Agent 协议：

- `POST /api/agent/heartbeat`：平台、构建版本、协议版本、能力、当前 job、状态；返回 `compatible`，协议不兼容时可附带 HTTPS Agent 更新清单指针。
- `GET /api/agent/jobs/next?wait=25`：长轮询；无任务返回 204。
- `POST /api/agent/jobs/{id}/start`：原子取得短租约。
- `POST /api/agent/jobs/{id}/progress`：当前店铺、总数、已完成、暂停/验证码状态。
- `POST /api/agent/jobs/{id}/complete|fail`：最终状态。
- `GET /api/agent/config`：店铺和调度配置版本。

构建版本用于诊断，不作为可用性硬门禁。中心以协议版本兼容范围为准；协议不兼容时，Agent 只有在收到完整且 HTTPS 的平台更新清单、通过版本/大小/SHA-256 校验后才会启动独立更新 worker。更新 worker 等待旧 Agent 释放进程锁，替换失败会重新拉起旧版本，避免中心端把“版本号不同”误判为必须停用。

任务类型至少覆盖 `capture_all`、`capture_shop`、`capture_url`、`pause`、`resume`、`stop`、`login`、`login_done`、`open_folder`、`scheduler_install`、`scheduler_uninstall`、`scheduler_update`。

任务租约是宕机恢复机制，不是用户协同锁：同一任务只能被一个 Agent 执行；租约过期后可重派。服务端和 Agent 均必须让重复 `start/progress/complete` 安全。

### 8.3 本机边界

- Patchright、浏览器路径、淘宝 Cookie/localStorage、验证码页面和 OCR 中间文件只在本机。
- `browser_state/state.json` 永不上传、永不进入日志和备份。
- 每次截图先写入本地 outbox，落盘并计算 SHA-256，再开始上传。
- 只有收到中心端完成确认后才将 outbox 项标记完成；删除策略可配置，默认至少保留至确认后一次清理周期。
- macOS 用 `launchd`，Windows 用任务计划程序/服务实现自启和定时；UI API 语义一致，平台实现分离。

## 9. BlobStorage 与腾讯云 COS

### 9.1 可插拔接口

中心服务只依赖抽象 `BlobStorage`，业务层不得拼接本地路径或直接调用 COS SDK。最小能力：

```python
class BlobStorage(Protocol):
    def put_bytes(key, data, content_type, metadata) -> BlobInfo: ...
    def open/read(key) -> BinaryIO: ...
    def head(key) -> BlobInfo | None: ...
    def delete(key) -> None: ...
    def create_upload(key, size, sha256, content_type, expires_in) -> UploadGrant: ...
    def verify_upload(key, size, sha256) -> BlobInfo: ...
    def create_download(key, expires_in) -> DownloadGrant: ...
```

实现：

- `LocalBlobStorage`：开发、测试和 COS 未配置时的显式降级；写临时文件后原子改名，storage key 仍使用统一格式。
- `TencentCosBlobStorage`：生产截图首选；使用私有桶、前缀隔离、预签名上传/下载和服务端校验。

存储 key 由中心生成，例如 `practical-tools/{user_id}/screenshots/YYYY/MM/{capture_id}.png`，不得接受 Agent/浏览器提供的任意 key。

### 9.2 COS 上传流程

1. Agent 上报 `capture_id`、文件大小、MIME、宽高和 SHA-256，请求上传。
2. 中心校验任务归属、大小上限、类型和幂等状态，创建 `practical_capture_uploads`。
3. 中心返回短时预签名 PUT/分块上传授权及必要请求头；永久 `SecretId/SecretKey` 只存在中心主机环境/密钥服务。
4. Agent 直接上传 COS；网络失败留在 outbox 并以同一 `capture_id` 重试。
5. Agent 调用完成确认。中心 `HEAD` 校验 key、size、content-type、自定义 `sha256` metadata；不能把 COS ETag 当作 SHA-256。
6. 强校验模式下中心流式读取对象重新计算 SHA-256，或使用等价可信校验通道；校验通过后在一个事务内激活截图元数据和完成上传 session。
7. 相同 `capture_id + sha256` 重复确认返回同一结果；同一 `capture_id` 携带不同摘要必须 409。

小文件可保留后端 multipart 上传作为降级路径，但不得使用 base64 JSON。默认图片上限 30 MB，代理/隧道限制至少 32 MB；分块阈值由存储适配层决定。

### 9.3 COS 下载、缩略图与 CORS

- 桶保持私有，数据库/页面只拿 storage key 或截图 id。
- `/api/screenshot/file` 鉴权后可返回短时预签名 GET 重定向；签名有效期建议 1–5 分钟。
- 缩略图可由中心首次读取原图后生成并写入独立 key，或由后端代理返回；旧 `/thumb` 路径保持。
- 浏览器若直接访问预签名 URL，COS CORS 只允许正式站点 origin，最小开放 `GET/HEAD`；若未来浏览器直传才增加 `PUT/POST`。允许的 headers、expose headers 和 max-age 均使用最小集合，不允许 `*` 搭配凭据。
- 原生 Agent 直传不依赖浏览器 CORS，但仍需测试签名头与代理/防火墙兼容性。

### 9.4 降级规则

- COS 配置完整且启动自检成功：使用 `TencentCosBlobStorage`。
- 未配置 COS：记录清晰告警并使用 `LocalBlobStorage`，用于开发或有限中心机部署。
- COS 已配置但鉴权/桶访问失败：生产默认启动失败，不静默降级，避免截图落到意外磁盘；只有显式 `ALLOW_STORAGE_FALLBACK=true` 才可降级并产生高优先级告警。
- 存储实现写入数据库，后续读取按记录定位，支持渐进迁移；不能因切换默认 provider 导致旧文件失联。

## 10. 安全底线

- 全部网页业务 API 依赖当前用户，并按 `user_id` 过滤；Agent API 只接受设备 token。
- 变更型 Cookie API检查 Origin/Referer 或 CSRF token；登录失败限速。
- 上传验证扩展名、MIME、魔数、图片解码、像素上限、字节上限和 SHA-256。
- 私有 COS 密钥、数据库口令、Agent token 不进入源码、安装包、URL 查询参数或日志。
- favicon 代理阻止 localhost、回环、链路本地、RFC1918/内网、云元数据地址、非 HTTP(S) 和重定向绕过。
- 所有文件访问都通过 storage key/id，拒绝 `..`、绝对路径、编码穿越和符号链接逃逸。
- 数据库与 Blob 必须协调备份；淘宝登录态明确排除在中心备份外。

## 11. 恢复与可观测性

- 心跳超过阈值后 Agent 显示离线；运行中 job 的租约到期后进入可恢复/失败状态，不永久卡住。
- 中心重启后恢复未完成任务、上传 session 和 Blob 清理队列。
- 日志带 request id、job id、capture id、agent id，但不记录 token、密码、Cookie、预签名完整 URL 或淘宝状态。
- 指标至少包含 Agent 在线数、任务成功/失败/耗时、outbox 待传量、上传重试、COS 请求错误、存储容量和分析解析耗时。
- 备份恢复验收必须同时覆盖 PostgreSQL、Local/COS 对象清单和校验值。

## 12. 实施顺序和架构门禁

1. 共享认证兼容与独立迁移骨架。
2. 原样静态前端和登录门禁。
3. 数据分析 PostgreSQL/API 兼容层。
4. BlobStorage 本地实现与截图图库。
5. Agent 配对、反向轮询、任务状态机和 outbox。
6. Tencent COS 实现、预签名直传和私有下载。
7. macOS/Windows Agent 打包。
8. 历史数据导入、视觉/API/真实采集/恢复测试。

任何阶段进入下一阶段前，必须满足：未修改两个源项目；共享认证表未被本项目迁移拥有；业务 API 通过契约测试；不存在网页直连 localhost；不存在将淘宝登录态或 COS 密钥上传/下发的代码路径。

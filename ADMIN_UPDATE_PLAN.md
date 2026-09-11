# 管理后台、历史数据包与绿色版在线更新契约

## 目标与边界

本轮把中心主机的日常维护收敛到浏览器管理员后台，并提供 macOS 历史数据一键打包、中心端校验导入及 Windows 绿色版在线更新。所有能力均不得要求管理员权限、注册 Windows 服务、写注册表或写入 `Program Files`。

历史数据打包本身不涉及系统级变更：它只读取用户明确选择的旧程序数据目录，并在用户指定的位置生成一个普通归档文件。需要特别防止把淘宝浏览器登录态、账号 Cookie、Agent token、数据库/COS 密钥等本机凭据一起打入归档。

## 1. macOS 历史数据包

### 1.1 用户流程

1. 双击 `打包历史数据.command`。
2. 工具自动探测 `~/.taobao_detail_extractor` 与旧程序的 `screenshot` 目录；探测失败时允许用户选择目录。
3. 只读扫描后显示年份、Excel、分析缓存、修正数据库、截图、店铺和计划任务的数量及总大小。
4. 生成单个 `PracticalToolsLegacy-YYYYMMDD-HHMMSS.ptbundle` 文件和摘要报告。
5. 用户把 `.ptbundle` 复制到中心主机，在管理员后台上传并选择目标账号。

### 1.2 包格式

`.ptbundle` 是 ZIP 容器，但扩展名用于避免用户误操作。归档根目录必须包含 `manifest.json`：

```json
{
  "schema_version": 1,
  "created_at": "2026-08-31T12:00:00Z",
  "source_platform": "macOS",
  "files": [
    {
      "path": "analysis/source/2025.xlsx",
      "kind": "analysis_source",
      "size": 12345,
      "sha256": "64位十六进制"
    }
  ],
  "totals": {"files": 1, "bytes": 12345}
}
```

允许的文件类别仅为：

- `analysis_cache`：分析缓存 JSON；
- `analysis_source`：原始 Excel；
- `analysis_database`：旧分析 SQLite（只读取允许的分析表）；
- `screenshot`：历史 PNG/JPEG/WebP；
- `screenshot_config`：店铺与调度配置。

必须排除：

- 任意 `browser_state`、`storage_state`、Cookie、浏览器 profile；
- `.env`、数据库连接串、COS SecretId/SecretKey；
- Agent token、Keychain 导出物、日志；
- 符号链接、设备文件、绝对路径、包含 `..` 的路径；
- 不在 manifest 中的额外文件。

每个文件必须记录字节数和 SHA-256。中心端先验证整个 manifest，再读取或解压任何业务文件。归档解压只能进入随机 staging 目录，验证失败时整包拒绝且不得产生数据库记录或部分文件。

### 1.3 导入语义

- 管理员上传包后先“扫描”，不立即写数据库。
- 页面显示包来源、创建时间、年份、文件/截图数量、总大小、校验结论及目标账号。
- 正式导入必须显式选择一个有效、启用中的 `users.id`，不得导入无主命名空间。
- 同一包重复导入应按包摘要和文件 SHA-256 幂等；已存在截图不得重复，分析数据按现有迁移规则更新。
- 浏览器登录态始终由各客户端 Agent 本地重新登录，中心端不提供导入入口。
- COS 模式应由中心端受控上传对象并确认后入库；失败时记录可重试状态，不能写入指向不存在对象的截图元数据。

## 2. 管理员后台

### 2.1 权限

- 复用共享 `users.role`；与参照程序保持一致，仅精确的 `admin` 可访问 `/admin/` 与 `/api/admin/*`，不得把未知角色、大小写变体或普通 `planner` 视为管理员。
- `planner`、匿名用户分别返回 `403`、`401`；前端隐藏入口不是安全边界。
- 管理操作必须使用现有 HttpOnly session cookie，并校验同源请求；任何改变状态的请求只接受 `POST/PUT/DELETE`。
- COS 配置测试、更新执行、历史导入应写审计日志：操作者、时间、动作、结果、对象标识；日志不得包含密码或密钥。

### 2.2 页面能力

管理员后台至少包含：

1. 系统状态：版本、数据库连接、存储模式、数据目录、磁盘可用空间、Agent 在线数量。
2. COS 设置：Bucket、Region、Prefix、SecretId、SecretKey、连接测试和保存。
3. 历史数据迁移：上传 `.ptbundle`、校验报告、目标账号选择、导入进度和结果。
4. 在线更新：当前版本、可用版本、更新说明、检查、下载、应用、健康检查和回滚状态。
5. Agent 配对码：为当前管理员或所选账号生成一次性配对码（不显示持久 token）。

### 2.3 密钥处理

- GET 设置接口只能返回 `configured: true/false` 和脱敏标识，不得返回 SecretKey、数据库密码或完整连接串。
- SecretKey 留空表示保持原值；显式“清除”必须用独立动作并二次确认。
- 保存前先做格式校验；“测试连接”不得隐式保存。
- 绿色版优先把敏感配置放到程序目录外的用户数据目录，文件权限尽可能限制为当前账户。
- API 响应、审计日志、错误栈和更新日志中不得回显密钥。

## 3. Windows 绿色版在线更新

### 3.1 包和发布清单

第一阶段照搬“主图一条龙服务协同版”的交互：管理员在任意已登录浏览器打开后台，选择官方更新 ZIP 上传，中心校验后由独立 updater 应用，不需要登录中心主机桌面。小包可使用可续传分片上传；大包可复用后台配置的 COS 预签名直传，再由中心下载并校验。上传阈值应可配置，不能把参照程序当前的 1MB 阈值硬编码成业务规则。

更新包自身提供版本清单，至少包含：格式、版本号、兼容 schema 范围、包大小、逐文件大小/SHA-256 和更新说明。正式发布应增加离线签名及内置公钥校验；只有 HTTPS/COS 与 SHA-256 不能抵御更新包发布者本身被冒充。

后台不得接受任意 URL、中心任意本地文件路径或任意可执行命令。若后续增加自动检查官方更新源，更新源应固定在部署配置中，且只能使用允许的 HTTPS 地址。

### 3.2 状态机

```text
idle -> uploading/downloading -> validating -> verified -> backing_up
     -> waiting_restart -> applying -> migrating -> health_check -> succeeded
                                                |              |
                                                +-> rollback <-+
```

每个状态应持久化到数据目录，中心进程重启后可恢复或明确回滚。并发执行只允许一个更新任务。

### 3.3 应用与回滚

1. 下载至用户可写数据目录的随机 staging 子目录。
2. 校验大小、SHA-256、签名、版本递增和兼容 schema；解压时防 zip-slip/符号链接。
3. 永远排除并保留 `.env`、`data/`、日志、上传 staging 和用户自定义配置。
4. 复制当前程序目录为 `previous`（或使用版本化目录与 `current` 指针）。
5. 独立 updater 进程等待中心进程退出，原子切换程序目录并启动新版本。
6. 调用本机 `/api/health` 与数据库检查；在限定时间内失败则恢复 `previous` 并重新启动旧版本。
7. 数据库迁移原则是向前兼容。若新版本包含不可逆迁移，后台必须阻止无人值守更新并提示人工维护；自动回滚不得擅自 downgrade 共享数据库。

在线更新不得结束或覆盖“主图一条龙服务协同版”，只检查 `18180` 占用并提示用户先关闭另一个程序。更新程序只操作本绿色版自己的明确目录。

## 4. API 最小契约

具体字段可扩展，但语义保持：

- `GET /api/admin/status`：管理员状态概览；
- `GET/PUT /api/admin/settings/storage`：读取脱敏配置、保存 COS 设置；
- `POST /api/admin/settings/storage/test`：仅测试连接；
- `POST /api/admin/migration/inspect`：上传并校验数据包；
- `POST /api/admin/migration/import`：选择目标账号并幂等导入；
- `GET /api/admin/updates/status`：更新状态机；
- `POST /api/admin/updates/uploads`：创建浏览器分片上传；
- `PUT /api/admin/updates/uploads/{id}/chunks`：按严格 offset 追加分片；
- `POST /api/admin/updates/uploads/{id}/apply`：校验并应用已上传包；
- `POST /api/admin/updates/cos/prepare`：为大包签发 COS 临时上传；
- `POST /api/admin/updates/cos/{id}/apply`：中心拉取、校验并应用 COS 包；
- `POST /api/admin/updates/check`：可选，检查受信任官方更新源；
- `POST /api/admin/updates/rollback`：仅回滚应用文件，不自动回滚数据库。

## 5. 验收门禁

- 打包两次相同源数据，文件内容清单与各文件 SHA-256 一致（包时间戳可不同）。
- 归档中不存在 `browser_state`、Cookie、token、`.env` 或 COS/DB 密钥。
- 篡改任一字节、manifest 路径穿越、符号链接或额外文件时，导入前整体拒绝。
- planner/匿名用户无法访问任何管理员接口，admin 可访问；密钥读取和日志均为脱敏值。
- 同一包重复导入不产生重复截图、店铺或调度；目标账号隔离有效。
- 更新包 hash/签名不符、版本倒退、schema 不兼容时拒绝应用。
- 模拟启动失败、健康检查超时和断电恢复时可回到旧版本；`.env` 与 `data/` 内容保持不变。
- 整个中心部署、配置、迁移与更新过程不要求管理员权限，不写服务/注册表/系统目录。

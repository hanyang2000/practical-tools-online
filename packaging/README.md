# 本地采集 Agent 打包

## 运行边界

Agent 运行在使用页面收集的 Mac/Windows 电脑，主动向中心服务（默认
`http://127.0.0.1:18180`，可改为局域网或 HTTPS 地址）轮询任务并上传截图。
淘宝浏览器 storage state 只写入 `~/.practical_tools/screenshot/browser_state/`，不会打包、上传或进入中心数据库；COS SecretId/SecretKey 也不会出现在 Agent 内。

中心服务与旧单机程序使用同一端口时，必须保证两者不同时启动。

中心主机也可以使用独立的 `PracticalToolsCenterConsole.exe`。它不依赖浏览器，
可以在一个窗口内启动/停止中心服务和 Cloudflare Tunnel，并把两个进程的最近日志显示在窗口中。
关闭窗口只会最小化控制台；点击“退出控制台”也不会停止正在运行的服务。Windows 安装包会自动创建
“中心控制台”开始菜单快捷方式。首次使用时，在控制台里选择 `cloudflared.exe`，或确保 cloudflared 已加入 PATH。

## 首次配置

```bash
python3 -m pip install -r agent/requirements.txt
python3 -m patchright install chromium
PRACTICAL_TOOLS_SERVER_URL=http://127.0.0.1:18180 python3 agent/agent.py --pair <中心服务生成的一次性配对码>
python3 agent/agent.py
```

如果这台电脑已经绑定过 Agent，重新解压/升级后直接双击
`packaging/install_agent.command`，脚本会自动进入修复模式：保留原设备 ID、固定中心地址、Keychain/Credential Manager 中的 token、淘宝登录态和本地定时配置，只重建 macOS `launchd` 或 Windows `schtasks`。命令行也可执行
`bash packaging/install_agent.command --repair`。只有明确执行
`--rebind` 才会再次要求一次性配对码。

浏览器目录必须由当前 Agent 使用的 Patchright 版本安装，不能把其他版本的
`chromium-<revision>` 目录直接复制进来；构建测试会校验 `browsers.json` 要求的
revision 与包内实际 Chrome 路径一致。

配对后 token 会保存到 macOS Keychain 或 Windows Credential Manager；`agent.json` 只保存设备 ID 和中心地址，不保存 token。也可以通过 `PRACTICAL_TOOLS_AGENT_TOKEN` 临时注入 token（不会写回磁盘）。若系统凭据库不可用，Agent 会报错并拒绝明文回退。

## 构建

- macOS：`bash packaging/build_mac.sh`，产物为 `dist/PracticalToolsAgent-package/PracticalToolsAgent`；再将该目录压缩即可交付。
- Windows：在 Windows 命令提示符执行 `packaging\\build_windows.cmd`；本机无法生成或声称生成 Windows exe，脚本会调用 Windows 上的 PyInstaller。

Windows 上应额外执行 `py -3 -m patchright install chromium`，并在防火墙中允许 Agent 出站访问中心服务。

## 中心服务 Windows 主机

受飞连限制时建议使用免安装绿色版：在另一台 Windows 构建机生成完整 onedir 包，再将整个目录复制/解压到中心主机的普通可写目录（例如 `D:\PracticalToolsOnline` 或用户目录）。不注册 Windows 服务、不写注册表、不要求管理员权限。

受限中心主机可以在 Windows 上使用 `build_center_windows.cmd` 生成
`dist\\PracticalToolsOnline\\` onedir 包，再将 `center_windows.env.example` 复制为同目录 `.env`，
填写已有 PostgreSQL 的连接地址后运行 `start_center_windows.cmd`。默认监听
`0.0.0.0:18180`，与旧版主图协同服务互斥，不能同时启动两个占用 18180 的中心进程。

绿色版升级时只替换程序文件，保留 `.env` 和 `PRACTICAL_DATA_ROOT` 指向的数据目录。若飞连禁止从程序目录写入，将数据目录改到 `%LOCALAPPDATA%\\PracticalToolsOnline\\data`；`start_center_windows.cmd` 双击启动，关闭窗口即停止服务。

中心包不会携带数据库、用户密码、淘宝 storage state 或 COS SecretId/SecretKey；生产环境应由
中心主机的环境文件/密钥管理提供这些配置。绿色版启动器会在启动服务前自动执行 Alembic 迁移，无需手工操作；`PRACTICAL_AUTO_CREATE_TABLES=false` 仍表示服务本身不执行开发模式全量建表。

如果使用腾讯云 COS，中心服务负责生成预签名 URL，Agent 只执行上传和确认，不接触 COS 密钥。

## 标准 Windows 安装包

当前项目同时提供标准安装包工程：`packaging\\build_installer.cmd` 会在 Windows x64 构建机刷新当前源码的完整 staging，并用 Inno Setup 生成 `PracticalToolsOnline-Setup-v<版本>.exe`。如果本机有 NSIS，也可运行 `packaging/build_installer_nsis.sh`，直接生成同等用途的 Windows `Setup.exe`。安装包包含中心程序、随包 Python runtime、PostgreSQL 客户端/服务文件、迁移脚本和在线更新器；数据库密码、COS 密钥、业务数据库和浏览器状态不会打进安装包。

新机不需要预先建立 PostgreSQL。首次运行安装目录中的 `MigrationAssistant.cmd` 时，输入 PostgreSQL 默认端口 `5432`；若本机没有 PostgreSQL 监听，向导会询问是否使用随包的服务端组件初始化数据库集群并创建目标数据库。数据库集群和业务数据默认跟随安装盘，例如安装到 D 盘时分别位于 `D:\PracticalToolsOnline\postgresql\data` 和 `D:\PracticalToolsOnline\data`。中心网页端口仍是 `18180`，不要把 `18180` 或 `18100` 填作 PostgreSQL 端口。安装目录根部的 `StartCenter.cmd` 用于启动中心，它会调用随包 `runtime\\python.exe` 和 `portable\\START.cmd`，所以不应期待另一个独立中心 exe。

安装后运行 `BackupCenter.cmd` 在旧中心生成最终数据库备份，再运行 `MigrationAssistant.cmd` 在新机恢复。向导默认推荐“恢复 PostgreSQL .dump”，适合旧中心只能通过 Cloudflare Tunnel 对外服务的网络；也支持先校验再导入旧中心 `.ptcenter.zip`。在线 PostgreSQL 复制保留为高级人工路径，要求旧库与新机存在私网直连，不能通过 Cloudflare Tunnel 暴露 5432。

向导完成后可调用 `installer\\verify_center_install.py`，检查首页、页面收集、首页分析、认证、截图任务/进度/调度、分析面板、管理员状态和更新状态。真实淘宝登录、真实截图采集、Agent 心跳和 COS 连接仍需在新 Windows 主机现场验收。

## Mac 历史数据一键打包

在 Mac 旧程序所在环境运行 `packaging/export_legacy_data.command`（也可直接双击；默认读取 `$HOME/MAC策划实用小工具/数据` 与 `$HOME/MAC策划实用小工具/截图数据`），会在桌面生成
`PracticalToolsMigration-YYYYMMDD-HHMMSS.ptmigration.zip`。脚本只收集分析 cache/source、旧 SQLite、截图和店铺配置，自动排除 `browser_state`、cookies、`.env`、token/secret 文件，不改动旧数据，并在 `manifest.json` 写入每个文件的大小和 SHA-256。

将压缩包上传到中心后台的“历史数据迁移”，选择目标账号并点击开始即可；也可在命令行用
`python scripts/migrate_legacy_data.py --bundle 文件.ptmigration.zip --apply --owner-id 用户ID`。

## 管理后台与在线更新

管理员登录后打开 `/admin/`，可查看版本、Agent、COS 配置/连接测试、生成配对码、上传历史包和更新包。
更新包必须是 `practical-tools-online-update-v1` 格式，包含 `manifest.json` 与 `payload/` 下逐文件 SHA-256 清单；上传后会备份 `.env` 与 data 下的业务目录，写入待重启标记。服务端不会执行任意上传内容，也不会注册服务或写注册表；绿色版更新器只替换清单允许的程序文件，并保留 `.env`、数据库和业务数据。

Agent 的构建版本不再作为硬门禁。中心心跳会按独立协议版本判断兼容性；配置 `PRACTICAL_AGENT_MIN_PROTOCOL_VERSION` 后，只有协议不兼容才会触发自更新。`PRACTICAL_AGENT_UPDATE_MANIFEST_URL` 指向管理员控制的 HTTPS Agent 清单，清单应按平台返回 `version`、`protocol_version`、`platform`、`url`、`sha256`、`size`。Agent 会下载到本地 staging，校验后启动独立 worker；worker 等旧进程退出后替换文件，失败自动重新启动旧版本。macOS 二进制包只替换 Agent 可执行文件，浏览器目录仍由当前安装包独立管理；源码/Windows 绿色 Agent 更新 `agent/` 源码目录。

## 中心服务整合检查（发布前）

- 将 `frontend/shell`、`frontend/screenshot`、`frontend/analysis` 复制/部署到中心静态目录，并确保 `/`、`/screenshot/`、`/analysis/` 三个路径可访问；iframe 依赖这些路径。
- `/api/agent/screenshots` 的 `capture_id`、`brand`、`captured_at` 为查询参数时，Agent 已按查询参数发送；不要改成只接受 multipart 字段而未同步客户端。
- COS `initiate` 返回预签名 URL 后，完成上传必须把批次确认落成 `PracticalScreenshot` 元数据，否则图库列表不会出现该图片。
- 页面收集登录、打开文件夹、定时安装应转成 Agent 任务；仅返回网页成功响应不会在用户电脑上打开淘宝或 Finder/资源管理器。
- 头像接口应提供 `/api/auth/avatar`（GET/POST），否则集合页会安全回退到默认头像。

## 中心主机一键迁移

管理员进入 `/admin/` 后，在“中心迁移”区域点击“导出完整中心迁移包”。导出的
`.ptcenter.zip` 会把当前中心程序树（前端、后端、迁移、绿色启动器以及包内运行时）和
`practical_*` 业务表、分析数据、截图/素材一起收集，已有 Agent 的设备 ID 与 token 摘要会保留，
因此中心换机但域名不变时无需逐台重新绑定。

迁移包不会包含 `.env`、数据库连接密码、COS SecretId/SecretKey、网页会话、配对码、淘宝
Cookie、Agent 浏览器状态、日志或更新暂存。它包含账号密码哈希和 Agent token 摘要，仍应像备份一样
妥善保管。新中心准备好数据库和 `.env` 后，先用
`packaging\\import_center_migration.cmd 旧中心导出的.ptcenter.zip --inspect-only` 校验清单，再执行
`packaging\\import_center_migration.cmd 旧中心导出的.ptcenter.zip` 导入业务数据；目标数据库默认必须是空的，
确认合并已有业务数据时才显式加 `--allow-nonempty`。

也可在命令行导出：

```bash
python scripts/export_center_migration.py --output /安全位置/PracticalToolsCenterMigration.ptcenter.zip
```

导出会读取当前中心正在使用的数据目录，不会删除或移动旧中心数据。固定域名方案下，迁移完成后只需让域名
指向新中心、复制必要的 `.env` 配置并导入迁移包，前端 Agent 会继续使用原绑定。

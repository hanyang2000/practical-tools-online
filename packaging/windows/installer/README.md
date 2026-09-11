# Windows Setup 与迁移向导

本目录提供标准 `Setup.exe` 安装工程和首次启动迁移助手。

## 构建

可在 Windows x64 构建机执行：

```bat
packaging\build_installer.cmd
```

Windows/Inno 构建机需要：

- Python 3.11+
- 当前项目依赖
- 已准备好的 Windows Agent runtime/浏览器 payload
- Inno Setup 的 `ISCC.exe`

如果当前电脑有 NSIS，也可以在 macOS/Linux 或 Windows 上直接执行：

```sh
packaging/build_installer_nsis.sh
```

它使用 `makensis` 生成同等用途的 Windows `Setup.exe`，不依赖 Wine；当前项目已用这个入口在本机构建。Windows 上也可以直接运行该脚本，或继续使用 Inno Setup 的 `build_installer.cmd`。

安装包会从 `dist\PracticalToolsOnlinePortable` 重新收集中心程序、前端、Python runtime、PostgreSQL 工具、迁移脚本和更新器。当前源码版本必须与完整包一致；不能使用旧 staging 目录声称生成新版本安装器。

## 安装后的迁移模式

安装包不要求新机事先安装 PostgreSQL，也不要求先手工建立数据库。首次运行
`MigrationAssistant.cmd` 时，如果填写的本机地址和端口没有 PostgreSQL 在监听，向导会询问是否使用安装包内置的 PostgreSQL；确认 `Y` 后，它会在安装目录的
`postgresql\data` 初始化数据库集群、启动本机 PostgreSQL（默认端口 `5432`）并创建你填写的目标数据库。
如果新机已经安装并运行 PostgreSQL，则直接填写它的地址、端口、数据库名、用户名和密码即可，向导不会覆盖已有数据目录。

安装器开始复制文件前，会先按当前安装目录检查并停止对应的 Windows 服务，再强制关闭已运行的中心程序、控制台、启动脚本和安装包内置 PostgreSQL；如果目标进程或服务仍未退出，安装会停止并列出具体对象，不会结束其他目录下的 Python 或 PostgreSQL。

这里要区分两个端口：中心网页默认是 `18180`，PostgreSQL 默认是 `5432`；`18100` 不是本安装包的默认数据库端口。

安装完成后，安装目录根部应有以下入口：

- `StartCenter.cmd`：启动中心网页服务；它会调用随包的 `portable\START.cmd` 和 `runtime\python.exe`。
- 开始菜单中的“中心控制台”：优先使用 Windows 自带 `mshta.exe` 打开本地 `installer\CenterConsole.hta` 规范化图形窗口；只有 HTA 文件缺失时才回退到原生 exe 或 PowerShell WinForms。快捷方式和窗口标题栏统一使用带右下角中心服务器角标的程序图标。
  控制台是中文图形界面：上方独立显示中心/Tunnel 状态，中间实时显示中心程序 CPU、中心内存、主机 CPU、主机内存和安装盘剩余空间，下方提供 Tunnel 配置和运行动态。关闭控制台窗口会停止本控制台启动的中心和 Tunnel；窗口内也提供“全部停止并退出”按钮。
  状态、性能和 PID 由 `runtime\pythonw.exe` 无窗口运行 `installer\CenterController.py` 采集；该后台脚本不需要 Tk/Tcl，也不调用 WMIC、WMI、PowerShell、`cscript.exe`、`wscript.exe` 或可见终端。性能读取失败不会冻结控制台，未确认启动状态时按钮会在 15 秒内恢复。
  中心 launcher PID 固定记录在 `data\logs\center.pid`。因此中心被在线更新器重启、从 `StartCenter.cmd` 启动或控制台重新打开后，控制台都能识别同一个运行实例，避免更新成功后界面误报“已停止”。
  Tunnel 支持“临时快速 Tunnel”“命名 Tunnel”和“Token Tunnel”三种模式。只有 Cloudflare Token 时选择 Token Tunnel，在 Token 输入框粘贴 `eyJ...` 字符串即可，不需要填写 Tunnel 名称；Token 通过 `TUNNEL_TOKEN` 环境变量传给 cloudflared，不写入控制台日志。
- `MigrationAssistant.cmd`：运行迁移向导。
- `BackupCenter.cmd`：在旧中心生成 PostgreSQL 最终备份。

`Setup.exe` 是安装程序本身；中心服务采用绿色目录方式随包提供，但中心控制台可以从开始菜单直接以独立 GUI 启动，不需要手工打开终端。

从 0.6.1 开始，在线更新包会额外携带实际运行的 `installer\CenterConsole.hta`、`CenterController.py`、图标和 PowerShell fallback。更新 Worker 对 `installer` 采用仅覆盖清单文件的模式，不删除迁移助手、备份脚本或安装日志；更新后重新打开控制台即可使用新版界面。

双击安装目录中的 `MigrationAssistant.cmd`，向导支持：

1. 恢复 PostgreSQL `.dump`（推荐）：旧中心停机后生成最终备份，新机恢复到新建数据库，再启动验收。
2. 导入旧中心 `.ptcenter.zip`：先运行 `--inspect-only`，再执行正式导入；目标数据库必须已经有与旧中心相同 ID 的共享 `users`，业务表保持为空。
3. 仅导入旧主机程序数据：保留截图、分析数据、分析文件和店铺配置，清空旧 owner/Agent/job 关联；账号、Agent、调度和任务在新机重新创建。
4. 高级在线复制 PostgreSQL：仅适合旧库与新机有私网直连的情况。它不会通过 Cloudflare Tunnel 暴露数据库，也不会擅自修改旧主机配置；当前网络不满足时应返回选择 1。

旧中心生成最终数据库备份时，运行安装目录中的 `BackupCenter.cmd`。它会使用包内的 `pg_dump.exe`，并显示 `pg_dump` 的原始输出、退出码、备份文件路径和 SHA-256；如果备份失败，不要只看“pg_dump 失败”这一句，请把屏幕上原始输出或同目录的 `*-pg_dump.log` 保存下来。

向导不会把密码、COS 密钥、浏览器状态或 Agent token 写入安装包。`.ptcenter.zip` 的 `payload/program` 只是备份内容；程序本身由 Setup 安装，中心迁移导入模式只导入业务数据和持久化文件。

## 验收

向导可调用 `verify_center_install.py` 验证：

- `/`、`/screenshot/`、`/analysis/`
- `/api/health`、认证状态和登录
- 页面收集列表、任务状态、进度、调度状态和登录任务接口
- 分析状态、文件、指标和面板接口
- 管理员状态、账号、Agent 和 COS 配置接口

真实淘宝登录、真实采集、真实 COS 上传仍需在新主机上做现场验收。

## Cloudflare Tunnel 切换顺序

新机安装和恢复期间，不要启动新机的 Tunnel，也不要修改旧机正在使用的 Tunnel。完成本机验收后，再按以下顺序切换：

1. 确认新机 `http://127.0.0.1:18180/api/health` 正常，并用验收助手完成公开、登录、管理员和业务接口检查。
2. 停止旧中心服务；保留旧主机和最终备份，不要立即卸载。
3. 停止旧机的 `cloudflared` Tunnel 或移除旧 Tunnel 的运行服务。
4. 在新机配置同一个 Tunnel 指向 `http://127.0.0.1:18180`，再启动新机 Tunnel。
5. 用原外部域名再次运行验收助手；如果外部访问失败，先恢复旧机 Tunnel，排查完成后再切换。

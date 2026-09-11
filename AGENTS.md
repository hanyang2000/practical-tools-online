# 项目协作约定

## 本机 Windows 兼容环境

- 本机已配置 Wine 环境，可用于运行和验证 Windows 程序及安装包流程。
- 遇到 Windows exe 打包、安装包构建或 Windows 行为验证时，优先检查并使用 Wine；不要仅因为当前宿主系统是 macOS 就直接判断“无法生成或验证 Windows 产物”。
- 如果 Wine 环境确实无法完成某一步，再说明具体失败点和需要 Windows 构建机的部分。

## 发布与安装包约定

- 完整 Windows 安装包是 `dist/installer/PracticalToolsOnline-Setup-v<版本>.exe`；在线更新包是 `dist/updates/PracticalToolsOnline-update-v<版本>.zip`，两者不能混用。
- 发布版本以 `app/__init__.py` 的 `__version__` 为准；当前基线是 `0.6.1`。安装器、PyInstaller 文件版本资源和更新包 manifest 必须保持一致。
- 完整安装包优先使用 `packaging/build_installer.cmd`；macOS/Linux 可用 `packaging/build_installer_nsis.sh`，但生成 Windows 控制台 exe 时必须确认实际使用的是 Windows/Wine 构建环境，不能把 macOS 二进制放进 Windows 包。
- 安装包必须排除 `.env`、`data/`、`postgresql/data/`、COS 密钥、Agent token、Cookie、浏览器登录态和日志；安装到 D 盘时业务数据、PostgreSQL 数据和日志应跟随安装目录。
- 构建后必须检查版本、Windows PE/NSIS 格式、7-Zip 完整性、安装包清单和关键稳定文件；不要覆盖上一版稳定安装包，除非新包已完成对比和验证。
- 安装器安装前必须按安装目录精确识别并停止对应 Windows 服务，强制关闭已有中心程序、控制台、启动脚本和随包 PostgreSQL；只要仍有目标进程或服务无法关闭，就中止安装，禁止结束整台机器上其他目录的 Python 或 PostgreSQL 进程。
- 安装包开始菜单必须提供“中心控制台”独立 GUI 入口：Windows 原生控制台 exe 存在时优先使用；没有 Windows/Wine 构建条件时，优先使用 Windows 自带 `mshta.exe` 打开本地 `installer\CenterConsole.hta`，最后才回退到 PowerShell WinForms `installer\CenterConsole.ps1`，禁止要求用户手工打开终端。精简 runtime 可能没有 Tk/Tcl，不能把 `runtime\pythonw.exe` 直接当作 GUI fallback。
- 中心控制台的 PowerShell fallback 必须保持中文图形界面，支持 quick/named/token 三种 Cloudflare Tunnel 模式；Token 模式使用 `TUNNEL_TOKEN` 环境变量传递密钥，不把 Token 写入日志，并显示中心进程与主机总体 CPU、内存及系统盘剩余空间。
- 中心控制台 HTA 的状态、性能、启动和停止不得调用 `cscript.exe`、`wscript.exe`、WMIC、WMI、PowerShell、`Shell.Exec` 或可见终端。统一通过随包的 `runtime\pythonw.exe + installer\CenterController.py` 在后台运行，性能数据使用 Windows 原生 API；后台异常时只影响状态/指标显示，15 秒内必须恢复操作按钮，不得阻塞中心、Tunnel、打开网页、停止和关闭操作。
- “中心控制台”开始菜单快捷方式和 HTA 标题栏统一使用 `installer\CenterConsole.ico`；该图标必须保留程序主图标，并在右下角显示中心服务器服务角标。HTA 是当前规范化界面的首选入口，旧原生/Powershell 控制台仅作为文件缺失时的回退。
- 在线更新包必须将控制台运行资产映射到安装根目录的 `installer\CenterConsole.*`，并将 Windows launcher 映射到 `portable\portable_center.py`；两个目录均以覆盖模式更新，禁止清理同目录的迁移、备份、START/STOP 脚本和安装日志。中心 launcher PID 使用 `data\logs\center.pid` 作为安装级共享状态；在线更新重启或从其他入口启动中心后，控制台必须能够自动识别并接管。

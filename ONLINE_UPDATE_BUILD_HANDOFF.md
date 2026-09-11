# Practical Tools Online：在线更新包与 Windows 安装包交接说明

> 用途：将本文件交给另一个对话或协作者，作为当前项目在线更新包、完整 Windows 安装包和迁移部署流程的上下文。
>
> 项目目录：`/Users/hanyang/策划实用小工具在线版`

## 1. 先区分两类产物

### 在线更新包

在线更新包不是 EXE，而是经过清单和 SHA-256 校验的 ZIP。它用于已安装中心的程序代码更新，不应覆盖数据库和业务数据。

典型文件名：

```text
dist/updates/PracticalToolsOnline-update-v<版本>.zip
```

之前用于测试的两个包：

```text
dist/updates/PracticalToolsOnline-update-v0.5.10.zip
SHA-256: f71453b7870aa3149f209d586b0964d1ec9f536ea2b92e5d2816efb85d8ae96b

dist/updates/PracticalToolsOnline-update-v0.5.10-COS-test.zip
SHA-256: 1ee93644112706576a78b352dd68e27ad35e3a918cc5ad7083c20505f3343d78
```

`COS-test` 包使用不压缩 ZIP，主要用于触发旧版后台对大于 1 MB 文件的 COS 上传分支；它不是生产包格式要求。

### 完整 Windows 安装包

完整安装包才是 EXE，负责首次安装中心、运行时、PostgreSQL 工具和迁移助手。

典型文件名：

```text
dist/installer/PracticalToolsOnline-Setup-v<版本>.exe
```

它适用于新 Windows 主机首次安装或完整部署，不等同于在线更新包。

## 2. 当前版本状态

当前源码版本以以下文件为准：

```text
app/__init__.py
```

当前源码中的版本应检查 `__version__`。当前基线为 `0.6.1`，重新构建时安装器、在线更新包和 Windows 文件版本必须保持一致，不能继续沿用旧版本号。

完整 Windows 安装包命名：

```text
dist/installer/PracticalToolsOnline-Setup-v<版本>.exe
```

当前 0.6.1 完整安装包：

```text
dist/installer/PracticalToolsOnline-Setup-v0.6.1.exe
大小：218378024 bytes
SHA-256：267f32b8ff730ed2eb675d326af7ce2a3bc06fa875f9de3bda265ca7bb99b1db
```

当前 0.6.1 在线更新包：

```text
dist/updates/PracticalToolsOnline-update-v0.6.1.zip
清单文件：114
SHA-256：2cb11c53f88562305689b28ce0bdb303cce7f929f4ac97b3ae87fc815f34efd3
```

已有 0.6.0 安装若要获得“控制台也可在线更新”的新能力，应先运行 0.6.1 完整 EXE；旧版 0.6.0 校验器尚不认识新增的 `installer/` 与 `portable/` 更新目标。安装 0.6.1 后，后续更高版本在线更新可以直接覆盖控制台运行文件和 launcher。

0.6.1 使用重构后的中文 HTA 中心控制台、`runtime/pythonw.exe + installer/CenterController.py` 无窗口控制器和带右下角中心服务器角标的程序图标。旧 `CenterMetrics.vbs + cscript.exe` 方案已经删除，不能恢复。在线更新包会把受限控制台资产映射到安装根目录 `installer/`；更新 Worker 对该目录仅覆盖清单文件，不删除迁移/备份工具。中心 PID 固定写入 `data/logs/center.pid`，使控制台能够接管在线更新后重启的中心。

此前的 `0.5.10` 更新包是在源码版本为 `0.5.10` 时构建的，不能自动代表当前 `0.6.0` 源码。

检查版本：

```bash
python -c "from app import __version__; print(__version__)"
```

## 3. 在线更新包的实际构建入口

脚本：

```text
scripts/create_update_bundle.py
```

普通压缩更新包命令：

```bash
python scripts/create_update_bundle.py \
  . \
  dist/updates/PracticalToolsOnline-update-v<版本>.zip \
  <版本> \
  --notes "本次版本更新说明"
```

例如当前源码版本为 `0.6.0` 时：

```bash
python scripts/create_update_bundle.py \
  . \
  dist/updates/PracticalToolsOnline-update-v0.6.0.zip \
  0.6.0 \
  --notes "中心服务在线更新"
```

COS 大文件分支测试包：

```bash
python scripts/create_update_bundle.py \
  . \
  dist/updates/PracticalToolsOnline-update-v0.6.0-COS-test.zip \
  0.6.0 \
  --notes "测试后台 COS 大文件直传分支" \
  --store
```

这里的 `--store` 表示使用 `ZIP_STORED` 不压缩方式，使包更容易达到旧版后台大文件上传阈值。生产更新包通常不需要这个选项。

## 4. 在线更新包包含什么

脚本允许收集以下程序目录：

```text
app/
agent/
frontend/
migrations/
packaging/
scripts/
```

另外允许收集以下根目录文件：

```text
pyproject.toml
alembic.ini
.python-version
```

ZIP 内部结构应为：

```text
manifest.json
payload/app/...
payload/agent/...
payload/frontend/...
payload/migrations/...
payload/packaging/...
payload/scripts/...
```

每个程序文件都会写入 manifest，字段包括：

```json
{
  "path": "app/main.py",
  "size": 12345,
  "sha256": "..."
}
```

## 5. 明确不会进入在线更新包的内容

构建脚本会排除运行时数据和敏感配置，包括：

```text
.env
agent.json
data/
runtime/
storage/
cache/
logs/
update-staging/
browser_state/
secrets/
tokens/
*.pyc
*.pyo
__pycache__/
.DS_Store
```

因此在线更新包不会包含：

- PostgreSQL 数据库及 `postgresql/data`
- 截图业务数据
- 分析缓存和 Excel 源文件
- COS SecretId/SecretKey
- Agent token
- 淘宝 Cookie 或浏览器登录态
- 当前中心的 `.env`

在线更新的目标是替换程序文件，而不是迁移或重建数据。

## 6. 构建完成后的自动校验

`create_update_bundle.py` 构建后会调用：

```python
app.services.update_bundle.inspect_update_bundle()
```

校验内容包括：

1. ZIP 不为空且未超过大小限制。
2. ZIP 文件数量和解压后总大小在限制内。
3. 存在有效的 `manifest.json`。
4. 更新格式必须是：

   ```text
   practical-tools-online-update-v1
   ```

5. manifest 中的版本号格式正确。
6. 所有清单路径都属于允许目录。
7. 拒绝绝对路径、盘符路径、`..` 路径和反斜杠路径。
8. 拒绝符号链接。
9. ZIP 实际成员必须全部位于 `payload/` 下。
10. ZIP 成员必须和 manifest 一一对应。
11. 每个文件的大小和 SHA-256 必须匹配。
12. 必须包含 `app/__init__.py` 和 `app/main.py`。
13. manifest 版本必须和 `app/__init__.py` 中的 `__version__` 一致。

构建失败时应先修复源码或包内容，不要绕过校验直接上传。

## 7. 上传到中心后台后的处理流程

管理员在中心后台 `/admin/` 上传更新 ZIP 后，中心会：

1. 再次检查 ZIP、manifest、路径、文件大小和 SHA-256。
2. 备份 `.env` 和业务数据相关目录。
3. 写入待更新状态。
4. 由绿色版更新 Worker 等待中心进程退出。
5. 只替换 manifest 允许的程序文件。
6. 保留数据库、截图、分析文件、`.env` 和运行数据。
7. 重新启动中心。
8. 检查 `/api/health` 是否正常。
9. 如果替换、启动或健康检查失败，则使用备份回滚。

关键更新 Worker：

```text
packaging/update_worker.py
```

更新器不执行上传包中的任意程序，不会把上传包当作安装程序运行。

## 8. 完整 Windows EXE 安装包的构建

完整安装包需要在 Windows x64 构建环境中执行，入口是：

```text
packaging/build_installer.cmd
```

它会先刷新 Windows 完整 staging，再调用 Inno Setup 生成：

```text
PracticalToolsOnline-Setup-v<版本>.exe
```

完整安装包包含：

- 中心服务源码和前端
- Windows Python runtime
- PostgreSQL 程序文件和客户端工具
- 迁移助手
- 备份工具
- 在线更新 Worker
- Windows 启动脚本
- Windows Agent 相关组件

完整安装包同样不应包含：

```text
.env
postgresql/data/
data/
数据库现有内容
COS 密钥
Cookie
浏览器登录态
```

特别注意：安装器必须排除 `postgresql/data/*`，否则重新安装可能覆盖已经迁移好的数据库。

完整绿色包刷新脚本：

```text
packaging/build_release.py
```

构建绿色包时会保留 runtime 和 PostgreSQL 程序文件，但会排除正在运行的数据库数据目录和业务数据目录。

## 9. Windows 安装后目录约定

如果安装到 D 盘，推荐结构如下：

```text
D:\PracticalToolsOnline\
├─ StartCenter.cmd
├─ MigrationAssistant.cmd
├─ BackupCenter.cmd
├─ runtime\
├─ postgresql\
│  ├─ bin\
│  └─ data\                 # 首次初始化或恢复后才产生
├─ data\                    # 中心业务数据和日志
│  └─ logs\
├─ portable\
├─ packaging\
└─ app\
```

`StartCenter.cmd` 调用安装包内的 `runtime\python.exe` 和中心启动器，不要求另外存在独立的中心 EXE。

新主机首次运行 `MigrationAssistant.cmd` 时，可以初始化随包 PostgreSQL；不需要提前手工创建数据库。PostgreSQL 默认端口是 `5432`，中心网页端口是 `18180`，两者不能混填。

## 10. 迁移和在线更新的边界

### 中心换机

中心换机需要使用：

- 完整 Windows 安装包或绿色包
- PostgreSQL `.dump` 备份恢复，或旧中心 `.ptcenter.zip` 数据导入模式
- 原有业务数据目录/截图素材
- 新中心 `.env` 配置
- 新中心重新配置 COS
- 最终切换 Cloudflare Tunnel

### 已安装中心升级

已安装中心升级才使用在线更新 ZIP。在线更新包不负责：

- 搬迁 PostgreSQL
- 搬迁截图文件
- 搬迁账号密码
- 搬迁 COS 密钥
- 迁移淘宝登录态
- 代替完整安装包初始化新主机

由于 Cloudflare Tunnel 切换会影响旧中心对外访问，当前默认迁移路线是：旧中心停机备份 → 新机安装恢复 → 新中心本地验收 → 最后切换 Tunnel。PostgreSQL 在线复制仅适用于旧库和新机有可靠私网直连的高级场景。

## 11. 发布前必须执行的检查

每次重新生成更新包后，至少确认：

```text
[ ] app/__init__.py 版本已更新
[ ] manifest 版本与 app/__init__.py 一致
[ ] ZIP 内所有文件位于 payload/ 下
[ ] 清单文件数量和实际成员一致
[ ] 每个文件 SHA-256 可复算
[ ] ZIP 可完整解压
[ ] 没有 .env、token、secret、Cookie、browser_state
[ ] 没有 data/、storage/、logs/、postgresql/data/
[ ] 更新包中包含 app/main.py
[ ] 更新包中包含当前需要发布的 agent/ 和 frontend/ 改动
[ ] 旧中心版本低于新版本
[ ] 更新后保留数据库和业务数据
[ ] 更新 Worker 能等待旧进程退出
[ ] 更新后 /api/health 正常
[ ] 健康检查失败时可以回滚
```

如果是完整 Windows 安装包，还要额外确认：

```text
[ ] 安装器为 Windows PE 格式
[ ] 7-Zip/归档检查通过
[ ] 安装包不包含 postgresql/data
[ ] 安装到 D 盘时数据和日志不落到 C 盘
[ ] MigrationAssistant.cmd 的 PowerShell 文件为 UTF-8 BOM
[ ] StartCenter.cmd 的相对路径正确
[ ] 不会覆盖已有 PostgreSQL data
[ ] 中心控制台不包含 cscript/wscript/WMIC/WMI/Shell.Exec 性能轮询
[ ] CenterController.py 由 runtime/pythonw.exe 无窗口启动并可返回中心/Tunnel PID
[ ] CenterConsole.ico 与 CenterConsoleIcon.png 已进入安装包
```

## 12. 给后续对话的工作要求

请先检查以下文件再进行新的打包或修复：

```text
app/__init__.py
scripts/create_update_bundle.py
app/services/update_bundle.py
packaging/build_release.py
packaging/build_installer.cmd
packaging/build_installer_nsis.sh
packaging/update_worker.py
packaging/windows/installer/PracticalToolsOnline.nsi
packaging/windows/installer/PracticalToolsOnline.iss
packaging/windows/installer/PracticalToolsOnlineMigration.ps1
packaging/windows/installer/CenterConsole.hta
packaging/windows/installer/CenterController.py
packaging/windows/installer/CenterConsole.ico
packaging/windows/portable/START.cmd
```

不要：

- 把在线更新 ZIP 称为 EXE 安装包。
- 为了通过校验而把 `data/`、数据库或 `.env` 塞进更新包。
- 使用同一个旧版本号重复发布不兼容的新代码。
- 用完整安装包覆盖已有 `postgresql/data`。
- 把 COS SecretId、SecretKey、Agent token 或 Cookie 写进源码、manifest 或 Markdown。
- 仅凭“后台生成了 pending.json”就判定更新完成；必须确认中心实际重启并通过 `/api/health`。
- 恢复已作废的 `CenterMetrics.vbs + cscript.exe` 性能轮询；Windows 11 会把它反复显示为 Windows Terminal 窗口。

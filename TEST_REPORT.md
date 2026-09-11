# 阶段 10 独立测试报告

## 结论

阶段 10 本地独立验证通过：最终全量 pytest **65 passed, 1 skipped, 0 failed**；真实 2025/2026 Excel 的上传、状态、文件列表与全部分析面板新旧结果逐项相等；最终 Playwright 冒烟无 page error、无未知 HTTP/console 失败；最终重建的 macOS ARM64 Agent `--help` 通过。首轮发现的 P0/P1 均已修复并有回归覆盖，当前无可复现的本地业务缺陷。

本结论不代替真实 PostgreSQL/COS、Windows 实机、真实淘宝采集、备份恢复和 18180 互斥启动演练；这些仍是发布前外部门禁。

## 环境

- 日期：2026-08-31（Asia/Shanghai）
- 工作区：`/Users/hanyang/策划实用小工具在线版`
- 工作区 `.venv`：Python 3.9.6；项目声明要求 Python >=3.11，因 3.9 解析 `Annotated[str | None, ...]` 补充安装 `eval-type-backport==0.4.0`
- pytest 8.4.2，FastAPI 0.128.8，SQLAlchemy 2.0.52
- 真实 Excel：`~/.taobao_detail_extractor/analysis/source/2025天猫旗舰店页面数据监测.xlsx` 和 2026 对应文件
- Chromium：Playwright 固定 headless Chromium，1440×900、DPR 1
- 快速集成数据库：隔离 SQLite；本轮未连接生产 PostgreSQL 或真实腾讯 COS 私有桶

## 已完成的证据

1. 最终全量命令：`PYTHONPYCACHEPREFIX=/tmp/practical_tools_pycache .venv/bin/python -m pytest -q -ra`，**65 passed, 1 skipped, 0 failed**。唯一 skip 是源码 Agent `--help` 用例发现当前 `.venv` 未安装 patchright/requests；已改用最终打包二进制做沙盒外冒烟并通过。
2. Alembic 独立链：`upgrade head -> downgrade base -> upgrade head` 通过；预存 `users`/`user_sessions` 行不变，未创建或写入协同版 `alembic_version`。
3. 冻结命名收敛：实现以 `practical_*` 和 `practical_alembic_version` 为准，`ARCHITECTURE.md`/`TEST_PLAN.md` 已同步。
4. 真实 Excel 命令：`.venv/bin/python -m pytest tests/test_analysis_contract.py -q -ra`，**20 passed**。同时加载 2025/2026 两年真实工作簿，比对 `metrics` 与 overview/newold/anomaly/blocks/blocks_yoy/calendar/overall_all 面板，浮点容差 `1e-9`，其他键、顺序、空值精确相等。
5. Agent/截图安全契约：`.venv/bin/python -m pytest tests/test_screenshot_agent.py -q -ra`，**14 passed**，覆盖一次性配对码防复用、config/login/upload-session owner 隔离、COS SHA-256/幂等和 pause 命令。
6. Playwright 最终命令通过：未登录门禁、错误密码、成功登录、页面收集 iframe、首页数据分析 iframe 均 pass；`page_errors=[]`、`unknown_http_failures=[]`。默认头像的 404 与故意错密码的 401 明确列为 known。证据见 `tests/artifacts/online-shell-analysis.png` 和 `browser-report.json`。
7. 最终 macOS Agent：`dist/PracticalToolsAgent-package/PracticalToolsAgent --help` 沙盒外通过；仅输出 LibreSSL/urllib3 兼容警告，退出码为 0。
8. 全量收口后的两项定向回归：`.venv/bin/python -m pytest -q tests/test_screenshot_agent.py::test_one_time_pairing_code_can_be_exchanged_without_browser_cookie tests/test_static_packaging.py::test_mac_installer_uses_one_time_pairing_code_not_plaintext_token`，**2 passed**。匿名 Agent 兑换后的 `owner_id` 精确继承 `CapturePairCode.created_by`；macOS 安装脚本只读取 10 分钟一次性配对码并调用 `--pair`，不再收集、导出或回显 `PRACTICAL_TOOLS_AGENT_TOKEN`。

## 首轮失败、修复与复测

### 已修复 P0

- 真实 Excel 上传及全面板在请求退出时崩溃：`app/api/analysis.py` 同步 generator 依赖在 FastAPI threadpool 的不同 Context 中 reset `ContextVar` Token，`app/analysis/legacy_db.py` 报 `ValueError: Token was created in a different Context`。
- 分析 daily/fix 访问原未按 owner 过滤且写入原未设 owner，存在跨账号互读/互删。
- Agent config 原返回全部用户的 enabled shops/schedule；login status/done 原可读写他人 job。
- `CaptureUpload` 原无 owner/agent 归属，另一已配对 Agent 可重用他人 `capture_id` 的上传会话。
- 配对码原为固定环境值且可无限复用，不符合“短时、一次性”契约。

### 已修复 P1 / UI 契约

- 前端 GET `/api/auth/avatar`，后端该路径原只有 POST，Chromium 收到 405。
- 无数据首次进入分析页时发送 `year=`，FastAPI `year: int` 在 handler 前返回 422。
- shell 使用根相对 `icon.png`，中心根路由原返回 404。

## 命令失败 / 超时记录

- 首次 pytest 收集失败：Python 3.9 无法回溯 `str | None`；安装 `eval-type-backport==0.4.0` 后解决。
- 首轮全量 pytest 失败：18 failures（其中 6 为修复后 strict XPASS），定位 ContextVar、pair 401 和过期 xfail；全部修复/更新后最终 65 passed, 1 skipped。
- 新增的 4 个安全回归用例首轮 4 failed，修复后 screenshot/Agent 文件 14 passed。
- Playwright 首轮因 avatar 405、根 icon 404、空 `year=` 422 失败；第二轮仅剩空 `period=` 422；第三轮通过。
- 沙盒内 Chromium 首次因 macOS Mach rendezvous 权限被拒，按授权在沙盒外复测。
- **无测试命令超时**；最后全量 pytest 在会话中断前已返回 100% 和最终摘要。

## 仅属外部实机门禁

- 真实 PostgreSQL 共库 schema fingerprint、真实腾讯 COS 私有桶/CORS/过期签名、Windows 10/11 实机包、macOS 真实淘宝登录与采集、备份恢复及 18180 互斥演练属交付前外部门禁，本地隔离环境不得伪报为已通过。

## 本轮新增功能回归

- `tests/test_admin_update.py tests/test_migration_bundle.py tests/test_static_packaging.py`：24 passed、1 skipped。
- `tests/test_auth_architecture.py`：20 passed；Alembic 当前 head 为 `0004_admin_migration_tasks`。
- 迁移包实际产物：`dist/PracticalToolsMigration-20260831.ptmigration.zip`，32 文件、232,941,135 bytes，压缩后约 225MB；manifest 与逐文件 SHA-256 已由 `inspect_bundle` 校验。
- 额度恢复后的自动续跑调度创建被平台用量上限拒绝，未绕过平台限制；剩余外部实机门禁需额度恢复后继续。

## 2026-09-01 公网诊断、独立认证与 0.2.0 交付

- 实际公网地址 `https://collab.wnnttzy.kdns.fr/` 的根页、`/api/health`、`/api/auth/status`、页面收集和分析静态页均能正常返回；当时连接建立耗时约 10–18 秒，超出内置浏览器 30 秒导航预算的情况可复现。
- 前端已增加 15 秒请求超时、防重入、仅可见页轮询和恢复可见时刷新；头像/启动状态改为并行初始化，静态资源使用 1 小时缓存，`/api/*` 明确排除在静态缓存规则外。
- 0.2.0 改用 `practical_auth_accounts` / `practical_auth_sessions` 和独立 Cookie `practical_tools_session`；共享 `users` / `user_sessions` 只作业务 owner 锺点，其密码哈希与会话不被改写。首次管理员设置由唯一 `setup_marker` 保证只能成功一次。
- 最终全量 pytest 回归返回 100%，无失败（唯一 skip 仍为本机 Agent runtime 依赖门禁）。
- Windows 绿色完整包：`dist/PracticalToolsOnlinePortable-Windows-x64-20260901-v0.2.0.zip`，SHA-256 `35155572f91d92161cb2153c7a485dfaef7719e65737098f47ebbb33d4a7f9e6`。绿色版启动器在服务启动前使用随包 Python 自动执行独立 Alembic 迁移，迁移失败则拒绝启动。
- 管理后台更新包：`dist/PracticalToolsOnline-update-0.2.0.zip`，66 个白名单文件，SHA-256 `05fa6ee4f645cdcce27a803e6ec9b417193e26f1acd2e3371ab8fbba279e8360`。

### Windows 共享 PostgreSQL 启动修复（0.2.1）

- Windows 实机 Traceback 确认失败为 `psycopg.errors.ConnectionTimeout`；Alembic 尚未执行任何迁移，不存在数据库半迁移。
- 根因是主图程序关闭后其便携 PostgreSQL 一并停止，旧策划启动器只复用 URL，未启动同一数据库进程。
- 0.2.1 会保存并验证 `PRACTICAL_SHARED_CENTER_ROOT`，仅调用主图目录的 `pg_ctl.exe` 启动已有 `data/postgresql`；不初始化新库、不修改共享配置、不启动主图应用。
- 数据库启动/等待上限 45 秒，Alembic 上限 120 秒；各阶段显示进度，失败时指向 `data/logs/postgresql-startup.log`。
- Windows 绿色完整包：`dist/PracticalToolsOnlinePortable-Windows-x64-20260901-v0.2.1.zip`，SHA-256 `28576bae4e5b336b9f76e0e7e22156699d0111fb7971668d6ab2cef50ad35267`。
- 后台更新包：`dist/PracticalToolsOnline-update-0.2.1.zip`，SHA-256 `a82f8383c4a3de42c74d87b5c6a882669aefd5cd608d13a95b6baa04113d21c8`。

### Windows COS SDK 运行时修复（0.2.2）

- 线上管理后台「测试连接」返回「已配置 COS，但未安装 cos-python-sdk-v5」，确认 Bucket/地域/密钥已被读取，失败属于 Windows 随包 runtime 缺失 SDK。
- 生产依赖新增 `cos-python-sdk-v5>=1.9.44,<2.0`；绿色 staging 补齐 `qcloud_cos`、requests、xmltodict、six、crcmod 纯 Python 实现和 pycryptodome Windows x86-64 wheel。
- 回归断言 SDK METADATA/版本、所有依赖目录、Windows `.pyd` PE `MZ` 头，并禁止 staging 出现 macOS/Linux `.so`/`.dylib`。全量 pytest 返回 100%，COS/管理后台定向组合 16 passed、1 skipped。
- 完整绿色包：`dist/PracticalToolsOnlinePortable-Windows-x64-20260901-v0.2.2.zip`，SHA-256 `676a1ad3e703edb3dfd879d550edef7298aafeaf10b36b8aae68c6a1e22a3d1c`。本修复包含 Windows runtime 文件，不能仅依赖旧在线更新包。

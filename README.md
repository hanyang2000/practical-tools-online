# 策划实用小工具在线版中心服务

本目录是“页面收集 + 首页数据分析”的中心服务端。违禁词排查和主图一条龙服务不在本项目范围内。页面收集仍由 Windows/macOS 本地 Agent 执行，中心服务负责账号、任务、截图元数据和分析数据。

## 本地启动

需要 Python 3.11+。首次开发可直接使用 SQLite：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
export PRACTICAL_APP_ENV=development
export PRACTICAL_DATABASE_URL=sqlite:///./practical_tools.db
export PRACTICAL_DATA_ROOT="$PWD/.runtime"
export PRACTICAL_AUTO_CREATE_TABLES=true
python -m app.main
```

服务默认监听 `http://127.0.0.1:18180`。该端口与主图一条龙服务协同版相同，有限中心主机上两个服务不能同时启动。

生产 PostgreSQL 必须使用 `.env` 或系统环境变量注入，不能把连接密码、COS SecretId/SecretKey 写入代码。绿色版启动器会在启动服务前自动执行 `alembic upgrade head`，无需手工输入命令；Alembic 只管理 `practical_*` 表，`users`、`user_sessions` 由协同版管理并复用。

## 历史数据迁移

默认只读扫描并打印 JSON 对账：

```bash
python scripts/migrate_legacy_data.py \
  --legacy-data-root ~/.taobao_detail_extractor \
  --screenshot-root /path/to/策划实用小工具/screenshot
```

确认对账无误后才加 `--apply`：

```bash
python scripts/migrate_legacy_data.py --apply --owner-id <users.id> \
  --legacy-data-root ~/.taobao_detail_extractor \
  --screenshot-root /path/to/策划实用小工具/screenshot
```

`--owner-id` 是在线中心共用 PostgreSQL 中已有账号的 `users.id`。先登录数据库查询目标账号，例如：

```sql
SELECT id, username, display_name FROM users ORDER BY username;
```

建议始终填写该参数；不填写时数据会写入无主历史命名空间，供迁移对账使用，但不会显示给已登录账号。绿色版会自动执行迁移；开发 SQLite 若设置 `PRACTICAL_AUTO_CREATE_TABLES=true` 才会自动建表。

脚本会迁移分析 cache/source、分析日数据/异常修正、店铺配置、调度配置和截图文件，并输出数量、字节数、SHA-256。它以截图内容 SHA-256 生成稳定的 `legacy-*` capture_id，可重复运行而不重复生成元数据。脚本不会遍历、读取、复制或上传淘宝 `browser_state` 登录态；登录态必须继续留在本地 Agent。

COS 模式下脚本不会伪造远端对象或写入指向不存在对象的截图记录。请先使用 Agent/COS 直传接口上传对象，再调用 `/api/agent/screenshots/confirm` 完成校验和元数据入库。

## 备份、恢复与回滚

备份必须同时覆盖 PostgreSQL 和 `PRACTICAL_DATA_ROOT`：

```bash
pg_dump --format=custom --file=backup-$(date +%Y%m%d).dump "$PRACTICAL_DATABASE_URL"
tar -czf practical-files-$(date +%Y%m%d).tgz -C "$PRACTICAL_DATA_ROOT" storage analysis
```

恢复前停止服务，将文件恢复到临时目录并先在临时 PostgreSQL 验证：

```bash
pg_restore --clean --if-exists --dbname "$PRACTICAL_DATABASE_URL" backup-YYYYMMDD.dump
tar -xzf practical-files-YYYYMMDD.tgz -C "$PRACTICAL_DATA_ROOT"
alembic upgrade head
```

回滚应用代码时保持数据库向前兼容，先停止新服务、恢复上一版本代码和 `.env`，再启动；不要删除共享 `users/user_sessions`。只有确认上一版本不再需要新表时，才执行 `alembic downgrade`，因为降级会删除 `practical_*` 业务表。COS 对象删除不会由数据库回滚自动恢复，必须依赖 COS 版本控制/生命周期策略或独立对象备份。

## 验证

```bash
pytest
```

主要接口位于 `/api/auth/*`、`/api/analysis/*`、`/api/screenshot/*` 和 Agent 专用的 `/api/agent/*`。截图私有对象在 COS 模式下只通过短期签名 URL 访问。

登录后可调用 `POST /api/agent/pair-code` 生成有效期 10 分钟、仅可使用一次的 Agent 配对码；Agent 配对成功后服务端立即消费该码。Agent token 仅保存于 macOS Keychain/Windows Credential Manager，不能写入 `agent.json`。

Agent 的构建版本与协议版本分开处理：只要心跳返回协议兼容，即使构建版本不同也继续使用。若协议不兼容，中心会在配置了 `PRACTICAL_AGENT_UPDATE_MANIFEST_URL` 时返回 Agent 更新清单地址；Agent 只接受 HTTPS 清单、匹配当前平台且 SHA-256 校验通过的包，由独立更新进程等待旧 Agent 退出后替换并重新启动。清单示例：

```json
{
  "version": "0.4.5",
  "protocol_version": 1,
  "platform": "Darwin",
  "url": "https://updates.example.com/PracticalToolsAgent-Mac-arm64-v0.4.5.zip",
  "sha256": "<64 位小写 SHA-256>",
  "size": 12345678
}
```

清单和包应放在管理员控制的 HTTPS/COS 地址，不能让 Agent 从不受信任的本地路径或 HTTP 地址自更新。版本号仍保留在后台用于诊断，是否阻断 Agent 由协议兼容性决定。

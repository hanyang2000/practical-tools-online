# 在线版前端

`shell/`、`screenshot/`、`analysis/` 是从旧程序对应页面复制的静态资源。页面收集和首页数据分析的 DOM、CSS、ECharts 图表与交互保留原样；集合页只移除了本次范围外的“违禁词排查”和“主图一条龙服务”入口。

## 中心服务契约

集合页首次加载调用 `GET /api/auth/status`，未认证时显示登录卡片；登录提交
`POST /api/auth/login`（JSON `{username,password}`），成功后依赖中心服务下发 HttpOnly 会话 Cookie。退出调用 `POST /api/auth/logout`。

子页面沿用旧版相对路径 API：`/api/screenshot/*`、`/api/analysis/*`、
`/api/auth/avatar`。页面收集按钮仍调用旧接口形状，由中心服务将操作转成 Agent 任务；
网页不直接访问 localhost Agent。

集合页“后台自启”按钮保留原位置和文案，调用 `/api/shell/launchd/*` 兼容路由，
实际中心服务由中心主机持续运行；本版按钮仅保留原位置和外观，不会让浏览器尝试启停受限中心主机。退出程序仅退出当前在线会话，不停止中心服务。

页面收集的“定时截图”设置写入中心 `/api/screenshot/scheduler/*`，Agent 定期读取
`/api/agent/config`，在本机安装 launchd/schtasks；“登录淘宝”和“打开图片文件夹”同样需要
中心端创建 Agent 任务后才能在本机执行。

# 实验样本流转并发台

青屿检测样本中心（24 人）的样本移交复核工作台。原生 HTML/CSS/JavaScript 前端，Python FastAPI 后端，真实 PostgreSQL 两连接事务与行锁演练。

## 场景和模块

团队原先通过纸质交接单、SQL 截图和 Excel 交接。三处痛点：样本版本难统一、并发移交容易重复、超时后无法确认回执是否回滚。上游为样本接收记录，本站承接样本登记、移交申请、并发复核和证据导出，下游为理化检测室与复核员。

- 样本台账：批次、可用份数、保管位置，可录入。
- 流转申请：关联样本、指定数量和接收实验室；同一编号支持更正，按 `version` 认最新版本。
- 签收核销：签收单可乱序、先于申请登记；申请补齐或更正后由 worker 自动对账核销，展示已核数量与待核原因。
- 并发执行：Redis 队列、独立 Python worker、可设锁等待上限。
- 冲突证据：Backend PID、SQLSTATE、回执回滚、数量守恒与 JSON 导出。
- 管理员审计：登录、写入和执行动作。

## 启动

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

前端 http://localhost:18242 ，后端 http://localhost:19242/docs ，数据库 localhost:17742。

|角色|账号|密码|权限|
|---|---|---|---|
|admin|admin@qingyu.test|Demo2026!|全业务写入、执行、审计|
|operator|operator@qingyu.test|Demo2026!|创建样本、申请、执行、导出|
|viewer|viewer@qingyu.test|Demo2026!|只读、导出；写 API 返回403|

## 一天剧本

09:00 operator 登录；09:05 复核 `QY-261117-A` 近海水质留样 120 份，位置冷藏间 A-03；09:10 查看申请 `MOVE-1117-01`，接收理化检测室 30 份；09:15 新建 `shift-20261117` 流转申请；09:20 以该申请发起 250ms 并发演练；09:25 查看 A 提交 30、B SQLSTATE 55P03 回滚，90+30=120；09:30 导出 `trial-1.json`；09:40 viewer 复核证据；10:00 admin 查看审计。

## 算法与业务边界

这是**真实数据库行锁的受控演练**。每次以样本可用量建立独立 `trial_balances` 镜像行，不扣减原台账。A 使用 `SELECT FOR UPDATE` 持锁并写未提交回执；Python threading.Event 确保 B 在 A 持锁期间运行。B 先写自己的回执，再以指定 `lock_timeout` 请求同一行锁；PostgreSQL 返回 SQLSTATE 55P03 后完整 ROLLBACK。B 结束事件释放 A，A 扣减 remaining、增加 received 并 COMMIT。没有随机结果、sleep 假等待或假 SQL 计划。

输入：流转申请、50–2000ms 锁等待上限。输出：实际 PID、错误码、等待耗时、剩余量、接收量、回执数和 SHA256。口径为 `remaining + received == initial`、remaining非负、仅A回执一条；B回执必须为零。等待耗时受主机负载影响，SHA包含PID和实际耗时因此每次不同。算法输入和最终不变量可复现。失败态包括申请量超限、数据库错误、线程协调超时、队列不可用；页面显示错误并允许创建新演练。

单 worker 顺序运行任务，无生产重试/崩溃恢复调度；worker中断时 running 任务需要人工重建，不能用作生产样本库存扣减。

## 签收核销与版本化更正

签收（signoff）与移交申请（transfer）解耦入库，解决乱序到达、更正重算与并发争用。

- **乱序到达**：签收单以 `transfer_code` 字符串关联，无外键强制，申请未到也能登记，状态 `pending`、原因 `application_missing`。申请补齐后在同一事务锁账本并入队，worker 自动核销。
- **版本化更正**：`PUT /transfers/{id}` 不新增申请行，只把 `version += 1` 并覆盖数量/接收方；对账始终读最新版本。已核数量 `verified_qty` 单调递增、**绝不回退**。
- **超量处理**：最新额度不足以覆盖已核总量时，保留每单原已核数量并把状态标为 `pending`、原因 `quota_insufficient`；后续放量更正腾出额度后自动重算恢复。
- **并发安全**：登记签收、新建申请、更正都先 `INSERT ... ON CONFLICT` 占位再 `SELECT ... FOR UPDATE` 锁 `transfer_ledgers` 单行，对账任务也锁同一行，争同一余额时在锁上排队，配合 3000ms `lock_timeout`。
- **不漏核**：每个写事件在持锁事务内幂等入对账任务（partial unique index 保证同申请仅一个 `queued/running` 任务）；事件还把账本续跑游标 `last_cursor_id` 归零，强制下一轮全量重算。
- **中断续跑**：worker 启动时把遗留 `running` 任务重置为 `queued`；单轮扫描上限 `BATCH_LIMIT=500`，超限则落游标并在提交后排 `resume` 任务从游标继续。
- **重试幂等**：核销结果只取决于当前行数据，重跑结果不变；签收单编号唯一，重复提交返回 200 与原单，不重复入队、不重复核销。
- **待核原因**：`application_missing`（申请未到）、`queued`（等待调度）、`quota_insufficient`（额度不足），移交页与签收接口均返回中文说明。

测试（真实 PostgreSQL，无 mock）：`tests/test_reconcile.py`（8 场景 30 断言）、`tests/test_stress.py`（多线程混合并发与 30 次反复更正）、`tests/test_http_flow.py`（FastAPI + Redis worker 端到端 17 断言）。

## 工程说明

|服务|技术|职责|
|---|---|---|
|frontend|原生HTML/CSS/JS + Nginx|4业务页+登录，反向代理 /api/|
|backend|Python3.11/FastAPI/SQLAlchemy/PyJWT|校验、权限、审计、持久化|
|worker|Python3.11/psycopg|真实双连接锁竞争|
|db|PostgreSQL16|业务数据、隔离镜像账本|
|redis|Redis7|持久队列|

目录：`frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}`；`backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}`；`backend/scripts/seed_demo.py`；`backend/migrations/001_initial.sql`、`002_signoff_recon.sql`。
核心实体 Sample、Transfer（含 version）、Signoff、TransferLedger、ReconJob、Trial 各有模型、schema、服务、路由、API、store与页面；共享 table/badge/frame 组件。共享枚举 roles/run states/signoff states/recon states 位于后端 `src/constants/enums.py`、模型/权限引用和前端 `src/constants/enums.js`、frame/badge；演练状态枚举 queued/running/completed/failed，签收状态 pending/verified，对账状态 queued/running/done/failed。两个后端中间件：request_id/errors。

环境变量见 `.env.example`：COMPOSE_PROJECT_NAME、三端口、DB_PASSWORD、JWT_SECRET、SEED_DEMO。默认密码仅用于本地演示；JWT8小时有效。seed按自然键仅插入缺失行，不覆盖修改。所有5服务有健康检查，backend/worker依赖健康数据库/Redis，frontend依赖backend；db/redis/artifacts使用命名卷。停止及清空本项目数据：`docker compose down -v --remove-orphans`。

本地开发：Python3.11安装 backend/requirements.txt；设置 DATABASE_URL/REDIS_URL 后 `uvicorn src.main:app --reload`（backend目录）；worker `python -m src.worker`；前端用Nginx同源代理后端。前端双字体经Google Fonts加载，离线使用浏览器字体回退；业务不依赖外部字体。

## License
MIT，见 LICENSE。测试脚本、截图与实际结果位于源码外 `../../test-artifacts/gb-4103/`。

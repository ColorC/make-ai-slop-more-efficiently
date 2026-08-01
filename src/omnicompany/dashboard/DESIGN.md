<!-- [OMNI] origin=claude-code domain=dashboard ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:dashboard.design_doc.architecture.markdown" -->

# dashboard · 设计文档

## 状态
- **版本**: V2 (2026-05-09 大改 · 双进程拆分 + 控制面 / cc 进程目录重组, 详见 D4)
- **成熟度**: active
- **下一步**: 接入跨网络视图 (`network_views.py`) 与 LLM 辅助的根因分析面板 (按 `[2026-04-05]CROSS-NETWORK-INTEROP` 路线推进)

## V2 变更 (2026-05-09)
道路 [`docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md`](../../../docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md):

- 13 个散落根目录的 `*_api.py` 整体收进 [`controlplane/`](controlplane/), 去掉 `_api` 后缀
- `cc_wrapper/` 整目录搬到 [`ccdaemon/`](ccdaemon/), `cc_chat_bridge.py` → `chat.py`, `pty_service.py` → `pty.py`, `settings_installer.py` → `installer.py`, `api.py` → `pty_routes.py`
- 走偏过的 `sdk_bridge.py` 跟 `_smoke.py` 直接删 (无旧兼容)
- 双进程拆分: dashboard 进程 (8200, --reload) 跟 ccdaemon 进程 (8201, 默认不 reload), 反向代理走 [`controlplane/cc_proxy.py`](controlplane/cc_proxy.py) (HTTP + WebSocket)
- daemon 生命周期 `omni cc daemon start|stop|restart|status` + 启动脚本 [`scripts/start_dashboard_dev.py`](../../../scripts/start_dashboard_dev.py)
- 前端 [`frontend/src/lib/wsAutoReconnect.ts`](frontend/src/lib/wsAutoReconnect.ts) + [`frontend/src/components/ConnectionStatus.tsx`](frontend/src/components/ConnectionStatus.tsx), CcChatEditor 接入自动重连 + sessionStorage 草稿持久化
- dogfood 韧性测试 [`scripts/dogfood_dashboard_resilience_test.py`](../../../scripts/dogfood_dashboard_resilience_test.py) 6/6 PASS

## 核心目的
`dashboard` 提供 omnicompany 系统的**统一可观测性入口**。集中展示管线运行状态、Trace 拓扑、IDE 会话上下文与 Format/Router 注册表健康度。

**解决**：运维/开发无需串联多日志文件即可定位管线瓶颈、审查审计轨迹、监控本地格式漂移与 Peer 状态。
**不解决**：主动告警推送（属 `services/guardian` 或外部通知）、底层 trace 采集与聚合（属 `tracing` 与 `network/trace_aggregator`）、业务逻辑执行（本包不承载任何 Worker/Format 运行时代码）。

## 核心接口
### 后端路由与聚合 (Python)
- **`app.py`** — FastAPI 主应用组装、静态资源代理、全局中间件 — [app.py](app.py)
- **`assistant_api.py`** — 可观测性数据读取端点 (如 `GET /api/evo?limit=10`) — assistant_api.py
- **`assistant_context_builder.py`** — 跨模块数据聚合器 (消费 tracing JSONL / .omni/health) — assistant_context_builder.py
- **`assistant_db.py`** — 轻量本地存储与会话缓存 — assistant_db.py
- **`ide_api.py` + `ide_session.py`** — IDE 会话生命周期管理、上下文注入代理 — ide_api.py / ide_session.py

### 前端视图 (React / Vite)
- **`TraceGraph.tsx`** — React Flow 管线/Trace 拓扑图 — frontend/src/components/TraceGraph.tsx
- **`TraceList.tsx`** — TanStack Table 时序列表与过滤 — frontend/src/components/TraceList.tsx
- **`NodeDetail.tsx`** — 右侧详情面板与属性钻取 — frontend/src/components/NodeDetail.tsx

## 架构决策
### D1 · 前后端同包部署 (Monorepo-internal SPA)
**决策**: `frontend/` (React+Vite) 构建产物由 `app.py` 统一挂载为静态服务，前后端共享同一进程端口，API 严格限定 `/api/*` 前缀。
**理由**: Dashboard 属控制面而非高频数据面，独立微服务/容器会徒增部署拓扑与跨域调试成本。同包部署符合框架“控制面轻量化”原则，且便于就近归档与一键启动。

### D2 · 只读聚合层，严格隔离写入路径
**决策**: `assistant_context_builder.py` 仅消费 `tracing`/`bus`/`core` 暴露的读取接口与本地 `.omni/` 落盘。所有“修改/触发”请求直接代理至对应 domain/service 的专用 API，本包不执行任何写操作。
**理由**: 防止 dashboard 成为竞态瓶颈或单点故障源。可观测性模块必须保持无状态或弱状态，写入职责严格归属业务执行面，符合 OMNI-007 边界规范。

### D3 · 视图按可观测性实体拆分 (Graph / List / Detail)
**决策**: 前端严格区分 `TraceGraph` (拓扑关系)、`TraceList` (时序检索)、`NodeDetail` (属性钻取)，通过轻量状态管理同步当前选中的 `Trace ID`，避免数据耦合。
**理由**: 对应 `[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY` 定义的三类交互范式。拆分后 `TraceList` 可独立启用 TanStack Table 虚拟化渲染，`TraceGraph` 专注 React Flow 节点计算，避免单组件内存溢出与主线程阻塞。

### D4 · 控制面 / cc 进程级隔离 (2026-05-09)
**决策**: dashboard 拆成两个独立 uvicorn 进程 — `dashboard` 进程 (8200) 持有 [`controlplane/`](controlplane/) 全部 API + 反向代理, 开 `--reload` 安全自更新; `ccdaemon` 进程 (8201) 独家持有 [`ccdaemon/chat.py`](ccdaemon/chat.py) 的 claude-agent-sdk client 跟 [`ccdaemon/pty.py`](ccdaemon/pty.py) 的 winpty PtySession, 默认不 reload, 由 `omni cc daemon restart` 显式控制. 浏览器只连 dashboard, [`controlplane/cc_proxy.py`](controlplane/cc_proxy.py) 把 `/api/cc/*` HTTP + WebSocket 透传到 ccdaemon.

**理由**: dogfood 期间 AI IDE 在网页 chat 框里改 `controlplane/*.py` 必触发 dashboard reload, 单进程方案下整个 worker 重启会把所有 chat session 跟 SDK 子进程一起杀掉 — AI IDE 自己改代码改到一半进程就没了, chat 历史丢, 用户体验崩. 进程级隔离让两侧独立生命周期: 改控制面 → dashboard 自动 reload, daemon 不动, chat 不掉. 改 ccdaemon → 显式重启, 浏览器走 [`frontend/src/lib/wsAutoReconnect.ts`](frontend/src/lib/wsAutoReconnect.ts) 自动重连协议续展历史 (snapshot 帧由 daemon 在 ws accept 后第一时间发送, 重连时也会重发).

**验证**: [`scripts/dogfood_dashboard_resilience_test.py`](../../../scripts/dogfood_dashboard_resilience_test.py) 6 个场景全 PASS — 含 `dashboard_reload` (改 `controlplane/notes.py` daemon pid 不变), `daemon_restart` (显式 restart pid 换 dashboard 仍能路由), `ws_through_reload` (WS 桥接断后浏览器侧重连仍通).

## 数据流 / 拓扑 (V2, 2026-05-09)
```
[浏览器 / IDE 客户端]
      │ HTTP /api/* + WebSocket /api/cc/*/ws
      ▼
┌────────────────────────────────────────────────┐
│   dashboard 进程 :8200 (uvicorn --reload)      │
│   app.py + controlplane/* (本进程内)            │
│     ├─ /api/notes /api/plans /api/catalogue …  │
│     ├─ /api/v2/lock /sandbox /registry /llm …  │
│     ├─ /api/v2/ide /ide-sessions               │
│     └─ /api/cc/* → cc_proxy.py ───┐            │
└─────────────────────────────────────┼──────────┘
                                      │ httpx + httpx-ws
                                      ▼
┌────────────────────────────────────────────────┐
│   ccdaemon 进程 :8201 (默认不 reload)           │
│   ccdaemon/main.py + chat / pty / installer    │
│     ├─ /cc/chat/sessions → claude-agent-sdk    │
│     ├─ /cc/sessions → winpty PTY               │
│     └─ /cc/install → settings_installer        │
└─────────────────────────────────────┬──────────┘
                                      │
                                      ▼
                         claude binary 子进程 + hooks
```
- 浏览器永远只连 dashboard 一个端口, 反向代理对前端透明
- AI IDE 改 controlplane/*.py → dashboard reload, daemon 不动, chat 不断
- AI IDE 改 ccdaemon/*.py → `omni cc daemon restart`, 浏览器自动重连续展
- 所有 cc-session 元数据落盘 `data/cc_sessions.json`, 跨重启可恢复

## 已知局限
- **局限 1**: 当前仅支持本机 Trace 与健康档案，跨网络 Peer 状态（如 format 版本漂移、离线节点感知）缺失。 · 升级路径: 按 `[2026-04-05]CROSS-NETWORK-INTEROP` 计划新增 `network_views.py`，对接 `network/trace_aggregator.py` 的 `ReadTraceDeep` 流，前端扩展 `NetworkTopology` 视图组件。
- **局限 2**: `assistant_db.py` 采用轻量本地存储，多并发 IDE 会话或长期运行下 SQLite 锁竞争可能影响聚合延迟。 · 升级路径: 抽象 `SessionStore` 接口层，后续切换至 `core` 提供的统一持久化后端 (PostgreSQL/Redis)；当前阶段启用 WAL 模式与连接池缓解。
- **局限 3**: 前端未接入实时 WebSocket 推送，Trace 列表依赖手动刷新或短轮询，低延迟运维体验不足。 · 升级路径: 利用 `bus` 的 `EventBus` 能力，在 `app.py` 挂载 `/ws/updates` 端点，前端实现 `useWebSocket` Hook 替换轮询逻辑。

## 参考资料
- 关联计划: `docs/plans/_archive/[2026-03-28]SIX-PRIMITIVE-OBSERVABILITY/README.md` (前端组件规划 / API 定义)
- 关联计划: `docs/plans/_archive/[2026-04-05]CROSS-NETWORK-INTEROP/plan.md` (跨网络视图扩展路线)
- 关联源码: `src/omnicompany/dashboard/frontend/src/components/`
- 关联规范: `docs/standards/distributed-docs.md` (B域就近设计文档规则 / OMNI-034 合规)
- 兄弟包依赖: `src/omnicompany/tracing/DESIGN.md` (数据源) · `src/omnicompany/bus/DESIGN.md` (事件总线)
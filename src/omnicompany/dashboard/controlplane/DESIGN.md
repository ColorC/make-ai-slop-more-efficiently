<!-- [OMNI] origin=ai-ide domain=dashboard/controlplane ts=2026-05-09T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:dashboard.controlplane.design_doc.architecture.markdown" -->

# controlplane · 设计文档

## 状态
- **版本**: V1 (2026-05-09 立, 跟道路 [2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE 阶段二同步落档)
- **成熟度**: skeleton (骨架已立, 13 个 *_api.py 整体迁入 + cc_proxy 待阶段二完成)
- **下一步**: 阶段三 cc_proxy.py 反向代理对接 daemon

## 核心目的
controlplane 是 dashboard 主进程 (uvicorn :8200) 装载的所有控制面 API 集合. 包括只读聚合 (events / nodes / traces / health), 写入操作 (lock / sandbox / meta_io / registry), IDE 会话桥接 (ide_session 跟 bus), 以及把 cc 相关请求反向代理到 ccdaemon 进程的 cc_proxy.

**解决**: 把原先散落 dashboard 根目录的 13 个 `*_api.py` 收进单一控制面目录, 跟 chat / pty 这些**长生命周期 SDK 持有方**进程级隔离. 改控制面文件可开 `--reload` 自动生效, chat 不掉.

**不解决**: claude code 子进程持有 (属 `ccdaemon/`); 前端构建 (属 `frontend/`); 业务执行 (本目录里的 API 都是协调读写, 不跑业务管线).

## 核心接口

### 路由模块
按主题命名, 文件名不带 `_api` 后缀 (因为整目录已经叫 controlplane, 后缀冗余):

- **`annotations.py`** — KB 注解 — [annotations.py](annotations.py)
- **`catalogue.py`** — Teams + Materials catalogue — [catalogue.py](catalogue.py)
- **`events.py`** — 事件流跟节点拓扑 (从原 `app.py` 抽出, 阶段二完成时建好) — [events.py](events.py)
- **`ide.py`** — IDE 会话生命周期路由 — [ide.py](ide.py)
- **`ide_session.py`** — IDE 会话管理器 (bus 连接 / Session 数据模型) — [ide_session.py](ide_session.py)
- **`llm.py`** — LLM 调用档案查询 — [llm.py](llm.py)
- **`lock.py`** — G4 锁组 — [lock.py](lock.py)
- **`meta_io.py`** — 元 IO 命令组 — [meta_io.py](meta_io.py)
- **`notes.py`** — KB notes — [notes.py](notes.py)
- **`plans.py`** — Plans catalogue — [plans.py](plans.py)
- **`registry.py`** — 注册中心实体查询 — [registry.py](registry.py)
- **`sandbox.py`** — G5 沙盒 — [sandbox.py](sandbox.py)
- **`system.py`** — 系统信息 — [system.py](system.py)
- **`workers.py`** — Workers catalogue — [workers.py](workers.py)

### 反向代理
- **`cc_proxy.py`** — HTTP + WebSocket 反向代理路由器, 把 `/api/cc/*` 转发到 ccdaemon (`http://127.0.0.1:<port>` 由 `ccdaemon/lifecycle.py` 写的 `data/cc_daemon.port` 决定). 阶段二完成时立 — [cc_proxy.py](cc_proxy.py)

## 架构决策

### D1 · 路由 URL 不变, 仅 import 路径换
**决策**: 所有控制面 API 的 URL 前缀保持不变 (`/api/notes` / `/api/v2/sandbox` / 等). 只是 Python import 路径从 `omnicompany.dashboard.X_api` 改为 `omnicompany.dashboard.controlplane.X`.
**理由**: 前端跟外部脚本对路径无感知, 避免本次重组造成跨端连锁修改. URL 是协议层契约, import 路径是代码组织层. 重组不破坏协议.

### D2 · cc 相关请求统一走反向代理, 不在本进程持有 SDK
**决策**: dashboard 主进程内**不**装载 chat / pty router. 所有 `/api/cc/*` 通过 `cc_proxy.py` 转发到 ccdaemon (8201).
**理由**: claude-agent-sdk client 跟 winpty PTY 对象生命周期长, 占用子进程资源. 这些进程必须独立于 file watcher reload 周期, 否则 AI IDE 改控制面任意文件触发 reload 都会把 chat 打死. 进程级隔离 + 反向代理是 dogfood 韧性的硬要求.

### D3 · ide_session.py 留控制面 (跟 ccdaemon 不重复)
**决策**: `ide_session.py` 维护的是浏览器 IDE 端会话 (跟 IDESessionManager + bus 接 ide_events.db), 跟 cc-session (claude binary 子进程) 是两个不同概念. ide_session 留控制面, cc-session 全归 ccdaemon.
**理由**: ide_session 不持有任何子进程, 仅跟 SQLiteBus 通讯, 进程重启时只需重连 bus 即可恢复. 没必要拆出去.

## 数据流 / 拓扑
```
[浏览器]
   │ HTTP (/api/*) + WebSocket (/api/ide/* /api/cc/*/ws)
   ▼
[dashboard 主进程 :8200] ← 本包驻留进程
   ├─ controlplane/* (本进程内, 跟着 reload 自动更新)
   │   ├─ annotations / catalogue / events / ide / ide_session
   │   ├─ llm / lock / meta_io / notes / plans
   │   ├─ registry / sandbox / system / workers
   │   └─ cc_proxy ──── httpx / httpx-ws ────┐
   └─ static (vite build 产物)               │
                                              ▼
                                  [ccdaemon 进程 :8201]
                                  (chat / pty / hooks / installer)
```

## 已知局限
- **局限 1**: cc_proxy 多一跳, 流式延迟可能加几十 ms. 升级路径: 阶段二做 RTT 基线, 真扛不住降级浏览器直连 daemon.
- **局限 2**: 本进程开 `--reload` 后, in-memory `app.state.ide_bus` 跟 `app.state.ide_session_manager` 在 reload 时丢失, 浏览器 IDE WebSocket 会断开重连. 升级路径: 阶段六验证 IDE 自动重连体验, 不行就把 ide bus 也搬到 daemon 进程.
- **局限 3**: 13 个文件搬迁动了大量 import, 漏改一处启动直接挂. 升级路径: 阶段二完成后跑 `python -c "import omnicompany.dashboard"` 全 import 自检, 加 `omni dashboard health` 子命令做端点 smoke.

## 参考资料
- 关联计划: [`docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md`](../../../../docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md)
- 兄弟包: [`ccdaemon/DESIGN.md`](../ccdaemon/DESIGN.md)
- 父级: [`../DESIGN.md`](../DESIGN.md)

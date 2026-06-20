<!-- [OMNI] origin=ai-ide domain=dashboard/ccdaemon ts=2026-05-09T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:dashboard.ccdaemon.design_doc.architecture.markdown" -->

# ccdaemon · 设计文档

## 状态
- **版本**: V1 (2026-05-09 立, 跟道路 [2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE 阶段二同步落档)
- **成熟度**: skeleton (骨架已立, chat / pty 全套迁入待阶段三完成)
- **下一步**: 阶段三把 `cc_wrapper/` 全套搬入 + 重命名 + 重写 chat 路线

## 核心目的
ccdaemon 是 dashboard 体系内**Claude Code 子进程的独家持有方**. 单独跑一个 uvicorn 进程 (8201), 装载所有 claude-agent-sdk client / winpty PtySession / claude binary 子进程, 跟 dashboard 控制面进程**进程级隔离**.

**解决**: dashboard 控制面文件 (`controlplane/*.py`) 改动后开 `--reload` 自动生效, 不影响 ccdaemon 持有的 chat 会话; 反过来 ccdaemon 自身代码改动通过 `omni cc daemon restart` 显式触发, 浏览器走自动重连协议感知重启 + 历史续展, 不会出现"AI IDE 改 chat 后端把当前对话杀掉" 的自杀事故.

**不解决**: HTTP/WebSocket 反向代理 (属 `controlplane/cc_proxy.py`); CLI 入口 (属 `cli/commands/cc.py` 跟 `cli/commands/cc_daemon.py`); 业务逻辑 (chat / pty 内的真业务), 仅做生命周期 + 路由装载.

## 核心接口

### 进程入口
- **`main.py`** — uvicorn FastAPI app + lifespan, 装载 `chat_router` / `pty_router` / `installer_router` — [main.py](main.py)
- **`lifecycle.py`** — pid/port 文件管理 + 启动健康自检 + reload 模式探测 — [lifecycle.py](lifecycle.py)

### 业务模块
- **`sessions.py`** — `CcSession` 共同基类, 封装 `data/cc_sessions.json` 元数据协议 (id/kind/cwd/started_at/ended_at/claude_session_id/active_plan/exit_reason). 阶段三填写 — sessions.py
- **`chat.py`** — claude-agent-sdk 路线 chat session manager + 路由 + WebSocket. 阶段三从 `cc_wrapper/cc_chat_bridge.py` 重写迁入 — [chat.py](chat.py)
- **`pty.py`** — winpty 路线 PTY session manager + 路由 + WebSocket. 阶段三从 `cc_wrapper/pty_service.py` 跟 `cc_wrapper/api.py` 合并迁入 — [pty.py](pty.py)
- **`installer.py`** — claude code settings 安装/卸载工具. 阶段三从 `cc_wrapper/settings_installer.py` 迁入 — [installer.py](installer.py)
- **`hooks/`** — claude code SessionStart / PreToolUse / UserPromptSubmit 等钩子. 阶段三整目录从 `cc_wrapper/hooks/` 搬入 — [hooks/](hooks/)
- **`mcp_server.py`** — claude code MCP server 集成. 阶段三搬入 — [mcp_server.py](mcp_server.py)

## 架构决策

### D1 · 进程级隔离 (跟 dashboard 控制面拆开)
**决策**: ccdaemon 独立 uvicorn 进程, 监听跟 dashboard (8200) 不同的端口 (默认 8201). 浏览器只连 dashboard, 走 `controlplane/cc_proxy.py` 反向代理到 ccdaemon.
**理由**: dashboard 控制面文件高频改动 (写新 API / 调路由), 必须能开 `--reload` 自动生效. 但 chat session 持有 SDK client + claude binary 子进程, reload 触发 worker 重启等于把所有进行中对话杀掉. 进程级隔离让两侧独立生命周期, 是 dogfood 韧性的硬要求.

### D2 · daemon 默认不开 file watcher reload
**决策**: ccdaemon 自身代码改动 (`chat.py` / `pty.py` 等) **不**自动 reload, 必须用户显式 `omni cc daemon restart` 触发.
**理由**: AI IDE 在网页 chat 框里改 ccdaemon 自身代码时, 如果 daemon 自动 reload, 会出现"改到一半 reload 触发, 当前对话连同改动者一起死"的自杀事故. 显式重启给改动者一个明确的"我准备好接受重启"信号, 浏览器同时进入 reconnecting 状态, 重启完后自动续展.

### D3 · `data/cc_sessions.json` 协议保持兼容
**决策**: ccdaemon 接管后 `cc_sessions.json` 的 schema 不变 (id/kind=chat|pty/cwd/started_at/ended_at/claude_session_id/active_plan/...). [2026-05-03]CC-PLAN-SESSION-CONTEXT 的 active_plan 绑定协议跟 SessionStart hook 写入路径不动.
**理由**: 持有方换了, 但落盘协议是其他模块 (cli plan / hooks) 共同消费的契约. 协议改动 = 跨模块连锁, 跟"无旧兼容"决策的边界 (无兼容只针对内部代码风格, 不针对持久化协议) 切开.

## 数据流 / 拓扑
```
[浏览器]
   │ HTTP + WebSocket
   ▼
[dashboard 进程 :8200]
   ├─ controlplane/* (本进程内)
   └─ controlplane/cc_proxy.py
        │ httpx / httpx-ws 双向桥接
        ▼
[ccdaemon 进程 :8201] ← 本包
   ├─ chat.py (ChatSessionManager 单例)
   ├─ pty.py (PtyManager 单例)
   ├─ installer.py (settings install/uninstall)
   ├─ hooks/ (子进程钩子, 由 claude binary 启动时加载)
   └─ sessions.py (落盘 data/cc_sessions.json)
        │
        └─ subprocess: claude binary (SDK 走 spawn / winpty 走 PTY.spawn)
```

## 已知局限
- **局限 1**: daemon 异常崩溃后 SDK 客户端在内存 ; 重启后不能保证 SDK 接得回原 session_id 的对话上下文 (取决于 claude-agent-sdk resume 能力, 当前未验证). 升级路径: 阶段六 dogfood 验证 SDK resume 行为, 不行降级"history_summary 当 first message 喂回去".
- **局限 2**: Windows winpty 子进程跨进程归属未验证, daemon 死后子进程是孤儿还是被回收待测. 升级路径: 阶段六场景 4 (kill -9 daemon) 真测; 不行加 atexit hook 杀子进程 + restart 时扫 zombie.
- **局限 3**: WebSocket 反向代理多一跳, 流式 token 延迟可能加几十 ms. 升级路径: 阶段二做 RTT 基线压测, 真扛不住降级"浏览器直连 daemon" (CORS 配好, 跨端口直连).

## 参考资料
- 关联计划: [`docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md`](../../../../docs/plans/dashboard/[2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE/plan.md)
- 协议依赖: [`docs/plans/dashboard/[2026-05-03]CC-PLAN-SESSION-CONTEXT/plan.md`](../../../../docs/plans/dashboard/[2026-05-03]CC-PLAN-SESSION-CONTEXT/plan.md) (active_plan 绑定 / cc_sessions.json schema)
- 兄弟包依赖: [`controlplane/DESIGN.md`](../controlplane/DESIGN.md) (反向代理协议 / cc_proxy.py)
- 关联规范: `docs/standards/cli/cc_wrapper_hooks.md`

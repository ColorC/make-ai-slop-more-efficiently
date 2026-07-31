<!-- [OMNI] origin=codex domain=dashboard/ccdaemon ts=2026-07-10T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:dashboard.ccdaemon.design_doc.architecture.markdown" -->

# ccdaemon · 设计文档

## 状态
- **版本**: V2（2026-07-10，Claude Code / Codex 原生能力对齐）
- **成熟度**: production（chat / PTY / hooks / installer 已迁入并被 dashboard 反代）
- **当前边界**: daemon 持有有状态会话；原生 CLI 的项目 hooks 由各 runtime 直接从 `.claude` / `.codex` 加载

## 核心目的
ccdaemon 是 dashboard 体系内**有状态 agent 会话的独立持有方**。它单独运行 uvicorn（8201），持有 Claude SDK/PTY 与 ChatUI provider 会话，并把 Claude Code/Codex 生命周期 hooks 接到统一 identity/event/Guardian 设施；与 dashboard 控制面进程做进程级隔离。

**解决**: dashboard 控制面文件 (`controlplane/*.py`) 改动后开 `--reload` 自动生效, 不影响 ccdaemon 持有的 chat 会话; 反过来 ccdaemon 自身代码改动通过 `omni cc daemon restart` 显式触发, 浏览器走自动重连协议感知重启 + 历史续展, 不会出现"AI IDE 改 chat 后端把当前对话杀掉" 的自杀事故.

**不解决**: HTTP/WebSocket 反向代理 (属 `controlplane/cc_proxy.py`); CLI 入口 (属 `cli/commands/cc.py` 跟 `cli/commands/cc_daemon.py`); 业务逻辑 (chat / pty 内的真业务), 仅做生命周期 + 路由装载.

## 与 chatui 的分工（两栈边界）

仓里有两套"创建会话"栈，**功能不重复、都在服役**——不要再争论删哪套，也不允许发明第三套：

- **ccdaemon（本包，Python FastAPI :8201）** — lofa 手机端的会话 API，链路：lofa app → Caddy 12443 → dashboard 8210 → `controlplane/cc_proxy.py` 透传 `/api/cc/*` → ccdaemon `/cc/*`。同时是 BOSS SIGHT 的 Python 进程内 spawn 通道（`boss_sight/captures/routes.py`、`boss_sight/services/workflow_orchestrator.py` 直接 `import ccdaemon.chat`）。**lofa + BOSS SIGHT 双依赖，不能删。**
- **chatui（`../chatui/`，Node vendored CCUI :7348）** — web 驾驶舱的会话后端，驾驶舱"新建会话"UI 全部走这里，边界说明见 `../chatui/VENDOR.md`。

约定：新增 AI CLI 时两边都评估——lofa 手机端用得上，就在 ccdaemon 加 Python provider（`chat.py` 的 provider 分支）；web 驾驶舱用得上，就在 chatui 按 `../chatui/server/modules/providers/README.md` 加 Node provider。

## 核心接口

### 进程入口
- **`main.py`** — uvicorn FastAPI app + lifespan, 装载 `chat_router` / `pty_router` / `installer_router` — [main.py](main.py)
- **`lifecycle.py`** — pid/port 文件管理 + 启动健康自检 + reload 模式探测 — [lifecycle.py](lifecycle.py)

### 业务模块
- **`chat.py`** — Claude Code / Codex / omni_agent / controller chat session manager + WebSocket — [chat.py](chat.py)
- **`pty.py`** — winpty Claude CLI session manager + WebSocket — [pty.py](pty.py)
- **`installer.py`** — Claude Code settings + MCP 安装/卸载 — [installer.py](installer.py)
- **`codex_installer.py`** — Codex `.codex/hooks.json` 安装/卸载/状态；保留用户 hooks — [codex_installer.py](codex_installer.py)
- **`hooks/`** — Claude Code / Codex 共用最小会话指针、计划切换、写入保护和结束记录；完整工具记录仅按需诊断 — [hooks/](hooks/)
- **`mcp_server.py`** — Claude Code MCP server 集成；Codex 侧使用本机 config/MCP provider 设施 — [mcp_server.py](mcp_server.py)

## 架构决策

### D1 · 进程级隔离 (跟 dashboard 控制面拆开)
**决策**: ccdaemon 独立 uvicorn 进程, 监听跟 dashboard (8200) 不同的端口 (默认 8201). 浏览器只连 dashboard, 走 `controlplane/cc_proxy.py` 反向代理到 ccdaemon.
**理由**: dashboard 控制面文件高频改动 (写新 API / 调路由), 必须能开 `--reload` 自动生效. 但 chat session 持有 SDK client + claude binary 子进程, reload 触发 worker 重启等于把所有进行中对话杀掉. 进程级隔离让两侧独立生命周期, 是 dogfood 韧性的硬要求.

### D2 · daemon 默认不开 file watcher reload
**决策**: ccdaemon 自身代码改动 (`chat.py` / `pty.py` 等) **不**自动 reload, 必须用户显式 `omni cc daemon restart` 触发.
**理由**: AI IDE 在网页 chat 框里改 ccdaemon 自身代码时, 如果 daemon 自动 reload, 会出现"改到一半 reload 触发, 当前对话连同改动者一起死"的自杀事故. 显式重启给改动者一个明确的"我准备好接受重启"信号, 浏览器同时进入 reconnecting 状态, 重启完后自动续展.

### D3 · provider-neutral 身份台账 + legacy PTY 协议
**决策**: `data/cc_sessions.json` 继续兼容旧 PTY/chat 元数据；跨 provider 聚合与权威绑定统一读 `data/cc_session_bindings.json`，字段为 `provider + session_id`，`claude_session_id` 只作兼容别名。
**理由**: 不能把 Codex 冒充 Claude 塞进旧字段；同时不破坏已有 PTY 恢复数据。

### D4 · 共用 hook 实现，不共用 runtime 配置
**决策**: Claude 安装到 `.claude/settings.json`，Codex 安装到 `.codex/hooks.json`；命令通过 `--provider` 进入同一组 Python hook。
**理由**: 两端事件/输出/信任模型不同，复制 Claude settings 不能形成正确的 Codex 支持。

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
   ├─ installer.py / codex_installer.py
   ├─ hooks/ (由 native runtime 触发)
   └─ identity ledger + data/cc_sessions.json legacy metadata
        │
        └─ provider subprocess: Claude / Codex（按会话路径选择）
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

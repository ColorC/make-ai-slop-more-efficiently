<!-- [OMNI] origin=claude-code domain=services/agent ts=2026-05-04T15:05:00Z type=doc status=active belongs_to_service=agent -->
<!-- [OMNI] material_id="material:core.agent.design_document.md" -->

# agent · 设计文档

> 设计目的请看 [README.md](README.md). 怎么用请看 [SKILL.md](SKILL.md). 本文档专管**架构内部** (接口 / 决策 / 数据流 / 局限).

## 状态
- **版本**: V1.2 (2026-05-04 加 D9 — Wave 3 P1 真 spawn 骨架 · agent_spawn_factory.py + 3 种 sub-agent 类型 + asyncio.run 驱动)
- **成熟度**: active
- **下一步**: 阶段 C 迁移（把 13 个旧 AgentNodeLoop 子类从 runtime/agent/ 迁入）；Wave 5 NODE_PROMPT 复刻 cc 原文 + 真 LLM smoke

## 核心接口

- [workers/__init__.py](workers/__init__.py) — `ALL_WORKERS` (12 Worker：5 主 + 7 SingleTool)
- [formats.py](formats.py) — 10 个 Material 定义
- [loop.py](loop.py) — `AgentNodeLoop` 薄调度器
- [routers/prompt_builder.py](routers/prompt_builder.py) — `PromptBuilderRouter`
- [routers/context_compact.py](routers/context_compact.py) — `ContextCompactRouter`
- [routers/llm_call.py](routers/llm_call.py) — `LLMCallRouter`
- [routers/tool_dispatch.py](routers/tool_dispatch.py) — `ToolDispatchRouter`
- [routers/single_tool.py](routers/single_tool.py) — `SingleToolRouter` + 5 内置工具子类 (Glob/Grep/ReadFile/ListDir/Finish)
- [routers/bash.py](routers/bash.py) — `BashRouter` 通用 Bash 工具 (走 BashBus · workspace/危险命令/审计) · 子类 override `_validate_command` 加业务白名单
- [routers/agent_spawn.py](routers/agent_spawn.py) — `AgentRouter` (Wave 3 真 spawn 骨架, asyncio.run 驱动 sub-agent)
- [routers/agent_spawn_factory.py](routers/agent_spawn_factory.py) — `GeneralPurposeSubAgent` / `ExploreSubAgent` / `PlanSubAgent` + `build_default_subagent_registry`
- [routers/extract_result.py](routers/extract_result.py) — `ExtractResultRouter`

## 架构决策

### D1 — Router 化消除单体 loop

原 AgentNodeLoop 的 `_run_loop()` 做了 6 件事（prompt 装配 / 上下文压缩 / LLM 调用 / 工具分发 / 结果提取 / 事件落盘）全在一个方法内。拆成 6 个独立 Router 后，每个 Router 可单独测试、单独 replay、单独换实现。

### D2 — 必接 bus（硬校验）

构造本包任意 Router 必须传 `bus`（SQLiteBus 或 MemoryBus）。`bus=None` 且无 `ALLOW_NO_BUS=True` → `RuntimeError`。这是 2026-04-17 artcontest 事件（bus=None 导致所有事件静默丢失）的直接教训。

### D3 — AgentNodeLoop 是薄调度器 Router

`loop.py::AgentNodeLoop` 继承 `Router`，本身 < 100 行，只做轮次调度：实例化 6 个子 Router → 循环 [ContextCompact → LLMCall → ToolDispatch* → ExtractResult]。无业务逻辑。

### D4 — 上下游 LLM 节点必须贯穿 disambiguation_hint

（继承自本项目铁律）所有子类继承 AgentNodeLoop 时，若业务涉及仓库分析类任务，须在 PromptBuilderRouter 的 `build_initial_messages()` 里注入身份锚 (D5 of repo_architect)。

### D5 — SingleToolRouter 是权限门

SingleToolRouter 的 `run()` 先读 `context["permission_mode"]` 做权限检查，再执行工具逻辑。ToolDispatchRouter 按 tool_name 路由到具体 SingleToolRouter 子类，不直接调用。

### D6 — workers/ 封装为 Worker（Phase D 2026-04-21）

本 Team 的 routers/ 是已正确实现的新代码，无 _archive/ 需要。workers/__init__.py 直接包装 routers/ 内的 Router 类为 Worker，无循环导入风险（workers 引用 routers，旧 routers 不引用 workers）。

### D7 — default vs deferred 工具集分层（2026-05-04 第二波 P0）

对齐 claude code 真实行为: 默认载入 ~10 个核心工具到 LLM system tools (schema 注入), 其余 ~30 个 deferred (只通过 system-reminder 告知工具名, schema 不加载, LLM 想用必须先调 ToolSearch 拉 schema).

实现位置 [routers/__init__.py](routers/__init__.py):
- `DEFAULT_TOOL_ROUTERS` (10): Read / Edit / Write / Glob / Grep / Bash / PowerShell / Agent / Skill / ToolSearch
- `DEFERRED_TOOL_ROUTERS` (32): NotebookEdit / TodoWrite / WebSearch / WebBrowser / EnterPlanMode / EnterWorktree / Monitor / RemoteTrigger / PushNotification / ScheduleCron / AskUserQuestion / Sleep / Config / Snip / TerminalCapture / Brief / CtxInspect / LSP / REPL / MCP* / OverflowTest / SyntheticOutput / 等
- `ALL_TOOL_ROUTERS = DEFAULT + DEFERRED` (兼容旧调用方)
- helper: `get_default_tool_specs()` / `get_deferred_tool_names_with_descriptions()` / `lookup_tool_schemas(names)`

ToolSearchRouter ([skill_tools.py](routers/skill_tools.py)) 真按 cc 协议工作:
- `select:Name1,Name2` → 按名字精确拉 deferred schema
- `keyword` → substring 匹配 name + description
- `+required` → 必含词 + 可选词排序
- 输出 `<functions>{spec}</functions>` 块格式跟 cc 输出一致
- 支持 `ctx.deferred_tools` 注入 (Worker 可定制 deferred 子集)

### D14 — BashRouter 接 PersistentShellSession + WebFetch/Search 真验证 (2026-05-05 Wave 4 续 + Web 真接通)

BashRouter 加 `persistent: bool` 参数:
- persistent=true 走 `self._persistent_session` (lazy 起 PersistentShellSession), cd/export 跨调用持久
- persistent=false (默认) 走原 BashBus, 无破现有调用
- 危险命令黑名单 (rm -rf / format C: / fork bomb) 在 persistent 模式仍 enforce — 复用 BashBus._match_dangerous module-level regex
- 透传 ctx.abort_event (Wave 8 集成) — 命中 → 杀进程树 + raise
- 输出加 `[session_cwd=<path>]` hint 让 LLM 知道 cd 已生效

WebFetch / WebSearch 现状勘察确认实现完整 (不是 stub):
- WebFetchRouter: urllib + HTML 抽文 (剥 script/style) + 错误分类 + 500KB 物理截断
- WebSearchRouter: DuckDuckGo + Serper + 域名过滤 + dry_run

e2e 测试用本地 TCPServer fixture 验真 HTTP 流程 (不依赖外网): 9 条 WebFetch 真验 (HTML/JSON/text/404/500/500KB/DNS) + 7 条 WebSearch dry_run + 1 条真网络 marker.

LLM smoke (test_wave5c_llm_smoke.py): qwen3.6-plus 简单 PONG 调用通, 但实测暴露**真 bug** —
qwen 收到 Anthropic tools_spec 后**不发 tool_use** block, 改返 `<tool_code>` 文本. 跨厂 LLM 协议适配
缺口, 留 LLMCallRouter 修. xfail 标记保留作 known issue.

### D13 — Abort/Cancel 协议骨架 (2026-05-05 Wave 8 P3)

L7 之前没实现 — 长跑工具 (sleep 60 / 卡死 webfetch / 跑飞 sub-agent) 一旦启了, 外部没法
打断, 只能等 timeout 或 ctrl-c 整 Python 进程.

实施:
- AgentNodeLoop 加 `_abort_event: threading.Event` 实例属性 + `abort()` / `is_aborted()` /
  `reset_abort()` 公开 API
- build_tool_context 默认注入 `abort_event: self._abort_event` (跟 read_files 同 pattern,
  跨工具引用)
- 主循环 run() 每 turn 头 check abort, 命中 → emit agent.aborted + 走 ExtractResult
  stop_reason="aborted" (PARTIAL Verdict)
- DevBashRouter: proc.wait 改 polling wait (0.5s), 每次 check ctx.abort_event,
  命中 → 杀整棵进程树 + raise ToolExecutionError("ABORTED")
- PersistentShellSession: proc.communicate 阻塞不能直接 honor event, 加 watchdog
  thread 周期 (0.2s) check abort_event, 命中 → 杀进程树 + raise. 没 abort_event 不启
  watchdog (零开销)

设计选择:
- threading.Event 不 asyncio.Event — _execute 跑在 to_thread 线程, 跨 loop 不行;
  threading.Event 跨线程 + asyncio is_set() 都 OK
- watchdog vs polling — communicate 阻塞用 watchdog 更准, proc.wait 阻塞改 polling 简单

测试 10/10 通过 (DevBashRouter / PersistentShell 真起 sleep 60 + abort + 杀树验证).

未做:
- cc AbortController 完整粒度 (cc 工具结束后还能 abort 后续, omnicompany 是 turn 边界)
- 跨工具 trace_id 串联
- cache_control 字段管理

### D12 — 主 agent (NativeIdeAgent) 真接通 Wave 3 + Wave 5+7 (2026-05-05)

之前 Wave 3 (subagent_registry) + Wave 5+7 (read_files Read→Edit 状态机) 都做了, 但
[dashboard/native_agent.py::NativeIdeAgent](../../../../dashboard/native_agent.py) override 了
`build_tool_context` 没调 super(), 缺这两字段, 子 router 协议在主 agent 失效 — 一直是 dangling 模块.

修法 ([dashboard/native_agent.py](../../../../dashboard/native_agent.py) build_tool_context):
- 加 `read_files: self._read_files` (Wave 5+7 Read→Edit 状态机)
- 懒构 `_subagent_registry_cache` 用 build_default_subagent_registry(bus=...), 注入 ctx
- 强制 `register_tool("bash", DevBashRouter)` 解决 BashRouter / DevBashRouter 名字撞 (auto_register 顺序不定时拿到 BashRouter, 实例化缺 bash_bus= crash)

测试 (tests/dashboard/test_native_agent_integration.py) 7/7:
- read_files 真注入 (跨 turn 同引用)
- subagent_registry 真注入 (3 种 sub-agent 类型 全 callable)
- AgentRouter 用主 agent ctx 干跑通 (整链路 wiring 通)

未做:
- 真 LLM smoke (NativeIdeAgent 真启 + qwen 真用 Agent 工具 + 真 Read→Edit 全流程)
- Wave 4 PersistentShellSession 接 BashRouter / NativeIdeAgent

### D11 — 9 default 工具 prompt 1:1 复刻 (2026-05-05 Wave 5 续)

之前 BashRouter / PowerShellRouter / SkillRouter / AgentRouter prompt 是简版自描述, 不是给 LLM 的指令. cc 工具 prompt 重点是规范 LLM 行为 (避免拿 bash 干 Read 该干的事 / 不用 PS 干 file ops / 调 skill 必须执行不是嘴说 / 写 sub-agent prompt 像智识同事的 brief).

实施 (用户 2026-05-05 指示"工具+协议通用优先, 用户交互降级"):
- Bash prompt 1:1 复刻 cc BashTool/prompt.ts::getSimplePrompt 静态部分 + omnicompany 独有约束 (find 禁令引用 357 zombie / 反斜杠路径拒 / dash-as-dir / 双盘符)
- PowerShell prompt 1:1 复刻 cc PowerShellTool/prompt.ts (edition 5.1 兼容版 — cc 也是这策略当 edition 未知)
- Skill prompt 1:1 复刻 cc SkillTool/prompt.ts (含 BLOCKING REQUIREMENT)
- Agent prompt 含 cc 核心智慧 (Brief like a smart colleague / Never delegate understanding / Trust but verify) + omnicompany 默认 registry 列表
- Glob/Grep 修小处: "multi-step exploration" (Wave 3 前占位) → "the Agent tool" (Wave 3 已通)

跳过段:
- sandbox / undercover / claude.ai PR 工作流 (claude.ai 商品特有)
- background task / run_in_background (omnicompany 没设施)
- forkSubagent (cc 特有 LLM context 共享机制)
- isolation: "remote" (CCR 特有)
- PlanMode / AskUserQuestion 系列 (用户 2026-05-05 指示降级 — omnicompany 是自动化管线, 不是人类交互聊天)

测试 58/58 通过.

### D10 — Read→Edit 状态机 (2026-05-04 Wave 5+7 部分)

之前 FileEditRouter 不强制 "先 Read", 但 cc 工具层有这个保护. 防 LLM 凭幻觉编辑没读过的文件.

实现:
- AgentNodeLoop.__init__ 加 `_read_files: set[str]` 实例属性
- AgentNodeLoop.build_tool_context 默认返 `{"read_files": self._read_files, ...}`
- FileReadRouter._execute 成功后 `ctx.read_files.add(abs_path)`
- FileEditRouter._execute 头部检查 abs_path ∈ ctx.read_files, 不在 → 报"先 Read"错
- WriteFileRouter._execute 成功后 add 进 read_files (Write→Edit 流不破)
- 老 ctx 没 read_files 属性 → 跳过检查 (向下兼容子类自管 ctx)

副作用:
- FileRead L1 prompt 完整复刻 cc 原文 (含 image/PDF/Jupyter/screenshot/empty 5 行)
- FileRead L2 边界: image / PDF / Jupyter 报清晰错误指引 (诚实, 这版未真支持多模态)
- INPUT_SCHEMA 加 pages 字段 (PDF 用)

### D9 — AgentRouter 真 spawn 骨架 (2026-05-04 第三波 P1)

之前 `AgentRouter._execute` 调 `agent.run_sync(prompt=, description=)`, 而 AgentNodeLoop
根本没这方法 — 占位假 API. 任何真 ctx.subagent_registry 注入都会 crash.

修法 ([routers/agent_spawn.py](routers/agent_spawn.py) + [routers/agent_spawn_factory.py](routers/agent_spawn_factory.py)):
- 改 `asyncio.run(agent.run({"task": prompt, "description": ..., "trace_id": ...}))`,
  从 Verdict.output["text"] 提取 final 文本; FAIL/PARTIAL 加 `[sub-agent KIND]` 前缀透传
- factory 合约: `factory(model=...) -> AgentNodeLoop` 实例; 必须 callable; 不可变占位字符串
- 默认 registry 由 `build_default_subagent_registry(*, bus)` 构建, 含 3 种类型:
  * `general-purpose` — 8 工具子集 (Read/Edit/Write/Glob/Grep/PowerShell/Skill/ToolSearch)
  * `Explore` — 只读 (Read/Glob/Grep)
  * `Plan` — 只读 + 规划 prompt
- BashRouter 暂不入 sub-agent 默认集 (需 bash_bus 注入跟 AgentNodeLoop 标准实例化 `R(bus=bus)`
  合约不兼容, Wave 5 解)
- agent_spawn_factory 不在 `routers/__init__.py` 里 re-export — import AgentNodeLoop 跟
  loop.py import routers 形成循环, 消费方直接从子模块 import

未做 (留 Wave 5):
- NODE_PROMPT 跟 cc 原文逐字段对照
- 真 LLM smoke (跑 qwen-3.6-plus 验证 sub-agent 真能解任务)
- IDEAgentLoop / DevAgent ctx.subagent_registry 注入

### D8 — BashBus 子进程管理 (2026-05-04 第一波 P0, 357 僵尸事故修复)

事故: 历史 `subprocess.run(timeout=)` 在 Windows + git bash 下 timeout 不杀子进程, 累积 357 僵尸 (find / grep / tail 跑了最久 16.7 天).

修复 ([runtime/buses/bash_bus.py](../../../../runtime/buses/bash_bus.py)):
- subprocess.run → Popen + 自管 timer
- Windows: CREATE_NEW_PROCESS_GROUP + taskkill /F /T /PID 杀整树
- POSIX: start_new_session=True + os.killpg(SIGKILL) 杀整 group
- 全局 `_ACTIVE_PROCESSES` (WeakSet) 注册表 + atexit hook 兜底清理
- find 命令禁令拦截 (`_check_find_forbidden`), 替代用 Glob / Grep

跟 DevBashRouter 已有 Popen + taskkill 模式一致, 现在 BashBus 主体也对齐.

## 数据流 / 拓扑

```
外部调用 AgentNodeLoop.run(input_data)
  │
  ├─ PromptBuilderWorker (agent.prompt-request → agent.prompt-built)
  │
  └─ 循环:
       ContextCompactWorker (agent.context-request → agent.context-compacted)
         → LLMCallWorker (agent.llm-request → agent.llm-response)
           ┌── [stop_reason=tool_use] ──────────────────────────────────┐
           │   ToolDispatchWorker (agent.tool-request → agent.tool-response)
           │     → SingleToolWorker 子类 (Glob/Grep/ReadFile/ListDir/Finish)
           │   └── 回循环顶部
           └── [stop_reason=end_turn/finish/max_turns]
               ExtractResultWorker (agent.result-request → agent.result-final)
               → 返回 Verdict 给外部
```

## 已知局限

1. **阶段 C 迁移未完成** — 13 个旧 `runtime.agent.AgentNodeLoop` 子类仍在原位，尚未迁移到本包接口。删除旧文件需等阶段 C 全部完成。

2. **L4 LLM 上下文压缩为 stub** — ContextCompactRouter 的 L4（大上下文 LLM 摘要压缩）尚未实现，当前只有 L1-L3 三层。

3. **ToolDispatchRouter 的权限审计仅基础** — 当前 permission_mode 检查粒度粗（default/auto），无细粒度工具级权限策略。

4. **SingleToolRouter 子类集有限** — 内置 6 个（Glob/Grep/ReadFile/ListDir/Bash/Finish）。**BashRouter (2026-04-23 加入)** 是通用基类, 底层走 BashBus 获得 workspace + 危险命令 + 审计; 业务子类 override `_validate_command` 加白名单即可 (例 config_service 的 p4/python/git 白名单). 更多业务工具 (viewImages 等) 仍须在使用 AgentNodeLoop 的 Team 内定义 (如 demogame/ux 的 SafeBashRouter 保持独立, 未合并到新 BashRouter, Phase B.1 是 additive 不 breaking)。

## 新哲学对齐（Phase D · 2026-04-21）

### Material 层（F-16/17/18/19）

| 条款 | 状态 | 说明 |
|---|---|---|
| F-16 kind 三分 | ✅ | prompt-request=source（loop 入口）; result-final=sink（loop 出口）; 其余 8 个=internal（loop 内中间态）|
| F-17 Workspace 大明文 | N/A | agent loop 本身不写 workspace 文件；业务子类各自管 |
| F-18 Job × Material 绑定 | N/A | 传统架构，待新 Runtime |
| F-19 kind.* tag 必填 | ✅ | Phase D 修正：10 条 Material 全部补 kind.* |

### Worker 层（R-18~R-25）

| 条款 | 状态 | 说明 |
|---|---|---|
| R-18 粒度 | ✅ | 6 主 Router + 5 SingleTool 各有独立职责 + FORMAT 边界 |
| R-19 Agent Worker 升级 | ✅ | AgentNodeLoop 本身就是 R-19 的目标实现 |
| R-20 Agent Worker 三件套 | ✅ | PromptBuilderRouter + ExtractResultRouter + TOOL_ROUTERS (SingleToolRouter 子类) = 三件套已实现 |
| R-21 Diagnosis Agent Worker | N/A | |
| R-22 WorkspaceWriterWorker | N/A | |
| R-23 Verdict.output 平铺 | ✅ | ExtractResultRouter 输出平铺 |
| R-24 FORMAT_IN_MODE | N/A | 各 Worker FORMAT_IN 为单 str |
| R-25 子 job | N/A | |

### Team 层（P-13~P-17）

| 条款 | 状态 | 说明 |
|---|---|---|
| P-13 声明即消费 | ✅ | 各 Router 只消费 FORMAT_IN 声明的 Material |
| P-14~17 Workspace 目录 | N/A | |

**结论**: F-19 缺口已修正。R-19/R-20 是本 Team 的设计初衷（自身就是 AgentWorker 三件套的载体）。

## T8 Agent Spawn Surface Authority - 2026-06-13

`spawn_surface.py` is the single registry for supported agent spawn paths.
New agent usage must fit one of the canonical entries below; adding another
launcher requires updating this registry and its tests first.

| Entry | Use When | Implementation Rule |
|---|---|---|
| `agent_tool` | An `AgentNodeLoop` needs an isolated in-process sub-agent from `ctx.subagent_registry`. | Register the sub-agent factory and call the `Agent` tool. |
| `external_worker_run` | CLI/API/workflow needs a synchronous audited Codex or Claude Code run. | Build `ExternalAgentRunRequest`; never call provider adapters directly. |
| `controller_spawn` | BOSS SIGHT needs an async plan-bound worker that returns a subagent id immediately. | Use `spawn_subagent` / `omni worker spawn`; keep plan guard, injection, and wakeup events in one path. |
| `workflow_run` | A deterministic workflow coordinates existing workers or controller spawns. | Extend workflow nodes and reuse the existing worker/controller surfaces. |

Adapter entries are deliberately not a fifth launch path:

- `external_worker_as_agent_tool` exposes external workers through
  `build_external_agent_subagent_registry`, under the `Agent` tool surface.
- `teamrunner_external_node` wraps `ExternalAgentRunRequest` for TeamRunner DAGs.
- `internal_agent_loop` names `AgentNodeLoop` as the implementation base, not a
  launch command.

Every audited run surface should carry `agent_spawn_surface`,
`agent_spawn_entry`, `agent_spawn_kind`, and `agent_spawn_launch_surface`
metadata from `spawn_surface.py`.

## 参考资料

- [loop.py](loop.py) — AgentNodeLoop 薄调度器
- [routers/](routers/) — 6 个子 Router 实现
- [workers/](workers/) — 11 个 Worker (Phase D 封装)
- [docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md](../../../../../docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md) — 迁移计划
- [../hypothesis/routers.py](../../_learning/hypothesis/routers.py) — 调用本包的使用示例

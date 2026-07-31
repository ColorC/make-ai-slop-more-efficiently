<!-- [OMNI] origin=ai-ide domain=services/agent ts=2026-05-04T15:05:00Z type=doc status=active agent=ai-ide belongs_to_service=agent -->
<!-- [OMNI] summary="agent service 自我叙事 README — AgentNodeLoop Routerization 新家. 把原 686 行单体 _run_loop 拆 6 子 Router (PromptBuilder/ContextCompact/LLMCall/ToolDispatch/SingleTool/ExtractResult), 必接 bus" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. 抽核心目的到 README, DESIGN 留架构性内容" -->
<!-- [OMNI] tags=readme,agent,core,agent-loop,self-narrative -->
<!-- [OMNI] material_id="material:services._core.agent.readme.self_narrative.md"-->

# agent · AgentNodeLoop 现代版

> 把原 686 行单体 `runtime/agent/agent_node_loop.py` 拆为 6 个子 Router (PromptBuilder / ContextCompact / LLMCall / ToolDispatch / SingleTool / ExtractResult), **每次 Router.run() 在 bus 上发 input + output 两条事件**, trace_id 贯穿, 实现完整审计链. 必接 bus, 不接抛 RuntimeError.

---

## 这是什么

agent 是 omnicompany 的 **AgentNodeLoop Routerization 新家**. 它是 `AGENT-NODE-LOOP-ROUTERIZATION` plan 的实施 — 把旧 [`runtime/agent/agent_node_loop.py`](../../../../runtime/agent/) 单体 loop 重构成可观测 / 可落盘 / 可 replay 的 Router 组合.

形态: **薄调度器 + 6 子 Router**:
- `AgentNodeLoop` ([loop.py](loop.py)) 是 Router, < 100 行, 只做轮次调度
- 6 子 Router 各自独立:
  - **PromptBuilder** (prompt 装配)
  - **ContextCompact** (上下文压缩 L1-L4, L4 是 stub)
  - **LLMCall** (LLM 调用)
  - **ToolDispatch** (工具分发, 含权限检查)
  - **SingleTool** (具体工具子类: Glob/Grep/ReadFile/ListDir/Bash/Finish)
  - **ExtractResult** (结果提取)

跟旧 `runtime/agent/agent_node_loop.py` 关系:
- 旧 loop 仍在原位, 13 个旧子类未迁 (阶段 C 计划做)
- 阶段 D 完成后旧 loop 删除
- 新代码必须用 `from packages.services._core.agent`, 不用 `from runtime.agent`

## 解决什么 / 不解决什么

**解决**:
- 旧单体 686 行 `_run_loop` 6 件事 (prompt 装配 / 上下文压缩 / LLM 调用 / 工具分发 / 结果提取 / 事件落盘) 全在一个方法内的设计反模式
- 让每个 Router 可单独测试 / 单独 replay / 单独换实现
- 必接 bus 硬校验 (防 2026-04-17 artcontest 事件 — bus=None 导致所有事件静默丢失)
- ToolDispatchRouter 的权限门 (SingleToolRouter 先读 `permission_mode` 再执行)
- 给 13 个旧 AgentNodeLoop 子类提供迁移目标

**不解决**:
- 旧 `runtime/agent` 13 子类的迁移 (阶段 C 计划)
- 业务逻辑 (各业务子类自己实现, 例 hypothesis / demogame_kb_storywiki)
- 跨 Team 协作 (单个 agent loop 内的事, 跨 Team 走 MaterialDispatcher)

## 设计目的与最终目标

**设计目的**: omnicompany 大量 agent 工作 (Worker 调 LLM + 工具循环) 都要 Agent Node Loop. 旧单体 loop 不可观测 / 不可 replay / bus 是可选的, 出问题查不到. 重构成 Router 组合后, 每次 Router 调用都过 bus, trace_id 贯穿, 出问题能完整 replay.

**理论锚点**: 体现 omnicompany 架构原则"所有 Format 都要进 bus" 跟"Router 体系进 EventBus 审计优越". 是 [LAP 协议第一红线"事件总线驱动"](../../../../../../docs/standards/) 的具体实施.

**最终目标** (当下能认知的):
- 阶段 C: 把 13 个旧 `runtime.agent.AgentNodeLoop` 子类迁移到本包接口
- 阶段 D: 删除旧 `runtime/agent/agent_node_loop.py`
- L4 LLM 上下文压缩从 stub 实装 (大上下文 LLM 摘要)
- 细粒度工具级权限策略 (当前 default/auto 粒度粗)
- 扩 SingleToolRouter 子类集 (当前 6 个: Glob/Grep/ReadFile/ListDir/Bash/Finish)

## 规划

- **当前 V1.1** (active, 2026-04-18 阶段 A 骨架 / 2026-04-21 Phase D 文档 + kind tags + workers/ / 2026-04-23 加 BashRouter)
- **下一步**: 阶段 C 迁移 (13 个旧子类) → 阶段 D 删旧文件
- **远景**: L4 实装 + 细粒度工具权限

## 构成

- 入口与 Team → [loop.py](loop.py) (`AgentNodeLoop` 薄调度器, < 100 行)
- Materials (10 条) → [formats.py](formats.py)
  - 入口 source: `agent.prompt-request`
  - 出口 sink: `agent.result-final`
  - 8 条 internal (各阶段中间态)
- 6 子 Router → [routers/](routers/)
  - [routers/prompt_builder.py](routers/prompt_builder.py) — `PromptBuilderRouter`
  - [routers/context_compact.py](routers/context_compact.py) — `ContextCompactRouter` (L1-L4)
  - [routers/llm_call.py](routers/llm_call.py) — `LLMCallRouter`
  - [routers/tool_dispatch.py](routers/tool_dispatch.py) — `ToolDispatchRouter` (权限门)
  - [routers/single_tool.py](routers/single_tool.py) — `SingleToolRouter` + 5 内置子类 (Glob/Grep/ReadFile/ListDir/Finish)
  - [routers/bash.py](routers/bash.py) — `BashRouter` 通用 Bash 工具 (走 BashBus + workspace + 危险命令 + 审计)
  - [routers/extract_result.py](routers/extract_result.py) — `ExtractResultRouter`
- Workers (12 个 = 5 主 + 7 SingleTool) → [workers/](workers/) (Phase D 封装)
- 调用样例 → ../../_diagnosis/hypothesis/routers.py

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## T8 spawn surface

`spawn_surface.py` is the authority for agent launch paths. There are four
canonical launch entries:

- `agent_tool`: in-loop sub-agent spawn through `AgentRouter` / the `Agent` tool.
- `external_worker_run`: synchronous audited Codex/Claude worker runs through
  `ExternalAgentRunRequest` and `omni worker run`.
- `controller_spawn`: async BOSS SIGHT plan-bound workers through
  `spawn_subagent` / `omni worker spawn`.
- `workflow_run`: deterministic workflow orchestration that reuses existing
  worker/controller surfaces.

`AgentNodeLoop` is the implementation base, not a fifth launch command.
`ExternalAgentWorkerNode` and `build_external_agent_subagent_registry` are
adapters over the same surfaces.

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- AGENT-NODE-LOOP-ROUTERIZATION 计划 → [docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md](../../../../../../docs/plans/agent-framework/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md)
- 旧 runtime/agent (待迁移) → [../../../runtime/agent/](../../../../runtime/agent/)
- 跟 omnicompany Worker 关系 → [../omnicompany/README.md](../omnicompany/README.md)
- Worker 设计单 R-19 Agent Worker 三件套 → [docs/standards/worker.md](../../../../../../docs/standards/concepts/worker.md)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)

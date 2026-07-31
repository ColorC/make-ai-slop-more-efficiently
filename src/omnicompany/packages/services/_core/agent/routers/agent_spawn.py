# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T16:00:00Z type=infrastructure
"""AgentRouter · Spawn 子 agent SingleTool, 对齐 claude-code AgentTool.

参考: 参考项目/claude-code-analysis/src/tools/AgentTool/AgentTool.tsx

核心:
  - 主 agent 调本工具 → 起新的 AgentNodeLoop 子任务跑 prompt
  - subagent_type 选择子 agent 配置 (general-purpose / Explore / Plan)
  - 传 description + prompt → 子 agent 跑完返结果文本
  - 子 agent 用独立 LLM 调用 (隔离 context) — 主 agent 不接触子 agent 的中间步骤

omnicompany 实现 (Wave 3 P1, 2026-05-04 真 spawn):
  - 子 agent 类型从 ctx.subagent_registry 读 (Worker 注入)
  - registry value 是 factory: factory(model=...) → AgentNodeLoop 实例
  - 默认 registry 由 build_default_subagent_registry(bus=...) 构建 (含 3 种类型)
  - 真启时 asyncio.run(agent.run({"task": prompt, "trace_id": ..., ...}))
    → Verdict, 取 output["text"] 返主 agent
  - 干跑模式 OMNI_AGENT_DRY_RUN=1 不真启 LLM (测试用)

Wave 3 警示 (反虚假声明):
  - 此 Router 通到了"真 spawn 骨架": factory 返真 AgentNodeLoop 实例,
    asyncio.run 驱动真 .run() → 真 Verdict 提取. L2 的 spawn wiring 完成.
  - 但 NODE_PROMPT 是 simplified 版 (Wave 5 复刻 cc 原文); 真 LLM smoke
    没跑过. 真"L2 行为对齐"差 prompt 复刻 + LLM dogfood 两步.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)
from omnicompany.packages.services._core.agent.spawn_surface import (
    ENTRY_AGENT_TOOL,
    ENTRY_CONTEXT_FORK,
    agent_spawn_metadata,
)
from omnicompany.packages.services._core.agent.context_fork import (
    CONTEXT_FORK_CHECKPOINT_KEY,
    AgentContextCheckpoint,
    ContextForkBudget,
    ContextForkRequest,
    run_context_fork,
    validate_context_fork_allocation,
)

logger = logging.getLogger(__name__)


_DEFAULT_SUBAGENT = "general-purpose"


class AgentRouter(SingleToolRouter):
    """Spawn a sub-agent to handle a complex multi-step task in isolation."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("*",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("*",)

    TOOL_NAME: ClassVar[str] = "Agent"
    # DESCRIPTION 跟 cc AgentTool/prompt.ts::getPrompt 静态部分对齐 (Wave 5 续, 2026-05-05)
    # 适配:
    #   - omnicompany subagent_registry 是 ctx 注入, 不是 cc 的 agentDefinitions[] — 跳过
    #     "Available agent types and the tools they have access to: ..." 完整列表段
    #   - 跳过 forkSubagent 段 (cc 特性, omnicompany 没)
    #   - 跳过 isolation: "remote" (CCR 特有, omnicompany 没)
    #   - 保留 isolation: "worktree" (omnicompany 已有 EnterWorktree 概念)
    #   - 保留 cc 核心智慧: "Brief like a smart colleague" / "Never delegate understanding"
    #     / "agent starts with zero context" / "trust but verify"
    DESCRIPTION: ClassVar[str] = (
        "Launch a new agent to handle complex, multi-step tasks. Each agent type has specific capabilities and tools available to it.\n"
        "\n"
        "Available agent types (in omnicompany's default subagent_registry):\n"
        "- general-purpose: full default tool access (Read/Edit/Write/Glob/Grep/PowerShell/Skill/ToolSearch)\n"
        "- Explore: read-only code search (Read/Glob/Grep)\n"
        "- Plan: design implementation plans, read-only\n"
        "Custom registry can override these via ctx.subagent_registry injection.\n"
        "\n"
        "When using the Agent tool, specify a `subagent_type` parameter to select which agent type to use. If omitted, the general-purpose agent is used.\n"
        "\n"
        "## When not to use\n"
        "\n"
        "If the target is already known, use the direct tool: Read for a known path, the Grep tool for a specific symbol or string. Reserve this tool for open-ended questions that span the codebase, or tasks that match an available agent type.\n"
        "\n"
        "## Usage notes\n"
        "\n"
        "- Always include a short description summarizing what the agent will do\n"
        "- When you launch multiple agents for independent work, send them in a single message with multiple tool uses so they run concurrently\n"
        "- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.\n"
        "- Trust but verify: an agent's summary describes what it intended to do, not necessarily what it did. When an agent writes or edits code, check the actual changes before reporting the work as done.\n"
        "- Each Agent invocation defaults to `cold_delegate` and starts fresh — provide a complete task description.\n"
        "- `context_fork` is available only when the deterministic Agent allocation gate selected it. The runtime inherits a real parent checkpoint and returns a reference-only receipt; never paste or summarize the parent conversation into `prompt` to imitate a fork.\n"
        "- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of your conversation context.\n"
        "- If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first.\n"
        "- If the user specifies that they want you to run agents \"in parallel\", you MUST send a single message with multiple Agent tool use content blocks. For example, if you need to launch both a build-validator agent and a test-runner agent in parallel, send a single message with both tool calls.\n"
        "- This is omnicompany's in-loop agent spawn surface. External Codex/Claude workers enter through ExternalAgentRunRequest, and long-running BOSS SIGHT plan workers enter through spawn_subagent.\n"
        "\n"
        "## Writing the prompt\n"
        "\n"
        "Brief the agent like a smart colleague who just walked into the room — it hasn't seen this conversation, doesn't know what you've tried, doesn't understand why this task matters.\n"
        "- Explain what you're trying to accomplish and why.\n"
        "- Describe what you've already learned or ruled out.\n"
        "- Give enough context about the surrounding problem that the agent can make judgment calls rather than just following a narrow instruction.\n"
        "- If you need a short response, say so (\"report in under 200 words\").\n"
        "- Lookups: hand over the exact command. Investigations: hand over the question — prescribed steps become dead weight when the premise is wrong.\n"
        "\n"
        "Terse command-style prompts produce shallow, generic work.\n"
        "\n"
        "**Never delegate understanding.** Don't write \"based on your findings, fix the bug\" or \"based on the research, implement it.\" Those phrases push synthesis onto the agent instead of doing it yourself. Write prompts that prove you understood: include file paths, line numbers, what specifically to change."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "A short (3-5 word) description of the task",
            },
            "prompt": {
                "type": "string",
                "description": "The task prompt for the sub-agent",
            },
            "subagent_type": {
                "type": "string",
                "description": f"Sub-agent type (default '{_DEFAULT_SUBAGENT}')",
            },
            "model": {
                "type": "string",
                "description": "Optional LLM override (default per agent config)",
            },
            "execution_mode": {
                "type": "string",
                "enum": ["cold_delegate", "context_fork"],
                "description": (
                    "Agent session mode. context_fork requires a runtime-injected "
                    "checkpoint and unblocked allocation decision."
                ),
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 3600,
                "description": "context_fork child timeout (default 300 seconds)",
            },
            "max_turns": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "context_fork child turn ceiling (default 50)",
            },
        },
        "required": ["description", "prompt"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        description = (args.get("description") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        subagent_type = (args.get("subagent_type") or _DEFAULT_SUBAGENT).strip()
        model = (args.get("model") or "").strip()
        execution_mode = (args.get("execution_mode") or "cold_delegate").strip()

        if not description:
            raise ToolExecutionError("description is required (3-5 word)")
        if not prompt:
            raise ToolExecutionError("prompt is required")
        if execution_mode not in {"cold_delegate", "context_fork"}:
            raise ToolExecutionError(
                "execution_mode must be 'cold_delegate' or 'context_fork'"
            )

        if os.environ.get("OMNI_AGENT_DRY_RUN") == "1":
            if execution_mode == "context_fork":
                _validate_fork_context(ctx)
            return json.dumps({
                **agent_spawn_metadata(
                    ENTRY_CONTEXT_FORK
                    if execution_mode == "context_fork"
                    else ENTRY_AGENT_TOOL
                ),
                "subagent_type": subagent_type,
                "description": description,
                "model": model or "(default)",
                "execution_mode": execution_mode,
                "result": f"(mock sub-agent result for '{description}', dry-run)",
                "dry_run": True,
            }, ensure_ascii=False, indent=2)

        # 真启子 agent: 走 ToolContext.subagent_registry 拿配置 + 起 AgentNodeLoop
        registry = getattr(ctx, "subagent_registry", None)
        if registry is None:
            raise ToolExecutionError(
                "no subagent_registry in tool context. "
                "Worker must inject ctx.subagent_registry. "
                "For offline tests set OMNI_AGENT_DRY_RUN=1."
            )

        if subagent_type not in registry:
            available = sorted(registry.keys()) if hasattr(registry, "keys") else "(unknown)"
            raise ToolExecutionError(
                f"unknown subagent_type {subagent_type!r}. Available: {available}"
            )

        factory = registry[subagent_type]
        if not callable(factory):
            raise ToolExecutionError(
                f"subagent_registry[{subagent_type!r}] is not callable: {type(factory).__name__}. "
                f"Expected factory(model=...) -> object with async run(input_data)."
            )

        # 1) factory(model=...) → AgentNodeLoop 实例
        try:
            agent = factory(model=model) if model else factory()
        except Exception as e:
            raise ToolExecutionError(f"sub-agent factory {subagent_type!r} crashed: {e}")
        if not callable(getattr(agent, "run", None)):
            raise ToolExecutionError(
                f"sub-agent factory {subagent_type!r} returned {type(agent).__name__}; "
                "expected an object with async run(input_data)."
            )

        # 2) 异步驱动真 .run() — 子 agent 自己有完整 PromptBuilder / LLMCall /
        #    ToolDispatch / ExtractResult 链, 拿独立 messages 列表 (跟主 agent 隔离)
        trace_id = getattr(ctx, "trace_id", "") or ""
        fork_data = None
        if execution_mode == "context_fork":
            if not trace_id:
                raise ToolExecutionError("context_fork requires a non-empty parent trace_id")
            fork_data = _validate_fork_context(ctx)
            checkpoint = fork_data["checkpoint"]
            checkpoint_suffix = checkpoint.checkpoint_id.rsplit(":", 1)[-1][:10]
            sub_trace_id = (
                f"{trace_id}.fork.{subagent_type}.{checkpoint_suffix}"
            )
        else:
            sub_trace_id = f"{trace_id}.spawn.{subagent_type}" if trace_id else ""

        # P1.2 (2026-05-05): 把子 trace_id 加进 ctx.spawned_traces, 让主 agent
        # extract_result 时知道哪些 sub-agent 跑了, owner 可按 trace 回溯子事件流.
        spawned_traces = getattr(ctx, "spawned_traces", None)
        if spawned_traces is not None and sub_trace_id:
            try:
                spawned_traces.append(sub_trace_id)
            except Exception:
                pass  # list 注入有问题不影响 spawn

        if execution_mode == "context_fork":
            assert fork_data is not None
            try:
                budget = ContextForkBudget(
                    timeout_seconds=args.get("timeout_seconds") or 300.0,
                    max_turns=args.get("max_turns") or 50,
                )
                request = ContextForkRequest(
                    description=description,
                    task=prompt,
                    subagent_type=subagent_type,
                    child_trace_id=sub_trace_id,
                    parent_trace_id=trace_id,
                    checkpoint=fork_data["checkpoint"],
                    allocation_decision_ref=fork_data["decision_ref"],
                    budget=budget,
                )
            except Exception as exc:
                raise ToolExecutionError(f"invalid context_fork request: {exc}") from exc

            return _run_context_fork_sync(
                agent,
                request,
                parent_abort_event=getattr(ctx, "abort_event", None),
            )

        agent_input = {
            **agent_spawn_metadata(ENTRY_AGENT_TOOL),
            "task": prompt,
            "description": description,
            "subagent_type": subagent_type,
            "trace_id": sub_trace_id,
            # 父 trace 让 sub-agent 知道自己被谁派的 (调试用, ExtractResultRouter 透传)
            "parent_trace_id": trace_id,
        }

        run_coro = agent.run(agent_input)

        # _execute 在 asyncio.to_thread 跑 (sync 上下文, 无 running loop), 可直接 asyncio.run
        try:
            verdict = asyncio.run(run_coro)
        except RuntimeError as e:
            # 兜底: 极少数 _execute 不在 to_thread (比如直接 unit test 调) 时, 复用当前 loop
            if "asyncio.run() cannot be called from a running event loop" in str(e):
                loop = asyncio.new_event_loop()
                try:
                    verdict = loop.run_until_complete(run_coro)
                finally:
                    loop.close()
            else:
                raise ToolExecutionError(f"sub-agent {subagent_type!r} run crashed: {e}")
        except Exception as e:
            raise ToolExecutionError(f"sub-agent {subagent_type!r} run crashed: {e}")

        # 3) 提取 Verdict.output["text"] (ExtractResultRouter 的标准输出)
        output = getattr(verdict, "output", None)
        if not isinstance(output, dict):
            raise ToolExecutionError(
                f"sub-agent {subagent_type!r} returned non-dict output: {type(output).__name__}"
            )
        final_text = output.get("text", "")
        if not isinstance(final_text, str):
            final_text = str(final_text)

        # FAIL/PARTIAL Verdict 也透传 — 主 agent 看完判断怎么处理
        verdict_kind = getattr(verdict.kind, "value", str(verdict.kind))
        diagnosis = getattr(verdict, "diagnosis", "") or ""
        if verdict_kind != "PASS" and diagnosis:
            return f"[sub-agent {verdict_kind}] {diagnosis}\n\n{final_text}"
        return final_text


def _validate_fork_context(ctx: ToolContext) -> dict[str, object]:
    raw_checkpoint = getattr(ctx, CONTEXT_FORK_CHECKPOINT_KEY, None)
    if raw_checkpoint is None:
        raise ToolExecutionError(
            "context_fork requires a runtime-injected parent checkpoint"
        )
    decision = getattr(ctx, "agent_allocation_decision", None)
    decision_ref = str(
        getattr(ctx, "agent_allocation_decision_ref", "") or ""
    ).strip()
    try:
        validate_context_fork_allocation(
            decision,
            decision_ref=decision_ref,
        )
        checkpoint = AgentContextCheckpoint.model_validate(raw_checkpoint)
    except Exception as exc:
        raise ToolExecutionError(f"context_fork gate rejected: {exc}") from exc
    return {
        "checkpoint": checkpoint,
        "decision_ref": decision_ref,
    }


def _run_context_fork_sync(
    agent: object,
    request: ContextForkRequest,
    *,
    parent_abort_event: object | None,
) -> str:
    run_coro = run_context_fork(
        agent,
        request,
        parent_abort_event=parent_abort_event,
    )
    try:
        receipt = asyncio.run(run_coro)
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" not in str(exc):
            raise ToolExecutionError(f"context_fork run crashed: {exc}") from exc
        loop = asyncio.new_event_loop()
        try:
            receipt = loop.run_until_complete(run_coro)
        finally:
            loop.close()
    except Exception as exc:
        raise ToolExecutionError(f"context_fork run crashed: {exc}") from exc
    return receipt.model_dump_json()

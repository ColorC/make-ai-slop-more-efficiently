# [OMNI] origin=claude-code domain=services/agent ts=2026-04-18
# [OMNI] material_id="material:core.agent.node_loop_scheduler.implementation.py"
# OMNI-024 ALLOW: AgentNodeLoop 是全仓 agent worker 的框架基类, 与 loop 执行机制同文件内聚, 非某 package 的路由入口(同 configurable.py/launch.py 先例)
"""AgentNodeLoop — 薄调度器 Router

承诺（plan §0.1 + §10.5.1 E7）：
- 本类**不含** LLMClient 直调 / ToolDefinition.call 直调 / compact 函数直调
- 主循环方法 < 100 行
- 所有数据流走 Format + bus
- trace_id 贯穿所有 Router 的 input / output 事件

每轮循环依次 `await`：
  1. context_compact   (agent.context-request → agent.context-compacted)
  2. llm_call          (agent.llm-request     → agent.llm-response)
  3. 无 tool_uses 或 finish → extract_result (退出循环)
  4. 有 tool_uses → tool_dispatch.run() × N  (agent.tool-request → agent.tool-response)
  5. 把 tool_result 拼进 messages，回到第 1 步

首轮（循环前）：
  0. prompt_builder    (agent.prompt-request  → agent.prompt-built)

预算耗尽：
  * 发 agent.budget_exhaust 信号，调用 extract_result 返回 PARTIAL
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import hashlib
import inspect
import itertools
import json
import logging
import threading
import time
import uuid
from typing import Any, ClassVar

from omnicompany.protocol.anchor import Verdict
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.agent.agent_loop_config import (
    LoopConfig,
    PRESET_STANDARD,
    RetryConfig,
)
from omnicompany.packages.services._core.agent._bus import emit_agent_signal
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.context_compact import ContextCompactRouter
from omnicompany.packages.services._core.agent.routers.llm_call import LLMCallRouter
from omnicompany.packages.services._core.agent.routers.tool_dispatch import ToolDispatchRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    FinishRouter,
)
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.execution_limits import FACILITY_TIMEOUT_MARKER
from omnicompany.packages.services._core.agent.run_observability import AgentRunObserver
from omnicompany.packages.services._core.agent.context_fork import (
    CONTEXT_FORK_CHECKPOINT_KEY,
    capture_context_checkpoint,
    inherit_context_fork_messages,
)
from omnicompany.packages.services._core.agent.model_visible_contract import (
    ModelVisibleAgentContract,
)
from omnicompany.packages.services._core.agent.routers.pi_context import (
    PiSessionCompactor,
    is_context_overflow_error,
)

logger = logging.getLogger(__name__)


class AgentNodeLoop(Router):
    """薄调度器 Router（<100 行主循环）。

    子类通常只需声明 NODE_PROMPT / TOOL_ROUTERS，override build_prompt_builder /
    build_extract_result 就能跑起来。
    """

    # ── 子类可覆盖的元数据 ──
    NODE_PROMPT: ClassVar[str] = ""
    TOOL_ROUTERS: ClassVar[list[type[SingleToolRouter]]] = []
    LOOP_CONFIG: ClassVar[LoopConfig] = PRESET_STANDARD
    ALLOW_NO_BUS: ClassVar[bool] = False
    AUTO_FINISH_TOOL: ClassVar[bool] = True
    ENABLE_TOOL_FUSE: ClassVar[bool] = True
    ENABLE_IDENTICAL_FAILURE_FUSE: ClassVar[bool] = True
    PARALLEL_TOOL_EXECUTION: ClassVar[bool] = False
    MODEL_USER_CONTENT_BLOCKS: ClassVar[bool] = False
    LLM_MAX_CONTINUATION_RETRIES: ClassVar[int | None] = None
    LLM_PREFIX_TOOL_ERRORS: ClassVar[bool] = True

    # BD.6e 跨厂 LLM 容错扩展（CC 原生无此机制，因 Claude 会从 is_error 自愈；
    # qwen/DeepSeek 等跨厂 LLM 可能忽视 <tool_use_error> + is_error 反复原样重试）：
    # 同一工具连续 N 次 is_error=True → 强制退出到 extract_result 让 final_text fallback 救
    MAX_CONSECUTIVE_TOOL_ERRORS: ClassVar[int] = 3

    # BOSS SIGHT 块 4 引入: 子类可声明额外的"末步工具" — 调到就跟 finish 一样结束 loop.
    # 用途: 总控的 submit_response / team_supervisor 的 submit_health_criteria 等.
    # tool 仍正常执行 (返结果给 LLM 看), 但本轮结束不再调下一轮 LLM.
    # 默认空 tuple = 只 finish 终结 (旧行为, 不影响现有 worker).
    TERMINATING_TOOLS: ClassVar[tuple[str, ...]] = ()

    # ── Router 元数据 ──
    DESCRIPTION: ClassVar[str] = "AgentNodeLoop: Router 化后的薄调度器"

    def __init__(
        self,
        *,
        model: str | None = None,
        role: str | None = None,
        bus: Any | None = None,
        config: LoopConfig | None = None,
        llm_timeout_seconds: float | None = None,
        llm_max_tokens: int = 16384,
        llm_extra_body: dict[str, Any] | None = None,
    ):
        if bus is None and not self.ALLOW_NO_BUS:
            raise RuntimeError(
                f"{type(self).__name__} requires an EventBus (bus=...). "
                f"Set ALLOW_NO_BUS=True on your subclass only for truly isolated smoke tests."
            )
        self._bus = bus
        self._config = config or self.LOOP_CONFIG
        # L5 协议状态: Read→Edit 状态机用. FileReadRouter 成功后 add abs_path,
        # FileEditRouter 检查 abs_path 必在该 set, 不在 → 报错指引"先 Read".
        # 同一 AgentNodeLoop 实例的所有工具调用共享这个 set.
        self._read_files: set[str] = set()
        # L7 abort/cancel 协议 (Wave 8 P3, 2026-05-05): 外部 (主 agent owner / 监督) 调
        # `agent.abort()` 设这个 event. 主循环每 turn 头检查; 长跑工具 (DevBashRouter
        # PersistentShellSession 等) 通过 ctx.abort_event 周期检查, 命中 → 杀子进程 + raise.
        # 用 threading.Event (不是 asyncio.Event) — _execute 跑在 to_thread worker 线程,
        # threading.Event.is_set() 跨线程安全; asyncio loop 也能 .is_set() 检查.
        self._abort_event: threading.Event = threading.Event()
        # L7 trace_id 跨工具串联 (P1.2, 2026-05-05): AgentRouter 派 sub-agent 时
        # 把子 trace_id append 进来, 主 agent extract_result 时 Verdict.output
        # 含 spawned_traces 字段, owner / 监督可按 trace 回溯子 agent 事件流.
        # bus 里事件已带 trace_id, 这个列表只是给上游拿到层级关系的索引.
        self._spawned_traces: list[str] = []

        # 工具 Router 实例（始终含 Finish）
        tool_classes = list(self.TOOL_ROUTERS)
        if (
            self.AUTO_FINISH_TOOL
            and not any(r.TOOL_NAME == "finish" for r in tool_classes)
        ):
            tool_classes.append(FinishRouter)
        tool_router_instances = [R(bus=bus) for R in tool_classes]

        # 子 Router 装配
        self._tool_dispatch = ToolDispatchRouter(tool_routers=tool_router_instances, bus=bus)
        self._prompt_builder = self.build_prompt_builder(bus=bus)
        self._context_compact = self.build_context_compact(bus=bus)
        self._llm_call = LLMCallRouter(
            model=model,
            role=role,
            tools_spec=self._tool_dispatch.tools_spec(),
            retry=self._config.retry,
            bus=bus,
            caller_prefix=type(self).__name__,
            timeout_seconds=llm_timeout_seconds,
            max_tokens=llm_max_tokens,
            extra_body=llm_extra_body,
            max_continuation_retries=self.LLM_MAX_CONTINUATION_RETRIES,
            prefix_tool_errors=self.LLM_PREFIX_TOOL_ERRORS,
        )
        self._extract_result = self.build_extract_result(bus=bus)
        self._session_compactor = self.build_session_compactor(
            model=model,
            role=role,
            retry=self._config.retry,
            extra_body=llm_extra_body,
            bus=bus,
        )

    def bind_runtime_context(
        self,
        *,
        bus: Any,
        trace_id: str,
        parent_event_id: str,
    ) -> None:
        """Rebind the complete Agent loop to TeamRunner's authoritative bus."""
        super().bind_runtime_context(
            bus=bus,
            trace_id=trace_id,
            parent_event_id=parent_event_id,
        )
        children = (
            self._prompt_builder,
            self._context_compact,
            self._llm_call,
            self._tool_dispatch,
            self._extract_result,
            *self._tool_dispatch.routers,
        )
        for child in children:
            child.bind_runtime_context(
                bus=bus,
                trace_id=trace_id,
                parent_event_id=parent_event_id,
            )
        if self._session_compactor is not None:
            self._session_compactor.bind_runtime_context(
                bus=bus,
                trace_id=trace_id,
                parent_event_id=parent_event_id,
            )

    # ── 子类 override 点 ──

    def build_prompt_builder(self, *, bus: Any) -> PromptBuilderRouter:
        return PromptBuilderRouter(template=self.NODE_PROMPT, bus=bus)

    def build_context_compact(self, *, bus: Any) -> ContextCompactRouter:
        return ContextCompactRouter(
            compact_cfg=self._config.compact,
            bus=bus,
            context_window=self._config.context_window,
        )

    def build_session_compactor(
        self,
        *,
        model: str | None,
        role: str | None,
        retry: RetryConfig,
        extra_body: dict[str, Any] | None,
        bus: Any,
    ) -> PiSessionCompactor | None:
        return None

    def build_extract_result(self, *, bus: Any) -> ExtractResultRouter:
        return ExtractResultRouter(bus=bus)

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        """构造每次 tool-request 的 context 字段。子类可 override 注入业务字段
        （如 prefab_name），业务 Router 从 ToolContext 读取用于 trace / allowlist / 分目录落盘等。

        默认含 read_files set 实例 (跨工具共享, FileRead/Edit 状态机用).
        子类 override 时若不调 super(), Read→Edit 协议在该子 agent 失效 — 是 OK 的,
        子类可自管或显式忽略.
        """
        return {
            "trace_id": trace_id,
            "turn_number": turn,
            "read_files": self._read_files,  # L5 协议: Read→Edit 状态机
            "abort_event": self._abort_event,  # L7 协议: 长跑工具 abort/cancel
            "spawned_traces": self._spawned_traces,  # L7 协议: 跨 sub-agent trace 收集
            # ReadImageRouter 在 SingleToolRouter 内会把 dict 转成 ToolContext。
            # 显式共享 list，才能把工具侧挂载的图片带回本循环下一轮。
            "pending_image_attachments": [],
        }

    def model_visible_contract(
        self,
        input_data: dict,
        *,
        rendered_system_prompt: str | None = None,
        rendered_initial_messages: list[dict] | None = None,
    ) -> ModelVisibleAgentContract:
        """Snapshot the exact shared model interface for conformance checks."""

        system_prompt = (
            rendered_system_prompt
            if rendered_system_prompt is not None
            else self._prompt_builder.render_system_prompt(input_data)
        )
        initial_messages = rendered_initial_messages
        if initial_messages is None:
            initial_messages = inherit_context_fork_messages(
                input_data,
                self._prompt_builder.build_initial_messages(input_data),
            )
        llm = self._llm_call._llm
        return ModelVisibleAgentContract(
            system_prompt=system_prompt,
            initial_messages=tuple(initial_messages),
            tools=tuple(self._tool_dispatch.tools_spec()),
            model=str(getattr(llm, "model", "") or ""),
            max_tokens=int(getattr(llm, "max_tokens", 0) or 0),
            model_options=dict(self._llm_call._extra_body),
            context_policy=(
                {
                    "context_window": self._config.context_window,
                    **self._session_compactor.policy,
                }
                if self._session_compactor is not None
                else {
                    "context_window": self._config.context_window,
                    "compact": asdict(self._config.compact),
                }
            ),
            retry_policy=asdict(self._config.retry),
            termination_policy={
                "max_turns": self._config.max_turns,
                "no_tool_calls": "finish",
                "finish_tool": "finish" if self.AUTO_FINISH_TOOL else None,
                "terminating_tools": list(self.TERMINATING_TOOLS),
                "max_consecutive_tool_errors": (
                    self.MAX_CONSECUTIVE_TOOL_ERRORS
                    if self.ENABLE_TOOL_FUSE
                    else None
                ),
                "identical_failed_tool_call_fuse": (
                    self.ENABLE_IDENTICAL_FAILURE_FUSE
                ),
            },
        )

    async def check_finish_guard(
        self,
        *,
        input_data: dict,
        messages: list[dict],
        turn: int,
        trace_id: str,
    ) -> tuple[bool, str]:
        """Allow a domain Agent to refuse premature finish/no-tool exits.

        The default keeps existing Agent behavior unchanged. Contract-heavy
        Agents can override this hook and return ``(False, reason)``; the
        reason is fed back into the same conversation as a failed finish tool
        result (or a user message for no-tool exits).
        """
        return True, ""

    # ── L7 abort/cancel 公开接口 ───────────────────────────────────

    def abort(self) -> None:
        """从外部触发 abort. 当前 turn 的工具调用 + 后续 turn 都会接收到信号.

        长跑工具 (DevBashRouter / PersistentShellSession 等) 周期检查 ctx.abort_event,
        命中 → 杀子进程 + raise ToolExecutionError("aborted").

        主循环每 turn 头检查, 命中 → break + extract_result PARTIAL.

        线程安全: threading.Event 跨 thread + asyncio loop 都 OK.
        """
        self._abort_event.set()

    def is_aborted(self) -> bool:
        return self._abort_event.is_set()

    def reset_abort(self) -> None:
        """清 abort flag, 准备下一轮调用. 通常 owner 调 abort 后 wait extract 出来,
        再调 reset_abort 才能下次 .run()."""
        self._abort_event.clear()

    async def on_turn_end_async(
        self, *, turn: int, messages: list[dict], trace_id: str,
    ) -> None:
        """每轮末尾 async 钩子（2026-04-18 晚为双脑 lockstep 架构新加）。

        调用时机：tool_result 已拼回 messages、agent.turn.end 信号已发、熔断检查之前。
        默认空操作；子类可 override 做"后轮注入"，典型场景：
          - lockstep 子类提交本轮观察给反思脑 daemon，拿到 substitutions 注入 messages
          - 审计子类把本轮 messages 快照外发给监督 agent

        约定：允许原位修改 messages（新追加的 user message 会进下一轮 context_compact）。
        """
        return

    async def on_tool_dispatch_start(
        self, *, tool_name: str, tool_args: dict, tool_use_id: str, turn: int, trace_id: str,
    ) -> None:
        """工具调用**前**钩子（2026-04-24 新增 · for 实时 UI 更新 · 默认空）.

        用途: 子类 override 把 (tool_name, tool_args) 推给外部 UI (如collab platform流式卡片),
        同事能看到 "agent 正在调 grep ...".
        """
        return

    async def on_tool_dispatch_end(
        self, *, tool_name: str, tool_use_id: str, result: str, is_error: bool,
        turn: int, trace_id: str,
    ) -> None:
        """工具调用**后**钩子（2026-04-24 新增）.

        result 是 tool 返回的文本 (可能很长 · UI 侧自己截断展示).
        """
        return

    # ── Router 入口 ──

    async def _signal(self, trace_id: str, event_type: str, payload: dict) -> None:
        await emit_agent_signal(
            self._bus, trace_id=trace_id, event_type=event_type,
            source=f"agent.{type(self).__name__}", payload=payload,
        )

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            input_data = {}
        # TeamRunner's bound trace is authoritative. Direct Agent callers may
        # still provide trace_id/session_id when no TeamRunner context exists.
        trace_id = (
            getattr(self, "_trace_id", "")
            or input_data.get("trace_id")
            or input_data.get("session_id")
            or str(uuid.uuid4())
        )
        observer = AgentRunObserver.from_input(
            input_data,
            trace_id=trace_id,
            agent_name=str(input_data.get("agent_name") or type(self).__name__),
        )
        observer.announce("agent_starting", max_turns=self._config.max_turns)
        cfg = self._config
        loop_started = time.monotonic()
        tool_call_count = 0
        tool_error_count = 0
        tool_timeout_count = 0
        repeat_block_count = 0
        llm_call_count = 0
        llm_input_tokens = 0
        llm_output_tokens = 0
        llm_models: set[str] = set()
        last_context_tokens = 0
        overflow_recovery_attempted = False
        await self._signal(trace_id, "agent.loop.start", {"max_turns": cfg.max_turns})
        await self._signal(trace_id, "agent_start", {})
        observer.announce("event_bus_registered")

        # 2026-04-18 晚：同步写入身份到每个 tool Router 的 executor。
        # 旧 AgentNodeLoop 在 run() 设 self._executor.origin/domain/agent_name，
        # 这些值被 tool_executor 内部写入（str_replace_editor/create 等）传给 guarded_write。
        # 新薄调度器下工具是独立 Router 实例，各自持有 executor，必须逐一同步。
        _origin = input_data.get("origin", "claude-code")
        _domain = input_data.get("domain", "")
        _agent_name = input_data.get("agent_name", type(self).__name__)
        for tr in self._tool_dispatch.routers:
            ex = getattr(tr, "_executor", None)
            if ex is not None:
                ex.origin = _origin
                ex.domain = _domain
                ex.agent_name = _agent_name

        # 0. Prompt 构造
        observer.announce("prompt_building")
        prompt_v = await self._prompt_builder.run({
            "input_data": input_data, "node_prompt_template": self.NODE_PROMPT, "trace_id": trace_id,
        })
        messages: list[dict] = list(prompt_v.output["initial_messages"])
        system: str = prompt_v.output["system_prompt"]
        observer.announce(
            "prompt_ready",
            initial_message_count=len(messages),
            system_prompt_chars=len(system),
        )
        model_visible_contract = self.model_visible_contract(
            input_data,
            rendered_system_prompt=system,
            rendered_initial_messages=messages,
        )
        await self._signal(
            trace_id,
            "agent.model_visible_contract",
            model_visible_contract.audit_payload(),
        )
        # BD.6e 熔断状态：同一工具连续 is_error 计数
        consecutive_errors_by_tool: dict[str, int] = {}
        # Identical deterministic failures are blocked only while the inputs
        # they observed remain unchanged. A successful controlled filesystem
        # mutation advances this generation and permits the exact same
        # verifier command to observe the new state.
        failed_tool_fingerprints: dict[str, tuple[str, int]] = {}
        workspace_mutation_generation = 0

        async def _finish(final_text: str, turn_count: int, reason: str) -> Verdict:
            nonlocal messages
            observer.announce("agent_finishing", turn=turn_count, reason=reason)
            performance = {
                "wall_duration_seconds": round(time.monotonic() - loop_started, 3),
                "turn_count": turn_count,
                "tool_call_count": tool_call_count,
                "tool_error_count": tool_error_count,
                "tool_timeout_count": tool_timeout_count,
                "repeat_block_count": repeat_block_count,
                "llm_call_count": llm_call_count,
                "input_tokens": llm_input_tokens,
                "output_tokens": llm_output_tokens,
                "reasoning_tokens": None,
                "total_tokens": llm_input_tokens + llm_output_tokens,
                "models": sorted(llm_models),
                "stop_reason": reason,
            }
            await self._signal(trace_id, "agent.loop.finish", performance)
            await self._signal(
                trace_id,
                "agent_end",
                {"messages": messages, "stop_reason": reason},
            )
            if (
                self._session_compactor is not None
                and self._session_compactor.should_compact(
                    last_context_tokens,
                    cfg.context_window,
                )
                and self._session_compactor.can_compact(messages)
            ):
                await self._signal(
                    trace_id,
                    "compaction_start",
                    {"reason": "threshold"},
                )
                try:
                    compacted = await self._session_compactor.compact(
                        messages,
                        trace_id=trace_id,
                    )
                except Exception as exc:
                    await self._signal(
                        trace_id,
                        "compaction_end",
                        {
                            "reason": "threshold",
                            "aborted": False,
                            "willRetry": False,
                            "errorMessage": f"Auto-compaction failed: {exc}",
                        },
                    )
                else:
                    if compacted is not None:
                        messages = compacted.messages
                    await self._signal(
                        trace_id,
                        "compaction_end",
                        {
                            "reason": "threshold",
                            "aborted": False,
                            "willRetry": False,
                            "result": (
                                {
                                    "tokensBefore": compacted.tokens_before,
                                    "estimatedTokensAfter": (
                                        compacted.estimated_tokens_after
                                    ),
                                    "firstKeptIndex": (
                                        compacted.first_kept_index
                                    ),
                                    "isSplitTurn": compacted.is_split_turn,
                                }
                                if compacted is not None
                                else None
                            ),
                        },
                    )
            verdict = await self._extract_result.run({
                "messages": messages, "final_text": final_text,
                "turn_count": turn_count, "stop_reason": reason, "trace_id": trace_id,
            })
            # P1.2 (2026-05-05): 把派生的 sub-agent trace_id 列表暴露在 Verdict.output,
            # owner / 监督拿到主 trace 后能按 trace 回溯所有 sub-agent 事件流.
            if isinstance(verdict.output, dict) and self._spawned_traces:
                verdict.output.setdefault("spawned_traces", list(self._spawned_traces))
            if isinstance(verdict.output, dict):
                verdict.output.setdefault("performance", performance)
                verdict.output.setdefault(
                    "model_visible_contract",
                    model_visible_contract.audit_payload(),
                )
                verdict.output.setdefault("observability_file", str(observer.path))
                if input_data.get("export_context_checkpoint"):
                    checkpoint = capture_context_checkpoint(
                        parent_trace_id=trace_id,
                        parent_turn=turn_count,
                        messages=messages,
                        context_refs=input_data.get("context_refs") or [],
                    )
                    verdict.output["context_fork_checkpoint"] = checkpoint.model_dump(
                        mode="python"
                    )
            await self._signal(
                trace_id,
                "agent_settled",
                {"stop_reason": reason, "turn_count": turn_count},
            )
            observer.announce("completed", **performance)
            return verdict

        turn_indices = (
            itertools.count()
            if cfg.max_turns is None
            else range(cfg.max_turns)
        )
        pending_messages: list[dict] = []
        for turn in turn_indices:
            # L7 abort 检查 (Wave 8): 每 turn 头检查 abort, 命中 → 走 PARTIAL extract
            if self._abort_event.is_set():
                observer.announce("aborted", turn=turn)
                await self._signal(trace_id, "agent.aborted", {"turn": turn})
                final_text = _extract_last_assistant_text(messages)
                return await _finish(final_text, turn, "aborted")
            await self._signal(trace_id, "agent.turn.start", {"turn": turn})
            await self._signal(trace_id, "turn_start", {"turn": turn})
            if turn == 0:
                for message in messages:
                    await self._signal(
                        trace_id,
                        "message_start",
                        {"message": message},
                    )
                    await self._signal(
                        trace_id,
                        "message_end",
                        {"message": message},
                    )
            if pending_messages:
                for message in pending_messages:
                    messages.append(message)
                    await self._signal(
                        trace_id,
                        "message_start",
                        {"message": message},
                    )
                    await self._signal(
                        trace_id,
                        "message_end",
                        {"message": message},
                    )
                pending_messages = []
            steering_messages = await _drain_queued_messages(
                input_data.get("get_steering_messages"),
                content_blocks=self.MODEL_USER_CONTENT_BLOCKS,
            )
            for message in steering_messages:
                messages.append(message)
                await self._signal(
                    trace_id,
                    "message_start",
                    {"message": message},
                )
                await self._signal(
                    trace_id,
                    "message_end",
                    {"message": message},
                )
            observer.announce("turn_started", turn=turn)
            # 1. Context 压缩
            ctx_v = await self._context_compact.run({
                "messages": messages, "compact_cfg": cfg.compact,
                "context_window": cfg.context_window, "turn": turn, "trace_id": trace_id,
            })
            messages = list(ctx_v.output["messages"])
            # 2. LLM 调用
            observer.announce("llm_waiting", turn=turn)
            try:
                llm_v = await self._llm_call.run({
                    "messages": messages, "system_prompt": system,
                    "tools_spec": self._tool_dispatch.tools_spec(),
                    "turn": turn, "trace_id": trace_id,
                    "agent_run_observer": observer,
                })
            except Exception as exc:
                if (
                    self._session_compactor is None
                    or not is_context_overflow_error(exc)
                    or overflow_recovery_attempted
                ):
                    raise
                overflow_recovery_attempted = True
                error_message = {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": str(exc),
                        }
                    ],
                    "stop_reason": "error",
                }
                messages.append(error_message)
                await self._signal(
                    trace_id,
                    "message_start",
                    {"message": error_message},
                )
                await self._signal(
                    trace_id,
                    "message_end",
                    {"message": error_message},
                )
                await self._signal(
                    trace_id,
                    "turn_end",
                    {
                        "turn": turn,
                        "message": error_message,
                        "toolResults": [],
                    },
                )
                await self._signal(
                    trace_id,
                    "agent_end",
                    {
                        "messages": messages,
                        "stop_reason": "error",
                        "willRetry": False,
                    },
                )
                if not self._session_compactor.can_compact(messages):
                    raise
                await self._signal(
                    trace_id,
                    "compaction_start",
                    {"reason": "overflow"},
                )
                try:
                    compacted = await self._session_compactor.compact(
                        messages,
                        trace_id=trace_id,
                    )
                except Exception as compact_exc:
                    await self._signal(
                        trace_id,
                        "compaction_end",
                        {
                            "reason": "overflow",
                            "aborted": False,
                            "willRetry": False,
                            "errorMessage": (
                                "Context overflow recovery failed: "
                                f"{compact_exc}"
                            ),
                        },
                    )
                    raise
                if compacted is None:
                    raise
                messages = compacted.messages
                if (
                    messages
                    and messages[-1].get("role") == "assistant"
                    and messages[-1].get("stop_reason") == "error"
                ):
                    messages = messages[:-1]
                await self._signal(
                    trace_id,
                    "compaction_end",
                    {
                        "reason": "overflow",
                        "aborted": False,
                        "willRetry": True,
                        "result": {
                            "tokensBefore": compacted.tokens_before,
                            "estimatedTokensAfter": (
                                compacted.estimated_tokens_after
                            ),
                            "firstKeptIndex": compacted.first_kept_index,
                            "isSplitTurn": compacted.is_split_turn,
                        },
                    },
                )
                await self._signal(trace_id, "agent_start", {})
                continue
            usage = llm_v.output.get("usage") or {}
            llm_call_count += 1
            llm_input_tokens += int(usage.get("input_tokens") or 0)
            llm_output_tokens += int(usage.get("output_tokens") or 0)
            last_context_tokens = int(
                usage.get("total_tokens")
                or (
                    int(usage.get("input_tokens") or 0)
                    + int(usage.get("output_tokens") or 0)
                    + int(usage.get("cache_read_tokens") or 0)
                    + int(usage.get("cache_write_tokens") or 0)
                )
            )
            if usage.get("model"):
                llm_models.add(str(usage["model"]))
            observer.announce("llm_completed", turn=turn)
            tool_uses: list[dict] = llm_v.output.get("tool_uses", [])
            assistant_message = llm_v.output["assistant_message"]
            messages.append(assistant_message)
            await self._signal(
                trace_id,
                "message_start",
                {"message": assistant_message},
            )
            await self._signal(
                trace_id,
                "message_end",
                {"message": assistant_message},
            )
            text: str = llm_v.output.get("text", "")
            stop_reason = str(llm_v.output.get("stop_reason") or "")
            if stop_reason in {"length", "max_tokens"} and tool_uses:
                truncated_blocks: list[dict[str, Any]] = []
                for tu in tool_uses:
                    error_text = (
                        f'Tool call "{tu["tool_name"]}" was not executed: the '
                        "response hit the output token limit, so its arguments "
                        "may be truncated. Re-issue the tool call with complete "
                        "arguments."
                    )
                    await self._signal(
                        trace_id,
                        "tool_execution_start",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tu["tool_name"],
                            "args": tu["tool_args"],
                        },
                    )
                    await self._signal(
                        trace_id,
                        "tool_execution_end",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tu["tool_name"],
                            "result": {"content": error_text},
                            "isError": True,
                        },
                    )
                    block = {
                        "type": "tool_result",
                        "tool_use_id": tu["tool_use_id"],
                        "content": error_text,
                        "is_error": True,
                    }
                    truncated_blocks.append(block)
                    result_message = {
                        "role": "toolResult",
                        "toolCallId": tu["tool_use_id"],
                        "toolName": tu["tool_name"],
                        "content": [{"type": "text", "text": error_text}],
                        "isError": True,
                    }
                    await self._signal(
                        trace_id,
                        "message_start",
                        {"message": result_message},
                    )
                    await self._signal(
                        trace_id,
                        "message_end",
                        {"message": result_message},
                    )
                messages.append({"role": "user", "content": truncated_blocks})
                await self._signal(
                    trace_id,
                    "turn_end",
                    {
                        "turn": turn,
                        "message": assistant_message,
                        "toolResults": truncated_blocks,
                    },
                )
                await self._signal(
                    trace_id,
                    "agent.turn.end",
                    {"turn": turn, "tool_calls": len(tool_uses)},
                )
                continue
            # 3. 收尾：无 tool_uses 或 finish
            finish_call = next((tu for tu in tool_uses if tu["tool_name"] == "finish"), None)
            # A no-tool response is a valid model decision. Do not invent a
            # retry turn merely because the text is short: that old heuristic
            # repeated completed work and made tool use look mandatory. A
            # domain that truly requires an artifact must express that through
            # check_finish_guard() and deterministic file checks.
            if not tool_uses or finish_call:
                try:
                    finish_allowed, finish_reason = await self.check_finish_guard(
                        input_data=input_data,
                        messages=messages,
                        turn=turn,
                        trace_id=trace_id,
                    )
                except Exception as exc:
                    logger.exception("check_finish_guard crashed; refusing finish")
                    finish_allowed = False
                    finish_reason = f"finish guard crashed: {type(exc).__name__}: {exc}"
                if not finish_allowed:
                    blocked_message = (
                        "[FINISH_BLOCKED] 当前交付合同尚未满足，继续在本对话修改现有产物。\n"
                        f"{finish_reason}"
                    )
                    if finish_call:
                        messages.append({
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": finish_call["tool_use_id"],
                                "content": blocked_message,
                                "is_error": True,
                            }],
                        })
                    else:
                        messages.append({"role": "user", "content": blocked_message})
                    await self._signal(
                        trace_id,
                        "agent.finish_blocked",
                        {"turn": turn, "reason": finish_reason},
                    )
                    continue
                final_text = finish_call["tool_args"].get("result", text) if finish_call else text
                await self._signal(
                    trace_id,
                    "turn_end",
                    {
                        "turn": turn,
                        "message": assistant_message,
                        "toolResults": [],
                    },
                )
                if finish_call is None:
                    follow_up_messages = await _drain_queued_messages(
                        input_data.get("get_follow_up_messages"),
                        content_blocks=self.MODEL_USER_CONTENT_BLOCKS,
                    )
                    if follow_up_messages:
                        pending_messages = follow_up_messages
                        continue
                return await _finish(final_text, turn + 1, "finish_tool" if finish_call else "no_tool_calls")
            # BOSS SIGHT 块 4: 子类声明的 TERMINATING_TOOLS 命中也结束 loop. 工具结果仍要
            # 执行 (让 LLM 看到 tool_result), 但工具调完后不再喂回 LLM 让它"再想一轮".
            # 处理顺序: 先正常跑工具 (落 tool_result), 然后跳出 loop 让 extract_result 收割.
            terminating_tu = None
            if self.TERMINATING_TOOLS:
                terminating_tu = next(
                    (tu for tu in tool_uses if tu["tool_name"] in self.TERMINATING_TOOLS),
                    None,
                )
            # 4. 工具调用
            tool_result_blocks = []
            tool_ctx = self.build_tool_context(input_data=input_data, turn=turn, trace_id=trace_id)
            if any(tu.get("tool_name") == "Agent" for tu in tool_uses):
                # Snapshot the compacted parent conversation before the assistant
                # message that invoked the Agent tool. The triggering task travels
                # separately in tool_args, so the checkpoint has no dangling
                # tool_use block and can be inherited without a summary.
                checkpoint = capture_context_checkpoint(
                    parent_trace_id=trace_id,
                    parent_turn=turn,
                    messages=messages[:-1],
                    context_refs=input_data.get("context_refs") or [],
                )
                tool_ctx[CONTEXT_FORK_CHECKPOINT_KEY] = checkpoint.model_dump(mode="python")
                tool_ctx["agent_allocation_decision"] = input_data.get(
                    "agent_allocation_decision"
                )
                tool_ctx["agent_allocation_decision_ref"] = input_data.get(
                    "agent_allocation_decision_ref"
                )
            fused_tool: str | None = None   # BD.6e 熔断触发的工具名
            fused_reason: str | None = None
            parallel_tool_results: dict[str, Verdict] = {}
            if (
                self.PARALLEL_TOOL_EXECUTION
                and cfg.enable_tool_concurrency
                and not self.ENABLE_IDENTICAL_FAILURE_FUSE
            ):
                parallel_calls = [
                    tu
                    for tu in tool_uses
                    if not (
                        isinstance(tu.get("tool_args"), dict)
                        and tu["tool_args"].get("__parse_error")
                    )
                ]
                for tu in parallel_calls:
                    await self._signal(
                        trace_id,
                        "tool_execution_start",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tu["tool_name"],
                            "args": tu["tool_args"],
                        },
                    )
                    try:
                        observer.announce(
                            "tool_running",
                            turn=turn,
                            tool_name=tu["tool_name"],
                        )
                        await self.on_tool_dispatch_start(
                            tool_name=tu["tool_name"],
                            tool_args=tu["tool_args"],
                            tool_use_id=tu["tool_use_id"],
                            turn=turn,
                            trace_id=trace_id,
                        )
                    except Exception:
                        logger.exception(
                            "on_tool_dispatch_start hook crashed (non-fatal)"
                        )
                tool_call_count += len(parallel_calls)

                async def _execute_parallel_tool(tu: dict) -> tuple[str, Verdict]:
                    verdict = await self._tool_dispatch.run({
                        "tool_name": tu["tool_name"],
                        "tool_args": tu["tool_args"],
                        "tool_use_id": tu["tool_use_id"],
                        "turn": turn,
                        "context": tool_ctx,
                        "trace_id": trace_id,
                    })
                    is_error = bool(verdict.output.get("is_error"))
                    observer.announce(
                        "tool_completed",
                        turn=turn,
                        tool_name=tu["tool_name"],
                        is_error=is_error,
                    )
                    await self._signal(
                        trace_id,
                        "tool_execution_end",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tu["tool_name"],
                            "result": {
                                "content": verdict.output.get("result", "")
                            },
                            "isError": is_error,
                        },
                    )
                    try:
                        await self.on_tool_dispatch_end(
                            tool_name=tu["tool_name"],
                            tool_use_id=tu["tool_use_id"],
                            result=str(verdict.output.get("result", "")),
                            is_error=is_error,
                            turn=turn,
                            trace_id=trace_id,
                        )
                    except Exception:
                        logger.exception(
                            "on_tool_dispatch_end hook crashed (non-fatal)"
                        )
                    return tu["tool_use_id"], verdict

                parallel_tool_results = dict(
                    await asyncio.gather(
                        *(_execute_parallel_tool(tu) for tu in parallel_calls)
                    )
                )
            for tu in tool_uses:
                tname = tu["tool_name"]
                args = tu["tool_args"]
                fingerprint = _tool_call_fingerprint(tname, args)
                # BD.6c: OpenAI function.arguments JSON 解析失败 → args 含 __parse_error
                # 不走真 tool dispatch（参数本身就错了），直接生成指导 LLM 修的错误 tool_result
                if isinstance(args, dict) and args.get("__parse_error"):
                    parse_err = args.get("__parse_error", "")
                    raw_args = args.get("__raw_args", "")
                    raw_len = len(raw_args) if isinstance(raw_args, str) else 0
                    err_content = (
                        f"[TOOL_ERROR] 你的 tool call arguments JSON 解析失败，工具**未被调用**。\n\n"
                        f"解析错误：{parse_err}\n"
                        f"原始 arguments 字符串长度：{raw_len} chars\n\n"
                        f"**常见原因**：\n"
                        f"1. 你生成的 JSON 被 max_tokens 截断（当前 output 可能 >8K tokens）\n"
                        f"2. 字符串里有未转义的 \\n / \" / \\\\ 等特殊字符\n"
                        f"3. Markdown 的代码块 ``` 需要转义\n\n"
                        f"**修复建议**：\n"
                        f"- 如果是长内容 tool (submit_findings/write_file)：压缩字符串（去掉过长段落、用简短 evidence）\n"
                        f"- 分多次小调用替代一次大调用\n"
                        f"- 确保 string 字段里所有 \" 和 \\ 都正确转义"
                    )
                    await self._signal(
                        trace_id,
                        "tool_execution_start",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tname,
                            "args": args,
                        },
                    )
                    await self._signal(
                        trace_id,
                        "tool_execution_end",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tname,
                            "result": {"content": err_content},
                            "isError": True,
                        },
                    )
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tu["tool_use_id"],
                        "content": err_content,
                        "is_error": True,
                    })
                    consecutive_errors_by_tool[tname] = consecutive_errors_by_tool.get(tname, 0) + 1
                    if (
                        self.ENABLE_TOOL_FUSE
                        and consecutive_errors_by_tool[tname]
                        >= self.MAX_CONSECUTIVE_TOOL_ERRORS
                    ):
                        fused_tool = tname
                        fused_reason = "consecutive_parse_errors"
                    continue

                # A failed call with unchanged native tool arguments has no new
                # information. Refuse it before dispatch instead of paying for
                # the same deterministic failure again.
                prior_failure = failed_tool_fingerprints.get(fingerprint)
                if (
                    self.ENABLE_IDENTICAL_FAILURE_FUSE
                    and
                    prior_failure is not None
                    and prior_failure[1] == workspace_mutation_generation
                ):
                    prior, _prior_generation = prior_failure
                    repeat_block_count += 1
                    tool_error_count += 1
                    repeat_message = (
                        "[FACILITY_REPEAT_BLOCKED] identical failed tool call was not executed again. "
                        f"Previous failure: {prior[:500]}"
                    )
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tu["tool_use_id"],
                        "content": repeat_message,
                        "is_error": True,
                    })
                    await self._signal(
                        trace_id,
                        "agent.tool_repeat_blocked",
                        {"turn": turn, "tool_name": tname, "fingerprint": fingerprint[:12]},
                    )
                    fused_tool = tname
                    fused_reason = "identical_failed_call"
                    break

                # 钩子: tool 调用前 (子类 override 可推 UI)
                preexecuted = tu["tool_use_id"] in parallel_tool_results
                if preexecuted:
                    tr_v = parallel_tool_results[tu["tool_use_id"]]
                else:
                    await self._signal(
                        trace_id,
                        "tool_execution_start",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tname,
                            "args": args,
                        },
                    )
                    try:
                        observer.announce("tool_running", turn=turn, tool_name=tname)
                        await self.on_tool_dispatch_start(
                            tool_name=tname, tool_args=args,
                            tool_use_id=tu["tool_use_id"], turn=turn, trace_id=trace_id,
                        )
                    except Exception:
                        logger.exception(
                            "on_tool_dispatch_start hook crashed (non-fatal)"
                        )

                    tool_call_count += 1
                    tr_v = await self._tool_dispatch.run({
                        "tool_name": tname, "tool_args": args,
                        "tool_use_id": tu["tool_use_id"], "turn": turn,
                        "context": tool_ctx,
                        "trace_id": trace_id,
                    })
                    observer.announce(
                        "tool_completed",
                        turn=turn,
                        tool_name=tname,
                        is_error=bool(tr_v.output.get("is_error")),
                    )
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tr_v.output["tool_use_id"],
                    "content": tr_v.output["result"],
                }
                is_err = bool(tr_v.output.get("is_error"))
                if not preexecuted:
                    await self._signal(
                        trace_id,
                        "tool_execution_end",
                        {
                            "toolCallId": tu["tool_use_id"],
                            "toolName": tname,
                            "result": {"content": tr_v.output.get("result", "")},
                            "isError": is_err,
                        },
                    )
                if is_err:
                    tool_error_count += 1
                    result_text = str(tr_v.output.get("result", ""))
                    failed_tool_fingerprints[fingerprint] = (
                        result_text,
                        workspace_mutation_generation,
                    )
                    block["is_error"] = True
                    consecutive_errors_by_tool[tname] = consecutive_errors_by_tool.get(tname, 0) + 1
                    if FACILITY_TIMEOUT_MARKER in result_text:
                        tool_timeout_count += 1
                        await self._signal(
                            trace_id,
                            "agent.tool_timeout",
                            {"turn": turn, "tool_name": tname, "fingerprint": fingerprint[:12]},
                        )
                        if self.ENABLE_TOOL_FUSE:
                            fused_tool = tname
                            fused_reason = "first_tool_timeout"
                    elif (
                        self.ENABLE_TOOL_FUSE
                        and consecutive_errors_by_tool[tname]
                        >= self.MAX_CONSECUTIVE_TOOL_ERRORS
                    ):
                        fused_tool = tname
                        fused_reason = "consecutive_tool_errors"
                else:
                    failed_tool_fingerprints.pop(fingerprint, None)
                    consecutive_errors_by_tool[tname] = 0  # 成功重置
                    if self._tool_dispatch.produces_filesystem_mutation(tname):
                        workspace_mutation_generation += 1
                        await self._signal(
                            trace_id,
                            "agent.workspace_mutation",
                            {
                                "turn": turn,
                                "tool_name": tname,
                                "generation": workspace_mutation_generation,
                            },
                        )
                tool_result_blocks.append(block)

                # 钩子: tool 调用后 (子类 override 可推 UI)
                if not preexecuted:
                    try:
                        await self.on_tool_dispatch_end(
                            tool_name=tname, tool_use_id=tu["tool_use_id"],
                            result=str(tr_v.output.get("result", "")),
                            is_error=is_err, turn=turn, trace_id=trace_id,
                        )
                    except Exception:
                        logger.exception(
                            "on_tool_dispatch_end hook crashed (non-fatal)"
                        )
                if fused_reason == "first_tool_timeout":
                    break
            # 5. 拼回 messages
            messages.append({"role": "user", "content": tool_result_blocks})
            tool_names_by_id = {
                tu["tool_use_id"]: tu["tool_name"] for tu in tool_uses
            }
            for block in tool_result_blocks:
                result_message = {
                    "role": "toolResult",
                    "toolCallId": block["tool_use_id"],
                    "toolName": tool_names_by_id.get(block["tool_use_id"], ""),
                    "content": [
                        {"type": "text", "text": str(block.get("content", ""))}
                    ],
                    "isError": bool(block.get("is_error")),
                }
                await self._signal(
                    trace_id,
                    "message_start",
                    {"message": result_message},
                )
                await self._signal(
                    trace_id,
                    "message_end",
                    {"message": result_message},
                )

            # 5.1 read_image 多模态: tool 把图挂到 ctx.pending_image_attachments,
            # 这里追加一条 user message 含 Anthropic image block, 让多模态主 agent
            # (qwen3.6-plus) 下一轮直接看图. LLMClient._anthropic_msgs_to_openai 会
            # 自动转成 OpenAI image_url 协议. tool_result message 本身不能含 image
            # (OpenAI tool message content 必须 string), 所以独立成一条 user message.
            pending_imgs = (
                tool_ctx.get("pending_image_attachments")
                if isinstance(tool_ctx, dict)
                else getattr(tool_ctx, "pending_image_attachments", None)
            ) or []
            if pending_imgs:
                img_content: list[dict[str, Any]] = []
                names_summary: list[str] = []
                for att in pending_imgs:
                    label = f"[图: {att.get('name', '?')}]"
                    if att.get("note"):
                        label = f"{label} {att['note']}"
                    img_content.append({"type": "text", "text": label})
                    img_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": att.get("mime", "image/png"),
                            "data": att.get("base64", ""),
                        },
                    })
                    names_summary.append(att.get("name", "?"))
                messages.append({"role": "user", "content": img_content})
                await self._signal(
                    trace_id, "agent.read_image_attached",
                    {"turn": turn, "count": len(pending_imgs), "names": names_summary},
                )
                # 清空, 防下一轮重复挂
                pending_imgs.clear()

            await self._signal(trace_id, "agent.turn.end", {"turn": turn, "tool_calls": len(tool_uses)})
            await self._signal(
                trace_id,
                "turn_end",
                {
                    "turn": turn,
                    "message": assistant_message,
                    "toolResults": tool_result_blocks,
                },
            )

            # 5.5 on_turn_end_async 钩子（双脑 lockstep 等后轮注入场景）
            # 默认空；子类 override 后可向 messages 追加 user 消息（下一轮 compact 会看到）
            await self.on_turn_end_async(turn=turn, messages=messages, trace_id=trace_id)

            # BOSS SIGHT 块 4: TERMINATING_TOOLS 命中, 工具已正常跑完落 tool_result, 跳出 loop
            # 让 extract_result 收割 (它会扫 messages 找最后一个 submit_response tool_use input).
            if terminating_tu is not None:
                await self._signal(
                    trace_id, "agent.terminating_tool",
                    {"turn": turn, "tool_name": terminating_tu["tool_name"]},
                )
                final_text = _extract_last_assistant_text(messages)
                return await _finish(
                    final_text, turn + 1,
                    f"terminating_tool:{terminating_tu['tool_name']}",
                )

            # BD.6e 熔断：同一工具连续 is_error=True 达阈值 → 强制进 extract_result
            # 避免 LLM (qwen 等跨厂) 反复原样调错工具耗光预算
            if fused_tool is not None:
                await self._signal(
                    trace_id, "agent.tool_fuse",
                    {"turn": turn, "tool_name": fused_tool,
                     "reason": fused_reason or "consecutive_tool_errors",
                     "consecutive_errors": consecutive_errors_by_tool.get(fused_tool, 0)},
                )
                final_text = _extract_last_assistant_text(messages)
                return await _finish(
                    final_text,
                    turn + 1,
                    f"tool_fuse:{fused_tool}:{fused_reason or 'consecutive_tool_errors'}",
                )

        # 6. 仅有显式有界任务才可能到达这里。默认长任务使用
        # max_turns=None，由 abort、finish guard、tool fuse 和可观测性异常结束。
        assert cfg.max_turns is not None
        await self._signal(trace_id, "agent.budget_exhaust", {"max_turns": cfg.max_turns})
        return await _finish(_extract_last_assistant_text(messages), cfg.max_turns, "max_turns")


def _extract_last_assistant_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                return "\n".join(texts)
    return ""


async def _drain_queued_messages(
    source: Any,
    *,
    content_blocks: bool = False,
) -> list[dict]:
    if source is None:
        return []
    raw = source() if callable(source) else source
    if inspect.isawaitable(raw):
        raw = await raw
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    messages: list[dict] = []
    for item in items:
        if isinstance(item, dict) and item.get("role"):
            messages.append(dict(item))
        else:
            text = str(item)
            content: Any = (
                [{"type": "text", "text": text}]
                if content_blocks
                else text
            )
            messages.append({"role": "user", "content": content})
    return messages


def _tool_call_fingerprint(tool_name: str, args: Any) -> str:
    """Stable fingerprint for native tool name + arguments."""

    payload = json.dumps(
        {"tool_name": tool_name, "tool_args": args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

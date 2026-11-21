# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.routing.router.base_interface_and_builtins.py"
"""Router — Node 的运行时绑定（V1.1 §2）

Router 是 LAP 中 Node 的运行时执行接口:
    TeamSpec 描述"是什么" (声明)
    Router 实现"怎么做" (执行)

三个内置 Router:
    ContextRouter — 拼接 messages (确定性, 总是 PASS)
    LLMRouter     — 调用 LLM (语义整流器), 支持多工具分发
    ToolRouter    — 执行工具 (确定性整流器), 支持 bash/editor/think

六元原语的唯一定义位于 omnicompany/primitives/。
本模块 re-export Signal/Hook 以保持向后兼容。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.protocol.info_audit import InfoAuditReport
from omnicompany.runtime.llm.llm import LLMClient
from omnicompany.runtime.exec.tool_executor import ToolExecutor

# IntentTracer 按需导入，避免循环依赖
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from omnicompany.tracing.intent_tracer import IntentTracer

# ── 六元原语 re-export（唯一定义在 primitives/）──────────────────────────────
# ── Router 原语（Node）───────────────────────────────────────────────────────

class Router(ABC):
    """Router 运行时接口 — Node 的唯一运行时绑定（V1.1 §2）

    每个节点自行验证:
        1. 输入是否符合 ℱ_in (validate_input)
        2. 输出是否符合 ℱ_out (validate_output)
    这将宏观进化问题拆解为节点判定问题。
    """

    # ── 已有：Schema 验证 ──
    INPUT_KEYS: list[str] | None = None
    OUTPUT_KEYS: list[str] | None = None

    # ── V1.1 新增：必须声明的元数据 ──
    DESCRIPTION: str = ""       # 人类可读描述：这个节点做什么
    FORMAT_IN: str = ""         # 输入 Format ID
    FORMAT_OUT: str = ""        # 输出 Format ID

    # ── V1.3 新增：知识透传 ──
    PASSTHROUGH: bool = False
    """True = 知识注入节点，run() 只做数据透传 + 知识附加。
    TeamRunner 对 PASSTHROUGH 节点: 不计入 decision_count、不触发 retry。"""

    # ── M4 (2026-04-15) 新增：声明式必需上下文字段 ──
    REQUIRED_CONTEXT: list[str] = []
    """本节点 run(input_data) 必需的字段 key 列表 (top-level dict keys).

    Runner 在调用 run() 前自动检查 input_data 是否齐全; 缺失直接 FAIL 并附
    详细 diagnosis, 不再让 LLM 带错跑下去 ("事前拦截" 优于 "事后发现").

    - 空列表 (默认) = 不做检查, 保持向后兼容
    - 约定: 只列 **结构性必需** 的字段, 不列"锦上添花"字段
      (后者应由 piggyback/post_hoc 层去挑出)
    - 嵌套字段用 dotted 风格: "foo.bar" 表示 input_data['foo']['bar'] 必须存在
    """

    # ── 反思机制已废弃 (D4, 2026-04-09) ──
    # 原 REFLECTION_ENABLED / SelfAssessment / _maybe_inject_reflection /
    # _parse_self_assessment / _check_reflection_partial 已全部删除。
    #
    # 替代: LLMClient.call() 在全局开关 OMNICOMPANY_INFO_AUDIT=1 或显式
    # 传 info_audit=True 时会自动 piggyback 注入 InfoAuditReport schema,
    # 响应后解析并通过 result.info_audit 属性返回。Router 子类只需:
    #
    #     resp = self.client.call(messages=..., system=...)
    #     ia = getattr(resp, "info_audit", None)   # InfoAuditReport | None
    #     clean_text = getattr(resp, "info_audit_cleaned_text", None) or resp.content[0].text
    #     return Verdict(kind=..., output=..., info_audit=ia)
    #
    # 下列 no-op 方法留作兼容壳, 让尚未迁移的旧代码不崩, 返回值永远无副作用。
    # 计划在 Phase 7 后完全删除。

    REFLECTION_ENABLED: bool = False
    """DEPRECATED (D4, 2026-04-09): 设置无效; 保留类变量避免子类 AttributeError。"""

    REFLECTION_INFO_THRESHOLD: int = 2
    """DEPRECATED (D4, 2026-04-09): 设置无效。"""

    def bind_runtime_context(
        self,
        *,
        bus: Any,
        trace_id: str,
        parent_event_id: str,
    ) -> None:
        """Bind the authoritative TeamRunner execution context.

        Composite routers should override this method, call ``super()``, and
        propagate the same context to their children.  Keeping this operation
        on the Router contract avoids TeamRunner having to know each composite
        router's private object graph.
        """
        self._bus = bus
        self._trace_id = trace_id
        self._parent_event_id = parent_event_id

    def _maybe_inject_reflection(self, system_prompt: str) -> str:
        """DEPRECATED (D4). 直接返回原 prompt, 不再注入 <self_assessment> 指令。

        停止双轨输出 (LLM 不再被要求吐 <self_assessment> 块), 立刻省 token +
        响应文本干净。info_audit 路径由 LLMClient.call() 统一处理。
        """
        return system_prompt

    @staticmethod
    def _parse_self_assessment(text: str) -> tuple[None, str]:
        """DEPRECATED (D4). 总是返回 (None, text) — 不解析任何自评块。

        子类现有调用形如 `sa, clean = self._parse_self_assessment(raw)`
        会拿到 sa=None, clean=raw (原始文本); 后续 `self_assessment=sa`
        构造 Verdict 时字段已删除, 调用点会在 Verdict rebuild 时抛错提醒迁移。
        """
        return None, text

    def _check_reflection_partial(
        self, self_assessment: Any, text: str, input_data: Any,
    ) -> Verdict | None:
        """DEPRECATED (D4). 总是返回 None — 不再降级 PARTIAL。

        原先"信息不足自动降级触发反馈回路"的职能,已交给 Phase 3 Runner 的
        info_audit 规则化触发机制,不在 Router 层做。
        """
        return None

    def summarize_input(self, input_data: Any) -> str:
        """生成输入数据的人类可读摘要。子类应覆盖提供有意义的摘要。"""
        if isinstance(input_data, dict):
            return f"keys={list(input_data.keys())[:8]}"
        return type(input_data).__name__

    def summarize_output(self, verdict: Verdict) -> str:
        """生成输出数据的人类可读摘要。子类应覆盖提供有意义的摘要。"""
        return verdict.kind.value

    def validate_input(self, input_data: Any) -> Verdict | None:
        """验前: 检查输入是否符合 ℱ_in

        Returns:
            None = 验证通过 (无 schema 时也通过)
            Verdict(FAIL) = 验证失败, 包含缺失字段信息
        """
        if self.INPUT_KEYS is None:
            return None

        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "input_validation_failed": True,
                    "reason": "expected dict input",
                    "node": self.__class__.__name__,
                },
                diagnosis=f"[{self.__class__.__name__}] Input validation failed: expected dict, got {type(input_data).__name__}",
            )

        missing = [k for k in self.INPUT_KEYS if k not in input_data]
        if missing:
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "input_validation_failed": True,
                    "missing_keys": missing,
                    "available_keys": list(input_data.keys()),
                    "node": self.__class__.__name__,
                },
                diagnosis=f"[{self.__class__.__name__}] Input validation failed: missing keys {missing}",
            )

        return None

    def validate_output(self, verdict: Verdict) -> Verdict | None:
        """验后: 检查输出是否符合 ℱ_out

        Returns:
            None = 验证通过
            Verdict(FAIL) = 输出不合规
        """
        if self.OUTPUT_KEYS is None:
            return None

        output = verdict.output
        if not isinstance(output, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "output_validation_failed": True,
                    "reason": "expected dict output",
                    "node": self.__class__.__name__,
                },
                diagnosis=f"[{self.__class__.__name__}] Output validation failed: expected dict, got {type(output).__name__}",
            )

        missing = [k for k in self.OUTPUT_KEYS if k not in output]
        if missing:
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={
                    "output_validation_failed": True,
                    "missing_keys": missing,
                    "produced_keys": list(output.keys()),
                    "node": self.__class__.__name__,
                },
                diagnosis=f"[{self.__class__.__name__}] Output validation failed: missing keys {missing}",
            )

        return None

    @abstractmethod
    def run(self, input_data: Any) -> Verdict:
        ...


class DynamicRouter(Router):
    """F3 拓扑拆分产生的动态节点 — 用指定 prompt 运行 LLM

    DESCRIPTION 在 __init__ 时由 node_description 设置。

    DynamicRouter 是通用容器：接收配置化的 system prompt，
    将输入透传给 LLM 并返回结果。用于实现 F3 拓扑变异
    插入的新节点，不需要手写新的 Router 类。
    """

    INPUT_KEYS = ["system_prompt", "messages"]
    FORMAT_IN = "agent-state"
    FORMAT_OUT = "agent-action"

    def __init__(
        self,
        *,
        node_prompt: str,
        node_description: str = "",
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.node_prompt = node_prompt
        self.DESCRIPTION = node_description or "可配置提示词的 LLM 动态节点"
        self.model = model
        self.base_url = base_url
        self.api_key = api_key

    async def run(self, input_data: Any) -> Verdict:
        import asyncio
        from omnicompany.runtime.llm.llm import LLMClient

        messages = input_data.get("messages", [])
        system_prompt = input_data.get("system_prompt", "")

        augmented_system = f"{system_prompt}\n\n## Dynamic Node Instructions\n{self.node_prompt}"

        try:
            client = LLMClient(
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
                tools=[],
            )
            response = await asyncio.to_thread(
                client.call, messages=messages, system=augmented_system,
            )

            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            updated_messages = messages + [
                {"role": "assistant", "content": [{"type": "text", "text": text}]},
                {"role": "user", "content": f"[{self.node_description or 'dynamic node'}] processed. Continue."},
            ]

            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "messages": updated_messages,
                    "system_prompt": system_prompt,
                    "dynamic_node_output": text[:500],
                },
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.PASS,
                output={**input_data, "dynamic_node_error": str(e)},
            )


class ContextRouter(Router):
    """拼接 messages — 确定性整流器

    输入: dict with keys:
        system_prompt: str
        user_input: str (首轮) 或 None (后续轮)
        messages: list[dict] (已有的 messages 历史)
        tool_results: list[dict] | None (工具执行结果列表, 多 tool_call 支持)
        tool_result: str | None (单个工具结果, 向后兼容)
        tool_use_id: str | None (单个 tool_use id, 向后兼容)

    输出 (PASS): 完整的 messages list (Anthropic 格式)

    上下文窗口管理 (Wave 2 升级):
        L1 — 工具结果老化: 距最近 N 轮前的工具结果替换为一行摘要
        L2 — 单条截断: 过长的工具输出截断到 max_tool_output 字符
        L3 — 滑动窗口: 总消息数超过 max_messages 时保头保尾
    """

    DESCRIPTION = "拼接对话消息上下文（确定性，总是 PASS）"
    FORMAT_IN = "agent-state"
    FORMAT_OUT = "agent-state"
    INPUT_KEYS = ["system_prompt"]

    def summarize_input(self, input_data):
        msgs = len(input_data.get("messages", []))
        has_user_input = bool(input_data.get("user_input"))
        has_tool_results = bool(input_data.get("tool_results"))
        return f"messages={msgs}, user_input={has_user_input}, tool_results={has_tool_results}"

    def __init__(self, *, max_messages: int = 40, max_tool_output: int = 3000,
                 aging_threshold: int = 6):
        self.max_messages = max_messages
        self.max_tool_output = max_tool_output
        self.aging_threshold = aging_threshold  # 超过 N 轮的工具结果被老化

    def _truncate_content(self, content: str) -> str:
        """截断过长的工具输出"""
        if len(content) <= self.max_tool_output:
            return content
        half = self.max_tool_output // 2
        return content[:half] + f"\n... [{len(content) - self.max_tool_output} chars truncated] ...\n" + content[-half:]

    def _age_tool_results(self, messages: list[dict]) -> list[dict]:
        """L1 工具结果老化：将老旧的 tool_result 内容替换为一行摘要。

        逻辑（学自 Claude Code 的 microCompact）：
        - 从后往前数，找到最近 N 轮 assistant 消息的位置
        - N 轮之前的 tool_result 内容替换为 "[已执行，结果已省略]"
        - 保持 tool_use_id 结构不变（API 格式合法性）
        """
        if not messages or self.aging_threshold <= 0:
            return messages

        # 从后往前数 assistant 消息
        assistant_count = 0
        aging_boundary = 0  # 老化分界线：此索引之前的都要老化
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant":
                assistant_count += 1
                if assistant_count >= self.aging_threshold:
                    aging_boundary = i
                    break

        if aging_boundary == 0:
            return messages  # 消息不够多，不需要老化

        result = []
        for i, msg in enumerate(messages):
            if i >= aging_boundary:
                result.append(msg)
                continue

            # 老化分界线之前：压缩 tool_result 内容
            content = msg.get("content")
            if msg.get("role") == "user" and isinstance(content, list):
                new_blocks = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        old_content = block.get("content", "")
                        if isinstance(old_content, str) and len(old_content) > 100:
                            # 保留首行作为摘要线索
                            first_line = old_content.split("\n")[0][:80]
                            new_blocks.append({
                                **block,
                                "content": f"[{first_line}... 内容已老化省略]",
                            })
                        else:
                            new_blocks.append(block)
                    else:
                        new_blocks.append(block)
                result.append({**msg, "content": new_blocks})
            else:
                result.append(msg)

        return result

    def _trim_messages(self, messages: list[dict]) -> list[dict]:
        """滑动窗口：保留第1条（任务描述）+ 最近 N-1 条"""
        if len(messages) <= self.max_messages:
            return messages
        # 保留首条 user message（任务描述）+ 最近的消息
        return [messages[0]] + messages[-(self.max_messages - 1):]

    def run(self, input_data: Any) -> Verdict:
        messages: list[dict] = list(input_data.get("messages", []))
        user_input = input_data.get("user_input")
        tool_results = input_data.get("tool_results")

        if user_input and not messages:
            # 首轮: 加入用户输入
            messages.append({"role": "user", "content": user_input})
        elif tool_results:
            # 后续轮: 加入所有工具执行结果 (批量)
            content_blocks = []
            for tr in tool_results:
                content_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tr["tool_use_id"],
                    "content": self._truncate_content(tr["content"]),
                })
            messages.append({"role": "user", "content": content_blocks})

        # L1: 工具结果老化（Wave 2）
        messages = self._age_tool_results(messages)

        # L3: 滑动窗口
        messages = self._trim_messages(messages)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "system_prompt": input_data.get("system_prompt", ""),
                "messages": messages,
            },
        )


class LLMRouter(Router):
    """调用 LLM — 语义整流器

    输入: dict with system_prompt + messages
    输出:
        PASS (finish tool_call 或纯文本) → 最终结果, 退出管线
        FAIL (有可执行 tool_call) → 工具调用列表, 需要 ToolRouter
        PARTIAL (反思发现信息不足) → 触发 feedback 回路补充信息

    V1.2: 原 REFLECTION_ENABLED 反思模式已于 D4 (2026-04-09) 废弃, 替代为
    LLMClient 层面的 InfoAuditReport piggyback (全局开关 OMNICOMPANY_INFO_AUDIT)。
    """

    DESCRIPTION = "调用 LLM 进行语义处理和决策"
    FORMAT_IN = "agent-state"
    FORMAT_OUT = "agent-action"
    INPUT_KEYS = ["system_prompt", "messages"]

    def summarize_output(self, verdict):
        from omnicompany.protocol.anchor import VerdictKind
        if verdict.kind == VerdictKind.PARTIAL:
            ia = verdict.info_audit
            missing = [m.description for m in (ia.missing_info if ia else [])][:3]
            return f"PARTIAL: info insufficient, missing {missing}"
        if verdict.kind == VerdictKind.FAIL:
            calls = verdict.output.get("tool_calls", []) if isinstance(verdict.output, dict) else []
            names = [c["tool_name"] for c in calls]
            return f"FAIL: {len(calls)} tool_call(s): {', '.join(names)}"
        text = str(verdict.output)
        return f"PASS: {text[:80]}{'...' if len(text) > 80 else ''}"

    def __init__(self, client: LLMClient):
        self.client = client
        self.tracer: IntentTracer | None = None
        self.last_token_count: int = 0

    # ── 主执行 ────────────────────────────────────────────────────────

    async def run(self, input_data: Any) -> Verdict:
        import asyncio
        system_prompt = input_data.get("system_prompt", "")
        messages = input_data.get("messages", [])

        response = await asyncio.to_thread(
            self.client.call, messages=messages, system=system_prompt
        )

        usage = getattr(response, "usage", None)
        token_count = 0
        if usage:
            token_count = getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)
        self.last_token_count = token_count

        # ── Phase 2 D4: info_audit 由 LLMClient 自动解析, 直接取出 ──
        info_audit: InfoAuditReport | None = getattr(response, "info_audit", None)
        cleaned_text_override: str | None = getattr(response, "info_audit_cleaned_text", None)

        text_parts = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        text_output = cleaned_text_override if cleaned_text_override else "\n".join(text_parts)

        # 构建 assistant message (包含所有 content blocks, 跳过 thinking)
        text_seen = False
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                if not text_seen:
                    clean = cleaned_text_override if cleaned_text_override else block.text
                    assistant_content.append({"type": "text", "text": clean})
                    text_seen = True
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        updated_messages = messages + [{"role": "assistant", "content": assistant_content}]

        # 无 tool_call → PASS → 管线出口
        if not tool_uses:
            return Verdict(
                kind=VerdictKind.PASS,
                output=text_output,
                info_audit=info_audit,
            )

        # 检查是否有 finish tool_call → PASS → 管线出口
        for tool in tool_uses:
            if tool.name == "finish":
                return Verdict(
                    kind=VerdictKind.PASS,
                    output=tool.input.get("message", text_output),
                    info_audit=info_audit,
                )

        # 有可执行 tool_call → FAIL → ToolRouter
        tool_calls = []
        for tool in tool_uses:
            # 从 tool.input 复制参数，剥离 intent 字段（不传给执行器）
            args = dict(tool.input) if isinstance(tool.input, dict) else {}
            intent = args.pop("intent", None)

            # 如果 tool.input 本身是 JSON 字符串（部分兼容端点的行为），解析它
            if not args and isinstance(tool.input, str):
                import json as _json
                try:
                    parsed = _json.loads(tool.input)
                    if isinstance(parsed, dict):
                        args = parsed
                        intent = args.pop("intent", None)
                except (ValueError, TypeError):
                    pass

            tool_call_entry = {
                "tool_name": tool.name,
                "tool_args": args,
                "tool_use_id": tool.id,
            }

            if self.tracer is not None:
                _violations, _step_num = self.tracer.record_step(tool.name, intent, tool_args=args)
                tool_call_entry["_intent_step_num"] = _step_num

            tool_calls.append(tool_call_entry)

        return Verdict(
            kind=VerdictKind.FAIL,
            output={
                "tool_calls": tool_calls,
                "text": text_output,
                "system_prompt": system_prompt,
                "messages": updated_messages,
                "_token_count": token_count,
            },
            diagnosis=f"LLM requests {len(tool_calls)} tool(s): {', '.join(tc['tool_name'] for tc in tool_calls)}",
            info_audit=info_audit,
        )


class ToolRouter(Router):
    """执行工具 — 确定性整流器

    输入: dict with tool_calls, messages, system_prompt
    输出 (PASS): 所有工具执行结果 + 回传给 ContextRouter 的数据
    """

    DESCRIPTION = "执行外部工具（bash/editor/think）"
    FORMAT_IN = "agent-action"
    FORMAT_OUT = "tool-observation"
    INPUT_KEYS = ["tool_calls", "messages", "system_prompt"]

    def summarize_output(self, verdict):
        results = verdict.output.get("tool_results", []) if isinstance(verdict.output, dict) else []
        return f"PASS: {len(results)} tool result(s) collected"

    def __init__(self, executor: ToolExecutor | None = None):
        self.executor = executor or ToolExecutor()

    async def run(self, input_data: Any) -> Verdict:
        import asyncio
        tool_calls = input_data.get("tool_calls", [])
        tool_results = []

        for tc in tool_calls:
            tool_name = tc["tool_name"]
            tool_args = tc["tool_args"]
            tool_use_id = tc["tool_use_id"]

            result = await asyncio.to_thread(self.executor.execute, tool_name, tool_args)
            tool_results.append({
                "tool_use_id": tool_use_id,
                "content": result,
            })

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "system_prompt": input_data.get("system_prompt", ""),
                "messages": input_data.get("messages", []),
                "tool_results": tool_results,
            },
        )

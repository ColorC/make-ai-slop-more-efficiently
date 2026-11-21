# [OMNI] origin=claude-code ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:dashboard.ide_session.lifecycle_manager.py"
"""IDE Session Manager — 管理交互式 Agent 会话

BusAdapter 将 AgentNodeLoop._emit(event_type, payload) 转换为 FactoryEvent，
写入 SQLiteBus，供 SSE 端点实时推送到前端。

IDESession 管理单个会话的生命周期：创建 → 运行 → 完成/错误/取消。
IDESessionManager 是所有活跃会话的中央注册表。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from omnicompany.bus.base import EventBus
from omnicompany.dashboard.session_workdir import resolve_session_cwd
from omnicompany.protocol.events import EventMetadata, FactoryEvent

logger = logging.getLogger(__name__)


def _create_ide_agent(
    bus_adapter: "BusAdapter",
    cwd: str | None = None,
    active_plan: str | None = None,
) -> Any:
    """创建 NativeIdeAgent 实例 (ConfigurableAgent + AgentSpec, prompt 走 .md material).

    使用 ModelRegistry ide_agent 角色 (override → qwen3.6-max-preview).
    active_plan 让 prompt 注入 plan.md / project.md 真信息源 (跟 cc_session 共享).
    """
    from omnicompany.dashboard.native_agent import NativeIdeAgent
    return NativeIdeAgent(
        cwd=resolve_session_cwd(cwd),
        active_plan=active_plan,
        bus=bus_adapter._bus,
    )


class BusAdapter:
    """将 AgentNodeLoop 的 emit(type, payload) 调用适配为 FactoryEvent publish。

    AgentNodeLoop._emit() 会检查 hasattr(bus, "emit")，
    所以这个类只需提供 emit() 方法即可被正确调用。
    """

    def __init__(self, bus: EventBus, trace_id: str, source: str = "ide.agent"):
        self._bus = bus
        self._trace_id = trace_id
        self._source = source
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        return self._loop

    def emit(self, event_type: str, payload: dict) -> None:
        """同步接口 — AgentNodeLoop._emit 通过 hasattr 发现并调用此方法。"""
        metadata = None
        if "model" in payload or "tokens" in payload:
            metadata = EventMetadata(
                model=payload.get("model"),
                prompt_tokens=payload.get("prompt_tokens"),
                completion_tokens=payload.get("completion_tokens"),
                tool_name=payload.get("tool"),
                latency_ms=payload.get("latency_ms"),
            )

        event = FactoryEvent(
            trace_id=self._trace_id,
            parent_id=payload.get("parent_id"),
            event_type=event_type,
            source=self._source,
            payload=payload,
            timestamp=datetime.now(timezone.utc),
            metadata=metadata,
        )
        # 调度异步 publish 到事件循环
        loop = self._get_loop()
        if loop.is_running():
            loop.create_task(self._bus.publish(event))
        else:
            loop.run_until_complete(self._bus.publish(event))


SessionStatus = Literal["idle", "running", "finished", "error", "cancelled"]


class IDESession:
    """单个 IDE 交互会话。"""

    def __init__(
        self, trace_id: str, bus: EventBus, *,
        use_mock: bool = False,
        active_plan: str | None = None,
        cwd: str | None = None,
    ):
        self.trace_id = trace_id
        self.status: SessionStatus = "idle"
        self.created_at = datetime.now(timezone.utc)
        self.last_active = self.created_at
        self.task_desc: str | None = None
        self.active_plan = active_plan  # plan id (e.g. "_infra/[2026-05-01]WEB-FOUNDATION")
        self.cwd = resolve_session_cwd(cwd)
        self._bus = bus
        self._task: asyncio.Task | None = None
        self._adapter = BusAdapter(bus, trace_id)
        self._use_mock = use_mock

    @property
    def bus_adapter(self) -> BusAdapter:
        return self._adapter

    async def submit(self, instruction: str) -> str:
        """提交用户指令，发布 TASK_INTENT 事件。

        Returns:
            发布的事件 ID。
        """
        self.task_desc = instruction
        self.last_active = datetime.now(timezone.utc)

        event = FactoryEvent(
            trace_id=self.trace_id,
            event_type="task.intent",
            source="ide.user",
            payload={"instruction": instruction},
        )
        event_id = await self._bus.publish(event)
        return event_id

    async def run_agent(
        self,
        instruction: str,
        agent_factory: Any = None,
    ) -> None:
        """启动 Agent 循环（在后台 asyncio.Task 中运行）。

        Args:
            instruction: 用户指令。
            agent_factory: 可选的 AgentNodeLoop 工厂函数，
                           签名: (bus_adapter) -> AgentNodeLoop 实例。
                           如果为 None，使用 MockAgent 做 echo 回显。
        """
        self.status = "running"

        # 状态变更事件
        await self._bus.publish(FactoryEvent(
            trace_id=self.trace_id,
            event_type="agent.state.change",
            source="ide.session",
            payload={"from_state": "idle", "to_state": "running"},
        ))

        async def _run() -> None:
            try:
                # 优先使用提供的工厂，其次尝试 IDEAgentLoop，最后 MockAgent
                agent = None
                if agent_factory:
                    agent = agent_factory(self._adapter)
                elif not self._use_mock:
                    agent = _create_ide_agent(
                        self._adapter,
                        cwd=self.cwd,
                        active_plan=self.active_plan,
                    )

                if agent:
                    # 传 trace_id 保证新架构 NativeIdeAgent 的 trace_id 跟 session 对齐
                    result = await agent.run({
                        "instruction": instruction,
                        "trace_id": self.trace_id,
                    })
                else:
                    # Mock agent: echo 回显 + 模拟工具调用
                    await self._mock_agent_run(instruction)

                self.status = "finished"
            except asyncio.CancelledError:
                self.status = "cancelled"
                await self._bus.publish(FactoryEvent(
                    trace_id=self.trace_id,
                    event_type="task.error",
                    source="ide.session",
                    payload={"reason": "user_cancelled"},
                ))
            except Exception as e:
                self.status = "error"
                logger.exception("IDE agent error for trace %s", self.trace_id)
                await self._bus.publish(FactoryEvent(
                    trace_id=self.trace_id,
                    event_type="task.error",
                    source="ide.agent",
                    payload={"error": str(e), "error_type": type(e).__name__},
                ))
            finally:
                await self._bus.publish(FactoryEvent(
                    trace_id=self.trace_id,
                    event_type="agent.state.change",
                    source="ide.session",
                    payload={"from_state": "running", "to_state": self.status},
                ))

        self._task = asyncio.create_task(_run())

    async def _mock_agent_run(self, instruction: str) -> None:
        """Mock agent — 用于前端开发和 E2E 测试。"""
        # 模拟思考
        await self._bus.publish(FactoryEvent(
            trace_id=self.trace_id,
            event_type="agent.think",
            source="ide.mock_agent",
            payload={"thought": f"Analyzing: {instruction}"},
        ))
        await asyncio.sleep(0.3)

        # 模拟 LLM 调用
        await self._bus.publish(FactoryEvent(
            trace_id=self.trace_id,
            event_type="agent.llm.response",
            source="ide.mock_agent",
            payload={
                "content": f"I'll help you with: {instruction}",
                "model": "mock-model",
            },
            metadata=EventMetadata(model="mock-model", prompt_tokens=100, completion_tokens=50),
        ))
        await asyncio.sleep(0.2)

        # 模拟工具调用
        tool_event = FactoryEvent(
            trace_id=self.trace_id,
            event_type="agent.tool.call",
            source="ide.mock_agent",
            payload={"tool": "bash", "args": {"command": "echo 'Hello from mock agent'"}},
        )
        await self._bus.publish(tool_event)
        await asyncio.sleep(0.3)

        # 模拟工具结果
        await self._bus.publish(FactoryEvent(
            trace_id=self.trace_id,
            parent_id=tool_event.id,
            event_type="agent.tool.result",
            source="ide.mock_agent",
            payload={
                "tool": "bash",
                "result": "Hello from mock agent",
                "exit_code": 0,
            },
            metadata=EventMetadata(tool_name="bash", duration_ms=150),
        ))
        await asyncio.sleep(0.2)

        # 完成
        await self._bus.publish(FactoryEvent(
            trace_id=self.trace_id,
            event_type="task.finish",
            source="ide.mock_agent",
            payload={"result": "Mock task completed", "verdict": "pass"},
        ))

    async def cancel(self) -> None:
        """取消运行中的 agent。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "status": self.status,
            "task_desc": self.task_desc,
            "active_plan": self.active_plan,
            "cwd": self.cwd,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
        }


class IDESessionManager:
    """所有活跃 IDE 会话的中央注册表。"""

    def __init__(self, bus: EventBus, *, use_mock: bool = False):
        self._bus = bus
        self._sessions: dict[str, IDESession] = {}
        self._use_mock = use_mock

    def get(self, trace_id: str) -> IDESession | None:
        return self._sessions.get(trace_id)

    def create(
        self, trace_id: str, *,
        active_plan: str | None = None, cwd: str | None = None,
    ) -> IDESession:
        session = IDESession(
            trace_id, self._bus, use_mock=self._use_mock,
            active_plan=active_plan, cwd=cwd,
        )
        self._sessions[trace_id] = session
        return session

    def get_or_create(
        self, trace_id: str, *,
        active_plan: str | None = None, cwd: str | None = None,
    ) -> IDESession:
        if trace_id in self._sessions:
            return self._sessions[trace_id]
        return self.create(trace_id, active_plan=active_plan, cwd=cwd)

    async def submit_and_run(
        self,
        trace_id: str,
        instruction: str,
        agent_factory: Any = None,
        *,
        active_plan: str | None = None,
        cwd: str | None = None,
    ) -> tuple[str, str]:
        """提交指令并启动 agent。

        Returns:
            (trace_id, event_id)
        """
        session = self.get_or_create(trace_id, active_plan=active_plan, cwd=cwd)
        event_id = await session.submit(instruction)
        await session.run_agent(instruction, agent_factory)
        return session.trace_id, event_id

    async def cancel(self, trace_id: str) -> bool:
        session = self._sessions.get(trace_id)
        if not session:
            return False
        await session.cancel()
        return True

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

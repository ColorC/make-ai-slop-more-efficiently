# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.exec.session_orchestrator.facade.py"
"""PipelineSession — 标准化管线执行会话（V1.1 §5）

封装 Format 注册、Bus 初始化、TeamRunner 创建、Signal 链收集。
所有项目的入口应通过 PipelineSession 执行管线。

用法::

    session = PipelineSession(
        pipeline=build_my_pipeline(),
        bindings={"node_id": MyRouter(), ...},
        register_formats_fn=my_manifest.register_formats,
        db_path="data/events.db",
    )
    result = await session.run({"input_key": "value"})
    print(result.trace_id, result.passed)
    for sig in result.signals:
        print(f"  [{sig.node_id}] {sig.format}: {sig.text}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from omnicompany.protocol.signal import Signal
from omnicompany.protocol.format import FormatRegistry, create_builtin_registry
from omnicompany.protocol.team import TeamSpec
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.exec.runner import TeamRunner

logger = logging.getLogger(__name__)


@dataclass
class SessionResult:
    """管线执行结果。"""

    output: Any                            # 最终输出
    trace_id: str                          # 追溯 ID
    signals: list[Signal] = field(default_factory=list)  # 全链路 Signal 流
    format_checks: list[dict] = field(default_factory=list)  # Format 校验结果
    metrics: dict = field(default_factory=dict)  # 性能指标
    passed: bool = False                   # 管线是否成功完成


class PipelineSession:
    """标准化的管线执行会话。"""

    def __init__(
        self,
        pipeline: TeamSpec,
        bindings: dict[str, Router],
        *,
        register_formats_fn: Callable[[FormatRegistry], None] | None = None,
        bus: Any | None = None,
        db_path: str | Path | None = None,
        max_steps: int = 50,
        source: str | None = None,
        decision_nodes: set[str] | None = None,
    ):
        self.pipeline = pipeline
        self.bindings = bindings
        self.register_formats_fn = register_formats_fn
        self._bus = bus
        self._db_path = db_path
        self.max_steps = max_steps
        self.source = source or pipeline.group or pipeline.id
        self.decision_nodes = decision_nodes

    async def run(self, initial_input: dict) -> SessionResult:
        """执行管线，返回标准化结果。"""

        # 1. Format 注册
        registry = create_builtin_registry()
        if self.register_formats_fn:
            self.register_formats_fn(registry)

        # 2. Bus 初始化
        bus = self._bus
        if bus is None:
            if self._db_path:
                from omnicompany.bus.sqlite import SQLiteBus
                bus = SQLiteBus(db_path=str(self._db_path))
            else:
                from omnicompany.bus.memory import MemoryBus
                bus = MemoryBus()

        needs_close = self._bus is None
        await bus.connect()

        try:
            # 3. 创建 Runner
            runner = TeamRunner(
                pipeline=self.pipeline,
                bindings=self.bindings,
                bus=bus,
                max_steps=self.max_steps,
                source=self.source,
                decision_nodes=self.decision_nodes,
                format_registry=registry,
            )

            # 4. 执行
            passed = True
            try:
                output = await runner.run(initial_input)
            except RuntimeError as e:
                logger.error("Pipeline execution failed: %s", e)
                output = {"error": str(e)}
                passed = False

            # 5. 收集结果
            return SessionResult(
                output=output,
                trace_id=runner.last_trace_id,
                signals=list(runner.signals),
                format_checks=list(runner.format_checks),
                metrics=runner.metrics_summary(),
                passed=passed,
            )
        finally:
            if needs_close:
                await bus.close()

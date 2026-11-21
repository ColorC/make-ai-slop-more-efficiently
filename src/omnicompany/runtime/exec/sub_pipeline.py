# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.exec.cross_pipeline_invoker.worker.py"
"""sub_pipeline — 跨管线调用标准接口

SubTeamWorker 是 Router 的子类，标准化了"一个管线节点调用另一条已注册管线"的模式。

使用方式：
    子类设置 TARGET_PIPELINE（注册表中的管线名称），
    重写 prepare_input() 做输入 Format 映射，
    重写 extract_output() 做输出 Format 映射。

    TeamRunner 检测到 SubTeamWorker 实例时，
    在 run() 调用前注入 _bus 和 _parent_event_id，
    使子管线事件归属父管线的事件树（可观测性不丢失）。

示例::

    class CallWFRouter(SubTeamWorker):
        TARGET_PIPELINE = "workflow-factory"
        TARGET_MAX_STEPS = 30
        FORMAT_IN = "my.requirement"
        FORMAT_OUT = "my.wf-result"

        def prepare_input(self, input_data):
            return {"text": input_data["requirement_doc"]}

        def extract_output(self, sub_result):
            return {"files": sub_result.get("files", {}), ...}
"""

from __future__ import annotations

import logging
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)


class SubTeamWorker(Router):
    """调用另一条已注册管线的标准基类。

    子类必须设置：
        TARGET_PIPELINE — 注册表中的管线名称
        FORMAT_IN / FORMAT_OUT / DESCRIPTION — 标准 Router 元数据

    子类可重写：
        prepare_input()  — FORMAT_IN → 子管线入口数据
        extract_output() — 子管线产出 → FORMAT_OUT
    """

    TARGET_PIPELINE: str = ""
    """子管线在注册表中的名称（如 "workflow-factory"）。"""

    TARGET_MAX_STEPS: int = 30
    """子管线最大决策步数。"""

    # ── TeamRunner 在 run() 前注入 ──
    _bus: Any = None
    _parent_event_id: str | None = None

    def prepare_input(self, input_data: dict) -> dict:
        """将本节点的 FORMAT_IN 数据映射为子管线的入口数据。

        默认透传。子类应重写以做 Format 映射。
        """
        return input_data

    def extract_output(self, sub_result: Any, input_data: dict) -> dict:
        """将子管线的产出映射为本节点的 FORMAT_OUT 数据。

        Args:
            sub_result: 子管线 TeamRunner.run() 的返回值
            input_data: 本节点的原始输入（用于透传上下文字段）

        默认透传。子类应重写以做 Format 映射。
        """
        return sub_result if isinstance(sub_result, dict) else {"result": sub_result}

    async def run(self, input_data: Any) -> Verdict:
        """执行子管线调用。"""
        if not self.TARGET_PIPELINE:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="SubTeamWorker: TARGET_PIPELINE 未设置",
            )

        # 1. 解析注册表
        from omnicompany.core.registry import discover, get
        discover()
        entry = get(self.TARGET_PIPELINE)
        if entry is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"子管线 '{self.TARGET_PIPELINE}' 未在注册表中找到",
            )

        # 2. 准备输入
        try:
            sub_input = self.prepare_input(input_data if isinstance(input_data, dict) else {})
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"prepare_input 失败: {e}",
            )

        # 3. 构建子管线
        try:
            sub_pipeline = entry.build_team()
            sub_bindings = entry.build_bindings(sub_input)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"子管线构建失败: {e}",
            )

        # 4. 执行（共享 bus 或独立 bus）
        from omnicompany.runtime.exec.runner import TeamRunner

        if self._bus is not None:
            # 共享父管线的 EventBus（事件树归属父管线）
            runner = TeamRunner(
                sub_pipeline, sub_bindings, self._bus,
                max_steps=self.TARGET_MAX_STEPS,
                source=f"sub:{self.TARGET_PIPELINE}",
            )
            try:
                result = await runner.run(
                    sub_input,
                    parent_event_id=self._parent_event_id,
                )
            except Exception as e:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=input_data,
                    diagnosis=f"子管线执行失败: {e}",
                )
        else:
            # 降级：无 bus 时使用 dispatch（独立 bus）
            logger.warning(
                "[SubTeamWorker] _bus 未注入，降级为 dispatch（事件不可观测）"
            )
            from omnicompany.core.dispatch import dispatch
            try:
                result = await dispatch(
                    self.TARGET_PIPELINE, sub_input,
                    max_steps=self.TARGET_MAX_STEPS,
                )
            except Exception as e:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=input_data,
                    diagnosis=f"子管线 dispatch 失败: {e}",
                )

        # 5. 提取输出
        try:
            output = self.extract_output(result, input_data if isinstance(input_data, dict) else {})
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"extract_output 失败: {e}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"子管线 '{self.TARGET_PIPELINE}' 执行完成",
        )


# ── 过渡期别名 (命名迁移 B 层, 2026-04-22) ──
SubPipelineRouter = SubTeamWorker

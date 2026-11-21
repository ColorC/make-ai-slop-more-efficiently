# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.exec.cold_start_bootstrapper.self_check_orchestrator.py"
"""ColdStartBootstrapper — 冷启动引导器

理论对应：
  终点§5   冷启动可能卡死，需要人工观察和指导
  03§三    MirrorNode 在冷启动时最先初始化

冷启动序列：
  1. 注册系统域 Format
  2. 初始化 MirrorNode → 生成首版自我认知
  3. 设置 BoltzmannRouter 低 β（探索模式）
  4. 运行自检任务（认知自己 → 审计类型 → 验证痛觉）
  5. 每 N 轮请求人工审查（安全网）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BootstrapTask:
    """冷启动自检任务"""
    name: str
    prompt: str
    expected_output_type: str
    result: str = ""
    success: bool = False


BOOTSTRAP_TASKS: list[BootstrapTask] = [
    BootstrapTask(
        name="self_introspection",
        prompt=(
            "You are performing a self-check of the OmniCompany system.\n"
            "Analyze the system's source code structure and list all core modules "
            "and their responsibilities. Focus on:\n"
            "1. Protocol layer (Format, Anchor, Pipeline)\n"
            "2. Runtime layer (RouteGraph, PainSystem, BoltzmannRouter)\n"
            "3. Evolution layer (if present)\n"
            "Use the `think` tool to record your analysis, then `finish` with a summary."
        ),
        expected_output_type="omnicompany.markdown.self_concept",
    ),
    BootstrapTask(
        name="type_system_audit",
        prompt=(
            "You are auditing the OmniCompany type system.\n"
            "Check the FormatRegistry for all registered types. Verify that:\n"
            "1. System types (omnicompany.*) are registered\n"
            "2. Trace types (trace.*) are registered\n"
            "3. No duplicate IDs exist\n"
            "Use bash to run: python -c \"from omnicompany.protocol.format import "
            "create_builtin_registry; r = create_builtin_registry(); "
            "print([f.id for f in r.list_all()])\"\n"
            "Then `finish` with the audit result."
        ),
        expected_output_type="omnicompany.json.type_audit_report",
    ),
    BootstrapTask(
        name="pain_system_verify",
        prompt=(
            "You are verifying the pain system's Death Zone rules.\n"
            "Run: python -c \"from omnicompany.runtime.signals.pain_system import "
            "BUILT_IN_RULES; print([(r.rule_id, r.description) for r in BUILT_IN_RULES])\"\n"
            "Verify that at least 3 rules exist and all have descriptions.\n"
            "Then `finish` with the verification result."
        ),
        expected_output_type="trace.log.pain_verification",
    ),
]


class ColdStartBootstrapper:
    """冷启动引导器

    负责系统从零开始的初始化序列。
    核心设计理念：冷启动不试图自动完成一切，
    而是建立最小可观测状态，让人工可以介入。

    "冷启动可能完全卡死" 这个认知本身就是系统自我认知的一部分。
    """

    def __init__(
        self,
        src_root: Path,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        initial_beta: float = 0.5,
        human_supervision_interval: int = 3,
    ):
        self.src_root = Path(src_root)
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.initial_beta = initial_beta
        self.human_supervision_interval = human_supervision_interval
        self.round_count = 0
        self.tasks = [BootstrapTask(**t.__dict__) for t in BOOTSTRAP_TASKS]

    async def bootstrap(self) -> dict[str, Any]:
        """执行冷启动序列。

        Returns:
            状态报告 dict，含各阶段结果。
        """
        report: dict[str, Any] = {"stages": [], "success": True}

        # Stage 1: 注册系统域 Format
        try:
            from omnicompany.protocol.format import create_builtin_registry
            registry = create_builtin_registry()
            system_types = [f.id for f in registry.list_all() if f.id.startswith("omnicompany.")]
            report["stages"].append({
                "name": "register_system_formats",
                "success": True,
                "system_types": system_types,
            })
            logger.info("Cold start: registered %d system format types", len(system_types))
        except Exception as e:
            report["stages"].append({
                "name": "register_system_formats",
                "success": False,
                "error": str(e),
            })
            logger.error("Cold start: format registration failed: %s", e)

        # Stage 2: 初始化 MirrorNode → 生成首版自我认知
        mirror = None
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            from omnicompany.runtime.signals.mirror_node import MirrorNode

            llm = LLMClient(
                model=self.model, base_url=self.base_url,
                api_key=self.api_key, tools=[],
            )
            mirror = MirrorNode(self.src_root, llm_client=llm)
            mirror.invalidate()
            concept = mirror.get_current_concept()
            report["stages"].append({
                "name": "mirror_node_init",
                "success": bool(concept),
                "concept_length": len(concept),
                "concept_preview": concept[:200] if concept else "",
            })
            logger.info("Cold start: self-concept generated (%d chars)", len(concept))
        except Exception as e:
            report["stages"].append({
                "name": "mirror_node_init",
                "success": False,
                "error": str(e),
            })
            logger.error("Cold start: MirrorNode init failed: %s", e)

        # Stage 3: 运行自检任务
        from omnicompany.runtime.agent.agent_loop import run_agent

        for task in self.tasks:
            self.round_count += 1
            logger.info(
                "Cold start: running self-check [%d/%d] %s",
                self.round_count, len(self.tasks), task.name,
            )
            try:
                result = await run_agent(
                    task.prompt,
                    model=self.model,
                    base_url=self.base_url,
                    api_key=self.api_key,
                    max_steps=20,
                    mirror=mirror,
                )
                task.result = result[:500] if result else ""
                task.success = bool(result)
            except Exception as e:
                task.result = f"Error: {e}"
                task.success = False
                logger.warning("Cold start: task %s failed: %s", task.name, e)

            report["stages"].append({
                "name": f"self_check_{task.name}",
                "success": task.success,
                "result_preview": task.result[:200],
            })

            if self.round_count % self.human_supervision_interval == 0:
                self._request_human_review()

        report["success"] = all(
            s.get("success", False) for s in report["stages"]
        )
        return report

    def _request_human_review(self) -> None:
        """冷启动期的安全网：定期请求人工审查。"""
        logger.warning(
            "[COLD START] Round %d: Human review requested. "
            "Check system growth matches theoretical principles.",
            self.round_count,
        )

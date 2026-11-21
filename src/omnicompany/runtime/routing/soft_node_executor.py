# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.routing.soft_node_executor.llm_pipeline_engine.py"
"""SoftNodeExecutor — executes DB-stored semantic nodes via LLM.

Soft nodes are natural-language-described processing units:
    input_types -> processing_prompt (LLM execution) -> output_types

Key design: single-direction transform (output of node N = input of node N+1),
NOT context accumulation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from omnicompany.runtime.route_graph import RouteGraph, SemanticNode
    from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class SoftNodeResult:
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    node_id: str = ""
    tokens_used: int = 0


class SoftNodeExecutor:
    """Executes a SemanticNode by calling LLM with its processing_prompt."""

    def __init__(
        self,
        llm_client: "LLMClient",
        route_graph: "RouteGraph",
    ):
        self._llm = llm_client
        self._graph = route_graph

    async def execute(
        self, node: "SemanticNode", input_data: dict[str, Any]
    ) -> SoftNodeResult:
        """Execute a single soft node.

        Constructs an LLM prompt from node.processing_prompt + input_data,
        calls LLM, parses output into structured result.
        """
        prompt = self._build_prompt(node, input_data)

        try:
            response = await asyncio.to_thread(
                self._llm.call,
                messages=[{"role": "user", "content": prompt}],
                system=(
                    "You are a semantic processing node. "
                    "Follow the processing instructions precisely. "
                    "Return your output as JSON with an 'output' field containing "
                    "the processed result, and 'output_types' listing the semantic "
                    "types of your output."
                ),
            )

            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            tokens = 0
            usage = getattr(response, "usage", None)
            if usage:
                tokens = getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)

            parsed = self._parse_output(text, node)

            self._graph.record_semantic_node_execution(node.node_id, success=True)

            return SoftNodeResult(
                success=True,
                output=parsed,
                node_id=node.node_id,
                tokens_used=tokens,
            )

        except Exception as e:
            logger.warning("SoftNode %s execution failed: %s", node.node_id[:12], e)
            self._graph.record_semantic_node_execution(node.node_id, success=False)
            return SoftNodeResult(
                success=False,
                error=str(e),
                node_id=node.node_id,
            )

    async def execute_path(
        self,
        path: list["SemanticNode"],
        initial_input: dict[str, Any],
    ) -> SoftNodeResult:
        """Execute a chain of soft nodes in sequence.

        Single-direction transform: output of node N becomes input of node N+1.
        Aborts on first failure.
        """
        current_data = initial_input
        total_tokens = 0

        for i, node in enumerate(path):
            result = await self.execute(node, current_data)
            total_tokens += result.tokens_used

            if not result.success:
                logger.info(
                    "SoftPath aborted at node %d/%d (%s): %s",
                    i + 1, len(path), node.node_id[:12], result.error,
                )
                result.tokens_used = total_tokens
                return result

            current_data = result.output

        return SoftNodeResult(
            success=True,
            output=current_data,
            node_id=path[-1].node_id if path else "",
            tokens_used=total_tokens,
        )

    @staticmethod
    def _build_prompt(node: "SemanticNode", input_data: dict[str, Any]) -> str:
        # 2026-04-18 零容忍截断：完整 input 进 prompt。若溢出，LLM API 报错
        # 由上层处理（llm_first.md 原则 3）。之前 [:4000] 会静默丢后半。
        input_summary = json.dumps(input_data, ensure_ascii=False, default=str)

        return (
            f"## Processing Instructions\n"
            f"{node.processing_prompt}\n\n"
            f"## Input (semantic types: {', '.join(node.input_types)})\n"
            f"{input_summary}\n\n"
            f"## Expected Output Types: {', '.join(node.output_types)}\n\n"
            f"Process the input according to the instructions and return JSON:\n"
            f'{{"output": <your processed result>, '
            f'"output_types": {json.dumps(node.output_types)}}}'
        )

    @staticmethod
    def _parse_output(text: str, node: "SemanticNode") -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                if "output" in data:
                    return data
                return {"output": data, "output_types": node.output_types}
        except (json.JSONDecodeError, ValueError):
            pass

        return {
            "output": text,
            "output_types": node.output_types,
            "_raw": True,
        }

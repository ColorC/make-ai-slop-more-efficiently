# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""VerifyPlanExecutionRouter · 验证 plan 实施完成度 SingleTool, 对齐 claude-code VerifyPlanExecutionTool.

核心:
  - 输入: plan 文档 (markdown 进度勾) + 实施事实 (git diff / file list / test 结果)
  - 输出: 结构化判断 — 哪些勾真打了, 哪些勾下面的内容没真做
  - 调 LLM (qwen-3.6-plus) 做语义比对, 不是字符串硬扫
  - 失败 → 降级 (返"无法判断, 人工核")

omnicompany 对应场景:
  - plan §进度 章节有 - [x] 勾, 但实际代码/测试是否真兑现了不一定
  - 此工具让 agent 在归档 plan 前自检
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "qwen-3.6-plus"

_SYSTEM_PROMPT = """\
你是 OmniGuardian 的 plan 实施完成度验证员.

任务: 给你一份 plan 文档 (markdown, 含进度勾 - [x] / - [ ]) 和实施事实清单,
逐条判断"勾上的"是否真在事实里有对应支撑.

【输出 JSON 严格格式】
{
  "verified_done": [{"item": "<勾上条目>", "evidence": "<事实里的支撑>"}],
  "claimed_done_no_evidence": [{"item": "<勾上但找不支撑的>", "reason": "<找不到啥>"}],
  "still_pending": [{"item": "<未勾的条目>"}],
  "summary": "<一句话, ≤80 字>"
}

只输出 JSON, 不要 markdown fence.
"""


class VerifyPlanExecutionRouter(SingleToolRouter):
    """Verify plan implementation completeness using LLM (compare plan checkboxes vs evidence)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.read_file",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "VerifyPlanExecution"
    DESCRIPTION: ClassVar[str] = (
        "Verify a plan document's implementation completeness via LLM.\n"
        "\n"
        "- Reads markdown plan with - [x] / - [ ] checkboxes.\n"
        "- Compares ticked items against evidence (git diff / file list / test output).\n"
        "- Returns structured JSON: verified_done / claimed_done_no_evidence / still_pending.\n"
        "- Use before archiving a plan to catch 'checked but not actually done' debt."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "plan_path": {
                "type": "string",
                "description": "Absolute path to plan.md",
            },
            "evidence": {
                "type": "string",
                "description": "Evidence text: git diff / file list / test output / commit log",
            },
            "model": {
                "type": "string",
                "description": f"LLM model (default {_DEFAULT_MODEL})",
            },
        },
        "required": ["plan_path", "evidence"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        plan_path = (args.get("plan_path") or "").strip()
        evidence = args.get("evidence", "")
        model = args.get("model") or _DEFAULT_MODEL

        if not plan_path:
            raise ToolExecutionError("plan_path is required")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ToolExecutionError("evidence is required (non-empty string)")

        path = Path(plan_path)
        if not path.is_absolute():
            raise ToolExecutionError(f"plan_path must be absolute: {plan_path}")
        if not path.exists():
            raise ToolExecutionError(f"plan does not exist: {plan_path}")

        try:
            plan_content = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ToolExecutionError(f"failed to read {plan_path}: {e}")

        # 干跑: 静态扫勾, 不调 LLM
        if os.environ.get("OMNI_VERIFY_PLAN_DRY_RUN") == "1":
            return self._dry_run_static_scan(plan_content)

        # 调 LLM
        try:
            from omnicompany.runtime.llm.llm import LLMClient
            client = LLMClient()
        except Exception as e:
            raise ToolExecutionError(f"LLMClient init failed: {e}")

        user_msg = (
            f"=== plan 文档 ({plan_path}) ===\n"
            f"{plan_content}\n\n"
            f"=== 实施事实 ===\n"
            f"{evidence}\n\n"
            f"按 SYSTEM 指引输出 JSON."
        )

        try:
            response = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=_SYSTEM_PROMPT,
                caller="agent.verify_plan_execution",
            )
            if hasattr(response, "content"):
                text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
            else:
                text = str(response).strip()
        except Exception as e:
            raise ToolExecutionError(f"LLM call failed: {e}")

        if not text:
            raise ToolExecutionError("LLM returned empty")

        # 容错 JSON 解析
        s = text.strip()
        if s.startswith("```"):
            s = s.split("\n", 1)[1] if "\n" in s else s
            if s.endswith("```"):
                s = s[:-3]
            s = s.strip()
            if s.startswith("json"):
                s = s[4:].strip()
        try:
            obj = json.loads(s)
        except Exception as e:
            raise ToolExecutionError(
                f"failed to parse LLM JSON: {e}. Raw (truncated): {text[:300]}"
            )

        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _dry_run_static_scan(self, plan_content: str) -> str:
        """干跑: 仅按勾扫提取条目, 不做完成度判断."""
        ticked = re.findall(r"-\s+\[x\]\s+(.+)$", plan_content, re.MULTILINE)
        unticked = re.findall(r"-\s+\[\s\]\s+(.+)$", plan_content, re.MULTILINE)
        return json.dumps({
            "verified_done": [{"item": t, "evidence": "(dry-run, not verified)"} for t in ticked],
            "claimed_done_no_evidence": [],
            "still_pending": [{"item": t} for t in unticked],
            "summary": (
                f"OMNI_VERIFY_PLAN_DRY_RUN=1: {len(ticked)} ticked, "
                f"{len(unticked)} unticked (no LLM verification)."
            ),
        }, ensure_ascii=False, indent=2)

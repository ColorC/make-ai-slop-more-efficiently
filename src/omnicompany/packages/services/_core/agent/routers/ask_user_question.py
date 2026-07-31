# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""AskUserQuestionRouter · 多选题询问用户 SingleTool, 对齐 claude-code AskUserQuestionTool.

参考: 参考项目/claude-code-analysis/src/tools/AskUserQuestionTool/prompt.ts

核心行为:
  - 提一个多选问题给用户, 收集回答
  - options: list[{label, description, value?}], 用户选其一 (或 multiSelect)
  - "Other" 选项默认追加 (用户可填自由文本)
  - omnicompany 实现: 走 HumanBus (要求 worker 真接到人类应答的总线)

注: omnicompany 不是聊天产品, 但有 HumanBus (humanbus material-gated human gate).
此工具用法: 把问题 emit 到 HumanBus, 阻塞等 approval.yaml 答复, 解析为 result.
没有 HumanBus 时 (单元测试 / 干跑) 走 ASK_USER_QUESTION_DRY_RUN=1 模拟.
"""
from __future__ import annotations

import json
import logging
import os
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


class AskUserQuestionRouter(SingleToolRouter):
    """Ask the user a multiple-choice question to clarify requirements / get a decision."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.user.input",)
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "AskUserQuestion"
    DESCRIPTION: ClassVar[str] = (
        "Asks the user a multiple-choice question to gather information, clarify ambiguity, "
        "understand preferences, make decisions or offer them choices.\n"
        "\n"
        "Usage:\n"
        "- Each question has a list of options (label + description per option).\n"
        "- Users will always be able to select 'Other' to provide custom text input.\n"
        "- Use multiSelect=true to allow multiple answers.\n"
        "- If you recommend a specific option, make it the first and add '(Recommended)' to its label.\n"
        "- Do NOT use this tool for trivial yes/no clarifications you can answer yourself."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask",
            },
            "options": {
                "type": "array",
                "description": "Multiple choice options",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Short option label"},
                        "description": {"type": "string", "description": "Longer description"},
                    },
                    "required": ["label"],
                },
                "minItems": 2,
            },
            "multiSelect": {
                "type": "boolean",
                "description": "Allow multiple answers (default false)",
            },
            "header": {
                "type": "string",
                "description": "Optional context shown above the question",
            },
        },
        "required": ["question", "options"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        question = (args.get("question") or "").strip()
        options = args.get("options", [])
        multi_select = bool(args.get("multiSelect", False))
        header = (args.get("header") or "").strip()

        if not question:
            raise ToolExecutionError("question is required (non-empty string)")
        if not isinstance(options, list) or len(options) < 2:
            raise ToolExecutionError("options must be a list with at least 2 entries")

        for i, opt in enumerate(options):
            if not isinstance(opt, dict) or not opt.get("label"):
                raise ToolExecutionError(f"options[{i}] missing required 'label' field")

        # 干跑模式: 返回首选项, 用于离线测试
        if os.environ.get("ASK_USER_QUESTION_DRY_RUN") == "1":
            picked = options[0]
            return json.dumps({
                "answer": picked["label"],
                "answer_index": 0,
                "is_other": False,
                "mode": "dry_run",
            }, ensure_ascii=False)

        # 真模式: emit 到 HumanBus 等响应
        # ctx 上 worker 注入 human_bus + answer_callback
        human_bus = getattr(ctx, "human_bus", None)
        if human_bus is None:
            raise ToolExecutionError(
                "AskUserQuestion requires a HumanBus injected via ToolContext.human_bus. "
                "In offline / unit-test contexts, set ASK_USER_QUESTION_DRY_RUN=1 to get the first option."
            )

        try:
            response = human_bus.ask(
                question=question,
                options=options,
                multi_select=multi_select,
                header=header,
            )
        except Exception as e:
            raise ToolExecutionError(f"HumanBus.ask failed: {e}")

        if not isinstance(response, dict):
            raise ToolExecutionError(f"HumanBus.ask returned non-dict: {type(response).__name__}")

        return json.dumps(response, ensure_ascii=False)

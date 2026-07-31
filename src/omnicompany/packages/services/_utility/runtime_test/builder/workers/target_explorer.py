# [OMNI] origin=claude-code domain=services/runtime_test_builder/workers ts=2026-04-27T00:00:00Z type=worker
# [OMNI] material_id="material:utility.runtime_test.builder.target_explorer.agent.py"
# OMNI-024 ALLOW: 域内私有 worker, 一类一文件经 workers/__init__ 聚合出口, 类实现与本文件模块级 prompt/helper/装配紧耦合, 迁入共享 routers.py 会割裂内聚
"""TargetExplorerWorker — Worker #1 (AGENT, 真 meta 层 v2 入口).

替代旧 TargetAnalyzerAndSpecBuilderWorker.

新职责: 不是判产物形态二选一, 是**深探 target 包**, 产 target_profile (多维度自然语言描述).
profile 给下游 HypothesisProposer 当原料用.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    GlobRouter,
    GrepRouter,
    ListDirRouter,
    ReadFileRouter,
    SingleToolRouter,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_config import PRESET_LIGHTWEIGHT

from .._paths import PROJECT_ROOT

_PROJECT_ROOT = PROJECT_ROOT


_SYSTEM_PROMPT = """你是 runtime_test_builder · TargetExplorer (真 meta 层 v2 第 1 节点).

## 你的任务

深探 target 包, 产**多维度 target_profile**. 不是二选一判"代码产物 vs 知识产物", 是写描述性 profile.

## 工具

- read_file / glob / grep / list_dir
- submit_target_profile: 终结提交

## 探包动作 (顺序)

1. read_file `<package_path>/DESIGN.md` 看产物描述 + 设计目的
2. read_file `<package_path>/formats.py` 看主 sink Material 的 schema 字段
3. read_file `<package_path>/team.py` 看末节点 / topology
4. glob `<package_path>/workers/*.py` 看 worker 类型
5. 若 tests/teams/<target>/ 或 docs/plans/.../requirements/<target>/ 存在 → read_file 看是否有 fixtures + expected
6. (可选) 看 sample_input 字段 → 推 input shape

## 提交字段 (严格)

- target_team_id
- package_path: target 源码目录
- output_format_summary: 1-3 句子, 主输出 Material 形态 + 关键字段类型
- design_purpose: 1-3 句子, target 工作目的, 自然语言
- product_kind_signals: ≥3 条句子, 多角度产物形态线索 (例: "输出含 proposals 列表带 reference_code", "FORMAT_OUT schema 含字面量约束章节标题", "tests/teams/csv_to_md/ 有 6 条 byte-exact fixtures")
- has_fixtures (bool): tests/teams/<target>/ 或 docs/plans/.../requirements/<target>/ 是否有 expected
- has_repo_input (bool): sample_input/FORMAT_IN 是否含 repo_path 类源仓库
- has_byte_diffable_output (bool): 输出是否可字节比 (有标杆 / 确定性映射)
- has_external_anchors (bool): 输出是否含 file/line/func/URL 等外部锚点
- has_random_or_creative (bool): 输出是否带随机或创意成分 (LLM 自由文本视为创意)
- consumed_input_shape: 1-2 句子, sample_input 形态简述

## 反模式

- 禁打分 / 标签 (除 bool 字段)
- 禁二选一硬归类 (像 "代码产物" "知识产物" 这种粗分)
- 禁套老模板 — 看到什么写什么"""


class _PromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        target_team_id = biz_input.get("target_team_id", "?")
        sample_input_hint = biz_input.get("sample_input_hint")

        pkg_name = target_team_id.replace("-", "_")
        team_code_dir = _PROJECT_ROOT / "src" / "omnicompany" / "packages" / "services" / pkg_name
        if not team_code_dir.is_dir():
            alt = _PROJECT_ROOT / "src" / "omnicompany" / "packages" / "services" / target_team_id
            if alt.is_dir():
                team_code_dir = alt

        tests_dir = _PROJECT_ROOT / "tests" / "teams" / pkg_name
        plan_dir_pattern = "docs/plans/*/requirements/" + pkg_name

        hint_str = ""
        if isinstance(sample_input_hint, dict) and sample_input_hint:
            import json as _json
            hint_str = (
                f"\n\n**用户提供 sample_input_hint** (可参考):\n```json\n"
                f"{_json.dumps(sample_input_hint, ensure_ascii=False, indent=2)}\n```"
            )

        task = f"""## 任务: 深探 target `{target_team_id}` 产 target_profile

### 关键路径
- target 包目录: `{team_code_dir}` ({'存在' if team_code_dir.is_dir() else '不存在'})
- target 测试目录: `{tests_dir}` ({'存在' if tests_dir.is_dir() else '不存在'})
- 计划/需求目录模式: `{plan_dir_pattern}` (用 glob 探)
{hint_str}

### 操作

按系统提示步骤探包. 探完后调 `submit_target_profile` 一次提交所有字段.

注: 不要超出 30 turn. 走重点不深扣每个 worker 内部."""

        return [{"role": "user", "content": task}]


class SubmitTargetProfileRouter(SingleToolRouter):
    TOOL_NAME: ClassVar[str] = "submit_target_profile"
    DESCRIPTION: ClassVar[str] = "Submit the target profile after exploration."
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "target_team_id": {"type": "string"},
            "package_path": {"type": "string"},
            "output_format_summary": {"type": "string", "minLength": 30},
            "design_purpose": {"type": "string", "minLength": 30},
            "product_kind_signals": {
                "type": "array",
                "minItems": 3,
                "items": {"type": "string", "minLength": 10},
            },
            "has_fixtures": {"type": "boolean"},
            "has_repo_input": {"type": "boolean"},
            "has_byte_diffable_output": {"type": "boolean"},
            "has_external_anchors": {"type": "boolean"},
            "has_random_or_creative": {"type": "boolean"},
            "consumed_input_shape": {"type": "string"},
        },
        "required": [
            "target_team_id",
            "package_path",
            "output_format_summary",
            "design_purpose",
            "product_kind_signals",
        ],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args, ctx) -> str:
        return f"submitted target_profile for {args.get('target_team_id')}"


class _ExtractResult(ExtractResultRouter):
    def extract(self, *, final_text, messages, turn_count, stop_reason) -> Verdict:
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "submit_target_profile"
                    ):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            inp.setdefault("has_fixtures", False)
                            inp.setdefault("has_repo_input", False)
                            inp.setdefault("has_byte_diffable_output", False)
                            inp.setdefault("has_external_anchors", False)
                            inp.setdefault("has_random_or_creative", False)
                            inp.setdefault("consumed_input_shape", "")
                            return Verdict(
                                kind=VerdictKind.PASS,
                                output=dict(inp),
                                diagnosis=f"target_profile for {inp.get('target_team_id')}",
                                confidence=0.9,
                            )
        return Verdict(
            kind=VerdictKind.FAIL,
            output={},
            diagnosis=f"未调 submit_target_profile (turns={turn_count})",
        )


class TargetExplorerWorker(AgentNodeLoop):
    DESCRIPTION: ClassVar[str] = "深探 target 包产 target_profile · 多维度自然语言描述 · 非二选一."
    FORMAT_IN: ClassVar[str] = "runtime_test_builder.build_request"
    FORMAT_OUT: ClassVar[str] = "runtime_test_builder.target_profile"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter,
        GlobRouter,
        GrepRouter,
        ListDirRouter,
        SubmitTargetProfileRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT
    LOOP_CONFIG = PRESET_LIGHTWEIGHT
    TERMINATING_TOOLS = ("submit_target_profile",)

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any):
        return _PromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any):
        return _ExtractResult(bus=bus)

# [OMNI] origin=claude-code domain=services/team_supervisor/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.purpose_interpreter.explorer.py"
"""PurposeInterpreterWorker — team_supervisor Worker #3 (AGENT).

Worker 协议:
  FORMAT_IN  = team_supervisor.target_metadata
  FORMAT_OUT = team_supervisor.design_purpose_brief

职责: Q2 设计目的答案. 通过 ReadFile/Glob/Grep 探索 DESIGN.md + team.py docstring +
      worker docstring + dispatch 调用方代码, 用自然语言句子产 design_purpose_brief.

铁律:
- 全字段自然语言句子, 禁分类 / 打分 / 标签
- evidence_sources 至少 1 条引用 (避免 LLM 凭空发挥)
- 末步必调 submit_design_purpose 工具
"""
from __future__ import annotations

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
    ToolContext,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM_PROMPT = """你是 team_supervisor 的 PurposeInterpreter · 一个深度阅读的 Agent.

## 你的任务

回答**第二个基本问题**: 这个 target team 为什么存在? 解决什么具体问题?

输入是 target_metadata, 含 target team 的代码目录 + DESIGN.md 路径. 你需要主动探索:

1. **DESIGN.md** (若存在): 第 1-2 节通常含名称/职责描述, 第 6 节边界与约束含 non-goals 暗示
2. **team.py docstring**: build_team() 函数文档常说明拓扑目的
3. **worker docstring**: 各 worker 的"职责"段
4. **调用方代码**: grep 整个 src/ 找 dispatch("<target_id>", ...) 看谁在使用它

## 反模式禁令 (绝对不要)

❌ 不要给 team "归类" (`type: "data_pipeline"` 这种)
❌ 不要"评估"它的成熟度/质量/复杂度 (你不是评分器)
❌ 不要用枚举词标签 (`category`, `kind`, `tier`)

## 正确姿势

✅ `essence`: "这个 team 用来 ... 解决 ..." (≥30 字符, 必含具体问题描述)
✅ `replaces`: "没有它时, 用 ... 手段做这事" (人工? 别的工具? 没人做?)
✅ `non_goals` (≥1 条): 它**不**做什么 · 反向定义 · 句子 ("不做单元测试自动生成", "不修改 target 源码")
✅ `stakeholder_use`: "谁会消费它产出 · 怎么用" (具体场景句)
✅ `evidence_sources` (≥1 条): 推断依据 · 每条 {file, section} 引用具体来源

## 关键约束

1. **non_goals 必须从代码或文档真推断出来**, 不要凭空想
   - 看 DESIGN.md "边界与约束" 节
   - 看 worker docstring 的"不做" / "不调 LLM" 等
   - 看 team.py 节点 maturity 是否标注 `STABLE / GROWING`
2. **evidence_sources 必须引用真实存在的文件**, 不要虚构路径
3. **末步必调 submit_design_purpose 工具**

## 工具

- **read_file**: 读 DESIGN.md / team.py / workers/*.py (全量不截断)
- **glob**: `**/*.py` `**/DESIGN.md`
- **grep**: `dispatch.*<target_id>` `not.*do` `职责` `目的` 找语义线索
- **list_dir**: 列目录
- **submit_design_purpose**: 终结性提交"""


class _PurposePromptBuilder(PromptBuilderRouter):
    """把 target_metadata 注入 agent 首轮会话."""

    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        target_team_id = biz_input.get("target_team_id", "?")
        team_code_dir = biz_input.get("team_code_dir", "")
        team_design_md = biz_input.get("team_design_md_path", "")
        team_py = biz_input.get("team_py_path", "")
        worker_files = biz_input.get("worker_files", [])

        worker_list = "\n".join(f"  - workers/{w}" for w in worker_files[:30]) or "  (无)"
        design_note = (
            f"- DESIGN.md: `{team_design_md}` (优先读, 含名称/职责/边界)"
            if team_design_md
            else "- DESIGN.md: 不存在 (跳过, 仅靠代码 docstring 推断)"
        )

        task = f"""## 任务: 回答 Q2 — target team `{target_team_id}` 为什么存在?

### Target 元数据

- target_team_id: `{target_team_id}`
- team_code_dir: `{team_code_dir}`
{design_note}
- team.py: `{team_py}`
- workers/ 文件:
{worker_list}

### 探索建议

1. 若 DESIGN.md 存在, `read_file` 读它 — 第 1-2 节 (名称/职责) 与第 6 节 (边界/约束)
2. `read_file` `{team_py}` 看 build_team() docstring + 顶部模块 docstring
3. 抽样 `read_file` 1-3 个 workers/*.py 看顶部 docstring 中"职责"段
4. `grep` `'{target_team_id}'` 在 src/ 全局, 看哪些代码 dispatch 它 (调用方语境揭示用途)

### 提交要求

调 **submit_design_purpose** 工具提交:

- `essence` (str, ≥30 字符): "这个 team 用来 ... 解决 ..."
- `replaces` (str, ≥10 字符): 没有它时用什么手段做这事
- `non_goals` (list[str], ≥1 条): 它不做什么 · 每条句子
- `stakeholder_use` (str, ≥20 字符): 谁消费产出怎么用
- `evidence_sources` (list[obj], ≥1 条): 每条 {{file, section?}} 引用真实存在的文件

请开始探索. 探索完成后调 submit_design_purpose 提交."""

        return [{"role": "user", "content": task}]


class SubmitDesignPurposeRouter(SingleToolRouter):
    """提交 Q2 设计目的 brief · 结构化 schema · 终结 agent loop."""

    TOOL_NAME: ClassVar[str] = "submit_design_purpose"
    DESCRIPTION: ClassVar[str] = (
        "Submit the design purpose brief (Q2 answer). All fields must be natural language sentences. "
        "Calling this terminates the agent loop. Do NOT use category/type/level/tags fields."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "essence": {
                "type": "string",
                "minLength": 30,
                "description": "解决什么具体问题 · 完整句子",
            },
            "replaces": {
                "type": "string",
                "minLength": 10,
                "description": "没有它时用什么手段 · 句子",
            },
            "non_goals": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 10},
                "description": "它不做什么 · 反向定义",
            },
            "stakeholder_use": {
                "type": "string",
                "minLength": 20,
                "description": "谁消费产出怎么用 · 具体场景句",
            },
            "evidence_sources": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "section": {"type": "string"},
                    },
                    "required": ["file"],
                },
                "description": "推断依据来源 · 必须引用真实文件",
            },
        },
        "required": [
            "essence",
            "replaces",
            "non_goals",
            "stakeholder_use",
            "evidence_sources",
        ],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        non_goals = args.get("non_goals", [])
        evidence = args.get("evidence_sources", [])
        return f"submitted: design_purpose_brief with {len(non_goals)} non_goals, {len(evidence)} evidence"


class _PurposeExtractResult(ExtractResultRouter):
    """从 messages 中提取 submit_design_purpose 的 tool_use input."""

    def extract(
        self,
        *,
        final_text: str,
        messages: list[dict],
        turn_count: int,
        stop_reason: str,
    ) -> Verdict:
        result_json: dict | None = None
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "submit_design_purpose"
                    ):
                        inp = block.get("input", {})
                        if isinstance(inp, dict):
                            result_json = dict(inp)
                            break
            if result_json:
                break

        if not isinstance(result_json, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"final_text": final_text[:500], "turn_count": turn_count},
                diagnosis=(
                    f"PurposeInterpreter 未调用 submit_design_purpose "
                    f"(turns={turn_count}, stop={stop_reason})"
                ),
            )

        # 反模式自检
        for forbidden in ("category", "type_tag", "level", "tier", "tags", "score", "rating"):
            if forbidden in result_json:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=result_json,
                    diagnosis=(
                        f"反模式: 输出含禁字段 '{forbidden}' (按 feedback_semantic_sentences_not_classification)"
                    ),
                )

        non_goals = result_json.get("non_goals")
        evidence = result_json.get("evidence_sources")
        if not isinstance(non_goals, list) or len(non_goals) < 1:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis="non_goals < 1 · 必须至少 1 条反向定义",
            )
        if not isinstance(evidence, list) or len(evidence) < 1:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis="evidence_sources < 1 · 必须至少 1 条引用",
            )

        if stop_reason == "max_turns":
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=result_json,
                diagnosis=f"预算耗尽: {turn_count} turns; 已产 brief 但探索未充分",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output=result_json,
            diagnosis=f"Q2 brief 提交: {len(non_goals)} non_goals, {len(evidence)} evidence",
            confidence=0.9,
        )


class PurposeInterpreterWorker(AgentNodeLoop):
    """Q2 设计目的答案 · AGENT."""

    DESCRIPTION: ClassVar[str] = (
        "Q2 设计目的答案 · AGENT. 探索 DESIGN.md + team.py docstring + worker docstring + "
        "调用方代码, 产 design_purpose_brief 全自然语言句子."
    )
    FORMAT_IN: ClassVar[str] = "team_supervisor.target_metadata"
    FORMAT_OUT: ClassVar[str] = "team_supervisor.design_purpose_brief"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter,
        GlobRouter,
        GrepRouter,
        ListDirRouter,
        SubmitDesignPurposeRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus

        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _PurposePromptBuilder:
        return _PurposePromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _PurposeExtractResult:
        return _PurposeExtractResult(bus=bus)

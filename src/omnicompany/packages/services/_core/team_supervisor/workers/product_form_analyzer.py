# [OMNI] origin=claude-code domain=services/team_supervisor/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.product_form_analyzer.explorer.py"
"""ProductFormAnalyzerWorker — team_supervisor Worker #2 (AGENT).

Worker 协议:
  FORMAT_IN  = team_supervisor.target_metadata
  FORMAT_OUT = team_supervisor.product_form_brief

职责: Q1 产物形式答案. 通过 ReadFile/Glob/Grep 探索 target FORMAT_OUT schema +
      末节点 worker 代码 + 历史 trace 产物, 用自然语言句子产 product_form_brief.

铁律 (按 feedback_semantic_sentences_not_classification):
- 全字段自然语言句子, 禁分类标签 / 打分 / kind / tier / tags
- schema_fields_observed 是物理事实 (从 FORMAT_OUT JSON Schema 抽), 允许
- 末步必调 submit_product_form 工具
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


_SYSTEM_PROMPT = """你是 team_supervisor 的 ProductFormAnalyzer · 一个深度探索的 Agent.

## 你的任务

回答**第一个基本问题**: 这个 target team 产物长什么样?

输入是 target_metadata, 含 target team 的代码目录路径. 你需要用工具 (read_file / glob / grep / list_dir) 主动探索:

1. **FORMAT_OUT schema**: 末节点的输出 Material 定义在哪 (通常 `<team_code_dir>/formats.py`), 字段有哪些
2. **末节点 worker 代码**: 末节点 worker 的 run() 实际产出什么 (是否与 schema 一致)
3. **历史 trace 产物** (可选): 若 historical_traces_dir 存在, 看 sink material 落盘的真实例子

## 反模式禁令 (绝对不要)

❌ 不要给产物"打分"或"分类"
❌ 不要写 `complexity_score: 7` `quality: high` `tier: T1` `tags: [stable]`
❌ 不要用枚举类标签 ("data_pipeline", "report_generator")

## 正确姿势

✅ 用**完整自然语言句子**承载语义
✅ `essence`: "这个 team 产物是 ... 用于 ..." (一句话本质)
✅ `minimal_passing_evidence`: 含具体阈值或可程序化特征的句子
✅ `failure_signals`: 每条都是具体特征句 ("proposals 列表为 0" 而非 "quality is low")
✅ `concrete_examples`: 引用真实路径 + excerpt + note

## 关键约束

1. **schema_fields_observed 必须从真实 schema 抽**, 不要凭空想象字段
2. **failure_signals 至少 2 条**, 越具体越好 (能直接转成 oracle)
3. **concrete_examples 优先来自真实 trace**, 找不到则从 worker 代码中抽 docstring/示例
4. **末步必调 submit_product_form 工具**提交结构化结果

## 工具

- **read_file**: 读 formats.py / workers/*.py / DESIGN.md / trace JSON · 全量读不截断
- **glob**: 探目录结构 (`**/*.py` `**/*.json`)
- **grep**: 搜符号 (`FORMAT_OUT` `class.*Worker` `kind.sink`)
- **list_dir**: 列目录内容
- **submit_product_form**: 终结性提交 (调它 = agent 终止)

记住: 你的输出会被下一节点消费来设计假设 — 越具体越能产出可验证的假设."""


class _ProductFormPromptBuilder(PromptBuilderRouter):
    """把 target_metadata 注入 agent 首轮会话."""

    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        target_team_id = biz_input.get("target_team_id", "?")
        team_code_dir = biz_input.get("team_code_dir", "")
        format_out_id = biz_input.get("format_out_id", "?")
        worker_files = biz_input.get("worker_files", [])
        historical_traces = biz_input.get("historical_traces_dir", "")
        team_design_md = biz_input.get("team_design_md_path", "")

        worker_list = "\n".join(f"  - {w}" for w in worker_files[:30]) or "  (无)"
        traces_hint = (
            f"- historical_traces_dir: `{historical_traces}` (可探索真实 trace 落盘)"
            if historical_traces
            else "- historical_traces_dir: 不存在 (此 team 还没跑过, 仅靠 schema + 代码推断)"
        )
        design_hint = (
            f"- team_design_md_path: `{team_design_md}`"
            if team_design_md
            else "- team_design_md_path: 不存在 (跳过 DESIGN.md 探索)"
        )

        task = f"""## 任务: 回答 Q1 — target team `{target_team_id}` 产物长什么样?

### Target 元数据

- target_team_id: `{target_team_id}`
- team_code_dir: `{team_code_dir}`
- 末节点 FORMAT_OUT material id: `{format_out_id}`
{design_hint}
- workers/ 目录下 .py 文件:
{worker_list}
{traces_hint}

### 探索建议 (你自由决定顺序)

1. `read_file` `{team_code_dir}/formats.py` 找到 `{format_out_id}` 的定义, 抽 json_schema 字段列表
2. `glob` `{team_code_dir}/workers/*.py` 找出疑似末节点 worker (assembler / sink / report)
3. `read_file` 末节点 worker 代码看 run() 真实产物形态
4. (可选) `list_dir` `{historical_traces}` 看历史落盘, `read_file` 一两个 sink 落盘的 JSON 看真实例子

### 提交要求

调 **submit_product_form** 工具提交以下结构化结果:

- `essence` (str, ≥20 字符): 一句话说本质 · 完整自然语言句子
- `minimal_passing_evidence` (str, ≥20 字符): 最低合格产物长什么样 · 含具体阈值/可程序化特征
- `failure_signals` (list[str], ≥2 条): 每条具体特征句子
- `concrete_examples` (list[obj]): 引用锚点 · 每条 {{path, excerpt?, note}}
- `schema_fields_observed` (list[str]): FORMAT_OUT schema 真字段列表 (必须从真 schema 抽)

请开始探索. 探索完成后调 submit_product_form 提交."""

        return [{"role": "user", "content": task}]


class SubmitProductFormRouter(SingleToolRouter):
    """提交 Q1 产物形式 brief · 结构化 schema · 终结 agent loop."""

    TOOL_NAME: ClassVar[str] = "submit_product_form"
    DESCRIPTION: ClassVar[str] = (
        "Submit the product form brief (Q1 answer). All fields must be natural language sentences. "
        "Calling this terminates the agent loop. Do NOT use score/level/tier/tags fields."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "essence": {
                "type": "string",
                "minLength": 20,
                "description": "一句话说本质 · 完整自然语言句子",
            },
            "minimal_passing_evidence": {
                "type": "string",
                "minLength": 20,
                "description": "最低合格产物长什么样 · 含具体阈值或可程序化特征",
            },
            "failure_signals": {
                "type": "array",
                "minItems": 2,
                "items": {"type": "string", "minLength": 10},
                "description": "失败信号列表 · 每条具体特征句",
            },
            "concrete_examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "excerpt": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["path", "note"],
                },
                "description": "历史 trace 中的具体例子 · 可选",
            },
            "schema_fields_observed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "FORMAT_OUT schema 真字段列表 · 物理事实",
            },
        },
        "required": ["essence", "minimal_passing_evidence", "failure_signals"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        signals = args.get("failure_signals", [])
        return f"submitted: product_form_brief with {len(signals)} failure signals"


class _ProductFormExtractResult(ExtractResultRouter):
    """从 messages 中提取 submit_product_form 的 tool_use input · 不解 JSON."""

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
                        and block.get("name") == "submit_product_form"
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
                    f"ProductFormAnalyzer 未调用 submit_product_form "
                    f"(turns={turn_count}, stop={stop_reason})"
                ),
            )

        # 必需字段校验 (兜底, INPUT_SCHEMA 已 API 层强制)
        essence = result_json.get("essence")
        evidence = result_json.get("minimal_passing_evidence")
        signals = result_json.get("failure_signals")
        if not essence or not evidence or not isinstance(signals, list) or len(signals) < 2:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis="submit_product_form 必填字段缺失或 failure_signals < 2",
            )

        # 反模式自检 — 抓 LLM 偷塞分类标签
        for forbidden in ("score", "rating", "level", "tier", "tags", "kind", "complexity"):
            if forbidden in result_json:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=result_json,
                    diagnosis=(
                        f"反模式: 输出含禁字段 '{forbidden}' (按 feedback_semantic_sentences_not_classification "
                        "禁分类/打分/标签 · 必用自然语言句子)"
                    ),
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
            diagnosis=f"Q1 brief 提交: essence + {len(signals)} 失败信号",
            confidence=0.9,
        )


class ProductFormAnalyzerWorker(AgentNodeLoop):
    """Q1 产物形式答案 · AGENT.

    通过 AgentNodeLoop 多轮探索 (ReadFile/Glob/Grep/ListDir),
    LLM 综合 FORMAT_OUT schema + 末节点 worker 代码 + 历史 trace 后,
    用自然语言句子产 product_form_brief.
    """

    DESCRIPTION: ClassVar[str] = (
        "Q1 产物形式答案 · AGENT. 探索 target FORMAT_OUT schema + 末节点 worker + 历史 trace, "
        "产 product_form_brief 全自然语言句子."
    )
    FORMAT_IN: ClassVar[str] = "team_supervisor.target_metadata"
    FORMAT_OUT: ClassVar[str] = "team_supervisor.product_form_brief"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter,
        GlobRouter,
        GrepRouter,
        ListDirRouter,
        SubmitProductFormRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus

        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _ProductFormPromptBuilder:
        return _ProductFormPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _ProductFormExtractResult:
        return _ProductFormExtractResult(bus=bus)

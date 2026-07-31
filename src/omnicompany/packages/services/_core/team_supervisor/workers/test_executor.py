# [OMNI] origin=claude-code domain=services/team_supervisor/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.test_executor.runner.py"
"""TestExecutorWorker — team_supervisor Worker #6 (AGENT).

Worker 协议:
  FORMAT_IN  = [hypothesis_set, target_metadata]
  FORMAT_OUT = team_supervisor.test_results
  FORMAT_IN_MODE = and

注 · target_spec 字段 (sample_input/run_count/target_team_id) 由 TargetIngressWorker
透传到 target_metadata 输出, 这里不再单独 fan-in target_spec.

职责: 调 dispatch_team 工具真跑 target team, 然后逐条假设跑 oracle (用 ReadFile/Grep
      验证引用 + agent reasoning) 评估 passed/failed, 收集 evidence.
"""
from __future__ import annotations

import json
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

from ..routers.dispatch_team import DispatchTeamRouter


_SYSTEM_PROMPT = """你是 team_supervisor 的 TestExecutor · 一个执行 + 评估 Agent.

## 你的任务

输入: 假设集 (≥10 条) + target_metadata + target_spec.
输出: test_results (target 跑出来的 verdict + 每条假设的 passed/observed/evidence).

## 工作流

1. **决定 sample_input**:
   - 优先用 target_spec.sample_input (若有)
   - 否则去 target_metadata.historical_traces_dir 找历史 input (用 list_dir / read_file)
   - 实在找不到, 抽 target_metadata.format_in_id 的 schema 推断一组合理 input (再 ReadFile target 的 formats.py 看)

2. **真跑 target**:
   - 调 **dispatch_team** 工具, 参数: target_team_id + input_data (+ max_steps=1000)
   - 拿到 verdict + output

3. **逐条假设评估**:
   - 对每条 hypothesis:
     - 读 oracle_code_hint
     - 用 ReadFile/Grep 必要时验证引用真实性 (例: hypothesis 说 "reference_code 行号应存在", 你就真去 ReadFile 那个文件看)
     - 决定 passed (bool) + observed (句子描述实际看到什么) + evidence (引用锚点列表)
   - 不要跳过任何假设. 跑不动的标 passed=false 且 observed='oracle 跑失败 ...'

4. **提交**:
   - 调 **submit_test_results** 工具一次性提交所有评估

## 反模式禁令

❌ 不要"模拟" target 跑 — 必须真调 dispatch_team
❌ 不要给假设打分 (`confidence: 0.7`) — 用 passed (布尔) + observed (句子)
❌ 不要批量 PASS 所有假设糊弄 — 每条都要有具体 observed 句子

## 关键约束

1. **dispatch_team 只调一次** (一次性跑全 target), 之后用本地 ReadFile 验证假设
2. **observed ≥ 10 字符** · 必须具体 ("verdict 是 PASS, 实际看到 6 条 proposals" 不能 "OK")
3. **evidence 引用真实路径** · 假设涉及 target 代码引用就 ReadFile 后摘 excerpt

## 工具

- **dispatch_team**: 真跑 target (参数 target_team_id, input_data) · 子进程隔离 · 返回 verdict + output JSON
- **read_file / glob / grep / list_dir**: 验证引用 / 找历史 trace / 抽 schema
- **submit_test_results**: 终结性提交"""


class _TestExecutorPromptBuilder(PromptBuilderRouter):
    """把 hypotheses + target_metadata + target_spec 注入 agent."""

    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        # fan-in 镜像
        meta_mirror = biz_input.get("_from_TargetIngressWorker") or {}
        spec = {}
        # target_spec 可能透传顶层 (target_team_id) 或 _from_<spec source 是外部, 没镜像>
        # 实际 target_spec 是 entry input 不经过 worker, 它的字段在 ingress worker 处被消费
        # 这里我们从 metadata 拿 sample_input + run_count

        target_team_id = meta_mirror.get("target_team_id") or biz_input.get("target_team_id", "")
        team_code_dir = meta_mirror.get("team_code_dir") or biz_input.get("team_code_dir", "")
        format_in_id = meta_mirror.get("format_in_id") or biz_input.get("format_in_id", "")
        traces_dir = meta_mirror.get("historical_traces_dir") or biz_input.get("historical_traces_dir", "")
        sample_input = meta_mirror.get("sample_input") or biz_input.get("sample_input")
        run_count = meta_mirror.get("run_count") or biz_input.get("run_count", 1)

        hyp_mirror = biz_input.get("_from_HypothesisGeneratorWorker") or {}
        hypotheses = hyp_mirror.get("hypotheses") or biz_input.get("hypotheses", [])

        sample_hint = (
            f"target_spec.sample_input 已提供 (用它):\n```json\n{json.dumps(sample_input, ensure_ascii=False, indent=2)[:800]}\n```"
            if sample_input
            else f"target_spec.sample_input 未提供 — 你需要去 historical_traces 找或从 schema 推断 ({format_in_id})"
        )

        task = f"""## 任务: 真跑 target + 评估每条假设

### Target

- target_team_id: `{target_team_id}`
- team_code_dir: `{team_code_dir}`
- format_in_id: `{format_in_id}`
- historical_traces_dir: `{traces_dir or '(不存在)'}`
- run_count: {run_count}

### Sample Input

{sample_hint}

### 假设集 ({len(hypotheses)} 条)

```json
{json.dumps(hypotheses, ensure_ascii=False, indent=2)[:6000]}
```

### 工作步骤

1. (若 sample_input 缺) ReadFile `{team_code_dir}/formats.py` 找 `{format_in_id}` schema · 推 input
2. 调 **dispatch_team** 真跑 target — 务必只调一次取 verdict + output
3. 对每条假设, 用 ReadFile/Grep 必要时验证, 然后判 passed + observed + evidence
4. 调 **submit_test_results** 提交

### 提交字段

- `target_run_verdict`: dispatch 出的 verdict ('PASS'/'FAIL'/'PARTIAL')
- `target_output_summary` (≥20 字符): target 产物要点摘要句
- `target_traces_path` (可选): 本次 trace 落盘路径 (从 dispatch_team 输出抽)
- `hypothesis_evaluations`: 每条 {{hypothesis_id, passed, observed, evidence?}}

请开始工作."""

        return [{"role": "user", "content": task}]


class SubmitTestResultsRouter(SingleToolRouter):
    """提交测试结果 · 结构化 schema."""

    TOOL_NAME: ClassVar[str] = "submit_test_results"
    DESCRIPTION: ClassVar[str] = (
        "Submit the test results: target's dispatch verdict + per-hypothesis evaluation. "
        "Each evaluation has: passed (bool), observed (sentence, ≥10 chars), evidence (refs)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "target_run_verdict": {
                "type": "string",
                "enum": ["PASS", "FAIL", "PARTIAL"],
            },
            "target_output_summary": {
                "type": "string",
                "minLength": 20,
            },
            "target_traces_path": {"type": "string"},
            "hypothesis_evaluations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "hypothesis_id": {
                            "type": "string",
                            "pattern": "^H-\\d{3}$",
                        },
                        "passed": {"type": "boolean"},
                        "observed": {"type": "string", "minLength": 10},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "excerpt": {"type": "string"},
                                },
                                "required": ["path"],
                            },
                        },
                    },
                    "required": ["hypothesis_id", "passed", "observed"],
                },
            },
        },
        "required": [
            "target_run_verdict",
            "target_output_summary",
            "hypothesis_evaluations",
        ],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        evals = args.get("hypothesis_evaluations", [])
        passed = sum(1 for e in evals if e.get("passed"))
        return f"submitted: {passed}/{len(evals)} hypotheses passed"


class _TestExecutorExtractResult(ExtractResultRouter):
    """从 messages 中提取 submit_test_results."""

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
                        and block.get("name") == "submit_test_results"
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
                    f"TestExecutor 未调用 submit_test_results "
                    f"(turns={turn_count}, stop={stop_reason})"
                ),
            )

        # 反模式自检 (评估字段禁打分)
        evals = result_json.get("hypothesis_evaluations", [])
        if not isinstance(evals, list) or len(evals) < 1:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis="hypothesis_evaluations 为空",
            )

        for i, e in enumerate(evals):
            if not isinstance(e, dict):
                continue
            for forbidden in ("confidence", "score", "rating", "severity"):
                if forbidden in e:
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output=result_json,
                        diagnosis=(
                            f"评估 #{i} ({e.get('hypothesis_id')}) 含禁字段 '{forbidden}'"
                        ),
                    )

        passed_count = sum(1 for e in evals if e.get("passed"))
        total = len(evals)

        if stop_reason == "max_turns":
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=result_json,
                diagnosis=f"预算耗尽: {turn_count} turns; 已评 {passed_count}/{total}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output=result_json,
            diagnosis=f"测试结果提交: {passed_count}/{total} 假设通过",
            confidence=0.9,
        )


class TestExecutorWorker(AgentNodeLoop):
    """测试执行 · AGENT."""

    DESCRIPTION: ClassVar[str] = (
        "测试执行 · AGENT. 真 dispatch target team + 跑 oracle 评估每条假设."
    )
    FORMAT_IN: ClassVar[list[str]] = [
        "team_supervisor.hypothesis_set",
        "team_supervisor.target_metadata",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_supervisor.test_results"
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        DispatchTeamRouter,
        ReadFileRouter,
        GlobRouter,
        GrepRouter,
        ListDirRouter,
        SubmitTestResultsRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self) -> None:
        from omnicompany.bus.memory import MemoryBus

        super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _TestExecutorPromptBuilder:
        return _TestExecutorPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _TestExecutorExtractResult:
        return _TestExecutorExtractResult(bus=bus)

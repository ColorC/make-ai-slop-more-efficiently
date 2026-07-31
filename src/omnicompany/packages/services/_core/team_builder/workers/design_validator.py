# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.design_validator.seven_dimension.py"
"""DesignValidatorWorker — Phase 7 · AgentNodeLoop (2026-04-23).

Worker 协议 (composite fan-in and):
  FORMAT_IN  = [team_design, workspace_spec, worker_design_detailed, material_design_detailed, contract_audit]
  FORMAT_OUT = team_builder.material.design_validation_report

**职责**: AgentNodeLoop · 综合判 7 维草图级健康:
  1. 格式 (DESIGN 七节 / Material 五要素)
  2. 命名 B 层 (禁 Format/Router/Pipeline class 名)
  3. workspace 合规 (唯一 canonical target package + 同源 data path)
  4. ServiceBus 对接 (HARD grep 式: 新 Worker 代码禁 subprocess/open('w')/requests)
  5. 契约闭环 (引用 contract_audit 结果)
  6. F-15 诚实 (context_sources 覆盖)
  7. Worker 18 项清单 (HARD 填没填 + SOFT 语义合理性)

工具: ReadFile / Grep / ListDir / Finish
可读: docs/standards/ + 当前已深化的 material/worker detail

**为什么 AgentNodeLoop**:
- 第 7 维 hallucination_risks 的语义合理性需 LLM 判
- 第 4 维 ServiceBus 对接需 grep 实际 Worker 代码 (若已生成)
- 其他维度 HARD 判但要 LLM 归纳 + 综合
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


_SYSTEM_PROMPT = """你是 team_builder 第 7 阶段 · DesignValidator agent.

## 职责
综合判断 team_builder 草图产出的**7 维健康度** · 产出 design_validation_report.
PASS → 进 Phase 8 代码生成. PARTIAL → NEXT 带 warn. FAIL → JUMP 回对应阶段 RETRY.

## 7 维校验

### 1. 格式
- team_design.sections 必须 7 条 (OMNI-034 七节)
- 每 material 的 description_5elems 5 要素齐全

### 2. 命名 B 层
- 禁用 Format/Router/Pipeline class 名 (用 Material/Worker/Team)
- Material id 合规: 小写 + 点号分隔

### 3. workspace 合规
- team_design.target_package_path 与 workspace_spec.target_package_path 完全一致
- business-private Team 使用 `src/omnicompany/packages/domains/<domain>/<pkg>/`
- shared service Team 使用 `src/omnicompany/packages/services/<canonical_bucket>/<pkg>/`
- 禁止平铺 `packages/services/<pkg>/`
- write_prefixes 第一项是 target_package_path，第二项是同 namespace 的 data path

### 4. ServiceBus 对接 (静态设计层审计)
- worker_design_detailed 若 impl_type=HARD 应**避免**声明直用 subprocess/open('w')/requests
- 代替应用 BashBus/DiskBus/WebBus (声明或 prompt_template 里体现)

### 5. 契约闭环
- contract_audit.overall_ok 必须 True
- 无 orphan_workers, 无 dangling_materials (非 source/sink)

### 6. F-15 诚实
- 每 SOFT/AGENT Worker context_sources 非空
- prompt_template 对 format_in 的消费显式 (不用 **input_data 透传)

### 7. Worker 18 项清单
- 每 Worker 有 hallucination_risks (≥ 1 条)
- 每 Worker 有 output_token_budget
- routes PASS + FAIL + PARTIAL 覆盖

## EXISTING Worker 复用规则

- `impl_type=EXISTING` 表示 WorkerDesigner 已从 `binding_ref` 导入真实 Worker 类并读取
  FORMAT_IN/OUT；它没有本 Team 新生成的本地 Worker 文件。
- 全部 Worker 都是 EXISTING 时，`material_design_detailed` 可以为空；格式契约由既有
  Worker 拥有。此时直接服从 HARD `contract_audit.overall_ok`，不得要求复制 Material。
- Worker 18 项代码生成检查不适用于 EXISTING；只检查 binding_ref 已解析、没有把
  model/prompt/tools/permissions/budget 写入 binding_kwargs。
- SOFT/AGENT 有 prompt_template, HARD 有 rule_spec

## 工具
- read_file / grep / list_dir: 读 docs/standards/ 实际校验规则 · 读 similar 代码
- submit_design_report: 提交结构化 7 维报告 (强 schema · 各字段直接结构化 · 不要嵌 JSON 字符串)

## 产出 JSON
```json
{
  "format_check": {"passed": bool, "issues": [...]},
  "naming_check": {"passed": bool, "issues": [...]},
  "workspace_check": {"passed": bool, "issues": [...]},
  "servicebus_adoption_check": {"passed": bool, "issues": [...]},
  "contract_closure_check": {"passed": bool, "issues": [...]},
  "f15_honesty_check": {"passed": bool, "issues": [...]},
  "worker_18item_check": {"passed": bool, "issues": [...]},
  "overall": "PASS|PARTIAL|FAIL",
  "must_fix": ["FAIL 级问题, 阻 Phase 8"],
  "should_fix": ["PARTIAL 级问题, 警告但不阻"]
}
```

**判 overall 规则**:
- 任一维 must_fix → overall=FAIL
- 无 must_fix 但有 should_fix → overall=PARTIAL
- 全过 → overall=PASS"""


class _DesignValidatorPromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        # 收集 composite fan-in 输入
        team_design = biz_input.get("_from_team_architect") or biz_input.get("team_design") or {}
        workspace_spec = biz_input.get("_from_workspace_designer") or biz_input.get("workspace_spec") or {}
        contract_audit = biz_input.get("_from_contract_auditor") or biz_input.get("contract_audit") or {}

        # worker/material details · V2 Orchestrator 输出结构: `_from_<producer>.details` (list)
        # 必须 peel details list, 而不是 append 整个 dict (那样 LLM 看到只 1 份 · 其实含 N 份嵌套)
        def _peel_details(prefix: str) -> list:
            for key, val in biz_input.items():
                if isinstance(key, str) and key.startswith(f"_from_{prefix}") and isinstance(val, dict):
                    ds = val.get("details")
                    if isinstance(ds, list):
                        return [d for d in ds if isinstance(d, dict)]
            # fallback: 老的单份结构
            return []

        workers_detailed = _peel_details("worker_designer")
        materials_detailed = _peel_details("material_designer")

        # 铁律 A: 禁预防性截断 · 全量喂 LLM (qwen-3.6-plus 1M context 容得下)
        task = f"""## team_design 草图

```json
{json.dumps(team_design, ensure_ascii=False, indent=2)}
```

## workspace_spec

```json
{json.dumps(workspace_spec, ensure_ascii=False, indent=2)}
```

## worker_design_detailed ({len(workers_detailed)} 份)

```json
{json.dumps(workers_detailed, ensure_ascii=False, indent=2)}
```

## material_design_detailed ({len(materials_detailed)} 份)

```json
{json.dumps(materials_detailed, ensure_ascii=False, indent=2)}
```

## contract_audit (HARD ContractAuditor 静态产 · `overall_ok` 字段在末尾, 直接读用)

```json
{json.dumps(contract_audit, ensure_ascii=False, indent=2)}
```

**关键提示**: contract_audit.overall_ok 已由 HARD ContractAuditorWorker 静态计算并填好.
如其值 = true, 你的 contract_closure_check 就应当 passed=true (直接信赖 HARD 结果, 不要重新判断或质疑).

---

请按 system prompt 的 7 维校验, 用 **submit_design_report** 工具提交 (结构化 schema · 各字段直接填 · 不要 JSON 字符串嵌套).
"""
        return [{"role": "user", "content": task}]


# 2026-04-25 重构 (L1 铁律): FinishRouter + _parse_json_loose 是反模式 (手解 JSON 违 feedback_no_manual_parse_use_structured_output).
# 改用自定义 SubmitDesignReportRouter · INPUT_SCHEMA 强 schema · API 校验 · 结果直接结构化读.


class SubmitDesignReportRouter(SingleToolRouter):
    """提交 7 维 design_validation_report · 结构化 schema · 替代旧 FinishRouter+json.loads."""

    TOOL_NAME: ClassVar[str] = "submit_design_report"
    DESCRIPTION: ClassVar[str] = (
        "Submit the 7-dimension design validation report. Calling this terminates the agent loop. "
        "All fields are validated by API schema; do not embed JSON in plain text."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "format_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["passed"]},
            "naming_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["passed"]},
            "workspace_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["passed"]},
            "servicebus_adoption_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["passed"]},
            "contract_closure_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["passed"]},
            "f15_honesty_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["passed"]},
            "worker_18item_check": {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["passed"]},
            "overall": {"type": "string", "enum": ["PASS", "PARTIAL", "FAIL"]},
            "must_fix": {"type": "array", "items": {"type": "string"}, "description": "FAIL 级问题 · 阻 Phase 8"},
            "should_fix": {"type": "array", "items": {"type": "string"}, "description": "PARTIAL 级问题 · 警告但不阻"},
        },
        "required": ["format_check", "naming_check", "workspace_check", "servicebus_adoption_check", "contract_closure_check", "f15_honesty_check", "worker_18item_check", "overall"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        return f"submitted: overall={args.get('overall')} · must_fix={len(args.get('must_fix', []))}"


class _DesignValidatorExtractResult(ExtractResultRouter):
    def extract(self, *, final_text: str, messages: list, turn_count: int, stop_reason: str) -> Verdict:
        # 找 submit_design_report tool_use · 直接读结构化 input · 不解 JSON
        result_json: dict | None = None
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "submit_design_report":
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
                diagnosis=f"DesignValidator 未调 submit_design_report (turns={turn_count}, stop={stop_reason})",
            )

        overall = result_json.get("overall")
        if overall not in ("PASS", "PARTIAL", "FAIL"):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis=f"overall 必须 PASS/PARTIAL/FAIL (got {overall!r})",
            )

        result_json.setdefault("_meta", {}).update({
            "worker": "DesignValidatorWorker",
            "stage": "v1_agent_loop",
            "turn_count": turn_count,
            "stop_reason": stop_reason,
        })

        # runner 路由: DesignValidator 的 PASS/PARTIAL/FAIL 对应 VerdictKind
        if overall == "PASS":
            return Verdict(kind=VerdictKind.PASS, output=result_json)
        if overall == "PARTIAL":
            must_fix = result_json.get("must_fix", [])
            should_fix = result_json.get("should_fix", [])
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output=result_json,
                diagnosis=f"must_fix={len(must_fix)} should_fix={len(should_fix)}",
            )
        # FAIL
        must_fix = result_json.get("must_fix", [])
        return Verdict(
            kind=VerdictKind.FAIL,
            output=result_json,
            diagnosis=f"design FAIL · must_fix: {'; '.join(must_fix[:3])}",
        )


class DesignValidatorWorker(AgentNodeLoop):
    """Phase 7 · AgentNodeLoop · 7 维综合草图级健康验证."""

    FORMAT_IN: ClassVar = [
        "team_builder.material.team_design",
        "team_builder.material.workspace_spec",
        "team_builder.material.worker_design_detailed",
        "team_builder.material.material_design_detailed",
        "team_builder.material.contract_audit",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.design_validation_report"
    DESCRIPTION: ClassVar[str] = (
        "Phase 7 · AgentNodeLoop · 综合 7 维草图级健康检查 (格式/命名/workspace/ServiceBus "
        "/契约/F-15/Worker 18 项) · PASS 进 Phase 8 代码生成. 必要时 grep 真代码验证 "
        "ServiceBus 对接."
    )
    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter, SubmitDesignReportRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _DesignValidatorPromptBuilder:
        return _DesignValidatorPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _DesignValidatorExtractResult:
        return _DesignValidatorExtractResult(bus=bus)

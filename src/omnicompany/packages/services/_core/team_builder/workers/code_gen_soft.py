# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-24T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.soft_agent_generators.orchestrator_design.py"
"""CodeGenerator 子 team · SOFT 部分 (2026-04-24 · 分形重构).

两个 Worker:
  Ws7 WorkerCodeOrchestrator  — asyncio.gather N 份并行 · per worker 产 workers/<name>.py
  Ws8 DesignMdGenerator       — 骨架预填 7 节 + LLM 填内容 · 产 DESIGN.md

feedback_100pct_required_goes_to_skeleton 的应用:
  - DESIGN.md 七节是固定结构, 骨架兜底章节标题, LLM 只填各节 body
  - per-worker 代码产出是创造性任务, 但骨架 strict FinishRouter + ServiceBus lint 兜底

复用已有设施:
  - team_code_generator._lint_service_bus_abuse
  - team_code_generator._normalize_design_md
  - team_code_generator._OMNI_MARK_RE
  - WorkerDesigner 的 _parse_json_loose
"""
from __future__ import annotations

import asyncio
from collections import Counter
import datetime as _dt
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.external_workers.base import (
    ExternalAgentEvent,
    ExternalAgentPermissionMode,
    ExternalAgentStatus,
    ExternalAgentWorkerRegistry,
)
from omnicompany.packages.services._core.agent.external_workers.runner import (
    ExternalAgentModelPolicy,
    ExternalAgentRunRequest,
    run_external_agent_request,
)
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
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._core.team_builder.package_location import (
    canonical_team_package_path,
    team_omni_domain,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent.agent_loop_tools import ToolContext

from .team_code_generator import (
    _OMNI_MARK_RE,
    _lint_service_bus_abuse,
    _normalize_design_md,
)
from .code_gen_hard import _class_name_for, _module_name_for


_TODAY = _dt.date.today().isoformat()


# ═══════════════════════════════════════════════════════════════════════
# 共享 strict FinishRouter · result = 单文件内容 (纯 str)
# ═══════════════════════════════════════════════════════════════════════


class _SingleFileFinishRouter(SingleToolRouter):
    """覆盖默认 finish, 强制 reason + result 双必填 (result = 单文件完整内容 str)."""

    TOOL_NAME: ClassVar[str] = "finish"
    DESCRIPTION: ClassVar[str] = (
        "Complete the task. BOTH 'reason' AND 'result' are required. "
        "'result' must contain the FULL file content as plain text (Python source for worker files, "
        "Markdown for DESIGN.md). Empty 'result' or placeholder ('Done'/'See above') will be rejected."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "One-sentence reason, e.g. 'worker impl produced' or 'DESIGN.md filled 7 sections'.",
                "minLength": 1,
            },
            "result": {
                "type": "string",
                "description": "FULL file content (not a summary, not 'See above'). ≥ 100 chars.",
                "minLength": 100,
            },
        },
        "required": ["reason", "result"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        return f"[finish] reason={args.get('reason', '')}"


# ═══════════════════════════════════════════════════════════════════════
# Ws7 · WorkerCodeOrchestrator (per-worker LLM sub-agent)
# ═══════════════════════════════════════════════════════════════════════


_WORKER_CODE_SYSTEM_PROMPT = """你是 team_builder 第 8 阶段 · WorkerCode 子 agent.

## 职责
给你一份 worker_design_detailed (单 Worker), 产出**该 Worker 的完整 .py 实现**.

## 工具
- read_file / grep / glob / list_dir: 必用 · 看 packages/services/doctor/workers/*.py 学继承 / FORMAT / run() 风格
- finish(reason, result): 提交完整 Python 源码 (result = 文件完整内容)

## 硬约束
- 继承 `omnicompany.packages.services._core.omnicompany.Worker`
- 类属性: DESCRIPTION (≥ 20 字) / FORMAT_IN / FORMAT_OUT / (list 时 FORMAT_IN_MODE)
- run(self, input_data) → Verdict (from omnicompany.protocol.anchor import Verdict, VerdictKind)
- 每文件首行必须有 OmniMark 头: `# [OMNI] origin=... domain=... ts=... type=worker`
- **不** 用 `pass` / `raise NotImplementedError` 糊弄 run()
- **不** import 不存在的模块

### ServiceBus 铁律 (骨架 lint 会抓)
- 写磁盘 → DiskBus.write(path, content) (DiskBus 没 read_file / open)
- subprocess → BashBus.run(cmd) (不要用 BashBus.run("cat/echo ...") 打印 · 用 print)
- HTTP → WebBus.fetch(url) (localhost/IPC 不用 WebBus)
- **只读文件** → Path(path).read_text() 不过 Bus
- **print/log** → print() / logging 直接用 不过 Bus

## impl_type 对应
- HARD: rule_spec 里的步骤 → Python if/else / for / 字段映射
- SOFT: prompt_template.system/user → 调 LLMClient (from omnicompany.runtime.llm.llm import LLMClient)
- AGENT: 需要多轮工具调用 → 继承 AgentNodeLoop (参考 packages/services/agent/loop.py)

## 交付 (必守)
调 `finish(reason, result)`:
- reason: 一句话状态 (如 "HARD worker impl produced")
- result: **完整 Python 源码文本** (不是概要, 不是 "See above"), ≥ 100 字符

如果 worker_design_detailed 的字段不齐 (缺 rule_spec / prompt_template), 按你的合理解释产一个**可运行的骨架**:
- 至少包含 FORMAT_IN/OUT 字段声明 + run() 签名 + 一个简单的 Verdict(PASS/FAIL) 分支
- 不允许空 pass

## 诚实
reason 真实说明 (例 "fields sparse, produced minimal skeleton"). 不虚构未存在的 material_id.
"""


class _WorkerCodePromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        worker_detail = biz_input.get("worker_detail") or {}
        team_name = biz_input.get("team_name") or "unnamed_team"
        package_domain = biz_input.get("package_domain") or "invalid-unbound-package"
        similar_teams = biz_input.get("similar_teams") or ["doctor", "guardian"]
        format_out_schema = biz_input.get("format_out_schema") or {}
        format_in_schemas = biz_input.get("format_in_schemas") or {}

        wid = worker_detail.get("worker_id", "unknown")
        from .code_gen_hard import _class_name_for, _module_name_for
        expected_class_name = _class_name_for(wid)
        expected_module_name = _module_name_for(wid)

        # 从 output schema 抽 required 字段 · 给 LLM 硬约束
        required_fields = (format_out_schema.get("required") or []) if isinstance(format_out_schema, dict) else []
        properties = (format_out_schema.get("properties") or {}) if isinstance(format_out_schema, dict) else {}
        required_desc = ""
        if required_fields:
            required_desc = "\n".join(
                f"  - **`{f}`**" + (f" ({properties[f].get('type', '?')})" if f in properties else "")
                + (f": {properties[f].get('description', '')[:120]}" if f in properties and properties[f].get('description') else "")
                for f in required_fields
            )

        # P6.3 · FORMAT_IN schemas: Worker 必读的 input 字段
        input_desc = ""
        if format_in_schemas:
            lines = []
            for mid, schema in format_in_schemas.items():
                req = (schema.get("required") or []) if isinstance(schema, dict) else []
                props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
                lines.append(f"Material `{mid}`:")
                for f in req:
                    desc = props.get(f, {}).get("description", "")[:100] if f in props else ""
                    typ = props.get(f, {}).get("type", "?") if f in props else "?"
                    lines.append(f"  - **`{f}`** ({typ})" + (f": {desc}" if desc else " · 必读"))
                if not req and props:
                    lines.append(f"  (无 required · 可选字段: {list(props.keys())})")
            input_desc = "\n".join(lines)

        task = f"""## 上下文

- team_name: {team_name}
- 参考 similar team: {similar_teams}

## 本次要实现的 Worker 深化设计

```json
{json.dumps(worker_detail, ensure_ascii=False, indent=2)[:4000]}
```

## 命名约束 (硬要求 · 下游 import 靠它对齐)

- **Python class name**: `{expected_class_name}` (必用这个, 不加前缀不改风格)
- **File saved as**: `workers/{expected_module_name}.py` (下游 workers/__init__.py 里 `from .{expected_module_name} import {expected_class_name}`)
- 若 class name 与文件名不匹配, `import` 失败 · 整 team 废

## Verdict.output 字段约束 (硬要求 · 下游 CodeReviewer 会校对)

FORMAT_OUT = `{worker_detail.get("format_out", "?")}`
Material.json_schema.required 字段 (必须在 `Verdict(output={{...}})` 里全部出现 · **逐字节一致**):

{required_desc if required_desc else "  (未指定 required, 按 worker_detail 设计自由产)"}

**铁律**: 字段名**严格匹配** (不改单复数 · 不换风格). 反例: schema 要 `header`, 代码写 `headers` → 整 team FAIL (2026-04-24 csv_to_md 实测).

## input_data 读取约束 (硬要求 · 下游 CodeReviewer 会校对)

FORMAT_IN = `{worker_detail.get("format_in", "?")}`
各 FORMAT_IN Material 的 required 字段 (Worker 必须从 input_data 读出这些字段):

{input_desc if input_desc else "  (未指定, 按 worker_detail 设计自由读)"}

**铁律**: 必须读 required 字段. 反例 (2026-04-24 csv_to_md #5 实测): Material required=`['rows']`, MarkdownWriter 代码没从 input 读 `rows` → CodeReviewer FAIL.

推荐 input 读法:
```python
def run(self, input_data):
    # composite fan-in: input_data[<material_id>] 是上游 payload (平铺)
    payload = input_data.get(self.FORMAT_IN) if isinstance(input_data, dict) and self.FORMAT_IN in input_data else input_data
    # 直接读 required 字段
    rows = payload.get("rows", [])
    # ... 业务
```

或 (source material · 顶层 input):
```python
def run(self, input_data):
    path = input_data.get("path")  # 直接读 schema 里的 required
```

## 操作要求

1. 先 grep / read_file 调研 1-2 个 similar worker (`packages/services/doctor/workers/*.py` 或
   `packages/services/guardian/workers/*.py`) 看 import / 继承 / FORMAT_IN 声明 / run() 实现风格
2. 按 impl_type 产出完整 .py 源码:
   - HARD: rule_spec 的步骤 → if/else / for 循环实现
   - SOFT: prompt_template 的 system/user → LLMClient.invoke() 调用
3. 首行 OmniMark 头 (当日日期, domain={package_domain}/workers/{expected_module_name}, type=worker)
4. **class 名必须是 `{expected_class_name}`** · 继承 Worker
5. **Verdict(output={{...}}) 里的字段名必须 ⊇ 上面列出的 required** (多余字段 OK, 缺一不可)
6. 调 finish(reason, result), result = 完整源码字符串 (≥ 100 字符)
"""
        # 补产模式
        retry_issue = biz_input.get("_retry_issue")
        if retry_issue:
            task += f"\n\n---\n\n## ⚠️ 补产模式\n\n上次问题: {retry_issue}\n请修复再 finish.\n"
        return [{"role": "user", "content": task}]


_CODE_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)\n```", re.DOTALL)


def _extract_python_body(text: str) -> str:
    """从 text 里提取 Python 源码 (尝试 fence 包裹, 否则返回原文)."""
    if not text:
        return ""
    m = _CODE_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


_WORKER_FILE_MARKER_RE = re.compile(
    r"BEGIN_WORKER_FILE\s*\n(?P<body>.*?)\nEND_WORKER_FILE",
    re.DOTALL | re.IGNORECASE,
)


def _json_preview(value: Any, *, max_chars: int = 6000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        text = json.dumps(str(value), ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _python_identifier_for_field(field: str) -> str:
    ident = re.sub(r"[^0-9a-zA-Z_]+", "_", field).strip("_").lower()
    if not ident:
        ident = "field_value"
    if ident[0].isdigit():
        ident = f"field_{ident}"
    return ident


def _format_required_input_contract(format_in_schemas: dict[str, dict]) -> tuple[str, str]:
    """Build a prompt fragment that the static field-access reviewer can audit."""

    checklist_lines: list[str] = []
    example_lines: list[str] = []
    for material_id, schema in format_in_schemas.items():
        required = (schema.get("required") or []) if isinstance(schema, dict) else []
        properties = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
        required = [field for field in required if isinstance(field, str) and field]
        if not required:
            continue

        checklist_lines.append(f"- {material_id}: {', '.join(f'`{field}`' for field in required)}")
        payload_var = f"payload_{_material_id_slug(material_id, max_chars=48)}"
        example_lines.append(
            f"{payload_var} = input_data.get({material_id!r}) "
            f"if isinstance(input_data, dict) and {material_id!r} in input_data else input_data"
        )
        example_lines.append(f"if not isinstance({payload_var}, dict):")
        example_lines.append(f"    {payload_var} = {{}}")
        for field in required:
            prop = properties.get(field, {}) if isinstance(properties, dict) else {}
            typ = prop.get("type") if isinstance(prop, dict) else None
            default = "[]" if typ == "array" else "{}" if typ == "object" else "None"
            example_lines.append(
                f"{_python_identifier_for_field(field)} = {payload_var}.get({field!r}, {default})"
            )
        example_lines.append("")

    if not checklist_lines:
        return "- No required input fields declared.", "# No required input fields declared."
    return "\n".join(checklist_lines), "\n".join(example_lines).rstrip()


def _build_external_worker_code_prompt(
    *,
    team_name: str,
    package_domain: str,
    worker_detail: dict,
    format_out_schema: dict,
    format_in_schemas: dict[str, dict],
) -> str:
    wid = worker_detail.get("worker_id") or "unknown"
    expected_class_name = _class_name_for(wid)
    expected_module_name = _module_name_for(wid)
    required_fields = []
    if isinstance(format_out_schema, dict):
        required_fields = list(format_out_schema.get("required") or [])
    input_required_contract, input_required_examples = _format_required_input_contract(format_in_schemas)

    return f"""You are implementing exactly one Omnicompany TeamBuilder worker file.

Critical execution mode:
- This is a pure text code-generation task, not a repository modification task.
- The target path workers/{expected_module_name}.py is a virtual generated-team path, not an existing repo file to edit.
- The only valid worker class name is {expected_class_name}. Do not return any existing repo worker class.
- Do not fix, patch, refactor, or mention any existing repository file unless it is only a style reference.
- Do not return diffs, apply_patch blocks, file links, implementation plans, or notes about read-only sandbox limits.
- Read-only means you may inspect examples, but you still must return the complete generated Python source as text.
- Ignore unrelated TODO, stub, pass, NotImplementedError, or legacy files in the repository.

Return contract:
- Return exactly one fenced ```python code block, or a BEGIN_WORKER_FILE / END_WORKER_FILE block.
- If a structured output schema is active, return JSON with exactly this shape: {{"files": {{"workers/{expected_module_name}.py": "<complete raw python source>"}}}}.
- In schema JSON mode, the file value must be raw Python source, not a fenced block and not markdown.
- The block must contain the complete Python file for workers/{expected_module_name}.py.
- The source must literally contain `class {expected_class_name}(Worker)`.
- If you are unsure about full business logic, still return a minimal runnable worker with class {expected_class_name}, exact FORMAT_IN/FORMAT_OUT, required field reads, VerdictKind.PARTIAL or PASS, and useful diagnosis. Never return placeholders like `print('placeholder')`.
- Do not write files. The caller will save the returned source after lint and review.
- Do not return a summary, patch, markdown explanation, or multiple alternatives.

Repository inspection requirement:
- Before returning code, inspect at least one existing Omnicompany worker example in this repo.
- Prefer `src/omnicompany/packages/services/_diagnosis/doctor/` or another generated team with real Worker classes.
- Do not inspect `src/omnicompany/packages/services/_core/team_builder/workers/` for implementation targets; those are framework internals and often contain unrelated stubs.
- Use read-only tools only, such as read_file, grep, glob, list_dir, `rg`, or `Get-Content`.
- These read events are audited as material provenance, so keep them relevant.

Required Python shape:
- First line must be an OmniMark header: # [OMNI] origin=team_builder domain={package_domain}/workers/{wid} ts={_TODAY}T00:00:00Z type=worker
- Import Worker from omnicompany.packages.services._core.omnicompany.
- Import Verdict and VerdictKind from omnicompany.protocol.anchor.
- Define class {expected_class_name}(Worker).
- Define DESCRIPTION, FORMAT_IN, FORMAT_OUT, and FORMAT_IN_MODE when FORMAT_IN is a list.
- Implement run(self, input_data) -> Verdict.
- Use only VerdictKind.PASS, VerdictKind.FAIL, or VerdictKind.PARTIAL.
- Do not use pass or raise NotImplementedError inside run.
- For HARD workers, implement the rule_spec with normal Python.
- For SOFT workers, keep LLM calls narrow and explicit if a prompt_template is present.
- For AGENT workers, only use existing AgentNodeLoop patterns if the design explicitly requires tools.

Data contract:
- FORMAT_OUT is {worker_detail.get("format_out")!r}.
- Verdict.output must include every required output field: {required_fields!r}.
- FORMAT_IN is {worker_detail.get("format_in")!r}.
- Read required input fields from input_data or from input_data[FORMAT_IN] when the material is nested.
- Required input fields to access in source:
{input_required_contract}
- Static review rule: every required input field above must appear in code as `payload.get("<field>", ...)`,
  `payload.get('<field>', ...)`, or `payload["<field>"]`. Do this even when rule_spec only mentions
  a subset of the fields. Use the values in validation, nodes, edges, diagnosis, or confidence notes;
  do not leave them as dead reads.
- Use Path(...).read_text() for simple read-only files. Use DiskBus only for writes, BashBus only for real shell commands, and WebBus only for network HTTP.

Recommended required-field access skeleton:
```python
{input_required_examples}
```

Worker design JSON:
```json
{_json_preview(worker_detail, max_chars=7000)}
```

FORMAT_OUT schema JSON:
```json
{_json_preview(format_out_schema, max_chars=5000)}
```

FORMAT_IN schemas JSON:
```json
{_json_preview(format_in_schemas, max_chars=7000)}
```

Quality bar:
- Prefer a small, correct, runnable worker over an over-engineered one.
- Preserve exact field names from schemas.
- If the design is sparse, still produce a runnable worker with explicit validation and useful diagnosis.
"""


def _external_prompt_quality_issues(
    prompt: str,
    *,
    worker_id: str,
    max_chars: int,
) -> list[str]:
    issues: list[str] = []
    if len(prompt) > max_chars:
        issues.append(f"prompt too large ({len(prompt)} chars > {max_chars})")
    if len(prompt.strip()) < 800:
        issues.append("prompt too small to preserve worker contract")
    for token in (worker_id, "Return contract", "FORMAT_OUT", "Verdict", "```python"):
        if token and token not in prompt:
            issues.append(f"prompt missing required token: {token}")

    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    if len(lines) >= 20:
        counts = Counter(lines)
        repeated_line, repeated_count = counts.most_common(1)[0]
        if repeated_count >= 10 and repeated_count / len(lines) >= 0.2:
            issues.append(f"prompt repeated line {repeated_count} times: {repeated_line[:80]}")
        uniqueness = len(counts) / len(lines)
        if uniqueness < 0.25:
            issues.append(f"prompt line uniqueness too low ({uniqueness:.2f})")
    return issues


def _external_worker_output_schema(*, rel_path: str, class_name: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "description": f"Structured result for exactly one generated worker file. The source must define class {class_name}(Worker).",
        "properties": {
            "files": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    rel_path: {
                        "type": "string",
                        "description": f"Complete raw Python source for {rel_path}. Must contain class {class_name}(Worker), not a patch, not markdown, not an existing repository worker.",
                    }
                },
                "required": [rel_path],
            }
        },
        "required": ["files"],
    }


def _looks_like_worker_source(source: str, *, class_name: str) -> bool:
    if len(source.strip()) < 120:
        return False
    required = (
        f"class {class_name}",
        "Worker",
        "Verdict",
        "def run(",
    )
    return all(token in source for token in required)


def _extract_external_worker_source(text: str, *, class_name: str) -> str:
    if not text:
        return ""

    marker = _WORKER_FILE_MARKER_RE.search(text)
    if marker:
        body = marker.group("body").strip()
        if _looks_like_worker_source(body, class_name=class_name):
            return body

    for match in _CODE_FENCE_RE.finditer(text):
        body = match.group(1).strip()
        if _looks_like_worker_source(body, class_name=class_name):
            return body

    body = _extract_python_body(text)
    if _looks_like_worker_source(body, class_name=class_name):
        return body
    return ""


def _external_worker_source_parse_diagnostics(text: str, *, class_name: str) -> dict[str, Any]:
    candidate_classes = sorted(set(re.findall(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text or "")))[:10]
    lower_text = (text or "").lower()
    returned_patch = "*** begin patch" in lower_text or "```diff" in lower_text
    mentions_readonly_block = "read-only" in lower_text or "read only" in lower_text or "readonly" in lower_text
    likely_issue = "no_python_worker_candidate"
    if returned_patch:
        likely_issue = "returned_patch_instead_of_worker_file"
    elif candidate_classes and class_name not in candidate_classes:
        likely_issue = "returned_wrong_worker_class"
    elif mentions_readonly_block:
        likely_issue = "treated_readonly_as_cannot_return_source"
    return {
        "expected_class_name": class_name,
        "candidate_class_names": candidate_classes,
        "returned_patch": returned_patch,
        "mentions_readonly_block": mentions_readonly_block,
        "likely_issue": likely_issue,
    }


def _external_agent_text_excerpt(text: str, *, limit: int = 1600) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) <= limit:
        return normalized
    if limit <= 40:
        return normalized[:limit]
    head_len = max(1, limit - 30)
    return normalized[:head_len].rstrip() + "\n... [truncated]"


def _ensure_worker_omni_header(
    source: str,
    *,
    package_domain: str,
    worker_id: str,
) -> str:
    if _OMNI_MARK_RE.search(source):
        return source
    header = (
        f"# [OMNI] origin=team_builder domain={package_domain}/workers/{worker_id} "
        f"ts={_TODAY}T00:00:00Z type=worker"
    )
    return header + "\n" + source.strip() + "\n"


def _python_compile_issue(*, rel_path: str, source: str) -> str | None:
    try:
        compile(source, rel_path, "exec")
    except SyntaxError as exc:
        location = f"{exc.lineno}:{exc.offset}" if exc.lineno else "unknown"
        return f"syntax error at {location}: {exc.msg}"
    except Exception as exc:  # noqa: BLE001
        return f"compile failed: {type(exc).__name__}: {exc}"
    return None


_VALID_VERDICT_KIND_NAMES = {kind.name for kind in VerdictKind}


def _verdict_kind_issue(source: str) -> str | None:
    used = set(re.findall(r"\bVerdictKind\.([A-Z_][A-Z0-9_]*)\b", source))
    invalid = sorted(used - _VALID_VERDICT_KIND_NAMES)
    if not invalid:
        return None
    valid = ", ".join(sorted(_VALID_VERDICT_KIND_NAMES))
    return f"invalid VerdictKind names: {', '.join(invalid)}; valid: {valid}"


_READ_TOOL_NAMES = {
    "read",
    "read_file",
    "grep",
    "glob",
    "list_dir",
    "ls",
    "shell",
    "shell_command",
    "bash",
    "powershell",
}
_READ_COMMAND_RE = re.compile(
    r"\b(Get-Content|Select-String|Get-ChildItem|rg|grep|findstr|cat|type|ls|dir)\b",
    re.IGNORECASE,
)


def _string_preview(value: Any, *, max_chars: int = 260) -> str:
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"


def _tool_call_key(name: str, input_obj: Any) -> str:
    try:
        input_text = json.dumps(input_obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        input_text = str(input_obj)
    return f"{name}\n{input_text[:1000]}"


def _extract_tool_targets(input_obj: Any) -> list[str]:
    targets: list[str] = []
    if isinstance(input_obj, str):
        if input_obj.strip():
            targets.append(_string_preview(input_obj))
        return targets
    if isinstance(input_obj, list):
        for item in input_obj:
            targets.extend(_extract_tool_targets(item))
        return targets
    if not isinstance(input_obj, dict):
        return targets

    for key in (
        "path",
        "file_path",
        "filepath",
        "glob",
        "pattern",
        "query",
        "command",
        "cmd",
        "cwd",
    ):
        value = input_obj.get(key)
        if isinstance(value, str) and value.strip():
            targets.append(f"{key}={_string_preview(value)}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    targets.append(f"{key}={_string_preview(item)}")
    argv = input_obj.get("argv")
    if isinstance(argv, list) and argv:
        targets.append("argv=" + _string_preview(" ".join(str(x) for x in argv)))
    return targets


def _is_read_like_tool(name: str, targets: list[str], input_obj: Any) -> bool:
    lowered = name.lower().replace("-", "_")
    if lowered in _READ_TOOL_NAMES:
        return True
    if any(token in lowered for token in ("read", "grep", "glob", "list", "search")):
        return True
    text = " ".join(targets)
    if not text and isinstance(input_obj, str):
        text = input_obj
    return bool(_READ_COMMAND_RE.search(text))


def _iter_tool_calls(payload: Any) -> list[tuple[str, Any, str]]:
    calls: list[tuple[str, Any, str]] = []
    seen: set[str] = set()

    def add_call(name: Any, input_obj: Any, call_id: Any = "") -> None:
        if not isinstance(name, str) or not name.strip():
            return
        call_id_text = str(call_id or "")
        key = call_id_text or _tool_call_key(name.strip(), input_obj)
        if key in seen:
            return
        seen.add(key)
        calls.append((name.strip(), input_obj, call_id_text))

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item)
            return
        if not isinstance(obj, dict):
            return

        block_type = str(obj.get("type") or obj.get("kind") or "").lower()
        name = obj.get("name") or obj.get("tool_name") or obj.get("tool")
        input_obj = obj.get("input")
        call_id = obj.get("id") or obj.get("tool_use_id") or obj.get("call_id")
        if input_obj is None:
            input_obj = obj.get("arguments")
        if input_obj is None and isinstance(obj.get("function"), dict):
            fn = obj["function"]
            name = name or fn.get("name")
            input_obj = fn.get("arguments")
        if input_obj is None:
            input_obj = {k: obj.get(k) for k in ("path", "file_path", "pattern", "command", "cmd", "argv") if k in obj}
        if (
            name
            and (
                block_type in {"tool_use", "tool_call", "function_call"}
                or input_obj
                or str(name).lower() in _READ_TOOL_NAMES
            )
        ):
            add_call(name, input_obj, call_id)
        elif any(k in obj for k in ("command", "cmd", "argv")):
            add_call("shell", {k: obj.get(k) for k in ("command", "cmd", "argv", "cwd") if k in obj}, call_id)

        for value in obj.values():
            walk(value)

    walk(payload)
    return calls


def _text_from_tool_result_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_from_tool_result_content(item) for item in value if item is not None)
    if isinstance(value, dict):
        for key in ("text", "content", "output", "stdout", "stderr", "result"):
            text = _text_from_tool_result_content(value.get(key))
            if text:
                return text
    return ""


def _iter_tool_results(payload: Any) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item)
            return
        if not isinstance(obj, dict):
            return
        block_type = str(obj.get("type") or obj.get("kind") or "").lower()
        if block_type in {"tool_result", "tool_response"} or "tool_use_id" in obj and "content" in obj:
            text = _text_from_tool_result_content(obj.get("content") or obj.get("result") or obj.get("output"))
            if text:
                results.append({
                    "tool_use_id": str(obj.get("tool_use_id") or obj.get("id") or obj.get("call_id") or ""),
                    "text": text,
                })
        for value in obj.values():
            walk(value)

    walk(payload)
    return results


def _paths_from_tool_result_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    patterns = (
        r"[A-Za-z]:\\[^\r\n:*?\"<>|]+?\.(?:py|md|json|yaml|yml|toml|txt)",
        r"src[\\/][^\s:'\"<>|]+?\.(?:py|md|json|yaml|yml|toml|txt)",
        r"docs[\\/][^\s:'\"<>|]+?\.(?:md|json|yaml|yml|toml|txt)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(0).strip().rstrip(".,);]")
            if value and value not in seen:
                seen.add(value)
                found.append(value)
            if len(found) >= 20:
                return found
    return found


def _result_path_evidence_kind(tool_name: str) -> str:
    lowered = tool_name.lower().replace("-", "_")
    if "read" in lowered:
        return "content_mention_path"
    if any(token in lowered for token in ("grep", "glob", "list", "search", "find")):
        return "search_hit_path"
    if any(token in lowered for token in ("bash", "shell", "exec", "cmd", "powershell")):
        return "command_output_path"
    return "tool_result_path"


def _summarize_external_agent_events(events: list[ExternalAgentEvent]) -> dict[str, Any]:
    tool_events: list[dict[str, Any]] = []
    tool_events_by_id: dict[str, dict[str, Any]] = {}
    read_targets: list[str] = []
    for event in events:
        for name, input_obj, call_id in _iter_tool_calls(event.payload):
            targets = _extract_tool_targets(input_obj)
            read_like = _is_read_like_tool(name, targets, input_obj)
            if read_like:
                read_targets.extend(targets or [name])
            tool_event = {
                "event_type": event.type,
                "tool": name,
                "tool_use_id": call_id,
                "targets": targets[:6],
                "read_like": read_like,
            }
            tool_events.append(tool_event)
            if call_id:
                tool_events_by_id[call_id] = tool_event
        for result in _iter_tool_results(event.payload):
            result_paths = _paths_from_tool_result_text(result["text"])
            if result_paths:
                read_targets.extend(f"file_path={path}" for path in result_paths)
            source_tool = ""
            if result["tool_use_id"] and result["tool_use_id"] in tool_events_by_id:
                source_tool = str(tool_events_by_id[result["tool_use_id"]].get("tool") or "")
            result_path_evidence_kind = _result_path_evidence_kind(source_tool)
            result_event = {
                "event_type": event.type,
                "tool": "tool_result",
                "tool_use_id": result["tool_use_id"],
                "targets": [],
                "read_like": bool(result_paths),
                "result_excerpt": _string_preview(result["text"], max_chars=500),
                "result_paths": result_paths[:12],
                "result_path_evidence_kind": result_path_evidence_kind,
            }
            if result["tool_use_id"] and result["tool_use_id"] in tool_events_by_id:
                tool_events_by_id[result["tool_use_id"]].update(
                    {
                        "result_excerpt": result_event["result_excerpt"],
                        "result_paths": result_event["result_paths"],
                        "result_path_evidence_kind": result_event["result_path_evidence_kind"],
                    }
                )
            else:
                tool_events.append(result_event)

    # 保持 material 元数据可读, 不把完整 provider stream 塞进去.
    deduped_reads = sorted({t for t in read_targets if t})[:40]
    return {
        "event_count": len(events),
        "tool_event_count": len(tool_events),
        "tool_events": tool_events[:40],
        "observed_read_targets": deduped_reads,
    }


def _resource_kind_for_read_target(target: str) -> str:
    lowered = target.lower()
    if any(token in lowered for token in (".db", "sqlite", "sql", "event.db", "events.db", "database")):
        return "database"
    if any(token in lowered for token in ("http://", "https://", "api", "fetch", "webbus")):
        return "external"
    return "workspace"


def _material_id_slug(value: str, *, max_chars: int = 120) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    while "__" in slug:
        slug = slug.replace("__", "_")
    if not slug:
        slug = "unknown"
    if len(slug) <= max_chars:
        return slug
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{slug[:max_chars - 13].rstrip('_')}_{digest}"


def _read_target_value(target: str) -> tuple[str, str]:
    if "=" not in target:
        return "target", target
    key, value = target.split("=", 1)
    return key.strip().lower() or "target", value.strip()


def _normalized_read_target_value(target: str) -> str:
    _, value = _read_target_value(target)
    normalized = value.replace("\\", "/").strip()
    marker = "omnicompany/"
    marker_idx = normalized.lower().find(marker)
    if marker_idx >= 0:
        normalized = normalized[marker_idx + len(marker):]
    return normalized


def _read_target_candidate_kind(target: str) -> str:
    key, _ = _read_target_value(target)
    if key in {"file_path", "filepath", "path"}:
        return "file"
    if key in {"glob", "pattern"}:
        return key
    if key in {"command", "cmd", "argv"}:
        return "command"
    if key == "query":
        return "query"
    return "resource"


def _repo_root_for_material_scan() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "src").is_dir():
            return parent
    return None


def _workspace_file_for_read_target(target: str) -> Path | None:
    key, value = _read_target_value(target)
    if key not in {"file_path", "filepath", "path", "target"}:
        return None
    raw = value.strip().strip("'\"")
    if not raw:
        return None
    path = Path(raw)
    if path.is_file():
        return path
    repo_root = _repo_root_for_material_scan()
    if not repo_root:
        return None
    candidates = [repo_root / raw.replace("\\", "/"), repo_root / _normalized_read_target_value(target)]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _material_ids_declared_in_read_target(target: str) -> list[str]:
    path = _workspace_file_for_read_target(target)
    if not path:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return []
    found = re.findall(r"material_id\s*=\s*['\"]([^'\"]+)['\"]", text)
    return sorted({item.strip() for item in found if item.strip()})


def _resource_candidate_reason(target: str, candidate_id: str, declared_material_ids: list[str], matched_material_ids: list[str]) -> str:
    kind = _read_target_candidate_kind(target)
    if declared_material_ids:
        return (
            "读取目标文件头声明了 material_id, 因此保留 workspace 候选节点, "
            "同时把声明的 material_id 作为更强的 material 读取命中。"
        )
    if matched_material_ids:
        return "读取目标文本直接包含当前 worker 声明 material 的 id 或名称片段, 可以作为中置信 material 读取命中。"
    if kind in {"file", "glob", "pattern"}:
        return f"运行记录指向 workspace {kind}, 先登记为待确认 material 线索: {candidate_id}。"
    if kind == "command":
        return f"运行记录里出现读取型命令, 命令整体先登记为待确认 material 线索: {candidate_id}。"
    return f"运行记录里出现外部资源目标, 先登记为待确认 material 线索: {candidate_id}。"


def _resource_promotion_hint(declared_material_ids: list[str], matched_material_ids: list[str]) -> str:
    if declared_material_ids:
        return "已经能从文件头 material_id 形成强命中; 下一步可以把候选节点连到正式 Material 定义。"
    if matched_material_ids:
        return "已经能从目标文本形成中置信命中; 下一步应结合工具参数和文件内容确认是否升级为正式读取边。"
    return "后续需要解析 grep/bash/Read 的具体文件内容或 OMNI header, 才能从 workspace 候选升级为正式 material 读取。"


def _material_candidate_id_for_read_target(target: str) -> str:
    key, value = _read_target_value(target)
    kind = _resource_kind_for_read_target(target)
    normalized = _normalized_read_target_value(target)
    if key in {"file_path", "filepath", "path"}:
        return f"{kind}.file.{_material_id_slug(normalized)}"
    if key in {"glob", "pattern"}:
        digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{kind}.{key}.{_material_id_slug(value, max_chars=60)}.{digest}"
    if key in {"command", "cmd", "argv", "query"}:
        digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{kind}.{key}.{_material_id_slug(value, max_chars=60)}.{digest}"
    digest = hashlib.sha1(target.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{kind}.resource.{_material_id_slug(target, max_chars=60)}.{digest}"


def _produced_file_material_id(*, team_name: str, rel_path: str) -> str:
    return f"team_builder.generated_file.{_material_id_slug(team_name)}.{_material_id_slug(rel_path)}"


def _target_matches_material(target: str, material_id: str) -> bool:
    lowered = target.lower()
    mid = material_id.lower()
    if mid in lowered:
        return True
    # material id 常以点号分层, 资源路径里可能只出现最后一段或下划线形态.
    tail = mid.rsplit(".", 1)[-1]
    normalized_tail = tail.replace("-", "_")
    normalized_target = re.sub(r"[^a-z0-9_]+", "_", lowered)
    return len(normalized_tail) >= 5 and normalized_tail in normalized_target


def _field_read_in_source(source: str, field: str) -> bool:
    escaped = re.escape(field)
    patterns = (
        rf"\.get\(\s*['\"]{escaped}['\"]",
        rf"\[\s*['\"]{escaped}['\"]\s*\]",
    )
    return any(re.search(pattern, source) for pattern in patterns)


def _field_written_in_source(source: str, field: str) -> bool:
    escaped = re.escape(field)
    patterns = (
        rf"['\"]{escaped}['\"]\s*:",
        rf"\.update\(\s*\{{[^}}]*['\"]{escaped}['\"]\s*:",
    )
    return any(re.search(pattern, source, re.DOTALL) for pattern in patterns)


def _material_io_provenance(
    *,
    source: str,
    team_name: str,
    rel_path: str,
    input_material_ids: list[str],
    output_material_id: str | None,
    input_schema_required: dict[str, list[str]],
    output_schema_required: list[str],
    observed_read_targets: list[str],
) -> dict[str, Any]:
    """全内容 material 化: contract、生成代码、读取资源都先进入 material 图谱."""

    material_io_links: list[dict[str, Any]] = []
    for material_id in input_material_ids:
        material_io_links.append(
            {
                "material_id": material_id,
                "direction": "read",
                "confidence": "high",
                "basis": "FORMAT_IN declaration",
                "evidence": ["worker.format_in"],
            }
        )
    if output_material_id:
        material_io_links.append(
            {
                "material_id": output_material_id,
                "direction": "write",
                "confidence": "high",
                "basis": "FORMAT_OUT declaration",
                "evidence": ["worker.format_out"],
            }
        )
    produced_file_material = {
        "material_id": _produced_file_material_id(team_name=team_name, rel_path=rel_path),
        "direction": "write",
        "confidence": "high",
        "basis": "external agent returned generated file content",
        "evidence": [rel_path],
        "registration_status": "generated-candidate",
        "content_kind": "python_source",
        "rel_path": rel_path,
        "bytes": len(source.encode("utf-8", errors="ignore")),
    }
    material_io_links.append(produced_file_material)

    input_field_reads: dict[str, list[str]] = {}
    missing_input_required: dict[str, list[str]] = {}
    for material_id, required in input_schema_required.items():
        observed = sorted(field for field in required if _field_read_in_source(source, field))
        missing = sorted(field for field in required if field not in observed)
        input_field_reads[material_id] = observed
        if missing:
            missing_input_required[material_id] = missing

    output_field_writes = sorted(
        field for field in output_schema_required if _field_written_in_source(source, field)
    )
    missing_output_required = sorted(
        field for field in output_schema_required if field not in set(output_field_writes)
    )

    all_material_ids = [*input_material_ids, *([output_material_id] if output_material_id else [])]
    resource_read_links: list[dict[str, Any]] = []
    resource_material_links: list[dict[str, Any]] = []
    inferred_material_read_links: list[dict[str, Any]] = []
    seen_inferred_reads: set[tuple[str, str]] = set()
    for target in observed_read_targets:
        candidate_id = _material_candidate_id_for_read_target(target)
        target_key, _ = _read_target_value(target)
        normalized_target = _normalized_read_target_value(target)
        candidate_kind = _read_target_candidate_kind(target)
        declared_material_ids = _material_ids_declared_in_read_target(target)
        matched_material_ids = sorted({
            material_id
            for material_id in all_material_ids
            if material_id and _target_matches_material(target, material_id)
        } | set(declared_material_ids))
        candidate_reason = _resource_candidate_reason(
            target,
            candidate_id,
            declared_material_ids,
            matched_material_ids,
        )
        promotion_hint = _resource_promotion_hint(declared_material_ids, matched_material_ids)
        resource_link = {
            "target": target,
            "target_key": target_key,
            "normalized_target": normalized_target,
            "candidate_kind": candidate_kind,
            "resource_kind": _resource_kind_for_read_target(target),
            "direction": "read",
            "confidence": "medium",
            "basis": "external agent read/tool event",
            "material_id": candidate_id,
            "registration_status": "candidate",
            "candidate_reason": candidate_reason,
            "promotion_hint": promotion_hint,
            "matched_material_ids": matched_material_ids,
        }
        resource_read_links.append(resource_link)
        resource_material_links.append(
            {
                "material_id": candidate_id,
                "direction": "read",
                "confidence": "medium",
                "basis": "external agent read/tool event",
                "evidence": [target],
                "registration_status": "candidate",
                "resource_kind": resource_link["resource_kind"],
                "target": target,
                "target_key": target_key,
                "normalized_target": normalized_target,
                "candidate_kind": candidate_kind,
                "candidate_reason": candidate_reason,
                "promotion_hint": promotion_hint,
                "matched_material_ids": matched_material_ids,
            }
        )
        for material_id in matched_material_ids:
            key = (material_id, target)
            if key in seen_inferred_reads:
                continue
            seen_inferred_reads.add(key)
            if material_id in declared_material_ids:
                confidence = "high"
                basis = "read target file declares OMNI material_id"
                registration_status = "declared-in-file"
            else:
                confidence = "medium"
                basis = "read target matched material id/name token"
                registration_status = "registered-or-declared"
            if material_id:
                inferred_material_read_links.append(
                    {
                        "material_id": material_id,
                        "direction": "read",
                        "confidence": confidence,
                        "basis": basis,
                        "evidence": [target],
                        "registration_status": registration_status,
                        "resource_kind": resource_link["resource_kind"],
                        "target": target,
                        "candidate_material_id": candidate_id,
                        "candidate_reason": candidate_reason,
                        "promotion_hint": promotion_hint,
                    }
                )

    return {
        "material_io_links": material_io_links,
        "produced_content_materials": [produced_file_material],
        "static_field_access": {
            "input_field_reads": input_field_reads,
            "missing_input_required": missing_input_required,
            "output_field_writes": output_field_writes,
            "missing_output_required": missing_output_required,
        },
        "resource_read_links": resource_read_links[:40],
        "resource_material_links": resource_material_links[:40],
        "inferred_material_read_links": inferred_material_read_links[:40],
    }


class _WorkerCodeExtractResult(ExtractResultRouter):
    def __init__(self, *, bus: Any):
        super().__init__(bus=bus)
        self._run_context: dict = {}

    def set_run_context(
        self,
        *,
        worker_id: str,
        team_name: str,
        package_domain: str,
    ) -> None:
        self._run_context = {
            "worker_id": worker_id,
            "team_name": team_name,
            "package_domain": package_domain,
        }

    def extract(self, *, final_text: str, messages: list, turn_count: int, stop_reason: str) -> Verdict:
        worker_id = self._run_context.get("worker_id") or "unknown"
        team_name = self._run_context.get("team_name") or "unnamed_team"
        package_domain = (
            self._run_context.get("package_domain")
            or "invalid-unbound-package"
        )

        # 1. 先找 finish tool_use 的 result
        result_body: str | None = None
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "finish":
                        inp = block.get("input", {})
                        r = inp.get("result")
                        if isinstance(r, str) and r.strip():
                            result_body = r
                            break
            if result_body:
                break

        # 2. fallback: final_text
        if not result_body:
            result_body = final_text or ""

        body = _extract_python_body(result_body)
        if not body or len(body.strip()) < 80:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "worker_id": worker_id,
                    "rel_path": f"workers/{_module_name_for(worker_id)}.py",
                    "content": body,
                    "lint_issues": ["empty or too short"],
                },
                diagnosis=f"worker {worker_id} code empty · turns={turn_count} stop={stop_reason}",
            )

        # 3. 骨架兜底补 OmniMark 头
        if not _OMNI_MARK_RE.search(body):
            header = (
                f"# [OMNI] origin=team_builder domain={package_domain}/workers/{worker_id} "
                f"ts={_TODAY}T00:00:00Z type=worker"
            )
            body = header + "\n" + body

        # 4. ServiceBus lint
        lint_issues = _lint_service_bus_abuse([(f"workers/{_module_name_for(worker_id)}.py", body)])

        rel_path = f"workers/{_module_name_for(worker_id)}.py"
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "worker_id": worker_id,
                "rel_path": rel_path,
                "content": body,
                "lint_issues": lint_issues,
            },
            diagnosis=f"worker {worker_id} · {len(body)} bytes · {len(lint_issues)} lint issues",
        )


class _WorkerCodeSingleAgent(AgentNodeLoop):
    """单 Worker 代码 sub-agent · 每 worker_design_detailed 新建一个独立实例."""

    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter, _SingleFileFinishRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _WORKER_CODE_SYSTEM_PROMPT
    DESCRIPTION: ClassVar[str] = "Phase 8 · sub-agent · 单 Worker 代码生成"
    MAX_RETRIES: ClassVar[int] = 1

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _WorkerCodePromptBuilder:
        return _WorkerCodePromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _WorkerCodeExtractResult:
        return _WorkerCodeExtractResult(bus=bus)

    async def run(self, input_data: Any) -> Verdict:
        if isinstance(input_data, dict):
            wid = (input_data.get("worker_detail") or {}).get("worker_id") or "unknown"
            tn = input_data.get("team_name") or "unnamed_team"
            package_domain = (
                input_data.get("package_domain")
                or "invalid-unbound-package"
            )
            if hasattr(self._extract_result, "set_run_context"):
                self._extract_result.set_run_context(
                    worker_id=wid,
                    team_name=tn,
                    package_domain=package_domain,
                )
        return await super().run(input_data)


def _skeleton_minimal_worker_code(
    worker_detail: dict,
    team_name: str,
    package_domain: str,
) -> str:
    """最小可编译 Worker 骨架 · 当 sub-agent 全失败时的保底产物."""
    wid = worker_detail.get("worker_id") or "unknown"
    impl_type = (worker_detail.get("impl_type") or "SOFT").upper()
    fmt_in = worker_detail.get("format_in")
    fmt_out = worker_detail.get("format_out") or "unknown_material"
    if isinstance(fmt_in, list):
        fmt_in_repr = "[" + ", ".join(f'"{x}"' for x in fmt_in) + "]"
    else:
        fmt_in_repr = f'"{fmt_in or "unknown_material"}"'
    class_name = "".join(p[:1].upper() + p[1:] for p in re.split(r"[_\-\s]+", wid) if p) + "Worker"
    return (
        f"# [OMNI] origin=team_builder domain={package_domain}/workers/{wid} "
        f"ts={_TODAY}T00:00:00Z type=worker\n"
        f'"""{wid} · 骨架保底 ({impl_type}, sub-agent 失败 · 需人工补)."""\n'
        f"from __future__ import annotations\n"
        f"from typing import Any\n"
        f"from omnicompany.packages.services._core.omnicompany import Worker\n"
        f"from omnicompany.protocol.anchor import Verdict, VerdictKind\n"
        f"\n"
        f"class {class_name}(Worker):\n"
        f'    DESCRIPTION = "{wid} · 骨架保底 · 需人工补真实现"\n'
        f"    FORMAT_IN = {fmt_in_repr}\n"
        f'    FORMAT_OUT = "{fmt_out}"\n'
        f"\n"
        f"    def run(self, input_data: Any) -> Verdict:\n"
        f"        return Verdict(\n"
        f"            kind=VerdictKind.PASS,\n"
        f"            output={{'status': 'skeleton stub'}},\n"
        f'            diagnosis="{wid} 骨架保底 · 等待人工填充",\n'
        f"        )\n"
    )


class WorkerCodeOrchestrator(Worker):
    """Ws7 Orchestrator · 对 worker_design_detailed.details 并行跑 N 份 sub-agent."""

    DESCRIPTION: ClassVar[str] = (
        "CodeGen-Ws7 · Orchestrator · for-each worker_design_detailed · asyncio.gather N 份独立 "
        "_WorkerCodeSingleAgent (sub-agent 独立 context · 避免单 AgentNodeLoop 10 文件内爆). "
        "失败的 worker 骨架保底填最小可编译实现."
    )
    FORMAT_IN: ClassVar = [
        "team_builder.material.team_design",
        "team_builder.material.worker_design_detailed",
        "team_builder.material.material_design_detailed",  # V3.2: 用于给 sub-agent 传 Material required 字段
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.worker_code_files_bundle"
    MAX_CONCURRENT: ClassVar[int] = 4

    def __init__(
        self,
        *,
        use_external_agent: bool = True,
        external_provider: str = "claude-code",
        external_permission_mode: ExternalAgentPermissionMode | str = ExternalAgentPermissionMode.READONLY,
        external_model: str | None = None,
        external_model_policy: ExternalAgentModelPolicy = "none",
        external_timeout_s: float = 900.0,
        external_worker_registry: ExternalAgentWorkerRegistry | None = None,
        external_cwd: Path | str | None = None,
        max_external_prompt_chars: int = 24000,
        fallback_to_legacy_agent: bool = False,
        internal_model: str | None = None,
    ) -> None:
        self.use_external_agent = use_external_agent
        self.external_provider = external_provider
        self.external_permission_mode = external_permission_mode
        self.external_model = external_model
        self.external_model_policy = external_model_policy
        self.external_timeout_s = external_timeout_s
        self.external_worker_registry = external_worker_registry
        self.external_cwd = Path(external_cwd).expanduser().resolve() if external_cwd is not None else None
        self.max_external_prompt_chars = max_external_prompt_chars
        self.fallback_to_legacy_agent = fallback_to_legacy_agent
        self.internal_model = internal_model

    async def _run_external_one(
        self,
        *,
        detail: dict,
        team_name: str,
        package_domain: str,
        out_schema: dict,
        in_schemas: dict[str, dict],
    ) -> tuple[str, str, list[str], bool, dict[str, Any]]:
        wid = detail.get("worker_id") or "unknown"
        rel_path = f"workers/{_module_name_for(wid)}.py"
        class_name = _class_name_for(wid)
        prompt = _build_external_worker_code_prompt(
            team_name=team_name,
            package_domain=package_domain,
            worker_detail=detail,
            format_out_schema=out_schema,
            format_in_schemas=in_schemas,
        )
        prompt_issues = _external_prompt_quality_issues(
            prompt,
            worker_id=wid,
            max_chars=self.max_external_prompt_chars,
        )
        base_meta: dict[str, Any] = {
            "worker_id": wid,
            "rel_path": rel_path,
            "provider": self.external_provider,
            "permission_mode": str(getattr(self.external_permission_mode, "value", self.external_permission_mode)),
            "model": self.external_model,
            "model_policy": self.external_model_policy,
            "prompt_chars": len(prompt),
            "input_material_ids": (
                list(detail.get("format_in"))
                if isinstance(detail.get("format_in"), list)
                else [detail.get("format_in")]
                if isinstance(detail.get("format_in"), str)
                else []
            ),
            "output_material_id": detail.get("format_out"),
            "input_schema_required": {
                mid: (schema.get("required") or [])
                for mid, schema in in_schemas.items()
                if isinstance(schema, dict)
            },
            "output_schema_required": (
                out_schema.get("required") or []
                if isinstance(out_schema, dict)
                else []
            ),
        }
        if prompt_issues:
            return (
                rel_path,
                _skeleton_minimal_worker_code(
                    detail,
                    team_name,
                    package_domain,
                ),
                [f"external-agent prompt rejected: {issue}" for issue in prompt_issues],
                True,
                {**base_meta, "status": "prompt_rejected", "prompt_quality_issues": prompt_issues},
            )

        output_schema_path: Path | None = None
        schema_tmpdir: tempfile.TemporaryDirectory | None = None
        if self.external_provider == "codex":
            schema_tmpdir = tempfile.TemporaryDirectory(prefix="omni-worker-code-schema-")
            output_schema_path = Path(schema_tmpdir.name) / "worker_output_schema.json"
            output_schema_path.write_text(
                json.dumps(_external_worker_output_schema(rel_path=rel_path, class_name=class_name), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        request = ExternalAgentRunRequest(
            provider=self.external_provider,
            prompt=prompt,
            cwd=self.external_cwd or Path.cwd(),
            permission_mode=self.external_permission_mode,
            model=self.external_model,
            model_policy=self.external_model_policy,
            output_schema_path=output_schema_path,
            timeout_s=self.external_timeout_s,
            trace_id=f"team_builder.worker_code.{team_name}.{wid}",
            metadata={
                "entrypoint": "team_builder.worker_code_orchestrator",
                "team_name": team_name,
                "worker_id": wid,
                "rel_path": rel_path,
                "format_in": detail.get("format_in"),
                "format_out": detail.get("format_out"),
            },
        )
        try:
            result = await run_external_agent_request(
                request,
                bus=getattr(self, "_bus", None),
                worker_registry=self.external_worker_registry,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                rel_path,
                _skeleton_minimal_worker_code(
                    detail,
                    team_name,
                    package_domain,
                ),
                [f"external-agent exception: {type(exc).__name__}: {exc}"],
                True,
                {**base_meta, "status": "exception", "error": str(exc)},
            )
        finally:
            if schema_tmpdir is not None:
                schema_tmpdir.cleanup()

        status = result.normalized_status()
        meta = {
            **base_meta,
            "run_id": result.run_id,
            "status": status.value,
            "duration_ms": result.duration_ms,
            "changed_files": list(result.changed_files),
            "error": result.error,
            **_summarize_external_agent_events(result.events),
        }
        if status != ExternalAgentStatus.SUCCEEDED:
            return (
                rel_path,
                _skeleton_minimal_worker_code(
                    detail,
                    team_name,
                    package_domain,
                ),
                [f"external-agent {status.value}: {result.error or result.diff_summary or 'no final source'}"],
                True,
                meta,
            )

        structured = result.structured_output or {}
        source_text = ""
        source_text_origin = ""
        files_obj = structured.get("files") if isinstance(structured, dict) else None
        if isinstance(files_obj, dict):
            candidate = files_obj.get(rel_path)
            if isinstance(candidate, str):
                source_text_origin = f"structured.files.{rel_path}"
            if not isinstance(candidate, str):
                candidate_item = next(((k, v) for k, v in files_obj.items() if str(k).endswith(".py") and isinstance(v, str)), None)
                if candidate_item:
                    source_text_origin = f"structured.files.{candidate_item[0]}"
                    candidate = candidate_item[1]
                else:
                    candidate = ""
            source_text = candidate or ""
        if not source_text and isinstance(structured.get("content"), str):
            source_text = structured["content"]
            source_text_origin = "structured.content"
        if not source_text:
            source_text = result.final_text or ""
            source_text_origin = "final_text"

        body = _extract_external_worker_source(source_text, class_name=class_name)
        if not body:
            parse_meta = {
                **meta,
                "parse_status": "no_worker_source",
                "final_text_chars": len(result.final_text or ""),
                "source_text_chars": len(source_text or ""),
                "source_text_origin": source_text_origin,
                "source_text_excerpt": _external_agent_text_excerpt(source_text),
                "final_text_excerpt": _external_agent_text_excerpt(result.final_text or ""),
                "parse_diagnostics": _external_worker_source_parse_diagnostics(source_text, class_name=class_name),
            }
            if isinstance(structured, dict):
                parse_meta["structured_output_keys"] = sorted(str(key)[:120] for key in structured.keys())
            return (
                rel_path,
                _skeleton_minimal_worker_code(
                    detail,
                    team_name,
                    package_domain,
                ),
                ["external-agent succeeded but returned no parseable worker source"],
                True,
                parse_meta,
            )

        body = _ensure_worker_omni_header(
            body,
            package_domain=package_domain,
            worker_id=wid,
        )
        compile_issue = _python_compile_issue(rel_path=rel_path, source=body)
        if compile_issue:
            return (
                rel_path,
                _skeleton_minimal_worker_code(
                    detail,
                    team_name,
                    package_domain,
                ),
                [f"external-agent generated invalid python: {compile_issue}"],
                True,
                {
                    **meta,
                    "parse_status": "syntax_error",
                    "source_chars": len(body),
                    "compile_issue": compile_issue,
                },
            )
        verdict_kind_issue = _verdict_kind_issue(body)
        if verdict_kind_issue:
            return (
                rel_path,
                _skeleton_minimal_worker_code(
                    detail,
                    team_name,
                    package_domain,
                ),
                [f"external-agent generated invalid VerdictKind use: {verdict_kind_issue}"],
                True,
                {
                    **meta,
                    "parse_status": "invalid_verdict_kind",
                    "source_chars": len(body),
                    "verdict_kind_issue": verdict_kind_issue,
                },
            )
        lint_issues = _lint_service_bus_abuse([(rel_path, body)])
        meta = {
            **meta,
            **_material_io_provenance(
                source=body,
                team_name=team_name,
                rel_path=rel_path,
                input_material_ids=base_meta["input_material_ids"],
                output_material_id=base_meta["output_material_id"],
                input_schema_required=base_meta["input_schema_required"],
                output_schema_required=base_meta["output_schema_required"],
                observed_read_targets=meta.get("observed_read_targets") or [],
            ),
        }
        return (
            rel_path,
            body,
            lint_issues,
            False,
            {**meta, "parse_status": "worker_source", "source_chars": len(body)},
        )

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="input_data must be dict")

        td = input_data.get("_from_team_architect") or {}
        team_name = (td.get("team_name") if isinstance(td, dict) else None) or "unnamed_team"
        try:
            target_package_path = canonical_team_package_path(
                td.get("target_package_path") if isinstance(td, dict) else None,
                team_name=team_name,
            )
            package_domain = team_omni_domain(
                target_package_path,
                team_name=team_name,
            )
        except ValueError as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"team package location invalid: {exc}",
            )

        wd = input_data.get("_from_worker_designer") or {}
        details = []
        if isinstance(wd, dict):
            ds = wd.get("details")
            if isinstance(ds, list):
                details = [d for d in ds if isinstance(d, dict)]
            elif wd.get("worker_id"):
                details = [wd]
        if not details:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"files": {}, "success_count": 0, "fail_count": 0},
                diagnosis="worker_design_detailed details is empty",
            )
        existing_details = [
            detail
            for detail in details
            if str(detail.get("impl_type") or "").upper() == "EXISTING"
        ]
        generated_details = [
            detail
            for detail in details
            if str(detail.get("impl_type") or "").upper() != "EXISTING"
        ]
        if not generated_details:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "team_name": team_name,
                    "target_package_path": target_package_path,
                    "files": {},
                    "success_count": 0,
                    "fail_count": 0,
                    "lint_summary": [],
                    "external_agent_runs": [
                        {
                            "worker_id": detail.get("worker_id"),
                            "provider": "existing-binding",
                            "status": "reused",
                            "binding_ref": detail.get("binding_ref"),
                        }
                        for detail in existing_details
                    ],
                },
                diagnosis=(
                    f"0 generated workers · {len(existing_details)} existing bindings reused"
                ),
            )

        # V3.2: 提取 material schema 给每个 sub-agent 看 required 字段 (防 output key 不匹配)
        md = input_data.get("_from_material_designer") or {}
        material_schemas: dict[str, dict] = {}
        if isinstance(md, dict):
            mds = md.get("details") if isinstance(md.get("details"), list) else []
            for m in mds:
                if isinstance(m, dict) and m.get("material_id"):
                    material_schemas[m["material_id"]] = m.get("json_schema") or {}

        sem = asyncio.Semaphore(self.MAX_CONCURRENT)

        async def _run_one(detail: dict) -> tuple[str, str, list[str], bool, dict[str, Any]]:
            """返回 (rel_path, content, lint_issues, is_skeleton_fallback)."""
            wid = detail.get("worker_id") or "unknown"
            # 找本 worker 的 FORMAT_OUT Material schema (output 约束)
            fmt_out = detail.get("format_out")
            out_schema = material_schemas.get(fmt_out, {}) if isinstance(fmt_out, str) else {}
            # P6.3 · 找 FORMAT_IN Material schema (input 约束)
            fmt_in = detail.get("format_in")
            fmt_in_list = fmt_in if isinstance(fmt_in, list) else [fmt_in] if isinstance(fmt_in, str) else []
            in_schemas = {fi: material_schemas.get(fi, {}) for fi in fmt_in_list if isinstance(fi, str)}
            async with sem:
                if self.use_external_agent:
                    external_result = await self._run_external_one(
                        detail=detail,
                        team_name=team_name,
                        package_domain=package_domain,
                        out_schema=out_schema,
                        in_schemas=in_schemas,
                    )
                    if not external_result[3] or not self.fallback_to_legacy_agent:
                        return external_result

                agent = _WorkerCodeSingleAgent(model=self.internal_model)
                sub_input = {
                    "team_name": team_name,
                    "package_domain": package_domain,
                    "worker_detail": detail,
                    "format_out_schema": out_schema,  # V3.2: 喂 output required 字段给 LLM
                    "format_in_schemas": in_schemas,  # P6.3: 喂 input required 字段
                }
                kind_val = "?"
                diag_preview = ""
                try:
                    v = await agent.run(sub_input)
                    # DEBUG · 2026-04-25: 诊断 non-PASS 情况下 Verdict 的真实 shape
                    kind_val = getattr(v.kind, "value", v.kind) if v.kind else "?"
                    out_type = type(v.output).__name__
                    out_keys = list(v.output.keys()) if isinstance(v.output, dict) else None
                    diag_preview = (v.diagnosis or "")[:150] if hasattr(v, "diagnosis") else ""
                    print(f"[Ws7-dbg] {wid}: kind={kind_val} output={out_type} keys={out_keys} diag={diag_preview!r}")
                    if v.kind == VerdictKind.PASS and isinstance(v.output, dict):
                        return (
                            v.output.get("rel_path") or f"workers/{_module_name_for(wid)}.py",
                            v.output.get("content") or "",
                            v.output.get("lint_issues") or [],
                            False,
                            {"worker_id": wid, "provider": "legacy-internal-agent", "status": "succeeded"},
                        )
                    # V3.2 · 2026-04-25: 若 non-PASS 但 output 里有 content · 也用 (不白费 LLM 产的代码)
                    # 但在 lint_summary 标 "accepted non-PASS" 让下游知道 CodeReviewer 会更严查
                    if isinstance(v.output, dict) and isinstance(v.output.get("content"), str) and len(v.output["content"]) > 100:
                        return (
                            v.output.get("rel_path") or f"workers/{_module_name_for(wid)}.py",
                            v.output["content"],
                            (v.output.get("lint_issues") or []) + [f"sub-agent verdict={kind_val} · content accepted"],
                            False,
                            {"worker_id": wid, "provider": "legacy-internal-agent", "status": f"accepted-{kind_val}"},
                        )
                except Exception as e:  # noqa: BLE001
                    import traceback
                    tb = traceback.format_exc()[-500:]
                    print(f"[Ws7-dbg] {wid}: EXCEPTION {type(e).__name__}: {e}\n{tb}")
                    # 骨架保底
                    return (
                        f"workers/{_module_name_for(wid)}.py",
                        _skeleton_minimal_worker_code(
                            detail,
                            team_name,
                            package_domain,
                        ),
                        [f"sub-agent exception: {type(e).__name__}: {e}"],
                        True,
                        {"worker_id": wid, "provider": "legacy-internal-agent", "status": "exception", "error": str(e)},
                    )
                return (
                    f"workers/{_module_name_for(wid)}.py",
                    _skeleton_minimal_worker_code(
                        detail,
                        team_name,
                        package_domain,
                    ),
                    [f"sub-agent non-PASS ({kind_val}) · skeleton fallback · diag={diag_preview!r}"],
                    True,
                    {"worker_id": wid, "provider": "legacy-internal-agent", "status": f"non-pass-{kind_val}"},
                )

        tasks = [_run_one(d) for d in generated_details]
        results = await asyncio.gather(*tasks)

        files: dict[str, str] = {}
        lint_summary: list[str] = []
        external_agent_runs: list[dict[str, Any]] = []
        success = 0
        fail = 0
        for rel_path, content, lint_issues, is_fallback, run_meta in results:
            files[rel_path] = content
            if run_meta:
                external_agent_runs.append(run_meta)
            if is_fallback:
                fail += 1
                lint_summary.append(f"{rel_path} · skeleton fallback")
            else:
                success += 1
            for issue in lint_issues:
                lint_summary.append(f"{rel_path} · {issue}")

        return Verdict(
            kind=VerdictKind.PASS if success > 0 else VerdictKind.PARTIAL,
            output={
                "team_name": team_name,
                "target_package_path": target_package_path,
                "files": files,
                "success_count": success,
                "fail_count": fail,
                "lint_summary": lint_summary,
                "external_agent_runs": external_agent_runs,
            },
            diagnosis=(
                f"{success}/{len(generated_details)} generated workers · "
                f"{len(existing_details)} existing bindings · "
                f"{fail} skeleton-fallback · {len(lint_summary)} issues"
            ),
        )


# ═══════════════════════════════════════════════════════════════════════
# Ws8 · DesignMdGenerator (SOFT · 骨架预填七节 + LLM 填内容)
# ═══════════════════════════════════════════════════════════════════════


_DESIGN_MD_SYSTEM_PROMPT = """你是 team_builder 第 8 阶段 · DesignMdGenerator.

## 职责
产出目标 team 的 DESIGN.md · 必须 OMNI-034 七节规范:
  1. 状态
  2. 核心目的
  3. 核心接口
  4. 架构决策
  5. 数据流 / 拓扑
  6. 已知局限
  7. 参考资料

## 工具
- finish(reason, result): 提交完整 DESIGN.md (result = markdown 文本)

## 要求
- 每节至少 2 句话, 真实内容 (不糊弄)
- 架构决策 节要写清本 team 的 3-5 条关键设计选择
- 数据流 / 拓扑 节画 ascii 流程图 (用 → 箭头)
- 已知局限 节诚实标 (不说"无局限")
- 参考资料 节列 docs/ 下相关 standards
- **章节标题必须用上述 7 个规范名** (骨架会规范化 · 但你先用规范名更好)

## 交付
调 finish(reason, result):
- reason: "DESIGN.md 七节填完" 或 "部分节不熟悉 · 填了 X 节"
- result: 完整 markdown 文本 (≥ 100 字符)
"""


class _DesignMdPromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        td = biz_input.get("_from_team_architect") or {}
        wd = biz_input.get("_from_worker_designer") or {}
        md = biz_input.get("_from_material_designer") or {}
        ws = biz_input.get("_from_workspace_designer") or {}

        purpose = td.get("purpose", "") if isinstance(td, dict) else ""
        workers_skel = td.get("workers_skeleton") or [] if isinstance(td, dict) else []
        materials_skel = td.get("materials_skeleton") or [] if isinstance(td, dict) else []

        worker_details = wd.get("details") if isinstance(wd, dict) else None
        material_details = md.get("details") if isinstance(md, dict) else None

        task = f"""## team 概览

purpose: {purpose}

## Workers ({len(workers_skel)})

```json
{json.dumps(workers_skel, ensure_ascii=False, indent=2)[:1500]}
```

## Materials ({len(materials_skel)})

```json
{json.dumps(materials_skel, ensure_ascii=False, indent=2)[:1500]}
```

## Worker 深化 (供"核心接口"节参考)

```json
{json.dumps(worker_details, ensure_ascii=False, indent=2)[:2500] if worker_details else '(无)'}
```

## Material 深化 (供"数据流"节参考)

```json
{json.dumps(material_details, ensure_ascii=False, indent=2)[:2000] if material_details else '(无)'}
```

## Workspace

```json
{json.dumps(ws, ensure_ascii=False, indent=2)[:500]}
```

---

请产 OMNI-034 七节 DESIGN.md 提交 finish(reason, result).

章节标题用规范名 (## 状态 / ## 核心目的 / ...).
"""
        # 补产模式
        retry_missing = biz_input.get("_retry_missing_sections") or []
        if retry_missing:
            task += f"\n\n---\n\n## ⚠️ 补产: 缺章节 {retry_missing}\n请补齐后再 finish.\n"
        return [{"role": "user", "content": task}]


class _DesignMdExtractResult(ExtractResultRouter):
    def extract(self, *, final_text: str, messages: list, turn_count: int, stop_reason: str) -> Verdict:
        body: str | None = None
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "finish":
                        r = (block.get("input") or {}).get("result")
                        if isinstance(r, str) and r.strip():
                            body = r
                            break
            if body:
                break
        if not body:
            body = final_text or ""
        body = body.strip()

        if not body or len(body) < 80:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "rel_path": "DESIGN.md",
                    "content": body,
                    "missing_sections": ["all"],
                },
                diagnosis=f"DESIGN.md body empty · turns={turn_count} stop={stop_reason}",
            )

        normalized, missing = _normalize_design_md(body)
        # 骨架兜底 · 缺章节补占位
        if missing:
            patch_lines = []
            for sect in missing:
                patch_lines.append(f"\n## {sect}\n\n(骨架占位 · LLM 未填具体内容 · 需人工补)\n")
            normalized = normalized.rstrip() + "\n" + "".join(patch_lines)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "rel_path": "DESIGN.md",
                "content": normalized,
                "missing_sections": missing,
            },
            diagnosis=f"DESIGN.md · {len(normalized)} bytes · 缺补 {len(missing)} 节",
        )


class DesignMdGenerator(AgentNodeLoop):
    """Ws8 · SOFT · 产 DESIGN.md (骨架规范化章节 + 兜底补占位)."""

    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [
        ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter, _SingleFileFinishRouter,
    ]
    NODE_PROMPT: ClassVar[str] = _DESIGN_MD_SYSTEM_PROMPT
    DESCRIPTION: ClassVar[str] = (
        "CodeGen-Ws8 · SOFT AgentNodeLoop · 依完整设计产 OMNI-034 七节 DESIGN.md. "
        "骨架规范化章节名 + 兜底补缺章节占位."
    )
    FORMAT_IN: ClassVar = [
        "team_builder.material.team_design",
        "team_builder.material.workspace_spec",
        "team_builder.material.worker_design_detailed",
        "team_builder.material.material_design_detailed",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.design_md"
    MAX_RETRIES: ClassVar[int] = 1

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _DesignMdPromptBuilder:
        return _DesignMdPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _DesignMdExtractResult:
        return _DesignMdExtractResult(bus=bus)

# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.material_deepener_orchestrator.worker.py"
"""MaterialDesignerWorker — Phase 4' · AgentNodeLoop (2026-04-23).

Worker 协议:
  FORMAT_IN  = team_builder.material.team_design (内含 materials_skeleton list)
  FORMAT_OUT = team_builder.material.material_design_detailed
  (额外输入 context: `target_material_name` · 运行时指定本次处理哪个 material;
   若未指定, LLM 自选 team_design.materials_skeleton[0])

**职责**: AgentNodeLoop · 对 team_design 的**单个** Material skeleton 深化:
    - json_schema (含 properties 字段)
    - description 五要素 (content_semantic, field_meaning, upstream_promise,
      downstream_use, minimal_sample)
    - lifecycle (source/internal/sink)
    - producer/consumers 推断

工具: ReadFile / Glob / Grep / ListDir / Finish
可读: packages/services/*/formats.py (参考 similar Material schema 风格)

**为什么 AgentNodeLoop**: Material schema 推敲要看 similar 案例 + parent material 关系,
单轮 LLM 产出常不诚实 (五要素可能只写一两条). agent 可 grep similar → 产出完整 schema.

**fan-out 策略** (M 份):
  - V2 本版: Worker 处理单 material, 外层 team 拓扑里多次实例化处理不同 material
  - V2+ 可升级: runner 支持动态 fan-out to M 实例, 或 Worker 内部 for-each 多 agent session
"""
from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.loop import AgentNodeLoop
from omnicompany.packages.services._core.agent.routers.extract_result import ExtractResultRouter
from omnicompany.packages.services._core.agent.routers.prompt_builder import PromptBuilderRouter
from omnicompany.packages.services._core.agent.routers.single_tool import (
    FinishRouter,
    GlobRouter,
    GrepRouter,
    ListDirRouter,
    ReadFileRouter,
)
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


_SYSTEM_PROMPT = """你是 team_builder 第 4' 阶段 · MaterialDesigner agent.

## 职责
对 team_design 里**一个** Material skeleton 做深化: json_schema + 五要素 description
+ lifecycle + producer/consumer 推断.

## 工具
- read_file / glob / grep / list_dir: 读 packages/services/*/formats.py 参考 Material 风格
- finish: 提交 JSON 结论

## 五要素 (description 必须覆盖这 5 条)
1. content_semantic: 这个 Material 是什么意义的产物
2. field_meaning: 每个 field 什么意思
3. upstream_promise: producer Worker 对格式的承诺
4. downstream_use: consumer Worker 会怎么用
5. minimal_sample: 最小合法样例

## 产出 JSON
```json
{
  "material_id": "**严格复用** skeleton 里给的 material_id · 禁自造 · 禁加前缀 (不加 'team_builder.material.' 等)",
  "parent": "doc | requirement (仅这 2 个可用 · builtin_registry 的唯二父类型)",
  "json_schema": { "type": "object", "properties": {...}, "required": [...] },
  "producer": "producer worker_id (推断 · 不确定时留 null)",
  "consumers": ["consumer worker_id", ...],
  "lifecycle": "source | internal | sink",
  "description_5elems": {
    "content_semantic": "...",
    "field_meaning": "...",
    "upstream_promise": "...",
    "downstream_use": "...",
    "minimal_sample": "..."
  }
}
```

**铁律 · 命名一致性**:
- `material_id` MUST 与 user prompt 里给的 skeleton.material_id **逐字节一致**
- 不允许加前缀 "team_builder.material." · 不允许改大小写 · 不允许同义词替换
- 若 skeleton 给 "mat_csv_source" → material_id 就是 "mat_csv_source" (不改)
- 若 skeleton 给 "csv_to_md.raw_matrix" → material_id 就是 "csv_to_md.raw_matrix" (不改)

**铁律 · 忠于需求字段名 (source material 必守)**:
- 若原始需求文本 (origin_request / requirement) 指定字段名 (如 `path`, `encoding`, `repo_path`),
  你设计的 source material (lifecycle=source · 入口) 的 json_schema.properties **键名必须逐字节一致**
- ❌ 反例 (2026-04-24 csv_to_md 实测): 需求说 `path`, LLM 改成 `file_path` · 下游 Worker 改用 `path` 读 · 三方不一致 → team 废
- ✅ 正例: 需求说 "path: CSV 文件路径" → json_schema.properties["path"], required=["path"]. 禁重命名为 file_path / csv_path 等.
- 非 source material (lifecycle=internal/sink) 的字段名可以自由设计, 但要与相邻 Worker 代码对齐.

**诚实**: 五要素每条**非空**且有实际内容 · 不允许 "TBD" / 空串 / 一字应付.
**实用**: json_schema.properties 必须列出合理字段 · 至少 2 个.
**parent**: 只能用 "doc" 或 "requirement" (registry 限制), 其他值会注册失败."""


class _MaterialDesignerPromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        team_design = biz_input.get("_from_team_architect") or biz_input.get("team_design") or biz_input
        materials_skeleton = team_design.get("materials_skeleton", []) if isinstance(team_design, dict) else []

        # 选择要深化的 material: 优先 biz_input.target_material_name, 否则取第 0 个
        target_name = biz_input.get("target_material_name")
        target = None
        if target_name and materials_skeleton:
            for m in materials_skeleton:
                if isinstance(m, dict) and (m.get("material_id") == target_name or m.get("material_name") == target_name):
                    target = m
                    break
        if target is None and materials_skeleton:
            target = materials_skeleton[0]
        if target is None:
            target = {"material_id": "unknown", "brief": "(no skeleton provided)"}

        # team 上下文简述
        team_purpose = team_design.get("purpose") or team_design.get("design_path", "")
        workers_skeleton = team_design.get("workers_skeleton", []) if isinstance(team_design, dict) else []
        workers_brief = "\n".join(
            f"  - {w.get('worker_name', w.get('worker_id', '?'))}: {w.get('brief', '')[:80]}"
            for w in workers_skeleton[:8] if isinstance(w, dict)
        )

        # V3.2 · 从 biz_input 抽原始需求文本 (origin_request · 用于 source material 字段名忠实对齐)
        origin_text = ""
        origin_req = biz_input.get("_from_origin_request_loader") or biz_input.get("origin_request") or {}
        if isinstance(origin_req, dict):
            origin_text = origin_req.get("text") or origin_req.get("raw_text") or ""

        retry_missing = biz_input.get("_retry_missing") or []
        retry_prev = biz_input.get("_retry_prev_output") or {}

        lifecycle_hint = target.get("lifecycle") or target.get("kind", "") if isinstance(target, dict) else ""
        is_source = lifecycle_hint == "source" or target.get("is_source") if isinstance(target, dict) else False

        task = f"""## team 上下文 (仅供理解 material 所处语境)

purpose: {team_purpose}

workers ({len(workers_skeleton)}):
{workers_brief}

## 本次要深化的 Material skeleton

```json
{json.dumps(target, ensure_ascii=False, indent=2)}
```
"""

        # source material 特殊提示: 忠于原需求字段名
        if is_source or "source" in str(target).lower():
            task += f"""

## ⚠️ 本 material 可能是 source (入口) · 字段名必须忠于原需求

原始需求文本 (用户手写 · 逐字节看, **别改字段名**):

```
{origin_text[:2000] if origin_text else '(未捕获 origin_request · 若 skeleton.lifecycle=source 需从 team_design 推)'}
```

如果原文说 "path: ..." / "repo_path: ..." / "input_dir: ..." 等, 你的 json_schema.properties 键名**逐字节一致**.
反例: 用户说 `path` · 你改成 `file_path` → 下游 Worker 不一致 → team 废.
"""

        if retry_missing:
            prev_dump = json.dumps(retry_prev, ensure_ascii=False, indent=2)[:1500]
            task += f"""

---

## ⚠️ 补产模式 (上轮不合格 · 骨架判据硬抓)

上次你产的 JSON 缺字段: **{retry_missing}**

上次产出 (供参考):
```json
{prev_dump}
```

**必修**: 把上述字段补齐 · 五要素 (content_semantic/field_meaning/upstream_promise/downstream_use/minimal_sample) 每条实质内容, 不许 "TBD"/空串/一字应付.
"""
        else:
            task += """

---

请:
1. 用 grep/read_file 查 `packages/services/*/formats.py` 里 1-2 个 similar material 作参考
2. 产出完整 material_design_detailed · **五要素每条实质内容**, 不许 "TBD"/空串
3. 用 finish 提交 JSON
"""
        return [{"role": "user", "content": task}]


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def _parse_json_loose(text: str) -> Any | None:
    if not text:
        return None
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class _MaterialDesignerExtractResult(ExtractResultRouter):
    def extract(self, *, final_text: str, messages: list, turn_count: int, stop_reason: str) -> Verdict:
        result_json = None
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "finish":
                        inp = block.get("input", {})
                        result = inp.get("result")
                        if isinstance(result, str):
                            result_json = _parse_json_loose(result)
                        elif isinstance(result, dict):
                            result_json = result
                        if result_json:
                            break
            if result_json:
                break
        if not result_json:
            result_json = _parse_json_loose(final_text)

        if not isinstance(result_json, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"final_text": final_text[:500], "turn_count": turn_count},
                diagnosis=f"MaterialDesigner 未产出 JSON (turns={turn_count}, stop={stop_reason})",
            )

        # 必字段: material_id + json_schema + description_5elems + lifecycle
        # 骨架判据 (feedback_100pct_required_goes_to_skeleton): 缺字段都带 _retry_missing 反馈
        retry_missing: list[str] = []
        for k in ("material_id", "json_schema", "description_5elems", "lifecycle"):
            if k not in result_json:
                retry_missing.append(k)

        # 五要素完整性
        d5 = result_json.get("description_5elems", {})
        if isinstance(d5, dict):
            for k in ("content_semantic", "field_meaning", "upstream_promise",
                      "downstream_use", "minimal_sample"):
                if not d5.get(k) or not str(d5.get(k)).strip():
                    retry_missing.append(f"description_5elems.{k}")
        elif "description_5elems" not in retry_missing:
            retry_missing.append("description_5elems")

        if retry_missing:
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={**result_json, "_needs_retry": True, "_retry_missing": retry_missing},
                diagnosis=f"material_design_detailed 字段不齐 (骨架铁律): 缺 {retry_missing}",
            )

        result_json.setdefault("_meta", {}).update({
            "worker": "MaterialDesignerWorker",
            "stage": "v1_agent_loop",
            "turn_count": turn_count,
            "stop_reason": stop_reason,
        })
        return Verdict(kind=VerdictKind.PASS, output=result_json)


class _MaterialDesignSingleAgent(AgentNodeLoop):
    """单 Material 深化 · 独立 agent session.

    2026-04-24 补产循环 (骨架铁律 · 同 WorkerDesigner): ExtractResult PARTIAL + _needs_retry
    → 补产 1 次, 带 _retry_missing 反馈. 不让"缺字段"混入 details list.
    """

    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter, FinishRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT
    DESCRIPTION: ClassVar[str] = "Phase 4' 单 Material 深化 agent session"
    MAX_RETRIES: ClassVar[int] = 1

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _MaterialDesignerPromptBuilder:
        return _MaterialDesignerPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _MaterialDesignerExtractResult:
        return _MaterialDesignerExtractResult(bus=bus)

    async def run(self, input_data: Any) -> Verdict:
        verdict = await AgentNodeLoop.run(self, input_data)
        retries = 0
        while (
            verdict.kind == VerdictKind.PARTIAL
            and isinstance(verdict.output, dict)
            and verdict.output.get("_needs_retry")
            and verdict.output.get("_retry_missing")
            and retries < self.MAX_RETRIES
        ):
            retries += 1
            retry_input = dict(input_data) if isinstance(input_data, dict) else {}
            retry_input["_retry_missing"] = verdict.output.get("_retry_missing") or []
            retry_input["_retry_prev_output"] = {
                k: v for k, v in verdict.output.items() if not k.startswith("_")
            }
            verdict = await AgentNodeLoop.run(self, retry_input)
        return verdict


def _skeleton_patch_material_detail(d: dict) -> dict:
    """骨架彻底接管: LLM 补产后仍缺字段时, 填通用占位 (feedback_100pct_required_goes_to_skeleton)."""
    d = dict(d)
    mid = d.get("material_id", "unknown.material")
    # json_schema
    if not isinstance(d.get("json_schema"), dict):
        d["json_schema"] = {"type": "object", "properties": {"_placeholder": {"type": "string"}}}
    # lifecycle (真值由 ContractAuditor 反算 · 这里占位)
    if not d.get("lifecycle"):
        d["lifecycle"] = "internal"
    # parent (builtin 只有 doc/requirement)
    if d.get("parent") not in ("doc", "requirement"):
        d["parent"] = "doc"
    # description_5elems
    d5 = d.get("description_5elems") or {}
    if not isinstance(d5, dict): d5 = {}
    defaults = {
        "content_semantic": f"{mid} · 单份 material · 内容由 producer Worker 产出并交 consumer Worker 消费",
        "field_meaning": "字段按 json_schema.properties 定义 · 每字段对应业务语义单元",
        "upstream_promise": "producer Worker 保证输出符合 json_schema · 非空字段不为 null",
        "downstream_use": f"consumer Worker 按 FORMAT_IN 读取 material_id={mid!r} 并按 schema 解析",
        "minimal_sample": '{"_placeholder": "example"}',
    }
    for k, v in defaults.items():
        if not d5.get(k) or not str(d5.get(k)).strip():
            d5[k] = v
    d["description_5elems"] = d5
    d.setdefault("_meta", {})["skeleton_patched"] = True
    return d


class MaterialDesignerWorker(Worker):
    """Phase 4' Orchestrator · for-each materials_skeleton · asyncio.gather M 份独立 agent.

    输出 `{"details": [material_design_detailed × M], "_meta": {...}}`
    """

    FORMAT_IN: ClassVar = [
        "team_builder.material.team_design",
        "team_builder.material.origin_request",  # V3.2: 用于 source material 字段名忠实对齐
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.material_design_detailed"
    DESCRIPTION: ClassVar[str] = (
        "Phase 4' Orchestrator · 对 team_design.materials_skeleton 并行 M 份深化 "
        "(每 skeleton 独立 agent session) · 收集产出 details list. "
        "V3.2: 额外读 origin_request 传 sub-agent 用于 source material 字段名忠实对齐."
    )
    MAX_CONCURRENT: ClassVar[int] = 4

    def __init__(self, *, model: str | None = None) -> None:
        self._model = model

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )
        team_design = input_data.get("_from_team_architect") or input_data.get("team_design") or input_data
        skeletons = team_design.get("materials_skeleton", []) if isinstance(team_design, dict) else []
        if not skeletons:
            worker_skeletons = (
                team_design.get("workers_skeleton", [])
                if isinstance(team_design, dict)
                else []
            )
            existing_only = bool(worker_skeletons) and all(
                isinstance(item, dict)
                and str(item.get("impl_type") or "").upper() == "EXISTING"
                for item in worker_skeletons
            )
            if existing_only:
                return Verdict(
                    kind=VerdictKind.PASS,
                    output={
                        "details": [],
                        "_meta": {
                            "worker": "MaterialDesignerWorker",
                            "stage": "existing_contracts",
                            "skeletons_count": 0,
                            "success_count": 0,
                        },
                    },
                    diagnosis="0 materials · formats owned by existing Worker contracts",
                )
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="team_design.materials_skeleton is empty",
            )

        import asyncio
        sem = asyncio.Semaphore(self.MAX_CONCURRENT)

        # 抽 origin_request 传给 sub-agent (V3.2)
        origin_req = input_data.get("_from_origin_request_loader") or {}

        async def _run_one(skeleton: dict) -> dict | None:
            async with sem:
                agent = _MaterialDesignSingleAgent(model=self._model)
                sub_input = {
                    "_from_team_architect": team_design,
                    "_from_origin_request_loader": origin_req,
                    "target_material_name": skeleton.get("material_id") or skeleton.get("material_name"),
                }
                try:
                    v = await agent.run(sub_input)
                    if v.kind == VerdictKind.PASS:
                        return v.output
                    if v.kind == VerdictKind.PARTIAL and isinstance(v.output, dict):
                        # 骨架彻底接管: LLM 补产后仍 PARTIAL · 骨架自动补齐结构 (feedback_100pct_required_goes_to_skeleton)
                        return _skeleton_patch_material_detail(v.output)
                    return None
                except Exception:
                    return None

        tasks = [_run_one(s) for s in skeletons if isinstance(s, dict)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        details = [d for d in results if isinstance(d, dict)]

        if not details:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"所有 {len(skeletons)} 份 material deepen 都失败",
            )

        # lifecycle 反算骨架由 ContractAuditor 负责 (它能同时看到 worker_detailed + material_detailed
        # 的真 format_in/out). 本层 team_design.workers_skeleton 只有 brief, 看不到 format_in/out,
        # 不在这反算以免误判. 参见 contract_auditor._audit_connections 的 lifecycle_overrides.
        return Verdict(
            kind=VerdictKind.PASS if len(details) == len(skeletons) else VerdictKind.PARTIAL,
            output={
                "details": details,
                "_meta": {
                    "worker": "MaterialDesignerWorker",
                    "stage": "v2_orchestrator",
                    "skeletons_count": len(skeletons),
                    "success_count": len(details),
                },
            },
            diagnosis=f"{len(details)}/{len(skeletons)} materials deepened",
        )

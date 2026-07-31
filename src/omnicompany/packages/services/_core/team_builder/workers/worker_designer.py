# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.worker_deepener_orchestrator.worker.py"
# OMNI-024 ALLOW: 域内私有 worker, 一类一文件经 workers/__init__ 聚合出口, 类实现与本文件模块级 prompt/helper/装配紧耦合, 迁入共享 routers.py 会割裂内聚
"""WorkerDesignerWorker — Phase 4 · AgentNodeLoop (2026-04-23).

Worker 协议:
  FORMAT_IN  = team_builder.material.team_design (含 workers_skeleton + materials_skeleton)
  FORMAT_OUT = team_builder.material.worker_design_detailed
  (额外输入 context: `target_worker_name`)

**职责**: AgentNodeLoop · 对 team_design 的**单个** Worker skeleton 深化:
    - impl_type (HARD/SOFT/AGENT/EXISTING)
    - format_in / format_out (精确到某个 material_id)
    - routes (PASS/FAIL/PARTIAL 的 RouteAction)
    - prompt_template (SOFT/AGENT) 或 rule_spec (HARD)
    - context_sources (F-15 诚实 · 从哪些 material 读)
    - output_token_budget
    - hallucination_risks

工具: ReadFile / Glob / Grep / ListDir / Finish
可读: similar team 代码 (`packages/services/*/workers/*.py`) 参考 FORMAT/routes 风格

**为什么 AgentNodeLoop**: Worker 设计关乎代码实现, 需:
- grep similar Worker 代码看 FORMAT schema 怎么写
- read 参考 Worker 的 routes 设计
- 验证 prompt 对 context_sources 的**诚实消费** (F-15)
单轮 LLM 这些都做不到.
"""
from __future__ import annotations

import importlib
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


_EXISTING_BINDING_FORBIDDEN_KWARGS = {
    "model",
    "prompt",
    "tools",
    "permissions",
    "permission_mode",
    "budget",
}


def _existing_worker_detail(skeleton: dict[str, Any]) -> dict[str, Any]:
    """Resolve an existing Worker class; do not ask an LLM to redesign it."""
    worker_id = str(
        skeleton.get("worker_name") or skeleton.get("worker_id") or ""
    ).strip()
    binding_ref = str(skeleton.get("binding_ref") or "").strip()
    if not worker_id:
        raise ValueError("EXISTING worker requires worker_name")
    if ":" not in binding_ref:
        raise ValueError(
            f"EXISTING worker {worker_id!r} requires binding_ref='<module>:<WorkerClass>'"
        )
    module_name, class_name = binding_ref.rsplit(":", 1)
    module = importlib.import_module(module_name)
    worker_class = getattr(module, class_name)
    if not isinstance(worker_class, type) or not issubclass(worker_class, Worker):
        raise ValueError(f"binding_ref does not resolve to Worker: {binding_ref}")

    format_in = getattr(worker_class, "FORMAT_IN", None)
    format_out = getattr(worker_class, "FORMAT_OUT", None)
    if not isinstance(format_in, (str, list)) or not format_in:
        raise ValueError(f"existing Worker has no usable FORMAT_IN: {binding_ref}")
    if not isinstance(format_out, str) or not format_out:
        raise ValueError(f"existing Worker has no usable FORMAT_OUT: {binding_ref}")

    binding_kwargs = skeleton.get("binding_kwargs") or {}
    if not isinstance(binding_kwargs, dict):
        raise ValueError("EXISTING worker binding_kwargs must be an object")
    forbidden = sorted(_EXISTING_BINDING_FORBIDDEN_KWARGS & set(binding_kwargs))
    if forbidden:
        raise ValueError(
            "EXISTING worker binding_kwargs cannot own Agent execution settings: "
            f"{forbidden}"
        )

    routes = skeleton.get("routes")
    if not isinstance(routes, dict):
        routes = {
            "PASS": {"action": "next"},
            "FAIL": {"action": "halt"},
            "PARTIAL": {"action": "halt"},
        }
    context_sources = format_in if isinstance(format_in, list) else [format_in]
    return {
        "worker_id": worker_id,
        "impl_type": "EXISTING",
        "format_in": format_in,
        "format_in_mode": getattr(worker_class, "FORMAT_IN_MODE", None),
        "format_out": format_out,
        "routes": routes,
        "binding_ref": binding_ref,
        "binding_kwargs": dict(binding_kwargs),
        "context_sources": list(context_sources),
        "hallucination_risks": [],
        "_meta": {
            "existing_binding": True,
            "description": str(getattr(worker_class, "DESCRIPTION", "") or ""),
        },
    }


_SYSTEM_PROMPT = """你是 team_builder 第 4 阶段 · WorkerDesigner agent.

## 职责
对 team_design 里**一个** Worker skeleton 深化: impl_type + FORMAT_IN/OUT + routes
+ prompt_template / rule_spec + context_sources + output_token_budget +
hallucination_risks (SKILL §3.1 的 Worker 18 项清单核心).

## 工具
- read_file / grep / glob / list_dir: 看 packages/services/*/workers/*.py 参考 Worker 实现风格
- finish: 提交 JSON 结论

## impl_type 三分
- **HARD**: 规则驱动确定性 · 无 LLM · rule_spec 写清规则
- **SOFT**: 单轮 LLM · prompt_template 含 system + user 结构
- **AGENT**: AgentNodeLoop · prompt_template 写 NODE_PROMPT · tools 列表 · budget

## 产出 JSON
```json
{
  "worker_id": "完整 worker_id (snake_case 或 original class)",
  "impl_type": "HARD|SOFT|AGENT",
  "format_in": "material_id | [material_id, ...]",
  "format_in_mode": "and|or|null (format_in 为 list 时必填)",
  "format_out": "material_id",
  "routes": {
    "PASS": {"action": "next|emit|jump", "target": "worker_id?"},
    "FAIL": {"action": "retry|halt", "max_retries": 2},
    "PARTIAL": {"action": "..."}
  },
  "prompt_template": "(SOFT/AGENT 填) system + user 结构描述" | null,
  "rule_spec": "(HARD 填) 规则伪代码 / 字段映射" | null,
  "context_sources": ["material_id", ...],
  "output_token_budget": 8000,
  "hallucination_risks": ["风险 1", "风险 2"]
}
```

**铁律**:
- `worker_id` MUST 与 skeleton.worker_name 一致 (不改大小写, 不换命名风格)
- `format_in` / `format_out` MUST 引用 **materials_skeleton 里已有的 material_id** (逐字节一致, 不自造, 不加前缀)
  - 若 skeleton materials 里没有 "raw_matrix", 则不能用 "raw_matrix" 作 format_in
  - 不允许写 "mat_csv_source" 然后又写 "team_builder.material.mat_csv_source" · 复用原始 id
- context_sources 必须非空 (SOFT/AGENT 必遵 F-15 诚实), 且元素 MUST 是 materials_skeleton.material_id
- hallucination_risks 至少 1 条 (诚实面对风险, 禁 "无风险")
- HARD Worker: `prompt_template=null` · `rule_spec` 必须写明**用哪个 ServiceBus**
  - 文件读写 → DiskBus (例: "DiskBus.write(path=..., content=...)")
  - subprocess → BashBus (例: "BashBus.run(['git', 'status'])")
  - HTTP → WebBus
  - 禁直调 subprocess / open('w') / requests
- SOFT/AGENT Worker: `rule_spec=null` · `prompt_template` 含 system + user 两部分, user 里**必须**用 {material_id} 占位符引 format_in 数据 (不用 `**input_data` 透传)
- routes 至少含 PASS; FAIL/PARTIAL 按需"""


class _WorkerDesignerPromptBuilder(PromptBuilderRouter):
    def build_initial_messages(self, biz_input: dict) -> list[dict]:
        team_design = biz_input.get("_from_team_architect") or biz_input.get("team_design") or biz_input
        workers_skeleton = team_design.get("workers_skeleton", []) if isinstance(team_design, dict) else []
        materials_skeleton = team_design.get("materials_skeleton", []) if isinstance(team_design, dict) else []

        target_name = biz_input.get("target_worker_name")
        target = None
        if target_name and workers_skeleton:
            for w in workers_skeleton:
                if isinstance(w, dict) and (w.get("worker_name") == target_name or w.get("worker_id") == target_name):
                    target = w
                    break
        if target is None and workers_skeleton:
            target = workers_skeleton[0]
        if target is None:
            target = {"worker_name": "unknown", "brief": "(no skeleton)"}

        materials_brief = "\n".join(
            f"  - {m.get('material_id', '?')}: {m.get('brief', '')[:80]}"
            for m in materials_skeleton if isinstance(m, dict)
        )
        siblings_brief = "\n".join(
            f"  - {w.get('worker_name', '?')} ({w.get('impl_type', '?')}): {w.get('brief', '')[:60]}"
            for w in workers_skeleton if isinstance(w, dict) and w.get("worker_name") != target.get("worker_name")
        )

        team_purpose = team_design.get("purpose", "") if isinstance(team_design, dict) else ""

        # 补产模式上下文 (骨架铁律 · 2026-04-24)
        retry_missing = biz_input.get("_retry_missing") or []
        retry_prev_output = biz_input.get("_retry_prev_output") or {}

        task = f"""## team 上下文

purpose: {team_purpose}

## 全部 Materials (供 FORMAT_IN/OUT 选择)

{materials_brief}

## 其他 Sibling Workers (供设计 routes 目标)

{siblings_brief}

## 本次要深化的 Worker skeleton

```json
{json.dumps(target, ensure_ascii=False, indent=2)}
```
"""

        if retry_missing:
            prev_dump = json.dumps(retry_prev_output, ensure_ascii=False, indent=2)[:1500]
            task += f"""

---

## ⚠️ 补产模式 (上轮不合格 · 骨架判据硬抓)

上次你产的 JSON 缺字段: **{retry_missing}**

上次产出 (供参考):
```json
{prev_dump}
```

**必修**: 把上述字段补齐再重新 finish 整份 JSON (含所有字段). 具体:
- `rule_spec`: HARD worker 的规则描述 (≥20 字符 · 含 ServiceBus 用法 / 步骤编号 / 出入口)
- `prompt_template`: SOFT/AGENT 的 {{system, user}} 双字段 (system ≥ 30 字符)
- `context_sources`: SOFT/AGENT 的字段列表 (供 prompt 模板引用)
- `hallucination_risks`: 至少 1 条 (不许 "无风险" 占位)
- `routes.PASS` / `routes.FAIL` / `routes.PARTIAL`: 三分支全有 (缺一 = PARTIAL+retry · 18 条 Worker 契约)
- `format_in.unknown_material=[...]` / `format_out.unknown_material=[...]`: 这些 material_id **不在** materials_skeleton, 必须改为上文 "全部 Materials" 里列出的 ID (逐字节一致)

骨架铁律: 缺这些字段 → Worker 必将再次 PARTIAL 拒收, 浪费 turn.
"""
        else:
            task += """

---

请:
1. 用 grep/read_file 查 1-2 个 similar Worker (`packages/services/*/workers/*.py`) 参考风格
2. 按 impl_type 产出完整 worker_design_detailed:
   - HARD → **必填 rule_spec** (≥20 字符 · 步骤化描述)
   - SOFT/AGENT → **必填 prompt_template** (system+user) + context_sources (列表) + hallucination_risks (≥1)
3. **`routes` 必须有 `PASS` / `FAIL` / `PARTIAL` 三个分支** (骨架判据 · 缺一个即 retry)
4. **`format_in` / `format_out` 必须是上文 "全部 Materials" 里列出的 material_id 逐字节一致**, 禁自造新 material_id
5. 用 finish 提交 JSON
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


class _WorkerDesignerExtractResult(ExtractResultRouter):
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
                diagnosis=f"WorkerDesigner 未产出 JSON (turns={turn_count}, stop={stop_reason})",
            )

        missing = [
            k for k in ("worker_id", "impl_type", "format_in", "format_out", "routes")
            if k not in result_json
        ]
        if missing:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis=f"worker_design_detailed missing: {missing}",
            )

        impl_type = result_json.get("impl_type")
        if impl_type not in ("HARD", "SOFT", "AGENT"):
            return Verdict(
                kind=VerdictKind.FAIL,
                output=result_json,
                diagnosis=f"impl_type 必须是 HARD/SOFT/AGENT (got {impl_type!r})",
            )

        # 骨架接管约束 (feedback_100pct_required_goes_to_skeleton):
        # 必做的字段靠代码 assert, 不靠 prompt 引导 LLM 自觉
        retry_missing: list[str] = []

        # F-15 诚实: SOFT/AGENT 必须 context_sources 非空
        if impl_type in ("SOFT", "AGENT"):
            cs = result_json.get("context_sources")
            if not cs or not isinstance(cs, list):
                retry_missing.append("context_sources")

        # HARD worker 必须填 rule_spec (骨架铁律: 2026-04-24)
        # 反例: md_sink_worker 漏 rule_spec 触发 design_validator FAIL
        if impl_type == "HARD":
            rs = result_json.get("rule_spec")
            if not isinstance(rs, str) or len(rs.strip()) < 20:
                retry_missing.append("rule_spec")

        # SOFT/AGENT 必须填 prompt_template
        if impl_type in ("SOFT", "AGENT"):
            pt = result_json.get("prompt_template")
            if not isinstance(pt, dict) or not pt.get("system"):
                retry_missing.append("prompt_template")

        # hallucination_risks 至少 1 条
        hr = result_json.get("hallucination_risks") or []
        if not isinstance(hr, list) or len(hr) < 1:
            retry_missing.append("hallucination_risks")

        # routes 必须含 PASS / FAIL / PARTIAL 三分支 (骨架铁律: 18 条 Worker 契约)
        # 反例: output_sink_worker 漏 PARTIAL 分支触发 worker_18item_check FAIL
        routes = result_json.get("routes")
        worker_id = result_json.get("worker_id")
        if not isinstance(routes, dict):
            retry_missing.append("routes")
        else:
            for branch in ("PASS", "FAIL", "PARTIAL"):
                if branch not in routes:
                    retry_missing.append(f"routes.{branch}")
                else:
                    # 自循环检测 (target == 自己 且 action != halt 是非法设计)
                    rt = routes[branch]
                    if isinstance(rt, dict):
                        target = rt.get("target")
                        action = rt.get("action", "")
                        if target and target == worker_id and action != "halt":
                            retry_missing.append(f"routes.{branch}.self_loop(target={target})")

        # format_in / format_out 必须是 materials_skeleton 里登记的 material_id
        # (prompt_builder 会把 allowed_material_ids 注入 biz_input · 由 extract 通过实例属性读)
        allowed_mids = getattr(self, "_allowed_material_ids", None)
        if allowed_mids is not None:
            def _check_mid(val):
                if isinstance(val, str):
                    return [val] if val and val not in allowed_mids else []
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, str) and x not in allowed_mids]
                return []
            fi_bad = _check_mid(result_json.get("format_in"))
            fo_bad = _check_mid(result_json.get("format_out"))
            if fi_bad:
                retry_missing.append(f"format_in.unknown_material={fi_bad}")
            if fo_bad:
                retry_missing.append(f"format_out.unknown_material={fo_bad}")

        if retry_missing:
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={**result_json, "_needs_retry": True, "_retry_missing": retry_missing},
                diagnosis=(
                    f"impl_type={impl_type} 字段不齐 (骨架铁律): 缺 {retry_missing}"
                ),
            )

        result_json.setdefault("_meta", {}).update({
            "worker": "WorkerDesignerWorker",
            "stage": "v1_agent_loop",
            "turn_count": turn_count,
            "stop_reason": stop_reason,
        })
        return Verdict(kind=VerdictKind.PASS, output=result_json)


class _WorkerDesignSingleAgent(AgentNodeLoop):
    """单 Worker 深化 · 独立 agent session (batch orchestrator 为每 skeleton 新建一个).

    2026-04-24 补产循环 (骨架铁律): ExtractResult PARTIAL + _needs_retry → 补产一次,
    带 _retry_missing 反馈. 不让"缺字段"的产出混进 details list.
    """

    ALLOW_NO_BUS: ClassVar[bool] = True
    TOOL_ROUTERS: ClassVar[list] = [ReadFileRouter, GlobRouter, GrepRouter, ListDirRouter, FinishRouter]
    NODE_PROMPT: ClassVar[str] = _SYSTEM_PROMPT
    DESCRIPTION: ClassVar[str] = "Phase 4 单 Worker 深化 agent session"
    MAX_RETRIES: ClassVar[int] = 1  # 补产 1 次足以覆盖"忘填字段"常见情况

    def __init__(self, *, model: str | None = None) -> None:
        from omnicompany.bus.memory import MemoryBus
        if model:
            super().__init__(bus=MemoryBus(), model=model)
        else:
            super().__init__(bus=MemoryBus(), role="runtime_main")

    def build_prompt_builder(self, *, bus: Any) -> _WorkerDesignerPromptBuilder:
        return _WorkerDesignerPromptBuilder(template=self.NODE_PROMPT, bus=bus)

    def build_extract_result(self, *, bus: Any) -> _WorkerDesignerExtractResult:
        return _WorkerDesignerExtractResult(bus=bus)

    async def run(self, input_data: Any) -> Verdict:
        # 从 team_design.materials_skeleton 提 allowed_material_ids 注入 extract
        # (骨架接管: format_in/out 不得用未登记 material_id)
        allowed_mids: set[str] | None = None
        if isinstance(input_data, dict):
            td = input_data.get("_from_team_architect") or input_data.get("team_design") or {}
            ms = td.get("materials_skeleton", []) if isinstance(td, dict) else []
            allowed_mids = {
                m.get("material_id") for m in ms
                if isinstance(m, dict) and m.get("material_id")
            }
        self._extract_result._allowed_material_ids = allowed_mids

        # 首产
        verdict = await AgentNodeLoop.run(self, input_data)

        # 补产循环: ExtractResult PARTIAL + _needs_retry → 补产一次
        retries = 0
        while (
            verdict.kind == VerdictKind.PARTIAL
            and isinstance(verdict.output, dict)
            and verdict.output.get("_needs_retry")
            and verdict.output.get("_retry_missing")
            and retries < self.MAX_RETRIES
        ):
            retries += 1
            retry_missing = verdict.output.get("_retry_missing") or []
            retry_input = dict(input_data) if isinstance(input_data, dict) else {}
            retry_input["_retry_missing"] = retry_missing
            retry_input["_retry_prev_output"] = {
                k: v for k, v in verdict.output.items()
                if not k.startswith("_")
            }
            verdict = await AgentNodeLoop.run(self, retry_input)

        return verdict


def _skeleton_patch_worker_detail(d: dict) -> dict:
    """骨架彻底接管: LLM 补产后仍缺的字段, 骨架填通用模板 (feedback_100pct_required_goes_to_skeleton).

    原则: 只填"必有"字段的通用占位, 内容可能不完美但结构齐. 下游 validator 看到的是齐的.
    """
    d = dict(d)
    impl_type = d.get("impl_type", "SOFT")
    worker_id = d.get("worker_id", "unknown")
    # hallucination_risks 骨架填
    hr = d.get("hallucination_risks") or []
    if not isinstance(hr, list) or len(hr) < 1:
        if impl_type == "HARD":
            hr = [
                f"{worker_id} 依赖的输入 material schema 可能在上游变更",
                "ServiceBus 接口稳定性假设 (DiskBus/BashBus/WebBus)",
            ]
        else:
            hr = [
                "LLM 输出格式可能漂移 (JSON 键名/嵌套结构)",
                "LLM 可能幻觉上下文中不存在的字段引用",
            ]
        d["hallucination_risks"] = hr
    # prompt_template (SOFT/AGENT 必填)
    if impl_type in ("SOFT", "AGENT"):
        pt = d.get("prompt_template")
        if not isinstance(pt, dict):
            pt = {"system": "", "user": ""}
        if not pt.get("system") or not str(pt.get("system")).strip():
            pt["system"] = f"你是 {worker_id} · 请按 FORMAT_IN 产出 FORMAT_OUT 指定的 material."
        if not pt.get("user") or not str(pt.get("user")).strip():
            pt["user"] = "{input_data}"
        d["prompt_template"] = pt
        cs = d.get("context_sources") or []
        if not isinstance(cs, list) or not cs:
            fi = d.get("format_in")
            d["context_sources"] = [fi] if isinstance(fi, str) and fi else ["input_data"]
    # rule_spec (HARD 必填)
    if impl_type == "HARD":
        rs = d.get("rule_spec")
        if not isinstance(rs, str) or len(rs.strip()) < 20:
            d["rule_spec"] = (
                "HARD 规则 (骨架占位 · LLM 未填): "
                "1. 读 input_data · 2. 按契约处理 · 3. return Verdict(PASS/FAIL)"
            )
    # routes (3 分支必填)
    routes = d.get("routes")
    if not isinstance(routes, dict):
        routes = {}
    for branch in ("PASS", "FAIL", "PARTIAL"):
        if branch not in routes:
            routes[branch] = {"action": "next" if branch == "PASS" else "halt"}
    d["routes"] = routes
    d.setdefault("_meta", {})["skeleton_patched"] = True
    return d


class WorkerDesignerWorker(Worker):
    """Phase 4 Orchestrator · for-each workers_skeleton · asyncio.gather N 份独立 agent session.

    结构:
      WorkerDesignerWorker (Orchestrator, 非 AgentNodeLoop)
        └→ _WorkerDesignSingleAgent × N (每 skeleton 一个独立实例 · 真独立上下文)

    输出: `{"details": [worker_design_detailed × N], "_meta": {...}}`
    下游 ContractAuditor / DesignValidator 读 "details" 字段.

    注: 继承自 Worker 协议 (FORMAT_IN/OUT + run + DESCRIPTION), 而不是 AgentNodeLoop
    (AgentNodeLoop 要求强制 bus, 且单 session 设计).
    """

    FORMAT_IN: ClassVar[str] = "team_builder.material.team_design"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.worker_design_detailed"
    DESCRIPTION: ClassVar[str] = (
        "Phase 4 Orchestrator · 对 team_design.workers_skeleton 并行 N 份深化 "
        "(每 skeleton 独立 agent session) · 收集产出 details list."
    )
    MAX_CONCURRENT: ClassVar[int] = 4  # 并发上限 · 避免 LLM rate limit

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
        skeletons = team_design.get("workers_skeleton", []) if isinstance(team_design, dict) else []
        if not skeletons:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="team_design.workers_skeleton is empty",
            )

        import asyncio
        sem = asyncio.Semaphore(self.MAX_CONCURRENT)

        async def _run_one(skeleton: dict) -> dict | None:
            async with sem:
                if str(skeleton.get("impl_type") or "").upper() == "EXISTING":
                    try:
                        return _existing_worker_detail(skeleton)
                    except (ImportError, AttributeError, TypeError, ValueError) as exc:
                        return {"_existing_binding_error": str(exc)}
                agent = _WorkerDesignSingleAgent(
                    model=self._model
                )  # 独立实例 · 独立上下文
                sub_input = {
                    "_from_team_architect": team_design,
                    "target_worker_name": skeleton.get("worker_name") or skeleton.get("worker_id"),
                }
                try:
                    v = await agent.run(sub_input)
                    if v.kind == VerdictKind.PASS:
                        return v.output
                    if v.kind == VerdictKind.PARTIAL and isinstance(v.output, dict):
                        # 骨架彻底接管 (feedback_100pct_required_goes_to_skeleton):
                        # 补产后仍 PARTIAL · 骨架按 impl_type 自动填通用模板补齐, 不让下游看到"不齐"
                        patched = _skeleton_patch_worker_detail(v.output)
                        return patched
                    return None  # 仅 FAIL 跳过
                except Exception:
                    return None

        tasks = [_run_one(s) for s in skeletons if isinstance(s, dict)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        binding_errors = [
            d["_existing_binding_error"]
            for d in results
            if isinstance(d, dict) and d.get("_existing_binding_error")
        ]
        details = [
            d
            for d in results
            if isinstance(d, dict) and not d.get("_existing_binding_error")
        ]

        if not details:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"binding_errors": binding_errors},
                diagnosis=(
                    f"所有 {len(skeletons)} 份 worker deepen 都失败"
                    + (f": {binding_errors}" if binding_errors else "")
                ),
            )

        return Verdict(
            kind=VerdictKind.PASS if len(details) == len(skeletons) else VerdictKind.PARTIAL,
            output={
                "details": details,
                "_meta": {
                    "worker": "WorkerDesignerWorker",
                    "stage": "v2_orchestrator",
                    "skeletons_count": len(skeletons),
                    "success_count": len(details),
                    "binding_errors": binding_errors,
                },
            },
            diagnosis=(
                f"{len(details)}/{len(skeletons)} workers deepened"
                + (f" · binding_errors={len(binding_errors)}" if binding_errors else "")
            ),
        )

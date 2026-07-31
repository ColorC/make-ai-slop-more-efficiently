# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.team_blueprint_planner.worker.py"
"""TeamArchitectWorker — agent-first 第三阶段 (2026-04-23).

Worker 协议 (composite fan-in):
  FORMAT_IN  = [team_builder.material.intent_analysis, team_builder.material.team_references]
  FORMAT_IN_MODE = "and"
  FORMAT_OUT = team_builder.material.team_design

**职责**: 独立上下文 · 综合 intent + references, 规划 Team 总体骨架.
产出 team_design (DESIGN.md 七节内容) · 同时链式产出 workspace_design (workspace.yaml)
+ worker_design 骨架清单 (各 worker 名 + 职责粗描, 后续由 WorkerDesigner × N 深化).

**独立上下文理由** (agent-first 方法论):
  - 接收两份 material 作输入 (intent + refs), 走独立 session 更干净
  - 深综合认知任务 · prompt 不被混入前阶段无关细节 (如 IntentAnalyzer 的 scope 推理过程)
  - 产出多类 material, 每类独立下游消费

**实现状态**:
  - V0: SOFT worker (一次大 LLM 调用产出 team_design + 衍生 material 骨架)
  - 观测后若发现一次调用 token 爆 / 质量差, 拆成 TeamDesigner + WorkspaceDesigner 两 worker
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._core.team_builder.team_design_contract import (
    finalize_team_design,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.buses import WebBus

from ._llm_client import call_llm_json


_SYSTEM_PROMPT = """你是 team_builder 团队中的 TeamArchitect · agent-first 第三阶段.

职责: 综合上游两份 material (intent_analysis + team_references), **规划产出 Team 总体骨架**.

**输入**:
- team_builder.material.intent_analysis: domain / purpose / key_capabilities / constraints / ambiguities
- team_builder.material.team_references: 参考清单 (standards / similar_team / bus infra)

**产出 team_design JSON**:
- team_id: TeamSpec.id (kebab-case, 必填；与 Python package 名分开)
- team_name: 产出 Team 的 package 名 (snake_case, 必填, 例 "csv_to_md_pipeline")
- target_package_path: 目标 package 的仓库相对目录 (必填):
  - 业务私域: `src/omnicompany/packages/domains/<domain>/<team_name>/`
  - 跨业务设施: `src/omnicompany/packages/services/<canonical_bucket>/<team_name>/`
  - canonical_bucket 只可为 `_core|_diagnosis|_authoring|_learning|_utility`
  - intent 已明确给出时必须原样复用；不得退回平铺的 `services/<team_name>`
- design_path / workspace_skeleton: 由确定性路径合同生成，不要另立路径
- sections: **OMNI-034 标准七节** - 必须**一字不差**用这七个标题:
  ["状态", "核心目的", "核心接口", "架构规则", "数据流", "已知局限", "参考资料"]
- node_count: 预估 Worker 数量
- material_count: 预估 Material 数量
- positions: list[{id, name, responsibilities, non_responsibilities, activation,
  activation_evidence_refs, context_refs, benchmark_refs, exit_conditions}]
  — Team 内岗位职责；不得包含 model/prompt/tools/permissions/budget 等 AgentSpec 字段
- workers_skeleton: list[{worker_name, impl_type: HARD|SOFT|AGENT|EXISTING, brief,
  position_id, binding_ref?, binding_kwargs?}]
  — 每个 Worker 必须映射 positions[].id；下游 WorkerDesigner 深化
- materials_skeleton: list[{material_id, brief}] — 下游 MaterialDesigner 深化
**命名铁律** (下游 WorkerDesigner / MaterialDesigner 会**严格复用** skeleton.material_id / worker_name):
- material_id 必须能作为 FORMAT event_type 使用 · 小写+点号分隔 · 例 "<team_name>.raw_matrix" 而非 "mat_raw_matrix"
- 不要用带 "mat_" 前缀的命名 (会让下游命名与 skeleton 不一致)

**原则**:
- P-13 充分性 (每 Worker FORMAT_IN 必有 producer · FORMAT_OUT 必有 consumer 或 sink)
- F-15 诚实 (Material 描述不搭便车)
- OMNI-034 七节 DESIGN.md
- 命名 B 层 (Material/Worker/Team)
- 铁律 A (无预防截断) + 铁律 B (预算宽松)
- **路径单一权威**: 只声明 target_package_path；不得在其他字段自造第二套路径
- **既有设施优先**: references 中已有 Worker 足以承担节点时，必须用
  `impl_type="EXISTING"` + `binding_ref="<python.module>:<WorkerClass>"`；不得再生成
  一套同义 HARD/SOFT/AGENT Worker。`binding_kwargs` 不得包含 model/prompt/tools/
  permissions/budget；这些仍由 AgentSpec 或运行输入管理。
- 当前可复用的 Agent 执行节点是
  `omnicompany.packages.services._core.agent.external_workers.routers.workflow_node:ExternalAgentWorkerNode`。
  它的 FORMAT_IN/OUT 由确定性代码从真实类读取，不由 LLM 猜。
- 若所有节点均为 EXISTING，`materials_skeleton` 必须是空列表；不得复制既有 Worker
  的 FORMAT 或 Material 契约。

**不要做**:
- 不深化 Worker 细节 (WorkerDesigner 做)
- 不写代码 (CodeGenerator 做)
- 不虚构 references 外的能力 (诚实第一)
- 不创建 RoleSpec / ProjectTeam / AgentTeam；所有产物最终都是同一个 TeamSpec
- 不把明确的 team_id 改写成 package 名；team_id 可带连字符，team_name 必须下划线
- 不自行决定 generation.builder_id；生成溯源由确定性代码接管
- 不确定 service bucket 或 business domain 时不要猜；缺 target_package_path 会由合同拒绝并回到用户
- **不自造带前缀的 material_id** (下游会严格复用 skeleton.material_id, 自造会跨层不一致)"""


def _build_user_prompt(intent: dict, refs: dict) -> str:
    intent_str = _pretty_json(intent)
    refs_list = refs.get("references", [])
    refs_str = "\n".join(f"  - [{r.get('kind', '?')}] {r.get('source_path', '')}: {r.get('reason', '')}"
                        for r in refs_list)
    return (
        f"# intent_analysis\n\n{intent_str}\n\n"
        f"---\n\n"
        f"# team_references ({len(refs_list)} 条)\n\n{refs_str}\n\n"
        f"请输出 JSON 格式的 team_design."
    )


def _pretty_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


class TeamArchitectWorker(Worker):
    """独立上下文 LLM · 综合 intent + refs 规划 Team 总体骨架."""

    DESCRIPTION = (
        "agent-first 第三阶段 · 独立上下文综合 intent_analysis + team_references, "
        "产出 team_design 总体骨架 (七节 DESIGN + worker/material skeleton + workspace)."
    )
    FORMAT_IN = ["team_builder.material.intent_analysis", "team_builder.material.team_references"]
    FORMAT_IN_MODE = "and"  # composite fan-in · 两份 material 到齐才激活
    FORMAT_OUT = "team_builder.material.team_design"

    def __init__(self, *, web_bus: WebBus | None = None, model: str | None = None, max_tokens: int = 32000):
        self._web_bus = web_bus
        self._model = model
        self._max_tokens = max_tokens  # TeamArchitect 输出可能大, 宽松 (铁律 B)

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )

        # composite fan-in · runner 把两份 material 的字段**平铺**到 input_data top-level,
        # 同时保留 `_from_<producer>` 子 dict. 优先用 `_from_<producer>`, 回退平铺字段.
        intent = input_data.get("_from_intent_analyzer")
        if intent is None:
            # 从平铺字段聚合
            intent_keys = ("domain", "purpose", "scope", "key_capabilities", "constraints", "ambiguities")
            intent = {k: input_data[k] for k in intent_keys if k in input_data}
        refs = input_data.get("_from_reference_scout")
        if refs is None:
            refs = {
                "references": input_data.get("references", []),
                "body_path": input_data.get("body_path", ""),
            }
        if not intent or not refs.get("references"):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"fan-in missing · intent_keys={list(intent.keys()) if isinstance(intent, dict) else None}, refs={len(refs.get('references', []))}",
            )

        user_prompt = _build_user_prompt(intent, refs)
        try:
            parsed = call_llm_json(
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                web_bus=self._web_bus,
                caller="team_builder.team_architect",
                model=self._model,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"LLM call failed: {type(e).__name__}: {e}",
            )

        if "_parse_error" in parsed:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=parsed,
                diagnosis=f"LLM output not JSON: {parsed['_parse_error']}",
            )

        # 骨架接管 sections (feedback_100pct_required_goes_to_skeleton · 2026-04-24):
        # OMNI-034 七节是**确定性常量**, 不该靠 LLM 自觉填准.
        # 骨架直接覆盖为规范名 (权威见 guardian/rules/design_md.py::_REQUIRED_SECTIONS)
        _OMNI_034_CANONICAL_SECTIONS = [
            "状态", "核心目的", "核心接口", "架构决策", "数据流 / 拓扑", "已知局限", "参考资料",
        ]
        llm_sections = parsed.get("sections") or []
        if llm_sections != _OMNI_034_CANONICAL_SECTIONS:
            parsed["sections"] = list(_OMNI_034_CANONICAL_SECTIONS)
            parsed.setdefault("_meta", {})["sections_override"] = {
                "llm": llm_sections, "canonical": _OMNI_034_CANONICAL_SECTIONS,
            }

        try:
            parsed = finalize_team_design(parsed, intent=intent, references=refs)
        except (TypeError, ValueError) as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=parsed,
                diagnosis=f"team design violates canonical TeamSpec contract: {exc}",
            )

        parsed.setdefault("_meta", {}).update(
            {
                "worker": "TeamArchitectWorker",
                "stage": "v1_llm",
                "prompt_chars": len(user_prompt),
                "input_domain": intent.get("domain") if isinstance(intent, dict) else None,
                "refs_count": len(refs.get("references", [])) if isinstance(refs, dict) else 0,
            }
        )
        return Verdict(
            kind=VerdictKind.PASS,
            output=parsed,
        )

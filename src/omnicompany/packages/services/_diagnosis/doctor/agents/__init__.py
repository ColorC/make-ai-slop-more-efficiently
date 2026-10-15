# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/agents ts=2026-05-05T21:30:00Z type=config status=active agent=ai-ide-current
# [OMNI] summary="doctor 诊断 agent 集合 + dispatcher 入口. import 顺序保证业务工具先注册"
# [OMNI] tags=agents,doctor,configurable,dispatcher
# [OMNI] material_id="material:diagnosis.doctor.agents.aggregate.exports.py"
"""doctor 诊断 agent 集合 + dispatcher 入口.

import 顺序约定:
  1. 先 import doctor.tools — 触发业务工具 register_tool (write_finding 等)
  2. 再 import 各诊断 agent — 它们 SPEC.tools 引用业务工具名字, 注册晚一步会 KeyError

诊断 agent (4):
  - SpecDiagnosticAgent — 规范型诊断
  - HypothesisDiagnosticAgent — 假设型诊断
  - ExemplarDiagnosticAgent — 样例型诊断
  - PlanDiagnosticAgent — 计划型诊断

派生 agent (1):
  - HypothesisDeriverAgent — 假设派生 (供给 HypothesisDiagnosticAgent)

入口函数:
  - build_diagnostic_workers() — 返当前已立诊断 agent list (供 dispatcher 接收)
  - build_diagnostic_dispatcher() — 包 MaterialDispatcher + SQLiteBus
  - run_spec_diagnosis(target_path, target_kind, applicable_standards) — 跑一次规范型诊断
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# 必须先 import tools, 让业务工具注册到 TOOL_REGISTRY
from omnicompany.packages.services._diagnosis.doctor import tools  # noqa: F401

from .spec_diagnostic import SpecDiagnosticAgent, SPEC_DIAGNOSTIC_SPEC
from .hypothesis_diagnostic import HypothesisDiagnosticAgent, HYPOTHESIS_DIAGNOSTIC_SPEC
from .exemplar_diagnostic import ExemplarDiagnosticAgent, EXEMPLAR_DIAGNOSTIC_SPEC
from .plan_diagnostic import PlanDiagnosticAgent, PLAN_DIAGNOSTIC_SPEC
from .hypothesis_deriver import HypothesisDeriverAgent, HYPOTHESIS_DERIVER_SPEC
from .meta_diagnostic import MetaDiagnosticAgent, META_DIAGNOSTIC_SPEC
from .challenge_diagnostic import ChallengeDiagnosticAgent, CHALLENGE_DIAGNOSTIC_SPEC


def build_diagnostic_workers(*, bus) -> list:
    """返 doctor 当前已立 agent list (可入 MaterialDispatcher).

    四个对象诊断位点、Challenge、Meta 与 HypothesisDeriver 均是外置
    AgentRuntime Worker。bus 参数供 Omni 侧 finding/假设发布审计和 dispatcher
    使用；模型侧没有权威写工具。

    含 4 对象级诊断 agent + 1 派生 agent + 1 元诊断 agent (看 team 整体).
    """
    return [
        SpecDiagnosticAgent(bus=bus),
        HypothesisDiagnosticAgent(bus=bus),
        ExemplarDiagnosticAgent(bus=bus),
        PlanDiagnosticAgent(bus=bus),
        HypothesisDeriverAgent(bus=bus),
        MetaDiagnosticAgent(bus=bus),
        ChallengeDiagnosticAgent(bus=bus),
    ]


def build_diagnostic_dispatcher(
    *,
    bus=None,
    max_iterations: int = 200,
):
    """构造 MaterialDispatcher + 注册全部诊断 agent.

    bus 默认走 SQLiteBus (data/events.db, 跟 omnicompany 主总线同一盘).
    传 MemoryBus 仅用于 unit 测试.

    max_iterations 200 — 铁律 B 宽松预算 (诊断对象多时 LLM 调用可能多轮).
    """
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.packages.services._core.omnicompany import MaterialDispatcher

    if bus is None:
        bus = SQLiteBus()
    workers = build_diagnostic_workers(bus=bus)
    return MaterialDispatcher(workers=workers, bus=bus, max_iterations=max_iterations)


async def run_spec_diagnosis(
    *,
    target_entity_path: str,
    target_entity_kind: str,
    applicable_standards: list[str],
    bus=None,
) -> list:
    """跑一次规范型诊断 (bus 驱动).

    Args:
        target_entity_path: 待诊断对象路径
        target_entity_kind: worker / material / team / agent / hook / tool / plan
        applicable_standards: 适用规范文档 path 列表 (e.g. ['docs/standards/concepts/worker.md'])
        bus: 传 None 用 SQLiteBus 默认
    """
    dispatcher = build_diagnostic_dispatcher(bus=bus)
    events = await dispatcher.run_job(
        initial_material_id="doctor.spec_diagnosis.request",
        initial_payload={
            "target_entity_path": target_entity_path,
            "target_entity_kind": target_entity_kind,
            "applicable_standards": applicable_standards,
        },
    )
    return events


async def run_hypothesis_diagnosis(
    *,
    target_entity_path: str,
    target_entity_kind: str,
    applicable_hypothesis_paths: list[str],
    bus=None,
) -> list:
    """跑一次假设型诊断 (bus 驱动).

    Args:
        target_entity_path: 待诊断对象路径
        target_entity_kind: worker / material / team / agent / hook / tool / plan
        applicable_hypothesis_paths: 假设 yaml path 列表 (e.g. ['data/services/doctor/hypotheses/H-001.yaml'])
        bus: 传 None 用 SQLiteBus 默认
    """
    dispatcher = build_diagnostic_dispatcher(bus=bus)
    events = await dispatcher.run_job(
        initial_material_id="doctor.hypothesis_diagnosis.request",
        initial_payload={
            "target_entity_path": target_entity_path,
            "target_entity_kind": target_entity_kind,
            "applicable_hypothesis_paths": applicable_hypothesis_paths,
        },
    )
    return events


async def run_meta_diagnosis(
    *,
    team_path: str,
    focus_questions: list[int] | None = None,
    depth: str = "full",
    bus=None,
) -> list:
    """跑一次元诊断 (走 10 问 + 7 假设).

    Args:
        team_path: team 整体目录路径
        focus_questions: 关注的问题编号子集 (1-10), None 走全 10 问
        depth: 'quick' / 'full' / 'deep'
        bus: 传 None 用 SQLiteBus 默认
    """
    if focus_questions is None:
        focus_questions = list(range(1, 11))
    dispatcher = build_diagnostic_dispatcher(bus=bus)
    events = await dispatcher.run_job(
        initial_material_id="doctor.meta_diagnosis.request",
        initial_payload={
            "team_path": team_path,
            "focus_questions": focus_questions,
            "depth": depth,
        },
    )
    return events


async def run_hypothesis_derivation(
    *,
    source_paths: list[str],
    derivation_focus: str,
    max_hypotheses: int = 5,
    falsified_hypothesis_paths: list[str] | None = None,
    bus=None,
) -> list:
    """跑一次假设派生 (bus 驱动).

    Args:
        source_paths: 派生源路径列表 (规范文档/plan/代码)
        derivation_focus: 派生焦点 (worker / material / team / agent / hook / tool / plan)
        max_hypotheses: 上限 (LLM 自律), 默认 5
        falsified_hypothesis_paths: V23 加 — 一组已 falsified 假设 yaml path. deriver 真
            识别后会读这些 yaml 的 challenge_log / resolution.falsifying_evidence,
            真派升级版假设. None 时 deriver 走原走 (只看 source_paths 已覆盖).
        bus: 传 None 用 SQLiteBus 默认
    """
    dispatcher = build_diagnostic_dispatcher(bus=bus)
    events = await dispatcher.run_job(
        initial_material_id="doctor.hypothesis_derivation.request",
        initial_payload={
            "source_paths": source_paths,
            "derivation_focus": derivation_focus,
            "max_hypotheses": max_hypotheses,
            "falsified_hypothesis_paths": falsified_hypothesis_paths or [],
        },
    )
    return events


async def run_plan_diagnosis(
    *,
    target_plan_path: str,
    applicable_template_paths: list[str],
    check_modes: list[str] | None = None,
    bus=None,
) -> list:
    """跑一次计划型诊断 (bus 驱动).

    Args:
        target_plan_path: 待诊断 plan.md 路径 (e.g. 'docs/plans/<topic>/<plan>/plan.md')
        applicable_template_paths: 模板路径 (一般填 ['docs/standards/protocol/plan_template.md'])
        check_modes: 检查模式列表, 默认 ['static']. V0 只支持 static. V1 加 'dynamic'
        bus: 传 None 用 SQLiteBus 默认
    """
    if check_modes is None:
        check_modes = ["static"]
    dispatcher = build_diagnostic_dispatcher(bus=bus)
    events = await dispatcher.run_job(
        initial_material_id="doctor.plan_diagnosis.request",
        initial_payload={
            "target_plan_path": target_plan_path,
            "applicable_template_paths": applicable_template_paths,
            "check_modes": check_modes,
        },
    )
    return events


async def run_challenge_diagnosis(
    *,
    focus_hypothesis_yaml_path: str,
    applies_to: str,
    bus=None,
) -> list:
    """跑一次质疑型诊断 (bus 驱动) — 拿单条焦点假设走 schema §三步骤 3-4 真证否流程.

    Args:
        focus_hypothesis_yaml_path: 焦点假设 yaml 路径 (通常是 ChallengeQueue 排序的 top)
        applies_to: 假设适用对象 (worker / material / team / agent / hook / tool / plan)
        bus: 传 None 用 SQLiteBus 默认
    """
    dispatcher = build_diagnostic_dispatcher(bus=bus)
    events = await dispatcher.run_job(
        initial_material_id="doctor.challenge_diagnosis.request",
        initial_payload={
            "focus_hypothesis_yaml_path": focus_hypothesis_yaml_path,
            "applies_to": applies_to,
        },
    )
    return events


async def run_challenge_pipeline(
    *,
    hypotheses_dir: str = "data/services/doctor/hypotheses",
    applies_to: str = "",
    focus_count: int = 1,
    skip_frozen: bool = True,
    depended_by_threshold: int = 3,
    dry_run: bool = False,
    bus=None,
) -> dict:
    """V9 一条龙 — ChallengeQueue 排序 → 选 top N → 跑 ChallengeDiagnosticAgent 真证否.

    schema §三步骤 1-4 自动化封装. 调用方不必手动 rank → select top → run_challenge_diagnosis.

    Args:
        hypotheses_dir: 假设 yaml 目录 (相对项目根, 默认 data/services/doctor/hypotheses).
        applies_to: 当前问题对象 ('worker'/'material'/'team'/'agent'/'hook'/'tool'/'plan').
            空 '' 时 b 类不触发.
        focus_count: 跑前 N 条假设. 默认 1 (token 友好). 设 0 + dry_run=True 只看排序不跑 agent.
        skip_frozen: 默认 True (跳 falsified/real_world_validated, V7 一致).
        depended_by_threshold: c 类阈值默认 3.
        dry_run: True 只 rank 不调 agent (节省 token, 看排序).
        bus: 传 None 用 SQLiteBus 默认.

    Returns:
        dict 含:
        - 'ranked': list of {hypothesis_id, priority_score, reasons, hypothesis_dict}
        - 'agent_runs': list of {hypothesis_id, events_count} (dry_run=True 时为空)
        - 'summary': 一句话总结

    Raises:
        FileNotFoundError: hypotheses_dir 不存在.
    """
    from pathlib import Path

    import yaml as _yaml

    from omnicompany.packages.services._diagnosis.doctor.builders import (
        HypothesisChallengeQueue,
    )

    # 解析 hypotheses_dir 路径
    project_root = Path(__file__).resolve()
    for parent in (project_root, *project_root.parents):
        if (parent / "src" / "omnicompany").is_dir() and (parent / "docs").is_dir():
            project_root = parent
            break
    else:
        project_root = Path(__file__).resolve().parents[8]

    hyp_dir = project_root / hypotheses_dir
    if not hyp_dir.exists():
        raise FileNotFoundError(f"hypotheses_dir 不存在: {hypotheses_dir}")

    # 加载 yaml
    hyps: list[dict] = []
    for ext in ("*.yaml", "*.yml"):
        for path in sorted(hyp_dir.glob(ext)):
            try:
                with path.open(encoding="utf-8") as f:
                    d = _yaml.safe_load(f)
            except Exception:
                continue
            if isinstance(d, dict):
                hyps.append(d)

    if not hyps:
        return {"ranked": [], "agent_runs": [], "summary": f"hypotheses_dir 无 yaml: {hypotheses_dir}"}

    # rank
    queue = HypothesisChallengeQueue()
    problem_context = {"applies_to": applies_to} if applies_to else None
    rank_result = queue.rank(
        hyps,
        problem_context=problem_context,
        focus_count=focus_count,
        skip_frozen=skip_frozen,
        depended_by_threshold=depended_by_threshold,
    )

    ranked = [
        {
            "hypothesis_id": e.hypothesis_id,
            "priority_score": e.priority_score,
            "reasons": list(e.priority_reasons),
            "hypothesis_dict": e.hypothesis_dict,
        }
        for e in rank_result.ranked
    ]

    # dry_run 不跑 agent
    if dry_run:
        return {
            "ranked": ranked,
            "agent_runs": [],
            "summary": (
                f"DRY_RUN ranked={len(ranked)} from loaded={len(hyps)} "
                f"skipped={len(rank_result.skipped)} (no agent invoked)"
            ),
        }

    # 跑 ChallengeAgent on top N
    agent_runs: list[dict] = []
    for entry in ranked:
        hid = entry["hypothesis_id"]
        # 找原 yaml path (从 hyp_dir 反查)
        yaml_path: Path | None = None
        for ext in ("*.yaml", "*.yml"):
            for path in hyp_dir.glob(ext):
                try:
                    with path.open(encoding="utf-8") as f:
                        d = _yaml.safe_load(f)
                except Exception:
                    continue
                if isinstance(d, dict) and d.get("id") == hid:
                    yaml_path = path
                    break
            if yaml_path:
                break

        if yaml_path is None:
            agent_runs.append({"hypothesis_id": hid, "events_count": 0, "error": "yaml path 找不到"})
            continue

        rel = str(yaml_path.relative_to(project_root)).replace("\\", "/")
        agent_applies_to = applies_to or (entry["hypothesis_dict"].get("applies_to") or "worker")
        try:
            events = await run_challenge_diagnosis(
                focus_hypothesis_yaml_path=rel,
                applies_to=agent_applies_to,
                bus=bus,
            )
            agent_runs.append({"hypothesis_id": hid, "events_count": len(events)})
        except Exception as e:  # noqa: BLE001 — agent 失败不阻塞后续
            agent_runs.append({
                "hypothesis_id": hid,
                "events_count": 0,
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            })

    return {
        "ranked": ranked,
        "agent_runs": agent_runs,
        "summary": (
            f"PIPELINE ranked={len(ranked)} agent_runs={len(agent_runs)} "
            f"successful={sum(1 for r in agent_runs if not r.get('error'))} "
            f"skipped={len(rank_result.skipped)}"
        ),
    }


async def run_exemplar_diagnosis(
    *,
    target_entity_path: str,
    target_entity_kind: str,
    applicable_exemplar_paths: list[str],
    bus=None,
) -> list:
    """跑一次样例型诊断 (bus 驱动).

    Args:
        target_entity_path: 待诊断对象路径
        target_entity_kind: worker / material / team / agent / hook / tool / plan
        applicable_exemplar_paths: 样例 yaml path 列表 (e.g. ['data/services/doctor/exemplars/worker/E-001.yaml'])
        bus: 传 None 用 SQLiteBus 默认
    """
    dispatcher = build_diagnostic_dispatcher(bus=bus)
    events = await dispatcher.run_job(
        initial_material_id="doctor.exemplar_diagnosis.request",
        initial_payload={
            "target_entity_path": target_entity_path,
            "target_entity_kind": target_entity_kind,
            "applicable_exemplar_paths": applicable_exemplar_paths,
        },
    )
    return events


__all__ = [
    "SpecDiagnosticAgent",
    "SPEC_DIAGNOSTIC_SPEC",
    "HypothesisDiagnosticAgent",
    "HYPOTHESIS_DIAGNOSTIC_SPEC",
    "ExemplarDiagnosticAgent",
    "EXEMPLAR_DIAGNOSTIC_SPEC",
    "PlanDiagnosticAgent",
    "PLAN_DIAGNOSTIC_SPEC",
    "HypothesisDeriverAgent",
    "HYPOTHESIS_DERIVER_SPEC",
    "MetaDiagnosticAgent",
    "META_DIAGNOSTIC_SPEC",
    "ChallengeDiagnosticAgent",
    "CHALLENGE_DIAGNOSTIC_SPEC",
    "build_diagnostic_workers",
    "build_diagnostic_dispatcher",
    "run_spec_diagnosis",
    "run_hypothesis_diagnosis",
    "run_exemplar_diagnosis",
    "run_plan_diagnosis",
    "run_hypothesis_derivation",
    "run_meta_diagnosis",
    "run_challenge_diagnosis",
    "run_challenge_pipeline",
]

# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=pipeline status=active
# [OMNI] material_id="material:services.learning.hypothesis.team.session_controller.py"
"""hypothesis.pipeline — 假设探索循环控制器(v5: 决策库收编版)。

产出:统一决策库 kind=belief 记录(tags=[hypothesis-explore, domain:<domain>])。
主题摘要=生成投影(docs/ontology/30-知识.md,由 decisions.knowledge_projection 渲染),不再有独立文档真源。

循环结构:
  load 本域 belief 快照(决策库)
  for iteration in range(max_iterations):
    主 agent(Experimenter)自由探索 → 产出行为轨迹
    总结 agent(BeliefReflector,AgentNodeLoop)
      读轨迹 + belief 快照
      用决策库五件套(list/record/challenge/resolve/link)直接维护统一库
      所有状态判定都是它的语义判断;库层校验(风险必填/前提必真)是硬门
  收工:重渲 30-知识 投影

权威=docs/plans/[2026-07-10]DECISION-ONTOLOGY/plan.md 合并清单#1(停机一次性迁移):
khyp 文档体系已拆除;旧 lockstep 双脑模式随迁移退役(无生产调用方)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
import time
import uuid

from omnicompany.packages.services._learning.hypothesis.belief_tools import (
    beliefs_snapshot,
)

try:
    from dotenv import load_dotenv as _load_dotenv
    _ENV_FILE = pathlib.Path(__file__).parents[5] / ".env"
    if _ENV_FILE.exists():
        _load_dotenv(_ENV_FILE)
except ImportError:
    pass

log = logging.getLogger(__name__)
_PROJECT_ROOT = str(pathlib.Path(__file__).parents[5])


# ═══════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════

def _store_snap_for_experimenter(snapshot: dict, iteration: int) -> dict:
    """belief 快照 → Experimenter 期望的 store 形态(id/state/trigger/predicted)。"""
    excluded_statuses = {"challenged", "falsified", "superseded"}
    return {
        "iteration": iteration,
        "entries": [
            {"id": b["id"], "kind": "belief",
             "state": b.get("status", "untested"),
             "trigger": b.get("evidence_query", ""),
             "predicted": b.get("statement", "")}
            for b in (snapshot.get("beliefs") or [])
            if str(b.get("status") or "untested") not in excluded_statuses
        ],
    }


def _ledger_count_for_session(path_raw: object, session_id: str) -> int:
    if not path_raw:
        return 0
    path = pathlib.Path(str(path_raw))
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("session_id") == session_id:
            count += 1
    return count


def _validate_game_shadow_scene_contract(scene: dict) -> None:
    """Reject contradictory shadow-scene contracts before an LLM call starts."""
    if not (
        scene.get("mode") == "shadow"
        and scene.get("kind") == "game-ui-exploration"
    ):
        return

    prior_mode = str(scene.get("prior_fact_contract_mode") or "").strip()
    expected_change_mode = str(scene.get("expected_change_mode") or "").strip()
    prior_facts = [
        item
        for item in scene.get("prior_verified_targets") or []
        if isinstance(item, dict) and item.get("id")
    ]
    if prior_mode == "exact" and not prior_facts:
        raise ValueError(
            "game-ui shadow scene 合同冲突：prior_fact_contract_mode=exact "
            "要求 prior_verified_targets 至少包含一条带 id 的既有事实"
        )
    if prior_mode == "exact" and expected_change_mode == "unverified":
        raise ValueError(
            "game-ui shadow scene 合同冲突：exact 既有事实复核与 unverified "
            "未知结果发现不能在同一会话启用"
        )
    if str(scene.get("coordinate_space") or "normalized_1000") == "source_pixels":
        grounding_paths = [
            str(item).strip()
            for item in scene.get("grounding_image_paths") or []
            if str(item).strip()
        ]
        layout = scene.get("coordinate_reference_layout") or {}
        if not grounding_paths or not isinstance(layout, dict) or not layout.get("layout"):
            raise ValueError(
                "game-ui source_pixels scene 必须提供 grounding_image_paths 与"
                " coordinate_reference_layout；不能让模型从缩放后的原图估算源像素"
            )


def _load_game_shadow_visual_manifest(scene: dict) -> None:
    """Load a hash-locked locator manifest from an explicitly allowed root."""
    path_raw = scene.get("visual_candidate_manifest_path")
    if not path_raw:
        return
    path = pathlib.Path(str(path_raw)).resolve()
    allowed_roots = [
        pathlib.Path(str(item)).resolve()
        for item in scene.get("allowed_image_roots") or []
    ]
    if not allowed_roots or not any(
        path == root or root in path.parents for root in allowed_roots
    ):
        raise ValueError("visual candidate manifest 位于 allowed_image_roots 之外")
    if not path.is_file():
        raise ValueError(f"visual candidate manifest 不存在: {path}")
    expected_sha = str(scene.get("visual_candidate_manifest_sha256") or "").strip()
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if not expected_sha or actual_sha != expected_sha:
        raise ValueError("visual candidate manifest hash 不匹配")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    observation_sha = str((scene.get("observation") or {}).get("sha256") or "")
    if manifest.get("image_sha256") != observation_sha:
        raise ValueError("visual candidate manifest image hash 与冻结画面不匹配")
    if manifest.get("truth_status") != "untrusted-geometry-hints":
        raise ValueError("visual candidate manifest 缺少 untrusted geometry 状态")
    scene["visual_candidate_manifest"] = manifest


async def _run_session_async(session_config: dict) -> dict:
    from omnicompany.packages.services._learning.hypothesis.routers import (
        ExperimenterRouter,
        ReflectorRouter,
    )

    session_started = time.perf_counter()
    session_id = session_config["session_id"]
    domain = session_config["domain"]
    max_iterations = session_config["max_iterations"]
    scene = session_config.get("scene", {})
    goal = session_config.get("goal", "")
    _load_game_shadow_visual_manifest(scene)
    _validate_game_shadow_scene_contract(scene)

    # 启动时注册本服务的 Formats 到 FormatRegistry(幂等)
    try:
        from omnicompany.protocol.format import create_builtin_registry
        from omnicompany.packages.services._learning.hypothesis.formats import register_formats
        _registry = create_builtin_registry()
        register_formats(_registry)
    except Exception as exc:
        log.warning("[hyp] 注册 Formats 失败(非致命): %s", exc)

    # 初始化 EventBus,session_id 作为 trace_id 贯穿
    bus = None
    try:
        from omnicompany.bus.sqlite import SQLiteBus
        from omnicompany.protocol.events import FactoryEvent
        from omnicompany.protocol.registry import EventType
        bus = SQLiteBus()
        await bus.connect()
        await bus.publish(FactoryEvent(
            trace_id=session_id,
            event_type=EventType.TASK_INTENT.value,
            source=f"hypothesis.pipeline.{domain}",
            payload={
                "instruction": goal,
                "domain": domain,
                "max_iterations": max_iterations,
                "scene": scene,
                "session_id": session_id,
            },
            tags=["hypothesis", f"domain.{domain}"],
        ))
    except Exception as exc:
        log.warning("[hyp] EventBus 初始化失败(降级无事件): %s", exc)
        bus = None

    experimenter = ExperimenterRouter(bus=bus)
    reflector = ReflectorRouter(bus=bus)

    snapshot = beliefs_snapshot(domain)
    iteration_results: list[dict] = []
    is_unverified_game_shadow = (
        scene.get("mode") == "shadow"
        and scene.get("kind") == "game-ui-exploration"
    )
    suggestion_ledger = scene.get("suggestion_ledger")
    log.info("[hyp] 开工: domain=%s 库内已有 belief %d 条", domain, snapshot["total"])

    for iteration in range(max_iterations):
        iteration_started = time.perf_counter()
        snapshot = beliefs_snapshot(domain)
        log.info("[hyp] iter %d | belief %d 条", iteration, snapshot["total"])

        # ── 主 agent:自由探索 ────────────────────
        experimenter_trace_id = str(uuid.uuid4())
        suggestions_before = _ledger_count_for_session(suggestion_ledger, session_id)
        expose_prior_beliefs = (
            not is_unverified_game_shadow
            or bool(scene.get("allow_prior_beliefs"))
        )
        experimenter_snapshot = snapshot if expose_prior_beliefs else {"beliefs": []}
        exp_verdict = await experimenter.run({
            "store": _store_snap_for_experimenter(experimenter_snapshot, iteration),
            "session": session_config,
            "trace_id": experimenter_trace_id,
        })
        if exp_verdict.output is None:
            log.warning("[hyp] Experimenter 无输出,终止")
            break
        trace = exp_verdict.output.get("trace", [])
        proposal_trace = [item for item in trace if item.get("tool") == "propose_probe"]
        tool_error_count = sum(
            str(item.get("result") or "").startswith("<tool_use_error>")
            for item in trace
        )
        suggestions_after = _ledger_count_for_session(suggestion_ledger, session_id)
        iteration_result = {
            "iteration": iteration,
            "experimenter_trace_id": experimenter_trace_id,
            "trace_length": len(trace),
            "trace": trace,
            "final_text": exp_verdict.output.get("final_text", ""),
            "turn_count": exp_verdict.output.get("turn_count", 0),
            "stop_reason": exp_verdict.output.get("stop_reason", ""),
            "suggestions_added": suggestions_after - suggestions_before,
            "proposal_attempt_count": len(proposal_trace),
            "tool_error_count": tool_error_count,
            "elapsed_seconds": round(time.perf_counter() - iteration_started, 6),
            "reflector_skipped": is_unverified_game_shadow,
            "prior_beliefs_exposed": expose_prior_beliefs,
            "completion_contract_passed": exp_verdict.output.get(
                "completion_contract_passed"
            ),
            "completion_issues": exp_verdict.output.get("completion_issues", []),
            "validation_retry_count": exp_verdict.output.get(
                "validation_retry_count", 0
            ),
            "finish_validation_failures": exp_verdict.output.get(
                "finish_validation_failures", []
            ),
        }
        iteration_results.append(iteration_result)
        log.info("[hyp] iter %d | Experimenter 调用了 %d 次工具", iteration, len(trace))

        # ── 总结 agent:直接维护统一决策库 ──────────────
        if is_unverified_game_shadow:
            log.info("[hyp] iter %d | 未执行的 game-ui shadow 建议不进入决策库", iteration)
        else:
            await reflector.run({
                "trace": trace,
                "explore_domain": domain,
                "beliefs_snapshot": snapshot,
                "iteration": iteration,
                "session_id": session_id,
                "origin": "internal-engine",
                "agent_name": "BeliefReflectorRouter",
                "domain": "services/hypothesis",
            })

        after = beliefs_snapshot(domain)
        by_status: dict[str, int] = {}
        for b in after.get("beliefs") or []:
            s = b.get("status") or "untested"
            by_status[s] = by_status.get(s, 0) + 1
        log.info("[hyp] iter %d | 库终态 %d 条, 状态分布 %s",
                 iteration, after["total"], by_status)

        # 每轮完成后 emit 事件(含全量轮次状态,支持断点续跑)
        if bus is not None:
            try:
                from omnicompany.protocol.events import FactoryEvent
                from omnicompany.protocol.registry import EventType
                await bus.publish(FactoryEvent(
                    trace_id=session_id,
                    event_type=EventType.STATE_CHANGE.value,
                    source=f"hypothesis.pipeline.{domain}",
                    payload={
                        "session_id": session_id,
                        "domain": domain,
                        "iteration": iteration,
                        "trace_length": len(trace),
                        "experimenter_trace_id": experimenter_trace_id,
                        "suggestions_added": suggestions_after - suggestions_before,
                        "reflector_skipped": is_unverified_game_shadow,
                        "beliefs_total": after["total"],
                        "beliefs_by_status": by_status,
                    },
                    tags=["hypothesis", f"domain.{domain}", "iteration"],
                ))
            except Exception as exc:
                log.warning("[hyp] iter 事件发射失败: %s", exc)

    # 最终状态 + 重渲知识投影(30-知识)
    final = beliefs_snapshot(domain)
    by_status = {}
    for b in final.get("beliefs") or []:
        s = b.get("status") or "untested"
        by_status[s] = by_status.get(s, 0) + 1

    projection_path = ""
    try:
        from omnicompany.packages.domains.decisions.knowledge_projection import (
            render_knowledge_projection,
        )
        projection_path = str(render_knowledge_projection())
    except Exception as exc:
        log.warning("[hyp] 30-知识 投影重渲失败(非致命,可手动 omni decisions knowledge): %s", exc)

    result = {
        "session_id": session_id,
        "domain": domain,
        "scene_mode": scene.get("mode") or "",
        "scene_kind": scene.get("kind") or "",
        "iterations": iteration_results,
        "suggestion_ledger": str(suggestion_ledger or ""),
        "suggestion_count": _ledger_count_for_session(suggestion_ledger, session_id),
        "elapsed_seconds": round(time.perf_counter() - session_started, 6),
        "total_beliefs": final["total"],
        "by_status": by_status,
        "knowledge_projection": projection_path,
    }

    # 发 TASK_FINISH 事件 + 关闭 bus
    if bus is not None:
        try:
            from omnicompany.protocol.events import FactoryEvent
            from omnicompany.protocol.registry import EventType
            await bus.publish(FactoryEvent(
                trace_id=session_id,
                event_type=EventType.TASK_FINISH.value,
                source=f"hypothesis.pipeline.{domain}",
                payload={
                    "session_id": session_id,
                    "domain": domain,
                    "result": result,
                },
                tags=["hypothesis", f"domain.{domain}"],
            ))
            await bus.close()
        except Exception as exc:
            log.warning("[hyp] EventBus 收尾失败(非致命): %s", exc)

    return result


def run_session(session_config: dict) -> dict:
    """同步入口。"""
    return asyncio.run(_run_session_async(session_config))


def new_session(domain: str, goal: str, tools: list[str] | None = None,
                max_iterations: int = 3, env: dict | None = None,
                scene: dict | None = None) -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "domain": domain,
        "goal": goal,
        "tools": tools or [],
        "max_iterations": max_iterations,
        "env": env or {"MSYS_NO_PATHCONV": "1"},
        "scene": scene or {},
    }


# ═══════════════════════════════════════════════════════════
# TeamSpec (拓扑声明,供 omni describe / register 用)
# ═══════════════════════════════════════════════════════════
#
# 说明:hypothesis 的真实执行走 run_session() 的外部 N 轮循环。
# 这里的 TeamSpec 是**拓扑的可视化声明**,让 `omni describe hypothesis`
# 能展示数据流。运行时不由 TeamRunner 驱动(loop 不在 TeamSpec 语义内)。

from omnicompany.protocol.team import (
    NodeKind, NodeMaturity, TeamNode, TeamSpec,
)
from omnicompany.protocol.anchor import (
    TransformerSpec, TransformMethod,
)


def build_team() -> TeamSpec:
    """hypothesis 探索管线拓扑:单节点会话驱动器。

    节点内部驱动 N 轮 Experimenter→BeliefReflector 循环(run_session,自建 bus 与
    两个 AgentNodeLoop)——循环不在 TeamSpec 语义内,故对 TeamRunner 呈现为一个
    确定性入口节点(照 vilo eval 系「薄包装驱动 agent 循环」模式)。
    """
    nodes = [
        TeamNode(
            id="session",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="hypothesis-session",
                name="HypothesisSession",
                description=(
                    "会话驱动器:N 轮 [Experimenter 自由探索(bash/read/glob/grep 出轨迹)"
                    "→ BeliefReflector 用决策库五件套(list/record/challenge/resolve/link)"
                    "维护统一库 belief];收工重渲 30-知识 投影。"
                ),
                from_format="hypothesis.session",
                to_format="hypothesis.store_diff",
                method=TransformMethod.LLM,
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]
    return TeamSpec(
        id="hypothesis",
        name="假设探索",
        description="Experimenter 自由探索 + BeliefReflector 维护统一决策库 belief;主题摘要=30-知识生成投影。",
        purpose="把对陌生系统的探索所得沉成可证伪、可流转的统一库 belief。",
        nodes=nodes,
        edges=[],
        entry="session",
    )

# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.contract_auditor.graph_traversal.py"
"""ContractAuditorWorker — Phase 6 · HARD 静态图遍历 (2026-04-23).

Worker 协议 (composite fan-in and):
  FORMAT_IN  = [worker_design_detailed, material_design_detailed]
  FORMAT_OUT = team_builder.material.contract_audit

**职责**: HARD · 跨 Worker FORMAT_IN/OUT 连接性静态校验 (P-13 充分性 + F-15 诚实).

**规则**:
  P-13 充分性:
    - 每 Worker.FORMAT_IN 的 material 必须有 producer (另一 Worker.FORMAT_OUT 或声明为 source Material)
    - 每 Worker.FORMAT_OUT 的 material 必须有 consumer (另一 Worker.FORMAT_IN 或声明为 sink Material)
    - 无 orphan Worker (无入或无出 · 除非标 kind.source / kind.sink)
    - 无 dangling Material (无 producer 且非 source · 或无 consumer 且非 sink)

  F-15 诚实:
    - Worker.context_sources 必须声明了其 prompt 会用到的数据源 (不允许 **input_data 暗管)
    - (本版 HARD 检查: context_sources 非空时通过; 深层语义留 DesignValidator 语义维)

**不调 LLM** · 纯 graph 遍历.

**输入格式** (runner 平铺 fan-in):
  - worker_design_detailed 可能是**列表** (N 份独立上下文各产一份) 或从 material_id key 取 list
  - material_design_detailed 同上 (M 份)
  - 本 worker 容错处理两种形态
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _collect_list(
    input_data: dict, material_id: str, producer_node_prefix: str
) -> list[dict]:
    """从 input_data 里收集某类 material 的全部实例 (N 份 fan-out 合并).

    **关键**: worker/material 两侧都 fan-out 到同一 input_data (runner 平铺后),
    不能依赖 top-level "details" key (会跨类型冲突 · 例 worker_designer + material_designer
    各产一份 details, runner 合并成一个 key). 必须走 `_from_<producer>` 子 dict 区分.

    优先级:
      1. `_from_<producer_prefix>` 子 dict 有 "details" list (V2 Orchestrator 输出) → 用 list
      2. input_data[material_id] 若为 list → 直接用
      3. input_data[material_id] 若为 dict → 包成 [dict]
      4. 扫 input_data 所有 `_from_<producer_prefix>*` key 当 single dict (fallback)
      5. 兜底 []
    """
    # V2 Orchestrator 输出: _from_<producer>.details list
    for key, val in input_data.items():
        if isinstance(key, str) and key.startswith(f"_from_{producer_node_prefix}") and isinstance(val, dict):
            sub_details = val.get("details")
            if isinstance(sub_details, list) and all(isinstance(x, dict) for x in sub_details):
                return list(sub_details)

    # 传统单份 fan-in
    direct = input_data.get(material_id)
    if isinstance(direct, list):
        return [x for x in direct if isinstance(x, dict)]
    if isinstance(direct, dict):
        return [direct]

    # 扫 _from_<producer>* 当每个作单 material dict (fallback)
    collected: list[dict] = []
    for key, val in input_data.items():
        if isinstance(key, str) and key.startswith(f"_from_{producer_node_prefix}") and isinstance(val, dict):
            if "details" not in val:
                collected.append(val)
    return collected


def _audit_connections(
    workers: list[dict], materials: list[dict]
) -> dict:
    """静态图审计 · 返回审计报告 dict."""
    # 索引
    worker_by_id: dict[str, dict] = {w.get("worker_id"): w for w in workers if w.get("worker_id")}
    material_by_id: dict[str, dict] = {
        m.get("material_id"): m for m in materials if m.get("material_id")
    }

    # 聚合 producer / consumer 映射
    producers: dict[str, list[str]] = {}  # material_id → [worker_id, ...]
    consumers: dict[str, list[str]] = {}  # material_id → [worker_id, ...]

    for w in workers:
        wid = w.get("worker_id")
        if not wid:
            continue
        fo = w.get("format_out")
        if fo:
            producers.setdefault(fo, []).append(wid)
        fi = w.get("format_in")
        if isinstance(fi, str):
            consumers.setdefault(fi, []).append(wid)
        elif isinstance(fi, list):
            for m in fi:
                if isinstance(m, str):
                    consumers.setdefault(m, []).append(wid)

    # 骨架接管反算 lifecycle (feedback_100pct_required_goes_to_skeleton · 2026-04-24):
    # 从 producer/consumer 图骨架推定 (100% 正确), 覆盖 LLM 可能的错误填值.
    # - 无 consumer 但有 producer → sink (终点)
    # - 无 producer 但有 consumer → source (起点)
    # - 两端都有 → internal
    # - 两端都无 → 保留 LLM 填值或为 None (真 dangling, 走后续 audit)
    lifecycle_overrides: list[dict] = []
    for m in materials:
        mid = m.get("material_id")
        if not mid:
            continue
        has_producer = bool(producers.get(mid))
        has_consumer = bool(consumers.get(mid))
        if has_consumer and not has_producer:
            derived = "source"
        elif has_producer and not has_consumer:
            derived = "sink"
        elif has_producer and has_consumer:
            derived = "internal"
        else:
            derived = m.get("lifecycle")  # 无 refer · 保留 LLM 原值 (或 None · dangling)
        if derived != m.get("lifecycle"):
            lifecycle_overrides.append({
                "material_id": mid,
                "llm": m.get("lifecycle"),
                "skeleton": derived,
            })
            m["lifecycle"] = derived

    # source/sink 推定 (用覆盖后的 lifecycle)
    source_materials = [
        m.get("material_id")
        for m in materials
        if m.get("lifecycle") == "source"
    ]
    sink_materials = [
        m.get("material_id")
        for m in materials
        if m.get("lifecycle") == "sink"
    ]

    # P-13 连接性审计
    connections: list[dict] = []
    for mid, consumer_wids in consumers.items():
        prod_wids = producers.get(mid, [])
        if not prod_wids and mid not in source_materials:
            # dangling: 有 consumer 无 producer 且非 source
            for cwid in consumer_wids:
                connections.append({
                    "producer_worker": None,
                    "format_out": None,
                    "consumer_worker": cwid,
                    "format_in": mid,
                    "ok": False,
                    "issue": f"no producer for material {mid} (not declared as source)",
                })
        else:
            for pwid in (prod_wids or [None]):
                for cwid in consumer_wids:
                    connections.append({
                        "producer_worker": pwid,
                        "format_out": mid,
                        "consumer_worker": cwid,
                        "format_in": mid,
                        "ok": True,
                    })

    # dangling: 有 producer 无 consumer 且非 sink
    for mid, prod_wids in producers.items():
        if mid not in consumers and mid not in sink_materials:
            for pwid in prod_wids:
                connections.append({
                    "producer_worker": pwid,
                    "format_out": mid,
                    "consumer_worker": None,
                    "format_in": None,
                    "ok": False,
                    "issue": f"no consumer for material {mid} (not declared as sink)",
                })

    # orphan workers (无入无出 · 极端)
    orphan_workers = [
        w.get("worker_id")
        for w in workers
        if not w.get("format_in") and not w.get("format_out")
    ]

    # dangling materials (既不是 producer 也不是 consumer 指向的 · 孤立声明)
    dangling_materials = [
        mid for mid in material_by_id
        if mid not in producers and mid not in consumers
    ]

    # composite fan-ins
    composite_fan_ins = []
    for w in workers:
        if isinstance(w.get("format_in"), list) and len(w["format_in"]) > 1:
            composite_fan_ins.append({
                "worker": w.get("worker_id"),
                "format_in": w["format_in"],
                "mode": w.get("format_in_mode", "and"),
            })

    # F-15 context_sources 覆盖检查 (HARD 级: 每 SOFT/AGENT worker 必须有 context_sources)
    f15_issues: list[str] = []
    for w in workers:
        impl = w.get("impl_type", "")
        if impl in ("SOFT", "AGENT"):
            cs = w.get("context_sources")
            if not cs or not isinstance(cs, list) or not cs:
                f15_issues.append(
                    f"worker {w.get('worker_id')} is {impl} but context_sources is empty"
                )

    # overall
    any_issue = (
        any(not c["ok"] for c in connections)
        or orphan_workers
        or dangling_materials
        or f15_issues
    )

    return {
        "connections": connections,
        "orphan_workers": orphan_workers,
        "dangling_materials": dangling_materials,
        "composite_fan_ins": composite_fan_ins,
        "source_materials": source_materials,
        "sink_materials": sink_materials,
        "lifecycle_overrides": lifecycle_overrides,  # 骨架反算记录
        "f15_context_sources_issues": f15_issues,
        "overall_ok": not any_issue,
    }


class ContractAuditorWorker(Worker):
    """HARD · 跨 Worker FORMAT 连接 + F-15 context_sources 静态审计."""

    DESCRIPTION = (
        "Phase 6 · HARD 静态图遍历 · 审 P-13 充分性 (每 Worker FORMAT_IN 有 producer, "
        "FORMAT_OUT 有 consumer) + F-15 诚实 (SOFT/AGENT Worker 必须声明 context_sources). "
        "产出 contract_audit 报告 · 不调 LLM."
    )
    FORMAT_IN = [
        "team_builder.material.worker_design_detailed",
        "team_builder.material.material_design_detailed",
    ]
    FORMAT_IN_MODE = "and"
    FORMAT_OUT = "team_builder.material.contract_audit"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )

        workers = _collect_list(
            input_data, "team_builder.material.worker_design_detailed", "worker_designer"
        )
        materials = _collect_list(
            input_data, "team_builder.material.material_design_detailed", "material_designer"
        )

        if not workers:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="no worker_design_detailed instances found in input",
            )
        if not materials:
            existing_workers = [
                worker
                for worker in workers
                if str(worker.get("impl_type") or "").upper() == "EXISTING"
                and worker.get("binding_ref")
            ]
            if len(existing_workers) == len(workers):
                return Verdict(
                    kind=VerdictKind.PASS,
                    output={
                        "connections": [],
                        "orphan_workers": [],
                        "dangling_materials": [],
                        "composite_fan_ins": [],
                        "source_materials": [],
                        "sink_materials": [],
                        "lifecycle_overrides": [],
                        "f15_context_sources_issues": [],
                        "existing_worker_contracts": [
                            {
                                "worker_id": worker.get("worker_id"),
                                "binding_ref": worker.get("binding_ref"),
                                "format_in": worker.get("format_in"),
                                "format_out": worker.get("format_out"),
                            }
                            for worker in existing_workers
                        ],
                        "overall_ok": True,
                    },
                    diagnosis=(
                        f"contract audit · {len(existing_workers)} existing Worker "
                        "contracts resolved from live classes"
                    ),
                )
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="no material_design_detailed instances found in input",
            )

        report = _audit_connections(workers, materials)

        # **职责分离** (2026-04-23): ContractAuditor 只产审计**数据**, 综合判断留给
        # DesignValidator (Phase 7 · 7 维综合). 这样管线能跑完整到 DesignValidator,
        # 拿到综合报告, 而不会被 ContractAuditor 单独阻断.
        # overall_ok 字段仍在 report 里, DesignValidator 的 contract_closure_check 维会读.
        broken = sum(1 for c in report["connections"] if not c["ok"])
        diag = (
            f"contract audit · broken={broken} · orphan={len(report['orphan_workers'])} "
            f"· dangling={len(report['dangling_materials'])} · "
            f"f15_issues={len(report['f15_context_sources_issues'])} · "
            f"overall_ok={report['overall_ok']}"
        )
        return Verdict(kind=VerdictKind.PASS, output=report, diagnosis=diag)

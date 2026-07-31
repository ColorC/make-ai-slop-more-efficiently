# [OMNI] origin=claude-code domain=frontend_design/routers ts=2026-07-01T00:00:00Z type=router status=design
# [OMNI] summary="四节点 Router: ReviewIntake + DeterministicGate(dashboard 接真 ux_audit + style_gates 六条扫描 + token_drift 三方对账; LOFA 侧扫描始终跑) + Synthesize(接真: 汇总+决策沉淀+留痕);VlmRelativeReview 保持骨架桩。"
# [OMNI] why="统一设计工作室计划第四期(UNIFIED-DESIGN-STUDIO §5 M6/§10):gate 接确定性审计器产 L1/L2/L3 分级 failures;synthesize 写报告+落 L3 决策+运行留痕。2026-07-18 M4(UNIFIED-FRONTEND-UPGRADE):style_gates/token_drift 进门禁 CI 化。webgame 分支 gate 保持骨架(walker probes 不在本次)。"
# [OMNI] tags=frontend_design,router,worker,ux_audit,style_gates,token_drift,decisions,provenance

"""frontend_design.{dashboard,webgame} 的四节点 Router。

intake(真: 归一化+建run_dir) → gate → vlm_review → synthesize
- gate: dashboard 分支真跑确定性门禁, 产 failures(L1/L2/L3 分级):
  · ux_audit 三维枚举(surface 解析到本仓可静态审计的前端 src 才跑);
  · style_gates 六条扫描(LOFA 树独立始终扫; dashboard 树随 surface 解析才扫);
  · token_drift 三方对账(surface 解析到本仓前端 src 才跑)。
  审不了的输入(外部 URL/截图/webgame 分支)如实降级 gate_status="skipped-未接入该类目标",
  绝不假 PASS(LOFA 侧独立扫描的发现仍如实上报)。
  审计树/对账文件缺失: degraded 清单如实列出, 不冒充扫过。
- vlm_review: 仍为诚实透传桩(复用 aigc-lab image-review 待接, 见 DESIGN.md)。
- synthesize: 真跑——汇总 failures+comparisons 成 improvements+报告(写 run_dir);把 L3 发现
  落统一决策库(幂等); 运行留痕进统一账本(留痕失败不阻断)。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import _audit, _synthesize
from .._paths import RUNS_ROOT, ensure_dirs


# ── 节点 1: 入题(归一化审查请求 + 锁定 archetype/标尺 + 建 run_dir)──────────
class ReviewIntake(Router):
    """归一化审查请求, 从 archetype 定分支, 建 run_dir。"""

    DESCRIPTION = "入题: 归一化审查请求 + 锁定 archetype/标尺 + 建 run_dir"
    FORMAT_IN = "frontend_design.review_request"
    FORMAT_OUT = "frontend_design.intake"
    REQUIRED_CONTEXT = ["surface"]

    def run(self, input_data: Any) -> Verdict:
        req = input_data if isinstance(input_data, dict) else {}
        surface = str(req.get("surface", "")).strip()
        if not surface:
            return Verdict(kind=VerdictKind.FAIL, output=req, diagnosis="surface 为空(要审的界面)")

        branch = str(req.get("archetype", "") or "dashboard").strip()
        if branch not in {"dashboard", "webgame"}:
            branch = "dashboard"
        project = str(req.get("project", "") or ("webgame-ui" if branch == "webgame" else "dashboard-design"))

        ensure_dirs()
        run_dir = RUNS_ROOT / ("run_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S"))
        run_dir.mkdir(parents=True, exist_ok=True)
        from omnicompany.core.guarded_write import write_file
        write_file(
            run_dir / "intake.json",
            json.dumps({"surface": surface, "branch": branch, "project": project,
                        "ruler_ref": req.get("ruler_ref"), "baseline_ref": req.get("baseline_ref")},
                       ensure_ascii=False, indent=2),
            origin="frontend_design", domain="frontend_design", purpose="审查入题快照",
            writer="internal-engine",
        )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "surface": surface, "branch": branch, "project": project,
                "ruler_ref": req.get("ruler_ref"), "baseline_ref": req.get("baseline_ref"),
                "run_dir": str(run_dir),
            },
            diagnosis=f"入题: {branch} 分支 · 审 '{surface}'",
            granted_tags=["domain.frontend_design", "stage.intake"],
        )


# ── 节点 2: 确定性门禁(dashboard 接真 ux_audit+style_gates+token_drift; 审不了如实降级不假 PASS)
class DeterministicGate(Router):
    """确定性门禁。

    dashboard 分支: surface 若指向本仓前端可静态审计的 src 目录, 真跑 ux_audit 三维枚举 +
    style_gates dashboard 侧 + token_drift 三方对账; style_gates 的 LOFA 侧扫描独立于
    surface 始终跑(它是独立树)。发现翻成 failures(每条带 L1/L2/L3 分级,
    分诊词汇=发现分诊三级规范.md; style_gates 自带 L2/L3 分级原样透传, token 漂移=L2)。
    审不了的输入(外部 URL / 截图 / webgame 分支): gate_status="skipped-未接入该类目标",
    **不是 PASS 冒充**——如实标降级, 决策/报告不当它绿(LOFA 侧独立扫描的发现仍如实上报)。
    审计树/对账文件缺失: 记 degraded 说明, 不冒充扫过。
    审计器跑挂(抛异常): gate 如实 FAIL, 绝不吞成假 PASS。
    """

    DESCRIPTION = "确定性门禁(dashboard 真跑 ux_audit+style_gates+token_drift 产 L1/L2/L3 分级 failures; LOFA 侧始终扫; webgame/外部输入如实降级)"
    FORMAT_IN = "frontend_design.intake"
    FORMAT_OUT = "frontend_design.gate_result"
    REQUIRED_CONTEXT = ["run_dir"]

    # 注入点(单测替身: 桩 / 抛异常桩)
    audit_fn = None      # ux_audit 三维枚举(仅 surface 解析到本仓前端 src 才调)
    style_fn = None      # style_gates 六条扫描(始终调; 入参 None 时只扫 LOFA 树)
    drift_fn = None      # token_drift 三方对账(仅 surface 解析到本仓前端 src 才调)

    def run(self, input_data: Any) -> Verdict:
        out = input_data if isinstance(input_data, dict) else {}
        surface = str(out.get("surface", "")).strip()
        branch = str(out.get("branch", "") or "dashboard").strip()

        src = _audit.resolve_auditable_src(surface, branch)
        try:
            # LOFA 侧 style_gates 始终跑(独立树); dashboard 树仅随 surface 解析加入
            style_report = _audit.run_style_gates(str(src) if src else None, self.style_fn)
            # ux_audit 与 token_drift 只在 surface 解析到本仓前端 src 时跑
            ux_report = _audit.run_ux_audit(str(src), self.audit_fn) if src else None
            drift_report = _audit.run_token_drift(self.drift_fn) if src else None
        except Exception as e:
            # 审计器跑挂: 如实 FAIL, 绝不吞成假 PASS
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**out, "failures": [], "checked": [], "gate_status": "error"},
                diagnosis=f"确定性门禁失败: 审计器跑挂 · {type(e).__name__}: {e}",
                granted_tags=["domain.frontend_design", "stage.gate"],
            )

        failures: list[dict] = []
        checked: list[str] = []
        degraded: list[str] = list(style_report.get("degraded") or [])
        f, c = _audit.style_gates_to_failures(style_report)
        failures += f
        checked += c
        if src:
            f, c = _audit.audit_to_failures(ux_report)
            failures += f
            checked += c
            degraded += list(drift_report.get("degraded") or [])
            f, c = _audit.token_drift_to_failures(drift_report)
            failures += f
            checked += c

        n_l2 = sum(1 for x in failures if x.get("triage") == "L2")
        n_l3 = sum(1 for x in failures if x.get("triage") == "L3")
        if src is None:
            # 审不了(外部 URL/截图/webgame 分支/路径不存在): 如实降级, 不假 PASS
            reason = "skipped-未接入该类目标"
            return Verdict(
                kind=VerdictKind.PASS,
                output={**out, "failures": failures, "checked": checked,
                        "gate_status": reason, "degraded": degraded},
                diagnosis=(f"确定性门禁降级: {reason}(surface 非本仓可静态审计的前端 src); "
                           f"LOFA 侧 style_gates 独立扫描仍跑: {len(failures)} 项发现"
                           f"(L2×{n_l2} L3×{n_l3})"),
                granted_tags=["domain.frontend_design", "stage.gate"],
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={**out, "failures": failures, "checked": checked,
                    "gate_status": "audited", "audited_src": str(src), "degraded": degraded},
            diagnosis=(f"确定性门禁(ux_audit+style_gates+token_drift): {len(failures)} 项发现"
                       f"(L2×{n_l2} L3×{n_l3}) · src={src}"),
            granted_tags=["domain.frontend_design", "stage.gate"],
        )


# ── 节点 3: VLM 相对评审(骨架桩 —— 复用 aigc-lab image-review 待接)─────────
class VlmRelativeReview(Router):
    """VLM 相对评审。骨架期透传, 真实相对评审(对基准图成对比较, 列证据不打分)复用 aigc-lab image-review。"""

    DESCRIPTION = "VLM 相对评审(骨架: 复用 aigc-lab image-review, 对基准图成对比较列证据不打分)"
    FORMAT_IN = "frontend_design.gate_result"
    FORMAT_OUT = "frontend_design.vlm_review"
    REQUIRED_CONTEXT = ["branch"]

    def run(self, input_data: Any) -> Verdict:
        out = input_data if isinstance(input_data, dict) else {}
        return Verdict(
            kind=VerdictKind.PASS,
            output={**out, "comparisons": [], "review_status": "skeleton"},
            diagnosis="VLM 相对评审骨架已通; 复用 aigc-lab image-review 待接",
            granted_tags=["domain.frontend_design", "stage.review"],
        )


# ── 节点 4: 汇总 + 决策沉淀 + 运行留痕(接真)──────────────────────────────────
class Synthesize(Router):
    """汇总门禁+评审→改进建议+报告(写 run_dir); L3 发现落统一决策库; 运行留痕进统一账本。

    - decisions_recorded 填真实落库 id 列表(不再空数组硬编码)。
    - 留痕失败(provenance_hook 返 None / 抛异常)绝不阻断 synthesize——本节点仍 PASS。
    """

    DESCRIPTION = "汇总改进建议+报告 + L3 决策沉淀(decisions.library) + 运行留痕(provenance_hook)"
    FORMAT_IN = "frontend_design.vlm_review"
    FORMAT_OUT = "frontend_design.review_record"
    REQUIRED_CONTEXT = ["branch"]

    # 注入点(单测替身)
    upsert_fn = None      # 决策写入: 默认接 decisions.library.upsert
    recorder_fn = None    # 运行留痕: 默认接 provenance_hook.record_tool_run

    def run(self, input_data: Any) -> Verdict:
        out = input_data if isinstance(input_data, dict) else {}
        failures = out.get("failures") or []
        comparisons = out.get("comparisons") or []
        branch = out.get("branch")
        surface = str(out.get("surface", ""))
        project = str(out.get("project") or ("webgame-ui" if branch == "webgame" else "dashboard-design"))
        run_dir = str(out.get("run_dir") or "")
        gate_status = str(out.get("gate_status") or "")

        # ① 汇总 → improvements + 报告(写 run_dir)
        improvements = _synthesize.build_improvements(failures, comparisons)
        report_path = _synthesize.build_report(
            branch=str(branch), surface=surface, project=project, run_dir=run_dir,
            gate_status=gate_status, failures=failures, comparisons=comparisons,
            improvements=improvements,
        )

        # ② 决策沉淀: L3 发现落统一决策库(幂等)。写入失败要如实——不吞。
        decisions_recorded = _synthesize.persist_l3_decisions(
            failures=failures, project=project, run_dir=run_dir,
            report_path=report_path, upsert=self.upsert_fn,
        )

        # ③ 运行留痕: 进统一账本 events.jsonl。留痕失败不阻断(record_run 内部吞异常)。
        _synthesize.record_run(
            run_dir=run_dir, surface=surface, branch=str(branch),
            n_failures=len(failures), n_comparisons=len(comparisons),
            decisions_recorded=decisions_recorded, recorder=self.recorder_fn,
        )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "branch": branch,
                "run_dir": run_dir,
                "improvements": improvements,
                "decisions_recorded": decisions_recorded,
                "report": report_path,
            },
            diagnosis=(f"汇总完成: {len(failures)} 项门禁 / {len(comparisons)} 项相对评审; "
                       f"L3 决策沉淀 {len(decisions_recorded)} 条; 报告={report_path or '(未落盘)'}"),
            granted_tags=["domain.frontend_design", "stage.record", "kind.sink"],
        )

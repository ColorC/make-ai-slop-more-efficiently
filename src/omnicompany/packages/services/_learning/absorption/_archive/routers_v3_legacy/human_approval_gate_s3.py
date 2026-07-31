# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-14T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.stage3.approval_gate.router.py"
"""human_approval_gate_s3 — Stage 3 R2 HumanApprovalGateS3Router（RULE）

人工审批提案清单。

逻辑：
1. 检查 data/domains/absorption/<repo>/approved_proposals.txt 是否存在
   - 存在 → 读取 proposal_id 列表，产出 approved / rejected
   - 不存在，auto 模式 → risk=low 直接通过，risk=medium/high 标记 pending
2. 读完后重命名 .done

PASS:    所有 risk=low 已审批（或文件明确指定）
PARTIAL: 有 pending（risk=medium/high 等待人工），仍继续执行已批准部分
FAIL:    inputs 为空

FORMAT_IN:  absorption.proposal.list
FORMAT_OUT: absorption.proposal.approved

设计文档：docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/plan.md
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.core.config import resolve_domain_data_dir
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


class HumanApprovalGateS3Router(Router):
    """Stage 3 R2：改进提案人工审批门（RULE）。

    auto 模式：risk=low 直接通过，risk=medium/high 等待 approved_proposals.txt。
    手动模式：读取 approved_proposals.txt 逐行匹配。
    """

    DESCRIPTION = (
        "Stage 3 提案审批门（RULE）：risk=low 自动通过，risk≥medium 等待人工写入 "
        "approved_proposals.txt；读完重命名 .done，返回 approved/rejected/pending 分组"
    )
    FORMAT_IN = "absorption.proposal.list"
    FORMAT_OUT = "absorption.proposal.approved"

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        proposals: list[dict] = input_data.get("proposals") or []

        if not proposals:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="HumanApprovalGateS3: proposals 为空",
            )

        repo_dir = resolve_domain_data_dir("absorption") / repo_name
        approved_path = repo_dir / "approved_proposals.txt"

        if approved_path.exists():
            approved_ids, rejected_ids, pending_ids = _read_approval_file(approved_path, proposals)
            # 重命名 .done
            done_path = repo_dir / "approved_proposals.txt.done"
            approved_path.rename(done_path)
            print(f"[ApprovalGateS3] approved_proposals.txt → {done_path.name}")
        else:
            # auto 模式：risk=low 直接通过，其余 pending
            approved_ids = [p["proposal_id"] for p in proposals if p.get("risk_level") == "low"]
            rejected_ids = []
            pending_ids = [p["proposal_id"] for p in proposals if p.get("risk_level") != "low"]

        rejected_list = [{"proposal_id": pid, "reason": "not in approved_proposals.txt"} for pid in rejected_ids]

        print(
            f"[ApprovalGateS3] approved={len(approved_ids)}, "
            f"rejected={len(rejected_ids)}, pending={len(pending_ids)}"
        )
        if pending_ids:
            print(f"[ApprovalGateS3] pending: {pending_ids}（写入 approved_proposals.txt 以批准）")

        kind = VerdictKind.PASS if not pending_ids else VerdictKind.PARTIAL

        return Verdict(
            kind=kind,
            output={
                **input_data,
                "approved_proposals": approved_ids,
                "rejected_proposals": rejected_list,
                "pending_proposals": pending_ids,
            },
            confidence=1.0,
            diagnosis=(
                f"ApprovalGateS3: {len(approved_ids)} approved, "
                f"{len(rejected_ids)} rejected, {len(pending_ids)} pending"
            ),
            granted_tags=["domain.absorption", "stage.v3.stage3"],
        )


def _read_approval_file(
    approved_path: Path,
    proposals: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    """从 approved_proposals.txt 读取审批结果。

    文件格式：每行一个 proposal_id（如 PRO-001），或 * 表示全部批准。
    """
    content = approved_path.read_text(encoding="utf-8").strip()
    all_ids = {p["proposal_id"] for p in proposals}

    if not content:
        # 空文件 = 全部跳过
        return [], list(all_ids), []

    if content.strip() == "*":
        # * = 全部批准
        return list(all_ids), [], []

    explicit = {line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")}
    approved = [pid for pid in all_ids if pid in explicit]
    rejected = [pid for pid in all_ids if pid not in explicit]
    return approved, rejected, []


# ══════════════════════════════════════════════════════════════════════
# Stage 3 feedback 回路（2026-04-18 补充）
# 架构缺口：spec_parser → approval_gate 原本是线性无反馈。Stage 2 的 feedback
# 回路（ReportWriter 级）没有 Stage 3 对等实现，导致人类无法要求 SpecParser
# 补产未被综合的主题。以下 2 个 Router 复用 Stage 2 的 HumanFeedbackGateV3 +
# FeedbackRouterV3 模式。
# ══════════════════════════════════════════════════════════════════════


class ProposalFeedbackGateRouter(Router):
    """Stage 3 人工提案反馈门（RULE）。

    检查 data/domains/absorption/<repo>/proposal_feedback.md：
    - 若存在：完整读取（铁律 A 不截断），解析方向列表，重命名为
      proposal_feedback_<iteration>.md.done
    - 若不存在：auto-pass，directions=[]

    输入：absorption.proposal.list
    输出：absorption.proposal.feedback
    """

    DESCRIPTION = (
        "Stage 3 人工提案反馈门（RULE）：读 proposal_feedback.md 完整内容，解析补充方向，"
        "重命名 .done；无文件则 auto-pass，directions=[]。与 Stage 2 的 HumanFeedbackGateV3 同构。"
    )
    FORMAT_IN = "absorption.proposal.list"
    FORMAT_OUT = "absorption.proposal.feedback"

    def run(self, input_data: Any) -> Verdict:
        import re

        repo_name = input_data.get("repo_name", "unknown")
        iteration: int = int(input_data.get("iteration", 1))
        proposals = input_data.get("proposals") or []

        repo_dir = resolve_domain_data_dir("absorption") / repo_name
        feedback_path = repo_dir / "proposal_feedback.md"

        if not feedback_path.exists():
            print("[ProposalFeedbackGate] 未发现 proposal_feedback.md，本轮完成（auto-pass）")
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "feedback_text": "",
                    "directions": [],
                    "has_feedback": False,
                    "iteration": iteration,
                    "proposals": proposals,
                },
                confidence=1.0,
                diagnosis=f"ProposalFeedbackGate: no proposal_feedback.md (iteration={iteration})",
            )

        # 完整读入（铁律 A），不截断
        feedback_text = feedback_path.read_text(encoding="utf-8").strip()
        print(f"[ProposalFeedbackGate] 发现 proposal_feedback.md ({len(feedback_text)} 字节)")

        # 解析列表项为 directions
        directions: list[str] = []
        for line in feedback_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^[-*\d.]+\s+(.+)$", line)
            if m:
                directions.append(m.group(1).strip())
        if not directions and feedback_text:
            # 无列表项但有内容，整段作为一个方向（零容忍截断：不 [:200]）
            directions = [feedback_text]

        done_path = repo_dir / f"proposal_feedback_{iteration}.md.done"
        feedback_path.replace(done_path)
        print(f"[ProposalFeedbackGate] proposal_feedback.md → {done_path.name}")
        print(f"[ProposalFeedbackGate] 解析到 {len(directions)} 个补充方向")

        has_feedback = bool(directions)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "feedback_text": feedback_text,
                "directions": directions,
                "has_feedback": has_feedback,
                "iteration": iteration,
                "proposals": proposals,
            },
            confidence=1.0,
            diagnosis=(
                f"ProposalFeedbackGate: {len(directions)} 方向 (iteration={iteration})"
                if has_feedback else
                "ProposalFeedbackGate: 空反馈，本轮完成"
            ),
        )


class ProposalFeedbackRouterRouter(Router):
    """Stage 3 提案反馈路由（RULE + 判断：PASS 或 JUMP）。

    判断逻辑：
    - has_feedback=False → PASS → 继续 approval_gate
    - has_feedback=True  → PARTIAL → JUMP 回 spec_parser，带 supplement_guidance

    PARTIAL 时产出 absorption.proposal.supplement_request，保留 composite components
    让 spec_parser 重新消费。

    输入：absorption.proposal.feedback
    输出：absorption.proposal.list（PASS）或 absorption.proposal.supplement_request（JUMP）
    """

    DESCRIPTION = (
        "Stage 3 提案反馈路由（RULE）：has_feedback=False → PASS 给 approval_gate；"
        "has_feedback=True → 构建 supplement_request + supplement_guidance，JUMP 回 spec_parser 重新综合。"
    )
    FORMAT_IN = "absorption.proposal.feedback"
    # 动态 OUT：PASS 时 absorption.proposal.list；JUMP 时 absorption.proposal.supplement_request
    FORMAT_OUT = "absorption.proposal.list"

    def run(self, input_data: Any) -> Verdict:
        has_feedback = bool(input_data.get("has_feedback"))
        directions = list(input_data.get("directions") or [])
        iteration: int = int(input_data.get("iteration", 1))
        repo_name = input_data.get("repo_name", "unknown")

        if not has_feedback or not directions:
            print(f"[ProposalFeedbackRouter] 无补充方向，提案锁定 (iteration={iteration})")
            # 保留 absorption.proposal.list 的完整 schema 字段（proposals / total_count / p0_count / repo_name / pending_review_path）
            # 仅剥离 feedback 专有字段
            output_dict = {k: v for k, v in input_data.items() if k not in (
                "feedback_text", "directions", "has_feedback"
            )}
            # 若 total_count/p0_count 被上游透传 layer 丢失则从 proposals 重算
            proposals = output_dict.get("proposals") or []
            if "total_count" not in output_dict:
                output_dict["total_count"] = len(proposals)
            if "p0_count" not in output_dict:
                output_dict["p0_count"] = sum(
                    1 for p in proposals if (p.get("source") or {}).get("priority") == "P0"
                )
            return Verdict(
                kind=VerdictKind.PASS,
                output=output_dict,
                confidence=1.0,
                diagnosis=f"ProposalFeedbackRouter: EMIT path (iteration={iteration})",
            )

        # 构建 supplement_request
        previous_proposals = list(input_data.get("proposals") or [])
        lines = ["本轮补充综合要求（请优先重点关注）："]
        for i, d in enumerate(directions, 1):
            lines.append(f"{i}. {d}")
        if previous_proposals:
            titles = [p.get("title", "?") for p in previous_proposals]
            lines.append("")
            lines.append("**已产出提案（请勿重复标题，但可补充、拆分、合并）**：")
            for t in titles:
                lines.append(f"  - {t}")
        supplement_guidance = "\n".join(lines)

        next_iter = iteration + 1

        # 保留 composite 三路原料（spec_parser 兜底读取）
        supplement_request = {
            "repo_name": repo_name,
            "supplement_guidance": supplement_guidance,
            "iteration": next_iter,
            "previous_proposals": previous_proposals,
            # 透传 composite components（run-time JUMP 时 spec_parser 走兜底路径）
            "absorption.report.v3": input_data.get("absorption.report.v3") or {
                k: v for k, v in input_data.items()
                if k in ("repo_name", "report_path", "report_md", "structured",
                         "findings", "feedback_incorporated")
            },
            "omni.self.capability_inventory": input_data.get("omni.self.capability_inventory") or {},
            "omni.self.gap_registry": input_data.get("omni.self.gap_registry") or {},
        }

        print(
            f"[ProposalFeedbackRouter] {len(directions)} 方向 → JUMP 回 spec_parser "
            f"(iteration {iteration} → {next_iter})"
        )
        for d in directions:
            print(f"  - {d}")

        return Verdict(
            kind=VerdictKind.PARTIAL,
            output=supplement_request,
            confidence=0.8,
            diagnosis=(
                f"ProposalFeedbackRouter: JUMP to spec_parser, "
                f"{len(directions)} 方向 (iteration={next_iter})"
            ),
        )

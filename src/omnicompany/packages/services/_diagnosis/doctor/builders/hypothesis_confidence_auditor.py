# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T13:50:00Z type=router status=active agent=ai-ide
# [OMNI] summary="HypothesisConfidenceAuditor — 据 V1 metadata + finding_count 启发式建议 confidence_level 升级. 修 V4-2 dogfood 暴露的'25 假设 confidence 都 low' 区分度低问题"
# [OMNI] why="V4-3. V0 升级时 confidence_level 默认 'low', 后续靠手工标. 但全 25 假设没人手工标, 导致 ChallengeQueue ranked 看不出区分度. 立审计类不修改, 列 suggestion 供调用方/用户决定"
# [OMNI] tags=builder,confidence-audit,hypothesis,structured,no-llm,V4
# [OMNI] material_id="material:diagnosis.doctor.builders.hypothesis_confidence_auditor.py"
"""HypothesisConfidenceAuditor · 假设 confidence 半自动审计 (V0).

跟 ChallengeRecorder / V1Upgrader 同形态 — 不用 LLM, 纯函数, 不直接落盘.
**审计类 — 不动假设本身**, 只列 suggestion. 调用方/用户决定是否手工标真值.

修 V4-2 dogfood 暴露的真问题: 跑本地 25 假设 ranked top 5 全 score=1100, 看不出
"哪条更值得质疑". 根因: V0 升级时 confidence_level 默认 'low' 没人手工标真值, 全 25
假设都同 confidence/risk → ChallengeQueue a 类全触发 → 区分度归零.

启发式 (按优先级降序, 第一个命中即返建议):
1. **verification_status='real_world_validated'** → suggest 'high'
   (按 schema §三步骤 5: red_green_pass + 实战 ≥3 → real_world_validated, 其本身意味着
   confidence=high. V1Upgrader 已自动升 — 这条主要 catch V1Upgrader 没覆盖的边角)

2. **len(related_finding_ids) ≥ 3** → suggest 'high'
   (实战 ≥3 次实例验过, 跟 §三步骤 5 等价. 但可能 verification_status 还是
   'red_green_pass' 没被 V1Upgrader 自动升过)

3. **len(challenge_log) ≥ 5 + verification_status ≠ 'falsified'** → suggest 'high'
   (V10 加: 经反复质疑 ≥5 次仍未被证否, 假设比新生成显著可信 — 真承受过质疑工作流)

4. **verification_status='red_green_pass'** → suggest 'medium'
   (跑过红绿但未实战, schema §三 V1 升级表里红绿过的标 medium)

5. **source_authority='HIGH'** → suggest 'medium'
   (规范派生且规范是用户拍板的高权威 → 假设值得信)

6. **len(related_finding_ids) ≥ 1** → suggest 'medium'
   (有 1-2 finding 实战, 但不到 ≥3 阈值)

7. **len(challenge_log) ≥ 2 + verification_status ≠ 'falsified'** → suggest 'medium'
   (V10 加: 经几次质疑 ≥2 次仍未被证否, 比新生成更稳固)

8. 否则 → suggest 'low' (维持默认 — 新生成未验证)

V0 不接通:
- 不直接修 yaml (审计类)
- 不调 LLM
- 调用方拿 audit 报告自己决定是否手工标
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConfidenceAuditEntry:
    """一条假设 confidence 审计."""
    hypothesis_id: str
    current_confidence: str           # 现有 confidence_level
    suggested_confidence: str         # 启发式建议
    reason: str                       # 启发式触发理由
    needs_upgrade: bool               # current < suggested 时为 True


@dataclass
class ConfidenceAuditResult:
    audited: list[ConfidenceAuditEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)

    @property
    def upgrade_count(self) -> int:
        return sum(1 for e in self.audited if e.needs_upgrade)

    @property
    def by_suggested(self) -> dict:
        d: dict = {}
        for e in self.audited:
            d[e.suggested_confidence] = d.get(e.suggested_confidence, 0) + 1
        return d

    @property
    def summary(self) -> str:
        if not self.audited:
            return f"audited 0, skipped {len(self.skipped)}"
        return (
            f"audited {len(self.audited)}, skipped {len(self.skipped)} | "
            f"needs_upgrade={self.upgrade_count} | "
            f"by_suggested={self.by_suggested}"
        )


# confidence 排序 (低 → 高), 用于判 needs_upgrade
_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


class HypothesisConfidenceAuditor:
    """据 V1 metadata + finding_count + challenge_log 启发式建议 confidence_level."""

    REAL_WORLD_FINDING_THRESHOLD = 3  # 跟 V1Upgrader.REAL_WORLD_VALIDATION_THRESHOLD 一致
    CHALLENGE_LOG_HIGH_THRESHOLD = 5    # V10 加: 反复质疑 ≥5 仍站立 → high
    CHALLENGE_LOG_MEDIUM_THRESHOLD = 2  # V10 加: 经 ≥2 质疑仍站立 → medium

    def audit(self, hypotheses: list[dict]) -> ConfidenceAuditResult:
        result = ConfidenceAuditResult()
        for hyp in hypotheses:
            if not isinstance(hyp, dict):
                result.skipped.append(("<non-dict>", f"非 dict: {type(hyp).__name__}"))
                continue
            hid = hyp.get("id")
            if not hid:
                result.skipped.append(("<no-id>", "缺 id"))
                continue
            current = hyp.get("confidence_level") or "low"
            suggested, reason = self._derive(hyp)
            needs = _CONF_ORDER.get(current, 0) < _CONF_ORDER.get(suggested, 0)
            result.audited.append(ConfidenceAuditEntry(
                hypothesis_id=hid,
                current_confidence=current,
                suggested_confidence=suggested,
                reason=reason,
                needs_upgrade=needs,
            ))
        return result

    def _derive(self, hyp: dict) -> tuple[str, str]:
        """据 V1 metadata 派生 suggested confidence + reason."""
        verification_status = hyp.get("verification_status", "untested")
        finding_ids = hyp.get("related_finding_ids") or []
        finding_count = len(finding_ids)
        challenge_log = hyp.get("challenge_log") or []
        challenge_count = len(challenge_log)
        source_authority = hyp.get("source_authority", "unknown")

        # 1. real_world_validated → high
        if verification_status == "real_world_validated":
            return ("high", "verification_status='real_world_validated' (schema §三步骤 5 终态)")

        # 2. ≥ 3 finding → high (跟 §三步骤 5 等价)
        if finding_count >= self.REAL_WORLD_FINDING_THRESHOLD:
            return ("high", f"实战 {finding_count} 次 ≥ {self.REAL_WORLD_FINDING_THRESHOLD} 阈值")

        # 3. V10: ≥5 质疑仍未被证否 → high
        if (challenge_count >= self.CHALLENGE_LOG_HIGH_THRESHOLD
                and verification_status != "falsified"):
            return (
                "high",
                f"经 {challenge_count} 次质疑 ≥ {self.CHALLENGE_LOG_HIGH_THRESHOLD} 仍未被证否 "
                f"(verification_status='{verification_status}')"
            )

        # 4. red_green_pass → medium
        if verification_status == "red_green_pass":
            return ("medium", "verification_status='red_green_pass' (跑过红绿但未实战)")

        # 5. HIGH 权威规范派生 → medium
        if source_authority == "HIGH":
            return ("medium", "source_authority='HIGH' (规范派生且规范用户拍板高权威)")

        # 6. 1-2 finding → medium
        if finding_count >= 1:
            return ("medium", f"实战 {finding_count} 次 (1-2 之间, 未达 high 阈值)")

        # 7. V10: ≥2 质疑仍未被证否 → medium
        if (challenge_count >= self.CHALLENGE_LOG_MEDIUM_THRESHOLD
                and verification_status != "falsified"):
            return (
                "medium",
                f"经 {challenge_count} 次质疑仍未被证否 (≥ {self.CHALLENGE_LOG_MEDIUM_THRESHOLD} 阈值)"
            )

        # 8. 默认 low
        return ("low", "无 V1 metadata 强信号 (新生成未验证)")


def audit_hypothesis_confidence(hypotheses: list[dict]) -> ConfidenceAuditResult:
    """便捷入口."""
    return HypothesisConfidenceAuditor().audit(hypotheses)


# ── V12 CLI 入口 (2026-05-07) ─────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """CLI 入口 — 跑 ConfidenceAuditor on 假设 yaml 目录.

    用法:
        # 默认看全 audit (含未触发升级的):
        python -m omnicompany.packages.services._diagnosis.doctor.builders.hypothesis_confidence_auditor \\
          --hypotheses-dir data/services/doctor/hypotheses

        # 只看 needs_upgrade=True 的:
        python -m omnicompany....hypothesis_confidence_auditor \\
          --hypotheses-dir data/services/doctor/hypotheses \\
          --only-needs-upgrade

        # 落档 json:
        python -m omnicompany....hypothesis_confidence_auditor \\
          --hypotheses-dir ... --output-json _scratch/audit.json

    注: 本 CLI 只 audit 不修. 想自动升 confidence 走 V1Upgrader CLI (它会接 archive 反向查)
    然后再跑本 CLI 看 V1Upgrader 升级后建议.

    返:
        0 = 成功, 1 = hypotheses-dir 不存在.
    """
    import argparse
    import json
    import sys
    from pathlib import Path

    import yaml

    parser = argparse.ArgumentParser(prog="hypothesis_confidence_auditor")
    parser.add_argument("--hypotheses-dir", required=True, help="假设 yaml 目录")
    parser.add_argument("--only-needs-upgrade", action="store_true",
                        help="只显示 needs_upgrade=True 的条目")
    parser.add_argument("--output-json", default=None,
                        help="把 audit result 写 json 文件 (相对当前目录)")
    args = parser.parse_args(argv)

    hyp_dir = Path(args.hypotheses_dir)
    if not hyp_dir.exists():
        print(f"ERROR: hypotheses-dir 不存在: {hyp_dir}", file=sys.stderr)
        return 1

    # 加载 yaml
    hyps: list[dict] = []
    for ext in ("*.yaml", "*.yml"):
        for path in sorted(hyp_dir.glob(ext)):
            try:
                with path.open(encoding="utf-8") as f:
                    d = yaml.safe_load(f)
            except Exception:
                continue
            if isinstance(d, dict):
                hyps.append(d)

    if not hyps:
        print(f"WARNING: hypotheses-dir 无 yaml: {hyp_dir}")
        return 0

    auditor = HypothesisConfidenceAuditor()
    result = auditor.audit(hyps)
    print(result.summary)

    # stdout 表
    entries = result.audited
    if args.only_needs_upgrade:
        entries = [e for e in entries if e.needs_upgrade]
    if entries:
        print()
        print(f"{'hypothesis_id':<30} {'current':<8} {'suggested':<10} {'upgrade':<8} reason")
        print("-" * 110)
        for e in entries:
            up_mark = "✓" if e.needs_upgrade else " "
            print(f"{e.hypothesis_id:<30} {e.current_confidence:<8} {e.suggested_confidence:<10} "
                  f"{up_mark:<8} {e.reason[:50]}")
    else:
        print("(无符合条件条目)")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        slim = {
            "summary": result.summary,
            "by_suggested": result.by_suggested,
            "upgrade_count": result.upgrade_count,
            "audited": [
                {
                    "hypothesis_id": e.hypothesis_id,
                    "current_confidence": e.current_confidence,
                    "suggested_confidence": e.suggested_confidence,
                    "needs_upgrade": e.needs_upgrade,
                    "reason": e.reason,
                }
                for e in result.audited
            ],
            "skipped": [{"id": s[0], "reason": s[1]} for s in result.skipped],
        }
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=2)
        print(f"\n落档 json: {out_path}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())

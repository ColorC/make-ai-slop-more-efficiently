# [OMNI] origin=claude-code domain=services/docauthor/workers ts=2026-04-25T00:00:00Z type=router
# [OMNI] material_id="material:authoring.docauthor.refine_relauncher.worker.py"
"""RefineRelauncher — 把未过 Reviewer 的 verdict 翻译回 new author request.

bus 驱动 refine 循环的关键一步. 拆成两个 Worker 因为 Worker FORMAT_OUT 是
`str` 硬约定, 不能 "有时 emit A 有时 emit B".

- `ManifestRefineRelauncher`: FORMAT_IN=review-verdict → FORMAT_OUT=manifest-request
- `DesignRefineRelauncher`:   FORMAT_IN=review-verdict → FORMAT_OUT=design-request

**激活条件** (硬):
  - target_type 匹配 (manifest / design)
  - passed == False
  - iter < max_refine_iters

不满足即 Verdict(FAIL, ...), dispatcher 跳过不 emit (dispatcher.py:181-183).

产出用 `_emit_as_new_job=True` 起**子 job** — 每次 refine 是独立 trace_id,
让 Author 在新 trace 里能再次激活 (Q1 单次激活以 (trace_id, worker_id) 为键).
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


def _format_feedback_for_refine(issues: list[dict]) -> str:
    """把 issues 列表格式化成 Author 能读的反馈文本."""
    if not issues:
        return "(reviewer says passed · 不应进入此路径)"
    by_sev: dict[str, list[dict]] = {"critical": [], "major": [], "minor": []}
    for i in issues:
        by_sev.setdefault(i.get("severity", "minor"), []).append(i)
    parts: list[str] = []
    for sev in ("critical", "major", "minor"):
        if not by_sev.get(sev):
            continue
        parts.append(f"### {sev.upper()}")
        for it in by_sev[sev]:
            parts.append(
                f"- [{it.get('field','?')}] {it.get('message','')}\n"
                f"  evidence: {it.get('evidence','(none)')}\n"
                f"  fix: {it.get('fix_hint','')}"
            )
    return "\n".join(parts)


class _RefineRelauncherBase(Worker):
    """内部基类 · 子类指定 target kind + FORMAT_OUT."""

    #: str — "manifest" 或 "design"
    TARGET_KIND: str = ""

    FORMAT_IN = "docauthor.review-verdict"
    FORMAT_OUT = ""  # 子类赋值

    def run(self, input_data: dict[str, Any]) -> Verdict:
        verdict = input_data.get(self.FORMAT_IN) or input_data
        target_type = (verdict.get("target_type") or "").strip().lower()

        # 本 Worker 只处理匹配 kind 的 verdict
        if target_type != self.TARGET_KIND:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"target_type={target_type} != {self.TARGET_KIND}")

        passed = bool(verdict.get("passed"))
        if passed:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="already passed; no refine needed")

        iter_num = int(verdict.get("iter") or 0)
        max_iter = int(verdict.get("max_refine_iters") or 1)
        if iter_num >= max_iter:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"refine budget exhausted (iter={iter_num} >= max={max_iter})")

        prior_draft = verdict.get("draft_content") or ""
        issues = verdict.get("issues") or []
        feedback = _format_feedback_for_refine(issues)

        # 构造新 request
        if self.TARGET_KIND == "manifest":
            target_path = verdict.get("target_service_path") or verdict.get("target_path") or ""
            request = {
                "target_service_path": target_path,
                "prior_draft": prior_draft,
                "review_feedback": feedback,
                "notes_hint": verdict.get("notes_hint") or "",
                "iter": iter_num + 1,
                "max_refine_iters": max_iter,
                # 子 job · Author 在新 trace 能再激活
                "_emit_as_new_job": True,
            }
        else:  # design / readme / skill 共用 target_package_path
            target_path = verdict.get("target_package_path") or verdict.get("target_path") or ""
            request = {
                "target_package_path": target_path,
                "prior_draft": prior_draft,
                "review_feedback": feedback,
                "iter": iter_num + 1,
                "max_refine_iters": max_iter,
                "_emit_as_new_job": True,
            }
            # design 特有
            if self.TARGET_KIND == "design":
                request["upgrade_from_skeleton"] = bool(verdict.get("upgrade_from_skeleton"))

        return Verdict(kind=VerdictKind.PASS, output=request)


class ManifestRefineRelauncher(_RefineRelauncherBase):
    DESCRIPTION = (
        "bus 驱动 refine 循环 · 监听 review-verdict, 若 target_type==manifest 且未通过且 iter<max, "
        "发回 manifest-request (子 job · 新 trace_id)."
    )
    TARGET_KIND = "manifest"
    FORMAT_OUT = "docauthor.manifest-request"


class DesignRefineRelauncher(_RefineRelauncherBase):
    DESCRIPTION = (
        "bus 驱动 refine 循环 · 监听 review-verdict, 若 target_type==design 且未通过且 iter<max, "
        "发回 design-request (子 job · 新 trace_id)."
    )
    TARGET_KIND = "design"
    FORMAT_OUT = "docauthor.design-request"


class ReadmeRefineRelauncher(_RefineRelauncherBase):
    DESCRIPTION = (
        "bus 驱动 refine 循环 · 监听 review-verdict, 若 target_type==readme 且未通过且 iter<max, "
        "发回 readme-request (子 job · 新 trace_id)."
    )
    TARGET_KIND = "readme"
    FORMAT_OUT = "docauthor.readme-request"


class SkillRefineRelauncher(_RefineRelauncherBase):
    DESCRIPTION = (
        "bus 驱动 refine 循环 · 监听 review-verdict, 若 target_type==skill 且未通过且 iter<max, "
        "发回 skill-request (子 job · 新 trace_id)."
    )
    TARGET_KIND = "skill"
    FORMAT_OUT = "docauthor.skill-request"


__all__ = [
    "ManifestRefineRelauncher",
    "DesignRefineRelauncher",
    "ReadmeRefineRelauncher",
    "SkillRefineRelauncher",
]

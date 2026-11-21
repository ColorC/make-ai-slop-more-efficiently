# [OMNI] origin=claude-code domain=services/docauthor/workers ts=2026-04-25T00:00:00Z type=router
# [OMNI] material_id="material:authoring.docauthor.final_draft_lander.worker.py"
"""FinalLanderWorker — bus 驱动终局 · passed 落 src/ · exhausted 隔离 quarantine.

**2026-04-25 修正**: src/ 是可提交的产品文档. 当 refine budget 耗尽且仍有 critical
issue, **不写 src/**, 改写 `data/services/docauthor/drafts/_quarantine/<slug>/`. 让 L2
后续审视/手工修/重跑 docauthor (加大 budget 或改 prompt). src/ 不被污染.

激活条件:
- 收到 docauthor.review-verdict
- passed=True (正常通过)   OR
- passed=False 且 iter >= max_refine_iters (refine budget 耗尽 · 走隔离)

不满足 → Verdict(FAIL, ...) 不 emit event, 等 Refine Relauncher 处理.

落盘目标:
- 通过 (passed=True): 写 src/
  - manifest → `<target>/.omni/manifest.yaml`
  - design   → `<target>/DESIGN.md`
- 隔离 (exhausted-not-passed): 写 quarantine
  - manifest → `data/services/docauthor/drafts/_quarantine/<slug>/manifest.yaml`
  - design   → `data/services/docauthor/drafts/_quarantine/<slug>/DESIGN.md`
  - 同时落 `data/services/docauthor/drafts/_quarantine/<slug>/issues.json` 含 verdict 全文

存在同名文件时覆盖 (refine 流程内 Author 已读过 prior_draft).

产出 `docauthor.job-final` Material 作**观测终局信号** (sink). terminal_status:
- `passed` → 写 src/ 成功
- `quarantined_at_iter_N` → 写 quarantine, src/ 不动
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .manifest_author import _default_repo_root, _FORBIDDEN_PATH_MARKERS


class FinalLanderWorker(Worker):
    DESCRIPTION = (
        "监听 review-verdict · passed 或 refine 耗尽即落盘最终 draft 到 src/ 目标位置 "
        "(manifest → <target>/.omni/manifest.yaml; design → <target>/DESIGN.md). "
        "产出 docauthor.job-final 作终局观测信号 (sink). 反泄漏: gold_samples 路径拒写."
    )
    FORMAT_IN = "docauthor.review-verdict"
    FORMAT_OUT = "docauthor.job-final"

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        self._repo_root = (repo_root or _default_repo_root()).resolve()
        self._dry_run = dry_run

    def run(self, input_data: dict[str, Any]) -> Verdict:
        verdict = input_data.get(self.FORMAT_IN) or input_data

        passed = bool(verdict.get("passed"))
        iter_num = int(verdict.get("iter") or 0)
        max_iter = int(verdict.get("max_refine_iters") or 1)

        should_land = passed or (iter_num >= max_iter)
        if not should_land:
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"not terminal (passed={passed}, iter={iter_num} < max={max_iter}); relauncher will handle",
            )

        target_type = (verdict.get("target_type") or "").strip().lower()
        if target_type not in {"manifest", "design", "readme", "skill"}:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"unknown target_type={target_type!r}")

        target_path = (
            verdict.get("target_service_path")
            or verdict.get("target_package_path")
            or verdict.get("target_path")
            or ""
        ).strip()
        if not target_path:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="no target_path in verdict")

        # 反泄漏
        for m in _FORBIDDEN_PATH_MARKERS:
            if m in target_path.replace("\\", "/"):
                return Verdict(kind=VerdictKind.FAIL,
                               diagnosis=f"forbidden marker in target: {m}")

        draft_content = verdict.get("draft_content") or ""
        if not draft_content.strip():
            return Verdict(kind=VerdictKind.FAIL, diagnosis="empty draft_content")

        # 落盘路径决策: passed → src/; exhausted-not-passed → quarantine
        slug = target_path.replace("/", "__").replace("\\", "__")

        # target_type → (suffix, src_path_suffix, filename) 路由
        # manifest → .yaml / .omni/manifest.yaml
        # design / readme / skill → .md / DESIGN.md / README.md / SKILL.md
        if target_type == "manifest":
            suffix = ".yaml"
            src_rel_suffix = "/.omni/manifest.yaml"
            filename = "manifest.yaml"
        elif target_type == "design":
            suffix = ".md"
            src_rel_suffix = "/DESIGN.md"
            filename = "DESIGN.md"
        elif target_type == "readme":
            suffix = ".md"
            src_rel_suffix = "/README.md"
            filename = "README.md"
        elif target_type == "skill":
            suffix = ".md"
            src_rel_suffix = "/SKILL.md"
            filename = "SKILL.md"
        else:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"unknown target_type={target_type!r}")

        if passed:
            # 写 src/ · 正常路径
            landing_rel = f"{target_path.rstrip('/')}{src_rel_suffix}"
            terminal_status = "passed"
            quarantined = False
        else:
            # exhausted-not-passed · 隔离 · 不污染 src/
            landing_rel = f"data/services/docauthor/drafts/_quarantine/{slug}/{filename}"
            terminal_status = f"quarantined_at_iter_{iter_num}"
            quarantined = True

        landing_abs = (self._repo_root / landing_rel).resolve()
        try:
            landing_abs.relative_to(self._repo_root)
        except ValueError:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"landing path outside repo_root: {landing_rel}")

        landing_abs.parent.mkdir(parents=True, exist_ok=True)

        # 备份 (仅 passed 写 src/ 时若已有内容)
        backup_info: dict[str, str] = {}
        if passed and landing_abs.exists():
            backup_dir = self._repo_root / "data/services/docauthor/drafts/_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = backup_dir / f"{slug}__{ts}__prev{suffix}"
            if not self._dry_run:
                try:
                    backup_path.write_text(
                        landing_abs.read_text(encoding="utf-8", errors="replace"),
                        encoding="utf-8",
                    )
                    backup_info["backup_path"] = str(backup_path.relative_to(self._repo_root).as_posix())
                except OSError as e:
                    backup_info["backup_error"] = f"{type(e).__name__}: {e}"
            else:
                backup_info["backup_path"] = f"(dry_run · would backup to {backup_path.relative_to(self._repo_root).as_posix()})"

        # 落盘
        written_bytes = 0
        write_status: str
        if self._dry_run:
            write_status = "dry_run"
        else:
            try:
                landing_abs.write_text(draft_content, encoding="utf-8")
                written_bytes = len(draft_content.encode("utf-8"))
                write_status = "quarantined" if quarantined else "written"
            except OSError as e:
                return Verdict(kind=VerdictKind.FAIL,
                               diagnosis=f"write failed: {type(e).__name__}: {e}")

        # 隔离时, 同目录下落 issues.json 含完整 verdict 让 L2 后续诊断
        if quarantined and not self._dry_run:
            import json
            issues_path = landing_abs.parent / "issues.json"
            try:
                issues_payload = {
                    "target_path": target_path,
                    "target_type": target_type,
                    "iter": iter_num,
                    "max_refine_iters": max_iter,
                    "passed": False,
                    "verdict": verdict.get("verdict", "uncertain"),
                    "counts": verdict.get("counts", {}),
                    "issues": verdict.get("issues", []),
                    "llm_notes": verdict.get("llm_notes", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                issues_path.write_text(
                    json.dumps(issues_payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            except OSError:
                pass

        output = {
            "terminal_status": terminal_status,      # passed | quarantined_at_iter_N
            "target_type": target_type,
            "target_path": target_path,
            "landing_rel": landing_rel,
            "write_status": write_status,            # written | quarantined | dry_run
            "written_bytes": written_bytes,
            "iter": iter_num,
            "max_refine_iters": max_iter,
            "passed": passed,
            "quarantined": quarantined,
            "issue_counts": verdict.get("counts") or {},
            "issues": verdict.get("issues") or [],   # 全量保留, 不做压缩
            "llm_notes": verdict.get("llm_notes", ""),
            **backup_info,
        }
        return Verdict(kind=VerdictKind.PASS, output=output)


__all__ = ["FinalLanderWorker"]

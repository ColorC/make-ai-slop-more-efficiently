# [OMNI] origin=claude-code domain=project_atlas/routers ts=2026-06-21 type=router status=active
# [OMNI] summary="确定性 RULE 节点: Intake(space→根+run_dir)、Survey(收线索地图)、Finalize(读 staging 写名录+报告)。"
# [OMNI] why="语义起草(collect)交给带工具 worker(worker.py);本文件只留确定性首中尾。"
# [OMNI] tags=project_atlas,router,intake,survey,finalize
"""project_atlas.run 的确定性 RULE 节点。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import spaces, survey, writer
from .._paths import RUNS_ROOT, STAGING_ROOT, ensure_dirs


def _truthy(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("1", "true", "yes"))


# ── 节点 1: 入题 ────────────────────────────────────────────────────────────
class Intake(Router):
    DESCRIPTION = "入题: 解析 space → 根路径, 建 run_dir"
    FORMAT_IN = "project_atlas.request"
    FORMAT_OUT = "project_atlas.intake"
    REQUIRED_CONTEXT = ["space"]

    def run(self, input_data: Any) -> Verdict:
        req = input_data if isinstance(input_data, dict) else {}
        space = str(req.get("space", "")).strip()
        sp = spaces.resolve(space)
        if not sp:
            return Verdict(kind=VerdictKind.FAIL, output=req,
                           diagnosis=f"未知 space '{space}'(可选: {', '.join(spaces.SPACES)})")
        root = Path(sp["root"])
        if not root.is_dir():
            return Verdict(kind=VerdictKind.FAIL, output=req,
                           diagnosis=f"space '{space}' 根不存在: {root}")
        ensure_dirs()
        run_dir = RUNS_ROOT / f"run_{space}_{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "space": space, "root": str(root), "run_dir": str(run_dir),
                "group": sp.get("group", "other"), "tier": sp.get("tier", "auto"),
                "dry_run": _truthy(req.get("dry_run")),
            },
            diagnosis=f"收集 space '{space}' ({root})",
            granted_tags=["domain.project_atlas", "stage.intake"],
        )


# ── 节点 2: 勘察 ────────────────────────────────────────────────────────────
class Survey(Router):
    DESCRIPTION = "勘察: 确定性收线索地图(顶层目录 + 清单摘要)"
    FORMAT_IN = "project_atlas.intake"
    FORMAT_OUT = "project_atlas.surveyed"
    REQUIRED_CONTEXT = ["space", "root", "run_dir"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        root = Path(ctx["root"])
        run_dir = Path(ctx["run_dir"])
        sheet, top_dirs = survey.gather(root)
        (run_dir / "clues.md").write_text(sheet, encoding="utf-8")
        return Verdict(
            kind=VerdictKind.PASS,
            output={**ctx, "clues": sheet, "top_dirs": top_dirs},
            diagnosis=f"线索 {len(sheet)} 字 · 顶层 {len(top_dirs)} 目录",
            granted_tags=["domain.project_atlas", "stage.surveyed"],
        )


# ── 节点末: 落名录 + 报告(读 worker 写进 staging 的实际产物)─────────────────
class Finalize(Router):
    DESCRIPTION = "落名录 + 报告(以 staging 实际产物为准)"
    FORMAT_IN = "project_atlas.collected"
    FORMAT_OUT = "project_atlas.record"
    REQUIRED_CONTEXT = ["space", "run_dir"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        space = ctx["space"]
        run_dir = Path(ctx["run_dir"])
        staging = STAGING_ROOT / space
        written = sorted(p.parent.name for p in staging.glob("*/SKILL.md")) if staging.exists() else []

        writer.write_atlas_entry({
            "id": space,
            "name": space,
            "group": ctx.get("group", "other"),
            "root": ctx.get("root", ""),
            "object_skills": written,
            "worker_status": ctx.get("worker_status", ""),
        })

        lines = [
            f"# project_atlas 收集报告 · {space}", "",
            f"- object-SKILL(staging, 待人审): **{len(written)}** 份",
            f"- worker_status: {ctx.get('worker_status', '-')}", "",
            "## 已落 staging 的对象", "",
        ]
        lines += [f"- {n}" for n in written]
        rp = run_dir / "report.md"
        rp.write_text("\n".join(lines), encoding="utf-8")

        return Verdict(
            kind=VerdictKind.PASS,
            output={"space": space, "run_dir": str(run_dir), "n_skills": len(written), "report": str(rp)},
            diagnosis=f"{len(written)} 份 object-SKILL 落 staging/{space}(待人审)· 报告 {rp.name}",
            granted_tags=["domain.project_atlas", "stage.record", "kind.sink"],
        )

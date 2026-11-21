# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.semantic_auditor.artifact_collector.worker.python"
"""ArtifactSelectorWorker — SemanticAuditor Team Worker #1.

Worker 协议:
  FORMAT_IN  = semantic_auditor.artifact-request
  FORMAT_OUT = semantic_auditor.artifact-set

职责: 把输入（paths / git-diff / full-scan）转成 Artifact 清单, 每个 Artifact 按
standards-index.yaml.kind_inference 打 kind 标签。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker

from ..standards_loader import load_standards_index, infer_kind


class ArtifactSelectorWorker(Worker):
    """把输入（path 列表 / git-diff / 全扫）转成 Artifact 清单。

    输入形态（择一）：
      - {"paths": ["src/.../foo.py", ...], "project_root": "..."}
      - {"source": "git-diff", "project_root": "..."}       # 读 git 变更
      - {"source": "full-scan", "project_root": "..."}      # 全扫 src/ + docs/

    每个 Artifact 打上 kind（router / design_md / format / ...），kind 由
    standards-index.yaml.kind_inference 推断。无法推断 kind 的文件也保留（kind=None），
    下游 StandardMatcherWorker 会按 path_match 单独判定。
    """

    INPUT_KEYS = ["project_root"]
    DESCRIPTION = (
        "收集待审 artifact：接受 paths 列表 / git-diff / full-scan 三种入口，"
        "按 kind_inference 打 kind 标签，输出 list[Artifact]"
    )
    FORMAT_IN = "semantic_auditor.artifact-request"
    FORMAT_OUT = "semantic_auditor.artifact-set"

    def __init__(self, project_root: str | Path | None = None):
        self._default_root = Path(project_root) if project_root else None

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 必须是 dict"},
            )

        root = Path(
            input_data.get("project_root")
            or (str(self._default_root) if self._default_root else ".")
        )

        try:
            index = load_standards_index(root)
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        paths: list[str] = []
        if "paths" in input_data and isinstance(input_data["paths"], list):
            paths = [str(p).replace("\\", "/") for p in input_data["paths"]]
        else:
            source = str(input_data.get("source", ""))
            if source == "git-diff":
                paths = self._collect_git_diff(root)
            elif source == "full-scan":
                paths = self._collect_full_scan(root)
            else:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output={
                        "reason": "缺少 paths 或 source；source 必须是 git-diff / full-scan",
                    },
                )

        artifacts: list[dict[str, Any]] = []
        for p in paths:
            kind = infer_kind(p, index)
            artifacts.append({"path": p, "kind": kind})

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(root),
                "artifacts": artifacts,
                "artifact_count": len(artifacts),
            },
        )

    def _collect_git_diff(self, root: Path) -> list[str]:
        """读 git status --porcelain 的变更文件。"""
        import subprocess
        try:
            out = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=str(root), text=True, stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []
        paths: list[str] = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            rel = line[3:].strip()
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1]
            paths.append(rel.replace("\\", "/"))
        return paths

    def _collect_full_scan(self, root: Path) -> list[str]:
        """扫 src/ 下 .py + docs/ 下 .md + 就近 DESIGN.md / knowledge/ .md"""
        results: list[str] = []
        for rel_dir in ("src", "docs"):
            d = root / rel_dir
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                if "__pycache__" in p.parts:
                    continue
                if p.suffix not in (".py", ".md", ".yaml"):
                    continue
                rel = str(p.relative_to(root)).replace("\\", "/")
                results.append(rel)
        return results

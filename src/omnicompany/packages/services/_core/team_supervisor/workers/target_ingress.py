# [OMNI] origin=claude-code domain=services/team_supervisor/workers ts=2026-04-26T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_supervisor.workers.target_ingress.loader.py"
"""TargetIngressWorker — team_supervisor Worker #1 (HARD).

Worker 协议:
  FORMAT_IN  = team_supervisor.target_spec
  FORMAT_OUT = team_supervisor.target_metadata

职责: 校 target_team_id 在 PipelineRegistry 注册, 解析 build_team() 抽 FORMAT_IN/OUT material id,
      列出 workers/ 文件, 验证 DESIGN.md/team.py 路径真实存在. 不调 LLM.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

logger = logging.getLogger(__name__)


# 项目根 · target team 包目录在 src/omnicompany/packages/services/<id>/
# __file__ = .../omnicompany/src/omnicompany/packages/services/team_supervisor/workers/target_ingress.py
# parents[6] = .../omnicompany (项目根)
_PROJECT_ROOT = Path(__file__).resolve().parents[6]
_SERVICES_ROOT = _PROJECT_ROOT / "src" / "omnicompany" / "packages" / "services"


def _slug_to_pkg(target_team_id: str) -> str:
    """team_id 'csv-to-md' → package 'csv_to_md'.

    注册 id 用 dash, 包目录用 underscore (omnicompany 惯例).
    """
    return target_team_id.replace("-", "_")


def _build_team_for(target_team_id: str):
    """通过 PipelineRegistry 解析目标 team 的 TeamSpec."""
    from omnicompany.core.registry import discover, get

    discover()
    entry = get(target_team_id)
    if entry is None:
        return None, f"target_team_id '{target_team_id}' 未在 registry 注册"

    try:
        team_spec = entry.build_team()
    except Exception as e:
        return None, f"build_team({target_team_id}) 失败: {type(e).__name__}: {e}"

    return team_spec, None


def _extract_format_ids(team_spec) -> tuple[str, str]:
    """从 TeamSpec 抽出口节点 FORMAT_OUT 与入口节点 FORMAT_IN material id."""
    entry_id = team_spec.entry
    nodes_by_id = {n.id: n for n in team_spec.nodes}

    entry_node = nodes_by_id.get(entry_id)
    format_in = ""
    if entry_node and entry_node.anchor:
        fin = entry_node.anchor.format_in
        format_in = fin if isinstance(fin, str) else (fin[0] if fin else "")

    # 末节点 = 没有 outgoing edge 的节点 (sink)
    out_nodes_with_edges = {e.source for e in team_spec.edges}
    sink_nodes = [n for n in team_spec.nodes if n.id not in out_nodes_with_edges]
    format_out = ""
    if sink_nodes and sink_nodes[0].anchor:
        format_out = sink_nodes[0].anchor.format_out

    return format_in, format_out


class TargetIngressWorker(Worker):
    """装入 target team 元数据, 不调 LLM."""

    DESCRIPTION = (
        "校 target_team_id 在 PipelineRegistry 注册, 解析 build_team() 抽 FORMAT_IN/OUT, "
        "列 workers/ 文件, 验证 DESIGN.md/team.py 路径真实存在."
    )
    FORMAT_IN = "team_supervisor.target_spec"
    FORMAT_OUT = "team_supervisor.target_metadata"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        target_team_id = input_data.get("target_team_id")
        if not target_team_id or not isinstance(target_team_id, str):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="target_team_id 缺失或类型非法",
            )

        team_spec, err = _build_team_for(target_team_id)
        if err is not None:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=err)

        # 推断 target 包目录 (注册 id 'csv-to-md' → 'csv_to_md')
        pkg_name = _slug_to_pkg(target_team_id)
        team_code_dir = _SERVICES_ROOT / pkg_name

        if not team_code_dir.is_dir():
            # 兼容包名直接 = team_id 的场景
            alt = _SERVICES_ROOT / target_team_id
            if alt.is_dir():
                team_code_dir = alt
                pkg_name = target_team_id
            else:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    diagnosis=(
                        f"target team 包目录不存在: 试过 {team_code_dir} 与 {alt}; "
                        "supervisor 仅支持 services/<pkg>/ 布局的 team"
                    ),
                )

        team_design_md = team_code_dir / "DESIGN.md"
        team_py = team_code_dir / "team.py"
        workers_dir = team_code_dir / "workers"

        if not team_py.is_file():
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis=f"team.py 不存在: {team_py}",
            )

        worker_files: list[str] = []
        if workers_dir.is_dir():
            for p in sorted(workers_dir.glob("*.py")):
                if p.name == "__init__.py":
                    continue
                worker_files.append(p.name)

        format_in_id, format_out_id = _extract_format_ids(team_spec)

        # 历史 traces 目录 (注册时声明的 default_db_dir; 简化: data/services/<pkg>/)
        traces_dir = _PROJECT_ROOT / "data" / "services" / pkg_name
        traces_path = str(traces_dir) if traces_dir.is_dir() else ""

        sample_input = input_data.get("sample_input")
        if sample_input is not None and not isinstance(sample_input, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                diagnosis="sample_input 类型非法 · 必须是 dict 或缺失",
            )

        run_count = input_data.get("run_count", 1)
        if not isinstance(run_count, int) or run_count < 1:
            run_count = 1

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "target_team_id": target_team_id,
                "team_code_dir": str(team_code_dir),
                "team_design_md_path": str(team_design_md) if team_design_md.is_file() else "",
                "team_py_path": str(team_py),
                "workers_dir": str(workers_dir) if workers_dir.is_dir() else "",
                "format_out_id": format_out_id,
                "format_in_id": format_in_id,
                "worker_files": worker_files,
                "historical_traces_dir": traces_path,
                "sample_input": sample_input,
                "run_count": run_count,
            },
            diagnosis=(
                f"装载完成: target={target_team_id} · format_out={format_out_id} · "
                f"{len(worker_files)} workers"
            ),
            confidence=1.0,
        )

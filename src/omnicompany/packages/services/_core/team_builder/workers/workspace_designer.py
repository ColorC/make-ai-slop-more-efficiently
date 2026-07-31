# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.workspace_spec_generator.rule_engine.py"
"""WorkspaceDesignerWorker — Phase 5 · HARD 推导 (2026-04-23).

Worker 协议:
  FORMAT_IN  = team_builder.material.team_design
  FORMAT_OUT = team_builder.material.workspace_spec

**职责**: HARD 规则 · 从 team_design 里抽 team_name, 按
`docs/standards/workspace.md` 规范推出 workspace.yaml 的完整声明.

**不调 LLM** · 规则确定性.

**规则**:
  - target_package_path 必须来自已完成路径合同的 team_design
  - write_prefixes = [target_package_path, 由同一路径推导的数据目录]
  - read_prefixes = READ_ANY
  - bash_cwd_prefixes = [<project_root>/]

team_name 来源 (优先级):
  1. team_design.team_name (若 TeamArchitect 产出时填了)
  2. team_design.design_path 反推 (例 services/csv_to_md_pipeline/DESIGN.md → csv_to_md_pipeline)
  3. fallback: "unnamed_team"
"""
from __future__ import annotations

import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._core.team_builder.package_location import (
    canonical_team_package_path,
    team_data_path,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


_TEAM_NAME_PATTERN = re.compile(r"[^a-z0-9_]")


def _slugify(name: str) -> str:
    """team_name 规范化: 小写 + 非 [a-z0-9_] 替为 _."""
    s = name.strip().lower().replace("-", "_").replace(" ", "_")
    s = _TEAM_NAME_PATTERN.sub("_", s)
    # 合并连续 _ + 去首尾 _
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "unnamed_team"


def _extract_team_name(team_design: dict) -> str:
    """从 team_design 推 team_name."""
    # 优先 team_name 字段
    name = team_design.get("team_name") or team_design.get("name")
    if name and isinstance(name, str) and name.strip():
        return _slugify(name)

    # 次选 design_path (例 services/csv_to_md_pipeline/DESIGN.md 或 src/.../<pkg>/DESIGN.md)
    dp = team_design.get("design_path", "")
    if isinstance(dp, str) and dp.strip():
        parts = [p for p in dp.replace("\\", "/").split("/") if p]
        # 找倒数第二段 (DESIGN.md 前面)
        if len(parts) >= 2 and parts[-1].lower().endswith(".md"):
            return _slugify(parts[-2])
        # 若路径尾是 <pkg> 目录
        if parts:
            last = parts[-1]
            if not last.lower().endswith(".md"):
                return _slugify(last)
    return "unnamed_team"


class WorkspaceDesignerWorker(Worker):
    """HARD · 从 team_design 推标准 workspace_spec."""

    DESCRIPTION = (
        "Phase 5 · HARD 推导 · 从 team_design 抽 team_name 按 docs/standards/workspace.md "
        "推规范 workspace.yaml 内容 (write 紧 / read 宽 / bash_cwd 项目根). 不调 LLM."
    )
    FORMAT_IN = "team_builder.material.team_design"
    FORMAT_OUT = "team_builder.material.workspace_spec"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )

        # input_data 可能是 team_design 本体, 也可能平铺了 _from_* 字段
        team_design = input_data.get("_from_team_architect") or input_data
        if not isinstance(team_design, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis="team_design not found in input_data",
            )

        team_name = _extract_team_name(team_design)
        try:
            target_package_path = canonical_team_package_path(
                team_design.get("target_package_path"),
                team_name=team_name,
            )
            data_path = team_data_path(
                target_package_path,
                team_name=team_name,
            )
        except ValueError as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"team package location invalid: {exc}",
            )

        workspace_spec = {
            "name": team_name,
            "write_prefixes": [
                target_package_path,
                data_path,
            ],
            "read_prefixes": "READ_ANY",
            "bash_cwd_prefixes": [""],  # 项目根 (空串, load_workspace 时展开)
            "target_package_path": target_package_path,
        }

        return Verdict(
            kind=VerdictKind.PASS,
            output=workspace_spec,
        )

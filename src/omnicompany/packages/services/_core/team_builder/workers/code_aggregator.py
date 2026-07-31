# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-24T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.code_package_aggregator.eight_way_merge.py"
"""CodeAggregator (Wa9) — 8 路 composite fan-in 合成 code_package (2026-04-24).

合并 upstream:
  _from_formats_generator       → formats.py
  _from_team_file_generator     → team.py
  _from_run_file_generator      → run.py
  _from_package_init_generator  → __init__.py
  _from_workers_init_generator  → workers/__init__.py
  _from_workspace_yaml_generator → .omni/workspace.yaml
  _from_worker_code_orchestrator → workers/*.py × N (bundle)
  _from_design_md_generator     → DESIGN.md

输出 team_builder.material.code_package · 保持与旧 CodeGeneratorLoopWorker 相同契约.

Registrar 上游 py_compile 校验仍在 Registrar 本身做 (不重复此处).
"""
from __future__ import annotations

from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._core.team_builder.package_location import (
    canonical_team_package_path,
)
from omnicompany.protocol.anchor import Verdict, VerdictKind


class CodeAggregator(Worker):
    """Wa9 HARD · 8 路合并 code_package."""

    DESCRIPTION: ClassVar[str] = (
        "CodeGen-Wa9 · HARD 聚合 · 8 路 composite fan-in (6 HARD 文件 + Ws7 workers bundle + Ws8 DESIGN.md) "
        "合并成 code_package.files dict · 交 Registrar."
    )
    FORMAT_IN: ClassVar = [
        "team_builder.material.formats_py",
        "team_builder.material.team_py",
        "team_builder.material.run_py",
        "team_builder.material.pkg_init_py",
        "team_builder.material.workers_init_py",
        "team_builder.material.workspace_yaml",
        "team_builder.material.worker_code_files_bundle",
        "team_builder.material.design_md",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.code_package"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="input_data must be dict")

        # 取每路 upstream payload · key = "_from_<producer_worker_id>"
        # producer worker_id 按 Phase 5 命名约定
        _FROM_KEYS = {
            "formats_generator": "formats.py",
            "team_file_generator": "team.py",
            "run_file_generator": "run.py",
            "package_init_generator": "__init__.py",
            "workers_init_generator": "workers/__init__.py",
            "workspace_yaml_generator": ".omni/workspace.yaml",
            "worker_code_orchestrator": None,   # bundle · 特殊处理
            "design_md_generator": "DESIGN.md",
        }

        files: dict[str, str] = {}
        missing_sources: list[str] = []
        merged_meta: dict[str, Any] = {}

        for from_id, default_rel_path in _FROM_KEYS.items():
            payload = input_data.get(f"_from_{from_id}")
            # fallback · 也允许直接 key 无 _from_ 前缀
            if payload is None:
                payload = input_data.get(from_id)
            if not isinstance(payload, dict):
                missing_sources.append(from_id)
                continue

            if from_id == "worker_code_orchestrator":
                # bundle · files dict 展开
                bundle_files = payload.get("files") or {}
                if isinstance(bundle_files, dict):
                    for rel, content in bundle_files.items():
                        if isinstance(rel, str) and isinstance(content, str):
                            files[rel] = content
                merged_meta["workers_success"] = payload.get("success_count", 0)
                merged_meta["workers_fail"] = payload.get("fail_count", 0)
                merged_meta["lint_summary"] = payload.get("lint_summary", [])
            else:
                rel = payload.get("rel_path") or default_rel_path
                content = payload.get("content") or ""
                if rel and isinstance(content, str):
                    files[rel] = content

        # All producers repeat the canonical location only for transport.  The
        # aggregator requires agreement; it never picks a "first" value or
        # reconstructs a flat services path.
        team_names: set[str] = set()
        targets: set[str] = set()
        for val in input_data.values():
            if not isinstance(val, dict):
                continue
            name = val.get("team_name") or val.get("name")
            if isinstance(name, str) and name.strip():
                team_names.add(name.strip())
            target = val.get("target_package_path")
            if isinstance(target, str) and target.strip():
                targets.add(target.strip())
        direct_name = input_data.get("team_name") or input_data.get("name")
        if isinstance(direct_name, str) and direct_name.strip():
            team_names.add(direct_name.strip())
        direct_target = input_data.get("target_package_path")
        if isinstance(direct_target, str) and direct_target.strip():
            targets.add(direct_target.strip())

        if len(team_names) != 1:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=(
                    "code generator outputs require exactly one team_name; "
                    f"got {sorted(team_names)}"
                ),
            )
        team_name = next(iter(team_names))
        try:
            normalized_targets = {
                canonical_team_package_path(target, team_name=team_name)
                for target in targets
            }
        except ValueError as exc:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"team package location invalid: {exc}",
            )
        if len(normalized_targets) != 1:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=(
                    "code generator outputs require one agreed "
                    f"target_package_path; got {sorted(normalized_targets)}"
                ),
            )
        target = next(iter(normalized_targets))

        if not files:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "team_name": team_name,
                    "target_package_path": target,
                    "files": {},
                    "_meta": {"missing_sources": missing_sources},
                },
                diagnosis=f"所有 upstream 路都缺 · missing={missing_sources}",
            )

        total_bytes = sum(len(c) for c in files.values() if isinstance(c, str))
        output = {
            "team_name": team_name,
            "target_package_path": target,
            "files": files,
            "_meta": {
                "worker": "CodeAggregator",
                "stage": "v3_2_sub_team",
                "file_count": len(files),
                "total_bytes": total_bytes,
                "missing_sources": missing_sources,
                **merged_meta,
            },
        }

        kind = VerdictKind.PASS if not missing_sources else VerdictKind.PARTIAL
        return Verdict(
            kind=kind,
            output=output,
            diagnosis=(
                f"code_package · {len(files)} files · {total_bytes} bytes · "
                f"missing_sources={missing_sources}"
            ),
        )

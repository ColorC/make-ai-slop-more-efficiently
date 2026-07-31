# [OMNI] origin=codex domain=services/_core/team_builder ts=2026-07-25T00:00:00Z type=contract
# [OMNI] summary="Rebuild a dry-run registration plan at one canonical package location"
# [OMNI] why="Correct location metadata without repeating Team Builder LLM work"
"""Deterministic location repair for an un-deployed Team registration plan."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

import yaml

from omnicompany.packages.services._core.team_builder.package_location import (
    canonical_package_file_path,
    canonical_team_package_path,
    team_data_path,
    team_omni_domain,
)
from omnicompany.packages.services._core.team_builder.workers.registrar import (
    RegistrarWorker,
)
from omnicompany.protocol.anchor import VerdictKind


_OMNI_HEADER_RE = re.compile(
    r"^(?P<prefix># \[OMNI\][^\r\n]*?\bdomain=)"
    r"(?P<domain>[^\s]+)"
    r"(?P<suffix>[^\r\n]*)",
)
_LEGACY_IMPLICIT_RETRY_BLOCK = (
    "            VerdictKind.PASS: Route(action=RouteAction.NEXT),\n"
    "            VerdictKind.FAIL: "
    "Route(action=RouteAction.RETRY, max_retries=1),\n"
)
_CONSERVATIVE_HALT_BLOCK = (
    "            VerdictKind.PASS: Route(action=RouteAction.NEXT),\n"
    "            VerdictKind.FAIL: Route(action=RouteAction.HALT),\n"
    "            VerdictKind.PARTIAL: Route(action=RouteAction.HALT),\n"
)


def _file_omni_domain(
    *,
    package_domain: str,
    rel_path: str,
) -> str:
    if rel_path == "testmap.yaml":
        return package_domain
    if rel_path.endswith(".py"):
        return f"{package_domain}/{rel_path[:-3]}"
    return package_domain


def _rewrite_omni_header(
    content: str,
    *,
    package_domain: str,
    rel_path: str,
) -> str:
    match = _OMNI_HEADER_RE.match(content)
    if not match:
        return content
    domain = _file_omni_domain(
        package_domain=package_domain,
        rel_path=rel_path,
    )
    return (
        f"{match.group('prefix')}{domain}{match.group('suffix')}"
        f"{content[match.end():]}"
    )


def _workspace_yaml(
    *,
    team_name: str,
    target_package_path: str,
) -> str:
    return yaml.safe_dump(
        {
            "name": team_name,
            "write_prefixes": [
                target_package_path,
                team_data_path(
                    target_package_path,
                    team_name=team_name,
                ),
            ],
            "read_prefixes": "READ_ANY",
            "bash_cwd_prefixes": [""],
            "target_package_path": target_package_path,
        },
        allow_unicode=True,
        sort_keys=False,
    )


def rebuild_registration_plan_location(
    plan: Mapping[str, Any],
    *,
    target_package_path: str,
    halt_on_non_pass: bool = False,
) -> dict[str, Any]:
    """Return a new dry-run plan without calling an LLM or writing files."""

    source = deepcopy(dict(plan))
    if source.get("dry_run") is not True:
        raise ValueError("only an un-deployed dry-run registration plan may be rebuilt")
    team_name = source.get("team_name")
    if not isinstance(team_name, str) or not team_name.strip():
        raise ValueError("registration plan team_name is required")
    target = canonical_team_package_path(
        target_package_path,
        team_name=team_name,
    )
    raw_files = source.get("files")
    if not isinstance(raw_files, Mapping) or not raw_files:
        raise ValueError("registration plan files mapping is required")

    package_domain = team_omni_domain(target, team_name=team_name)
    files: dict[str, str] = {}
    for raw_rel_path, raw_content in raw_files.items():
        rel_path = canonical_package_file_path(raw_rel_path)
        if not isinstance(raw_content, str):
            raise ValueError(
                f"registration plan file content must be text: {rel_path}"
            )
        files[rel_path] = _rewrite_omni_header(
            raw_content,
            package_domain=package_domain,
            rel_path=rel_path,
        )

    files[".omni/workspace.yaml"] = _workspace_yaml(
        team_name=team_name,
        target_package_path=target,
    )
    if halt_on_non_pass:
        team_source = files.get("team.py")
        if not isinstance(team_source, str):
            raise ValueError("team.py is required for conservative route repair")
        replacement_count = team_source.count(_LEGACY_IMPLICIT_RETRY_BLOCK)
        if replacement_count < 1:
            raise ValueError(
                "legacy implicit retry template was not found; "
                "refusing a non-exact route rewrite"
            )
        files["team.py"] = team_source.replace(
            _LEGACY_IMPLICIT_RETRY_BLOCK,
            _CONSERVATIVE_HALT_BLOCK,
        )

    verdict = RegistrarWorker().run(
        {
            "team_name": team_name,
            "target_package_path": target,
            "files": files,
        }
    )
    if verdict.kind != VerdictKind.PASS or not isinstance(verdict.output, dict):
        raise ValueError(
            "rebuilt registration plan failed Registrar validation: "
            f"{verdict.diagnosis}"
        )
    result = dict(verdict.output)
    result["notes"] = [
        *list(result.get("notes") or []),
        (
            "本计划由已有 dry-run 候选确定性重建部署位置；"
            "未重新调用 Team Builder LLM，未落盘、未注册、未执行"
        ),
        *(
            [
                (
                    "旧生成器写死的隐式重试已确定性改为 "
                    "FAIL/PARTIAL 停止；未执行 Worker"
                )
            ]
            if halt_on_non_pass
            else []
        ),
    ]
    return result


__all__ = ["rebuild_registration_plan_location"]

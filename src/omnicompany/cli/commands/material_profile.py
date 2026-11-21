# [OMNI] origin=codex domain=cli/material_profile ts=2026-08-19T00:00:00+08:00 type=cli status=active
# [OMNI] summary="omni material profile：按项目发现分类权威、读取一棵树并生成已校验分类引用"
# [OMNI] material_id="material:cli.material_profile.command.py"
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.material_profiles import (
    MaterialClassificationProfileError,
    MaterialProfileRegistry,
)

from .._access import any_caller


def _registry(manifest: Path | None = None) -> MaterialProfileRegistry:
    root = omni_workspace_root()
    return MaterialProfileRegistry(
        manifest or root / "config" / "material_profiles.yaml",
        omnicompany_root=root,
    )


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2))


def _parse_facets(values: tuple[str, ...]) -> dict[str, list[tuple[str, ...]]]:
    result: dict[str, list[tuple[str, ...]]] = {}
    for value in values:
        facet_id, separator, raw_path = value.partition("=")
        if not separator or not facet_id.strip() or not raw_path.strip():
            raise MaterialClassificationProfileError("--facet 必须是 FACET_ID=path/to/leaf")
        result.setdefault(facet_id.strip(), []).append(tuple(part for part in raw_path.split("/") if part))
    return result


@click.group("profile")
def cmd_material_profile() -> None:
    """域自持的 Material 主分类树与次要标签树。"""


@cmd_material_profile.command("list")
@click.option("--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
@any_caller
def material_profile_list(manifest: Path | None, as_json: bool) -> None:
    """只列 profile 指针；不会聚合或输出任何接入方完整分类树。"""
    try:
        payload = {
            "schema": "omni.material-classification-profile-list/v1",
            "profiles": [item.to_mapping() for item in _registry(manifest).list()],
        }
        _emit(payload, as_json)
    except (MaterialClassificationProfileError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@cmd_material_profile.command("show")
@click.argument("owner")
@click.option("--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
@any_caller
def material_profile_show(owner: str, manifest: Path | None, as_json: bool) -> None:
    """显式读取一个项目/profile 的 Agent 分类上下文。"""
    try:
        _emit(_registry(manifest).agent_context(owner), as_json)
    except (MaterialClassificationProfileError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@cmd_material_profile.command("classify")
@click.argument("owner")
@click.option("--primary", required=True, help="主分类完整路径，例如 action/process-step。")
@click.option("--facet", "facet_values", multiple=True, help="FACET_ID=path/to/leaf；many 可重复。")
@click.option("--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
@any_caller
def material_profile_classify(
    owner: str,
    primary: str,
    facet_values: tuple[str, ...],
    manifest: Path | None,
    as_json: bool,
) -> None:
    """按项目权威树校验并生成可存入 Material metadata 的引用。"""
    try:
        registry = _registry(manifest)
        profile = registry.load(owner)
        reference = profile.classify(
            tuple(part for part in primary.split("/") if part),
            _parse_facets(facet_values),
        ).to_mapping()
        context_ref = registry.context_ref(owner, read_via="omni material profile classify")
        _emit({
            "ok": True,
            "classification_ref": reference,
            "classification_context_ref": context_ref,
            "material_metadata": {
                "classification_ref": reference,
                "classification_context_ref": context_ref,
            },
        }, as_json)
    except (MaterialClassificationProfileError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

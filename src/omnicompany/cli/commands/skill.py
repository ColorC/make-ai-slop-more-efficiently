# [OMNI] origin=codex domain=omnicompany/cli ts=2026-10-04 type=cli status=active
"""Unified skill routing commands."""

from __future__ import annotations

import json
from pathlib import Path

import click


@click.group("skill")
def cmd_skill() -> None:
    """Resolve one skill path across Claude, Codex and shared roots."""


@cmd_skill.command("resolve")
@click.argument("query")
@click.option("--cwd", type=click.Path(path_type=Path), default=None,
              help="按项目工作目录选择项目级来源")
@click.option("--limit", type=click.IntRange(min=1, max=50), default=8, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="输出机器可读 JSON")
def cmd_skill_resolve(query: str, cwd: Path | None, limit: int, as_json: bool) -> None:
    """把同名 skill 归并为一个 canonical 路径。"""
    from omnicompany.core.skills_registry import resolve_skills

    matches = resolve_skills(query, cwd=cwd, limit=limit)
    payload = {"query": query, "cwd": str(cwd or Path.cwd()), "matches": matches, "total": len(matches)}
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not matches:
        click.echo(f"(没有找到 skill: {query})")
        return
    for item in matches:
        click.echo(f"{item['name']}  →  {item['canonical_path']}  (copies: {item['duplicate_count']})")
        if item.get("project", {}).get("index_path"):
            click.echo(f"  project index: {item['project']['index_path']}")
        for warning in item.get("warnings", []):
            click.echo(f"  warning: {warning}")


__all__ = ["cmd_skill"]

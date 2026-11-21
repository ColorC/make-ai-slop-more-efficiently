# [OMNI] origin=ai-ide domain=cli/commands ts=2026-05-02T08:30:00Z type=router status=active agent=ai-ide-current
# [OMNI] summary="omni guardian check-tag-consistency - OmniMark 头 tags vs 文档体 hashtag 一致性扫描"
# [OMNI] why="tag 字典 v1 (tags_and_wikilinks.md) 立了, 守护要扫两边 (机器扫的 tags + 人看的 hashtag) 一致"
# [OMNI] tags=cli,guardian,tag-consistency,scan
# [OMNI] material_id="material:cli.commands.guardian.tag_consistency_scanner.py"
"""omni guardian 扩规则.

本模块加入 omni guardian 命令组的子命令:
  omni guardian check-tag-consistency  - 扫 OmniMark 头 tags vs 文档体 hashtag 不一致

关联:
- standards/_global/tags_and_wikilinks.md
- 现有 cmd_guardian (cli/commands/guardian.py)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import click


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[3]


def _walk_md_files(root: Path) -> Iterator[Path]:
    """遍历 docs/ + templates/ 内的 .md 文件."""
    for d in (root / "docs", root / "templates"):
        if not d.is_dir():
            continue
        for f in d.rglob("*.md"):
            if "_archive" in f.parts or "_graveyard" in f.parts:
                continue
            yield f


def _extract_omnimark_tags(text: str) -> set[str]:
    """抓 OmniMark 头 tags=foo,bar 字段."""
    tags: set[str] = set()
    for m in re.finditer(r"tags=([^\s\"\>\-]+)", text[:4096]):
        for t in m.group(1).split(","):
            t = t.strip()
            if t:
                tags.add(t)
    return tags


def _extract_hashtags(text: str) -> set[str]:
    """抓文档体里的 #tag 跟 #category/sub. 跳过代码块内的 #."""
    hashtags: set[str] = set()
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        # 识别 #tag (字母数字 + 下划线 + /), 不接 ##
        for m in re.finditer(r"(?<![#\w])#([a-zA-Z][\w/]*)", line):
            hashtags.add(m.group(1))
    return hashtags


def cmd_check_tag_consistency_impl(verbose: bool = False, limit: int = 50) -> dict:
    proj = _project_root()
    issues: list[dict] = []
    scanned = 0
    for f in _walk_md_files(proj):
        scanned += 1
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        head_tags = _extract_omnimark_tags(text)
        body_hashtags = _extract_hashtags(text)
        if not head_tags and not body_hashtags:
            continue  # 文档没用 tag 系统
        # 主要不一致: 头有 X 但体没 #X (反之亦然)
        only_head = head_tags - body_hashtags
        only_body = body_hashtags - head_tags
        if only_head or only_body:
            issues.append({
                "file": str(f.relative_to(proj)).replace("\\", "/"),
                "head_tags": sorted(head_tags),
                "body_hashtags": sorted(body_hashtags),
                "only_in_head": sorted(only_head),
                "only_in_body": sorted(only_body),
            })
    return {
        "scanned": scanned,
        "issue_count": len(issues),
        "issues": issues[:limit],
    }


@click.command("check-tag-consistency")
@click.option("--limit", type=int, default=20)
@click.option("--json", "as_json", is_flag=True)
def cmd_check_tag_consistency(limit: int, as_json: bool) -> None:
    """扫 docs/ + templates/ 的 .md 文件, 看 OmniMark 头 tags vs 文档体 #hashtag 一致性.

    规范: docs/standards/_global/tags_and_wikilinks.md
    """
    import json
    result = cmd_check_tag_consistency_impl(limit=limit)
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    click.echo(f"扫描 {result['scanned']} 份 .md, 发现 {result['issue_count']} 处 tag 不一致")
    for issue in result["issues"][:limit]:
        click.echo(f"  {issue['file']}")
        if issue["only_in_head"]:
            click.echo(f"    [head only]: {issue['only_in_head']}")
        if issue["only_in_body"]:
            click.echo(f"    [body only]: {issue['only_in_body']}")
    if result["issue_count"] > limit:
        click.echo(f"  ... 还有 {result['issue_count'] - limit} 处")

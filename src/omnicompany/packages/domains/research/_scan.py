# [OMNI] origin=ai-ide domain=research ts=2026-06-14T00:00:00Z type=scanner status=active
# [OMNI] summary="参考项目扫描器:扫 C:/workspace/参考项目 的本地 repo/资料,确定性抽 名字/路径/来源URL/描述/别名,产 catalog 条目。"
# [OMNI] why="手写 INDEX.md 必馊必漏(漏 19/34)。扫目录不靠人记 —— 读 .git/config remote + README 首段 + package.json,自动盘点。"
# [OMNI] tags=research,scan,catalog,reference
"""参考项目扫描器 —— 把仓外的本地参考资产盘成 catalog 条目(确定性,无模型)。

每个一级子目录: 读 .git/config 的 origin remote(provenance 铁证)+ README 首段 + package.json。
容器目录(自身无 .git 但子目录是 repo,如 agents/、figma_ui/)下潜一层,逐子 repo 出条目。
顶层 *.md/*.txt 归 kind=material。根路径 OMNI_REFS_ROOT env 覆盖。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_SKIP = {".git", "node_modules", "__pycache__", "_archive", ".idea", ".vscode"}
_MATERIAL_EXT = {".md", ".txt", ".pdf", ".docx"}


def refs_root() -> Path:
    """参考项目扫描根 — env 驱动 (OMNI_REFS_ROOT), 缺失即报错。

    不在代码留绝对路径默认 (原值含中文目录名, 换机必断)。开发机在 .env
    配置 OMNI_REFS_ROOT 维持现用法 (见 .env.example)。
    """
    env = os.environ.get("OMNI_REFS_ROOT", "").strip()
    if not env:
        raise RuntimeError(
            "本功能需要参考项目目录: 请设置环境变量 OMNI_REFS_ROOT (见 .env.example)。"
        )
    return Path(env)


def _sanitize_remote(url: str) -> str:
    """剥掉 url 里的 userinfo(https://user:token@host → https://host),别把凭据写进可 grep 的 catalog。"""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1", url or "")


def _git_remote(d: Path) -> str:
    cfg = d / ".git" / "config"
    if not cfg.is_file():
        return ""
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text, re.S)
    if m:
        return _sanitize_remote(m.group(1).strip())
    # origin 段无 url: 仅当全文件恰好只有一个 url= 才采用(否则会抓到别的 remote 的 url 当 origin)
    urls = re.findall(r"url\s*=\s*(\S+)", text)
    return _sanitize_remote(urls[0].strip()) if len(urls) == 1 else ""


def _repo_name_from_url(url: str) -> str:
    m = re.search(r"[/:]([^/]+?)(?:\.git)?/?$", url or "")
    return m.group(1) if m else ""


def _readme_first(d: Path) -> str:
    for name in ("README.md", "README_CN.md", "README.zh.md", "readme.md", "README.rst", "README.txt", "README"):
        p = d / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for para in re.split(r"\n\s*\n", text):
            c = para.strip()
            c = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", c)        # 图片
            c = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", c)     # 链接→文字
            c = re.sub(r"<[^>]+>", " ", c)                      # HTML 标签(居中 logo/徽章头的 README 常见,先剥)
            c = re.sub(r"^#+\s*", "", c)                        # 标题井号
            c = re.sub(r"[`*_>]+", "", c)
            c = re.sub(r"\s+", " ", c).strip()                  # 合并空白
            if len(c) > 12:
                return c[:300]
        return ""
    return ""


def _pkg(d: Path) -> dict:
    p = d / "package.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_repo(d: Path) -> bool:
    return (d / ".git").exists()


def _repo_item(d: Path, id_prefix: str = "") -> dict:
    name = d.name
    remote = _git_remote(d)
    pkg = _pkg(d)
    desc = (pkg.get("description") or "").strip() or _readme_first(d)
    repo_name = _repo_name_from_url(remote)
    aliases = sorted({a for a in (name, repo_name, str(pkg.get("name") or "")) if a})
    kws = [k for k in (pkg.get("keywords") or []) if isinstance(k, str)][:8]
    rid = f"repo:{id_prefix}{name}" if id_prefix else f"repo:{name}"
    return {
        "id": rid, "kind": "repo", "name": name, "path": str(d),
        "description": desc[:300], "aliases": aliases, "source_url": remote,
        "tags": ["reference", "repo"] + kws,
    }


def scan_reference_repos(root: Path | str | None = None) -> list[dict]:
    """扫参考项目根,产 catalog 条目列表(repo/material)。容器目录下潜一层。"""
    base = Path(root) if root else refs_root()
    items: list[dict] = []
    if not base.is_dir():
        return items
    for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        name = entry.name
        if name in _SKIP or name.startswith("."):
            continue
        if entry.is_file():
            if entry.suffix.lower() in _MATERIAL_EXT:
                items.append({
                    "id": f"material:{name}", "kind": "material", "name": name,
                    "path": str(entry), "description": "", "aliases": [name],
                    "source_url": "", "tags": ["reference", "material"],
                })
            continue
        # 目录
        if _is_repo(entry):
            items.append(_repo_item(entry))
            continue
        # 容器目录: 自身无 .git,但子目录是 repo → 下潜一层
        children = [c for c in entry.iterdir() if c.is_dir() and c.name not in _SKIP and _is_repo(c)] \
            if entry.is_dir() else []
        if children:
            for c in sorted(children, key=lambda p: p.name.lower()):
                items.append(_repo_item(c, id_prefix=f"{name}/"))
        else:
            # 普通资料目录(figma 集合、文件集等)
            items.append({
                "id": f"material:{name}", "kind": "material", "name": name,
                "path": str(entry), "description": _readme_first(entry)[:300],
                "aliases": [name], "source_url": "", "tags": ["reference", "material"],
            })
    return items

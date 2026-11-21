# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=helper status=active
# [OMNI] summary="survey:确定性地从一个工作空间收线索(顶层目录 + 清单文件摘要),喂给 classify 做语义归类。"
# [OMNI] why="收集本就不能自动扫到底(评审),survey 只做确定性的'线索归集',语义归类交 classify;有界(剪枝+封顶)防超时。"
# [OMNI] tags=project_atlas,survey
"""project_atlas survey —— 确定性线索归集。"""

from __future__ import annotations

import json
import os
from pathlib import Path

# 剪枝目录(构建/缓存/运行态/产物/二进制重灾区)
_PRUNE = {
    "node_modules", ".git", "venv", ".venv", "data", "dist", "dist-ssr", "build",
    "target", "__pycache__", ".pytest_cache", ".pytest_tmp", ".ruff_cache", ".worktrees",
    "logs", "temp", "tmp", "captures", "uploads", "gen", "_archive", "_scratch", "_backups",
    ".idea", ".vscode", ".agent_state", ".omni", ".playwright-mcp", "coverage",
}
# 线索文件(清单/说明/契约)
_CLUE_FILES = {"SKILL.md", "DESIGN.md", "README.md", "package.json", "pyproject.toml",
               "AGENTS.md", "CLAUDE.md"}
_MAX_FILES = 170
_MAX_CHARS = 28000


def _clue_from(path: Path) -> str:
    """从一个清单文件抽一条短线索。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    name = path.name
    if name == "package.json":
        try:
            j = json.loads(text)
            scripts = ", ".join(list((j.get("scripts") or {}).keys())[:8])
            return f"name={j.get('name', '')} desc={j.get('description', '')} scripts=[{scripts}]"[:400]
        except Exception:
            return ""
    if name == "pyproject.toml":
        lines = [ln.strip() for ln in text.splitlines()
                 if ln.strip().startswith(("name", "description"))]
        return " ".join(lines[:3])[:300]
    # md 类:优先 frontmatter description,否则首标题 + 首段
    desc = ""
    for ln in text.splitlines():
        ls = ln.strip()
        if ls.lower().startswith("description:"):
            desc = ls.split(":", 1)[1].strip().lstrip(">|").strip()
            break
    if desc:
        return desc[:400]
    head = ""
    para = ""
    for ln in text.splitlines():
        ls = ln.strip()
        if not ls or ls.startswith("---"):
            continue
        if ls.startswith("#") and not head:
            head = ls.lstrip("# ").strip()
            continue
        if head and not ls.startswith(("#", "```", ">", "|", "-", "*", "<")):
            para = ls
            break
    if head:
        return (head + (" — " + para if para else ""))[:400]
    return para[:400]


def gather(root: Path) -> tuple[str, list[str]]:
    """返回 (线索清单文本, 顶层目录名列表)。有界:剪枝 + 文件数/字数封顶。"""
    top_dirs: list[str] = []
    try:
        top_dirs = sorted([p.name for p in root.iterdir()
                           if p.is_dir() and p.name not in _PRUNE and not p.name.startswith(".")])
    except OSError:
        pass

    lines: list[str] = []
    total = 0
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _PRUNE and not d.startswith(".")]
        for f in files:
            if f not in _CLUE_FILES:
                continue
            p = Path(cur) / f
            clue = _clue_from(p)
            if not clue:
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                rel = p.name
            line = f"- {rel}: {clue}"
            lines.append(line)
            total += len(line)
            if len(lines) >= _MAX_FILES or total >= _MAX_CHARS:
                break
        if len(lines) >= _MAX_FILES or total >= _MAX_CHARS:
            break

    sheet = (f"顶层目录: {', '.join(top_dirs)}\n\n"
             f"清单摘要({len(lines)} 条):\n" + "\n".join(lines))
    return sheet, top_dirs

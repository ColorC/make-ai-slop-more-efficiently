"""落地层 ↔ vilo wiki 的保留式写回(取舍 1A:wiki/cards|events 是游戏内容单一真源)。

narrative_studio 编辑落地层 GameText → 写回对应 md,只更新建模的段
(文案/正文 body、创作者批注 annotations),**保留未建模的段**(frontmatter、
基础、元素、卡图、关联事件、选择结构等),避免破坏游戏内容文件。
新条目按模板新建到 wiki/cards|events(或 drafts)。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .models import GameText

# text_type → wiki 子目录
_DIR = {"card": "cards", "event": "events", "tag": "tags", "wiki": "wiki-entries"}
# text_type → 正文段名
_BODY_SECTION = {"card": "文案", "event": "正文", "tag": "文案", "wiki": "正文"}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        # replace 失败时清掉临时文件,避免 .tmp 残留被同步/git 误捡
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _replace_section(text: str, section: str, new_content: Optional[str]) -> str:
    """替换 '## <section>' 段的内容(到下一个 '## ' 或文末);段不存在则追加。

    幂等:group1 只捕获标题行(不吞后续空行),写回固定 标题\\n + \\n + 内容 + \\n\\n,
    再次解析→序列化收敛(连续写两次逐字节相等)。
    """
    content = (new_content or "").strip()
    pattern = re.compile(
        r"(^##\s+" + re.escape(section) + r"[^\n]*\n)(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    def _repl(m: "re.Match[str]") -> str:
        return m.group(1) + (("\n" + content + "\n\n") if content else "\n")

    new, n = pattern.subn(_repl, text, count=1)
    if n == 0:
        new = text.rstrip() + f"\n\n## {section}\n\n{content}\n"
    return new


def _target_path(repo: Path, gt: GameText) -> Path:
    """决定写回路径:优先沿用 provenance.source(防穿越);否则按 类型+id 新建(稳定,改 title 不生孤儿)。"""
    repo = Path(repo)
    src = gt.provenance.source if gt.provenance else None
    if src:
        norm = src.replace("\\", "/")
        # 防路径穿越:必须在 wiki/ 下,且无 .. 段
        if norm.startswith("wiki/") and ".." not in norm.split("/"):
            return repo / norm
        raise ValueError(f"非法 provenance.source(必须在 wiki/ 下、无 ..): {src}")
    sub = _DIR.get(gt.text_type, "cards")
    base = repo / "wiki"
    if gt.is_draft:
        base = base / "drafts"
    # 用稳定 id 派生文件名(改 title 不会产生新文件/孤儿)
    fname = (gt.id or gt.title or "untitled").strip().replace("/", "-") + ".md"
    return base / sub / fname


def _new_template(gt: GameText) -> str:
    """新条目的 md 骨架(含 frontmatter + 必要段)。"""
    body_sec = _BODY_SECTION.get(gt.text_type, "文案")
    fm = [f'id: "{gt.id}"', f"type: {gt.text_type}"]
    if gt.category:
        fm.append(f'category: "{gt.category}"')
    if gt.host:
        fm.append(f'host: "{gt.host}"')
    fm.append("tags:\n  - vilo/" + gt.text_type)
    parts = [
        "---\n" + "\n".join(fm) + "\n---\n",
        f"# {gt.title or gt.id}\n",
        f"## {body_sec}\n\n{(gt.body or '').strip()}\n",
    ]
    if gt.choices:
        sel = ["## 选择\n"]
        for ch in gt.choices:
            sel.append(f"### {ch.label or ch.id or ''}\n")
            if ch.id:
                sel.append(f"- id: `{ch.id}`\n")
            if ch.body:
                sel.append((ch.body or "").strip() + "\n")
        parts.append("\n".join(sel))
    parts.append(f"## 创作者批注\n\n{(gt.annotations or '').strip()}\n")
    return "\n".join(parts)


def write_game_text(repo: str | os.PathLike, gt: GameText) -> str:
    """把一条 GameText 写回 vilo wiki。返回写入的相对路径。"""
    repo = Path(repo)
    path = _target_path(repo, gt)
    body_sec = _BODY_SECTION.get(gt.text_type, "文案")

    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = _replace_section(text, body_sec, gt.body)
        text = _replace_section(text, "创作者批注", gt.annotations)
    else:
        text = _new_template(gt)

    _atomic_write(path, text)
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def delete_game_text(repo: str | os.PathLike, gt: GameText) -> bool:
    """删除落地层条目对应的 wiki md(游戏内容随之移除)。"""
    repo = Path(repo)
    path = _target_path(repo, gt)
    if path.exists():
        path.unlink()
        return True
    return False

# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-20T00:00:00Z type=helper status=active
# [OMNI] summary="确定性渲染器: slide IR(JSON) -> Slidev Markdown。封装本域的 Slidev 约定(v-click/magic-move/mermaid/布局)。"
# [OMNI] why="IR-first 的'render_slidev'节点核心。内容与表现解耦: LLM 只产结构化 IR, 这里确定性翻成会动的 Slidev。"
# [OMNI] tags=slidecast,render,slidev,ir
"""slide IR -> Slidev Markdown。

IR 形态(deck):
  {
    "meta": {"title": str, "subtitle": str, "info": str},
    "slides": [ slide, ... ]
  }
slide.layout ∈ cover | statement | bullets | two-col | big-stat | code | mermaid | magic-move | quote | end
各 layout 用到的字段见 _render_slide。
"""

from __future__ import annotations

from typing import Any


def _s(v: Any) -> str:
    return ("" if v is None else str(v)).replace("\r", "").strip()


def _front(meta: dict) -> str:
    title = _s(meta.get("title")) or "演示"
    info = _s(meta.get("info")) or "由 slidecast 自动生成"
    return (
        "---\n"
        "theme: seriph\n"
        f"title: {title}\n"
        f"info: {info}\n"
        "class: text-center\n"
        "transition: slide-left\n"
        "highlighter: shiki\n"
        "mdc: true\n"
        "fonts:\n"
        "  provider: none\n"
        '  sans: "Microsoft YaHei, PingFang SC, sans-serif"\n'
        '  serif: "Microsoft YaHei, PingFang SC, serif"\n'
        '  mono: "Cascadia Code, Consolas, monospace"\n'
        "---"
    )


def _v_clicks(items: list) -> str:
    items = [i for i in (items or []) if _s(i)]
    if not items:
        return ""
    out = ["<v-clicks>", ""]
    out += [f"- {_s(i)}" for i in items]
    out += ["", "</v-clicks>"]
    return "\n".join(out)


def _render_slide(s: dict) -> tuple[str, dict]:
    """返回 (markdown 正文, 该页的 per-slide frontmatter 键值)。"""
    layout = _s(s.get("layout")) or "bullets"
    title = _s(s.get("title"))
    lead = _s(s.get("lead"))
    note = _s(s.get("note"))
    fm: dict[str, str] = {}
    parts: list[str] = []

    if layout == "cover":
        parts.append(f"# {title}")
        if _s(s.get("subtitle")):
            parts.append(f"\n## {_s(s.get('subtitle'))}")
        if lead:
            parts.append(f'\n<div class="text-xl opacity-75 mt-4">\n\n{lead}\n\n</div>')
        if _s(s.get("info")):
            parts.append(f'\n<div class="abs-br m-6 text-sm opacity-50">\n{_s(s.get("info"))}\n</div>')

    elif layout in ("statement", "quote"):
        fm["layout"] = "center"
        fm["class"] = "text-center"
        if layout == "quote":
            q = _s(s.get("quote")) or title
            parts.append(f'<div class="text-3xl leading-relaxed max-w-3xl">\n\n“{q}”\n\n</div>')
            if _s(s.get("cite")):
                parts.append(f'\n<div class="mt-6 opacity-60">— {_s(s.get("cite"))}</div>')
        else:
            if title:
                parts.append(f"# {title}")
            if lead:
                parts.append(f'\n<div class="text-2xl opacity-80 mt-4">\n\n{lead}\n\n</div>')
            bc = _v_clicks(s.get("bullets"))
            if bc:
                parts.append("\n" + bc)

    elif layout == "big-stat":
        fm["layout"] = "center"
        fm["class"] = "text-center"
        if title:
            parts.append(f"# {title}")
        stat = _s(s.get("stat"))
        parts.append(f'\n<div class="text-7xl font-bold mt-6 text-teal-400">{stat}</div>')
        if _s(s.get("stat_label")):
            parts.append(f'\n<div class="text-2xl opacity-70 mt-2">{_s(s.get("stat_label"))}</div>')
        if _s(s.get("stat_sub")):
            parts.append(f'\n<div class="text-lg opacity-50 mt-4">{_s(s.get("stat_sub"))}</div>')

    elif layout == "two-col":
        fm["layout"] = "two-cols"
        if title:
            parts.append(f"# {title}")
        if lead:
            parts.append(f"\n{lead}")
        parts.append("\n" + (_v_clicks(s.get("left")) or ""))
        parts.append("\n::right::\n")
        parts.append(_v_clicks(s.get("right")) or "")

    elif layout == "code":
        if title:
            parts.append(f"# {title}")
        if lead:
            parts.append(f"\n{lead}")
        lang = _s(s.get("lang")) or "text"
        code = _s(s.get("code"))
        parts.append(f"\n```{lang}\n{code}\n```")
        bc = _v_clicks(s.get("bullets"))
        if bc:
            parts.append("\n" + bc)

    elif layout == "mermaid":
        if title:
            parts.append(f"# {title}")
        if lead:
            parts.append(f"\n{lead}")
        mm = _s(s.get("mermaid"))
        parts.append("\n```mermaid {scale: 0.8}\n" + mm + "\n```")

    elif layout == "magic-move":
        if title:
            parts.append(f"# {title}")
        if lead:
            parts.append(f"\n{lead}")
        frames = [f for f in (s.get("frames") or []) if _s(f)]
        lang = _s(s.get("lang")) or "text"
        if frames:
            block = ["\n````md magic-move"]
            for fr in frames:
                block.append(f"```{lang}\n{_s(fr)}\n```")
            block.append("````")
            parts.append("\n".join(block))

    elif layout == "end":
        fm["layout"] = "center"
        fm["class"] = "text-center"
        if title:
            parts.append(f"# {title}")
        bc = _v_clicks(s.get("bullets"))
        if bc:
            parts.append("\n" + bc)
        if _s(s.get("info")):
            parts.append(f'\n<div class="mt-10 opacity-50 text-sm">\n\n{_s(s.get("info"))}\n\n</div>')

    else:  # bullets (default)
        if title:
            parts.append(f"# {title}")
        if lead:
            parts.append(f'\n<div class="opacity-75 mb-2">\n\n{lead}\n\n</div>')
        bc = _v_clicks(s.get("bullets"))
        if bc:
            parts.append("\n" + bc)

    body = "\n".join(p for p in parts if p is not None)
    if note:
        body += f"\n\n<!--\n{note}\n-->"
    return body, fm


def render_slidev(deck: dict) -> str:
    """deck IR -> Slidev Markdown 全文。"""
    meta = deck.get("meta") or {}
    slides = deck.get("slides") or []
    if not slides:
        slides = [{"layout": "cover", "title": _s(meta.get("title")) or "演示"}]

    chunks: list[str] = [_front(meta)]
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            continue
        body, fm = _render_slide(s)
        if i == 0:
            chunks.append(body)
        else:
            if fm:
                fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
                sep = f"---\n{fm_lines}\n---"
            else:
                sep = "---"
            chunks.append(sep + "\n\n" + body)
    return "\n\n".join(chunks) + "\n"

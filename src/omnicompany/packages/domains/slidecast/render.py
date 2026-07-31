# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-21T00:00:00Z type=helper status=active
# [OMNI] summary="确定性渲染器: slide IR(JSON) -> Slidev Markdown,目标=真 field-manual 主题(24 布局/组件),弃自研裸 CSS。"
# [OMNI] why="IR-first 渲染核心。把 IR 映射到 fm-theme 的具名 layout + slot,内容走丰富版式而非纯 bullet。"
# [OMNI] tags=slidecast,render,slidev,fieldmanual
"""slide IR -> Slidev Markdown(field-manual 主题)。

布局映射(IR layout -> fm-theme layout):
  cover->cover  section->section  statement->statement  bullets->default
  two-col->two-column  comparison->comparison  big-stat->default(大数字)
  code->code-full  mermaid->chart-full  magic-move->two-column(前/后)
  quote->quote  callout->callout  timeline->timeline  dashboard->dashboard  end->end
真主题在 data/domains/slidecast/_studio/fm-theme(MIT,本机/内部用)。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .themes import theme_css

_CJK = "一-鿿㐀-䶿　-〿＀-￯"
_PUNCT_MAP = {",": "，", ":": "：", ";": "；", "!": "！", "?": "？"}

# field-manual 主题相对路径(slides.md 在 studio 根,主题在 studio/fm-theme)
THEME = "./fm-theme"


def _s(v: Any) -> str:
    return ("" if v is None else str(v)).replace("\r", "").strip()


def _rich(v: Any) -> str:
    """塞进裸 <div> 的文字:**粗**/*斜* 转 HTML(div 内联不走 markdown,否则 ** 泄漏)。"""
    t = _s(v)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\*])\*(?!\s)(.+?)(?<!\s)\*(?![\*])", r"<em>\1</em>", t)
    return t


def _fence(code: str, lang: str = "text") -> str:
    """围栏比内容里任何反引号串都长(防 ``` 撑破);剥掉作者误塞的最外层围栏。"""
    code = _s(code)
    code = re.sub(r"^\s*`{3,}[\w+-]*\n", "", code)
    code = re.sub(r"\n`{3,}\s*$", "", code)
    longest = max((len(r) for r in re.findall(r"`+", code)), default=0)
    bar = "`" * max(3, longest + 1)
    return f"{bar}{lang}\n{code}\n{bar}"


def _ascii_banner(text: str, font: str = "standard") -> str:
    """英文/数字大字 → figlet ASCII 复古 banner(像 colorc.cc 的 webworks logo)。CJK 不行,失败返空。
    用 standard(纯 ASCII 线条)而非 ansi_shadow(实心 Unicode 块)——后者缩放时块对不齐会畸变。"""
    t = _s(text)
    if not t or not re.search(r"[A-Za-z0-9]", t):
        return ""
    try:
        import pyfiglet
        return pyfiglet.figlet_format(t, font=font, width=90).rstrip("\n")
    except Exception:
        return ""


# 对外宣发清洗:去掉"制作过程/用户意图"类元信息(脱敏/打码/内部/删改…),括号内含关键词整段删。
_META_RE = re.compile(
    r"\s*[（(][^（()）]*?(脱敏|已脱敏|脱敏后|打码|马赛克|匿名化|隐去|去敏|"
    r"仅内部|内部资料|内部使用|示意|仅示例|举例示意|删改\s*\d*\s*[遍次]?|改写自|为隐私|为脱敏)"
    r"[^（()）]*?[)）]"
)


def _clean_public(v: Any) -> str:
    """剥离对外内容里不该出现的制作过程/脱敏类元信息(括号内含脱敏/打码/内部等→整段删)。"""
    t = _s(v)
    prev = None
    while prev != t:
        prev = t
        t = _META_RE.sub("", t)
    return t  # 不折叠空白:否则会吃掉代码换行(meta 正则已含前导 \\s* 不留双空格)


def _zh_punct(t: str) -> str:
    """中文语境内半角标点转全角;只在紧邻 CJK 时转,英文/代码不动。"""
    if not t or not any("一" <= c <= "鿿" for c in t):
        return t
    t = re.sub(rf"(?<=[{_CJK}])([,:;!?])", lambda m: _PUNCT_MAP[m.group(1)], t)
    t = re.sub(rf"([,:;!?])(?=[{_CJK}])", lambda m: _PUNCT_MAP[m.group(1)], t)
    t = t.replace("(", "(").replace(")", ")")
    return t


_TEXT_FIELDS = ("title", "subtitle", "lead", "note", "stat", "stat_label", "stat_sub",
                "quote", "cite", "info", "left_header", "right_header", "callout",
                "callout_title", "fig_label", "code_title")
_LIST_FIELDS = ("bullets", "left", "right")


def _cp(v: Any) -> str:
    """清洗对外元信息 + 中文标点全角(纯文本字段用)。"""
    return _zh_punct(_clean_public(v))


def _normalize_deck(deck: dict) -> dict:
    meta = dict(deck.get("meta") or {})
    for k in ("title", "subtitle", "info"):
        if meta.get(k):
            meta[k] = _cp(meta[k])
    slides = []
    for s in (deck.get("slides") or []):
        if not isinstance(s, dict):
            continue
        s = dict(s)
        for k in _TEXT_FIELDS:
            if s.get(k):
                s[k] = _cp(s[k])
        for k in _LIST_FIELDS:
            if isinstance(s.get(k), list):
                s[k] = [_cp(x) for x in s[k]]
        # 代码/流程图内容:只去元信息(不动标点)
        for k in ("code", "mermaid"):
            if s.get(k):
                s[k] = _clean_public(s[k])
        if isinstance(s.get("frames"), list):
            s["frames"] = [_clean_public(x) for x in s["frames"]]
        if isinstance(s.get("events"), list):
            s["events"] = [{kk: _cp(vv) for kk, vv in (e or {}).items()} for e in s["events"]]
        if isinstance(s.get("panels"), list):
            s["panels"] = [{kk: _cp(vv) for kk, vv in (p or {}).items()} for p in s["panels"]]
        slides.append(s)
    return {"meta": meta, "slides": slides}


def _v_clicks(items: list, flatten: bool = False) -> str:
    items = [i for i in (items or []) if _s(i)]
    if not items:
        return ""
    if flatten:  # 视频静态/全显:全部直接可见
        return "\n".join(f"- {_s(i)}" for i in items)
    return "<v-clicks>\n\n" + "\n".join(f"- {_s(i)}" for i in items) + "\n\n</v-clicks>"


def _slot(name: str, content: str) -> str:
    return f"<template v-slot:{name}>\n\n{content}\n\n</template>"


# Backwards-compatible default used by callers that still expect one stylesheet.
# New runs ask ``themes.theme_css`` for one or more comparable variants.
STYLE_CSS = theme_css("crt")

_THEME_UI_LABELS = {
    "blueprint": {"doc": "SYSTEM DRAWING", "end": "END OF SCHEMATIC"},
    "crt": {"doc": "TERMINAL LOG", "end": "SESSION COMPLETE"},
    "comic": {"doc": "STORYBOARD 01", "end": "END OF ISSUE"},
    "notebook": {"doc": "WORKING NOTES", "end": "END OF NOTES"},
}


def _render_slide(s: dict, flatten: bool = False, meta: dict | None = None) -> tuple[str, dict]:
    """返回 (body markdown, per-slide frontmatter dict)。frontmatter 含 layout + 主题 props。
    flatten=True 时 v-clicks/magic-move 拍平(视频静态用,现已少用)。"""
    layout = _s(s.get("layout")) or "bullets"
    title = _s(s.get("title"))
    lead = _s(s.get("lead"))
    fm: dict[str, Any] = {}
    body: list[str] = []
    theme_id = _s((meta or {}).get("_visual_theme"))
    ui_labels = _THEME_UI_LABELS.get(
        theme_id, {"doc": "FIELD NOTE", "end": "END OF BRIEFING"})
    doc = _s((meta or {}).get("docNumber")) or ui_labels["doc"]

    if layout == "cover":
        fm = {"layout": "cover", "docNumber": doc}
        if _s(s.get("date")) or _s((meta or {}).get("date")):
            fm["date"] = _s(s.get("date")) or _s((meta or {}).get("date"))
        fm["classification"] = _s((meta or {}).get("info")) or "据 colorc.cc 原文自动生成"
        # ASCII 标题不再作为主题默认件。它会强行把不同视觉方向拉回同一套
        # “终端海报”语法；只有内容 IR 明确 opt-in 时才渲染。
        banner = ""
        if bool((meta or {}).get("use_ascii_banner")):
            banner = _ascii_banner(_s(s.get("banner")) or _s((meta or {}).get("banner")))
        if banner:  # figlet ASCII logo(英文,像 colorc.cc 的 webworks)
            body.append(f'<pre class="fm-ascii">\n{banner}\n</pre>')
        # 封面标题按长度缩放:长标题(LLM 偶尔写成整句)自动缩小,避免挤成多行
        n = len(title)
        disp = f'<span style="font-size:.68em">{title}</span>' if n > 15 else \
               (f'<span style="font-size:.84em">{title}</span>' if n > 11 else title)
        body.append(f"# {disp}")
        sub = _s(s.get("subtitle")) or _s((meta or {}).get("subtitle"))
        if sub:
            body.append(f"## {sub}")
        if lead:
            body.append(_slot("subtitle", lead))

    elif layout == "section":
        fm = {"layout": "section", "docNumber": doc, "sectionNumber": _s(s.get("section")) or ""}
        body.append(f"# {title}")
        if _s(s.get("subtitle")):
            body.append(f"## {_s(s.get('subtitle'))}")
        if lead:
            body.append(_slot("descriptor", lead))

    elif layout == "statement":
        bc = _v_clicks(s.get("bullets"), flatten)
        if bc:  # 有要点 → 普通内页(statement 布局无要点槽)
            fm = {"layout": "default", "slideTitle": title, "docNumber": doc}
            if lead:
                body.append(lead)
            body.append(bc)
        else:
            fm = {"layout": "statement", "docNumber": doc}
            stmt = title or _s(s.get("quote")) or lead
            body.append(stmt)

    elif layout == "bullets":
        fm = {"layout": "default", "slideTitle": title, "docNumber": doc}
        if lead:
            body.append(lead)
        bc = _v_clicks(s.get("bullets"), flatten)
        if bc:
            body.append(bc)

    elif layout == "big-stat":
        fm = {"layout": "default", "slideTitle": title, "docNumber": doc}
        if lead:
            body.append(lead)
        stat = _rich(s.get("stat"))
        block = ['<div class="text-center mt-6">',
                 f'<div class="fm-stat text-[5.2rem] leading-none font-extrabold">{stat}</div>']
        if _s(s.get("stat_label")):
            block.append(f'<div class="text-2xl mt-3" style="color:#3a3a1e">{_rich(s.get("stat_label"))}</div>')
        if _s(s.get("stat_sub")):
            block.append(f'<div class="text-base opacity-70 mt-2">{_rich(s.get("stat_sub"))}</div>')
        block.append("</div>")
        body.append("\n".join(block))
        bc = _v_clicks(s.get("bullets"), flatten)
        if bc:
            body.append('<div class="mt-6">\n\n' + bc + "\n\n</div>")

    elif layout in ("two-col", "comparison"):
        left = _v_clicks(s.get("left"), flatten)
        right = _v_clicks(s.get("right"), flatten)
        if layout == "comparison" or _s(s.get("left_header")) or _s(s.get("right_header")):
            fm = {"layout": "comparison", "slideTitle": title, "docNumber": doc,
                  "leftHeader": _s(s.get("left_header")) or "方案 A",
                  "rightHeader": _s(s.get("right_header")) or "方案 B",
                  "leftAccent": _s(s.get("left_accent")) or "red",
                  "rightAccent": _s(s.get("right_accent")) or "olive"}
        else:
            fm = {"layout": "two-column", "slideTitle": title, "docNumber": doc}
        body.append(_slot("left", left or " "))
        body.append(_slot("right", right or " "))

    elif layout == "code":
        fm = {"layout": "code-full", "slideTitle": title, "docNumber": doc,
              "codeTitle": _s(s.get("code_title")) or (title or "LISTING"),
              "codeLang": _s(s.get("lang")) or "text"}
        if lead:
            body.append(lead)
        body.append(_fence(s.get("code"), _s(s.get("lang")) or "text"))
        if _s(s.get("caption")):
            body.append(_slot("caption", _s(s.get("caption"))))

    elif layout == "mermaid":
        fm = {"layout": "chart-full", "slideTitle": title, "docNumber": doc,
              "figNumber": _s(s.get("fig_number")) or "", "figLabel": _s(s.get("fig_label")) or title}
        mm = _s(s.get("mermaid"))
        mm = re.sub(r"\b(flowchart|graph)\s+TD\b", r"\1 LR", mm)
        mm = re.sub(r"\b(flowchart|graph)\s+TB\b", r"\1 LR", mm)
        theme_id = _s((meta or {}).get("_visual_theme"))
        mermaid_vars = _MERMAID_VARS_BY_THEME.get(
            theme_id, _MERMAID_VARS_BY_THEME["notebook"])
        init = json.dumps(
            {"theme": "base", "themeVariables": mermaid_vars},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        mm = f"%%{{init: {init}}}%%\n{mm}"
        body.append(_slot("chart", _fence(mm, "mermaid")))

    elif layout == "magic-move":
        frames = [f for f in (s.get("frames") or []) if _s(f)]
        lang = _s(s.get("lang")) or "text"
        fm = {"layout": "two-column", "slideTitle": title, "docNumber": doc}
        if len(frames) >= 2:
            body.append(_slot("left", "**之前**\n\n" + _fence(frames[0], lang)))
            body.append(_slot("right", "**之后**\n\n" + _fence(frames[-1], lang)))
        elif frames:
            fm = {"layout": "code-full", "slideTitle": title, "docNumber": doc, "codeLang": lang}
            body.append(_fence(frames[-1], lang))

    elif layout == "quote":
        fm = {"layout": "quote", "docNumber": doc}
        if _s(s.get("cite")):
            fm["attribution"] = _s(s.get("cite"))
        body.append(_s(s.get("quote")) or title)

    elif layout == "callout":
        fm = {"layout": "callout", "slideTitle": title, "docNumber": doc,
              "calloutType": _s(s.get("callout_type")) or "warning",
              "calloutTitle": _s(s.get("callout_title")) or "注意"}
        if lead:
            body.append(lead)
        bc = _v_clicks(s.get("bullets"), flatten)
        if bc:
            body.append(bc)
        body.append(_slot("callout", _s(s.get("callout")) or lead or title))

    elif layout == "timeline":
        fm = {"layout": "timeline", "slideTitle": title, "docNumber": doc,
              "direction": _s(s.get("direction")) or "horizontal"}
        entries = []
        for e in (s.get("events") or []):
            e = e or {}
            entries.append(
                '<div class="tl-entry">\n'
                '  <div class="tl-entry-marker"><div class="tl-entry-dot"></div></div>\n'
                '  <div class="tl-entry-body">\n'
                f'    <div class="tl-entry-date fm-label">{_rich(e.get("date"))}</div>\n'
                f'    <div class="tl-entry-title">{_rich(e.get("title"))}</div>\n'
                f'    <div class="tl-entry-desc">{_rich(e.get("desc"))}</div>\n'
                '  </div>\n</div>')
        body.append("\n".join(entries))

    elif layout == "dashboard":
        panels = (s.get("panels") or [])[:4]
        fm = {"layout": "dashboard", "slideTitle": title, "docNumber": doc}
        for i, p in enumerate(panels, 1):
            p = p or {}
            fm[f"panel{i}Label"] = _s(p.get("label")) or f"指标 {i}"
            val = _rich(p.get("value"))
            body.append(_slot(f"panel{i}", f'<div class="fm-stat text-5xl font-extrabold">{val}</div>'))
            if _s(p.get("caption")):
                body.append(_slot(f"caption{i}", _rich(p.get("caption"))))

    elif layout == "end":
        fm = {"layout": "end", "docNumber": doc, "endLabel": ui_labels["end"]}
        fm["classification"] = _s((meta or {}).get("info")) or "据 colorc.cc 原文自动生成"
        body.append(_slot("title", title or "完"))
        contact = (
            _s(s.get("subtitle"))
            or _s(s.get("info"))
            or _s((meta or {}).get("info"))
        )
        bullets = [b for b in (s.get("bullets") or []) if _s(b)]
        if bullets:
            body.append(_slot("contact", "<br>".join(_rich(b) for b in bullets)))
        elif contact:
            body.append(_slot("contact", contact))

    else:  # 兜底 → default
        fm = {"layout": "default", "slideTitle": title, "docNumber": doc}
        if lead:
            body.append(lead)
        bc = _v_clicks(s.get("bullets"), flatten)
        if bc:
            body.append(bc)

    return "\n\n".join(p for p in body if p), fm


def _yaml_val(v: Any) -> str:
    """frontmatter 标量值的安全序列化(字符串单引号包,转义内部单引号)。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    return "'" + s.replace("'", "''") + "'"


_MERMAID_VARS_BY_THEME = {
    "blueprint": {
        "background": "#0a4770", "primaryColor": "#0b527e", "primaryTextColor": "#f3fbff",
        "primaryBorderColor": "#d7f4ff", "lineColor": "#ffd15c", "mainBkg": "#0b527e",
        "nodeBorder": "#d7f4ff", "secondaryColor": "#0a4167", "tertiaryColor": "#0d5b89",
        "edgeLabelBackground": "#0a4770", "titleColor": "#f3fbff",
    },
    "crt": {
        "background": "#020503", "primaryColor": "#061009", "primaryTextColor": "#d7ffe8",
        "primaryBorderColor": "#1aff8c", "lineColor": "#1aff8c", "mainBkg": "#061009",
        "nodeBorder": "#1aff8c", "secondaryColor": "#07130d", "tertiaryColor": "#0a1810",
        "edgeLabelBackground": "#020503", "titleColor": "#d7ffe8",
    },
    "comic": {
        "background": "#fff7df", "primaryColor": "#ffd83d", "primaryTextColor": "#151515",
        "primaryBorderColor": "#151515", "lineColor": "#151515", "mainBkg": "#ffd83d",
        "nodeBorder": "#151515", "secondaryColor": "#79d6ff", "tertiaryColor": "#ff8fa0",
        "edgeLabelBackground": "#fff7df", "titleColor": "#151515",
    },
    "notebook": {
        "background": "#fffdf4", "primaryColor": "#fffef9", "primaryTextColor": "#242a31",
        "primaryBorderColor": "#477eb4", "lineColor": "#477eb4", "mainBkg": "#fffef9",
        "nodeBorder": "#477eb4", "secondaryColor": "#f3ead5", "tertiaryColor": "#eee1c2",
        "edgeLabelBackground": "#fffdf4", "titleColor": "#242a31",
    },
}


def _headmatter(
    meta: dict,
    no_trans: bool,
    fm0: dict,
    visual_theme: str | None = None,
) -> str:
    title = _s(meta.get("title")) or "演示"
    lines = [
        f"theme: {THEME}",
        "canvasWidth: 960",
        "colorSchema: light",
        f"title: {_yaml_val(title)}",
        "highlighter: shiki",
        "lineNumbers: false",
        "routerMode: hash",  # 静态托管/嵌 iframe 翻页不 404(history 模式直达 /2 会 404)
        f"transition: {'none' if no_trans else 'slide-left'}",
        "mdc: true",
        "fonts:",
        "  sans: Noto Sans SC",
        "  serif: Noto Serif SC",
        "  mono: Courier Prime",
        "mermaid:",
        "  theme: base",
        "  themeVariables:",
    ]
    mermaid_vars = _MERMAID_VARS_BY_THEME.get(
        visual_theme or "", _MERMAID_VARS_BY_THEME["notebook"])
    lines += [f"    {k}: '{v}'" for k, v in mermaid_vars.items()]
    for k, v in fm0.items():
        if k == "title":
            continue
        lines.append(f"{k}: {_yaml_val(v)}")
    return "---\n" + "\n".join(lines) + "\n---"


def render_slidev(
    deck: dict,
    video: bool = False,
    steps: bool = False,
    visual_theme: str | None = None,
) -> str:
    """deck IR -> Slidev Markdown(field-manual 主题)。
    video=True 静态全显;steps=True 保 v-clicks/magic-move 但禁转场(视频逐 click 截帧)。"""
    deck = _normalize_deck(deck)
    meta = deck.get("meta") or {}
    render_meta = dict(meta)
    render_meta["_visual_theme"] = visual_theme or ""
    slides = deck.get("slides") or [{"layout": "cover", "title": _s(meta.get("title")) or "演示"}]

    flatten = video and not steps
    no_trans = video or steps

    body0, fm0 = _render_slide(slides[0], flatten, render_meta)
    chunks = [
        _headmatter(meta, no_trans, fm0, visual_theme=visual_theme)
        + "\n\n"
        + body0
    ]
    for s in slides[1:]:
        if not isinstance(s, dict):
            continue
        body, fm = _render_slide(s, flatten, render_meta)
        fm_lines = "\n".join(f"{k}: {_yaml_val(v)}" for k, v in fm.items())
        chunks.append(f"---\n{fm_lines}\n---\n\n{body}")
    return "\n\n".join(chunks) + "\n"

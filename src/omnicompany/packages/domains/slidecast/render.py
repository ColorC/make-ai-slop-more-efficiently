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

import re
from typing import Any

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


# ── webworks-terminal 皮:套在 fm-theme 上(只覆盖 CSS 变量 + 扫描线/辉光,24 布局原样不动)──
# 调色/配方据真站 colorc.cc theme.css;点阵字体 Fusion Pixel(OFL+MIT 可商用,自托管 /fonts/)。
STYLE_CSS = """/* ===== webworks-terminal SKIN(套 field-manual,只换皮)===== */
@font-face{font-family:"FusionPixelLatin";src:url("/fonts/fusion-pixel-latin.woff2") format("woff2");font-display:swap;}
@font-face{font-family:"FusionPixelZH";src:url("/fonts/fusion-pixel-zh.woff2") format("woff2");font-display:swap;}

:root{
  /* 背景:近黑磷管 */
  --c-paper:#020503; --c-paper-dark:#061009; --c-paper-deeper:#07130d; --c-paper-shadow:#0a1810;
  /* 文字:薄荷绿 */
  --c-ink:#d7ffe8; --c-ink-muted:#9fe0bf; --c-ink-light:#75b98f;
  /* 橄榄→磷光绿(规则/结构线) */
  --c-olive-dark:#15cc70; --c-olive:#1aff8c; --c-olive-mid:#36ffa0; --c-olive-light:#5effb0;
  --c-olive-ghost:rgba(26,255,140,.08); --c-olive-subtle:rgba(26,255,140,.16);
  /* khaki→暗绿 */
  --c-khaki-dark:#3a7a60; --c-khaki:#4ea882; --c-khaki-light:#6abf95; --c-khaki-pale:#2a4a3a;
  /* 琥珀:链接/次强调 */
  --c-amber:#ffd166; --c-amber-pale:rgba(255,209,102,.12);
  /* 红:危险/警示(少用) */
  --c-red:#ff5d73; --c-red-light:#ff7d8d; --c-red-pale:rgba(255,93,115,.14);
  /* 蓝:important 留点终端蓝 */
  --c-blue:#3a6da8; --c-blue-mid:#4a7db8; --c-blue-light:#5a8dc8; --c-blue-pale:rgba(58,109,168,.16);
  /* 语义:强调/规则线用绿不用红 */
  --color-accent:#1aff8c; --color-accent-alt:#3a6da8; --color-rule:#1aff8c; --color-rule-light:rgba(26,255,140,.55);
  --bracket-color:#1aff8c;
  /* 字体:标题点阵(latin→zh),正文薄荷可读 sans,代码 Cascadia */
  --font-heading:"FusionPixelLatin","FusionPixelZH",monospace;
  --font-condensed-sans:"FusionPixelLatin","FusionPixelZH",monospace;
  --font-label:"FusionPixelLatin","FusionPixelZH",monospace;
  --font-body:"Noto Sans SC","Microsoft YaHei","PingFang SC",sans-serif;
  --font-mono:"Cascadia Code","FusionPixelLatin","Microsoft YaHei",monospace;
}

/* 点阵标题:关抗锯齿保锐利 + 磷光辉光 */
.slidev-layout h1,.slidev-layout h2,.slidev-layout h3,.fm-label,.fm-stat{
  -webkit-font-smoothing:none; image-rendering:pixelated;
  text-shadow:0 0 4px rgba(26,255,140,.5),0 0 14px rgba(26,255,140,.22);
}
.fm-stat{color:#1aff8c !important; text-shadow:0 0 6px rgba(26,255,140,.6),0 0 18px rgba(26,255,140,.3) !important;}
/* figlet ASCII logo(封面英文大字) */
.fm-ascii{color:#1aff8c; font-family:"Cascadia Code",monospace; font-size:.6rem; line-height:1.02; white-space:pre; overflow:hidden; margin:0 0 .5rem; text-shadow:0 0 6px rgba(26,255,140,.6);}

/* 背景:扫描线 + 顶/底绿辉光 + 暗角(贴 colorc.cc 配方;走 background-image 不动 DOM 伪元素)*/
.slidev-layout{
  background-color:#020503 !important;
  background-image:
    repeating-linear-gradient(0deg, rgba(0,0,0,.16) 0 1px, transparent 1px 3px),
    repeating-linear-gradient(0deg, rgba(26,255,140,.022) 0 1px, transparent 1px 4px),
    radial-gradient(ellipse at 50% 116%, rgba(26,255,140,.10), transparent 55%),
    radial-gradient(ellipse at 50% -12%, rgba(26,255,140,.15), transparent 45%) !important;
  box-shadow: inset 0 0 160px rgba(0,0,0,.5), inset 0 0 70px rgba(26,255,140,.04);
}
/* 封面:加协调的终端示意图形(扫描线 + 终端网格 + 磷光团 + 暗角),比纯黑底更有"屏幕感" */
.slidev-layout.layout-cover{
  background-image:
    repeating-linear-gradient(0deg, rgba(0,0,0,.15) 0 1px, transparent 1px 3px),
    linear-gradient(rgba(26,255,140,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(26,255,140,.05) 1px, transparent 1px),
    radial-gradient(circle at 84% 26%, rgba(26,255,140,.20), transparent 38%),
    radial-gradient(circle at 14% 86%, rgba(26,255,140,.13), transparent 42%),
    radial-gradient(ellipse at 50% 55%, rgba(2,12,7,0) 60%, #020503 92%) !important;
  background-size: auto, 46px 46px, 46px 46px, auto, auto, auto !important;
}

/* 正文薄荷绿可读 sans;代码块终端化 */
.slidev-layout, .slidev-layout p, .slidev-layout li, .slidev-layout td, .slidev-layout th{ font-family:var(--font-body); color:#d7ffe8; }
.slidev-layout pre, .slidev-layout .shiki{ background:#020805 !important; border-color:rgba(26,255,140,.28) !important; }
.slidev-layout pre code, .slidev-layout .shiki code, .slidev-layout .shiki span{ color:#cdeede !important; }
.slidev-layout :not(pre)>code{ color:#ffd166 !important; background:#061009 !important; border:1px solid rgba(26,255,140,.28); }
.slidev-layout a{ color:#ffd166 !important; }
/* —— 用户反馈修正 —— */
/* 1. 中文点阵大字克制尺寸(点阵字本身偏大,整体缩一档;封面/陈述是硬编码,单独压) */
:root{ --text-lg:1.4rem; --text-xl:1.85rem; --text-2xl:2.3rem; --text-3xl:3rem; --text-4xl:4.6rem; }
.cover-title{ font-size:3.3rem !important; }
.statement-content h1,.statement-content h2,.statement-content p{ font-size:2.2rem !important; }
/* 2. 亮绿底标题条撞色看不清 → 改深底 + 绿字(终端反白条,高对比) */
.cdf-code-header,.cdr-code-header,.thc-col-header,.db-panel-header{
  background:#07130d !important; border-bottom-color:rgba(26,255,140,.35) !important; }
.cdf-code-title,.cdr-code-title,.thc-col-header,.db-panel-label{ color:#1aff8c !important; }
.cdf-code-badge,.cdr-code-badge{ color:#9fe0bf !important; border-color:rgba(26,255,140,.45) !important; }
/* 3. 去掉每页页眉一直显示的 UNCLASSIFIED */
.fm-header__class{ display:none !important; }
@media (prefers-reduced-motion: reduce){ .slidev-layout h1,.slidev-layout h2,.slidev-layout h3{ text-shadow:none; } }
"""


def _render_slide(s: dict, flatten: bool = False, meta: dict | None = None) -> tuple[str, dict]:
    """返回 (body markdown, per-slide frontmatter dict)。frontmatter 含 layout + 主题 props。
    flatten=True 时 v-clicks/magic-move 拍平(视频静态用,现已少用)。"""
    layout = _s(s.get("layout")) or "bullets"
    title = _s(s.get("title"))
    lead = _s(s.get("lead"))
    fm: dict[str, Any] = {}
    body: list[str] = []
    doc = _s((meta or {}).get("docNumber")) or "FIELD NOTE"

    if layout == "cover":
        fm = {"layout": "cover", "docNumber": doc}
        if _s(s.get("date")) or _s((meta or {}).get("date")):
            fm["date"] = _s(s.get("date")) or _s((meta or {}).get("date"))
        fm["classification"] = _s((meta or {}).get("info")) or "据 colorc.cc 原文自动生成"
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
            fm = {"layout": "default", "title": title, "docNumber": doc}
            if lead:
                body.append(lead)
            body.append(bc)
        else:
            fm = {"layout": "statement", "docNumber": doc}
            stmt = title or _s(s.get("quote")) or lead
            body.append(stmt)

    elif layout == "bullets":
        fm = {"layout": "default", "title": title, "docNumber": doc}
        if lead:
            body.append(lead)
        bc = _v_clicks(s.get("bullets"), flatten)
        if bc:
            body.append(bc)

    elif layout == "big-stat":
        fm = {"layout": "default", "title": title, "docNumber": doc}
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
            fm = {"layout": "comparison", "title": title, "docNumber": doc,
                  "leftHeader": _s(s.get("left_header")) or "方案 A",
                  "rightHeader": _s(s.get("right_header")) or "方案 B",
                  "leftAccent": _s(s.get("left_accent")) or "red",
                  "rightAccent": _s(s.get("right_accent")) or "olive"}
        else:
            fm = {"layout": "two-column", "title": title, "docNumber": doc}
        body.append(_slot("left", left or " "))
        body.append(_slot("right", right or " "))

    elif layout == "code":
        fm = {"layout": "code-full", "title": title, "docNumber": doc,
              "codeTitle": _s(s.get("code_title")) or (title or "LISTING"),
              "codeLang": _s(s.get("lang")) or "text"}
        if lead:
            body.append(lead)
        body.append(_fence(s.get("code"), _s(s.get("lang")) or "text"))
        if _s(s.get("caption")):
            body.append(_slot("caption", _s(s.get("caption"))))

    elif layout == "mermaid":
        fm = {"layout": "chart-full", "docNumber": doc,
              "figNumber": _s(s.get("fig_number")) or "", "figLabel": _s(s.get("fig_label")) or title}
        mm = _s(s.get("mermaid"))
        mm = re.sub(r"\b(flowchart|graph)\s+TD\b", r"\1 LR", mm)
        mm = re.sub(r"\b(flowchart|graph)\s+TB\b", r"\1 LR", mm)
        body.append(_slot("chart", _fence(mm, "mermaid")))

    elif layout == "magic-move":
        frames = [f for f in (s.get("frames") or []) if _s(f)]
        lang = _s(s.get("lang")) or "text"
        fm = {"layout": "two-column", "title": title, "docNumber": doc}
        if len(frames) >= 2:
            body.append(_slot("left", "**之前**\n\n" + _fence(frames[0], lang)))
            body.append(_slot("right", "**之后**\n\n" + _fence(frames[-1], lang)))
        elif frames:
            fm = {"layout": "code-full", "title": title, "docNumber": doc, "codeLang": lang}
            body.append(_fence(frames[-1], lang))

    elif layout == "quote":
        fm = {"layout": "quote", "docNumber": doc}
        if _s(s.get("cite")):
            fm["attribution"] = _s(s.get("cite"))
        body.append(_s(s.get("quote")) or title)

    elif layout == "callout":
        fm = {"layout": "callout", "title": title, "docNumber": doc,
              "calloutType": _s(s.get("callout_type")) or "warning",
              "calloutTitle": _s(s.get("callout_title")) or "注意"}
        if lead:
            body.append(lead)
        bc = _v_clicks(s.get("bullets"), flatten)
        if bc:
            body.append(bc)
        body.append(_slot("callout", _s(s.get("callout")) or lead or title))

    elif layout == "timeline":
        fm = {"layout": "timeline", "title": title, "docNumber": doc,
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
        fm = {"layout": "dashboard", "title": title, "docNumber": doc}
        for i, p in enumerate(panels, 1):
            p = p or {}
            fm[f"panel{i}Label"] = _s(p.get("label")) or f"指标 {i}"
            val = _rich(p.get("value"))
            body.append(_slot(f"panel{i}", f'<div class="fm-stat text-5xl font-extrabold">{val}</div>'))
            if _s(p.get("caption")):
                body.append(_slot(f"caption{i}", _rich(p.get("caption"))))

    elif layout == "end":
        fm = {"layout": "end", "docNumber": doc}
        fm["classification"] = _s((meta or {}).get("info")) or "据 colorc.cc 原文自动生成"
        body.append(_slot("title", title or "完"))
        contact = _s(s.get("info")) or _s((meta or {}).get("info"))
        bullets = [b for b in (s.get("bullets") or []) if _s(b)]
        if bullets:
            body.append(_slot("contact", "<br>".join(_rich(b) for b in bullets)))
        elif contact:
            body.append(_slot("contact", contact))

    else:  # 兜底 → default
        fm = {"layout": "default", "title": title, "docNumber": doc}
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


_MERMAID_VARS = {
    "background": "#f5f0e0", "primaryColor": "#ede8d0", "primaryTextColor": "#1a1a14",
    "primaryBorderColor": "#8a7a50", "lineColor": "#8b1a1a", "mainBkg": "#ede8d0",
    "nodeBorder": "#8a7a50", "secondaryColor": "#e0d8be", "tertiaryColor": "#f5f0e0",
    "edgeLabelBackground": "#f5f0e0", "titleColor": "#3a3a1e",
}


def _headmatter(meta: dict, no_trans: bool, fm0: dict) -> str:
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
    lines += [f"    {k}: '{v}'" for k, v in _MERMAID_VARS.items()]
    for k, v in fm0.items():
        if k == "title":
            continue
        lines.append(f"{k}: {_yaml_val(v)}")
    return "---\n" + "\n".join(lines) + "\n---"


def render_slidev(deck: dict, video: bool = False, steps: bool = False) -> str:
    """deck IR -> Slidev Markdown(field-manual 主题)。
    video=True 静态全显;steps=True 保 v-clicks/magic-move 但禁转场(视频逐 click 截帧)。"""
    deck = _normalize_deck(deck)
    meta = deck.get("meta") or {}
    slides = deck.get("slides") or [{"layout": "cover", "title": _s(meta.get("title")) or "演示"}]

    flatten = video and not steps
    no_trans = video or steps

    body0, fm0 = _render_slide(slides[0], flatten, meta)
    chunks = [_headmatter(meta, no_trans, fm0) + "\n\n" + body0]
    for s in slides[1:]:
        if not isinstance(s, dict):
            continue
        body, fm = _render_slide(s, flatten, meta)
        fm_lines = "\n".join(f"{k}: {_yaml_val(v)}" for k, v in fm.items())
        chunks.append(f"---\n{fm_lines}\n---\n\n{body}")
    return "\n\n".join(chunks) + "\n"

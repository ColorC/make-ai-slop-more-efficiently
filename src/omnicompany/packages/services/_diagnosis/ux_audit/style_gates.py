# [OMNI] origin=claude-code domain=services/_diagnosis/ux_audit ts=2026-07-18T00:00:00Z type=lib status=active
# [OMNI] summary="去 AI 味/形态确定性门禁(M1+M2+M3):ambient-recipe(L2 多色径向铺底/背景位移动画/噪点) + motion-budget(L3 无限强调动画) + emoji-scan(L3 emoji/符号图标) + glass-scope(L3 玻璃越白名单) + touch-target(L2 LOFA 可点选择器 <44px;M3 落地 dashboard src/shell/** 可点样式 <24px 桌面 AA 硬线) + hover-only(L3 dashboard title=/onAuxClick 渐进清零基线)。扫 dashboard src + lofa www,纯 Python 无 LLM,可复跑。"
# [OMNI] material_id="material:services._diagnosis.ux_audit.style_gates"
"""去 AI 味/形态适配六条确定性枚举器(dashboard frontend/src + lofa app/www)。无 LLM。

gate 语义:有 L2 → exit 1(硬违规);仅 L3/零发现 → exit 0(L3=待评清单)。
计划锚点: docs/plans/frontend-design/[2026-07-18]UNIFIED-FRONTEND-UPGRADE/plan.md W4 枚举器表。
"""
from __future__ import annotations

import datetime
import os
import re
import sys
from pathlib import Path

_EXCLUDE = {".git", "node_modules", "dist", "__pycache__", ".venv", "vendor"}
_SRC_EXT = (".ts", ".tsx", ".js", ".jsx", ".html")
_SKIP_TEST = (".test.", "__tests__")

# motion-budget 白名单:spinner/光标/骨架扫光(viewIn/viewOut 为一次性进入动画,非 infinite,双保险登记)
_KF_WHITELIST = {"spin", "toolspin", "chatcaret", "fp-skeleton", "fp-shimmer", "viewin", "viewout"}
_KF_WHITELIST_SHOW = ("spin", "toolSpin", "chatCaret", "fp-skeleton", "fp-shimmer", "viewIn", "viewOut")
_KF_NAME_BAD = re.compile(r"pulse|breathe|ping|bounce|glow|shimmer", re.I)
_KF_BODY_EMPHASIS = re.compile(r"\bopacity\b|\bbox-shadow\b|\bscale\s*\(", re.I)
_KF_BODY_BGMOVE = re.compile(r"\bbackground-position\b|\btransform\b", re.I)
_TAILWIND_BAD = re.compile(r"animate-(?:pulse|ping|bounce)\b")

# emoji 区间 + 符号黑名单(✓✕✗✎⚑⚡ 已在 2600-27BF 段;🗑📦📌📍🧭🌐⚗🖼🧊🏛 已在 1F000-1FAFF 段;
# 区间外补 ⌨ U+2328 ⌸ U+2338 ⏻ U+23FB ▶ U+25B6)
_EMOJI = re.compile("[\u2600-\u27BF\u2B00-\u2BFF\uFE0F\U0001F000-\U0001FAFF\u2328\u2338\u23FB\u25B6]")

_GLASS = re.compile(r"backdrop-?filter", re.I)
# glass-scope 外壳白名单:文件名含其一即合法(顶栏/底栏/浮层/sheet/toast 一族)
_GLASS_NAME_OK = ("dialog", "sheet", "toast", "menu", "modal", "commandpalette", "hovercard",
                  "tooltip", "bottompanel", "statusbar", "activitybar", "sidebar", "cockpitshell")


# ---------- 基础:文件遍历 / finding / CSS 解析 ----------

def _repo_root() -> Path:
    for q in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (q / "docs/projects/frontend-design/dashboard/theme.css").is_file():
            return q
    raise SystemExit("找不到 omnicompany 仓根(缺少 docs/projects/frontend-design/dashboard/theme.css)")


def _trees(root: Path) -> list[tuple[str, Path, Path]]:
    """(树名, 审计根, 报告路径基准)。dashboard 相对仓根,lofa 相对仓根 parent。"""
    return [
        ("dashboard", root / "src/omnicompany/dashboard/frontend/src", root),
        ("lofa", root.parent / "lofa/app/www", root.parent),
    ]


def _iter_files(tree_root: Path, exts: tuple[str, ...], skip_tests: bool = False):
    for dp, dns, fns in os.walk(tree_root):
        dns[:] = [d for d in dns if d not in _EXCLUDE]
        for fn in sorted(fns):
            if not fn.endswith(exts):
                continue
            if skip_tests and any(s in fn or s in dp for s in _SKIP_TEST):
                continue
            p = Path(dp) / fn
            try:
                yield p, p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue


def _rel(p: Path, base: Path) -> str:
    return p.relative_to(base).as_posix()


def _mk(gate: str, level: str, file: str, line: int, snippet: str, rule: str) -> dict:
    snippet = re.sub(r"\s+", " ", snippet).strip()
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return {"gate": gate, "level": level, "file": file, "line": line, "snippet": snippet, "rule": rule}


def _strip_comments(text: str) -> str:
    """去 /* */ 注释但保留换行数,保证行号不失真(同 token_drift)。"""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def _walk_css(text: str, lo: int = 0, hi: int | None = None):
    """yield (selector, body, "{"的绝对偏移)。@media/@layer 深入内层;@keyframes 作整体一块。注释需已剔除。"""
    hi = len(text) if hi is None else hi
    i = lo
    while i < hi:
        j = text.find("{", i, hi)
        if j < 0:
            return
        selector = re.sub(r"\s+", " ", text[i:j]).strip()
        depth, k = 1, j + 1
        while k < hi and depth:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        body = text[j + 1:k - 1]
        low = selector.lower()
        if low.startswith("@keyframes"):
            yield selector, body, j
        elif low.startswith("@"):
            yield from _walk_css(text, j + 1, k - 1)
        else:
            yield selector, body, j
        i = k


def _keyframes(text: str) -> dict[str, tuple[str, int]]:
    """{keyframes 名: (body, 行号)}。"""
    out = {}
    for selector, body, off in _walk_css(text):
        m = re.match(r"@keyframes\s+([\w-]+)", selector, re.I)
        if m and m.group(1) not in out:
            out[m.group(1)] = (body, text.count("\n", 0, off) + 1)
    return out


def _animation_refs(body: str) -> list[tuple[str, bool, int, str]]:
    """块内动画引用 [(keyframes名, 是否 infinite, 相对块内偏移, 声明原文)]。简写 + animation-name/-iteration-count 分写。"""
    refs = []
    for m in re.finditer(r"(?<![\w-])animation\s*:\s*([^;{}]+);", body, re.I):
        val = m.group(1)
        infinite = "infinite" in val.lower()
        for tok in re.split(r"[\s,]+", val):
            tok = tok.strip()
            if tok and not re.match(r"^[\d.]+m?s$|^[\d.]+$", tok):
                refs.append((tok, infinite, m.start(), m.group(0)))
    names = {m.group(1).strip(): m.start() for m in re.finditer(r"animation-name\s*:\s*([^;{}]+);", body, re.I)}
    infinite_split = re.search(r"animation-iteration-count\s*:\s*infinite", body, re.I)
    if names and infinite_split:
        for name, off in names.items():
            for tok in name.split(","):
                refs.append((tok.strip(), True, off, f"animation-name: {name}; + animation-iteration-count: infinite"))
    return refs


# ---------- ① ambient-recipe (L2) ----------

def _chromatic_rgbs(val: str) -> set[tuple[int, int, int]]:
    """background 值里彩色(非灰阶)rgb 三元组集合。灰阶(黑/白/灰 vignette)不算一色。"""
    out = set()
    for m in re.finditer(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", val, re.I):
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if max(r, g, b) - min(r, g, b) > 24:
            out.add((r, g, b))
    return out


def scan_ambient_recipe(trees: list[tuple[str, Path, Path]]) -> list[dict]:
    """body/#ambient 全屏背景层:禁多色 radial-gradient 铺底、禁背景位移动画、禁噪点贴图。
    方向 C(2026-07-18 用户裁决)合法化:单 hue 多 stop + 灰阶 vignette 不算「多色」。"""
    out = []
    gate, level = "ambient-recipe", "L2"
    for _name, root, base in trees:
        for p, raw in _iter_files(root, (".css",)):
            rel, text = _rel(p, base), _strip_comments(raw)
            for m in re.finditer(r"\bbackground(?:-image)?\s*:\s*([^;{}]+);", text, re.I | re.S):
                if len(re.findall(r"radial-gradient\s*\(", m.group(1), re.I)) >= 2 and len(_chromatic_rgbs(m.group(1))) >= 2:
                    out.append(_mk(gate, level, rel, text.count("\n", 0, m.start()) + 1,
                                   m.group(0), "多色 radial-gradient 铺底(同一 background 值 ≥2 段径向渐变且 ≥2 种彩色 hue)"))
            for i, line in enumerate(text.split("\n"), 1):
                if re.search(r"\bmix-blend-mode\s*:", line, re.I):
                    out.append(_mk(gate, level, rel, i, line, "mix-blend-mode 混合铺底"))
            kfs = _keyframes(text)
            bgmove = {n for n, (b, _ln) in kfs.items() if _KF_BODY_BGMOVE.search(b)}
            for selector, body, off in _walk_css(text):
                if not re.search(r"(?:^|[\s,])body(?:\s*$|[\s,{.:])|#ambient\b", selector):
                    continue
                for name, _inf, roff, decl in _animation_refs(body):
                    if name in bgmove:
                        out.append(_mk(gate, level, rel, text.count("\n", 0, off + 1 + roff) + 1,
                                       decl, f"全屏层(body/#ambient)背景位移动画(keyframes `{name}` 含 background-position/transform)"))
        # feTurbulence 是 SVG 元素,可能内联在 tsx/js/html,全源文件扫
        for p, raw in _iter_files(root, (".css",) + _SRC_EXT):
            for i, line in enumerate(_strip_comments(raw).split("\n"), 1):
                if "feTurbulence" in line:
                    out.append(_mk(gate, level, _rel(p, base), i, line, "噪点贴图(feTurbulence)"))
                elif re.search(r"\bmixBlendMode\s*:", line):
                    out.append(_mk(gate, level, _rel(p, base), i, line, "mixBlendMode 混合铺底(内联样式)"))
    return out


# ---------- ② motion-budget (L3) ----------

def scan_motion_budget(trees: list[tuple[str, Path, Path]]) -> list[dict]:
    """脉冲/呼吸类无限循环强调动画只许白名单 spinner;另扫 dashboard tsx 的 tailwind animate-pulse/ping/bounce。"""
    out = []
    gate, level = "motion-budget", "L3"
    for _name, root, base in trees:
        for p, raw in _iter_files(root, (".css",)):
            rel, text = _rel(p, base), _strip_comments(raw)
            kfs = _keyframes(text)
            for name, (_body, ln) in kfs.items():
                if _KF_NAME_BAD.search(name) and name.lower() not in _KF_WHITELIST:
                    out.append(_mk(gate, level, rel, ln, f"@keyframes {name}",
                                   "脉冲/呼吸类 keyframes 命名(pulse/breathe/ping/bounce/glow/shimmer)"))
            for _selector, body, off in _walk_css(text):
                for name, infinite, roff, decl in _animation_refs(body):
                    if not infinite or name.lower() in _KF_WHITELIST or name not in kfs:
                        continue
                    if _KF_BODY_EMPHASIS.search(kfs[name][0]):
                        out.append(_mk(gate, level, rel, text.count("\n", 0, off + 1 + roff) + 1,
                                       decl, f"无限循环强调动画(keyframes `{name}` 改 opacity/box-shadow/scale)"))
    # tailwind 无限强调类(dashboard 才用 tailwind,只扫 .tsx/.ts)
    dash = [t for t in trees if t[0] == "dashboard"]
    for _name, root, base in dash:
        for p, raw in _iter_files(root, (".tsx", ".ts")):
            for i, line in enumerate(_strip_comments(raw).split("\n"), 1):
                if _TAILWIND_BAD.search(line):
                    out.append(_mk(gate, level, _rel(p, base), i, line,
                                   "tailwind 无限强调类(animate-pulse/ping/bounce)"))
    return out


# ---------- ③ emoji-scan (L3) ----------

def _strip_source_comments(text: str) -> str:
    """剔除注释(保留行号):/* */ 与 <!-- --> 块、行首 // 行。markdown 内容字符串不豁免(宁多报)。"""
    text = _strip_comments(text)
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return "\n".join("" if line.lstrip().startswith("//") else line for line in text.split("\n"))


def scan_emoji(trees: list[tuple[str, Path, Path]]) -> list[dict]:
    """JS/TS/JSX/TSX/HTML 源码字符串禁 emoji/符号当图标。豁免:注释、src/i18n/**、测试文件。"""
    out = []
    gate, level = "emoji-scan", "L3"
    for _name, root, base in trees:
        for p, raw in _iter_files(root, _SRC_EXT, skip_tests=True):
            rel = _rel(p, base)
            if p.relative_to(root).parts[0] == "i18n":  # src/i18n/** locale 文件豁免
                continue
            stripped = _strip_source_comments(raw)
            orig = raw.split("\n")
            for i, line in enumerate(stripped.split("\n"), 1):
                if _EMOJI.search(line):
                    snippet = orig[i - 1] if i - 1 < len(orig) else line
                    out.append(_mk(gate, level, rel, i, snippet, "emoji/符号当图标(源码字符串,应换 lucide/icons 表)"))
    return out


# ---------- ④ glass-scope (L3) ----------

def _glass_allowed(tree: str, p: Path, root: Path, text: str) -> bool:
    fn = p.name.lower()
    if fn in ("frostpane.css", "theme.css"):
        return True
    rel_tree = p.relative_to(root).as_posix()
    if tree == "lofa" and rel_tree.startswith("css/"):  # LOFA 玻璃件本就限定导航/浮层,全 css 白名单
        return True
    # G.7 统一网格+玻璃面板 sanctioned(2026-07-19 用户裁决,V2 阶段四第四波):
    # 蓝图玻璃(--fp-bp-panel*)只落样式层 —— styles/blueprint.css(共享原子件)、
    # index.css(dockview 页签条描图纸)、shell/shellA.css(rail/fab)、entities/**/*.css(实体页
    # 面板/行卡);玻璃滥用检测逻辑对其他文件(tsx 内联/组件文件)不变。
    if tree == "dashboard" and (
        rel_tree in ("styles/blueprint.css", "index.css", "shell/shellA.css")
        or (rel_tree.startswith("entities/") and fn.endswith(".css"))
    ):
        return True
    if any(k in fn for k in _GLASS_NAME_OK):
        return True
    if tree == "dashboard" and rel_tree.startswith("shared/view/ui/") and "backdrop-blur" in text:
        return True  # shadcn ui 玻璃件
    return False


def scan_glass_scope(trees: list[tuple[str, Path, Path]]) -> list[dict]:
    """backdrop-filter/backdropFilter 只许在外壳白名单(顶栏/spine/inspector/浮层/sheet/toast/menu 等)。"""
    out = []
    gate, level = "glass-scope", "L3"
    for tree, root, base in trees:
        for p, raw in _iter_files(root, (".css",) + _SRC_EXT):
            if _glass_allowed(tree, p, root, raw):
                continue
            for i, line in enumerate(_strip_comments(raw).split("\n"), 1):
                if _GLASS.search(line):
                    out.append(_mk(gate, level, _rel(p, base), i, line, "backdrop-filter 出现在外壳白名单之外"))
    return out


# ---------- ⑤ touch-target (L2) ----------

# 已知可点类名/选择器清单(LOFA 触控硬线 ≥44px,写死;视觉尺寸保留而热区扩大的,须在规则块
# 上方/块内注释标注「视觉尺寸/热区扩大」手法才豁免)。
_LOFA_TOUCH_SELECTORS = (
    ".lg-icon-btn", ".lg-nav-back", ".chat-plus", ".chat-send", ".float-back",
    ".lg-switch", ".lg-chip", ".lg-seg button", ".tool-head", ".term-key", ".rv-check",
)
_TOUCH_MIN = 44
# M3(2026-07-18 落地): dashboard 侧壳层桌面 AA 硬线 ≥24px, 范围 = dashboard src/shell/**。
# tsx 内联样式是可量字面量(width: 32 / minHeight: 18 等); 可点判据=匹配行 ±6 行窗口内含
# cursor:/onClick/onMouseDown(容器/布局尺寸不误报); 豁免同 LOFA 注释约定(视觉尺寸/热区扩大)。
_DASH_TOUCH_MIN = 24
_DASH_TSX_DIM = re.compile(r"(?<![\w-])(min-)?(width|height|minWidth|minHeight)\s*:\s*(\d+(?:\.\d+)?)(?![\d.])")
_DASH_CLICKY = re.compile(r"cursor\s*:\s*['\"]|onClick|onMouseDown")
_DASH_TOUCH_WIN = 6
_TOUCH_EXEMPT = re.compile(r"视觉尺寸|热区扩大")
_TOUCH_DIM = re.compile(r"(?<![\w-])(min-)?(width|height)\s*:\s*(\d+(?:\.\d+)?)px", re.I)


def _scan_touch_dash_shell(root: Path, base: Path) -> list[dict]:
    """dashboard src/shell/**(tsx/ts + css): 可点样式 width/height/min-* <24px = L2(桌面 AA 硬线)。
    豁免: 匹配行 ±6 行窗口内注释含「视觉尺寸/热区扩大」(同 LOFA ::before/padding 扩热区约定)。"""
    out = []
    gate, level = "touch-target", "L2"
    shell = root / "shell"
    if not shell.is_dir():
        return out
    for p, raw in _iter_files(shell, (".tsx", ".ts"), skip_tests=True):
        rel, lines = _rel(p, base), raw.split("\n")
        for i, line in enumerate(lines, 1):
            m = _DASH_TSX_DIM.search(line)
            if not m:
                continue
            v = float(m.group(3))
            if not (0 < v < _DASH_TOUCH_MIN):
                continue
            lo, hi = max(0, i - 1 - _DASH_TOUCH_WIN), min(len(lines), i + _DASH_TOUCH_WIN)
            win = "\n".join(lines[lo:hi])
            if not _DASH_CLICKY.search(win) or _TOUCH_EXEMPT.search(win):
                continue
            out.append(_mk(gate, level, rel, i, line.strip(),
                           f"触控目标 {m.group(2)} {v}px <24px(dashboard 壳层桌面 AA 硬线;"
                           "视觉尺寸保留须注释标注热区扩大手法)"))
    for p, raw in _iter_files(shell, (".css",)):
        rel, text = _rel(p, base), _strip_comments(raw)
        raw_lines = raw.split("\n")
        for selector, body, off in _walk_css(text):
            if not _DASH_CLICKY.search(body):
                continue
            block_start = text.count("\n", 0, off) + 1
            lo = max(0, block_start - 2)
            block_raw = "\n".join(raw_lines[lo:block_start + body.count("\n") + 1])
            if _TOUCH_EXEMPT.search(block_raw):
                continue
            for m in _TOUCH_DIM.finditer(body):
                if float(m.group(3)) < _DASH_TOUCH_MIN:
                    line = text.count("\n", 0, off + 1 + m.start()) + 1
                    out.append(_mk(gate, level, rel, line, m.group(0),
                                   f"触控目标 {m.group(0).split(':')[0].strip()} {m.group(3)}px <24px"
                                   f"(dashboard 壳层 `{selector}` 桌面 AA 硬线)"))
    return out


def scan_touch_target(trees: list[tuple[str, Path, Path]]) -> list[dict]:
    """LOFA css:已知可点选择器的 width/height/min-width/min-height <44px = L2。
    dashboard src/shell/**:可点样式 width/height/min-* <24px = L2(M3 落地, 桌面 AA 硬线)。
    豁免:规则块上方一行或块内注释标注视觉尺寸保留/热区扩大手法(::before/padding 扩热区、行级点击覆盖)。"""
    out = []
    gate, level = "touch-target", "L2"
    for tree, root, base in trees:
        if tree == "dashboard":
            out.extend(_scan_touch_dash_shell(root, base))
            continue
        for p, raw in _iter_files(root, (".css",)):
            rel, text = _rel(p, base), _strip_comments(raw)
            raw_lines = raw.split("\n")
            for selector, body, off in _walk_css(text):
                parts = [s.strip() for s in selector.split(",")]
                if not any(s in _LOFA_TOUCH_SELECTORS for s in parts):
                    continue
                block_start = text.count("\n", 0, off) + 1  # "{" 所在行(通常即选择器行)
                block_end = block_start + body.count("\n") + 1
                lo = max(0, block_start - 2)  # 上方一行注释也算豁免窗口
                block_raw = "\n".join(raw_lines[lo:block_end])
                if _TOUCH_EXEMPT.search(block_raw):
                    continue
                for m in _TOUCH_DIM.finditer(body):
                    if float(m.group(3)) < _TOUCH_MIN:
                        line = text.count("\n", 0, off + 1 + m.start()) + 1
                        prop = m.group(0).split(":")[0].strip()
                        out.append(_mk(gate, level, rel, line, m.group(0),
                                       f"触控目标 {prop} {m.group(3)}px <44px(可点选择器 `{selector}`;"
                                       "视觉尺寸保留须注释标注热区扩大手法)"))
    return out


# ---------- ⑥ hover-only (L3, dashboard) ----------

# 原生 title= 提示(触屏永不显示)与 onAuxClick 中键辅助(触屏无中键)。渐进清零基线:
# 逐条列档不阻塞; 组件 props(如 <Modal title=>)与原生属性难静态区分, 宁多报(同 emoji-scan 口径)。
_HOVER_TITLE = re.compile(r"(?<![\w-])title\s*=\s*[\{\"'`]")
_HOVER_AUX = re.compile(r"(?<![\w-])onAuxClick\s*=")


def scan_hover_only(trees: list[tuple[str, Path, Path]]) -> list[dict]:
    """dashboard src 内 title= / onAuxClick 逐条列档(文件:行号), 作为 hover-only 交互的渐进清零基线。
    只扫 dashboard(lofa 无 JSX); 测试文件豁免。L3 待评清单, 不阻塞。"""
    out = []
    gate, level = "hover-only", "L3"
    for tree, root, base in trees:
        if tree != "dashboard":
            continue
        for p, raw in _iter_files(root, (".tsx", ".ts", ".jsx", ".js"), skip_tests=True):
            rel = _rel(p, base)
            orig = raw.split("\n")
            for i, line in enumerate(_strip_source_comments(raw).split("\n"), 1):
                if line == "" :
                    continue  # 注释行已被清空, 跳过(行号不失真)
                snippet = orig[i - 1] if i - 1 < len(orig) else line
                if _HOVER_TITLE.search(line):
                    out.append(_mk(gate, level, rel, i, snippet,
                                   "原生 title= 提示(触屏不可见;应 Tooltip 组件或可见 label)"))
                if _HOVER_AUX.search(line):
                    out.append(_mk(gate, level, rel, i, snippet,
                                   "onAuxClick 中键辅助(触屏无中键;须配 ⋯/右键菜单等价路径)"))
    return out


# ---------- 汇总:报告 + main ----------

_GATES = (
    ("ambient-recipe", "L2", "body/#ambient 禁多色 radial 铺底/背景位移动画/噪点", scan_ambient_recipe),
    ("motion-budget", "L3", "脉冲/呼吸类无限强调动画只许白名单 spinner", scan_motion_budget),
    ("emoji-scan", "L3", "源码字符串禁 emoji/符号图标", scan_emoji),
    ("glass-scope", "L3", "backdrop-filter 只许外壳白名单", scan_glass_scope),
    ("touch-target", "L2", "LOFA 已知可点选择器 <44px;dashboard src/shell/** 可点样式 <24px(热区扩大注释豁免)", scan_touch_target),
    ("hover-only", "L3", "dashboard title=/onAuxClick 逐条列档(渐进清零基线)", scan_hover_only),
)


def _cell(s: str) -> str:
    return s.replace("|", "\\|")


def render_markdown(results: dict[str, list[dict]], root: Path) -> str:
    n_l2 = sum(1 for fs in results.values() for f in fs if f["level"] == "L2")
    lines = [
        f"# 去 AI 味门禁报告 · {datetime.date.today().isoformat()}",
        "",
        "> 生成:`src/omnicompany/packages/services/_diagnosis/ux_audit/style_gates.py`(纯 Python 无 LLM,可复跑)",
        "> 审计树:dashboard `src/omnicompany/dashboard/frontend/src/`(.tsx/.ts/.css)vs LOFA `lofa/app/www/`"
        "(.css/.js/.html;`vendor/` 第三方库排除,同 node_modules 口径)",
        "> 口径:L2=硬违规(阻塞,exit 1),L3=待评清单(不阻塞);规则来源:本计划 W4 枚举器表",
        f"> 结果:" + " · ".join(f"{g} {len(results[g])}" for g, _lv, _r, _fn in _GATES)
        + f" · L2 合计 {n_l2} → gate **{'FAIL' if n_l2 else 'PASS'}**",
        "",
    ]
    titles = {"ambient-recipe": "①", "motion-budget": "②", "emoji-scan": "③", "glass-scope": "④", "touch-target": "⑤", "hover-only": "⑥"}
    for gate, level, rule, _fn in _GATES:
        fs = results[gate]
        lines += [f"## {titles[gate]} {gate}({level},{len(fs)} 发现)", "", f"> 规则:{rule}", ""]
        if not fs:
            lines += ["无发现。✅", ""]
            continue
        lines += ["| 文件 | 行 | 片段 | 判定 |", "|---|--:|---|---|"]
        for f in fs:
            lines.append(f"| `{f['file']}` | L{f['line']} | {_cell(f['snippet'])} | {_cell(f['rule'])} |")
        lines.append("")
    lines += [
        "## ⑦ 口径与豁免备注",
        "",
        "- 注释豁免:扫描前剔除 `/* */`、`<!-- -->` 块与行首 `//` 行(行号保持不失真);markdown 内容字符串难判定,不豁免(宁多报)。",
        "- touch-target 口径:LOFA css 中已知可点选择器(清单写死在 `_LOFA_TOUCH_SELECTORS`)的 `width/height/min-width/min-height` <44px = L2;"
        "规则块上方一行或块内注释标注「视觉尺寸保留 + 热区扩大」手法(::before/padding 扩热区、行级点击覆盖)者豁免。"
        "M3 已落地 dashboard 侧(2026-07-18):`src/shell/**` 的 tsx/ts 内联样式与 css 规则中,可量 width/height/minWidth/minHeight <24px"
        "(桌面 AA 硬线)且匹配行 ±6 行窗口内可点(含 `cursor:`/`onClick`/`onMouseDown`) = L2,同窗口注释含豁免词者豁免。",
        "- hover-only 口径:只扫 dashboard(src/shell 已完成 M3 清零, 其余实体为渐进基线);逐条列 `title=` 原生提示与 `onAuxClick` 中键辅助"
        "(文件:行号);组件 props(如 `<Modal title=>`)与原生属性难静态区分,宁多报;L3 待评清单不阻塞。",
        "- emoji-scan 豁免:`src/i18n/**` locale 文件、测试文件(`.test.`/`__tests__`)、`vendor/` 第三方库;"
        "检测区间 `\\u2600-\\u27BF`、`\\u2B00-\\u2BFF`、`\\uFE0F`、`\\U0001F000-\\U0001FAFF` + 区间外符号黑名单(⌨⌸⏻▶)。",
        "- motion-budget 白名单:" + "、".join(f"`{k}`" for k in _KF_WHITELIST_SHOW)
        + "(spinner/光标/骨架扫光;viewIn/viewOut 为一次性进入动画)。keyframes 体近似判据:opacity/box-shadow/scale。"
        "tailwind `animate-pulse/ping/bounce` 仅 dashboard 用 tailwind,只扫其 .tsx/.ts。",
        "- glass-scope 白名单:`frostpane.css`/`theme.css`、LOFA `css/` 全量(玻璃件本就限定导航/浮层)、"
        "文件名含 " + "/".join(_GLASS_NAME_OK) + "、`shared/view/ui/**` 中含 `backdrop-blur` 的 shadcn ui 文件;"
        "G.7 统一网格+玻璃面板 sanctioned(2026-07-19 用户裁决波四):dashboard `styles/blueprint.css`、"
        "`index.css`(dockview 页签条)、`shell/shellA.css`、`entities/**/*.css`(蓝图玻璃面板/行卡集中样式层),"
        "其余文件(尤其 tsx 内联)检测逻辑不变。",
        "- ambient-recipe 合法基线(2026-07-18 方向 C 用户裁决):frostpane.css `--fp-bg-grad` 与 LOFA base.css `#ambient` 均为"
        "**单 hue 多 stop 静态深空静景 + 灰阶 vignette**;判据为「≥2 段 radial-gradient 且 ≥2 种彩色 hue」才算多色铺底,单色多 stop/灰阶不触发。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台防乱码
    root = _repo_root()
    trees = _trees(root)
    for name, path, _base in trees:
        if not path.is_dir():
            raise SystemExit(f"缺审计树:{name} → {path}")
    results: dict[str, list[dict]] = {}
    for gate, _level, _rule, fn in _GATES:
        results[gate] = fn(trees)

    report = render_markdown(results, root)
    out = root / "docs/plans/frontend-design/[2026-07-18]UNIFIED-FRONTEND-UPGRADE/style-gates-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    for gate, level, _rule, _fn in _GATES:
        fs = results[gate]
        print(f"{gate}({level}): {len(fs)} 发现")
        show = fs if level == "L2" else fs[:20]  # L2 全打印;L3 超 20 条只打前 20
        for f in show:
            print(f"  {f['file']}:{f['line']} {f['snippet']} | {f['rule']}")
        if len(fs) > len(show):
            print(f"  ... 其余 {len(fs) - len(show)} 条见报告")
    print(f"报告已写入:{out}")
    n_l2 = sum(1 for fs in results.values() for f in fs if f["level"] == "L2")
    ok = n_l2 == 0  # gate 语义:有 L2 → 非零退出;仅 L3/零发现 → 0
    print("style-gates: " + ("PASS(仅 L3/零发现)" if ok else f"FAIL(L2×{n_l2})"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

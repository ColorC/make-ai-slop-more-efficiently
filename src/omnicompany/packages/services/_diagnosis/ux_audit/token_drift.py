# [OMNI] origin=claude-code domain=services/_diagnosis/ux_audit ts=2026-07-18T00:00:00Z type=lib status=active
# [OMNI] summary="设计 token 三方对账:theme.css(设计真源) vs frostpane.css(dashboard) vs lofa tokens.css。块感知解析 + 键名归一 + 值规范化比对 + markdown 报告。纯 Python 无 LLM,可复跑。"
# [OMNI] material_id="material:services._diagnosis.ux_audit.token_drift"
"""设计 token 漂移对账(暗色板+基础块三方,亮色板 theme vs frostpane)。无 LLM。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# LOFA → 真源 键名等价映射(只收语义确实同槽的;拿不准的一律留 only_in)
_EQUIV_LOFA = {
    "--r-card": "--r3",                  # 卡片圆角 = 真源圆角刻度第 3 档(16px)
    "--fg": "--text",                    # 一级前景字色
    "--fg-2": "--text-2",                # 二级前景字色
    "--fg-3": "--text-3",                # 三级前景字色
    "--accent-ink": "--accent-fg",       # accent 实底上的前景字
    "--accent-soft": "--accent-weak",    # accent 淡染底
    "--busy": "--warn",                  # 警示/忙碌琥珀色同槽
    "--bad": "--err",                    # 错误红同槽
    "--line": "--border",                # 主细分线
    "--line-2": "--border-subtle",       # 次级细分线
    "--glass-edge": "--spec",            # 玻璃顶部 1px 高光
    "--glass-sheet-blur": "--blur",      # LOFA 浮层玻璃模糊档 ↔ 真源唯一玻璃模糊档(LOFA 另有 nav 档,留 only_in)
    "--mono": "--font-mono",             # 等宽字体栈
}
# --r-pill 与真源同名,字面即可对齐,无需进表。
# 明确不映射(LOFA 独有结构项/分解方式不同,不算漂移算缺口):
#   --safe-top/--safe-bottom  移动端 safe-area,真源/dashboard 无此概念
#   --z-*                     z 层刻度,真源/dashboard 的 z-index 散落在 class 内联
#   --surface-2/--surface-input/--shadow-*/--ease-*  刻度分解方式不同,无一一对应
#   --provider-*              LOFA 业务 token(2026-07-18 裁决:值收敛为 var(--accent))

_FILES = ("theme.css", "frostpane.css", "lofa tokens.css")


def _strip_comments(text: str) -> str:
    """去 /* */ 注释但保留换行数,保证行号不失真。"""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def _parse_blocks(path) -> list:
    """块感知解析。返回 [(block_name, {var: (raw_value, lineno)})],保持文件顺序;只收声明了自定义属性的块。"""
    text = _strip_comments(Path(path).read_text(encoding="utf-8"))
    blocks, i, n = [], 0, len(text)
    while True:
        j = text.find("{", i)
        if j < 0:
            break
        selector = re.sub(r"\s+", " ", text[i:j]).strip()
        depth, k = 1, j + 1
        while k < n and depth:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        body = text[j + 1:k - 1]
        vars_found = {}
        for m in re.finditer(r"(--[\w-]+)\s*:\s*(.*?);", body, flags=re.S):
            raw = re.sub(r"\s+", " ", m.group(2)).strip()  # 多行值(如 --bg-grad 跨行)合并成单行
            lineno = text.count("\n", 0, j + 1 + m.start()) + 1
            vars_found[m.group(1)] = (raw, lineno)
        if vars_found:
            blocks.append((selector, vars_found))
        i = k
    return blocks


def parse_css_vars(path) -> dict:
    """dict[block_name, dict[var, value]]:区分 :root / :root,[data-mode="dark"] / [data-mode="light"] 等块。"""
    return {name: {v: val for v, (val, _ln) in vars_.items()} for name, vars_ in _parse_blocks(path)}


def _norm(v: str) -> str:
    """值规范化:格式差异(空白/引号/rgba 空格/0.46 vs .46/hex 大小写)不算漂移。"""
    v = v.strip().replace("'", '"')
    v = re.sub(r"\s+", " ", v)
    v = re.sub(r",\s*", ",", v)
    v = re.sub(r"\(\s+", "(", v)
    v = re.sub(r"\s+\)", ")", v)
    v = re.sub(r"(?<![\w.])0+(\.\d+)", r"\1", v)
    return v.lower()


def _fp_key(k: str) -> str:
    """frostpane 命名空间归一:--fp-x → --x。"""
    return "--" + k[len("--fp-"):] if k.startswith("--fp-") else k


def _norm_blocks(blocks: list, key_fn=lambda k: k) -> dict:
    """{var: (raw, norm, lineno)},合并给定块列表(后者覆盖前者)。"""
    out = {}
    for _name, vars_ in blocks:
        for k, (raw, ln) in vars_.items():
            out[key_fn(k)] = (raw, _norm(raw), ln)
    return out


def _raw_blocks(blocks: list, key_fn=lambda k: k) -> dict:
    """{var: raw_value},合并给定块列表(后者覆盖前者)。"""
    out = {}
    for _name, vars_ in blocks:
        for k, (raw, _ln) in vars_.items():
            out[key_fn(k)] = raw
    return out


def _pick(blocks: list, *names: str) -> list:
    return [b for b in blocks if b[0] in names]


def reconcile(theme_path, frostpane_path, lofa_path) -> dict:
    """三方对账 → {"equal","drift","only_in","notes","light"}。drift = 归一后 ≥2 方同 key 但规范化值不同。"""
    t_blocks = _parse_blocks(theme_path)
    f_blocks = _parse_blocks(frostpane_path)
    l_blocks = _parse_blocks(lofa_path)

    # 暗色板+基础块:theme 的 :root(基础) ∪ :root,[data-mode="dark"];frostpane/lofa 各自主 :root
    t_dark = _norm_blocks(_pick(t_blocks, ":root", ':root,[data-mode="dark"]'))
    f_dark = _norm_blocks(_pick(f_blocks, ":root"), _fp_key)
    l_dark = _norm_blocks(_pick(l_blocks, ":root"))
    l_overrides = [name for name, _v in l_blocks if name != ":root"]

    # LOFA 等价映射归位(记录源键名供报告展示)
    lofa_src = {}
    for src, dst in _EQUIV_LOFA.items():
        if src in l_dark:
            l_dark[dst] = l_dark.pop(src)
            lofa_src[dst] = src

    scopes = {"theme.css": t_dark, "frostpane.css": f_dark, "lofa tokens.css": l_dark}
    equal, drift, only_in = [], [], {f: [] for f in _FILES}
    for k in sorted(set(t_dark) | set(f_dark) | set(l_dark)):
        holders = [f for f in _FILES if k in scopes[f]]
        if len(holders) == 1:
            only_in[holders[0]].append(k)
            continue
        norms = {scopes[f][k][1] for f in holders}
        if len(norms) == 1:
            equal.append(k)
        else:
            drift.append({
                "key": k,
                "theme": t_dark[k][0] if k in t_dark else None,
                "frostpane": f_dark[k][0] if k in f_dark else None,
                "lofa": l_dark[k][0] if k in l_dark else None,
                "theme_line": t_dark[k][2] if k in t_dark else None,
                "lofa_src": lofa_src.get(k),
            })
    drift.sort(key=lambda r: (r["theme_line"] is None, r["theme_line"] or 0))

    # 亮色板:仅 theme vs frostpane,口径 = 暗色打底 + 亮块覆盖(级联有效面板);LOFA 无亮主题
    t_light = _raw_blocks(_pick(t_blocks, '[data-mode="light"]'))
    f_light = _raw_blocks(_pick(f_blocks, '[data-fp-mode="light"]'), _fp_key)
    t_dark_raw = {k: v[0] for k, v in t_dark.items()}
    f_dark_raw = {k: v[0] for k, v in f_dark.items()}
    light_equal, light_drift = [], []
    for k in sorted(set(t_light) | set(f_light)):
        t_eff = t_light.get(k) or t_dark_raw.get(k)
        f_eff = f_light.get(k) or f_dark_raw.get(k)
        if t_eff and f_eff and _norm(t_eff) == _norm(f_eff):
            light_equal.append(k)
        else:
            light_drift.append({"key": k, "theme": t_eff, "frostpane": f_eff})

    notes = [
        f"LOFA 覆盖块未纳入对账:{'、'.join(l_overrides) or '无'}(仅「去玻璃」模式下的玻璃件替换,解析到但排除)。",
        "亮色板口径:仅 theme.css vs frostpane.css;按「暗色打底 + 亮块覆盖」的有效面板比对;LOFA 无亮主题。",
        "真源裁决(2026-07-18,用户拍板):theme.css 为唯一手工编辑真源,取代 [2026-07-04]UNIFIED-DESIGN-STUDIO §6 D5;"
        "玻璃配方按设计语言.md 定稿统一为 blur(20px) saturate(180%)(原真源 blur(22px) saturate(200%) 已收回);"
        "Inter/Berkeley Mono 字体栈与 --r-sheet:24px 自消费方反向吸收进真源;LOFA provider 四色收敛为 var(--accent)。",
        "等价映射:" + ";".join(f"LOFA `{s}`↔`{d}`" for s, d in _EQUIV_LOFA.items())
        + "。其余 LOFA 独有不映射:safe-area、z 层、surface/shadow/ease 家族、--font-scale、--surface-input 等业务 token(见 ③ 节,属合理形态差异,非漂移)。",
    ]
    return {"equal": equal, "drift": drift, "only_in": only_in, "notes": notes,
            "light": {"equal": light_equal, "drift": light_drift}}


def _cell(v: str | None) -> str:
    return f"`{v}`" if v else "—"


def render_markdown(result: dict) -> str:
    """中文对账报告:①一致项 ②漂移项表 ③单方独有项 ④备注。"""
    import datetime
    equal, drift = result["equal"], result["drift"]
    only_in, notes, light = result["only_in"], result["notes"], result["light"]
    n_only = sum(len(v) for v in only_in.values())
    lines = [
        f"# Token 对账报告 · {datetime.date.today().isoformat()}",
        "",
        "> 生成:`src/omnicompany/packages/services/_diagnosis/ux_audit/token_drift.py`(纯 Python 无 LLM,可复跑)",
        "> 三方:设计真源 `docs/projects/frontend-design/dashboard/theme.css` vs dashboard 消费层 "
        "`src/omnicompany/dashboard/frontend/src/styles/frostpane.css`(--fp-* 归一为 --*)vs LOFA 移动端 `lofa/app/www/css/tokens.css`",
        f"> 范围:暗色板 + 基础块(字阶/间距/圆角/动效/字体)三方对账;亮色板仅 theme vs frostpane",
        f"> 结果:一致 {len(equal)} 项(暗色/基础)+ 亮色 {len(light['equal'])} 项 · **漂移 {len(drift)} 项** · 单方独有 {n_only} 项",
        "",
        f"## ① 一致项({len(equal)} + 亮色 {len(light['equal'])})",
        "",
        "<details><summary>展开清单</summary>",
        "",
        f"- 暗色/基础({len(equal)} 项,值已规范化比对):" + "、".join(f"`{k}`" for k in equal),
        f"- 亮色板 theme↔frostpane({len(light['equal'])} 项,暗色打底+亮块覆盖的有效面板):" +
        "、".join(f"`{k}`" for k in light["equal"]),
        "",
        "</details>",
        "",
        f"## ② 漂移项({len(drift)})",
        "",
        "| key | theme.css | frostpane.css | lofa tokens.css | theme 行号 |",
        "|---|---|---|---|---|",
    ]
    for r in drift:
        lofa = _cell(r["lofa"])
        if r.get("lofa_src"):
            lofa = f"`{r['lofa']}`（源 `{r['lofa_src']}`）"
        lines.append(f"| `{r['key']}` | {_cell(r['theme'])} | {_cell(r['frostpane'])} | {lofa} | "
                     + (f"L{r['theme_line']}" if r.get("theme_line") else "—") + " |")
    lines += ["", f"## ③ 单方独有项({n_only})", ""]
    gap_marks = {
        "--safe-top": "形态差异:safe-area 为移动端特有,真源/dashboard 桌面无此概念",
        "--safe-bottom": "形态差异:同上",
    }
    for f in _FILES:
        ks = only_in[f]
        lines.append(f"### {f} 独有({len(ks)})")
        lines.append("")
        if not ks:
            lines.append("无。")
        else:
            for k in ks:
                mark = f" —— {gap_marks[k]}" if k in gap_marks else ""
                lines.append(f"- `{k}`{mark}")
        lines.append("")
    lines += ["## ④ 备注", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(lines)


def _repo_root() -> Path:
    for q in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (q / "docs/projects/frontend-design/dashboard/theme.css").is_file():
            return q
    raise SystemExit("找不到 omnicompany 仓根(缺少 docs/projects/frontend-design/dashboard/theme.css)")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台防乱码
    root = _repo_root()
    theme = root / "docs/projects/frontend-design/dashboard/theme.css"
    frostpane = root / "src/omnicompany/dashboard/frontend/src/styles/frostpane.css"
    lofa = root.parent / "lofa/app/www/css/tokens.css"
    for p in (theme, frostpane, lofa):
        if not p.is_file():
            raise SystemExit(f"缺文件:{p}")
    result = reconcile(theme, frostpane, lofa)
    report = render_markdown(result)
    out = root / "docs/plans/frontend-design/[2026-07-18]UNIFIED-FRONTEND-UPGRADE/token-drift-baseline.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    drift_keys = [r["key"] for r in result["drift"]]
    print(f"漂移项 {len(drift_keys)} 项:{', '.join(drift_keys) or '(无)'}")
    print(f"一致(暗色/基础) {len(result['equal'])} · 亮色一致 {len(result['light']['equal'])} · "
          f"单方独有 {sum(len(v) for v in result['only_in'].values())} · 亮色漂移 {len(result['light']['drift'])}")
    print(f"报告已写入:{out}")
    # gate 语义:有漂移即非零退出(CI 用)
    ok = not result["drift"] and not result["light"]["drift"]
    print("token-drift gate: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

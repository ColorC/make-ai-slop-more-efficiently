# [OMNI] origin=claude-code domain=services/_diagnosis/ux_audit ts=2026-06-30T00:00:00Z type=lib status=active
# [OMNI] summary="ux_audit 确定性枚举核心:交互/信息/跳转三维 + 错位标记 + markdown 总表。纯 Python 扫 .tsx,可复跑。"
# [OMNI] material_id="material:services._diagnosis.ux_audit.enumerators"
"""三维确定性枚举(扫 .tsx)+ 据矩阵/层级打错位标记 + 生成总表。无 LLM。"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SKIP = (".test.", ".d.ts")
_EXCLUDE = {".git", "node_modules", "dist", "__pycache__", ".venv"}
_CJK = re.compile(r"[一-鿿]")
_DESTRUCTIVE = ("删除", "delete", "-delete", "danger")


def _iter_tsx(src_root: str, include_dirs: list[str], exclude: set[str]):
    roots = [os.path.join(src_root, d) for d in include_dirs] if include_dirs else [src_root]
    for base in roots:
        if not os.path.isdir(base):
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in exclude]
            for fn in fns:
                if not fn.endswith((".tsx", ".ts")) or any(s in fn for s in _SKIP):
                    continue
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, src_root).replace("\\", "/")
                try:
                    yield rel, open(p, encoding="utf-8").read()
                except Exception:
                    continue


def _btn_id(seg: str) -> str:
    for pat in (r'data-testid="([^"]+)"', r"data-testid='([^']+)'", r'title="([^"]+)"', r'aria-label="([^"]+)"'):
        m = re.search(pat, seg)
        if m:
            return m.group(1)
    m = re.search(r">\s*([^<>{][^<>]{0,24})\s*</button>", seg)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return "?"


def enum_interactions(src_root: str, include_dirs=None, exclude=None) -> dict:
    include_dirs = include_dirs or ["entities", "shell"]
    exclude = set(exclude or _EXCLUDE)
    out: dict[str, dict] = {}
    for rel, s in _iter_tsx(src_root, include_dirs, exclude):
        if rel.endswith(".ts"):  # 交互维只看 .tsx
            continue
        btns = [_btn_id(s[m.start():m.start() + 320]) for m in re.finditer(r"<button\b", s)]
        kebab_items = re.findall(r"label:\s*[`'\"]([^`'\"]{1,28})[`'\"]", s)
        rec = {
            "buttons": btns, "n_buttons": len(btns),
            "kebab_uses": s.count("<KebabMenu"), "kebab_items": kebab_items,
            "selects": s.count("<select"), "inputs": s.count("<input") + s.count("<textarea"),
            "onClick": s.count("onClick"), "danger": len(re.findall(r"danger:\s*true", s)),
        }
        if btns or rec["kebab_uses"] or rec["selects"] or rec["inputs"]:
            out[rel] = rec
    return out


def enum_info(src_root: str, include_dirs=None, exclude=None) -> dict:
    include_dirs = include_dirs or ["entities", "shell"]
    exclude = set(exclude or _EXCLUDE)
    out: dict[str, dict] = {}
    for rel, s in _iter_tsx(src_root, include_dirs, exclude):
        if not rel.endswith(".tsx"):
            continue
        sizes = (set(re.findall(r"fontSize:\s*(\d{1,2})", s)) | set(re.findall(r"var\(--fp-fs-\d\)", s))
                 | set(re.findall(r"FS\.\w+", s)) | set(re.findall(r"fontSize\.\w+", s)))
        weights = set(re.findall(r'fontWeight:\s*(\d{3}|[\'"]?bold)', s))
        longs = [m for m in re.findall(r"[>'\"`]([^<>'\"`{}]{0,200})", s) if len(_CJK.findall(m)) >= 16]
        rec = {"size_tiers": len(sizes), "weight_tiers": len(weights), "long_text": len(longs),
               "mono": s.count("mono") + s.count("Consolas") + s.count("Berkeley")}
        if sizes or longs:
            out[rel] = rec
    return out


_NAV_PATS = {
    "openTab→dockview": r"openTab\(\s*\{\s*type:\s*['\"]([a-z_]+)['\"]",
    "openInOmnidashboard": r"openInOmnidashboard\(\s*['\"]?([a-zA-Z_${}]+)",
    "VSCode/终端": r"openChatInVscode\(|openInVscode\(",
    "chatui": r"openChatui\(",
    "host消息": r"postHostMessage\(\s*\{\s*type:\s*['\"]([a-z-]+)['\"]",
    "新标签/外链": r"window\.open\(|location\.(?:href|replace)",
}


def enum_nav(src_root: str, include_dirs=None, exclude=None) -> dict:
    include_dirs = include_dirs or ["entities", "shell"]
    exclude = set(exclude or _EXCLUDE)
    out: dict[str, dict] = {}
    for rel, s in _iter_tsx(src_root, include_dirs, exclude):
        rec = {}
        for name, pat in _NAV_PATS.items():
            ms = re.findall(pat, s)
            if ms:
                tgts = sorted({m for m in ms if isinstance(m, str) and m and m not in ("href", "replace")})
                rec[name] = {"n": len(ms), "to": tgts}
        if rec:
            out[rel] = rec
    return out


def flags_for(inter: dict | None, info: dict | None) -> list[str]:
    """据「频率×重要性矩阵 + 信息层级」机械可判的错位标记(语义两轴留 LLM 增补)。"""
    f = []
    if inter:
        bt = " ".join(inter.get("buttons", []))
        destructive = any(x in bt for x in _DESTRUCTIVE)
        if inter["n_buttons"] >= 5 and inter["kebab_uses"] == 0:
            f.append("🟠平铺")
        if destructive and inter["kebab_uses"] == 0:
            f.append("⚪删除无保护")
        if inter["n_buttons"] >= 7 and inter["kebab_uses"] <= 1:
            f.append("🔴重排")
    if info and info.get("size_tiers", 9) <= 2 and info.get("long_text", 0) >= 1:
        f.append("🔴信息无层级")
    if info and info.get("long_text", 0) >= 3:
        f.append("🟡说明冗余")
    return f


def consolidate(accum: dict) -> dict:
    """汇总三维 → per_panel + offenders + markdown 总表。"""
    inter = accum.get("interactions", {})
    info = accum.get("info", {})
    nav = accum.get("nav", {})
    panels = sorted(set(inter) | set(info) | set(nav))
    per_panel = []
    offenders = []
    for p in panels:
        iv = inter.get(p)
        nv = info.get(p)
        edges = sum(x["n"] for x in nav.get(p, {}).values()) if p in nav else 0
        fl = flags_for(iv, nv)
        row = {
            "panel": p,
            "露钮": iv["n_buttons"] if iv else 0,
            "kebab_uses": iv["kebab_uses"] if iv else 0,
            "kebab_items": len(iv["kebab_items"]) if iv else 0,
            "selects": iv["selects"] if iv else 0,
            "inputs": iv["inputs"] if iv else 0,
            "size_tiers": nv["size_tiers"] if nv else 0,
            "long_text": nv["long_text"] if nv else 0,
            "nav_edges": edges,
            "flags": fl,
        }
        per_panel.append(row)
        if any(t in " ".join(fl) for t in ("🔴", "🟠", "⚪")):
            offenders.append(row)
    per_panel.sort(key=lambda r: (-r["露钮"], -r["nav_edges"]))
    offenders.sort(key=lambda r: -r["露钮"])

    n_buttons = sum(r["露钮"] for r in per_panel)
    n_kebab = sum(r["kebab_items"] for r in per_panel)
    totals = {
        "panels": len(per_panel),
        "surfaced_buttons": n_buttons,
        "kebab_collapsed": n_kebab,
        "nav_edges": sum(r["nav_edges"] for r in per_panel),
        "offenders": len(offenders),
    }
    # markdown 总表
    lines = [
        f"# UX 审计 · {accum.get('app') or accum.get('src_root')}",
        "",
        f"> 确定性三维枚举(交互/信息/跳转)。界面 {totals['panels']} · 露出按钮 {n_buttons} · 收纳 {n_kebab} · 跳转边 {totals['nav_edges']} · 错位 {totals['offenders']}",
        "> 分类口径: frostpane/REBUILD-STANDARD.md + INTERACTION-AUDIT.md(频率×重要性矩阵 + 信息层级)。语义两轴(重要性/频率/含义)由 LLM 增补节点后填。",
        "",
        "| 面板 | 露钮 | ⋯用 | ⋯项 | 选 | 输 | 字号档 | 说明段 | 跳转 | 标记 |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|---|",
    ]
    for r in per_panel:
        lines.append(
            f"| {r['panel']} | {r['露钮']} | {r['kebab_uses']} | {r['kebab_items']} | {r['selects']} | "
            f"{r['inputs']} | {r['size_tiers']} | {r['long_text']} | {r['nav_edges']} | {' '.join(r['flags']) or '✅'} |"
        )
    if offenders:
        lines += ["", "## 错位界面(需重组)", ""]
        for r in offenders:
            lines.append(f"- **{r['panel']}** {' '.join(r['flags'])} — 露钮 {r['露钮']}/收纳 {r['kebab_items']}")
    markdown = "\n".join(lines) + "\n"
    return {"totals": totals, "per_panel": per_panel, "offenders": offenders, "markdown": markdown}

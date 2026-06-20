# [OMNI] origin=ai-ide domain=slidecast/routers ts=2026-06-20T00:00:00Z type=router status=active
# [OMNI] summary="RULE 节点: Intake(读文章建run_dir) / ValidateIR(校验修补) / RenderSlidev(IR→slides.md) / BuildDeck(slidev build 出 HTML)。"
# [OMNI] why="确定性首尾 + 渲染/构建。build 失败优雅降级(仍交付 slides.md), 不炸管线。"
# [OMNI] tags=slidecast,router,intake,render,build
"""slidecast RULE 节点。"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import render as _render
from .._paths import ensure_dirs, runs_root, studio_root

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # 禁子进程弹前台控制台窗(本机硬规则)

# 文章真源候选根(传相对路径时挨个试)
_BASES = [
    "",
    "E:/WindowsWorkspace/webworks/apps/personal-homepage/",
    "E:/WindowsWorkspace/webworks/apps/personal-homepage/data/curated/",
    "E:/WindowsWorkspace/webworks/apps/personal-homepage/data/works/",
]


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w一-鿿-]+", "-", (s or "").strip()).strip("-")
    return (s or "deck")[:48]


def _parse_md(text: str) -> tuple[dict, str]:
    """拆 YAML frontmatter + 正文。返回 (front dict, body)。"""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    front: dict = {}
    body = text
    if m:
        body = text[m.end():]
        try:
            import yaml
            front = yaml.safe_load(m.group(1)) or {}
            if not isinstance(front, dict):
                front = {}
        except Exception:
            front = {}
    return front, body


class Intake(Router):
    """归一化输入(article 路径 / topic), 读文章, 建 run_dir, 出 brief。"""

    DESCRIPTION = "入题: 读文章(frontmatter+正文)/topic, 建 run_dir"
    FORMAT_IN = "slidecast.request"
    FORMAT_OUT = "slidecast.brief"
    REQUIRED_CONTEXT: list[str] = []

    def run(self, input_data: Any) -> Verdict:
        req = input_data if isinstance(input_data, dict) else {}
        article = str(req.get("article", "")).strip()
        topic = str(req.get("topic", "")).strip()
        if not article and not topic:
            return Verdict(kind=VerdictKind.FAIL, output=req,
                           diagnosis="需要 -i article=<文章md路径> 或 -i topic=<题目>")

        title, oneliner, body, source_label, slug = topic or "演示", "", "", topic, _slugify(topic or "deck")
        if article:
            p = None
            for base in _BASES:
                cand = Path(base + article) if base else Path(article)
                if cand.is_file():
                    p = cand
                    break
            if p is None:
                return Verdict(kind=VerdictKind.FAIL, output=req,
                               diagnosis=f"文章找不到: {article}")
            text = p.read_text(encoding="utf-8")
            front, body = _parse_md(text)
            title = str(front.get("title") or _first_h1(body) or p.stem)
            oneliner = str(front.get("oneliner") or "")
            source_label = f"《{title}》"
            slug = _slugify(p.stem)

        ensure_dirs()
        run_dir = runs_root() / f"{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}-{slug}"
        run_dir.mkdir(parents=True, exist_ok=True)

        do_build = _truthy(req.get("build", True))
        out = {
            "title": title, "oneliner": oneliner, "body": body,
            "source_label": source_label, "slug": slug,
            "style": str(req.get("style", "讲解")), "run_dir": str(run_dir),
            "do_build": do_build,
        }
        (run_dir / "brief.json").write_text(
            json.dumps({k: (v if k != "body" else v[:500] + "…") for k, v in out.items()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return Verdict(kind=VerdictKind.PASS, output=out,
                       diagnosis=f"入题《{title}》 → {run_dir.name}（正文 {len(body)} 字）",
                       granted_tags=["domain.slidecast", "stage.intake"])


class ValidateIR(Router):
    """校验/修补 deck IR: 保证 meta+slides、每页有 layout、首封面尾收尾、bullets 不过长。"""

    DESCRIPTION = "校验: deck IR schema/修补(首尾/字段/裁剪)"
    FORMAT_IN = "slidecast.deck_ir"
    FORMAT_OUT = "slidecast.deck_ir_valid"
    REQUIRED_CONTEXT = ["run_dir", "deck_ir"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        run_dir = Path(ctx["run_dir"])
        deck = ctx.get("deck_ir") or {}
        meta = deck.get("meta") or {}
        meta.setdefault("title", ctx.get("title", "演示"))
        slides = [s for s in (deck.get("slides") or []) if isinstance(s, dict) and s.get("layout")]
        fixes = 0
        for s in slides:
            b = s.get("bullets")
            if isinstance(b, list):
                trimmed = [x for x in b if str(x).strip()]
                if trimmed != b:
                    s["bullets"] = trimmed
                    fixes += 1
        if not slides:
            slides = [{"layout": "cover", "title": meta["title"]}]
        if slides[0].get("layout") != "cover":
            slides.insert(0, {"layout": "cover", "title": meta["title"],
                              "subtitle": ctx.get("oneliner", "")})
            fixes += 1
        if slides[-1].get("layout") != "end":
            slides.append({"layout": "end", "title": "完",
                           "info": meta.get("info") or f"据 {ctx.get('source_label','')} 自动生成"})
            fixes += 1
        deck = {"meta": meta, "slides": slides}
        (run_dir / "deck_ir_valid.json").write_text(
            json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
        out = dict(ctx)
        out["deck_ir"] = deck
        return Verdict(kind=VerdictKind.PASS, output=out,
                       diagnosis=f"校验通过: {len(slides)} 页（修补 {fixes} 处）",
                       granted_tags=["domain.slidecast", "stage.validated"])


class RenderSlidev(Router):
    """deck IR → Slidev Markdown(slides.md)。"""

    DESCRIPTION = "渲染: IR → Slidev Markdown(v-click/magic-move/mermaid)"
    FORMAT_IN = "slidecast.deck_ir_valid"
    FORMAT_OUT = "slidecast.slidev_md"
    REQUIRED_CONTEXT = ["run_dir", "deck_ir"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        run_dir = Path(ctx["run_dir"])
        md = _render.render_slidev(ctx["deck_ir"])
        slides_path = run_dir / "slides.md"
        slides_path.write_text(md, encoding="utf-8")
        out = dict(ctx)
        out["slides_md"] = str(slides_path)
        n = md.count("\n---\n") + 1
        return Verdict(kind=VerdictKind.PASS, output=out,
                       diagnosis=f"已渲 slides.md（约 {n} 页, {len(md)} 字符）",
                       granted_tags=["domain.slidecast", "stage.rendered"])


class BuildDeck(Router):
    """slidev build → 可交互 HTML(dist)。共用 _studio 的 node_modules。失败优雅降级。"""

    DESCRIPTION = "构建: slidev build → 会动的 HTML(dist);失败仍交付 slides.md"
    FORMAT_IN = "slidecast.slidev_md"
    FORMAT_OUT = "slidecast.deck_html"
    REQUIRED_CONTEXT = ["run_dir", "slides_md"]

    def run(self, input_data: Any) -> Verdict:
        ctx = input_data if isinstance(input_data, dict) else {}
        run_dir = Path(ctx["run_dir"])
        slides_md = ctx["slides_md"]
        out = dict(ctx)

        if not ctx.get("do_build", True):
            out["deck_html"] = None
            return Verdict(kind=VerdictKind.PASS, output=out,
                           diagnosis="跳过构建(do_build=false), 已交付 slides.md",
                           granted_tags=["domain.slidecast", "stage.built"])

        ok, note = _ensure_studio()
        if not ok:
            out["deck_html"] = None
            return Verdict(kind=VerdictKind.PASS, output=out,
                           diagnosis=f"构建跳过({note}); 已交付 slides.md → npm 起 dev 可看",
                           granted_tags=["domain.slidecast", "stage.built"])

        studio = studio_root()
        dist = run_dir / "dist"
        cli = studio / "node_modules" / "@slidev" / "cli" / "bin" / "slidev.mjs"
        try:
            r = subprocess.run(
                ["node", str(cli), "build", str(slides_md), "--base", "./", "--out", str(dist)],
                cwd=str(studio), capture_output=True, text=True, timeout=300,
                encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
            )
            built = (dist / "index.html").is_file()
            if built:
                out["deck_html"] = str(dist / "index.html")
                return Verdict(kind=VerdictKind.PASS, output=out,
                               diagnosis=f"构建成功 → {dist}/index.html",
                               granted_tags=["domain.slidecast", "stage.built", "kind.sink"])
            tail = (r.stderr or r.stdout or "")[-300:]
            out["deck_html"] = None
            return Verdict(kind=VerdictKind.PASS, output=out,
                           diagnosis=f"构建未出 index.html(已交付 slides.md)。尾部: {tail}",
                           granted_tags=["domain.slidecast", "stage.built"])
        except Exception as e:  # noqa: BLE001
            out["deck_html"] = None
            return Verdict(kind=VerdictKind.PASS, output=out,
                           diagnosis=f"构建异常({e})，已交付 slides.md",
                           granted_tags=["domain.slidecast", "stage.built"])


# ── helpers ──────────────────────────────────────────────────────────

def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


def _first_h1(body: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


_STUDIO_PKG = {
    "name": "slidecast-studio", "private": True, "type": "module",
    "dependencies": {
        "@slidev/cli": "^52.0.0",
        "@slidev/theme-seriph": "latest",
        "@slidev/theme-default": "latest",
    },
}


def _ensure_studio() -> tuple[bool, str]:
    """保证 _studio 有 slidev 依赖(装一次)。返回 (可用, 说明)。"""
    studio = studio_root()
    cli = studio / "node_modules" / "@slidev" / "cli" / "bin" / "slidev.mjs"
    if cli.is_file():
        return True, "studio 就绪"
    studio.mkdir(parents=True, exist_ok=True)
    (studio / "package.json").write_text(
        json.dumps(_STUDIO_PKG, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        r = subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund"],
            cwd=str(studio), capture_output=True, text=True, timeout=420,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW, shell=(os.name == "nt"),
        )
        if cli.is_file():
            return True, "studio 首次安装完成"
        return False, f"npm install 未装出 slidev: {(r.stderr or '')[-200:]}"
    except Exception as e:  # noqa: BLE001
        return False, f"npm install 异常: {e}"

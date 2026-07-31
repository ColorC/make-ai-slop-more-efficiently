# [OMNI] origin=ai-ide domain=slidecast/routers ts=2026-06-20T00:00:00Z type=router status=active
# [OMNI] summary="RULE 节点: Intake(读文章建run_dir) / ValidateIR(校验修补) / RenderSlidev(IR→slides.md) / BuildDeck(slidev build 出 HTML)。"
# [OMNI] why="确定性首尾 + 渲染/构建。build 失败优雅降级(仍交付 slides.md), 不炸管线。"
# [OMNI] tags=slidecast,router,intake,render,build
"""slidecast RULE 节点。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import render as _render
from .. import themes as _themes
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
        deck_ir_path = str(req.get("deck_ir", "")).strip()
        topic = str(req.get("topic", "")).strip()
        if not article and not topic and not deck_ir_path:
            return Verdict(kind=VerdictKind.FAIL, output=req,
                           diagnosis=(
                               "需要 -i article=<文章md路径>、-i topic=<题目>"
                               " 或 -i deck_ir=<已校验IR路径>"
                           ))

        title, oneliner, body, source_label, slug = topic or "演示", "", "", topic, _slugify(topic or "deck")
        prebuilt_deck_ir = None
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
        elif deck_ir_path:
            ir_path = Path(deck_ir_path)
            if not ir_path.is_file():
                return Verdict(kind=VerdictKind.FAIL, output=req,
                               diagnosis=f"deck IR 找不到: {deck_ir_path}")
            try:
                prebuilt_deck_ir = json.loads(ir_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                return Verdict(kind=VerdictKind.FAIL, output=req,
                               diagnosis=f"deck IR 读取失败: {exc}")
            if not isinstance(prebuilt_deck_ir, dict) or not prebuilt_deck_ir.get("slides"):
                return Verdict(kind=VerdictKind.FAIL, output=req,
                               diagnosis="deck IR 必须是含 slides 的 JSON 对象")
            meta = prebuilt_deck_ir.get("meta") or {}
            title = str(meta.get("title") or ir_path.stem)
            oneliner = str(meta.get("subtitle") or "")
            source_label = f"复用 {ir_path.name}"
            slug = _slugify(f"{ir_path.stem}-restyle")

        ensure_dirs()
        run_dir = runs_root() / f"{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}-{slug}"
        run_dir.mkdir(parents=True, exist_ok=True)
        if body:  # 存全文,供视频脚本撰写(给不懂的人讲清,需完整原文不能只靠精简 IR)
            (run_dir / "source.md").write_text(body, encoding="utf-8")

        do_build = _truthy(req.get("build", True))
        flatten = _truthy(req.get("flatten", False))
        visual_themes = _themes.normalize_themes(
            req.get("themes") or req.get("visual_themes") or req.get("visual_theme")
        )
        out = {
            "title": title, "oneliner": oneliner, "body": body,
            "source_label": source_label, "slug": slug,
            "style": str(req.get("style", "讲解")), "run_dir": str(run_dir),
            "do_build": do_build, "visual_themes": visual_themes,
            "flatten": flatten, "prebuilt_deck_ir": prebuilt_deck_ir,
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
        density_pages: list[dict[str, Any]] = []
        for s in slides:
            b = s.get("bullets")
            if isinstance(b, list):
                trimmed = [x for x in b if str(x).strip()]
                if trimmed != b:
                    s["bullets"] = trimmed
                    fixes += 1
            if s.get("layout") == "dashboard":
                panels = [
                    panel for panel in (s.get("panels") or [])
                    if isinstance(panel, dict)
                    and any(str(panel.get(key) or "").strip() for key in ("label", "value", "caption"))
                ][:4]
                if panels != s.get("panels"):
                    s["panels"] = panels
                    fixes += 1
            density_pages.append(_density_summary(len(density_pages) + 1, s))
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
        (run_dir / "density_report.json").write_text(
            json.dumps(
                {
                    "contract": {
                        "canvas": "960x540 (scales to 1920x1080)",
                        "body": "1.08rem (~35px at 1080p)",
                        "title": "2.08rem (~67px at 1080p)",
                        "cover": "3.35rem (~107px at 1080p)",
                        "max_panels": 4,
                        "empty_panels": "not rendered",
                    },
                    "pages": density_pages,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
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
        visual_themes = _themes.normalize_themes(ctx.get("visual_themes"))
        theme_slides: dict[str, str] = {}
        first_md = ""
        for theme in visual_themes:
            md = _render.render_slidev(
                ctx["deck_ir"],
                video=bool(ctx.get("flatten")),
                visual_theme=theme,
            )
            slides_variant = run_dir / f"slides-{theme}.md"
            slides_variant.write_text(md, encoding="utf-8")
            theme_slides[theme] = str(slides_variant)
            if not first_md:
                first_md = md
        slides_path = run_dir / "slides.md"
        slides_path.write_text(first_md, encoding="utf-8")
        css_paths: dict[str, str] = {}
        for theme in visual_themes:
            css_path = run_dir / f"style-{theme}.css"
            css_path.write_text(_themes.theme_css(theme), encoding="utf-8")
            css_paths[theme] = str(css_path)
        # Compatibility for video/export callers that still expect style.css.
        (run_dir / "style.css").write_text(
            _themes.theme_css(visual_themes[0]), encoding="utf-8")
        out = dict(ctx)
        out["slides_md"] = str(slides_path)
        out["theme_slides"] = theme_slides
        out["visual_themes"] = visual_themes
        out["theme_css"] = css_paths
        n = first_md.count("\n---\n") + 1
        return Verdict(kind=VerdictKind.PASS, output=out,
                       diagnosis=(
                           f"已渲 slides.md（约 {n} 页, {len(first_md)} 字符）"
                           f" + {len(visual_themes)} 套可比主题"
                       ),
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
        cli = studio / "node_modules" / "@slidev" / "cli" / "bin" / "slidev.mjs"
        visual_themes = _themes.normalize_themes(ctx.get("visual_themes"))
        css_paths = ctx.get("theme_css") if isinstance(ctx.get("theme_css"), dict) else {}
        theme_slides = (
            ctx.get("theme_slides") if isinstance(ctx.get("theme_slides"), dict) else {}
        )
        # 在 studio 内构建:userRoot=studio → 自动加载 studio/style.css;
        # 同一份 slides.md 逐个换皮,保证四版内容/布局/动画完全一致。
        try:
            # 浅色 shiki 主题(代码块高对比);setup/ 随项目自动加载
            (studio / "setup").mkdir(exist_ok=True)
            (studio / "setup" / "shiki.ts").write_text(
                "import { defineShikiSetup } from '@slidev/types'\n"
                "export default defineShikiSetup(() => ({ themes: { light: 'github-dark', dark: 'github-dark' } }))\n",
                encoding="utf-8")
            # 资产放 public/assets:Slidev background 用字面路径 ./assets/x.png,public 原样serve不哈希(否则404)
            shutil.rmtree(studio / "assets", ignore_errors=True)
            studio_assets = studio / "public" / "assets"
            if studio_assets.exists():
                shutil.rmtree(studio_assets, ignore_errors=True)
            run_assets = run_dir / "assets"
            if run_assets.is_dir():
                studio_assets.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(run_assets, studio_assets)

            theme_decks: dict[str, str] = {}
            failures: dict[str, str] = {}
            for theme in visual_themes:
                slides_source = Path(str(theme_slides.get(theme) or slides_md))
                shutil.copyfile(slides_source, studio / "slides.md")
                css_source = Path(str(css_paths.get(theme) or ""))
                css = (
                    css_source.read_text(encoding="utf-8")
                    if css_source.is_file()
                    else _themes.theme_css(theme)
                )
                (studio / "style.css").write_text(css, encoding="utf-8")
                dist_name = "dist" if len(visual_themes) == 1 else f"dist-{theme}"
                dist = (run_dir / dist_name).resolve()
                r = subprocess.run(
                    ["node", str(cli), "build", "slides.md", "--base", "./", "--out", str(dist)],
                    cwd=str(studio), capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                )
                built = (dist / "index.html").is_file()
                if built:
                    theme_decks[theme] = str(dist / "index.html")
                else:
                    failures[theme] = (r.stderr or r.stdout or "")[-500:]

            out["theme_decks"] = theme_decks
            manifest = {
                "title": ctx.get("title", ""),
                "source": ctx.get("source_label", ""),
                "same_ir": str(run_dir / "deck_ir_valid.json"),
                "themes": {
                    theme: {
                        "label": _themes.THEME_LABELS[theme],
                        "description": _themes.THEME_DESCRIPTIONS[theme],
                        "html": path,
                    }
                    for theme, path in theme_decks.items()
                },
                "failures": failures,
            }
            (run_dir / "theme_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            if theme_decks:
                if len(theme_decks) > 1:
                    comparison = _write_theme_comparison(
                        run_dir, str(ctx.get("title") or "风格对比"), theme_decks)
                    out["deck_html"] = str(comparison)
                else:
                    out["deck_html"] = next(iter(theme_decks.values()))
                failed_note = f"; {len(failures)} 套失败" if failures else ""
                return Verdict(kind=VerdictKind.PASS, output=out,
                               diagnosis=(
                                   f"构建成功: {len(theme_decks)} 套主题"
                                   f"{failed_note} → {out['deck_html']}"
                               ),
                               granted_tags=["domain.slidecast", "stage.built", "kind.sink"])
            out["deck_html"] = None
            return Verdict(kind=VerdictKind.PASS, output=out,
                           diagnosis=(
                               "所有主题均未构建出 index.html(已交付 slides.md)。"
                               f"尾部: {json.dumps(failures, ensure_ascii=False)[-500:]}"
                           ),
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


def _density_summary(page: int, slide: dict[str, Any]) -> dict[str, Any]:
    """Record screen-level density without inventing or rewriting authored copy."""
    layout = str(slide.get("layout") or "")
    list_items = sum(
        len(slide.get(key) or [])
        for key in ("bullets", "left", "right", "events", "panels", "frames")
        if isinstance(slide.get(key), list)
    )
    text_fields = sum(
        1
        for key in ("title", "subtitle", "lead", "stat", "stat_label", "stat_sub",
                    "quote", "callout", "code", "mermaid", "info")
        if str(slide.get(key) or "").strip()
    )
    anchors = list_items + text_fields
    status = "dense" if anchors >= 5 else ("balanced" if anchors >= 3 else "sparse")
    return {
        "page": page,
        "layout": layout,
        "anchors": anchors,
        "list_items": list_items,
        "text_fields": text_fields,
        "status": status,
    }


def _write_theme_comparison(
    run_dir: Path,
    title: str,
    theme_decks: dict[str, str],
) -> Path:
    """Create one local review surface for all successfully built variants."""
    items = [
        {
            "id": theme,
            "label": _themes.THEME_LABELS[theme],
            "description": _themes.THEME_DESCRIPTIONS[theme],
            "src": Path(path).relative_to(run_dir).as_posix(),
        }
        for theme, path in theme_decks.items()
    ]
    payload = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    safe_title = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    html = _THEME_COMPARISON_HTML.replace("__TITLE__", safe_title).replace(
        "__THEMES_JSON__", payload)
    path = run_dir / "theme-comparison.html"
    path.write_text(html, encoding="utf-8")
    return path


_THEME_COMPARISON_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · 四版风格对比</title>
<style>
:root{color-scheme:dark;font-family:Inter,"Noto Sans SC","Microsoft YaHei",sans-serif}
*{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;overflow:hidden;background:#111318;color:#f4f6fb}
body{display:grid;grid-template-rows:104px 1fr}
header{display:grid;grid-template-columns:300px minmax(0,1fr) 230px;grid-template-rows:42px 42px;align-items:center;column-gap:16px;padding:9px 18px;border-bottom:1px solid #30343d;background:#171a20}
.identity{grid-row:1 / 3;align-self:center;min-width:0}.identity h1{font-size:17px;line-height:1.2;margin:0 0 5px}.identity p{font-size:12px;color:#aab2c1;margin:0}
.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
button{appearance:none;border:1px solid #3d4350;background:#20242c;color:#dfe5ef;border-radius:8px;padding:8px 12px;cursor:pointer;font-weight:650}
button:hover{border-color:#6d7890}button.active{background:#eef2f8;color:#171a20;border-color:#eef2f8}
.page-controls{display:flex;align-items:center;gap:6px;min-width:0}
.page-controls::before{content:"样板页";color:#788397;font-size:10px;margin-right:2px}
.page-controls button{padding:6px 9px;font-size:11px}
.hint{grid-column:3;grid-row:1 / 3;color:#8993a5;font-size:11px;text-align:right;line-height:1.45}
main{min-height:0;padding:12px;background:#0d0f13}
.grid{height:100%;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));gap:12px}
.grid.single{grid-template-columns:1fr;grid-template-rows:1fr}
.stage{position:relative;min-width:0;min-height:0;display:flex;align-items:center;justify-content:center;overflow:hidden;border:1px solid #2d323b;border-radius:10px;background:#08090b}
.stage.loading::after{content:"页面载入中…";position:absolute;inset:0;z-index:2;display:grid;place-items:center;background:#0b0d11;color:#7d8797;font-size:12px;letter-spacing:.08em}
.stage.hidden{display:none}.badge{position:absolute;z-index:3;top:8px;right:8px;max-width:calc(100% - 16px);padding:6px 9px;border-radius:6px;background:rgba(11,13,17,.88);border:1px solid rgba(255,255,255,.18);backdrop-filter:blur(8px);pointer-events:none}
.badge b{font-size:12px}.badge span{display:block;color:#aeb7c6;font-size:10px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.grid:not(.single) .badge span{display:none}
iframe{display:block;width:100%;height:100%;border:0;background:#000}
@media(max-width:1100px){body{grid-template-rows:126px 1fr}header{grid-template-columns:230px 1fr;grid-template-rows:48px 48px}.identity{grid-row:1 / 3}.hint{display:none}.page-controls{flex-wrap:wrap}.grid{grid-template-columns:1fr;grid-template-rows:repeat(4,minmax(0,1fr))}}
</style>
</head>
<body>
<header>
  <div class="identity"><h1>__TITLE__ · 四版视觉 Demo</h1><p>同一份 Qwen 内容与页序；四套主题各自重排构图、组件和动效。</p></div>
  <div class="controls"><button data-mode="grid" class="active">四宫格 G</button></div>
  <div class="page-controls">
    <button data-page="1" class="active">封面</button>
    <button data-page="2">总览</button>
    <button data-page="4">流程</button>
    <button data-page="7">状态</button>
    <button data-page="8">解释</button>
    <button data-page="11">收尾</button>
  </div>
  <div class="hint">先看整体气质，再单版检查字体与密度<br>数字键 1–4 切换，G 返回四宫格</div>
</header>
<main><div class="grid" id="grid"></div></main>
<script>
const themes=__THEMES_JSON__;
const grid=document.querySelector('#grid');
const controls=document.querySelector('.controls');
let currentPage=1;
themes.forEach((theme,index)=>{
  const button=document.createElement('button');
  button.textContent=`${index+1} ${theme.label}`;
  button.dataset.theme=theme.id;
  controls.appendChild(button);
  const stage=document.createElement('section');
  stage.className='stage loading';stage.dataset.theme=theme.id;
  stage.innerHTML=`<div class="badge"><b>${theme.label}</b><span>${theme.description}</span></div><iframe title="${theme.label}" src="${theme.src}#/1"></iframe>`;
  const frame=stage.querySelector('iframe');
  frame.addEventListener('load',()=>setTimeout(()=>stage.classList.remove('loading'),650));
  grid.appendChild(stage);
});
function show(id){
  const isGrid=id==='grid';
  grid.classList.toggle('single',!isGrid);
  document.querySelectorAll('.stage').forEach(el=>el.classList.toggle('hidden',!isGrid&&el.dataset.theme!==id));
  document.querySelectorAll('button').forEach(el=>el.classList.toggle('active',(isGrid&&el.dataset.mode==='grid')||el.dataset.theme===id));
  document.querySelectorAll('[data-page]').forEach(el=>el.classList.toggle('active',Number(el.dataset.page)===currentPage));
}
function showPage(page){
  currentPage=Number(page)||1;
  document.querySelectorAll('.stage').forEach((stage,index)=>{
    stage.classList.add('loading');
    stage.querySelector('iframe').src=`${themes[index].src}#/${currentPage}`;
    setTimeout(()=>stage.classList.remove('loading'),900);
  });
  show('grid');
}
document.querySelector('header').addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.page){showPage(button.dataset.page);return;}
  show(button.dataset.mode||button.dataset.theme);
});
addEventListener('keydown',event=>{
  if(event.key.toLowerCase()==='g')show('grid');
  const index=Number(event.key)-1;if(themes[index])show(themes[index].id);
});
</script>
</body>
</html>
"""


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

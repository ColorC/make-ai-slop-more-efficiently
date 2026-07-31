# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-21T00:00:00Z type=helper status=active
# [OMNI] summary="把生成好的 deck(dist)批量发布到 colorc.cc 真源(personal-homepage):拷 dist+点阵字体,写 demos.json 清单。"
# [OMNI] why="网页 demo 批量出口:repeatable —— 按文章清单找最新 run、发布、生成清单,供 demos.html/post.js 消费。"
# [OMNI] tags=slidecast,publish,demos,colorc
"""slidecast 网页 demo 发布器(批量、可重复)。

用法: python -m omnicompany.packages.domains.slidecast.publish
按 DEMOS 清单:① 找每篇文章最新 run 的 dist;② 拷到 personal-homepage/demos/<slug>/;
③ 点阵字体拷到站根 fonts/;④ 写 data/demos.json 给 demos.html / post.js 消费。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import render as R
from ._paths import runs_root, studio_root

# colorc.cc 真源(memory: webworks/apps/personal-homepage 是 colorc.cc 真站)
# 发布目标根由环境变量 SLIDECAST_PUBLISH_HOME 配置, 不硬编码本机路径。
HOME = Path(os.environ.get("SLIDECAST_PUBLISH_HOME", ""))

# 选定也出 demo 的 works(项目页,挑需要的;curated 已发布 posts 自动全收)
WORKS_DEMOS: list[str] = ["aigc-lab"]


def _articles() -> list[dict]:
    """要做 demo 的文章清单 = curated 已发布 posts(index.json)+ 选定 works。
    每项 {slug, src(相对 HOME), article(文章页链接参数)}。批量(batch)与发布(publish)共用。"""
    out: list[dict] = []
    idx = HOME / "data" / "curated" / "index.json"
    if idx.is_file():
        try:
            posts = json.loads(idx.read_text(encoding="utf-8")).get("posts") or []
        except Exception:
            posts = []
        for p in posts:
            slug = p.get("slug")
            if slug and (HOME / "data" / "curated" / f"{slug}.md").is_file():
                out.append({"slug": slug, "src": f"data/curated/{slug}.md",
                            "article": f"post.html?curated={slug}"})
    for slug in WORKS_DEMOS:
        if (HOME / "data" / "works" / f"{slug}.md").is_file():
            out.append({"slug": slug, "src": f"data/works/{slug}.md",
                        "article": f"post.html?work={slug}"})
    # 已构建过的 work demo 自动纳入(demo 分支给某 work 出过 demo 后无需手动加进 WORKS_DEMOS)
    seen = {a["slug"] for a in out}
    demos_dir = HOME / "demos"
    if demos_dir.is_dir():
        for dd in sorted(demos_dir.iterdir()):
            slug = dd.name
            if slug in seen or not (dd / "index.html").is_file():
                continue
            if (HOME / "data" / "works" / f"{slug}.md").is_file():
                out.append({"slug": slug, "src": f"data/works/{slug}.md",
                            "article": f"post.html?work={slug}"})
    return out


def _latest_run(slug: str) -> Path | None:
    # run_dir 的 slug 被 _slugify 截到 48 字符,长 slug 要按截断形匹配(否则 endswith 全名永远落空)
    trunc = slug[:48]
    cands = [d for d in runs_root().iterdir()
             if d.is_dir() and d.name.endswith(trunc) and (d / "dist" / "index.html").is_file()]
    return max(cands, key=lambda d: d.stat().st_mtime) if cands else None


def _deck_meta(run: Path) -> dict:
    try:
        return (json.loads((run / "deck_ir_valid.json").read_text(encoding="utf-8")).get("meta") or {})
    except Exception:
        return {}


def _build_at_base(run: Path, slug: str, out: Path) -> bool:
    """在 studio 里用正确的子路径 base 重新构建(SPA 路由+资源都需要 /demos/<slug>/;
    必须走 Python subprocess —— Git-Bash 会把 /demos 前导斜杠转成 Git 安装路径)。"""
    studio = studio_root()
    (studio / "style.css").write_text(R.STYLE_CSS, encoding="utf-8")
    shutil.rmtree(studio / "public" / "assets", ignore_errors=True)
    if (run / "assets").is_dir():
        (studio / "public").mkdir(parents=True, exist_ok=True)
        shutil.copytree(run / "assets", studio / "public" / "assets")
    # 按当前 render 重渲 slides.md(让 hash 路由/皮等改动随发布生效,不必重跑管线)
    deck = json.loads((run / "deck_ir_valid.json").read_text(encoding="utf-8"))
    (studio / "slides.md").write_text(R.render_slidev(deck), encoding="utf-8")
    cli = studio / "node_modules" / "@slidev" / "cli" / "bin" / "slidev.mjs"
    shutil.rmtree(out, ignore_errors=True)
    # base 必须用相对 './':配合 hash 路由(render 头部 routerMode: hash)才能在子路径静态托管下翻页。
    # ⚠ 用 '/demos/<slug>/' 会把 base 塞进 hash 路由(#/demos/.../2)→ 匹配不到 → Slidev 弹 not-found 页。
    subprocess.run(["node", str(cli), "build", "slides.md", "--base", "./", "--out", str(out)],
                   cwd=str(studio), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (out / "index.html").is_file()


def _load_env() -> None:
    """脚本撰写要 LLM(THE_COMPANY_API_KEY 在 omnicompany/.env);任何入口跑都先加载。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[5] / ".env")
    except Exception:
        pass


def _gen_script(run: Path, out: Path, force: bool = False) -> bool:
    """据原文全文 + 每页屏幕内容,写逐页讲解脚本 → demos/<slug>/script.json(供讲解面板跟随翻页)。
    幂等:script.json 已存在则跳过 LLM(除非 force),批量重发不重复花钱。"""
    if not force and (out / "script.json").is_file():
        return True
    from .video import write_script
    try:
        deck = json.loads((run / "deck_ir_valid.json").read_text(encoding="utf-8"))
        src = run / "source.md"
        article = src.read_text(encoding="utf-8") if src.is_file() else ""
        script = write_script(deck, article)
        if len(script) != len(deck.get("slides") or []):
            print(f"  script 页数不齐({len(script)} vs {len(deck.get('slides') or [])})"); return False
        (out / "script.json").write_text(
            json.dumps({"slides": script}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  script {len(script)} 页")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  script fail: {e}"); return False


def publish(articles: list[dict] | None = None, force_script: bool = False,
            rebuild: set[str] | None = None) -> list[dict]:
    """发布所有(或给定子集)已生成 demo 到 colorc.cc 真源,写完整 demos.json。
    articles=None → 全部已发布文章(_articles());没 run 的跳过(留给 batch 先生成)。
    rebuild=None → 重建所有 deck(改皮/路由时用);rebuild={slugs} → 只重建这些,其余复用已构建产物(增量,省时)。"""
    _load_env()
    articles = articles if articles is not None else _articles()
    demos_root = HOME / "demos"
    demos_root.mkdir(parents=True, exist_ok=True)
    # 点阵字体放站根 fonts/(deck CSS 用绝对 /fonts/,所有 deck 共用一份)
    fonts_dst = HOME / "fonts"
    fonts_dst.mkdir(exist_ok=True)
    for f in (studio_root() / "public" / "fonts").glob("*.woff2"):
        shutil.copyfile(f, fonts_dst / f.name)

    manifest, to_thumb = [], []
    for d in articles:
        slug = d["slug"]
        run = _latest_run(slug)
        if not run:
            continue  # 还没生成(batch 会先跑 slidecast.run);静默跳过
        out = demos_root / slug
        built = (out / "index.html").is_file()
        need_build = rebuild is None or slug in rebuild or not built
        if need_build:
            if not _build_at_base(run, slug, out):
                print(f"  构建失败 {slug}")
                continue
        elif not built:
            continue
        if need_build or not (out / "cover.png").is_file():
            to_thumb.append(slug)
        meta = _deck_meta(run)
        has_script = _gen_script(run, out, force=force_script)
        manifest.append({
            "slug": slug,
            "deck": f"demos/{slug}/index.html",
            "thumb": f"demos/{slug}/cover.png",
            "title": str(meta.get("title") or slug),
            "desc": str(meta.get("subtitle") or ""),
            "article": d["article"],
            "run": run.name,
            "v": int((out / "index.html").stat().st_mtime),  # 构建时间戳:每次重建都变 → iframe ?v 破缓存/CDN
            **({"script": f"demos/{slug}/script.json"} if has_script else {}),
        })
        print(f"  [ok] {slug} <- {run.name}")

    _thumbs(to_thumb)  # 只截新建/缺图的 deck 封面(增量,省时)
    (HOME / "data" / "demos.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _sync_index_demos(manifest)  # 回填 curated index.json 的 demo 字段(文章列表显示有演示)
    print(f"写 demos.json: {len(manifest)} 个 demo + 字体到站根 fonts/")
    return manifest


def _sync_index_demos(manifest: list[dict]) -> None:
    """把每个 demo 的链接回填到 curated index.json 的 posts[].demo,文章列表/页面据此显示「有演示」。"""
    by_slug = {m["slug"]: f"/demos/{m['slug']}/" for m in manifest}
    idxp = HOME / "data" / "curated" / "index.json"
    if not idxp.is_file():
        return
    try:
        idx = json.loads(idxp.read_text(encoding="utf-8"))
    except Exception:
        return
    changed = False
    for p in idx.get("posts", []):
        link = by_slug.get(p.get("slug"))
        if link and p.get("demo") != link:
            p["demo"] = link; changed = True
    if changed:
        idxp.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _thumbs(slugs: list[str]) -> None:
    """启临时静态服,截每个 deck 封面(slide 1)→ demos/<slug>/cover.png 作列表缩略图。"""
    import functools
    import http.server
    import socketserver
    import threading
    import time
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("  无 playwright,跳过缩略图"); return
    socketserver.TCPServer.allow_reuse_address = True
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HOME))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 960, "height": 540})
            for slug in slugs:
                try:
                    pg.goto(f"http://127.0.0.1:{port}/demos/{slug}/", wait_until="networkidle", timeout=30000)
                    time.sleep(3.5)
                    pg.screenshot(path=str(HOME / "demos" / slug / "cover.png"))
                    print(f"  thumb {slug}")
                except Exception as e:  # noqa: BLE001
                    print(f"  thumb fail {slug}: {e}")
            b.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    publish()

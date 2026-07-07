# [OMNI] origin=ai-ide domain=slidecast ts=2026-06-21T00:00:00Z type=helper status=active
# [OMNI] summary="视频出口(A路:帧+旁白+ffmpeg)。deck IR → 每页口语旁白(LLM)→ edge-tts → 截帧(全显模式)→ ffmpeg 每页一段拼成带旁白 MP4。"
# [OMNI] why="真视频:旁白为核心、讲完一页再翻(audio-driven)。CosyVoice3 为生产 TTS(需模型部署),本机用 edge-tts 打通端到端。"
# [OMNI] tags=slidecast,video,tts,ffmpeg,narration
"""slidecast 视频生成(A 路:audio-driven 帧拼接)。

用法: python -m omnicompany.packages.domains.slidecast.video <run_dir>
产物: <run_dir>/video/deck.mp4 + narration.json + 每页 mp3/frame。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

from ._llm import safe_json
from .render import _s, _clean_public

VOICE = "zh-CN-YunxiNeural"  # 活泼男声,讲解/说书口吻;生产可换 CosyVoice3
SCRIPT_SYSTEM = """你是讲解视频的脚本撰写人。给你【原文全文】+【据它做的演示 deck 每页内容】。为视频写**旁白脚本**——讲给完全没看过原文、不了解背景的人听。

铁律:
- **旁白 ≠ 屏幕文字**。屏幕上是精炼要点/图表;你的旁白要把它讲清楚(为什么、怎么回事、说明了什么),绝不是把要点念一遍。
- **第 1 页先把背景立住**:这在讲什么、要解决什么问题、对谁有用。别一上来就甩 "v1""KB" 这种没头没脑的词。
- **每个术语第一次出现就解释**:v1 = 这套提示词的第一版;KB = 要建的本地知识库,把分散在各处的资料聚成一份能 grep 检索的 markdown;agent/代理 = 跑大模型自动完成任务的脚本;RAG = 让模型基于检索到的真实素材生成…… 原文有定义就用原文的说法。
- **完整、流畅、像人在好好讲**。别惜字如金:该铺垫就铺垫,该举例就举例,用"所以/但是/接下来"自然衔接,连成一段完整讲解,不是一堆孤立短句。讲完整、表达完整。
- **忠于原文事实**:具体数字/接口/结论按原文;原文没讲清或跳步的地方用常识补全让外行跟得上,但**绝不编造**具体数据/名字/接口。原文里含糊或像是笔误的地方,讲清楚而不是将错就错。
- **这是对外宣发内容,绝不出现"制作过程/用户意图/内部"类元信息**:不要说"脱敏/已脱敏/打码/示意/内部/这里删改过/为隐私改写"之类——把它当一篇面向公众的成品来讲,观众不需要知道它是怎么做出来的、改过几版本身。原文里若带这类括注,直接当它不存在、只讲实质内容。
- 第 1 页总起(可稍长把背景讲透),中间每页承接讲清该页论点,最后一页收束点题。
- 自然中文口语;不要"这一页/如图所示/接下来这页"这类念稿话术。

输出 JSON: {"script": ["第1页旁白","第2页旁白", ...]},长度必须等于页数;每段是 2-6 句的完整讲解。"""


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _plain(t: str) -> str:
    """去掉 markdown 强调标记,供口播/字幕(别把 ** 念出来或烧上屏)。"""
    import re
    t = (t or "").strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t.strip()


def _slide_brief(s: dict, i: int) -> dict:
    """给脚本撰写人看的"这页屏幕上有什么"(精简,供其据此讲解,不是让它照抄)。"""
    b: dict = {"page": i + 1, "layout": s.get("layout"), "title": _plain(s.get("title") or "")}
    for k in ("lead", "subtitle", "stat", "stat_label", "stat_sub"):
        if s.get(k):
            b[k] = _plain(s.get(k))
    pts = [_plain(x) for x in (s.get("bullets") or s.get("left") or []) if _plain(x)]
    pts += [_plain(x) for x in (s.get("right") or []) if _plain(x)]
    if pts:
        b["points"] = pts
    if s.get("code"):
        b["code"] = _s(s.get("code"))[:400]
    if s.get("mermaid"):
        b["diagram"] = _s(s.get("mermaid"))[:300]
    if s.get("events"):
        b["timeline"] = s.get("events")
    if s.get("panels"):
        b["panels"] = s.get("panels")
    if s.get("callout"):
        b["callout"] = _plain(s.get("callout"))
    return b


def write_script(deck: dict, article: str) -> list[str]:
    """据【原文全文】+【每页屏幕内容】写完整、自洽、给外行听得懂的逐页旁白脚本(与屏幕文字两回事)。"""
    from .render import _normalize_deck
    deck = _normalize_deck(deck)  # 清洗元信息(脱敏/内部…)+全角,brief 也干净
    slides = deck.get("slides") or []
    briefs = [_slide_brief(s, i) for i, s in enumerate(slides)]
    res = safe_json(
        SCRIPT_SYSTEM,
        {"title": (deck.get("meta") or {}).get("title", ""), "article": _clean_public(article)[:9000],
         "slides": briefs, "n": len(slides)},
        {"type": "object", "properties": {"script": {"type": "array", "items": {"type": "string"}}},
         "required": ["script"]},
        caller="slidecast.script", max_tokens=8000, default=None)
    script = (res or {}).get("script") if isinstance(res, dict) else None
    if not script or len(script) != len(slides):  # 降级:note/lead/title(不崩,但不理想)
        script = [_plain(s.get("note") or s.get("lead") or s.get("title") or "。") for s in slides]
    return [(_clean_public(x) or "。") for x in script]


async def _tts_one(text: str, out_mp3: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, VOICE).save(str(out_mp3))


def tts_all(narr: list[str], vdir: Path) -> list[Path]:
    mp3s = []
    for i, text in enumerate(narr):
        p = vdir / f"narr_{i:02d}.mp3"
        try:
            asyncio.run(_tts_one(text, p))
        except Exception as e:  # noqa: BLE001
            print(f"  tts {i} 失败: {e}")
        mp3s.append(p)
    return mp3s


def audio_dur(mp3: Path) -> float:
    try:
        from mutagen.mp3 import MP3
        return max(1.2, float(MP3(str(mp3)).info.length))
    except Exception:
        return 3.0


def capture_steps(dist_video: Path, vdir: Path, port: int = 0) -> list[dict]:
    """启静态服 dist_video,playwright 按 ArrowRight 逐 click 推进,每个状态截 1080p 帧。
    通过 URL(/<slide>?clicks=<k>)精确识别 (slide, click),URL 不再变化即到末尾。
    返回 [{'slide':int(1基), 'click':int, 'path':Path}, ...] 演示顺序。"""
    import http.server, socketserver, threading, functools, time, re
    from playwright.sync_api import sync_playwright

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(dist_video))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    actual_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    steps: list[dict] = []

    def parse(u: str) -> tuple[int, int]:
        # 锚到 URL 末尾,别误匹配 host 里的 127(端口/IP);形如 .../<slide>[?clicks=k][#..]
        m = re.search(r"/(\d+)(?:\?clicks=(\d+))?(?:#.*)?$", u)
        sl = int(m.group(1)) if m else 1
        ck = int(m.group(2)) if (m and m.group(2)) else 0
        return sl, ck

    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1920, "height": 1080})
            pg.goto(f"http://127.0.0.1:{actual_port}/", wait_until="networkidle")
            try:  # 等字体 + 封面底图(修 M3)
                pg.wait_for_function("document.fonts && document.fonts.status === 'loaded'", timeout=6000)
            except Exception:
                pass
            time.sleep(3.8)
            i = 0
            while i < 500:
                u = pg.url
                sl, ck = parse(u)
                fr = vdir / f"step_{i:03d}.png"
                pg.screenshot(path=str(fr))
                steps.append({"slide": sl, "click": ck, "path": fr})
                pg.keyboard.press("ArrowRight")
                time.sleep(0.9)
                if pg.url == u:  # 没变 = 已到最后一页最后一个 click
                    break
                i += 1
            b.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return steps


def _caption_frame(src_png: Path, text: str, out_png: Path) -> Path:
    """在帧底部画半透明字幕条 + 中文文字(PIL,CJK 安全,不依赖 libass)。失败则原图返回。"""
    text = (text or "").strip()
    if not text:
        return src_png
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(src_png).convert("RGB")
        W, H = img.size
        fs = max(30, W // 46)
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", fs)
        except Exception:
            font = ImageFont.truetype(r"C:\Windows\Fonts\simhei.ttf", fs)
        # 按宽度折行,英文/数字整词不拆(修 M9:Agent 不再断成 Agen+t)
        import re as _re
        parts = _re.findall(r"[A-Za-z0-9][\w.,:/#%+\-]*|\s+|[^\sA-Za-z0-9]", text)
        draw0 = ImageDraw.Draw(img)
        limit = W * 0.86
        lines, cur = [], ""
        for p in parts:
            if p.isspace():
                if cur and draw0.textlength(cur + " ", font=font) <= limit:
                    cur += " "
                continue
            trial = cur + p
            if cur and draw0.textlength(trial, font=font) > limit:
                lines.append(cur.rstrip()); cur = p
            else:
                cur = trial
        if cur.strip():
            lines.append(cur.rstrip())
        # 修 B3:行首孤立收尾标点吸回上一行末(CJK 避头尾)
        fixed: list[str] = []
        for ln in lines:
            while ln and ln[0] in "。,，!！?？;；:：、）)」』”":
                if fixed:
                    fixed[-1] += ln[0]
                ln = ln[1:].lstrip()
            if ln:
                fixed.append(ln)
        lines = fixed[:3]
        line_h = int(fs * 1.4)
        bar_h = line_h * len(lines) + int(fs * 0.7)
        draw = ImageDraw.Draw(img, "RGBA")
        draw.rectangle([0, H - bar_h, W, H], fill=(26, 26, 20, 205))
        y = H - bar_h + int(fs * 0.35)
        for ln in lines:
            w = draw.textlength(ln, font=font)
            draw.text(((W - w) / 2, y), ln, font=font, fill=(245, 240, 224, 255))
            y += line_h
        img.save(out_png)
        return out_png
    except Exception as e:  # noqa: BLE001
        print(f"  caption 失败({e}),用原帧")
        return src_png


def _encode_still(ff: str, img: Path, dur: float, out: Path, audio: Path | None = None) -> bool:
    """单帧编码成时长 dur 的视频。静止不缩放——弃 Ken Burns(zoompan 逐帧取整会抖)。"""
    cmd = [ff, "-y", "-loop", "1", "-i", str(img)]
    if audio:
        cmd += ["-i", str(audio)]
    vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
    cmd += ["-c:v", "libx264", "-t", f"{dur:.2f}", "-pix_fmt", "yuv420p", "-vf", vf, "-r", "30"]
    if audio:
        # 不加 -shortest:让 -t dur 当家,旁白念完留尾白(修 B4:机关枪硬切、零呼吸)
        cmd += ["-c:a", "aac", "-ar", "44100"]
    cmd += [str(out)]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.is_file()


def _build_slide_clip(ff: str, frames: list[Path], mp3: Path | None, dur: float,
                      clip: Path, vdir: Path, tag: str) -> bool:
    """一页一段:多 click 子帧按序铺满该页旁白时长(揭示动效),旁白整段铺音轨。无字幕、无缩放。"""
    n = len(frames)
    if n <= 1:
        return _encode_still(ff, frames[0], dur, clip, audio=mp3)
    subdur = dur / n
    subs = []
    for j, img in enumerate(frames):
        d = subdur if j < n - 1 else (dur - subdur * (n - 1))
        sp = vdir / f"sub_{tag}_{j}.mp4"
        if _encode_still(ff, img, max(0.8, d), sp, audio=None):
            subs.append(sp)
    if not subs:
        return False
    silent = vdir / f"slide_{tag}.mp4"
    lf = vdir / f"sl_{tag}.txt"
    lf.write_text("".join(f"file '{s.name}'\n" for s in subs), encoding="utf-8")
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lf), "-c", "copy", str(silent)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not silent.is_file():
        subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lf),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if mp3 and silent.is_file():
        subprocess.run([ff, "-y", "-i", str(silent), "-i", str(mp3),
                        "-c:v", "copy", "-c:a", "aac", "-ar", "44100", str(clip)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    elif silent.is_file():
        shutil.copyfile(silent, clip)
    return clip.is_file()


def assemble_steps(steps: list[dict], scripts: list[str], out_mp4: Path, vdir: Path) -> bool:
    """按 slide 分组:每页一段完整旁白(独立脚本),子帧按序揭示,**不烧字幕**,旁白念完留尾白。"""
    from collections import OrderedDict
    ff = _ffmpeg()
    bys: "OrderedDict[int, list[dict]]" = OrderedDict()
    for st in steps:
        bys.setdefault(st["slide"], []).append(st)
    clips = []
    for si, subs in bys.items():
        ni = si - 1
        line = scripts[ni] if 0 <= ni < len(scripts) else ""
        mp3 = vdir / f"narr_{ni:02d}.mp3"
        try:
            asyncio.run(_tts_one(line, mp3))
        except Exception as e:  # noqa: BLE001
            print(f"  tts {ni} 失败: {e}")
        dur = max(audio_dur(mp3) + 0.6, 3.0) if mp3.is_file() else 3.0
        frames = [st["path"] for st in subs]
        clip = vdir / f"clip_{ni:02d}.mp4"
        if _build_slide_clip(ff, frames, mp3 if mp3.is_file() else None, dur, clip, vdir, f"{ni:02d}"):
            clips.append(clip)
        try:
            shutil.copyfile(subs[-1]["path"], vdir / f"frame_{ni:02d}.png")  # 代表帧(无字幕)
        except Exception:
            pass
    if not clips:
        return False
    listf = vdir / "concat.txt"
    listf.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", "-r", "30", str(out_mp4)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out_mp4.is_file()


def make_video(run_dir: Path) -> Path | None:
    from . import render as R
    from ._paths import studio_root
    run_dir = Path(run_dir)
    deck = json.loads((run_dir / "deck_ir_valid.json").read_text(encoding="utf-8"))
    slides = deck.get("slides") or []
    vdir = run_dir / "video"; vdir.mkdir(exist_ok=True)

    # 1) 旁白脚本:据原文全文专门写(给外行讲清、与屏幕文字两回事)
    src = run_dir / "source.md"
    article = src.read_text(encoding="utf-8") if src.is_file() else _s((deck.get("meta") or {}).get("title"))
    scripts = write_script(deck, article)
    # 脚本存 Markdown(人读),每页一节
    title = _s((deck.get("meta") or {}).get("title")) or "演示"
    md = [f"# 旁白脚本 · {title}\n"]
    for i, seg in enumerate(scripts):
        st = slides[i] if i < len(slides) else {}
        head = _s(st.get("title")) or _s(st.get("layout")) or f"第{i + 1}页"
        md.append(f"## 第 {i + 1} 页 · {head}\n\n{seg}\n")
    (vdir / "narration.md").write_text("\n".join(md), encoding="utf-8")
    print(f"旁白脚本 {len(scripts)} 段 (原文 {len(article)} 字) -> narration.md")

    # 2) 视频帧用"分步揭示"渲染(保留 v-click/magic-move,禁转场),build-in-studio
    vmd = R.render_slidev(deck, steps=True)
    (run_dir / "slides_video.md").write_text(vmd, encoding="utf-8")
    studio = studio_root()
    (studio / "style.css").write_text(R.STYLE_CSS, encoding="utf-8")
    # 代码高亮交给 fm-theme(它有自己的 light Prism/shiki 配色),不再覆盖 shiki
    shiki_ts = studio / "setup" / "shiki.ts"
    if shiki_ts.exists():
        shiki_ts.unlink()
    shutil.rmtree(studio / "assets", ignore_errors=True)
    sa = studio / "public" / "assets"
    if sa.exists():
        shutil.rmtree(sa, ignore_errors=True)
    if (run_dir / "assets").is_dir():
        sa.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(run_dir / "assets", sa)
    (studio / "slides.md").write_text(vmd, encoding="utf-8")
    dist_video = (run_dir / "dist_video").resolve()
    cli = studio / "node_modules" / "@slidev" / "cli" / "bin" / "slidev.mjs"
    subprocess.run(["node", str(cli), "build", "slides.md", "--base", "./", "--out", str(dist_video)],
                   cwd=str(studio), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not (dist_video / "index.html").is_file():
        print("视频用 build 失败"); return None

    # 3) 逐 click 截帧
    steps = capture_steps(dist_video, vdir)
    n_sl = len({st["slide"] for st in steps})
    print(f"截帧 {len(steps)} 步 / {n_sl} 页(逐 click 揭示)")

    # 4) 合成(逐页:每页一段完整旁白脚本 + 子帧揭示,不烧字幕)
    out = vdir / "deck.mp4"
    ok = assemble_steps(steps, scripts, out, vdir)
    print("合成:", "OK -> " + str(out) if ok else "失败")
    return out if ok else None


if __name__ == "__main__":
    # 视频不是 omni 管线 stage,独立跑;需自己加载 .env(脚本撰写要调 LLM)
    try:
        from dotenv import load_dotenv
        from ._paths import studio_root
        load_dotenv(studio_root().parents[3] / ".env")  # omnicompany/.env
    except Exception:
        pass
    make_video(Path(sys.argv[1]))

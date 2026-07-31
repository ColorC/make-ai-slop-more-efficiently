# [OMNI] origin=ai-ide ts=2026-07-19 type=infra
"""controlplane/bilibili_draft.py — B站写稿页「远程可写」草稿 API + RemoteUI 入口.

三个端点（router 自带全路径，挂载时 prefix=None）:

- ``GET /api/bilibili-draft/{episode}``  读该期草稿（draft/填词.blocks.json + 填词.md）；
  没有草稿则从最新 台本-vN.md 初始化（blockpage.parse_blocks）并立即落盘。
- ``PUT /api/bilibili-draft/{episode}``  写入草稿：整个 blocks 状态 + 渲染好的填词 md。
  body = {scenes?, blocks, nextNum?, scriptVersion?, md?}；md 缺省时服务端按同格式重渲染。
- ``GET /bilibili-write/{episode}``      返回最新 写稿页-vN.html（RemoteUI 入口，
   页面在 http(s) 协议下自动进入服务器模式：GET 草稿 → 编辑 400ms 防抖 PUT）。
- ``GET /bilibili-video/{episode}/``    返回该期“网页原工程”审阅壳；审阅壳按
  ``video/production-manifest.json`` 装配网页影片真源，资源全部走同源路径，
  驾驶舱可递归圈选 DOM。没有 manifest 的 EP0 历史版本继续走原审阅页。

安全纪律：episode 白名单 ``^[a-z0-9-]+$``；所有路径 resolve 后强制落在
``episodes/<episode>/`` 内，草稿只允许写进其 ``draft/`` 子目录（防路径穿越）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from omnicompany.dashboard.boss_sight.reviewstage.links import review_material_open_path
from omnicompany.packages.domains.bilibili_publish.blockpage import EPISODES_DIR, parse_blocks

logger = logging.getLogger(__name__)

bilibili_draft_router = APIRouter(tags=["bilibili-draft"])

_EP_RE = re.compile(r"^[a-z0-9-]+$")
_MAX_BODY_BYTES = 4 * 1024 * 1024
_SPEAKERS = {"我", "vd_system", "vd_assistant", "vd_daemon"}

# 写稿台 → 审阅台闭环契约（submit-review）：project/track/version_family/source_plan 固定口径
_REVIEW_PROJECT = "bilibili-publish"
_REVIEW_TRACK = "写稿"
_REVIEW_SOURCE_PLAN = "publish/[2026-07-19]BILIBILI-CONTENT-LINE"

_EPISODES_BASE = EPISODES_DIR.resolve()
_WEB_REVIEW_SHELL_PATH = (EPISODES_DIR.parent / "video" / "facility" / "web_review_shell.html").resolve()
_WEB_REVIEW_KINDS = {"demo", "html", "static-report"}


# ─────────────────────────── 路径与校验 ───────────────────────────


def _episode_dir(episode: str) -> Path:
    if not _EP_RE.fullmatch(episode or ""):
        raise HTTPException(status_code=400, detail=f"invalid episode: {episode!r}")
    ep_dir = (_EPISODES_BASE / episode).resolve()
    if not ep_dir.is_relative_to(_EPISODES_BASE) or not ep_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"unknown episode: {episode}")
    return ep_dir


def _draft_dir(ep_dir: Path) -> Path:
    d = (ep_dir / "draft").resolve()
    if not d.is_relative_to(ep_dir):  # 理论不可达（episode 已过白名单），双保险
        raise HTTPException(status_code=400, detail="draft path escapes episode dir")
    return d


def _web_review_config(episode: str, video_dir: Path) -> dict | None:
    """从当期 manifest 装配共享网页视频审阅壳；非 Web 审阅声明返回 None。"""
    manifest_path = video_dir / "production-manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"video manifest is invalid: {exc}") from exc

    review = manifest.get("review") or {}
    if review.get("kind") not in _WEB_REVIEW_KINDS or review.get("profile_id") != "generic-web":
        return None

    render = manifest.get("render") or {}
    entry_html = str(render.get("entry_html") or "").strip("/").replace("\\", "/")
    if not entry_html:
        raise HTTPException(status_code=500, detail="web review requires render.entry_html")
    entry_path = (video_dir / entry_html).resolve()
    if not entry_path.is_relative_to(video_dir):
        raise HTTPException(status_code=400, detail="render.entry_html escapes episode video dir")
    if not entry_path.is_file():
        raise HTTPException(status_code=404, detail=f"web video source missing: {entry_html}")

    beats: list[dict] = []
    for raw in manifest.get("beats") or []:
        if not isinstance(raw, dict):
            continue
        try:
            start = max(0.0, float(raw.get("start") or 0))
        except (TypeError, ValueError):
            continue
        beats.append(
            {
                "id": str(raw.get("id") or ""),
                "start": start,
                "label": str(raw.get("claim") or raw.get("spoken_span") or raw.get("id") or ""),
            }
        )

    episode_meta = manifest.get("episode") or {}
    return {
        "episode": episode,
        "title": str(episode_meta.get("working_name") or episode),
        "source": f"/bilibili-video/{episode}/{quote(entry_html, safe='/')}",
        "apiName": str(render.get("facility_api") or "__VIDEO_FACILITY__"),
        "duration": float(render.get("duration_seconds") or 0),
        "beats": beats,
    }


def _web_review_page(episode: str, video_dir: Path) -> HTMLResponse | None:
    config = _web_review_config(episode, video_dir)
    if config is None:
        return None
    if not _WEB_REVIEW_SHELL_PATH.is_file():
        raise HTTPException(status_code=500, detail="shared web video review shell is missing")
    template = _WEB_REVIEW_SHELL_PATH.read_text(encoding="utf-8")
    marker = "__VIDEO_REVIEW_CONFIG_JSON__"
    if marker not in template:
        raise HTTPException(status_code=500, detail="shared web video review shell marker is missing")
    payload = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return HTMLResponse(template.replace(marker, payload, 1))


def _latest_version(out_dir: Path, prefix: str, suffix: str) -> str | None:
    top = 0
    for p in out_dir.glob(f"{prefix}-v*.{suffix}"):
        m = re.fullmatch(rf"{re.escape(prefix)}-v(\d+)\.{suffix}", p.name)
        if m:
            top = max(top, int(m.group(1)))
    return f"v{top}" if top else None


def _clean_block(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="block must be an object")
    speaker = str(raw.get("speaker") or "")
    if speaker not in _SPEAKERS:
        raise HTTPException(status_code=400, detail=f"unknown speaker: {speaker!r}")
    blk: dict = {
        "id": str(raw.get("id") or ""),
        "scene": int(raw.get("scene") or 0),
        "speaker": speaker,
        "text": str(raw.get("text") or ""),
    }
    if raw.get("hint") is not None:
        blk["hint"] = str(raw["hint"])
    if raw.get("blank") is not None:
        blk["blank"] = int(raw["blank"])
    return blk


def _clean_scene(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="scene must be an object")
    return {
        "n": int(raw.get("n") or 0),
        "title": str(raw.get("title") or ""),
        "time": str(raw.get("time") or ""),
        "visual": str(raw.get("visual") or ""),
    }


# ─────────────────────────── md 渲染 / 落盘 ───────────────────────────


def _render_md(title: str, scenes: list[dict], blocks: list[dict], source: str) -> str:
    """与写稿页 JS buildMarkdown() 同格式（## 场 分节 + "- 【speaker】：text" 行）。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = (
        f"# {title}（本人填词稿）\n\n"
        f"> 由 {source} 生成，{stamp}。克隆声只念本人显式写的话，电子音归 agent。\n\n"
    )
    for sc in scenes:
        md += f"## 场 {sc['n']} · {sc['title']}（约 {sc['time']}）\n\n"
        md += f"画面：{sc['visual']}\n\n"
        for b in [x for x in blocks if x["scene"] == sc["n"]]:
            t = (b.get("text") or "").strip()
            flat = re.sub(r"\s*\n+\s*", " ", t)
            if b["speaker"] == "我":
                if t:
                    tag = f"（留空 {b['blank']}）" if b.get("blank") else ""
                    md += f"- 【我】{tag}：{flat}\n"
                elif b.get("hint") and b.get("blank"):
                    hint = re.sub(r"\s*\n+\s*", " ", b["hint"].strip())
                    md += f"- 【留空 {b['blank']}】提示：{hint}（未填写）\n"
            elif t:
                md += f"- 【{b['speaker']}】：{flat}\n"
        md += "\n"
    return md


def _write_draft(ddir: Path, state: dict, md: str) -> None:
    """原子写 draft/填词.blocks.json + draft/填词.md（tmp + replace）。"""
    ddir.mkdir(parents=True, exist_ok=True)
    json_path = ddir / "填词.blocks.json"
    md_path = ddir / "填词.md"
    json_tmp = ddir / ".填词.blocks.json.tmp"
    md_tmp = ddir / ".填词.md.tmp"
    json_tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    md_tmp.write_text(md, encoding="utf-8")
    json_tmp.replace(json_path)
    md_tmp.replace(md_path)


def _latest_script(ep_dir: Path) -> tuple[str, Path]:
    version = _latest_version(ep_dir, "台本", "md")
    if not version:
        raise HTTPException(status_code=404, detail="该期还没有 台本-vN.md，无法初始化草稿")
    return version, ep_dir / f"台本-{version}.md"


def _require_current_script_version(payload: dict, current_version: str) -> None:
    """拒绝旧写稿页把过期结构覆盖到当前运行真源。"""
    payload_version = str(payload.get("scriptVersion") or "")
    if payload_version != current_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"stale writing page: payload scriptVersion={payload_version or 'missing'}, "
                f"current={current_version}; reload before editing"
            ),
        )


def _rebase_draft_onto_script(existing: dict, parsed: dict, version: str, episode: str) -> dict:
    """用最新 Qwen 台本替换 AI 块，同时按 blank 编号原字保留本人填词。

    draft/填词.blocks.json 是编辑页与视频页共同读取的运行真源。Qwen 生成新版本后，
    不能继续沿用旧 AI 块，也不能清空用户已经填写的本人块。
    """
    filled_by_blank: dict[int, str] = {}
    for raw in existing.get("blocks") or []:
        if not isinstance(raw, dict) or str(raw.get("speaker") or "") != "我":
            continue
        try:
            blank = int(raw.get("blank") or 0)
        except (TypeError, ValueError):
            continue
        text = str(raw.get("text") or "")
        if blank > 0 and text:
            filled_by_blank[blank] = text

    blocks: list[dict] = []
    for raw in parsed.get("blocks") or []:
        block = dict(raw)
        if block.get("speaker") == "我" and block.get("blank"):
            block["text"] = filled_by_blank.get(int(block["blank"]), "")
        blocks.append(block)

    return {
        "format": "xiegao-blocks/v1",
        "episode": episode,
        "scriptVersion": version,
        "savedAt": datetime.now().isoformat(timespec="seconds"),
        "scenes": list(parsed.get("scenes") or []),
        "blocks": blocks,
        "nextNum": len(blocks) + 1,
        "rebasedFrom": str(existing.get("scriptVersion") or "unknown"),
    }


# ─────────────────────────── 端点 ───────────────────────────


@bilibili_draft_router.get("/api/bilibili-draft/{episode}")
def get_draft(episode: str) -> dict:
    """读草稿；没有则从最新台本初始化（blocks 全空文本 + 台本 hint）并落盘。"""
    ep_dir = _episode_dir(episode)
    ddir = _draft_dir(ep_dir)
    json_path = ddir / "填词.blocks.json"
    md_path = ddir / "填词.md"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — 草稿损坏不致命，按重新初始化处理
            logger.warning("bilibili-draft: 草稿 JSON 损坏，重新初始化 (%s)", e)
        else:
            version, script_path = _latest_script(ep_dir)
            if str(data.get("scriptVersion") or "") != version:
                parsed = parse_blocks(script_path.read_text(encoding="utf-8"))
                if not parsed["scenes"] or not parsed["blocks"]:
                    raise HTTPException(status_code=500, detail=f"台本解析失败：{script_path.name}")
                data = _rebase_draft_onto_script(data, parsed, version, episode)
                md = _render_md(
                    parsed["title"] or episode,
                    data["scenes"],
                    data["blocks"],
                    source=f"台本-{version}.md 重基（本人填词按留空编号保留）",
                )
                _write_draft(ddir, data, md)
            data["initialized"] = False
            data["md"] = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
            return data
    version, script_path = _latest_script(ep_dir)
    parsed = parse_blocks(script_path.read_text(encoding="utf-8"))
    if not parsed["scenes"] or not parsed["blocks"]:
        raise HTTPException(status_code=500, detail=f"台本解析失败：{script_path.name}")
    state = {
        "format": "xiegao-blocks/v1",
        "episode": episode,
        "scriptVersion": version,
        "initialized": True,
        "savedAt": None,
        "scenes": parsed["scenes"],
        "blocks": parsed["blocks"],
        "nextNum": len(parsed["blocks"]) + 1,
    }
    md = _render_md(parsed["title"], parsed["scenes"], parsed["blocks"], source=f"台本-{version}.md 初始化")
    _write_draft(ddir, state, md)
    state["md"] = md
    return state


@bilibili_draft_router.put("/api/bilibili-draft/{episode}")
async def put_draft(episode: str, request: Request) -> dict:
    """写入草稿（整个 blocks 状态）。md 缺省时服务端按写稿页同格式重渲染。"""
    ep_dir = _episode_dir(episode)
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="draft too large")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise HTTPException(status_code=400, detail="payload must be an object with blocks: [...]")
    version, script_path = _latest_script(ep_dir)
    _require_current_script_version(payload, version)
    blocks = [_clean_block(b) for b in payload["blocks"]]
    parsed = parse_blocks(script_path.read_text(encoding="utf-8"))
    title = parsed["title"] or episode
    if isinstance(payload.get("scenes"), list) and payload["scenes"]:
        scenes = [_clean_scene(s) for s in payload["scenes"]]
    else:  # 客户端没带 scenes：回退到最新台本的解析结果
        scenes = parsed["scenes"]
    saved_at = datetime.now().isoformat(timespec="seconds")
    state = {
        "format": "xiegao-blocks/v1",
        "episode": episode,
        "scriptVersion": str(payload.get("scriptVersion") or ""),
        "savedAt": saved_at,
        "scenes": scenes,
        "blocks": blocks,
        "nextNum": int(payload.get("nextNum") or len(blocks) + 1),
    }
    md = payload.get("md")
    if not isinstance(md, str) or not md.strip():
        md = _render_md(title, scenes, blocks, source="写稿页服务器模式")
    _write_draft(_draft_dir(ep_dir), state, md)
    return {"ok": True, "episode": episode, "savedAt": saved_at, "blockCount": len(blocks)}


@bilibili_draft_router.get("/bilibili-write/{episode}", response_class=HTMLResponse)
def write_page(episode: str) -> HTMLResponse:
    """RemoteUI 入口：直接返回该期最新写稿页 html（http 打开即进入服务器模式）。"""
    ep_dir = _episode_dir(episode)
    version = _latest_version(ep_dir, "写稿页", "html")
    if not version:
        raise HTTPException(status_code=404, detail="该期还没有 写稿页-vN.html")
    return HTMLResponse((ep_dir / f"写稿页-{version}.html").read_text(encoding="utf-8"))


@bilibili_draft_router.get("/bilibili-video/{episode}", include_in_schema=False)
def video_page_redirect(episode: str) -> RedirectResponse:
    """补齐尾斜杠，让审阅壳和网页影片素材的相对路径稳定。"""
    _episode_dir(episode)
    return RedirectResponse(url=f"/bilibili-video/{episode}/", status_code=307)


@bilibili_draft_router.get("/bilibili-video/{episode}/{asset_path:path}", include_in_schema=False)
def video_page_asset(episode: str, asset_path: str = "") -> Response:
    """同源提供网页原工程、共享审阅壳及素材，避免录制副本与审阅真源漂移。"""
    ep_dir = _episode_dir(episode)
    video_dir = (ep_dir / "video").resolve()
    if not video_dir.is_relative_to(ep_dir) or not video_dir.is_dir():
        raise HTTPException(status_code=404, detail="该期还没有 video 目录")
    if not asset_path:
        web_review = _web_review_page(episode, video_dir)
        if web_review is not None:
            return web_review
    clean = (asset_path or "ep00_review.html").strip("/").replace("\\", "/")
    canonical = {
        "ep00_review.html": video_dir / "pipeline" / "ep00_review.html",
        "ep00.html": video_dir / "pipeline" / "ep00.html",
        "ep00_review_v9.html": video_dir / "pipeline" / "ep00_review_v9.html",
        "ep00_v9.html": video_dir / "pipeline" / "ep00_v9.html",
        "ep00_review_v12.html": video_dir / "pipeline" / "ep00_review_v12.html",
        "ep00_v12.html": video_dir / "pipeline" / "ep00_v12.html",
    }
    fp = canonical.get(clean, video_dir / clean)
    fp = fp.resolve()
    if not fp.is_relative_to(video_dir):
        raise HTTPException(status_code=400, detail="video path escapes episode dir")
    if not fp.is_file():
        raise HTTPException(status_code=404, detail=f"video asset missing: {clean}")
    media = {
        ".html": "text/html; charset=utf-8", ".json": "application/json",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webm": "video/webm", ".mp4": "video/mp4", ".wav": "audio/wav",
    }.get(fp.suffix.lower())
    return FileResponse(str(fp), media_type=media)


# ─────────────────────────── 写稿台 → 审阅台闭环 ───────────────────────────


def _episode_code(episode: str) -> str:
    """ep00-meta-first → EP0；不匹配回退原串。"""
    m = re.match(r"ep(\d+)", episode)
    return f"EP{int(m.group(1))}" if m else episode


@bilibili_draft_router.post("/api/bilibili-draft/{episode}/submit-review")
def submit_review(episode: str) -> dict:
    """把 draft/填词.md 提交审阅台（version = 同 family 内最大 version + 1，不覆盖旧版）。

    材料契约（用户拍板）：project=bilibili-publish、track=写稿、
    version_family="<EP 码> 台本-填词"、kind=markdown、tier=important。
    文件走 MaterialStore.stage_file_from_bytes 落 store 的 files/（与 omni review submit 同径）。
    """
    ep_dir = _episode_dir(episode)
    md_path = _draft_dir(ep_dir) / "填词.md"
    if not md_path.is_file():
        raise HTTPException(status_code=409, detail="还没有草稿（draft/填词.md 不存在）——先在写稿页填词并保存")
    md_text = md_path.read_text(encoding="utf-8")
    if not md_text.strip():
        raise HTTPException(status_code=409, detail="草稿是空的——先填词再提交审阅")
    ep_code = _episode_code(episode)
    family = f"{ep_code} 台本-填词"
    blocks_path = _draft_dir(ep_dir) / "填词.blocks.json"
    revision = None
    if blocks_path.is_file():
        try:
            script_version = str(json.loads(blocks_path.read_text(encoding="utf-8")).get("scriptVersion") or "")
            match = re.fullmatch(r"v(\d+)", script_version, re.IGNORECASE)
            revision = int(match.group(1)) if match else None
        except (OSError, ValueError, json.JSONDecodeError):
            revision = None
    try:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store

        store = get_store()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"审阅台 store 不可用: {e}")
    # 含已归档一起取 max——归档只是不在默认列表显示，版本号不许回退重用
    siblings = [
        m
        for m in store.list(project=_REVIEW_PROJECT, track=_REVIEW_TRACK, include_archived=True)
        if (m.version_family or "") == family
    ]
    version = (max((m.version or 0) for m in siblings) + 1) if siblings else 1
    if revision is None:
        revision = version
    try:
        file_relpath = store.stage_file_from_bytes(md_text.encode("utf-8"), ext=".md")
        material = store.create(
            kind="markdown",
            tier="important",
            title=f"{ep_code} 台本-填词 v{version}（本人填词，待审）",
            source_plan_id=_REVIEW_SOURCE_PLAN,
            file_relpath=file_relpath,
            project=_REVIEW_PROJECT,
            track=_REVIEW_TRACK,
            version=version,
            version_family=family,
            subject_id=ep_code,
            subject_type="episode",
            revision=revision,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "ok": True,
        "name": material.title,
        "url": review_material_open_path(material.id),
        "file_url": f"/api/boss-sight/reviewstage/{material.id}/file?raw=1",
        "id": material.id,
        "version": material.version,
        "track": material.track,
        "version_family": material.version_family,
        "subject_id": material.subject_id,
        "subject_type": material.subject_type,
        "revision": material.revision,
    }


__all__ = ["bilibili_draft_router"]

# [OMNI] origin=ai-ide ts=2026-06-03 type=infra
"""捕获(圈选/快照/调试交接)落盘 + 批量交给总控读取 的后端路由。

挂载于 /api/boss-sight/captures(跟 reviewstage 完全分开 —— 用户明示捕获不进审阅队列)。

- POST   /api/boss-sight/captures           保存一条捕获到 data/boss_sight/captures/pending/<ts>.md
- GET    /api/boss-sight/captures           列 pending 数量 + 文件
- POST   /api/boss-sight/captures/dispatch  把 pending/* 移到 batch_<ts>/, 注入一条消息给唯一总控让其 Read 处理
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from omnicompany.packages.services._core.omnicompany.formats import CAPTURE
from omnicompany.packages.services._core.omnicompany.material_events import publish_material_event

from .surface_registry import get_surface_registry

captures_router = APIRouter(prefix="/api/boss-sight/captures", tags=["captures"])

# omni 实体 kind → 札记 target.kind 映射(统一捕获挂到真实实体, 而不是只挂 page_element)。
# 见 authored/store.py NOTE_TARGET_KINDS 与 entity_registry.make_entity_uri 的 kind。
_OMNI_KIND_TO_TARGET = {
    "review_material": "material",
    "material": "material",
    "plan": "plan",
    "project": "project",
    "cc_session": "llm_session",
    "subagent": "llm_session",
    "file": "file",          # 笔记/文档(authored store 不强校验 kind, 直接存)
    "note": "note",
    "task": "task",
}
# 三态(评论的结构化形态)。详见 plan「三、模态收口」。
VERDICTS = {"keep", "reject", "undecided"}

# omni 实体 kind → 人话标签。解析结果给 AI 看时用文件路径 + 完整描述, 不暴露 omni:// 这种模型陌生的自造规范
# (用户反馈 2026-06-27)。omni:// 只留作内部匹配/挂札记的稳定句柄。
_KIND_CN = {
    "review_material": "审阅台的审阅材料", "material": "审阅材料",
    "plan": "计划", "project": "项目",
    "file": "笔记/文档", "note": "笔记",
    "cc_session": "agent 对话会话", "subagent": "子 agent 会话", "task": "任务",
}


def _describe_target(kind: str, title: str, path: str | None, snippet: str,
                     entity_id: str) -> str:
    """把解析到的实体翻成一句"什么东西 + 文件在哪 + 内容是啥"的人话, 给 AI 直接看懂。"""
    label = _KIND_CN.get(kind, kind or "实体")
    parts = [f"{label}:「{title or entity_id}」"]
    if path:
        if kind in ("cc_session", "subagent"):
            parts.append(f"工作目录(对应代码在这里): {path}")
        else:
            parts.append(f"对应文件: {path}")
    if snippet:
        parts.append(str(snippet)[:200])
    return " · ".join(parts)


def _enrich_target(hit: dict[str, Any]) -> dict[str, Any]:
    """给几何解析出的命中(omni_uri/kind/title)补上 真实文件路径 + 完整描述。
    查 entity_registry 拿 path/snippet/source; 查不到就用信标上报的 kind/title 兜底。"""
    uri = hit.get("omni_uri")
    kind = hit.get("kind") or ""
    title = hit.get("title") or ""
    path = None
    snippet = ""
    entity_id = ""
    try:
        from ..entity_registry import parse_entity_uri, resolve_entity_uri
        try:
            _, entity_id = parse_entity_uri(uri)
        except Exception:
            entity_id = ""
        rec = resolve_entity_uri(uri)
        if rec:
            path = rec.get("path") or None
            title = rec.get("title") or title
            kind = rec.get("kind") or kind
            snippet = rec.get("snippet") or ""
    except Exception:
        pass
    return {
        **hit,
        "path": path,
        "description": _describe_target(kind, title, path, snippet, entity_id),
    }


def _enrich_context(hit: dict[str, Any]) -> dict[str, Any]:
    """把 resolve 的富上下文(best + page + 页内 entities)统一翻成 AI 看得懂的 文件路径 + 人话。
    回答: 在哪个页面 · 压在哪个材料(best) · 这页里还有哪些材料(contained)。"""
    page = hit.get("page") or {}
    contained = [_enrich_target(e) for e in (hit.get("entities") or [])]
    contained_out = [
        {"kind": c.get("kind"), "title": c.get("title"), "path": c.get("path"),
         "description": c.get("description")}
        for c in contained
    ]
    if hit.get("omni_uri"):
        best = _enrich_target({"omni_uri": hit["omni_uri"], "kind": hit.get("kind"),
                               "title": hit.get("title")})
        description, path = best["description"], best.get("path")
    else:
        # 在 omni 页面上但没正好压在某张埋点卡 → 用通用 DOM 元素说清"指的是哪个元素"(不靠埋点), 再带页内材料。
        titles = "、".join(c.get("title") or "" for c in contained_out[:8] if c.get("title"))
        pg = page.get("title") or page.get("url") or "页面"
        el = hit.get("element")
        if el and el.get("in_selection") and (el.get("text") or el.get("tag")):
            etxt = (el.get("text") or "").replace("\n", " ").strip()[:70]
            etag = f"{el.get('tag') or '元素'}" + (f"#{el.get('id')}" if el.get("id") else "")
            description = f"在页面「{pg}」上指向 {etag}" + (f"「{etxt}」" if etxt else "")
            if titles:
                description += f"; 附近材料: {titles}"
        else:
            description = f"在页面「{pg}」" + (f"; 这一块附近的材料: {titles}" if titles else "(没压在具体材料上)")
        path = None
    return {**hit, "description": description, "path": path,
            "page": page, "contained": contained_out}


def _captures_root() -> Path:
    # 用户明示 2026-06-04: 路径太长, 在 WindowsWorkspace 下放一个专门文件夹。
    # omni_workspace_root() = .../WindowsWorkspace/omnicompany → .parent = .../WindowsWorkspace。
    # 复制文件直接落根(最短路径); 提交进 pending/ 子目录; dispatch 批次 batch_<ts>/。
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root().parent / "captures"


def _pending_dir() -> Path:
    d = _captures_root() / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clips_dir() -> Path:
    # 「复制」把完整内容(含大段 HTML)写这里(captures 根, 路径最短), 剪贴板只放文件路径一行
    # (用户明示 2026-06-04: 还是太长, 就留文件路径)。clips 不计入「待处理」、不进 dispatch 批次。
    d = _captures_root()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _capture_filename(body: "CaptureBody", ts: str) -> str:
    """文件名体现一点元素(用户明示 2026-06-04), 但保持短: <短名>-<HHMMSS>-<3hex>.md。"""
    t = body.target or {}
    raw = str(t.get("label") or t.get("selector") or body.capture_kind)
    slug = re.sub(r"[^0-9A-Za-z_-]+", "-", raw).strip("-")[:24] or body.capture_kind
    hhmmss = ts.split("T")[-1] if "T" in ts else ts
    return f"{slug}-{hhmmss}-{uuid.uuid4().hex[:3]}.md"


class CaptureBody(BaseModel):
    # 统一捕获: capture_kind 是历史标签(element_comment/debug_start), page_snapshot 已折进 modality。
    # 新统一入口用 "capture"。保留旧值兼容历史调用。
    capture_kind: str = Field(default="capture",
                              pattern="^(capture|element_comment|page_snapshot|debug_start)$")
    # 模态: 截图(still) / 录屏(video) / DOM 快照(dom_snapshot)。录屏与快照都是同一条捕获的选项。
    modality: str = Field(default="still", pattern="^(still|video|dom_snapshot)$")
    title: str | None = Field(default=None, max_length=200)
    comment: str = Field(default="", max_length=20000)
    # 三态(评论的结构化形态): keep / reject / undecided。可与 comment 并存或单独存在。
    verdict: str | None = Field(default=None, max_length=16)
    url: str = Field(default="", max_length=2000)
    route: str = Field(default="", max_length=2000)
    target: dict[str, Any] | None = None
    # 解析出的 omni 实体(omni://kind/id)。caller 已解析就直接传; 没传但给了 screen_rect+content_origin
    # 则本端用 surface_registry 解析(第一层信标几何相交)。
    omni_uri: str | None = Field(default=None, max_length=2000)
    # poof 几何提示(物理像素): 屏幕矩形 [l,t,r,b] + 浏览器内容区屏幕原点 [ox,oy] + devicePixelRatio。
    screen_rect: list[float] | None = None
    content_origin: list[float] | None = None
    dpr: float | None = None
    # 逐条文字标注(评论): {x,y(屏幕物理像素), text}。给了就按每条的位置各自挂到它旁边的那条材料,
    # 而不是整张截图共用一条 best-overlap。见 continuation-plan 点 1。
    annotations: list[dict[str, Any]] | None = None
    text_snapshot: str | None = Field(default=None, max_length=80000)
    dom_snapshot: str | None = Field(default=None, max_length=240000)
    # 真实截图: poof 截任意窗口的真像素, 或同文档表面(如 vilo demo html2canvas)发来的 data:image/png;base64,...
    image_data_url: str | None = Field(default=None, max_length=8_000_000)
    # True(提交)= 存到 pending 进 dispatch 批次; False(复制)= 存到 clips 只为拿文件链接。
    enqueue: bool = True


_KIND_LABEL = {
    "capture": "捕获",
    "element_comment": "圈选元素",
    "page_snapshot": "页面快照",
    "debug_start": "Codex 调试交接",
}
_MODALITY_LABEL = {"still": "截图", "video": "录屏", "dom_snapshot": "DOM 快照"}
_VERDICT_LABEL = {"keep": "保留", "reject": "弃用", "undecided": "未定"}


def _safe_fence(text: str, lang: str = "") -> str:
    body = str(text).replace("```", "`\u200b``")
    return f"```{lang}\n{body}\n```"


def _save_image_data_url(data_url: str, ts: str) -> str | None:
    """\u628a data:image/png;base64,... \u843d\u5230 captures \u6839\u4e0b\u7684\u56fe\u7247\u6587\u4ef6, \u8fd4\u56de\u76f8\u5bf9 captures \u6839\u7684\u8def\u5f84
    (\u4f9b GET /file?path= \u8bfb\u53d6\u3001\u5199\u8fdb note.captures)\u3002\u89e3\u6790\u5931\u8d25\u8fd4\u56de None\u3002"""
    import base64
    import re as _re
    m = _re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", (data_url or "").strip(), _re.DOTALL)
    if not m:
        return None
    ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except Exception:  # noqa: BLE001
        return None
    if not raw or len(raw) > 6_000_000:
        return None
    hhmmss = ts.split("T")[-1] if "T" in ts else ts
    name = f"shot-{hhmmss}-{uuid.uuid4().hex[:6]}.{ext}"
    try:
        (_clips_dir() / name).write_bytes(raw)
    except OSError:
        return None
    return name  # \u76f8\u5bf9 captures \u6839


def _omnimark_header_for_capture(body: "CaptureBody", ts: str) -> str:
    """捕获落盘自述头(批3锚②错误样本㊄): 只补头, 不改既有正文结构。

    字段取"现场就有的值" —— ts/origin 是核心字段; points_to(指向实体 omni_uri, 没有则显式
    留空)/content_time(内容产生时点, 取捕获时间)/ingested_time(落盘时点, 与 content_time
    同时刻, 捕获是即时落盘无延迟)走 extra KV。
    """
    from omnicompany.core.omnimark import OmniMarkFields

    fields = OmniMarkFields(
        origin="boss_sight.captures",
        ts=ts,
        type="capture",
        summary=f"统一捕获({body.modality})落盘的 UI 圈选/截图/批注记录",
        why="批3输入侧语义化: 捕获自述来源+时间+指向实体, 供后续检索与治理识别",
        tags=("capture", "boss-sight"),
    )
    lines = fields.to_comment_lines(comment_prefix="<!--")
    points_to = body.omni_uri or "none"
    lines.append(f'<!-- [OMNI] points_to={points_to} content_time={ts} ingested_time={ts} -->')
    return "\n".join(lines)


def _render_md(body: CaptureBody, ts: str, shot_abs: str | None = None) -> str:
    t = body.target or {}
    lines = [
        _omnimark_header_for_capture(body, ts),
        "",
        f"# 捕获 · {_MODALITY_LABEL.get(body.modality, body.modality)}",
        "",
        f"- 时间: {ts}",
        f"- 模态: {body.modality}",
        f"- URL: {body.url}",
        f"- 路由: {body.route}",
    ]
    if body.omni_uri:
        lines.append(f"- 指向实体: `{body.omni_uri}`")
    if body.verdict:
        lines.append(f"- 三态: {_VERDICT_LABEL.get(body.verdict, body.verdict)}")
    if t.get("selector"):
        lines.append(f"- 选择器: `{t.get('selector')}`")
    if t.get("label"):
        lines.append(f"- 标签: {t.get('label')}")
    if t.get("text"):
        lines.append(f"- 文本: {str(t.get('text'))[:500]}")
    lines += ["", "## 用户批注", "", (body.comment.strip() or "(无)")]
    form_values = t.get("form_values") if isinstance(t.get("form_values"), list) else []
    if form_values:
        lines += ["", "## 表单当前值"]
        for idx, item in enumerate(form_values[:20], start=1):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("name") or item.get("id") or item.get("tag") or f"field-{idx}")
            lines += ["", f"### {idx}. {label}"]
            if item.get("selector"):
                lines.append(f"- 选择器: `{item.get('selector')}`")
            if item.get("checked") is not None:
                lines.append(f"- checked: {bool(item.get('checked'))}")
            lines += ["", _safe_fence(str(item.get("value") or "")[:8000], "text")]
    if t.get("outer_html"):
        lines += ["", "## 元素 HTML", "", "```html", str(t.get("outer_html"))[:8000], "```"]
    if body.text_snapshot:
        lines += ["", "## 页面文本快照(所见文字)", "", body.text_snapshot[:60000]]
    if body.dom_snapshot:
        lines += [
            "",
            "## 页面 DOM 结构(完整元素树)",
            "",
            "> 宿主外壳 + 递归展开的同源内嵌页面。每个元素的标签 / 属性 / data-testid / "
            "内联样式都在此 —— 据此可定位「每个网页元素」的身份、状态与位置。",
            "",
            _safe_fence(body.dom_snapshot[:200000], "html"),
        ]
    if shot_abs:
        lines += ["", "## 截图", "", f"![页面截图]({shot_abs})", "", f"- 文件: `{shot_abs}`"]
    return "\n".join(lines)


@captures_router.post("")
async def save_capture(body: CaptureBody) -> dict[str, Any]:
    """保存捕获到文件(不创建审阅材料、不进审阅队列)。

    enqueue=True(提交)→ pending/(进 dispatch 批次, 计入待处理数);
    enqueue=False(复制)→ clips/(只为给剪贴板一个文件链接, 不计入待处理)。
    """
    ts = time.strftime("%Y%m%dT%H%M%S")
    # 三态校验
    if body.verdict and body.verdict not in VERDICTS:
        raise HTTPException(400, f"invalid verdict: {body.verdict} (允许 {sorted(VERDICTS)})")
    # 目标解析(第一层信标): caller 没预解析 omni_uri, 但给了几何提示 → 本端几何相交解析。
    resolved = None
    if not body.omni_uri and body.screen_rect and body.content_origin:
        try:
            resolved = get_surface_registry().resolve(
                screen_rect=body.screen_rect, content_origin=body.content_origin,
                dpr_hint=body.dpr, url_hint=body.url, title_hint=body.title or "",
            )
            if resolved:
                resolved = _enrich_context(resolved)  # 补 页面 + 文件路径 + 人话 + 页内材料
                if resolved.get("omni_uri"):
                    body.omni_uri = resolved["omni_uri"]
        except Exception:
            resolved = None
    name = _capture_filename(body, ts)
    d = _pending_dir() if body.enqueue else _clips_dir()
    path = d / name
    # 真实截图(若发来): 先落盘成图片, 既挂到札记的 captures(集中管理面渲染缩略图),
    # 也写进本条 md 的「截图」节(给 AI 一个可直接 Read 的绝对路径看画面)。
    shot_rel = _save_image_data_url(body.image_data_url, ts) if body.image_data_url else None
    shot_abs = str((_clips_dir() / shot_rel).resolve()) if shot_rel else None
    try:
        path.write_text(_render_md(body, ts, shot_abs), encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"capture write failed: {e}") from e
    pending = sorted(_pending_dir().glob("*.md"))
    saved_path = str(path.resolve())
    # 带评论或三态的捕获 → 统一并进札记, 挂到解析出的真实实体(material/plan/note/project/task),
    # 解析不出则回退 page_element。纯快照(无评论无三态)只落盘, 不建 note。
    note_id = None
    point_targets = None
    has_annotation = bool((body.comment or "").strip()) or bool(body.verdict)
    if body.capture_kind != "debug_start" and body.annotations:
        # 点 1: 逐条文字标注各自挂到它旁边那条材料(精确), 而非整张共用一条。
        try:
            from ..authored.store import get_authored_store
            point_targets = _create_annotation_notes(
                body, shot_rel=shot_rel, src_capture=saved_path, store=get_authored_store())
            note_id = next((p["note_id"] for p in point_targets if p.get("note_id")), None)
        except Exception:
            point_targets = None
    elif body.capture_kind != "debug_start" and has_annotation:
        try:
            from ..authored.store import get_authored_store
            note_id = _create_capture_note(
                body, shot_rel=shot_rel, src_capture=saved_path, store=get_authored_store())
        except Exception:
            note_id = None
    payload = body.model_dump()
    payload.update({"path": saved_path, "saved_path": saved_path, "created_at": ts,
                    "shot": shot_rel, "omni_uri": body.omni_uri, "resolved": resolved})
    publish_material_event(CAPTURE.id, payload, source="boss_sight.captures")
    return {"saved_path": saved_path, "pending_count": len(pending), "note_id": note_id,
            "shot": shot_rel, "omni_uri": body.omni_uri, "resolved": resolved,
            "path": (resolved or {}).get("path"),
            "description": (resolved or {}).get("description"),
            "page": (resolved or {}).get("page"),
            "contained": (resolved or {}).get("contained"),
            "point_targets": point_targets}


def _target_from_omni_uri(omni_uri: str, body: "CaptureBody") -> dict[str, Any]:
    """omni://kind/id → 札记 target(挂到真实实体)。解析失败回退 page_element。"""
    from ..entity_registry import parse_entity_uri
    loc = body.target or {}
    try:
        kind, entity_id = parse_entity_uri(omni_uri)
    except Exception:
        return _page_element_target(body)
    target_kind = _OMNI_KIND_TO_TARGET.get(kind, kind)
    tgt: dict[str, Any] = {
        "kind": target_kind, "id": entity_id, "uri": omni_uri,
        "url": body.url, "route": body.route,
        "title": body.title or loc.get("label"), "locator": loc or None,
    }
    # 让 compute_project_id 能归属: plan/material 各自带上对应 id 字段。
    if target_kind == "plan":
        tgt["plan_id"] = entity_id
    elif target_kind == "material":
        tgt["material_id"] = entity_id
    elif target_kind == "llm_session":
        tgt["session_id"] = entity_id
    return tgt


def _page_element_target(body: "CaptureBody") -> dict[str, Any]:
    loc = body.target or {}
    return {
        "kind": "page_element",
        "id": (loc.get("selector") or body.route or body.url or "page")[:200],
        "url": body.url, "route": body.route, "selector": loc.get("selector"),
        "title": body.title or loc.get("label"), "locator": loc or None,
    }


def _create_capture_note(body: "CaptureBody", *, shot_rel: str | None,
                         src_capture: str, store: Any) -> str:
    target = _target_from_omni_uri(body.omni_uri, body) if body.omni_uri else _page_element_target(body)
    content = (body.comment or "").strip()
    if not content and body.verdict:
        content = f"[三态] {_VERDICT_LABEL.get(body.verdict, body.verdict)}"
    n = store.create(
        content=content,
        target=target,
        uses=["comment"],
        captures=[shot_rel] if shot_rel else None,
        extra={"src_capture": src_capture, "verdict": body.verdict,
               "omni_uri": body.omni_uri, "modality": body.modality},
    )
    _notify_material_comment(target)
    return n.id


def _create_annotation_notes(body: "CaptureBody", *, shot_rel: str | None,
                             src_capture: str, store: Any) -> list[dict[str, Any]]:
    """点 1: 逐条文字标注各自挂到它旁边那条材料。每条 {x,y,text} → 解析点所在实体 → 建 comment note。
    返回每条的 {text,omni_uri,note_id,description,path}, 供 poof 写进结构化 MD。"""
    anns = [a for a in (body.annotations or []) if isinstance(a, dict) and str(a.get("text") or "").strip()]
    if not anns:
        return []
    pts: list[dict[str, Any] | None]
    if body.content_origin:
        pts = get_surface_registry().resolve_points(
            points=[[float(a.get("x", 0)), float(a.get("y", 0))] for a in anns],
            content_origin=body.content_origin, dpr_hint=body.dpr,
            url_hint=body.url, title_hint=body.title or "")
    else:
        pts = [None] * len(anns)
    out: list[dict[str, Any]] = []
    for a, pt in zip(anns, pts):
        text = str(a.get("text") or "").strip()
        omni_uri = pt.get("omni_uri") if pt else None
        target = _target_from_omni_uri(omni_uri, body) if omni_uri else _page_element_target(body)
        n = store.create(
            content=text, target=target, uses=["comment"],
            captures=[shot_rel] if shot_rel else None,
            extra={"src_capture": src_capture, "omni_uri": omni_uri, "modality": body.modality})
        _notify_material_comment(target)
        enr = _enrich_target({"omni_uri": omni_uri, "kind": pt.get("kind") if pt else None,
                              "title": pt.get("title") if pt else None}) if omni_uri else None
        out.append({
            "text": text, "omni_uri": omni_uri, "note_id": n.id,
            "description": enr.get("description") if enr else None,
            "path": enr.get("path") if enr else None,
        })
    return out


def _notify_material_comment(target: dict[str, Any]) -> None:
    """点 2: 捕获评论挂到某条 review_material 时, 发审阅台 WS comment_added 事件, 让审阅台实时刷出来
    (不必等下次读材料)。best-effort: 不是 material 目标 / 审阅台没起都安静跳过。"""
    if not isinstance(target, dict) or target.get("kind") != "material":
        return
    mid = target.get("material_id") or target.get("id")
    if not mid:
        return
    try:
        from ..reviewstage.routes import notify_comment_added
        notify_comment_added(str(mid))
    except Exception:
        pass


@captures_router.get("")
async def list_captures() -> dict[str, Any]:
    d = _pending_dir()
    items = [{"name": p.name, "path": str(p.resolve())} for p in sorted(d.glob("*.md"))]
    return {"pending_count": len(items), "items": items}


@captures_router.get("/file")
async def get_capture_file(path: str):
    """读 captures 根下的截图(集中管理面渲染 note.captures 缩略图)。path 相对 captures 根, 防越界。"""
    from fastapi.responses import FileResponse
    root = _captures_root().resolve()
    p = (root / path).resolve()
    if root != p and root not in p.parents:
        raise HTTPException(400, "path 越界")
    if not p.is_file():
        raise HTTPException(404, "file not found")
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}.get(p.suffix.lower())
    return FileResponse(str(p), media_type=media)


# ── 统一捕获 · 目标解析(第一层 omni 表面信标) ───────────────────────────

class SurfaceBody(BaseModel):
    """omni 网页信标周期上报: 自己 + 当前可见实体的视口矩形(CSS px)。"""
    surface_id: str = Field(..., max_length=128)
    url: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=400)
    dpr: float = 1.0
    viewport: dict[str, float] = Field(default_factory=dict)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    # 通用 DOM 元素解析: 光标下最新元素(不靠埋点), {tag,id,cls,text,selector,x,y,w,h,omni_*}。
    hover: dict[str, Any] | None = None
    # 视口内内容原子(叶子有文本/媒体交互/埋点), 供"选区里有哪些元素"几何切分。
    content_els: list[dict[str, Any]] = Field(default_factory=list)


class ResolveBody(BaseModel):
    """poof: 屏幕矩形(物理像素) + 内容区原点(物理像素) + 主机提示 → omni 实体。"""
    screen_rect: list[float]
    content_origin: list[float] | None = None
    dpr: float | None = None
    url: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=400)
    # 可选: 逐条标注点(屏幕物理像素 [x,y]), 各自映射到落在的实体。
    points: list[list[float]] | None = None


@captures_router.post("/surface")
async def upsert_surface(body: SurfaceBody) -> dict[str, Any]:
    """信标上报。entities 每项: {omni_uri,kind,title,x,y,w,h}(x/y/w/h 为视口 CSS px)。"""
    ents = [e for e in (body.entities or []) if isinstance(e, dict) and e.get("omni_uri")][:2000]
    hover = body.hover if isinstance(body.hover, dict) and body.hover.get("tag") else None
    cels = [e for e in (body.content_els or []) if isinstance(e, dict) and e.get("tag")][:600]
    get_surface_registry().upsert(
        surface_id=body.surface_id, url=body.url, title=body.title,
        dpr=body.dpr, viewport=body.viewport or {}, entities=ents, hover=hover, content_els=cels,
    )
    return {"ok": True, "surface_id": body.surface_id, "entity_count": len(ents), "content_count": len(cels)}


@captures_router.get("/surfaces")
async def list_surfaces() -> dict[str, Any]:
    """当前活着的信标(调试用)。"""
    return {"surfaces": get_surface_registry().list_live()}


@captures_router.post("/resolve")
async def resolve_target(body: ResolveBody) -> dict[str, Any]:
    """屏幕矩形 → 实体。返回 文件路径 + 完整描述(给 AI 看懂的人话, 不暴露 omni://)。
    解析不出(无信标/无原点/没压到实体)返回 omni_uri=None, 交给像素兜底。"""
    hit = get_surface_registry().resolve(
        screen_rect=body.screen_rect, content_origin=body.content_origin,
        dpr_hint=body.dpr, url_hint=body.url, title_hint=body.title,
    )
    if not hit:
        return {"omni_uri": None}
    ctx = _enrich_context(hit)
    if body.points:
        pts = get_surface_registry().resolve_points(
            points=body.points, content_origin=body.content_origin,
            dpr_hint=body.dpr, url_hint=body.url, title_hint=body.title)
        ctx["point_targets"] = [
            (_enrich_target(p) if p and p.get("omni_uri") else None) for p in pts
        ]
    return ctx


def _find_canonical_controller(mgr: Any) -> Any | None:
    """唯一(最新非归档)总控 —— 与 ControllerWaker._find_active_controllers / 前端 ControllerChat 同规则。"""
    sessions = getattr(mgr, "_sessions", {})
    live = [
        s for s in sessions.values()
        if getattr(s, "provider", "") == "controller"
        and getattr(s, "ended_at", None) is None
        and not getattr(s, "archived", False)
    ]
    if not live:
        return None
    live.sort(key=lambda s: getattr(s, "started_at", 0) or 0, reverse=True)
    return live[0]


@captures_router.post("/dispatch")
async def dispatch_captures() -> dict[str, Any]:
    """把 pending 的捕获整体交给唯一总控读取处理。

    1) 把 pending/* 移到 captures/batch_<ts>/(immutable 批次, 下一批回到干净 pending)。
    2) 注入一条消息给唯一总控, 让它逐个 Read 这批 .md 文件并处理。
    """
    d = _pending_dir()
    pending = sorted(d.glob("*.md"))
    if not pending:
        return {"dispatched": False, "reason": "没有待处理的捕获", "count": 0}

    try:
        from omnicompany.dashboard.ccdaemon.chat import get_chat_manager
        mgr = get_chat_manager()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"chat manager unavailable: {e}") from e

    controller = _find_canonical_controller(mgr)
    if controller is None:
        return {
            "dispatched": False,
            "reason": "没有活跃总控会话, 请先打开总控对话再试",
            "count": len(pending),
        }

    batch_ts = time.strftime("%Y%m%dT%H%M%S")
    batch_dir = _captures_root() / f"batch_{batch_ts}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for p in pending:
        dest = batch_dir / p.name
        try:
            p.rename(dest)
            moved.append(dest)
        except OSError:
            continue

    file_list = "\n".join(f"  - {m.resolve()}" for m in moved)
    msg = (
        "[用户捕获批次, not_user: true]\n"
        f"用户在驾驶舱提交了 {len(moved)} 条 UI 捕获(圈选/快照/调试交接, 含用户批注), 已存到目录:\n"
        f"{batch_dir.resolve()}\n\n"
        "请逐个用 Read 读取这些 .md 文件(每条含: 圈选目标的选择器·文本·HTML 或页面快照 + 用户批注), "
        "理解用户想指出 / 想改什么, 再决定动作(派 subagent 改界面 / 记问题到 plan / 直接回应); "
        "处理完用自然语言把这批的结论汇总给用户。\n"
        f"文件:\n{file_list}"
    )
    try:
        await mgr.submit_user_prompt(controller, msg, record_history=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"inject to controller failed: {e}") from e

    return {
        "dispatched": True,
        "count": len(moved),
        "batch_dir": str(batch_dir.resolve()),
        "controller_session": getattr(controller, "id", None),
    }


__all__ = ["captures_router"]

# [OMNI] origin=kimi-code ts=2026-08-12 type=cli
# [OMNI] material_id="material:cli.commands.canvas.py"
"""omni canvas — 会话札记材料画布(React Flow 换核后的新画布)的外部操控入口。

读(list/show)直查权威 store(经 8210 invoke 桥 notes_canvas_*/notes_text_*); 卡片新增、覆写和
几何更新走实体级原子 mutation，画布无需打开；运行时视野、移除、聚焦和高亮等深度操控经
cb_push 排进文件队列, 由开着该画布的 notes-web 页认领执行。
旧 BlockSuite 笔记的对应设施是 `omni notes`(nb 桥), 两套别混。
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import click

from .._access import any_caller

_DASHBOARD_PORT = int(os.environ.get("OMNI_DASHBOARD_PORT", "8210"))
_ADAPTERS = ["auto", "text", "image", "pdf", "audio", "video", "web", "link", "file", "legacy-note"]
_CANVAS_TARGET = "dashboard"
_CANVAS_PROJECT: str | None = None


def _invoke_url() -> str:
    """Return the authoritative bridge for the selected Canvas data domain."""
    if _CANVAS_TARGET == "workshop":
        base = os.environ.get("OMNI_CANVAS_WORKSHOP_URL", os.environ.get("WORKSHOP_URL", "http://127.0.0.1:5460"))
        return f"{base.rstrip('/')}/lofa/overlay/invoke"
    return f"http://127.0.0.1:{_DASHBOARD_PORT}/lofa/overlay/invoke"


def _open_base_url() -> str:
    return os.environ.get(
        "OMNI_CANVAS_OPEN_URL",
        f"http://127.0.0.1:{_DASHBOARD_PORT}/bside/canvas",
    ).rstrip("?")


def _canvas_key(session_id: str) -> str:
    """与 notes-web materialCanvasModel.canvasStorageId 同构。"""
    encoded = base64.urlsafe_b64encode(f"external:{session_id}".encode("utf-8")).decode("ascii").rstrip("=")
    return f"session-{encoded[:180]}"


def _slugify_canvas_name(raw: str) -> str:
    """canvas:<名> 或路径式名(walker/paper-explore) → 裸 slug 存储键。

    与 core canvasStorageId("canvas:<slug>") 同构: 命名画布文件名就是 slug 本身,
    人可读、可直接 grep, 不经过 base64/hash。
    """
    name = raw[len("canvas:"):] if raw.startswith("canvas:") else raw
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    if not slug:
        raise click.ClickException(f"画布名不能为空: {raw}")
    return slug[:120]


def _resolve_canvas(canvas: str | None, session: str | None) -> str:
    if canvas:
        # 命名体系: canvas:<名> 或带 / 的路径式名 → 裸 slug; 裸 slug/原生 key 原样透传。
        if canvas.startswith("canvas:") or ("/" in canvas and not canvas.startswith("session-")):
            return _slugify_canvas_name(canvas)
        return canvas
    if not session:
        try:
            from omnicompany.packages.services._core.identity import current_session_meta
            session = (current_session_meta() or {}).get("pty_id") or ""
        except Exception:
            session = ""
    if not session:
        raise click.ClickException("缺画布目标: 给 --session <pty会话id> 或 --canvas <画布key>(当前会话身份也没解析出来)")
    return _canvas_key(session)


def _resolve_card_target(canvas: str | None, session: str | None, card: str) -> tuple[str, str]:
    """--card 支持卡片复制按钮产出的 <会话id>#<卡片id> 或 <画布key>#<卡片id> 引用串。"""
    if "#" in card:
        left, _, card = card.partition("#")
        if left and not canvas and not session:
            if left.startswith("session-"):
                canvas = left
            else:
                session = left
    return _resolve_canvas(canvas, session), card


def _invoke(cmd: str, args: dict, timeout: float = 8.0):
    url = _invoke_url()
    req = urllib.request.Request(
        url,
        data=json.dumps({"cmd": cmd, "args": args}, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    # Workshop(5460)的桥在登录门后面，而且作用域按项目切：`x-workshop-project` 选项目，
    # 会话 cookie 认人。两样都不带就只能拿到 401「请先登录」或者悄悄写进默认项目。
    if _CANVAS_PROJECT:
        req.add_header("x-workshop-project", _CANVAS_PROJECT)
    cookie = os.environ.get("OMNI_CANVAS_COOKIE", "")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
        except Exception:
            detail = {}
        message = detail.get("error") or f"桥返回 HTTP {error.code}: {cmd}"
        if error.code == 401:
            message = (
                f"{message}（{url} 在登录门后面）。把一份工作区会话 cookie 放进 "
                "OMNI_CANVAS_COOKIE（形如 better-auth.session_token=…），"
                "并用 --project 选项目（如 --project workshop）。"
            )
        raise click.ClickException(message) from None
    except Exception as error:
        raise click.ClickException(f"Canvas {_CANVAS_TARGET} bridge 不可达 ({url}): {error}") from None
    if not payload.get("ok"):
        raise click.ClickException(str(payload.get("error") or f"{cmd} 失败"))
    return payload.get("result")


def _push(canvas: str, op: str, args: dict, timeout: float):
    return _invoke("cb_push", {"op": op, "canvas": canvas, "args": args, "timeout": timeout}, timeout=timeout + 15.0)


def _emit(data) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _reject_mojibake_runs(raw: str, label: str) -> None:
    if "???" not in raw:
        return
    raise click.ClickException(
        f"{label} 含连续 ???，疑似有损编码，拒绝写入。"
        "Windows PowerShell 5.1 管道默认使用 ASCII；请先设置 "
        "$OutputEncoding = New-Object Text.UTF8Encoding($false)，或改用 UTF-8 JSON 文件配合 --file。"
    )


def _materialize_inline_text_cards(cards: list[dict]) -> tuple[list[dict], int]:
    """Split inline card bodies into the shared text store before Canvas mutation.

    Canvas documents intentionally persist text bodies out of line. Keeping an
    evidence link as ``source`` while only supplying ``text`` looks correct for
    one render, then the next browser save drops that inline body. The CLI makes
    this durable and retains the original evidence locator in Material metadata.
    """
    prepared: list[dict] = []
    hydrated = 0
    for raw_card in cards:
        if not isinstance(raw_card, dict):
            prepared.append(raw_card)
            continue
        card = dict(raw_card)
        body = card.get("text")
        if card.get("kind") != "text" or not isinstance(body, str):
            prepared.append(card)
            continue
        card_id = str(card.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,220}", card_id):
            raise click.ClickException(f"带正文的卡片 id 不合法: {card_id!r}")
        text_material = _invoke("notes_text_put", {"id": card_id, "content": body})
        original_source = card.get("source")
        if isinstance(original_source, dict) and original_source.get("kind") != "file":
            material = dict(card.get("material") or {})
            metadata = dict(material.get("metadata") or {})
            metadata.setdefault("evidence_source", original_source)
            material["metadata"] = metadata
            card["material"] = material
        card["source"] = {
            "kind": "file",
            "path": text_material["path"],
            "token": text_material["open_token"],
            "mime": text_material.get("mime"),
            "editableId": card_id,
        }
        prepared.append(card)
        hydrated += 1
    return prepared, hydrated


def _canvas_document(key: str) -> dict:
    """读 builtin 域画布文档（8210 桥 notes_canvas_get）。"""
    try:
        raw = _invoke("notes_canvas_get", {"id": key})
    except click.ClickException:
        raw = None
    if isinstance(raw, str) and raw.strip():
        try:
            document = json.loads(raw)
            if isinstance(document, dict):
                return document
        except json.JSONDecodeError:
            pass
    return {
        "version": 1,
        "sessionId": _session_id_for_canvas_key(key),
        "revision": 0,
        "cards": [],
        "relations": [],
    }


def _write_identity(*, purpose: str | None = None, batch: str | None = None,
                    human: dict | None = None) -> dict | None:
    """当前 CLI 进程的写入身份（被动采集：hook 台账 + 环境变量 + 机器身份文件）。

    采集失败不阻断写入——存储会记旧的 actor 字符串，不伪造身份。
    human 给了就是人经 CLI 代写（口述意见录入等）。"""
    try:
        from omnicompany.packages.services._core.identity.write_identity import (
            current_write_identity, human_write_identity,
        )
        if human:
            return human_write_identity(
                str(human.get("name") or ""), input_method=str(human.get("input") or "cli"),
                device=human.get("device"), purpose=purpose,
            )
        return current_write_identity(purpose=purpose, batch=batch, captured_by="cli")
    except Exception:  # noqa: BLE001 - 身份采集永不阻断写入
        return None


def _mutate(key: str, document: dict, patch: dict, *, actor: str = "agent:omni-canvas",
            purpose: str | None = None, batch: str | None = None, human: dict | None = None) -> dict:
    """builtin 域 mutation（实体级 CAS + 幂等键，语义与 core 契约一致）。每次写入带写入身份。"""
    args = {
        "id": key,
        "session_id": document.get("sessionId") or _session_id_for_canvas_key(key),
        "actor": actor,
        "base_revision": int(document.get("revision") or 0),
        "mutation_id": f"cli-{uuid.uuid4().hex}",
        "patch": patch,
    }
    identity = _write_identity(purpose=purpose, batch=batch, human=human)
    if identity:
        args["identity"] = identity
        args["actor_ref"] = _actor_ref_of(identity, actor)
    result = _invoke("notes_canvas_mutate", args)
    return result if isinstance(result, dict) else {"revision": None}


def _actor_ref_of(identity: dict, fallback: str) -> dict:
    """由身份派生兼容 actor_ref（与 TS writeIdentityActorRef 同口径）。"""
    principal = str(identity.get("principal") or "agent")
    if principal == "human":
        who = str((identity.get("human") or {}).get("name") or "human")
    else:
        who = str((identity.get("runtime") or {}).get("provider") or principal)
    machine = str((identity.get("machine") or {}).get("id") or "")
    native = str((identity.get("session") or {}).get("native_id") or "")
    ref = f"{who}@{machine}" + (f"/{native}" if native else "")
    return {"kind": principal, "ref": (ref or fallback)[:220]}


def _text_card_size(text: str) -> tuple[float, float]:
    """Give notes and references useful defaults instead of one inherited giant size."""
    lines = text.splitlines() or [""]
    length = len(text)
    if length <= 360:
        width = 440.0
        chars_per_line = 32
    elif length <= 1100:
        width = 600.0
        chars_per_line = 46
    else:
        width = 680.0
        chars_per_line = 54
    visual_lines = sum(max(1, math.ceil(len(line) / chars_per_line)) for line in lines)
    height = min(760.0, max(220.0, 112.0 + visual_lines * 26.0))
    return width, height


def _overlaps(
    x: float,
    y: float,
    width: float,
    height: float,
    card: dict,
    *,
    gap: float = 48.0,
) -> bool:
    other_x = float(card.get("x") or 0)
    other_y = float(card.get("y") or 0)
    other_width = float(card.get("width") or 440)
    other_height = float(card.get("height") or 220)
    return not (
        x + width + gap <= other_x
        or other_x + other_width + gap <= x
        or y + height + gap <= other_y
        or other_y + other_height + gap <= y
    )


def _next_card_position(document: dict, anchor: dict, width: float, height: float) -> tuple[float, float]:
    """Place sibling replies in one non-overlapping lane to the right of their parent."""
    x = float(anchor.get("x") or 0) + float(anchor.get("width") or 440) + 80.0
    y = float(anchor.get("y") or 0)
    cards = [card for card in document.get("cards") or [] if isinstance(card, dict)]
    while True:
        collisions = [card for card in cards if _overlaps(x, y, width, height, card)]
        if not collisions:
            return x, y
        y = max(float(card.get("y") or 0) + float(card.get("height") or 220) + 48.0 for card in collisions)


@click.group("canvas")
@click.option(
    "--target",
    type=click.Choice(["dashboard", "workshop"], case_sensitive=False),
    default=lambda: os.environ.get("OMNI_CANVAS_TARGET", "dashboard"),
    show_default="dashboard",
    help="Canvas 数据域；所有子命令共用同一个入口与契约。",
)
@click.option(
    "--project",
    default=lambda: os.environ.get("OMNI_CANVAS_PROJECT") or None,
    help="workshop 域里的工作区项目(如 workshop)；不给就用服务端默认项目(spark)。",
)
def cmd_canvas(target: str, project: str | None) -> None:
    """操控会话札记材料画布(内容和几何可离线原子写；视野、高亮等需画布打开)。"""
    global _CANVAS_TARGET, _CANVAS_PROJECT
    _CANVAS_TARGET = target.lower()
    _CANVAS_PROJECT = (project or "").strip() or None


def _canvas_recent_fallback(limit: int, keys: list[str] | None = None) -> dict:
    """Project recent canvases through the older bridge's modern-store reads.

    A running dashboard daemon can lag the source checkout.  Its existing
    ``keys``, ``head`` and ``get`` commands still address the modern store, so
    use them as a read-only compatibility path until the daemon is naturally
    replaced.  This never consults the retired notes files.
    """
    if keys is None:
        candidate = _invoke("notes_canvas_keys", {})
        keys = [str(key) for key in candidate] if isinstance(candidate, list) else []
    rows: list[tuple[str, str]] = []
    for key in keys:
        try:
            head = _invoke("notes_canvas_head", {"id": key})
        except click.ClickException:
            head = None
        if isinstance(head, dict):
            rows.append((key, str(head.get("updatedAt") or "")))
    rows.sort(key=lambda item: item[1], reverse=True)
    items: list[dict] = []
    from omnicompany.packages.services.canvas.canvas_observation import canvas_projection

    for key, _ in rows[:limit]:
        try:
            raw = _invoke("notes_canvas_get", {"id": key})
        except click.ClickException:
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict):
            items.append(canvas_projection(key, document))
    return {"source": "modern-canvas-store", "items": items, "count": len(items), "limit": limit}


@cmd_canvas.command("list")
@any_caller
def cmd_canvas_list() -> None:
    """列全部现代 Canvas，并附最近投影与人可读名。"""
    recent: dict = {}
    try:
        candidate = _invoke("notes_canvas_recent", {"limit": 200})
        if isinstance(candidate, dict):
            recent = candidate
            keys = [
                str(item.get("document_id"))
                for item in candidate.get("items") or []
                if isinstance(item, dict) and item.get("document_id")
            ]
        else:
            keys = candidate if isinstance(candidate, list) else []
    except click.ClickException:
        # Keep list useful against an older running dashboard bridge while the
        # source update is being rolled out; the authoritative fallback remains
        # the modern store's key query, never legacy notes.
        keys = _invoke("notes_canvas_keys", {})
        keys = keys if isinstance(keys, list) else []
        recent = _canvas_recent_fallback(200, [str(key) for key in keys])
    readable = {}
    for key in keys:
        if key.startswith("session-"):
            readable[key] = _session_from_canvas_key(key)
        else:
            readable[key] = f"canvas:{key}"
    _emit({
        "source": "modern-canvas-store",
        "canvases": keys,
        "readable": readable,
        "recent": recent.get("items", []) if isinstance(recent, dict) else [],
    })


@cmd_canvas.command("recent")
@click.option("--limit", type=click.IntRange(min=1, max=200), default=20, show_default=True)
@any_caller
def cmd_canvas_recent(limit: int) -> None:
    """只读查询现代 Canvas 最近更新投影。"""
    try:
        result = _invoke("notes_canvas_recent", {"limit": limit})
    except click.ClickException as error:
        if "unknown cmd" not in str(error).lower():
            raise
        result = _canvas_recent_fallback(limit)
    if not isinstance(result, dict):
        raise click.ClickException("现代 canvas recent 返回了无效投影")
    _emit(result)


@cmd_canvas.command("open")
@click.option("--canvas", default=None, help="画布 key 或命名画布; 缺省=当前会话画布(零输入)")
@click.option("--session", "session_id", default=None, help="pty 会话 id(缺省=当前会话)")
@click.option("--focus", default=None, help="加载后聚焦的卡片 id")
@click.option("--no-browser", is_flag=True, help="只打印 URL，不拉起浏览器")
@any_caller
def cmd_canvas_open(canvas: str | None, session_id: str | None, focus: str | None, no_browser: bool) -> None:
    """零输入打开画布: 不给任何 id 时直接打开当前会话画布。"""
    key = _resolve_canvas(canvas, session_id)
    if _CANVAS_TARGET == "dashboard" and not key.startswith("session-"):
        raise click.ClickException(
            f"URL 直达目前只支持会话画布(拿到的是 {key}); 命名画布内容用 omni canvas show --canvas {key}"
        )
    if _CANVAS_TARGET == "workshop":
        url = f"{_open_base_url()}?canvas={urllib.parse.quote(key)}"
    else:
        url = f"{_open_base_url()}?session={urllib.parse.quote(_session_from_canvas_key(key))}"
    if focus:
        url += f"&card={urllib.parse.quote(focus)}"
    if not no_browser:
        import webbrowser
        webbrowser.open(url)
    _emit({"ok": True, "canvas": key, "url": url})


@cmd_canvas.command("show")
@click.option("--canvas", default=None, help="画布 key(session-...)")
@click.option("--session", "session_id", default=None, help="pty 会话 id(缺省=当前会话)")
@click.option("--with-text", is_flag=True, help="逐卡拉文字材料正文(notes_text_get)")
@any_caller
def cmd_canvas_show(canvas: str | None, session_id: str | None, with_text: bool) -> None:
    """读画布全量: 卡片所有字段(id/kind/title/adapter/x/y/width/height/source) + 关系。"""
    key = _resolve_canvas(canvas, session_id)
    raw = _invoke("notes_canvas_get", {"id": key})
    document = None
    if isinstance(raw, str) and raw.strip():
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            document = None
    if not isinstance(document, dict):
        raise click.ClickException(f"画布不存在: {key}")
    from omnicompany.packages.services.canvas.canvas_observation import canvas_projection

    _emit({
        "source": "modern-canvas-store",
        "canvas": key,
        "document": document,
        "summary": canvas_projection(key, document),
    })


@cmd_canvas.command("state")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--timeout", default=15.0)
@any_caller
def cmd_canvas_state(canvas: str | None, session_id: str | None, timeout: float) -> None:
    """读活画布运行时状态: viewport / 选中卡 / 卡数(需画布开着)。"""
    _emit(_push(_resolve_canvas(canvas, session_id), "state", {}, timeout))


@cmd_canvas.command("update")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True, help="卡片 id")
@click.option("--x", type=float, default=None)
@click.option("--y", type=float, default=None)
@click.option("--width", type=float, default=None)
@click.option("--height", type=float, default=None)
@click.option("--title", default=None)
@click.option("--adapter", type=click.Choice(_ADAPTERS), default=None)
@click.option("--timeout", default=15.0)
@any_caller
def cmd_canvas_update(canvas, session_id, card, x, y, width, height, title, adapter, timeout) -> None:
    """定向原子修改卡片几何/展示属性；画布无需打开。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    document = _canvas_document(key)
    target = next((item for item in document.get("cards") or [] if item.get("id") == card), None)
    if not target:
        raise click.ClickException(f"没找到卡片: {card}")
    updated = dict(target)
    for name, value in (("x", x), ("y", y), ("width", width), ("height", height),
                        ("title", title), ("adapter", adapter)):
        if value is not None:
            updated[name] = value
    if updated == target:
        raise click.ClickException("没给要改的字段(--x/--y/--width/--height/--title/--adapter 至少一个)")
    result = _mutate(key, document, {"upsert_cards": [updated]})
    _emit({
        "ok": True,
        "result": {"updated": card, "mode": "atomic-mutation", "revision": result.get("revision")},
    })


@cmd_canvas.command("remove")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True)
@click.option("--timeout", default=15.0)
@any_caller
def cmd_canvas_remove(canvas, session_id, card, timeout) -> None:
    """从活画布移除一张卡(需画布开着)。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    _emit(_push(key, "remove", {"card": card}, timeout))


@cmd_canvas.command("viewport")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--x", type=float, default=None, help="视野平移 x(缺省保持)")
@click.option("--y", type=float, default=None, help="视野平移 y(缺省保持)")
@click.option("--zoom", type=float, default=None, help="缩放(缺省保持)")
@click.option("--timeout", default=15.0)
@any_caller
def cmd_canvas_viewport(canvas, session_id, x, y, zoom, timeout) -> None:
    """设活画布视野(需画布开着)。"""
    args = {}
    for name, value in (("x", x), ("y", y), ("zoom", zoom)):
        if value is not None:
            args[name] = value
    if not args:
        raise click.ClickException("没给要设的视野字段(--x/--y/--zoom 至少一个)")
    _emit(_push(_resolve_canvas(canvas, session_id), "viewport", args, timeout))


@cmd_canvas.command("focus")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True)
@click.option("--zoom", type=float, default=None, help="居中时的缩放(缺省保持当前)")
@click.option("--timeout", default=15.0)
@any_caller
def cmd_canvas_focus(canvas, session_id, card, zoom, timeout) -> None:
    """把活画布视野居中到指定卡(需画布开着)。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    args = {"card": card}
    if zoom is not None:
        args["zoom"] = zoom
    _emit(_push(key, "focus", args, timeout))


@cmd_canvas.command("say")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--text", required=True, help="文字卡内容(标题自动取首行)")
@click.option("--card-id", default=None, help="语义卡 id(如 readback、mockup-idle-1); 缺省自动生成")
@click.option("--after", default=None, help="接在指定卡片后面，并建立关系；支持 <会话id>#<卡片id>")
@click.option("--relation-label", default="回答 · Agent 提议", help="--after 关系标签")
@any_caller
def cmd_canvas_say(canvas, session_id, text, card_id, after, relation_label) -> None:
    """原子写入文字卡；画布无需打开，打开的页面会按 revision 自动接收。"""
    key = _resolve_canvas(canvas, session_id)
    if after and "#" in after:
        addressed_key, after = _resolve_card_target(canvas, session_id, after)
        if addressed_key != key:
            raise click.ClickException("--after 指向了另一张画布")
    document = _canvas_document(key)
    anchor = next((card for card in document.get("cards") or [] if card.get("id") == after), None) if after else None
    if after and not anchor:
        raise click.ClickException(f"没找到上游卡片: {after}")
    if card_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", card_id):
            raise click.ClickException("--card-id 只允许字母数字 . _ - ，长度 1-120(与存储键规则一致)")
        if any(card.get("id") == card_id for card in document.get("cards") or []):
            raise click.ClickException(f"卡片 id 已存在: {card_id}(要改正文用 omni canvas rewrite)")
    else:
        card_id = f"text-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:10]}"
    material = _invoke("notes_text_put", {"id": card_id, "content": text})
    title = next((line.strip() for line in text.splitlines() if line.strip()), "随手记")[:120]
    width, height = _text_card_size(text)
    if anchor:
        x, y = _next_card_position(document, anchor, width, height)
    else:
        cards = document.get("cards") or []
        x = max((float(card.get("x") or 0) + float(card.get("width") or 440) for card in cards), default=0) + 80
        y = min((float(card.get("y") or 0) for card in cards), default=72)
    card = {
        "id": card_id,
        "kind": "text",
        "title": title,
        "source": {
            "kind": "file",
            "path": material["path"],
            "token": material["open_token"],
            "mime": material.get("mime"),
            "editableId": card_id,
        },
        "adapter": "text",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }
    patch = {"upsert_cards": [card]}
    relation_id = None
    if anchor:
        relation_id = f"relation-{uuid.uuid4().hex}"
        patch["upsert_relations"] = [{
            "id": relation_id,
            "source": after,
            "target": card_id,
            "label": relation_label,
        }]
    result = _mutate(key, document, patch)
    _emit({
        "ok": True,
        "canvas": key,
        "created": card_id,
        "ref": f"{_session_from_canvas_key(key)}#{card_id}",
        "relation": relation_id,
        "revision": result.get("revision"),
        "merged": result.get("merged"),
    })


@cmd_canvas.command("add-cards")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--file", "file_path", default=None, help="JSON 文件路径; 缺省读 stdin")
@click.option("--actor", default="agent:omni-canvas")
@any_caller
def cmd_canvas_add_cards(canvas, session_id, file_path, actor) -> None:
    """批量增删改卡/边: {"cards":[...], "relations":[...], "remove_card_ids":[...], "remove_relation_ids":[...], "title":?}。

    一次 notes_canvas_mutate 原子提交(实体级冲突合并); 先取最新 base_revision,
    409(与较新修改冲突)自动重取 revision 重试, 上限 3 次; 画布无需打开。
    """
    key = _resolve_canvas(canvas, session_id)
    try:
        raw = open(file_path, encoding="utf-8").read() if file_path else sys.stdin.read()
    except OSError as exc:
        raise click.ClickException(f"读输入失败: {exc}") from exc
    _reject_mojibake_runs(raw, "add-cards 输入")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"输入不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise click.ClickException('输入必须是 JSON 对象: {"cards":[...], "relations":[...], ...}')
    cards = payload.get("cards") or []
    relations = payload.get("relations") or []
    remove_cards = payload.get("remove_card_ids") or []
    remove_relations = payload.get("remove_relation_ids") or []
    if not all(isinstance(value, list) for value in (cards, relations, remove_cards, remove_relations)):
        raise click.ClickException("cards/relations/remove_card_ids/remove_relation_ids 必须是数组")
    cards, hydrated_text_cards = _materialize_inline_text_cards(cards)
    patch: dict = {}
    if cards:
        patch["upsert_cards"] = cards
    if remove_cards:
        patch["remove_card_ids"] = remove_cards
    if relations:
        patch["upsert_relations"] = relations
    if remove_relations:
        patch["remove_relation_ids"] = remove_relations
    if "title" in payload:
        patch["set_title"] = True
        patch["title"] = payload.get("title")
    if not patch:
        raise click.ClickException("空 patch: 没有 cards/relations/remove_*/title 任何一项")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        document = _canvas_document(key)  # 每次重取最新 revision
        try:
            result = _mutate(key, document, patch, actor=actor)
            _emit({
                "ok": True,
                "canvas": key,
                "attempt": attempt,
                "upsert_cards": len(cards),
                "hydrated_text_cards": hydrated_text_cards,
                "remove_cards": len(remove_cards),
                "upsert_relations": len(relations),
                "remove_relations": len(remove_relations),
                "set_title": bool(patch.get("set_title")),
                "revision": result.get("revision"),
                "merged": result.get("merged"),
                "duplicate": result.get("duplicate"),
            })
            return
        except click.ClickException as error:
            last_error = error
            if "冲突" not in str(error):
                raise  # 非冲突类错误(参数/校验)不重试
    raise click.ClickException(f"add-cards 连续 3 次冲突未合并: {last_error}")


_SUBMISSION_ACTIVE = ("unreviewed", "seen")
# 与 materialCanvasModel.ts SUBMISSION_FAIL_REASONS 同一份词表(改一侧必须同步另一侧)
SUBMISSION_FAIL_REASONS = ["模态/形态错误", "内容错误/需迭代", "方向错误", "不需要审阅"]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _submission_card(key: str, card: str) -> tuple[dict, dict]:
    document = _canvas_document(key)
    target = next((c for c in document.get("cards") or [] if c.get("id") == card), None)
    if not target:
        raise click.ClickException(f"没找到卡片: {card}")
    return document, target


@cmd_canvas.command("submit")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True, help="卡片 id(支持 <会话id>#<卡片id>)")
@click.option("--by", "actor", default="agent:omni-canvas", help="提审人标识(submittedBy)")
@any_caller
def cmd_canvas_submit(canvas, session_id, card, actor) -> None:
    """把一张卡提交审阅(submission.state=unreviewed); 已在审阅流程中的卡报错。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    document, target = _submission_card(key, card)
    existing = target.get("submission")
    if isinstance(existing, dict) and existing.get("state") in _SUBMISSION_ACTIVE:
        raise click.ClickException(f"卡片已在审阅流程中({existing.get('state')}): {card}")
    target["submission"] = {"state": "unreviewed", "submittedAt": _now_iso(), "submittedBy": actor}
    result = _mutate(key, document, {"upsert_cards": [target]}, actor=actor)
    _emit({"ok": True, "canvas": key, "submitted": card, "state": "unreviewed", "revision": result.get("revision")})


def _verdict_card(key: str, card: str, passed: bool, reason: str | None,
                  note: str | None, actor: str) -> dict:
    """提审卡二值裁决核心(canvas verdict / review verdict 共用)。

    写回 submission.state=fully-passed|not-fully-passed; fail 必须给受控词表 reason。
    返回 {canvas, card, state, reason, revision}。
    """
    if passed and reason:
        reason = None  # --pass 时忽略 reason(沿用旧行为)
    reason_text = None
    document, target = _submission_card(key, card)
    existing = target.get("submission")
    if not isinstance(existing, dict) or not existing.get("state"):
        raise click.ClickException(f"卡片未提审, 先提交审阅: {card}")
    if not passed:
        if not reason:
            raise click.ClickException(f"未通过必须给 reason(受控词表: {' / '.join(SUBMISSION_FAIL_REASONS)})")
        if reason not in SUBMISSION_FAIL_REASONS:
            raise click.ClickException(f"reason 不在受控词表: {reason}(可选: {' / '.join(SUBMISSION_FAIL_REASONS)})")
        reason_text = f"{reason}: {note.strip()}" if note and note.strip() else reason
    target["submission"] = {
        **existing,
        "state": "fully-passed" if passed else "not-fully-passed",
        "verdictAt": _now_iso(),
        "verdictBy": actor,
        **({"reason": reason_text} if reason_text else {}),
    }
    result = _mutate(key, document, {"upsert_cards": [target]}, actor=actor)
    return {"canvas": key, "card": card, "state": target["submission"]["state"],
            "reason": reason_text, "revision": result.get("revision")}


@cmd_canvas.command("verdict")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True, help="卡片 id(支持 <会话id>#<卡片id>)")
@click.option("--pass", "passed", is_flag=True, help="完全通过")
@click.option("--fail", "failed", is_flag=True, help="未完全通过(需 --reason 受控词表值)")
@click.option("--reason", default=None, help=f"受控词表: {' / '.join(SUBMISSION_FAIL_REASONS)}")
@click.option("--note", default=None, help="自由补充(可空; 追加在理由后)")
@click.option("--by", "actor", default="agent:omni-canvas", help="裁决人标识(verdictBy)")
@any_caller
def cmd_canvas_verdict(canvas, session_id, card, passed, failed, reason, note, actor) -> None:
    """给提审卡下二值裁决: --pass 或 --fail(配受控 reason)。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    if passed == failed:
        raise click.ClickException("必须且只能给一个: --pass 或 --fail")
    out = _verdict_card(key, card, passed, reason, note, actor)
    _emit({"ok": True, "canvas": out["canvas"], "verdict": out["card"], "state": out["state"],
           "reason": out["reason"], "revision": out["revision"]})


@cmd_canvas.command("group-describe")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True, help="组卡 id(支持 <会话id>#<卡片id>)")
@click.option("--text", default=None, help="组描述正文(与 --file 二选一)")
@click.option("--file", "file_path", default=None, help="从 UTF-8 文件读正文")
@click.option("--by", "actor", default="agent:omni-canvas")
@any_caller
def cmd_canvas_group_describe(canvas, session_id, card, text, file_path, actor) -> None:
    """给组卡写描述正文(M2/M3c: 正文走 materials/<卡id>.md 分居链, 卡上只挂 source 引用)。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    if (text is None) == (file_path is None):
        raise click.ClickException("--text 与 --file 必须且只能给一个")
    if file_path:
        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise click.ClickException(f"读文件失败: {exc}") from exc
    document, target = _submission_card(key, card)
    if target.get("kind") != "group":
        raise click.ClickException(f"不是组卡: {card}")
    material = _invoke("notes_text_put", {"id": card, "content": text})
    target["source"] = {
        "kind": "file",
        "path": material["path"],
        "token": material["open_token"],
        "mime": material.get("mime"),
        "editableId": card,
    }
    result = _mutate(key, document, {"upsert_cards": [target]}, actor=actor)
    _emit({"ok": True, "canvas": key, "described": card, "revision": result.get("revision")})


# 计划泳道任务列顺序(七态归一化后): 进行→待验→待办→阻塞→挂起→完成→取消
_PLAN_LANE_COLUMNS = ["in_progress", "review", "pending", "blocked", "deferred", "done", "cancelled"]


@cmd_canvas.command("add-plan-lane")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--group", "group_id", required=True, help="plan-groups 组 id(data/services/plan-groups/<id>/)")
@click.option("--plan", "plan_id", default=None, help="只铺该 plan 的任务(按 task.plan_id 过滤)")
@click.option("--by", "actor", default="agent:omni-canvas")
@any_caller
def cmd_canvas_add_plan_lane(canvas, session_id, group_id, plan_id, actor) -> None:
    """把 plan-groups 一组任务铺成画布泳道(M3c)。幂等: 卡 id 约定 lane-<组>/task-<任务id>,
    重跑=upsert 更新(状态/统计刷新), 不重复铺。只读 plan-groups 数据。"""
    from omnicompany.packages.services._core.lifecycle.plan_groups_store import (
        load_group,
        normalize_status,
    )

    key = _resolve_canvas(canvas, session_id)
    group = load_group(group_id)
    if not group:
        raise click.ClickException(f"plan-groups 里没有组: {group_id}")
    tasks = [t for t in (group.get("tasks") or []) if isinstance(t, dict) and not t.get("archived")]
    if plan_id:
        tasks = [t for t in tasks if str(t.get("plan_id") or "") == plan_id]
    group_title = str(group.get("title") or group_id)
    group_desc = str(group.get("description") or "").strip()

    # 统计 + 泳道几何: 每状态一列, 列内纵向排
    buckets: dict[str, list[dict]] = {status: [] for status in _PLAN_LANE_COLUMNS}
    for task in tasks:
        buckets.setdefault(normalize_status(task.get("status")), []).append(task)
    used_columns = [s for s in _PLAN_LANE_COLUMNS if buckets.get(s)]
    total = len(tasks)
    done_count = len(buckets.get("done") or [])
    doing_count = len(buckets.get("in_progress") or [])
    stats = f"{done_count}/{total} done · {doing_count} doing"
    lane_id = f"lane-{group_id}"

    lane_material = _invoke("notes_text_put", {
        "id": lane_id,
        "content": f"{group_title}\n\n{group_desc}\n\n进度: {stats}" + (f"\n过滤 plan: {plan_id}" if plan_id else ""),
    })

    CARD_W, CARD_H, COL_GAP, ROW_GAP, PAD, HEAD = 360.0, 220.0, 40.0, 20.0, 28.0, 34.0
    MAX_ROWS = 8  # 每状态列最多 8 行, 超出向右折子列(否则长列把泳道拉成上万 px 的竖条)
    # 展开成 (状态, 子列任务块) 序列: 列序 = 状态序, 块序 = 组内顺序
    columns: list[tuple[str, list[dict]]] = []
    for status in used_columns:
        column_tasks = buckets[status]
        for start in range(0, len(column_tasks), MAX_ROWS):
            columns.append((status, column_tasks[start:start + MAX_ROWS]))
    lane_w = len(columns) * (CARD_W + COL_GAP) + PAD * 2
    lane_h = min(MAX_ROWS, max((len(chunk) for _, chunk in columns), default=1)) * (CARD_H + ROW_GAP) + HEAD + PAD * 2

    document = _canvas_document(key)
    existing = [c for c in document.get("cards") or [] if isinstance(c, dict)]
    lane_old = next((c for c in existing if c.get("id") == lane_id), None)
    if lane_old:
        lane_x, lane_y = float(lane_old.get("x") or 0), float(lane_old.get("y") or 0)
    else:
        max_bottom = max((float(c.get("y") or 0) + float(c.get("height") or 320) for c in existing), default=0)
        lane_x, lane_y = 60.0, max_bottom + 120.0

    upserts: list[dict] = [{
        "id": lane_id,
        "kind": "group",
        "title": f"{group_title} · {stats}",
        "adapter": "auto",
        "x": lane_x, "y": lane_y, "width": lane_w, "height": lane_h,
        "source": {"kind": "file", "path": lane_material["path"], "token": lane_material["open_token"],
                   "mime": lane_material.get("mime"), "editableId": lane_id},
    }]
    for col, (status, chunk) in enumerate(columns):
        for row, task in enumerate(chunk):
            task_id = str(task.get("id") or "")
            card_id = f"task-{task_id}"
            title = f"【{status}】{task.get('title') or task_id}"
            body_lines = [
                f"{task.get('title') or task_id}",
                "",
                f"状态: {status} · 完成度 {task.get('completion', 0)}%",
                f"plan: {task.get('plan_id') or '-'} · milestone: {task.get('milestone_id') or '-'}",
            ]
            if task.get("description"):
                body_lines += ["", str(task["description"])]
            task_material = _invoke("notes_text_put", {"id": card_id, "content": "\n".join(body_lines)})
            upserts.append({
                "id": card_id,
                "kind": "text",
                "title": title[:120],
                "adapter": "text",
                # 子卡坐标是【相对泳道组卡】的(store/前端约定; 写绝对值会被当成相对值双重偏移)
                "x": PAD + col * (CARD_W + COL_GAP),
                "y": HEAD + PAD + row * (CARD_H + ROW_GAP),
                "width": CARD_W, "height": CARD_H,
                "parentId": lane_id,
                "source": {"kind": "file", "path": task_material["path"], "token": task_material["open_token"],
                           "mime": task_material.get("mime"), "editableId": card_id},
            })
    result = _mutate(key, document, {"upsert_cards": upserts}, actor=actor)
    _emit({"ok": True, "canvas": key, "group": group_id, "lane": lane_id,
           "tasks": len(upserts) - 1, "stats": stats, "revision": result.get("revision")})


def _session_from_canvas_key(key: str) -> str:
    """session-<base64url(external:<sid>)> 反解出 pty 会话 id(拼卡片引用用)。"""
    try:
        encoded = key[len("session-"):]
        encoded += "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded).decode("utf-8")
        return raw[len("external:"):] if raw.startswith("external:") else raw
    except Exception:
        return key


def _session_id_for_canvas_key(key: str) -> str:
    """Return the session identity whose canvasStorageId is exactly ``key``."""
    if key.startswith("session-"):
        return f"external:{_session_from_canvas_key(key)}"
    return f"canvas:{key}"


@cmd_canvas.command("mentions")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--mark", default="@AI", help="提及标记(默认 @AI)")
@any_caller
def cmd_canvas_mentions(canvas, session_id, mark) -> None:
    """扫文字卡里的 @AI 提及(给札记绑定的 AI 的简易约定入口); 返回卡片引用+指令正文。"""
    key = _resolve_canvas(canvas, session_id)
    raw = _invoke("notes_canvas_get", {"id": key})
    if raw is None:
        raise click.ClickException(f"画布不存在: {key}")
    document = json.loads(raw)
    session = _session_from_canvas_key(key)
    hits = []
    for card in document.get("cards") or []:
        if card.get("kind") != "text":
            continue
        material_id = (card.get("source") or {}).get("editableId") or card.get("id")
        try:
            content = (_invoke("notes_text_get", {"id": material_id}) or {}).get("content") or ""
        except click.ClickException:
            continue
        if mark not in content:
            continue
        instruction = content.split(mark, 1)[1].strip()
        hits.append({"ref": f"{session}#{card.get('id')}", "title": card.get("title"), "instruction": instruction})
    _emit({"canvas": key, "mark": mark, "mentions": hits})


@cmd_canvas.command("rewrite")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True)
@click.option("--text", required=True, help="覆写后的正文(标题自动取首行)")
@any_caller
def cmd_canvas_rewrite(canvas, session_id, card, text) -> None:
    """原子覆写既有文字卡正文；页面按 revision 自动接收。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    document = _canvas_document(key)
    target = next((c for c in document.get("cards") or [] if c.get("id") == card), None)
    if not target or target.get("kind") != "text":
        raise click.ClickException(f"不是文字卡或没找到: {card}")
    material_id = (target.get("source") or {}).get("editableId") or card
    material = _invoke("notes_text_put", {"id": material_id, "content": text})
    first_line = (text.strip().splitlines() or [""])[0][:60]
    if first_line:
        target["title"] = first_line
    target["source"] = {
        "kind": "file",
        "path": material["path"],
        "token": material["open_token"],
        "mime": material.get("mime"),
        "editableId": material_id,
    }
    result = _mutate(key, document, {"upsert_cards": [target]})
    _emit({"ok": True, "result": {"rewritten": card, "mode": "atomic-mutation", "revision": result.get("revision")}})


@cmd_canvas.command("freeze")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True)
@click.option("--timeout", default=60.0, help="捕获烘焙较慢, 默认 60s")
@any_caller
def cmd_canvas_freeze(canvas, session_id, card, timeout) -> None:
    """把 web 链接卡冻结成静态快照(剥交互, 单文件 HTML, 原地取代; 需画布开着)。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    _emit(_push(key, "freeze", {"card": card}, timeout))


@cmd_canvas.command("unfreeze")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", required=True)
@click.option("--timeout", default=15.0)
@any_caller
def cmd_canvas_unfreeze(canvas, session_id, card, timeout) -> None:
    """解冻冻结卡: 按记住的活源在旁边新开一块活卡(快照保留; 需画布开着)。"""
    key, card = _resolve_card_target(canvas, session_id, card)
    _emit(_push(key, "unfreeze", {"card": card}, timeout))


@cmd_canvas.command("highlight")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", default=None, help="要高亮的卡片 id")
@click.option("--clear", "clear", is_flag=True, help="清掉当前高亮")
@click.option("--seconds", type=float, default=6.0, help="高亮自动消失秒数(默认 6)")
@click.option("--timeout", default=15.0)
@any_caller
def cmd_canvas_highlight(canvas, session_id, card, clear, seconds, timeout) -> None:
    """高亮一张卡(脉冲描边, 到秒自动消)或清掉高亮(需画布开着)。"""
    key, card = _resolve_card_target(canvas, session_id, card) if card else (_resolve_canvas(canvas, session_id), None)
    if clear:
        args = {"clear": True}
    elif card:
        args = {"card": card, "seconds": seconds}
    else:
        raise click.ClickException("给 --card <id> 或 --clear")
    _emit(_push(key, "highlight", args, timeout))


# ── 评论 / 写入溯源 / 目的后补（2026-09-12） ────────────────────────────────────
#
# 评论是 kind=comment 的特殊卡：评一张或多张卡，可接续；同一条基础评论的接续归为一个线程组。
# 写入溯源来自存储里的 mutation 历史（每条带 WriteIdentity）；目的可后补，一批正式写入
# 可从本地会话 jsonl 拉出当时的用户原话总结成本批目的。

_SEMANTICS = ["question", "correction", "decision", "suggestion", "approval", "note"]
_SEMANTIC_LABEL = {"question": "疑问", "correction": "纠正", "decision": "裁定", "suggestion": "建议", "approval": "认可", "note": "备忘"}
_COMMENT_WIDTH = 260.0
_COMMENT_GAP = 24.0
_THREAD_PAD = 16.0
_THREAD_HEAD = 34.0


def _cards_of(document: dict) -> list[dict]:
    return [card for card in document.get("cards") or [] if isinstance(card, dict)]


def _absolute_xy(document: dict, card: dict) -> tuple[float, float]:
    """子卡坐标是相对组卡的；沿 parentId 链加回去得到画布绝对坐标。"""
    by_id = {c.get("id"): c for c in _cards_of(document)}
    x = float(card.get("x") or 0)
    y = float(card.get("y") or 0)
    parent = card.get("parentId")
    seen: set[str] = set()
    while isinstance(parent, str) and parent in by_id and parent not in seen:
        seen.add(parent)
        p = by_id[parent]
        x += float(p.get("x") or 0)
        y += float(p.get("y") or 0)
        parent = p.get("parentId")
    return x, y


def _comment_size(text: str) -> tuple[float, float]:
    lines = text.splitlines() or [""]
    visual = sum(max(1, math.ceil(len(line) / 18)) for line in lines)
    return _COMMENT_WIDTH, min(520.0, max(150.0, 78.0 + visual * 24.0))


def _place_comment_beside(document: dict, targets: list[dict], width: float, height: float) -> tuple[float, float]:
    """落在目标包围盒右侧，与现有卡不重叠（按画布绝对坐标算）。"""
    xs, ys, rights = [], [], []
    for target in targets:
        ax, ay = _absolute_xy(document, target)
        xs.append(ax)
        ys.append(ay)
        rights.append(ax + float(target.get("width") or 440))
    x = max(rights) + _COMMENT_GAP
    y = min(ys)
    absolute_cards = []
    for card in _cards_of(document):
        ax, ay = _absolute_xy(document, card)
        absolute_cards.append({**card, "x": ax, "y": ay})
    for _ in range(60):
        collisions = [c for c in absolute_cards if _overlaps(x, y, width, height, c, gap=12.0)]
        if not collisions:
            break
        y = max(float(c["y"]) + float(c.get("height") or 220) + 12.0 for c in collisions)
    return x, y


def _thread_group_id(root_id: str) -> str:
    return f"comment-thread-{root_id}"[:120]


def _build_reply(document: dict, reply_to: dict, new_id: str, text: str) -> tuple[list[dict], dict]:
    """接续：第一次接续自动建组把根评论和接续收在一起；之后追加进组。返回 (upserts, 新评论卡)。"""
    cards = _cards_of(document)
    by_id = {c.get("id"): c for c in cards}
    thread = reply_to.get("commentThread") or {}
    root_id = str(thread.get("root") or reply_to.get("id"))
    root = by_id.get(root_id) or reply_to
    root_thread = root.get("commentThread") or thread
    width, height = _comment_size(text)
    upserts: list[dict] = []
    group_id = _thread_group_id(root_id)
    group = by_id.get(group_id)
    if not group:
        rx, ry = _absolute_xy(document, root)
        root_w = float(root.get("width") or _COMMENT_WIDTH)
        root_h = float(root.get("height") or 180)
        group = {
            "id": group_id, "kind": "group", "title": "评论线程", "adapter": "auto",
            "x": rx - _THREAD_PAD, "y": ry - _THREAD_PAD - _THREAD_HEAD,
            "width": max(root_w, width) + _THREAD_PAD * 2,
            "height": _THREAD_HEAD + _THREAD_PAD * 2 + root_h + _COMMENT_GAP + height,
            "presentation": {"style": "comment-thread"},
        }
        upserts.append(group)
        moved_root = {**root, "parentId": group_id, "x": _THREAD_PAD, "y": _THREAD_HEAD + _THREAD_PAD}
        upserts.append(moved_root)
        new_y = _THREAD_HEAD + _THREAD_PAD + root_h + _COMMENT_GAP
    else:
        members = [c for c in cards if c.get("parentId") == group_id]
        bottom = max((float(c.get("y") or 0) + float(c.get("height") or 180) for c in members), default=_THREAD_HEAD + _THREAD_PAD)
        new_y = bottom + _COMMENT_GAP
        grown = {**group, "height": max(float(group.get("height") or 0), new_y + height + _THREAD_PAD),
                 "width": max(float(group.get("width") or 0), width + _THREAD_PAD * 2)}
        upserts.append(grown)
    comment = {
        "id": new_id, "kind": "comment", "title": "接续", "text": text, "adapter": "text",
        "parentId": group_id, "x": _THREAD_PAD, "y": new_y, "width": width, "height": height,
        "commentThread": {"targets": list(root_thread.get("targets") or []), "root": root_id, "replyTo": str(reply_to.get("id"))},
    }
    upserts.append(comment)
    return upserts, comment


@cmd_canvas.command("comment")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", "cards", multiple=True, help="被评的卡 id（可多次给）；接续时可省略")
@click.option("--text", required=True, help="评论正文（Markdown）")
@click.option("--reply-to", default=None, help="接续这条评论（评论卡 id）")
@click.option("--semantic", type=click.Choice(_SEMANTICS), default=None, help="语义分类（agent 评论可直接给）")
@click.option("--by-human", default=None, help="人经 CLI 代写：写机主名，身份记为 human / 命令行")
@click.option("--purpose", default=None, help="这次写入的目的（可后补）")
@any_caller
def cmd_canvas_comment(canvas, session_id, cards, text, reply_to, semantic, by_human, purpose) -> None:
    """对一张或多张卡发评论（便利签卡），或接续一条已有评论；带写入身份。"""
    key = _resolve_canvas(canvas, session_id)
    text = text.strip()
    if not text:
        raise click.ClickException("评论正文不能为空")
    if "???" in text:
        raise click.ClickException("评论正文含连续 ???，疑似有损编码；请检查终端编码后重试")
    document = _canvas_document(key)
    by_id = {c.get("id"): c for c in _cards_of(document)}
    new_id = f"comment-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"
    if reply_to:
        target = by_id.get(reply_to)
        if not target or target.get("kind") != "comment":
            raise click.ClickException(f"--reply-to 不是评论卡: {reply_to}")
        upserts, comment = _build_reply(document, target, new_id, text)
    else:
        targets = [by_id[c] for c in cards if c in by_id]
        missing = [c for c in cards if c not in by_id]
        if missing or not targets:
            raise click.ClickException(f"被评的卡不存在: {', '.join(missing) or '(未给 --card)'}")
        width, height = _comment_size(text)
        x, y = _place_comment_beside(document, targets, width, height)
        comment = {
            "id": new_id, "kind": "comment", "title": "评论", "text": text, "adapter": "text",
            "x": x, "y": y, "width": width, "height": height,
            "commentThread": {"targets": [str(t.get("id")) for t in targets], "root": new_id},
        }
        upserts = [comment]
    if semantic:
        comment["commentThread"]["semantic"] = semantic
    human = {"name": by_human, "input": "cli"} if by_human else None
    result = _mutate(key, document, {"upsert_cards": upserts}, actor="agent:omni-canvas-comment",
                     purpose=purpose or (f"评论 {', '.join(comment['commentThread']['targets'])}" if not reply_to else f"接续评论 {reply_to}"),
                     human=human)
    _emit({
        "ok": True, "canvas": key, "created": new_id, "ref": f"{_session_from_canvas_key(key)}#{new_id}",
        "thread": comment["commentThread"], "revision": result.get("revision"),
        "identity": (result.get("lastMutation") or {}).get("identity") if isinstance(result.get("lastMutation"), dict) else None,
    })


def _identity_label(identity) -> str:
    from omnicompany.packages.services._core.identity.write_identity import write_identity_label
    return write_identity_label(identity if isinstance(identity, dict) else None)


@cmd_canvas.command("comments")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--unclassified", is_flag=True, help="只列还没有语义分类的评论")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_canvas_comments(canvas, session_id, unclassified, as_json) -> None:
    """列画布上的评论线程：谁评了哪些卡、说了什么、分类了没有。"""
    key = _resolve_canvas(canvas, session_id)
    document = _canvas_document(key)
    cards = _cards_of(document)
    by_id = {c.get("id"): c for c in cards}
    comments = [c for c in cards if c.get("kind") == "comment"]
    threads: dict[str, list[dict]] = {}
    for c in comments:
        root = str((c.get("commentThread") or {}).get("root") or c.get("id"))
        threads.setdefault(root, []).append(c)
    rows = []
    for root_id, members in threads.items():
        members.sort(key=lambda c: (0 if c.get("id") == root_id else 1, str(((c.get("provenance") or {}).get("created") or {}).get("at") or "")))
        root = by_id.get(root_id) or members[0]
        thread = root.get("commentThread") or {}
        if unclassified and thread.get("semantic"):
            continue
        rows.append({
            "root": root_id,
            "targets": [{"id": t, "title": (by_id.get(t) or {}).get("title")} for t in thread.get("targets") or []],
            "semantic": thread.get("semantic"),
            "promoted_to": thread.get("promotedTo"),
            "entries": [{
                "id": c.get("id"), "text": c.get("text") or "", "reply_to": (c.get("commentThread") or {}).get("replyTo"),
                "by": _identity_label(((c.get("provenance") or {}).get("created"))),
                "principal": (((c.get("provenance") or {}).get("created")) or {}).get("principal"),
                "at": (((c.get("provenance") or {}).get("created")) or {}).get("at"),
            } for c in members],
        })
    if as_json:
        _emit({"canvas": key, "threads": rows, "count": len(rows)})
        return
    if not rows:
        click.echo("（没有评论）")
        return
    for row in rows:
        targets = "、".join(str(t["title"] or t["id"]) for t in row["targets"]) or "—"
        tag = _SEMANTIC_LABEL.get(row["semantic"] or "", "未分类")
        click.echo(f"■ {row['root']}  评 {targets}  [{tag}]" + (f"  → 已转正 {row['promoted_to']}" if row["promoted_to"] else ""))
        for entry in row["entries"]:
            prefix = "  ↳ " if entry["reply_to"] else "  "
            who = entry["by"] if entry["principal"] else "（无身份记录）"
            click.echo(f"{prefix}{entry['id']}  {who}  {str(entry['at'] or '')[:16]}")
            for line in (entry["text"] or "").splitlines()[:6]:
                click.echo(f"      {line}")


@cmd_canvas.command("classify")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--comment", "comment_id", required=True, help="评论卡 id（线程里任一条都可，分类记在根评论上）")
@click.option("--semantic", type=click.Choice(_SEMANTICS), required=True)
@click.option("--promote-to", default=None, help="评论已转正为正式内容时，指向那张卡")
@any_caller
def cmd_canvas_classify(canvas, session_id, comment_id, semantic, promote_to) -> None:
    """AI 给一条评论（线程）贴语义分类；人写的评论天然是意见，分类是事后标签。"""
    key = _resolve_canvas(canvas, session_id)
    document = _canvas_document(key)
    by_id = {c.get("id"): c for c in _cards_of(document)}
    target = by_id.get(comment_id)
    if not target or target.get("kind") != "comment":
        raise click.ClickException(f"不是评论卡: {comment_id}")
    root_id = str((target.get("commentThread") or {}).get("root") or comment_id)
    root = by_id.get(root_id) or target
    thread = dict(root.get("commentThread") or {})
    thread["semantic"] = semantic
    if promote_to:
        if promote_to not in by_id:
            raise click.ClickException(f"--promote-to 指向的卡不存在: {promote_to}")
        thread["promotedTo"] = promote_to
    result = _mutate(key, document, {"upsert_cards": [{**root, "commentThread": thread}]},
                     actor="agent:omni-canvas-classify", purpose=f"评论 {root_id} 分类为 {semantic}")
    _emit({"ok": True, "canvas": key, "comment": root_id, "semantic": semantic, "promoted_to": promote_to, "revision": result.get("revision")})


@cmd_canvas.command("provenance")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--card", default=None, help="只看触碰过这张卡的写入")
@click.option("--since", type=int, default=None, help="只看 sequence 大于此值的写入")
@click.option("--limit", type=int, default=30)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_canvas_provenance(canvas, session_id, card, since, limit, as_json) -> None:
    """画布写入历史：每次写入的身份（机器 / 运行时 / 会话）、目的、触碰的卡。"""
    key = _resolve_canvas(canvas, session_id)
    if card and "#" in card:
        key, card = _resolve_card_target(canvas, session_id, card)
    args: dict = {"id": key, "limit": limit}
    if card:
        args["card"] = card
    if since is not None:
        args["since"] = since
    payload = _invoke("notes_canvas_mutations", args, timeout=30.0)
    mutations = payload.get("mutations") if isinstance(payload, dict) else []
    if as_json:
        _emit(payload)
        return
    if not mutations:
        click.echo("（没有写入记录）")
        return
    for m in mutations:
        identity = m.get("identity")
        who = _identity_label(identity) if identity else f"（旧式记录）{m.get('actor')}"
        purpose = m.get("purpose") or "—"
        touched = len(m.get("cards") or [])
        click.echo(f"r{m.get('sequence')}  {str(m.get('at') or '')[:19]}  {who}  卡×{touched}  目的: {purpose}")
        session = (identity or {}).get("session") or {}
        if session.get("file"):
            click.echo(f"      会话文件: {session['file']}")


@cmd_canvas.command("purpose")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--mutation", "mutation_ids", multiple=True, help="mutation id（可多次给）")
@click.option("--from-seq", type=int, default=None)
@click.option("--to-seq", type=int, default=None)
@click.option("--text", default=None, help="写入目的")
@click.option("--batch", default=None, help="批号")
@any_caller
def cmd_canvas_purpose(canvas, session_id, mutation_ids, from_seq, to_seq, text, batch) -> None:
    """后补写入目的 / 批号（不推进 revision）。"""
    key = _resolve_canvas(canvas, session_id)
    if not text and not batch:
        raise click.ClickException("给 --text 或 --batch")
    if not mutation_ids and from_seq is None and to_seq is None:
        raise click.ClickException("给 --mutation 或 --from-seq/--to-seq")
    args: dict = {"id": key}
    if mutation_ids:
        args["mutation_ids"] = list(mutation_ids)
    if from_seq is not None:
        args["from_sequence"] = from_seq
    if to_seq is not None:
        args["to_sequence"] = to_seq
    if text:
        args["purpose"] = text
    if batch:
        args["batch"] = batch
    _emit(_invoke("notes_canvas_annotate", args, timeout=30.0))


def _user_utterances(file: str, start: str, end: str, before_seconds: int = 3 * 3600) -> list[tuple[str, str]]:
    """从本地会话 jsonl 拉出时间窗内的用户原话（Claude 与 Codex 两种行格式都认；认不出的行跳过）。

    时间窗 = [首次写入前 before_seconds, 末次写入]；窗内一句都没有时，退回取首次写入之前最后一句——
    一批写入通常是几小时前那句指令引发的。"""
    import datetime as _dt

    def _parse(ts: str) -> _dt.datetime | None:
        try:
            return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None

    lo = _parse(start)
    hi = _parse(end)
    if not lo or not hi:
        return []
    first_write = lo
    lo = lo - _dt.timedelta(seconds=before_seconds)
    out: list[tuple[str, str]] = []
    last_before: tuple[str, str] | None = None
    try:
        with open(file, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"user"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("timestamp") or (row.get("payload") or {}).get("timestamp") or ""
                when = _parse(ts) if ts else None
                if when and when > hi:
                    continue
                in_window = not when or when >= lo
                if not in_window and when and when >= first_write:
                    continue
                message = row.get("message") if isinstance(row.get("message"), dict) else (row.get("payload") if isinstance(row.get("payload"), dict) else {})
                if str(message.get("role") or row.get("role") or "") != "user":
                    continue
                content = message.get("content")
                texts: list[str] = []
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            texts.append(part["text"])
                text = "\n".join(t for t in texts if t).strip()
                if not text or text.startswith(("<system-reminder>", "[SYSTEM", "<command-", "<task-notification>")):
                    continue
                if in_window:
                    out.append((ts, text))
                else:
                    last_before = (ts, text)
    except OSError:
        return []
    if not out and last_before:
        out.append(last_before)
    return out


@cmd_canvas.command("batch-summary")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--since", type=int, default=None, help="从这个 sequence 之后算一批（缺省：最近一批同一会话的连续写入）")
@click.option("--batch", default=None, help="批号（缺省按日期与序列区间生成）")
@click.option("--dry-run", is_flag=True, help="只打印总结，不写回")
@any_caller
def cmd_canvas_batch_summary(canvas, session_id, since, batch, dry_run) -> None:
    """把一批正式写入的目的从本地会话历史里总结出来并盖上批号——agent 合作提交最强调溯源。"""
    key = _resolve_canvas(canvas, session_id)
    payload = _invoke("notes_canvas_mutations", {"id": key, "limit": 500, **({"since": since} if since is not None else {})}, timeout=30.0)
    mutations = [m for m in (payload.get("mutations") if isinstance(payload, dict) else []) if isinstance(m, dict)]
    if since is None and mutations:
        last = mutations[-1]
        last_session = ((last.get("identity") or {}).get("session") or {}).get("native_id")
        picked = []
        for m in reversed(mutations):
            native = ((m.get("identity") or {}).get("session") or {}).get("native_id")
            if native != last_session:
                break
            picked.append(m)
        mutations = list(reversed(picked))
    if not mutations:
        raise click.ClickException("没有可总结的写入")
    seqs = [int(m.get("sequence") or 0) for m in mutations]
    first, last = min(seqs), max(seqs)
    batch = batch or f"batch-{time.strftime('%Y%m%d')}-r{first}-r{last}"
    by_session: dict[str, list[dict]] = {}
    for m in mutations:
        native = ((m.get("identity") or {}).get("session") or {}).get("native_id") or "(无会话)"
        by_session.setdefault(native, []).append(m)
    lines = [f"本批 {len(mutations)} 次写入（r{first}–r{last}），批号 {batch}。"]
    from omnicompany.packages.services._core.identity.write_identity import locate_session_file
    for native, group in by_session.items():
        identity = group[0].get("identity") or {}
        who = _identity_label(identity) if identity else "（无身份记录）"
        touched = sorted({c for m in group for c in (m.get("cards") or [])})
        lines.append(f"- {who}：{len(group)} 次，触碰 {len(touched)} 张卡" + (f"（{', '.join(touched[:8])}{'…' if len(touched) > 8 else ''}）" if touched else ""))
        purposes = [m.get("purpose") for m in group if m.get("purpose")]
        if purposes:
            lines.append("  已记目的：" + "；".join(dict.fromkeys(purposes)))
        session = identity.get("session") or {}
        file = session.get("file") or (locate_session_file((identity.get("runtime") or {}).get("provider"), native) if native != "(无会话)" else None)
        if file:
            utterances = _user_utterances(file, str(group[0].get("at") or ""), str(group[-1].get("at") or ""))
            if utterances:
                lines.append(f"  期间用户原话（{len(utterances)} 条，取最近 5 条）：")
                for ts, text in utterances[-5:]:
                    lines.append(f"    · {str(ts)[:16]} {text[:160].replace(chr(10), ' ')}")
            else:
                lines.append(f"  会话文件 {file}：时间窗内没有用户原话")
        else:
            lines.append("  会话文件未定位到，无法拉取原话")
    summary = "\n".join(lines)
    click.echo(summary)
    if dry_run:
        return
    result = _invoke("notes_canvas_annotate", {"id": key, "from_sequence": first, "to_sequence": last, "purpose": summary[:2000], "batch": batch}, timeout=30.0)
    _emit({"ok": True, "canvas": key, "batch": batch, "annotated": (result or {}).get("annotated")})


@cmd_canvas.command("edits")
@click.option("--canvas", default=None)
@click.option("--session", "session_id", default=None)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_canvas_edits(canvas, session_id, as_json) -> None:
    """列出人改 AI 内容留下的原地拷贝（人改稿）及其原卡，供 AI 按人的要求补全与正式化。"""
    key = _resolve_canvas(canvas, session_id)
    document = _canvas_document(key)
    cards = _cards_of(document)
    by_id = {c.get("id"): c for c in cards}
    rows = []
    for c in cards:
        derived = c.get("derivedFrom") or {}
        if derived.get("mode") != "human-edit-copy":
            continue
        original = by_id.get(derived.get("card")) or {}
        rows.append({
            "copy": c.get("id"), "title": c.get("title"),
            "by": _identity_label((c.get("provenance") or {}).get("created")),
            "original": derived.get("card"), "original_revision": derived.get("revision"),
            "original_hidden": ((original.get("presentation") or {}).get("state") == "hidden"),
            "copy_text_chars": len(c.get("text") or ""),
        })
    if as_json:
        _emit({"canvas": key, "edits": rows, "count": len(rows)})
        return
    if not rows:
        click.echo("（没有人改稿）")
        return
    for row in rows:
        click.echo(f"{row['copy']}  「{row['title']}」  {row['by']}  ← 原卡 {row['original']}@r{row['original_revision']}"
                   + ("（已收进已隐藏）" if row["original_hidden"] else "（原卡仍显示）"))


def _perf_budgets() -> dict:
    budget_file = Path("C:/workspace/webworks/packages/material-core/contract/canvas-perf-budgets.json")
    override = os.environ.get("OMNI_CANVAS_PERF_BUDGETS")
    if override:
        budget_file = Path(override)
    try:
        return json.loads(budget_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _perf_verdict(data: dict, budgets: dict) -> list[str]:
    """与前端 canvasPerfMonitor.evaluateCanvasPerf 同口径：样本太少不判；手势帧与静止帧分开判。"""
    live = budgets.get("live") or {}
    frame = data.get("frame") or {}
    idle = data.get("idleFrame") or {}
    long_tasks = data.get("longTasks") or {}
    interacting = bool((data.get("gesture") or {}).get("pan") or (data.get("gesture") or {}).get("zoom"))
    problems = []
    interaction = live.get("interaction") or {}
    idle_limit = live.get("idle") or {}
    if interacting and int(frame.get("samples") or 0) >= 20:
        if interaction.get("p95FrameMs") and float(frame.get("p95") or 0) > float(interaction["p95FrameMs"]):
            problems.append(f"手势中 p95 帧 {frame.get('p95')}ms > {interaction['p95FrameMs']}")
        if interaction.get("over100PerWindow") is not None and int(frame.get("over100") or 0) > int(interaction["over100PerWindow"]):
            problems.append(f">100ms 帧 {frame.get('over100')} 次")
    if int(idle.get("samples") or 0) >= 60:
        if idle_limit.get("p95FrameMs") and float(idle.get("p95") or 0) > float(idle_limit["p95FrameMs"]):
            problems.append(f"静止 p95 帧 {idle.get('p95')}ms > {idle_limit['p95FrameMs']}")
        if not interacting and idle_limit.get("longTasksPerWindow") is not None and int(long_tasks.get("count") or 0) > int(idle_limit["longTasksPerWindow"]):
            problems.append(f"静止长任务 {long_tasks.get('count')} 次")
    if live.get("heapMB") and float(data.get("heapMB") or 0) > float(live["heapMB"]):
        problems.append(f"堆 {data.get('heapMB')}MB > {live['heapMB']}")
    if live.get("liveIframesNotFullscreen") is not None and not data.get("fullscreen") and int(data.get("liveIframes") or 0) > int(live["liveIframesNotFullscreen"]):
        problems.append(f"非全屏活 iframe {data.get('liveIframes')} 个")
    return problems


@cmd_canvas.command("perf")
@click.option("--limit", type=int, default=40, help="最多看多少条采样")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def cmd_canvas_perf(limit, as_json) -> None:
    """画布性能后台监控：汇总真实页面上报的 canvas_perf 采样并按性能标准判定。"""
    budgets = _perf_budgets()
    samples: list[dict] = []
    # dashboard 8210 内存环（直连 8210 的页面）
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{_DASHBOARD_PORT}/api/diagnostics/frontend?limit=1000")
        with urllib.request.urlopen(req, timeout=10) as resp:
            ring = json.loads(resp.read().decode("utf-8"))
        for row in ring.get("events") or ring.get("items") or []:
            if str(row.get("kind") or "").startswith("canvas_perf"):
                samples.append({"source": "dashboard", **row})
    except Exception:  # noqa: BLE001 - 一路不可达不影响另一路
        pass
    # workshop 落盘（经 /bside 或 /workshop 反代、或直连 5460 的页面）
    workshop_log = Path(os.environ.get("WORKSHOP_DIAGNOSTICS_LOG")
                        or "C:/workspace/spark.workshop/debug-agent/workshop/data/diagnostics/frontend-events.jsonl")
    if workshop_log.is_file():
        try:
            tail = workshop_log.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]
        except OSError:
            tail = []
        for line in tail:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            instance = row.get("instance") if isinstance(row.get("instance"), dict) else {}
            for ev in row.get("events") or []:
                if isinstance(ev, dict) and str(ev.get("kind") or "").startswith("canvas_perf"):
                    samples.append({"source": "workshop", "instance_id": instance.get("instance_id"), "origin": instance.get("origin"),
                                    "ua": instance.get("ua"), "received_at": row.get("received_at"), **ev})
    samples.sort(key=lambda s: float(s.get("t") or 0))
    samples = samples[-limit:]
    if as_json:
        _emit({"budgets": budgets.get("live"), "samples": samples, "count": len(samples)})
        return
    if not samples:
        click.echo("（还没有画布性能采样；打开画布页面后每 30 秒上报一条）")
        return
    click.echo(f"性能标准（真机口径）：{json.dumps(budgets.get('live') or {}, ensure_ascii=False)}")
    for s in samples:
        data = s.get("data") or {}
        when = time.strftime("%m-%d %H:%M:%S", time.localtime(float(s.get("t") or 0))) if s.get("t") else "?"
        frame = data.get("frame") or {}
        gesture = data.get("gesture") or {}
        mode = "缩放" if gesture.get("zoom") else "平移" if gesture.get("pan") else "静止"
        verdict = _perf_verdict(data, budgets)
        flag = "✗ " + "；".join(verdict) if verdict else "✓ 达标"
        device = (s.get("ua") or "")[:40]
        click.echo(f"{when}  {s.get('kind')}  {str(s.get('instance_id') or '')[:8]}  画布 {str(data.get('canvas') or '')[:14]}  "
                   f"缩放 {data.get('zoom')} {mode}  p95 {frame.get('p95')}ms  >100ms {frame.get('over100')}  长任务 {(data.get('longTasks') or {}).get('count')}  "
                   f"卡 {data.get('renderedNodes')}/{data.get('totalNodes')}  iframe {data.get('liveIframes')}  堆 {data.get('heapMB')}MB  "
                   f"{'静态' if data.get('static') else '可交互'}{' 已降级' if data.get('degraded') else ''}  {flag}  {device}")

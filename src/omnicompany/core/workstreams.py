"""Project workstreams and their compact cross-session work archives.

A workstream is a stable, project-scoped responsibility such as Walker card
rules or Dashboard session coordination.  It is deliberately smaller than a
Project and larger than a Task.  The archive stores short checkpoints and
source references, never copied conversation bodies.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\.[a-z0-9][a-z0-9_-]*")
_lock = threading.RLock()


DEFAULT_WORKSTREAMS: tuple[dict[str, Any], ...] = (
    {
        "id": "omnidashboard.multiagent-coordination",
        "project": "omnidashboard",
        "name": "多 Agent 协作与会话治理",
        "kind": "product",
        "scope": "Multiagent、会话身份、分工、消息投递与项目归属",
        "keywords": ["multiagent", "多agent", "会话", "session", "协作", "消息", "项目绑定"],
    },
    {
        "id": "omnidashboard.session-runtime",
        "project": "omnidashboard",
        "name": "CLI 会话运行时",
        "kind": "platform",
        "scope": "ccdaemon、PTY、结构化聊天与 CLI provider 生命周期",
        "keywords": ["ccdaemon", "pty", "provider", "终端", "cli", "运行时"],
    },
    {
        "id": "omnidashboard.interface-shell",
        "project": "omnidashboard",
        "name": "驾驶舱界面与工作台",
        "kind": "product",
        "scope": "Dashboard 外观、布局、导航和响应式交互",
        "keywords": ["dashboard", "驾驶舱", "界面", "布局", "前端", "响应式", "ui"],
    },
    {
        "id": "walker.frontend-appearance",
        "project": "walker",
        "name": "前端外观",
        "kind": "game-product",
        "scope": "页面布局、视觉风格、响应式和界面交互",
        "keywords": ["前端", "外观", "界面", "布局", "css", "ui", "视觉", "响应式"],
    },
    {
        "id": "walker.character-vfx",
        "project": "walker",
        "name": "小人与特效",
        "kind": "game-product",
        "scope": "角色表现、动画、粒子、战斗演出与美术资产",
        "keywords": ["小人", "角色", "character", "动画", "特效", "vfx", "粒子", "演出"],
    },
    {
        "id": "walker.card-programming",
        "project": "walker",
        "name": "卡牌编程",
        "kind": "game-product",
        "scope": "卡牌规则、效果实现、牌组、战斗逻辑与验证",
        "keywords": ["卡牌", "card", "牌组", "deck", "规则", "技能", "战斗逻辑", "effect"],
    },
    {
        "id": "demogame-config.business-delivery",
        "project": "demogame-config",
        "name": "配表业务交付",
        "kind": "demogame-business-family",
        "scope": "具体 demogame 业务的资料、配表、回放验证与 LIVE 交付",
        "keywords": ["配表", "xlsm", "csv", "live", "业务", "benchmark", "p4"],
    },
    {
        "id": "demogame-design.feature-design",
        "project": "demogame-design",
        "name": "玩法细化案",
        "kind": "demogame-business-family",
        "scope": "具体玩法或系统的策划案、规则核对与交付门禁",
        "keywords": ["策划案", "细化案", "玩法", "系统设计", "lint", "词表"],
    },
    {
        "id": "demogame-prefab.prefab-production",
        "project": "demogame-prefab",
        "name": "UX 预制件生产",
        "kind": "demogame-business-family",
        "scope": "具体业务界面的 Figma、Prefab、素材与加载验证",
        "keywords": ["prefab", "figma", "预制件", "ux", "ui", "素材"],
    },
    {
        "id": "demogame-unity.runtime-qa",
        "project": "demogame-unity",
        "name": "Unity 运行验证",
        "kind": "demogame-business-family",
        "scope": "具体业务在 Unity 内的运行、交互、截图和缺陷复验",
        "keywords": ["unity", "qa", "跑测", "截图", "交互", "缺陷", "复验"],
    },
    {
        "id": "demogame-publishing.knowledge-release",
        "project": "demogame-publishing",
        "name": "业务知识发布",
        "kind": "demogame-business-family",
        "scope": "具体 demogame 业务知识的整理、校验与 wiki 发布",
        "keywords": ["wiki", "发布", "知识", "文档", "collab platform", "业务"],
    },
    {
        "id": "demogame-demo.demo-screen",
        "project": "demogame-demo",
        "name": "Demo 屏拆解",
        "kind": "demogame-business-family",
        "scope": "业务 Demo 屏的拆解、重置、素材和交互还原",
        "keywords": ["demo", "屏", "拆解", "重置", "还原", "交互"],
    },
    {
        "id": "bilibili-publish.content-editorial",
        "project": "bilibili-publish",
        "name": "选题与文稿",
        "kind": "content-production",
        "scope": "选题、事实核验、母稿、台本和对外文字",
        "keywords": ["bilibili", "B站", "选题", "事实母稿", "文稿", "台本", "写稿"],
        "context_refs": ["docs/projects/bilibili_publish/HANDOFF.md"],
    },
    {
        "id": "bilibili-publish.video-production",
        "project": "bilibili-publish",
        "name": "视频制作",
        "kind": "content-production",
        "scope": "配音、素材、网页影片、时间轴、构建和成片审阅",
        "keywords": ["bilibili", "B站", "视频", "配音", "素材", "网页影片", "成片"],
        "context_refs": ["docs/projects/bilibili_publish/VIDEO_LANGUAGE_MANUAL.md"],
    },
    {
        "id": "bilibili-publish.release-operations",
        "project": "bilibili-publish",
        "name": "发布与审阅",
        "kind": "content-operations",
        "scope": "审阅材料、投稿包、草稿箱和用户明确授权后的发布操作",
        "keywords": ["bilibili", "B站", "审阅", "reviewstage", "投稿包", "草稿箱", "发布"],
        "context_refs": ["docs/projects/bilibili_publish/HANDOFF.md"],
    },
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def registry_path() -> Path:
    override = os.environ.get("OMNI_WORKSTREAM_REGISTRY_PATH")
    return Path(override) if override else omni_workspace_root() / "data" / "registry" / "workstreams.json"


def archive_root() -> Path:
    override = os.environ.get("OMNI_WORKSTREAM_ARCHIVE_ROOT")
    return Path(override) if override else omni_workspace_root() / "data" / "workstreams"


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(registry_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("workstreams"), list):
            return raw
    except (OSError, ValueError):
        pass
    return {"version": 1, "workstreams": []}


def _write(data: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_workstreams(project: str | None = None) -> list[dict[str, Any]]:
    rows = [dict(row) for row in _read()["workstreams"] if isinstance(row, dict)]
    if project:
        rows = [row for row in rows if row.get("project") == project]
    return sorted(rows, key=lambda row: (str(row.get("project") or ""), str(row.get("id") or "")))


def get_workstream(workstream_id: str) -> dict[str, Any] | None:
    key = str(workstream_id or "").strip().lower()
    for row in list_workstreams():
        aliases = [row.get("id"), *(row.get("aliases") or [])]
        if any(str(alias or "").strip().lower() == key for alias in aliases):
            return row
    return None


def set_workstream(
    workstream_id: str,
    *,
    project: str,
    name: str,
    kind: str = "product",
    scope: str = "",
    keywords: Iterable[str] = (),
    aliases: Iterable[str] = (),
    context_refs: Iterable[str] = (),
    business_ref: str = "",
    by: str = "human",
) -> dict[str, Any]:
    workstream_id = str(workstream_id or "").strip().lower()
    project = str(project or "").strip().lower()
    if not _ID_RE.fullmatch(workstream_id):
        raise ValueError("workstream id 须为 <project>.<slug>，只用小写字母、数字、中划线")
    if not workstream_id.startswith(project + "."):
        raise ValueError("workstream id 前缀必须等于 project")
    from omnicompany.core.projects_registry import list_projects

    if project not in {str(row.get("id")) for row in list_projects()}:
        raise ValueError(f"未注册项目: {project}；先运行 omni project register")
    clean_name = " ".join(str(name or "").split())
    if not clean_name:
        raise ValueError("workstream name 不能为空")

    with _lock:
        data = _read()
        rows = data["workstreams"]
        row = next((item for item in rows if item.get("id") == workstream_id), None)
        if row is None:
            row = {"id": workstream_id, "created_at": _now()}
            rows.append(row)
        row.update(
            project=project,
            name=clean_name,
            kind=str(kind or "product").strip(),
            scope=" ".join(str(scope or "").split()),
            keywords=list(dict.fromkeys(str(v).strip() for v in keywords if str(v).strip())),
            aliases=list(dict.fromkeys(str(v).strip() for v in aliases if str(v).strip())),
            context_refs=list(dict.fromkeys(str(v).strip() for v in context_refs if str(v).strip())),
            business_ref=str(business_ref or "").strip(),
            updated_at=_now(),
            updated_by=by,
            status="active",
        )
        _write(data)
        return dict(row)


def seed_defaults() -> list[dict[str, Any]]:
    existing = {row["id"] for row in list_workstreams()}
    created: list[dict[str, Any]] = []
    for spec in DEFAULT_WORKSTREAMS:
        if spec["id"] in existing:
            continue
        fields = dict(spec)
        workstream_id = str(fields.pop("id"))
        created.append(set_workstream(workstream_id, **fields, by="system_seed"))
    return created


def infer_workstream(project: str, *texts: Any) -> str:
    """Return one unambiguous registered workstream for a project, else ''."""
    rows = list_workstreams(project)
    if not rows:
        return ""
    blob = " ".join(str(value or "") for value in texts).lower()
    scored: list[tuple[int, str]] = []
    for row in rows:
        terms = [row.get("name"), *(row.get("keywords") or []), *(row.get("aliases") or [])]
        score = sum(3 if str(term).lower() in blob and len(str(term)) >= 4 else 1
                    for term in terms if str(term or "").strip() and str(term).lower() in blob)
        if score:
            scored.append((score, str(row["id"])))
    if not scored:
        return str(rows[0]["id"]) if len(rows) == 1 else ""
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return ""
    return scored[0][1]


def _archive_path(workstream_id: str) -> Path:
    project, slug = workstream_id.split(".", 1)
    return archive_root() / project / f"{slug}.jsonl"


def add_checkpoint(
    workstream_id: str,
    summary: str,
    *,
    kind: str = "checkpoint",
    refs: Iterable[str] = (),
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = get_workstream(workstream_id)
    if row is None:
        raise ValueError(f"未注册 workstream: {workstream_id}")
    clean = " ".join(str(summary or "").split())
    if not clean:
        raise ValueError("checkpoint summary 不能为空")
    entry = {
        "id": f"ws-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}",
        "workstream": row["id"],
        "project": row["project"],
        "kind": str(kind or "checkpoint"),
        "summary": clean[:1200],
        "refs": list(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip())),
        "source": source or {},
        "created_at": _now(),
    }
    path = _archive_path(row["id"])
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recent_checkpoints(workstream_id: str, limit: int = 8) -> list[dict[str, Any]]:
    row = get_workstream(workstream_id)
    if row is None:
        raise ValueError(f"未注册 workstream: {workstream_id}")
    path = _archive_path(row["id"])
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, min(int(limit), 50)):]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def compact_context(workstream_id: str, *, limit: int = 8, max_chars: int = 4000) -> str:
    row = get_workstream(workstream_id)
    if row is None:
        raise ValueError(f"未注册 workstream: {workstream_id}")
    lines = [
        f"# Workstream {row['id']} · {row['name']}",
        f"project: {row['project']}",
        f"scope: {row.get('scope') or '-'}",
    ]
    if row.get("business_ref"):
        lines.append(f"demogame business ref: {row['business_ref']}")
    if row.get("context_refs"):
        lines.append("context refs: " + ", ".join(row["context_refs"]))
    entries = recent_checkpoints(row["id"], limit=limit)
    lines.append("recent checkpoints:")
    if not entries:
        lines.append("- (none yet)")
    for entry in entries:
        refs = ", ".join(entry.get("refs") or [])
        source = entry.get("source") or {}
        source_id = source.get("trace_id") or source.get("session_id") or "unknown"
        suffix = f" | refs: {refs}" if refs else ""
        lines.append(f"- {entry.get('created_at')} [{entry.get('kind')}] {entry.get('summary')} | source: {source_id}{suffix}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


__all__ = [
    "DEFAULT_WORKSTREAMS",
    "add_checkpoint",
    "compact_context",
    "get_workstream",
    "infer_workstream",
    "list_workstreams",
    "recent_checkpoints",
    "seed_defaults",
    "set_workstream",
]

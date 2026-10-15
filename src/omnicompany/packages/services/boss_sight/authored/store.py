# [OMNI] origin=ai-ide domain=dashboard/boss_sight ts=2026-06-14T00:00:00Z type=infra status=active
# [OMNI] summary="Note(札记) 模型 + AuthoredStore —— 评论/草稿/llm输入归一, target 多态, 一条一文件落 data/boss_sight/authored。"
# [OMNI] why="收敛 4 个分散写入口为一套中心 store(用户定向 2026-06-14); 仿 reviewstage Comment 模型便于无损迁移。"
# [OMNI] tags=authored,notes,store
"""统一自撰内容(札记) store。

一个 Note = 一段用户写的话 + target(挂谁身上, 多态) + uses(用途, 可叠加) + 截图。
评论 = uses 含 comment; 草稿 = uses 含 draft + target 可为 new_object。
落盘 data/boss_sight/authored/<note_id>.json, 中心 store 不分散, 用 project_id 字段索引。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOTE_USES = {"comment", "draft", "llm_input"}
# material_version / material_link(第二期 A6): 材料轨迹画布上, 评论可以挂在"某个版本"
# 或"两个材料间的一条连线"上, 不只是挂在材料整体上。
# - material_version target 形状: {"kind": "material_version", "material_id": X, "version": N}
# - material_link    target 形状: {"kind": "material_link", "source": mid, "target": mid, "rel": ...}
NOTE_TARGET_KINDS = {
    "material", "project", "plan", "llm_session", "page_element", "new_object",
    "material_version", "material_link",
}
FEEDBACK_STATUSES = {"saved", "delivered", "read", "to_todo", "todo_done"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "note") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


@dataclass
class Note:
    """一条自撰内容。评论/草稿/决策归一。"""
    id: str
    content: str
    title: str = ""                  # 可选显示名(重命名用); 空则列表标题回退到正文首行
    author: str = "user"             # user / controller / subagent
    target: dict[str, Any] = field(default_factory=dict)   # NoteTarget(见 store docstring)
    uses: list[str] = field(default_factory=lambda: ["comment"])  # 子集 of NOTE_USES
    feedback_status: str = "saved"   # 沿用 reviewstage 五态, 兼容 delivered→总控
    feedback_history: list[dict[str, Any]] = field(default_factory=list)
    captures: list[str] = field(default_factory=list)      # 截图相对路径(阶段三)
    project_id: str = "unfiled"      # 归属项目(按项目打标)
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Note":
        return cls(
            id=d["id"],
            content=d.get("content", ""),
            title=d.get("title", ""),
            author=d.get("author", "user"),
            target=d.get("target") or {},
            uses=list(d.get("uses") or ["comment"]),
            feedback_status=d.get("feedback_status") or "saved",
            feedback_history=list(d.get("feedback_history") or []),
            captures=list(d.get("captures") or []),
            project_id=d.get("project_id") or "unfiled",
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            archived=bool(d.get("archived", False)),
            extra=dict(d.get("extra") or {}),
        )


def _store_root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "boss_sight" / "authored"


# ── target → project_id 归属(复用 projects_registry 的 plan↔project 前缀匹配) ──

def compute_project_id(target: dict[str, Any], explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if not isinstance(target, dict):
        return "unfiled"
    # target 自带 project_id(前端在任意实体上写草稿时带上)优先 —— 让 web_review/material 等
    # 子实体草稿也能正确归属项目, 而不是 unfiled。
    if target.get("project_id"):
        return str(target["project_id"])
    kind = target.get("kind")
    try:
        if kind == "project":
            return target.get("id") or "unfiled"
        # 取一个 plan id 用前缀匹配项目
        plan_id = None
        if kind == "plan":
            plan_id = target.get("plan_id") or target.get("id")
        else:
            plan_id = target.get("plan_id") or (target.get("extra") or {}).get("source_plan_id")
        if plan_id:
            pid = _project_for_plan(plan_id)
            if pid:
                return pid
    except Exception:
        pass
    return "unfiled"


def _project_for_plan(plan_id: str) -> str | None:
    """plan_id 形如 'category/[date]NAME' 或 'category' → 按 registry 的 plan_categories 前缀匹配。"""
    try:
        from omnicompany.core.projects_registry import list_projects
    except Exception:
        return None
    cat = str(plan_id).split("/", 1)[0].strip()
    best = None
    for p in list_projects():
        cats = p.get("plan_categories") or []
        for c in cats:
            if cat == c or str(plan_id).startswith(str(c)):
                # 取最长匹配的 category
                if best is None or len(str(c)) > best[1]:
                    best = (p.get("id"), len(str(c)))
    return best[0] if best else None


class AuthoredStore:
    """中心 store。一条 Note 一个 json, 懒加载+缓存(仿 reviewstage MaterialStore)。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._cache: dict[str, Note] | None = None
        # 文件级签名台账: name → (mtime_ns, size)。reload() 靠它做增量加载 —
        # 签名没变的文件复用已解析对象, 不全量重读 (571 文件全量重解析是
        # _attach_notes_to_materials 每次 reload 的主开销, 2026-07 P0 批)。
        self._file_sigs: dict[str, tuple[int, int]] = {}

    def _scan_file_sigs(self) -> dict[str, tuple[int, int]] | None:
        """os.scandir 取每个 note JSON 的 (mtime_ns, size) 签名。
        None = 扫盘失败(临时 IO 错), 调用方保守保持现状; 空 dict = 目录不存在/无文件。"""
        sigs: dict[str, tuple[int, int]] = {}
        try:
            with os.scandir(self.root) as it:
                for entry in it:
                    try:
                        if not entry.is_file() or not entry.name.lower().endswith(".json"):
                            continue
                        st = entry.stat()
                    except OSError:
                        continue
                    sigs[entry.name] = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return {}
        except OSError:
            return None
        return sigs

    def _refresh_locked(self) -> None:
        """签名增量刷新 (须已持 self._lock 且 self._cache 非 None):
        签名没变的文件复用已解析对象; 只重读变化的、补读新增的、删除消失的。"""
        assert self._cache is not None
        sigs = self._scan_file_sigs()
        if sigs is None:
            return
        for name, sig in sigs.items():
            stem = name[:-len(".json")]
            # 未变 — 复用缓存对象, 不重读 (缓存里必须真有该对象才算命中, 防孤签名挡掉首载;
            # 坏文件缓存里没有, 每次 reload 会重试一次, 与原全量重读语义一致)
            if self._file_sigs.get(name) == sig and stem in self._cache:
                continue
            try:
                self._cache[stem] = Note.from_dict(json.loads((self.root / name).read_text(encoding="utf-8")))
            except Exception:
                # 坏文件静默跳过 (沿袭原 _ensure_loaded 语义)
                self._cache.pop(stem, None)
            self._file_sigs[name] = sig
        # 盘上消失的文件 → 缓存同步摘除
        for name in list(self._file_sigs):
            if name not in sigs:
                self._file_sigs.pop(name)
                self._cache.pop(name[:-len(".json")], None)

    def _ensure_loaded(self) -> dict[str, Note]:
        with self._lock:
            if self._cache is None:
                self._cache = {}
                self._refresh_locked()
            return self._cache

    def reload(self) -> None:
        """重新扫盘, 保证调用后看到盘上最新内容 (CLI 进程外直写, 测试/外部修改时用)。

        增量实现: 文件级 (mtime_ns, size) 签名比对, 未变文件不重读 — 对外语义不变。
        """
        with self._lock:
            if self._cache is None:
                return  # 尚未加载 — 下次 _ensure_loaded 首载即全量
            self._refresh_locked()

    def json_path(self, note_id: str) -> str:
        return str((self.root / f"{note_id}.json").resolve())

    def _persist(self, n: Note) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{n.id}.json"
        path.write_text(
            json.dumps(n.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        # 自己写的文件同步签名台账, 下次 reload 不会把自己刚写的盘当"外部变更"重读
        try:
            st = path.stat()
            self._file_sigs[path.name] = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._file_sigs.pop(path.name, None)

    # ── CRUD ──

    def create(self, *, content: str, author: str = "user",
               target: dict[str, Any] | None = None, uses: list[str] | None = None,
               feedback_status: str = "saved", project_id: str | None = None,
               captures: list[str] | None = None, extra: dict[str, Any] | None = None) -> Note:
        content = (content or "").strip()
        if not content:
            raise ValueError("content is empty")
        target = target or {}
        uses = [u for u in (uses or ["comment"]) if u in NOTE_USES] or ["comment"]
        if feedback_status not in FEEDBACK_STATUSES:
            feedback_status = "saved"
        now = _now_iso()
        n = Note(
            id=_new_id("note"),
            content=content,
            author=author,
            target=target,
            uses=uses,
            feedback_status=feedback_status,
            feedback_history=[{"status": feedback_status, "by": author, "at": now}],
            captures=list(captures or []),
            project_id=compute_project_id(target, project_id),
            created_at=now,
            updated_at=now,
            extra=dict(extra or {}),
        )
        with self._lock:
            self._ensure_loaded()[n.id] = n
            self._persist(n)
        return n

    def get(self, note_id: str) -> Note | None:
        return self._ensure_loaded().get(note_id)

    def list(self, *, target_kind: str | None = None, target_id: str | None = None,
             project: str | None = None, uses: str | None = None, q: str | None = None,
             include_archived: bool = False) -> list[Note]:
        items = list(self._ensure_loaded().values())
        if not include_archived:
            items = [n for n in items if not n.archived]
        if target_kind:
            items = [n for n in items if (n.target or {}).get("kind") == target_kind]
        if target_id:
            items = [n for n in items if _target_matches_id(n.target, target_id)]
        if project:
            items = [n for n in items if n.project_id == project]
        if uses:
            items = [n for n in items if uses in (n.uses or [])]
        if q:
            ql = q.lower()
            items = [n for n in items if ql in n.content.lower()
                     or ql in json.dumps(n.target, ensure_ascii=False).lower()
                     or ql in (n.project_id or "").lower()]
        items.sort(key=lambda n: n.updated_at or n.created_at, reverse=True)
        return items

    def update(self, note_id: str, *, content: str | None = None,
               uses: list[str] | None = None, feedback_status: str | None = None,
               title: str | None = None,
               by: str = "user") -> Note:
        with self._lock:
            n = self.get(note_id)
            if n is None:
                raise KeyError(note_id)
            now = _now_iso()
            if content is not None:
                c = content.strip()
                if not c:
                    raise ValueError("content is empty")
                n.content = c
            if title is not None:
                n.title = title.strip()[:200]   # 空字符串=清掉自定义名, 回退正文首行
            if uses is not None:
                n.uses = [u for u in uses if u in NOTE_USES] or n.uses
            if feedback_status is not None:
                if feedback_status not in FEEDBACK_STATUSES:
                    raise ValueError(f"invalid feedback status: {feedback_status}")
                old = n.feedback_status
                n.feedback_status = feedback_status
                n.feedback_history.append({"from": old, "status": feedback_status, "by": by, "at": now})
            n.updated_at = now
            self._persist(n)
            return n

    def delete(self, note_id: str) -> bool:
        """软归档(沿用 Material.archived 语义)。"""
        with self._lock:
            n = self.get(note_id)
            if n is None:
                return False
            n.archived = True
            n.updated_at = _now_iso()
            self._persist(n)
            return True


def _target_matches_id(target: dict[str, Any], tid: str) -> bool:
    if not isinstance(target, dict):
        return False
    return tid in (
        target.get("id"), target.get("uri"), target.get("material_id"),
        target.get("plan_id"), target.get("session_id"),
    )


_STORE: AuthoredStore | None = None


def get_authored_store() -> AuthoredStore:
    global _STORE
    if _STORE is None:
        _STORE = AuthoredStore(_store_root())
    return _STORE


__all__ = ["Note", "AuthoredStore", "get_authored_store", "compute_project_id",
           "NOTE_USES", "NOTE_TARGET_KINDS", "FEEDBACK_STATUSES"]

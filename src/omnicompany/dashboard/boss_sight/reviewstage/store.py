# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.reviewstage.store.py"
"""MaterialStore — 审阅台 material 持久化.

落实块 4 B4-R1.

设计:
- 单 Material 一份 JSON 在 `data/boss_sight/reviewstage/<material_id>.json`
- 实际内容文件 (image/html/markdown) 在 `data/boss_sight/reviewstage/files/<material_id>.<ext>`
- atomic write: tempfile + rename (Windows 兼容)
- 不引入数据库, 走文件系统 + 内存索引 + best-effort lock; 用户级使用规模够 (审阅台
  通常 <1000 条 material, 不需要 sqlite)
- subscribers 机制: routes.py 的 WS 端点订阅 store, store 写盘 + 状态变化时同步通知

数据模型:
- MaterialKind: image / markdown / html / key_question (Phase A 4 类, 用户原文 §4.2)
- MaterialTier: mandatory / important / processual / ignored (4 级, 用户原文 §4.6)
- MaterialStatus: pending / accepted / rejected / blocked (审阅状态)
- Annotation: AI 批注, 跟正式内容分离 (§4.4.1 / §4.5)
- Comment: 用户评论 (§4.4 / §4.5)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, ClassVar, Mapping

from omnicompany.packages.services._core.omnicompany.formats import REVIEW_MATERIAL
from omnicompany.packages.services._core.omnicompany.material_events import publish_material_event
from omnicompany.protocol.format import FormatRegistry

from .capabilities import (
    ReminderSeverity,
    ReviewContext,
    ReviewSubmissionIntent,
    is_structured_aigc_candidate_registration,
    resolve_review_submission,
)
from .content_validators import TEXT_KINDS, validate_kind_file_compat, validate_material_structure
from .material_types import (
    ATTACHMENT_ONLY_REVIEW_KINDS,
    normalize_review_kind,
    normalize_review_project,
    normalize_review_tier,
    project_domains,
    project_registered_tracks,
    project_subject_types,
    review_material_tags,
)

_log = logging.getLogger(__name__)
_REMOTE_PREFLIGHT_WRITERS = frozenset({
    "review-submit-remote-preflight",
    "review-push-remote-preflight",
    "remote-verification-service",
})
_REMOTE_VERIFICATION_WRITERS = frozenset({"remote-verification-service"})


def _load_review_context(value: Any) -> ReviewContext | None:
    """Read persisted review metadata without making legacy files unloadable."""

    if value is None:
        return None
    try:
        return ReviewContext.from_value(value)
    except (TypeError, ValueError):
        _log.warning("MaterialStore: ignored invalid persisted review_context", exc_info=True)
        return None


# ────────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────────


class MaterialKind(str, Enum):
    """Legacy built-in review material kinds.

    新 kind 的权威扩展路径是注册带 `review.kind.*` tag 的 Format, 不是继续改本 enum。
    """
    image = "image"
    markdown = "markdown"
    html = "html"
    key_question = "key_question"
    # 块 5 B5-R1: §4.2.5 自定义网页模板元编程
    # inline_content 是 JSON 字符串, extra.data_schema_id 指定渲染模板
    custom_web_template = "custom_web_template"
    # 公众号式审阅总览的可读媒体类型(2026-06-24): 视频材料,file_relpath 指向视频文件,
    # extra.poster_relpath / extra.video_url(YouTube 等)可选。见 docs/plans/dashboard 计划。
    video = "video"


class MaterialTier(str, Enum):
    """4 级 — 用户原文 §4.6.1-4."""
    mandatory = "mandatory"      # 必验收 (阻断)
    important = "important"      # 重要
    processual = "processual"    # 有意义过程性
    ignored = "ignored"          # 其余 / 不审阅


class MaterialStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    blocked = "blocked"      # 必验收发现严重偏差, 需总控调整后再 submit 新版本


class AnnotationKind(str, Enum):
    """批注种类: ai 是总控自动加的, user 是用户手动加的."""
    ai = "ai"
    user = "user"


# ────────────────────────────────────────────────────────────────────────
# Dataclasses
# ────────────────────────────────────────────────────────────────────────


# saved = 已保存但未送达总控(新评论默认). 只有用户显式"发送"才转 delivered。
# saved 不在 cockpit 的未解决/优先白名单里, 故对总控天然隐身 —— 保存评论 ≠ 使用总控。
COMMENT_FEEDBACK_STATUSES = {"saved", "delivered", "read", "to_todo", "todo_done"}


@dataclass
class Annotation:
    """AI 批注 / 用户高亮. 跟正式内容 DOM 分离 (§4.4.1).

    target 字段定位:
    - image: {"x": ..., "y": ..., "w": ..., "h": ...}  归一化 0-1
    - markdown: {"line_start": ..., "line_end": ...} 行号
    - html: {"selector": "..."} CSS 选择器 (圈选元素时)
    - key_question: {"question_index": ...}
    """
    id: str
    kind: AnnotationKind | str
    content: str
    target: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    author: str = "controller"  # controller / user / subagent

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value if isinstance(self.kind, AnnotationKind) else self.kind
        return d


@dataclass
class Comment:
    """用户对 material 的评论. 历史性内容跟正式内容分离 (§4.4.1)."""
    id: str
    content: str
    author: str  # user / controller / subagent
    target: dict[str, Any] = field(default_factory=dict)  # 同 Annotation.target, 可选定位
    created_at: str = ""
    feedback_status: str = "saved"  # 新评论默认只保存, 不自动发总控
    feedback_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Material:
    """一条待审阅 material 完整状态."""

    id: str
    kind: MaterialKind | str
    tier: MaterialTier | str
    title: str
    status: MaterialStatus | str = MaterialStatus.pending
    # 来源 (§4.3 总控指定 / subagent 写入)
    source_subagent_id: str | None = None
    source_plan_id: str | None = None
    # 内容: 二选一. file_relpath = data/boss_sight/reviewstage/files/<basename> 相对工作目录
    # inline_content = 直接放数据库 (key_question 用 JSON, 短 markdown 也可)
    file_relpath: str | None = None
    inline_content: str | None = None
    # AI 批注 + 用户评论. annotations_allowed=False 时不显批注按钮 (§4.5: 小说类产物)
    annotations: list[Annotation] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    annotations_allowed: bool = True
    # 时间 + history
    created_at: str = ""
    updated_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    # 推送标记 (§2.10 总控主动推送)
    pushed_to_user: bool = False
    pushed_reason: str | None = None
    pushed_at: str | None = None
    # 软归档(用户手动): 不删文件, 默认 list 不返回; "已归档"筛选可还原。
    archived: bool = False
    # extra: 留给元编程 phase B
    extra: dict[str, Any] = field(default_factory=dict)
    # 审阅场景是一等字段；profile/schema/references 可由生产者声明，
    # resolution/reminders 由统一解析器生成，不能藏进 extra 形成第二真源。
    review_context: ReviewContext | None = None
    # ── Material 契约链(第二期): 项目上下文画布用它做投影(A1) ──
    project: str = ""            # 复用 decisions 库项目名录; "unfiled"=未分组
    track: str = ""              # 阶段/轨道名(如 信息审阅稿/交互审阅稿/工作报告)
    version: int | None = None   # 版本号(同 version_family 内递增)
    version_family: str = ""     # 版本族(同一份稿的多版本共 family; 默认=title)
    # 内容资产身份: project → subject(如 EP0) → revision(整期修改版) → track → material。
    # version 仍是某一材料族自身版本；revision 是跨台本/写稿/成片共享的整期版本。
    subject_id: str = ""
    subject_type: str = ""
    revision: int | None = None
    links: dict[str, Any] = field(default_factory=dict)  # {parent?, supersedes?: [], related?: []} 值=material id

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "kind": self.kind.value if isinstance(self.kind, MaterialKind) else self.kind,
            "tier": self.tier.value if isinstance(self.tier, MaterialTier) else self.tier,
            "title": self.title,
            "status": self.status.value if isinstance(self.status, MaterialStatus) else self.status,
            "source_subagent_id": self.source_subagent_id,
            "source_plan_id": self.source_plan_id,
            "file_relpath": self.file_relpath,
            "inline_content": self.inline_content,
            "annotations": [a.to_dict() for a in self.annotations],
            "comments": [c.to_dict() for c in self.comments],
            "annotations_allowed": self.annotations_allowed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history": list(self.history),
            "pushed_to_user": self.pushed_to_user,
            "pushed_reason": self.pushed_reason,
            "pushed_at": self.pushed_at,
            "archived": self.archived,
            "extra": dict(self.extra),
            "review_context": (
                self.review_context.to_dict()
                if self.review_context is not None
                else None
            ),
            "project": self.project,
            "track": self.track,
            "version": self.version,
            "version_family": self.version_family,
            "subject_id": self.subject_id,
            "subject_type": self.subject_type,
            "revision": self.revision,
            "links": dict(self.links),
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Material:
        annotations = [
            Annotation(
                id=a.get("id", ""),
                kind=a.get("kind", "ai"),
                content=a.get("content", ""),
                target=a.get("target") or {},
                created_at=a.get("created_at", ""),
                author=a.get("author", "controller"),
            )
            for a in (d.get("annotations") or [])
        ]
        comments = [
            Comment(
                id=c.get("id", ""),
                content=c.get("content", ""),
                author=c.get("author", "user"),
                target=c.get("target") or {},
                created_at=c.get("created_at", ""),
                feedback_status=c.get("feedback_status") or "delivered",
                feedback_history=list(c.get("feedback_history") or []),
            )
            for c in (d.get("comments") or [])
        ]
        return cls(
            id=d["id"],
            kind=d.get("kind", "markdown"),
            tier=d.get("tier", "important"),
            title=d.get("title", ""),
            status=d.get("status", "pending"),
            source_subagent_id=d.get("source_subagent_id"),
            source_plan_id=d.get("source_plan_id"),
            file_relpath=d.get("file_relpath"),
            inline_content=d.get("inline_content"),
            annotations=annotations,
            comments=comments,
            annotations_allowed=bool(d.get("annotations_allowed", True)),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            history=list(d.get("history") or []),
            pushed_to_user=bool(d.get("pushed_to_user", False)),
            pushed_reason=d.get("pushed_reason"),
            pushed_at=d.get("pushed_at"),
            archived=bool(d.get("archived", False)),
            extra=dict(d.get("extra") or {}),
            review_context=_load_review_context(d.get("review_context")),
            project=d.get("project", "") or "",
            track=d.get("track", "") or "",
            version=d.get("version", None),
            version_family=d.get("version_family", "") or "",
            subject_id=d.get("subject_id", "") or "",
            subject_type=d.get("subject_type", "") or "",
            revision=d.get("revision", None),
            links=dict(d.get("links") or {}),
        )


# 决策候选等治理记录，以及图片附件，复用 MaterialStore 做持久化，但不是给用户逐件
# 查看的一等工作报告/对照稿。默认审阅列表隐藏；专用治理命令可用
# include_internal=True 显式读取。
_INTERNAL_REVIEW_KINDS = frozenset({"decision-candidate"}) | ATTACHMENT_ONLY_REVIEW_KINDS


def is_internal_review_material(material: Material) -> bool:
    """普通审阅队列不应直接展示的内部记录或附件。"""

    kind = material.kind.value if isinstance(material.kind, MaterialKind) else material.kind
    visibility = str((material.extra or {}).get("reviewstage_visibility") or "").strip().lower()
    return kind in _INTERNAL_REVIEW_KINDS or visibility == "internal"


# ────────────────────────────────────────────────────────────────────────
# Store
# ────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "mat") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _normalize_subject_identity(
    subject_id: Any,
    subject_type: Any,
    revision: Any,
    *,
    required_types: set[str] | None = None,
) -> tuple[str, str, int | None]:
    """校验跨阶段内容身份；空集合表示项目未声明强制主体类型。"""
    sid = str(subject_id or "").strip()
    stype = str(subject_type or "").strip().lower()
    required = set(required_types or ())
    any_value = bool(sid or stype or revision is not None)
    if not any_value and not required:
        return "", "", None
    if not sid:
        raise ValueError("subject_id is required — 例如视频分期使用 `--subject-id EP0`。")
    if not stype:
        raise ValueError("subject_type is required — 例如视频分期使用 `--subject-type episode`。")
    if len(sid) > 80 or len(stype) > 40:
        raise ValueError("subject_id/subject_type too long")
    if required and stype not in required:
        raise ValueError(
            f"subject_type {stype!r} 不符合项目主体类型登记；允许值: {', '.join(sorted(required))}。"
        )
    if revision is None:
        raise ValueError("revision is required — 这是跨台本/写稿/成片共享的整期修改版本。")
    try:
        rev = int(revision)
    except (TypeError, ValueError):
        raise ValueError(f"revision must be a positive integer, got {revision!r}")
    if rev <= 0:
        raise ValueError(f"revision must be a positive integer, got {rev}")
    return sid, stype, rev


def _atomic_write(path: Path, data: str) -> None:
    """tempfile + rename. Windows 兼容: 用同目录 tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(path.parent), suffix=".tmp",
    )
    try:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, str(path))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _track_registration_warning(
    project: str, track: str, registry: FormatRegistry | None,
) -> dict[str, Any] | None:
    """轨道在册软校验。project 不属任何注册域 → None(存量不校验)。属注册域但 track 既非
    该域层级名、也非 "<project>/..." 前缀轨道 → 返回一条 code=track-unregistered 警告
    (message 附该项目可用层级清单)。绝不阻断创建。"""
    if not project or not track:
        return None
    if not project_domains(project, registry):
        return None  # 项目不属任何注册域: 完全不校验
    if track.startswith(f"{project}/"):
        return None  # 项目前缀轨道视为在册(项目专属层级)
    registered = project_registered_tracks(project, registry)
    if track in registered:
        return None
    available = "、".join(sorted(registered)) if registered else "(无)"
    return {
        "code": "track-unregistered",
        "severity": "warning",
        "message": (
            f"轨道 {track!r} 不在项目 {project!r} 的在册层级里。该项目可用层级: {available}"
            f"(或用 \"{project}/...\" 前缀登记项目专属层级)。"
        ),
        "path": "track",
    }


class MaterialStore:
    """审阅台 material 持久化.

    Path layout:
        <root>/<material_id>.json     # Material metadata
        <root>/files/<basename>       # 实际内容文件 (image/html/markdown)

    Subscribers: store 写盘 + 状态转移时同步触发. 用于:
    - WS endpoint 实时推送
    - subagent.spawned 时阻断检查 (块 4 R8)
    """

    SUBDIR_FILES: ClassVar[str] = "files"

    def __init__(self, root: Path | str, *, format_registry: FormatRegistry | None = None) -> None:
        self.root = Path(root)
        self.files_dir = self.root / self.SUBDIR_FILES
        # 2026-06-13: 每材料一个评论 markdown 文件(用户自读自写/VSCode 直接编辑)。
        # 与 reviewstage Comment 数组分离 — 走 comments-file API, 不发 store 事件、不唤起总控。
        self.comments_dir = self.root / "comments"
        self.format_registry = format_registry
        self._lock = threading.RLock()
        self._cache: dict[str, Material] | None = None
        # 文件级签名台账: name → ((mtime_ns, size), material_id|None)。
        # reload() 靠它做增量加载 — 签名没变的文件复用已解析对象, 不全量重读
        # (733 个 JSON 全量重解析实测 0.17s, 是 review-canvas/domain-tree 每次全刷的主因)。
        # CLI 会从进程外直写这些文件, 不能用进程内 dirty token; Windows 目录 mtime
        # 不可靠, 必须文件级签名 (2026-07 P0 批)。
        self._file_sigs: dict[str, tuple[tuple[int, int], str | None]] = {}
        self._subscribers: list[Callable[[str, Material], None]] = []
        self.root.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.comments_dir.mkdir(parents=True, exist_ok=True)

    # ── subscribe ─────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[str, Material], None]) -> None:
        """订阅写事件. callback(event_type, material) — 同步调用. event_type ∈
        {'created', 'updated', 'verdict_changed', 'comment_added', 'annotation_added',
        'pushed', 'deleted'}."""
        self._subscribers.append(callback)

    def _notify(self, event_type: str, material: Material) -> None:
        for cb in self._subscribers:
            try:
                cb(event_type, material)
            except Exception:  # noqa: BLE001
                _log.exception("MaterialStore subscriber failed: %s", event_type)

    # ── cache / load ─────────────────────────────────────────────────

    def _scan_file_sigs(self) -> dict[str, tuple[int, int]] | None:
        """os.scandir 取每个 material JSON 的 (mtime_ns, size) 签名 (毫秒级)。

        None = 扫盘失败(权限/临时 IO 错), 调用方保守保持现状; 空 dict = 目录不存在/无文件。
        """
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

    def _drop_id_if_orphaned(self, material_id: str | None) -> None:
        """摘除缓存里的 material_id — 但仅当没有其它在册文件还提供同一 id
        (病态但可能的场景: 同 id 多文件, 删/坏/改一个不应误摘另一个的内容)。"""
        if not material_id:
            return
        if any(v[1] == material_id for v in self._file_sigs.values()):
            return
        self._cache.pop(material_id, None)

    def _refresh_locked(self) -> None:
        """签名增量刷新 (须已持 self._lock 且 self._cache 非 None):
        签名没变的文件复用已解析对象; 只重读变化的、补读新增的、删除消失的。"""
        assert self._cache is not None
        sigs = self._scan_file_sigs()
        if sigs is None:
            return
        for name, sig in sigs.items():
            prev = self._file_sigs.get(name)
            # 未变 — 复用缓存对象, 不重读 (prev[1] 为 None = 坏文件, 没变之前同样不重读;
            # 缓存里必须真有该对象才算命中, 防孤签名挡掉首载)
            if prev is not None and prev[0] == sig and (prev[1] is None or prev[1] in self._cache):
                continue
            old_id = prev[1] if prev else None
            try:
                data = json.loads((self.root / name).read_text(encoding="utf-8"))
                m = Material.from_dict(data)
            except (OSError, json.JSONDecodeError, KeyError):
                _log.exception("MaterialStore: skip corrupt %s", name)
                # 坏文件也记签名 — 没变之前不再反复重读/刷 log
                self._file_sigs[name] = (sig, None)
                self._drop_id_if_orphaned(old_id)
                continue
            self._file_sigs[name] = (sig, m.id)
            if old_id and old_id != m.id:
                self._drop_id_if_orphaned(old_id)
            self._cache[m.id] = m
        # 盘上消失的文件 → 缓存同步摘除
        for name in list(self._file_sigs):
            if name not in sigs:
                old_id = self._file_sigs.pop(name)[1]
                self._drop_id_if_orphaned(old_id)

    def _refresh_material_locked(self, material_id: str) -> None:
        """Refresh one canonical material file before an ID-scoped read/write.

        Reviewstage is used by the daemon, CLI processes, and multiple HTTP
        workers at the same time. A worker that already has an ID cached must
        still notice when another process records a remote-verification receipt;
        otherwise its next mutation can overwrite that receipt with stale data.
        """
        assert self._cache is not None
        name = f"{material_id}.json"
        if Path(name).name != name:
            return
        path = self.root / name
        previous = self._file_sigs.get(name)
        try:
            stat = path.stat()
        except FileNotFoundError:
            if previous is not None:
                old_id = self._file_sigs.pop(name)[1]
                self._drop_id_if_orphaned(old_id)
            return
        except OSError:
            return

        signature = (stat.st_mtime_ns, stat.st_size)
        if (
            previous is not None
            and previous[0] == signature
            and (previous[1] is None or previous[1] in self._cache)
        ):
            return

        old_id = previous[1] if previous else None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            material = Material.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError):
            _log.exception("MaterialStore: skip corrupt %s", name)
            self._file_sigs[name] = (signature, None)
            self._drop_id_if_orphaned(old_id)
            return

        self._file_sigs[name] = (signature, material.id)
        if old_id and old_id != material.id:
            self._drop_id_if_orphaned(old_id)
        self._cache[material.id] = material

    def _ensure_loaded(self) -> dict[str, Material]:
        if self._cache is None:
            with self._lock:
                if self._cache is None:
                    self._cache = {}
                    self._refresh_locked()
        return self._cache

    def reload(self) -> None:
        """重新扫盘, 保证调用后看到盘上最新内容 (CLI 会进程外直写, 测试/外部修改时用)。

        增量实现: 文件级 (mtime_ns, size) 签名比对, 未变文件不重读 — 对外语义不变。
        """
        with self._lock:
            if self._cache is None:
                return  # 尚未加载 — 下次 _ensure_loaded 首载即全量, 无需先扫一遍
            self._refresh_locked()

    # ── CRUD ─────────────────────────────────────────────────────────

    def _persist(self, m: Material) -> None:
        m.updated_at = _now_iso()
        path = self.root / f"{m.id}.json"
        _atomic_write(path, json.dumps(m.to_dict(), ensure_ascii=False, indent=2))
        # 自己写的文件同步签名台账, 下次 reload 不会把自己刚写的盘当"外部变更"重读
        try:
            st = path.stat()
            self._file_sigs[path.name] = ((st.st_mtime_ns, st.st_size), m.id)
        except OSError:
            self._file_sigs.pop(path.name, None)

    # ── 评论文件(每材料一个 markdown) ─────────────────────────────────
    # 用户 2026-06-13: 评论不进 Comment 数组、不自动发总控; 落一个 .md, 追加式,
    # 用户可在 VSCode 直接编辑; dashboard 渲染并提供追加/打开。每条评论 = 一个 `## [时间]` 段。

    def comments_file_path(self, material_id: str) -> Path:
        safe = "".join(ch for ch in material_id if ch.isalnum() or ch in "_-")
        return self.comments_dir / f"{safe or 'unknown'}.md"

    def write_comments_file(self, material_id: str, content: str) -> str:
        """整文件替换(就地编辑/删除某条评论用)。content = 用户改后的完整 .md。"""
        text = (content or "").strip()
        _atomic_write(self.comments_file_path(material_id), text + ("\n" if text else ""))
        return text

    def read_comments_file(self, material_id: str, *, title: str | None = None) -> str:
        # 文件即真源, 纯评论内容; 不预置标题/说明文字(那些是网页的事, 不塞进文件)。
        path = self.comments_file_path(material_id)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""
        return ""

    def append_comment_block(
        self, material_id: str, content: str, *, author: str = "user",
        anchor: str | None = None, title: str | None = None,
    ) -> str:
        content = (content or "").strip()
        if not content:
            raise ValueError("comment content is empty")
        base = self.read_comments_file(material_id, title=title)
        anchor_line = f"\n> 锚点: {anchor.strip()}\n" if anchor and anchor.strip() else ""
        block = f"## [{_now_iso()}] {author}{anchor_line}\n{content}\n"
        new_text = (base.rstrip() + "\n\n" + block) if base.strip() else block
        _atomic_write(self.comments_file_path(material_id), new_text)
        return new_text

    def _resolve_declared_file_path(self, file_relpath: str) -> Path:
        path = Path(file_relpath)
        if path.is_absolute():
            return path
        root = self.root.resolve()
        candidate = (self.root / path).resolve()
        if not _is_relative_to(candidate, root):
            raise ValueError("file_relpath must stay under reviewstage root")
        return candidate

    def _prepare_declared_file(self, file_relpath: str, inline_content: str | None) -> None:
        path = self._resolve_declared_file_path(file_relpath)
        if inline_content is None:
            if not path.is_file():
                raise ValueError(f"file_relpath does not exist: {file_relpath}")
            return
        if Path(file_relpath).is_absolute():
            raise ValueError("inline_content cannot write to absolute file_relpath")
        _atomic_write(path, inline_content)

    def _read_declared_file_text(self, file_relpath: str, *, cap: int = 8_000_000) -> str | None:
        """Read text material for structural checks, including self-contained HTML reports.

        The former 120 KB cap routinely stopped inside the first embedded base64 image,
        hiding later localhost or sibling-file dependencies from submit-time validation.
        Eight MB covers normal review reports while keeping malformed/huge inputs bounded.
        """
        path = self._resolve_declared_file_path(file_relpath)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return text[:cap]

    def validate_attachment_policy(
        self,
        *,
        kind: MaterialKind | str,
        links: Mapping[str, Any] | None,
        extra: Mapping[str, Any] | None = None,
        review_context: ReviewContext | Mapping[str, Any] | None = None,
    ) -> Material | None:
        """Validate internal image nodes without allowing naked image reviews.

        A structured ``aigc-image`` candidate may be registered before its
        comparison report because the report needs canonical candidate IDs.
        It remains internal and is related from the report immediately after
        registration.  Every ordinary image still requires an existing parent.
        """

        kind_text = normalize_review_kind(kind, self.format_registry)
        if kind_text not in ATTACHMENT_ONLY_REVIEW_KINDS:
            return None

        parent_id = (links or {}).get("parent")
        if not isinstance(parent_id, str) or not parent_id.strip():
            if is_structured_aigc_candidate_registration(
                kind=kind_text,
                extra=extra,
                review_context=review_context,
            ):
                return None
            raise ValueError(
                f"{kind_text} 仅可作为报告附件，不允许顶层单图送审。"
                "请先提交含文字说明和对照关系的报告，再用 "
                "`--attachment-of <report_material_id>`（或 links.parent）挂载；"
                "能做成自包含 HTML 时，优先直接把图片嵌入报告。"
            )
        parent_id = parent_id.strip()
        with self._lock:
            parent = self._ensure_loaded().get(parent_id)
        if parent is None:
            raise ValueError(
                f"{kind_text} 的父报告 {parent_id!r} 不存在；"
                "必须先提交报告，再挂载图片附件。"
            )
        if getattr(parent, "archived", False):
            raise ValueError(
                f"{kind_text} 的父报告 {parent_id!r} 已归档；"
                "请挂到当前有效的报告版本。"
            )
        if is_internal_review_material(parent):
            raise ValueError(
                f"{kind_text} 的 parent 必须是一份可审阅报告，"
                f"不能挂到内部记录或图片附件 {parent_id!r}。"
            )
        return parent

    def create(
        self,
        *,
        kind: MaterialKind | str,
        tier: MaterialTier | str,
        title: str,
        source_subagent_id: str | None = None,
        source_plan_id: str | None = None,
        file_relpath: str | None = None,
        inline_content: str | None = None,
        annotations_allowed: bool = True,
        extra: dict[str, Any] | None = None,
        project: str | None = None,
        track: str | None = None,
        version: int | None = None,
        version_family: str | None = None,
        subject_id: str | None = None,
        subject_type: str | None = None,
        revision: int | None = None,
        links: dict[str, Any] | None = None,
        review_context: ReviewContext | Mapping[str, Any] | None = None,
    ) -> Material:
        """新建 material 落盘 + emit 'created'.

        Material 契约链(第二期 A1): project/track/version 是强制标签, 在这里(唯一收口)
        做阻断校验(ValueError) —— HTTP 层 400、CLI 层报错都透传这里的错误信息, 不在
        routes.py / cli 各写一份规则(双权威会漂移)。
        """
        # 校验: enum 只保留内置兼容; 扩展 kind/tier 走 Format tags。
        kind_text = normalize_review_kind(kind, self.format_registry)
        tier_text = normalize_review_tier(tier, self.format_registry)
        try:
            kind_v: MaterialKind | str = MaterialKind(kind_text)
        except ValueError:
            kind_v = kind_text
        try:
            tier_v: MaterialTier | str = MaterialTier(tier_text)
        except ValueError:
            tier_v = tier_text
        if not title.strip():
            raise ValueError("title required")
        if file_relpath is None and inline_content is None:
            raise ValueError("must provide file_relpath or inline_content")

        # ── Material 契约链强制标签(A1): project / track / version ─────────
        project_text = (project or "").strip()
        if not project_text:
            raise ValueError(
                "project is required — 补 `--project <project>`(或 API/工具的 project 字段)。"
                "项目名录真源=决策库, 用 `omni decisions list` 看已有项目, "
                "或 `omni decisions record -p <project>` 立项。"
            )
        project_v = normalize_review_project(project_text)

        track_text = (track or "").strip()
        if not track_text:
            raise ValueError(
                "track is required — 补 `--track <track>`(如 信息审阅稿/交互审阅稿/工作报告)。"
            )

        if version is None:
            raise ValueError("version is required — 补 `--version <int>`(同一份稿的版本号, 从 1 起)。")
        try:
            version_int = int(version)
        except (TypeError, ValueError):
            raise ValueError(f"version must be a positive integer, got {version!r}")
        if version_int <= 0:
            raise ValueError(f"version must be a positive integer, got {version_int}")

        version_family_text = (version_family or "").strip() or title.strip()

        registered_tracks = project_registered_tracks(project_v, self.format_registry)
        required_subject_types = (
            project_subject_types(project_v, self.format_registry)
            if track_text in registered_tracks else set()
        )
        subject_id_text, subject_type_text, revision_int = _normalize_subject_identity(
            subject_id,
            subject_type,
            revision,
            required_types=required_subject_types,
        )

        links_v: dict[str, Any] = dict(links or {})
        allowed_link_keys = {"parent", "supersedes", "related"}
        for key, value in links_v.items():
            if key not in allowed_link_keys:
                raise ValueError(
                    f"links 里出现未知键 {key!r} — 只允许 {sorted(allowed_link_keys)}。"
                )
            if isinstance(value, str):
                continue
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                continue
            raise ValueError(
                f"links[{key!r}] 的值必须是字符串或字符串列表(material id), 收到 {type(value).__name__}。"
            )

        extra_v = dict(extra or {})
        structured_aigc_candidate = is_structured_aigc_candidate_registration(
            kind=kind_text,
            extra=extra_v,
            review_context=review_context,
        )
        self.validate_attachment_policy(
            kind=kind_text,
            links=links_v,
            extra=extra_v,
            review_context=review_context,
        )

        if file_relpath is not None:
            self._prepare_declared_file(file_relpath, inline_content)
        if kind_text in ATTACHMENT_ONLY_REVIEW_KINDS:
            extra_v["reviewstage_visibility"] = "internal"
            extra_v["reviewstage_role"] = (
                "aigc_candidate"
                if structured_aigc_candidate
                else "report_attachment"
            )
        # 双权威禁令(A1): extra 里不许塞 project/track/version/version_family —— 这些是
        # 正式字段, 塞进 extra 会造成"两套真源"(一处走 store 校验, 一处绕过)。
        reserved_extra_keys = {
            "project", "track", "version", "version_family",
            "subject_id", "subject_type", "revision", "review_context",
            # Server-derived trust records. Accepting either from producer/Agent
            # input would let a submit payload fabricate its own push proof.
            "remote_preflight", "remote_verification",
        }
        leaked = reserved_extra_keys & set(extra_v.keys())
        if leaked:
            raise ValueError(
                f"extra 里不能塞 {sorted(leaked)} — 这些是正式字段, 请用 create() 的同名关键字参数"
                "(或 CLI 的 --project/--track/--version/--version-family), 不要塞进 extra 旁路。"
            )
        # 阻断校验: kind 决定渲染器, 与内容格式不匹配直接拒(如 .md 提 static-report,
        # 会被 HtmlMaterialView 当 HTML 渲染 — 2026-07-02 实踩)。所有提交入口都过这里。
        compat_error = validate_kind_file_compat(
            kind=kind_text,
            inline_content=inline_content,
            file_relpath=file_relpath,
            extra=extra_v,
        )
        if compat_error:
            raise ValueError(compat_error)
        validation_content = inline_content
        if validation_content is None and file_relpath is not None and kind_text in TEXT_KINDS:
            validation_content = self._read_declared_file_text(file_relpath)
        structure_warnings = validate_material_structure(
            kind=kind_text,
            title=title.strip(),
            inline_content=validation_content,
            file_relpath=file_relpath,
            extra=extra_v,
        )
        # 轨道软校验(决策树=具象管线, 先软后硬): 项目属某注册域, 且 track 不在册
        # (不是该域任一层级名, 也不是 "<project>/..." 项目前缀轨道)→ 追加一条警告, 绝不阻断。
        # 项目不属任何注册域则完全跳过(存量不惊动)。
        track_warning = _track_registration_warning(project_v, track_text, self.format_registry)
        if track_warning:
            structure_warnings.append(track_warning)
        if structure_warnings:
            extra_v["structure_warnings"] = structure_warnings

        review_resolution = resolve_review_submission(
            ReviewSubmissionIntent(
                kind=kind_text,
                tier=tier_text,
                title=title.strip(),
                source_plan_id=source_plan_id or "",
                source_subagent_id=source_subagent_id or "",
                file_path=file_relpath or "",
                # File-backed Markdown/HTML already has one validated read above;
                # pass that exact body into submission preflight so canonical
                # embed placeholders can be checked against typed references.
                inline_content=validation_content or "",
                data_schema_id=str(extra_v.get("data_schema_id") or ""),
                extra=extra_v,
                project=project_v,
                track=track_text,
                version=version_int,
                version_family=version_family_text,
                subject_id=subject_id_text,
                subject_type=subject_type_text,
                revision=revision_int,
                links=links_v,
                review_context=review_context,
            ),
            registry=self.format_registry,
        )
        if review_resolution.blocked:
            blocking_messages = [
                item.message
                for item in review_resolution.context.reminders
                if (
                    item.severity.value
                    if isinstance(item.severity, ReminderSeverity)
                    else str(item.severity)
                )
                == ReminderSeverity.blocking.value
            ]
            raise ValueError(
                "review submission preflight blocked: "
                + " | ".join(blocking_messages)
            )

        with self._lock:
            self._ensure_loaded()
            mid = _new_id("mat")
            now = _now_iso()
            history = [{"event": "created", "at": now}]
            if structure_warnings:
                history.append({
                    "event": "structure_warning",
                    "at": now,
                    "count": len(structure_warnings),
                    "warnings": structure_warnings,
                })
            m = Material(
                id=mid,
                kind=kind_v,
                tier=tier_v,
                title=title.strip(),
                status=MaterialStatus.pending,
                source_subagent_id=source_subagent_id,
                source_plan_id=source_plan_id,
                file_relpath=file_relpath,
                inline_content=inline_content,
                annotations_allowed=annotations_allowed,
                created_at=now,
                updated_at=now,
                history=history,
                extra=extra_v,
                review_context=review_resolution.context,
                project=project_v,
                track=track_text,
                version=version_int,
                version_family=version_family_text,
                subject_id=subject_id_text,
                subject_type=subject_type_text,
                revision=revision_int,
                links=links_v,
            )
            self._cache[mid] = m
            self._persist(m)
            self._notify("created", m)
            _log.info("MaterialStore created: id=%s kind=%s tier=%s", mid, kind_text, tier_text)
            event_payload = m.to_dict()
        publish_material_event(
            REVIEW_MATERIAL.id,
            event_payload,
            source="boss_sight.reviewstage",
            tags=review_material_tags(kind_text, tier_text),
        )
        # 封面生成不能占用提交请求，也不能因 Playwright/网络暂不可用而让材料
        # 提交失败。这里只写一个可恢复、按材料版本去重的持久任务；Dashboard
        # 后台 worker 异步消费。
        try:
            from .preview_queue import enqueue_preview

            enqueue_preview(m, self.root)
        except Exception:  # noqa: BLE001
            _log.exception("Failed to enqueue preview for material %s", m.id)
        return m

    def get(self, material_id: str) -> Material | None:
        with self._lock:
            cache = self._ensure_loaded()
            self._refresh_material_locked(material_id)
            return cache.get(material_id)

    def list(
        self,
        *,
        status: MaterialStatus | str | None = None,
        tier: MaterialTier | str | None = None,
        plan_id: str | None = None,
        subagent_id: str | None = None,
        pushed_only: bool = False,
        include_archived: bool = False,
        include_internal: bool = False,
        project: str | None = None,
        track: str | None = None,
        subject_id: str | None = None,
        revision: int | None = None,
    ) -> list[Material]:
        with self._lock:
            items = list(self._ensure_loaded().values())
        # 默认不返回已软归档的材料; 只有"已归档"视图显式 include_archived 才带上。
        if not include_archived:
            items = [m for m in items if not getattr(m, "archived", False)]
        # 内部治理件不是普通审阅交付物。避免决策账、自动巡检记录混入用户审阅队列；
        # 专用治理流水线仍可显式 include_internal=True 读取并处理。
        if not include_internal:
            items = [m for m in items if not is_internal_review_material(m)]
        if status is not None:
            sv = status.value if isinstance(status, MaterialStatus) else status
            items = [m for m in items if (m.status.value if isinstance(m.status, MaterialStatus) else m.status) == sv]
        if tier is not None:
            tv = tier.value if isinstance(tier, MaterialTier) else tier
            items = [m for m in items if (m.tier.value if isinstance(m.tier, MaterialTier) else m.tier) == tv]
        if plan_id is not None:
            items = [m for m in items if m.source_plan_id == plan_id]
        if subagent_id is not None:
            items = [m for m in items if m.source_subagent_id == subagent_id]
        if pushed_only:
            items = [m for m in items if m.pushed_to_user]
        if project is not None:
            items = [m for m in items if getattr(m, "project", "") == project]
        if track is not None:
            items = [m for m in items if getattr(m, "track", "") == track]
        if subject_id is not None:
            items = [m for m in items if getattr(m, "subject_id", "") == subject_id]
        if revision is not None:
            items = [m for m in items if getattr(m, "revision", None) == revision]
        items.sort(key=lambda m: m.created_at, reverse=True)
        return items

    def set_verdict(
        self,
        material_id: str,
        verdict: MaterialStatus | str,
        *,
        by: str = "user",
        reason: str = "",
    ) -> Material:
        """status 转移. 'rejected' / 'blocked' / 'accepted' / 重置回 'pending'.

        history 记一条 audit. emit 'verdict_changed'.
        """
        v = MaterialStatus(verdict) if not isinstance(verdict, MaterialStatus) else verdict
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            old_status = m.status.value if isinstance(m.status, MaterialStatus) else m.status
            m.status = v
            m.history.append({
                "event": "verdict",
                "from": old_status, "to": v.value,
                "by": by, "reason": reason, "at": _now_iso(),
            })
            self._persist(m)
            self._notify("verdict_changed", m)
            return m

    def patch_extra(self, material_id: str, patch: dict[str, Any], *, by: str = "system") -> Material:
        """浅合并更新 extra(如候选流水线写回后盖 applied 标记)。history 记 audit。"""
        patch_keys = set((patch or {}).keys())
        if "remote_preflight" in patch_keys and by not in _REMOTE_PREFLIGHT_WRITERS:
            raise PermissionError(
                "remote_preflight is server-derived and cannot be patched by "
                f"{by!r}"
            )
        if "remote_verification" in patch_keys and by not in _REMOTE_VERIFICATION_WRITERS:
            raise PermissionError(
                "remote_verification requires the independent remote verification service"
            )
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            m.extra = {**(m.extra or {}), **(patch or {})}
            m.history.append({
                "event": "extra_patched",
                "keys": sorted((patch or {}).keys()),
                "by": by, "at": _now_iso(),
            })
            self._persist(m)
            self._notify("updated", m)
            return m

    def set_structure(
        self,
        material_id: str,
        *,
        subject_id: str,
        subject_type: str,
        revision: int,
        track: str | None = None,
        by: str = "system",
    ) -> Material:
        """登记或修正内容资产层级；用于存量迁移，保留材料 id、评论与裁决历史。"""
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            next_track = (track or m.track or "").strip()
            registered_tracks = project_registered_tracks(m.project, self.format_registry)
            required_types = (
                project_subject_types(m.project, self.format_registry)
                if next_track in registered_tracks else set()
            )
            sid, stype, rev = _normalize_subject_identity(
                subject_id, subject_type, revision, required_types=required_types,
            )
            before = {
                "subject_id": m.subject_id,
                "subject_type": m.subject_type,
                "revision": m.revision,
                "track": m.track,
            }
            m.subject_id = sid
            m.subject_type = stype
            m.revision = rev
            if track is not None:
                m.track = next_track
            m.updated_at = _now_iso()
            m.history.append({
                "event": "structure_registered",
                "before": before,
                "after": {
                    "subject_id": sid,
                    "subject_type": stype,
                    "revision": rev,
                    "track": m.track,
                },
                "by": by,
                "at": m.updated_at,
            })
            self._persist(m)
            self._notify("updated", m)
            return m

    def set_archived(self, material_id: str, archived: bool, *, by: str = "user") -> Material:
        """软归档/还原，不删文件；归档会撤销当前推送状态。

        还原材料不能复活一个旧的、可能已失效的远端推送证明；如需再次推送，必须
        重新经过实时远端门禁。
        """
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            cleared_push = bool(archived and m.pushed_to_user)
            if cleared_push:
                m.pushed_to_user = False
                m.pushed_reason = None
                m.pushed_at = None
            m.archived = bool(archived)
            m.history.append({
                "event": "archived" if archived else "unarchived",
                "by": by,
                "at": _now_iso(),
                "cleared_push": cleared_push,
            })
            self._persist(m)
            self._notify("updated", m)
            return m

    def add_comment(
        self,
        material_id: str,
        *,
        content: str,
        author: str = "user",
        target: dict[str, Any] | None = None,
    ) -> Comment:
        """已冻结(2026-07-04,双权威 A8): 评论唯一真源=authored store 的 Note,
        Material.comments[] 只是历史+水合视图。生产代码禁止新增本方法调用
        (studio_authority_audit A8 机械核查零调用方);保留仅为旧数据兼容与测试。"""
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            now = _now_iso()
            c = Comment(
                id=_new_id("cmt"),
                content=content.strip(),
                author=author,
                target=target or {},
                created_at=now,
                feedback_status="saved",  # 只保存, 不发总控; 用户显式发送时再转 delivered
                feedback_history=[{"status": "saved", "by": author, "at": now}],
            )
            m.comments.append(c)
            mentions = c.target.get("mentions") if isinstance(c.target, dict) else None
            mention_count = len(mentions) if isinstance(mentions, list) else 0
            m.history.append({
                "event": "comment",
                "by": author,
                "at": c.created_at,
                "mention_count": mention_count,
                "comment_id": c.id,
                "feedback_status": c.feedback_status,
            })
            self._persist(m)
            self._notify("comment_added", m)
            return c

    def record_authored_comment(
        self,
        material_id: str,
        *,
        note_id: str,
        author: str,
        created_at: str,
        mention_count: int = 0,
        feedback_status: str = "saved",
    ) -> Material:
        """Record the audit edge for a canonical authored-Note comment.

        Authored Notes remain the only comment objects.  This preserves the
        material history and event notification without repopulating the
        deprecated ``Material.comments`` array.
        """

        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            m.history.append({
                "event": "comment",
                "by": author,
                "at": created_at,
                "mention_count": max(0, int(mention_count)),
                "comment_id": note_id,
                "feedback_status": feedback_status,
                "source": "authored_note",
            })
            self._persist(m)
            self._notify("comment_added", m)
            return m

    def set_comment_feedback(
        self,
        material_id: str,
        comment_id: str,
        *,
        status: str,
        by: str = "controller",
        note: str = "",
    ) -> Comment:
        if status not in COMMENT_FEEDBACK_STATUSES:
            raise ValueError(f"invalid comment feedback status: {status}")
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            comment = next((c for c in m.comments if c.id == comment_id), None)
            if comment is None:
                raise KeyError(comment_id)
            old = comment.feedback_status or "delivered"
            now = _now_iso()
            comment.feedback_status = status
            comment.feedback_history.append({
                "from": old,
                "status": status,
                "by": by,
                "note": note,
                "at": now,
            })
            m.history.append({
                "event": "comment_feedback",
                "comment_id": comment_id,
                "from": old,
                "to": status,
                "by": by,
                "note": note,
                "at": now,
            })
            self._persist(m)
            self._notify("updated", m)
            return comment

    def record_authored_comment_feedback(
        self,
        material_id: str,
        *,
        note_id: str,
        from_status: str,
        to_status: str,
        by: str,
        updated_at: str,
        note: str = "",
    ) -> Material:
        """Record feedback transitions for a canonical authored Note."""

        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            m.history.append({
                "event": "comment_feedback",
                "comment_id": note_id,
                "from": from_status,
                "to": to_status,
                "by": by,
                "note": note,
                "at": updated_at,
                "source": "authored_note",
            })
            self._persist(m)
            self._notify("updated", m)
            return m

    def edit_comment(
        self,
        material_id: str,
        comment_id: str,
        *,
        content: str,
        by: str = "user",
    ) -> Comment:
        """改一条已存评论的正文。不改 feedback_status(saved 仍 saved), 不唤起总控。"""
        content = (content or "").strip()
        if not content:
            raise ValueError("comment content is empty")
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            comment = next((c for c in m.comments if c.id == comment_id), None)
            if comment is None:
                raise KeyError(comment_id)
            now = _now_iso()
            old = comment.content
            comment.content = content
            comment.feedback_history.append({
                "event": "edit", "by": by, "at": now,
                "old_len": len(old), "new_len": len(content),
            })
            m.history.append({
                "event": "comment_edit", "comment_id": comment_id, "by": by, "at": now,
            })
            self._persist(m)
            self._notify("updated", m)  # 非 comment_added: 不触发任何总控路径
            return comment

    def add_annotation(
        self,
        material_id: str,
        *,
        content: str,
        kind: AnnotationKind | str = AnnotationKind.ai,
        author: str = "controller",
        target: dict[str, Any] | None = None,
    ) -> Annotation:
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            if not m.annotations_allowed:
                raise PermissionError(
                    f"material {material_id} has annotations_allowed=False; "
                    "this material kind (e.g. 小说) does not accept annotations (§4.5)"
                )
            k = AnnotationKind(kind) if not isinstance(kind, AnnotationKind) else kind
            a = Annotation(
                id=_new_id("ann"),
                kind=k,
                content=content.strip(),
                target=target or {},
                created_at=_now_iso(),
                author=author,
            )
            m.annotations.append(a)
            m.history.append({"event": "annotation", "by": author, "at": a.created_at})
            self._persist(m)
            self._notify("annotation_added", m)
            return a

    def mark_pushed(self, material_id: str, *, reason: str) -> Material:
        """块 4 R7: 打推送标记 + emit ``pushed``。

        2026-07-25 用户裁定：远端验证硬门禁去除，降级为 advisory。预检/回执
        blocker 仍计算并记入 history（``push_gate_advisory``），但不再阻止推送。
        """
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            # Push is a state transition, not a read receipt. Repeating it must
            # not rewrite history or emit another ``pushed`` WS event; otherwise
            # a client refresh can create an event/reload feedback loop.
            if m.pushed_to_user:
                return m
            from .remote_preflight import remote_push_blockers

            advisory_blockers = remote_push_blockers(
                material_id=m.id,
                remote_preflight=(m.extra or {}).get("remote_preflight"),
                remote_verification=(m.extra or {}).get("remote_verification"),
            )
            m.pushed_to_user = True
            m.pushed_reason = reason
            m.pushed_at = _now_iso()
            m.history.append({"event": "pushed", "reason": reason, "at": m.pushed_at})
            if advisory_blockers:
                m.history.append({
                    "event": "push_gate_advisory",
                    "blockers": advisory_blockers,
                    "note": "远程验证未通过；按 2026-07-25 裁定门禁为提示级，仍已推送",
                    "at": m.pushed_at,
                })
            self._persist(m)
            self._notify("pushed", m)
            return m

    def adjust_tier(
        self, material_id: str, *, new_tier: MaterialTier | str, by: str = "user",
    ) -> Material:
        """§4.6.5 准则可调: 用户/总控调 material 分级. history 留 audit."""
        tier_text = normalize_review_tier(new_tier, self.format_registry)
        try:
            nt: MaterialTier | str = MaterialTier(tier_text)
        except ValueError:
            nt = tier_text
        with self._lock:
            m = self.get(material_id)
            if m is None:
                raise KeyError(material_id)
            old = m.tier.value if isinstance(m.tier, MaterialTier) else m.tier
            m.tier = nt
            m.history.append({
                "event": "tier_change", "from": old, "to": tier_text,
                "by": by, "at": _now_iso(),
            })
            self._persist(m)
            self._notify("updated", m)
            return m

    def delete(self, material_id: str) -> bool:
        with self._lock:
            m = self.get(material_id)
            if m is None:
                return False
            (self.root / f"{material_id}.json").unlink(missing_ok=True)
            self._file_sigs.pop(f"{material_id}.json", None)
            # 也删 file 内容
            if m.file_relpath:
                f = self.resolve_file_path(m)
                if f is not None and f.is_file():
                    f.unlink(missing_ok=True)
            self._cache.pop(material_id, None)
            self._notify("deleted", m)
            return True

    # ── 阻断查询 (块 4 R8 联通块 3 R8 硬 guard) ───────────────────────

    def has_unaccepted_mandatory(self, plan_id: str | None) -> list[Material]:
        """块 4 R8: 检查某 plan 有没有未通过的 mandatory material.

        spawn_subagent 启动前调这个. 返回有阻断的 material 列表 (空则可放行).
        """
        if not plan_id:
            return []
        blockers: list[Material] = []
        for m in self.list(plan_id=plan_id, tier=MaterialTier.mandatory):
            st = m.status.value if isinstance(m.status, MaterialStatus) else m.status
            if st in {"pending", "rejected", "blocked"}:
                blockers.append(m)
        return blockers

    # ── 写 file (image / html / md) ──────────────────────────────────

    def stage_file_from_path(self, src: Path | str, *, suggested_ext: str = "") -> str:
        """从工作区某文件拷贝到 files/<id>.<ext>, 返回 file_relpath (相对 store root)."""
        src_path = Path(src)
        if not src_path.is_file():
            raise FileNotFoundError(str(src_path))
        ext = suggested_ext or src_path.suffix or ".bin"
        if not ext.startswith("."):
            ext = "." + ext
        fname = f"{_new_id('file')}{ext}"
        dest = self.files_dir / fname
        shutil.copy2(src_path, dest)
        return f"{self.SUBDIR_FILES}/{fname}"

    def stage_file_from_bytes(self, data: bytes, *, ext: str) -> str:
        """直接把字节流落到 files/<id>.<ext>."""
        if not ext.startswith("."):
            ext = "." + ext
        fname = f"{_new_id('file')}{ext}"
        dest = self.files_dir / fname
        dest.write_bytes(data)
        return f"{self.SUBDIR_FILES}/{fname}"

    def resolve_file_path(self, m: Material) -> Path | None:
        """拿 material 文件绝对路径."""
        if not m.file_relpath:
            return None
        p = Path(m.file_relpath)
        if p.is_absolute():
            return p if p.is_file() else None
        try:
            candidate = self._resolve_declared_file_path(m.file_relpath)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

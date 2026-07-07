# [OMNI] origin=claude-code domain=runtime/buses ts=2026-04-23T00:00:00Z type=infrastructure
# [OMNI] material_id="material:runtime.buses.human_bus.approval_inbox.py"
"""HumanBus · 人类审批统一入口.

解决 L2 被各层"需要人类"事件淹没的问题 (2026-04-23 用户). 三类分流:

| kind             | 默认行为                      | 用例                           |
|------------------|------------------------------|-------------------------------|
| auto_continue    | 暂存 + 按默认值继续            | LLM 返回可接受 / 低风险 fallback |
| core_diagnose    | 入 inbox 标 core 处理            | 核心层可修范围 (A4 对接 self_repair) |
| human_blocking   | 入 inbox 等人类 resolve         | 不在可修集 / 高风险 / 语义歧义    |

持久化: SQLite `data/runtime/buses/human_inbox.db`.
CLI 入口: `omni human inbox` / `omni human resolve <id> <answer>` (见 cli/commands/human.py).

**多身份扩展** (2026-04-23 Phase B.2 · 为 config_service 面向同事collab platform审批):
  - 每个问题带 `target = HumanTarget(kind, id)` 二元组
  - 默认 `HumanTarget("l2_claude_code", "")` (向后兼容, 老数据 migration 默认此值)
  - 可注册 `NotifierProtocol` 按 target_kind 分发通知 (例: collab platform notifier 发卡给同事)

**设计决策**:
  - `ask()` 不阻塞 (避免卡死 agent); 立即返回 HumanQuestion 对象, status 反映当前状态
  - auto_continue 直接给 default, 不入 inbox (只审计一条 default_applied)
  - core_diagnose / human_blocking 入 inbox, 调用方可选 poll 或丢手
  - 阻塞等待用 `wait_for_resolve(qid, timeout)` 显式调用
  - notifier 失败吞异常 (不让通知挂了 break 核心 inbox 流程); 审计一条 notifier_error
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from omnicompany.runtime.buses.base import ServiceBus

logger = logging.getLogger(__name__)


class HumanKind(str, Enum):
    AUTO_CONTINUE = "auto_continue"
    CORE_DIAGNOSE = "core_diagnose"
    HUMAN_BLOCKING = "human_blocking"


class QuestionStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    DEFAULT_APPLIED = "default_applied"
    EXPIRED = "expired"


# ═══════════════════════════════════════════════════════════════════
# 多身份 target 模型 (Phase B.2)
# ═══════════════════════════════════════════════════════════════════


# 常用 target_kind 常量 (约定俗成 · 可扩 · 非 Enum 让外部自定义 kind)
TARGET_L2_CLAUDE_CODE = "l2_claude_code"       # L2 Claude Code (现有默认)
TARGET_COLLEAGUE_FEISHU = "colleague_feishu"   # collab platform同事 (id = open_id)
TARGET_CORE_SELF_REPAIR = "core_self_repair"   # 核心层 A4 self_repair 消费
TARGET_ANY_HUMAN = "any_human"                 # 任意能看到 inbox 的人


@dataclass(frozen=True)
class HumanTarget:
    """审批目标 · `kind` 是类别 (路由 notifier), `id` 是具体身份 (如collab platform open_id).

    设计为 frozen dataclass 而非 tuple, 是因为:
      - 将来可能扩字段 (如 email / escalation / timeout_hours override)
      - 可 hash 进 dict (notifier 注册表以 kind 为 key)
      - dataclass 不影响 SQLite 落盘 (仍用两列 target_kind / target_id)

    Example:
      HumanTarget(kind=TARGET_COLLEAGUE_FEISHU, id="ou-abc123")
      HumanTarget(kind=TARGET_L2_CLAUDE_CODE)  # id 默认 ""
    """
    kind: str = TARGET_L2_CLAUDE_CODE
    id: str = ""


@runtime_checkable
class NotifierProtocol(Protocol):
    """通知者协议 · HumanBus 收到新问题 / 问题被 resolved 时回调.

    实现是 sync 函数 (HumanBus 内部在锁外调, 不阻塞 inbox DB 操作).
    实现应**自己处理异常**; HumanBus 侧兜底 try/except 吞错 + 审计 notifier_error.

    典型实现: collab platform机器人 notifier — on_question 发卡片给同事, on_resolved 发感谢+归档.
    放在 package 内 (例: `packages/services/config_service/notifiers/feishu.py`),
    不污染 `runtime/buses/` 基础设施层.
    """

    def on_question(self, q: "HumanQuestion") -> None:
        """新问题入 inbox 时触发 (auto_continue 不触发 · 它没进 inbox)."""
        ...

    def on_resolved(self, q: "HumanQuestion") -> None:
        """问题被 resolve() / default_applied 时触发. 可选实现 (可 no-op)."""
        ...


@dataclass
class HumanQuestion:
    id: str
    kind: HumanKind
    question: str
    context: dict = field(default_factory=dict)
    default_answer: str | None = None
    status: QuestionStatus = QuestionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    answer: str | None = None
    resolver: str | None = None
    source: str = ""  # "absorption" / "doctor" / ... 产生者标注
    target: HumanTarget = field(default_factory=HumanTarget)  # B.2 多身份

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "question": self.question,
            "context": self.context,
            "default_answer": self.default_answer,
            "status": self.status.value,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "answer": self.answer,
            "resolver": self.resolver,
            "source": self.source,
            "target_kind": self.target.kind,
            "target_id": self.target.id,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "HumanQuestion":
        # target_* 列可能为 NULL (老数据 migration 前) · 用 .get 模式防御
        target_kind = _row_get(row, "target_kind") or TARGET_L2_CLAUDE_CODE
        target_id = _row_get(row, "target_id") or ""
        return cls(
            id=row["id"],
            kind=HumanKind(row["kind"]),
            question=row["question"],
            context=json.loads(row["context"]) if row["context"] else {},
            default_answer=row["default_answer"],
            status=QuestionStatus(row["status"]),
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            answer=row["answer"],
            resolver=row["resolver"],
            source=row["source"] or "",
            target=HumanTarget(kind=target_kind, id=target_id),
        )


def _row_get(row: sqlite3.Row, key: str) -> Any:
    """sqlite3.Row 按 key 取值, key 不存在返 None (不抛 IndexError)."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


_INBOX_REPO_ROOT_SEARCH_LIMIT = 10


def _find_repo_root_from(start: Path) -> Path:
    """从 start 起向上走, 找含 src/omnicompany 目录的祖先(仓根标志).

    最多向上走 _INBOX_REPO_ROOT_SEARCH_LIMIT 层; 找不到时抛 RuntimeError(绝不
    静默回退到调用方传入的起点或当前目录拼路径).
    """
    cursor = start
    for _ in range(_INBOX_REPO_ROOT_SEARCH_LIMIT):
        if (cursor / "src" / "omnicompany").is_dir():
            return cursor
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    raise RuntimeError(
        "无法定位仓库根: 从模块路径 "
        f"{start} 向上最多 {_INBOX_REPO_ROOT_SEARCH_LIMIT} 层均未找到 "
        "src/omnicompany 标志目录。请检查是否安装/复制方式破坏了仓库结构"
        "(模块被移出了 src/omnicompany 之下)。"
    )


def _resolve_inbox_path() -> Path:
    """SQLite inbox 路径. 走和 audit log 同级的 data/runtime/buses/.

    2026-07-03 批4返修 "仓库外启动即漂移"缺陷修复:
      旧实现从 Path.cwd() 向上找仓根标志, 若进程从仓库外目录(如 C:\\ 或用户
      主目录)启动, 向上永远走不到标志, 会静默退回到用最后的 cursor 拼路径,
      产生仓库外的错误数据库文件——不报错也不提示。
      现改为从模块自身 __file__ 向上找仓根标志(模块文件永远在仓库内, 这个锚点
      不因进程 cwd 而丢失); 找不到仓根标志时不再静默回退, 改为抛 RuntimeError。
    """
    import os

    override = os.environ.get("OMNI_HUMAN_INBOX_PATH")
    if override:
        return Path(override)
    repo_root = _find_repo_root_from(Path(__file__).resolve())
    return repo_root / "data" / "runtime" / "buses" / "human_inbox.db"


# Schema 版本通过 PRAGMA user_version 管理:
#   v0: 初始 (无 user_version 字段)
#   v1: 加 target_kind / target_id 列 (Phase B.2 多身份, 2026-04-23)
# 新表直接用 v1 完整 schema; 老表检测到 user_version<1 走 _migrate_v0_to_v1.
_SCHEMA_CURRENT_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    question        TEXT NOT NULL,
    context         TEXT,
    default_answer  TEXT,
    status          TEXT NOT NULL,
    created_at      REAL NOT NULL,
    resolved_at     REAL,
    answer          TEXT,
    resolver        TEXT,
    source          TEXT,
    target_kind     TEXT DEFAULT 'l2_claude_code',
    target_id       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_kind ON questions(kind);
CREATE INDEX IF NOT EXISTS idx_created ON questions(created_at);
CREATE INDEX IF NOT EXISTS idx_target_kind ON questions(target_kind);
"""


class HumanBus(ServiceBus):
    """人类审批总线.

    典型用法:
      bus = HumanBus()

      # 1. auto_continue: 立即拿 default
      q = bus.ask("LLM returned X, accept?", kind=HumanKind.AUTO_CONTINUE, default="yes")
      assert q.status == QuestionStatus.DEFAULT_APPLIED
      assert q.answer == "yes"

      # 2. human_blocking: 入 inbox, 返回 pending 对象
      q = bus.ask("Unknown schema change, proceed?", kind=HumanKind.HUMAN_BLOCKING,
                  source="absorption", context={"file": "..."})
      # L2 查看: bus.inbox(status=QuestionStatus.PENDING)
      # L1 回答: bus.resolve(q.id, "no")

      # 3. core_diagnose: 入 inbox 标 core (A4 阶段 self_repair 消费)
      q = bus.ask("DB lock timeout", kind=HumanKind.CORE_DIAGNOSE, source="team_runner")
    """

    bus_name = "human"

    def __init__(self, audit_log_path=None, inbox_path: Path | None = None, *, workspace=None):
        super().__init__(audit_log_path=audit_log_path, workspace=workspace)
        self._inbox_path = inbox_path or _resolve_inbox_path()
        self._inbox_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_lock = threading.Lock()
        # notifier 注册表 · key=target_kind · value=NotifierProtocol 实例
        # 用 list[NotifierProtocol] 让同一 kind 可多 notifier (未来扩)
        self._notifiers: dict[str, list[NotifierProtocol]] = {}
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            # 1. 拉当前 schema 版本
            version = conn.execute("PRAGMA user_version").fetchone()[0]

            # 2. 新表或已是最新版本: 直接跑 _SCHEMA (CREATE IF NOT EXISTS)
            if version >= _SCHEMA_CURRENT_VERSION:
                conn.executescript(_SCHEMA)
                return

            # 3. 老表 migration: 检测 questions 表是否存在
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='questions'"
            ).fetchone()

            if existing is None:
                # 新库 · 直接建最新 schema
                conn.executescript(_SCHEMA)
            else:
                # v0 → v1 migration
                self._migrate_v0_to_v1(conn)
                # 确保索引到位 (新加的 idx_target_kind 在老库没有)
                conn.executescript(_SCHEMA)

            # 4. 标记 schema 版本
            conn.execute(f"PRAGMA user_version = {_SCHEMA_CURRENT_VERSION}")

    @staticmethod
    def _migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
        """v0 → v1: 为 questions 表加 target_kind / target_id 两列.

        SQLite 的 ALTER TABLE ADD COLUMN 是安全的 O(1) 元数据操作.
        老数据 target_kind 由 column DEFAULT 保持 'l2_claude_code' 兼容 (等同旧行为).
        """
        cols = {row[1] for row in conn.execute("PRAGMA table_info(questions)").fetchall()}
        if "target_kind" not in cols:
            conn.execute(
                "ALTER TABLE questions ADD COLUMN target_kind TEXT DEFAULT 'l2_claude_code'"
            )
        if "target_id" not in cols:
            conn.execute("ALTER TABLE questions ADD COLUMN target_id TEXT DEFAULT ''")
        logger.info("HumanBus: migrated inbox schema v0 → v1 (added target_kind / target_id)")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._inbox_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Notifier 管理 (Phase B.2) ──────────────────────────────────

    def register_notifier(self, target_kind: str, notifier: NotifierProtocol) -> None:
        """按 target_kind 注册 notifier. 同 kind 可注册多个 (按注册顺序调).

        Args:
          target_kind: 如 TARGET_COLLEAGUE_FEISHU
          notifier: 实现 NotifierProtocol 的对象 (ducktype, 不强制继承)
        """
        self._notifiers.setdefault(target_kind, []).append(notifier)
        logger.info(
            "HumanBus: registered notifier for target_kind='%s' (%s)",
            target_kind,
            type(notifier).__name__,
        )

    def _dispatch_notify(self, q: HumanQuestion, event: str) -> None:
        """调 q.target.kind 下所有 notifier 的对应 event 方法. 吞异常只审计."""
        notifiers = self._notifiers.get(q.target.kind)
        if not notifiers:
            return
        for n in notifiers:
            method = getattr(n, event, None)
            if method is None:
                continue
            try:
                method(q)
            except Exception as exc:
                logger.warning(
                    "HumanBus: notifier %s.%s failed for %s: %s",
                    type(n).__name__, event, q.id, exc,
                )
                self._audit(
                    "notifier_error",
                    {
                        "id": q.id,
                        "target_kind": q.target.kind,
                        "event": event,
                        "notifier": type(n).__name__,
                        "error": str(exc),
                    },
                    ok=False,
                )

    # ── 主接口 ──────────────────────────────────────────────────────

    def ask(
        self,
        question: str,
        *,
        kind: HumanKind | str,
        default: str | None = None,
        context: dict | None = None,
        source: str = "",
        target: HumanTarget | None = None,
    ) -> HumanQuestion:
        """提问. auto_continue 立即用 default; 其余入 inbox.

        Args:
          question: 问题正文
          kind: auto_continue / core_diagnose / human_blocking
          default: auto_continue 必须; 其他可选 (作为 pending 时的建议答案)
          context: 附加数据 (JSON 序列化存)
          source: 产生者标注 ("config_service" / "absorption" / ...)
          target: 审批目标 · 默认 HumanTarget(kind=TARGET_L2_CLAUDE_CODE, id="")
            常见值: TARGET_COLLEAGUE_FEISHU + open_id / TARGET_CORE_SELF_REPAIR

        注意: 本方法**不阻塞**. 阻塞等 resolve 用 `wait_for_resolve()`.
        """
        if isinstance(kind, str):
            kind = HumanKind(kind)
        if target is None:
            target = HumanTarget()  # 默认 TARGET_L2_CLAUDE_CODE
        qid = f"hq-{uuid.uuid4().hex[:12]}"
        now = time.time()

        if kind == HumanKind.AUTO_CONTINUE:
            if default is None:
                raise self._reject(
                    "ask",
                    "auto_continue requires default",
                    {"question": question, "source": source},
                )
            q = HumanQuestion(
                id=qid,
                kind=kind,
                question=question,
                context=context or {},
                default_answer=default,
                status=QuestionStatus.DEFAULT_APPLIED,
                created_at=now,
                resolved_at=now,
                answer=default,
                resolver="default",
                source=source,
                target=target,
            )
            self._insert(q)
            self._audit(
                "default_applied",
                {
                    "id": qid,
                    "kind": kind.value,
                    "question": question,
                    "default": default,
                    "source": source,
                    "target_kind": target.kind,
                    "target_id": target.id,
                },
            )
            # auto_continue 也触发 on_resolved (语义上就是立即"被回答")
            # on_question 不触发 (它没进 pending 状态)
            self._dispatch_notify(q, "on_resolved")
            return q

        q = HumanQuestion(
            id=qid,
            kind=kind,
            question=question,
            context=context or {},
            default_answer=default,
            status=QuestionStatus.PENDING,
            created_at=now,
            source=source,
            target=target,
        )
        self._insert(q)
        self._audit(
            "question",
            {
                "id": qid,
                "kind": kind.value,
                "question": question,
                "default": default,
                "source": source,
                "target_kind": target.kind,
                "target_id": target.id,
            },
        )
        self._dispatch_notify(q, "on_question")
        return q

    def _insert(self, q: HumanQuestion) -> None:
        with self._db_lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO questions
                   (id, kind, question, context, default_answer, status,
                    created_at, resolved_at, answer, resolver, source,
                    target_kind, target_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    q.id,
                    q.kind.value,
                    q.question,
                    json.dumps(q.context, ensure_ascii=False),
                    q.default_answer,
                    q.status.value,
                    q.created_at,
                    q.resolved_at,
                    q.answer,
                    q.resolver,
                    q.source,
                    q.target.kind,
                    q.target.id,
                ),
            )

    def inbox(
        self,
        *,
        status: QuestionStatus | str | None = QuestionStatus.PENDING,
        kind: HumanKind | str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[HumanQuestion]:
        """查 inbox.

        Args:
          status: 默认 pending; 传 None 查所有状态.
          kind: 可选按 kind 过滤.
          target_kind: 可选按 target_kind 过滤 (如 TARGET_COLLEAGUE_FEISHU 只看collab platform同事工单).
          target_id: 可选按 target_id 过滤 (如单同事 open_id).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            if isinstance(status, str):
                status = QuestionStatus(status)
            clauses.append("status = ?")
            params.append(status.value)
        if kind is not None:
            if isinstance(kind, str):
                kind = HumanKind(kind)
            clauses.append("kind = ?")
            params.append(kind.value)
        if target_kind is not None:
            clauses.append("target_kind = ?")
            params.append(target_kind)
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM questions {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [HumanQuestion.from_row(r) for r in rows]

    def get(self, question_id: str) -> HumanQuestion | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
        return HumanQuestion.from_row(row) if row else None

    def resolve(self, question_id: str, answer: str, *, resolver: str = "human") -> HumanQuestion:
        """回答 pending 问题. resolver 字段承载"谁批准的"信息 (如collab platform同事 name 或 open_id)."""
        now = time.time()
        with self._db_lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if row is None:
                raise self._reject(
                    "resolve",
                    "question not found",
                    {"id": question_id},
                )
            if row["status"] != QuestionStatus.PENDING.value:
                raise self._reject(
                    "resolve",
                    f"question not pending (current: {row['status']})",
                    {"id": question_id, "status": row["status"]},
                )
            conn.execute(
                """UPDATE questions
                   SET status = ?, answer = ?, resolved_at = ?, resolver = ?
                   WHERE id = ?""",
                (QuestionStatus.RESOLVED.value, answer, now, resolver, question_id),
            )
        self._audit(
            "answer",
            {"id": question_id, "answer": answer, "resolver": resolver},
        )
        q = self.get(question_id)
        if q is not None:
            self._dispatch_notify(q, "on_resolved")
        return q  # type: ignore[return-value]

    def wait_for_resolve(
        self, question_id: str, *, timeout: float = 300.0, poll_interval: float = 2.0
    ) -> HumanQuestion:
        """阻塞等待 resolve. 超时 raise TimeoutError.

        注意: 这是**显式阻塞**接口, 只在业务明确要等人类时用. ask() 本身不阻塞.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            q = self.get(question_id)
            if q is None:
                raise self._reject(
                    "wait_for_resolve",
                    "question disappeared",
                    {"id": question_id},
                )
            if q.status == QuestionStatus.RESOLVED:
                return q
            time.sleep(poll_interval)
        raise TimeoutError(f"human question {question_id} not resolved within {timeout}s")

    def expire_old(self, older_than_seconds: float = 7 * 86400) -> int:
        """将超过阈值的 pending 问题标记为 expired. 返回数量."""
        cutoff = time.time() - older_than_seconds
        with self._db_lock, self._conn() as conn:
            cursor = conn.execute(
                """UPDATE questions SET status = ?
                   WHERE status = ? AND created_at < ?""",
                (QuestionStatus.EXPIRED.value, QuestionStatus.PENDING.value, cutoff),
            )
            count = cursor.rowcount
        if count:
            self._audit("expire", {"count": count, "cutoff_ts": cutoff})
        return count

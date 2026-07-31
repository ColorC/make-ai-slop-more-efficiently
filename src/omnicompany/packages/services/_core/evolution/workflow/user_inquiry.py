# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.user_inquiry_system.py"
"""用户询问接口

当进化工作流无法自主判断时（error_category=needs_user_clarification），
将问题提交到询问队列，等待用户通过 CLI/网页/文件回答。

设计原则：
- 问题以 SQLite 持久化，进程重启不丢失
- 每个问题附带完整上下文（board_id, trace_id, 诊断上下文）
- 回答后自动唤醒等待中的进化流程
- CLI: omnicompany inquiry list / answer <id> <text>
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 2026-07-03 批4 ㋑ 问询库路径修死:
#   旧实现用裸相对字符串 "omnicompany_inquiries.db", 落盘位置随进程 cwd 漂移,
#   是潜伏 bug(不同工作目录起进程 → 出第二个库文件)。现改为向上找仓根标志
#   (src/omnicompany 目录)后锚定到 data/runtime/buses/inquiries.db, 与人工审批
#   问询库 human_inbox.db 同级(参照 runtime/buses/human_bus.py::_resolve_inbox_path)。
#   逃生舱环境变量 OMNI_INQUIRY_DB_PATH 供测试/多实例隔离用。
#   位置权威: config/ledgers.yaml id=evolution-inquiries。
#
# 2026-07-03 批4返修 ㋒ "仓库外启动即漂移"缺陷修复:
#   上一版仍是从 Path.cwd() 向上找仓根标志, 若进程从仓库外目录(如 C:\ 或用户
#   主目录)启动, 向上永远走不到标志, 会静默退回到用最后的 cursor(通常是驱动器
#   根或用户主目录)拼路径, 产生仓库外的错误数据库文件——不报错也不提示。
#   现改为从模块自身 __file__ 向上找仓根标志(模块文件永远在仓库内, 这个锚点
#   不因进程 cwd 而丢失); 若从 __file__ 向上走也定位不到仓根标志(理论上不该
#   发生, 除非模块被复制到了仓库结构之外), 不再静默回退, 改为抛 RuntimeError。
_LEGACY_DB_FILENAME = "omnicompany_inquiries.db"
_INQUIRY_DB_ENV_VAR = "OMNI_INQUIRY_DB_PATH"
_REPO_ROOT_SEARCH_LIMIT = 10


def _find_repo_root_from(start: Path) -> Path:
    """从 start 起向上走, 找含 src/omnicompany 目录的祖先(仓根标志)。

    最多向上走 _REPO_ROOT_SEARCH_LIMIT 层; 找不到时抛 RuntimeError(绝不静默
    回退到调用方传入的起点或当前目录拼路径)。
    """
    cursor = start
    for _ in range(_REPO_ROOT_SEARCH_LIMIT):
        if (cursor / "src" / "omnicompany").is_dir():
            return cursor
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    raise RuntimeError(
        "无法定位仓库根: 从模块路径 "
        f"{start} 向上最多 {_REPO_ROOT_SEARCH_LIMIT} 层均未找到 src/omnicompany "
        "标志目录。请检查是否安装/复制方式破坏了仓库结构"
        "(模块被移出了 src/omnicompany 之下)。"
    )


def _resolve_default_db_path() -> Path:
    """解析进化问询 SQLite 的锚定落盘路径, 与当前工作目录无关。

    优先级:
      1. 环境变量 OMNI_INQUIRY_DB_PATH (逃生舱, 测试/多实例隔离用)。
      2. 从模块自身 __file__ 向上最多 _REPO_ROOT_SEARCH_LIMIT 层找仓根标志
         (src/omnicompany 目录) —— 用 __file__ 而非 Path.cwd(), 因为模块文件
         永远在仓库内, 不会因进程从仓库外目录启动而定位失败。
         命中则落 <仓根>/data/runtime/buses/inquiries.db。
      3. 走到头找不到仓根标志: 不静默回退到 cwd 拼路径, 抛 RuntimeError。
    """
    override = os.environ.get(_INQUIRY_DB_ENV_VAR)
    if override:
        return Path(override)
    repo_root = _find_repo_root_from(Path(__file__).resolve())
    return repo_root / "data" / "runtime" / "buses" / "inquiries.db"


def migrate_legacy_inquiry_db(
    *,
    legacy_path: Path | str,
    target_path: Path | str,
) -> bool:
    """把旧漂移库文件迁到锚定位置, 迁移前先留回滚副本(不做无副本覆盖)。

    行为:
      - 旧文件不存在 → 无事可做, 返回 False。
      - 目标已存在 → 不覆盖(锚定库优先), 返回 False。
      - 迁移: 先把旧文件复制成 <旧文件>.bak 回滚副本, 再把旧文件搬到目标位置。
        搬移用 copy(而非 move), 使旧文件原地保留同样构成回滚副本 —— 双保险。

    返回是否实际执行了迁移。
    """
    legacy = Path(legacy_path)
    target = Path(target_path)
    if not legacy.is_file():
        return False
    if target.is_file():
        logger.info("[inquiry] target db already exists, skip migration: %s", target)
        return False

    # 回滚副本先行: 旧文件原地保留 + 额外 .bak 副本, 保证迁移可逆。
    backup = legacy.with_suffix(legacy.suffix + ".bak")
    try:
        shutil.copy2(legacy, backup)
    except OSError as e:
        logger.warning("[inquiry] failed to write rollback copy %s: %s", backup, e)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    logger.info(
        "[inquiry] migrated legacy db %s -> %s (rollback copies: %s + original kept)",
        legacy, target, backup,
    )
    return True


# ── 数据结构 ──

@dataclass
class UserInquiry:
    """单条用户询问"""

    id: str
    board_id: str
    trace_id: str
    pipeline_id: str

    question: str
    """具体向用户提出的问题"""

    context: str
    """诊断上下文（根因节点、已有证据等）"""

    error_category_suspected: str = "needs_user_clarification"
    tags: list[str] = field(default_factory=list)

    status: str = "pending"
    """pending | answered | expired"""

    answer: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    answered_at: str = ""

    @staticmethod
    def new(
        board_id: str,
        trace_id: str,
        pipeline_id: str,
        question: str,
        context: str = "",
        tags: list[str] | None = None,
    ) -> "UserInquiry":
        return UserInquiry(
            id=str(uuid.uuid4())[:8],
            board_id=board_id,
            trace_id=trace_id,
            pipeline_id=pipeline_id,
            question=question,
            context=context,
            tags=tags or [],
        )


# ── 存储层 ──

class UserInquiryStore:
    """SQLite 持久化询问队列"""

    def __init__(self, db_path: str | Path | None = None):
        # db_path=None → 走锚定解析(与 cwd 无关); 显式传入则尊重调用方(测试/隔离)。
        resolved = Path(db_path) if db_path is not None else _resolve_default_db_path()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(resolved)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inquiries (
                    id TEXT PRIMARY KEY,
                    board_id TEXT,
                    trace_id TEXT,
                    pipeline_id TEXT,
                    question TEXT,
                    context TEXT,
                    status TEXT DEFAULT 'pending',
                    answer TEXT DEFAULT '',
                    created_at TEXT,
                    answered_at TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    error_category_suspected TEXT DEFAULT 'needs_user_clarification'
                )
            """)
            conn.commit()

    def submit(self, inquiry: UserInquiry) -> str:
        """提交一条询问，返回 inquiry.id"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO inquiries
                  (id, board_id, trace_id, pipeline_id, question, context,
                   status, answer, created_at, answered_at, tags, error_category_suspected)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                inquiry.id, inquiry.board_id, inquiry.trace_id, inquiry.pipeline_id,
                inquiry.question, inquiry.context,
                inquiry.status, inquiry.answer,
                inquiry.created_at, inquiry.answered_at,
                json.dumps(inquiry.tags, ensure_ascii=False),
                inquiry.error_category_suspected,
            ))
            conn.commit()
        logger.info("[inquiry] Submitted inquiry %s: %s", inquiry.id, inquiry.question[:80])
        return inquiry.id

    def answer(self, inquiry_id: str, answer_text: str) -> bool:
        """回答一条询问，返回是否成功"""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE inquiries SET status='answered', answer=?, answered_at=? WHERE id=?",
                (answer_text, now, inquiry_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                logger.warning("[inquiry] Inquiry %s not found", inquiry_id)
                return False
        logger.info("[inquiry] Answered inquiry %s", inquiry_id)
        return True

    def get(self, inquiry_id: str) -> UserInquiry | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM inquiries WHERE id=?", (inquiry_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_inquiry(row)

    def list_pending(self) -> list[UserInquiry]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM inquiries WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_inquiry(r) for r in rows]

    def list_all(self, limit: int = 50) -> list[UserInquiry]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM inquiries ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_inquiry(r) for r in rows]

    @staticmethod
    def _row_to_inquiry(row: tuple) -> UserInquiry:
        # id, board_id, trace_id, pipeline_id, question, context,
        # status, answer, created_at, answered_at, tags, error_category_suspected
        return UserInquiry(
            id=row[0], board_id=row[1], trace_id=row[2], pipeline_id=row[3],
            question=row[4], context=row[5],
            status=row[6], answer=row[7],
            created_at=row[8], answered_at=row[9],
            tags=json.loads(row[10]) if row[10] else [],
            error_category_suspected=row[11] if len(row) > 11 else "needs_user_clarification",
        )


# ── 异步等待接口 ──

class InquiryAwaiter:
    """轮询等待询问被回答（供 orchestrator 用）

    用法：
        awaiter = InquiryAwaiter(store, inquiry_id)
        answer = await awaiter.wait(timeout=3600)
    """

    def __init__(self, store: UserInquiryStore, inquiry_id: str, poll_interval: float = 5.0):
        self._store = store
        self._id = inquiry_id
        self._poll_interval = poll_interval

    async def wait(self, timeout: float = 3600.0) -> str | None:
        """等待回答，返回 answer 文本。超时返回 None。"""
        elapsed = 0.0
        while elapsed < timeout:
            inq = self._store.get(self._id)
            if inq and inq.status == "answered":
                return inq.answer
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval
        logger.warning("[inquiry] Timeout waiting for inquiry %s", self._id)
        return None


# ── 文件回答接口（离线模式）──

def write_inquiry_to_file(inquiry: UserInquiry, out_dir: str = ".") -> Path:
    """将询问写到文件，供用户直接编辑 answer 字段后回答"""
    out_path = Path(out_dir) / f"inquiry_{inquiry.id}.json"
    data = {
        "id": inquiry.id,
        "question": inquiry.question,
        "context": inquiry.context,
        "board_id": inquiry.board_id,
        "pipeline_id": inquiry.pipeline_id,
        "answer": "",  # 用户填写这里
        "_instructions": "在 answer 字段填写答案后，运行: omnicompany inquiry answer <id> <answer>",
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[inquiry] Inquiry file written: %s", out_path)
    return out_path


# ── 全局默认 store 实例（供 orchestrator 直接使用）──

_default_store: UserInquiryStore | None = None


def get_default_store(db_path: str | Path | None = None) -> UserInquiryStore:
    global _default_store
    resolved = str(Path(db_path)) if db_path is not None else str(_resolve_default_db_path())
    if _default_store is None or _default_store.db_path != resolved:
        _default_store = UserInquiryStore(resolved)
    return _default_store

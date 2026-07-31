# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.hypothesis_store_persistence.py"
"""HypothesisBoardStore — HypothesisBoard 的 SQLite 持久化

黑板存在独立的 SQLite 文件里（和 EventBus 分开，
避免混入运行时事件流）。

Schema 设计：极简，用 JSON 序列化整个 board，
关键字段单独提列建索引（board_id, pipeline_id, status）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.evolution.workflow.hypothesis import (
    ExperimentRecord,
    Hypothesis,
    HypothesisBoard,
    HypothesisStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypothesis_boards (
    board_id     TEXT PRIMARY KEY,
    pipeline_id  TEXT NOT NULL,
    trace_id     TEXT NOT NULL,
    status       TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_boards_pipeline ON hypothesis_boards (pipeline_id);
CREATE INDEX IF NOT EXISTS idx_boards_status   ON hypothesis_boards (status);
CREATE INDEX IF NOT EXISTS idx_boards_trace    ON hypothesis_boards (trace_id);
"""


def _serialize_board(board: HypothesisBoard) -> str:
    def _default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, HypothesisStatus):
            return obj.value
        raise TypeError(f"Cannot serialize {type(obj)}")

    return json.dumps(asdict(board), default=_default)


def _deserialize_board(data: str) -> HypothesisBoard:
    raw = json.loads(data)

    def _parse_dt(s: str | None) -> datetime | None:
        if s is None:
            return None
        return datetime.fromisoformat(s)

    hypotheses = []
    for h_raw in raw.get("hypotheses", []):
        h_raw["status"] = HypothesisStatus(h_raw["status"])
        h_raw["created_at"] = datetime.fromisoformat(h_raw["created_at"])
        h_raw["last_updated"] = datetime.fromisoformat(h_raw["last_updated"])
        hypotheses.append(Hypothesis(**h_raw))

    experiments = []
    for e_raw in raw.get("experiment_log", []):
        e_raw["created_at"] = datetime.fromisoformat(e_raw["created_at"])
        e_raw["completed_at"] = _parse_dt(e_raw.get("completed_at"))
        experiments.append(ExperimentRecord(**e_raw))

    raw["hypotheses"] = hypotheses
    raw["experiment_log"] = experiments
    raw["created_at"] = datetime.fromisoformat(raw["created_at"])
    raw["updated_at"] = datetime.fromisoformat(raw["updated_at"])
    return HypothesisBoard(**raw)


class HypothesisBoardStore:
    """HypothesisBoard 的 SQLite 持久化层"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── 写入 ──

    def save(self, board: HypothesisBoard) -> None:
        """插入或更新黑板"""
        board.updated_at = datetime.now(timezone.utc)
        data = _serialize_board(board)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hypothesis_boards
                    (board_id, pipeline_id, trace_id, status, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(board_id) DO UPDATE SET
                    status     = excluded.status,
                    data       = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (
                    board.board_id,
                    board.pipeline_id,
                    board.trace_id,
                    board.status,
                    data,
                    board.created_at.isoformat(),
                    board.updated_at.isoformat(),
                ),
            )

    # ── 读取 ──

    def load(self, board_id: str) -> HypothesisBoard | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM hypothesis_boards WHERE board_id = ?",
                (board_id,),
            ).fetchone()
        return _deserialize_board(row["data"]) if row else None

    def load_by_trace(self, trace_id: str) -> HypothesisBoard | None:
        """按 trace_id 查找最近的黑板"""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT data FROM hypothesis_boards
                   WHERE trace_id = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (trace_id,),
            ).fetchone()
        return _deserialize_board(row["data"]) if row else None

    def list_active(self, pipeline_id: str | None = None) -> list[HypothesisBoard]:
        """列出所有非 done/escalated 状态的黑板"""
        sql = """SELECT data FROM hypothesis_boards
                 WHERE status NOT IN ('done', 'escalated')"""
        params: list[Any] = []
        if pipeline_id:
            sql += " AND pipeline_id = ?"
            params.append(pipeline_id)
        sql += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_deserialize_board(r["data"]) for r in rows]

    # ── 工厂方法 ──

    @staticmethod
    def new_board_id() -> str:
        return str(uuid.uuid4())

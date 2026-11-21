# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.tool.execution_abc.py"
"""Tool 原语 ABC（Phase 0）

Tool = 操作外界并产生可观测变化的代码单元。

设计原则：
  - 只操作，不决策（决策在 Node 中）
  - 操作必须可观测（Hook 可以感知 Tool 的输出）
  - 输入/输出均可用 Signal 或结构化数据表达

Tool 的两种形态：
  SyncTool  — 同步操作（DB 写入、文件读写、进程调用）
  AsyncTool — 异步操作（LLM 调用、HTTP 请求、embedding 计算）

现有实现（隐式 Tool，未继承此 ABC）：
  runtime/db_access.py            — DB 读写 Tool
  runtime/llm.py LLMClient        — LLM 调用 Tool
  runtime/embedding_client.py     — Embedding 计算 Tool
  runtime/tool_executor.py        — Shell/Editor 执行 Tool

未来实现（应继承此 ABC）：
  PainDBWriteTool      — 写 pain_signals 表
  NodeCreateTool       — 写 semantic_nodes 表（进化操作）
  EvolutionOutcomeTool — 写 evolution_signals 表
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """所有 Tool 的基类。

    Tool 执行可观测的外部操作，返回操作结果。
    不包含决策逻辑（use/not-use 由调用方 Node 决定）。
    """

    @abstractmethod
    def execute(self, input_data: Any) -> dict[str, Any]:
        """执行操作，返回结构化结果。

        Args:
            input_data: 操作输入，可以是 Signal.to_dict() 或任意结构化数据。

        Returns:
            结构化结果，必须包含 "success": bool 字段。
            额外字段视操作类型而定。
        """


class AsyncBaseTool(ABC):
    """异步 Tool 的基类（LLM、HTTP 等场景）。"""

    @abstractmethod
    async def execute(self, input_data: Any) -> dict[str, Any]:
        """异步执行操作，返回结构化结果。"""


# ── 常用 Tool 基础实现 ──────────────────────────────────────────────────

class DBWriteTool(BaseTool):
    """数据库写入 Tool 基类。

    子类只需实现 _write()，此基类处理连接和错误。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def execute(self, input_data: Any) -> dict[str, Any]:
        try:
            result = self._write(input_data)
            return {"success": True, **result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @abstractmethod
    def _write(self, input_data: Any) -> dict[str, Any]:
        """实际写入逻辑，由子类实现。"""


class PainSignalWriteTool(DBWriteTool):
    """写入 pain_signals 表的 Tool。

    替代旧的 PainPropagator 数值写入。
    输入 Signal（format='pain_signal'），写入语义文本。
    """

    def _write(self, input_data: Any) -> dict[str, Any]:
        import sqlite3
        import time

        if isinstance(input_data, dict):
            node_id = input_data.get("metadata", {}).get("node_id") or input_data.get("node_id", "")
            text = input_data.get("text", "")
            severity = input_data.get("metadata", {}).get("severity") or input_data.get("severity", "medium")
            source = input_data.get("source", "tool")
        else:
            return {"success": False, "error": "input must be dict or Signal.to_dict()"}

        if not node_id or not text:
            return {"success": False, "error": "node_id and text required"}

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO pain_signals (node_id, signal_text, severity, source, created_at)"
                " VALUES (?,?,?,?,?)",
                (node_id, text[:500], severity, source, time.time()),
            )
            conn.commit()
            return {"node_id": node_id, "severity": severity}
        finally:
            conn.close()

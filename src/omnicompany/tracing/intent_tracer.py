# [OMNI] origin=claude-code ts=2026-04-08T03:23:36Z
# [OMNI] material_id="material:tracing.llm_intent_collector.engine.py"
"""IntentTracer — 意图轨迹采集器（权威位置：omnicompany.tracing）

每次 LLM 调用工具时，从工具参数中提取 intent 字段，
校验语义类型约束，写入独立的 SQLite 表。

Intent 字段格式（LLM 在每个工具调用中附加）:
{
    "input_types":  ["user_request"],           # 本步消耗的语义类型
    "output_types": ["feishu_message_id"],       # 本步产出的语义类型
    "action_class": "execute",                   # acquire | execute | summarize | think
    "desc":         "Send text message via Feishu API"
}

类型约束规则:
- 初始持有集合 = {"user_request"}
- 每步执行后，output_types 并入持有集合
- input_types 中任何不在持有集合的类型 → violations 列记录（幻觉信号）

This is the canonical location. The old shim at omnicompany.runtime.intent_tracer
was removed 2026-04-07; callers import directly from omnicompany.tracing now.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.protocol.events import FactoryEvent
from omnicompany.runtime.storage.db_access import open_db_rw

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intent_steps (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id          TEXT NOT NULL,
    step_num          INTEGER NOT NULL,
    tool_name         TEXT NOT NULL,
    input_types       TEXT NOT NULL DEFAULT '[]',
    output_types      TEXT NOT NULL DEFAULT '[]',
    action_class      TEXT NOT NULL DEFAULT '',
    desc              TEXT NOT NULL DEFAULT '',
    rationale         TEXT NOT NULL DEFAULT '',
    violations        TEXT NOT NULL DEFAULT '[]',
    timestamp         TEXT NOT NULL,
    -- V0.3 fields: provenance & routing
    type_source       TEXT NOT NULL DEFAULT 'llm_infer',
    type_confidence   REAL NOT NULL DEFAULT -1.0,
    parent_task_id    TEXT NOT NULL DEFAULT '',
    origin            TEXT NOT NULL DEFAULT 'human',
    route_node_id     TEXT NOT NULL DEFAULT '',
    route_decision    TEXT NOT NULL DEFAULT '',
    -- V0.4 fields: rich decision metadata
    expected_output   TEXT NOT NULL DEFAULT '',
    depends_on        TEXT NOT NULL DEFAULT '[]',
    info_transform    TEXT NOT NULL DEFAULT '',
    tool_args_summary TEXT NOT NULL DEFAULT '',
    tool_result       TEXT NOT NULL DEFAULT '',
    tool_exit_ok      INTEGER NOT NULL DEFAULT -1
);

CREATE INDEX IF NOT EXISTS idx_intent_trace ON intent_steps (trace_id, step_num);
CREATE INDEX IF NOT EXISTS idx_intent_origin ON intent_steps (origin, parent_task_id);
"""


class IntentTracer:
    """意图轨迹记录器。

    非入侵性：intent 字段缺失时静默跳过，不影响正常执行流。
    一个 IntentTracer 实例对应一次 run_agent 调用（一条 trace_id）。

    CH1 集成：当检测到新的 output_types 时，通过 TypeDiscoveryService
    并行注册新语义类型（不阻塞主循环）。
    """

    def __init__(
        self,
        db_path: str | Path,
        trace_id: str,
        parent_task_id: str = "",
        origin: str = "human",
        type_discovery: Any = None,
        event_bus: Any = None,
    ):
        """Args:
            parent_task_id: ULID of the task that spawned this trace
                            (empty = root task initiated by a human).
            origin: who initiated this trace — 'human' | 'explorer' | 'meta_agent'
            type_discovery: TypeDiscoveryService instance for CH1 real-time type registration
            event_bus: Optional publisher to dual-write intent events
        """
        self.db_path = Path(db_path)
        self.trace_id = trace_id
        self.parent_task_id = parent_task_id
        self.origin = origin
        self._held_types: set[str] = {"user_request"}
        self._conn: sqlite3.Connection | None = None
        self._step = 0
        self._type_discovery = type_discovery
        self._event_bus = event_bus

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = open_db_rw(str(self.db_path))
            self._conn.executescript(_SCHEMA)
            # 向后兼容旧 DB：按需补列（SQLite ALTER TABLE 不支持 IF NOT EXISTS）
            _compat_cols = [
                "ALTER TABLE intent_steps ADD COLUMN rationale TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN type_source TEXT NOT NULL DEFAULT 'llm_infer'",
                "ALTER TABLE intent_steps ADD COLUMN type_confidence REAL NOT NULL DEFAULT -1.0",
                "ALTER TABLE intent_steps ADD COLUMN parent_task_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN origin TEXT NOT NULL DEFAULT 'human'",
                "ALTER TABLE intent_steps ADD COLUMN route_node_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN route_decision TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN expected_output TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE intent_steps ADD COLUMN info_transform TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN tool_args_summary TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN tool_result TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE intent_steps ADD COLUMN tool_exit_ok INTEGER NOT NULL DEFAULT -1",
            ]
            for ddl in _compat_cols:
                try:
                    self._conn.execute(ddl)
                except Exception:
                    pass  # 列已存在
            self._conn.commit()
        return self._conn

    def record_step(
        self,
        tool_name: str,
        intent: dict[str, Any] | str | None,
        tool_args: dict[str, Any] | None = None,
    ) -> tuple[list[str], int]:
        """记录一个工具调用步骤。

        Args:
            tool_name: 被调用的工具名称
            intent:    从工具参数中提取的 intent dict（或 JSON 字符串）；为 None 时静默跳过
            tool_args: 传给工具的参数（不含 intent），用于记录输入内容

        Returns:
            (violations, step_num): 类型约束违规列表 + 本步的 step_num（用于结果回填）
        """
        if intent is None:
            step = self._step
            self._step += 1
            return ([], step)

        if isinstance(intent, str):
            try:
                intent = json.loads(intent)
            except (json.JSONDecodeError, TypeError):
                logger.debug("IntentTracer: intent is non-JSON string, skipping step %d", self._step)
                step = self._step
                self._step += 1
                return ([], step)

        if not isinstance(intent, dict):
            step = self._step
            self._step += 1
            return ([], step)

        input_types: list[str] = intent.get("input_types") or []
        output_types: list[str] = intent.get("output_types") or []
        action_class: str = intent.get("action_class") or ""
        desc: str = intent.get("desc") or ""
        rationale: str = intent.get("rationale") or ""
        expected_output: str = intent.get("expected_output") or ""
        depends_on: list[str] = intent.get("depends_on") or []
        info_transform: str = intent.get("info_transform") or ""

        violations = [t for t in input_types if t not in self._held_types]
        self._held_types.update(output_types)

        # CH1 Discovery: detect and register new semantic types (non-blocking)
        if self._type_discovery and output_types:
            try:
                self._type_discovery.check_and_register(
                    output_types=output_types,
                    input_types=input_types,
                    desc=desc,
                    rationale=rationale,
                    info_transform=info_transform,
                    tool_name=tool_name,
                    action_class=action_class,
                )
            except Exception as e:
                logger.debug("CH1 Discovery check failed: %s", e)

        # tool_args 摘要（截断防止过大）
        args_summary = ""
        if tool_args:
            if tool_name == "bash":
                args_summary = str(tool_args.get("command", ""))[:500]
            elif tool_name == "str_replace_editor":
                cmd = tool_args.get("command", "")
                path = tool_args.get("path", "")
                args_summary = f"{cmd}:{path}"
                if cmd == "create":
                    args_summary += f" ({len(tool_args.get('file_text', ''))} chars)"
                elif cmd == "str_replace":
                    args_summary += f" old={str(tool_args.get('old_str', ''))[:100]}"
            elif tool_name == "think":
                args_summary = str(tool_args.get("thought", ""))[:300]
            else:
                args_summary = json.dumps(tool_args, ensure_ascii=False)[:500]

        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO intent_steps
              (trace_id, step_num, tool_name, input_types, output_types,
               action_class, desc, rationale, violations, timestamp,
               type_source, type_confidence, parent_task_id, origin,
               route_node_id, route_decision,
               expected_output, depends_on, info_transform, tool_args_summary,
               tool_result, tool_exit_ok)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.trace_id,
                self._step,
                tool_name,
                json.dumps(input_types, ensure_ascii=False),
                json.dumps(output_types, ensure_ascii=False),
                action_class,
                desc,
                rationale,
                json.dumps(violations, ensure_ascii=False),
                now,
                "llm_infer",
                -1.0,
                self.parent_task_id,
                self.origin,
                "",
                "",
                expected_output[:1000],
                json.dumps(depends_on, ensure_ascii=False),
                info_transform[:1000],
                args_summary,
                "",   # tool_result: 执行后回填
                -1,   # tool_exit_ok: 执行后回填
            ),
        )
        conn.commit()

        self._emit_eventbus("intent.step", {
            "step": self._step,
            "node": tool_name,
            "action_class": action_class,
            "description": desc,
            "rationale": rationale,
            "input_types": input_types,
            "output_types": output_types,
            "tool_args": tool_args,
            "tool_args_summary": args_summary,
        })

        recorded_step = self._step
        if violations:
            logger.warning(
                "IntentTracer [%s] step=%d (%s): undeclared input types %s",
                self.trace_id[:8],
                self._step,
                tool_name,
                violations,
            )

        self._step += 1
        return (violations, recorded_step)

    @property
    def held_types(self) -> frozenset[str]:
        """当前持有的语义类型集合（不可变快照）"""
        return frozenset(self._held_types)

    @property
    def step_count(self) -> int:
        return self._step

    def record_tool_result(
        self,
        step_num: int,
        result_summary: str,
        exit_ok: bool,
    ) -> None:
        """工具执行后回填结果摘要。

        Args:
            step_num:       对应的 step_num
            result_summary: 执行结果摘要（截断到合理长度）
            exit_ok:        执行是否成功
        """
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE intent_steps
            SET tool_result=?, tool_exit_ok=?
            WHERE trace_id=? AND step_num=?
            """,
            (result_summary[:2000], int(exit_ok), self.trace_id, step_num),
        )
        conn.commit()
        self._emit_eventbus("intent.tool_result", {
            "step": step_num,
            "result_summary": result_summary[:2000],
            "exit_ok": bool(exit_ok),
        })

    def record_route_decision(
        self,
        step_num: int,
        route_node_id: str,
        route_decision: str,
        type_confidence: float = -1.0,
    ) -> None:
        """由 RouteClassifier 回填：记录该步骤被归并到哪个节点。

        Args:
            step_num:       对应的 step_num
            route_node_id:  IntentNode 的 node_id
            route_decision: 'NEW' | 'MERGE' | 'NOISE'
            type_confidence: embedding 相似度（可选）
        """
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE intent_steps
            SET route_node_id=?, route_decision=?, type_confidence=?
            WHERE trace_id=? AND step_num=?
            """,
            (route_node_id, route_decision, type_confidence, self.trace_id, step_num),
        )
        conn.commit()
        self._emit_eventbus("intent.route_decision", {
            "step": step_num,
            "route_node_id": route_node_id,
            "route_decision": route_decision,
            "type_confidence": type_confidence,
        })

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _emit_eventbus(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self._event_bus:
            return
        ev = FactoryEvent(
            trace_id=self.trace_id,
            event_type=event_type,
            source=f"intent_tracer.{self.origin}",
            payload=payload,
            tags=["intent_tracer", f"origin:{self.origin}"],
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._event_bus.publish(ev))
        except RuntimeError:
            pass  # IntentTracer remains non-blocking outside an async bus context.

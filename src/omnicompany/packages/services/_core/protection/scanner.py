# [OMNI] origin=ai-ide domain=services/_core/protection ts=2026-05-02T04:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="protection scanner - 扫文件树跟 cc_wrapper event bus 对账, 找违规"
# [OMNI] why="离线扫描型 G4 MVP, 不实时拦. scan 找出所有 watched 内 + 非白名单 + 不在注册中心 + event bus 无 trace 的文件 → 违规候选"
# [OMNI] tags=protection,scanner,violation,event-bus
# [OMNI] material_id="material:core.protection.violation_scanner.event_reconciler.py"
"""protection scanner - 扫描违规.

走 watched_paths 内每个文件:
  - 如果 whitelisted → 跳过
  - 如果在注册中心 (registry InstanceRegistry) → 跳过 (合法注册过的)
  - 如果 event bus 找到 agent.tool.call 写过 → 标 internal_misplace
    (内部代码写到了 watched 内 + 不在白名单 + 不在注册中心 — 错位了)
  - 如果 event bus 找不到 → 标 external_write (外部直接写的, 没经过 omnicompany 体系)

输出 Violation 列表, 给 handlers.py 处理.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterator

from omnicompany.packages.services._core.protection.policy import (
    is_watched,
    is_whitelisted,
    load_policy,
    load_baseline,
    _project_root,
)


# 内部写入工具集 (cc_wrapper trace.py 记录的 agent.tool.call.tool 字段值)
_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_editor"})


@dataclass
class Violation:
    """一个违规候选."""

    file_path: str  # 绝对路径
    rel_path: str   # 相对项目根
    classification: str  # 'internal_misplace' / 'external_write'
    has_trace: bool      # event bus 找到 agent.tool.call 事件
    trace_id: str | None = None         # 事件的 trace_id (为 internal_misplace 提供身份)
    tool: str | None = None             # 哪个工具写的
    timestamp: str | None = None        # 写入时间
    in_registry: bool = False           # 是否在注册中心 (跳过)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _events_db() -> Path:
    return _project_root() / "data" / "ide_events.db"


def _query_event_bus_for_path(rel_path: str) -> dict | None:
    """从 event bus 查 file_path 对应的最近一次写入事件.

    rel_path 是相对项目根的 unix-style path (例 src/omnicompany/foo/bar.py).
    返回 {trace_id, tool, timestamp} 或 None.
    """
    db = _events_db()
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(str(db), timeout=2.0)
    except sqlite3.Error:
        return None

    try:
        cur = conn.execute(
            "SELECT trace_id, timestamp, data FROM events "
            "WHERE event_type = 'agent.tool.call' "
            "ORDER BY timestamp DESC LIMIT 5000"
        )
        for trace_id, ts, data_json in cur:
            try:
                body = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            payload = body.get("payload") or {}
            tool = payload.get("tool")
            if tool not in _WRITE_TOOLS:
                continue
            args = payload.get("args") or {}
            fp = args.get("file_path") or args.get("path") or args.get("notebook_path")
            if not fp:
                continue
            # 标准化路径作匹配
            fp_norm = str(Path(fp).as_posix())
            if fp_norm.endswith(rel_path) or fp_norm == rel_path or fp_norm.endswith("/" + rel_path):
                return {"trace_id": trace_id, "tool": tool, "timestamp": ts}
    finally:
        conn.close()
    return None


def _registered_source_files() -> set[str]:
    """从注册中心拉所有 entity 的 source_file (相对路径 set)."""
    try:
        from omnicompany.packages.services._core.registry import get_registry
    except ImportError:
        return set()
    reg = get_registry()
    paths: set[str] = set()
    for entry in reg.list_all():
        if entry.source_file:
            paths.add(entry.source_file.replace("\\", "/"))
    return paths


def _walk_watched(policy: dict) -> Iterator[Path]:
    """遍历 watched_paths 内所有文件."""
    proj = _project_root()
    for prefix in policy.get("watched_paths", []):
        if prefix in (".", "./"):
            # 仓库根直接子文件, 不递归 (子目录归各自的 watched 前缀管)
            for f in proj.iterdir():
                if f.is_file():
                    yield f
            continue
        base = proj / prefix
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.is_file():
                yield f


def classify_violation(
    file_path: Path,
    policy: dict,
    registered: set[str],
    baseline: set[str] | None = None,
) -> Violation | None:
    """判断单个文件是否违规, 是的话返回 Violation, 否则 None.

    判定逻辑:
      1. 不在 watched → 不查
      2. 在白名单 → 不查
      3. 在 baseline (grandfathered 历史快照) → 不查
      4. 在注册中心 → 不查 (合法实体)
      5. event bus 找到 trace → internal_misplace (内部错位)
      6. event bus 找不到 → external_write (外部直接写)
    """
    proj = _project_root()
    if not is_watched(file_path, policy):
        return None
    if is_whitelisted(file_path, policy):
        return None

    try:
        rel = file_path.relative_to(proj).as_posix()
    except ValueError:
        return None

    if baseline is not None and rel in baseline:
        return None

    if rel in registered:
        return Violation(
            file_path=str(file_path),
            rel_path=rel,
            classification="registered",
            has_trace=True,
            in_registry=True,
        )

    trace = _query_event_bus_for_path(rel)
    if trace:
        return Violation(
            file_path=str(file_path),
            rel_path=rel,
            classification="internal_misplace",
            has_trace=True,
            trace_id=trace["trace_id"],
            tool=trace["tool"],
            timestamp=trace["timestamp"],
        )
    return Violation(
        file_path=str(file_path),
        rel_path=rel,
        classification="external_write",
        has_trace=False,
    )


def scan_violations(
    *,
    policy: dict | None = None,
    skip_registered: bool = True,
    skip_baseline: bool = True,
) -> list[Violation]:
    """扫描所有违规.

    skip_registered=True 时跳过注册中心已记录的实体 (合法).
    skip_baseline=True 时跳过 baseline 历史快照 (grandfathered).
    两者 False 时也返回作 debug 用.
    """
    if policy is None:
        policy = load_policy()
    registered = _registered_source_files()
    baseline = load_baseline() if skip_baseline else None

    violations: list[Violation] = []
    for f in _walk_watched(policy):
        v = classify_violation(f, policy, registered, baseline)
        if v is None:
            continue
        if v.classification == "registered" and skip_registered:
            continue
        violations.append(v)
    return violations


def snapshot_current_as_baseline(policy: dict | None = None) -> int:
    """把 watched 内当前所有非白名单 / 非已注册文件加进 baseline.

    用法: 锁 enable 后跑这个函数, 把现有历史文件全 grandfathered, 之后只查新写入.
    返回 baseline 路径数.
    """
    from omnicompany.packages.services._core.protection.policy import save_baseline
    if policy is None:
        policy = load_policy()
    registered = _registered_source_files()
    proj = _project_root()
    baseline: set[str] = set()
    for f in _walk_watched(policy):
        if is_whitelisted(f, policy):
            continue
        try:
            rel = f.relative_to(proj).as_posix()
        except ValueError:
            continue
        if rel in registered:
            continue
        baseline.add(rel)
    save_baseline(baseline)
    return len(baseline)

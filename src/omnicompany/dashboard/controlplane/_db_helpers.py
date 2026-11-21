# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.controlplane.db_helpers.shared_utilities.py"
"""controlplane 共享 DB 工具函数 — 阶段 9 拆离自 app.py.

跟 [2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE 阶段 9 配套. 6 个 controlplane topic
文件 (events.py / nodes.py / traces.py / health.py / evolution.py / semantic.py)
都依赖这里的几个底层函数, 集中放避免重复:

- `db_paths()` — OMNICOMPANY_DB_DIR 下各 .db / .json / .jsonl 路径表
- `read_jsonl(path)` — 行式 JSON 容错读
- `read_json(path)` — 单 JSON 容错读
- `safe_conn(path)` — sqlite3.Connection (Row factory + timeout); 不存在或开不了返 None
- `discover_event_dbs()` — Move 8 后固定两个 unified events.db 路径
- `parse_output_types(raw)` — JSON 字符串容错解析
- `fetch_route_nodes(path)` — route_graph.db.route_nodes 查询 + 清洗
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def resolve_db_dir() -> Path:
    """OMNICOMPANY_DB_DIR 解析 — 默认 'data/autonomous', 相对路径相对 cwd."""
    raw = os.environ.get("OMNICOMPANY_DB_DIR", "data/autonomous")
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()


def db_paths() -> dict[str, Path]:
    """各类 db / json / jsonl 文件路径表.

    Move 8: events 走 unified data/events.db; 其他辅助文件仍走 OMNICOMPANY_DB_DIR.
    """
    from omnicompany.core.config import resolve_unified_db_path
    d = resolve_db_dir()
    return {
        "dir": d,
        "route_graph": d / "route_graph.db",
        "events": resolve_unified_db_path("events.db"),
        "intent_traces": d / "intent_traces.db",
        "evolution_log": d / "evolution_log.jsonl",
        "mutation_state": d / "mutation_state.json",
        "params": d / "params.json",
        "params_history": d / "params_history.jsonl",
        "meta_guardian_log": d / "meta_guardian_log.jsonl",
        "budget_state": d / "marathon_budget.json",
    }


def read_jsonl(path: Path) -> list[dict]:
    """行式 JSON 容错读. 文件不存在 / OSError / 单行 decode 失败都吞."""
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def read_json(path: Path) -> dict | list | None:
    """单 JSON 容错读. 文件不存在 / decode 失败返 None."""
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def parse_output_types(raw: object) -> object:
    """route_nodes.output_types 字段可能是 JSON 字符串, 容错解析."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def discover_event_dbs() -> list[tuple[str, Path]]:
    """Move 8 后: 返回固定的两个 unified 事件 DB.

    历史上 dashboard 用 rglob 扫遍 data/ 下所有 events.db / ide_events.db
    (13+ 个 stray 文件), 然后跨文件 join trace. Move 8 在引擎层强制了
    SQLiteBus 只能写 data/events.db 或 data/ide_events.db, 这里跟着简化为
    返回固定两条. label 用 source 字段区分 domain, 不再来自路径.
    """
    from omnicompany.core.config import resolve_unified_db_path

    results: list[tuple[str, Path]] = []
    for basename in ("events.db", "ide_events.db"):
        p = resolve_unified_db_path(basename)
        if p.is_file():
            results.append((basename.replace(".db", ""), p))
    return results


def safe_conn(db_path: Path) -> sqlite3.Connection | None:
    """sqlite3 connection w/ Row factory + 5s timeout. 文件不存在或开不了返 None."""
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def fetch_route_nodes(db_path: Path) -> list[dict]:
    """读 route_graph.db.route_nodes 表, 清洗输出. 给 /api/nodes 用."""
    conn = safe_conn(db_path)
    if conn is None:
        return []
    try:
        cur = conn.execute("SELECT * FROM route_nodes")
        col_names = [d[0] for d in cur.description]
        has_crystallized = "crystallized" in col_names
        rows: list[dict] = []
        for row in cur.fetchall():
            od = dict(row)
            rows.append({
                "id": od.get("node_id", ""),
                "tool_name": od.get("tool_name", "") or "",
                "output_types": parse_output_types(od.get("output_types", [])),
                "pain_score": float(od.get("pain_score") or 0.0),
                "success_rate": float(od.get("success_rate") if od.get("success_rate") is not None else -1.0),
                "hit_count": int(od.get("hit_count") or 0),
                "crystallized": bool(od.get("crystallized")) if has_crystallized else False,
                "deprecated": bool(od.get("deprecated")),
                "hard_eliminated": bool(od.get("hard_eliminated")),
                "node_guidance": od.get("node_guidance", "") or "",
                "created_at": od.get("created_at", "") or "",
            })
        return rows
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    """sqlite Row → dict, 剔除 embedding 大字段 (semantic 节点存 vector blob)."""
    d = dict(row)
    d.pop("embedding", None)
    return d


def sem_db() -> sqlite3.Connection | None:
    """semantic_network.db connection — events / nodes / traces 多个 topic 用."""
    paths = db_paths()
    sem = paths["dir"] / "semantic_network.db"
    return safe_conn(sem)

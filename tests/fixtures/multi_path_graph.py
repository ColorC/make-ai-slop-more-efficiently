"""
Multi-path Route Graph test fixtures.

Provides pre-built route graphs with diverse topologies for testing features
that depend on real multi-path routing: Boltzmann selection, pain propagation,
deprecation, type_split/merge, etc.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from typing import Any

_NOW = datetime.now(timezone.utc).isoformat()


def _init_db(db_path: str) -> None:
    """Initialize route_graph schema matching RouteGraph's actual schema."""
    from omnicompany.runtime.route_graph import RouteGraph
    rg = RouteGraph(db_path)
    rg._get_conn()  # triggers schema creation


def _insert_node(db_path: str, **kwargs: Any) -> None:
    conn = sqlite3.connect(db_path)
    defaults = {
        "input_types": "[]",
        "output_types": "[]",
        "action_class": "",
        "canonical_desc": "",
        "hit_count": 1,
        "tool_name": "",
        "success_rate": -1.0,
        "embedding": "[]",
        "created_at": _NOW,
        "last_seen": _NOW,
        "pain_score": 0.0,
        "pain_count": 0,
        "deprecated": 0,
        "deprecated_at": "",
        "hard_eliminated": 0,
        "energy": 1.0,
        "node_guidance": "",
    }
    for k, v in kwargs.items():
        if k in ("input_types", "output_types") and isinstance(v, str) and not v.startswith("["):
            v = json.dumps([v])
        defaults[k] = v

    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    conn.execute(f"INSERT OR REPLACE INTO route_nodes ({col_names}) VALUES ({placeholders})", vals)
    conn.commit()
    conn.close()


def _insert_edge(db_path: str, **kwargs: Any) -> None:
    conn = sqlite3.connect(db_path)
    from ulid import ULID
    defaults = {
        "edge_id": str(ULID()),
        "from_output_types": "[]",
        "to_node_id": "",
        "weight": 1,
    }
    for k, v in kwargs.items():
        if k == "from_output_types" and isinstance(v, str) and not v.startswith("["):
            v = json.dumps([v])
        defaults[k] = v

    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    conn.execute(f"INSERT INTO route_edges ({col_names}) VALUES ({placeholders})", vals)
    conn.commit()
    conn.close()


def build_diamond_graph(db_path: str | None = None) -> str:
    """Diamond topology: A -> B, A -> C, B -> D, C -> D.

    Two competing paths with different pain scores.
    Path A->B->D: low pain (good path)
    Path A->C->D: high pain (bad path)
    """
    if db_path is None:
        db_path = tempfile.mktemp(suffix=".db")
    _init_db(db_path)

    _insert_node(db_path, node_id="node_A", input_types="user_request",
                 output_types="parsed_intent", tool_name="intent_parser",
                 hit_count=20, success_rate=0.9, pain_score=0.1, energy=1.0)
    _insert_node(db_path, node_id="node_B", input_types="parsed_intent",
                 output_types="code_result", tool_name="bash",
                 hit_count=15, success_rate=0.85, pain_score=0.15, energy=0.9)
    _insert_node(db_path, node_id="node_C", input_types="parsed_intent",
                 output_types="code_result", tool_name="str_replace_editor",
                 hit_count=10, success_rate=0.3, pain_score=0.7, pain_count=5, energy=0.3)
    _insert_node(db_path, node_id="node_D", input_types="code_result",
                 output_types="final_output", tool_name="validator",
                 hit_count=25, success_rate=0.8, pain_score=0.2, energy=0.8)

    _insert_edge(db_path, from_output_types="parsed_intent",
                 to_node_id="node_B", weight=1)
    _insert_edge(db_path, from_output_types="parsed_intent",
                 to_node_id="node_C", weight=1)
    _insert_edge(db_path, from_output_types="code_result",
                 to_node_id="node_D", weight=1)

    return db_path


def build_degraded_graph(db_path: str | None = None) -> str:
    """One normal path + one high-pain path + one deprecated path.

    Tests deprecation safety valve and Boltzmann selection under degraded conditions.
    """
    if db_path is None:
        db_path = tempfile.mktemp(suffix=".db")
    _init_db(db_path)

    _insert_node(db_path, node_id="entry", input_types="user_request",
                 output_types="task_spec", tool_name="planner",
                 hit_count=30, success_rate=0.9, pain_score=0.05, energy=1.0)
    _insert_node(db_path, node_id="good_path", input_types="task_spec",
                 output_types="result", tool_name="bash",
                 hit_count=20, success_rate=0.85, pain_score=0.1, energy=0.95)
    _insert_node(db_path, node_id="bad_path", input_types="task_spec",
                 output_types="result", tool_name="bash",
                 hit_count=15, success_rate=0.02, pain_score=0.9, energy=0.1)
    _insert_node(db_path, node_id="dead_path", input_types="task_spec",
                 output_types="result", tool_name="bash",
                 hit_count=10, success_rate=0.01, pain_score=0.95,
                 deprecated=1, hard_eliminated=1, energy=0.0)

    _insert_edge(db_path, from_output_types="task_spec",
                 to_node_id="good_path", weight=1)
    _insert_edge(db_path, from_output_types="task_spec",
                 to_node_id="bad_path", weight=1)
    _insert_edge(db_path, from_output_types="task_spec",
                 to_node_id="dead_path", weight=1)

    return db_path


def build_high_variance_graph(db_path: str | None = None) -> str:
    """Graph with a high-variance node suitable for type_split testing.

    node_unstable has 50% success rate with many hits — candidate for split.
    """
    if db_path is None:
        db_path = tempfile.mktemp(suffix=".db")
    _init_db(db_path)

    _insert_node(db_path, node_id="node_stable", input_types="user_request",
                 output_types="command", tool_name="bash",
                 hit_count=30, success_rate=0.95, pain_score=0.05, energy=1.0)
    _insert_node(db_path, node_id="node_unstable", input_types="user_request",
                 output_types="command", tool_name="str_replace_editor",
                 hit_count=20, success_rate=0.50, pain_score=0.45, pain_count=5, energy=0.5)
    _insert_node(db_path, node_id="node_similar_a", input_types="user_request",
                 output_types="file_content", tool_name="bash",
                 hit_count=8, success_rate=0.8, pain_score=0.1, energy=0.8)
    _insert_node(db_path, node_id="node_similar_b", input_types="user_request",
                 output_types="file_content", tool_name="bash",
                 hit_count=6, success_rate=0.75, pain_score=0.12, energy=0.75)

    return db_path


def build_crystallization_candidate_graph(db_path: str | None = None) -> str:
    """Graph with a node that meets crystallization criteria.

    node_crystal: hit_count=25, success_rate=0.98, low entropy.
    """
    if db_path is None:
        db_path = tempfile.mktemp(suffix=".db")
    _init_db(db_path)

    _insert_node(db_path, node_id="node_crystal", input_types="user_request",
                 output_types="listed_files", tool_name="bash",
                 hit_count=25, success_rate=0.98, pain_score=0.01, energy=1.0,
                 node_guidance="List directory contents using ls -la")
    _insert_node(db_path, node_id="node_normal", input_types="user_request",
                 output_types="code_output", tool_name="bash",
                 hit_count=10, success_rate=0.7, pain_score=0.2, energy=0.7)

    return db_path

# [OMNI] origin=qoder ts=2026-08-17 type=format
# [OMNI] material_id="material:packages.services._core.conventional_workflow.spec"
"""WorkflowSpec 契约（Python 侧镜像 webworks material-core/workflow.js，golden 测试钉一致性）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

NODE_KINDS = ("anchor", "worker", "check", "gate")
EDGE_KINDS = ("sequence", "feedback")
WORKFLOW_LABELS = ("source", "spec", "run", "check", "gate", "template", "placeholder", "env-profile", "ephemeral")
WORKFLOW_PREDICATES = ("executes", "overrides", "instantiates", "feedback", "syncs-to")
DEFAULT_FEEDBACK_MAX = 3


class WorkflowSpecError(ValueError):
    pass


@dataclass(frozen=True)
class SpecNode:
    id: str
    kind: str
    title: str = ""
    worker_ref: str | None = None
    check_ref: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpecEdge:
    src: str
    dst: str
    kind: str = "sequence"
    condition: str | None = None
    max: int | None = None


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    title: str
    entry: str
    nodes: tuple[SpecNode, ...]
    edges: tuple[SpecEdge, ...]

    def node(self, node_id: str) -> SpecNode:
        for item in self.nodes:
            if item.id == node_id:
                return item
        raise WorkflowSpecError(f"unknown node {node_id}")


def validate_spec(raw: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if not isinstance(raw, Mapping):
        return ["spec must be a mapping"]
    if not raw.get("id"):
        issues.append("id must be a non-empty string")
    if not raw.get("title"):
        issues.append("title must be a non-empty string")
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        issues.append("nodes must be a non-empty array")
        return issues
    ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            issues.append(f"nodes[{index}] must be a mapping")
            continue
        node_id = node.get("id")
        if not node_id:
            issues.append(f"nodes[{index}].id must be a non-empty string")
        elif node_id in ids:
            issues.append(f"nodes[{index}].id duplicate node id {node_id}")
        else:
            ids.add(node_id)
        if node.get("kind") not in NODE_KINDS:
            issues.append(f"nodes[{index}].kind must be one of {', '.join(NODE_KINDS)}")
        if node.get("kind") == "worker" and not node.get("worker_ref"):
            issues.append(f"nodes[{index}].worker_ref worker nodes must declare worker_ref")
        if node.get("kind") == "check" and not node.get("check_ref"):
            issues.append(f"nodes[{index}].check_ref check nodes must declare check_ref")
        if node.get("params") is not None and not isinstance(node.get("params"), Mapping):
            issues.append(f"nodes[{index}].params must be a mapping")
    entry = raw.get("entry")
    if not entry or entry not in ids:
        issues.append("entry must reference an existing node id")
    for index, edge in enumerate(raw.get("edges") or []):
        if not isinstance(edge, Mapping):
            issues.append(f"edges[{index}] must be a mapping")
            continue
        if edge.get("from") not in ids:
            issues.append(f"edges[{index}].from must reference an existing node id")
        if edge.get("to") not in ids:
            issues.append(f"edges[{index}].to must reference an existing node id")
        kind = edge.get("kind") or "sequence"
        if kind not in EDGE_KINDS:
            issues.append(f"edges[{index}].kind must be one of {', '.join(EDGE_KINDS)}")
        if kind == "feedback" and edge.get("max") is not None:
            max_value = edge.get("max")
            if not isinstance(max_value, int) or isinstance(max_value, bool) or max_value < 1:
                issues.append(f"edges[{index}].max feedback iteration cap must be a positive integer")
    forward_out: dict[str, list[str]] = {}
    for edge in raw.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        if (edge.get("kind") or "sequence") != "sequence" or edge.get("from") not in ids or edge.get("to") not in ids:
            continue
        forward_out.setdefault(edge["from"], []).append(edge["to"])
    color: dict[str, int] = {}

    def find_cycle(start: str) -> tuple[str, str] | None:
        stack: list[tuple[str, list[str]]] = [(start, list(forward_out.get(start, [])))]
        color[start] = 1
        while stack:
            node, remaining = stack[-1]
            if not remaining:
                color[node] = 2
                stack.pop()
                continue
            nxt = remaining.pop(0)
            state = color.get(nxt, 0)
            if state == 1:
                return (node, nxt)
            if state == 0:
                color[nxt] = 1
                stack.append((nxt, list(forward_out.get(nxt, []))))
        return None

    for node_id in ids:
        if color.get(node_id, 0) != 0:
            continue
        cycle = find_cycle(node_id)
        if cycle:
            issues.append(f"edges sequence edges form a cycle ({cycle[0]} -> {cycle[1]}); cycles must be feedback edges")
            break
    return issues


def load_spec(raw: Mapping[str, Any]) -> WorkflowSpec:
    issues = validate_spec(raw)
    if issues:
        raise WorkflowSpecError("Invalid WorkflowSpec: " + "; ".join(issues[:8]))
    nodes = tuple(
        SpecNode(
            id=item["id"],
            kind=item["kind"],
            title=item.get("title") or "",
            worker_ref=item.get("worker_ref"),
            check_ref=item.get("check_ref"),
            params=dict(item.get("params") or {}),
        )
        for item in raw["nodes"]
    )
    edges = tuple(
        SpecEdge(
            src=item["from"],
            dst=item["to"],
            kind=item.get("kind") or "sequence",
            condition=item.get("condition"),
            max=item.get("max"),
        )
        for item in (raw.get("edges") or [])
    )
    return WorkflowSpec(id=raw["id"], title=raw["title"], entry=raw["entry"], nodes=nodes, edges=edges)


def load_spec_json(text: str) -> WorkflowSpec:
    return load_spec(json.loads(text))


@dataclass(frozen=True)
class RunNode:
    id: str
    source_node_id: str
    iteration: int
    node: SpecNode


@dataclass(frozen=True)
class RunEdge:
    src: str
    dst: str
    kind: str
    condition: str | None = None
    iteration_from: int | None = None
    iteration_to: int | None = None


def unroll_spec(spec: WorkflowSpec, *, default_max: int = DEFAULT_FEEDBACK_MAX) -> dict[str, Any]:
    """模板层（可带环）→ 执行层（恒 DAG）。回边展开为下一迭代副本，上限 edge.max。"""
    forward_out: dict[str, list[str]] = {}
    feedback: list[SpecEdge] = []
    for edge in spec.edges:
        if edge.kind == "sequence":
            forward_out.setdefault(edge.src, []).append(edge.dst)
        else:
            feedback.append(edge)

    copies: list[RunNode] = []
    run_edges: list[RunEdge] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(spec.entry, 0)]
    while queue:
        node_id, iteration = queue.pop(0)
        copy_id = f"{node_id}#it{iteration}"
        if copy_id in seen:
            continue
        seen.add(copy_id)
        copies.append(RunNode(id=copy_id, source_node_id=node_id, iteration=iteration, node=spec.node(node_id)))
        for nxt in forward_out.get(node_id, []):
            queue.append((nxt, iteration))
        for edge in feedback:
            if edge.src != node_id:
                continue
            cap = edge.max if isinstance(edge.max, int) else default_max
            if iteration + 1 > cap:
                continue
            next_copy = f"{edge.dst}#it{iteration + 1}"
            run_edges.append(RunEdge(src=copy_id, dst=next_copy, kind="feedback", condition=edge.condition, iteration_from=iteration, iteration_to=iteration + 1))
            queue.append((edge.dst, iteration + 1))
        for dst in forward_out.get(node_id, []):
            run_edges.append(RunEdge(src=copy_id, dst=f"{dst}#it{iteration}", kind="sequence"))
    return {"spec_id": spec.id, "nodes": copies, "edges": run_edges, "entry": f"{spec.entry}#it0"}

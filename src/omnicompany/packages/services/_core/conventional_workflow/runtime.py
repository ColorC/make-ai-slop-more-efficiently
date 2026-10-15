# [OMNI] origin=qoder ts=2026-08-17 type=runtime
# [OMNI] material_id="material:packages.services._core.conventional_workflow.runtime"
"""WorkflowRunner：惯例工作流执行层。

铁律（继承 demogame 红线与决策 D1–D5）：
- preview 是纯函数求值，零副作用；
- 检查/审批通过不等于自动写入——写入只存在于显式副作用事务（commit_effects），
  且每笔事务带 preview/证据/可恢复引用；
- gate 节点必须显式裁决；run 完成默认停在 delivered-pending-review 语义由宿主掌握。
执行层图由 unroll_spec 展开（恒 DAG），实际路径按 verdict 与回边条件动态走。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .spec import RunEdge, RunNode, WorkflowSpec, unroll_spec

VERDICT_PASS = "PASS"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_FAIL = "FAIL"
VERDICTS = (VERDICT_PASS, VERDICT_PARTIAL, VERDICT_FAIL)

NODE_PENDING = "pending"
NODE_RUNNING = "running"
NODE_DONE = "done"
NODE_SKIPPED = "skipped"
NODE_GATE_PENDING = "gate-pending"
NODE_FAILED = "failed"


class GatePending(RuntimeError):
    """run 停在确认门，等待显式裁决。"""


@dataclass
class Verdict:
    result: str  # PASS | PARTIAL | FAIL
    note: str = ""
    evidence: list[Mapping[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.result not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}")


@dataclass
class WorkerOutput:
    verdict: Verdict
    outputs: dict[str, Any] = field(default_factory=dict)
    effects: list[Mapping[str, Any]] = field(default_factory=list)  # 副作用提议（未落盘）


WorkerFn = Callable[["RunContext"], WorkerOutput]
CheckFn = Callable[["RunContext"], Verdict]


class WorkerRegistry:
    """worker_ref / check_ref → 可执行体。M3 起接 demogame CLI 桥。"""

    def __init__(self) -> None:
        self._workers: dict[str, WorkerFn] = {}
        self._checks: dict[str, CheckFn] = {}

    def register_worker(self, ref: str, fn: WorkerFn) -> None:
        self._workers[ref] = fn

    def register_check(self, ref: str, fn: CheckFn) -> None:
        self._checks[ref] = fn

    def worker(self, ref: str) -> WorkerFn:
        if ref not in self._workers:
            raise KeyError(f"unregistered worker {ref}")
        return self._workers[ref]

    def check(self, ref: str) -> CheckFn:
        if ref not in self._checks:
            raise KeyError(f"unregistered check {ref}")
        return self._checks[ref]

    def has_worker(self, ref: str) -> bool:
        return ref in self._workers

    def has_check(self, ref: str) -> bool:
        return ref in self._checks


@dataclass
class RunContext:
    run_id: str
    copy_id: str
    node: RunNode
    inputs: dict[str, Any]
    upstream_outputs: dict[str, dict[str, Any]]
    params: dict[str, Any]
    dry_run: bool = True


class WorkflowRunner:
    def __init__(self, spec: WorkflowSpec, registry: WorkerRegistry, *, runs_dir: Path | None = None) -> None:
        self.spec = spec
        self.registry = registry
        self.runs_dir = runs_dir

    # ---- preview：纯求值，零副作用 ----
    def preview(self, *, default_max: int = 3) -> dict[str, Any]:
        unrolled = unroll_spec(self.spec, default_max=default_max)
        plan_nodes = []
        for run_node in unrolled["nodes"]:
            spec_node = run_node.node
            executor = spec_node.worker_ref if spec_node.kind == "worker" else spec_node.check_ref if spec_node.kind == "check" else None
            plan_nodes.append({
                "copy_id": run_node.id,
                "source_node_id": run_node.source_node_id,
                "iteration": run_node.iteration,
                "kind": spec_node.kind,
                "title": spec_node.title or run_node.source_node_id,
                "executor": executor,
                "registered": (
                    True if spec_node.kind == "gate"
                    else self.registry.has_worker(executor) if spec_node.kind == "worker"
                    else self.registry.has_check(executor) if spec_node.kind == "check"
                    else spec_node.kind == "anchor"
                ),
            })
        return {
            "spec_id": self.spec.id,
            "entry": unrolled["entry"],
            "nodes": plan_nodes,
            "edges": [{"from": e.src, "to": e.dst, "kind": e.kind, "condition": e.condition} for e in unrolled["edges"]],
        }

    # ---- 执行 ----
    def run(self, *, run_id: str | None = None, inputs: Mapping[str, Any] | None = None, dry_run: bool = True, default_max: int = 3, spec_path: str | None = None) -> dict[str, Any]:
        run_id = run_id or f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        unrolled = unroll_spec(self.spec, default_max=default_max)
        nodes: dict[str, RunNode] = {n.id: n for n in unrolled["nodes"]}
        out_edges: dict[str, list[RunEdge]] = {}
        for edge in unrolled["edges"]:
            out_edges.setdefault(edge.src, []).append(edge)
        indegree: dict[str, int] = {node_id: 0 for node_id in nodes}
        for edge in unrolled["edges"]:
            indegree[edge.dst] += 1

        state: dict[str, Any] = {
            "run_id": run_id,
            "spec_id": self.spec.id,
            "spec_path": spec_path,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dry_run": dry_run,
            "status": "running",
            "nodes": {},
            "outputs": {},
            "transactions": [],
            "gates": {},
        }
        upstream: dict[str, dict[str, Any]] = {}

        self._execute(state, ready=[unrolled["entry"]], nodes=nodes, out_edges=out_edges, indegree=indegree, inputs=inputs or {}, upstream=upstream, default_max=default_max)
        return state

    def _execute(self, state: dict[str, Any], *, ready: list[str], nodes: dict[str, RunNode], out_edges: dict[str, list[RunEdge]], indegree: dict[str, int] | None, inputs: Mapping[str, Any], upstream: dict[str, dict[str, Any]], default_max: int) -> None:
        visited: set[str] = set(state.get("_visited", []))
        while ready:
            copy_id = ready.pop(0)
            if copy_id in visited:
                continue
            run_node = nodes.get(copy_id)
            if run_node is None:
                continue  # 条件未触发的迭代副本不在实际路径上
            visited.add(copy_id)
            spec_node = run_node.node
            record: dict[str, Any] = {"copy_id": copy_id, "kind": spec_node.kind, "status": NODE_RUNNING}
            state["nodes"][copy_id] = record

            if spec_node.kind == "anchor":
                record["status"] = NODE_DONE
                state["outputs"][copy_id] = dict(inputs)
            elif spec_node.kind == "worker":
                context = RunContext(run_id=state["run_id"], copy_id=copy_id, node=run_node, inputs=dict(inputs), upstream_outputs=dict(upstream), params=dict(spec_node.params), dry_run=state.get("dry_run", True))
                output = self.registry.worker(spec_node.worker_ref)(context)
                record["status"] = NODE_DONE
                record["verdict"] = output.verdict.result
                state["outputs"][copy_id] = output.outputs
                if output.effects:
                    state["transactions"].append({
                        "tx_id": f"tx-{len(state['transactions']) + 1}",
                        "copy_id": copy_id,
                        "status": "proposed",  # 审批通过≠写入；须显式 commit_effects
                        "effects": list(output.effects),
                    })
            elif spec_node.kind == "check":
                context = RunContext(run_id=state["run_id"], copy_id=copy_id, node=run_node, inputs=dict(inputs), upstream_outputs=dict(upstream), params=dict(spec_node.params), dry_run=state.get("dry_run", True))
                verdict = self.registry.check(spec_node.check_ref)(context)
                record["status"] = NODE_DONE
                record["verdict"] = verdict.result
            elif spec_node.kind == "gate":
                record["status"] = NODE_GATE_PENDING
                state["gates"][copy_id] = {"decision": None, "decided_at": None}
                state["_visited"] = sorted(visited)
                self._persist(state)
                raise GatePending(f"run {state['run_id']} paused at gate {copy_id}; use approve_gate")

            upstream[copy_id] = state["outputs"].get(copy_id, {})
            for edge in out_edges.get(copy_id, []):
                if edge.kind == "feedback":
                    verdict = record.get("verdict")
                    if edge.condition and verdict != edge.condition:
                        continue  # 回边条件未满足：不进入下一迭代
                if indegree is not None:
                    indegree[edge.dst] -= 1
                    if indegree[edge.dst] <= 0:
                        ready.append(edge.dst)
                else:
                    ready.append(edge.dst)  # gate 续跑：下游 BFS，去重靠 visited

        state.pop("_visited", None)
        state["status"] = "finished" if all(
            item["status"] in (NODE_DONE, NODE_SKIPPED) for item in state["nodes"].values()
        ) else "finished-partial"
        state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._persist(state)

    # ---- gate 裁决 ----
    def approve_gate(self, run_id: str, copy_id: str, decision: str, *, actor: str = "human") -> dict[str, Any]:
        state = self.load(run_id)
        gate = state["gates"].get(copy_id)
        if gate is None:
            raise KeyError(f"no gate {copy_id} in run {run_id}")
        if decision not in ("pass", "reject"):
            raise ValueError("gate decision must be pass or reject")
        gate.update({"decision": decision, "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "actor": actor})
        state["nodes"][copy_id]["status"] = NODE_DONE
        state["nodes"][copy_id]["verdict"] = VERDICT_PASS if decision == "pass" else VERDICT_FAIL
        if decision == "pass":
            unrolled = unroll_spec(self.spec)
            nodes = {n.id: n for n in unrolled["nodes"]}
            out_edges: dict[str, list[RunEdge]] = {}
            for edge in unrolled["edges"]:
                out_edges.setdefault(edge.src, []).append(edge)
            upstream = {key: value for key, value in state.get("outputs", {}).items()}
            downstream = [edge.dst for edge in out_edges.get(copy_id, [])]
            if downstream:
                self._execute(state, ready=downstream, nodes=nodes, out_edges=out_edges, indegree=None, inputs={}, upstream=upstream, default_max=3)
            elif all(item["status"] in (NODE_DONE, NODE_SKIPPED) for item in state["nodes"].values()):
                state["status"] = "finished"
        else:
            state["status"] = "rejected"
        self._persist(state)
        return state

    # ---- 副作用事务：唯一写入入口 ----
    def commit_effects(self, run_id: str, tx_id: str, *, applier: Callable[[Mapping[str, Any]], None] | None = None) -> dict[str, Any]:
        state = self.load(run_id)
        for tx in state["transactions"]:
            if tx["tx_id"] == tx_id:
                if tx["status"] != "proposed":
                    raise ValueError(f"transaction {tx_id} already {tx['status']}")
                self._authorize_commit(state, tx)
                if applier is not None:
                    for effect in tx["effects"]:
                        applier(effect)
                tx["status"] = "committed"
                tx["committed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._persist(state)
                return tx
        raise KeyError(f"no transaction {tx_id} in run {run_id}")

    @staticmethod
    def _authorize_commit(state: Mapping[str, Any], tx: Mapping[str, Any]) -> None:
        """提交授权：门 reject 过、或存在未被后续迭代 PASS 覆盖的 FAIL 检查，一律拒。"""
        for gate_copy, gate in (state.get("gates") or {}).items():
            if gate.get("decision") == "reject":
                raise ValueError(f"commit refused: gate {gate_copy} was rejected")
        checks = [
            (copy_id, record) for copy_id, record in (state.get("nodes") or {}).items()
            if record.get("kind") == "check"
        ]
        for copy_id, record in checks:
            if record.get("verdict") != VERDICT_FAIL:
                continue
            source, _, iteration_part = copy_id.rpartition("#")
            iteration = int(iteration_part.replace("it", "")) if iteration_part.startswith("it") else 0
            overridden = any(
                other_copy.rpartition("#")[0] == source
                and other_record.get("verdict") == VERDICT_PASS
                and int(other_copy.rpartition("#")[2].replace("it", "") or 0) > iteration
                for other_copy, other_record in checks
            )
            if not overridden:
                raise ValueError(f"commit refused: check {copy_id} FAILED without a passing re-check")

    # ---- 状态存取 ----
    def _persist(self, state: Mapping[str, Any]) -> None:
        if self.runs_dir is None:
            return
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        target = self.runs_dir / f"{state['run_id']}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)

    def load(self, run_id: str) -> dict[str, Any]:
        if self.runs_dir is None:
            raise RuntimeError("runner has no runs_dir; cannot load persisted state")
        target = self.runs_dir / f"{run_id}.json"
        if not target.exists():
            raise FileNotFoundError(f"run {run_id} not found in {self.runs_dir}")
        return json.loads(target.read_text(encoding="utf-8"))

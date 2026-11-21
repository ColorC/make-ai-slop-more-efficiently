# [OMNI] origin=claude-code domain=omnicompany/tools ts=2026-04-14T00:00:00Z
# [OMNI] material_id="material:tools.step_pipeline_debugger.engine.py"
"""逐节点步进执行器 — 运行单个节点，保存/恢复中间状态。

用法（Python API）:
    from omnicompany.tools.step_runner import StepRunner

    runner = StepRunner(pipeline_name="demogame-table-learning", domain="demogame")
    result = await runner.run_step(
        node_id="schema_bootstrap",
        input_data=my_input,
        fixture_overrides={"p4_fetcher_fn": my_fetcher},  # 不可序列化的值
    )
    # 结果自动保存到 data/demogame/scratch/steps/schema_bootstrap.json

    # 下一节点：从上一步的保存状态恢复
    result2 = await runner.run_step(
        node_id="multi_version_diff",
        from_step="schema_bootstrap",           # 加载上一步的 JSON
        fixture_overrides={"p4_fetcher_fn": my_fetcher},
    )

用途：逐节点调试，观察每步输出，不依赖完整管线注册。
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SENTINEL = "<non-serializable>"


def _safe_serialize(obj: Any) -> Any:
    """递归地将不可序列化的值替换为描述字符串，用于 JSON 快照。

    dataclass 实例自动转为 {"__dataclass__": "ClassName", ...fields...} 格式，
    load_snapshot 时以普通 dict 返回（不重建类型，下游节点通过 fixture_overrides 获得真实对象）。
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = {"__dataclass__": type(obj).__name__}
        for f in dataclasses.fields(obj):
            d[f.name] = _safe_serialize(getattr(obj, f.name))
        return d
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if callable(obj):
        name = getattr(obj, "__name__", None) or getattr(obj, "__class__", {__name__: "?"})
        if not isinstance(name, str):
            name = type(obj).__name__
        return f"<callable:{name}>"
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return f"<non-serializable:{type(obj).__name__}>"


class StepRunner:
    """单节点步进执行器。

    - run_step(): 运行一个节点，保存输出快照
    - load_step(): 加载某节点的保存快照
    - 支持 fixture_overrides 注入不可序列化的值（callables 等）
    """

    def __init__(
        self,
        pipeline_name: str,
        domain: str = "demogame",
        steps_dir: str | Path | None = None,
        pipeline_input: dict | None = None,
    ):
        self.pipeline_name = pipeline_name
        self.domain = domain
        if steps_dir:
            self.steps_dir = Path(steps_dir)
        else:
            from omnicompany.core.config import resolve_db_dir
            self.steps_dir = resolve_db_dir(domain) / "scratch" / "steps"
        self.steps_dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, dict] = {}  # node_id → actual output (with callables)
        # pipeline_input 传递给 build_bindings，例如注入 xlsm_path / sheet_name
        self._pipeline_input = dict(pipeline_input or {})

    def _step_path(self, node_id: str) -> Path:
        return self.steps_dir / f"{node_id}.json"

    def save_snapshot(self, node_id: str, data: dict) -> Path:
        """将数据保存为 JSON 快照（不可序列化的值用描述替换）。"""
        snapshot = _safe_serialize(data)
        path = self._step_path(node_id)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Step snapshot saved: %s", path)
        return path

    def load_snapshot(self, node_id: str) -> dict:
        """从磁盘加载 JSON 快照（不含 callables，只含可序列化数据）。"""
        path = self._step_path(node_id)
        if not path.exists():
            raise FileNotFoundError(f"Step snapshot not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _get_router(self, node_id: str):
        """从已注册管线的 build_bindings 里取 Router 实例。"""
        from omnicompany.core.registry import get_or_raise
        from omnicompany.core.dispatch import _call_build_bindings
        entry = get_or_raise(self.pipeline_name)
        bindings = _call_build_bindings(entry, dict(self._pipeline_input))
        router = bindings.get(node_id)
        if router is None:
            raise KeyError(
                f"Node '{node_id}' not in bindings for '{self.pipeline_name}'. "
                f"Available: {sorted(bindings.keys())}"
            )
        return router

    async def run_step(
        self,
        node_id: str,
        input_data: dict | None = None,
        *,
        from_step: str | None = None,
        fixture_overrides: dict | None = None,
        print_output: bool = True,
    ) -> dict:
        """运行单个节点，保存输出快照，返回实际输出（含 callables）。

        Args:
            node_id:           节点 ID（与 TeamSpec 一致）
            input_data:        直接提供输入 dict（与 from_step 二选一）
            from_step:         从上一步的快照加载输入（node_id 字符串）
            fixture_overrides: 注入不可序列化的值（会覆盖 JSON 快照里的占位符）
            print_output:      是否打印摘要到 stdout
        """
        # ── 准备输入 ──
        if input_data is not None:
            merged = dict(input_data)
        elif from_step is not None:
            # 先查内存（有 callable），再回退到磁盘快照
            if from_step in self._memory:
                merged = dict(self._memory[from_step])
            else:
                merged = self.load_snapshot(from_step)
        else:
            raise ValueError("Must provide either input_data or from_step")

        if fixture_overrides:
            merged.update(fixture_overrides)

        # ── 取 Router ──
        router = self._get_router(node_id)

        # ── 执行 ──
        ts_start = datetime.now()
        if inspect.iscoroutinefunction(router.run):
            verdict = await router.run(merged)
        else:
            verdict = await asyncio.to_thread(router.run, merged)

        elapsed = (datetime.now() - ts_start).total_seconds()

        # ── 保存 ──
        output = verdict.output if verdict.output is not None else {}
        self._memory[node_id] = output          # 内存：含 callables，供下一步使用
        snap_path = self.save_snapshot(node_id, output)  # 磁盘：JSON 快照

        # ── 打印 ──
        if print_output:
            _print_step_result(node_id, verdict, elapsed, snap_path)

        return output

    def run_step_sync(self, node_id: str, **kwargs) -> dict:
        """同步版本，供非 async 环境调用。"""
        return asyncio.run(self.run_step(node_id, **kwargs))


def _print_step_result(node_id: str, verdict, elapsed: float, snap_path: Path) -> None:
    """格式化打印节点执行结果。"""
    from omnicompany.protocol.anchor import VerdictKind
    ok = verdict.kind == VerdictKind.PASS
    symbol = "PASS" if ok else "FAIL"
    print(f"\n{'='*60}")
    print(f"  [{symbol}]  Node: {node_id}  ({elapsed:.2f}s)")
    print(f"{'='*60}")
    if verdict.diagnosis:
        print(f"  diagnosis: {verdict.diagnosis}")
    if isinstance(verdict.output, dict):
        print(f"\n  output keys ({len(verdict.output)}):")
        for k, v in verdict.output.items():
            if callable(v):
                print(f"    {k}: <callable>")
            elif isinstance(v, list) and len(v) > 3:
                print(f"    {k}: [{type(v[0]).__name__}, ...] (len={len(v)})")
            elif isinstance(v, dict) and len(v) > 5:
                print(f"    {k}: {{...}} (keys={len(v)})")
            else:
                vstr = repr(v)
                if len(vstr) > 80:
                    vstr = vstr[:77] + "..."
                print(f"    {k}: {vstr}")
    print(f"\n  snapshot: {snap_path}")
    print(f"{'='*60}\n")

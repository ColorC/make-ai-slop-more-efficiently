# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="plan→task 拆分器: 走统一 run_json_agent 读 plan.md 拆成自包含 task 树(deps/test_strategy/复杂度/并行), 落 TaskStore"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-split: 过门 plan 拆成渐进执行 task; 抄 task-master 模型 + spec-kit [P] + BMAD 自包含"
# [OMNI] tags=lifecycle,split,task,plan,unified-agent
# [OMNI] material_id="material:services._core.lifecycle.splitter.py"
"""plan→task 拆分器。

走**统一** AgentNodeLoop(run_json_agent), 不 fork agent。读 plan.md → LLM 拆成
task 树 → 落 TaskStore。每个 task 自包含全部执行细节(BMAD story 理念), 带 test_strategy
(spec-kit 覆盖矩阵硬门要查), dependencies 用 1-based 序号(回填成 task id), 复杂度 1-10,
parallel = spec-kit [P] 标记。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from omnicompany.core.plans_catalogue import _plans_root
from omnicompany.packages.services._core.agent.launch import run_json_agent
from omnicompany.packages.services._core.lifecycle.task import (
    VALID_PRIORITY,
    Task,
    TaskStore,
)

_SPLIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "split_rationale": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "details": {"type": "string"},
                    "test_strategy": {"type": "string"},
                    "priority": {"type": "string"},
                    "complexity": {"type": "integer"},
                    "parallel": {"type": "boolean"},
                    "reasoning": {"type": "string"},
                    "file_scope": {"type": "array", "items": {"type": "string"}},
                    "expected_outputs": {"type": "array", "items": {"type": "string"}},
                    "workload": {"type": "integer"},
                    "difficulty": {"type": "integer"},
                    "dependencies": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "details", "test_strategy", "file_scope", "expected_outputs"],
            },
        }
    },
    "required": ["tasks"],
}

_NODE_PROMPT = """你是 plan→task 拆分器。读给定的 plan.md, 拆成一组**渐进执行**的 task。

**粒度优先粗(关键)**: 每个 task = 一次独立 headless 会话, 会冷启重新探索代码 + 各自验证, 固定开销大。
所以**偏向少而大的 task**, 别过度细拆:
- 触碰**同一批文件 / 属同一特性 / 必须线性接力**的工作 → **合并成一个 task**(让一个会话连贯做完, 共享上下文, 只验证一次)。
- 只在以下情况才拆成多个 task: ① 彼此**真正独立**(不同文件/不同关注点) ② 可**并行**(parallel=true) ③ 单个会话**装不下**(范围过大/复杂度>7)。
- 经验值: 一个中等 plan 通常 2-5 个 task 足够; 别拆成 8+ 个微任务。宁可一个 task 多改几个相关文件, 也别为"每个文件一个 task"而拆。

其余硬要求(下游覆盖矩阵硬门会查):
- 覆盖所有需求与产物, 不漏。
- 每个 task 的 details **自包含全部执行细节**(要改哪些文件/复用哪些现成设施/边界), 让执行 agent 不必回看 plan。
- 每个 task 必须有 test_strategy(怎么验证它做完了, 可跑/可查)。
- 不要残留 `[NEEDS CLARIFICATION: ...]`; 真有歧义就在 details 写清假设。
- dependencies 用任务在本数组里的 1-based 序号(前置任务序号), 无依赖给 []。
- priority ∈ high|medium|low; complexity 1-10; parallel=true 表示可与同层并行(spec-kit [P])。
- 每个 task 一句 reasoning(为何这样一块, 而不是更碎)。
- 顶层 split_rationale 写整体拆分思路 + **为什么是这个粒度**(哪些合并了、为什么没拆更细)。

**动手前先写好(每个 task 必填, 让人/调度都能提前判清)**:
- **file_scope**: 这件事会碰哪些文件/目录(具体路径, 尽量准)。**这是判并行的依据**: 两个 task 的 file_scope 不重叠才能同时跑, 重叠就得排队。所以拆分时也要尽量让可并行的 task **文件范围互不重叠**。
- **expected_outputs**: 预计产出什么(新建/改的文件、新增命令、要提交的审阅物料)。
- **workload**: 工作量估分 1-10(规模/体量: 要写多少、动多少处)。
- **difficulty**: 难度估分 1-10(不确定性/技术难度: 多少地方拿不准、要试错)。

只输出 JSON: {"split_rationale": "...", "tasks": [{title, description, details, test_strategy, priority, complexity, parallel, reasoning, file_scope:[...], expected_outputs:[...], workload, difficulty, dependencies:[...]}]}"""


async def _run_split(plan_id: str, model: str | None) -> dict[str, Any]:
    plan_md = _plans_root() / plan_id / "plan.md"
    task = (
        f"读 plan 文件: {plan_md}\n"
        f"(plan_id = {plan_id})\n"
        f"按 NODE_PROMPT 的硬要求把它拆成 task 树, 输出 JSON。"
    )
    kwargs: dict[str, Any] = {
        "task": task,
        "node_prompt": _NODE_PROMPT,
        "result_schema": _SPLIT_SCHEMA,
        "project_root": str(_plans_root().parent.parent),  # omni workspace root
        "max_turns": 20,
        "caller": "plan_split",
    }
    if model:
        kwargs["model"] = model
    return await run_json_agent(**kwargs)


def split_plan_to_tasks(
    plan_id: str, *, model: str | None = None, replace: bool = True
) -> dict[str, Any]:
    """拆 plan → 落 TaskStore。返回 {ok, plan_id, created:[task...], error, agent}。"""
    plan_md = _plans_root() / plan_id / "plan.md"
    if not plan_md.is_file():
        return {"ok": False, "plan_id": plan_id, "created": [], "error": f"plan.md 不存在: {plan_md}"}

    result = asyncio.run(_run_split(plan_id, model))
    if not result.get("ok"):
        return {
            "ok": False, "plan_id": plan_id, "created": [],
            "error": f"拆分 agent 失败: {result.get('error')}",
            "agent": {"turn_count": result.get("turn_count"), "text_preview": (result.get("text") or "")[:300]},
        }

    raw_tasks = (result.get("final") or {}).get("tasks", [])
    if not raw_tasks:
        return {"ok": False, "plan_id": plan_id, "created": [], "error": "agent 没产出任何 task"}

    store = TaskStore()
    if replace:
        store.replace_all(plan_id, [])

    # 第一遍: 建 task 占位(暂不接依赖), 记录 序号→task_id
    idx_to_id: dict[int, str] = {}
    created: list[Task] = []
    for i, rt in enumerate(raw_tasks, start=1):
        prio = str(rt.get("priority") or "medium").lower()
        if prio not in VALID_PRIORITY:
            prio = "medium"
        def _score(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        t = store.add(
            plan_id,
            title=str(rt.get("title") or f"task {i}"),
            description=str(rt.get("description") or ""),
            details=str(rt.get("details") or ""),
            test_strategy=str(rt.get("test_strategy") or ""),
            priority=prio,
            complexity=_score(rt.get("complexity")),
            parallel=bool(rt.get("parallel", False)),
            reasoning=str(rt.get("reasoning") or ""),
            file_scope=[str(x) for x in (rt.get("file_scope") or [])],
            expected_outputs=[str(x) for x in (rt.get("expected_outputs") or [])],
            workload=_score(rt.get("workload")),
            difficulty=_score(rt.get("difficulty")),
        )
        idx_to_id[i] = t.id
        created.append(t)

    # 第二遍: 回填依赖(序号→task_id)
    for i, rt in enumerate(raw_tasks, start=1):
        deps_idx = rt.get("dependencies") or []
        dep_ids = [idx_to_id[d] for d in deps_idx if isinstance(d, int) and d in idx_to_id]
        if dep_ids:
            store.update(idx_to_id[i], plan_id=plan_id, dependencies=dep_ids)

    cycle = store.detect_dependency_cycle(plan_id)
    split_rationale = str((result.get("final") or {}).get("split_rationale") or "")

    # 落盘拆分记录(含整体拆分思路与每 task 拆分理由)
    from omnicompany.core.config import omni_workspace_root

    splits_dir = omni_workspace_root() / "data" / "lifecycle" / "splits"
    splits_dir.mkdir(parents=True, exist_ok=True)
    plan_safe = re.sub(r"[^A-Za-z0-9._-]", "_", plan_id)
    split_record = {
        "plan_id": plan_id,
        "split_rationale": split_rationale,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "complexity": t.complexity,
                "priority": t.priority,
                "parallel": t.parallel,
                "dependencies": t.dependencies,
                "reasoning": t.reasoning,
                "file_scope": t.file_scope,
                "expected_outputs": t.expected_outputs,
                "workload": t.workload,
                "difficulty": t.difficulty,
            }
            for t in store.list_tasks(plan_id)
        ],
    }
    splits_path = splits_dir / f"{plan_safe}.json"
    tmp = splits_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(split_record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(splits_path)

    return {
        "ok": True,
        "plan_id": plan_id,
        "split_rationale": split_rationale,
        "created": [t.to_dict() for t in store.list_tasks(plan_id)],
        "count": len(created),
        "dependency_cycle": cycle,
        "error": "" if not cycle else f"⚠ 检出循环依赖: {cycle}",
    }


__all__ = ["split_plan_to_tasks"]

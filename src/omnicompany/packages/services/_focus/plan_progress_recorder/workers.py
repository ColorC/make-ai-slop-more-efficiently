# [OMNI] origin=claude-code domain=services/_focus ts=2026-06-23T00:00:00Z type=worker
# [OMNI] material_id="material:services._focus.plan_progress_recorder.workers.py"
"""plan-progress-recorder 的两个 Worker。

  planprog.request → [Extractor 脑子: gpt-5.5 tool-agent 读计划自评] → planprog.assessment
                   → [Recorder 手: 确定性 POST 进 whatnow]           → planprog.recorded(sink)

SOFT(Extractor) 紧跟 HARD(Recorder) —— 对齐 team.md P-04 / worker.md R-05。
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.agent.launch import run_json_agent  # 统一 AgentNodeLoop 启动器
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

WHATNOW = "http://127.0.0.1:8230"

ASSESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "line", "status", "completion", "progress_note"],
    "properties": {
        "title": {"type": "string"},
        "line": {"type": "string", "enum": ["main", "side"]},
        "status": {"type": "string", "enum": ["todo", "in_progress", "paused", "done"]},
        "completion": {"type": "integer"},
        "progress_note": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}

_EXTRACT_PROMPT = """你是计划进度评估员(只读)。给你一个计划目录, 用 read_file/list_dir/grep 进去看真东西:
1. list_dir 计划目录, read_file 读 plan.md(看 frontmatter summary + 正文 Workstream 的"已完成/部分完成/撞死/✅/✗"标记)。
2. 进子目录看证据产物(各 RESULT.md / progress_log.md / check 结果等), 用 grep 找"完成/已通过/PASS/撞死/待补/TODO"等线索。
3. 综合给最终评估, 调 finish 返回满足 schema 的 JSON:
   - title: 计划中文标题(取 plan.md 的 H1 或 summary 提炼)
   - line: "main"(北极星/主线大工程) 或 "side"(支线/小收尾)
   - status: todo / in_progress / paused / done
   - completion: 0-100 整数, 用证据支撑别拍脑袋
   - progress_note: 一句话最新进展(中文, ≤40字)
   - evidence: 3-6 条具体证据(形如 "WS4 RESULT.md 7/7 通过" / "plan.md 标 WS5 撞死"), 指向真实文件/标记
别臆造没读到的东西。只用 finish 返回 JSON。"""


class PlanProgressExtractorWorker(Worker):
    """脑子: 统一 AgentNodeLoop(gpt-5.5) 进计划目录读真产物, 自评进度(SOFT)。"""

    DESCRIPTION = (
        "计划进度提取器(SOFT)。借**统一** AgentNodeLoop(run_json_agent, gpt-5.5, 只读 read/grep/list_dir)"
        "读 plan.md 及 RESULT.md/progress 等证据, 自评 completion/status/line/progress_note/evidence, "
        "产出 planprog.assessment。读不到计划目录则 FAIL。"
    )
    FORMAT_IN = "planprog.request"
    FORMAT_OUT = "planprog.assessment"

    async def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get("planprog.request") or {}
        plan_id = str(req.get("plan_id", "")).strip()
        if not plan_id:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="planprog.request 缺 plan_id")
        omni_root = Path(omni_workspace_root())
        root = Path(req.get("plan_root") or (omni_root / "docs" / "plans"))
        plan_dir = (root / plan_id).resolve()
        if not plan_dir.is_dir():
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"计划目录不存在: {plan_dir} (plan_id={plan_id})")

        res = await run_json_agent(
            task=f"评估这个计划的进度。计划目录(绝对路径)={plan_dir}; plan_id={plan_id}。",
            node_prompt=_EXTRACT_PROMPT,
            result_schema=ASSESS_SCHEMA,
            project_root=str(omni_root),
            model="gpt-5.5",
            caller="planprog.extractor",
            max_turns=16,
        )
        if not res["ok"] or not isinstance(res["final"], dict):
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"AgentNodeLoop 评估失败: {res.get('error') or '无 final'}")
        a = res["final"]
        comp = max(0, min(100, int(a.get("completion", 0))))
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "plan_id": plan_id,
                "task_id": str(req.get("task_id", "")),
                "title": str(a.get("title", plan_id))[:120],
                "line": a.get("line", "side") if a.get("line") in ("main", "side") else "side",
                "status": a.get("status", "in_progress"),
                "completion": comp,
                "progress_note": str(a.get("progress_note", ""))[:120],
                "evidence": [str(e)[:160] for e in (a.get("evidence") or [])][:8],
                # 透传 goal_id: Recorder 在 board 里查无对应 task 时, 用它建一条新 task(新计划自动纳管)
                "goal_id": str(req.get("goal_id", "")).strip(),
                "_agent_turns": res.get("turn_count", 0),
            },
        )


def _plan_task_id(plan_id: str) -> str:
    """新建 task 的确定性 id(与历史 p_<sanitized plan_id> 约定一致, 保证幂等不重复建)。"""
    return "p_" + re.sub(r"[^A-Za-z0-9]+", "_", plan_id).strip("_")


def _get(path: str) -> dict:
    with urllib.request.urlopen(WHATNOW + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(WHATNOW + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _match_task_by_plan(board: dict, plan_id: str) -> str:
    """在 board 里按 plan_id 找 task: 精确 → 后缀(去 [日期]/路径前缀) 双匹配。"""
    leaf = plan_id.rstrip("/").split("/")[-1]
    cand_exact, cand_suffix = "", ""
    for c in board.get("clusters", []):
        for g in c.get("goals", []):
            for t in g.get("tasks", []):
                pid = (t.get("plan_id") or "").strip()
                if not pid:
                    continue
                if pid == plan_id:
                    cand_exact = t["id"]
                elif pid.endswith(plan_id) or plan_id.endswith(pid) or pid.split("/")[-1] == leaf:
                    cand_suffix = cand_suffix or t["id"]
    return cand_exact or cand_suffix


def _exec_subtask_rate(subs: list[dict]) -> int | None:
    """执行子任务完成率(TASK-SSOT-UNIFICATION §N-completion)。

    计划被 `omni plan split` 拆过时, board 的计划 task 下挂执行子任务(parent_task_id);
    完成率 = done / 非 cancelled 子任务。无(有效)子任务 → None(完全交给 LLM 评估)。
    """
    valid = [s for s in subs if str(s.get("status", "")) != "cancelled"]
    if not valid:
        return None
    done = sum(1 for s in valid if str(s.get("status", "")) == "done")
    return round(100 * done / len(valid))


class WhatnowRecorderWorker(Worker):
    """手: 确定性把评估 POST 进 whatnow(:8230)（HARD, 紧跟 Extractor）。"""

    DESCRIPTION = (
        "whatnow 进度落地器(HARD/确定性)。校验 planprog.assessment 后, 解析目标 task"
        "(task_id 优先, 否则按 plan_id 在 /api/board 匹配), POST /api/progress + /api/task/patch, "
        "并复查 board 确认写入。whatnow 不可达或无匹配 task 则 FAIL+diagnosis(不静默)。产 planprog.recorded(sink)。"
    )
    FORMAT_IN = "planprog.assessment"
    FORMAT_OUT = "planprog.recorded"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        a = input_data.get("planprog.assessment") or {}
        plan_id = str(a.get("plan_id", ""))
        # whatnow 可达性 + 取 board。必须带 archived=1 与 sync 发现阶段同口径——
        # 否则已归档 task 在这里不可见, 会被误判成新计划重建(还把 archived 洗掉)。
        try:
            board = _get("/api/board?archived=1")
        except Exception as e:  # noqa: BLE001
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"whatnow 不可达(:8230): {e} —— 先确认服务在跑(start-whatnow.cmd)")
        task_id = str(a.get("task_id", "")).strip() or _match_task_by_plan(board, plan_id)
        created = False
        if not task_id:
            # 查无对应 task —— 新计划自动纳管: 有 goal_id 就建一条新 task, 否则才 FAIL(不静默)。
            goal_id = str(a.get("goal_id", "")).strip()
            if not goal_id:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    diagnosis=(f"whatnow board 里没有 plan_id={plan_id} 对应的 task, "
                               f"且未给 goal_id(无法新建归属), 无法记录"),
                    output={"plan_id": plan_id, "recorded": False, "whatnow_ok": True,
                            "note": "no matching task, no goal_id to create under"},
                )
            task_id = _plan_task_id(plan_id)
            try:
                _post("/api/tasks", {
                    "id": task_id, "goal_id": goal_id, "plan_id": plan_id,
                    "title": str(a.get("title", plan_id))[:120],
                    "status": a.get("status", "in_progress"),
                    "completion": max(0, min(100, int(a.get("completion", 0)))),
                    "line": a.get("line", "side"), "channel": "local",
                })
            except Exception as e:  # noqa: BLE001
                return Verdict(kind=VerdictKind.FAIL,
                               diagnosis=f"新建 whatnow task 失败(plan_id={plan_id}, goal={goal_id}): {e}")
            created = True
            board = _get("/api/board")  # 刷新, 让下面"当前完成度不倒退"能查到刚建的 task
        # 当前完成度(不倒退) + 执行子任务完成率(有子任务时三者取 max, §N-completion)
        cur = 0
        exec_rate: int | None = None
        for c in board.get("clusters", []):
            for g in c.get("goals", []):
                for t in g.get("tasks", []):
                    if t["id"] == task_id:
                        cur = int(t.get("completion", 0))
                        exec_rate = _exec_subtask_rate(t.get("subtasks") or [])
        new_c = max(cur, int(a.get("completion", 0)), exec_rate or 0)
        ev = " / ".join(a.get("evidence") or [])
        note = f"[plan-progress-recorder] {a.get('progress_note', '')}" + (f" | 证据: {ev}" if ev else "")
        try:
            _post("/api/progress", {"subject_kind": "task", "subject_id": task_id,
                                    "text": note, "source": "plan-progress-recorder"})
            _post("/api/task/patch", {"id": task_id, "completion": new_c,
                                      "status": a.get("status", "in_progress"),
                                      "line": a.get("line", "side")})
        except Exception as e:  # noqa: BLE001
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"whatnow 写入失败: {e}")
        # 复查
        ok = False
        try:
            b2 = _get("/api/board")
            for c in b2.get("clusters", []):
                for g in c.get("goals", []):
                    for t in g.get("tasks", []):
                        if t["id"] == task_id and int(t.get("completion", -1)) == new_c:
                            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "plan_id": plan_id, "task_id": task_id, "recorded": True,
                "created": created,
                "completion": new_c, "status": a.get("status", "in_progress"),
                "whatnow_ok": ok,
                "note": (f"task {task_id} {'NEW ' if created else ''}{cur}=>{new_c}% "
                         f"| {a.get('progress_note', '')[:40]}"),
            },
        )


ALL_WORKERS = [PlanProgressExtractorWorker, WhatnowRecorderWorker]

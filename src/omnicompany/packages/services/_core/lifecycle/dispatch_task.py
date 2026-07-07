# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="task 投递/观测/介入/兜底后端: 复用 winjump/会话扫描/agent_registry/external_workers, 不另造; 含 vscode 真实会话档 + sdk 受控档"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-dispatch/observe/intervene/backup: 双-agent 兜底的执行端"
# [OMNI] tags=lifecycle,dispatch,watch,inject,backup,sdk,vscode
# [OMNI] material_id="material:services._core.lifecycle.dispatch_task.py"
"""task 投递 / 观测 / 介入 / 兜底 — 全部复用现成设施。

- 投递 dispatch_task: vscode 真实会话(winjump 注入) / sdk 受控会话(run_external_agent_request)
- 观测 watch_target: 复用 ccdaemon _scan_claude/_scan_codex + _tail_lines, 实时逐行 + 卡死判断
- 介入 inject_to_task: 复用 winjump set_clipboard + activate_location(paste)
- 兜底 reassign_task / takeover_task: 改绑定 + 给接管上下文

绑定落 data/lifecycle/task_bindings.json (task 维度, 比 plan_worker_bindings 更细)。
公司机约束: 零窗口、禁 tmux、注入走 winjump(Windows API, 不弹控制台)/HTTP。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

STALL_SEC = 300  # 无新输出超过这个秒数 = 疑似卡死


def _bindings_path() -> Path:
    from omnicompany.core.config import omni_workspace_root

    d = omni_workspace_root() / "data" / "lifecycle"
    d.mkdir(parents=True, exist_ok=True)
    return d / "task_bindings.json"


def _load_bindings() -> dict[str, dict[str, Any]]:
    p = _bindings_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_bindings(b: dict[str, dict[str, Any]]) -> None:
    p = _bindings_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _store():
    from omnicompany.packages.services._core.lifecycle.task import TaskStore
    return TaskStore()


def list_task_bindings(plan_id: str | None = None) -> list[dict[str, Any]]:
    rows = list(_load_bindings().values())
    if plan_id:
        rows = [r for r in rows if r.get("plan_id") == plan_id]
    return rows


def compose_task_prompt(task: Any, plan_id: str) -> str:
    """组装投给 agent 的执行消息 (自包含 + 边做边记进度 + 审阅约定)。"""
    return (
        f"[omni task 投递] plan={plan_id} task#{task.id}\n"
        f"⚠ **全程中文**: 你产出的所有给人看的文字——进度 note、审阅报告、文档、提交说明,"
        f"以及你这一轮的**最终总结消息(会被当作执行自评进审阅台)**——一律用中文,不要英文。"
        f"用户是中文用户,这些会直接显示在审阅台给人看。代码标识符/命令/API 路径/文件名保留原文即可。"
        f"最终消息写成**干净的中文小结**(做了什么/改了哪些文件/验证结果),不要贴英文流水账思考过程。\n"
        f"## 任务: {task.title}\n"
        f"{task.description}\n\n"
        f"### 执行细节(自包含)\n{task.details}\n\n"
        f"### 完成验证(test_strategy)\n{task.test_strategy}\n\n"
        f"### 收尾约定\n"
        f"1) 做完后必做: `omni task update {task.id} --note \"用平实的中文人话写: 我具体做了什么、"
        f"改了哪些文件、跑了什么验证、结果如何\"` —— 记一条给人看的进度(中文, 别用术语缩写, 别空话)。"
        f"这条记录会直接进工作报告。\n"
        f"2) 若产出值得人审的东西(报告/页面/图), `omni review submit --plan-id {plan_id} ...` 推审阅"
        f"(报告正文必须中文)。\n"
        f"3) `omni task complete {task.id}`。"
    )


def _resolve_agent_session(agent: str | None) -> dict[str, Any] | None:
    """把 agent (会话 key / 名字 / 身份) 解析成 agent_registry 记录。"""
    if not agent:
        return None
    try:
        from omnicompany.dashboard.boss_sight.services.agent_registry import load_registry
        reg = load_registry()
    except Exception:
        return None
    if agent in reg:
        return {"key": agent, **reg[agent]}
    for key, rec in reg.items():
        if rec.get("name") == agent or rec.get("identity") == agent or rec.get("session_id") == agent:
            return {"key": key, **rec}
    return None


def dispatch_task(task_id: str, *, agent: str | None = None, plan_id: str | None = None,
                  carrier: str = "vscode", dry: bool = False,
                  cwd: str | None = None, auto_submit_material: bool = True) -> dict[str, Any]:
    """投递一个 task 给 agent 执行。"""
    store = _store()
    t = store.get(task_id, plan_id)
    if not t:
        return {"ok": False, "summary": f"task 不存在: {task_id}"}
    pid = t.plan_id
    prompt = compose_task_prompt(t, pid)

    if dry:
        return {"ok": True, "dry": True, "task_id": t.id, "plan_id": pid,
                "carrier": carrier, "agent": agent, "prompt": prompt,
                "summary": f"[dry] 将投递 task[{t.id}] → {agent or '自动路由'} via {carrier}"}

    # 记绑定 + 状态
    binds = _load_bindings()
    binds[t.id] = {"task_id": t.id, "plan_id": pid, "agent": agent, "carrier": carrier,
                   "status": "dispatched", "bound_at": time.time()}
    _save_bindings(binds)
    try:
        if t.status == "pending":
            store.set_status(t.id, "in_progress", pid)
    except Exception:
        pass
    if agent:
        try:
            store.update(t.id, plan_id=pid, assignee=agent)
        except Exception:
            pass

    if carrier == "sdk":
        return _dispatch_sdk(t, pid, prompt, agent, cwd=cwd,
                             auto_submit_material=auto_submit_material)
    return _dispatch_vscode(t, pid, prompt, agent)


def _dispatch_vscode(task: Any, plan_id: str, prompt: str, agent: str | None) -> dict[str, Any]:
    """vscode 真实会话档: winjump 注入到目标窗口 (注入失败=绑定已记, 留人工接)。"""
    rec = _resolve_agent_session(agent)
    location = (rec or {}).get("location") or "vscode"
    try:
        from omnicompany.dashboard.boss_sight.services import winjump
        winjump.set_clipboard(prompt)
        res = winjump.activate_location(location, paste=True)
        injected = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "task_id": task.id, "carrier": "vscode", "agent": agent,
                "injected": False, "injection_error": str(e),
                "summary": f"✓ task[{task.id}] 已绑定 {agent or location}(注入未成: {e}; 绑定已记, 可 omni task inject 重试)"}
    return {"ok": True, "task_id": task.id, "carrier": "vscode", "agent": agent,
            "location": location, "injected": injected,
            "summary": f"✓ task[{task.id}] 投递到 {agent or location}{' 并注入' if injected else ' (注入待确认)'}"}


def _dispatch_sdk(task: Any, plan_id: str, prompt: str, agent: str | None,
                  *, cwd: str | None = None, auto_submit_material: bool = True) -> dict[str, Any]:
    """sdk 受控会话档: 复用 run_external_agent_request 起受控 Claude Code 会话。"""
    import asyncio

    from omnicompany.core.config import omni_workspace_root
    from omnicompany.packages.services._core.agent.external_workers import (
        ExternalAgentPermissionMode,
        ExternalAgentRunRequest,
        run_external_agent_request,
    )

    import os as _os
    # 重任务(前端整页/多特性)600s 不够; 用 OMNI_TASK_TIMEOUT_S 调大, 默认 600 不变(opt-in)。
    try:
        _timeout_s = float(_os.environ.get("OMNI_TASK_TIMEOUT_S", "600"))
    except ValueError:
        _timeout_s = 600.0
    req = ExternalAgentRunRequest(
        provider="claude-code",
        prompt=prompt,
        cwd=cwd or str(omni_workspace_root()),
        run_id=f"task-{task.id}",
        permission_mode=ExternalAgentPermissionMode.WORKSPACE_WRITE,
        trace_id=f"task-{task.id}",
        timeout_s=_timeout_s,
        metadata={"task_id": task.id, "plan_id": plan_id},
    )
    try:
        result = asyncio.run(run_external_agent_request(req))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "task_id": task.id, "carrier": "sdk",
                "summary": f"sdk 受控会话起失败: {e}"}
    status = getattr(result, "status", None)
    status_val = str(getattr(status, "value", status) or "").lower()
    ok = status_val in {"succeeded", "ok", "completed", "success"}
    changed = list(getattr(result, "changed_files", []) or [])
    final_text = (getattr(result, "final_text", "") or "")
    material_id = None
    if ok:
        try:
            _store().set_status(task.id, "review", plan_id)  # 跑完进审阅, 不直接 done
        except Exception:
            pass
        if auto_submit_material:  # 编排端自动提交物料(关上 agent 被沙箱挡的闭环缺口)
            sub = submit_run_material(task, plan_id, final_text=final_text, changed_files=changed)
            material_id = sub.get("material_id")
    return {"ok": ok, "task_id": task.id, "carrier": "sdk", "status": status_val,
            "changed_files": changed, "material_id": material_id,
            "final_text": final_text[:300],
            "summary": f"{'✓' if ok else '✗'} task[{task.id}] sdk 受控会话 status={status_val} "
                       f"(changed_files={len(changed)}, material={material_id or '-'})"}


def submit_run_material(task: Any, plan_id: str, *, final_text: str = "",
                        changed_files: list[str] | None = None,
                        tier: str = "processual") -> dict[str, Any]:
    """编排端**自动**把一次 task 执行的产物提交成审阅物料(不依赖 agent 自己跑 Bash)。

    这关上了"agent 干完活但闭环那步被沙箱挡"的缺口: agent 只管干, orchestrator 提审阅。
    """
    changed = changed_files or []
    body = (
        f"# task#{getattr(task, 'id', '?')} {getattr(task, 'title', '')}\n\n"
        f"**plan**: {plan_id}\n\n"
        f"## 执行自评\n{final_text or '(无)'}\n\n"
        f"## 改动文件 ({len(changed)})\n"
        + ("\n".join(f"- `{f}`" for f in changed) if changed else "(git 检测无改动 / 非 git cwd)")
        + f"\n\n## 完成验证标准\n{getattr(task, 'test_strategy', '') or '(无)'}\n"
    )
    try:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
        title = f"task#{getattr(task,'id','?')} 执行产物: {getattr(task,'title','')}"[:80]
        # 编排端自动提交: 义务在 agent 不在人, 项目/轨道标签不阻塞这条自动闭环
        # (与 reviewstage/routes.py 的 /capture、/from_path 人工快捷端点同一处理:
        # project="unfiled" + track="task-execution" + version=1)。
        m = get_store().create(
            kind="markdown", tier=tier,
            title=title,
            source_plan_id=plan_id,
            source_subagent_id=f"task-{getattr(task,'id','?')}",
            inline_content=body,
            project="unfiled",
            track="task-execution",
            version=1,
        )
        mid = getattr(m, "id", None) or getattr(m, "material_id", None)
        return {"ok": True, "material_id": mid}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _all_live_sessions() -> list[dict[str, Any]]:
    """复用 ccdaemon 扫描, 返回所有本机会话 (含 transcript 文件路径)。"""
    try:
        from omnicompany.dashboard.ccdaemon.import_routes import _scan_claude, _scan_codex
        return list(_scan_claude()) + list(_scan_codex())
    except Exception:
        return []


def watch_target(target: str, *, lines: int = 40) -> dict[str, Any]:
    """实时观测某 task/plan 在哪个 agent 跑、跑到哪 (逐行 tail + 卡死判断)。

    target 可为 task_id 或 plan_id。先查绑定找 agent, 再扫会话 tail 它的 transcript。
    """
    from omnicompany.dashboard.ccdaemon.import_routes import _tail_lines

    binds = _load_bindings()
    if target in binds:
        rel = [binds[target]]
    else:
        rel = [b for b in binds.values() if b.get("plan_id") == target]
    if not rel:
        return {"ok": True, "target": target, "sessions": [],
                "note": "该 task/plan 还没有绑定到任何 agent (omni task dispatch 后才有)"}

    live = {s.get("session_id"): s for s in _all_live_sessions()}
    now = time.time()
    out_sessions: list[dict[str, Any]] = []
    for b in rel:
        rec = _resolve_agent_session(b.get("agent"))
        sid = (rec or {}).get("session_id") or b.get("agent")
        sess = live.get(sid)
        if not sess and rec:
            # 用 registry 里的 cwd/mtime 兜底
            sess = {"session_id": sid, "mtime": rec.get("mtime"), "file": None}
        if not sess:
            out_sessions.append({"task_id": b.get("task_id"), "agent": b.get("agent"),
                                 "session_id": sid, "run_status": "未找到活会话", "tail": []})
            continue
        mtime = sess.get("mtime") or 0
        idle = int(now - mtime) if mtime else None
        tail: list[str] = []
        fp = sess.get("file")
        if fp:
            try:
                tail = _tail_lines(Path(fp), max_lines=lines)
            except Exception:
                tail = []
        out_sessions.append({
            "task_id": b.get("task_id"),
            "agent": b.get("agent"),
            "session_id": sid,
            "run_status": (rec or {}).get("run_status") or "?",
            "idle_sec": idle,
            "stalled": bool(idle is not None and idle > STALL_SEC),
            "tail": tail[-lines:],
        })
    return {"ok": True, "target": target, "sessions": out_sessions}


def inject_to_task(task_id: str, message: str, *, plan_id: str | None = None) -> dict[str, Any]:
    """向 task 所在会话注入一条消息 (winjump 注入到目标窗口)。"""
    binds = _load_bindings()
    b = binds.get(task_id)
    if not b:
        return {"ok": False, "summary": f"task[{task_id}] 未绑定到会话 (先 omni task dispatch)"}
    rec = _resolve_agent_session(b.get("agent"))
    location = (rec or {}).get("location") or "vscode"
    try:
        from omnicompany.dashboard.boss_sight.services import winjump
        winjump.set_clipboard(message)
        res = winjump.activate_location(location, paste=True)
        ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "summary": f"注入失败: {e}"}
    return {"ok": ok, "task_id": task_id, "location": location,
            "summary": f"{'✓' if ok else '✗'} 注入到 {b.get('agent') or location}: {message[:40]}"}


def reassign_task(task_id: str, agent: str, *, plan_id: str | None = None) -> dict[str, Any]:
    """卡死/质量差时把 task 重派给另一个 agent。"""
    store = _store()
    t = store.get(task_id, plan_id)
    if not t:
        return {"ok": False, "summary": f"task 不存在: {task_id}"}
    binds = _load_bindings()
    prev = (binds.get(task_id) or {}).get("agent")
    binds[task_id] = {"task_id": task_id, "plan_id": t.plan_id, "agent": agent,
                      "carrier": (binds.get(task_id) or {}).get("carrier", "vscode"),
                      "status": "reassigned", "bound_at": time.time(), "prev_agent": prev}
    _save_bindings(binds)
    try:
        store.update(task_id, plan_id=t.plan_id, assignee=agent)
    except Exception:
        pass
    return {"ok": True, "task_id": task_id, "from": prev, "to": agent,
            "summary": f"✓ task[{task_id}] 重派 {prev or '?'} → {agent}"}


def takeover_task(task_id: str, *, plan_id: str | None = None) -> dict[str, Any]:
    """外部对话本地兜底接管 task: 标记 + 给出接管上下文 (task 详情 + 前一会话最近输出)。"""
    store = _store()
    t = store.get(task_id, plan_id)
    if not t:
        return {"ok": False, "summary": f"task 不存在: {task_id}"}
    binds = _load_bindings()
    b = binds.get(task_id) or {}
    prev = b.get("agent")
    binds[task_id] = {**b, "task_id": task_id, "plan_id": t.plan_id,
                      "agent": "external-backup", "status": "takeover",
                      "bound_at": time.time(), "prev_agent": prev}
    _save_bindings(binds)
    try:
        store.update(task_id, plan_id=t.plan_id, assignee="external-backup")
    except Exception:
        pass
    w = watch_target(task_id, lines=20)
    recent = []
    for s in w.get("sessions", []):
        recent.extend(s.get("tail", [])[-10:])
    ctx = (
        f"## 接管 task[{t.id}] {t.title}\n"
        f"plan={t.plan_id}\n前一会话: {prev}\n\n"
        f"### 执行细节\n{t.details}\n\n### 完成验证\n{t.test_strategy}\n"
        + (("\n### 前一会话最近输出\n" + "\n".join(recent)) if recent else "")
    )
    return {"ok": True, "task_id": task_id, "from": prev,
            "summary": f"✓ task[{task_id}] 由外部兜底接管 (前: {prev or '?'})",
            "context": ctx}


__all__ = [
    "list_task_bindings", "compose_task_prompt", "dispatch_task",
    "watch_target", "inject_to_task", "reassign_task", "takeover_task",
]

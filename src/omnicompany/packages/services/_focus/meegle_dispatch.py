# [OMNI] origin=codex domain=services/_focus ts=2026-07-24 type=infrastructure status=active
# [OMNI] summary="Meegle 当前待办到现有 Agent 与 Skill 的薄派发桥；默认只观察/路由，真实执行由显式 start 触发"
# [OMNI] tags=meegle,intake,agent,skill,dispatch
"""Meegle 待办的最短派发桥。

来源任务仍以 progress-service 为唯一真源；Agent、Skill 和执行分别复用现有
agent_registry、DiscoverSkills/Skill 与 ExternalAgentRunRequest。这里不管理模型、
预算、权限策略，也不另建任务队列。
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.routers.single_tool import ToolContext
from omnicompany.packages.services._core.agent.routers.skill_tools import discover_skills

PROGRESS_SERVICE = "http://127.0.0.1:8230"

_GENERIC_TERMS = {
    "一个",
    "任务",
    "功能",
    "固定",
    "处理",
    "工作",
    "开始",
    "当前",
    "相关",
    "系统",
    "进行",
    "项目",
    "需求",
    "需要",
    "支持",
    "使用",
}

_TERM_ALIASES = {
    # Meegle 标题里的常见口语/错别字；只扩召回，不直接决定 Skill。
    "录频": {"录屏", "录像", "录制"},
}


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def list_current_meegle_items(
    *,
    board: dict[str, Any] | None = None,
    service_url: str = PROGRESS_SERVICE,
) -> list[dict[str, Any]]:
    """读取当前 Meegle 收件箱；不创建第二份任务存储。"""
    payload = board if board is not None else _get_json(f"{service_url.rstrip('/')}/api/board")
    items = []
    for task in payload.get("loose_tasks") or []:
        refs = [str(ref) for ref in task.get("external_refs") or []]
        if task.get("source_active") is False:
            continue
        if task.get("channel") == "meego" or any(ref.startswith("meego:") for ref in refs):
            items.append(dict(task))
    return items


def observe_meegle_intake(
    *,
    board: dict[str, Any] | None = None,
    service_url: str = PROGRESS_SERVICE,
) -> dict[str, Any]:
    """只读盘点，不调用 LLM，也不启动真实任务。"""
    items = list_current_meegle_items(board=board, service_url=service_url)
    states: dict[str, int] = {}
    projects: dict[str, int] = {}
    for item in items:
        state = str(item.get("status") or "(空)")
        project = str(item.get("source_project") or "(未记录)")
        states[state] = states.get(state, 0) + 1
        projects[project] = projects.get(project, 0) + 1
    return {
        "ok": True,
        "mode": "observe",
        "items": len(items),
        "unrouted": sum(1 for item in items if not item.get("dispatch_status")),
        "routed": sum(1 for item in items if item.get("dispatch_status") == "routed"),
        "needs_user": sum(1 for item in items if item.get("dispatch_status") == "needs_user"),
        "needs_controller": sum(
            1 for item in items if item.get("dispatch_status") == "needs_controller"
        ),
        "started": sum(1 for item in items if item.get("dispatch_status") == "started"),
        "states": states,
        "source_projects": projects,
        "execution_started": False,
    }


def _terms(text: str) -> set[str]:
    normalized = text.casefold()
    terms = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_.+-]{1,}", normalized)
        if len(token) >= 3
    }
    for run in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
        if len(run) <= 10:
            terms.add(run)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    for source, aliases in _TERM_ALIASES.items():
        if source in normalized:
            terms.update(aliases)
    return {term for term in terms if term not in _GENERIC_TERMS}


def shortlist_skills(
    item: dict[str, Any],
    catalog: list[dict[str, str]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """只做低成本召回；最终选择仍由总控路由器在候选内完成。"""
    title = str(item.get("title") or "")
    project = str(item.get("source_project") or "")
    query_terms = _terms(title)
    project_key = re.sub(r"[^a-z0-9]+", "", project.casefold())
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for skill in catalog:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        description = str(skill.get("description") or "")
        haystack = f"{name} {description}".casefold()
        score = sum(max(2, len(term)) for term in query_terms if term in haystack)
        skill_key = re.sub(r"[^a-z0-9]+", "", name.casefold())
        # 项目名只能在已经有语义命中的候选间做弱排序，不能凭 `demogame-*`
        # 前缀把所有领域 Skill 塞进每张 demogame 工单。
        if score and project_key and project_key in skill_key:
            score += 4
        if score:
            ranked.append((score, name.casefold(), {**skill, "score": score}))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [skill for _, _, skill in ranked[: max(1, limit)]]


def _route_context(item: dict[str, Any]) -> str:
    return (
        f"来源=Meegle\n"
        f"来源项目={item.get('source_project') or '未知'}\n"
        f"来源状态={item.get('status') or '未知'}\n"
        f"截止={item.get('due_date') or '未记录'}"
    )


def route_meegle_item(
    item: dict[str, Any],
    *,
    catalog: list[dict[str, str]] | None = None,
    cwd: str | Path | None = None,
    router: Callable[..., dict[str, Any]] | None = None,
    record: bool = False,
    service_url: str = PROGRESS_SERVICE,
) -> dict[str, Any]:
    """为一张当前待办选择 Agent 与 Skill；不启动执行。"""
    if item.get("source_active") is False:
        return {
            "ok": False,
            "status": "skipped",
            "reason": "该条目已不在最近一次 Meegle 当前待办快照中",
            "execution_started": False,
        }
    task_id = str(item.get("id") or "")
    if not task_id:
        return {
            "ok": False,
            "status": "needs_user",
            "reason": "Meegle 条目缺少 canonical Task id",
            "needs_user_for": "source_identity",
            "execution_started": False,
        }
    context = ToolContext(cwd=str(cwd or Path.cwd()), project_root=str(cwd or Path.cwd()))
    available = catalog if catalog is not None else discover_skills(context)
    candidates = shortlist_skills(item, available)
    if not candidates:
        result = {
            "ok": False,
            "status": "needs_user",
            "task_id": task_id,
            "reason": "现有 Skill 目录中没有召回可处理该工作项的能力",
            "needs_user_for": "missing_skill",
            "execution_started": False,
        }
    else:
        if router is None:
            from omnicompany.dashboard.boss_sight.services.dispatch_router import route

            router = route
        decision = router(
            str(item.get("title") or ""),
            context=_route_context(item),
            skill_candidates=candidates,
        )
        target_project = decision.get("project") or decision.get("target_project")
        ready = (
            decision.get("kind")
            not in {"ask_user", "self_handle", None}
            and bool(decision.get("skills"))
            and bool(target_project)
        )
        result = {
            **decision,
            "ok": ready,
            "status": "routed" if ready else "needs_user",
            "task_id": task_id,
            "skill_candidates": [skill["name"] for skill in candidates],
            "execution_started": False,
        }
        if decision.get("kind") == "self_handle":
            result["reason"] = "工作项需要主控 Agent 明确处理，不能作为一句话回答自动结单"
            result["needs_user_for"] = "controller"
        elif not target_project and result["status"] == "needs_user":
            result.setdefault("reason", "尚未确定项目目录中的唯一 Project")
            result.setdefault("needs_user_for", "project")

    if record:
        patch = {
            "id": task_id,
            "routed_skills": result.get("skills") or [],
            "dispatch_status": result["status"],
        }
        # route 只记录路由结论。Task.assignee 必须与 Team/岗位经原子 claim-route
        # 一起落位，不能从通用 patch 旁路 Task 7 的唯一认领合同。
        _post_json(
            f"{service_url.rstrip('/')}/api/task/patch",
            patch,
        )
    return result


def build_agent_message(item: dict[str, Any], decision: dict[str, Any]) -> str:
    """只传开始工作所需的最小上下文，其余方法由 Skill 自己负责。"""
    skills = "、".join(str(name) for name in decision.get("skills") or [])
    return (
        f"请处理 Meegle 工作项：{item.get('title') or '(无标题)'}\n"
        f"来源项目：{item.get('source_project') or '未知'}；"
        f"目标项目：{decision.get('project') or decision.get('target_project') or '未绑定'}；"
        f"来源状态：{item.get('status') or '未知'}；"
        f"截止：{item.get('due_date') or '未记录'}。\n"
        f"开始前加载并遵循 Skill：{skills}。\n"
        "若工单材料不足以安全开工，停止猜测并向用户询问缺失信息。"
    )


def _record_start_result(
    *,
    task_id: str,
    decision: dict[str, Any],
    status: str,
    service_url: str,
) -> None:
    patch = {
        "id": task_id,
        "routed_skills": decision.get("skills") or [],
        "dispatch_status": status,
    }
    _post_json(f"{service_url.rstrip('/')}/api/task/patch", patch)


async def _submit_managed_pane(pane_id: str, message: str) -> dict[str, Any]:
    """经 ccdaemon 的受管终端通道粘贴并提交，不借用户键盘。"""
    import websockets

    from omnicompany.dashboard.ccdaemon.lifecycle import read_status

    daemon = read_status()
    if not daemon.serving or not daemon.port:
        return {
            "ok": False,
            "submitted": False,
            "error": "受管 Agent 运行时当前不可用",
        }

    uri = f"ws://127.0.0.1:{daemon.port}/cc/sessions/{pane_id}/ws"
    try:
        async with websockets.connect(uri, max_size=None, open_timeout=5) as socket:
            async with asyncio.timeout(10):
                while True:
                    payload = json.loads(await socket.recv())
                    if payload.get("type") == "exit":
                        return {
                            "ok": False,
                            "submitted": False,
                            "error": payload.get("reason") or "受管 Agent 已退出",
                        }
                    if payload.get("type") == "snapshot_end":
                        break

            # 终端的括号粘贴模式保证多行消息先作为一个输入块进入，再由 \r 提交。
            data = f"\x1b[200~{message}\x1b[201~\r"
            await socket.send(json.dumps({"type": "input", "data": data}, ensure_ascii=False))
            return {"ok": True, "submitted": True}
    except (OSError, TimeoutError, websockets.WebSocketException) as exc:
        return {"ok": False, "submitted": False, "error": str(exc)}


async def start_routed_meegle_item(
    item: dict[str, Any],
    decision: dict[str, Any],
    *,
    model: str | None = None,
    record: bool = True,
    service_url: str = PROGRESS_SERVICE,
) -> dict[str, Any]:
    """显式启动已路由工作；本轮观察不会调用此函数。"""
    if decision.get("status") != "routed" or not decision.get("skills"):
        return {
            "ok": False,
            "status": "needs_user",
            "reason": "Agent 或 Skill 尚未明确，不能启动",
            "execution_started": False,
        }
    if not item.get("assignee") or not item.get("team_id") or not item.get("position_id"):
        return {
            "ok": False,
            "status": "needs_controller",
            "reason": "任务尚未通过原子认领进入已绑定 Team 岗位，不能启动执行",
            "needs_controller_for": "task_claim_route",
            "execution_started": False,
        }
    kind = str(decision.get("kind") or "")
    task_id = str(item.get("id") or "")
    if kind == "send_active_window":
        from omnicompany.dashboard.boss_sight.services import agent_registry, winjump

        target_key = str(decision.get("target_key") or "")
        if str(item.get("assignee") or "") != target_key:
            return {
                "ok": False,
                "status": "needs_controller",
                "reason": "路由目标与 canonical Task 当前负责人不一致",
                "needs_controller_for": "assignee_mismatch",
                "execution_started": False,
            }
        target = agent_registry.load_registry().get(target_key)
        if not target:
            return {
                "ok": False,
                "status": "needs_controller",
                "reason": "路由目标已不在 Agent 注册表中",
                "needs_controller_for": "agent_reroute",
                "execution_started": False,
            }
        message = build_agent_message(item, decision)
        clipboard_ok = winjump.set_clipboard(message)
        action = (
            winjump.activate_location(
                str(target.get("location") or ""),
                title_hint=str(target.get("name") or "") or None,
                paste=True,
                submit=True,
            )
            if clipboard_ok
            else {"ok": False, "submitted": False, "error": "写入剪贴板失败"}
        )
        started = bool(
            action.get("ok") and action.get("pasted") and action.get("submitted")
        )
        response = {
            "ok": started,
            "status": "started" if started else "needs_controller",
            "task_id": task_id,
            "execution_started": started,
            "submitted": bool(action.get("submitted")),
            "target_key": target_key,
            "error": action.get("error"),
        }
        if not started:
            response["needs_controller_for"] = "active_session_submit"
            response["reason"] = "自动提交失败，未把粘贴误报为已经开工"
        if record:
            _record_start_result(
                task_id=task_id,
                decision=decision,
                status=response["status"],
                service_url=service_url,
            )
        return response

    if kind == "send_poof_pane":
        pane_id = str(decision.get("target_pane") or "")
        target_key = str(decision.get("target_key") or f"poof:{pane_id}")
        if str(item.get("assignee") or "") != target_key:
            return {
                "ok": False,
                "status": "needs_controller",
                "reason": "路由目标与 canonical Task 当前负责人不一致",
                "needs_controller_for": "assignee_mismatch",
                "execution_started": False,
            }
        if not pane_id:
            return {
                "ok": False,
                "status": "needs_controller",
                "reason": "受管 Agent 路由缺少窗格标识",
                "needs_controller_for": "agent_reroute",
                "execution_started": False,
            }
        action = await _submit_managed_pane(
            pane_id,
            build_agent_message(item, decision),
        )
        started = bool(action.get("ok") and action.get("submitted"))
        response = {
            "ok": started,
            "status": "started" if started else "needs_controller",
            "task_id": task_id,
            "execution_started": started,
            "submitted": bool(action.get("submitted")),
            "target_pane": pane_id,
            "error": action.get("error"),
        }
        if not started:
            response["needs_controller_for"] = "managed_session_submit"
            response["reason"] = "受管 Agent 自动提交失败"
        if record:
            _record_start_result(
                task_id=task_id,
                decision=decision,
                status=response["status"],
                service_url=service_url,
            )
        return response

    provider = str(decision.get("provider") or decision.get("target_provider") or "")
    provider = "claude-code" if provider == "claude" else provider
    if not provider:
        return {
            "ok": False,
            "status": "needs_user",
            "reason": "新 Agent 路由没有给出可启动的 provider",
            "needs_user_for": "agent_provider",
            "execution_started": False,
        }

    from omnicompany.core.config import omni_workspace_root
    from omnicompany.packages.services._core.agent.external_workers import (
        ExternalAgentPermissionMode,
        ExternalAgentRunRequest,
        run_external_agent_request,
    )

    request = ExternalAgentRunRequest(
        provider=provider,
        prompt=build_agent_message(item, decision),
        cwd=decision.get("target_cwd") or str(omni_workspace_root()),
        run_id=f"meegle-{task_id}",
        permission_mode=ExternalAgentPermissionMode.WORKSPACE_WRITE,
        model=model,
        trace_id=f"meegle-{task_id}",
        metadata={
            "entrypoint": "meegle_dispatch",
            "task_id": task_id,
            "source": "meegle",
            "skills": list(decision.get("skills") or []),
            "project": decision.get("project") or decision.get("target_project"),
            "target_agent": decision.get("target_key"),
        },
    )
    result = await run_external_agent_request(request)
    status = result.normalized_status().value
    started = status not in {"failed", "timed_out", "permission_violation"}
    response = {
        "ok": started,
        "status": "started" if started else "needs_user",
        "worker_status": status,
        "task_id": task_id,
        "run_id": result.run_id,
        "execution_started": started,
        "error": result.error,
    }
    if record:
        _record_start_result(
            task_id=task_id,
            decision=decision,
            status=response["status"],
            service_url=service_url,
        )
    return response


def start_routed_meegle_item_sync(
    item: dict[str, Any],
    decision: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """同步入口，供 CLI/定时设施调用。"""
    return asyncio.run(start_routed_meegle_item(item, decision, **kwargs))


__all__ = [
    "build_agent_message",
    "list_current_meegle_items",
    "observe_meegle_intake",
    "route_meegle_item",
    "shortlist_skills",
    "start_routed_meegle_item",
    "start_routed_meegle_item_sync",
]

# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-21T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.dispatch_router.py"
"""总控派发路由器 — 把一条消息归到 6 类之一。

用户(2026-06-21): 后续对话大幅升级, 走"总控同款逻辑": 喂最近各对话的近期内容, 快速路由:
  0 self_handle        : 简易请求(事实/常识/小确认)→ 总控直接答(写进 answer)
  1 send_active_window : 其实要发给一个已活跃的 vscode 里 codex/claude 或 codex 桌面对话 → 跳过去粘贴
  2 send_poof_pane     : 要发给 poof 里在跑的某 CLI 窗格 → 跳过去直接发
  3 new_with_project   : 该新起一个对话并带上某 project 上下文 → 受控新起, 载入项目上下文并发
  4 new_strongest      : 该新起一个对话, 用最新最强模型(claude/codex) → 受控新起并发
  5 ask_user           : 理论上发给某已有对话但不知是哪个 → 问用户

⚠ LLM 调用走标准 `runtime.llm.call_json`(唯一权威 LLM 入口, schema 强约束, 非自定义调用 →
Guardian OMNI-094 干净)。模型由统一的 `OMNI_STRUCTURED_LLM_MODEL` 槽位解析；本路由器不再
持有自己的模型常量。
路由器是薄的: 候选清单来自 agent_registry(被动维护的缓存, 不每次扫盘), LLM 只归类 + 选目标 + 给理由/答案。
"""
from __future__ import annotations

from typing import Any

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["self_handle", "send_active_window", "send_poof_pane",
                     "new_with_project", "new_strongest", "ask_user"],
        },
        "target_key": {"type": "string", "description": "case 1/2: 目标对话 key (provider:session_id)"},
        "target_pane": {"type": "string", "description": "case 2: 目标 poof 窗格 pty_id"},
        "project": {
            "type": "string",
            "description": "工作项路由或 case 3: 项目目录中的精确 project id",
        },
        "provider": {"type": "string", "enum": ["claude", "codex"], "description": "case 3/4: 用哪个 CLI"},
        "text": {"type": "string", "description": "实际要发送/带入的消息(通常原样, 可清理)"},
        "answer": {"type": "string", "description": "case0 self_handle: 总控直接给的答案(中文)"},
        "candidates": {"type": "array", "items": {"type": "string"},
                       "description": "case 5: 几个候选 key 让用户挑"},
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "提供 Skill 候选时，只能返回其中确实需要加载的 Skill 名称",
        },
        "reason": {"type": "string", "description": "一句话中文理由"},
    },
    "required": ["kind", "reason"],
}

_SYSTEM = (
    "你是 Omnicompany 总控的派发路由器。用户给你一条想发出的消息, 以及本机当前在跑的对话清单。"
    "判断这条消息该怎么处理, 严格归到下面 6 类之一。\n\n"
    "0 self_handle: 能用一两句话当场答清的简易请求(事实/常识/小确认/小算术)。"
    "⚠ 任何要写代码、动文件、查资料、做具体事情的都【不】算简易 —— 那些该新起(3/4)或派发(1/2)。"
    "把简短答案写进 answer(中文)。\n"
    "1 send_active_window: 这条其实是接着某个『外部窗口里已活跃的对话』(vscode 里的 claude/codex 扩展, "
    "或 codex 桌面)。给 target_key。\n"
    "2 send_poof_pane: 这条要发给某个『poof 里在跑的 CLI 窗格』(清单里 pane 非空的)。给 target_key 和 target_pane。\n"
    "3 new_with_project: 这是一个新任务, 但明显属于某个已知 project, 该新起一个对话并带上该项目上下文。"
    "project 必须从给出的项目目录中选精确 id；给 project 和 provider。\n"
    "4 new_strongest: 这是一个全新/通用、且有点份量的任务, 不归属任何在跑对话或具体项目, 该用最新最强模型新起。"
    "给 provider(claude=最强, 偏好 claude)。\n"
    "5 ask_user: 像是接着某条已有对话, 但有多个都可能、无法确定是哪个。给 candidates(几个 key)。\n\n"
    "判据: 简易能当场答清 → 0; 消息明确指向某对话正在做的事/项目 → 投给它(它在 poof 窗格就 2, 在外部窗口就 1); "
    "是没法归到在跑对话、有份量的新活儿 → 新起(能归到项目就 3, 否则 4); 像接着某对话但模糊 → 5。\n"
    "text(非 self_handle 时)一般原样照搬用户消息(必要时轻清理)。reason 用一句中文说为什么这么处理。"
    "不要编造清单里没有的对话。"
)


def _format_candidates(agents: list[dict[str, Any]]) -> str:
    if not agents:
        return "(当前没有在跑的对话)"
    lines = []
    for a in agents:
        pane = a.get("pty_id") or "-"
        lines.append(
            f"- key={a.get('key')} | 身份={a.get('identity')} | 位置={a.get('location')} | "
            f"在做={a.get('current_task')} | pane={pane}"
        )
    return "\n".join(lines)


def _format_projects(projects: list[dict[str, Any]]) -> str:
    if not projects:
        return "(当前项目目录为空)"
    return "\n".join(
        f"- id={project.get('id')} | 名称={project.get('name') or project.get('id')} | "
        f"分组={project.get('group') or 'other'}"
        for project in projects
        if project.get("id")
    )


def _canonical_project_id(
    value: Any,
    projects: list[dict[str, Any]],
) -> str | None:
    """把模型返回的项目 id/名称收敛到现有 Project registry，不猜新项目。"""
    wanted = str(value or "").strip().casefold()
    if not wanted:
        return None
    matches = {
        str(project.get("id"))
        for project in projects
        if project.get("id")
        and wanted
        in {
            str(project.get("id")).casefold(),
            str(project.get("name") or "").strip().casefold(),
        }
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _call_router_llm(user: str) -> dict[str, Any]:
    """经统一结构化模型槽位路由，schema 直接出 dict(无手解)。"""
    from omnicompany.runtime.llm import call_json, default_structured_model

    model = default_structured_model()

    out = call_json(
        system=_SYSTEM,
        user=user,
        schema=ROUTE_SCHEMA,
        model=model,
        caller="dispatch_router.route",
        max_tokens=900,
    )
    if not isinstance(out, dict):
        return {}
    out.setdefault("routing_model", model)
    return out


def route(message: str, *, context: str | None = None, running_only: bool = True,
          poof_panes: list[dict[str, Any]] | None = None,
          skill_candidates: list[dict[str, Any]] | None = None,
          timeout: int = 120) -> dict[str, Any]:
    """把一条消息路由到 5 类之一, 返回决策 dict。

    poof_panes: poof 自己在跑的 CLI 窗格 [{id, provider, label}] —— 它们走 poof 独立的 pty.rs,
    不在 agent_registry(只扫 ~/.claude 等), 所以由调用方(poof)在请求时带进来当候选, 让路由器
    能判 send_poof_pane。
    """
    from omnicompany.dashboard.boss_sight.services.agent_registry import list_records
    from omnicompany.core.projects_registry import list_projects

    # rebuild_first=False: 用被动维护的缓存注册表, 不每次路由都全盘扫文件系统(慢几秒)。
    agents = list_records(running_only=running_only, limit=30, rebuild_first=False)
    projects = list_projects()
    for p in (poof_panes or []):
        pid = p.get("id")
        if not pid:
            continue
        agents.append({
            "key": f"poof:{pid}",
            "identity": f"poof-{p.get('provider') or 'cli'}-{pid}",
            "location": "poof-powershell",
            "current_task": p.get("label") or "(poof 窗格, 尚无内容)",
            "pty_id": pid,
            "provider": p.get("provider"),
            "running": True,
        })
    user = f"消息:\n{message}\n\n"
    if context:
        user += f"最近上下文:\n{context[:2000]}\n\n"
    user += "当前在跑的对话:\n" + _format_candidates(agents)
    user += "\n\n权威项目目录:\n" + _format_projects(projects)
    if skill_candidates is not None:
        user += (
            "\n\n这是需要开始处理的工作项。请同时选择开始工作前必须加载的 Skill，"
            "只能使用下面候选的精确名称；并为任何非 ask_user 结果返回权威项目目录中的"
            "精确 project id。无法确定 Skill、Project 或 Agent 中任一项时返回 ask_user，"
            "不要编造。候选里可能混有标注「领域保底候选，低置信」的条目，它们只是按领域"
            "保底进场的低置信选项，不代表词面命中；无论保底还是召回候选，只要没有一个"
            "真正匹配该工作项所需能力，就返回 ask_user，不要选最不差的一个。\n"
            "可用 Skill 候选:\n"
        )
        for skill in skill_candidates:
            name = str(skill.get("name") or "").strip()
            if not name:
                continue
            description = " ".join(str(skill.get("description") or "").split())[:300]
            user += f"- {name}: {description or '(无说明)'}\n"

    decision = _call_router_llm(user)
    decision.setdefault("text", message)
    if skill_candidates is not None:
        allowed = {
            str(skill.get("name") or "").strip()
            for skill in skill_candidates
            if str(skill.get("name") or "").strip()
        }
        raw_skills = decision.get("skills")
        chosen = (
            [str(name).strip() for name in raw_skills if str(name).strip() in allowed]
            if isinstance(raw_skills, list)
            else []
        )
        decision["skills"] = list(dict.fromkeys(chosen))
        if not decision["skills"]:
            decision["kind"] = "ask_user"
            decision["reason"] = "现有 Skill 候选中无法确定开始工作所需的能力"
            decision["skill_candidates"] = sorted(allowed)
            decision["needs_user_for"] = "skill"

    # 回填便于执行的目标细节(LLM 只给了 key, 我们补 pane/provider/identity)
    by_key = {a.get("key"): a for a in agents}
    project_by_id = {
        str(project.get("id")): project
        for project in projects
        if project.get("id")
    }
    kind = decision.get("kind")
    if kind in {"send_active_window", "send_poof_pane"} and decision.get("target_key") not in by_key:
        decision["kind"] = "ask_user"
        decision["reason"] = "没有找到路由结果指定的在跑 Agent"
        decision["candidates"] = sorted(str(key) for key in by_key if key)
        decision["needs_user_for"] = "agent"

    tgt = by_key.get(decision.get("target_key"))
    if tgt:
        decision.setdefault("target_pane", tgt.get("pty_id"))
        decision["target_identity"] = tgt.get("identity")
        decision["target_location"] = tgt.get("location")
        decision["target_provider"] = tgt.get("provider")
        decision["target_cwd"] = tgt.get("cwd")

    # 普通消息派发保持旧行为；工作项派发必须拿到项目目录里的精确 Project，
    # 不能把 Agent 显示名或来源系统里的 "demogame" 分组当成 canonical project id。
    project_required = (
        skill_candidates is not None
        and decision.get("kind") not in {"ask_user", "self_handle"}
    )
    if decision.get("kind") == "new_with_project" or project_required:
        project_id = _canonical_project_id(decision.get("project"), projects)
        if project_id is None and tgt:
            project_id = _canonical_project_id(tgt.get("project"), projects)
        if project_id is None:
            decision["kind"] = "ask_user"
            decision["reason"] = "无法把工作项绑定到项目目录中的唯一 Project"
            decision["project_candidates"] = sorted(project_by_id)
            decision["needs_user_for"] = "project"
        else:
            decision["project"] = project_id
            decision["target_project"] = project_id
    elif tgt:
        decision["target_project"] = tgt.get("project")
    # new_with_project: 补项目主文件夹(#4 工作 agent 进对应项目目录, 别落脏文件在 home)。
    # 启发式: 在跑对话里找同项目的, 用它的 cwd 作项目主目录。
    if decision.get("kind") == "new_with_project" and decision.get("project"):
        proj = str(decision["project"])
        project = project_by_id.get(proj) or {}
        roots = [
            str(root)
            for root in project.get("roots") or []
            if str(root).strip()
        ]
        if roots:
            decision["target_cwd"] = roots[0]
        decision["target_project"] = proj
    # ask_user: 把候选的身份/位置/pane 补全, 让 poof 能渲染一个真选择器
    if decision.get("kind") == "ask_user":
        details = []
        for k in (decision.get("candidates") or []):
            a = by_key.get(k)
            if a:
                details.append({
                    "key": k, "identity": a.get("identity"),
                    "location": a.get("location"), "pane": a.get("pty_id"),
                    "current_task": a.get("current_task"),
                })
        decision["candidate_details"] = details
    decision["_agents_considered"] = len(agents)
    return decision


__all__ = ["route", "ROUTE_SCHEMA"]

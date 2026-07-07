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
Guardian OMNI-094 干净)。模型 `_ROUTER_MODEL`(用户 2026-06-21: 本机 claude code worker 12s 太慢,
换快模型; 实测可靠的快模型≈qwen3.6-plus 7~9s, flash 系会破坏结构化输出不可用; qwen3.7-max 也≈10s)。
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
        "project": {"type": "string", "description": "case 3: 项目名"},
        "provider": {"type": "string", "enum": ["claude", "codex"], "description": "case 3/4: 用哪个 CLI"},
        "text": {"type": "string", "description": "实际要发送/带入的消息(通常原样, 可清理)"},
        "answer": {"type": "string", "description": "case0 self_handle: 总控直接给的答案(中文)"},
        "candidates": {"type": "array", "items": {"type": "string"},
                       "description": "case 5: 几个候选 key 让用户挑"},
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
    "给 project 和 provider。\n"
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


# 路由用快模型(用户 2026-06-21: 本机 claude code 12s 太慢, 换 qwen3.7-max ~2s)。
# 走标准 call_json(唯一权威 LLM 入口, schema 强约束结构化输出, 非自定义调用 → OMNI-094 干净)。
_ROUTER_MODEL = "qwen3.6-plus"


def _call_router_llm(user: str) -> dict[str, Any]:
    """标准 call_json 跑快模型路由, schema 直接出 dict(无手解)。"""
    from omnicompany.runtime.llm import call_json

    out = call_json(
        system=_SYSTEM,
        user=user,
        schema=ROUTE_SCHEMA,
        model=_ROUTER_MODEL,
        caller="dispatch_router.route",
        max_tokens=900,
    )
    return out if isinstance(out, dict) else {}


def route(message: str, *, context: str | None = None, running_only: bool = True,
          poof_panes: list[dict[str, Any]] | None = None,
          timeout: int = 120) -> dict[str, Any]:
    """把一条消息路由到 5 类之一, 返回决策 dict。

    poof_panes: poof 自己在跑的 CLI 窗格 [{id, provider, label}] —— 它们走 poof 独立的 pty.rs,
    不在 agent_registry(只扫 ~/.claude 等), 所以由调用方(poof)在请求时带进来当候选, 让路由器
    能判 send_poof_pane。
    """
    from omnicompany.dashboard.boss_sight.services.agent_registry import list_records

    # rebuild_first=False: 用被动维护的缓存注册表, 不每次路由都全盘扫文件系统(慢几秒)。
    agents = list_records(running_only=running_only, limit=30, rebuild_first=False)
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

    decision = _call_router_llm(user)
    decision.setdefault("text", message)

    # 回填便于执行的目标细节(LLM 只给了 key, 我们补 pane/provider/identity)
    by_key = {a.get("key"): a for a in agents}
    tgt = by_key.get(decision.get("target_key"))
    if tgt:
        decision.setdefault("target_pane", tgt.get("pty_id"))
        decision["target_identity"] = tgt.get("identity")
        decision["target_location"] = tgt.get("location")
        decision["target_provider"] = tgt.get("provider")
    # new_with_project: 补项目主文件夹(#4 工作 agent 进对应项目目录, 别落脏文件在 home)。
    # 启发式: 在跑对话里找同项目的, 用它的 cwd 作项目主目录。
    if decision.get("kind") == "new_with_project" and decision.get("project"):
        proj = decision["project"]
        for a in agents:
            if a.get("project") == proj and a.get("cwd"):
                decision["target_cwd"] = a.get("cwd")
                break
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

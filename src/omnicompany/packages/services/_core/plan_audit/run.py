# [OMNI] origin=claude-code domain=services/plan_audit ts=2026-06-19T00:00:00Z type=service
# [OMNI] material_id="material:core.plan_audit.sync_runner_and_report.py"
"""plan_audit.run — 从 sync CLI 跑 async audit agent + 渲染/留档报告.

范式(全包一个 asyncio.run, 避免跨 loop):
    verdict = run_conversation_audit(session_id="...", provider="claude_code")

报告:
- render_report(verdict_output) → 人读 Markdown(指示清单 + 状态 + 证据 + 未落地汇总).
- persist_report(...) → 走 guarded_write 落一份到 data/services/plan_audit/<sid>-<ts>.md(+ .json).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


def _render_transcript_text(messages: list[dict], truncated: bool) -> str:
    """渲染抽取后的对话(仅用户直接输入 + 助理直接文本输出, 无工具过程)为可分片读的文本."""
    lines: list[str] = [
        "===== 对话(仅用户直接输入 + 助理直接文本输出; 已剔除工具调用/工具返回/思考/compact) =====",
    ]
    for i, m in enumerate(messages):
        who = "用户" if m.get("role") == "user" else "助理"
        lines.append(f"\n[{i:03d} {who}]\n{m.get('text', '')}")
    if truncated:
        lines.append("\n(注: 对话过长, 尾部已按 cap 截断)")
    return "\n".join(lines)


def _write_work_transcript(stem: str, messages: list[dict], truncated: bool) -> tuple[str, int]:
    """把对话写到 data/services/plan_audit/_work/<stem>.md, 供 agent 用 read_file 分片读(不塞进 prompt).

    渐进式分片读 + 标准 compact 才能处理长对话; 一次性塞 prompt 会压垮模型并废掉 compact.
    返回 (绝对路径, 字符数).
    """
    from omnicompany.core.config import omni_workspace_root

    text = _render_transcript_text(messages, truncated)
    work = omni_workspace_root() / "data" / "services" / "plan_audit" / "_work"
    work.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)[:80]
    p = work / f"{safe}.md"
    p.write_text(text, encoding="utf-8")
    return str(p), len(text)


# ────────────────────────────────────────────────────────────────────────
# 异步执行入口 (sync CLI 调)
# ────────────────────────────────────────────────────────────────────────


def run_conversation_audit(
    *,
    session_id: str,
    provider: str | None = None,
    model: str | None = None,
    repo_root: str | None = None,
    cap_chars: int = 600_000,
) -> dict:
    """审计一段对话, 返回 {ok, verdict_output, meta}. 同步入口(内部 asyncio.run)."""
    from omnicompany.packages.services._core.plan_audit.discovery import (
        find_conversation_by_session_id,
        load_full_transcript,
    )

    hit = find_conversation_by_session_id(session_id, provider)
    if hit is None:
        return {
            "ok": False,
            "error": f"找不到 session_id={session_id} (provider={provider or 'any'}) 的本机对话. "
                     f"用 `omni convos list --json` 看可用 session.",
        }
    prov = hit.get("provider", "claude_code")
    transcript, truncated = load_full_transcript(prov, hit["file"], cap_chars=cap_chars)
    if not transcript:
        return {"ok": False, "error": "这段对话抽不到可读消息(可能全是工具/系统记录)."}

    from omnicompany.core.config import omni_workspace_root
    rroot = repo_root or hit.get("cwd") or str(omni_workspace_root())
    use_model = model or "deepseek-v4-flash"
    # 写成文件让 agent 用 read_file 分片读, 不塞进 prompt(渐进式 + 标准 compact 才能处理长对话)
    tfile, tchars = _write_work_transcript(f"convo-{session_id}", transcript, truncated)

    input_data = {
        "transcript_file": tfile,
        "message_count": len(transcript),
        "char_count": tchars,
        "truncated": truncated,
        "cwd": hit.get("cwd", ""),
        "repo_root": rroot,
        "provider": prov,
        "session_id": session_id,
        "trace_id": f"plan-audit-convo-{session_id[:16]}-{int(time.time())}",
    }

    async def _go() -> Any:
        from omnicompany.packages.services._core.plan_audit.auditor import ConversationAuditor

        agent = ConversationAuditor(model=use_model)
        return await agent.run(input_data)

    verdict = asyncio.run(_go())
    return {
        "ok": verdict.kind.value != "fail",
        **({"error": verdict.diagnosis} if verdict.kind.value == "fail" else {}),
        "verdict_output": verdict.output if isinstance(verdict.output, dict) else {"raw": verdict.output},
        "verdict_kind": verdict.kind.value,
        "meta": {
            "mode": "conversation",
            "session_id": session_id,
            "provider": prov,
            "cwd": hit.get("cwd", ""),
            "repo_root": rroot,
            "message_count": len(transcript),
            "char_count": tchars,
            "truncated": truncated,
            "model": use_model,
            "transcript_file": tfile,
        },
    }


def run_plan_audit(
    *,
    plan_id: str,
    model: str | None = None,
    repo_root: str | None = None,
    max_conversations: int = 6,
    cap_chars: int = 200_000,
) -> dict:
    """审计一个 plan + 相关对话, 返回 {ok, verdict_output, meta}. 同步入口."""
    from omnicompany.packages.services._core.plan_audit.discovery import (
        discover_plan_conversations,
        load_full_transcript,
        load_plan_md,
    )
    from omnicompany.core.config import omni_workspace_root

    plan_md, fm = load_plan_md(plan_id)
    rroot = repo_root or str(omni_workspace_root())
    use_model = model or "deepseek-v4-flash"

    candidates = discover_plan_conversations(plan_id)
    # 取最相关的前 N 条(cc_sessions 来源优先, 已在 discover 里靠前). 每条读 transcript.
    convos: list[dict] = []
    for c in candidates[:max_conversations]:
        try:
            transcript, truncated = load_full_transcript(
                c.get("provider", "claude_code"), c["file"], cap_chars=cap_chars,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("读候选对话失败 %s: %s", c.get("file"), e)
            continue
        if not transcript:
            continue
        tfile, tchars = _write_work_transcript(
            f"plan-{plan_id}-{c.get('session_id', 'x')}", transcript, truncated,
        )
        convos.append({
            "provider": c.get("provider"),
            "session_id": c.get("session_id"),
            "match_reason": c.get("match_reason"),
            "transcript_file": tfile,
            "message_count": len(transcript),
            "char_count": tchars,
            "truncated": truncated,
        })

    if not plan_md and not convos:
        return {
            "ok": False,
            "error": f"plan_id={plan_id} 既读不到 plan.md, 也找不到任何相关对话. "
                     f"确认 plan_id 形如 `<cat>/[date]NAME` 且存在于 docs/plans/.",
        }

    exit_criteria = fm.get("exit_criteria") or []
    if not isinstance(exit_criteria, list):
        exit_criteria = [str(exit_criteria)]

    input_data = {
        "plan_id": plan_id,
        "plan_md": plan_md,
        "exit_criteria": exit_criteria,
        "repo_root": rroot,
        "conversations": convos,
        "trace_id": f"plan-audit-plan-{plan_id.replace('/', '_')[:24]}-{int(time.time())}",
    }

    async def _go() -> Any:
        from omnicompany.packages.services._core.plan_audit.auditor import PlanAuditor

        agent = PlanAuditor(model=use_model)
        return await agent.run(input_data)

    verdict = asyncio.run(_go())
    return {
        "ok": verdict.kind.value != "fail",
        **({"error": verdict.diagnosis} if verdict.kind.value == "fail" else {}),
        "verdict_output": verdict.output if isinstance(verdict.output, dict) else {"raw": verdict.output},
        "verdict_kind": verdict.kind.value,
        "meta": {
            "mode": "plan",
            "plan_id": plan_id,
            "repo_root": rroot,
            "exit_criteria_count": len(exit_criteria),
            "candidate_conversations": len(candidates),
            "audited_conversations": len(convos),
            "model": use_model,
        },
    }


# ────────────────────────────────────────────────────────────────────────
# 报告渲染 + 留档
# ────────────────────────────────────────────────────────────────────────


_STATUS_MARK = {"DONE": "[DONE]", "PARTIAL": "[PARTIAL]", "PENDING": "[PENDING]"}


def render_report(result: dict) -> str:
    """把 audit 结果渲染成人读 Markdown."""
    out = result.get("verdict_output") or {}
    meta = result.get("meta") or {}
    lines: list[str] = []
    if meta.get("mode") == "plan":
        lines.append(f"# Plan Audit 报告 — {meta.get('plan_id', '?')}")
    else:
        lines.append(f"# 对话落地审计报告 — {meta.get('session_id', '?')}")
    lines.append("")
    lines.append("## 元信息")
    for k, v in meta.items():
        lines.append(f"- {k}: {v}")
    lines.append(f"- verdict_kind: {result.get('verdict_kind')}")
    lines.append("")

    # plan 模式: 相关对话筛选结果
    rel = out.get("relevant_conversations")
    if rel:
        lines.append("## 相关对话筛选(是否真在执行/起草该 plan)")
        for r in rel:
            mark = "是" if r.get("is_executing_plan") else "否"
            lines.append(f"- [{mark}] {r.get('session_id', '?')}: {r.get('reason', '')}")
        lines.append("")

    instructions = out.get("instructions") or []
    lines.append(f"## 指示落地清单(共 {len(instructions)} 条)")
    if not instructions:
        if out.get("raw_text") or out.get("parse_error"):
            # 真失败: agent 没吐出可解析 JSON(常见于超长对话跑满轮次)
            lines.append("(agent 未产出可解析的结构化输出 — 见下方原始输出; 超长对话+默认模型常见, 可换更强模型重试)")
            if out.get("raw_text"):
                lines.append("")
                lines.append("### agent 原始输出(未能解析为 JSON)")
                lines.append("```")
                lines.append(str(out.get("raw_text"))[:4000])
                lines.append("```")
        else:
            # 真的 0 条: agent 正常完成, 判定本对话无执行性指示(见汇总)
            lines.append("(本对话无执行性指示 — 见下方汇总)")
    for i, ins in enumerate(instructions, 1):
        status = (ins.get("status") or "").upper()
        mark = _STATUS_MARK.get(status, f"[{status}]")
        suffix = "  (曾落地后又删/挪走)" if ins.get("landed_then_removed") else ""
        lines.append(f"\n### {i}. {mark}{suffix} {ins.get('text', '')}")
        if ins.get("evidence"):
            lines.append(f"- 证据: {ins.get('evidence')}")
    lines.append("")

    not_landed = out.get("not_landed") or []
    lines.append(f"## 未落地清单(共 {len(not_landed)} 条)")
    if not_landed:
        for nl in not_landed:
            lines.append(f"- {nl}")
    else:
        lines.append("(无未落地项)")
    lines.append("")

    if out.get("summary"):
        lines.append("## 汇总")
        lines.append(str(out.get("summary")))
        lines.append("")

    return "\n".join(lines)


def persist_report(result: dict, *, trace: str = "") -> dict:
    """把报告留档到 data/services/plan_audit/<stem>-<ts>.{md,json}.

    - .md(人读报告): 走 guarded_write.write_file (Markdown 注释合法, 受门禁 + OmniMark 头).
    - .json(结构化, 供网页/管线消费): 是 data 文件, 用 sidecar provenance 模式
      (raw write + write_data_sidecar) —— 与 guardian.audit_store 一致. 不走 write_file
      因为 stamp_file 会往 JSON 顶部塞 `#` 注释行, 破坏 JSON 可解析性.

    返回 {md_path, json_path} (相对仓库根的 posix). 失败抛异常.
    """
    from omnicompany.core.guarded_write import write_file
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.core.omnimark import write_data_sidecar

    meta = result.get("meta") or {}
    if meta.get("mode") == "plan":
        stem = "plan-" + str(meta.get("plan_id", "unknown")).replace("/", "_")[:48]
    else:
        stem = "convo-" + str(meta.get("session_id", "unknown"))[:32]
    # ts 用 time 模块(非 Date.now), 文件名安全的本地时间戳
    ts = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    base = f"data/services/plan_audit/{stem}-{ts}"
    md_rel = f"{base}.md"
    json_rel = f"{base}.json"

    md_text = render_report(result)
    json_text = json.dumps(result, ensure_ascii=False, indent=2, default=str)

    # .md: 走门禁写入 (data/services/<svc>/ 是 internal-engine 的产物区)
    write_file(
        md_rel, md_text,
        origin="claude-code", domain="services/plan_audit", trace=trace,
        purpose="plan audit 落地审计报告(人读)", writer="internal-engine",
    )

    # .json: data 文件, raw write + sidecar (保证 JSON 可解析)
    root = omni_workspace_root()
    json_abs = root / json_rel
    json_abs.parent.mkdir(parents=True, exist_ok=True)
    json_abs.write_text(json_text, encoding="utf-8")
    try:
        write_data_sidecar(
            json_abs,
            written_by="services._core.plan_audit.run.persist_report",
            trace=trace, origin="claude-code",
            summary="plan audit 结构化结果(网页/管线消费)",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("plan_audit json sidecar 写入失败(非致命): %s", e)
    return {"md_path": md_rel, "json_path": json_rel}

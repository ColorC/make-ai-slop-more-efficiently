# [OMNI] origin=ai-ide ts=2026-05-24 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.services.controller_waker.py"
"""ControllerWaker — 把 subagent.* 事件转成 controller session 的 user.message inject.

落实 B3-R9 (唤起源接通) + B3-R10 (SubagentStatusAggregator 实时化).

设计:
- 进程内 best-effort 回调, 注册到 CcChatSessionManager.subscribe_events()
- 触发条件: event_type in {subagent.spawned, subagent.completed, subagent.blocked}
- 行为:
  1. 同步更新 SubagentStatusAggregator (R10)
  2. 找所有活跃 controller session (provider == "controller"), 排除自己
  3. asyncio.create_task(submit_user_prompt) inject 一条机器格式 user message
- 用户原话 §3.3: "总控 agent 在以下情况被唤起: subagent 完成并返回 / subagent 出现阻断性违规 / 用户对话"
- 用户原话 U-032: 不另起 daemon, 跟已有 chat session 抽象统一

不走 EventBus 跨进程 — controller_waker 跟 chat.py 同 daemon, 直接回调即可.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.caller_identity import CALLER_SUBAGENT

_log = logging.getLogger(__name__)


def _auto_wake_enabled() -> bool:
    """读「总控自动唤起」控制位(设置面板 controller.auto_wake)。

    历史上 ControllerWaker 从不读这个开关 —— 设置面板里的「总控自动唤起」是个**摆设**,
    关了也照样唤起。这里把它接通: 关闭时所有唤起(subagent 完成/阻断、审阅评论)都不再注入总控。
    读不到(store 异常)按 True 处理, 沿用历史默认 = 开。
    """
    try:
        from omnicompany.dashboard.boss_sight.services.control_observability_store import (
            get_control_observability_store,
        )
        controls = get_control_observability_store().list_controls().get("by_key", {})
        return bool(controls.get("controller.auto_wake", {}).get("value", True))
    except Exception:  # noqa: BLE001
        _log.exception("ControllerWaker: read controller.auto_wake failed, default on")
        return True


def _is_delegated_subagent(sess: Any) -> bool:
    """这个会话是不是"总控派出去的 subagent"。

    判据: caller_identity == "subagent"(只有经 from_controller spawn 或 #2 采纳出来的才会被打上)。
    用户自己另开的 codex / claude_code 会话 caller_identity 为空 —— 它们完成 turn 不该
    唤起总控(白白消耗总控 opus 额度, 用户反馈 2026-06-05: "感觉和总控无关在浪费我的AI额度")。
    #2: 用户"接管"(taken_over)的采纳会话也不唤起 —— 此时用户在自己驾驶, 总控不自动 hook。
    """
    if sess is None or getattr(sess, "caller_identity", None) != CALLER_SUBAGENT:
        return False
    if getattr(sess, "taken_over", False):
        return False
    return True


def _workspace_root() -> Path:
    # 委托到唯一权威 core.config.omni_workspace_root(), 不再硬编码 parents[N]
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def _auto_record_plan_completion(payload: dict[str, Any]) -> Path | None:
    """块 3 R12 backstop: subagent.completed 时无脑写一条 auto-log 到
    data/boss_sight/plan_completion_log/. 跟手动 record_plan_completion 工具
    分开命名前缀, 让总控/审阅人能区分.

    返回写出的文件路径 (出错则 None).
    """
    plan_id = payload.get("active_plan") or "_unknown_plan"
    verdict = payload.get("verdict") or "?"
    sid = payload.get("subagent_id") or "?"
    provider = payload.get("provider") or "?"
    preview = (payload.get("last_assistant_preview") or "")[:500]

    ws = _workspace_root()
    log_dir = ws / "data" / "boss_sight" / "plan_completion_log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        _log.exception("auto plan_completion: mkdir failed")
        return None

    ts = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "_")[:25]
    slug = (plan_id or "").replace("/", "_").replace("\\", "_")[:80] or "general"
    out_path = log_dir / f"{ts}_auto_{slug}.json"

    record = {
        "_source": "controller_waker_auto",  # 跟 LLM 手动调的区分
        "plan_id": plan_id,
        "status": "partial",  # 自动 log 永远标 partial — 由总控之后确认是否 done
        "subagent_id": sid,
        "provider": provider,
        "verdict_from_subagent": verdict,
        "last_assistant_preview": preview,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "assessment": (
            f"Auto-recorded on subagent.completed event (controller waker backstop). "
            f"Subagent {sid} ({provider}) finished a turn with verdict={verdict}. "
            f"Controller should review and update if needed."
        ),
    }
    try:
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        _log.exception("auto plan_completion: write failed for %s", out_path)
        return None
    return out_path


# 触发唤起的事件类型. 用户原话 §3.3 三种唤起源:
#   1. subagent 完成并返回 → subagent.completed
#   2. subagent 出现阻断性违规 → subagent.blocked
#   3. 用户对话 → 走 chat session 原生 WS → submit_user_prompt, 不需要 waker
# subagent.spawned 不唤起 — spawn 本来就是总控发起的, 重新唤起会形成 turn N
# 派 spawn 立即触发 turn N+1 处理 spawned 通知的循环.
# aggregator 状态仍然更新 (要算 active 数), 但 controller session 不被 inject.
HANDLED_EVENT_TYPES = {
    "subagent.completed",
    "subagent.blocked",
    # 2026-06-13 用户明示: 审阅评论**不再**自动发送/回灌总控("不要搞自动发送")。
    # 评论改为落"每材料一个 markdown 文件"(用户自读自写、可在 VSCode 直接编辑), 不进唤起路径。
    # reviewstage.verdict(裁决)同样不唤起。两者都不在本集合里。
}

# aggregator 关心的更广 — 包括 spawn (要追踪 in-flight subagent 列表)
AGGREGATOR_EVENT_TYPES = {
    "subagent.spawned",
    "subagent.completed",
    "subagent.blocked",
}


def _format_event_as_user_msg(event_type: str, payload: dict[str, Any]) -> str:
    """把事件渲染成 controller 能直接吃的一条 user.message.

    格式跟 spawn 时 from_controller 前缀 [from: BOSS-SIGHT ...] 同套, 表明这是机器
    源, 不是真人. 后续可以演变成结构化 schema.
    """
    sid = payload.get("subagent_id") or payload.get("session_id") or "?"
    plan = payload.get("active_plan") or "(no plan)"
    provider = payload.get("provider") or "?"
    verdict = payload.get("verdict") or ""
    preview = (payload.get("last_assistant_preview") or "")[:400]
    soft_violations: list[dict] = payload.get("soft_violations") or []

    parts = [
        "[from: BOSS-SIGHT bus event, not_user: true]",
        f"event_type: {event_type}",
        f"subagent_id: {sid}",
        f"provider: {provider}",
        f"active_plan: {plan}",
    ]
    if verdict:
        parts.append(f"verdict: {verdict}")
    if preview:
        parts.append(f"last_assistant_preview: {preview}")
    # 块 5 R2: 软 guard 违规集中清单 (§6.3 "最终返回的时候再集中看")
    if soft_violations:
        parts.append(f"\nsoft_guard_violations ({len(soft_violations)} 条 — §6.3 集中审视):")
        for v in soft_violations[:10]:
            parts.append(
                f"  - tool={v.get('tool_name')} input={(v.get('tool_input_summary') or '')[:80]} "
                f"denial=\"{(v.get('denial_message') or '')[:120]}...\""
            )
        if len(soft_violations) > 10:
            parts.append(f"  (... 还有 {len(soft_violations) - 10} 条)")
    # 提示总控决定下一步动作
    if event_type == "subagent.completed":
        action_hint = (
            "\n你被唤起的原因: 上述 subagent 完成了一个 turn. "
            "请: (a) 决定是否要 record_plan_completion / 给它派下个 turn / 收尾; "
            "(b) 用自然语言把结论总结给用户 (可一句话, 也可短回执)."
        )
        if soft_violations:
            action_hint += (
                "\n注意: 本次完成附带软 guard 违规清单, "
                "请总结其中是否有真风险, 必要时 propose_change 调 guard 或 record 状态."
            )
        parts.append(action_hint)
    elif event_type == "subagent.blocked":
        parts.append(
            "\n你被唤起的原因: 上述 subagent 触发硬 guard 被阻断. "
            "请审阅它要做的操作 + 决定 emit_event(subagent.unblock) 放行或 "
            "subagent.shutdown 终止."
        )
    elif event_type == "subagent.spawned":
        parts.append(
            "\n你被唤起的原因: 一个 subagent 刚被 spawn 出来 (可能你自己刚调的 spawn_subagent, "
            "也可能是其他来源). 通常不需要立刻动作, 但可以记一下当前 in-flight 的 worker 列表."
        )
    elif event_type in ("reviewstage.verdict", "reviewstage.comment"):
        # M2 Phase 2 步骤 3: 审阅台事件渲染. 上面 sid/plan/preview 都是 fallback 空值,
        # 这里重新组织以 material 为中心的 header (覆盖上面通用段, 避免误读).
        parts = [
            "[from: BOSS-SIGHT bus event, not_user: true]",
            f"event_type: {event_type}",
        ]
        material_id = payload.get("material_id") or "?"
        title = payload.get("title") or "(no title)"
        kind = payload.get("kind") or "?"
        tier = payload.get("tier") or "?"
        source_plan_id = payload.get("source_plan_id") or "(no plan)"
        source_subagent_id = payload.get("source_subagent_id") or "?"
        parts.append(f"material_id: {material_id}")
        parts.append(f"title: {title}")
        parts.append(f"kind: {kind} / tier: {tier}")
        parts.append(f"source_plan_id: {source_plan_id}")
        if source_subagent_id and source_subagent_id != "?":
            parts.append(f"source_subagent_id: {source_subagent_id}")
        if event_type == "reviewstage.verdict":
            verdict_v = payload.get("verdict") or "?"
            reason_v = payload.get("reason") or ""
            parts.append(f"verdict: {verdict_v}")
            if reason_v:
                parts.append(f"reason: {reason_v[:400]}")
            if verdict_v == "accepted":
                parts.append(
                    "\n你被唤起的原因: 用户在审阅台 ACCEPTED 了上述 material. "
                    "请: (a) 用自然语言给用户一句确认收尾 (例: 已采纳 <X>, "
                    "后续基于此推进); (b) 必要时 spawn 下一阶段 subagent."
                )
            elif verdict_v == "rejected":
                parts.append(
                    "\n你被唤起的原因: 用户在审阅台 REJECTED 了上述 material. "
                    "请读 reason 决定: (a) spawn 新 subagent 改进 (initial_prompt 带上 reason "
                    "让它知道改啥); (b) 或自己提议方向调整, 用自然语言跟用户对齐."
                )
            elif verdict_v == "blocked":
                parts.append(
                    "\n你被唤起的原因: 用户在审阅台把上述 material 标 BLOCKED — "
                    "严重偏差, 需总控调整后再 submit 新版本. 读 reason, 决定下一步."
                )
            elif verdict_v == "pending":
                parts.append(
                    "\n你被唤起的原因: 用户把 verdict 重置回 pending. "
                    "通常不需要立刻动作, 记一下用户改主意了."
                )
        else:  # reviewstage.comment
            comment_content = (payload.get("comment_content") or "")[:400]
            comment_author = payload.get("comment_author") or "user"
            if comment_content:
                parts.append(f"comment_by: {comment_author}")
                parts.append(f"comment_content: {comment_content}")
            comment_id = payload.get("comment_id") or ""
            if comment_id:
                parts.append(f"comment_id: {comment_id}")
            feedback_status = payload.get("feedback_status") or ""
            if feedback_status:
                parts.append(f"feedback_status: {feedback_status}")
            target = payload.get("target")
            if target:
                parts.append(f"target: {json.dumps(target, ensure_ascii=False)[:500]}")
            parts.append(
                "\n你被唤起的原因: 用户在审阅台给上述 material 加了 comment. "
                "看批注内容决定: (a) 用自然语言回应; (b) 必要时 spawn 修订 subagent. "
                "If follow-up is needed, turn this comment into a todo and advance its "
                "feedback_status to to_todo; when done, advance it to todo_done."
            )
    try:
        from omnicompany.dashboard.boss_sight.cockpit_workflow import (
            build_workflow_summary,
            format_workflow_ctx_summary,
        )

        workflow = payload.get("workflow_summary")
        if not isinstance(workflow, dict):
            workflow = build_workflow_summary(ws=_workspace_root(), action_limit=20).get("ctx_summary", {})
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append(format_workflow_ctx_summary(workflow))
    except Exception:  # noqa: BLE001
        _log.exception("ControllerWaker workflow summary render failed")
    return "\n".join(parts)


class ControllerWaker:
    """订阅 CcChatSessionManager 事件 → 唤起 controller chat session."""

    def __init__(self, chat_manager: Any, aggregator: Any | None = None) -> None:
        self.chat_manager = chat_manager
        self.aggregator = aggregator
        self._inflight_tasks: set[asyncio.Task] = set()

    def attach(self) -> None:
        """注册到 chat_manager 的事件回调列表. 幂等 — 多次 attach 只有第一次生效."""
        cbs = getattr(self.chat_manager, "_event_subscribers", [])
        if self.on_event in cbs:
            return
        self.chat_manager.subscribe_events(self.on_event)
        _log.info("ControllerWaker attached to CcChatSessionManager")

    def on_event(self, sess: Any, event_type: str, payload: dict[str, Any], tags: list[str]) -> None:
        """同步回调. 不能阻塞 — emit 路径上的 hot path.

        sess 含义:
        - subagent.* 事件: sess = 发出事件的 chat session (provider/ended_at 可读)
        - reviewstage.* 事件 (M2 Phase 2 新): sess = None (源是 MaterialStore subscriber,
          不挂在 chat_manager 的 session 上). aggregator update / self-wake 检查跳过.
        """
        # 1) aggregator 接更广的事件类型 (含 spawn) — 给总控 ctx 列 in-flight subagent 用
        #    reviewstage.* 不进 aggregator (它只跟 subagent 状态相关).
        if self.aggregator is not None and event_type in AGGREGATOR_EVENT_TYPES:
            try:
                if event_type == "subagent.spawned":
                    self.aggregator.on_subagent_spawned(payload)
                elif event_type == "subagent.completed":
                    self.aggregator.on_subagent_completed(payload)
                elif event_type == "subagent.blocked":
                    self.aggregator.on_subagent_blocked(payload)
            except Exception:  # noqa: BLE001
                _log.exception("aggregator update failed for %s", event_type)

        # 1.5) R12 backstop: subagent.completed 时自动写 auto plan_completion log
        #      不依赖 LLM 主动调 record_plan_completion, 防止漏记 (用户原话 §2.9 强约束).
        #      只对"总控派出去的 subagent"记 —— 用户自己开的 codex/claude 会话不是 subagent,
        #      不该给它写 plan_completion / 也不该唤起总控 (用户反馈 2026-06-05)。
        if (
            event_type == "subagent.completed"
            and _is_delegated_subagent(sess)
        ):
            try:
                p = _auto_record_plan_completion(payload)
                if p is not None:
                    _log.info("auto plan_completion log written: %s", p)
            except Exception:  # noqa: BLE001
                _log.exception("auto plan_completion failed")

        # 2) 只有 HANDLED_EVENT_TYPES 真唤起 controller (spawn/aggregator-only 类不唤起).
        if event_type not in HANDLED_EVENT_TYPES:
            return

        # 2.5) 尊重「总控自动唤起」开关(设置面板 controller.auto_wake)。关 → 不唤起任何总控。
        #      step 1 / 1.5 的 aggregator 更新与 auto plan_completion 记录是 bookkeeping, 照常跑;
        #      这里只挡住"注入总控对话"这一步。用户没用总控时, 关掉它即可彻底安静。
        if not _auto_wake_enabled():
            _log.debug("ControllerWaker: controller.auto_wake off, skip wake for %s", event_type)
            return

        # 3) controller 自己 emit 的事件不应该唤起自己 (chat.py 已经在
        #    _consume_provider 排除了 provider == "controller", 这里再保险一次).
        #    reviewstage.* 事件没有 sess, 这步直接跳过.
        if sess is not None and getattr(sess, "provider", "") == "controller":
            return

        # 3.5) 关键(用户反馈 2026-06-05): subagent.* 只有"总控派出去的 subagent"才唤起总控。
        #      用户自己另开的 codex/claude 会话也会 emit subagent.completed —— 那跟总控无关,
        #      唤起只会白烧总控 opus 额度。reviewstage.*(sess=None)是用户审阅动作, 照常唤起。
        if event_type in ("subagent.completed", "subagent.blocked") and not _is_delegated_subagent(sess):
            _log.debug(
                "ControllerWaker: skip wake for %s from non-subagent session %s (caller_identity=%r)",
                event_type, getattr(sess, "id", "?"), getattr(sess, "caller_identity", None),
            )
            return

        # 4) 找所有活跃 controller session
        controllers = self._find_active_controllers()
        if not controllers:
            _log.debug("no active controller session for %s", event_type)
            return

        # 块 5 R2: subagent.completed 时把软 guard 违规清单 drain 出来塞 payload, 然后 format
        # (reviewstage.* 没 subagent_id, drain 直接跳)
        enriched_payload = dict(payload)
        if event_type == "subagent.completed":
            try:
                from omnicompany.dashboard.boss_sight.services.soft_violation_store import (
                    get_soft_violation_store,
                )
                sid = payload.get("subagent_id") or ""
                if sid:
                    violations = get_soft_violation_store().drain(sid)
                    if violations:
                        enriched_payload["soft_violations"] = [v.to_dict() for v in violations]
                        _log.info("ControllerWaker: %d soft violations drained for %s", len(violations), sid)
            except Exception:  # noqa: BLE001
                _log.exception("soft violations drain failed")

        # 5) inject user message — 整体丢后台, on_event 立即返回(兑现本函数"不能阻塞"契约)。
        #    关键: _format_event_as_user_msg 内部会 build_workflow_summary(全量扫工作区, 实测数秒)。
        #    它绝不能跑在这条同步 _notify 热路径上 —— 否则 store.add_comment / reviewstage capture
        #    (圈选/快照/调试交接)会被拖几秒甚至在多 controller 下叠加成"卡死"。
        #    把"格式化(丢线程, 不堵事件循环) + 注入"整体塞进一个后台 task。
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _log.warning("ControllerWaker: no running loop, skip inject for %s", event_type)
            return

        task = loop.create_task(
            self._format_and_broadcast(list(controllers), event_type, enriched_payload)
        )
        self._inflight_tasks.add(task)
        task.add_done_callback(self._inflight_tasks.discard)
        _log.info(
            "ControllerWaker: scheduled inject %s into %d controller(s) (source=%s)",
            event_type, len(controllers), payload.get("subagent_id", "?"),
        )

    def _find_active_controllers(self) -> list[Any]:
        """唯一(收敛)总控 session: provider==controller 且 alive 且未归档, 取最新一个。

        收敛(用户明示 2026-06-03 "收敛总控会话"): 历史上同时存在多个 controller session
        (测试/探针残留), 旧逻辑会把同一事件 inject 进**全部**活跃 controller → N 个总控同时跑
        turn, 既浪费又可能拖垮事件循环。现在只唤起唯一总控:
        - 排除已归档(archived)的 —— 前端 ControllerChat 也只认未归档, 两端一致。
        - 多个未归档时取 started_at 最新的那个(与前端选择规则一致), 保证唤起的恰是用户在用的那个。
        """
        sessions = getattr(self.chat_manager, "_sessions", {})
        live = [
            s for s in sessions.values()
            if getattr(s, "provider", "") == "controller"
            and getattr(s, "ended_at", None) is None
            and not getattr(s, "archived", False)
        ]
        if not live:
            return []
        live.sort(key=lambda s: getattr(s, "started_at", 0) or 0, reverse=True)
        return live[:1]

    async def _format_and_broadcast(
        self, controllers: list[Any], event_type: str, payload: dict[str, Any]
    ) -> None:
        """重活(workflow summary 全量构建)丢线程, 不阻塞事件循环; 再逐个注入 controller。

        格式化只做一次, 多 controller 复用同一条消息。
        """
        try:
            msg = await asyncio.to_thread(_format_event_as_user_msg, event_type, payload)
        except Exception:  # noqa: BLE001
            _log.exception("ControllerWaker format failed for %s", event_type)
            return
        for cs in controllers:
            await self._safe_inject(cs, msg)

    async def _safe_inject(self, controller_session: Any, msg: str) -> None:
        try:
            await self.chat_manager.submit_user_prompt(
                controller_session, msg, record_history=True
            )
        except Exception:  # noqa: BLE001
            _log.exception(
                "ControllerWaker inject failed for controller session %s",
                getattr(controller_session, "id", "?"),
            )


def make_reviewstage_bridge(waker: "ControllerWaker"):
    """M2 Phase 2 步骤 3: 把 MaterialStore 的 store-level 事件转 waker.on_event 调用.

    返回一个 store.subscribe(callback) 期待的回调签名:
        (event_type: str, material: Material) -> None

    映射:
      comment_added   → reviewstage.comment
      verdict_changed / 其他 (created/updated/pushed/annotation_added/deleted) → 不唤起。
        verdict(材料"通过/拒绝/阻断")是用户终结动作, 明确**不**回灌总控(用户 2026-06-13);
        前端审阅 UI 仍经独立 WS hub 实时收到 verdict_changed, 不受影响。
        (下面 reviewstage.verdict 的 payload/渲染机制保留但不再被触发, 留作日后可经
         开关重新接通的现成路径。)

    payload 字段:
      material_id, title, kind, tier, source_plan_id, source_subagent_id,
      verdict (verdict_changed 时), reason (verdict_changed 时),
      comment_content + comment_author (comment_added 时, 取最后一条 comment).

    sess=None (审阅台事件不属于任何 chat session).
    """
    def _bridge(store_event_type: str, material) -> None:
        try:
            mapping: dict[str, str] = {
                # 2026-06-13 用户明示: 评论与裁决都**不**自动唤起/回灌总控("不要搞自动发送")。
                # 评论落"每材料一个 markdown 文件"(独立 comments-file API, 不经此桥);
                # verdict 只走前端 WS hub 实时刷新。两者都不映射成唤起事件。
            }
            mapped = mapping.get(store_event_type)
            if mapped is None:
                return  # 评论/裁决/其他 store 事件都不唤起总控

            # material.to_dict() 字段
            md = material.to_dict() if hasattr(material, "to_dict") else {}

            # 独立性(用户明示 2026-06-03): UI 捕获(圈选/快照/调试交接)是用户的即时基础交互,
            # **不**同步唤起总控。否则每次捕获都触发一次总控 turn, 总控 turn 占着 ccdaemon
            # 事件循环, 连续/多 controller 下会把后续捕获请求拖到卡死(实测)。
            # 捕获材料照常进审阅队列 + workflow summary, 总控下次自然唤起(用户对话/子代理事件)
            # 时自会看到, 不丢信息, 只是不抢占。
            if md.get("source_plan_id") == "cockpit/user-capture":
                return
            payload: dict[str, Any] = {
                "material_id": md.get("id") or getattr(material, "id", "?"),
                "title": md.get("title") or getattr(material, "title", ""),
                "kind": md.get("kind"),
                "tier": md.get("tier"),
                "source_plan_id": md.get("source_plan_id"),
                "source_subagent_id": md.get("source_subagent_id"),
            }
            if mapped == "reviewstage.verdict":
                # 最后一条 verdict history 拿 verdict + reason + by
                history = md.get("history") or []
                last_verdict = next(
                    (h for h in reversed(history) if h.get("event") == "verdict"),
                    None,
                )
                payload["verdict"] = md.get("status")
                if last_verdict:
                    payload["reason"] = last_verdict.get("reason") or ""
                    payload["by"] = last_verdict.get("by") or "user"
            else:  # reviewstage.comment
                # 最后一条 comment
                comments = md.get("comments") or []
                if comments:
                    last_c = comments[-1]
                    payload["comment_id"] = last_c.get("id") or ""
                    payload["comment_content"] = last_c.get("content") or ""
                    payload["comment_author"] = last_c.get("author") or "user"
                    payload["target"] = last_c.get("target") or {}
                    payload["feedback_status"] = last_c.get("feedback_status") or "delivered"

            waker.on_event(sess=None, event_type=mapped, payload=payload, tags=[])
        except Exception:  # noqa: BLE001
            _log.exception("reviewstage bridge failed for %s", store_event_type)

    return _bridge


__all__ = ["ControllerWaker", "HANDLED_EVENT_TYPES", "make_reviewstage_bridge"]

# [OMNI] origin=codex domain=services/session-companion ts=2026-08-11 type=service
# [OMNI] material_id="material:services.session-companion.agent.py"
"""Persistent low-cost agent lane for proposing companion maintenance actions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnicompany.bus.memory import MemoryBus
from omnicompany.packages.services._core.agent.external_workers.runner import (
    ExternalAgentRunRequest,
    run_external_agent_request,
)

from .actions import validate_actions
from .models import CompanionEvent, stable_digest
from .policy import material_submission_allowed
from .store import CompanionStore, LockBusy


_SYSTEM = """你是主对话的伴随维护 agent。你不改代码，也不直接写台账；你只按当前事件和证据提出结构化动作，由确定性执行器写入既有真源。

每次事件都重新遵守下面的 CURRENT POLICY；它覆盖伴随会话此前关于“用户可能关心就送审”的旧习惯。

三个通道必须严格分开：
1. 会话历史简述 → digest。概括“关于什么、做了什么、产出什么、反馈什么”。状态、过程、完成报告、测试结果、故障、下一步都只属于 digest，不是审阅材料。
2. 长期有效的用户明确选择/规则 → decisions。必须附当前 user_input 中逐字可找到的 evidence_quote；普通任务要求、提问、AI 推断和临时状态不是决策。
3. 用户要看的少量成品 → materials/feedback。ReviewStage 默认拒绝新建；只有 review_context.creation_allowed=true，即当前 user_input 明确要求提交/送交/审阅某个成品时，才允许最多 1 份完整 markdown 工作报告。绝不因为“用户可能关心”“工作完成”“值得确认”“主 agent 漏交”而送审。

永远不得作为新审阅材料：会话摘要、决策或偏好、计划/项目绑定、进度/完成报告、实现说明、测试/审计结果、问题或待办、故障与修复过程、provider/轨迹/id、一般状态更新。待裁决问题留在对话；不要创建 key_question 材料。

反馈规则：只有 review_context.existing_materials 中的真实材料才可反馈。material_id 必须逐字取自该列表，禁止 mat_x 等占位符。明确通过=accept，明确拒绝=reject，修改要求=update，一般意见=comment；不能唯一匹配就不输出 feedback，也不要把它改造成新材料。

范围规则：只根据会话的用户输入、助手输出和压缩摘要识别当前 project/workstream/plan；工作目录不是项目证据。project_catalog 提供可选项目的 id、名称、标签和描述，只有内容与其中一个项目充分吻合才提出 scope segment。较新的显式绑定优先；不确定时留空，不要猜。

搜索规则：当前输入不足时最多给 3 个 search_queries。搜索只能补摘要、范围和明确决策的证据，不能用搜索把 creation_allowed=false 变成可送审。

只输出一个 JSON 对象，不要 markdown fence，不要解释：
{
  "search_queries": ["关键词"],
  "digest": {"project":"", "workstream":"", "plan":"", "title":"≤20字", "last_step":"≤40字", "summary":"持续摘要"},
  "scope_segments": [{"segment_id":"", "project":"", "workstream":"", "plan":"", "confidence":0.0, "make_default":false, "rationale":"", "evidence_refs":[]}],
  "decisions": [{"semantic_key":"", "statement":"长期有效的明确选择", "rationale":"", "boundary":"何时失效", "applies_to":"", "project":"", "plan":"", "chosen":[{"option":"", "why":""}], "rejected":[{"option":"", "why":""}], "evidence_quote":"当前用户原话的逐字片段", "evidence_refs":[]}],
  "materials": [{"semantic_key":"", "title":"", "kind":"markdown", "tier":"mandatory|important", "project":"", "plan":"", "track":"工作报告", "content":"完整可审成品", "rationale":"当前用户明确要求送审的原话", "evidence_refs":[]}],
  "feedback": [{"semantic_key":"", "material_id":"review_context.existing_materials 中真实存在的 mat_... ID", "kind":"accept|reject|comment|update", "explicit":false, "content":"", "reason":"", "evidence_refs":[]}],
  "unresolved": []
}

默认输出 materials=[]、feedback=[]、decisions=[]。只能逐字使用 available_evidence_refs；每个 scope/decision/material/feedback 至少一个证据。"""


@dataclass(frozen=True)
class AgentProposal:
    actions: dict[str, Any]
    audit: dict[str, Any]


def _json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("companion Agent did not return a JSON object") from None
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("companion Agent output must be a JSON object")
    return value


def _project_catalog() -> list[dict[str, str]]:
    project_catalog: list[dict[str, str]] = []
    try:
        from omnicompany.core.projects_registry import list_projects

        for project in list_projects():
            project_catalog.append({
                "id": str(project.get("id") or ""),
                "name": str(project.get("name") or ""),
                "short": str(project.get("short") or ""),
                "tags": ", ".join(str(tag) for tag in project.get("tags") or []),
                "desc": str(project.get("desc") or "")[:180],
            })
    except Exception:  # noqa: BLE001 - catalog enrichment is fail-open
        project_catalog = []
    return project_catalog


def _event_prompt(event: CompanionEvent, context: dict[str, Any]) -> str:
    body = {
        "event": event.to_dict(),
        "current_binding": context.get("binding") or {},
        "project_catalog": _project_catalog(),
        "maintenance_sinks": context.get("maintenance_sinks") or {},
        "review_context": context.get("review_context") or {},
        "available_evidence_refs": list(event.evidence_refs),
    }
    return _SYSTEM + "\n\nCURRENT EVENT（只以本段为准）：\n" + json.dumps(body, ensure_ascii=False, indent=2)


def _search_prompt(event: CompanionEvent, results: list[dict[str, Any]]) -> str:
    return (
        "这是你刚才请求的主会话 CLI 搜索结果。只使用其中属于本会话的内容；"
        "现在必须返回最终 JSON，并令 search_queries=[]。搜索结果不能改变当前轮的审阅准入；"
        "若当前 user_input 没有明确要求提交/审阅成品，materials 必须仍为空。\n"
        + json.dumps(
            {
                "event_id": event.event_id,
                "review_creation_allowed": material_submission_allowed(event.user_input),
                "project_catalog": _project_catalog(),
                "available_evidence_refs": list(event.evidence_refs),
                "search_results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _safe_chat_search(event: CompanionEvent, queries: list[str]) -> list[dict[str, Any]]:
    """Search the provider-native transcript resolved by the identity service."""

    transcript, native_session_id = _locate_event_transcript(event)
    if transcript is None:
        replay_marker = "[PTY replay only; screen buffer may be incomplete]"
        if replay_marker in event.assistant_output:
            replay = event.assistant_output.split(replay_marker, 1)[1].strip()
            rows: list[dict[str, Any]] = []
            for query in queries[:3]:
                try:
                    pattern = re.compile(str(query)[:120], re.IGNORECASE)
                except re.error as exc:
                    rows.append({"query": query, "error": str(exc)[:1000]})
                    continue
                matches = [line for line in replay.splitlines() if pattern.search(line)]
                if not matches:
                    matches = replay.splitlines()[:80]
                output = json.dumps(
                    {
                        "source": "pty_replay",
                        "complete": False,
                        "native_session_id": "",
                        "matches": matches[:80],
                    },
                    ensure_ascii=False,
                )[-16_000:]
                rows.append({"query": query, "exit_code": 0, "output": output, "error": ""})
            return rows
        return [{"query": query, "error": "native transcript unavailable"} for query in queries]
    rows: list[dict[str, Any]] = []
    for query in queries[:3]:
        try:
            matches = _search_transcript(
                transcript,
                query,
                native_session_id=native_session_id,
                limit=30,
            )
        except (OSError, re.error, sqlite3.Error) as exc:
            rows.append({"query": query, "error": str(exc)})
            continue
        output = json.dumps(
            {
                "source": str(transcript),
                "native_session_id": native_session_id,
                "matches": matches,
            },
            ensure_ascii=False,
        )[-16_000:]
        rows.append({
            "query": query,
            "exit_code": 0,
            "output": output,
            "error": "",
        })
    return rows


def _locate_event_transcript(event: CompanionEvent) -> tuple[Path | None, str]:
    from omnicompany.packages.services._core.identity import locate_sessions

    for identity in dict.fromkeys((event.session_id, event.trace_id)):
        if not identity:
            continue
        for match in locate_sessions(
            identity,
            limit=5,
            peek=False,
            include_orphans=True,
        ):
            transcript = match.transcript
            if transcript is None or not transcript.exists or not transcript.path:
                continue
            entry = match.entry or {}
            native_session_id = str(
                entry.get("provider_session_id")
                or entry.get("claude_session_id")
                or event.session_id
            )
            return Path(transcript.path), native_session_id
    return None, ""


def _search_transcript(
    path: Path,
    query: str,
    *,
    native_session_id: str,
    limit: int,
) -> list[str]:
    pattern = re.compile(str(query)[:120], re.IGNORECASE)
    if path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
        return _search_sqlite_transcript(
            path,
            pattern,
            native_session_id=native_session_id,
            limit=limit,
        )
    matches: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not pattern.search(line):
                continue
            value = line.strip()
            matches.append(f"line {line_number}: {value[:2000]}")
            if len(matches) >= limit:
                break
    return matches


def _search_sqlite_transcript(
    path: Path,
    pattern: re.Pattern[str],
    *,
    native_session_id: str,
    limit: int,
) -> list[str]:
    if not native_session_id:
        return []
    uri = f"file:{path.as_posix()}?mode=ro"
    matches: list[str] = []
    with sqlite3.connect(uri, uri=True, timeout=1.0) as database:
        for table in ("message", "part"):
            try:
                rows = database.execute(
                    f"SELECT data FROM {table} WHERE session_id = ? ORDER BY rowid",  # noqa: S608 - fixed allowlist
                    (native_session_id,),
                )
            except sqlite3.Error:
                continue
            for (raw,) in rows:
                value = str(raw or "")
                if not pattern.search(value):
                    continue
                matches.append(f"{table}: {value[:2000]}")
                if len(matches) >= limit:
                    return matches
    return matches


class CompanionAgent:
    """Run/resume one provider conversation per main-session project lane."""

    def __init__(self, store: CompanionStore) -> None:
        self.store = store

    def propose(self, event: CompanionEvent, context: dict[str, Any]) -> AgentProposal:
        lane = self.store.lane_state(event.lane_key)
        preferred = str(
            lane.get("provider")
            or os.environ.get("OMNI_SESSION_COMPANION_PROVIDER")
            or "opencode"
        )
        # Logical routes are attempted in the operator-approved order.  The
        # worker aliases are mapped below so every fallback is auditable.
        fallback_order = (
            "opencode",
            "the_company-api",
            "qwen-token-plan",
            "codex",
            "grok",
        )
        providers = list(dict.fromkeys([preferred, *fallback_order]))
        errors: list[str] = []
        for provider in providers:
            try:
                proposal = self._propose_with_provider(provider, event, context, lane)
                proposal.audit["fallback_errors"] = list(errors)
                return proposal
            except LockBusy as exc:
                errors.append(f"{provider}: busy ({exc})")
            except Exception as exc:  # noqa: BLE001 - fallback is part of the runtime contract
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
        raise RuntimeError("; ".join(errors) or "no companion provider available")

    def _propose_with_provider(
        self,
        provider: str,
        event: CompanionEvent,
        context: dict[str, Any],
        lane: dict[str, Any],
    ) -> AgentProposal:
        lock_key = "provider:opencode-state" if provider == "opencode" else f"provider:{provider}:{event.lane_key}"
        with self.store.lock(lock_key, stale_after_s=300):
            first, first_actions = self._run_action_round(
                provider,
                event,
                _event_prompt(event, context),
                lane,
            )
            run_result = first
            search_queries = list(first_actions["search_queries"])
            search_audit: list[dict[str, Any]] = []
            if first_actions["search_queries"]:
                search_results = _safe_chat_search(event, first_actions["search_queries"])
                search_audit = [
                    {
                        "query": str(row.get("query") or ""),
                        "exit_code": row.get("exit_code"),
                        "error": str(row.get("error") or "")[:1000],
                        "output_chars": len(str(row.get("output") or "")),
                        "output_digest": stable_digest(str(row.get("output") or ""), size=32),
                    }
                    for row in search_results
                ]
                refreshed_lane = self.store.lane_state(event.lane_key)
                run_result, actions = self._run_action_round(
                    provider,
                    event,
                    _search_prompt(event, search_results),
                    refreshed_lane,
                )
                if actions["search_queries"]:
                    raise ValueError("companion Agent requested another search round")
            else:
                actions = first_actions
            rounds = [first] if run_result is first else [first, run_result]
            run_ids = list(dict.fromkeys(
                str(run_id)
                for row in rounds
                for run_id in row.get("run_ids") or [row.get("run_id")]
                if run_id
            ))
            audit = {
                "provider": provider,
                "model": run_result.get("model"),
                "run_id": run_result.get("run_id"),
                "run_ids": run_ids,
                "duration_ms": sum(float(row.get("round_duration_ms") or row.get("duration_ms") or 0.0) for row in rounds),
                "session_id": run_result.get("provider_session_id"),
                "search_queries": search_queries,
                "searches": search_audit,
                "validation_repaired": any(bool(row.get("validation_repaired")) for row in rounds),
                "proposal_digest": stable_digest(actions, size=32),
                "fallback_errors": [],
            }
            return AgentProposal(actions=actions, audit=audit)

    def _run_action_round(
        self,
        provider: str,
        event: CompanionEvent,
        prompt: str,
        lane: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run one JSON round, resuming once when a cheap model truncates JSON."""

        result = self._run_provider(provider, event, prompt, lane)
        result["run_ids"] = [result.get("run_id")]
        result["round_duration_ms"] = float(result.get("duration_ms") or 0.0)
        result["validation_repaired"] = False
        try:
            return result, validate_actions(_json_object(result["final_text"]), event)
        except (TypeError, ValueError) as exc:
            repair_prompt = (
                "你上一轮输出未通过 JSON/schema 校验。不要搜索、不要解释；"
                "请修复为一个完整 JSON 对象，search_queries=[]。"
                f"当前轮 review_creation_allowed={material_submission_allowed(event.user_input)}；"
                "为 false 时 materials 必须为空。\n"
                f"校验错误：{type(exc).__name__}: {exc}\n"
                f"上一轮输出：\n{str(result.get('final_text') or '')[-12000:]}"
            )
            refreshed_lane = self.store.lane_state(event.lane_key)
            repaired = self._run_provider(
                provider,
                event,
                repair_prompt,
                refreshed_lane,
            )
            repaired["run_ids"] = [*result["run_ids"], repaired.get("run_id")]
            repaired["round_duration_ms"] = (
                float(result.get("round_duration_ms") or 0.0)
                + float(repaired.get("duration_ms") or 0.0)
            )
            repaired["validation_repaired"] = True
            return repaired, validate_actions(_json_object(repaired["final_text"]), event)

    def _run_provider(
        self,
        provider: str,
        event: CompanionEvent,
        prompt: str,
        lane: dict[str, Any],
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "minimal_context": True,
            "text_integrity": "strict",
            "idle_timeout_s": float(os.environ.get("OMNI_SESSION_COMPANION_IDLE_TIMEOUT_S") or 90),
        }
        model = None
        model_provider = ""
        runtime_profile = None
        worker_provider = provider
        if provider == "opencode":
            model = os.environ.get("OMNI_SESSION_COMPANION_OPENCODE_MODEL") or "opencode-go/deepseek-v4.1-flash"
            if lane.get("opencode_session_id"):
                metadata["session_id"] = lane["opencode_session_id"]
        elif provider == "the_company-api":
            worker_provider = "codex"
            model = os.environ.get("OMNI_SESSION_COMPANION_THE_COMPANY_MODEL") or "deepseek-v4.1-flash"
            model_provider = "the_company"
        elif provider == "qwen-token-plan":
            worker_provider = "omni-native"
            model = os.environ.get("OMNI_SESSION_COMPANION_NATIVE_MODEL") or "deepseek-v4-flash"
            model_provider = os.environ.get("OMNI_SESSION_COMPANION_NATIVE_MODEL_PROVIDER") or "qwen-token-plan"
            runtime_profile = "stable_pi"
            metadata.update({
                "export_context_checkpoint": True,
                "run_record_dir": str(self.store.root / "run_records" / event.event_id),
            })
            if lane.get("native_checkpoint_path"):
                metadata["resume_context_checkpoint_path"] = lane["native_checkpoint_path"]
        elif provider == "codex":
            worker_provider = "codex"
            model = os.environ.get("OMNI_SESSION_COMPANION_CODEX_MODEL") or "gpt-5.6-luna"
        elif provider == "omni-native":
            # Backward-compatible spelling for pre-existing lane state.
            worker_provider = "omni-native"
            model = os.environ.get("OMNI_SESSION_COMPANION_NATIVE_MODEL") or "deepseek-v4-flash"
            model_provider = os.environ.get("OMNI_SESSION_COMPANION_NATIVE_MODEL_PROVIDER") or "qwen-token-plan"
            runtime_profile = "stable_pi"
            metadata.update({
                "export_context_checkpoint": True,
                "run_record_dir": str(self.store.root / "run_records" / event.event_id),
            })
            if lane.get("native_checkpoint_path"):
                metadata["resume_context_checkpoint_path"] = lane["native_checkpoint_path"]
        elif provider == "grok":
            raise ValueError("grok companion worker is not registered")
        else:
            raise ValueError(f"unsupported companion provider: {provider}")

        # The main repository can be dirty or actively edited by other Agents.
        # Run the proposal-only companion in its own empty workspace so
        # readonly verification observes only this lane, not unrelated changes.
        agent_cwd = self.store.root / "workspaces" / stable_digest(event.lane_key, size=32)
        agent_cwd.mkdir(parents=True, exist_ok=True)
        request = ExternalAgentRunRequest(
            provider=worker_provider,
            prompt=prompt,
            cwd=agent_cwd,
            model=model,
            model_provider=model_provider,
            runtime_profile=runtime_profile,
            # Native exports checkpoints and may create runtime files in this
            # isolated lane workspace. It gets no repository cwd, and all
            # semantic writes still go through ActionExecutor.
            permission_mode="readonly" if provider == "opencode" else "workspace-write",
            model_policy="none",
            timeout_s=float(os.environ.get("OMNI_SESSION_COMPANION_TIMEOUT_S") or 180),
            trace_id=f"companion_{event.event_id}",
            metadata=metadata,
        )
        # Companion receipts are persisted by CompanionStore.  A short-lived
        # MemoryBus avoids contending with the main 500MB EventBus SQLite while
        # the provider still emits its normal in-run events.
        result = asyncio.run(run_external_agent_request(request, bus=MemoryBus()))
        status = result.normalized_status().value
        if status != "succeeded":
            raise RuntimeError(result.error or f"external Agent status={status}")
        provider_session_id = str(result.raw.get("session_id") or "")
        checkpoint_path = str(result.raw.get("context_checkpoint_path") or "")
        patch: dict[str, Any] = {"provider": provider}
        if provider_session_id:
            patch["opencode_session_id"] = provider_session_id
        if checkpoint_path:
            patch["native_checkpoint_path"] = checkpoint_path
        self.store.save_lane_state(event.lane_key, patch)
        return {
            "run_id": result.run_id,
            "final_text": result.final_text,
            "duration_ms": result.duration_ms,
            "provider_session_id": provider_session_id or checkpoint_path,
            "model": model,
        }


__all__ = ["AgentProposal", "CompanionAgent"]

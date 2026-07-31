"""Executable AFKJ benchmark for persistent external AI-player sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import utc_now
from .external_agent_continuity import (
    AFKJExternalAgentContinuityManifestV1,
    ExternalAgentBenchmarkCandidateV1,
    ExternalAgentContinuousSessionV1,
    PlayerFacilityContractV1,
    build_afkj_external_agent_manifest,
    build_player_facility_contract,
)
from .external_agent_runtime import (
    ContinuousExternalAgentRunner,
    ExternalAgentInvocationV1,
    ExternalAgentSessionLedger,
    ExternalAgentTokenUsageV1,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class AFKJB0QuestionV1(_StrictModel):
    id: str = Field(pattern=r"^Q[0-9]{2}$")
    question: str = Field(min_length=1)
    options: dict[str, str]
    correct_choice: str = Field(pattern=r"^[A-D]$")


class ExternalAgentBenchmarkTurnV1(_StrictModel):
    invocation_id: str
    operation: Literal["start", "resume"]
    status: Literal["succeeded", "failed", "timed_out"]
    external_session_id: str | None
    resolved_model_id: str | None
    model_evidence: str
    actual_effort: str
    effort_evidence: str
    duration_seconds: float
    provider_duration_seconds: float | None
    time_to_first_token_seconds: float | None
    provider_cost_usd: float | None
    usage: ExternalAgentTokenUsageV1
    unexpected_tool_events: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    event_log_path: str
    event_log_sha256: str
    last_message_path: str
    last_message_sha256: str


class AFKJB0BenchmarkResultV1(_StrictModel):
    schema_id: Literal[
        "game-observatory.ai-player.afkj-external-agent-b0-result.v1"
    ] = Field(
        default="game-observatory.ai-player.afkj-external-agent-b0-result.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    benchmark_id: Literal["afkj_external_agent_continuity_v1"]
    task_id: Literal["B0"] = "B0"
    candidate_id: str
    repetition: int = Field(ge=1)
    facility_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_manifest_path: str
    fixture_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_semantic_status: str
    fixture_freeze_status: str
    provider: Literal["codex-cli", "claude-code-cli"]
    requested_model_selector: str
    requested_effort: Literal["medium"]
    external_session_id: str | None
    continuity_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    b0_correct: int = Field(ge=0, le=10)
    b0_total: Literal[10] = 10
    warm_probe_correct: int = Field(ge=0, le=4)
    warm_probe_total: Literal[4] = 4
    same_native_session: bool
    quality_pass: bool
    quality_failures: list[str]
    turns: list[ExternalAgentBenchmarkTurnV1]
    raw_runtime_root: str
    status: Literal["succeeded", "failed"]
    started_at: str
    completed_at: str


B0_QUESTIONS: tuple[AFKJB0QuestionV1, ...] = (
    AFKJB0QuestionV1(
        id="Q01",
        question="一个已验证且当前来源匹配的固定操作应优先交给哪一层？",
        options={"A": "A0 快速执行", "B": "A2 持续 Agent", "C": "A3 设施工程", "D": "新建 Session"},
        correct_choice="A",
    ),
    AFKJB0QuestionV1(
        id="Q02",
        question="遇到未见过的界面且没有成熟技能时，第一原则是什么？",
        options={"A": "重复点击最近坐标", "B": "步进观察、单动作验证并记录证据", "C": "立即退出游戏", "D": "跳过不记"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q03",
        question="普通点击完成后，持续探索 Agent 应如何继续？",
        options={"A": "新建 Agent", "B": "结束任务", "C": "resume 同一原生 Session", "D": "清空记忆"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q04",
        question="一个游戏动作的最小可复盘证据边界是什么？",
        options={"A": "只有 After", "B": "文字结论", "C": "Before、Action、After 与点击位置", "D": "只存 UI 树"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q05",
        question="画面长时间不变时如何保存？",
        options={"A": "持续逐帧保存", "B": "保存变化帧、关键状态和必要上下文", "C": "完全不存", "D": "每秒截图"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q06",
        question="哪类动作始终需要新增授权？",
        options={"A": "使用虚拟资源", "B": "领取任务奖励", "C": "真实货币支付或提交外部身份资料", "D": "切换英雄"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q07",
        question="任务队列暂时为空时应做什么？",
        options={"A": "空转等待", "B": "枚举剩余前沿与阻断原因并生成任务", "C": "重复当前动作", "D": "删除状态图"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q08",
        question="发现需要编写复杂新适配器时如何处理？",
        options={"A": "A2 边玩边大改", "B": "生成设施变更请求，由 A3 或主 Agent 实现验收", "C": "忽略", "D": "让 A0 猜测"},
        correct_choice="B",
    ),
    AFKJB0QuestionV1(
        id="Q09",
        question="AFKJ fixture 标记为 candidate、not_frozen 时可如何表述？",
        options={"A": "已冻结真值", "B": "官方规则", "C": "保留来源并明确为候选证据", "D": "删除状态"},
        correct_choice="C",
    ),
    AFKJB0QuestionV1(
        id="Q10",
        question="外部调用达到 120 秒硬预算后应如何收口？",
        options={"A": "继续无限等待", "B": "只关 wrapper", "C": "终止进程树并把部分事件结算为超时调用", "D": "覆盖日志重试"},
        correct_choice="C",
    ),
)


def build_b0_prompt(
    contract: PlayerFacilityContractV1,
    fixture_summary: dict[str, Any],
    *,
    continuity_token: str,
) -> str:
    questions = [
        {
            "id": item.id,
            "question": item.question,
            "options": item.options,
        }
        for item in B0_QUESTIONS
    ]
    return (
        "你正在执行 AFKJ 外部 AI 玩家连续 Session 基准 B0。禁止设备操作、禁止修改文件、"
        "禁止调用工具。设施合同只在本轮加载一次，后续通过同一原生 Session 续接。"
        "请根据合同回答十道单选题。只输出一个 JSON 对象，不要 Markdown："
        '{"answers":[{"id":"Q01","choice":"A"}],'
        '"continuity_token":"原样复述收到的 token"}。\
\
'
        f"continuity_token={continuity_token}\
\
"
        f"设施合同={json.dumps(contract.model_dump(mode='json', by_alias=True), ensure_ascii=False)}\
\
"
        f"AFKJ fixture={json.dumps(fixture_summary, ensure_ascii=False)}\
\
"
        f"题目={json.dumps(questions, ensure_ascii=False)}"
    )


def build_b0_warm_prompt() -> str:
    return (
        "延续 B0 首轮的同一原生 Session。禁止读取文件、禁止调用工具、禁止重新加载完整设施合同。"
        "只输出一个 JSON 对象，不要 Markdown："
        '{"continuity_token":"复述首轮 token",'
        '"same_session":true,"would_reload_full_contract":false,'
        '"next_layer_for_known_action":"A0"}'
    )


def parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\
".join(lines[1:-1]).strip() if len(lines) >= 3 else candidate
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def score_b0_answers(payload: dict[str, Any] | None) -> int:
    if not payload or not isinstance(payload.get("answers"), list):
        return 0
    choices = {
        str(item.get("id")): str(item.get("choice", "")).upper()
        for item in payload["answers"]
        if isinstance(item, dict)
    }
    return sum(choices.get(item.id) == item.correct_choice for item in B0_QUESTIONS)


def score_warm_probe(payload: dict[str, Any] | None, *, continuity_token: str) -> int:
    if payload is None:
        return 0
    return sum(
        (
            payload.get("continuity_token") == continuity_token,
            payload.get("same_session") is True,
            payload.get("would_reload_full_contract") is False,
            str(payload.get("next_layer_for_known_action", "")).upper() == "A0",
        )
    )


class AFKJExternalAgentBenchmarkRunner:
    def __init__(
        self,
        repository_root: Path,
        *,
        runner_factory: Callable[[ExternalAgentSessionLedger], ContinuousExternalAgentRunner]
        | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.runner_factory = runner_factory or ContinuousExternalAgentRunner

    async def run_b0(
        self,
        *,
        candidate_id: str,
        repetition: int,
        timeout_seconds: float,
        output_root: Path | None = None,
        run_id: str | None = None,
    ) -> tuple[AFKJB0BenchmarkResultV1, Path]:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        contract = build_player_facility_contract()
        manifest = build_afkj_external_agent_manifest(contract)
        candidate = _candidate(manifest, candidate_id)
        fixture_path = (
            self.repository_root
            / "data/domains/game_observatory/benchmarks/ai_player/fixtures/"
            / "afk_hero_growth_v1_candidate_v4/candidate_manifest.v1.json"
        )
        fixture_bytes = fixture_path.read_bytes()
        fixture = json.loads(fixture_bytes)
        identifier = run_id or (
            f"afkj-b0.{candidate.id}.r{repetition}.{uuid.uuid4().hex[:12]}"
        )
        root = (
            output_root.resolve()
            if output_root is not None
            else self.repository_root
            / "data/domains/game_observatory/benchmarks/ai_player/runtime/external_agent"
        )
        run_root = root / identifier
        run_root.mkdir(parents=True, exist_ok=False)
        ledger = ExternalAgentSessionLedger(run_root)
        runner = self.runner_factory(ledger)
        started_at = utc_now()
        token = uuid.uuid4().hex
        external_session = ExternalAgentContinuousSessionV1(
            id=f"external-session.{identifier}",
            provider=candidate.provider,
            model_selector=candidate.cli_model_selector,
            requested_effort="medium",
            actual_effort="unreported",
            permission_mode="readonly",
            environment_id="environment.benchmark.afkj.candidate-v4.offline",
            phase_id="EA-3.B0",
            facility_contract_sha256=contract.facility_contract_sha256,
            task_ids=["benchmark.afkj.B0"],
            started_at=started_at,
            last_heartbeat_at=started_at,
            updated_at=started_at,
        )
        fixture_summary = {
            "path": str(fixture_path.relative_to(self.repository_root)),
            "id": fixture.get("id"),
            "semantic_status": fixture.get("semantic_status"),
            "freeze_status": fixture.get("freeze_status"),
            "counts": fixture.get("counts"),
            "rule": "candidate fixture cannot be represented as frozen truth",
        }
        session, cold = await runner.start(
            external_session,
            prompt=build_b0_prompt(contract, fixture_summary, continuity_token=token),
            cwd=self.repository_root,
            timeout_seconds=timeout_seconds,
        )
        turns = [_turn(ledger, cold)]
        warm: ExternalAgentInvocationV1 | None = None
        if cold.status == "succeeded":
            session, warm = await runner.resume(
                session.id,
                prompt=build_b0_warm_prompt(),
                cwd=self.repository_root,
                timeout_seconds=timeout_seconds,
            )
            turns.append(_turn(ledger, warm))
        cold_payload = _invocation_message(ledger, cold)
        warm_payload = _invocation_message(ledger, warm) if warm else None
        b0_correct = score_b0_answers(cold_payload)
        warm_correct = score_warm_probe(warm_payload, continuity_token=token)
        same_session = bool(
            warm
            and cold.external_session_id
            and warm.external_session_id == cold.external_session_id
        )
        failures: list[str] = []
        if cold.status != "succeeded":
            failures.append(f"cold turn {cold.status}")
        if warm is None or warm.status != "succeeded":
            failures.append("warm resume did not succeed")
        if b0_correct != 10:
            failures.append(f"facility understanding {b0_correct}/10")
        if warm_correct != 4:
            failures.append(f"warm continuity {warm_correct}/4")
        if not same_session:
            failures.append("provider session id changed")
        final_turn = warm or cold
        if not final_turn.resolved_model_id or (
            candidate.expected_model_family not in final_turn.resolved_model_id
        ):
            failures.append(
                f"resolved model mismatch: {final_turn.resolved_model_id or 'unreported'}"
            )
        if final_turn.actual_effort != "medium":
            failures.append(f"actual effort is {final_turn.actual_effort}")
        unexpected_tools = sum(item.unexpected_tool_events for item in turns)
        if unexpected_tools:
            failures.append(f"unexpected tool events: {unexpected_tools}")
        completed_at = utc_now()
        result = AFKJB0BenchmarkResultV1(
            id=identifier,
            benchmark_id=manifest.benchmark_id,
            candidate_id=candidate.id,
            repetition=repetition,
            facility_contract_sha256=contract.facility_contract_sha256,
            fixture_manifest_path=str(fixture_path.relative_to(self.repository_root)),
            fixture_manifest_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
            fixture_semantic_status=str(fixture.get("semantic_status") or "unknown"),
            fixture_freeze_status=str(fixture.get("freeze_status") or "unknown"),
            provider=candidate.provider,
            requested_model_selector=candidate.cli_model_selector,
            requested_effort="medium",
            external_session_id=session.external_session_id,
            continuity_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            b0_correct=b0_correct,
            warm_probe_correct=warm_correct,
            same_native_session=same_session,
            quality_pass=not failures,
            quality_failures=failures,
            turns=turns,
            raw_runtime_root=str(run_root.relative_to(self.repository_root)),
            status="succeeded" if not failures else "failed",
            started_at=started_at,
            completed_at=completed_at,
        )
        result_path = run_root / "result.json"
        result_path.write_text(
            json.dumps(
                result.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\
",
            encoding="utf-8",
            newline="\
",
        )
        return result, result_path


def compare_b0_results(results: list[AFKJB0BenchmarkResultV1]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for candidate_id in sorted({item.candidate_id for item in results}):
        samples = [item for item in results if item.candidate_id == candidate_id]
        cold = [item.turns[0].duration_seconds for item in samples if item.turns]
        warm = [item.turns[1].duration_seconds for item in samples if len(item.turns) > 1]
        rows.append(
            {
                "candidate_id": candidate_id,
                "samples": len(samples),
                "quality_passes": sum(item.quality_pass for item in samples),
                "b0_accuracy": sum(item.b0_correct for item in samples)
                / (10 * len(samples)),
                "warm_accuracy": sum(item.warm_probe_correct for item in samples)
                / (4 * len(samples)),
                "cold_duration_median_seconds": _median(cold),
                "warm_duration_median_seconds": _median(warm),
                "unexpected_tool_events": sum(
                    turn.unexpected_tool_events
                    for item in samples
                    for turn in item.turns
                ),
                "input_tokens": sum(
                    turn.usage.input_tokens for item in samples for turn in item.turns
                ),
                "cached_input_tokens": sum(
                    turn.usage.cached_input_tokens for item in samples for turn in item.turns
                ),
                "cache_creation_input_tokens": sum(
                    turn.usage.cache_creation_input_tokens
                    for item in samples
                    for turn in item.turns
                ),
                "output_tokens": sum(
                    turn.usage.output_tokens for item in samples for turn in item.turns
                ),
            }
        )
    return {
        "schema": "game-observatory.ai-player.afkj-external-agent-b0-comparison.v1",
        "benchmark_id": "afkj_external_agent_continuity_v1",
        "task_id": "B0",
        "rows": rows,
        "selection_allowed": bool(rows)
        and all(row["quality_passes"] == row["samples"] for row in rows),
        "selection_rule": "only compare speed after every included sample passes quality",
    }


def load_b0_result(path: Path) -> AFKJB0BenchmarkResultV1:
    return AFKJB0BenchmarkResultV1.model_validate_json(path.read_text(encoding="utf-8"))


def _candidate(
    manifest: AFKJExternalAgentContinuityManifestV1,
    candidate_id: str,
) -> ExternalAgentBenchmarkCandidateV1:
    for candidate in manifest.candidates:
        if candidate.id == candidate_id:
            return candidate
    raise ValueError(f"unknown benchmark candidate: {candidate_id}")


def _invocation_message(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1 | None,
) -> dict[str, Any] | None:
    if invocation is None:
        return None
    path = ledger.root / invocation.last_message_path
    return parse_json_object(path.read_text(encoding="utf-8", errors="replace"))


def _turn(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> ExternalAgentBenchmarkTurnV1:
    return ExternalAgentBenchmarkTurnV1(
        invocation_id=invocation.id,
        operation=invocation.operation,
        status=invocation.status,
        external_session_id=invocation.external_session_id,
        resolved_model_id=invocation.resolved_model_id,
        model_evidence=invocation.model_evidence,
        actual_effort=invocation.actual_effort,
        effort_evidence=invocation.effort_evidence,
        duration_seconds=invocation.duration_seconds,
        provider_duration_seconds=invocation.provider_duration_seconds,
        time_to_first_token_seconds=invocation.time_to_first_token_seconds,
        provider_cost_usd=invocation.provider_cost_usd,
        usage=invocation.usage,
        unexpected_tool_events=_count_tool_events(ledger, invocation),
        warning_count=len(invocation.warnings),
        event_log_path=invocation.event_log_path,
        event_log_sha256=invocation.event_log_sha256,
        last_message_path=invocation.last_message_path,
        last_message_sha256=invocation.last_message_sha256,
    )


def _count_tool_events(
    ledger: ExternalAgentSessionLedger,
    invocation: ExternalAgentInvocationV1,
) -> int:
    path = ledger.root / invocation.event_log_path
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = record.get("event") if isinstance(record, dict) else None
        if not isinstance(event, dict):
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        if item.get("type") in {"command_execution", "mcp_tool_call", "tool_call"}:
            count += 1
        count += sum(
            1
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        )
    return count


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def run_b0_sync(**kwargs: Any) -> tuple[AFKJB0BenchmarkResultV1, Path]:
    runner = kwargs.pop("runner")
    return asyncio.run(runner.run_b0(**kwargs))


__all__ = [
    "AFKJB0BenchmarkResultV1",
    "AFKJB0QuestionV1",
    "AFKJExternalAgentBenchmarkRunner",
    "B0_QUESTIONS",
    "build_b0_prompt",
    "build_b0_warm_prompt",
    "compare_b0_results",
    "load_b0_result",
    "parse_json_object",
    "run_b0_sync",
    "score_b0_answers",
    "score_warm_probe",
]
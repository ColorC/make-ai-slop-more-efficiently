# [OMNI] origin=codex domain=services/hypothesis ts=2026-08-14T00:00:00Z type=worker status=active
# [OMNI] material_id="material:learning.hypothesis.belief_reflector.external_proposal_application.py"
"""Reflect exploration evidence through an external proposal/application split."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from typing import Any, ClassVar

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.agent_runtime import run_readonly_structured_agent

from ..belief_tools import EXPLORE_TAG, beliefs_snapshot, domain_tag


StructuredReflectorRunner = Callable[..., Awaitable[dict[str, Any]]]

_ACTIONS = ("record", "challenge", "resolve", "link")
_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "operations", "gaps"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "information_insufficient"]},
        "summary": {"type": "string", "maxLength": 1000},
        "operations": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "target_id",
                    "statement",
                    "risk_if_wrong",
                    "evidence_query",
                    "rationale",
                    "rests_on",
                    "outcome",
                    "evidence",
                    "method",
                    "reason",
                    "source",
                    "rel",
                    "dst",
                ],
                "properties": {
                    "action": {"type": "string", "enum": list(_ACTIONS)},
                    "target_id": {"type": "string"},
                    "statement": {"type": "string"},
                    "risk_if_wrong": {
                        "type": "string",
                        "enum": ["", "low", "medium", "high"],
                    },
                    "evidence_query": {"type": "string"},
                    "rationale": {"type": "string"},
                    "rests_on": {"type": "array", "items": {"type": "string"}},
                    "outcome": {
                        "type": "string",
                        "enum": ["", "supported", "partial", "falsified"],
                    },
                    "evidence": {"type": "string"},
                    "method": {"type": "string"},
                    "reason": {"type": "string"},
                    "source": {"type": "string"},
                    "rel": {
                        "type": "string",
                        "enum": ["", "rests_on", "related", "supersedes"],
                    },
                    "dst": {"type": "string"},
                },
            },
        },
        "gaps": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
}

_INSTRUCTIONS = """\
You are the hypothesis pipeline's belief reflector. Analyze the supplied
Experimenter trace and current domain-scoped belief snapshot. Return proposals;
you do not own or mutate the decision library. Omni will validate the complete
operation set and apply it only after the run.

Use record for a new falsifiable behavioral statement that is not synonymous
with an existing belief. risk_if_wrong is required for record; rests_on may
only contain supplied belief ids. Use challenge when the trace contains a
counterexample. Resolve to supported or partial only after that belief has
already been challenged, or after an earlier challenge operation in this same
proposal. Resolve to falsified only with concrete evidence and method; every
supplied downstream dependency must then have a later challenge operation.
Use link only between supplied belief ids. Do not use generic activity notes as
beliefs, and do not infer evidence absent from the trace.

Every operation object must contain every schema field. Put an empty string or
empty array in fields unused by that action. If the trace cannot support a safe
proposal, return information_insufficient with explicit gaps. A valid no-op is
status=ok with an empty operations array and a factual summary. Return exactly
one schema-valid JSON object with no Markdown fence.
"""

_MAX_INPUT_CHARS = 120_000


class _DecisionGateway:
    """Thin Omni control-plane adapter around the canonical decision library."""

    @staticmethod
    def _library():
        from omnicompany.packages.domains.decisions import library

        return library

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self._library().get(record_id)

    def upsert(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return self._library().upsert(record)

    def challenge(self, record_id: str, reason: str, *, source: str, challenger: str) -> dict[str, Any]:
        return self._library().challenge(
            record_id,
            reason,
            source=source,
            challenger=challenger,
        )

    def resolve(
        self,
        record_id: str,
        outcome: str,
        *,
        evidence: str,
        method: str,
        by: str,
    ) -> dict[str, Any]:
        return self._library().resolve(
            record_id,
            outcome,
            evidence=evidence,
            method=method,
            by=by,
        )

    def add_link(self, src: str, rel: str, dst: str) -> dict[str, Any]:
        return self._library().add_link(src, rel, dst)

    def impacted_by(self, record_id: str) -> list[dict[str, Any]]:
        return self._library().impacted_by(record_id)


class _DecisionApplicationError(RuntimeError):
    def __init__(self, message: str, *, applied: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.applied = applied


def _bounded_task(input_data: dict[str, Any], gateway: Any) -> str:
    domain = str(input_data.get("explore_domain") or "").strip()
    snapshot = input_data.get("beliefs_snapshot") or beliefs_snapshot(domain)
    beliefs = snapshot.get("beliefs", []) if isinstance(snapshot, dict) else []
    downstream: dict[str, list[str]] = {}
    for belief in beliefs if isinstance(beliefs, list) else []:
        if not isinstance(belief, dict) or not belief.get("id"):
            continue
        belief_id = str(belief["id"])
        downstream[belief_id] = [
            str(item.get("id"))
            for item in gateway.impacted_by(belief_id)
            if isinstance(item, dict) and item.get("id")
        ]
    payload = {
        "explore_domain": domain,
        "session_id": str(input_data.get("session_id") or ""),
        "iteration": input_data.get("iteration", 0),
        "beliefs_snapshot": snapshot,
        "downstream_rests_on_this": downstream,
        "experimenter_trace": input_data.get("trace", []),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(raw) > _MAX_INPUT_CHARS:
        raise ValueError(
            f"belief reflection input exceeds {_MAX_INPUT_CHARS} characters; split the trace explicitly"
        )
    return "Belief-reflection input:\n" + raw


def _operation_error(
    operations: list[Any],
    *,
    snapshot: dict[str, Any],
    gateway: Any,
) -> str | None:
    beliefs = snapshot.get("beliefs", []) if isinstance(snapshot, dict) else []
    by_id = {
        str(item.get("id")): item
        for item in beliefs
        if isinstance(item, dict) and item.get("id")
    }
    known_ids = set(by_id)
    known_statements = {
        str(item.get("statement") or "").strip().casefold()
        for item in by_id.values()
        if str(item.get("statement") or "").strip()
    }
    challenged = {
        record_id
        for record_id, item in by_id.items()
        if item.get("status") == "challenged"
    }
    proposed_statements: set[str] = set()

    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            return f"operations[{index}] must be an object"
        action = raw.get("action")
        if action not in _ACTIONS:
            return f"operations[{index}] has unknown action {action!r}"
        target = str(raw.get("target_id") or "").strip()
        if action == "record":
            statement = str(raw.get("statement") or "").strip()
            normalized = statement.casefold()
            if len(statement) < 10:
                return f"operations[{index}] record statement is too short"
            if normalized in known_statements or normalized in proposed_statements:
                return f"operations[{index}] duplicates an existing/proposed statement"
            proposed_statements.add(normalized)
            if raw.get("risk_if_wrong") not in {"low", "medium", "high"}:
                return f"operations[{index}] record lacks risk_if_wrong"
            if len(str(raw.get("evidence_query") or "").strip()) < 5:
                return f"operations[{index}] record lacks an evidence_query"
            rests_on = raw.get("rests_on")
            if not isinstance(rests_on, list) or not set(map(str, rests_on)).issubset(known_ids):
                return f"operations[{index}] record rests_on contains an unknown domain belief"
            continue

        if target not in known_ids:
            return f"operations[{index}] targets an unknown domain belief {target!r}"
        if gateway.get(target) is None:
            return f"operations[{index}] target disappeared from the decision library"

        if action == "challenge":
            if len(str(raw.get("reason") or "").strip()) < 5:
                return f"operations[{index}] challenge lacks a concrete reason"
            challenged.add(target)
        elif action == "resolve":
            outcome = raw.get("outcome")
            if outcome not in {"supported", "partial", "falsified"}:
                return f"operations[{index}] resolve has an invalid outcome"
            if len(str(raw.get("evidence") or "").strip()) < 5:
                return f"operations[{index}] resolve lacks evidence"
            if len(str(raw.get("method") or "").strip()) < 3:
                return f"operations[{index}] resolve lacks a method"
            if outcome in {"supported", "partial"} and target not in challenged:
                return f"operations[{index}] cannot support an unchallenged belief"
            if outcome == "falsified":
                later_challenges = {
                    str(item.get("target_id") or "")
                    for item in operations[index + 1 :]
                    if isinstance(item, dict) and item.get("action") == "challenge"
                }
                impacted = {
                    str(item.get("id"))
                    for item in gateway.impacted_by(target)
                    if isinstance(item, dict) and item.get("id")
                }
                missing = sorted(impacted - later_challenges)
                if missing:
                    return (
                        f"operations[{index}] falsification lacks later downstream challenges: {missing}"
                    )
        elif action == "link":
            rel = raw.get("rel")
            dst = str(raw.get("dst") or "").strip()
            if rel not in {"rests_on", "related", "supersedes"}:
                return f"operations[{index}] link has an invalid relation"
            if dst not in known_ids or gateway.get(dst) is None:
                return f"operations[{index}] link destination is not a current domain belief"
            if target == dst:
                return f"operations[{index}] cannot link a belief to itself"
    return None


def _apply_operations(
    operations: list[dict[str, Any]],
    *,
    domain: str,
    session_id: str,
    gateway: Any,
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    actor = "hypothesis.reflector"
    for operation in operations:
        action = operation["action"]
        try:
            if action == "record":
                record: dict[str, Any] = {
                    "kind": "belief",
                    "statement": str(operation["statement"]).strip(),
                    "risk_if_wrong": operation["risk_if_wrong"],
                    "evidence_query": str(operation["evidence_query"]).strip(),
                    "nature": "factual",
                    "tags": [EXPLORE_TAG, domain_tag(domain)],
                    "track": {"kind": "plan", "id": f"hypothesis-{domain}"},
                    "origin": {
                        "channel": "agent-runtime",
                        "author": actor,
                        "session_ref": session_id,
                    },
                    "created_by": actor,
                }
                rationale = str(operation.get("rationale") or "").strip()
                if rationale:
                    record["rationale"] = rationale
                rests_on = [str(item) for item in operation.get("rests_on", [])]
                if rests_on:
                    record["links"] = {"rests_on": rests_on}
                result, _ = gateway.upsert(record)
            elif action == "challenge":
                result = gateway.challenge(
                    str(operation["target_id"]),
                    str(operation["reason"]).strip(),
                    source=str(operation.get("source") or "").strip(),
                    challenger=actor,
                )
            elif action == "resolve":
                result = gateway.resolve(
                    str(operation["target_id"]),
                    str(operation["outcome"]),
                    evidence=str(operation["evidence"]).strip(),
                    method=str(operation["method"]).strip(),
                    by=actor,
                )
            else:
                result = gateway.add_link(
                    str(operation["target_id"]),
                    str(operation["rel"]),
                    str(operation["dst"]),
                )
        except Exception as error:  # noqa: BLE001 - retain exact partial-application evidence
            raise _DecisionApplicationError(
                f"operation {len(applied)} ({action}) failed: {type(error).__name__}: {error}",
                applied=list(applied),
            ) from error
        applied.append(
            {
                "action": action,
                "record_id": str(result.get("id") or operation.get("target_id") or ""),
                "status": str(result.get("status") or ""),
            }
        )
    return applied


class BeliefReflectorRouter(Worker):
    """Ask DSH for proposals; let Omni validate and apply decision mutations."""

    DESCRIPTION: ClassVar[str] = (
        "External zero-tool belief proposal followed by Omni-owned full-set "
        "validation, canonical decision-library application, and snapshot readback."
    )
    FORMAT_IN: ClassVar[str] = "hypothesis.factlog"
    FORMAT_OUT: ClassVar[str] = "hypothesis.store_diff"

    def __init__(
        self,
        *,
        model: str | None = None,
        bus: Any = None,
        report_runner: StructuredReflectorRunner = run_readonly_structured_agent,
        decision_gateway: Any = None,
        **_: Any,
    ) -> None:
        self._model = model or "deepseek-v4-flash"
        self._bus = bus
        self._report_runner = report_runner
        self._gateway = decision_gateway or _DecisionGateway()

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="belief-reflector input must be an object")
        domain = str(input_data.get("explore_domain") or "").strip()
        if not domain:
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="explore_domain is required")
        snapshot = input_data.get("beliefs_snapshot") or beliefs_snapshot(domain)
        if not isinstance(snapshot, dict) or snapshot.get("domain") not in {None, "", domain}:
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="belief snapshot domain does not match")
        try:
            response = await self._report_runner(
                task=_bounded_task(input_data, self._gateway),
                instructions=_INSTRUCTIONS,
                result_schema=_RESULT_SCHEMA,
                workspace_cwd=omni_workspace_root(),
                caller="hypothesis.belief-reflector",
                model=self._model,
                max_steps=8,
                max_output_tokens=8192,
                timeout_seconds=240,
                allowed_tools=(),
            )
        except Exception as error:  # noqa: BLE001 - runtime/control-plane faults fail closed
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"belief reflection execution failed: {type(error).__name__}: {error}",
            )
        if not response.get("ok") or not isinstance(response.get("final"), dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"external belief reflection failed: {response.get('error') or 'missing result'}",
                details={"agent_runtime": response.get("runtime", {})},
            )

        result = dict(response["final"])
        if result.get("status") == "information_insufficient":
            gaps = result.get("gaps")
            if not isinstance(gaps, list) or not gaps:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    output=result,
                    diagnosis="information_insufficient result lacks explicit gaps",
                )
            return Verdict(
                kind=VerdictKind.PARTIAL,
                output={"andon": True, "reason": "information_insufficient", "gaps": gaps},
                diagnosis="belief reflector requested more evidence",
                details={"agent_runtime": response.get("runtime", {})},
            )
        if result.get("status") != "ok" or not isinstance(result.get("operations"), list):
            return Verdict(kind=VerdictKind.FAIL, output=result, diagnosis="invalid belief proposal envelope")

        operations = list(result["operations"])
        validation_error = _operation_error(
            operations,
            snapshot=snapshot,
            gateway=self._gateway,
        )
        if validation_error:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"proposal_summary": result.get("summary", "")},
                diagnosis=f"belief proposal rejected before application: {validation_error}",
                details={"agent_runtime": response.get("runtime", {})},
            )
        try:
            applied = _apply_operations(
                operations,
                domain=domain,
                session_id=str(input_data.get("session_id") or ""),
                gateway=self._gateway,
            )
            final_snapshot = beliefs_snapshot(domain) if isinstance(self._gateway, _DecisionGateway) else self._gateway.snapshot(domain)
        except Exception as error:  # noqa: BLE001 - explicit partial-application evidence is returned
            partial = error.applied if isinstance(error, _DecisionApplicationError) else locals().get("applied", [])
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"applied": partial},
                diagnosis=f"Omni decision application failed: {type(error).__name__}: {error}",
                details={"agent_runtime": response.get("runtime", {})},
            )

        output = {
            "domain": domain,
            "snapshot": final_snapshot,
            "proposal_summary": str(result.get("summary") or "").strip(),
            "applied": applied,
            "applied_count": len(applied),
            "_meta": {
                "worker": "BeliefReflectorRouter",
                "stage": "external_proposal_omni_application",
                "decision_authority": "omni-control-plane",
                "agent_runtime": response.get("runtime", {}),
            },
        }
        return Verdict(
            kind=VerdictKind.PASS,
            output=output,
            diagnosis=f"belief proposal applied by Omni: {len(applied)} operations",
            details={"agent_runtime": response.get("runtime", {})},
        )


class BeliefReflectorWorker(BeliefReflectorRouter):
    """Worker compatibility name for the canonical reflector position."""


__all__ = ["BeliefReflectorRouter", "BeliefReflectorWorker"]

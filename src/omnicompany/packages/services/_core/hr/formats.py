# [OMNI] material_id="material:core.hr.formats.py" origin=codex ts=2026-08-18 type=config status=active agent=codex
# [OMNI] summary="HR digest Worker 的证据请求与非排名画像 Material 契约"
# [OMNI] tags=hr,material,format,digest
"""Material contracts for the HR digest worker."""

from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry


M_HR_DIGEST_REQUEST = Material(
    id="hr.digest-request.v1",
    name="hr.digest-request.v1",
    description=(
        "单个 actor 的有界 HR 证据请求。packet 由确定性层生成，包含客观事件计数、"
        "用户或 agent 反馈、去重互评、Material 映射、代表性 evidence refs 与明确约束；"
        "HrDigestWorker 只能依据这些引用生成语义摘要。"
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "packet": {
                "type": "object",
                "properties": {
                    "schema": {"const": "omnicompany.hr-digest-packet.v1"},
                    "actor_id": {"type": "string", "minLength": 1},
                    "packet_fingerprint": {"type": "string", "minLength": 1},
                    "allowed_evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["schema", "actor_id", "packet_fingerprint", "allowed_evidence_refs"],
            },
        },
        "required": ["packet"],
    },
    tags=["hr", "digest", "kind.source"],
)


M_HR_DIGEST = Material(
    id="hr.digest.v1",
    name="hr.digest.v1",
    description=(
        "HrDigestWorker 输出的证据约束画像。digest 分开列 strength、risk、speed、review reminder"
        "和 caveat；model 与 packet_fingerprint 固定本次模型和输入版本。该 Material 不含总分、"
        "跨任务排名，也不会自动转写成 peer review。"
    ),
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "digest": {"type": "object"},
            "model": {"type": "string", "minLength": 1},
            "packet_fingerprint": {"type": "string", "minLength": 1},
        },
        "required": ["digest", "model", "packet_fingerprint"],
    },
    tags=["hr", "digest", "kind.sink"],
)


M_HR_HISTORICAL_FEEDBACK_CANDIDATE = Material(
    id="hr.historical-feedback-candidate.v1",
    name="hr.historical-feedback-candidate.v1",
    description="一条根会话真实用户消息及其有限前文；用于复核是否构成历史评价。",
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "candidate": {
                "type": "object",
                "properties": {"user_text": {"type": "string", "minLength": 1}},
                "required": ["user_text"],
            },
        },
        "required": ["candidate"],
    },
    tags=["hr", "user-feedback", "kind.source"],
)


M_HR_HISTORICAL_FEEDBACK_CLASSIFICATION = Material(
    id="hr.historical-feedback-classification.v1",
    name="hr.historical-feedback-classification.v1",
    description="历史用户评价候选的有证据分类；不得创作 actor 或脱离原文的判断。",
    parent="doc",
    json_schema={
        "type": "object",
        "properties": {
            "classification": {"type": "object"},
            "model": {"type": "string", "minLength": 1},
        },
        "required": ["classification", "model"],
    },
    tags=["hr", "user-feedback", "kind.sink"],
)


def register_formats(registry: FormatRegistry) -> None:
    for material in (
        M_HR_DIGEST_REQUEST,
        M_HR_DIGEST,
        M_HR_HISTORICAL_FEEDBACK_CANDIDATE,
        M_HR_HISTORICAL_FEEDBACK_CLASSIFICATION,
    ):
        if not registry.is_registered(material.id):
            registry.register(material)


__all__ = [
    "M_HR_DIGEST_REQUEST", "M_HR_DIGEST",
    "M_HR_HISTORICAL_FEEDBACK_CANDIDATE", "M_HR_HISTORICAL_FEEDBACK_CLASSIFICATION",
    "register_formats",
]

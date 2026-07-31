"""Evidence-bound reconstruction overlays for known legacy capture damage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .store import AIPlayerStore
from .text_integrity import (
    CanonicalTextCorrectionV1,
    canonical_text_issue,
    canonical_text_sha256,
    value_at_json_path,
)


@dataclass(frozen=True, slots=True)
class TextReconstructionSpec:
    source_table: str
    record_id: str
    field_path: str
    expected_damaged_text: str
    projected_text: str
    diagnosis: str
    basis_reference_ids: tuple[str, ...]


SANGUO_CAPTURE_RECONSTRUCTIONS: tuple[TextReconstructionSpec, ...] = (
    TextReconstructionSpec(
        source_table="evidence_steps",
        record_id="evidence.step.aeee3a32330644929d00a80c2c96636d",
        field_path="$.target_name",
        expected_damaged_text="?????????",
        projected_text="好友面板联系人页签",
        diagnosis=(
            "The reserved command names the friend-panel Contacts tab; the bound tap "
            "and OCR evidence place it on the 联系人 tab."
        ),
        basis_reference_ids=(
            "command.sanguo.friend-panel-contacts-tab.20260716.v1",
            "task.sanguo.friend-panel.inspect-contacts-tab.20260716.v1",
        ),
    ),
    TextReconstructionSpec(
        source_table="evidence_steps",
        record_id="evidence.step.d82e0088e68e4dc3b48706d942d65d6f",
        field_path="$.target_name",
        expected_damaged_text="????????????",
        projected_text="好友面板右侧折叠箭头",
        diagnosis=(
            "The reserved command and observed-change record both identify the gold "
            "collapse arrow at the exact captured bounds."
        ),
        basis_reference_ids=(
            "command.sanguo.friend-panel-collapse-arrow.20260716.v1",
            "task.frontier.d910ff087c6a119f4aa17194",
        ),
    ),
    TextReconstructionSpec(
        source_table="evidence_steps",
        record_id="evidence.step.0cb85504498f4a31877453156ecd2756",
        field_path="$.target_name",
        expected_damaged_text="????",
        projected_text="聊天按钮",
        diagnosis=(
            "The run purpose is open-chat-no-message, and the later adjudication names "
            "the same action as a chat-button tap with no semantic state change."
        ),
        basis_reference_ids=(
            "command.sanguo.inventory-explore.step01.20260716.v1",
            "task.sanguo.coverage.inventory-unlocked-businesses.v1",
            "state.auto.4cedb552e3238fc6@2",
        ),
    ),
    TextReconstructionSpec(
        source_table="evidence_runs",
        record_id="evidence.run.284deb5c28d547d6826d1d4e712c64c4",
        field_path="$.environment.world",
        expected_damaged_text="????",
        projected_text="投鞭断水",
        diagnosis=(
            "The immutable world_scope_id and selected environment identity both encode "
            "the world name toubian-duanshui."
        ),
        basis_reference_ids=(
            "world.sanguo.bilibili.1641.toubian-duanshui",
            "environment.sanguo.bilibili.mumu.account.lueyang-zhenwei-jiang."
            "server-1641.world-toubian-duanshui.1_31_0",
        ),
    ),
)


def _source_payload(player: AIPlayerStore, spec: TextReconstructionSpec) -> dict:
    if spec.source_table == "evidence_steps":
        record = player.observatory_store.get_evidence_step(spec.record_id)
    elif spec.source_table == "evidence_runs":
        record = player.observatory_store.get_evidence_run(spec.record_id)
    else:  # pragma: no cover - specs are module-owned and statically bounded
        raise ValueError(f"unsupported reconstruction source: {spec.source_table}")
    if record is None:
        raise ValueError(f"missing reconstruction source record: {spec.record_id}")
    return record.model_dump(mode="json", by_alias=True)


def _correction_id(spec: TextReconstructionSpec, original_sha256: str) -> str:
    identity = json.dumps(
        {
            "source_table": spec.source_table,
            "record_id": spec.record_id,
            "field_path": spec.field_path,
            "original_sha256": original_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "text-reconstruction." + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:32]


def register_text_reconstructions(
    player: AIPlayerStore,
    *,
    specs: tuple[TextReconstructionSpec, ...],
    created_at: str,
    created_by: str,
) -> list[CanonicalTextCorrectionV1]:
    """Append exact overlays only while every immutable source still matches."""

    corrections: list[CanonicalTextCorrectionV1] = []
    for spec in specs:
        source_value = value_at_json_path(_source_payload(player, spec), spec.field_path)
        if source_value != spec.expected_damaged_text:
            raise ValueError(
                f"reconstruction source changed at {spec.record_id}{spec.field_path}"
            )
        if not isinstance(source_value, str) or canonical_text_issue(source_value) is None:
            raise ValueError(
                f"reconstruction source is not damaged at {spec.record_id}{spec.field_path}"
            )
        original_sha256 = canonical_text_sha256(source_value)
        correction = CanonicalTextCorrectionV1(
            id=_correction_id(spec, original_sha256),
            source_table=spec.source_table,
            record_key={"id": spec.record_id},
            source_column="body_json",
            field_path=spec.field_path,
            original_sha256=original_sha256,
            status="reconstructed",
            projected_text=spec.projected_text,
            diagnosis=spec.diagnosis,
            basis_reference_ids=list(spec.basis_reference_ids),
            created_by=created_by,
            created_at=created_at,
        )
        corrections.append(player.append_text_correction(correction))
    return corrections


def register_sanguo_capture_reconstructions(
    player: AIPlayerStore,
    *,
    created_at: str,
    created_by: str = "sanguo-capture-text-reconstruction.v1",
) -> list[CanonicalTextCorrectionV1]:
    """Append the four known Sanguo capture repairs idempotently."""

    return register_text_reconstructions(
        player,
        specs=SANGUO_CAPTURE_RECONSTRUCTIONS,
        created_at=created_at,
        created_by=created_by,
    )

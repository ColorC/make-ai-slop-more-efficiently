# [OMNI] material_id="material:core.hr.note.py" origin=claude-code ts=2026-08-17T00:00:00+08:00 type=module
# [OMNI] summary="LLM 人事系统用户评价落盘(P2-1) —— 先 blame 反查 actor 再落盘，
#             window/unknown 档不猜 actor，也允许 --actor 直接声明跳过反查"
# [OMNI] why="用户硬要求(plan.md §2.4 第 1 条)：用户说的是'这东西不行'不是'某会话不行'，
#             要求用户自己查是谁干的等于这条通道不存在；但冤枉一个模型比丢一条评价更严重，
#             所以反查定不下来时必须如实留白，不能编造归属"
# [OMNI] tags=hr,note,attribution,user-feedback
"""LLM 人事系统 —— 用户评价落盘层（纯确定性，不含任何 LLM 调用）。

权威依据: plan.md §2.4 第 1 条(用户评价落盘)。

`record_note()` 的红线: 反查 `blame()` 命中 `window`/`unknown` 档时，`actor_id`
必须留空——**不许把评价静默挂到一个猜出来的 actor 上**。`attribution_tier` 如实
写四档之一（或 `"declared"`，见下）；调用方(CLI)必须把这一点显式告知用户。

这条红线在实现上是"结构性"的，不是靠 note.py 自己判断：`blame.BlameResult.actor_id`
本身只在 `tier=="direct"` 时非空(见 blame.py 三条红线)，`vcs`/`window`/`unknown`
三档一律返回 `""`。`record_note()` 原样透传 `result.actor_id`，不做任何"看着差不多
就给个归属"的加工，红线因此自动成立，不依赖本文件重复判断一次。

`--actor` 直接声明(用户已经知道是谁干的，跳过反查)对应 `attribution_tier="declared"`
——这不是 `blame.TIERS` 闭集里的档位(反查得出)，是使用者主动声明，两者语义不同，
故意用不同的字符串区分，不混进 blame 的四档closed set。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from uuid import uuid4

from .blame import CommitIndex, blame
from .schema import HrEvent

NOTES_FILENAME = "notes.jsonl"
NOTE_AMENDMENTS_FILENAME = "note_amendments.jsonl"
NOTE_SCHEMA = "omnicompany.hr-note.v3"
NOTE_AMENDMENT_SCHEMA = "omnicompany.hr-note-amendment.v1"

SEVERITIES: tuple[str, ...] = ("pass", "imperfect", "severe")
"""与已有互评设施对齐(packages/services/_core/agent/resource_control.py 的
VALID_SEVERITIES)，词表保持一致，方便后续 P4 把两套记录接到一起看。"""

SOURCES: tuple[str, ...] = ("user", "agent")

NOTE_ATTRIBUTION_TIERS: tuple[str, ...] = (
    "direct", "vcs", "window", "unknown", "declared", "historical_session"
)
"""前四个来自 blame.TIERS(反查得出)，"declared" 是调用方用 --actor 主动声明、
跳过反查时才会出现的第五种取值，不属于 blame.py 的四档置信度闭集。"""

TARGET_KINDS: tuple[str, ...] = ("path", "material", "session", "facility", "unknown")
FEEDBACK_DIMENSIONS: tuple[str, ...] = (
    "quality", "correctness", "speed", "scope", "communication", "hygiene", "usability"
)
EXTRACTION_METHODS: tuple[str, ...] = ("direct", "historical_rule", "historical_llm")
EXTRACTION_CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")


class HrNoteRejected(ValueError):
    """record_note() 入参不合法，拒收，不落盘。"""


@dataclass
class HrNote:
    """一条落盘的用户/agent 评价记录。"""

    id: str
    ts: str
    path: str
    severity: str                  # SEVERITIES 之一
    note: str
    attribution_tier: str          # NOTE_ATTRIBUTION_TIERS 之一
    actor_id: str = ""             # 仅 attribution_tier in ("direct","declared") 时非空
    evidence_refs: list[str] = field(default_factory=list)
    source: str = "agent"          # SOURCES 之一；CLI 不得默认冒充 user
    source_ref: str = ""            # 评价本身的可回读来源，不等同于归因证据
    reporter_actor_id: str = ""     # source=agent 时必须明确谁作出了评价
    schema: str = NOTE_SCHEMA
    target_kind: str = "path"
    target_ref: str = ""
    task_type: str = "general"
    dimension: str = "quality"
    extraction_method: str = "direct"
    extraction_confidence: str = "high"
    feedback_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HrNoteAmendment:
    id: str
    ts: str
    note_id: str
    actor_id: str
    changes: dict[str, str]
    reason: str
    evidence_refs: list[str]
    schema: str = NOTE_AMENDMENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    """UTC ISO8601，毫秒精度，形如 2026-08-17T05:33:42.911Z(与 schema.py 的 ts 约定一致)。"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _notes_path(data_dir: Path) -> Path:
    return Path(data_dir) / NOTES_FILENAME


def _note_amendments_path(data_dir: Path) -> Path:
    return Path(data_dir) / NOTE_AMENDMENTS_FILENAME


def _append_note(note: HrNote, *, data_dir: Path) -> None:
    """append-only 单行写入，与 store.py 的原子写模式一致(单次 os.write，
    Windows 下 'a'/O_APPEND 语义保证只追加不覆盖)。notes 是一次性用户动作，不
    做去重——不像 collector 产出那样存在"增量重跑导致同一条被再次解析"的场景。
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _notes_path(data_dir)
    line = (json.dumps(note.to_dict(), ensure_ascii=False) + "\n").encode("utf-8", errors="replace")
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _append_amendment(amendment: HrNoteAmendment, *, data_dir: Path) -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(amendment.to_dict(), ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(_note_amendments_path(data_dir)), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)


def record_note(
    *,
    path: str,
    severity: str,
    note: str,
    data_dir: Path,
    events: Iterable[HrEvent] | None = None,
    actor_id: str | None = None,
    commit_index: CommitIndex = None,
    file_mtime: float | None = None,
    source: str = "agent",
    source_ref: str = "",
    reporter_actor_id: str = "",
    attribution_evidence_refs: Iterable[str] | None = None,
    target_kind: str = "path",
    target_ref: str = "",
    task_type: str = "general",
    dimension: str = "quality",
    extraction_method: str = "direct",
    extraction_confidence: str = "high",
    feedback_fingerprint: str = "",
    historical_session_actor_id: str = "",
) -> HrNote:
    """先(除非 `actor_id` 显式指定)调 `blame()` 反查 actor，再落盘一条评价。

    Args:
        path: 被评价的文件路径。
        severity: SEVERITIES 之一("pass"/"imperfect"/"severe")。
        note: 一句话说明，不可为空。
        data_dir: 落盘目录(注入，禁止硬编码)。
        events: 反查用的 HrEvent 全集；`actor_id` 已显式指定时可省略/忽略。
        actor_id: 用户已经知道是谁干的时直接传入，跳过 blame 反查
            (`attribution_tier` 记为 `"declared"`)。
        commit_index / file_mtime: 透传给 `blame()`(见 blame.py 对应参数)。
        source: "user"(用户口头评价) 或 "agent"(agent 习惯性互评触发的记录)。

    Returns:
        落盘成功后的 HrNote。

    红线: 若走反查且命中 `window`/`unknown` 档，`actor_id` 留空——不猜、不编造。
    """
    if severity not in SEVERITIES:
        raise HrNoteRejected(f"severity 须为 {SEVERITIES}，收到 {severity!r}")
    if not (note or "").strip():
        raise HrNoteRejected("note 不可为空")
    if source not in SOURCES:
        raise HrNoteRejected(f"source 须为 {SOURCES}，收到 {source!r}")
    if target_kind not in TARGET_KINDS:
        raise HrNoteRejected(f"target_kind must be one of {TARGET_KINDS}, got {target_kind!r}")
    if dimension not in FEEDBACK_DIMENSIONS:
        raise HrNoteRejected(f"dimension must be one of {FEEDBACK_DIMENSIONS}, got {dimension!r}")
    if extraction_method not in EXTRACTION_METHODS:
        raise HrNoteRejected(
            f"extraction_method must be one of {EXTRACTION_METHODS}, got {extraction_method!r}"
        )
    if extraction_confidence not in EXTRACTION_CONFIDENCES:
        raise HrNoteRejected(
            f"extraction_confidence must be one of {EXTRACTION_CONFIDENCES}, "
            f"got {extraction_confidence!r}"
        )
    if not (path or "").strip():
        raise HrNoteRejected("path 不可为空")
    source_ref = (source_ref or "").strip()
    reporter_actor_id = (reporter_actor_id or "").strip()
    if not source_ref:
        raise HrNoteRejected("source_ref 不可为空；评价必须能回读到原始用户消息或 agent 证据")
    if source == "agent" and not reporter_actor_id:
        raise HrNoteRejected("source=agent 时 reporter_actor_id 不可为空")

    historical_session_actor_id = (historical_session_actor_id or "").strip()
    if historical_session_actor_id:
        if actor_id:
            raise HrNoteRejected("actor_id and historical_session_actor_id are mutually exclusive")
        if source != "user" or extraction_method not in {"historical_rule", "historical_llm"}:
            raise HrNoteRejected(
                "historical_session_actor_id requires source=user and a historical extraction method"
            )
        attribution_tier = "historical_session"
        resolved_actor_id = historical_session_actor_id
        evidence_refs = [str(ref).strip() for ref in attribution_evidence_refs or [] if str(ref).strip()]
        if source_ref not in evidence_refs:
            evidence_refs.append(source_ref)
    elif actor_id:
        attribution_tier = "declared"
        resolved_actor_id = actor_id.strip()
        evidence_refs = [str(ref).strip() for ref in attribution_evidence_refs or [] if str(ref).strip()]
        if not evidence_refs:
            raise HrNoteRejected("显式 actor 归因必须提供 attribution_evidence_refs")
    else:
        result = blame(path, events=list(events or []), commit_index=commit_index, file_mtime=file_mtime)
        attribution_tier = result.tier
        resolved_actor_id = result.actor_id  # blame() 的红线保证: 非 direct 档时恒为 ""
        evidence_refs = list(result.evidence_refs)

    hr_note = HrNote(
        id=f"note_{uuid4().hex}",
        ts=_now_iso(),
        path=path,
        severity=severity,
        note=note.strip(),
        attribution_tier=attribution_tier,
        actor_id=resolved_actor_id,
        evidence_refs=evidence_refs,
        source=source,
        source_ref=source_ref,
        reporter_actor_id=reporter_actor_id,
        target_kind=target_kind,
        target_ref=(target_ref or path).strip(),
        task_type=(task_type or "general").strip(),
        dimension=dimension,
        extraction_method=extraction_method,
        extraction_confidence=extraction_confidence,
        feedback_fingerprint=(feedback_fingerprint or "").strip(),
    )
    _append_note(hr_note, data_dir=data_dir)
    return hr_note


def read_note_amendments(*, data_dir: Path) -> Iterator[HrNoteAmendment]:
    path = _note_amendments_path(Path(data_dir))
    if not path.is_file():
        return
    known_fields = set(HrNoteAmendment.__dataclass_fields__)
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or raw.get("schema") != NOTE_AMENDMENT_SCHEMA:
                continue
            try:
                yield HrNoteAmendment(**{key: value for key, value in raw.items() if key in known_fields})
            except TypeError:
                continue


_AMENDABLE_FIELDS = {"severity", "task_type", "dimension", "target_kind", "target_ref"}


def record_note_amendment(
    *, note_id: str, actor_id: str, changes: dict[str, str], reason: str,
    evidence_refs: Iterable[str], data_dir: Path,
) -> HrNoteAmendment:
    """Append a bounded classification correction without rewriting user evidence."""
    note_id = note_id.strip()
    actor_id = actor_id.strip()
    reason = reason.strip()
    evidence = [str(item).strip() for item in evidence_refs if str(item).strip()]
    clean_changes = {str(key): str(value).strip() for key, value in changes.items() if str(value).strip()}
    if not note_id or not actor_id or not reason or not evidence:
        raise HrNoteRejected("note amendment requires note_id, actor_id, reason and evidence_refs")
    if not clean_changes or not set(clean_changes).issubset(_AMENDABLE_FIELDS):
        raise HrNoteRejected(f"note amendment fields must be a non-empty subset of {_AMENDABLE_FIELDS}")
    current = next((row for row in read_notes(data_dir=data_dir, raw=True) if row.id == note_id), None)
    if current is None:
        raise HrNoteRejected(f"unknown note_id: {note_id}")
    if "severity" in clean_changes and clean_changes["severity"] not in SEVERITIES:
        raise HrNoteRejected("invalid amended severity")
    if "dimension" in clean_changes and clean_changes["dimension"] not in FEEDBACK_DIMENSIONS:
        raise HrNoteRejected("invalid amended dimension")
    if "target_kind" in clean_changes and clean_changes["target_kind"] not in TARGET_KINDS:
        raise HrNoteRejected("invalid amended target_kind")
    amendment = HrNoteAmendment(
        id=f"note_amendment_{uuid4().hex}", ts=_now_iso(), note_id=note_id,
        actor_id=actor_id, changes=clean_changes, reason=reason, evidence_refs=evidence,
    )
    _append_amendment(amendment, data_dir=data_dir)
    return amendment


def read_notes(*, data_dir: Path, raw: bool = False) -> Iterator[HrNote]:
    """流式逐行读取 `data_dir/notes.jsonl`。坏行跳过，不中断整体读取。"""
    path = _notes_path(Path(data_dir))
    if not path.is_file():
        return
    known_fields = set(HrNote.__dataclass_fields__)
    rows: list[HrNote] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            clean = {k: v for k, v in rec.items() if k in known_fields}
            try:
                rows.append(HrNote(**clean))
            except TypeError:
                continue
    if raw:
        yield from rows
        return
    changes_by_id: dict[str, dict[str, str]] = {}
    for amendment in read_note_amendments(data_dir=data_dir):
        changes_by_id.setdefault(amendment.note_id, {}).update(amendment.changes)
    for row in rows:
        changes = changes_by_id.get(row.id) or {}
        yield replace(row, **changes) if changes else row


def summarize_notes(notes: Iterable[HrNote]) -> dict[str, Any]:
    """Aggregate feedback as evidence counts, never as a quality score."""
    by_actor: dict[str, dict[str, Any]] = {}
    unattributed = {"total": 0, "pass": 0, "imperfect": 0, "severe": 0, "note_ids": []}
    total = 0
    for item in notes:
        total += 1
        if item.actor_id:
            row = by_actor.setdefault(item.actor_id, {
                "total": 0,
                "pass": 0,
                "imperfect": 0,
                "severe": 0,
                "by_source": {"user": 0, "agent": 0},
                "by_attribution_tier": {},
                "note_ids": [],
            })
            row["total"] += 1
            row[item.severity] += 1
            row["by_source"][item.source] = row["by_source"].get(item.source, 0) + 1
            row["by_attribution_tier"][item.attribution_tier] = (
                row["by_attribution_tier"].get(item.attribution_tier, 0) + 1
            )
            row["note_ids"].append(item.id)
        else:
            unattributed["total"] += 1
            unattributed[item.severity] += 1
            unattributed["note_ids"].append(item.id)
    return {
        "total": total,
        "attributed_total": sum(row["total"] for row in by_actor.values()),
        "unattributed": unattributed,
        "by_actor": by_actor,
        "quality_score": None,
        "ranking": None,
        "evidence_policy": {
            "primary": "user_feedback",
            "auxiliary": "peer_review",
            "rule": "peer review cannot override explicit user feedback",
        },
    }


__all__ = [
    "NOTES_FILENAME",
    "NOTE_AMENDMENTS_FILENAME",
    "NOTE_SCHEMA",
    "NOTE_AMENDMENT_SCHEMA",
    "SEVERITIES",
    "SOURCES",
    "NOTE_ATTRIBUTION_TIERS",
    "TARGET_KINDS",
    "FEEDBACK_DIMENSIONS",
    "EXTRACTION_METHODS",
    "EXTRACTION_CONFIDENCES",
    "HrNoteRejected",
    "HrNote",
    "HrNoteAmendment",
    "record_note",
    "record_note_amendment",
    "read_notes",
    "read_note_amendments",
    "summarize_notes",
]

"""Discover evidence-grounded gameplay boundaries from canonical play history.

The producer is deliberately conservative.  It uses deterministic business
signals only to propose a review candidate; it never promotes a candidate into
confirmed design content.  Navigation edges may describe the entrance or exit
of a candidate, but they cannot create one on their own.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable

from ..models import EvidenceRun, EvidenceStep
from .contracts import (
    EvidenceReferenceV1,
    FrontierTaskV1,
    GameplayCandidateV1,
    TransitionEdgeV1,
)
from .store import AIPlayerStore


_NAVIGATION_TERMS = (
    "返回",
    "关闭",
    "退出",
    "进入",
    "打开",
    "切换",
    "入口",
    "箭头",
    "列表",
    "页签",
    "面板",
    "浏览",
    "展开",
    "收起",
    "上一页",
    "下一页",
)
_SIGNAL_TERMS: dict[str, tuple[str, ...]] = {
    "resource": (
        "资源",
        "消耗",
        "花费",
        "产出",
        "生产",
        "采集",
        "粮食",
        "木材",
        "石料",
        "铁矿",
        "铜币",
        "金币",
        "体力",
        "材料",
    ),
    "progression": (
        "升级",
        "升至",
        "等级",
        "强化",
        "突破",
        "成长",
        "研究",
        "建造",
        "经验",
        "共鸣",
    ),
    "unlock": ("解锁", "开启条件", "前置条件", "升级条件"),
    "loop": ("扫荡", "攻占", "征兵", "训练", "招募", "合成", "采集"),
    "result": ("领取", "奖励", "结算", "完成", "获得"),
    "configuration": ("编队", "换将", "阵型", "装备", "战法"),
    "combat": ("出征", "战斗", "军演", "讨伐"),
}
_CATEGORY_LABELS = {
    "resource": "资源循环",
    "progression": "成长",
    "unlock": "解锁",
    "loop": "循环",
    "result": "奖励与结算",
    "configuration": "配置",
    "combat": "战斗",
}
_ACTION_TERMS = tuple(
    sorted(
        {
            *(term for terms in _SIGNAL_TERMS.values() for term in terms),
            "使用",
            "兑换",
            "购买",
        },
        key=len,
        reverse=True,
    )
)
_UI_SUFFIXES = (
    "建筑卡片",
    "操作面板",
    "升级面板",
    "确认面板",
    "卡片",
    "标牌",
    "按钮",
    "入口",
    "面板",
    "界面",
    "操作",
    "任务",
    "奖励",
    "结算",
    "条件",
)
_LEADING_WORDS = (
    "确认",
    "查看",
    "打开",
    "关闭",
    "进入",
    "退出",
    "返回",
    "选择",
    "点击",
    "执行",
    "当前",
    "使用",
)
_NAVIGATION_PREFIXES = ("返回", "关闭", "退出", "查看", "打开", "进入", "浏览")
_CONFIGURATION_ACTION_TERMS = (
    "选择",
    "更换",
    "切换",
    "上阵",
    "下阵",
    "换将",
    "穿戴",
    "卸下",
    "学习",
    "装配",
    "保存",
)
_COMBAT_ACTION_TERMS = ("挑战", "出征", "攻占", "扫荡", "开战", "战斗", "讨伐")
_RESOURCE_ACTION_TERMS = (
    "采集",
    "征收",
    "生产",
    "领取",
    "使用",
    "消耗",
    "兑换",
    "购买",
)
_RESOURCE_CONTAINER_TERMS = ("自选箱", "资源箱", "礼包", "宝箱", "补给箱")
_CITY_BUILDING_TERMS = (
    "君王殿",
    "民居",
    "仓库",
    "冶铁场",
    "伐木场",
    "采石场",
    "磨坊",
    "农田",
    "木材场",
    "石料场",
    "铁矿场",
    "城建",
    "建筑升级",
)


@dataclass(frozen=True)
class GameplayCandidateDiscoveryReport:
    scanned_edge_count: int
    eligible_anchor_count: int
    candidate_version_ids: tuple[str, ...]
    unchanged_candidate_ids: tuple[str, ...]
    review_locked_candidate_ids: tuple[str, ...]
    rejected_navigation_edge_count: int
    rejected_incomplete_anchor_count: int
    invalid_evidence_edge_count: int


@dataclass(frozen=True)
class _EdgeEvidence:
    edge: TransitionEdgeV1
    step: EvidenceStep
    run: EvidenceRun
    task: FrontierTaskV1
    target_name: str
    expectation: str
    signal_kinds: frozenset[str]
    anchor_key: str | None
    context_family_key: str | None
    subject: str | None
    category: str | None

    @property
    def text(self) -> str:
        return "；".join(item for item in (self.target_name, self.expectation) if item)

    @property
    def navigation_only(self) -> bool:
        return bool(self.signal_kinds == frozenset() and _contains_any(self.text, _NAVIGATION_TERMS))


class _EvidenceCache:
    def __init__(self, store: AIPlayerStore) -> None:
        self._store = store.observatory_store
        self._steps: dict[str, EvidenceStep | None] = {}
        self._runs: dict[str, EvidenceRun | None] = {}

    def step(self, step_id: str) -> EvidenceStep | None:
        if step_id not in self._steps:
            self._steps[step_id] = self._store.get_evidence_step(step_id)
        return self._steps[step_id]

    def run(self, run_id: str) -> EvidenceRun | None:
        if run_id not in self._runs:
            self._runs[run_id] = self._store.get_evidence_run(run_id)
        return self._runs[run_id]


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _normalise_label(value: str | None) -> str:
    text = (value or "").strip()
    text = re.sub(r"\s*/\s*step\.[^\s]+$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", text)


def _signal_kinds(text: str) -> frozenset[str]:
    return frozenset(
        kind for kind, terms in _SIGNAL_TERMS.items() if _contains_any(text, terms)
    )


def _meaningful_signal_kinds(target_name: str, expectation: str) -> frozenset[str]:
    """Return signals that can seed gameplay rather than merely describe its UI."""

    if target_name.startswith(_NAVIGATION_PREFIXES):
        return frozenset()
    combined = f"{target_name}；{expectation}"
    broad = _signal_kinds(combined)
    meaningful: set[str] = set()
    if "progression" in broad and _contains_any(
        target_name,
        ("升级", "升至", "强化", "突破", "成长", "研究", "建造", "共鸣", "提升"),
    ):
        meaningful.add("progression")
    if "unlock" in broad and _contains_any(target_name, ("解锁", "开启")):
        meaningful.add("unlock")
    if "loop" in broad and _contains_any(target_name, _SIGNAL_TERMS["loop"]):
        meaningful.add("loop")
    if "result" in broad and _contains_any(target_name, ("领取", "获得", "完成")):
        meaningful.add("result")
    if "configuration" in broad and _contains_any(
        target_name, _CONFIGURATION_ACTION_TERMS
    ):
        meaningful.add("configuration")
    if "combat" in broad and _contains_any(target_name, _COMBAT_ACTION_TERMS):
        meaningful.add("combat")
    if "resource" in broad and _contains_any(target_name, _RESOURCE_ACTION_TERMS):
        meaningful.add("resource")
    return frozenset(meaningful)


def _canonical_business_family(
    target_name: str,
    expectation: str,
    signal_kinds: frozenset[str],
) -> tuple[str, str, str] | None:
    """Map a concrete action into a review-sized gameplay/business family.

    A resource item or one named skill is evidence inside a gameplay boundary,
    not an independent gameplay.  Unknown signals stay in trace/task storage
    until a stronger family clue exists; conservative recall is preferable to
    flooding review with mechanically named pseudo-gameplay.
    """

    if not signal_kinds:
        return None
    text = f"{target_name}；{expectation}"
    if _contains_any(target_name, _RESOURCE_CONTAINER_TERMS):
        return None
    if "军演" in text:
        return "military-exercise", "军演", "combat"
    if "远征" in text:
        return "expedition", "远征", "combat"
    if _contains_any(target_name, ("编队", "换将", "阵型", "上阵", "下阵", "配将")):
        return "formation", "编队", "configuration"
    if _contains_any(target_name, ("土地", "攻占", "开垦", "屯田", "资源地")):
        return "land-operation", "土地经营", "loop"
    if _contains_any(target_name, _CITY_BUILDING_TERMS):
        return "city-development", "城建经营", "progression"
    if _contains_any(text, ("战法", "武将")):
        return "general-development", "武将培养", "progression"
    if _contains_any(text, ("英雄", "共鸣", "专武", "专属武器")):
        return "hero-development", "英雄养成", "progression"
    if _contains_any(text, ("寻访", "招募", "抽取", "卡池")):
        return "recruitment", "寻访招募", "loop"
    if _contains_any(text, ("同盟", "联盟", "帮会", "公会")):
        return "alliance", "同盟协作", "loop"
    if _contains_any(text, ("章节", "主线任务", "章节任务")):
        return "chapter-progression", "章节推进", "progression"
    if _contains_any(text, ("职业天赋", "职业技能", "转职")):
        return "profession", "职业成长", "progression"
    if _contains_any(text, ("装备", "属性点", "加点")):
        return "character-development", "角色养成", "progression"
    return None


def _strip_subject_noise(text: str) -> str:
    value = re.sub(r"第[0-9一二三四五六七八九十百]+章", "", text)
    value = re.sub(r"\d+级", "", value)
    value = re.sub(r"[（(].*?[）)]", "", value)
    changed = True
    while changed:
        changed = False
        for prefix in _LEADING_WORDS:
            if value.startswith(prefix):
                value = value[len(prefix) :]
                changed = True
        for suffix in _UI_SUFFIXES:
            if value.endswith(suffix):
                value = value[: -len(suffix)]
                changed = True
    value = value.strip("-—_：:，,。.;；/ ")
    return value[:48]


def _business_anchor(target_name: str, expectation: str) -> tuple[str, str, str] | None:
    """Return a stable subject/category anchor without turning navigation into gameplay."""

    kinds = _meaningful_signal_kinds(target_name, expectation)
    family = _canonical_business_family(target_name, expectation, kinds)
    if family is None:
        return None
    family_key, subject, category = family
    return f"{family_key}:{category}", subject, category


def _is_composite_skill_step_target(raw_target_name: str | None) -> bool:
    return bool(
        re.search(
            r"\s*/\s*step\.\d+\.action\s*$",
            (raw_target_name or "").strip(),
            flags=re.IGNORECASE,
        )
    )


def _evidence_step_ids(edge: TransitionEdgeV1) -> list[str]:
    return list(
        dict.fromkeys(
            step_id
            for reference in edge.evidence_refs
            for step_id in reference.evidence_step_ids
        )
    )


def _step_is_usable(
    edge: TransitionEdgeV1,
    step: EvidenceStep,
    run: EvidenceRun,
    *,
    environment_id: str,
) -> bool:
    return all(
        (
            edge.to_state_id is not None,
            step.status == "passed",
            bool(step.ended_at),
            bool(step.before_frame_id),
            bool(step.after_frame_id),
            not step.quality_issues,
            step.stability.settled,
            step.action == edge.action,
            step.target_bounds == edge.target_bounds,
            run.status == "passed",
            bool(run.ended_at),
            run.environment.get("environment_id") == environment_id,
            run.environment.get("task_id") is not None,
        )
    )


def _edge_evidence(
    store: AIPlayerStore,
    cache: _EvidenceCache,
    edge: TransitionEdgeV1,
    *,
    environment_id: str,
) -> _EdgeEvidence | None:
    for step_id in _evidence_step_ids(edge):
        step = cache.step(step_id)
        if step is None:
            continue
        run = cache.run(step.evidence_run_id)
        if run is None or not _step_is_usable(edge, step, run, environment_id=environment_id):
            continue
        task_id = run.environment.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        task = store.get_task(environment_id, task_id)
        if task is None:
            continue
        raw_target_name = step.target_name
        target = _normalise_label(raw_target_name)
        expectation = _normalise_label(
            str(run.environment.get("pre_execution_expectation") or edge.expected_change)
        )
        kinds = _signal_kinds(f"{target}；{expectation}")
        context_family = _canonical_business_family(target, expectation, kinds)
        anchor = (
            None
            if _is_composite_skill_step_target(raw_target_name)
            else _business_anchor(target, expectation)
        )
        return _EdgeEvidence(
            edge=edge,
            step=step,
            run=run,
            task=task,
            target_name=target,
            expectation=expectation,
            signal_kinds=kinds,
            anchor_key=anchor[0] if anchor else None,
            context_family_key=(
                f"{context_family[0]}:{context_family[2]}" if context_family else None
            ),
            subject=anchor[1] if anchor else None,
            category=anchor[2] if anchor else None,
        )
    return None


def _context_records(
    task_records: list[_EdgeEvidence],
    seeds: list[_EdgeEvidence],
    *,
    anchor_key: str,
    maximum_hops: int = 2,
) -> list[_EdgeEvidence]:
    """Grow through same-family support and one terminal navigation edge.

    Descriptive UI edges may connect a strong gameplay action to its entry
    route without becoming gameplay seeds. Unlabelled navigation is terminal,
    so traversal cannot leak through menus into neighbouring gameplay.
    """

    records_by_state: dict[str, list[_EdgeEvidence]] = defaultdict(list)
    for record in task_records:
        records_by_state[record.edge.from_state_id].append(record)
        if record.edge.to_state_id is not None:
            records_by_state[record.edge.to_state_id].append(record)
    selected: dict[str, _EdgeEvidence] = {record.edge.id: record for record in seeds}
    queue = deque((record, 0) for record in seeds)
    while queue:
        current, distance = queue.popleft()
        if distance >= maximum_hops:
            continue
        states = (current.edge.from_state_id, current.edge.to_state_id)
        for state_id in states:
            if state_id is None:
                continue
            for candidate in records_by_state[state_id]:
                if candidate.edge.id in selected:
                    continue
                if candidate.anchor_key is not None and candidate.anchor_key != anchor_key:
                    continue
                if (
                    candidate.anchor_key is None
                    and candidate.context_family_key not in (None, anchor_key)
                ):
                    continue
                belongs_to_family = (
                    candidate.anchor_key == anchor_key
                    or candidate.context_family_key == anchor_key
                )
                if not belongs_to_family and not candidate.navigation_only:
                    continue
                selected[candidate.edge.id] = candidate
                if belongs_to_family:
                    queue.append((candidate, distance + 1))
    return sorted(selected.values(), key=lambda item: (item.edge.created_at, item.edge.id))


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _boundary_states(
    records: list[_EdgeEvidence],
) -> tuple[list[str], list[str], list[str]]:
    incoming: defaultdict[str, int] = defaultdict(int)
    outgoing: defaultdict[str, int] = defaultdict(int)
    all_states: list[str] = []
    for record in records:
        source = record.edge.from_state_id
        target = record.edge.to_state_id
        all_states.append(source)
        outgoing[source] += 1
        if target is not None:
            all_states.append(target)
            incoming[target] += 1
    states = _ordered_unique(all_states)
    entries = [state for state in states if incoming[state] == 0]
    exits = [state for state in states if outgoing[state] == 0]
    if not entries:
        entries = [records[0].edge.from_state_id]
    if not exits:
        exits = [records[-1].edge.to_state_id or records[-1].edge.from_state_id]
    main = [state for state in states if state not in {*entries, *exits}]
    if not main:
        main = _ordered_unique(
            state
            for record in records
            if record.anchor_key is not None
            for state in (record.edge.from_state_id, record.edge.to_state_id or "")
        )
    return entries, main, exits


def _dedupe_evidence_refs(
    references: Iterable[EvidenceReferenceV1],
) -> list[EvidenceReferenceV1]:
    unique: dict[str, EvidenceReferenceV1] = {}
    for reference in references:
        key = reference.model_dump_json(by_alias=True, exclude_none=True)
        unique.setdefault(key, reference)
    return list(unique.values())


def _automatic_candidate_id(game_id: str, anchor_key: str) -> str:
    digest = hashlib.sha256(f"{game_id}\n{anchor_key}".encode("utf-8")).hexdigest()[:20]
    return f"gameplay.auto.{game_id}.{digest}"


def _candidate_from_records(
    *,
    environment_id: str,
    game_id: str,
    anchor_key: str,
    records: list[_EdgeEvidence],
    adjacent_labels: Iterable[str] = (),
) -> GameplayCandidateV1 | None:
    if len(records) < 2:
        return None
    seeds = [record for record in records if record.anchor_key == anchor_key]
    if not seeds or not any(record.signal_kinds for record in seeds):
        return None
    if all(record.navigation_only for record in records):
        return None
    entries, main, exits = _boundary_states(records)
    if not entries or not main or not exits:
        return None
    subject = next((record.subject for record in seeds if record.subject), "待审玩法")
    category = next((record.category for record in seeds if record.category), "progression")
    rule_clues = _ordered_unique(
        f"已记录操作“{record.target_name}”，其预期变化为“{record.expectation}”。"
        for record in seeds
    )
    progression_clues = _ordered_unique(
        f"{_CATEGORY_LABELS[kind]}线索：{record.target_name or record.expectation}。"
        for record in seeds
        for kind in _CATEGORY_LABELS
        if kind in record.signal_kinds
    )
    if not rule_clues or not progression_clues:
        return None
    task_ids = _ordered_unique(record.task.id for record in records)
    references = _dedupe_evidence_refs(
        reference
        for record in records
        for reference in (*record.task.evidence_refs, *record.edge.evidence_refs)
    )
    return GameplayCandidateV1(
        id=_automatic_candidate_id(game_id, anchor_key),
        environment_id=environment_id,
        evidence_refs=references,
        game_id=game_id,
        title=subject,
        status="candidate",
        triggering_task_ids=task_ids,
        entry_state_ids=entries,
        main_state_ids=main,
        transition_edge_ids=[record.edge.id for record in records],
        rule_clues=rule_clues,
        resource_or_progression_clues=progression_clues,
        exit_state_ids=exits,
        adjacent_gameplay_labels=_ordered_unique(adjacent_labels),
        boundary_summary=(
            f"待审边界以“{subject}”的{_CATEGORY_LABELS[category]}证据为核心，"
            f"保留 {len(entries)} 个入口状态、{len(records)} 条实机转移和 "
            f"{len(exits)} 个已观察终点；相邻纯导航仅用于说明进出关系。"
        ),
    )


def _candidate_fingerprint(candidate: GameplayCandidateV1) -> str:
    payload = candidate.model_dump(mode="json", by_alias=True)
    payload.pop("version", None)
    payload.pop("created_at", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _merge_candidate(
    existing: GameplayCandidateV1,
    proposed: GameplayCandidateV1,
) -> GameplayCandidateV1:
    payload = existing.model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "version": existing.version + 1,
            "evidence_refs": proposed.evidence_refs,
            "triggering_task_ids": proposed.triggering_task_ids,
            "entry_state_ids": proposed.entry_state_ids,
            "main_state_ids": proposed.main_state_ids,
            "transition_edge_ids": proposed.transition_edge_ids,
            "interface_family_ids": proposed.interface_family_ids,
            "rule_clues": proposed.rule_clues,
            "resource_or_progression_clues": proposed.resource_or_progression_clues,
            "exit_state_ids": proposed.exit_state_ids,
            "adjacent_gameplay_candidate_ids": proposed.adjacent_gameplay_candidate_ids,
            "adjacent_gameplay_labels": proposed.adjacent_gameplay_labels,
            "boundary_summary": proposed.boundary_summary,
        }
    )
    payload.pop("created_at", None)
    return GameplayCandidateV1.model_validate(payload)


def discover_gameplay_candidates(
    store: AIPlayerStore,
    environment_id: str,
    *,
    recent_edge_limit: int = 512,
    write_limit: int = 8,
) -> GameplayCandidateDiscoveryReport:
    """Discover and idempotently persist pending gameplay candidates.

    The scan is bounded and performs no model or device call.  Review-owned
    lifecycle states are sticky: automatic discovery never edits a candidate in
    ``scope_review``, ``closed`` or ``invalidated``.
    """

    if recent_edge_limit < 2:
        raise ValueError("gameplay discovery requires at least two recent edges")
    if write_limit < 1:
        raise ValueError("gameplay discovery write limit must be positive")
    environment = store.get_environment(environment_id)
    if environment is None:
        raise ValueError(f"unknown environment: {environment_id}")
    edges = store.list_recent_transition_edges(environment_id, limit=recent_edge_limit)
    cache = _EvidenceCache(store)
    records: list[_EdgeEvidence] = []
    invalid_count = 0
    with store.observatory_store.read_session():
        for edge in edges:
            record = _edge_evidence(store, cache, edge, environment_id=environment_id)
            if record is None:
                invalid_count += 1
                continue
            records.append(record)

    records_by_task: dict[str, list[_EdgeEvidence]] = defaultdict(list)
    for record in records:
        records_by_task[record.task.id].append(record)
    records_by_anchor: dict[str, list[_EdgeEvidence]] = defaultdict(list)
    adjacent_labels_by_anchor: dict[str, set[str]] = defaultdict(set)
    for task_records in records_by_task.values():
        seeds_by_anchor: dict[str, list[_EdgeEvidence]] = defaultdict(list)
        for record in task_records:
            if record.anchor_key is not None:
                seeds_by_anchor[record.anchor_key].append(record)
        for anchor_key, seeds in seeds_by_anchor.items():
            records_by_anchor[anchor_key].extend(
                _context_records(task_records, seeds, anchor_key=anchor_key)
            )
        anchored = [record for record in task_records if record.anchor_key is not None]
        for record in anchored:
            record_states = {record.edge.from_state_id, record.edge.to_state_id}
            for adjacent in anchored:
                if adjacent.anchor_key == record.anchor_key:
                    continue
                adjacent_states = {adjacent.edge.from_state_id, adjacent.edge.to_state_id}
                if record_states.intersection(adjacent_states) and adjacent.subject:
                    adjacent_labels_by_anchor[record.anchor_key].add(adjacent.subject)

    proposals: list[GameplayCandidateV1] = []
    rejected_incomplete = 0
    for anchor_key, anchor_records in sorted(records_by_anchor.items()):
        unique_records = {
            (record.edge.id, record.edge.version): record for record in anchor_records
        }
        proposal = _candidate_from_records(
            environment_id=environment_id,
            game_id=environment.game_id,
            anchor_key=anchor_key,
            records=sorted(
                unique_records.values(), key=lambda item: (item.edge.created_at, item.edge.id)
            ),
            adjacent_labels=sorted(adjacent_labels_by_anchor.get(anchor_key, set())),
        )
        if proposal is None:
            rejected_incomplete += 1
            continue
        proposals.append(proposal)

    written: list[str] = []
    unchanged: list[str] = []
    review_locked: list[str] = []
    for proposal in proposals:
        existing = store.get_gameplay_candidate(environment_id, proposal.id)
        if existing is not None and existing.status != "candidate":
            review_locked.append(existing.id)
            continue
        candidate = proposal if existing is None else _merge_candidate(existing, proposal)
        if existing is not None and _candidate_fingerprint(existing) == _candidate_fingerprint(
            candidate
        ):
            unchanged.append(existing.id)
            continue
        if len(written) >= write_limit:
            break
        store.append_gameplay_candidate(candidate)
        written.append(candidate.id)

    return GameplayCandidateDiscoveryReport(
        scanned_edge_count=len(edges),
        eligible_anchor_count=len(records_by_anchor),
        candidate_version_ids=tuple(written),
        unchanged_candidate_ids=tuple(unchanged),
        review_locked_candidate_ids=tuple(review_locked),
        rejected_navigation_edge_count=sum(record.navigation_only for record in records),
        rejected_incomplete_anchor_count=rejected_incomplete,
        invalid_evidence_edge_count=invalid_count,
    )


__all__ = [
    "GameplayCandidateDiscoveryReport",
    "discover_gameplay_candidates",
]

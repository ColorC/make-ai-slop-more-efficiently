"""Deterministic routing over already learned game-operation skills."""

from __future__ import annotations

import heapq
import re
import statistics
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .contracts import SkillRunV1, SkillVersionV1
from .store import AIPlayerStore, KnownRouteSkillRunSummary


_SAFETY_ORDER = {
    "read_only": 0,
    "reversible": 1,
    "progression": 2,
    "social": 3,
    "economic": 4,
    "restricted": 5,
}

_ENTRY_BRIDGE_MAX_VISUAL_DISTANCE = 0.012


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class KnownSkillArcV1(_FrozenModel):
    schema_id: Literal["game-observatory.ai-player.known-skill-arc.v1"] = Field(
        default="game-observatory.ai-player.known-skill-arc.v1",
        alias="schema",
    )
    skill_version_id: str
    skill_title: str
    from_state_id: str
    to_state_id: str
    action_count: int = Field(ge=1)
    successful_run_count: int = Field(ge=0)
    median_decision_latency_ms: float = Field(ge=0)
    median_baseline_decision_latency_ms: float = Field(ge=0)
    median_baseline_model_input_tokens: int = Field(ge=0)
    lifecycle_status: str
    safety_level: str
    entry_bridge_proof_skill_version_id: str | None = None
    entry_bridge_proof_required_state_id: str | None = None


class KnownSkillRouteV1(_FrozenModel):
    schema_id: Literal["game-observatory.ai-player.known-skill-route.v1"] = Field(
        default="game-observatory.ai-player.known-skill-route.v1",
        alias="schema",
    )
    environment_id: str
    observed_start_state_id: str
    selected_entry_state_id: str
    goal_query: str
    goal_state_id: str
    skill_version_ids: tuple[str, ...]
    state_ids: tuple[str, ...]
    total_cost: float = Field(ge=0)
    learned_only: Literal[True] = True

    @computed_field(return_type=int)
    @property
    def action_count(self) -> int:
        return len(self.skill_version_ids)


def _successful_runs(
    runs: Sequence[SkillRunV1 | KnownRouteSkillRunSummary],
) -> list[SkillRunV1 | KnownRouteSkillRunSummary]:
    # A fixed path must reflect the latest decisive evidence, not merely retain
    # any success it has ever seen.  A terminal mismatch, false success, or
    # safety violation quarantines earlier successes until a later guarded run
    # explicitly proves the same version usable again.  Ordinary unmet
    # preconditions and recovered interruptions describe caller state rather
    # than a broken operation and therefore do not erase the warm path.
    latest_decisive_failure = max(
        (
            index
            for index, run in enumerate(runs)
            if run.false_success
            or run.safety_violation_count > 0
            or run.outcome == "false_success"
            or (
                run.outcome == "failed"
                and not getattr(run, "recovery_succeeded", False)
            )
        ),
        default=-1,
    )
    return [
        run
        for run in runs[latest_decisive_failure + 1 :]
        if run.outcome == "success"
        and run.objective_success
        and run.validation_passed
        and not run.false_success
        and run.safety_violation_count == 0
        and getattr(run, "semantic_sedimentation_settled", True)
    ]


def _terminal_state_id(skill: SkillVersionV1) -> str | None:
    terminal = [
        step.expected_state_id
        for step in skill.steps
        if step.kind == "assert" and step.expected_state_id is not None
    ]
    return terminal[-1] if terminal else None


def _goal_haystack(skill: SkillVersionV1) -> str:
    return "\n".join(
        [
            skill.id,
            skill.skill_id,
            skill.title,
            skill.applicability,
            *skill.preconditions,
            *skill.procedure_steps,
            *skill.success_checks,
        ]
    ).casefold()


def _normalized_goal_text(value: str) -> str:
    return re.sub(r"[\s\-_/·路径→:：,，.。;；、()（）]+", "", value).casefold()


_STRUCTURED_GOAL_COORDINATE_PATTERN = re.compile(
    r"(?<!\d)(\d+)\s*[-–—_/]\s*(\d+)(?!\d)"
)
_STRUCTURED_GOAL_LABELED_NUMBER_PATTERN = re.compile(
    r"第?\s*(\d+)\s*(章节|章|关卡|关|节|阶段|层|级)"
)
_STRUCTURED_GOAL_LABEL_AXIS = {
    "章节": "chapter",
    "章": "chapter",
    "关卡": "stage",
    "关": "stage",
    "节": "stage",
    "阶段": "stage",
    "层": "floor",
    "级": "level",
}


def _structured_goal_identifiers(value: str) -> tuple[set[tuple[int, int]], dict[str, set[int]]]:
    """Extract explicit stage coordinates without flattening their separators."""

    coordinates = {
        (int(first), int(second))
        for first, second in _STRUCTURED_GOAL_COORDINATE_PATTERN.findall(value)
    }
    labeled: dict[str, set[int]] = defaultdict(set)
    for number, label in _STRUCTURED_GOAL_LABELED_NUMBER_PATTERN.findall(value):
        labeled[_STRUCTURED_GOAL_LABEL_AXIS[label]].add(int(number))
    chapters = labeled.get("chapter", set())
    stages = labeled.get("stage", set())
    if len(chapters) == 1 and len(stages) == 1:
        coordinates.add((next(iter(chapters)), next(iter(stages))))
    return coordinates, dict(labeled)


def _structured_goal_identifiers_conflict(title: str, needle: str) -> bool:
    """Fail closed when both goal phrases explicitly name different numbered content."""

    title_coordinates, title_labeled = _structured_goal_identifiers(title)
    needle_coordinates, needle_labeled = _structured_goal_identifiers(needle)
    if (
        title_coordinates
        and needle_coordinates
        and title_coordinates.isdisjoint(needle_coordinates)
    ):
        return True
    for axis in title_labeled.keys() & needle_labeled.keys():
        if title_labeled[axis].isdisjoint(needle_labeled[axis]):
            return True
    return False


def _exact_goal_alias_key(value: str) -> str:
    """Normalize prose while preserving the identity of numbered coordinates."""

    with_coordinate_markers = _STRUCTURED_GOAL_COORDINATE_PATTERN.sub(
        lambda match: (
            f"\u241fcoordinate{int(match.group(1))}stage{int(match.group(2))}\u241f"
        ),
        value,
    )
    return _normalized_goal_text(with_coordinate_markers)


def _goal_bigrams(value: str) -> set[str]:
    normalized = _normalized_goal_text(value)
    return {
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
    }


_NAMED_SURFACE_TERMS = (
    "详情",
    "入口",
    "面板",
    "页面",
    "界面",
    "弹窗",
    "条目",
    "卡片",
)

_GENERIC_SURFACE_OBJECT_TERMS = (
    "战法",
    "技能",
    "属性",
    "说明",
    "按钮",
)

_GENERIC_SURFACE_SUBJECTS = {
    "",
    "当前",
    "此处",
    "这里",
    "其他",
    "下一个",
    "下一位",
}


def _named_surface_subject(value: str) -> str | None:
    """Return the named object that distinguishes otherwise identical screens.

    A route to ``揭竿而起战法详情`` and one to ``妖武战法详情`` may share most
    of their UI nouns while still being different controls and terminal content.
    Keep broad matching for operation goals, but make named content surfaces
    carry their object identity into deterministic route selection.
    """

    normalized = _normalized_goal_text(value)
    if not any(term in normalized for term in _NAMED_SURFACE_TERMS):
        return None
    for term in (
        "查看",
        "打开",
        "进入",
        "前往",
        "关闭",
        "返回",
        "退出",
        "收起",
        "离开",
        *_NAMED_SURFACE_TERMS,
        *_GENERIC_SURFACE_OBJECT_TERMS,
    ):
        normalized = normalized.replace(term, "")
    return normalized


def _named_surface_subjects_compatible(title: str, needle: str) -> bool:
    title_subject = _named_surface_subject(title)
    needle_subject = _named_surface_subject(needle)
    if title_subject is None or needle_subject is None:
        return True
    if needle_subject in _GENERIC_SURFACE_SUBJECTS:
        return True
    if title_subject in _GENERIC_SURFACE_SUBJECTS:
        return False
    if title_subject == needle_subject:
        return True
    if (
        title_subject in needle_subject or needle_subject in title_subject
    ) and abs(len(title_subject) - len(needle_subject)) <= 2:
        # A route may omit a two-character parent name (``妖武`` versus
        # ``张梁妖武``), but a broad parent page (``韩当``) must not satisfy a
        # deeper named child (``韩当精擅善射``).
        return True
    title_bigrams = _goal_bigrams(title_subject)
    needle_bigrams = _goal_bigrams(needle_subject)
    shared = title_bigrams.intersection(needle_bigrams)
    larger = max(len(title_bigrams), len(needle_bigrams))
    return larger > 0 and len(shared) / larger >= 0.60


def _exit_surface_subject(value: str) -> str | None:
    """Keep the named caller for exit controls even without a surface suffix."""

    normalized = _normalized_goal_text(value)
    if not any(term in normalized for term in ("关闭", "返回", "退出", "收起", "离开")):
        return None
    for term in (
        "关闭",
        "返回",
        "退出",
        "收起",
        "离开",
        *_NAMED_SURFACE_TERMS,
    ):
        normalized = normalized.replace(term, "")
    return normalized


def _exit_surface_subjects_compatible(title: str, needle: str) -> bool:
    """Reject a same-shaped exit learned for a different named subsystem."""

    title_subject = _exit_surface_subject(title)
    needle_subject = _exit_surface_subject(needle)
    if title_subject is None or needle_subject is None:
        return True
    if needle_subject in _GENERIC_SURFACE_SUBJECTS:
        return True
    if title_subject in _GENERIC_SURFACE_SUBJECTS:
        return False
    if title_subject == needle_subject:
        return True
    if title_subject in needle_subject or needle_subject in title_subject:
        return True
    title_bigrams = _goal_bigrams(title_subject)
    needle_bigrams = _goal_bigrams(needle_subject)
    shared = title_bigrams.intersection(needle_bigrams)
    larger = max(len(title_bigrams), len(needle_bigrams))
    # Exit labels often add one parent or operation phrase (for example
    # ``林场开垦出征选择`` versus ``3级林场开垦选择``).  A modest shared-object
    # threshold keeps that useful alias while separating neighboring systems
    # that only share a generic control noun (``军演部队`` versus ``远征部队``).
    return larger > 0 and len(shared) / larger >= 0.30


def _goal_operation_family(value: str) -> str | None:
    normalized = _normalized_goal_text(value)
    # “启动升级/建造” asks for the state-changing click, not merely the
    # detail or requirement surface that precedes it.  Keep this explicit so
    # a learned "打开升级条件" flow can never satisfy a commit request.
    if normalized.startswith("启动"):
        return "commit"
    if any(term in normalized for term in ("关闭", "返回", "退出", "收起", "离开")):
        return "exit"
    # “发起攻占” names the progression action after an already visible
    # confirmation surface. Keep it distinct from merely reaching “出征确认”;
    # otherwise the planner can report already_at_goal on the intermediate
    # panel and hand the final click back to the semantic model.
    if normalized.startswith("确认") or any(
        term in normalized for term in ("发起", "开始", "提交", "执行", "派遣")
    ):
        return "commit"
    # “详情” names a surface.  Treating it as an enter verb would discard a
    # verified “返回详情” path when the caller only asks to be at that surface.
    if any(term in normalized for term in ("查看", "打开", "进入", "前往")):
        return "enter"
    return None


_REQUIREMENTS_SURFACE_TERMS = (
    "升级条件",
    "升级所需",
    "所需资源",
    "资源需求",
    "前置条件",
    "前置建筑",
)


def _goal_surface_family(value: str) -> str | None:
    normalized = _normalized_goal_text(value)
    if any(term in normalized for term in _REQUIREMENTS_SURFACE_TERMS):
        return "requirements"
    return None


def _requirements_surface_subject(value: str) -> str | None:
    normalized = _normalized_goal_text(value)
    for prefix in ("查看", "打开", "进入", "前往"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    matches = [
        (normalized.find(term), term)
        for term in _REQUIREMENTS_SURFACE_TERMS
        if term in normalized
    ]
    if not matches:
        return None
    index, _term = min(matches)
    return normalized[:index]


def _skill_proves_surface(
    skill: SkillVersionV1,
    surface_family: str | None,
    needle: str,
) -> bool:
    if surface_family is None:
        return True
    checks = [_normalized_goal_text(check) for check in skill.success_checks]
    if surface_family == "requirements":
        has_terminal_proof = any(
            term in check
            for check in checks
            for term in _REQUIREMENTS_SURFACE_TERMS
        )
        subject = _requirements_surface_subject(needle)
        surface_text = _normalized_goal_text(skill.title) + "".join(checks)
        return has_terminal_proof and (not subject or subject in surface_text)
    return False


_LEAVING_TARGET_PREFIXES = ("关闭", "退出", "离开", "收起")
_LEAVING_TARGET_SUFFIXES = ("关闭", "退出", "离开", "收起")


def _terminal_goal_match_score(skill: SkillVersionV1, needle: str) -> int:
    """Score the interface proven by terminal checks above the clicked control."""

    normalized_needle = _normalized_goal_text(needle)
    if not normalized_needle or _goal_operation_family(normalized_needle) is not None:
        return 0
    for check in skill.success_checks:
        normalized_check = _normalized_goal_text(check)
        start = 0
        while True:
            index = normalized_check.find(normalized_needle, start)
            if index < 0:
                break
            prefix = normalized_check[:index]
            suffix = normalized_check[index + len(normalized_needle) :]
            leaves_named_surface = any(
                prefix.endswith(term) for term in _LEAVING_TARGET_PREFIXES
            ) or any(suffix.startswith(term) for term in _LEAVING_TARGET_SUFFIXES)
            if not leaves_named_surface:
                # Very short Chinese surface names such as ``武将`` or ``主城``
                # occur inside many broader terminal descriptions. Keep the
                # terminal evidence usable when no exact control exists, but
                # let an exact learned entrance title outrank that ambiguous
                # substring instead of merging both terminal states.
                return 90 if len(normalized_needle) <= 2 else 120
            start = index + len(normalized_needle)
    return 0


def _goal_match_score(skill: SkillVersionV1, needle: str) -> int:
    raw_title = skill.title
    raw_needle = needle
    if _structured_goal_identifiers_conflict(raw_title, raw_needle):
        return 0
    title = _normalized_goal_text(raw_title)
    needle = _normalized_goal_text(raw_needle)
    needle_family = _goal_operation_family(needle)
    title_family = _goal_operation_family(title)
    if not _skill_proves_surface(skill, _goal_surface_family(needle), needle):
        return 0
    if needle_family == "exit" and title_family != "exit":
        return 0
    if needle_family == "enter" and title_family == "exit":
        return 0
    if needle_family == "commit" and title_family != "commit":
        return 0
    if (
        needle_family == "exit"
        and _named_surface_subject(needle) in _GENERIC_SURFACE_SUBJECTS
        and _named_surface_subject(title) is not None
    ):
        # ``关闭当前战法详情`` deliberately leaves the object name open. The
        # current semantic state still guards which specific close control is
        # executable, so this generic request may select that caller-scoped
        # exit without conflating named forward entrances.
        return 55
    if needle_family != "exit" and not _named_surface_subjects_compatible(title, needle):
        return 0
    if title == needle:
        return 100
    terminal_score = _terminal_goal_match_score(skill, needle)
    if terminal_score:
        return terminal_score
    if needle_family == "exit" and not _exit_surface_subjects_compatible(
        raw_title,
        raw_needle,
    ):
        return 0
    # A plain surface query names the interface we want to occupy.  An exit
    # skill title commonly embeds the interface being left (for example,
    # ``孙策列传左上返回``), so its terminal state must never be inferred as that
    # surface from the title or generic haystack alone.  A real child -> parent
    # route still matches through an explicit terminal success check above.
    if needle_family is None and title_family == "exit":
        return 0
    if title.startswith(needle) or title.endswith(needle):
        return 80
    if needle in title:
        return 60
    # Chinese goal phrases often reorder the same operation and object, such as
    # “空地攻占出征” and “出征攻占2级石料”.  Two shared adjacent terms with
    # sufficient coverage provide an order-tolerant deterministic match while
    # still keeping nearby controls such as “武将” and “编队换将” separate.
    title_bigrams = _goal_bigrams(title)
    needle_bigrams = _goal_bigrams(needle)
    shared = title_bigrams.intersection(needle_bigrams)
    smaller = min(len(title_bigrams), len(needle_bigrams))
    if len(shared) >= 2 and smaller > 0 and len(shared) / smaller >= 0.40:
        return 50
    return 40 if needle in _goal_haystack(skill) else 0


def _matching_goal_skill_ids(
    skills: Sequence[SkillVersionV1],
    needle: str,
) -> set[str]:
    """Resolve all learned entrances to a surface, not only one action title."""

    scores = {skill.id: _goal_match_score(skill, needle) for skill in skills}
    best_score = max(scores.values(), default=0)
    if best_score <= 0:
        return set()
    normalized_needle = _normalized_goal_text(needle)
    has_exact_title = any(
        _normalized_goal_text(skill.title) == normalized_needle for skill in skills
    )
    operation_family = _goal_operation_family(needle)
    if has_exact_title:
        # An exact public skill title is already the strongest deterministic
        # selector.  Expanding it to loosely related exit skills can turn a
        # one-step unavailable return into a multi-screen cycle.
        threshold = 100
    elif operation_family == "exit":
        # Exit phrases are often recorded at two granularities: an atomic
        # caller-specific return (``孙策列传返回武将管理``) and a longer learned
        # flow whose summary repeats the caller's broad wording (``关闭并返回上级``).
        # Keeping only the highest text score can therefore discard the direct
        # one-action edge and force a detour through child screens.  Admit every
        # semantically overlapping exit candidate, then let graph cost and the
        # current caller state select the shortest executable return.
        threshold = min(best_score, 50)
    elif operation_family is not None or best_score < 100:
        threshold = best_score
    else:
        # A plain surface goal can have several valid entrances: open it from a
        # list, return to it from a child page, or select its tab.  Terminal-check
        # matches score 120; an exact control title scores 100 and remains valid.
        threshold = 100
    return {skill_id for skill_id, score in scores.items() if score >= threshold}


def _goal_scoped_arcs(
    arcs: Sequence[KnownSkillArcV1],
    skills: dict[str, SkillVersionV1],
    *,
    matching_skill_ids: set[str],
    goal_state_ids: set[str],
    exact_observed_start_state_id: str | None = None,
) -> list[KnownSkillArcV1]:
    """Keep caller-dependent exits out of unrelated intermediate routes.

    Back/close/exit controls encode their caller through the navigation stack.
    A persisted edge can therefore reach its verified endpoint when that edge
    itself is the requested operation (or its endpoint is the exact requested
    state).  A business route may also leave the exact observed caller as its
    first leg; it is not a universal bridge between arbitrary later screens.
    """

    bridged_entry_states = {
        arc.from_state_id
        for arc in arcs
        if arc.entry_bridge_proof_skill_version_id is not None
    }
    return [
        arc
        for arc in arcs
        if (
            _goal_operation_family(getattr(skills.get(arc.skill_version_id), "title", ""))
            != "exit"
            or arc.skill_version_id in matching_skill_ids
            or arc.to_state_id in goal_state_ids
            # A caller-scoped exit may be the first leg of a business route only
            # when its exact endpoint has a separately proven one-hop entry bridge.
            # This keeps ordinary Back edges out of arbitrary shortcut paths.
            or arc.to_state_id in bridged_entry_states
            # The exact observed caller is already a stronger source guard than
            # a remembered visual alias.  Admit its exit so the route search can
            # compose one caller-leave step with the requested business entry;
            # the search loop below still restricts it to the first hop.
            or (
                exact_observed_start_state_id is not None
                and arc.from_state_id == exact_observed_start_state_id
                and arc.from_state_id
                in getattr(
                    getattr(
                        skills.get(arc.skill_version_id),
                        "applicability_scope",
                        None,
                    ),
                    "required_state_ids",
                    (),
                )
            )
        )
    ]


_DYNAMIC_WORLD_OBJECT_PATTERN = re.compile(
    r"(?:城外|场景内).{0,18}"
    r"(?:地块|土地|资源地|空地|粮食地|石料地|铁矿地|林场|农田)"
)


def _requires_dynamic_world_object_locator(skill: SkillVersionV1) -> bool:
    """Reject raw coordinates for movable world-map content before device access.

    Fixed chrome, panel controls, and tabs remain eligible for source-pixel replay.
    The narrow compatibility rule covers legacy skills that predate an explicit
    locator scope: only a world/map context followed by a land/resource object is
    classified as movable.  Once crystallization emits a real ``template`` or
    adapter locator, that step no longer needs this quarantine.
    """

    locators = list(getattr(skill, "locators", ()) or ())
    if any(
        getattr(locator, "mobility", None) == "dynamic_world_object"
        and getattr(locator, "strategy", None) != "template"
        for locator in locators
    ):
        return True
    has_legacy_raw_pointer = any(
        getattr(locator, "strategy", None) == "source_pixel"
        and getattr(locator, "reference_bounds", None) is not None
        and getattr(locator, "mobility", None) is None
        for locator in locators
    )
    if not has_legacy_raw_pointer:
        return False
    description = _normalized_goal_text(
        "\n".join(
            [
                str(getattr(skill, "title", "")),
                str(getattr(skill, "applicability", "")),
                *(str(item) for item in getattr(skill, "procedure_steps", ()) or ()),
            ]
        )
    )
    return _DYNAMIC_WORLD_OBJECT_PATTERN.search(description) is not None


class KnownRouteProgram:
    """Plan low-cost non-model routes from persisted successful skills."""

    def __init__(self, store: AIPlayerStore) -> None:
        self.store = store
        self._arc_cache: dict[tuple[object, ...], tuple[KnownSkillArcV1, ...]] = {}
        self._skill_cache: dict[tuple[str, str, bool, object], tuple[SkillVersionV1, ...]] = {}
        self._alias_memory_cache: dict[tuple[str, object], tuple[object, ...]] = {}
        self._goal_alias_cache: dict[
            tuple[str, object],
            dict[str, tuple[tuple[str, str], ...]],
        ] = {}
        self._entry_bridge_group_cache: dict[
            tuple[object, ...],
            tuple[tuple[str, str, tuple[str, ...]], ...],
        ] = {}

    def _revision(self, environment_id: str) -> object:
        revision = getattr(self.store, "known_route_revision", None)
        return revision(environment_id) if callable(revision) else None

    def _route_skills(
        self,
        environment_id: str,
        *,
        max_safety: str,
        require_successful_run: bool,
        revision: object | None = None,
    ) -> tuple[SkillVersionV1, ...]:
        revision = self._revision(environment_id) if revision is None else revision
        cache_key = (environment_id, max_safety, require_successful_run, revision)
        cached = self._skill_cache.get(cache_key)
        if cached is not None:
            return cached
        route_loader = getattr(self.store, "list_known_route_skill_versions", None)
        if callable(route_loader):
            skills = route_loader(
                environment_id,
                max_safety=max_safety,
                require_successful_run=require_successful_run,
            )
        else:
            # Lightweight fixture stores and older external adapters retain the
            # original in-memory contract.  Eligibility is still enforced in
            # ``arcs`` below, so this compatibility path is semantically exact.
            skills = self.store.list_skill_versions(environment_id)
        result = tuple(skills)
        self._skill_cache[cache_key] = result
        return result

    def _route_alias_memories(
        self,
        environment_id: str,
        *,
        revision: object | None = None,
    ) -> tuple[object, ...]:
        revision = self._revision(environment_id) if revision is None else revision
        cache_key = (environment_id, revision)
        if revision is not None:
            cached = self._alias_memory_cache.get(cache_key)
            if cached is not None:
                return cached
        route_loader = getattr(self.store, "list_known_route_alias_memories", None)
        if callable(route_loader):
            memories = route_loader(environment_id)
        else:
            memories = self.store.list_memories(environment_id)
        result = tuple(memories)
        if revision is not None:
            self._alias_memory_cache[cache_key] = result
        return result

    def _goal_alias_index(
        self,
        environment_id: str,
        *,
        revision: object | None = None,
    ) -> dict[str, tuple[tuple[str, str], ...]]:
        """Load exact, run-proven semantic names for fixed skill endpoints."""

        revision = self._revision(environment_id) if revision is None else revision
        cache_key = (environment_id, revision)
        if revision is not None:
            cached = self._goal_alias_cache.get(cache_key)
            if cached is not None:
                return cached
        memories = [
            memory
            for memory in self._route_alias_memories(environment_id, revision=revision)
            if getattr(memory, "status", None) == "active"
            and getattr(memory, "kind", None) == "procedural"
            and getattr(memory, "payload", {}).get("schema")
            == "game-observatory.ai-player.known-skill-goal-alias.v1"
        ]
        skill_ids = {
            str(memory.payload["skill_version_id"])
            for memory in memories
            if isinstance(memory.payload.get("skill_version_id"), str)
        }
        run_loader = getattr(self.store, "list_known_route_run_summaries", None)
        if callable(run_loader):
            runs = run_loader(environment_id, sorted(skill_ids)) if skill_ids else []
        else:
            runs = self.store.list_skill_runs(environment_id)
        runs_by_skill: dict[str, list[SkillRunV1 | KnownRouteSkillRunSummary]] = defaultdict(list)
        for run in runs:
            if run.skill_version_id in skill_ids:
                runs_by_skill[run.skill_version_id].append(run)
        successful_run_skill_ids = {
            str(run_id): skill_id
            for skill_id, skill_runs in runs_by_skill.items()
            for run in _successful_runs(skill_runs)
            if (run_id := getattr(run, "run_id", getattr(run, "id", None))) is not None
        }
        aliases: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for memory in memories:
            payload = memory.payload
            skill_version_id = payload.get("skill_version_id")
            goal_alias = payload.get("goal_alias")
            successful_run_id = payload.get("successful_run_id")
            if (
                not isinstance(skill_version_id, str)
                or not isinstance(goal_alias, str)
                or not isinstance(successful_run_id, str)
                or payload.get("requires_settled_run") is not True
                or successful_run_skill_ids.get(successful_run_id) != skill_version_id
            ):
                continue
            normalized = _exact_goal_alias_key(goal_alias)
            if normalized:
                aliases[normalized].add((skill_version_id, goal_alias))
        result = {
            normalized: tuple(sorted(entries))
            for normalized, entries in aliases.items()
        }
        if revision is not None:
            self._goal_alias_cache[cache_key] = result
        return result

    def remembered_skill_goal_aliases(
        self,
        environment_id: str,
        skill_version_id: str,
    ) -> set[str]:
        """Return exact semantic goal phrases proven by settled successful runs."""

        return {
            goal_alias
            for entries in self._goal_alias_index(environment_id).values()
            for candidate_skill_id, goal_alias in entries
            if candidate_skill_id == skill_version_id
        }

    def _matching_goal_skill_ids(
        self,
        environment_id: str,
        skills: Sequence[SkillVersionV1],
        needle: str,
    ) -> set[str]:
        normalized = _exact_goal_alias_key(needle)
        eligible_skill_ids = {skill.id for skill in skills}
        alias_matches = {
            skill_version_id
            for skill_version_id, raw_alias in self._goal_alias_index(environment_id).get(
                normalized,
                (),
            )
            if skill_version_id in eligible_skill_ids
            and not _structured_goal_identifiers_conflict(raw_alias, needle)
        }
        if alias_matches:
            # A persisted phrase is an exact endpoint name, so it outranks broad
            # title similarity and never expands into a child/detail target.
            return alias_matches
        return _matching_goal_skill_ids(skills, needle)

    def arcs(
        self,
        environment_id: str,
        *,
        max_safety: str = "economic",
        require_successful_run: bool = False,
        preloaded_skills: Sequence[SkillVersionV1] | None = None,
    ) -> list[KnownSkillArcV1]:
        revision = self._revision(environment_id)
        preloaded = tuple(preloaded_skills) if preloaded_skills is not None else None
        preloaded_ids = tuple(skill.id for skill in preloaded) if preloaded is not None else None
        if preloaded is not None:
            if len(preloaded_ids) != len(set(preloaded_ids)):
                raise ValueError("preloaded route skills contain a duplicate id")
            if any(
                getattr(skill, "environment_id", None) != environment_id
                for skill in preloaded
            ):
                raise ValueError("a preloaded route skill is outside the requested environment")
            latest_guard = getattr(self.store, "skill_versions_are_current_latest", None)
            if callable(latest_guard) and not latest_guard(environment_id, preloaded):
                raise ValueError("a preloaded route skill is not its current latest version")
        base_cache_key = (
            environment_id,
            max_safety,
            require_successful_run,
            revision,
        )
        cache_key = (
            *base_cache_key,
            preloaded_ids,
        )
        cached = self._arc_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        maximum = _SAFETY_ORDER[max_safety]
        arcs: list[KnownSkillArcV1] = []
        skills = (
            preloaded
            if preloaded is not None
            else self._route_skills(
                environment_id,
                max_safety=max_safety,
                require_successful_run=require_successful_run,
                revision=revision,
            )
        )
        runs_by_skill: dict[
            str, list[SkillRunV1 | KnownRouteSkillRunSummary]
        ] = defaultdict(list)
        run_loader = getattr(self.store, "list_known_route_run_summaries", None)
        if callable(run_loader):
            route_runs = run_loader(environment_id, [skill.id for skill in skills])
        else:
            route_runs = self.store.list_skill_runs(environment_id)
        for run in route_runs:
            runs_by_skill[run.skill_version_id].append(run)
        alias_memories = self._route_alias_memories(environment_id, revision=revision)
        successful_run_skill_ids = {
            str(run_id): str(run.skill_version_id)
            for runs in runs_by_skill.values()
            for run in _successful_runs(runs)
            if (run_id := getattr(run, "run_id", getattr(run, "id", None))) is not None
        }
        entry_aliases: dict[tuple[str, str], set[str]] = defaultdict(set)
        for memory in alias_memories:
            payload = memory.payload
            if (
                memory.status == "active"
                and memory.kind == "procedural"
                and payload.get("schema")
                == "game-observatory.ai-player.known-skill-entry-alias.v1"
                and isinstance(payload.get("skill_version_id"), str)
                and isinstance(payload.get("observed_state_id"), str)
                and isinstance(payload.get("required_state_id"), str)
                and self._direct_entry_alias_is_usable(
                    payload,
                    successful_run_skill_ids=successful_run_skill_ids,
                )
            ):
                entry_aliases[
                    (
                        str(payload["skill_version_id"]),
                        str(payload["required_state_id"]),
                    )
                ].add(str(payload["observed_state_id"]))
        eligible_skill_ids = {
            skill.id
            for skill in skills
            if (
                skill.status not in {"degraded", "invalidated"}
                and skill.executor_kind == "normalized_actions"
                and _SAFETY_ORDER[skill.safety_level] <= maximum
                and not _requires_dynamic_world_object_locator(skill)
                and (
                    getattr(skill, "level", "L2") != "L3"
                    or skill.status in {"validated", "preferred"}
                )
            )
        }
        bridge_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
        for memory in alias_memories:
            payload = memory.payload
            if not self._entry_alias_is_bridge_proof(
                memory,
                eligible_skill_ids=eligible_skill_ids,
                successful_run_skill_ids=successful_run_skill_ids,
            ):
                continue
            bridge_groups[
                (
                    str(payload["skill_version_id"]),
                    str(payload["required_state_id"]),
                )
            ].add(str(payload["observed_state_id"]))
        proven_bridge_groups = tuple(
            (skill_version_id, required_state_id, tuple(sorted(observed_state_ids)))
            for (skill_version_id, required_state_id), observed_state_ids in sorted(
                bridge_groups.items()
            )
            if len(observed_state_ids) >= 2
        )
        if preloaded is None:
            self._entry_bridge_group_cache[base_cache_key] = proven_bridge_groups
        for skill in skills:
            if (
                skill.status in {"degraded", "invalidated"}
                or skill.executor_kind != "normalized_actions"
                or _SAFETY_ORDER[skill.safety_level] > maximum
                or _requires_dynamic_world_object_locator(skill)
                # A candidate L3 flow is only a historical multi-action proposal.
                # The atomic graph can compose the same route on demand and keeps
                # every intermediate guard visible.  Admit a stored L3 shortcut
                # only after it has completed the explicit lifecycle promotion.
                or (
                    getattr(skill, "level", "L2") == "L3"
                    and skill.status not in {"validated", "preferred"}
                )
            ):
                continue
            terminal_state_id = _terminal_state_id(skill)
            action_count = sum(step.kind == "action" for step in skill.steps)
            if terminal_state_id is None or action_count == 0:
                continue
            recorded_runs = runs_by_skill.get(skill.id, [])
            runs = _successful_runs(recorded_runs)
            if require_successful_run and not runs:
                # A crystallized candidate preserves a real first interaction,
                # but its semantic title still originates from the exploring
                # Agent's intent.  It becomes a production navigation edge only
                # after one explicit guarded replay confirms the same endpoint.
                continue
            # Candidate skills are crystallized only from publication-complete,
            # successful state transitions.  A later SkillRun increases confidence,
            # while the first deterministic reuse must already be available.  Once
            # that first reuse has been tried and no successful validation exists,
            # the candidate leaves the fixed graph until a new version is learned;
            # otherwise every later visit repeats a path that already disproved
            # itself.
            if (
                skill.status not in {"validated", "preferred"}
                and not runs
                and (
                    recorded_runs
                    or not skill.source_transition_ids
                    or not skill.evidence_refs
                )
            ):
                continue
            median_latency = statistics.median(
                [run.decision_latency_ms for run in runs]
            ) if runs else 0.0
            median_baseline_latency = statistics.median(
                [run.baseline_decision_latency_ms for run in runs]
            ) if runs else 0.0
            median_baseline_tokens = round(
                statistics.median([run.baseline_model_input_tokens for run in runs])
            ) if runs else 0
            for required_state_id in skill.applicability_scope.required_state_ids:
                learned_source_states = {
                    required_state_id,
                    *entry_aliases.get((skill.id, required_state_id), set()),
                }
                # A different learned skill can prove that two observed state IDs
                # are the same guarded entry surface.  Mirror this skill only one
                # hop across that exact persisted alias group.  Do not close over
                # groups: shared global controls on otherwise different screens
                # must never turn the state graph into a global equivalence class.
                bridge_source_proofs = self._entry_bridge_peer_proofs_from_groups(
                    required_state_id,
                    proven_bridge_groups,
                )
                for source_state_id in sorted(learned_source_states):
                    arcs.append(
                        KnownSkillArcV1(
                            skill_version_id=skill.id,
                            skill_title=skill.title,
                            from_state_id=source_state_id,
                            to_state_id=terminal_state_id,
                            action_count=action_count,
                            successful_run_count=len(runs),
                            median_decision_latency_ms=median_latency,
                            median_baseline_decision_latency_ms=median_baseline_latency,
                            median_baseline_model_input_tokens=median_baseline_tokens,
                            lifecycle_status=skill.status,
                            safety_level=skill.safety_level,
                        )
                    )
                for source_state_id, proof in sorted(bridge_source_proofs.items()):
                    if source_state_id in learned_source_states:
                        continue
                    proof_skill_version_id, proof_required_state_id = proof
                    arcs.append(
                        KnownSkillArcV1(
                            skill_version_id=skill.id,
                            skill_title=skill.title,
                            from_state_id=source_state_id,
                            to_state_id=terminal_state_id,
                            action_count=action_count,
                            successful_run_count=len(runs),
                            median_decision_latency_ms=median_latency,
                            median_baseline_decision_latency_ms=median_baseline_latency,
                            median_baseline_model_input_tokens=median_baseline_tokens,
                            lifecycle_status=skill.status,
                            safety_level=skill.safety_level,
                            entry_bridge_proof_skill_version_id=proof_skill_version_id,
                            entry_bridge_proof_required_state_id=proof_required_state_id,
                        )
                    )
        ordered = tuple(sorted(arcs, key=lambda item: (item.from_state_id, item.skill_version_id)))
        self._arc_cache[cache_key] = ordered
        return list(ordered)

    @staticmethod
    def _direct_entry_alias_is_usable(
        payload: dict[str, object],
        *,
        successful_run_skill_ids: dict[str, str],
    ) -> bool:
        """Keep a deferred alias inert until its signed run is fully settled."""

        if payload.get("requires_settled_run") is not True:
            return True
        skill_version_id = payload.get("skill_version_id")
        successful_run_id = payload.get("successful_run_id")
        return bool(
            isinstance(skill_version_id, str)
            and isinstance(successful_run_id, str)
            and successful_run_skill_ids.get(successful_run_id) == skill_version_id
        )

    @staticmethod
    def _entry_alias_is_bridge_proof(
        memory: object,
        *,
        eligible_skill_ids: set[str],
        successful_run_skill_ids: dict[str, str],
    ) -> bool:
        """Accept only durable, independently successful, near-identical aliases.

        Direct skill aliases keep their existing compatibility semantics.  The
        stronger requirements here apply only when one skill's alias group is
        used to expose another skill at an intermediate route state.
        """

        payload = getattr(memory, "payload", {})
        if not isinstance(payload, dict):
            return False
        if (
            getattr(memory, "status", None) != "active"
            or getattr(memory, "kind", None) != "procedural"
            or payload.get("schema")
            != "game-observatory.ai-player.known-skill-entry-alias.v1"
        ):
            return False
        skill_version_id = payload.get("skill_version_id")
        observed_state_id = payload.get("observed_state_id")
        required_state_id = payload.get("required_state_id")
        if (
            not isinstance(skill_version_id, str)
            or skill_version_id not in eligible_skill_ids
            or not isinstance(observed_state_id, str)
            or not isinstance(required_state_id, str)
            or observed_state_id == required_state_id
        ):
            return False

        explicit_fixture_proof = getattr(memory, "bridge_eligible", None)
        if explicit_fixture_proof is not None:
            return bool(explicit_fixture_proof)
        successful_run_id = payload.get("successful_run_id")
        if (
            not isinstance(successful_run_id, str)
            or successful_run_skill_ids.get(successful_run_id) != skill_version_id
        ):
            return False
        evidence_ref_count = payload.get("evidence_ref_count")
        if evidence_ref_count is None:
            evidence_ref_count = len(getattr(memory, "evidence_refs", ()) or ())
        try:
            visual_distance = float(payload["visual_distance"])
            evidence_count = int(evidence_ref_count)
        except (KeyError, TypeError, ValueError):
            return False
        return (
            evidence_count > 0
            and 0.0 <= visual_distance <= _ENTRY_BRIDGE_MAX_VISUAL_DISTANCE
        )

    @staticmethod
    def _entry_bridge_peers_from_groups(
        state_id: str,
        groups: Sequence[tuple[str, str, tuple[str, ...]]],
    ) -> set[str]:
        return {
            peer_state_id
            for _skill_version_id, _required_state_id, observed_state_ids in groups
            if state_id in observed_state_ids
            for peer_state_id in observed_state_ids
            if peer_state_id != state_id
        }

    @staticmethod
    def _entry_bridge_peer_proofs_from_groups(
        state_id: str,
        groups: Sequence[tuple[str, str, tuple[str, ...]]],
    ) -> dict[str, tuple[str, str]]:
        candidates: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for skill_version_id, required_state_id, observed_state_ids in groups:
            if state_id not in observed_state_ids:
                continue
            for peer_state_id in observed_state_ids:
                if peer_state_id != state_id:
                    candidates[peer_state_id].add((skill_version_id, required_state_id))
        return {
            peer_state_id: next(iter(proofs))
            for peer_state_id, proofs in candidates.items()
            if len(proofs) == 1
        }

    def entry_bridge_peers(
        self,
        environment_id: str,
        state_id: str,
        *,
        max_safety: str = "economic",
        require_successful_run: bool = True,
    ) -> set[str]:
        """Return one-hop peers from exact, evidence-backed alias groups only."""

        revision = self._revision(environment_id)
        cache_key = (environment_id, max_safety, require_successful_run, revision)
        if cache_key not in self._entry_bridge_group_cache:
            self.arcs(
                environment_id,
                max_safety=max_safety,
                require_successful_run=require_successful_run,
            )
        return self._entry_bridge_peers_from_groups(
            state_id,
            self._entry_bridge_group_cache.get(cache_key, ()),
        )

    def required_state_alias(
        self,
        environment_id: str,
        observed_state_id: str,
        skill: SkillVersionV1,
        *,
        max_safety: str = "economic",
        require_successful_run: bool = True,
    ) -> str | None:
        """Resolve a unique declared source through direct or one-hop proof.

        A one-hop bridge only makes the route plannable.  It is deliberately not
        returned by ``remembered_skill_entry_aliases``; replay must still compare
        the live source EvidenceStep against the target skill's own source before
        any device action.  A successful replay then sediments the direct alias.
        """

        declared = set(skill.applicability_scope.required_state_ids)
        direct_matches = {
            required_state_id
            for skill_version_id, required_state_id in self.remembered_skill_entry_aliases(
                environment_id,
                observed_state_id,
            )
            if skill_version_id == skill.id and required_state_id in declared
        }
        if len(direct_matches) == 1:
            return next(iter(direct_matches))
        if direct_matches:
            return None
        bridge_peers = self.entry_bridge_peers(
            environment_id,
            observed_state_id,
            max_safety=max_safety,
            require_successful_run=require_successful_run,
        )
        bridge_matches = declared.intersection(bridge_peers)
        return next(iter(bridge_matches)) if len(bridge_matches) == 1 else None

    def remembered_entry_aliases(
        self,
        environment_id: str,
        observed_state_id: str,
    ) -> set[str]:
        aliases: set[str] = set()
        for memory in self.store.list_memories(environment_id):
            payload = memory.payload
            if (
                memory.status == "active"
                and memory.kind == "procedural"
                and payload.get("schema")
                == "game-observatory.ai-player.known-skill-entry-alias.v1"
                and payload.get("observed_state_id") == observed_state_id
                and isinstance(payload.get("required_state_id"), str)
            ):
                aliases.add(str(payload["required_state_id"]))
        return aliases

    def terminal_state_contradicts_goal(
        self,
        environment_id: str,
        terminal_state_id: str,
        goal_query: str,
    ) -> bool:
        """Reject a mislabeled candidate when later actions identify its surface.

        The first explorer may call a click "内政入口" even though the resulting
        surface is an alliance prompt.  If a later evidence-backed atomic skill
        starts there and is explicitly named "关闭加入同盟弹窗", that exit control
        gives the terminal state a stronger object identity than the earlier
        intent label.  Such a contradiction must block automatic second-use
        validation and return the ambiguity to semantic exploration.
        """

        goal_subject = _named_surface_subject(goal_query)
        if goal_subject is None or goal_subject in _GENERIC_SURFACE_SUBJECTS:
            return False
        latest_by_skill: dict[str, SkillVersionV1] = {}
        for skill in self.store.list_skill_versions(environment_id):
            previous = latest_by_skill.get(skill.skill_id)
            if previous is None or skill.version > previous.version:
                latest_by_skill[skill.skill_id] = skill
        for skill in latest_by_skill.values():
            if (
                skill.status in {"degraded", "invalidated"}
                or terminal_state_id not in skill.applicability_scope.required_state_ids
                or _goal_operation_family(skill.title) != "exit"
            ):
                continue
            exit_subject = _named_surface_subject(skill.title)
            if (
                exit_subject is not None
                and exit_subject not in _GENERIC_SURFACE_SUBJECTS
                and not _named_surface_subjects_compatible(skill.title, goal_query)
            ):
                return True
        return False

    def remembered_skill_entry_aliases(
        self,
        environment_id: str,
        observed_state_id: str,
        *,
        require_settled_proof: bool = False,
    ) -> set[tuple[str, str]]:
        memories = self._route_alias_memories(environment_id)
        deferred_skill_ids = {
            str(memory.payload["skill_version_id"])
            for memory in memories
            if memory.status == "active"
            and memory.kind == "procedural"
            and memory.payload.get("schema")
            == "game-observatory.ai-player.known-skill-entry-alias.v1"
            and memory.payload.get("observed_state_id") == observed_state_id
            and memory.payload.get("requires_settled_run") is True
            and isinstance(memory.payload.get("skill_version_id"), str)
        }
        successful_run_skill_ids: dict[str, str] = {}
        if deferred_skill_ids:
            run_loader = getattr(self.store, "list_known_route_run_summaries", None)
            if callable(run_loader):
                route_runs = run_loader(environment_id, sorted(deferred_skill_ids))
            else:
                route_runs = self.store.list_skill_runs(environment_id)
            runs_by_skill: dict[
                str,
                list[SkillRunV1 | KnownRouteSkillRunSummary],
            ] = defaultdict(list)
            for run in route_runs:
                if run.skill_version_id in deferred_skill_ids:
                    runs_by_skill[run.skill_version_id].append(run)
            successful_run_skill_ids = {
                str(run_id): str(run.skill_version_id)
                for runs in runs_by_skill.values()
                for run in _successful_runs(runs)
                if (run_id := getattr(run, "run_id", getattr(run, "id", None)))
                is not None
            }
        aliases: set[tuple[str, str]] = set()
        for memory in memories:
            payload = memory.payload
            if (
                memory.status == "active"
                and memory.kind == "procedural"
                and payload.get("schema")
                == "game-observatory.ai-player.known-skill-entry-alias.v1"
                and payload.get("observed_state_id") == observed_state_id
                and isinstance(payload.get("skill_version_id"), str)
                and isinstance(payload.get("required_state_id"), str)
                and (
                    not require_settled_proof
                    or payload.get("requires_settled_run") is True
                )
                and self._direct_entry_alias_is_usable(
                    payload,
                    successful_run_skill_ids=successful_run_skill_ids,
                )
            ):
                aliases.add(
                    (
                        str(payload["skill_version_id"]),
                        str(payload["required_state_id"]),
                    )
                )
        return aliases

    def entry_equivalence_closure(
        self,
        environment_id: str,
        state_ids: Sequence[str],
    ) -> set[str]:
        """Expand persisted entry aliases in both directions for loop checks.

        Entry aliases remain skill-scoped during execution.  For cycle rejection,
        however, either side proves that both IDs can denote the same functional
        screen for at least one fixed operation.  Returning to either side cannot
        satisfy an exit goal that started on the other side.
        """

        pairs: list[tuple[str, str]] = []
        for memory in self._route_alias_memories(environment_id):
            payload = memory.payload
            if (
                memory.status == "active"
                and memory.kind == "procedural"
                and payload.get("schema")
                == "game-observatory.ai-player.known-skill-entry-alias.v1"
                and isinstance(payload.get("observed_state_id"), str)
                and isinstance(payload.get("required_state_id"), str)
            ):
                pairs.append(
                    (
                        str(payload["observed_state_id"]),
                        str(payload["required_state_id"]),
                    )
                )
        closure = set(state_ids)
        changed = True
        while changed:
            changed = False
            for left, right in pairs:
                if left in closure or right in closure:
                    before = len(closure)
                    closure.update((left, right))
                    changed = changed or len(closure) != before
        return closure

    def remembered_skill_terminal_aliases(
        self,
        environment_id: str,
        skill_version_id: str,
        source_state_id: str,
    ) -> set[str]:
        """Return semantically confirmed endpoints for one fixed operation.

        A caller-dependent control can return to a live-content variant whose
        pixels and induced state ID differ from the endpoint captured during the
        first crystallization.  These aliases are deliberately scoped by both
        operation and source state: a confirmation for one Back path must never
        make an unrelated screen change pass its terminal guard.
        """

        aliases: set[str] = set()
        for memory in self._route_alias_memories(environment_id):
            payload = memory.payload
            if (
                memory.status == "active"
                and memory.kind == "procedural"
                and payload.get("schema")
                == "game-observatory.ai-player.known-skill-terminal-alias.v1"
                and payload.get("skill_version_id") == skill_version_id
                and payload.get("source_state_id") == source_state_id
                and isinstance(payload.get("observed_terminal_state_id"), str)
            ):
                aliases.add(str(payload["observed_terminal_state_id"]))
        return aliases

    def goal_source_state_ids(
        self,
        environment_id: str,
        goal_query: str,
        *,
        max_safety: str = "economic",
        require_successful_run: bool = False,
    ) -> tuple[str, ...]:
        needle = goal_query.casefold().strip()
        skills = self._route_skills(
            environment_id,
            max_safety=max_safety,
            require_successful_run=require_successful_run,
        )
        matching_skills = self._matching_goal_skill_ids(environment_id, skills, needle)
        return tuple(
            sorted(
                {
                    arc.from_state_id
                    for arc in self.arcs(
                        environment_id,
                        max_safety=max_safety,
                        require_successful_run=require_successful_run,
                    )
                    if arc.skill_version_id in matching_skills
                }
            )
        )

    def candidate_entry_state_ids(
        self,
        environment_id: str,
        goal_query: str,
        *,
        max_safety: str = "economic",
        require_successful_run: bool = False,
    ) -> tuple[str, ...]:
        """Return possible known-route entries ordered outward from the goal."""

        needle = goal_query.casefold().strip()
        arcs = self.arcs(
            environment_id,
            max_safety=max_safety,
            require_successful_run=require_successful_run,
        )
        skills = {
            skill.id: skill
            for skill in self._route_skills(
                environment_id,
                max_safety=max_safety,
                require_successful_run=require_successful_run,
            )
        }
        exact_state = self.store.get_semantic_state(environment_id, goal_query)
        if exact_state is not None:
            matched: set[str] = set()
            goal_states = {exact_state.id}
        else:
            matched = self._matching_goal_skill_ids(
                environment_id,
                tuple(skills.values()),
                needle,
            )
            goal_states = {arc.to_state_id for arc in arcs if arc.skill_version_id in matched}
        if not goal_states:
            return ()
        arcs = _goal_scoped_arcs(
            arcs,
            skills,
            matching_skill_ids=matched,
            goal_state_ids=goal_states,
        )
        incoming: dict[str, set[str]] = defaultdict(set)
        for arc in arcs:
            incoming[arc.to_state_id].add(arc.from_state_id)
        seen = set(goal_states)
        frontier = set(goal_states)
        ordered: list[str] = []
        while frontier:
            next_frontier = {
                source
                for destination in frontier
                for source in incoming.get(destination, set())
                if source not in seen
            }
            ordered.extend(sorted(next_frontier))
            seen.update(next_frontier)
            frontier = next_frontier
        return tuple(ordered)

    def plan(
        self,
        environment_id: str,
        observed_start_state_id: str,
        goal_query: str,
        *,
        additional_entry_state_ids: Sequence[str] = (),
        max_safety: str = "economic",
        max_skills: int = 12,
        require_successful_run: bool = False,
    ) -> KnownSkillRouteV1:
        if max_skills < 0:
            raise ValueError("max_skills must be non-negative")
        needle = goal_query.casefold().strip()
        if not needle:
            raise ValueError("known-route goal query is empty")
        arcs = self.arcs(
            environment_id,
            max_safety=max_safety,
            require_successful_run=require_successful_run,
        )
        skill_by_id = {
            skill.id: skill
            for skill in self._route_skills(
                environment_id,
                max_safety=max_safety,
                require_successful_run=require_successful_run,
            )
        }
        exact_state = self.store.get_semantic_state(environment_id, goal_query)
        if exact_state is not None:
            matching_skill_ids: set[str] = set()
            goal_state_ids = {exact_state.id}
        else:
            matching_skill_ids = self._matching_goal_skill_ids(
                environment_id,
                tuple(skill_by_id.values()),
                needle,
            )
            goal_state_ids = {
                arc.to_state_id for arc in arcs if arc.skill_version_id in matching_skill_ids
            }
        if not goal_state_ids:
            raise LookupError(f"no learned route target matches: {goal_query}")
        arcs = _goal_scoped_arcs(
            arcs,
            skill_by_id,
            matching_skill_ids=matching_skill_ids,
            goal_state_ids=goal_state_ids,
            exact_observed_start_state_id=observed_start_state_id,
        )

        remembered = self.remembered_entry_aliases(
            environment_id,
            observed_start_state_id,
        )
        entry_states = {
            observed_start_state_id,
            *remembered,
            *additional_entry_state_ids,
        }
        operation_family = _goal_operation_family(needle)
        if operation_family == "exit":
            equivalent_start_states = self.entry_equivalence_closure(
                environment_id,
                tuple(entry_states),
            )
            goal_state_ids.difference_update(equivalent_start_states)
            if not goal_state_ids:
                raise LookupError(
                    f"learned exit targets only return to the current screen: {goal_query}"
                )
        for state_id in sorted(entry_states):
            # A commit goal names a state-changing operation. Merely standing
            # on a confirmation prompt never proves that the confirmation was
            # submitted, even when an earlier save/configure skill happened to
            # use that prompt as its terminal state.
            if state_id in goal_state_ids and operation_family != "commit":
                return KnownSkillRouteV1(
                    environment_id=environment_id,
                    observed_start_state_id=observed_start_state_id,
                    selected_entry_state_id=state_id,
                    goal_query=goal_query,
                    goal_state_id=state_id,
                    skill_version_ids=(),
                    state_ids=(state_id,),
                    total_cost=0,
                )

        adjacency: dict[str, list[KnownSkillArcV1]] = defaultdict(list)
        for arc in arcs:
            adjacency[arc.from_state_id].append(arc)
        for values in adjacency.values():
            values.sort(key=lambda item: (item.action_count, item.skill_version_id))

        queue: list[
            tuple[float, int, str, str, tuple[str, ...], tuple[str, ...]]
        ] = []
        best: dict[tuple[str, str], tuple[float, int]] = {}
        for entry_state_id in sorted(entry_states):
            key = (entry_state_id, entry_state_id)
            best[key] = (0.0, 0)
            heapq.heappush(
                queue,
                (0.0, 0, entry_state_id, entry_state_id, (entry_state_id,), ()),
            )
        while queue:
            cost, skill_count, state_id, entry_state_id, states, skills = heapq.heappop(queue)
            if state_id in goal_state_ids and (
                operation_family != "commit" or skill_count > 0
            ):
                return KnownSkillRouteV1(
                    environment_id=environment_id,
                    observed_start_state_id=observed_start_state_id,
                    selected_entry_state_id=entry_state_id,
                    goal_query=goal_query,
                    goal_state_id=state_id,
                    skill_version_ids=skills,
                    state_ids=states,
                    total_cost=cost,
                )
            if skill_count >= max_skills:
                continue
            for arc in adjacency.get(state_id, []):
                skill = skill_by_id.get(arc.skill_version_id)
                caller_dependent_exit = (
                    _goal_operation_family(getattr(skill, "title", "")) == "exit"
                )
                exit_is_goal = (
                    arc.skill_version_id in matching_skill_ids
                    or arc.to_state_id in goal_state_ids
                )
                if caller_dependent_exit and not exit_is_goal and not (
                    skill_count == 0
                    and entry_state_id == observed_start_state_id
                    and state_id == observed_start_state_id
                    and state_id
                    in getattr(
                        getattr(skill, "applicability_scope", None),
                        "required_state_ids",
                        (),
                    )
                ):
                    # A close/back control may leave the exact current caller as
                    # the route's first action.  It must not be reached through
                    # an alias, an additional entry, or a later graph node.
                    continue
                lifecycle_penalty = 0.0 if arc.lifecycle_status == "preferred" else 0.01
                # A virgin candidate is available for one guarded trial only;
                # it must never beat an independently successful route merely
                # because the proven route has a measured latency. This keeps
                # repeated navigation on the deterministic program layer while
                # preserving candidate fallback when no learned path exists.
                validation_penalty = 0.0 if arc.successful_run_count > 0 else 100.0
                next_cost = (
                    cost
                    + arc.action_count
                    + arc.median_decision_latency_ms / 1_000_000
                    + lifecycle_penalty
                    + validation_penalty
                )
                next_count = skill_count + 1
                key = (entry_state_id, arc.to_state_id)
                previous = best.get(key)
                if previous is not None and previous <= (next_cost, next_count):
                    continue
                best[key] = (next_cost, next_count)
                heapq.heappush(
                    queue,
                    (
                        next_cost,
                        next_count,
                        arc.to_state_id,
                        entry_state_id,
                        (*states, arc.to_state_id),
                        (*skills, arc.skill_version_id),
                    ),
                )
        raise LookupError(
            f"no learned route from {observed_start_state_id} to {goal_query}"
        )

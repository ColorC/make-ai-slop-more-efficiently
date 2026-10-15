# [OMNI] origin=grok created_by=grok intent="expose testable agent freedom on top of MAA recruit/shop interpreters"
"""Agent-authored score and job freedom on top of the MAA policy interpreters.

Reasonable ranking and reliable execution stay in ``RecruitPolicy`` /
``ShopPolicy`` / MAA hands.  This module only exposes what an agent may write:

- a named score profile (MAA default + optional computed blend + AI deltas);
- declarative when/boost and job-selector rules;
- a restricted script that can only call ``api.*``.

Declarative rules and scripts add.  Neither may import, open files, or touch
pixels.  The existing interpreters are not forked.
"""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .is_policy import (
    RecruitOffsetRule,
    RecruitOperatorRule,
    RecruitPolicy,
    ShopItemRule,
    ShopPolicy,
    SquadCompletenessRule,
    SquadFacts,
)
from .maa_operator import MaaPaths

__all__ = [
    "API_METHODS",
    "COMPUTED_WEIGHTS_ZERO",
    "PLUGIN_SCHEMA",
    "PROFILE_SCHEMA",
    "ComputedDims",
    "ComputedWeights",
    "ContestantPlugin",
    "ContestantScriptError",
    "ContestantSurfaceError",
    "HookSpec",
    "MaaRecruitmentTable",
    "PluginResult",
    "RunContext",
    "ScoreBreakdown",
    "ScoreProfile",
    "ScoreRevision",
    "SurfaceEvaluation",
    "apply_plugin",
    "empty_squad_facts",
    "evaluate_surface",
    "load_maa_recruitment",
    "load_sami_default_table",
    "parse_contestant_plugin",
    "sami_recruitment_path",
]

PROFILE_SCHEMA = "omnicompany.arknights.score-profile.v1"
PLUGIN_SCHEMA = "omnicompany.arknights.contestant-plugin.v1"

API_METHODS = frozenset(
    {
        "has_operator",
        "has_relic",
        "has_group",
        "add_score_offset",
        "add_group_offset",
        "add_relic_offset",
        "set_job",
        "register_hook",
        "stage",
        "operators",
        "relics",
    }
)

_SAFE_BUILTINS: dict[str, Any] = {
    "True": True,
    "False": False,
    "None": None,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "set": set,
    "str": str,
    "sum": sum,
    "tuple": tuple,
}

_ALLOWED_AST = (
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.If,
    ast.For,
    ast.While,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.FunctionDef,
    ast.Return,
    ast.arguments,
    ast.arg,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Del,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.Dict,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.UAdd,
    ast.USub,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Call,
    ast.keyword,
    ast.IfExp,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Attribute,
    ast.Slice,
    ast.Subscript,
)


class ContestantSurfaceError(ValueError):
    """A score profile, plugin payload, or evaluation input is invalid."""


class ContestantScriptError(ContestantSurfaceError):
    """A contestant script used forbidden syntax or an unknown API."""


@dataclass(frozen=True, slots=True)
class ComputedDims:
    skill_dps: float = 0.0
    skill_hps: float = 0.0
    block: float = 0.0
    aoe: float = 0.0
    range_air: float = 0.0


@dataclass(frozen=True, slots=True)
class ComputedWeights:
    skill_dps: float = 0.0
    skill_hps: float = 0.0
    block: float = 0.0
    aoe: float = 0.0
    range_air: float = 0.0


COMPUTED_WEIGHTS_ZERO = ComputedWeights()


@dataclass(frozen=True, slots=True)
class ScoreRevision:
    revision_id: str
    author: str
    reason: str
    operator: str
    delta: int


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    operator: str
    default_priority: int
    computed_blend: int
    ai_delta: int
    plugin_offset: int
    final: int


@dataclass(frozen=True, slots=True)
class HookSpec:
    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MaaOperatorRecord:
    name: str
    priority: int
    priority_when_team_full: int | None
    is_key: bool
    is_start: bool
    offsets: tuple[RecruitOffsetRule, ...]
    groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaaRecruitmentTable:
    theme: str
    operators: Mapping[str, MaaOperatorRecord]
    groups: Mapping[str, tuple[str, ...]]
    team_complete_condition: tuple[SquadCompletenessRule, ...]

    def group_names(self) -> tuple[str, ...]:
        names = set(self.groups)
        for record in self.operators.values():
            for offset in record.offsets:
                names.update(offset.groups)
        for rule in self.team_complete_condition:
            names.update(rule.groups)
        return tuple(sorted(names))


@dataclass(frozen=True, slots=True)
class ScoreProfile:
    profile_id: str
    author: str
    table: MaaRecruitmentTable
    ai_deltas: Mapping[str, int] = field(default_factory=dict)
    weights: ComputedWeights = COMPUTED_WEIGHTS_ZERO
    revisions: tuple[ScoreRevision, ...] = ()

    @classmethod
    def from_maa_table(
        cls,
        table: MaaRecruitmentTable,
        *,
        profile_id: str,
        author: str,
        weights: ComputedWeights = COMPUTED_WEIGHTS_ZERO,
    ) -> ScoreProfile:
        if not profile_id.strip() or not author.strip():
            raise ContestantSurfaceError("profile_id and author must be non-blank")
        return cls(
            profile_id=profile_id,
            author=author,
            table=table,
            weights=weights,
        )

    def default_priority(self, operator: str) -> int:
        record = self.table.operators.get(operator)
        if record is None:
            raise ContestantSurfaceError(f"operator not in default table: {operator!r}")
        return record.priority

    def computed_blend(self, dims: ComputedDims | None) -> int:
        if dims is None:
            return 0
        value = (
            self.weights.skill_dps * dims.skill_dps
            + self.weights.skill_hps * dims.skill_hps
            + self.weights.block * dims.block
            + self.weights.aoe * dims.aoe
            + self.weights.range_air * dims.range_air
        )
        return int(value)

    def score(
        self,
        operator: str,
        *,
        dims: ComputedDims | None = None,
        plugin_offset: int = 0,
    ) -> ScoreBreakdown:
        default = self.default_priority(operator)
        computed = self.computed_blend(dims)
        delta = int(self.ai_deltas.get(operator, 0))
        return ScoreBreakdown(
            operator=operator,
            default_priority=default,
            computed_blend=computed,
            ai_delta=delta,
            plugin_offset=int(plugin_offset),
            final=default + computed + delta + int(plugin_offset),
        )

    def apply_delta(self, operator: str, delta: int, *, author: str, reason: str) -> ScoreProfile:
        if not operator.strip():
            raise ContestantSurfaceError("operator must be non-blank")
        if operator not in self.table.operators:
            raise ContestantSurfaceError(f"operator not in default table: {operator!r}")
        if not author.strip() or not reason.strip():
            raise ContestantSurfaceError("author and reason must be non-blank")
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise ContestantSurfaceError("delta must be an integer")
        revision = ScoreRevision(
            revision_id=f"r{len(self.revisions) + 1:03d}",
            author=author,
            reason=reason,
            operator=operator,
            delta=delta,
        )
        deltas = dict(self.ai_deltas)
        deltas[operator] = int(deltas.get(operator, 0)) + delta
        if deltas[operator] == 0:
            del deltas[operator]
        return replace(self, ai_deltas=deltas, revisions=(*self.revisions, revision))

    def rollback(self, revision_id: str) -> ScoreProfile:
        index = next(
            (i for i, item in enumerate(self.revisions) if item.revision_id == revision_id),
            None,
        )
        if index is None:
            raise ContestantSurfaceError(f"unknown revision: {revision_id!r}")
        deltas = dict(self.ai_deltas)
        for item in self.revisions[index:]:
            deltas[item.operator] = int(deltas.get(item.operator, 0)) - item.delta
            if deltas[item.operator] == 0:
                del deltas[item.operator]
        return replace(self, ai_deltas=deltas, revisions=self.revisions[:index])


@dataclass(frozen=True, slots=True)
class _When:
    has_operator: str | None = None
    has_relic: str | None = None
    has_group: str | None = None

    def holds(self, context: RunContext) -> bool:
        if self.has_operator is not None and not context.has_operator(self.has_operator):
            return False
        if self.has_relic is not None and not context.has_relic(self.has_relic):
            return False
        if self.has_group is not None and not context.has_group(self.has_group):
            return False
        return True


@dataclass(frozen=True, slots=True)
class ScoreRule:
    when: _When
    operators: tuple[str, ...]
    groups: tuple[str, ...]
    relics: tuple[str, ...]
    offset: int


@dataclass(frozen=True, slots=True)
class JobRule:
    job_id: str
    stage: str | None = None
    when: _When = field(default_factory=_When)


@dataclass(frozen=True, slots=True)
class ContestantPlugin:
    score_rules: tuple[ScoreRule, ...] = ()
    job_rules: tuple[JobRule, ...] = ()


@dataclass(frozen=True, slots=True)
class RunContext:
    operators: tuple[str, ...] = ()
    relics: tuple[str, ...] = ()
    stage: str | None = None
    groups: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "operators", tuple(dict.fromkeys(self.operators)))
        object.__setattr__(self, "relics", tuple(dict.fromkeys(self.relics)))
        cleaned: dict[str, tuple[str, ...]] = {}
        for name, members in self.groups.items():
            if not isinstance(name, str) or not name.strip():
                raise ContestantSurfaceError("group names must be non-blank strings")
            cleaned[name] = tuple(dict.fromkeys(members))
        object.__setattr__(self, "groups", cleaned)

    def has_operator(self, name: str) -> bool:
        return name in self.operators

    def has_relic(self, name: str) -> bool:
        return name in self.relics

    def has_group(self, name: str) -> bool:
        if name in self.operators:
            return True
        members = self.groups.get(name)
        if not members:
            return False
        owned = set(self.operators)
        return any(member in owned for member in members)


@dataclass(frozen=True, slots=True)
class PluginResult:
    operator_offsets: Mapping[str, int]
    relic_offsets: Mapping[str, int]
    job_id: str | None
    hooks: tuple[HookSpec, ...]

    def operator_offset(self, name: str) -> int:
        return int(self.operator_offsets.get(name, 0))

    def relic_offset(self, name: str) -> int:
        return int(self.relic_offsets.get(name, 0))


@dataclass(frozen=True, slots=True)
class SurfaceEvaluation:
    scores: Mapping[str, ScoreBreakdown]
    relic_scores: Mapping[str, int]
    job_id: str | None
    hooks: tuple[HookSpec, ...]
    recruit_policy: RecruitPolicy
    shop_policy: ShopPolicy | None
    plugin: PluginResult


def sami_recruitment_path(*, maa_root: Path | str | None = None) -> Path:
    root = MaaPaths.locate(root=maa_root).root
    return root / "resource" / "roguelike" / "Sami" / "recruitment.json"


def load_sami_default_table(*, maa_root: Path | str | None = None) -> MaaRecruitmentTable:
    return load_maa_recruitment(sami_recruitment_path(maa_root=maa_root))


def load_maa_recruitment(path: Path | str) -> MaaRecruitmentTable:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ContestantSurfaceError("MAA recruitment root must be an object")
    theme = raw.get("theme")
    if not isinstance(theme, str) or not theme.strip():
        raise ContestantSurfaceError("MAA recruitment theme must be a non-blank string")
    groups_raw = raw.get("priority")
    if not isinstance(groups_raw, list) or not groups_raw:
        raise ContestantSurfaceError("MAA recruitment priority must be a non-empty list")
    groups: dict[str, list[str]] = {}
    operators: dict[str, MaaOperatorRecord] = {}
    for index, group in enumerate(groups_raw):
        where = f"priority[{index}]"
        if not isinstance(group, Mapping):
            raise ContestantSurfaceError(f"{where} must be an object")
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name.strip():
            raise ContestantSurfaceError(f"{where}.name must be a non-blank string")
        opers = group.get("opers")
        if not isinstance(opers, list) or not opers:
            raise ContestantSurfaceError(f"{where}.opers must be a non-empty list")
        members: list[str] = []
        for oper_index, oper in enumerate(opers):
            record = _parse_maa_oper(oper, f"{where}.opers[{oper_index}]", group_name)
            if record is None:
                continue
            members.append(record.name)
            existing = operators.get(record.name)
            if existing is None:
                operators[record.name] = record
            else:
                operators[record.name] = replace(
                    existing, groups=tuple(dict.fromkeys((*existing.groups, group_name)))
                )
        groups[group_name] = list(dict.fromkeys(members))
    completeness = _parse_maa_completeness(raw.get("team_complete_condition"))
    return MaaRecruitmentTable(
        theme=theme,
        operators=operators,
        groups={name: tuple(members) for name, members in groups.items()},
        team_complete_condition=completeness,
    )


def empty_squad_facts(table: MaaRecruitmentTable) -> SquadFacts:
    return SquadFacts({name: 0 for name in table.group_names()})


def parse_contestant_plugin(raw: Mapping[str, object]) -> ContestantPlugin:
    if not isinstance(raw, Mapping):
        raise ContestantSurfaceError("plugin must be an object")
    _allowed(raw, {"schema", "score_rules", "job_rules"}, "plugin")
    if raw.get("schema") != PLUGIN_SCHEMA:
        raise ContestantSurfaceError("unsupported contestant plugin schema")
    score_rules = tuple(
        _parse_score_rule(item, f"score_rules[{index}]")
        for index, item in enumerate(_as_list(raw.get("score_rules"), "score_rules"))
    )
    job_rules = tuple(
        _parse_job_rule(item, f"job_rules[{index}]")
        for index, item in enumerate(_as_list(raw.get("job_rules"), "job_rules"))
    )
    return ContestantPlugin(score_rules=score_rules, job_rules=job_rules)


def apply_plugin(
    plugin: ContestantPlugin | Mapping[str, object] | None,
    context: RunContext,
    *,
    script: str | None = None,
) -> PluginResult:
    parsed = plugin if isinstance(plugin, ContestantPlugin) else (
        parse_contestant_plugin(plugin) if plugin is not None else ContestantPlugin()
    )
    operator_offsets: dict[str, int] = defaultdict(int)
    relic_offsets: dict[str, int] = defaultdict(int)
    job_id: str | None = None
    for rule in parsed.score_rules:
        if not rule.when.holds(context):
            continue
        for name in rule.operators:
            operator_offsets[name] += rule.offset
        for group in rule.groups:
            for member in context.groups.get(group, ()):
                operator_offsets[member] += rule.offset
        for relic in rule.relics:
            relic_offsets[relic] += rule.offset
    for rule in parsed.job_rules:
        if rule.stage is not None and rule.stage != context.stage:
            continue
        if not rule.when.holds(context):
            continue
        job_id = rule.job_id
        break
    hooks: list[HookSpec] = []
    if script:
        job_id, script_hooks = _run_script(
            script,
            context,
            operator_offsets=operator_offsets,
            relic_offsets=relic_offsets,
            job_id=job_id,
        )
        hooks.extend(script_hooks)
    return PluginResult(
        operator_offsets=dict(operator_offsets),
        relic_offsets=dict(relic_offsets),
        job_id=job_id,
        hooks=tuple(hooks),
    )


def evaluate_surface(
    profile: ScoreProfile,
    context: RunContext,
    *,
    plugin: ContestantPlugin | Mapping[str, object] | None = None,
    script: str | None = None,
    shop: ShopPolicy | None = None,
    dims: Mapping[str, ComputedDims] | None = None,
    operators: Sequence[str] | None = None,
) -> SurfaceEvaluation:
    groups = dict(context.groups) if context.groups else dict(profile.table.groups)
    bound = replace(context, groups=groups)
    result = apply_plugin(plugin, bound, script=script)
    names = list(operators) if operators is not None else list(profile.table.operators)
    scores = {
        name: profile.score(
            name,
            dims=None if dims is None else dims.get(name),
            plugin_offset=result.operator_offset(name),
        )
        for name in names
        if name in profile.table.operators
    }
    relic_scores: dict[str, int] = {}
    shop_policy = None
    if shop is not None:
        adjusted: list[ShopItemRule] = []
        for item in shop.items:
            extra = result.relic_offset(item.name)
            relic_scores[item.name] = item.priority + extra
            adjusted.append(replace(item, priority=item.priority + extra))
        shop_policy = ShopPolicy(items=tuple(adjusted))
    return SurfaceEvaluation(
        scores=scores,
        relic_scores=relic_scores,
        job_id=result.job_id,
        hooks=result.hooks,
        recruit_policy=_materialize_recruit_policy(profile, scores),
        shop_policy=shop_policy,
        plugin=result,
    )


def _materialize_recruit_policy(
    profile: ScoreProfile,
    scores: Mapping[str, ScoreBreakdown],
) -> RecruitPolicy:
    operators: dict[str, RecruitOperatorRule] = {}
    for name, record in profile.table.operators.items():
        breakdown = scores.get(name)
        extra = 0 if breakdown is None else (
            breakdown.computed_blend + breakdown.ai_delta + breakdown.plugin_offset
        )
        when_full = None if record.priority_when_team_full is None else record.priority_when_team_full + extra
        operators[name] = RecruitOperatorRule(
            name=name,
            priority=record.priority + extra,
            priority_when_team_full=when_full,
            is_key=record.is_key,
            is_start=record.is_start,
            offsets=record.offsets,
        )
    return RecruitPolicy(
        operators=operators,
        team_complete_condition=profile.table.team_complete_condition,
    )


def _parse_maa_oper(raw: object, where: str, group_name: str) -> MaaOperatorRecord | None:
    if not isinstance(raw, Mapping):
        raise ContestantSurfaceError(f"{where} must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ContestantSurfaceError(f"{where}.name must be a non-blank string")
    priority_raw = raw.get("recruit_priority")
    if priority_raw is None:
        return None
    if isinstance(priority_raw, bool) or not isinstance(priority_raw, int):
        raise ContestantSurfaceError(f"{where}.recruit_priority must be an integer")
    when_full_raw = raw.get("recruit_priority_when_team_full")
    when_full = None
    if when_full_raw is not None:
        if isinstance(when_full_raw, bool) or not isinstance(when_full_raw, int):
            raise ContestantSurfaceError(
                f"{where}.recruit_priority_when_team_full must be an integer"
            )
        when_full = when_full_raw
    offsets_raw = raw.get("recruit_priority_offsets")
    offsets: list[RecruitOffsetRule] = []
    if offsets_raw is not None:
        if not isinstance(offsets_raw, list):
            raise ContestantSurfaceError(f"{where}.recruit_priority_offsets must be a list")
        for index, item in enumerate(offsets_raw):
            offsets.append(_parse_maa_offset(item, f"{where}.recruit_priority_offsets[{index}]"))
    return MaaOperatorRecord(
        name=name,
        priority=priority_raw,
        priority_when_team_full=when_full,
        is_key=bool(raw.get("is_key", False)),
        is_start=bool(raw.get("is_start", False)),
        offsets=tuple(offsets),
        groups=(group_name,),
    )


def _parse_maa_offset(raw: object, where: str) -> RecruitOffsetRule:
    if not isinstance(raw, Mapping):
        raise ContestantSurfaceError(f"{where} must be an object")
    groups_raw = raw.get("groups")
    if not isinstance(groups_raw, list) or not groups_raw:
        raise ContestantSurfaceError(f"{where}.groups must be a non-empty list")
    groups: list[str] = []
    for index, group in enumerate(groups_raw):
        if not isinstance(group, str) or not group.strip():
            raise ContestantSurfaceError(f"{where}.groups[{index}] must be a non-blank string")
        groups.append(group)
    is_less = bool(raw.get("is_less", False))
    threshold = 1 if raw.get("threshold") is None else raw["threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise ContestantSurfaceError(f"{where}.threshold must be a non-negative integer")
    offset = raw.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ContestantSurfaceError(f"{where}.offset must be an integer")
    return RecruitOffsetRule(
        groups=tuple(groups),
        is_less=is_less,
        threshold=threshold,
        offset=offset,
    )


def _parse_maa_completeness(raw: object) -> tuple[SquadCompletenessRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ContestantSurfaceError("team_complete_condition must be a list")
    rules: list[SquadCompletenessRule] = []
    for index, item in enumerate(raw):
        where = f"team_complete_condition[{index}]"
        if not isinstance(item, Mapping):
            raise ContestantSurfaceError(f"{where} must be an object")
        groups_raw = item.get("groups")
        if not isinstance(groups_raw, list) or not groups_raw:
            raise ContestantSurfaceError(f"{where}.groups must be a non-empty list")
        groups: list[str] = []
        for group_index, group in enumerate(groups_raw):
            if not isinstance(group, str) or not group.strip():
                raise ContestantSurfaceError(
                    f"{where}.groups[{group_index}] must be a non-blank string"
                )
            groups.append(group)
        threshold = item.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ContestantSurfaceError(f"{where}.threshold must be a positive integer")
        rules.append(SquadCompletenessRule(groups=tuple(groups), threshold=threshold))
    return tuple(rules)


def _parse_score_rule(raw: object, where: str) -> ScoreRule:
    if not isinstance(raw, Mapping):
        raise ContestantSurfaceError(f"{where} must be an object")
    _allowed(raw, {"when", "boost"}, where)
    when = _parse_when(raw.get("when"), f"{where}.when")
    boost = raw.get("boost")
    if not isinstance(boost, Mapping):
        raise ContestantSurfaceError(f"{where}.boost must be an object")
    _allowed(boost, {"operators", "groups", "relics", "offset"}, f"{where}.boost")
    operators = tuple(_as_name_list(boost.get("operators"), f"{where}.boost.operators"))
    groups = tuple(_as_name_list(boost.get("groups"), f"{where}.boost.groups"))
    relics = tuple(_as_name_list(boost.get("relics"), f"{where}.boost.relics"))
    if sum(bool(item) for item in (operators, groups, relics)) != 1:
        raise ContestantSurfaceError(
            f"{where}.boost must set exactly one of operators, groups, relics"
        )
    offset = boost.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ContestantSurfaceError(f"{where}.boost.offset must be an integer")
    return ScoreRule(when=when, operators=operators, groups=groups, relics=relics, offset=offset)


def _parse_job_rule(raw: object, where: str) -> JobRule:
    if not isinstance(raw, Mapping):
        raise ContestantSurfaceError(f"{where} must be an object")
    _allowed(raw, {"stage", "when", "job_id"}, where)
    job_id = raw.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ContestantSurfaceError(f"{where}.job_id must be a non-blank string")
    stage = raw.get("stage")
    if stage is not None and (not isinstance(stage, str) or not stage.strip()):
        raise ContestantSurfaceError(f"{where}.stage must be a non-blank string")
    when = _When() if raw.get("when") is None else _parse_when(raw.get("when"), f"{where}.when")
    return JobRule(job_id=job_id, stage=stage, when=when)


def _parse_when(raw: object, where: str) -> _When:
    if raw is None:
        return _When()
    if not isinstance(raw, Mapping):
        raise ContestantSurfaceError(f"{where} must be an object")
    _allowed(raw, {"has_operator", "has_relic", "has_group"}, where)
    return _When(
        has_operator=_optional_name(raw.get("has_operator"), f"{where}.has_operator"),
        has_relic=_optional_name(raw.get("has_relic"), f"{where}.has_relic"),
        has_group=_optional_name(raw.get("has_group"), f"{where}.has_group"),
    )


def _optional_name(raw: object, where: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ContestantSurfaceError(f"{where} must be a non-blank string")
    return raw


def _as_list(raw: object, where: str) -> list[object]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ContestantSurfaceError(f"{where} must be a list")
    return raw


def _as_name_list(raw: object, where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise ContestantSurfaceError(f"{where} must be a non-empty list of strings")
    names: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ContestantSurfaceError(f"{where}[{index}] must be a non-blank string")
        names.append(item)
    return names


def _allowed(raw: Mapping[str, object], allowed: set[str], where: str) -> None:
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ContestantSurfaceError(f"{where} contains unknown fields: {extra}")


class _ScriptApi:
    def __init__(
        self,
        context: RunContext,
        operator_offsets: dict[str, int],
        relic_offsets: dict[str, int],
        job_id: str | None,
    ) -> None:
        self._context = context
        self._operator_offsets = operator_offsets
        self._relic_offsets = relic_offsets
        self._job_id = job_id
        self._hooks: list[HookSpec] = []

    @property
    def stage(self) -> str | None:
        return self._context.stage

    @property
    def operators(self) -> tuple[str, ...]:
        return self._context.operators

    @property
    def relics(self) -> tuple[str, ...]:
        return self._context.relics

    def has_operator(self, name: str) -> bool:
        return self._context.has_operator(_script_name(name, "has_operator"))

    def has_relic(self, name: str) -> bool:
        return self._context.has_relic(_script_name(name, "has_relic"))

    def has_group(self, name: str) -> bool:
        return self._context.has_group(_script_name(name, "has_group"))

    def add_score_offset(self, operator: str, offset: int) -> None:
        self._operator_offsets[_script_name(operator, "add_score_offset")] += _script_int(offset)

    def add_group_offset(self, group: str, offset: int) -> None:
        name = _script_name(group, "add_group_offset")
        extra = _script_int(offset)
        for member in self._context.groups.get(name, ()):
            self._operator_offsets[member] += extra

    def add_relic_offset(self, relic: str, offset: int) -> None:
        self._relic_offsets[_script_name(relic, "add_relic_offset")] += _script_int(offset)

    def set_job(self, job_id: str) -> None:
        self._job_id = _script_name(job_id, "set_job")

    def register_hook(self, kind: str, **payload: object) -> None:
        self._hooks.append(HookSpec(kind=_script_name(kind, "register_hook"), payload=dict(payload)))


def _script_name(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContestantScriptError(f"{where} requires a non-blank string")
    return value


def _script_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContestantScriptError("offset must be an integer")
    return value


def _run_script(
    source: str,
    context: RunContext,
    *,
    operator_offsets: dict[str, int],
    relic_offsets: dict[str, int],
    job_id: str | None,
) -> tuple[str | None, tuple[HookSpec, ...]]:
    tree = ast.parse(source, filename="<contestant-script>", mode="exec")
    _validate_script_ast(tree)
    api = _ScriptApi(context, operator_offsets, relic_offsets, job_id)
    compiled = compile(tree, "<contestant-script>", "exec")
    exec(compiled, {"__builtins__": dict(_SAFE_BUILTINS), "api": api}, {})
    return api._job_id, tuple(api._hooks)


def _validate_script_ast(node: ast.AST) -> None:
    if not isinstance(node, _ALLOWED_AST):
        raise ContestantScriptError(f"forbidden syntax: {type(node).__name__}")
    if isinstance(node, ast.Attribute):
        if not isinstance(node.value, ast.Name) or node.value.id != "api":
            raise ContestantScriptError("only api.<method> attribute access is allowed")
        if node.attr not in API_METHODS:
            raise ContestantScriptError(f"unknown api method: {node.attr}")
    if isinstance(node, ast.Name) and node.id.startswith("_"):
        raise ContestantScriptError("private names are forbidden")
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            if func.id not in _SAFE_BUILTINS:
                raise ContestantScriptError(f"forbidden call: {func.id}")
        elif not isinstance(func, ast.Attribute):
            raise ContestantScriptError("only api methods and safe builtins may be called")
    for child in ast.iter_child_nodes(node):
        _validate_script_ast(child)

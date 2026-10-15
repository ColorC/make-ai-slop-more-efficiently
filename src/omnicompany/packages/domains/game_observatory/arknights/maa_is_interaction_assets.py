# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T16:35:00+08:00 type=asset-compiler status=active
"""Compile MAA Integrated Strategies UI tasks into interaction-only assets.

MAA's Roguelike task files contain two different things in the same graph:
recognition/click geometry and plugins/edges that can choose routes, event
answers, recruits, or shop items automatically.  The first-seen tournament
package may keep the former, but must never inherit the latter.

This compiler therefore resolves MAA template-task inheritance, keeps only a
closed set of recognition and direct-operation fields, and removes every task
graph edge.  The output is a trusted-adapter asset: contestants submit a
semantic action and current visible target id; they never invoke a raw MAA
task or author pixel coordinates.

The compiler is offline and read-only.  It does not start MAA, ADB, MuMu, or
the game client.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .is_shell import ISActionKind, ISScreenKind, legal_actions_for_screen

__all__ = [
    "InteractionAssetBundleV1",
    "MaaInteractionAssetError",
    "assert_maa_is_interaction_asset_bundle",
    "compile_maa_is_interaction_assets",
]


class MaaInteractionAssetError(ValueError):
    """A source asset is missing, ambiguous, executable, or knowledge-bearing."""


_SCHEMA = "omnicompany.arknights.maa-is-interaction-assets.v1"
_LICENSE = "AGPL-3.0-only"
_THEME_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_HEX_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")

_GRAPH_FIELDS = frozenset(
    {
        "baseTask",
        "sub",
        "next",
        "onErrorNext",
        "exceededNext",
        "reduceOtherTimes",
    }
)
_DOCUMENTATION_FIELDS = frozenset(
    {
        "Doc",
        "Doc2",
        "doc",
        "next_Doc",
        "specificRect_Doc",
        "rectMove_Doc",
        "specialParams_Doc",
        "specificRect_doc",
        "rectMove_doc",
        "specialParams_doc",
    }
)
_SAFE_FIELDS = frozenset(
    {
        "action",
        "algorithm",
        "fullMatch",
        "maskRange",
        "maxTimes",
        "ocrReplace",
        "postDelay",
        "preDelay",
        "rectMove",
        "roi",
        "specialParams",
        "specificRect",
        "templThreshold",
        "template",
        "text",
        "withoutDet",
    }
)
_SAFE_ALGORITHMS = frozenset({"JustReturn", "MatchTemplate", "OcrDetect", "FeatureMatch"})
_SAFE_ACTIONS = frozenset({"DoNothing", "ClickSelf", "ClickRect", "Stop", "Swipe"})
_FORBIDDEN_TASK_MARKERS = (
    "strategy",
    "routing",
    "judgeoption",
    "autodecision",
    "autoselect",
    "eventresult",
    "encounterresult",
    "outcome",
    "recommend",
    "walkthrough",
)
_FORBIDDEN_TASK_NAMES = frozenset(
    {
        "Roguelike@Stages",
        "Roguelike@StageEncounterOptions",
        "Roguelike@LastRewardRand",
        "Roguelike@LastReward-Strategy",
        "Roguelike@Squad-Random",
        "Roguelike@TraderRandomShopping",
    }
)


def _tasks(*names: str) -> tuple[str, ...]:
    return names


_ACTION_TASKS: dict[ISActionKind, tuple[str, ...]] = {
    ISActionKind.ENTER: _tasks(
        "Roguelike@FromIntegratedStrategies",
        "Roguelike@SelectTheme",
        "Roguelike@StartExplore",
    ),
    ISActionKind.RESUME: _tasks("Roguelike@Continue"),
    ISActionKind.SELECT_ROUTE_NODE: _tasks(
        "Roguelike@Stage",
        "Roguelike@StageEnter",
        "Roguelike@StageVertical",
        "Roguelike@StageVerticalConfirm",
    ),
    ISActionKind.SCROLL_ROUTE: _tasks("SlowlySwipeToTheLeft", "SlowlySwipeToTheRight"),
    ISActionKind.CHOOSE_EVENT_OPTION: tuple(
        f"Roguelike@OptionChoose{count}-{index}"
        for count in range(1, 5)
        for index in range(1, count + 1)
    ),
    ISActionKind.SCROLL_EVENT_OPTIONS: _tasks("SlowlySwipeToTheUp", "SlowlySwipeToTheDown"),
    ISActionKind.CONFIRM_EVENT: _tasks(
        "Roguelike@StageEncounterJudgeClick",
        "Roguelike@StageEncounterJudgeClick2",
        "Roguelike@CloseEvent",
    ),
    ISActionKind.CHOOSE_RECRUIT: _tasks(
        "RoguelikeRecruitOcr",
        "RoguelikeRecruitLazyClick1",
        "RoguelikeRecruitLazyClick2",
        "RoguelikeRecruitLazyClick3",
        "RoguelikeRecruitLazyClick4",
    ),
    ISActionKind.SCROLL_RECRUIT: _tasks(
        "RoguelikeRecruitOperListSlowlySwipeToTheLeft",
        "RoguelikeRecruitOperListSlowlySwipeToTheRight",
    ),
    ISActionKind.CONFIRM_RECRUIT: _tasks(
        "Roguelike@ChooseOperConfirm", "Roguelike@EnterAfterRecruit"
    ),
    ISActionKind.CANCEL_RECRUIT: _tasks("RoguelikeRecruit-GiveUp"),
    ISActionKind.BUY_SHOP_ITEM: _tasks("RoguelikeTraderShopping", "RoguelikeTraderShoppingOcr"),
    ISActionKind.SCROLL_SHOP: _tasks("SlowlySwipeToTheLeft", "SlowlySwipeToTheRight"),
    ISActionKind.CONFIRM_SHOP_PURCHASE: _tasks("Roguelike@TraderRandomShoppingConfirm"),
    ISActionKind.INVEST_SHOP: _tasks(
        "Roguelike@StageTraderInvestSystem",
        "Roguelike@StageTraderInvestSystemEnter",
        "Roguelike@StageTraderInvestConfirm",
        "Roguelike@StageTraderInvest-Confirm",
    ),
    ISActionKind.REFRESH_SHOP: _tasks(
        "Roguelike@StageTraderRefreshWithDice",
        "Roguelike@StageTraderRefreshWithDiceConfirm",
    ),
    ISActionKind.LEAVE_SHOP: _tasks(
        "Roguelike@StageTraderLeave", "Roguelike@StageTraderLeaveConfirm"
    ),
    ISActionKind.EDIT_SQUAD: _tasks("Roguelike@RolesDefault", "Roguelike@SquadDefault"),
    ISActionKind.SCROLL_SQUAD: _tasks("Roguelike@SquadSlowlySwipeToTheRight"),
    ISActionKind.CONFIRM_SQUAD: _tasks("Roguelike@SquadConfirm"),
    ISActionKind.CHOOSE_RELIC: _tasks("Roguelike@GetDropSelectReward"),
    ISActionKind.SCROLL_RELIC: _tasks(
        "RoguelikeFoldartalSlowlySwipeToTheUp",
        "RoguelikeFoldartalSwipeToTheDown",
    ),
    ISActionKind.CONFIRM_RELIC: _tasks("Roguelike@LastRewardConfirm", "Roguelike@CloseCollection"),
    ISActionKind.CHOOSE_REWARD: _tasks("Roguelike@GetDropSelect", "Roguelike@LastReward"),
    ISActionKind.CONFIRM_REWARD: _tasks(
        "Roguelike@GetDropCompleted", "Roguelike@LastRewardConfirm"
    ),
    ISActionKind.CONFIRM_DIALOG: _tasks(
        "Roguelike@DialogSkip",
        "Roguelike@DialogSkipConfirm",
        "Roguelike@CloseInterview",
    ),
    ISActionKind.GO_BACK: _tasks("Return"),
}


# This is deliberately an exact, reviewed inventory rather than a task-graph
# traversal.  The MAA theme file also contains route preferences, event answer
# machinery, and plugin configuration.  Matching a broad name prefix or
# following ``next``/``sub`` would silently import those decisions.
_SAMI_ROUTE_NODE_SUFFIXES = _tasks(
    "StageBoons",
    "StageBoonsAI6",
    "StageBoskyPassage",
    "StageBoskyPassageAI6",
    "StageCombatOps",
    "StageCombatOpsAI6",
    "StageDreadfulFoe",
    "StageDreadfulFoe-5",
    "StageEmergencyOps",
    "StageEmergencyOpsAI6",
    "StageEmergencyTransportation",
    "StageEmergencyTransportationAI6",
    "StageEncounter",
    "StageEncounterAI6",
    "StageFerociousPresage",
    "StageGambling",
    "StageGamblingAI6",
    "StageMysteriousPresage",
    "StageProphecy",
    "StageProphecyAI6",
    "StageSafeHouse",
    "StageSafeHouseAI6",
    "StageTrader",
    "StageTraderAI6",
    "StageVerticalBoons",
    "StageVerticalBoonsAI6",
    "StageVerticalBoskyPassage",
    "StageVerticalBoskyPassageAI6",
    "StageVerticalCombatOps",
    "StageVerticalCombatOpsAI6",
    "StageVerticalEmergencyOps",
    "StageVerticalEmergencyOpsAI6",
    "StageVerticalEmergencyTransportation",
    "StageVerticalEmergencyTransportationAI6",
    "StageVerticalEncounter",
    "StageVerticalEncounterAI6",
    "StageVerticalGambling",
    "StageVerticalGamblingAI6",
    "StageVerticalProphecy",
    "StageVerticalProphecyAI6",
    "StageVerticalSafeHouse",
    "StageVerticalSafeHouseAI6",
    "StageVerticalTrader",
    "StageVerticalTraderAI6",
    "StageVerticalWindAndRain",
    "StageVerticalWindAndRainAI6",
    "StageWindAndRain",
    "StageWindAndRainAI6",
)
_SAMI_ROUTE_ENTRY_SUFFIXES = _tasks(
    "StageBoonsEnter",
    "StageBoskyPassageEnter",
    "StageCombatOpsEnter",
    "StageDreadfulFoe-5Enter",
    "StageDreadfulFoeEnter",
    "StageEmergencyOpsEnter",
    "StageEncounterEnter",
    "StageFerociousPresageEnter",
    "StageMysteriousPresageEnter",
    "StageSafeHouseEnter",
    "StageTraderEnter",
    "StageVerticalConfirm",
)
_SAMI_ATOMIC_ALLOWLIST: dict[str, tuple[ISScreenKind, tuple[str, ...]]] = {
    "route_nodes": (ISScreenKind.ROUTE, _SAMI_ROUTE_NODE_SUFFIXES),
    "route_entry_controls": (ISScreenKind.ROUTE, _SAMI_ROUTE_ENTRY_SUFFIXES),
    "event": (
        ISScreenKind.EVENT,
        _tasks(
            "StageEncounterEnter",
            "StageEncounterLeaveConfirm",
            "StageEncounterSpecialClose",
        ),
    ),
    "recruit": (
        ISScreenKind.RECRUIT,
        _tasks(
            "RecruitMain",
            "RecruitOther",
            "GetDropRecruit",
            "GetDropSelectRecruit",
        ),
    ),
    "shop": (
        ISScreenKind.SHOP,
        _tasks("TraderCheck", "StageTraderEnter", "StageTraderRefresh"),
    ),
    "squad": (ISScreenKind.SQUAD, _tasks("RolesConfirm")),
    "reward": (
        ISScreenKind.REWARD,
        _tasks(
            "DropsFlag",
            "GetDrop1",
            "GetDrop2",
            "GetDrop3",
            "GetDrop4",
            "GetDropLeave",
            "GetDropRecruit",
            "GetDropSelectRecruit",
            "GetDropSelectReward2",
            "LastReward2",
            "LastReward3",
            "LastReward4",
            "LastRewardChoose",
        ),
    ),
    "relic_and_foldartal": (
        ISScreenKind.RELIC,
        _tasks(
            "CloseCollectionClose",
            "Foldartal",
            "FoldartalBack",
            "FoldartalGain",
            "FoldartalGainOcr",
            "FoldartalGainOcrAfterCombat",
            "FoldartalGainOcrNextLevel",
            "FoldartalUseConfirm",
            "FoldartalUseOcr",
            "FoldartalUseOnStageBattle",
            "FoldartalUseOnStageBoss",
            "FoldartalUseOnStageEncounter",
            "FoldartalUseOnStageGambling",
            "FoldartalUseOnStageTrader",
        ),
    ),
    "sami_collapsal_panel": (
        ISScreenKind.ROUTE,
        _tasks(
            "CheckCollapsalParadigms",
            "CheckCollapsalParadigmsBanner",
            "CheckCollapsalParadigmsOnBanner",
            "CheckCollapsalParadigmsOnPanel",
            "CheckCollapsalParadigmsOnStage",
            "CheckCollapsalParadigmsPanel",
            "SwipeCollapsalParadigmPanelUp",
            "ToggleCollapsalParadigmPanel",
        ),
    ),
}
_THEME_ATOMIC_ALLOWLISTS = {"Sami": _SAMI_ATOMIC_ALLOWLIST}
_SAMI_ROUTE_NODE_RE = re.compile(
    r"Stage(?:Vertical)?(?:Boons|BoskyPassage|CombatOps|DreadfulFoe(?:-5)?|"
    r"EmergencyOps|EmergencyTransportation|Encounter|FerociousPresage|Gambling|"
    r"MysteriousPresage|Prophecy|SafeHouse|Trader|WindAndRain)(?:AI6)?\Z"
)


@dataclass(frozen=True, slots=True)
class InteractionAssetBundleV1:
    """Deterministic interaction-only manifest for the trusted I/O adapter."""

    payload: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return json.loads(self.to_json())

    def to_json(self) -> str:
        return json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


def _bundle_object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MaaInteractionAssetError(f"{name} must be an object")
    return value


def _bundle_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise MaaInteractionAssetError(f"{name} must be a list")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise MaaInteractionAssetError(
            f"{name} fields drifted: missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _safe_relative_posix_path(value: object, name: str, *, suffix: str | None = None) -> str:
    if not isinstance(value, str):
        raise MaaInteractionAssetError(f"{name} must be a string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or path.as_posix() != value
        or any(part.endswith((" ", ".")) for part in path.parts)
    ):
        raise MaaInteractionAssetError(f"{name} is not a canonical relative POSIX path")
    if suffix is not None and path.suffix.casefold() != suffix.casefold():
        raise MaaInteractionAssetError(f"{name} must end in {suffix}")
    return value


def assert_maa_is_interaction_asset_bundle(
    value: Mapping[str, object],
    *,
    expected_theme: str | None = None,
    expected_client_version: str | None = None,
    require_activatable: bool = False,
) -> None:
    """Revalidate an untrusted serialized interaction bundle.

    The compiler constructs this schema, while this function is the release
    boundary.  It accepts exactly the interaction-only fields produced by the
    compiler and rejects unknown payload containers, missing semantic actions,
    graph edges, stale target pins, or an unapproved license record.
    """

    _exact_keys(
        value,
        {
            "schema",
            "source",
            "target",
            "target_theme_atomic_allowlist",
            "trust_boundary",
            "bindings",
            "task_clips",
            "templates",
            "placeholder_tasks",
            "activation_ready",
            "pending_reasons",
        },
        "MAA interaction bundle",
    )
    if value.get("schema") != _SCHEMA:
        raise MaaInteractionAssetError("unsupported MAA interaction bundle schema")

    source = _bundle_object(value["source"], "source")
    _exact_keys(
        source,
        {
            "project",
            "source_commit",
            "declared_license",
            "license_review_approved",
            "license_review_record_sha256",
            "files",
        },
        "source",
    )
    if source.get("project") != "MaaAssistantArknights":
        raise MaaInteractionAssetError("unexpected MAA source project")
    commit = source.get("source_commit")
    if not isinstance(commit, str) or not _HEX_RE.fullmatch(commit):
        raise MaaInteractionAssetError("MAA source commit is not pinned")
    if source.get("declared_license") != _LICENSE:
        raise MaaInteractionAssetError("unexpected MAA declared license")
    approved = source.get("license_review_approved")
    review_hash = source.get("license_review_record_sha256")
    if not isinstance(approved, bool):
        raise MaaInteractionAssetError("license_review_approved must be boolean")
    if approved:
        if not isinstance(review_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", review_hash):
            raise MaaInteractionAssetError("approved MAA assets require a review record hash")
    elif review_hash is not None:
        raise MaaInteractionAssetError("unapproved MAA assets cannot name a review record")
    files = _bundle_list(source["files"], "source.files")
    if not files:
        raise MaaInteractionAssetError("MAA source file inventory must not be empty")
    source_paths: set[str] = set()
    for index, raw in enumerate(files):
        item = _bundle_object(raw, f"source.files[{index}]")
        _exact_keys(item, {"path", "sha256"}, f"source.files[{index}]")
        path = _safe_relative_posix_path(item["path"], f"source.files[{index}].path")
        digest = item.get("sha256")
        if path in source_paths:
            raise MaaInteractionAssetError(f"duplicate MAA source file path: {path}")
        source_paths.add(path)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MaaInteractionAssetError("MAA source file hash is invalid")

    target = _bundle_object(value["target"], "target")
    _exact_keys(target, {"theme", "client_version"}, "target")
    theme = target.get("theme")
    client = target.get("client_version")
    if theme is not None and not isinstance(theme, str):
        raise MaaInteractionAssetError("MAA target theme must be a string or null")
    if client is not None and not isinstance(client, str):
        raise MaaInteractionAssetError("MAA client version must be a string or null")
    if expected_theme is not None and theme != expected_theme:
        raise MaaInteractionAssetError("MAA target theme does not match the frozen season")
    if expected_client_version is not None and client != expected_client_version:
        raise MaaInteractionAssetError("MAA client version does not match the frozen season")

    expected_trust = {
        "consumer": "host_trusted_io_adapter_only",
        "contestant_receives": "current_visible_semantic_targets",
        "contestant_authors_coordinates": False,
        "task_graph_edges_included": False,
        "decision_plugins_included": False,
        "knowledge_payloads_included": False,
    }
    if dict(_bundle_object(value["trust_boundary"], "trust_boundary")) != expected_trust:
        raise MaaInteractionAssetError("MAA interaction trust boundary was widened")

    clips = _bundle_list(value["task_clips"], "task_clips")
    clip_ids: set[str] = set()
    for index, raw in enumerate(clips):
        clip = _bundle_object(raw, f"task_clips[{index}]")
        _exact_keys(
            clip,
            {"task_id", "screen", "source_scope", "inheritance_chain", "payload"},
            f"task_clips[{index}]",
        )
        task_id = clip.get("task_id")
        if not isinstance(task_id, str):
            raise MaaInteractionAssetError("MAA task id must be a string")
        _safe_task_name(task_id)
        if task_id in clip_ids:
            raise MaaInteractionAssetError(f"duplicate MAA task clip: {task_id}")
        clip_ids.add(task_id)
        screen = clip.get("screen")
        if not isinstance(screen, str) or screen not in {item.value for item in ISScreenKind} - {
            "unknown"
        }:
            raise MaaInteractionAssetError(f"invalid MAA task clip screen: {task_id}")
        _safe_relative_posix_path(clip["source_scope"], f"{task_id}.source_scope")
        chain = _bundle_list(clip["inheritance_chain"], f"{task_id}.inheritance_chain")
        if not chain or any(not isinstance(item, str) or not item for item in chain):
            raise MaaInteractionAssetError(f"invalid MAA inheritance chain: {task_id}")
        payload = _bundle_object(clip["payload"], f"{task_id}.payload")
        if _sanitize_task(task_id, payload) != dict(payload):
            raise MaaInteractionAssetError(f"unsanitized MAA task payload: {task_id}")

    bindings = _bundle_list(value["bindings"], "bindings")
    expected_pairs = {
        (screen.value, action.value)
        for screen in ISScreenKind
        if screen is not ISScreenKind.UNKNOWN
        for action in legal_actions_for_screen(screen)
    }
    observed_pairs: set[tuple[str, str]] = set()
    for index, raw in enumerate(bindings):
        binding = _bundle_object(raw, f"bindings[{index}]")
        _exact_keys(
            binding,
            {
                "screen",
                "semantic_action",
                "source_tasks",
                "selection_authority",
                "raw_task_invocation_exposed",
            },
            f"bindings[{index}]",
        )
        screen = binding.get("screen")
        action = binding.get("semantic_action")
        if not isinstance(screen, str) or not isinstance(action, str):
            raise MaaInteractionAssetError("MAA binding screen/action must be strings")
        pair = (screen, action)
        if pair in observed_pairs:
            raise MaaInteractionAssetError(f"duplicate MAA semantic binding: {pair}")
        observed_pairs.add(pair)
        tasks = _bundle_list(binding["source_tasks"], f"bindings[{index}].source_tasks")
        if not tasks or any(not isinstance(task, str) or task not in clip_ids for task in tasks):
            raise MaaInteractionAssetError(f"MAA binding references an unknown task: {pair}")
        if binding.get("selection_authority") != "agent_current_visible_choice_only":
            raise MaaInteractionAssetError(f"MAA binding widened selection authority: {pair}")
        if binding.get("raw_task_invocation_exposed") is not False:
            raise MaaInteractionAssetError(f"MAA binding exposes raw tasks: {pair}")
    if observed_pairs != expected_pairs:
        raise MaaInteractionAssetError(
            "MAA semantic interaction surface is incomplete or contains extra actions"
        )

    templates = _bundle_list(value["templates"], "templates")
    template_names: set[str] = set()
    template_paths: set[str] = set()
    any_placeholder = False
    for index, raw in enumerate(templates):
        template = _bundle_object(raw, f"templates[{index}]")
        _exact_keys(template, {"name", "path", "sha256", "placeholder"}, f"templates[{index}]")
        name = template.get("name")
        if not isinstance(name, str):
            raise MaaInteractionAssetError("MAA template name must be a string")
        path = _safe_relative_posix_path(
            template["path"], f"templates[{index}].path", suffix=".png"
        )
        if name in template_names or not name or Path(name).name != name:
            raise MaaInteractionAssetError(f"invalid or duplicate MAA template name: {name}")
        template_names.add(name)
        if path in template_paths:
            raise MaaInteractionAssetError(f"duplicate MAA template path: {path}")
        template_paths.add(path)
        digest = template.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MaaInteractionAssetError(f"invalid MAA template hash: {name}")
        if not isinstance(template.get("placeholder"), bool):
            raise MaaInteractionAssetError(f"MAA template placeholder flag is invalid: {name}")
        any_placeholder = any_placeholder or bool(template["placeholder"])

    placeholder_tasks = _bundle_list(value["placeholder_tasks"], "placeholder_tasks")
    if any(not isinstance(item, str) or item not in clip_ids for item in placeholder_tasks):
        raise MaaInteractionAssetError("MAA placeholder task inventory is invalid")
    if bool(placeholder_tasks) != any_placeholder:
        raise MaaInteractionAssetError("MAA placeholder task/template inventory disagrees")

    allowlist = _bundle_object(
        value["target_theme_atomic_allowlist"], "target_theme_atomic_allowlist"
    )
    _exact_keys(
        allowlist,
        {"mode", "route_node_name_discovery", "task_graph_traversal", "categories"},
        "target_theme_atomic_allowlist",
    )
    if allowlist.get("task_graph_traversal") is not False:
        raise MaaInteractionAssetError("MAA theme allowlist enabled task graph traversal")
    categories = _bundle_object(allowlist["categories"], "allowlist.categories")
    for category, raw in categories.items():
        item = _bundle_object(raw, f"allowlist.categories.{category}")
        _exact_keys(item, {"screen", "source_tasks"}, f"allowlist.categories.{category}")
        tasks = _bundle_list(item["source_tasks"], f"allowlist.categories.{category}.source_tasks")
        if any(not isinstance(task, str) or task not in clip_ids for task in tasks):
            raise MaaInteractionAssetError(
                f"allowlist category references unknown tasks: {category}"
            )

    expected_reasons: list[str] = []
    if theme is None or client is None:
        expected_reasons.append("target_theme_and_client_not_frozen")
    if placeholder_tasks:
        expected_reasons.append("placeholder_templates_require_target_calibration")
    if not approved:
        expected_reasons.append("maa_redistribution_review_pending")
    pending = _bundle_list(value["pending_reasons"], "pending_reasons")
    if pending != expected_reasons:
        raise MaaInteractionAssetError("MAA pending reasons do not match bundle facts")
    if value.get("activation_ready") is not (not expected_reasons):
        raise MaaInteractionAssetError("MAA activation flag does not match bundle facts")
    if require_activatable and expected_reasons:
        raise MaaInteractionAssetError(f"MAA interaction bundle is not activatable: {pending}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MaaInteractionAssetError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise MaaInteractionAssetError(f"non-finite JSON number is forbidden: {value}")


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise MaaInteractionAssetError(f"cannot read MAA task source: {path}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaaInteractionAssetError(f"MAA task source is not UTF-8: {path}") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except json.JSONDecodeError as exc:
        raise MaaInteractionAssetError(f"invalid MAA task JSON: {path}") from exc
    if not isinstance(value, dict):
        raise MaaInteractionAssetError(f"MAA task root must be an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MaaInteractionAssetError(f"cannot hash MAA asset: {path}") from exc


def _task_object(value: object, *, source: Path, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MaaInteractionAssetError(f"MAA task must be an object: {source}:{name}")
    return value


class _TaskResolver:
    def __init__(self, task_sources: Sequence[tuple[Path, dict[str, object]]]) -> None:
        self._tasks: dict[str, dict[str, object]] = {}
        self._scope_by_task: dict[str, str] = {}
        self._cache: dict[str, tuple[dict[str, object], tuple[str, ...]]] = {}
        for path, payload in task_sources:
            for name, raw_task in payload.items():
                if not isinstance(name, str) or not name:
                    raise MaaInteractionAssetError(f"invalid MAA task name in {path}")
                task = _task_object(raw_task, source=path, name=name)
                if name in self._tasks and "baseTask" in task:
                    self._tasks[name] = dict(task)
                elif name in self._tasks:
                    self._tasks[name].update(task)
                else:
                    self._tasks[name] = dict(task)
                self._scope_by_task[name] = path.as_posix()

    def source_scope(self, task_name: str) -> str:
        if task_name in self._scope_by_task:
            return self._scope_by_task[task_name]
        if "@" in task_name:
            return self.source_scope(task_name.split("@", 1)[1])
        raise MaaInteractionAssetError(f"unknown MAA interaction task: {task_name}")

    def resolve(self, task_name: str) -> tuple[dict[str, object], tuple[str, ...]]:
        return self._resolve(task_name, ())

    def _resolve(
        self, task_name: str, stack: tuple[str, ...]
    ) -> tuple[dict[str, object], tuple[str, ...]]:
        if task_name in self._cache:
            payload, chain = self._cache[task_name]
            return dict(payload), chain
        if task_name in stack:
            raise MaaInteractionAssetError(
                f"cyclic MAA task inheritance: {' -> '.join((*stack, task_name))}"
            )

        explicit = task_name in self._tasks
        raw = dict(self._tasks.get(task_name, {}))
        raw_base = raw.get("baseTask")
        if raw_base is not None and not isinstance(raw_base, str):
            raise MaaInteractionAssetError(f"baseTask must be a string: {task_name}")
        if raw_base == "#none":
            base_name = None
        elif isinstance(raw_base, str):
            if not raw_base or any(token in raw_base for token in ("+", "^", "*", "#")):
                raise MaaInteractionAssetError(
                    f"interaction task requires one concrete baseTask: {task_name}"
                )
            base_name = raw_base
        elif "@" in task_name:
            implicit_base = task_name.split("@", 1)[1]
            # Some MAA files use an ``@`` in a fully declared task name even
            # though the suffix is not itself a task.  It is a template task
            # only when that suffix can actually resolve.
            if not explicit or implicit_base in self._tasks:
                base_name = implicit_base
            else:
                base_name = None
        else:
            base_name = None

        if not explicit and base_name is None:
            raise MaaInteractionAssetError(f"unknown MAA interaction task: {task_name}")

        inherited: dict[str, object] = {}
        chain: tuple[str, ...] = ()
        if base_name is not None:
            inherited, chain = self._resolve(base_name, (*stack, task_name))
        merged = {**inherited, **raw}
        merged.pop("baseTask", None)

        algorithm = merged.get("algorithm", "MatchTemplate")
        if not isinstance(algorithm, str) or algorithm not in _SAFE_ALGORITHMS:
            raise MaaInteractionAssetError(
                f"unsafe recognition algorithm on interaction task {task_name}: {algorithm!r}"
            )
        action = merged.get("action", "DoNothing")
        if not isinstance(action, str) or action not in _SAFE_ACTIONS:
            raise MaaInteractionAssetError(
                f"unsafe process action on interaction task {task_name}: {action!r}"
            )
        merged["algorithm"] = algorithm
        merged["action"] = action

        is_derived = explicit and (raw_base is not None or "@" in task_name)
        if is_derived and algorithm == "MatchTemplate" and "template" not in raw:
            merged["template"] = f"{task_name}.png"

        result = (merged, (*chain, task_name))
        self._cache[task_name] = (dict(merged), result[1])
        return dict(merged), result[1]


def _safe_task_name(task_name: str) -> None:
    unprefixed = (
        task_name
        if task_name.startswith("Roguelike@")
        else task_name.split("@", 1)[1]
        if "@" in task_name
        else task_name
    )
    lowered = unprefixed.casefold()
    if unprefixed in _FORBIDDEN_TASK_NAMES or any(
        marker in lowered for marker in _FORBIDDEN_TASK_MARKERS
    ):
        raise MaaInteractionAssetError(
            f"knowledge-bearing or automatic MAA task is forbidden: {task_name}"
        )


def _sanitize_task(task_name: str, payload: Mapping[str, object]) -> dict[str, object]:
    _safe_task_name(task_name)
    unknown = sorted(
        key
        for key in payload
        if key not in _SAFE_FIELDS and key not in _GRAPH_FIELDS and key not in _DOCUMENTATION_FIELDS
    )
    if unknown:
        raise MaaInteractionAssetError(
            f"unreviewed fields on MAA interaction task {task_name}: {unknown}"
        )
    sanitized = {key: value for key, value in payload.items() if key in _SAFE_FIELDS}
    encoded = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    lowered = encoded.casefold()
    if any(token in lowered for token in ('"script"', '"plugin"', '"eval"', '"code"')):
        raise MaaInteractionAssetError(
            f"executable field escaped the MAA interaction sanitizer: {task_name}"
        )
    return sanitized


def _effective_task_name(task_name: str, theme: str | None) -> str:
    if theme is None or not task_name.startswith("Roguelike@"):
        return task_name
    return f"{theme}@{task_name}"


def _template_names(payload: Mapping[str, object], task_name: str) -> tuple[str, ...]:
    raw = payload.get("template")
    if raw is None:
        return ()
    if isinstance(raw, str):
        names = (raw,)
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        names = tuple(raw)
    else:
        raise MaaInteractionAssetError(f"template must be a string or string array: {task_name}")
    if any(not item or Path(item).name != item for item in names):
        raise MaaInteractionAssetError(
            f"template names must be traversal-free basenames: {task_name}"
        )
    return names


def _template_index(template_root: Path) -> dict[str, tuple[Path, ...]]:
    if not template_root.is_dir():
        raise MaaInteractionAssetError(f"MAA template directory is missing: {template_root}")
    indexed: dict[str, list[Path]] = {}
    for path in sorted(template_root.rglob("*.png")):
        indexed.setdefault(path.name, []).append(path)
    return {name: tuple(paths) for name, paths in indexed.items()}


def _resolve_template(
    name: str,
    *,
    template_root: Path,
    index: Mapping[str, tuple[Path, ...]],
    theme: str | None,
) -> Path:
    candidates = list(index.get(name, ()))
    if not candidates:
        raise MaaInteractionAssetError(f"MAA interaction template is missing: {name}")
    if len(candidates) == 1:
        return candidates[0]
    preferred_parts = []
    if theme is not None:
        preferred_parts.append(("Roguelike", theme))
    preferred_parts.append(("Roguelike", "base"))
    for required in preferred_parts:
        matched = [path for path in candidates if all(part in path.parts for part in required)]
        if len(matched) == 1:
            return matched[0]
    relative = [path.relative_to(template_root).as_posix() for path in candidates]
    raise MaaInteractionAssetError(f"MAA interaction template is ambiguous: {name}: {relative}")


def _normalized_extras(
    extras: Mapping[ISScreenKind, Sequence[str]] | None,
) -> dict[ISScreenKind, tuple[str, ...]]:
    if extras is None:
        return {}
    normalized: dict[ISScreenKind, tuple[str, ...]] = {}
    for screen, task_names in extras.items():
        if screen in {ISScreenKind.UNKNOWN, ISScreenKind.ENTRY}:
            raise MaaInteractionAssetError(
                f"extra MAA tasks require a concrete non-battle screen: {screen.value}"
            )
        names = tuple(task_names)
        if not names or any(not isinstance(name, str) or not name for name in names):
            raise MaaInteractionAssetError(f"invalid extra MAA task list: {screen.value}")
        for name in names:
            _safe_task_name(name)
        normalized[screen] = names
    return normalized


def _target_theme_atomic_allowlist(
    theme: str,
    theme_payload: Mapping[str, object],
) -> dict[str, tuple[ISScreenKind, tuple[str, ...]]]:
    raw_allowlist = _THEME_ATOMIC_ALLOWLISTS.get(theme)
    if raw_allowlist is None:
        raise MaaInteractionAssetError(
            f"target theme has no reviewed atomic interaction allowlist: {theme}"
        )

    # Route-node names are the one family where omissions can make the shell
    # unusable while still looking superficially complete.  Discover this
    # narrowly defined visual family and require exact equality with the
    # reviewed set.  No graph field participates in discovery.
    if theme == "Sami":
        prefix = f"{theme}@Roguelike@"
        discovered_route_suffixes = {
            name.removeprefix(prefix)
            for name in theme_payload
            if isinstance(name, str)
            and name.startswith(prefix)
            and _SAMI_ROUTE_NODE_RE.fullmatch(name.removeprefix(prefix))
        }
        expected_route_suffixes = set(_SAMI_ROUTE_NODE_SUFFIXES)
        if discovered_route_suffixes != expected_route_suffixes:
            missing = sorted(expected_route_suffixes - discovered_route_suffixes)
            unreviewed = sorted(discovered_route_suffixes - expected_route_suffixes)
            raise MaaInteractionAssetError(
                "target theme route-node inventory drifted from the reviewed "
                f"allowlist: missing={missing}, unreviewed={unreviewed}"
            )

    normalized: dict[str, tuple[ISScreenKind, tuple[str, ...]]] = {}
    for category, (screen, suffixes) in raw_allowlist.items():
        task_names = tuple(f"{theme}@Roguelike@{suffix}" for suffix in suffixes)
        for task_name in task_names:
            _safe_task_name(task_name)
            if task_name not in theme_payload:
                raise MaaInteractionAssetError(
                    f"reviewed target-theme atomic task is missing: {category}:{task_name}"
                )
        normalized[category] = (screen, task_names)
    return normalized


def _source_descriptor(path: Path, resource_root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(resource_root).as_posix(),
        "sha256": _sha256_file(path),
    }


def compile_maa_is_interaction_assets(
    resource_root: str | Path,
    *,
    source_commit: str,
    target_theme: str | None = None,
    target_client_version: str | None = None,
    license_review_approved: bool = False,
    license_review_record_sha256: str | None = None,
    extra_task_names_by_screen: Mapping[ISScreenKind, Sequence[str]] | None = None,
) -> InteractionAssetBundleV1:
    """Build a deterministic, graph-free UI asset manifest from pinned MAA.

    ``extra_task_names_by_screen`` is for judge-reviewed target-specific UI
    controls (for example a theme's relic panel).  It cannot add task-graph
    fields or known strategy/routing/plugin tasks.  A package remains
    non-activatable until theme, client version, template calibration, and
    redistribution review are all complete.
    """

    if not _HEX_RE.fullmatch(source_commit):
        raise MaaInteractionAssetError("MAA source_commit must be a 40/64-char hex pin")
    if target_theme is not None and not _THEME_RE.fullmatch(target_theme):
        raise MaaInteractionAssetError("target_theme contains unsafe characters")
    if target_client_version is not None and not target_client_version.strip():
        raise MaaInteractionAssetError("target_client_version must not be blank")
    if license_review_approved:
        if license_review_record_sha256 is None or not re.fullmatch(
            r"[0-9a-f]{64}", license_review_record_sha256
        ):
            raise MaaInteractionAssetError(
                "license approval requires a signed review record SHA-256"
            )
    elif license_review_record_sha256 is not None:
        raise MaaInteractionAssetError(
            "license review record cannot be attached without explicit approval"
        )

    root = Path(resource_root).resolve()
    generic_path = root / "tasks/tasks.json"
    base_path = root / "tasks/Roguelike/base.json"
    sources = [
        (generic_path, _read_json_object(generic_path)),
        (base_path, _read_json_object(base_path)),
    ]
    source_paths = [generic_path, base_path]
    theme_atomic_allowlist: dict[str, tuple[ISScreenKind, tuple[str, ...]]] = {}
    if target_theme is not None:
        theme_path = root / f"tasks/Roguelike/{target_theme}.json"
        theme_payload = _read_json_object(theme_path)
        theme_atomic_allowlist = _target_theme_atomic_allowlist(target_theme, theme_payload)
        sources.append((theme_path, theme_payload))
        source_paths.append(theme_path)

    resolver = _TaskResolver(sources)
    extras = _normalized_extras(extra_task_names_by_screen)
    bindings: list[dict[str, object]] = []
    requested_tasks: dict[str, ISScreenKind] = {}
    for screen in ISScreenKind:
        if screen is ISScreenKind.UNKNOWN:
            continue
        for action in sorted(legal_actions_for_screen(screen), key=lambda item: item.value):
            if action is ISActionKind.SELECT_ROUTE_NODE and theme_atomic_allowlist:
                source_tasks = tuple(
                    task_name
                    for category in ("route_nodes", "route_entry_controls")
                    for task_name in theme_atomic_allowlist[category][1]
                )
                effective = source_tasks
            else:
                source_tasks = _ACTION_TASKS.get(action)
                effective = tuple(
                    _effective_task_name(name, target_theme) for name in source_tasks or ()
                )
            if not source_tasks:
                raise MaaInteractionAssetError(
                    f"semantic action has no trusted MAA binding: {action.value}"
                )
            for task_name in effective:
                requested_tasks.setdefault(task_name, screen)
            bindings.append(
                {
                    "screen": screen.value,
                    "semantic_action": action.value,
                    "source_tasks": list(effective),
                    "selection_authority": "agent_current_visible_choice_only",
                    "raw_task_invocation_exposed": False,
                }
            )

    for _, (screen, task_names) in theme_atomic_allowlist.items():
        for task_name in task_names:
            requested_tasks.setdefault(task_name, screen)

    for screen, task_names in sorted(extras.items(), key=lambda item: item[0].value):
        for task_name in task_names:
            requested_tasks.setdefault(task_name, screen)

    task_clips: list[dict[str, object]] = []
    template_names: set[str] = set()
    placeholder_tasks: list[str] = []
    for task_name, screen in sorted(requested_tasks.items()):
        payload, inheritance_chain = resolver.resolve(task_name)
        sanitized = _sanitize_task(task_name, payload)
        names = _template_names(sanitized, task_name)
        template_names.update(names)
        if "empty.png" in names:
            placeholder_tasks.append(task_name)
        task_clips.append(
            {
                "task_id": task_name,
                "screen": screen.value,
                "source_scope": Path(resolver.source_scope(task_name)).relative_to(root).as_posix(),
                "inheritance_chain": list(inheritance_chain),
                "payload": sanitized,
            }
        )

    index = _template_index(root / "template")
    template_assets: list[dict[str, object]] = []
    for name in sorted(template_names):
        path = _resolve_template(
            name,
            template_root=root / "template",
            index=index,
            theme=target_theme,
        )
        template_assets.append(
            {
                "name": name,
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
                "placeholder": name == "empty.png",
            }
        )

    pending_reasons: list[str] = []
    if target_theme is None or target_client_version is None:
        pending_reasons.append("target_theme_and_client_not_frozen")
    if placeholder_tasks:
        pending_reasons.append("placeholder_templates_require_target_calibration")
    if not license_review_approved:
        pending_reasons.append("maa_redistribution_review_pending")

    payload: dict[str, object] = {
        "schema": _SCHEMA,
        "source": {
            "project": "MaaAssistantArknights",
            "source_commit": source_commit,
            "declared_license": _LICENSE,
            "license_review_approved": license_review_approved,
            "license_review_record_sha256": license_review_record_sha256,
            "files": [
                _source_descriptor(path, root)
                for path in sorted(source_paths, key=lambda item: item.as_posix())
            ],
        },
        "target": {
            "theme": target_theme,
            "client_version": target_client_version,
        },
        "target_theme_atomic_allowlist": {
            "mode": "exact_reviewed_closed_set" if target_theme is not None else None,
            "route_node_name_discovery": target_theme is not None,
            "task_graph_traversal": False,
            "categories": {
                category: {
                    "screen": screen.value,
                    "source_tasks": list(task_names),
                }
                for category, (screen, task_names) in theme_atomic_allowlist.items()
            },
        },
        "trust_boundary": {
            "consumer": "host_trusted_io_adapter_only",
            "contestant_receives": "current_visible_semantic_targets",
            "contestant_authors_coordinates": False,
            "task_graph_edges_included": False,
            "decision_plugins_included": False,
            "knowledge_payloads_included": False,
        },
        "bindings": bindings,
        "task_clips": task_clips,
        "templates": template_assets,
        "placeholder_tasks": sorted(placeholder_tasks),
        "activation_ready": not pending_reasons,
        "pending_reasons": pending_reasons,
    }
    return InteractionAssetBundleV1(payload=payload)

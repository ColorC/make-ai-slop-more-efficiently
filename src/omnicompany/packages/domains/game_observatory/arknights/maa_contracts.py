# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T06:25:00Z type=operation-contracts status=active
# [OMNI] summary="从 MAA 文档批量生成参数 schema，并用源码纠偏绑定风险、效果与恢复契约。"
# [OMNI] why="发现语义不等于可安全调用；代表长链需要闭门参数校验、显式授权和独立效果判据。"
# [OMNI] tags=game-observatory,arknights,maa,contracts,safety,effect-oracle
"""MAA 公开任务和战斗动作的统一调用契约。

参数字段从固定版本 ``integration.md`` 批量解析；文档与 MaaCore 不一致时应用有来源的
源码纠偏。契约只编译调用，不执行，也不把 ``contract_only`` 冒充动态效果验证。
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from .maa_semantics import (
    MaaSemanticRegistry,
    MaaSemanticValidationError,
    TaskExpression,
    UnknownMaaSemanticError,
    parse_task_expression,
)

__all__ = [
    "BattleActionCall",
    "ContractKind",
    "EffectContract",
    "EffectEvidence",
    "MaaContractCatalog",
    "MaaContractValidationError",
    "OperationAuthorization",
    "OperationContract",
    "ParameterSpec",
    "PublicTaskCall",
    "RecoveryContract",
    "RecoveryMode",
    "RiskLevel",
    "TaskParameterSchema",
    "build_maa_contract_catalog",
    "compile_battle_action",
    "compile_public_task_call",
    "parse_integration_parameter_schemas",
]


class MaaContractValidationError(ValueError):
    """调用不满足静态参数、安全或语义契约。"""


class ContractKind(str, Enum):
    PUBLIC_TASK = "public_task"
    BATTLE_ACTION = "battle_action"


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    ACCOUNT_STATE = "account_state"
    GAME_RESOURCE = "game_resource"
    HIGH = "high"


class EffectEvidence(str, Enum):
    FRESH_FRAME = "fresh_frame"
    FRESH_FRAME_AND_EVENT = "fresh_frame_and_event"
    PROCESS_STATE = "process_state"
    OFFLINE_ARTIFACT = "offline_artifact"
    DELAYED_BEHAVIOR = "delayed_behavior"
    DECLARED_EVENT = "declared_event"


class RecoveryMode(str, Enum):
    REOBSERVE = "reobserve"
    RETRY_SAME = "retry_same"
    RELOCALIZE = "relocalize"
    BACKTRACK = "backtrack"
    RESTART_TASK = "restart_task"
    RESTART_APP = "restart_app"
    STOP_AND_REPORT = "stop_and_report"


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    json_type: str
    required: bool
    default_raw: str | None
    deprecated: bool
    allowed_values: tuple[Any, ...]
    source: str

    def validate(self, value: Any) -> None:
        kind = self.json_type
        valid = False
        if kind == "boolean":
            valid = isinstance(value, bool)
        elif kind == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif kind == "string":
            valid = isinstance(value, str)
        elif kind == "object":
            valid = isinstance(value, dict)
        elif kind == "array":
            valid = isinstance(value, list)
        elif kind.startswith("array<") and kind.endswith(">"):
            item_kind = kind[6:-1]
            valid = isinstance(value, list) and all(_json_item_matches(item, item_kind) for item in value)
        if not valid:
            raise MaaContractValidationError(
                f"parameter {self.name!r} must be {self.json_type}, got {type(value).__name__}"
            )
        if self.allowed_values and value not in self.allowed_values:
            raise MaaContractValidationError(
                f"parameter {self.name!r} must be one of {self.allowed_values!r}, got {value!r}"
            )


def _json_item_matches(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    return True


@dataclass(frozen=True, slots=True)
class ConditionalRequirement:
    field: str
    equals: Any
    required_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskParameterSchema:
    task_name: str
    parameters: tuple[ParameterSpec, ...]
    exactly_one_of: tuple[tuple[str, ...], ...] = ()
    conditional_requirements: tuple[ConditionalRequirement, ...] = ()
    corrections: tuple[str, ...] = ()

    def require_parameter(self, name: str) -> ParameterSpec:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        raise UnknownMaaSemanticError(f"unknown parameter for {self.task_name}: {name}")

    def validate(self, params: dict[str, Any]) -> tuple[str, ...]:
        if not isinstance(params, dict):
            raise MaaContractValidationError(f"{self.task_name} params must be an object")
        known = {parameter.name: parameter for parameter in self.parameters}
        unknown = sorted(set(params) - set(known))
        if unknown:
            raise MaaContractValidationError(
                f"unknown {self.task_name} parameters: {', '.join(unknown)}"
            )
        missing = sorted(
            parameter.name
            for parameter in self.parameters
            if parameter.required and parameter.name not in params
        )
        if missing:
            raise MaaContractValidationError(
                f"missing required {self.task_name} parameters: {', '.join(missing)}"
            )
        warnings: list[str] = []
        for name, value in params.items():
            parameter = known[name]
            parameter.validate(value)
            if parameter.deprecated:
                warnings.append(f"deprecated parameter: {self.task_name}.{name}")
        for group in self.exactly_one_of:
            present = [name for name in group if name in params]
            if len(present) != 1:
                raise MaaContractValidationError(
                    f"{self.task_name} requires exactly one of {group!r}, got {present!r}"
                )
        for rule in self.conditional_requirements:
            if params.get(rule.field) == rule.equals:
                absent = [name for name in rule.required_fields if name not in params]
                if absent:
                    raise MaaContractValidationError(
                        f"{self.task_name} requires {absent!r} when {rule.field}={rule.equals!r}"
                    )
        _validate_specific_task_params(self.task_name, params)
        return tuple(warnings)


@dataclass(frozen=True, slots=True)
class EffectContract:
    evidence: EffectEvidence
    success_predicate: str
    forbidden_predicates: tuple[str, ...]
    callback_is_sufficient: bool = False
    verification_status: str = "contract_only"


@dataclass(frozen=True, slots=True)
class RecoveryContract:
    modes: tuple[RecoveryMode, ...]
    reobserve_before_retry: bool = True
    max_same_action_retries: int = 2


@dataclass(frozen=True, slots=True)
class OperationContract:
    contract_id: str
    semantic_id: str
    kind: ContractKind
    name: str
    category: str
    risk: RiskLevel
    parameters: tuple[ParameterSpec, ...]
    preconditions: tuple[str, ...]
    effect: EffectContract
    recovery: RecoveryContract
    required_authorizations: tuple[str, ...]
    idempotent: bool
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationAuthorization:
    allow_account_state_change: bool = False
    allow_game_resource: bool = False
    allow_premium_currency: bool = False
    allow_account_switch: bool = False
    allow_external_reporting: bool = False
    allow_custom_task_graph: bool = False


@dataclass(frozen=True, slots=True)
class PublicTaskCall:
    contract_id: str
    semantic_id: str
    task_name: str
    params: dict[str, Any]
    risk: RiskLevel
    expected_effect: EffectContract
    recovery: RecoveryContract
    warnings: tuple[str, ...]

    def as_operator_task(self) -> tuple[str, dict[str, Any]]:
        return self.task_name, dict(self.params)


@dataclass(frozen=True, slots=True)
class BattleActionCall:
    contract_id: str
    semantic_id: str
    action_name: str
    details: dict[str, Any]
    risk: RiskLevel
    expected_effect: EffectContract
    recovery: RecoveryContract
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaaContractCatalog:
    semantic_registry: MaaSemanticRegistry
    task_schemas: tuple[TaskParameterSchema, ...]
    public_task_contracts: tuple[OperationContract, ...]
    battle_action_contracts: tuple[OperationContract, ...]

    def require_task_schema(self, name: str) -> TaskParameterSchema:
        for schema in self.task_schemas:
            if schema.task_name == name:
                return schema
        raise UnknownMaaSemanticError(f"unknown MAA task schema: {name}")

    def require_public_task_contract(self, name: str) -> OperationContract:
        self.semantic_registry.require_public_task(name)
        for contract in self.public_task_contracts:
            if contract.name == name:
                return contract
        raise UnknownMaaSemanticError(f"unknown MAA public task contract: {name}")

    def require_battle_action_contract(self, name_or_alias: str) -> OperationContract:
        action = self.semantic_registry.require_battle_action(name_or_alias)
        for contract in self.battle_action_contracts:
            if contract.name == action.name:
                return contract
        raise UnknownMaaSemanticError(f"unknown MAA battle action contract: {name_or_alias}")

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "public_tasks": [_contract_manifest(contract) for contract in self.public_task_contracts],
            "battle_actions": [
                _contract_manifest(contract) for contract in self.battle_action_contracts
            ],
            "summary": {
                "public_task_contracts": len(self.public_task_contracts),
                "battle_action_contracts": len(self.battle_action_contracts),
                "callback_sufficient_contracts": sum(
                    contract.effect.callback_is_sufficient
                    for contract in (*self.public_task_contracts, *self.battle_action_contracts)
                ),
                "dynamic_effects_verified": sum(
                    contract.effect.verification_status != "contract_only"
                    for contract in (*self.public_task_contracts, *self.battle_action_contracts)
                ),
            },
        }

    def manifest_json(self) -> str:
        return json.dumps(self.manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


_TASK_MARKER_RE = re.compile(r"^- `([A-Za-z]+)`\s*$", re.MULTILINE)
_FIELD_RE = re.compile(
    r'^\s*::: field name="([^"]+)" type="([^"]+)"([^\n]*)$', re.MULTILINE
)


def parse_integration_parameter_schemas(
    document: str, task_names: Sequence[str], *, source: str = "docs/zh-cn/protocol/integration.md"
) -> tuple[TaskParameterSchema, ...]:
    """从 MAA 集成文档的 field 指令生成全部公开任务参数 schema。"""

    wanted = set(task_names)
    markers = [match for match in _TASK_MARKER_RE.finditer(document) if match.group(1) in wanted]
    found = {match.group(1) for match in markers}
    if found != wanted:
        missing = sorted(wanted - found)
        raise MaaContractValidationError(f"integration document missing task sections: {missing}")
    schemas: list[TaskParameterSchema] = []
    for index, marker in enumerate(markers):
        task_name = marker.group(1)
        next_task = markers[index + 1].start() if index + 1 < len(markers) else len(document)
        next_interface = document.find("\n### `AsstSetTaskParams`", marker.end())
        end = min(next_task, next_interface if next_interface >= 0 else len(document))
        section = document[marker.end() : end]
        parameters: list[ParameterSpec] = []
        for field in _FIELD_RE.finditer(section):
            name, raw_type, attrs = field.groups()
            default_match = re.search(r'default="([^"]*)"', attrs)
            json_type = html.unescape(raw_type).replace("`", "")
            line = document.count("\n", 0, marker.end() + field.start()) + 1
            parameters.append(
                ParameterSpec(
                    name=name,
                    json_type=json_type,
                    required=bool(re.search(r"\brequired\b", attrs)),
                    default_raw=(
                        html.unescape(default_match.group(1)).replace("\\_", "_")
                        if default_match
                        else None
                    ),
                    deprecated=bool(re.search(r"\bdeprecated\b", attrs)),
                    allowed_values=(),
                    source=f"{source}:{line}",
                )
            )
        if not parameters:
            raise MaaContractValidationError(f"no parameters parsed for public task {task_name}")
        schemas.append(TaskParameterSchema(task_name=task_name, parameters=tuple(parameters)))
    return tuple(_apply_schema_corrections(schema) for schema in schemas)


def _apply_schema_corrections(schema: TaskParameterSchema) -> TaskParameterSchema:
    task_name = schema.task_name
    parameters = list(schema.parameters)
    exactly_one = schema.exactly_one_of
    conditional = schema.conditional_requirements
    corrections: list[str] = []

    def update_parameter(name: str, **changes: Any) -> None:
        for index, parameter in enumerate(parameters):
            if parameter.name == name:
                parameters[index] = replace(parameter, **changes)
                return
        raise MaaContractValidationError(f"document parameter missing for correction: {task_name}.{name}")

    def add_source_parameter(
        name: str,
        json_type: str,
        default_raw: str,
        source: str,
        *,
        deprecated: bool = False,
    ) -> None:
        parameters.append(
            ParameterSpec(
                name=name,
                json_type=json_type,
                required=False,
                default_raw=default_raw,
                deprecated=deprecated,
                allowed_values=(),
                source=source,
            )
        )
        corrections.append(f"source-only parameter: {name}")

    if task_name == "SingleStep":
        parameters = [parameter for parameter in parameters if parameter.name != "subtask"]
        details_index = next(i for i, p in enumerate(parameters) if p.name == "details")
        parameters.insert(
            details_index,
            ParameterSpec(
                name="subtype",
                json_type="string",
                required=True,
                default_raw=None,
                deprecated=False,
                allowed_values=("stage", "start", "action"),
                source="src/MaaCore/Task/Interface/SingleStepTask.cpp:21",
            ),
        )
        update_parameter("type", allowed_values=("copilot",))
        conditional = (
            ConditionalRequirement("subtype", "stage", ("details",)),
            ConditionalRequirement("subtype", "action", ("details",)),
        )
        corrections.append("subtask->subtype; details.stage->stage_name; action details use actions[]")
    elif task_name == "Copilot":
        exactly_one = (("filename", "copilot_list"),)
        corrections.append("filename/copilot_list are source-required exactly-one alternatives")
    elif task_name == "ParadoxCopilot":
        update_parameter("filename", required=False)
        update_parameter("list", required=False)
        exactly_one = (("filename", "list"),)
        corrections.append("filename/list are source-required exactly-one alternatives")
    elif task_name == "Infrast":
        update_parameter("filename", required=False)
        update_parameter("plan_index", required=False)
        conditional = (ConditionalRequirement("mode", 10000, ("filename", "plan_index")),)
        corrections.append("filename/plan_index are required only for mode=10000")
        add_source_parameter(
            "continue_training",
            "boolean",
            "false",
            "src/MaaCore/Task/Interface/InfrastTask.cpp:142",
        )
    elif task_name == "Mall":
        add_source_parameter(
            "select_formation",
            "number",
            "0",
            "src/MaaCore/Task/Interface/MallTask.cpp:117",
            deprecated=True,
        )
    elif task_name == "Recruit":
        add_source_parameter(
            "force_refresh",
            "boolean",
            "true",
            "src/MaaCore/Task/Interface/RecruitTask.cpp:50",
        )
    elif task_name == "Reclamation":
        add_source_parameter(
            "clear_store",
            "boolean",
            "false",
            "src/MaaCore/Task/Interface/ReclamationTask.cpp:73",
        )

    allowed: dict[tuple[str, str], tuple[Any, ...]] = {
        ("StartUp", "client_type"): _CLIENT_TYPES,
        ("CloseDown", "client_type"): _CLIENT_TYPES,
        ("Fight", "client_type"): _CLIENT_TYPES,
        ("Fight", "server"): ("CN", "US", "JP", "KR"),
        ("Recruit", "server"): ("CN", "US", "JP", "KR"),
        ("Infrast", "mode"): (0, 10000, 20000),
        ("Infrast", "facility"): (),
        ("Infrast", "drones"): (
            "_NotUse",
            "Money",
            "SyntheticJade",
            "CombatRecord",
            "PureGold",
            "OriginStone",
            "Chip",
        ),
        ("SingleStep", "type"): ("copilot",),
    }
    for index, parameter in enumerate(parameters):
        values = allowed.get((task_name, parameter.name))
        if values:
            parameters[index] = replace(parameter, allowed_values=values)
    return TaskParameterSchema(
        task_name=task_name,
        parameters=tuple(parameters),
        exactly_one_of=exactly_one,
        conditional_requirements=conditional,
        corrections=tuple(corrections),
    )


_CLIENT_TYPES = ("Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR")


def _validate_specific_task_params(task_name: str, params: dict[str, Any]) -> None:
    if task_name == "Fight":
        for field in ("medicine", "medicine_expire_days", "expiring_medicine", "stone", "times"):
            if field in params and params[field] < 0:
                raise MaaContractValidationError(f"Fight.{field} cannot be negative")
        if "series" in params and not -1 <= params["series"] <= 10:
            raise MaaContractValidationError("Fight.series must be between -1 and 10")
    elif task_name == "Recruit":
        if "extra_tags_mode" in params and params["extra_tags_mode"] not in {0, 1, 2}:
            raise MaaContractValidationError("Recruit.extra_tags_mode must be 0, 1 or 2")
    elif task_name == "Infrast":
        facilities = {
            "Mfg",
            "Trade",
            "Power",
            "Control",
            "Reception",
            "Office",
            "Dorm",
            "Processing",
            "Training",
        }
        unknown = sorted(set(params.get("facility", [])) - facilities)
        if unknown:
            raise MaaContractValidationError(f"unknown Infrast facilities: {unknown}")
    elif task_name == "Copilot":
        if "loop_times" in params and params["loop_times"] < 1:
            raise MaaContractValidationError("Copilot.loop_times must be positive")
        if "formation_index" in params and not 0 <= params["formation_index"] <= 4:
            raise MaaContractValidationError("Copilot.formation_index must be between 0 and 4")
        if "support_unit_usage" in params and not 0 <= params["support_unit_usage"] <= 3:
            raise MaaContractValidationError("Copilot.support_unit_usage must be between 0 and 3")
    elif task_name == "SingleStep":
        subtype = params.get("subtype")
        details = params.get("details")
        if subtype == "stage":
            if not isinstance(details, dict) or not isinstance(details.get("stage_name"), str):
                raise MaaContractValidationError("SingleStep stage requires details.stage_name")
            if set(details) != {"stage_name"}:
                raise MaaContractValidationError("SingleStep stage details only accepts stage_name")
        elif subtype == "start" and details is not None:
            raise MaaContractValidationError("SingleStep start must not carry details")
        elif subtype == "action":
            if not isinstance(details, dict) or not isinstance(details.get("actions"), list):
                raise MaaContractValidationError("SingleStep action requires details.actions[]")
            if not details["actions"]:
                raise MaaContractValidationError("SingleStep action details.actions cannot be empty")


_TASK_META: dict[str, tuple[str, RiskLevel, tuple[str, ...], EffectEvidence, str, bool]] = {
    "StartUp": ("lifecycle", RiskLevel.REVERSIBLE, (), EffectEvidence.FRESH_FRAME, "fresh scene is the requested account main screen", True),
    "CloseDown": ("lifecycle", RiskLevel.REVERSIBLE, (), EffectEvidence.PROCESS_STATE, "bound game process/window is absent", True),
    "Fight": ("battle", RiskLevel.GAME_RESOURCE, ("allow_game_resource",), EffectEvidence.FRESH_FRAME_AND_EVENT, "battle reaches declared terminal and resource delta is reconciled", False),
    "Recruit": ("recruit", RiskLevel.GAME_RESOURCE, ("allow_game_resource",), EffectEvidence.FRESH_FRAME_AND_EVENT, "recruit slots/tags reflect the requested calculation or confirmed recruit", False),
    "Infrast": ("infrastructure", RiskLevel.ACCOUNT_STATE, ("allow_account_state_change",), EffectEvidence.FRESH_FRAME, "facility overview reflects the requested shift/collection state", False),
    "Mall": ("commerce", RiskLevel.GAME_RESOURCE, ("allow_game_resource",), EffectEvidence.FRESH_FRAME_AND_EVENT, "credit visit/shop result and currency delta reconcile", False),
    "Award": ("reward", RiskLevel.ACCOUNT_STATE, ("allow_account_state_change",), EffectEvidence.FRESH_FRAME_AND_EVENT, "enabled reward/mail counters are cleared or explicitly unchanged", False),
    "Roguelike": ("roguelike", RiskLevel.ACCOUNT_STATE, ("allow_account_state_change",), EffectEvidence.FRESH_FRAME_AND_EVENT, "roguelike run reaches its declared stop condition", False),
    "Copilot": ("battle", RiskLevel.GAME_RESOURCE, ("allow_game_resource",), EffectEvidence.FRESH_FRAME_AND_EVENT, "copilot battle reaches declared terminal with stage identity preserved", False),
    "SSSCopilot": ("sss", RiskLevel.ACCOUNT_STATE, ("allow_account_state_change",), EffectEvidence.FRESH_FRAME_AND_EVENT, "SSS run reaches declared terminal without wrong-stage continuation", False),
    "ParadoxCopilot": ("paradox", RiskLevel.ACCOUNT_STATE, ("allow_account_state_change",), EffectEvidence.FRESH_FRAME, "paradox simulation reaches its declared terminal", False),
    "SingleStep": ("battle", RiskLevel.ACCOUNT_STATE, ("allow_account_state_change",), EffectEvidence.FRESH_FRAME, "fresh battle state satisfies the subtype/action effect predicate", False),
    "VideoRecognition": ("offline", RiskLevel.READ_ONLY, (), EffectEvidence.OFFLINE_ARTIFACT, "recognition artifact exists and validates against the input video", True),
    "Depot": ("inventory", RiskLevel.READ_ONLY, (), EffectEvidence.OFFLINE_ARTIFACT, "depot export is fresh and structurally valid", True),
    "OperBox": ("inventory", RiskLevel.READ_ONLY, (), EffectEvidence.OFFLINE_ARTIFACT, "operator-box export is fresh and structurally valid", True),
    "Reclamation": ("reclamation", RiskLevel.ACCOUNT_STATE, ("allow_account_state_change",), EffectEvidence.FRESH_FRAME_AND_EVENT, "reclamation run reaches its declared stop condition", False),
    "Custom": ("orchestration", RiskLevel.HIGH, ("allow_custom_task_graph", "allow_account_state_change"), EffectEvidence.FRESH_FRAME_AND_EVENT, "all declared child-chain terminal predicates reconcile", False),
}


_ACTION_EFFECTS: dict[str, tuple[EffectEvidence, str, RiskLevel, bool]] = {
    "Deploy": (EffectEvidence.FRESH_FRAME, "operator appears on the intended tile and card/cost state changes", RiskLevel.ACCOUNT_STATE, False),
    "Skill": (EffectEvidence.FRESH_FRAME, "target skill readiness/animation state transitions", RiskLevel.ACCOUNT_STATE, False),
    "Retreat": (EffectEvidence.FRESH_FRAME, "target leaves the tile and returns to deployment state when applicable", RiskLevel.ACCOUNT_STATE, False),
    "SpeedUp": (EffectEvidence.FRESH_FRAME, "battle speed indicator changes exactly once", RiskLevel.REVERSIBLE, False),
    "BulletTime": (EffectEvidence.FRESH_FRAME, "battle enters the declared bullet-time state", RiskLevel.REVERSIBLE, False),
    "SkillUsage": (EffectEvidence.DELAYED_BEHAVIOR, "subsequent skill behavior follows the new usage policy", RiskLevel.ACCOUNT_STATE, False),
    "Output": (EffectEvidence.DECLARED_EVENT, "declared output event is emitted with no game-state mutation", RiskLevel.READ_ONLY, True),
    "SkillDaemon": (EffectEvidence.DELAYED_BEHAVIOR, "battle continues to terminal while only ready skills are used", RiskLevel.ACCOUNT_STATE, False),
    "MoveCamera": (EffectEvidence.FRESH_FRAME, "camera/layout transform matches the requested distance", RiskLevel.REVERSIBLE, False),
    "DrawCard": (EffectEvidence.FRESH_FRAME, "SSS card/hand state changes by one legal draw", RiskLevel.ACCOUNT_STATE, False),
    "CheckIfStartOver": (EffectEvidence.FRESH_FRAME_AND_EVENT, "operator requirement branch either continues or safely restarts", RiskLevel.ACCOUNT_STATE, False),
    "ResetStopwatch": (EffectEvidence.DELAYED_BEHAVIOR, "a later elapsed-time condition is measured from the reset point", RiskLevel.READ_ONLY, True),
}


_DEPLOY_DIRECTION_ALIASES = (
    "Right",
    "RIGHT",
    "right",
    "右",
    "Left",
    "LEFT",
    "left",
    "左",
    "Up",
    "UP",
    "up",
    "上",
    "Down",
    "DOWN",
    "down",
    "下",
    "None",
    "NONE",
    "none",
    "无",
)


_ACTION_PARAMETERS = (
    ParameterSpec("type", "string", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("name", "string", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("location", "array<number>", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec(
        "direction",
        "string",
        False,
        None,
        False,
        _DEPLOY_DIRECTION_ALIASES,
        "CopilotConfig.cpp:string_to_direction",
    ),
    ParameterSpec("kills", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("cost_changes", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("costs", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("cooling", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("skill_usage", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("skill_times", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("pre_delay", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("post_delay", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("rear_delay", "number", False, None, True, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("timeout", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("doc", "string", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("doc_color", "string", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("tool_men", "object", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("distance", "array<number>", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("skip_if_not_ready", "boolean", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
    ParameterSpec("elapsed_time", "number", False, None, False, (), "CopilotConfig.cpp:parse_actions"),
)


def build_maa_contract_catalog(
    semantic_registry: MaaSemanticRegistry, source_root: str | Path
) -> MaaContractCatalog:
    source_path = Path(source_root)
    document_path = source_path / "docs/zh-cn/protocol/integration.md"
    try:
        document = document_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MaaContractValidationError(f"cannot read MAA integration document: {document_path}") from exc
    task_schemas = parse_integration_parameter_schemas(
        document,
        semantic_registry.public_task_names,
        source=document_path.relative_to(source_path).as_posix(),
    )
    schema_by_name = {schema.task_name: schema for schema in task_schemas}
    public_contracts: list[OperationContract] = []
    for task in semantic_registry.public_tasks:
        if task.name not in _TASK_META:
            raise MaaContractValidationError(f"public task contract metadata missing: {task.name}")
        category, risk, authorizations, evidence, predicate, idempotent = _TASK_META[task.name]
        schema = schema_by_name[task.name]
        public_contracts.append(
            OperationContract(
                contract_id=f"{task.semantic_id}.contract.v1",
                semantic_id=task.semantic_id,
                kind=ContractKind.PUBLIC_TASK,
                name=task.name,
                category=category,
                risk=risk,
                parameters=schema.parameters,
                preconditions=_task_preconditions(task.name),
                effect=EffectContract(
                    evidence=evidence,
                    success_predicate=predicate,
                    forbidden_predicates=(
                        "stale frame is used as success evidence",
                        "callback success is the only success evidence",
                        "execution continues on a wrong scene or wrong target",
                    ),
                ),
                recovery=_default_recovery(task.name),
                required_authorizations=authorizations,
                idempotent=idempotent,
                source_refs=(task.header, "docs/zh-cn/protocol/integration.md", *schema.corrections),
            )
        )
    action_contracts: list[OperationContract] = []
    for action in semantic_registry.battle_actions:
        if action.name not in _ACTION_EFFECTS:
            raise MaaContractValidationError(f"battle action contract metadata missing: {action.name}")
        evidence, predicate, risk, idempotent = _ACTION_EFFECTS[action.name]
        required_auth = () if risk in {RiskLevel.READ_ONLY, RiskLevel.REVERSIBLE} else (
            "allow_account_state_change",
        )
        action_contracts.append(
            OperationContract(
                contract_id=f"{action.semantic_id}.contract.v1",
                semantic_id=action.semantic_id,
                kind=ContractKind.BATTLE_ACTION,
                name=action.name,
                category="battle",
                risk=risk,
                parameters=_ACTION_PARAMETERS,
                preconditions=("fresh battle state", "target/action precondition is unambiguous"),
                effect=EffectContract(
                    evidence=evidence,
                    success_predicate=predicate,
                    forbidden_predicates=(
                        "stale frame is used as success evidence",
                        "callback success is the only success evidence",
                        "action affects a different operator/tile/object",
                    ),
                ),
                recovery=_default_recovery(action.name),
                required_authorizations=required_auth,
                idempotent=idempotent,
                source_refs=(f"{action.source}:{action.source_line}",),
            )
        )
    return MaaContractCatalog(
        semantic_registry=semantic_registry,
        task_schemas=task_schemas,
        public_task_contracts=tuple(public_contracts),
        battle_action_contracts=tuple(action_contracts),
    )


def _task_preconditions(name: str) -> tuple[str, ...]:
    base = ("device connected", "fresh scene observation available", "no blocking payment/account dialog")
    additions = {
        "VideoRecognition": ("input video exists",),
        "Depot": ("game account is at a readable scene",),
        "OperBox": ("game account is at a readable scene",),
        "Custom": ("every child task resolves in the pinned task graph",),
        "SingleStep": ("battle stage is bound before start/action",),
    }
    return base + additions.get(name, ())


def _default_recovery(name: str) -> RecoveryContract:
    if name in {"Fight", "Copilot", "SSSCopilot", "Roguelike", "Reclamation", "SkillDaemon"}:
        modes = (
            RecoveryMode.REOBSERVE,
            RecoveryMode.RELOCALIZE,
            RecoveryMode.BACKTRACK,
            RecoveryMode.RESTART_TASK,
            RecoveryMode.STOP_AND_REPORT,
        )
    else:
        modes = (
            RecoveryMode.REOBSERVE,
            RecoveryMode.RETRY_SAME,
            RecoveryMode.RELOCALIZE,
            RecoveryMode.STOP_AND_REPORT,
        )
    return RecoveryContract(modes=modes)


def _require_authorizations(
    required: Iterable[str], authorization: OperationAuthorization
) -> None:
    missing = [name for name in required if not getattr(authorization, name)]
    if missing:
        raise MaaContractValidationError(f"missing explicit authorizations: {', '.join(missing)}")


def compile_public_task_call(
    catalog: MaaContractCatalog,
    task_name: str,
    params: dict[str, Any],
    *,
    authorization: OperationAuthorization = OperationAuthorization(),
) -> PublicTaskCall:
    contract = catalog.require_public_task_contract(task_name)
    schema = catalog.require_task_schema(task_name)
    warnings = list(schema.validate(params))
    _require_authorizations(contract.required_authorizations, authorization)
    if task_name == "StartUp" and params.get("account_name"):
        _require_authorizations(("allow_account_switch",), authorization)
    if task_name == "Fight" and params.get("stone", 0) > 0:
        _require_authorizations(("allow_premium_currency",), authorization)
    if params.get("report_to_penguin") or params.get("report_to_yituliu"):
        _require_authorizations(("allow_external_reporting",), authorization)
    if task_name == "Custom":
        _validate_custom_task_names(catalog.semantic_registry, params["task_names"])
    normalized = dict(params)
    normalized.setdefault("enable", True)
    return PublicTaskCall(
        contract_id=contract.contract_id,
        semantic_id=contract.semantic_id,
        task_name=task_name,
        params=normalized,
        risk=contract.risk,
        expected_effect=contract.effect,
        recovery=contract.recovery,
        warnings=tuple(warnings),
    )


def _validate_custom_task_names(registry: MaaSemanticRegistry, task_names: Sequence[str]) -> None:
    if not task_names:
        raise MaaContractValidationError("Custom.task_names cannot be empty")
    base_names = {
        node.name for node in registry.task_nodes if node.scope == "tasks/tasks.json"
    }
    for raw in task_names:
        expression: TaskExpression
        try:
            expression = parse_task_expression(raw)
        except MaaSemanticValidationError as exc:
            raise MaaContractValidationError(f"invalid Custom task expression {raw!r}: {exc}") from exc
        if not expression.task_names or expression.task_names[-1] not in base_names:
            raise MaaContractValidationError(f"Custom task does not resolve in base graph: {raw!r}")


def compile_battle_action(
    catalog: MaaContractCatalog,
    name_or_alias: str,
    details: dict[str, Any],
    *,
    authorization: OperationAuthorization = OperationAuthorization(),
) -> BattleActionCall:
    action_semantic = catalog.semantic_registry.require_battle_action(name_or_alias)
    contract = catalog.require_battle_action_contract(name_or_alias)
    known = {parameter.name: parameter for parameter in contract.parameters}
    unknown = sorted(set(details) - set(known))
    if unknown:
        raise MaaContractValidationError(
            f"unknown {action_semantic.name} action parameters: {', '.join(unknown)}"
        )
    warnings: list[str] = []
    for name, value in details.items():
        known[name].validate(value)
        if known[name].deprecated:
            warnings.append(f"deprecated battle action parameter: {name}")
    if "type" in details:
        declared = catalog.semantic_registry.require_battle_action(details["type"])
        if declared.name != action_semantic.name:
            raise MaaContractValidationError(
                f"battle action type {details['type']!r} conflicts with {name_or_alias!r}"
            )
    normalized = dict(details)
    normalized["type"] = action_semantic.name
    _validate_specific_battle_action(action_semantic.name, normalized)
    _require_authorizations(contract.required_authorizations, authorization)
    return BattleActionCall(
        contract_id=contract.contract_id,
        semantic_id=contract.semantic_id,
        action_name=action_semantic.name,
        details=normalized,
        risk=contract.risk,
        expected_effect=contract.effect,
        recovery=contract.recovery,
        warnings=tuple(warnings),
    )


def _validate_specific_battle_action(name: str, details: dict[str, Any]) -> None:
    location = details.get("location")
    if location is not None and len(location) != 2:
        raise MaaContractValidationError("battle action location must contain exactly two values")
    if name == "Deploy":
        for field in ("name", "location", "direction"):
            if field not in details:
                raise MaaContractValidationError(f"Deploy requires {field}")
    elif name in {"Skill", "Retreat"}:
        if not details.get("name") and location is None:
            raise MaaContractValidationError(f"{name} requires name or location")
    elif name == "SkillUsage":
        if not details.get("name") or "skill_usage" not in details:
            raise MaaContractValidationError("SkillUsage requires name and skill_usage")
    elif name == "MoveCamera":
        distance = details.get("distance")
        if not isinstance(distance, list) or len(distance) != 2:
            raise MaaContractValidationError("MoveCamera requires distance with two values")
    elif name == "CheckIfStartOver":
        if not isinstance(details.get("tool_men"), dict) or not details["tool_men"]:
            raise MaaContractValidationError("CheckIfStartOver requires non-empty tool_men")


def _contract_manifest(contract: OperationContract) -> dict[str, Any]:
    payload = {
        "contract_id": contract.contract_id,
        "semantic_id": contract.semantic_id,
        "kind": contract.kind.value,
        "name": contract.name,
        "category": contract.category,
        "risk": contract.risk.value,
        "parameters": [
            {
                "name": parameter.name,
                "json_type": parameter.json_type,
                "required": parameter.required,
                "default_raw": parameter.default_raw,
                "deprecated": parameter.deprecated,
                "allowed_values": list(parameter.allowed_values),
                "source": parameter.source,
            }
            for parameter in contract.parameters
        ],
        "preconditions": list(contract.preconditions),
        "effect": {
            "evidence": contract.effect.evidence.value,
            "success_predicate": contract.effect.success_predicate,
            "forbidden_predicates": list(contract.effect.forbidden_predicates),
            "callback_is_sufficient": contract.effect.callback_is_sufficient,
            "verification_status": contract.effect.verification_status,
        },
        "recovery": {
            "modes": [mode.value for mode in contract.recovery.modes],
            "reobserve_before_retry": contract.recovery.reobserve_before_retry,
            "max_same_action_retries": contract.recovery.max_same_action_retries,
        },
        "required_authorizations": list(contract.required_authorizations),
        "idempotent": contract.idempotent,
        "source_refs": list(contract.source_refs),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload

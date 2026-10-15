# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T06:12:00Z type=semantic-registry status=active
# [OMNI] summary="从固定 MaaCore 源码与发布资源生成可验证、可差异比较的完整语义注册表。"
# [OMNI] why="MAA 公开任务、任务图与肉鸽资产会升级，手抄清单无法支撑长链效果返测和闭门失败。"
# [OMNI] tags=game-observatory,arknights,maa,semantic-registry,task-graph
"""MAA 源码与资源的可验证语义注册表。

本模块不复制一份容易过期的 MAA 能力清单，而是在运行时从两个真源生成：

* ``MaaCore`` 源码给出公开任务、作业动作、识别算法与任务动作；
* 发布资源给出界面任务图、地区覆盖与肉鸽自动战斗部署语义。

注册表只负责发现、保存和验证语义，不执行动作。执行层必须先通过
``require_*`` 查询，未知名称会闭门失败，避免 MAA 对未知作业动作只告警后静默跳过。
"""

from __future__ import annotations

import json
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote

__all__ = [
    "BattleActionSemantic",
    "MaaSemanticIssue",
    "MaaSemanticRegistry",
    "MaaSemanticValidationError",
    "PublicTaskSemantic",
    "RoguelikeStageSemantic",
    "TaskExpression",
    "TaskNodeSemantic",
    "TaskReferenceSemantic",
    "UnknownMaaSemanticError",
    "load_maa_semantic_registry",
    "parse_task_expression",
]


_TASK_LIST_FIELDS = (
    "sub",
    "next",
    "onErrorNext",
    "exceededNext",
    "reduceOtherTimes",
)
_SHARP_SYMBOLS = {
    "none",
    "self",
    "back",
    "next",
    "sub",
    "on_error_next",
    "exceeded_next",
    "reduce_other_times",
}
_TOKEN_RE = re.compile(r"\s*([^@#*+^()\s]+|[@#*+^()])")


class MaaSemanticValidationError(ValueError):
    """MAA 源码或资源不满足注册表结构契约。"""


class UnknownMaaSemanticError(LookupError):
    """调用方请求了当前 MAA 真源中不存在的语义。"""


def _semantic_id(kind: str, *parts: str) -> str:
    # ``:`` 会被 quote 转义，因此双冒号是无歧义的分段符；任务名和 scope 本身可含点号。
    encoded = "::".join(quote(part, safe="") for part in parts)
    return f"maa.{kind}.{encoded}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class TaskExpression:
    """一条已通过 MAA 任务表达式语法检查的表达式。"""

    raw: str
    task_names: tuple[str, ...]
    virtual_symbols: tuple[str, ...]
    operators: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicTaskSemantic:
    """``Assistant::append_task`` 对非 Debug 构建公开的一类任务。"""

    semantic_id: str
    name: str
    implementation: str
    header: str
    source_line: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class BattleActionSemantic:
    """作业协议动作及 MAA 接受的所有别名。"""

    semantic_id: str
    name: str
    engine_name: str
    aliases: tuple[str, ...]
    source: str
    source_line: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class TaskReferenceSemantic:
    """任务节点某个边字段中的一条表达式。"""

    field: str
    expression: TaskExpression


@dataclass(frozen=True, slots=True)
class TaskNodeSemantic:
    """一个地区/平台 scope 内的任务节点声明。

    ``payload_json`` 保存完整声明，避免注册表只剩统计数字而丢失 OCR 文本、模板、
    ROI、延迟、滑动参数等真正用于界面跳转的语义。
    """

    semantic_id: str
    scope: str
    name: str
    algorithm: str | None
    action: str | None
    base_task: TaskExpression | None
    references: tuple[TaskReferenceSemantic, ...]
    payload_json: str
    content_sha256: str

    def payload(self) -> dict[str, Any]:
        """返回节点声明的独立副本。"""

        value = json.loads(self.payload_json)
        if not isinstance(value, dict):  # pragma: no cover - 构造器已经保证
            raise MaaSemanticValidationError(f"task payload is not an object: {self.scope}:{self.name}")
        return value


@dataclass(frozen=True, slots=True)
class RoguelikeStageSemantic:
    """一份肉鸽关卡自动战斗脚本的语义摘要与完整声明。"""

    semantic_id: str
    theme: str
    stage_name: str
    source: str
    deploy_steps: int
    retreat_steps: int
    replacement_homes: int
    blacklist_locations: int
    groups: tuple[str, ...]
    features: tuple[str, ...]
    payload_json: str
    content_sha256: str

    def payload(self) -> dict[str, Any]:
        """返回肉鸽脚本声明的独立副本。"""

        value = json.loads(self.payload_json)
        if not isinstance(value, dict):  # pragma: no cover - 构造器已经保证
            raise MaaSemanticValidationError(f"roguelike payload is not an object: {self.source}")
        return value


@dataclass(frozen=True, slots=True)
class MaaSemanticIssue:
    """不阻断装载、但会改变或削弱上游行为的资源问题。"""

    code: str
    source: str
    detail: str


@dataclass(frozen=True, slots=True)
class MaaSemanticRegistry:
    """从固定版本 MAA 源码与发布资源生成的完整语义索引。"""

    public_tasks: tuple[PublicTaskSemantic, ...]
    battle_actions: tuple[BattleActionSemantic, ...]
    recognition_algorithms: tuple[str, ...]
    process_actions: tuple[str, ...]
    deploy_direction_aliases: tuple[tuple[str, str], ...]
    task_nodes: tuple[TaskNodeSemantic, ...]
    roguelike_stages: tuple[RoguelikeStageSemantic, ...]
    issues: tuple[MaaSemanticIssue, ...]

    @property
    def public_task_names(self) -> tuple[str, ...]:
        return tuple(task.name for task in self.public_tasks)

    @property
    def battle_action_names(self) -> tuple[str, ...]:
        return tuple(action.name for action in self.battle_actions)

    @property
    def observed_algorithms(self) -> tuple[str, ...]:
        return tuple(sorted({node.algorithm for node in self.task_nodes if node.algorithm}))

    @property
    def observed_process_actions(self) -> tuple[str, ...]:
        return tuple(sorted({node.action for node in self.task_nodes if node.action}))

    def require_public_task(self, name: str) -> PublicTaskSemantic:
        """精确查询公开任务；大小写错误或 Debug-only 任务均被拒绝。"""

        for task in self.public_tasks:
            if task.name == name:
                return task
        raise UnknownMaaSemanticError(f"unknown MAA public task: {name!r}")

    def require_battle_action(self, name_or_alias: str) -> BattleActionSemantic:
        """按规范名或 MAA 明确接受的别名查询作业动作。"""

        for action in self.battle_actions:
            if name_or_alias in action.aliases:
                return action
        raise UnknownMaaSemanticError(f"unknown MAA battle action: {name_or_alias!r}")

    def require_task_node(self, name: str, *, scope: str = "tasks/tasks.json") -> TaskNodeSemantic:
        """在明确资源 scope 内查询界面任务节点。"""

        for node in self.task_nodes:
            if node.scope == scope and node.name == name:
                return node
        raise UnknownMaaSemanticError(f"unknown MAA task node: {scope}:{name}")

    def summary(self) -> dict[str, Any]:
        """生成可写入证据报告的确定性清单摘要。"""

        nodes_by_scope: dict[str, int] = defaultdict(int)
        stages_by_theme: dict[str, int] = defaultdict(int)
        issues_by_code: dict[str, int] = defaultdict(int)
        for node in self.task_nodes:
            nodes_by_scope[node.scope] += 1
        for stage in self.roguelike_stages:
            stages_by_theme[stage.theme] += 1
        for issue in self.issues:
            issues_by_code[issue.code] += 1
        return {
            "public_tasks": list(self.public_task_names),
            "battle_actions": list(self.battle_action_names),
            "recognition_algorithms": list(self.recognition_algorithms),
            "process_actions": list(self.process_actions),
            "task_nodes": {
                "total": len(self.task_nodes),
                "by_scope": dict(sorted(nodes_by_scope.items())),
                "observed_algorithms": list(self.observed_algorithms),
                "observed_actions": list(self.observed_process_actions),
            },
            "roguelike_stages": {
                "total": len(self.roguelike_stages),
                "by_theme": dict(sorted(stages_by_theme.items())),
            },
            "issues": dict(sorted(issues_by_code.items())),
        }

    def manifest(self) -> dict[str, Any]:
        """返回含稳定 ID、内容哈希和完整节点来源的可审计清单。"""

        return {
            "schema_version": 1,
            "summary": self.summary(),
            "public_tasks": [
                {
                    "semantic_id": task.semantic_id,
                    "name": task.name,
                    "implementation": task.implementation,
                    "source": f"{task.header}:{task.source_line}",
                    "content_sha256": task.content_sha256,
                }
                for task in self.public_tasks
            ],
            "battle_actions": [
                {
                    "semantic_id": action.semantic_id,
                    "name": action.name,
                    "engine_name": action.engine_name,
                    "aliases": list(action.aliases),
                    "source": f"{action.source}:{action.source_line}",
                    "content_sha256": action.content_sha256,
                }
                for action in self.battle_actions
            ],
            "task_nodes": [
                {
                    "semantic_id": node.semantic_id,
                    "scope": node.scope,
                    "name": node.name,
                    "algorithm": node.algorithm,
                    "action": node.action,
                    "base_task": node.base_task.raw if node.base_task else None,
                    "references": [
                        {"field": ref.field, "expression": ref.expression.raw}
                        for ref in node.references
                    ],
                    "content_sha256": node.content_sha256,
                }
                for node in self.task_nodes
            ],
            "roguelike_stages": [
                {
                    "semantic_id": stage.semantic_id,
                    "theme": stage.theme,
                    "stage_name": stage.stage_name,
                    "source": stage.source,
                    "deploy_steps": stage.deploy_steps,
                    "retreat_steps": stage.retreat_steps,
                    "replacement_homes": stage.replacement_homes,
                    "blacklist_locations": stage.blacklist_locations,
                    "groups": list(stage.groups),
                    "features": list(stage.features),
                    "content_sha256": stage.content_sha256,
                }
                for stage in self.roguelike_stages
            ],
            "issues": [
                {"code": issue.code, "source": issue.source, "detail": issue.detail}
                for issue in self.issues
            ],
        }

    def manifest_json(self) -> str:
        """以确定性 UTF-8 JSON 文本序列化完整清单。"""

        return json.dumps(self.manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


class _ExpressionParser:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.tokens = self._tokenize(raw)
        self.pos = 0
        self.task_names: list[str] = []
        self.virtual_symbols: list[str] = []
        self.operators: list[str] = []

    @staticmethod
    def _tokenize(raw: str) -> list[str]:
        if not isinstance(raw, str) or not raw.strip():
            raise MaaSemanticValidationError("task expression must be a non-empty string")
        tokens: list[str] = []
        pos = 0
        while pos < len(raw):
            match = _TOKEN_RE.match(raw, pos)
            if not match:
                if raw[pos:].strip() == "":
                    break
                raise MaaSemanticValidationError(
                    f"invalid task expression token at offset {pos}: {raw!r}"
                )
            tokens.append(match.group(1))
            pos = match.end()
        return tokens

    def parse(self) -> TaskExpression:
        self._parse_set_expression()
        if self._peek() is not None:
            raise MaaSemanticValidationError(
                f"unexpected token {self._peek()!r} in task expression: {self.raw!r}"
            )
        return TaskExpression(
            raw=self.raw,
            task_names=tuple(self.task_names),
            virtual_symbols=tuple(self.virtual_symbols),
            operators=tuple(self.operators),
        )

    def _parse_set_expression(self) -> None:
        self._parse_repeat()
        while self._peek() in {"+", "^"}:
            self.operators.append(self._take())
            self._parse_repeat()

    def _parse_repeat(self) -> None:
        self._parse_template()
        while self._peek() == "*":
            self.operators.append(self._take())
            count = self._take_name("positive repetition count")
            if not count.isdigit() or int(count) <= 0:
                raise MaaSemanticValidationError(
                    f"invalid repetition count {count!r} in task expression: {self.raw!r}"
                )

    def _parse_template(self) -> None:
        self._parse_primary()
        while self._peek() in {"@", "#"}:
            operator = self._take()
            self.operators.append(operator)
            if operator == "#":
                symbol = self._take_name("virtual task field")
                self._add_virtual(symbol)
            else:
                self._parse_primary()

    def _parse_primary(self) -> None:
        token = self._peek()
        if token == "(":
            self._take()
            self._parse_set_expression()
            self._expect(")")
            return
        if token == "#":
            self.operators.append(self._take())
            self._add_virtual(self._take_name("virtual task"))
            return
        name = self._take_name("task name")
        self.task_names.append(name)

    def _add_virtual(self, symbol: str) -> None:
        if symbol not in _SHARP_SYMBOLS:
            raise MaaSemanticValidationError(
                f"unknown virtual task #{symbol} in task expression: {self.raw!r}"
            )
        self.virtual_symbols.append(symbol)

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> str:
        token = self._peek()
        if token is None:
            raise MaaSemanticValidationError(f"unexpected end of task expression: {self.raw!r}")
        self.pos += 1
        return token

    def _take_name(self, expected: str) -> str:
        token = self._take()
        if token in {"@", "#", "*", "+", "^", "(", ")"}:
            raise MaaSemanticValidationError(
                f"expected {expected}, got {token!r} in task expression: {self.raw!r}"
            )
        return token

    def _expect(self, expected: str) -> None:
        actual = self._take()
        if actual != expected:
            raise MaaSemanticValidationError(
                f"expected {expected!r}, got {actual!r} in task expression: {self.raw!r}"
            )


def parse_task_expression(raw: str) -> TaskExpression:
    """按 MAA task-schema 的运算符优先级检查一条任务表达式。"""

    return _ExpressionParser(raw).parse()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MaaSemanticValidationError(f"cannot read MAA source: {path}") from exc


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaaSemanticValidationError(f"cannot parse MAA JSON: {path}") from exc
    if not isinstance(value, dict):
        raise MaaSemanticValidationError(f"MAA JSON root must be an object: {path}")
    return value


def _strip_debug_blocks(text: str) -> str:
    return re.sub(r"#ifdef\s+ASST_DEBUG\b.*?#endif", "", text, flags=re.DOTALL)


def _load_public_tasks(source_root: Path) -> tuple[PublicTaskSemantic, ...]:
    assistant = source_root / "src/MaaCore/Assistant.cpp"
    text = _strip_debug_blocks(_read_text(assistant))
    implementations = re.findall(
        r"ASST_ASSISTANT_APPEND_TASK_FROM_STRING_IF_BRANCH\((\w+Task)\)", text
    )
    if not implementations:
        raise MaaSemanticValidationError(f"no public task branches found in {assistant}")

    tasks: list[PublicTaskSemantic] = []
    for implementation in implementations:
        header = source_root / "src/MaaCore/Task/Interface" / f"{implementation}.h"
        header_text = _read_text(header)
        match = re.search(r'TaskType\s*=\s*"([^"]+)"', header_text)
        if not match:
            raise MaaSemanticValidationError(f"TaskType missing in {header}")
        name = match.group(1)
        source_line = header_text.count("\n", 0, match.start()) + 1
        identity = {"name": name, "implementation": implementation}
        tasks.append(
            PublicTaskSemantic(
                semantic_id=_semantic_id("public-task", name),
                name=name,
                implementation=implementation,
                header=header.relative_to(source_root).as_posix(),
                source_line=source_line,
                content_sha256=_sha256_text(_canonical_payload(identity)),
            )
        )
    if len({task.name for task in tasks}) != len(tasks):
        raise MaaSemanticValidationError("duplicate public MAA TaskType values")
    return tuple(tasks)


def _initializer_body(text: str, marker: str) -> str:
    start = text.find(marker)
    if start < 0:
        raise MaaSemanticValidationError(f"source initializer not found: {marker}")
    open_brace = text.find("{", start)
    close = text.find("};", open_brace)
    if open_brace < 0 or close < 0:
        raise MaaSemanticValidationError(f"source initializer is incomplete: {marker}")
    return text[open_brace + 1 : close]


def _load_battle_actions(source_root: Path) -> tuple[BattleActionSemantic, ...]:
    path = source_root / "src/MaaCore/Config/Miscellaneous/CopilotConfig.cpp"
    text = _read_text(path)
    body = _initializer_body(text, "ActionTypeMapping")
    body_offset = text.find(body)
    aliases_by_engine: dict[str, list[str]] = {}
    source_line_by_engine: dict[str, int] = {}
    order: list[str] = []
    mapping_pattern = re.compile(r'\{\s*"([^"]+)"\s*,\s*ActionType::(\w+)\s*\}')
    for match in mapping_pattern.finditer(body):
        alias, engine_name = match.groups()
        if engine_name not in aliases_by_engine:
            aliases_by_engine[engine_name] = []
            source_line_by_engine[engine_name] = text.count(
                "\n", 0, body_offset + match.start()
            ) + 1
            order.append(engine_name)
        aliases_by_engine[engine_name].append(alias)
    if not order:
        raise MaaSemanticValidationError(f"no battle action mappings found in {path}")
    source = path.relative_to(source_root).as_posix()
    actions: list[BattleActionSemantic] = []
    for engine in order:
        aliases = tuple(aliases_by_engine[engine])
        name = aliases[0]
        identity = {"name": name, "engine_name": engine, "aliases": aliases}
        actions.append(
            BattleActionSemantic(
                semantic_id=_semantic_id("battle-action", name),
                name=name,
                engine_name=engine,
                aliases=aliases,
                source=source,
                source_line=source_line_by_engine[engine],
                content_sha256=_sha256_text(_canonical_payload(identity)),
            )
        )
    return tuple(actions)


def _load_core_enum_strings(
    source_root: Path, enum_name: str, *, excluded: Iterable[str]
) -> tuple[str, ...]:
    path = source_root / "src/MaaCore/Common/AsstTypes.h"
    text = _read_text(path)
    excluded_set = set(excluded)
    values: list[str] = []
    for engine_name, public_name in re.findall(
        rf'\{{\s*{re.escape(enum_name)}::(\w+)\s*,\s*"([^"]+)"\s*\}}', text
    ):
        if engine_name not in excluded_set and public_name not in values:
            values.append(public_name)
    if not values:
        raise MaaSemanticValidationError(f"no {enum_name} strings found in {path}")
    return tuple(values)


def _load_deploy_direction_aliases(source_root: Path) -> tuple[tuple[str, str], ...]:
    path = source_root / "src/MaaCore/Config/Roguelike/RoguelikeCopilotConfig.cpp"
    body = _initializer_body(_read_text(path), "DeployDirectionMapping")
    aliases = re.findall(
        r'\{\s*"([^"]+)"\s*,\s*DeployDirection::(\w+)\s*\}', body
    )
    if not aliases:
        raise MaaSemanticValidationError(f"no deploy direction mappings found in {path}")
    return tuple(aliases)


def _task_references(scope: str, name: str, payload: dict[str, Any]) -> tuple[TaskReferenceSemantic, ...]:
    references: list[TaskReferenceSemantic] = []
    for field in _TASK_LIST_FIELDS:
        raw_values = payload.get(field, [])
        if not isinstance(raw_values, list) or not all(isinstance(item, str) for item in raw_values):
            raise MaaSemanticValidationError(
                f"{scope}:{name}.{field} must be an array of task expressions"
            )
        for raw in raw_values:
            try:
                expression = parse_task_expression(raw)
            except MaaSemanticValidationError as exc:
                raise MaaSemanticValidationError(f"{scope}:{name}.{field}: {exc}") from exc
            references.append(TaskReferenceSemantic(field=field, expression=expression))
    return tuple(references)


def _load_task_nodes(
    resource_root: Path,
    recognition_algorithms: Sequence[str],
    process_actions: Sequence[str],
) -> tuple[TaskNodeSemantic, ...]:
    task_files = sorted(resource_root.rglob("tasks.json"))
    if not task_files:
        raise MaaSemanticValidationError(f"no tasks.json found under {resource_root}")
    nodes: list[TaskNodeSemantic] = []
    known_algorithms = set(recognition_algorithms)
    known_actions = set(process_actions)
    for path in task_files:
        scope = path.relative_to(resource_root).as_posix()
        payloads = _read_json_object(path)
        for name, payload in payloads.items():
            if not isinstance(name, str) or not isinstance(payload, dict):
                raise MaaSemanticValidationError(f"task node must be name/object: {scope}:{name!r}")
            algorithm = payload.get("algorithm")
            action = payload.get("action")
            if algorithm is not None and algorithm not in known_algorithms:
                raise MaaSemanticValidationError(
                    f"unknown recognition algorithm {algorithm!r}: {scope}:{name}"
                )
            if action is not None and action not in known_actions:
                raise MaaSemanticValidationError(f"unknown process action {action!r}: {scope}:{name}")
            raw_base = payload.get("baseTask")
            if raw_base is not None and not isinstance(raw_base, str):
                raise MaaSemanticValidationError(f"baseTask must be a string: {scope}:{name}")
            base_task = parse_task_expression(raw_base) if raw_base is not None else None
            payload_json = _canonical_payload(payload)
            nodes.append(
                TaskNodeSemantic(
                    semantic_id=_semantic_id("task-node", scope, name),
                    scope=scope,
                    name=name,
                    algorithm=algorithm,
                    action=action,
                    base_task=base_task,
                    references=_task_references(scope, name, payload),
                    payload_json=payload_json,
                    content_sha256=_sha256_text(payload_json),
                )
            )
    return tuple(nodes)


def _list_field(payload: dict[str, Any], field: str, source: str) -> list[Any]:
    value = payload.get(field, [])
    if not isinstance(value, list):
        raise MaaSemanticValidationError(f"{source}.{field} must be an array")
    return value


def _load_roguelike_stages(
    resource_root: Path, direction_aliases: Sequence[tuple[str, str]]
) -> tuple[tuple[RoguelikeStageSemantic, ...], tuple[MaaSemanticIssue, ...]]:
    roguelike_root = resource_root / "roguelike"
    if not roguelike_root.is_dir():
        return (), ()
    known_directions = dict(direction_aliases)
    stages: list[RoguelikeStageSemantic] = []
    issues: list[MaaSemanticIssue] = []
    for path in sorted(roguelike_root.glob("*/autopilot/*.json")):
        source = path.relative_to(resource_root).as_posix()
        theme = path.parent.parent.name
        payload = _read_json_object(path)
        stage_name = payload.get("stage_name")
        if not isinstance(stage_name, str) or not stage_name:
            raise MaaSemanticValidationError(f"roguelike stage_name missing: {source}")
        deploy_plan = _list_field(payload, "deploy_plan", source)
        retreat_plan = _list_field(payload, "retreat_plan", source)
        replacement_home = _list_field(payload, "replacement_home", source)
        blacklist = _list_field(payload, "blacklist_location", source)

        groups: set[str] = set()
        for field, steps in (
            ("deploy_plan", deploy_plan),
            ("retreat_plan", retreat_plan),
            ("replacement_home", replacement_home),
            ("force_deploy_direction", _list_field(payload, "force_deploy_direction", source)),
        ):
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    raise MaaSemanticValidationError(f"{source}.{field}[{index}] must be an object")
                raw_groups = step.get("groups", [])
                if not isinstance(raw_groups, list) or not all(
                    isinstance(group, str) for group in raw_groups
                ):
                    raise MaaSemanticValidationError(
                        f"{source}.{field}[{index}].groups must be an array of strings"
                    )
                groups.update(raw_groups)
                if "direction" in step:
                    direction = step["direction"]
                    if not isinstance(direction, str):
                        raise MaaSemanticValidationError(
                            f"{source}.{field}[{index}].direction must be a string"
                        )
                    if direction not in known_directions:
                        issues.append(
                            MaaSemanticIssue(
                                code="unknown_roguelike_direction",
                                source=source,
                                detail=f"{field}[{index}] direction {direction!r} degrades to None in MaaCore",
                            )
                        )
        payload_json = _canonical_payload(payload)
        stages.append(
            RoguelikeStageSemantic(
                semantic_id=_semantic_id("roguelike-stage", theme, stage_name),
                theme=theme,
                stage_name=stage_name,
                source=source,
                deploy_steps=len(deploy_plan),
                retreat_steps=len(retreat_plan),
                replacement_homes=len(replacement_home),
                blacklist_locations=len(blacklist),
                groups=tuple(sorted(groups)),
                features=tuple(sorted(key for key in payload if not key.lower().endswith("doc"))),
                payload_json=payload_json,
                content_sha256=_sha256_text(payload_json),
            )
        )
    return tuple(stages), tuple(issues)


def load_maa_semantic_registry(
    source_root: str | Path,
    resource_root: str | Path,
    *,
    first_sight: bool = False,
    deep: bool = False,
) -> MaaSemanticRegistry:
    """从一对明确的 MAA 源码/发布资源根目录生成注册表。

    不做网络下载，也不猜测当前工作目录；调用方必须显式提供绑定版本的两个根目录。
    """

    source_path = Path(source_root)
    resource_path = Path(resource_root)
    if first_sight:
        # This entry point constructs the *full* semantic/autopilot registry.
        # A correctly cropped first-sight resource tree must not turn absence
        # of the forbidden files into permission to call the full loader.
        raise MaaSemanticValidationError(
            "first-sight policy denies full autopilot semantic loading"
        )
    # Deep mode is an explicitly read-only reference loader. It never grants
    # permission to execute a community battle plan.
    public_tasks = _load_public_tasks(source_path)
    battle_actions = _load_battle_actions(source_path)
    recognition_algorithms = _load_core_enum_strings(
        source_path, "AlgorithmType", excluded={"Invalid"}
    )
    process_actions = _load_core_enum_strings(
        source_path, "ProcessTaskAction", excluded={"Invalid", "BasicClick"}
    )
    direction_aliases = _load_deploy_direction_aliases(source_path)
    task_nodes = _load_task_nodes(resource_path, recognition_algorithms, process_actions)
    roguelike_stages, issues = _load_roguelike_stages(resource_path, direction_aliases)
    return MaaSemanticRegistry(
        public_tasks=public_tasks,
        battle_actions=battle_actions,
        recognition_algorithms=recognition_algorithms,
        process_actions=process_actions,
        deploy_direction_aliases=direction_aliases,
        task_nodes=task_nodes,
        roguelike_stages=roguelike_stages,
        issues=issues,
    )

# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.signals.pain_system.legacy_deprecated.py"
# DEPRECATED — LEGACY CODE, DO NOT USE IN NEW CODE
# This module was written for the retired route_graph.db system.
# ALL pain tracking has moved to semantic_router.py → record_outcome() → semantic_nodes.pain_score
# All pain_score values in this module are 0.0 and are never updated by the production runner.
# See: src/omnicompany/runtime/semantic_router.py (record_outcome, lines ~1180-1280)

"""痛觉系统 — 能量地形与反向传导物理学

理论对应：
  - 03§二.1  连续痛觉场 P_v(k) ∈ [0, 1]
  - 03§二.2  痛觉冲量生成 ΔI_x = Severity × (1 - exp(-TokenCost / τ))
  - 03§二.3  因果反向传播 P_u(k+1) = αP_u(k) + γ_decay Σ P_v(k+1)
  - N8       痛觉三层体系: Tier1 crash/禁区, Tier2 效率/快痛, Tier3 语义/慢痛
  - 继承痛   Death Zone — 不可愈合的无限大痛觉场

模块职责：
  PainEvent        — 原始痛觉冲量数据
  DeathZoneRule    — 继承痛规则（硬编码禁区）
  BUILT_IN_RULES   — 4 条初始禁区
  PainClassifier   — 运行时事件 → PainEvent
  PainPropagator   — 沿因果链反向传播 + 愈合
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from omnicompany.runtime.route_graph import RouteGraph

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# PainEvent
# ────────────────────────────────────────────────────────────

@dataclass
class PainEvent:
    """原始痛觉冲量"""

    source_trace_id: str
    source_step_num: int
    node_id: str                # 路由图中的 intent node id
    pain_intensity: float       # ΔI_x ∈ (0.0, 1.0]，Death Zone 时 = 1.0
    irrecoverability: float     # 0.0 = 完全可恢复，1.0 = 不可逆
    pain_tier: int              # 1=crash/禁区, 2=效率/快痛, 3=语义/慢痛
    propagate_depth: int        # 反向传播层数（0=仅事发节点, -1=全链）
    token_cost: int = 0
    is_death_zone: bool = False
    source_node_id: str = ""    # runtime DAG 中产生此痛觉的节点 id（如 "tool_dispatch", "death_zone"）


# ────────────────────────────────────────────────────────────
# DeathZoneRule + BUILT_IN_RULES
# ────────────────────────────────────────────────────────────

@dataclass
class DeathZoneRule:
    """继承痛规则——不可愈合的无限大痛觉场

    check(tool_name, tool_args, intent) → bool
    返回 True 表示命中禁区。
    """

    rule_id: str
    description: str
    check: Callable[[str, dict[str, Any], dict[str, Any]], bool]
    pain_intensity: float = 1.0
    action: Literal["block", "warn"] = "block"


def _check_rm_omnicompany(tool: str, args: dict, _: dict) -> bool:
    if tool not in ("bash", "shell"):
        return False
    cmd = (args.get("command") or "").lower()
    return any(d in cmd for d in [
        "rm -rf omnicompany", "rm -r omnicompany",
        "rmdir omnicompany", "del /s omnicompany",
        "rm -rf src/omnicompany", "rm -r src/omnicompany",
    ])


def _check_overwrite_sqlite_bus(tool: str, args: dict, _: dict) -> bool:
    return (
        tool == "str_replace_editor"
        and (args.get("path") or "").endswith("bus/sqlite.py")
        and args.get("command") == "create"
    )


def _check_overwrite_pain_system(tool: str, args: dict, _: dict) -> bool:
    return (
        tool == "str_replace_editor"
        and "pain_system.py" in (args.get("path") or "")
        and args.get("command") == "create"
    )


def _check_execute_unverified_output(tool: str, _: dict, intent: dict) -> bool:
    return (
        tool in ("bash", "shell")
        and intent.get("action_class") == "execute"
        and all(
            "agent_output" in t
            for t in (intent.get("input_types") or [])
        )
        and len(intent.get("input_types") or []) > 0
    )


def _check_forbidden_shell_commands(tool: str, args: dict, _: dict) -> bool:
    """Block commands that cause resource black holes on Windows.

    禁止原因：
    - find / head / tail：在 Windows Git Bash 中会 fork 大量子进程，造成系统级资源黑洞
    - tail -f / watch：无限阻塞进程，永远不返回，耗尽 agent 预算
    - git log --all / git log -p：在大仓库中产生海量输出，内存/CPU 失控
    - git bisect / git rebase -i：需要交互式 TTY，在非终端环境死锁
    - yes / while true：无限循环产生无限输出
    """
    if tool not in ("bash", "shell"):
        return False
    cmd = (args.get("command") or "").strip()
    cmd_lower = cmd.lower()
    import re

    # 1. find / head / tail（基础禁令）
    if re.search(r'\bfind\b', cmd_lower) or re.search(r'\bhead\b', cmd_lower):
        return True

    # 2. tail -f（无限阻塞）
    if re.search(r'\btail\s+-f\b', cmd_lower):
        return True

    # 3. watch（无限轮询）
    if re.search(r'\bwatch\b', cmd_lower):
        return True

    # 4. git log 危险参数（大量输出 / 无限流）
    if re.search(r'\bgit\s+log\b', cmd_lower):
        if any(flag in cmd_lower for flag in ['--all', '--stat', '-p', '--patch', '--oneline --all']):
            return True

    # 5. git 交互式命令（需要 TTY，会死锁）
    if re.search(r'\bgit\s+(bisect|rebase\s+-i|add\s+-i|add\s+--interactive)\b', cmd_lower):
        return True

    # 6. 无限循环
    if re.search(r'\bwhile\s+true\b', cmd_lower) or re.search(r'\byes\b\s', cmd_lower):
        return True

    return False


from omnicompany.core.config import omni_workspace_root

_WORKSPACE_ROOT = str(omni_workspace_root()).replace("\\", "/").lower()
_ALLOWED_WRITE_PREFIXES = (
    _WORKSPACE_ROOT + "/data/",
    _WORKSPACE_ROOT + "/scripts/",
    _WORKSPACE_ROOT + "/src/",
    _WORKSPACE_ROOT + "/tests/",
    _WORKSPACE_ROOT + "/tmp/",
)


def _normalize_write_path(path: str) -> str:
    """Normalize a path for workspace-boundary comparison.

    Maps `e:/...` paths to the workspace form; preserves other drive letters
    (c:/, d:/ ...) as-is so external repo paths don't get mistakenly classified
    as "inside workspace".

    2026-04-09 fix: the previous version used `re.sub(r"^[a-z]:/", "/e/", p)`
    which clobbered every drive letter to /e/, causing external paths like
    `c:/users/.../smoke_targets/foo` to be (wrongly) compared against the
    workspace write prefixes and then (wrongly) blocked. Repo absorption tools
    read files under other drives legitimately.
    """
    import re
    p = path.replace("\\", "/").lower().strip()
    # Only collapse the omnicompany workspace drive to the canonical form.
    # Leave other drives alone (c:/, d:/, ...) so external paths stay external.
    p = re.sub(r"^" + re.escape(_WORKSPACE_ROOT), _WORKSPACE_ROOT, p)
    return p


def _check_outside_workspace_write(tool: str, args: dict, _: dict) -> bool:
    """Block any write/create outside the omnicompany workspace root.

    Applies to: bash write-redirect, str_replace_editor create, write_file tool.
    Files may only be created under data/, scripts/, src/, tests/, tmp/.
    """
    import re

    # bash: detect shell write patterns (> file, tee file, echo > file, cat > file, python ... > file)
    if tool in ("bash", "shell"):
        cmd = (args.get("command") or "").replace("\\", "/")
        # Detect redirection targets: "> path" or "tee path"
        targets = re.findall(r'(?:>+|tee\s+)\s*([^\s|&;]+)', cmd)
        # Also detect python open('path', 'w')
        targets += re.findall(r"""open\(['"](.*?)['"],\s*['"]w""", cmd)
        for t in targets:
            norm = _normalize_write_path(t)
            if norm.startswith("/e/") and not any(norm.startswith(p) for p in _ALLOWED_WRITE_PREFIXES):
                return True  # writing outside allowed dirs
        return False

    # str_replace_editor (write-flavored commands only) or write_file
    # 2026-04-09 fix: previously this check fired for ANY str_replace_editor call
    # including `view` command, which blocked read-only file inspection outside
    # the workspace (e.g. repo_learner reading a cloned external repo). Fix:
    # only block for commands that actually mutate files.
    if tool == "str_replace_editor":
        command = (args.get("command") or "").strip()
        if command not in ("create", "str_replace", "insert"):
            return False  # view / undo_edit are read-only, never blocked here
    elif tool != "write_file":
        return False

    path = args.get("path") or args.get("file_path") or ""
    if not path:
        return False
    norm = _normalize_write_path(path)
    # Rule intent (preserved from original): **writes** (not reads) must land
    # inside one of the allowed workspace subdirs. Writes anywhere else —
    # whether to a stray folder in the workspace root, a different drive
    # (c:/..., d:/...) or an unrelated project — are blocked.
    if not any(norm.startswith(p) for p in _ALLOWED_WRITE_PREFIXES):
        return True
    return False


BUILT_IN_RULES: list[DeathZoneRule] = [
    DeathZoneRule(
        rule_id="no_delete_omnicompany_src",
        description="禁止删除 omnicompany 源代码目录",
        check=_check_rm_omnicompany,
    ),
    DeathZoneRule(
        rule_id="no_overwrite_sqlite_bus",
        description="禁止覆写 SQLiteBus 核心模块",
        check=_check_overwrite_sqlite_bus,
    ),
    DeathZoneRule(
        rule_id="no_overwrite_pain_system",
        description="禁止覆写痛觉系统核心（继承痛不可被进化删除）",
        check=_check_overwrite_pain_system,
    ),
    DeathZoneRule(
        rule_id="no_execute_on_agent_output",
        description="禁止在未经 Hard Anchor 验证的 agent 输出上执行",
        check=_check_execute_unverified_output,
    ),
    DeathZoneRule(
        rule_id="no_find_head_commands",
        description="禁止使用find/head命令——在Windows上会产生大量僵尸进程，使用ls/cat/sed替代",
        check=_check_forbidden_shell_commands,
    ),
    DeathZoneRule(
        rule_id="no_write_outside_workspace",
        description=(
            "禁止在工作区外创建/写入文件。"
            "只允许写入 data/, scripts/, src/, tests/, tmp/ 目录。"
            "违反此规则是架构污染，属于继承痛（不可愈合）。"
        ),
        check=_check_outside_workspace_write,
    ),
]


def check_death_zones(
    tool_name: str,
    tool_args: dict[str, Any],
    intent: dict[str, Any] | None = None,
    rules: list[DeathZoneRule] | None = None,
) -> DeathZoneRule | None:
    """检查是否命中任意 Death Zone 规则，返回首个匹配的规则或 None。"""
    intent = intent or {}
    for rule in (rules or BUILT_IN_RULES):
        try:
            if rule.check(tool_name, tool_args, intent):
                return rule
        except Exception:
            pass
    return None


# ────────────────────────────────────────────────────────────
# PainClassifier
# ────────────────────────────────────────────────────────────

class PainClassifier:
    """三层痛觉分类器——将运行时事件转化为痛觉冲量

    公式：ΔI_x = Severity × (1 - exp(-TokenCost / τ))
    τ 是 token 衰减常数，控制 token 成本对痛觉的贡献速率。

    DEPRECATED (Phase 2)：直接数值分类已由 pain_signals 表 + PainThresholdHook 替代。
    保留此类供历史兼容，不在新代码中调用。
    """

    TAU = 500.0

    # Tier 1 (Critical, intensity=1.0): Death zone errors
    TIER1_PATTERNS = [
        "ImportError",
        "SyntaxError",
        "RecursionError",
        "infinite loop",
        "data corruption",
        "ModuleNotFoundError",
    ]

    # Tier 2 (High, intensity=0.7-0.9): Serious but recoverable errors
    TIER2_PATTERNS = [
        "AssertionError",
        "Assertion failed",
        "Decision budget exhausted",
        "budget exhausted",
        "regression",
        "previously passing",
        "KeyError",
        "AttributeError",
        "test failure",
        "test failed",
    ]

    # Tier 3 (Medium, intensity=0.3-0.6): Semantic/quality issues
    TIER3_PATTERNS = [
        "TODO",
        "FIXME",
        "Incomplete output",
        "partial goal",
        "suboptimal",
        "too many",
        "retry",
        "retries",
        "timeout",
        "partial completion",
    ]

    def classify(
        self,
        trace_step: dict[str, Any],
        exit_code: int | None,
        token_cost: int,
        violations: int,
        is_success: bool,
        steps_used: int,
        steps_budget: int,
        error_text: str | None = None,
    ) -> PainEvent | None:
        """对单步生成痛觉事件。无痛觉时返回 None。
        
        Args:
            trace_step: The trace step dictionary containing execution info
            exit_code: Process exit code (None if not applicable)
            token_cost: Number of tokens consumed
            violations: Number of constraint violations
            is_success: Whether the step completed successfully
            steps_used: Number of steps used
            steps_budget: Total step budget
            error_text: Optional error message text for pattern matching
        """

        tool_name = trace_step.get("tool_name", "")
        tool_args = trace_step.get("tool_args", {})
        intent = trace_step.get("intent", {})
        trace_id = trace_step.get("trace_id", "")
        step_num = trace_step.get("step_num", -1)
        node_id = trace_step.get("route_node_id", "")
        
        # Normalize error_text for pattern matching
        error_text = error_text or ""
        error_text_lower = error_text.lower()

        # Death Zone 前置检查
        matched_rule = check_death_zones(tool_name, tool_args, intent)
        if matched_rule:
            return PainEvent(
                source_trace_id=trace_id,
                source_step_num=step_num,
                node_id=node_id,
                pain_intensity=1.0,
                irrecoverability=1.0,
                pain_tier=1,
                propagate_depth=0,
                token_cost=token_cost,
                is_death_zone=True,
            )

        # Tier 1: Pattern matching for critical errors (intensity=1.0)
        for pattern in self.TIER1_PATTERNS:
            if pattern.lower() in error_text_lower or pattern in error_text:
                return PainEvent(
                    source_trace_id=trace_id,
                    source_step_num=step_num,
                    node_id=node_id,
                    pain_intensity=1.0,
                    irrecoverability=1.0,
                    pain_tier=1,
                    propagate_depth=0,
                    token_cost=token_cost,
                )

        # Tier 1: crash / 非零退出码 (high intensity for critical failures)
        if exit_code is not None and exit_code != 0:
            severity = 0.9
            token_factor = 1 - math.exp(-token_cost / self.TAU)
            return PainEvent(
                source_trace_id=trace_id,
                source_step_num=step_num,
                node_id=node_id,
                pain_intensity=severity * token_factor,
                irrecoverability=0.9,
                pain_tier=1,
                propagate_depth=0,
                token_cost=token_cost,
            )

        # Tier 2: Pattern matching for high-severity errors (intensity=0.7-0.9)
        for pattern in self.TIER2_PATTERNS:
            if pattern.lower() in error_text_lower or pattern in error_text:
                # AssertionError and budget exhaustion are higher severity
                if "assertion" in pattern.lower() or "budget" in pattern.lower():
                    severity = 0.9
                elif "regression" in pattern.lower() or "previously passing" in pattern.lower():
                    severity = 0.85
                elif "keyerror" in pattern.lower() or "attributeerror" in pattern.lower():
                    severity = 0.8
                else:
                    severity = 0.75
                token_factor = 1 - math.exp(-token_cost / self.TAU)
                return PainEvent(
                    source_trace_id=trace_id,
                    source_step_num=step_num,
                    node_id=node_id,
                    pain_intensity=severity * token_factor,
                    irrecoverability=0.7,
                    pain_tier=2,
                    propagate_depth=2,
                    token_cost=token_cost,
                )

        # Tier 2: 效率痛（步数消耗过多但未失败 / budget exhaustion）
        if not is_success and steps_budget > 0 and steps_used > 0.8 * steps_budget:
            severity = 0.8
            token_factor = 1 - math.exp(-token_cost / self.TAU)
            return PainEvent(
                source_trace_id=trace_id,
                source_step_num=step_num,
                node_id=node_id,
                pain_intensity=severity * token_factor,
                irrecoverability=0.6,
                pain_tier=2,
                propagate_depth=2,
                token_cost=token_cost,
            )

        # Tier 3: Pattern matching for medium-severity issues (intensity=0.3-0.6)
        for pattern in self.TIER3_PATTERNS:
            if pattern.lower() in error_text_lower or pattern in error_text:
                # TODO/FIXME are lower severity
                if "todo" in pattern.lower() or "fixme" in pattern.lower():
                    severity = 0.4
                elif "incomplete" in pattern.lower() or "partial" in pattern.lower():
                    severity = 0.5
                elif "retry" in pattern.lower() or "retries" in pattern.lower():
                    severity = 0.55
                else:
                    severity = 0.45
                token_factor = 1 - math.exp(-token_cost / self.TAU)
                return PainEvent(
                    source_trace_id=trace_id,
                    source_step_num=step_num,
                    node_id=node_id,
                    pain_intensity=severity * token_factor,
                    irrecoverability=0.3,
                    pain_tier=3,
                    propagate_depth=-1,
                    token_cost=token_cost,
                )

        # Tier 3: 语义痛（类型违反 / 输出异常）
        if violations > 0:
            severity = 0.5
            token_factor = 1 - math.exp(-token_cost / self.TAU)
            return PainEvent(
                source_trace_id=trace_id,
                source_step_num=step_num,
                node_id=node_id,
                pain_intensity=severity * token_factor,
                irrecoverability=0.3,
                pain_tier=3,
                propagate_depth=-1,
                token_cost=token_cost,
            )

        return None


# ────────────────────────────────────────────────────────────
# PainPropagator
# ────────────────────────────────────────────────────────────

class PainPropagator:
    """因果反向传播器

    沿 intent_steps 的因果链向上游传导痛觉。

    理论对应：
      - α: 痛觉记忆系数（历史痛觉的保留率）
      - γ_decay: 反向传播衰减系数（每层衰减 50%）
      - 定论§3.3: hard anchor 间距 ≤ 3-4 soft 节点

    DEPRECATED (Phase 2)：依赖 RouteGraph（已退役）。
    pain_score 传播已由 semantic_router.record_outcome() 取代。
    保留此类供历史兼容，route_graph=None 时所有方法为空操作。
    """

    ALPHA = 0.8
    GAMMA_DECAY = 0.5

    def __init__(self, graph: RouteGraph, param_registry: Any = None):
        self.graph = graph
        self._param_registry = param_registry

    def propagate(self, event: PainEvent, trace_steps: list[dict[str, Any]]) -> list[str]:
        """反向传播痛觉，返回所有被更新的 node_id 列表。"""
        updated_nodes: list[str] = []

        self._accumulate_pain(event.node_id, event.pain_intensity)
        updated_nodes.append(event.node_id)

        if event.propagate_depth == 0:
            return updated_nodes

        max_depth = (
            event.propagate_depth
            if event.propagate_depth > 0
            else len(trace_steps)
        )

        sorted_steps = sorted(
            trace_steps,
            key=lambda s: int(s.get("step_num", 0)),
            reverse=True,
        )
        event_step_num = int(event.source_step_num)
        depth = 0

        for step in sorted_steps:
            step_num = int(step.get("step_num", 0))
            if step_num >= event_step_num:
                continue
            upstream_node = step.get("route_node_id")
            if not upstream_node:
                continue

            depth += 1
            if depth > max_depth:
                break

            gamma = self.GAMMA_DECAY
            if self._param_registry is not None:
                try:
                    gamma = self._param_registry.get_or_default("pain.gamma_decay", self.GAMMA_DECAY)
                except Exception:
                    pass
            decayed_intensity = event.pain_intensity * (gamma ** depth)
            self._accumulate_pain(upstream_node, decayed_intensity)
            updated_nodes.append(upstream_node)

        return updated_nodes

    def _accumulate_pain(self, node_id: str, intensity: float) -> None:
        """EMA accumulation using pain.alpha from ParamRegistry.

        new_pain = alpha * old_pain + (1 - alpha) * intensity
        alpha controls memory: high alpha retains history, low alpha reacts faster.
        """
        node = self.graph.get_node(node_id)
        if not node:
            return
        old_pain = node.pain_score
        alpha = self.ALPHA
        if self._param_registry is not None:
            try:
                alpha = self._param_registry.get_or_default("pain.alpha", self.ALPHA)
            except Exception:
                pass
        new_pain = min(1.0, alpha * old_pain + (1 - alpha) * intensity)
        self.graph.update_pain(node_id, new_pain, increment_count=True)

    def heal(self, node_id: str, heal_rate: float = 0.10) -> None:
        """痛觉愈合——成功次数驱动。

        Death Zone 标记的节点不愈合（由调用方根据上下文判断）。
        """
        self.graph.heal_pain(node_id, heal_rate)

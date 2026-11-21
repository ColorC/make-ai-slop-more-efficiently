# [OMNI] origin=codex domain=omnicompany/guardian ts=2026-06-13T04:20:00+08:00 type=config
# [OMNI] material_id="material:core.guardian.rules.authority_convergence.scanner.py"
"""Guardian 规则 — 唯一权威收束防漂移 (OMNI-093).

本规则家族守护 2026-06-13 LLM-CALL-UNIFICATION 收束决断:

- `authority-confirmation.md` 是设施统一方向权威, 必须 active。
- `autonomous-execution-rules.md` 是长程执行门禁权威, 必须指回确认表。
- README / standards / templates / guardian SKILL 等分散入口只能指向权威, 不能另造一套。

## 定位 (2026-06-13 用户裁决: 语义判断用性价比模型 agent 为主, 规则是批量规律的结晶)

本规则族是**确定性兜底**: 只查"特定文件里特定标记在不在"(status=active / 锚点字串),
不做语义判断。**全面的权威漂移语义判断由治理部门 doc_steward(性价比模型)承担**
(docs/standards/concepts/governance_semantic_first.md)。规则只在 content 真实可读时判定;
content 不可用(full_scan 未加载 / 文件删除)时一律不违规, 避免凭空误报。
"""
from __future__ import annotations

from ._base import FileContext, GuardianRule


_PLAN_ROOT = "docs/plans/agent-framework/[2026-06-13]LLM-CALL-UNIFICATION/"
_AUTHORITY_PATH = _PLAN_ROOT + "authority-confirmation.md"
_AUTONOMOUS_PATH = _PLAN_ROOT + "autonomous-execution-rules.md"
_AUTHORITY_MARKER = "authority-confirmation.md"
_AUTONOMOUS_MARKER = "autonomous-execution-rules.md"

_REQUIRED_AUTHORITY_TOKENS: tuple[str, ...] = (
    "status=active",
    "本表是本批设施统一工作的最高决断表",
    "MaterialDispatcher 转正",
    "protocol.Format + FormatRegistry.register",
    "runtime/llm/structured.py::call_json",
    "runtime/llm/batch.py",
    "EventBus 是 agent 事件权威记录面",
    "AuditTowWorker",
)

_REQUIRED_AUTONOMOUS_TOKENS: tuple[str, ...] = (
    _AUTHORITY_MARKER,
    "同一时刻只允许一个实施块",
    "真实路径测试",
    "omni guardian patrol",
    "OMNI-093",
)

_REQUIRED_SURFACES: frozenset[str] = frozenset({
    "README.md",
    "docs/standards/cli/llm_infrastructure.md",
    "docs/standards/concepts/material.md",
    "docs/standards/concepts/agent_first.md",
    "docs/standards/_global/distributed-docs.md",
    "docs/standards/protocol/plan_template.md",
    "templates/agent/向导.md",
    "templates/worker/向导.md",
    "templates/material/向导.md",
    "templates/plan/向导.md",
    "templates/template/向导.md",
    "src/omnicompany/packages/services/_core/guardian/README.md",
    "src/omnicompany/packages/services/_core/guardian/DESIGN.md",
    "src/omnicompany/packages/services/_core/guardian/SKILL.md",
})

def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _content(ctx: FileContext) -> str:
    return ctx.content or ""


def _check_authority_confirmation_not_active(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if p != _AUTHORITY_PATH:
        return False
    if ctx.content is None:  # content 未加载(full_scan 非 .py)→ 无法判定, 不误报
        return False
    text = _content(ctx)
    if "pending-confirmation" in text:
        return True
    return any(token not in text for token in _REQUIRED_AUTHORITY_TOKENS)


def _check_autonomous_rules_not_bound(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if p != _AUTONOMOUS_PATH:
        return False
    if ctx.content is None:
        return False
    text = _content(ctx)
    return any(token not in text for token in _REQUIRED_AUTONOMOUS_TOKENS)


def _check_required_surface_missing_authority(ctx: FileContext) -> bool:
    p = _norm(ctx.path)
    if p not in _REQUIRED_SURFACES:
        return False
    if ctx.content is None:  # 关键: full_scan 对 src/ 下 .md(guardian README/DESIGN/SKILL)不读 content → 此前凭空报 HIGH
        return False
    text = _content(ctx)
    return _AUTHORITY_MARKER not in text or _AUTONOMOUS_MARKER not in text


# 注(2026-06-13 裁决): 原 OMNI-093d `_check_unanchored_convergence_authority` 用字符串启发式
# 判"文档是否另立权威", 在 plan.md/project_index.md/self_narrative 等正当规范上误报 5 条 HIGH。
# 按 governance_semantic_first.md, 这种语义判断改由 doc_steward 的 competing_authority 语义类承担,
# 不再作为阻断规则。保留 093a/b/c 三条确定性检查。


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-093a",
        name="authority-confirmation-active",
        severity="HIGH",
        description=(
            "LLM-CALL-UNIFICATION 的 authority-confirmation.md 必须是 active 方向权威, "
            "不能退回 pending 或漏掉核心唯一权威决断。"
        ),
        check=_check_authority_confirmation_not_active,
        disposition=["warn", "stamp"],
        message_template=(
            "{path}: 唯一权威集中确认表未处于 active 完整状态。"
            "必须保留 active 状态与材料/LLM/agent/EventBus/AuditTowWorker 核心决断。"
        ),
    ),
    GuardianRule(
        id="OMNI-093b",
        name="autonomous-rules-bound-to-authority",
        severity="HIGH",
        description=(
            "autonomous-execution-rules.md 必须指回 authority-confirmation.md, "
            "并写明一块一验收、真实路径测试、OMNI-093 guard 验证。"
        ),
        check=_check_autonomous_rules_not_bound,
        disposition=["warn", "stamp"],
        message_template=(
            "{path}: 自主执行规范没有完整绑定集中确认表/测试门禁/OMNI-093 guard。"
        ),
    ),
    GuardianRule(
        id="OMNI-093c",
        name="distributed-surface-authority-anchor",
        severity="HIGH",
        description=(
            "README / standards / templates / guardian SKILL 等分散入口必须指向 "
            "authority-confirmation.md 与 autonomous-execution-rules.md, 不能各写一套权威。"
        ),
        check=_check_required_surface_missing_authority,
        disposition=["warn", "stamp"],
        message_template=(
            "{path}: 分散入口缺少 authority-confirmation.md 或 autonomous-execution-rules.md 锚点。"
            "只允许指向集中权威, 不允许复制另一套设施统一规则。"
        ),
    ),
]


__all__ = [
    "RULES",
    "_check_authority_confirmation_not_active",
    "_check_autonomous_rules_not_bound",
    "_check_required_surface_missing_authority",
]

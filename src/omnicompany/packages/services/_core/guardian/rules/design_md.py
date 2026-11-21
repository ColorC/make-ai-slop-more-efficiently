# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-17T00:00:00Z
# [OMNI] material_id="material:core.guardian.design_md_structure.checker.py"
"""Guardian 规则 — DESIGN.md 结构合规 (OMNI-034 家族)。

本规则扫所有 `DESIGN.md`，按自我叙事三件套规范
`docs/standards/protocol/self_narrative_three_files.md` §五 检查
（2026-06-13 用户裁决"规范冲突以新的为准"后从旧七节模板切换:
"核心目的"段归 README 承载, 不再是 DESIGN 必需节; 存量含核心目的不违规）。
模板细则见 `docs/standards/protocol/design_md_template.md`（从属于三件套规范）。

## 当前启用的规则（纯结构检查，regex 合理）

  OMNI-034a  缺 OmniMark 头                           HIGH
  OMNI-034b  status 字段不在枚举 skeleton/design/active/deprecated 之一  HIGH
  OMNI-034c  六个必需二级标题不齐全                    HIGH
  OMNI-034f  skeleton 文档 (INFO 统计，不算违规)       INFO
  OMNI-034g  基础设施模块 active/design 下缺 接收意愿 节  INFO

## 暂时禁用：等待 Guardian Agent LLM 巡逻实现后接入

  OMNI-034d  status=active 时含 TBD/待补充/TODO 标记   (原 HIGH)
  OMNI-034e  status=design/active 但架构决策全空       (原 MEDIUM)

**为什么禁用**：2026-04-17 用户指出"guardian 里加固定文本检测反而违反原则"。
034d / 034e 是语义判断（"文档看起来未完成" / "架构决策是否有实质内容"），
用字符串硬匹配（`<!-- TBD:` / `### D\\d+`）是 post-check，容易误报漏报。

**升级路径**（见 `docs/plans/[2026-04-17]OMNICOMPANY-SELF-KNOWLEDGE/HANDOFF.md` §三 M1）：
这两条检查移入 Guardian Agent 的 LLM 巡逻 backlog — agent 每周/每 commit 跑一次，
读 DESIGN.md 全文 + 源码对比，判断"这文档是否真的反映现状"，
产出"这些 DESIGN.md 看起来需要更新"告警（而非布尔违规）。

原实现保留为注释 / 死代码（_check_active_has_tbd / _check_decisions_empty_nonskeleton /
_TBD_PATTERNS / _decisions_empty），作为将来 Agent 巡逻的参考素材。

## 检查编号说明

  保留启用: 034a, 034b, 034c, 034f (纯结构)
  暂禁用:   034d, 034e (语义判断，待 Agent 巡逻)
"""
from __future__ import annotations

import re
from typing import Optional

from ._base import FileContext, GuardianRule, _has_content

# ── 六个必需的二级标题（文字硬约束; 三件套规范 §五）──
# "## 核心目的" 已移交 README（设计目的 ≠ 构成）; "## 内部构成" 仅有子模块时建议加, 不强制。
_REQUIRED_SECTIONS = [
    "## 状态",
    "## 核心接口",
    "## 架构决策",
    "## 数据流 / 拓扑",
    "## 已知局限",
    "## 参考资料",
]

# ── status 四选一 ──
_VALID_STATUS = {"skeleton", "design", "active", "deprecated"}

# ── TBD 标记模式 ──
_TBD_PATTERNS = [
    re.compile(r"<!--\s*TBD\s*[:：]"),
    re.compile(r"_待补充"),
    re.compile(r"TODO[:：]"),  # 也容忍常见 TODO 形式
]


# ─── 辅助 ────────────────────────────────────────────────────────────


def _is_design_md(ctx: FileContext) -> bool:
    """判定是否为 DESIGN.md 文档。"""
    if not _has_content(ctx):
        return False
    path = ctx.path.replace("\\", "/")
    return path.endswith("/DESIGN.md") or path == "DESIGN.md"


def _extract_status(content: str) -> Optional[str]:
    """从 OmniMark 头提取 status 字段，找不到返回 None。"""
    # 头必须在第一行 HTML 注释里：<!-- [OMNI] ... status=xxx ... -->
    first_line = content.splitlines()[0] if content else ""
    if "[OMNI]" not in first_line:
        return None
    m = re.search(r"status=([a-zA-Z]+)", first_line)
    return m.group(1) if m else None


def _has_omnimark(content: str) -> bool:
    first_line = content.splitlines()[0] if content else ""
    return "[OMNI]" in first_line


def _missing_sections(content: str) -> list[str]:
    """返回缺失的二级标题列表。"""
    missing = []
    for section in _REQUIRED_SECTIONS:
        if section not in content:
            missing.append(section)
    return missing


def _contains_tbd(content: str) -> bool:
    for pat in _TBD_PATTERNS:
        if pat.search(content):
            return True
    return False


def _decisions_empty(content: str) -> bool:
    """`架构决策` 节下是否有任何 `### D` 条目。"""
    # 抓 `## 架构决策` 到下一个 `## ` 之间的内容
    m = re.search(r"## 架构决策\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    if not m:
        return True  # 节都不在
    body = m.group(1)
    # 是否有 ### D1 / D2 / ... 条目
    return not re.search(r"###\s+D\d+\b", body)


# ─── 规则实现 ────────────────────────────────────────────────────────


def _check_missing_omnimark(ctx: FileContext) -> bool:
    if not _is_design_md(ctx):
        return False
    return not _has_omnimark(ctx.content or "")


def _check_invalid_status(ctx: FileContext) -> bool:
    if not _is_design_md(ctx):
        return False
    if not _has_omnimark(ctx.content or ""):
        return False  # 已由 _check_missing_omnimark 报
    status = _extract_status(ctx.content or "")
    return status is None or status not in _VALID_STATUS


def _check_missing_sections(ctx: FileContext) -> bool:
    if not _is_design_md(ctx):
        return False
    return len(_missing_sections(ctx.content or "")) > 0


def _check_active_has_tbd(ctx: FileContext) -> bool:
    if not _is_design_md(ctx):
        return False
    status = _extract_status(ctx.content or "")
    if status != "active":
        return False
    return _contains_tbd(ctx.content or "")


def _check_decisions_empty_nonskeleton(ctx: FileContext) -> bool:
    if not _is_design_md(ctx):
        return False
    status = _extract_status(ctx.content or "")
    if status not in {"design", "active"}:
        return False
    return _decisions_empty(ctx.content or "")


def _check_is_skeleton(ctx: FileContext) -> bool:
    """INFO 级：标记这份 DESIGN.md 是 skeleton，供 dashboard 统计。不算违规。"""
    if not _is_design_md(ctx):
        return False
    status = _extract_status(ctx.content or "")
    return status == "skeleton"


# ── 基础设施模块路径前缀（用于 OMNI-034g 判定） ──
_INFRASTRUCTURE_PREFIXES = (
    "src/omnicompany/runtime/",
    "src/omnicompany/protocol/",
    "src/omnicompany/core/",
    "src/omnicompany/bus/",
    "src/omnicompany/primitives/",
    "src/omnicompany/tools/",
    "src/omnicompany/tracing/",
)


def _is_infrastructure_module(ctx: FileContext) -> bool:
    """判定 DESIGN.md 是否属于基础设施模块（适用 OMNI-034g）。"""
    path = ctx.path.replace("\\", "/")
    return any(path.startswith(prefix) or ("/" + prefix) in ("/" + path)
               for prefix in _INFRASTRUCTURE_PREFIXES)


def _check_infrastructure_missing_reception(ctx: FileContext) -> bool:
    """INFO 级：基础设施模块在 active/design 状态下若缺第 8 节 ## 接收意愿，提醒。

    不对 domain 层 / skeleton / deprecated 检查；不阻塞。
    """
    if not _is_design_md(ctx):
        return False
    if not _is_infrastructure_module(ctx):
        return False
    status = _extract_status(ctx.content or "")
    if status not in {"active", "design"}:
        return False
    return "## 接收意愿" not in (ctx.content or "")


# ─── 规则清单 ────────────────────────────────────────────────────────


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-034a",
        name="design-md-missing-omnimark",
        severity="HIGH",
        description="DESIGN.md 第一行缺 [OMNI] 头（含 status 字段）",
        check=_check_missing_omnimark,
        disposition=["warn"],
        message_template=(
            "{path} 首行应为: "
            "<!-- [OMNI] origin=... domain=... ts=... type=doc status=... --> "
            "见 docs/standards/protocol/self_narrative_three_files.md"
        ),
    ),
    GuardianRule(
        id="OMNI-034b",
        name="design-md-invalid-status",
        severity="HIGH",
        description="DESIGN.md OmniMark 头 status 字段缺失或不在 skeleton/design/active/deprecated 之一",
        check=_check_invalid_status,
        disposition=["warn"],
        message_template=(
            "{path} status 字段无效。必须是 skeleton/design/active/deprecated 之一。"
        ),
    ),
    GuardianRule(
        id="OMNI-034c",
        name="design-md-missing-sections",
        severity="HIGH",
        description="DESIGN.md 缺失必需的二级标题",
        check=_check_missing_sections,
        disposition=["warn"],
        message_template=(
            "{path} 缺必需二级标题。需齐全: "
            "## 状态 / ## 核心接口 / ## 架构决策 / "
            "## 数据流 / 拓扑 / ## 已知局限 / ## 参考资料"
            "（核心目的归 README; 见 self_narrative_three_files.md §五）"
        ),
    ),
    # ─── OMNI-034d / OMNI-034e：语义判断规则已禁用 ──────────────────────────
    # 原实现：用固定字符串/正则判断"文档看起来未完成"或"决策节是否有实质内容"。
    # 2026-04-17 用户指出此类固定文本检测违反 llm_first 原则；需移入 Guardian Agent
    # LLM 巡逻 backlog。保留 _check_active_has_tbd / _check_decisions_empty_nonskeleton
    # 函数为死代码，作为将来 Agent 巡逻的判据参考。
    #
    # TODO(LLM-patrol): 把 034d / 034e 迁移到 Guardian Agent 的 LLM 巡逻任务，
    #                   让 LLM 读 DESIGN.md 全文 + 相关源码，判断"是否真反映现状"。
    # ─────────────────────────────────────────────────────────────────
    GuardianRule(
        id="OMNI-034f",
        name="design-md-is-skeleton",
        severity="INFO",
        description="标记 DESIGN.md 为 skeleton 状态（统计信号，非违规）",
        check=_check_is_skeleton,
        disposition=["info"],
        message_template="{path} 是 skeleton，多数节尚未填充。",
    ),
    GuardianRule(
        id="OMNI-034g",
        name="design-md-reception-intent-check",
        severity="INFO",
        description="基础设施模块（runtime/protocol/core/bus/primitives/tools/tracing）active/design 下建议填第 8 节 ## 接收意愿",
        check=_check_infrastructure_missing_reception,
        disposition=["info"],
        message_template=(
            "{path} 建议添加 ## 接收意愿 节"
            "（welcome_themes / hard_constraints / soft_preferences / maturity_preference）。"
            "见 docs/standards/protocol/design_md_template.md §九。"
        ),
    ),
]

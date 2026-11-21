# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.rules_shared_types.engine.py"
"""Guardian 规则共享类型与 helper。

所有规则模块从此处导入 FileContext / GuardianRule / Violation / 共用 helper。
不在此处定义任何具体规则。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal, Optional

logger = logging.getLogger(__name__)


# ─── 数据结构 ──────────────────────────────────────────────────


@dataclass
class FileContext:
    """规则引擎的输入单元：单个文件的上下文。"""

    path: str                        # 相对项目根的路径（/ 分隔）
    abs_path: str                    # 绝对路径
    change_type: str                 # "A"（新增）/ "M"（修改）/ "D"（删除）/ "?"（未跟踪）
    content: Optional[str]           # 文件内容（删除时为 None）
    omnimark: Optional[dict] = None  # 解析出的 OmniMark 字段（无头则 None）


@dataclass
class GuardianRule:
    """单条守护规则。"""

    id: str
    name: str
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    description: str
    check: Callable[[FileContext], bool]   # 返回 True = 违规
    disposition: list[str]                 # Phase 1 全为 ["warn"]
    message_template: str                  # 格式化时使用 {path}
    certainty: Literal["absolute", "needs_judgment"] = "absolute"
    # absolute: 规则命中即违规（confidence=1.0），如路径检查、文件存在性
    # needs_judgment: 规则命中只是疑似，需 GuardianAgent 复核（如代码语义判断）


@dataclass
class Violation:
    """一条违规记录。"""

    ticket_id: str
    rule_id: str
    severity: str
    path: str
    message: str
    disposition: list[str]
    confidence: float = 1.0
    detected_at: str = ""


# ─── OmniMark 解析 ──────────────────────────────────────────────


def parse_omnimark(content: str) -> Optional[dict]:
    """从文件内容的前 20 行中解析 OmniMark 头。无头返回 None。

    委托给 omnicompany.core.omnimark.parse_omnimark，
    返回 dict（向后兼容 FileContext.omnimark 字段类型）。
    """
    if not content:
        return None
    try:
        from omnicompany.core.omnimark import parse_omnimark as _parse
        result = _parse(content)
        if result is None:
            return None
        return {
            "origin": result.origin,
            "domain": result.domain,
            "agent": result.agent,
            "ts": result.ts,
            "trace": result.trace,
            "node": result.node,
            "status": result.status,
            # v2 新增字段
            "type": result.type,
            "module": result.module,
            # 兼容旧字段
            "created_by": result.created_by,
            "intent": result.intent,
        }
    except ImportError:
        return None


# ─── 共用 helper ──────────────────────────────────────────────


def _is_python(ctx: FileContext) -> bool:
    return ctx.path.endswith(".py")


def _is_scratch(ctx: FileContext) -> bool:
    """scratch 目录豁免 (2026-05-08 立).

    用户 2026-05-08 原话 "scratch 可以相对宽容, 本来就是一些自由开获区, 但是要按
    照一定规律自动整理". scratch/ 下大部分命名/位置/卫生规则豁免 (除 OMNI-049 老化).

    覆盖路径模式:
      _scratch/                          — 工作区级或 omnicompany 顶级 (omnicompany/_scratch/)
      data/_scratch/                     — data 域 scratch
      data/services/<svc>/scratch/       — service 级 scratch
      docs/_sandbox/                     — docs 沙盒 (类似 scratch)
    """
    p = ctx.path.replace("\\", "/").lower()
    if p.startswith("_scratch/") or "/_scratch/" in p:
        return True
    if "/scratch/" in p:
        return True
    if p.startswith("docs/_sandbox/"):
        return True
    return False


def _has_content(ctx: FileContext) -> bool:
    return bool(ctx.content and ctx.content.strip())


def _not_graveyard(ctx: FileContext) -> bool:
    return "_graveyard" not in ctx.path


_VENDORED_PACKAGES = ("mcp_builder",)  # 上游 vendored 代码（Phase C 之前在 packages/imported/）


def _is_external(ctx: FileContext) -> bool:
    """外部导入包（vendors/、_graveyard/、_archive）— 不受我们控制，一律豁免。

    Session 3b.3 (2026-04-08): mcp_builder 等 vendored 上游代码现在统一在
    packages/vendors/<name>/ 下。
    """
    p = ctx.path.replace("\\", "/")
    if "imported/" in p or "_graveyard/" in p or "_archive" in p:
        return True
    # node_modules/ 是 npm 上游依赖, 整目录豁免 (跟 vendors/ 同性质)
    # 2026-05-08 立: OMNI-030 中段 v\d+ 扩后 uuid 库 v1.js 等 84 处错杀
    if "node_modules/" in p:
        return True
    # vendors/ 整个层是上游 vendored 代码
    if "/packages/vendors/" in p or p.startswith("packages/vendors/") \
       or p.startswith("src/omnicompany/packages/vendors/"):
        return True
    # 兼容旧路径（理论上 S3b.3 之后不再出现）
    for vendored in _VENDORED_PACKAGES:
        if f"/packages/vendors/{vendored}/" in p or p.startswith(f"packages/vendors/{vendored}/"):
            return True
        if p.startswith(f"src/omnicompany/packages/vendors/{vendored}/"):
            return True
    return False

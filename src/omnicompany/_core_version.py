# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:omnicompany.core_version.contract_checker.py"
"""OmniCompany 核心版本契约

各组群在 manifest.py 中声明 CORE_VERSION_MIN，
TeamRunner 启动时比较版本号，不兼容时 warning。

版本规则 (语义化版本):
  - MAJOR: 破坏性变更 (删除/重命名公开 API)
  - MINOR: 兼容性扩展 (新增字段/方法)
  - PATCH: bug 修复 (行为不变)
"""

from __future__ import annotations

CORE_VERSION = "0.3.0"

# protocol/__init__.py 的公开 API 名称列表指纹
# 当核心公开 API 变更时，应同时更新此版本号
_PUBLIC_API = [
    # events
    "FactoryEvent", "EventMetadata", "EventType",
    # format
    "Format", "FormatRegistry", "ConnectionCheck", "create_builtin_registry",
    # anchor
    "Verdict", "VerdictKind", "Route", "RouteAction",
    "ValidatorSpec", "ValidatorKind", "Validator",
    "AnchorSpec", "TransformerSpec", "TransformMethod", "Transformer",
    # pipeline
    "TeamSpec", "TeamNode", "TeamEdge", "NodeKind",
    "TeamChecker", "TeamCheckResult", "EdgeCheckResult",
]


def check_compat(group_id: str, min_version: str) -> bool:
    """检查核心版本是否兼容组群要求的最低版本。

    Returns:
        True 如果兼容, False 并打印 warning 如果不兼容。
    """
    import logging
    logger = logging.getLogger("omnicompany.core")

    core_parts = tuple(int(x) for x in CORE_VERSION.split("."))
    min_parts = tuple(int(x) for x in min_version.split("."))

    if core_parts < min_parts:
        logger.warning(
            "⚠️  组群 '%s' 要求核心版本 >= %s，当前核心版本 %s。"
            "可能存在 API 不兼容，请更新核心或调整组群代码。",
            group_id, min_version, CORE_VERSION,
        )
        return False
    return True

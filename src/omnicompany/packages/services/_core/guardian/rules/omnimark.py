# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.rules.omnimark_header_validator.rule.py"
"""Guardian 规则 — OmniMark 身份头 (OMNI-001)。"""
from __future__ import annotations

from ._base import FileContext, GuardianRule, _is_python, _has_content, _is_external


def _check_missing_omnimark(ctx: FileContext) -> bool:
    if not _is_python(ctx) or not _has_content(ctx):
        return False
    # 只检查 packages/ 下的业务文件
    if "packages/" not in ctx.path:
        return False
    # 豁免：vendored / graveyard / 自动生成的 __init__
    if _is_external(ctx):
        return False
    if ctx.path.endswith("__init__.py"):
        return False
    return ctx.omnimark is None


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-001",
        name="missing-omnimark",
        severity="INFO",   # 升级为 MEDIUM 的时机：OmniMark 强制执行后（Phase 2）
        description="packages/ 下的 Python 文件缺少 [OMNI] 身份头",
        check=_check_missing_omnimark,
        disposition=["warn"],   # Phase 2+: stamp
        message_template="{path} 缺少 [OMNI] 身份头。请在文件顶部添加 # [OMNI] origin=... created_by=... intent=... 声明。",
    ),
]

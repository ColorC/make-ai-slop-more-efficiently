# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-19T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.terminology.legacy_naming_guard.rules.py"
"""Guardian 规则 — 命名迁移反倒退检测 (OMNI-036)。

背景：`docs/standards/terminology.md` (2026-04-19) 启动术语自底向上 ABCD 迁移。
本规则监控"新 module"（白名单路径）内是否仍在用旧命名，给 WARN 防止迁移期回退。

OMNI-036: new-module-legacy-naming

Phase A 状态（2026-07-03 批4 定性收尾）：**命名迁移主体已完成**。用户 2026-07-03
校准：此前的"停摆"是边际递减 / 执行方自认到头，而非弃坑——剩余基本无需迁移，
新代码按约定已用新名，旧代码永久 grandfathered。因此：
  - 本规则**保持实装**，作为对未来"新名回退"的常驻护栏（不废除）。
  - `_NEW_MODULE_WHITELIST` 保持为空 **不再是"待办/待 L1 填入"**，而是现实的
    忠实反映：不存在需要专门圈定并强制轮训的活跃迁移前沿。其他软件的第二波
    命名随第七批各件顺带做，不在此单列强制。
  - 若将来某新建 module 需要强制新名护栏，再按需把其路径加进白名单即可（机制在，
    随时可用），这与"Phase A 是否完成"是两回事。

参考：docs/standards/terminology.md §4 / §4.1 / §13.4 / §13.5（2026-07-03 批4 收尾）
"""
from __future__ import annotations

import re

from ._base import FileContext, GuardianRule, _is_external, _not_graveyard

# ══════════════════════════════════════════════════════════════════════
# 白名单：2026-07-03 批4 起, 空 = 迁移主体已完成, 无活跃迁移前沿需强制轮训
# （不是"待填"）。未来若某新建 module 需新名强制护栏, 按需加入其路径即可。
# ══════════════════════════════════════════════════════════════════════
_NEW_MODULE_WHITELIST: tuple[str, ...] = (
    # 需要时把新 module 路径加进来即可, 例:
    # "src/omnicompany/packages/services/omnicompany/",
)

# ══════════════════════════════════════════════════════════════════════
# 旧命名 identifier / import pattern
# ══════════════════════════════════════════════════════════════════════

# 在源码正文中出现即视为违反（Phase A 开启后扩展）
_LEGACY_IDENTIFIERS = (
    "TeamEdge",
    "TeamSpec",
)

# import 语句 pattern（命中即违反）
_LEGACY_IMPORT_PATTERNS = (
    re.compile(r"\bfrom\s+omnicompany\.protocol\.format\s+import\s+Format\b"),
    re.compile(r"\bfrom\s+omnicompany\.protocol\b.*\bPipelineEdge\b"),
)


# ══════════════════════════════════════════════════════════════════════
# 检测函数
# ══════════════════════════════════════════════════════════════════════


def _in_new_module(ctx: FileContext) -> bool:
    """文件是否在新 module 白名单范围内。"""
    if not _NEW_MODULE_WHITELIST:
        return False
    p = ctx.path.replace("\\", "/")
    return any(p.startswith(w) or f"/{w}" in p for w in _NEW_MODULE_WHITELIST)


def _check_new_module_legacy_naming(ctx: FileContext) -> bool:
    """新 module 内使用旧命名 → WARN。"""
    if not ctx.content:
        return False
    if _is_external(ctx) or not _not_graveyard(ctx):
        return False
    if not _in_new_module(ctx):
        return False

    content = ctx.content
    for identifier in _LEGACY_IDENTIFIERS:
        if identifier in content:
            return True
    for pattern in _LEGACY_IMPORT_PATTERNS:
        if pattern.search(content):
            return True
    return False


# ══════════════════════════════════════════════════════════════════════
# 规则清单
# ══════════════════════════════════════════════════════════════════════


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-036",
        name="new-module-legacy-naming",
        severity="MEDIUM",
        description=(
            "命名迁移（terminology.md）反倒退检测：新 module 白名单内禁止"
            "使用旧命名（Format / TeamEdge / TeamSpec 等）。"
            "旧代码 grandfathered 不在此规则范围。"
        ),
        check=_check_new_module_legacy_naming,
        disposition=["warn"],
        message_template=(
            "{path}: 新 module 内检出旧命名（legacy identifier 或 import）。"
            "请用新命名：Material / Worker / Team / Stock / Department。"
            "详见 docs/standards/terminology.md。"
        ),
        certainty="absolute",
    ),
]

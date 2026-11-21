# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.data_location.validator.py"
"""Guardian 规则 — 数据文件位置 (OMNI-005/011)。

OMNI-005: .db 文件出现在 data/ 之外
OMNI-011: events.db / ide_events.db 必须落在 unified canonical 路径
"""
from __future__ import annotations

from ._base import FileContext, GuardianRule


def _check_db_outside_data(ctx: FileContext) -> bool:
    if not ctx.path.endswith(".db"):
        return False
    return not ctx.path.startswith("data/") and not ctx.path.startswith("logs/")


def _check_scattered_events_db(ctx: FileContext) -> bool:
    if not ctx.path.endswith(("events.db", "ide_events.db")):
        return False
    # 归一化路径
    p = ctx.path.replace("\\", "/")
    # 允许 data/_archive*/  和 data/_stray_from_parent/  作为已知归档
    if "/data/_archive" in p or p.startswith("data/_archive"):
        return False
    if "/data/_stray_from_parent" in p or p.startswith("data/_stray_from_parent"):
        return False
    # canonical: 必须正好是 data/events.db 或 data/ide_events.db
    if p in ("data/events.db", "data/ide_events.db"):
        return False
    # 任何其他位置都是 drift
    return True


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-005",
        name="db-outside-data",
        severity="HIGH",
        description=".db 文件出现在 data/ 之外",
        check=_check_db_outside_data,
        disposition=["warn"],
        message_template="{path} 是数据库文件但不在 data/ 目录下。请移至 data/ 目录。",
    ),
    GuardianRule(
        id="OMNI-011",
        name="scattered-events-db",
        severity="MEDIUM",
        description="events.db / ide_events.db 不在 unified canonical 路径",
        check=_check_scattered_events_db,
        disposition=["warn"],
        message_template="{path} 不是 unified 路径。Move 8 已上线：唯一合法位置是 data/events.db 或 data/ide_events.db（_archive*/ 例外）。请用 scripts/_move8_migrate_events.py 合并后归档。",
    ),
]

# [OMNI] origin=ai-ide domain=services/_core/protection ts=2026-05-02T04:00:00Z type=service status=active agent=ai-ide-current
# [OMNI] summary="omnicompany 主动防御 - 文件级写入违规识别 + 内部/外部分类处理"
# [OMNI] why="目录规范化 G1-G6 收尾后, 锁机制范围性开启. 内部错位写入源头注释 + 外部直接写入移除留指导. 跟 cc_wrapper event bus 联动判内/外"
# [OMNI] tags=protection,lock,defense,guardian
# [OMNI] material_id="material:core.protection.module_aggregate.exports.py"
"""omnicompany 主动防御 (G4 锁组).

**两类违规**:
- 内部错位: 通过 cc_wrapper hook 的 Edit/Write 写到了不该写的位置 (event bus 有 trace,
  但 file_path 不在合法路径白名单)
- 外部直接: 文件存在但 cc_wrapper event bus 找不到对应 agent.tool.call 事件 (不是
  通过 omnicompany 体系写入的)

**两类处理**:
- 内部错位: 留 notice (在文件头加 OMNI-LOCK-VIOLATION 注释 + 引用规范文档教正确写法)
- 外部直接: 移除内容到 quarantine + 原地留 .OMNI-EVICTED.md 指导文件 (注册身份 + 合法方式)

**范围性开启** (用户硬规则):
不是 all-or-nothing 全局锁. policy 配 watched_paths 列出"被锁的目录" + 例外白名单
(沙盒 / 系统文件 / 注册过的实体 source_file). 写入只有落到 watched 内 + 不在白名单
才视为违规.

**当前阶段** (MVP):
离线扫描 (omni lock scan) + 离线处理 (omni lock handle). 实时拦截 (PostToolUse hook)
留下一阶段做.
"""
from __future__ import annotations

from omnicompany.packages.services._core.protection.policy import (
    DEFAULT_WATCHED_PATHS,
    DEFAULT_WHITELIST_PATTERNS,
    is_watched,
    is_whitelisted,
    is_in_baseline,
    load_policy,
    save_policy,
    load_baseline,
    save_baseline,
)
from omnicompany.packages.services._core.protection.scanner import (
    Violation,
    scan_violations,
    classify_violation,
    snapshot_current_as_baseline,
)
from omnicompany.packages.services._core.protection.handlers import (
    handle_internal_misplace,
    handle_external_write,
    quarantine_dir,
)

__all__ = [
    "DEFAULT_WATCHED_PATHS",
    "DEFAULT_WHITELIST_PATTERNS",
    "is_watched",
    "is_whitelisted",
    "is_in_baseline",
    "load_policy",
    "save_policy",
    "load_baseline",
    "save_baseline",
    "Violation",
    "scan_violations",
    "classify_violation",
    "snapshot_current_as_baseline",
    "handle_internal_misplace",
    "handle_external_write",
    "quarantine_dir",
]

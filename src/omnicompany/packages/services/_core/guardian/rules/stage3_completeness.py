# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-21T00:00:00Z
# [OMNI] material_id="material:core.guardian.stage3_completeness.migration_checker.rules.py"
"""Guardian 规则 — Clean Migration Stage 3 完整性 (OMNI-040).

OMNI-040: services/<svc>/ 下任何 .py 文件 (含顶层 routers.py / 任意子包 / workers/)
          `from .._archive import...` 或绝对路径 `services.<svc>._archive` import
          说明该服务停留在 Stage 2 Diamond shortcut, 业务代码未真正独立。

Stage 2 中间态 (Diamond shortcut, 三种常见位置):
    # 1) workers/__init__.py (典型 Diamond)
    from .._archive.routers_legacy import FooRouter as _FooRouter
    class FooWorker(Worker, _FooRouter): pass

    # 2) services/<svc>/routers.py 顶层 (老 routers 直接借壳归档)
    from ._archive.routers_legacy import (FooRouter, BarRouter)

    # 3) services/<svc>/sub_pkg/foo.py (子包内借壳)
    from .._archive.routers_v3_legacy.module_x import XxxRouter

Stage 3 终态 (真 Clean Migration):
    # workers/foo_worker.py (独立文件)
    from omnicompany.packages.services._core.omnicompany import Worker
    class FooWorker(Worker):
        def run(...): ...  # 业务代码在此

豁免:
    - `_archive/` 内部文件本身的 import (归档自含, 不算违规, 符合"归档不重写历史")

背景:
    2026-04-21 用户指出 "你宣称的 Clean Migration 完成是假的", 发现 lap_auditor /
    cleanup_bot / knowledge 只做到 Diamond shortcut (Stage 2), 业务代码仍在 _archive.

    2026-04-23 S-01b: 范围从 `workers/` 扩到整个 services/<svc>/ 下任何 .py.
    原范围漏抓了 team_builder/routers.py (顶层) · doctor/routers.py · hypothesis/routers.py
    · absorption/routers/report_writer.py 等顶层/子包位置的 Diamond 残留.

    此规则确保未来任何声称 Clean Migration 的服务都达到 Stage 3 标准, 防止倒退.
"""
from __future__ import annotations

import ast
import re

from ._base import FileContext, GuardianRule


# 保留 regex 供 PatrolWorker 等外部工具复用 (非规则主路径)
# 注意: 直接 regex 会误命中 docstring/注释里的示例代码字符串.
# 规则主路径用下方 _has_archive_import_via_ast() 走 AST 解析, 只看真 import 语句.
_ARCHIVE_IMPORT_RE = re.compile(
    r"^\s*from\s+(?:"
    r"\.+_archive"  # 相对: from ._archive / from .._archive / from ..._archive ...
    r"|"
    r"omnicompany\.packages\.services\.[a-zA-Z_][a-zA-Z0-9_]*\._archive"  # 绝对
    r")",
    re.MULTILINE,
)

# AST 视角的 _archive 模块名匹配
# 相对: ImportFrom(module="_archive..." 或 module=None+level>=1+names 含 _archive)
#       但实际相对 import `from ._archive.foo import X` 的 module 字段是 "_archive.foo", level=1
# 绝对: ImportFrom(module="omnicompany.packages.services.<svc>._archive...")
_ABS_ARCHIVE_RE = re.compile(
    r"^omnicompany\.packages\.services\.[a-zA-Z_][a-zA-Z0-9_]*\._archive(\.|$)"
)


def _has_archive_import_via_ast(content: str) -> bool:
    """用 AST 检查文件是否含 `from ..._archive import ...` 真 import 语句.

    跳过 docstring / 注释 / 字符串字面量里的示例 (regex 单解会误判).
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # 相对 import: level >= 1, module 可能是 "_archive" / "_archive.xxx" / None
        if node.level >= 1:
            mod = node.module or ""
            if mod == "_archive" or mod.startswith("_archive."):
                return True
            # 极端情况: from .. import _archive (level>=1, module=None, names 含 _archive)
            if node.module is None:
                for alias in node.names:
                    if alias.name == "_archive":
                        return True
        # 绝对 import: module 形如 omnicompany.packages.services.<svc>._archive[.xxx]
        elif node.module and _ABS_ARCHIVE_RE.match(node.module):
            return True
    return False


def _check_stage3_completeness(ctx: FileContext) -> bool:
    """OMNI-040: services/<svc>/ 下任何 .py 文件 import _archive → Stage 2 假迁移.

    覆盖三种位置:
      1. workers/ 下: `from .._archive.routers_legacy import XxxRouter as _X`
      2. service 顶层 routers.py: `from ._archive.routers_legacy import (...)`
      3. 子包内: `from ..._archive.routers_v3_legacy.foo import BarRouter as _B`

    覆盖两种 import 写法:
      - 相对: `from ._archive` / `from .._archive` / `from ..._archive` ...
      - 绝对: `from omnicompany.packages.services.<svc>._archive...`

    豁免:
      - `_archive/` 内部文件本身的 import (归档自含, 历史代码自由 import 历史代码)
    """
    p = ctx.path.replace("\\", "/")
    if "/services/" not in p:
        return False
    if not p.endswith(".py"):
        return False
    # 豁免: 文件本身就在 _archive/ 里 → 归档内部 import 归档, 不算违规
    if "/_archive/" in p or "/_archive." in p:
        return False
    if not ctx.content:
        return False
    # AST 解析, 跳过 docstring/注释里的示例代码 (Guardian rule 文件自身就是典型反例)
    return _has_archive_import_via_ast(ctx.content)


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-040",
        name="stage3-completeness",
        severity="HIGH",
        description="Clean Migration Stage 2 假迁移: services/<svc>/ 下任何 .py 文件 import _archive (含顶层 routers.py / 子包 / workers/), 说明业务代码未真正独立",
        check=_check_stage3_completeness,
        disposition=["warn"],
        message_template=(
            "{path}: 存在 `from .._archive` import, 说明该服务停留在 Stage 2 Diamond shortcut. "
            "请把业务代码从 _archive/routers_legacy.py 真正搬到 workers/<name>.py 独立文件, "
            "删除 _archive/ (或只保留 README 作为历史参考, 不再被 workers 继承). "
            "参见 2026-04-21 lap_auditor/cleanup_bot/knowledge 作为 Stage 3 完整迁移参考."
        ),
        certainty="absolute",
    ),
]

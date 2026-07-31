# [OMNI] origin=omnicompany domain=omnicompany/guardian ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.guardian.definition_location.enforcer.py"
"""Guardian 规则 — 文件标准位置 (OMNI-023/024)。

诊断管线的结构前提：Format/Router 定义必须集中在标准位置，
以便诊断管线和 Registry 自动发现。

OMNI-023: Format 定义不在 formats.py 或 formats/ 目录下
OMNI-024: Router 定义不在 routers.py 或 routers/ 目录下
"""
from __future__ import annotations

import re

from ._base import FileContext, GuardianRule, _is_external


def _check_format_location(ctx: FileContext) -> bool:
    """OMNI-023: packages/ 下的 .py 文件含 Format 定义但不在 formats 标准位置。"""
    p = ctx.path.replace("\\", "/")
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if not p.endswith(".py"):
        return False
    # 在标准位置的不检查
    basename = p.rsplit("/", 1)[-1] if "/" in p else p
    if basename == "formats.py":
        return False
    if "/formats/" in p:
        return False
    # 没有内容则跳过
    if not ctx.content:
        return False
    # 检查是否有 Format 实例赋值: XXX = Format(...) 或 XXX=Format(...)
    # 排除 import / 注释 / docstring 里的引用
    for line in ctx.content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "import ", "from ", '"', "'")):
            continue
        # 赋值模式: 变量名 = Format(
        if re.search(r'^[A-Z_]\w*\s*=\s*Format\s*\(', stripped):
            return True
    return False


def _check_router_location(ctx: FileContext) -> bool:
    """OMNI-024: packages/ 下的 .py 文件含 Router 子类但不在 routers 标准位置。

    豁免：文件顶部（前 5 行）含 `# OMNI-024 ALLOW: <原因>` 注释的文件跳过检查。
    适用于有意识地将少量紧耦合 Router 辅助类放在非标准位置的模块
    （如独立执行流程文件、测试夹具等）。
    """
    p = ctx.path.replace("\\", "/")
    if not p.startswith("src/omnicompany/packages/"):
        return False
    if not p.endswith(".py"):
        return False
    # 在标准位置的不检查
    basename = p.rsplit("/", 1)[-1] if "/" in p else p
    if basename == "routers.py":
        return False
    if "/routers/" in p:
        return False
    # 没有内容则跳过
    if not ctx.content:
        return False
    # 文件级豁免：前 5 行含 OMNI-024 ALLOW 注释
    header_lines = ctx.content.splitlines()[:5]
    if any("OMNI-024 ALLOW" in ln for ln in header_lines):
        return False
    # 检查是否有 class Xxx(Router) / (LLMRouter) / (AgentNodeLoop) 定义
    if re.search(
        r'^class\s+\w+\s*\(\s*(?:Router|LLMRouter|AgentNodeLoop)',
        ctx.content,
        re.MULTILINE,
    ):
        return True
    return False


# ── OMNI-055: data/ 不放可执行代码 (反向规则, 2026-05-08 V0-V26 巡检后立) ──
#
# 反向规则空白补: 有 OMNI-007 (src/ 不放散文 .md), 没有 data/ 不放代码.
# 真案例触发: data/services/doctor/repair/run_router_repair.py 漏在 data 域,
# 应在 src/omnicompany/packages/services/_core/doctor/repair/ 下.
#
# data/ 域只放运行时数据 + 配置, 不放可执行 Python/Shell/PowerShell 脚本.
# 豁免: 文件顶部 5 行含 `# OMNI-055 ALLOW: <原因>` 注释 → 跳 (例 dogfood / probe 一次性脚本).

_DATA_CODE_EXTS = (".py", ".sh", ".ps1", ".bat", ".js", ".ts")


def _check_data_no_code(ctx: FileContext) -> bool:
    """OMNI-055: data/ 域不应含可执行代码. 反向规则补 OMNI-007 反面."""
    if _is_external(ctx):
        return False
    p = ctx.path.replace("\\", "/")
    if not p.startswith("data/"):
        return False
    # 跳归档/外部/team_builder 动态 worktree (含完整 git/node_modules 副本)
    if "_archive" in p or "_graveyard" in p or "/vendors/" in p:
        return False
    if "/_workspaces/" in p or "/repo_abs_" in p or "/scratch/" in p:
        return False
    # references/ 是 voxelcraft 等真业务参考的上游 mod 真源码, 同 vendors/ 性质
    if "/references/" in p:
        return False
    # 必须是可执行代码后缀
    if not any(p.endswith(ext) for ext in _DATA_CODE_EXTS):
        return False
    # 文件级豁免: 前 5 行含 `# OMNI-055 ALLOW: <原因>` 注释
    if ctx.content:
        header_lines = ctx.content.splitlines()[:5]
        if any("OMNI-055 ALLOW" in ln for ln in header_lines):
            return False
    return True


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-023",
        name="format-location",
        severity="HIGH",
        description="Format 定义不在 formats.py 或 formats/ 目录下",
        check=_check_format_location,
        disposition=["warn"],
        message_template="{path}: 包含 Format 定义但不在 formats.py 或 formats/ 目录下。Format 定义应集中在 <package>/formats.py 或 <package>/formats/<any>.py 中，以便诊断管线和 Registry 自动发现。",
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-024",
        name="router-location",
        severity="HIGH",
        description="Router 定义不在 routers.py 或 routers/ 目录下",
        check=_check_router_location,
        disposition=["warn"],
        message_template="{path}: 包含 Router 子类定义但不在 routers.py 或 routers/ 目录下。Router 定义应集中在 <package>/routers.py 或 <package>/routers/<any>.py 中，以便诊断管线和 Registry 自动发现。",
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-055",
        name="data-no-code",
        severity="HIGH",
        description="data/ 域不应含可执行代码 (.py/.sh/.ps1/.bat/.js/.ts), 应在 src/ 下",
        check=_check_data_no_code,
        disposition=["warn"],
        message_template="{path}: data/ 域漏可执行代码. data/ 只放运行时数据/配置, 代码应在 src/omnicompany/. 一次性脚本豁免请前 5 行加 `# OMNI-055 ALLOW: <原因>` 注释.",
        certainty="absolute",
    ),
]

# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-07-10T00:00:00Z type=rule status=active
# [OMNI] summary="OMNI-101 决策本体清场废墟检测:合并清单已拆除的路径若复活或被新代码引用,报'清场未完成'。墓碑清单真源=domains/decisions/patrol.RUIN_PATHS。"
# [OMNI] why="决策本体清场纪律(plan §十二·五,DEC-2026-07-10-008):四步缺一不算完成;guardian patrol 增加废墟检测常项,机制卡巡检不靠自觉。"
# [OMNI] tags=guardian,rule,decision-ontology,ruins
"""OMNI-101 — 决策本体清场废墟检测。

两个面:
  a) 墓碑路径复活:合并清单标记「应拆除」的文件若重新出现 → CRITICAL。
  b) 死引用:任何新改文件引用已拆除模块(hypothesis.validator / knowledge.graph 等) → HIGH。
墓碑清单唯一真源 = omnicompany.packages.domains.decisions.patrol.RUIN_PATHS。
"""
from __future__ import annotations

import re

from ._base import FileContext, GuardianRule


def _ruin_paths() -> set[str]:
    try:
        from omnicompany.packages.domains.decisions.patrol import RUIN_PATHS
        return {rel for rel, _ in RUIN_PATHS}
    except Exception:
        return set()


# 已拆除模块的 import 指纹(引用即废墟;注释/文档提及不算——只匹配 import 语句)
_DEAD_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+omnicompany\.packages\.services\._learning\."
    r"(?:hypothesis\.(?:store|validator|reflector_daemon|pipeline)|knowledge\.graph)\b",
    re.M,
)


def _check_ruin_revival(ctx: FileContext) -> bool:
    if ctx.change_type == "D":
        return False
    return ctx.path in _ruin_paths()


def _check_dead_import(ctx: FileContext) -> bool:
    if ctx.change_type == "D" or not ctx.path.endswith(".py") or not ctx.content:
        return False
    if "/_archive/" in ctx.path or ctx.path.startswith("data/"):
        return False
    return bool(_DEAD_IMPORT_RE.search(ctx.content))


RULES = [
    GuardianRule(
        id="OMNI-101a",
        name="决策本体清场·墓碑路径复活",
        severity="CRITICAL",
        description=(
            "合并清单已拆除的路径重新出现=清场未完成"
            "(墓碑清单=domains/decisions/patrol.RUIN_PATHS;全局巡检=omni decisions patrol)"
        ),
        check=_check_ruin_revival,
        disposition=["warn"],
        message_template="{path} 是决策本体清场纪律标记「应拆除」的路径,不得复活(见 patrol.RUIN_PATHS 出处注记)",
        certainty="absolute",
    ),
    GuardianRule(
        id="OMNI-101b",
        name="决策本体清场·死模块引用",
        severity="HIGH",
        description="import 已拆除的 hypothesis.store/validator/reflector_daemon/pipeline 或 knowledge.graph",
        check=_check_dead_import,
        disposition=["warn"],
        message_template="{path} import 了决策本体合并清单已拆除的模块(改用 belief_tools/统一决策库)",
        certainty="absolute",
    ),
]

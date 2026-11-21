# [OMNI] origin=claude-code domain=runtime/codegen_tools ts=2026-04-09T00:00:00Z
# [OMNI] material_id="material:runtime.codegen.python_fixer.py"
"""Python codegen post-processing tools (GAP ③ 沉淀).

一组**领域无关**的 Python 源码清理函数, 供任何代码生成类管线复用,
不再锁死在 workflow_factory.DeterministicFixerRouter 内部。

覆盖的高频 LLM 生成错误模式:
  1. `from typing import Dict/List/...` → Python 3.9+ 用内置类型
  2. `kind="ANCHOR"` → `kind=NodeKind.ANCHOR` (Pydantic 要求小写枚举值)
  3. `kind="HARD"/"SOFT"` → `kind=ValidatorKind.HARD/SOFT`
  4. pipeline.py / routers.py 缺关键 import → 补全标准 import 块

设计决策:
  - 纯函数, 无副作用, 无 Router/Verdict/Format 依赖
  - 输入: `str` (源码), 输出: `str` (清理后的源码)
  - `apply_python_lap_cleanup` 是批量入口, 接收 files dict, 返回 (new_files, fix_count)
  - Router 层 (DeterministicFixerRouter) 只需 import 这个模块即可复用
"""

from __future__ import annotations

import re

# 标准 import 块 (pipeline.py 必须有)
_PIPELINE_IMPORTS = (
    "from omnicompany.protocol.anchor import (\n"
    "    AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind,\n"
    ")\n"
    "from omnicompany.protocol.team import (\n"
    "    NodeKind, TeamEdge, TeamNode, TeamSpec,\n"
    ")\n"
)

# 标准 import 块 (routers.py 必须有)
_ROUTER_IMPORTS = (
    "from omnicompany.protocol.anchor import Verdict, VerdictKind\n"
    "from omnicompany.runtime.routing.router import Router\n"
)


def clean_typing_imports(source: str) -> str:
    """移除 typing.Dict/List/Optional/Tuple/Set 别名 (Python 3.9+ 用内置类型)。"""
    # 整行全是被废弃别名
    source = re.sub(
        r'from typing import (?:Dict|List|Optional|Tuple|Set)(?:\s*,\s*(?:Dict|List|Optional|Tuple|Set))*\s*\n',
        '',
        source,
    )
    # 混合行, 只移除废弃别名部分
    source = re.sub(r',\s*(?:Dict|List|Optional|Tuple|Set)\b', '', source)
    source = re.sub(r'\b(?:Dict|List|Optional|Tuple|Set)\s*,\s*', '', source)
    return source


def fix_nodekind_string_literals(source: str) -> str:
    """`kind="ANCHOR"` → `kind=NodeKind.ANCHOR` 及类似。"""
    source = re.sub(r'kind\s*=\s*["\']ANCHOR["\']', 'kind=NodeKind.ANCHOR', source)
    source = re.sub(r'kind\s*=\s*["\']TRANSFORMER["\']', 'kind=NodeKind.TRANSFORMER', source)
    source = re.sub(r'kind\s*=\s*["\']SCATTER["\']', 'kind=NodeKind.SCATTER', source)
    return source


def fix_validatorkind_string_literals(source: str) -> str:
    """`kind="HARD"/"SOFT"` → `kind=ValidatorKind.HARD/SOFT`。"""
    source = re.sub(r'kind\s*=\s*["\']HARD["\']', 'kind=ValidatorKind.HARD', source)
    source = re.sub(r'kind\s*=\s*["\']SOFT["\']', 'kind=ValidatorKind.SOFT', source)
    return source


def patch_pipeline_imports(source: str) -> str:
    """确保 pipeline.py 有 omnicompany.protocol.anchor + pipeline 标准 import。"""
    if "from omnicompany.protocol.anchor import" not in source:
        source = _PIPELINE_IMPORTS + "\n" + source
    if "from omnicompany.protocol.team import" not in source:
        anchor_end = source.find("from omnicompany.protocol.pipeline")
        if anchor_end == -1:
            anchor_import_end = source.find(")\n", source.find("from omnicompany.protocol.anchor"))
            if anchor_import_end > 0:
                insert_pos = anchor_import_end + 2
                source = (
                    source[:insert_pos]
                    + "from omnicompany.protocol.team import (\n"
                    + "    NodeKind, TeamEdge, TeamNode, TeamSpec,\n"
                    + ")\n"
                    + source[insert_pos:]
                )
    return source


def patch_router_imports(source: str) -> str:
    """确保 routers.py 有 Router 基类 + Verdict 标准 import。"""
    if "from omnicompany.protocol.anchor import" not in source:
        source = _ROUTER_IMPORTS + "\n" + source
    return source


def apply_python_lap_cleanup(
    files: dict[str, str],
) -> tuple[dict[str, str], int]:
    """批量应用所有 LAP Python 生成清理规则。

    Args:
        files: {filename: source_code}

    Returns:
        (new_files, fix_count)
        - new_files: 清理后的文件字典 (只含有变化的文件;未变化的保持原样)
        - fix_count: 被修改的文件数
    """
    new_files = dict(files)
    fix_count = 0
    for fname, content in list(files.items()):
        if not fname.endswith(".py"):
            continue
        original = content
        content = clean_typing_imports(content)
        content = fix_nodekind_string_literals(content)
        content = fix_validatorkind_string_literals(content)
        if fname == "pipeline.py":
            content = patch_pipeline_imports(content)
        elif fname == "routers.py":
            content = patch_router_imports(content)
        if content != original:
            new_files[fname] = content
            fix_count += 1
    return new_files, fix_count


__all__ = [
    "clean_typing_imports",
    "fix_nodekind_string_literals",
    "fix_validatorkind_string_literals",
    "patch_pipeline_imports",
    "patch_router_imports",
    "apply_python_lap_cleanup",
]

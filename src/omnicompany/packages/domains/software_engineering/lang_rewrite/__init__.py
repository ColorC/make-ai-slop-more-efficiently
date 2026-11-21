# [OMNI] origin=human domain=software_engineering/lang_rewrite ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite.module_aggregate.exports.py"
"""lang_rewrite — 跨语言改写管线

将 Python 引擎层模块改写为 TypeScript / Rust，
保持六元语义等价，通过编译 + 等价性验证闭环。

公开 API:
  build_pipeline()   → TeamSpec（14 节点 DAG）
  build_bindings()   → dict[str, Router]
  DOMAIN             → "rewrite"
  FORMATS            → 所有 Format 定义列表
"""

from omnicompany.packages.domains.software_engineering.lang_rewrite.formats import DOMAIN, FORMATS, register_formats
from omnicompany.packages.domains.software_engineering.lang_rewrite.pipeline import build_pipeline

__all__ = [
    "DOMAIN",
    "FORMATS",
    "register_formats",
    "build_pipeline",
    "build_bindings",
]


def build_bindings(input_dict=None):
    """构建管线节点→Router 绑定（延迟 import）。"""
    from omnicompany.packages.domains.software_engineering.lang_rewrite.run import build_bindings as _bb
    return _bb(input_dict)

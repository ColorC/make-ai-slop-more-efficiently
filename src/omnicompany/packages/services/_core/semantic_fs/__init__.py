# [OMNI] origin=claude-code domain=services/_core/semantic_fs ts=2026-06-27T00:00:00Z type=material
# [OMNI] summary="语义文件系统设施(所有产出皆 material)。把 MaterialIdIndex 当 Spotlight 旁路索引层、registry attrs 当 TMSU 标签层, 补语义元数据 schema(受控分类标签/双时间/嵌入指针) + 产出即分类入册 + 语义检索投影。做 Spotlight 不做 WinFS。"
# [OMNI] why="用户主旨:所有产出皆 material、文件由语义组织索引。赢家=真源不动+旁路叠加+优雅降级;不做统一本体论框死/中央双写库。SEMANTIC-OS 方向A child。"
# [OMNI] tags=semantic-os,material,semantic-filesystem,registry
# [OMNI] material_id="material:core.semantic_fs.__init__.py"
"""semantic_fs — 语义文件系统设施(SEMANTIC-OS 方向 A child)。

分层(全建在现成 registry 之上, 真源不动):
  - schema.py : 语义元数据 schema(semantic_tags 受控词表 / 双时间 / embedding_ref)+ 校验。
  - classify.py(M2): 分类 Worker + 落盘即入册。
  - index.py(M3)   : embedding + chunk 两级索引, hybrid 检索。
"""
from .schema import (  # noqa: F401
    SEMANTIC_FIELDS,
    TAG_NAMESPACES,
    KIND_VALUES,
    known_domains,
    validate_tags,
    get_semantic,
    set_semantic,
)

# [OMNI] origin=claude-code domain=omnicompany/knowledge ts=2026-04-21T00:00:00Z type=config
# [OMNI] material_id="material:learning.knowledge.material_formats.definition.py"
"""knowledge.formats — OmniKB 知识库管理 Material 定义 (Clean Migration 2026-04-21).

Material kind 标注 (F-19):
  kb.query          → kind.source   (外部查询请求, 无 producer Worker)
  kb.query_result   → kind.sink     (查询结果输出, 无 consumer Worker)
  kb.entry_draft    → kind.source   (写入请求, 无 producer Worker)
  kb.entry_committed → kind.sink    (写入确认, 无 consumer Worker)
  kb.locate_query   → kind.source   (定位请求, 无 producer Worker)
  kb.locate_result  → kind.sink     (定位结果, 无 consumer Worker)
  kb.audit_request  → kind.source   (审计请求, 无 producer Worker)
  kb.audit_report   → kind.sink     (审计报告, 无 consumer Worker)
  kb.rebuild_request → kind.source  (重建索引请求, 无 producer Worker)
  kb.index_stats    → kind.sink     (索引统计, 无 consumer Worker)

注: KB 的 5 个 Worker 均为独立入口（无链路链接），因此每对都是 source→sink 结构。
"""

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry

# ─────────────────── 1. 查询 ───────────────────

KB_QUERY = Material(
    id="kb.query",
    name="KB Query Request",
    description=(
        "触发 OmniKB 多维度查询的入口请求。"
        "支持 5 种查询形态（至少提供一个字段）："
        "id（精确 id 查询）、types（类型枚举）、tags（标签 AND 过滤）、"
        "domain（领域快捷查）、scope（karch/krepo 作用域）、"
        "id_prefix（id 前缀匹配）、text（模糊文本搜索）。"
        "可组合 maturity 过滤。"
        "Kind: source（外部触发，无 producer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "query", "kind.source"],
    examples=[
        {"id": "kb.arch.bus_unification"},
        {"types": ["karch", "kdec"]},
        {"domain": "absorption", "maturity": "stable"},
        {"text": "context compression", "limit": 5},
    ],
)

KB_QUERY_RESULT = Material(
    id="kb.query_result",
    name="KB Query Result",
    description=(
        "KBQueryWorker 查询后产出的结果集。"
        "包含 entries（list of entry dict，含 id/omnikb_type/tags/body 等字段）"
        "和 count（命中条目数）。"
        "Kind: sink（最终查询结果，无 consumer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "query", "output", "kind.sink"],
    examples=[
        {"entries": [{"id": "kb.arch.bus_unification", "omnikb_type": "karch"}], "count": 1},
        {"entries": [], "count": 0},
    ],
)

# ─────────────────── 2. 写入 ───────────────────

KB_ENTRY_DRAFT = Material(
    id="kb.entry_draft",
    name="KB Entry Draft",
    description=(
        "触发 OmniKB 条目写入的入口请求。"
        "entry（dict，必含 omnikb_type，如 karch/kdec/krouter/kfmt/krepo/kexp）、"
        "body（可选 Markdown 正文）、"
        "overwrite（可选 bool，True 时覆盖整个文件，默认 False）。"
        "写入经 guarded_write 落盘到 .omni/knowledge/ 目录。"
        "Kind: source（外部触发，无 producer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "write", "kind.source"],
    examples=[
        {
            "entry": {"omnikb_type": "karch", "id": "kb.arch.new_feature", "summary": "..."},
            "body": "## 详细描述\n...",
            "overwrite": False,
        }
    ],
)

KB_ENTRY_COMMITTED = Material(
    id="kb.entry_committed",
    name="KB Entry Committed",
    description=(
        "KBWriteWorker 写入成功后的确认结果。"
        "包含 id（条目 id）、omnikb_type（条目类型）、path（落盘文件路径）、"
        "was_update（是否为更新）、was_new（是否为新建）。"
        "Kind: sink（写入确认，无 consumer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "write", "output", "kind.sink"],
    examples=[
        {
            "id": "kb.arch.new_feature",
            "omnikb_type": "karch",
            "path": ".omni/knowledge/karch/new_feature.md",
            "was_update": False,
            "was_new": True,
        }
    ],
)

# ─────────────────── 3. 定位 ───────────────────

KB_LOCATE_QUERY = Material(
    id="kb.locate_query",
    name="KB Locate Query",
    description=(
        "触发 OmniKB 自然语言定位的入口请求。"
        "给定自然语言问题（query 必填），返回最相关的 entries 及其 code_anchors。"
        "可选 limit（默认 5）和 types（类型过滤）。"
        "适用于 Q2（OmniCompany 某功能对应在哪）场景。"
        "Kind: source（外部触发，无 producer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "locate", "kind.source"],
    examples=[
        {"query": "哪里处理 LLM 上下文压缩"},
        {"query": "absorption pipeline entry point", "limit": 3, "types": ["karch"]},
    ],
)

KB_LOCATE_RESULT = Material(
    id="kb.locate_result",
    name="KB Locate Result",
    description=(
        "KBLocateWorker 定位后产出的结果。"
        "包含 query（原始问题）、entries（相关 entry 列表）、"
        "code_anchors（所有匹配 entry 的 code_anchors 聚合去重）、count（条目数）。"
        "Kind: sink（定位结果，无 consumer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "locate", "output", "kind.sink"],
    examples=[
        {
            "query": "哪里处理 LLM 上下文压缩",
            "entries": [{"id": "kb.arch.compression"}],
            "code_anchors": ["src/omnicompany/runtime/llm/compression_summary.py:L1-L200"],
            "count": 1,
        }
    ],
)

# ─────────────────── 4. 审计 ───────────────────

KB_AUDIT_REQUEST = Material(
    id="kb.audit_request",
    name="KB Audit Request",
    description=(
        "触发 OmniKB 全量一致性审计的入口请求。"
        "无必填字段（空 dict 即可），审计范围是整个 project_root。"
        "执行 5 类检查：validation / anchor drift / orphan routers / staleness / format coverage。"
        "Kind: source（外部触发，无 producer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "audit", "kind.source"],
    examples=[
        {},
        {"project_root": "e:/WindowsWorkspace/omnicompany"},
    ],
)

KB_AUDIT_REPORT = Material(
    id="kb.audit_report",
    name="KB Audit Report",
    description=(
        "KBAuditWorker 全量审计后产出的报告。"
        "包含 summary（文本摘要）、has_issues（bool）、"
        "validation_issues / anchor_drifts / orphan_routers / staleness / format_coverage 五大检查结果。"
        "Verdict: PASS（无问题）/ PARTIAL（有 warning）/ FAIL（有 error 级问题）。"
        "Kind: sink（审计报告，无 consumer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "audit", "output", "kind.sink"],
    examples=[
        {
            "summary": "validation=0 drifts=2 orphans=5 ...",
            "has_issues": True,
            "validation_issues": [],
            "anchor_drifts": [{"karch_id": "kb.arch.x", "anchor": "...", "reason": "..."}],
        }
    ],
)

# ─────────────────── 5. 重建索引 ───────────────────

KB_REBUILD_REQUEST = Material(
    id="kb.rebuild_request",
    name="KB Index Rebuild Request",
    description=(
        "触发 OmniKB 知识索引重建的入口请求。"
        "无必填字段，全量扫描 project_root/.omni/knowledge/ 后重新生成 knowledge_index.json。"
        "通常在 KBWriteWorker 写入后触发，或用户手动调用。"
        "Kind: source（外部触发，无 producer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "rebuild", "kind.source"],
    examples=[
        {},
    ],
)

KB_INDEX_STATS = Material(
    id="kb.index_stats",
    name="KB Index Stats",
    description=(
        "KBIndexRebuildWorker 重建索引后产出的统计信息。"
        "包含 stats（dict，含 total 等聚合字段）和 path（索引文件落盘路径）。"
        "Kind: sink（索引统计，无 consumer Worker，见 F-19）。"
    ),
    parent="requirement",
    tags=["knowledge", "rebuild", "output", "kind.sink"],
    examples=[
        {
            "stats": {"total": 42, "by_type": {"karch": 10, "kdec": 15}},
            "path": ".omni/knowledge_index.json",
        }
    ],
)


ALL_FORMATS = [
    KB_QUERY,
    KB_QUERY_RESULT,
    KB_ENTRY_DRAFT,
    KB_ENTRY_COMMITTED,
    KB_LOCATE_QUERY,
    KB_LOCATE_RESULT,
    KB_AUDIT_REQUEST,
    KB_AUDIT_REPORT,
    KB_REBUILD_REQUEST,
    KB_INDEX_STATS,
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            try:
                registry.register(fmt)
            except ValueError:
                pass


__all__ = [
    "KB_QUERY",
    "KB_QUERY_RESULT",
    "KB_ENTRY_DRAFT",
    "KB_ENTRY_COMMITTED",
    "KB_LOCATE_QUERY",
    "KB_LOCATE_RESULT",
    "KB_AUDIT_REQUEST",
    "KB_AUDIT_REPORT",
    "KB_REBUILD_REQUEST",
    "KB_INDEX_STATS",
    "ALL_FORMATS",
    "register_formats",
]

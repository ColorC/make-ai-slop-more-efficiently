# [OMNI] origin=claude-code domain=semantic_auditor/formats.py ts=2026-04-20T00:00:00Z
# [OMNI] material_id="material:diagnosis.semantic_auditor.material_definitions.python"
"""semantic_auditor — Material 定义 (Clean Migration 2026-04-20).

Material kind 标注 (F-19):
  semantic_auditor.artifact-request      → kind.source    (外部触发, 无 producer Worker)
  semantic_auditor.artifact-set          → kind.internal  (Worker 间流转)
  semantic_auditor.audit-target-set      → kind.internal  (Worker 间流转)
  semantic_auditor.audit-excerpt-set     → kind.internal  (Worker 间流转)
  semantic_auditor.finding-set           → kind.internal  (Worker 间流转)
  semantic_auditor.finding-written       → kind.sink      (最终落盘产出, 无 consumer Worker)
  semantic_auditor.audit-single-request  → kind.source    (AuditAgent 输入, 由外壳 Worker 触发)
  semantic_auditor.audit-single-finding  → kind.sink      (AuditAgent 输出, 外壳 Worker 聚合)
"""
from __future__ import annotations

from omnifactory.packages.services._core.omnicompany import Material
from omnifactory.protocol.format import FormatRegistry


FORMATS = [
    Material(
        id="semantic_auditor.artifact-request",
        name="SemanticAuditorArtifactRequest",
        description=(
            "语义审计触发入口：可传 paths 列表 / source=git-diff / source=full-scan 三种形式。"
            "验证标准：必须含 project_root 或 paths/source 之一。"
            "下游用途：ArtifactSelectorWorker 据此枚举 artifact 并打 kind 标签。"
            "Kind: source (外部触发 · 无 producer Worker · 见 F-19)。"
        ),
        parent="requirement",
        tags=["semantic_auditor", "kind.source"],
        json_schema={
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string", "enum": ["git-diff", "full-scan"]},
            },
        },
    ),
    Material(
        id="semantic_auditor.artifact-set",
        name="SemanticAuditorArtifactSet",
        description=(
            "待审 Artifact 清单：每个 Artifact 含 path + kind (router / design_md / format / ...)"
            "kind 由 standards-index.yaml.kind_inference 推断, 未命中为 None。"
            "验证标准：artifacts 字段必须是 list[{path, kind}]。"
            "下游用途：StandardMatcherWorker 按 kind + path_match 匹配适用 standard id。"
            "Kind: internal (Worker 间流转 · 见 F-19)。"
        ),
        parent="semantic_auditor.artifact-request",
        tags=["semantic_auditor", "structured", "kind.internal"],
        json_schema={
            "type": "object",
            "required": ["artifacts"],
            "properties": {
                "project_root": {"type": "string"},
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["path"],
                        "properties": {
                            "path": {"type": "string"},
                            "kind": {"type": ["string", "null"]},
                        },
                    },
                },
                "artifact_count": {"type": "integer"},
            },
        },
    ),
    Material(
        id="semantic_auditor.audit-target-set",
        name="SemanticAuditorAuditTargetSet",
        description=(
            "审计目标清单：每个 target 含 artifact + applicable_standards (适用的 standard id 列表)。"
            "无适用标准的 artifact 不会出现在此 set 中（unmatched_artifacts 记录数量）。"
            "验证标准：audit_targets 字段必须是 list[{artifact, applicable_standards}]。"
            "下游用途：ExcerptRetrieverWorker 按 standard_id 取标准摘录。"
            "Kind: internal (Worker 间流转 · 见 F-19)。"
        ),
        parent="semantic_auditor.artifact-set",
        tags=["semantic_auditor", "structured", "kind.internal"],
        json_schema={
            "type": "object",
            "required": ["audit_targets"],
            "properties": {
                "project_root": {"type": "string"},
                "audit_targets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["artifact", "applicable_standards"],
                        "properties": {
                            "artifact": {"type": "object"},
                            "applicable_standards": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "target_count": {"type": "integer"},
                "unmatched_artifacts": {"type": "integer"},
            },
        },
    ),
    Material(
        id="semantic_auditor.audit-excerpt-set",
        name="SemanticAuditorAuditExcerptSet",
        description=(
            "审计摘录清单：每条 excerpt 含 target / standard_id / excerpt_text / excerpt_len。"
            "excerpt_strategy=full → 整份; section → 按 key_sections 切块; fallback full。"
            "验证标准：excerpts 字段必须是 list[{target, standard_id, excerpt_text}]。"
            "下游用途：LLMAuditWorker 对每条 excerpt 调起一次 AuditAgent 单审。"
            "Kind: internal (Worker 间流转 · 见 F-19)。"
        ),
        parent="semantic_auditor.audit-target-set",
        tags=["semantic_auditor", "structured", "kind.internal"],
        json_schema={
            "type": "object",
            "required": ["excerpts"],
            "properties": {
                "project_root": {"type": "string"},
                "excerpts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["target", "standard_id", "excerpt_text"],
                        "properties": {
                            "target": {"type": "object"},
                            "standard_id": {"type": "string"},
                            "excerpt_text": {"type": "string"},
                            "excerpt_len": {"type": "integer"},
                        },
                    },
                },
                "excerpt_count": {"type": "integer"},
                "failed_retrievals": {"type": "array"},
            },
        },
    ),
    Material(
        id="semantic_auditor.finding-set",
        name="SemanticAuditorFindingSet",
        description=(
            "LLM 审计产出的 Finding 清单。每个 Finding 含 standard_id / target_path / description /"
            " confidence / recommended_action (可选 line_hint)。"
            "验证标准：findings 字段必须是 list; FindingWriter 再做严格字段校验。"
            "下游用途：FindingWriterWorker 验证字段后 append REGISTRY.md + ARCH-CHANGES.jsonl。"
            "Kind: internal (Worker 间流转 · 见 F-19)。"
        ),
        parent="semantic_auditor.audit-excerpt-set",
        tags=["semantic_auditor", "structured", "kind.internal"],
        json_schema={
            "type": "object",
            "required": ["findings"],
            "properties": {
                "project_root": {"type": "string"},
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "standard_id", "target_path", "description",
                            "confidence", "recommended_action",
                        ],
                    },
                },
                "finding_count": {"type": "integer"},
                "audit_count": {"type": "integer"},
                "parse_errors": {"type": "array"},
            },
        },
    ),
    Material(
        id="semantic_auditor.finding-written",
        name="SemanticAuditorFindingWritten",
        description=(
            "Finding 落盘结果：added / deduped / rejected / arch_events / new_ids。"
            "REGISTRY.md §语义合规待审 和 ARCH-CHANGES.jsonl 已 append, 返回统计给调用者。"
            "验证标准：含 added / deduped / rejected 三字段。"
            "Kind: sink (最终产出, 无 consumer Worker · 见 F-19)。"
        ),
        parent="semantic_auditor.finding-set",
        tags=["semantic_auditor", "structured", "validated", "kind.sink"],
        json_schema={
            "type": "object",
            "required": ["added"],
            "properties": {
                "added": {"type": "integer"},
                "deduped": {"type": "integer"},
                "rejected": {"type": "array"},
                "arch_events": {"type": "integer"},
                "new_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    Material(
        id="semantic_auditor.audit-single-request",
        name="SemanticAuditorAuditSingleRequest",
        description=(
            "AuditAgent 单次审计输入：含 task (user message, 描述 artifact/standard/excerpt) 和 trace_id。"
            "由 LLMAuditWorker 循环外壳拼装并交给 AuditAgent (AgentNodeLoop 子类) 处理。"
            "Kind: source (AuditAgent 订阅入口, producer 是循环外壳 · 见 F-19)。"
        ),
        parent="requirement",
        tags=["semantic_auditor", "kind.source", "agent_loop"],
        json_schema={
            "type": "object",
            "required": ["task"],
            "properties": {
                "task": {"type": "string"},
                "trace_id": {"type": "string"},
            },
        },
    ),
    Material(
        id="semantic_auditor.audit-single-finding",
        name="SemanticAuditorAuditSingleFinding",
        description=(
            "AuditAgent 单次审计输出：LLM 通过 finish 工具提交的 JSON 字符串 (在 output.text 内)。"
            "外壳 Worker 解析 text = {findings: [...]}, 合并到 finding-set。"
            "Kind: sink (AuditAgent 终产出, 外壳 Worker 消费 · 见 F-19)。"
        ),
        parent="semantic_auditor.audit-single-request",
        tags=["semantic_auditor", "structured", "kind.sink", "agent_loop"],
        json_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "finish 工具的 result (JSON 字符串)"},
                "turn_count": {"type": "integer"},
                "stop_reason": {"type": "string"},
                "trace_id": {"type": "string"},
            },
        },
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        registry.register(fmt)

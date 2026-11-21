# [OMNI] origin=claude-code domain=services/docauthor ts=2026-04-25T00:00:00Z type=format
# [OMNI] material_id="material:authoring.docauthor.material_schemas.registry.py"
"""docauthor service Material (Format) 声明.

Phase A: manifest-request + manifest-draft
Phase B: 追加 design-request / design-draft / review-request / review-verdict
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Material
from omnicompany.protocol.format import FormatRegistry


MANIFEST_REQUEST = Material(
    id="docauthor.manifest-request",
    name="DocAuthorManifestRequest",
    description=(
        "请求 ManifestAuthorWorker 为指定 service/package 生成 .omni/manifest.yaml draft. "
        "target_service_path 是仓库相对路径 (如 'src/omnicompany/packages/services/foo' 或 "
        "'src/omnicompany/packages/domains/voxelcraft/item'). Worker 依此扫目录 + 读 DESIGN + "
        "grep plan history + 调 LLM (qwen-3.6-plus) 产三 kind (data_layout / aging_policy / "
        "size_limits) 的 manifest 内容."
    ),
    parent="requirement",
    tags=["docauthor", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "target_service_path": {
                "type": "string",
                "description": "目标 service/package 路径 (repo-relative)",
            },
            "notes_hint": {
                "type": "string",
                "description": "可选 · 人类提示 Worker 关注点 (如 '该 service 产 sandbox 子目录')",
            },
        },
        "required": ["target_service_path"],
    },
)


MANIFEST_DRAFT = Material(
    id="docauthor.manifest-draft",
    name="DocAuthorManifestDraft",
    description=(
        "ManifestAuthorWorker 产出的 manifest.yaml draft (未落盘). 包含 manifest 文本 + "
        "扫描证据 (scan_evidence 说明 Worker 看到什么目录/文件, 便于 Reviewer 和人工复核) + "
        "notes (Worker 自报的假设/不确定点). Phase A 由测试 harness 收取; Phase B 作为 "
        "DocReviewerWorker 的输入."
    ),
    parent="requirement",
    tags=["docauthor", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "manifest_path": {
                "type": "string",
                "description": "若最终落盘, 应在 target 下的 .omni/manifest.yaml 相对路径",
            },
            "manifest_content": {
                "type": "string",
                "description": "完整 manifest YAML 文本 (含 OmniMark 头 + 三 kind document)",
            },
            "scan_evidence": {
                "type": "object",
                "description": "Worker 扫到的原始信息快照 (目录列表/DESIGN 节选/plan 命中)",
            },
            "notes": {
                "type": "string",
                "description": "Worker 自报的假设 / 不确定点 / 未覆盖场景",
            },
        },
        "required": ["manifest_path", "manifest_content"],
    },
)


DESIGN_REQUEST = Material(
    id="docauthor.design-request",
    name="DocAuthorDesignRequest",
    description=(
        "请求 DesignDocAuthorWorker 为指定 package 生成 DESIGN.md draft. "
        "target_package_path 是仓库相对路径. Worker 扫源文件 + 读 docstring + "
        "读已有 DESIGN + grep plan 节选, 调 qwen-3.6-plus 产七节齐全的 DESIGN.md "
        "(基础设施模块自动加第 8 节 ## 接收意愿). 支持 Refine 循环 (传 prior_draft + review_feedback)."
    ),
    parent="requirement",
    tags=["docauthor", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "target_package_path": {"type": "string"},
            "upgrade_from_skeleton": {"type": "boolean"},
            "prior_draft": {"type": "string"},
            "review_feedback": {"type": "string"},
        },
        "required": ["target_package_path"],
    },
)


DESIGN_DRAFT = Material(
    id="docauthor.design-draft",
    name="DocAuthorDesignDraft",
    description=(
        "DesignDocAuthorWorker 产出的 DESIGN.md draft (未落盘). 含 design 文本 + sections_filled + "
        "scan_evidence + notes. Phase B 作为 DocReviewerWorker 的 review-request 输入."
    ),
    parent="requirement",
    tags=["docauthor", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "design_path": {"type": "string"},
            "design_content": {"type": "string"},
            "sections_filled": {"type": "array", "items": {"type": "string"}},
            "scan_evidence": {"type": "object"},
            "notes": {"type": "string"},
        },
        "required": ["design_path", "design_content"],
    },
)


README_REQUEST = Material(
    id="docauthor.readme-request",
    name="DocAuthorReadmeRequest",
    description=(
        "请求 ReadmeAuthorWorker 为指定 service/package 生成 README.md draft (按 self_narrative_three_files.md §四). "
        "Worker 扫源文件 + 读已有 DESIGN/manifest + grep plan, 调 qwen-3.6-plus 产 6 节齐全的 README.md "
        "(这是什么 / 解决什么不解决什么 / 设计目的与最终目标 / 规划 / 构成 / 想了解更多)."
    ),
    parent="requirement",
    tags=["docauthor", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "target_package_path": {"type": "string"},
            "prior_draft": {"type": "string"},
            "review_feedback": {"type": "string"},
        },
        "required": ["target_package_path"],
    },
)


README_DRAFT = Material(
    id="docauthor.readme-draft",
    name="DocAuthorReadmeDraft",
    description=(
        "ReadmeAuthorWorker 产出的 README.md draft. 含 readme 文本 + sections_filled + scan_evidence + notes."
    ),
    parent="requirement",
    tags=["docauthor", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "readme_path": {"type": "string"},
            "readme_content": {"type": "string"},
            "sections_filled": {"type": "array", "items": {"type": "string"}},
            "scan_evidence": {"type": "object"},
            "notes": {"type": "string"},
        },
        "required": ["readme_path", "readme_content"],
    },
)


SKILL_REQUEST = Material(
    id="docauthor.skill-request",
    name="DocAuthorSkillRequest",
    description=(
        "请求 SkillAuthorWorker 为指定 service/package 生成 SKILL.md draft (按 self_narrative_three_files.md §六). "
        "Worker 扫源文件 + 读已有 README/DESIGN + grep cli/commands/, 调 qwen-3.6-plus 产 frontmatter + 6 节. "
        "(适用范围 / 前置条件 / 操作步骤 / 入口清单 / 故障排查 / 想了解更多)."
    ),
    parent="requirement",
    tags=["docauthor", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {
            "target_package_path": {"type": "string"},
            "prior_draft": {"type": "string"},
            "review_feedback": {"type": "string"},
        },
        "required": ["target_package_path"],
    },
)


SKILL_DRAFT = Material(
    id="docauthor.skill-draft",
    name="DocAuthorSkillDraft",
    description=(
        "SkillAuthorWorker 产出的 SKILL.md draft. 含 skill 文本 (frontmatter + OmniMark 头 + 6 节) + "
        "sections_filled + scan_evidence + notes."
    ),
    parent="requirement",
    tags=["docauthor", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "skill_path": {"type": "string"},
            "skill_content": {"type": "string"},
            "sections_filled": {"type": "array", "items": {"type": "string"}},
            "scan_evidence": {"type": "object"},
            "notes": {"type": "string"},
        },
        "required": ["skill_path", "skill_content"],
    },
)


REVIEW_REQUEST = Material(
    id="docauthor.review-request",
    name="DocAuthorReviewRequest",
    description=(
        "请求 DocReviewerWorker 审一份 Author 产的 draft. target_type 'manifest'|'design'|'readme'|'skill'. "
        "Reviewer 做结构硬校 (OmniMark/节齐全/severity合法) + 引用 grep 真实性 + "
        "LLM 内容质量判 (非占位/决策有理由/升级路径具体/与scan_evidence一致). "
        "独立审判纪律: 不与 Author 混用 LLM 调用."
    ),
    parent="requirement",
    tags=["docauthor", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "draft_content": {"type": "string"},
            "target_type": {"type": "string", "enum": ["manifest", "design", "readme", "skill"]},
            "target_path": {"type": "string"},
            "scan_evidence": {"type": "object"},
        },
        "required": ["draft_content", "target_type"],
    },
)


REVIEW_VERDICT = Material(
    id="docauthor.review-verdict",
    name="DocAuthorReviewVerdict",
    description=(
        "DocReviewerWorker 审判结果. passed=True (critical==0) 进终局落盘; "
        "否则 RefineConductor 把 issues 反馈给 Author 重写. "
        "**不含 score** (无统一尺度, 避免压缩语义信号). 每 issue 带 evidence 原文引用."
    ),
    parent="requirement",
    tags=["docauthor", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "passed": {"type": "boolean", "description": "True iff zero critical issues"},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                        "field": {"type": "string"},
                        "message": {"type": "string"},
                        "evidence": {"type": "string", "description": "draft/scan 原文引用 · 可核验"},
                        "fix_hint": {"type": "string"},
                    },
                },
            },
            "counts": {"type": "object", "description": "仅计数, 不作分数 {critical, major, minor}"},
            "llm_notes": {"type": "string"},
            "target_type": {"type": "string"},
            "target_path": {"type": "string"},
        },
        "required": ["passed", "issues", "counts"],
    },
)


JOB_FINAL = Material(
    id="docauthor.job-final",
    name="DocAuthorJobFinal",
    description=(
        "FinalLanderWorker 产出的**终局观测信号** (sink). 声明本 docauthor job 走到了 terminal "
        "(passed 或 refine budget 耗尽), 文件是否实际落盘, 备份路径, issue 全量. CLI / 监控可订此 "
        "Material 作'跑完了'信号."
    ),
    parent="requirement",
    tags=["docauthor", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "terminal_status": {"type": "string", "description": "passed | exhausted_at_iter_N"},
            "target_type": {"type": "string", "enum": ["manifest", "design"]},
            "target_path": {"type": "string"},
            "landing_rel": {"type": "string", "description": "src/ 下落盘的相对路径"},
            "write_status": {"type": "string", "enum": ["written", "dry_run"]},
            "written_bytes": {"type": "integer"},
            "iter": {"type": "integer"},
            "max_refine_iters": {"type": "integer"},
            "passed": {"type": "boolean"},
            "issue_counts": {"type": "object"},
            "issues": {"type": "array"},
            "llm_notes": {"type": "string"},
            "backup_path": {"type": "string"},
        },
        "required": ["terminal_status", "target_type", "target_path", "write_status"],
    },
)


def register_formats(registry: FormatRegistry) -> None:
    """注册 docauthor 的 Material 到全局 FormatRegistry."""
    registry.register(MANIFEST_REQUEST)
    registry.register(MANIFEST_DRAFT)
    registry.register(DESIGN_REQUEST)
    registry.register(DESIGN_DRAFT)
    registry.register(README_REQUEST)
    registry.register(README_DRAFT)
    registry.register(SKILL_REQUEST)
    registry.register(SKILL_DRAFT)
    registry.register(REVIEW_REQUEST)
    registry.register(REVIEW_VERDICT)
    registry.register(JOB_FINAL)


__all__ = [
    "MANIFEST_REQUEST", "MANIFEST_DRAFT",
    "DESIGN_REQUEST", "DESIGN_DRAFT",
    "README_REQUEST", "README_DRAFT",
    "SKILL_REQUEST", "SKILL_DRAFT",
    "REVIEW_REQUEST", "REVIEW_VERDICT",
    "JOB_FINAL",
    "register_formats",
]

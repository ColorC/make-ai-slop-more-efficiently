# [OMNI] origin=claude-code domain=project_atlas ts=2026-06-21 type=format status=active
# [OMNI] summary="project_atlas domain 的 Material(Format)定义。收集管线节点间流动的数据契约(4 节点)。"
# [OMNI] why="框架级统一:产物只能是 Material。语义起草交给带工具 worker,故中间态只到 surveyed→collected。"
# [OMNI] tags=project_atlas,format,material
"""project_atlas domain Materials。

链路: project_atlas.request → .intake → .surveyed → .collected → .record
"""

from __future__ import annotations

from omnicompany.protocol.format import Format, FormatRegistry

PA_REQUEST = Format(
    id="project_atlas.request",
    name="ProjectAtlasRequest",
    description="一次收集的发起请求。来自 omni run CLI。字段: space(必填,见 spaces.py)、dry_run。",
    tags=["domain.project_atlas", "stage.request", "kind.source"],
    json_schema={
        "type": "object",
        "properties": {"space": {"type": "string"}, "dry_run": {"type": ["boolean", "string"]}},
        "required": ["space"],
    },
)

PA_INTAKE = Format(
    id="project_atlas.intake",
    name="ProjectAtlasIntake",
    description="入题态:space 解析成根路径 + run_dir + group/tier + dry_run。",
    tags=["domain.project_atlas", "stage.intake", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "space": {"type": "string"}, "root": {"type": "string"}, "run_dir": {"type": "string"},
            "group": {"type": "string"}, "tier": {"type": "string"}, "dry_run": {"type": "boolean"},
        },
        "required": ["space", "root", "run_dir"],
    },
)

PA_SURVEYED = Format(
    id="project_atlas.surveyed",
    name="ProjectAtlasSurveyed",
    description="勘察态:确定性收来的线索地图(顶层目录 + 清单文件摘要),给 worker 当起点。",
    tags=["domain.project_atlas", "stage.surveyed", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "space": {"type": "string"}, "root": {"type": "string"}, "run_dir": {"type": "string"},
            "clues": {"type": "string"}, "top_dirs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["space", "run_dir", "clues"],
    },
)

PA_COLLECTED = Format(
    id="project_atlas.collected",
    name="ProjectAtlasCollected",
    description="收集态:带工具 worker 已实地核实并把 grounded object-SKILL 写进 staging;带回 worker_status。",
    tags=["domain.project_atlas", "stage.collected", "kind.internal"],
    json_schema={
        "type": "object",
        "properties": {
            "space": {"type": "string"}, "run_dir": {"type": "string"},
            "worker_status": {"type": "string"}, "worker_final": {"type": "string"},
        },
        "required": ["space", "run_dir"],
    },
)

PA_RECORD = Format(
    id="project_atlas.record",
    name="ProjectAtlasRecord",
    description="管线 sink:读 staging 实际产物得 object-SKILL 数 + 名录草稿 + 报告路径(待人审)。",
    tags=["domain.project_atlas", "stage.record", "kind.sink"],
    json_schema={
        "type": "object",
        "properties": {
            "space": {"type": "string"}, "run_dir": {"type": "string"},
            "n_skills": {"type": "integer"}, "report": {"type": "string"},
        },
        "required": ["space", "n_skills"],
    },
)

ALL_FORMATS = [PA_REQUEST, PA_INTAKE, PA_SURVEYED, PA_COLLECTED, PA_RECORD]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in ALL_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)

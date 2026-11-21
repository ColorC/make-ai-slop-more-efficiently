# [OMNI] origin=claude-code domain=services/_focus ts=2026-06-23T00:00:00Z type=format
# [OMNI] material_id="material:services._focus.plan_progress_recorder.formats.py"
"""plan-progress-recorder 的 Material 契约（三件: source/internal/sink, 各带 kind.* tag, F-19）。"""
from omnicompany.packages.services._core.omnicompany import Material

# --- planprog.request (kind=source) -----------------------------------------
REQUEST = Material(
    id="planprog.request",
    name="planprog.request",
    description=(
        "计划进度记录请求(source)。外部注入: 要给哪个计划评估并记录进度。"
        "plan_id=docs/plans 下的子路径(可含 [日期] 前缀的目录名); task_id=可选, "
        "whatnow 里要更新的 task id(缺省则 Recorder 按 plan_id 在 board 匹配); "
        "plan_root=可选, 覆盖默认的 docs/plans 根。被 PlanProgressExtractorWorker 消费。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "task_id": {"type": "string"},
            "plan_root": {"type": "string"},
        },
        "required": ["plan_id"],
    },
    tags=["domain.focus", "team.plan_progress_recorder", "kind.source"],
)

# --- planprog.assessment (kind=internal) ------------------------------------
ASSESSMENT = Material(
    id="planprog.assessment",
    name="planprog.assessment",
    description=(
        "计划进度评估(internal)。由 PlanProgressExtractorWorker 的 gpt-5.5 tool-agent "
        "进计划目录 list/read/grep 真产物后自评: title(中文标题)、line(main/side)、"
        "status(todo/in_progress/paused/done)、completion(0-100)、progress_note(≤40字最新进展)、"
        "evidence[](支撑判断的具体文件/勾叉证据)。被 WhatnowRecorderWorker 消费。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "task_id": {"type": "string"},
            "title": {"type": "string"},
            "line": {"type": "string", "enum": ["main", "side"]},
            "status": {"type": "string", "enum": ["todo", "in_progress", "paused", "done"]},
            "completion": {"type": "integer"},
            "progress_note": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["plan_id", "title", "line", "status", "completion", "progress_note"],
    },
    tags=["domain.focus", "team.plan_progress_recorder", "kind.internal"],
)

# --- planprog.recorded (kind=sink) ------------------------------------------
RECORDED = Material(
    id="planprog.recorded",
    name="planprog.recorded",
    description=(
        "进度落地回执(sink)。WhatnowRecorderWorker 把评估 POST 进 whatnow(:8230) 后的结果: "
        "recorded(是否成功写入)、task_id(实际命中的 whatnow task)、completion/status(写入后的值)、"
        "whatnow_ok(服务是否可达且确认)、note(人读说明)。team 终点。"
    ),
    json_schema={
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "task_id": {"type": "string"},
            "recorded": {"type": "boolean"},
            "completion": {"type": "integer"},
            "status": {"type": "string"},
            "whatnow_ok": {"type": "boolean"},
            "note": {"type": "string"},
        },
        "required": ["recorded", "whatnow_ok"],
    },
    tags=["domain.focus", "team.plan_progress_recorder", "kind.sink"],
)

ALL_FORMATS = [REQUEST, ASSESSMENT, RECORDED]

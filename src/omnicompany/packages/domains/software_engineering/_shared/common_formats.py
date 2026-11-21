# [OMNI] origin=human domain=software_engineering/_shared ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:domains.software_engineering.shared_format_base.definitions.py"
"""sw.common_formats — 软件工程管线共享 Format 基础层

所有 sw-* 管线共用的 9 个基础 Format:

  sw.task-input          统一输入
  sw.project-snapshot    项目快照
  sw.file-content        单文件内容
  sw.file-batch          文件批次（分片载体）
  sw.code-change         单文件变更（支持 diff）
  sw.change-set          变更集（逻辑功能单元）
  sw.test-exec-result    测试执行结果
  sw.llm-review          LLM 审查结果
  sw.report              最终报告

分片规则:
  - file-content:  单文件 > 8KB → 截断为 imports + signatures + 首 200 行
  - file-batch:    > 5 文件或 > 30KB → 拆批
  - change-set:    > 5 个变更 → 拆成多个 change-set
  - project-snapshot: tree > 3KB → 截断至前 3 层
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "sw"

# ═══════════════════════════════════════════════════════════════════════════════
# 分片/截断常量
# ═══════════════════════════════════════════════════════════════════════════════

MAX_FILE_CONTENT_BYTES = 8192          # 单文件最大全文大小
MAX_FILE_BATCH_SIZE = 5                # 单批次最大文件数
MAX_FILE_BATCH_BYTES = 30_000          # 单批次最大总字节
MAX_CHANGE_SET_SIZE = 5                # 单变更集最大变更数
MAX_TREE_BYTES = 3000                  # 项目目录树最大大小
AGENT_LOOP_THRESHOLD_FILES = 5         # 超过此文件数 → 考虑 agent_loop
AGENT_LOOP_THRESHOLD_BYTES = 40_000    # 超过此字节数 → 考虑 agent_loop


# ═══════════════════════════════════════════════════════════════════════════════
# 共享基础 Format 定义
# ═══════════════════════════════════════════════════════════════════════════════

SHARED_FORMATS = [

    # ── 1. 统一输入 ──
    Format(
        id=f"{DOMAIN}.task-input",
        name="SWTaskInput",
        description=(
            "所有 sw-* 管线的统一入口。"
            "包含: task_text(任务描述), project_dir(项目目录), "
            "task_type(design/plan/tdd/implement/review/verify), "
            "scope(targeted/feature/module) 范围提示。"
        ),
        parent="intent",
        json_schema={
            "type": "object",
            "required": ["task_text", "project_dir"],
            "properties": {
                "task_text":    {"type": "string", "description": "任务/需求描述文本"},
                "project_dir":  {"type": "string", "description": "项目根目录绝对路径"},
                "task_type":    {"type": "string", "enum": ["design", "plan", "tdd", "implement", "review", "verify"]},
                "scope":        {"type": "string", "enum": ["targeted", "feature", "module", "project"]},
                "related_files": {"type": "array", "items": {"type": "string"}, "description": "任务直接相关的文件路径"},
            },
        },
    ),

    # ── 2. 项目快照 ──
    Format(
        id=f"{DOMAIN}.project-snapshot",
        name="ProjectSnapshot",
        description=(
            "项目结构快照。tree(目录树, ≤3KB), "
            "primary_language, file_count, "
            "key_files(配置/入口文件列表)。"
            "不含文件内容 — 内容在 file-content 中。"
        ),
        parent="tool-observation",
        json_schema={
            "type": "object",
            "required": ["tree", "primary_language"],
            "properties": {
                "tree":             {"type": "string", "maxLength": 3000},
                "primary_language": {"type": "string"},
                "file_count":       {"type": "integer"},
                "key_files":        {"type": "array", "items": {"type": "string"}},
                "top_level_dirs":   {"type": "array", "items": {"type": "string"}},
            },
        },
    ),

    # ── 3. 单文件内容 ──
    Format(
        id=f"{DOMAIN}.file-content",
        name="FileContent",
        description=(
            "单个文件的结构化内容。"
            "path(相对路径), language, size_bytes, content(≤8KB), "
            "imports(提取的导入), signatures(函数/类签名), "
            "truncated(是否被截断)。"
        ),
        parent="tool-observation",
        json_schema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path":        {"type": "string"},
                "language":    {"type": "string"},
                "size_bytes":  {"type": "integer"},
                "content":     {"type": "string"},
                "imports":     {"type": "array", "items": {"type": "string"}},
                "signatures":  {"type": "array", "items": {"type": "string"}},
                "truncated":   {"type": "boolean", "default": False},
            },
        },
    ),

    # ── 4. 文件批次（分片载体）──
    Format(
        id=f"{DOMAIN}.file-batch",
        name="FileBatch",
        description=(
            "文件批次 — 分片载体。"
            "每批 ≤ 5 个文件或 ≤ 30KB。"
            "batch_index/total_batches 用于多批场景。"
        ),
        parent="tool-observation",
        json_schema={
            "type": "object",
            "required": ["files"],
            "properties": {
                "batch_index":   {"type": "integer", "default": 0},
                "total_batches": {"type": "integer", "default": 1},
                "files":         {"type": "array", "description": "FileContent 列表"},
                "total_files_in_project": {"type": "integer"},
            },
        },
    ),

    # ── 5. 单文件变更 ──
    Format(
        id=f"{DOMAIN}.code-change",
        name="CodeChange",
        description=(
            "单个文件的代码变更。"
            "action(create/modify/delete)。"
            "modify 时: diff(unified format) + changed_lines。"
            "create 时: full_content。"
            "delete 时: 仅 path。"
            "rationale(变更理由)。"
        ),
        parent="spec",
        json_schema={
            "type": "object",
            "required": ["path", "action"],
            "properties": {
                "path":          {"type": "string"},
                "action":        {"type": "string", "enum": ["create", "modify", "delete"]},
                "diff":          {"type": "string", "description": "unified diff (modify 时)"},
                "full_content":  {"type": "string", "description": "完整内容 (create 时)"},
                "changed_lines": {"type": "array", "items": {"type": "integer"}},
                "rationale":     {"type": "string"},
            },
        },
    ),

    # ── 6. 变更集 ──
    Format(
        id=f"{DOMAIN}.change-set",
        name="ChangeSet",
        description=(
            "一个逻辑功能的全部变更。"
            "changes(≤5 个 code-change)。"
            "超过 5 个文件或 40KB → 拆分或升级为 agent_loop。"
            "管线间串联的核心类型。"
        ),
        parent="spec",
        json_schema={
            "type": "object",
            "required": ["changes"],
            "properties": {
                "changes":        {"type": "array", "description": "CodeChange 列表"},
                "description":    {"type": "string"},
                "depends_on":     {"type": "array", "items": {"type": "string"}, "description": "前置 change-set id"},
                "test_cmd":       {"type": "string", "description": "验证该变更集的测试命令"},
                "needs_agent_loop": {"type": "boolean", "default": False},
            },
        },
    ),

    # ── 7. 测试执行结果 ──
    Format(
        id=f"{DOMAIN}.test-exec-result",
        name="TestExecResult",
        description=(
            "测试命令执行结果。"
            "cmd, exit_code, stdout(≤3KB), stderr(≤1KB), "
            "passed/failed/errors 计数, duration_ms。"
        ),
        parent="tool-observation",
        json_schema={
            "type": "object",
            "required": ["cmd", "exit_code"],
            "properties": {
                "cmd":          {"type": "string"},
                "exit_code":    {"type": "integer"},
                "stdout":       {"type": "string", "maxLength": 3000},
                "stderr":       {"type": "string", "maxLength": 1000},
                "passed":       {"type": "integer"},
                "failed":       {"type": "integer"},
                "errors":       {"type": "integer"},
                "duration_ms":  {"type": "integer"},
            },
        },
    ),

    # ── 8. LLM 审查结果 ──
    Format(
        id=f"{DOMAIN}.llm-review",
        name="LLMReview",
        description=(
            "LLM 审查输出。"
            "findings(按严重度分级: Critical/Important/Minor), "
            "conclusion(APPROVE/NEEDS_REVISION/REJECT), "
            "summary(审查摘要)。"
        ),
        parent="agent-state",
        json_schema={
            "type": "object",
            "required": ["conclusion"],
            "properties": {
                "findings":  {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["Critical", "Important", "Minor"]},
                        "title":    {"type": "string"},
                        "detail":   {"type": "string"},
                        "file":     {"type": "string"},
                        "line":     {"type": "integer"},
                        "suggestion": {"type": "string"},
                    },
                }},
                "conclusion":  {"type": "string", "enum": ["APPROVE", "NEEDS_REVISION", "REJECT"]},
                "summary":     {"type": "string"},
            },
        },
    ),

    # ── 9. 最终报告 ──
    Format(
        id=f"{DOMAIN}.report",
        name="SWReport",
        description=(
            "sw-* 管线的通用最终报告。"
            "report_text(格式化文本), conclusion, "
            "metrics(键值对指标, 如 pass_rate/files_changed)。"
        ),
        parent="spec",
        json_schema={
            "type": "object",
            "required": ["report_text", "conclusion"],
            "properties": {
                "report_text":  {"type": "string"},
                "conclusion":   {"type": "string"},
                "metrics":      {"type": "object", "additionalProperties": True},
            },
        },
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 注册 + 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def register_shared_formats(registry: FormatRegistry) -> None:
    """注册共享 Format（需先注册 BUILTIN_FORMATS）"""
    for fmt in SHARED_FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)


def needs_chunking(files: list[dict]) -> bool:
    """判断文件列表是否需要分片"""
    if len(files) > MAX_FILE_BATCH_SIZE:
        return True
    total = sum(len(f.get("content", "")) for f in files)
    return total > MAX_FILE_BATCH_BYTES


def chunk_files(files: list[dict]) -> list[list[dict]]:
    """将文件列表按分片规则拆成多批"""
    batches = []
    current = []
    current_size = 0

    for f in files:
        fsize = len(f.get("content", ""))
        if current and (len(current) >= MAX_FILE_BATCH_SIZE
                        or current_size + fsize > MAX_FILE_BATCH_BYTES):
            batches.append(current)
            current = []
            current_size = 0
        current.append(f)
        current_size += fsize

    if current:
        batches.append(current)
    return batches


def truncate_file_content(content: str, max_bytes: int = MAX_FILE_CONTENT_BYTES) -> tuple[str, bool]:
    """截断文件内容，返回 (内容, 是否截断)"""
    if len(content) <= max_bytes:
        return content, False
    lines = content.splitlines(keepends=True)
    result = []
    size = 0
    for line in lines:
        if size + len(line) > max_bytes:
            break
        result.append(line)
        size += len(line)
    return "".join(result) + "\n... (truncated)\n", True


def needs_agent_loop(changes: list[dict]) -> bool:
    """判断变更集是否需要升级为 agent_loop"""
    if len(changes) > AGENT_LOOP_THRESHOLD_FILES:
        return True
    total = sum(len(c.get("full_content", "") + c.get("diff", "")) for c in changes)
    return total > AGENT_LOOP_THRESHOLD_BYTES

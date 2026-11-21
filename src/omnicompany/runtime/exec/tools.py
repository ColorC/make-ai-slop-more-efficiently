# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.exec.tool_definitions.schema_registry.py"
"""工具定义 — Anthropic tool_use 格式

复刻 OpenHands CodeAct Agent 的 4 个核心工具:
    bash            — 执行 shell 命令 (Windows: cmd, Linux: bash)
    str_replace_editor — 文件查看/创建/编辑 (view, create, str_replace, insert, undo_edit)
    finish          — 信号任务完成
    think           — 记录推理过程 (无副作用)
"""

import os as _os

_SHELL_DESC = "cmd" if _os.name == "nt" else "bash"

# 意图字段 schema，嵌入到各工具定义中（$defs 引用）
_INTENT_SCHEMA = {
    "type": "object",
    "description": (
        "Semantic intent metadata for this tool call. "
        "Extracted before execution — never passed to the tool itself."
    ),
    "properties": {
        "input_types": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Semantic types consumed by this step (must already exist in working context).",
        },
        "output_types": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Semantic types produced by this step (added to working context after execution).",
        },
        "action_class": {
            "type": "string",
            "enum": ["acquire", "execute", "summarize", "think"],
            "description": "acquire=read/fetch, execute=change state, summarize=transform info, think=reasoning only.",
        },
        "desc": {
            "type": "string",
            "description": "One concise English sentence (≤15 words) naming what this step does.",
        },
        "rationale": {
            "type": "string",
            "description": (
                "Detailed reasoning for this step (2–4 sentences). Cover: "
                "(1) what specific information/data you currently hold and are consuming, "
                "(2) what concrete information you expect to obtain after this step, "
                "(3) why this step is necessary right now given the overall goal. "
                "Be specific — name actual file paths, API names, variable values when known."
            ),
        },
        "expected_output": {
            "type": "string",
            "description": (
                "Concrete prediction of what this tool call will return. "
                "Be specific: file contents shape, command stdout pattern, data structure, "
                "expected values. Example: 'A DataFrame with columns [A,B,C] and 4 rows, "
                "last row = [100,200,300]' or 'Exit code 0, stdout shows test results with "
                "PASSED count'."
            ),
        },
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Which previous step outputs this decision depends on. "
                "Reference by output_type name from earlier steps. "
                "Example: ['fs.path.solution_file', 'bash.stdout.test_output']. "
                "Must be complete — list ALL prior outputs that informed this decision."
            ),
        },
        "info_transform": {
            "type": "string",
            "description": (
                "What reasoning transformation happened: what information was combined or "
                "inferred to produce this action. Example: 'Observed NameError in test output "
                "(bash.stdout.test_output) → inferred missing import → decided to add import "
                "statement to solution file (fs.path.solution_file)'."
            ),
        },
    },
    "required": [
        "input_types", "output_types", "action_class", "desc", "rationale",
        "expected_output", "depends_on", "info_transform",
    ],
}

BASH_TOOL = {
    "name": "bash",
    "description": (
        f"Execute a {_SHELL_DESC} command in a shell session.\n"
        "* One command at a time. Chain with && or ; if needed.\n"
        "* For long-running commands, run in background.\n"
        "* Verify parent directory exists before creating files.\n"
        "* Use absolute paths when possible.\n"
        "* FORBIDDEN COMMANDS (will trigger death-zone pain and be blocked):\n"
        "  - find / find.exe: spawns massive process trees, use ls/glob instead\n"
        "  - head / head.exe: spawns excessive processes, use cat with pipe or sed instead\n"
        "  - Any use of these commands will be immediately rejected with pain propagation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": f"The {_SHELL_DESC} command to execute.",
            },
            "intent": {"$ref": "#/$defs/Intent"},
        },
        "required": ["command"],
        "$defs": {"Intent": _INTENT_SCHEMA},
    },
}

STR_REPLACE_EDITOR_TOOL = {
    "name": "str_replace_editor",
    "description": (
        "Custom editing tool for viewing, creating and editing files.\n"
        "* If path is a file, 'view' displays with line numbers (cat -n).\n"
        "  If path is a directory, 'view' lists files up to 2 levels deep.\n"
        "* 'create' creates a new file (fails if file already exists).\n"
        "* 'str_replace' replaces old_str with new_str. old_str must match exactly\n"
        "  and uniquely in the file. Include 3-5 lines of context for uniqueness.\n"
        "* 'insert' inserts new_str AFTER the specified insert_line.\n"
        "* 'undo_edit' reverts the last edit made to the file.\n"
        "* Always use absolute file paths (starting with /)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to run: view, create, str_replace, insert, undo_edit.",
                "enum": ["view", "create", "str_replace", "insert", "undo_edit"],
            },
            "path": {
                "type": "string",
                "description": "Absolute path to file or directory.",
            },
            "file_text": {
                "type": "string",
                "description": "Required for 'create': content of the new file.",
            },
            "old_str": {
                "type": "string",
                "description": "Required for 'str_replace': exact string to replace.",
            },
            "new_str": {
                "type": "string",
                "description": (
                    "For 'str_replace': replacement string (empty to delete). "
                    "For 'insert': string to insert after insert_line."
                ),
            },
            "insert_line": {
                "type": "integer",
                "description": "Required for 'insert': line number after which to insert new_str.",
            },
            "view_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Optional for 'view': [start_line, end_line] to show. "
                    "1-indexed. Use [start, -1] for start to end of file."
                ),
            },
            "intent": {"$ref": "#/$defs/Intent"},
        },
        "required": ["command", "path"],
        "$defs": {"Intent": _INTENT_SCHEMA},
    },
}

FINISH_TOOL = {
    "name": "finish",
    "description": (
        "Signal completion of the current task.\n"
        "Use when you have successfully completed the task, "
        "cannot proceed further, or need to ask for clarification.\n"
        "Include a clear summary of actions taken and results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Final message summarizing the result.",
            },
        },
        "required": ["message"],
    },
}

THINK_TOOL = {
    "name": "think",
    "description": (
        "Record intermediate reasoning synthesized from ACTUAL tool outputs. "
        "Must be grounded in real bash/editor results — not speculation. "
        "Calling think does NOT complete the task. You must still produce real output via bash or str_replace_editor."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "The thought to log.",
            },
            "intent": {"$ref": "#/$defs/Intent"},
        },
        "required": ["thought"],
        "$defs": {"Intent": _INTENT_SCHEMA},
    },
}

# ── Wave 1: 专用搜索工具（替代 bash find/grep，省 token、结构化返回）─────────

GLOB_TOOL = {
    "name": "glob",
    "description": (
        "Fast file path search using glob patterns.\n"
        "* Returns matching file paths with size and modification time.\n"
        "* Use instead of 'bash find' — faster, no process tree issues on Windows.\n"
        "* Pattern examples: '*.py', 'test_*.py', '**/*.md'\n"
        "* Results capped at 100 matches."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match (e.g. '*.py', 'src/**/*.ts').",
            },
            "path": {
                "type": "string",
                "description": "Root directory to search in. Defaults to current working directory.",
            },
            "intent": {"$ref": "#/$defs/Intent"},
        },
        "required": ["pattern"],
        "$defs": {"Intent": _INTENT_SCHEMA},
    },
}

GREP_TOOL = {
    "name": "grep",
    "description": (
        "Search for text patterns inside files.\n"
        "* Returns matching lines with file path, line number, and content.\n"
        "* Use instead of 'bash grep' — structured output, better for analysis.\n"
        "* Supports regex patterns.\n"
        "* Use 'include' to filter by file extension (e.g. '*.py').\n"
        "* Results capped at 50 matches."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Text or regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to current working directory.",
            },
            "include": {
                "type": "string",
                "description": "Optional glob to filter files (e.g. '*.py', '*.ts').",
            },
            "intent": {"$ref": "#/$defs/Intent"},
        },
        "required": ["pattern"],
        "$defs": {"Intent": _INTENT_SCHEMA},
    },
}

REGISTER_SEMANTIC_TYPES_TOOL = {
    "name": "register_semantic_types",
    "description": (
        "Register one or more semantic types for a domain you are working with.\n"
        "Use when you recognize a domain has well-known concepts worth tracking.\n"
        "Example: for Unity3D, register 'unity.asset.animation_clip', 'unity.script.monobehaviour', etc.\n"
        "Each type needs type_id (domain.format.entity), description, keywords, "
        "and optionally handler_guidance (processing instructions for this type).\n"
        "This helps the system learn to route future similar tasks more efficiently."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Domain being cataloged (e.g. 'unity3d', 'git', 'python', 'feishu').",
            },
            "types": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type_id": {
                            "type": "string",
                            "description": "Structured type ID: domain.format.entity (e.g. 'git.stdout.log_text').",
                        },
                        "description": {
                            "type": "string",
                            "description": "Clear description of what this type represents.",
                        },
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Keywords for fast matching.",
                        },
                        "handler_guidance": {
                            "type": "string",
                            "description": "Optional: how to process/handle this type.",
                        },
                    },
                    "required": ["type_id", "description"],
                },
                "description": "Array of semantic types to register.",
            },
            "intent": {"$ref": "#/$defs/Intent"},
        },
        "required": ["domain", "types"],
        "$defs": {"Intent": _INTENT_SCHEMA},
    },
}

ALL_TOOLS = [
    BASH_TOOL, STR_REPLACE_EDITOR_TOOL, FINISH_TOOL, THINK_TOOL,
    GLOB_TOOL, GREP_TOOL,
    REGISTER_SEMANTIC_TYPES_TOOL,
]

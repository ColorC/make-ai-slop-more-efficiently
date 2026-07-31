# [OMNI] origin=codex domain=services/agent ts=2026-07-29 type=infrastructure
# [OMNI] material_id="material:core.agent.pi_behavior_contract.py"
"""Omni-owned behavioral contract aligned to Pi 0.82.1.

Pi is a specification and conformance oracle here, not a runtime dependency.
The constants below were extracted from the locally pinned MIT-licensed
``@earendil-works/pi-coding-agent`` package.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


PI_BEHAVIOR_REFERENCE = "@earendil-works/pi-coding-agent@0.82.1"
PI_BEHAVIOR_SOURCE = "https://github.com/earendil-works/pi"
PI_DEFAULT_TOOL_NAMES = ("read", "bash", "edit", "write")
PI_CONTEXT_FILE_NAMES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")


PI_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "read": {
        "description": (
            "Read the contents of a file. Supports text files and images "
            "(jpg, png, gif, webp, bmp). Images are sent as attachments. "
            "For text files, output is truncated to 2000 lines or 50KB "
            "(whichever is hit first). Use offset/limit for large files. "
            "When you need the full file, continue with offset until complete."
        ),
        "prompt_snippet": "Read file contents",
        "prompt_guidelines": (
            "Use read to examine files instead of cat or sed.",
        ),
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative or absolute)",
                },
                "offset": {
                    "type": "number",
                    "description": "Line number to start reading from (1-indexed)",
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of lines to read",
                },
            },
        },
    },
    "bash": {
        "description": (
            "Execute a bash command in the current working directory. Returns "
            "stdout and stderr. Output is truncated to last 2000 lines or 50KB "
            "(whichever is hit first). If truncated, full output is saved to a "
            "temp file. Optionally provide a timeout in seconds."
        ),
        "prompt_snippet": "Execute bash commands (ls, grep, find, etc.)",
        "prompt_guidelines": (
            "Inspect PI_* environment variables for current model and session details.",
        ),
        "input_schema": {
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command to execute",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (optional, no default timeout)",
                },
            },
        },
    },
    "edit": {
        "description": (
            "Edit a single file using exact text replacement. Every "
            "edits[].oldText must match a unique, non-overlapping region of the "
            "original file. If two changes affect the same block or nearby "
            "lines, merge them into one edit instead of emitting overlapping "
            "edits. Do not include large unchanged regions just to connect "
            "distant changes."
        ),
        "prompt_snippet": (
            "Make precise file edits with exact text replacement, including "
            "multiple disjoint edits in one call"
        ),
        "prompt_guidelines": (
            "Use edit for precise changes (edits[].oldText must match exactly)",
            "When changing multiple separate locations in one file, use one "
            "edit call with multiple entries in edits[] instead of multiple edit calls",
            "Each edits[].oldText is matched against the original file, not "
            "after earlier edits are applied. Do not emit overlapping or nested "
            "edits. Merge nearby changes into one edit.",
            "Keep edits[].oldText as small as possible while still being unique "
            "in the file. Do not pad with large unchanged regions.",
        ),
        "input_schema": {
            "type": "object",
            "required": ["path", "edits"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit (relative or absolute)",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["oldText", "newText"],
                        "properties": {
                            "oldText": {
                                "type": "string",
                                "description": (
                                    "Exact text for one targeted replacement. It "
                                    "must be unique in the original file and must "
                                    "not overlap with any other edits[].oldText "
                                    "in the same call."
                                ),
                            },
                            "newText": {
                                "type": "string",
                                "description": "Replacement text for this targeted edit.",
                            },
                        },
                    },
                    "description": (
                        "One or more targeted replacements. Each edit is matched "
                        "against the original file, not incrementally. Do not "
                        "include overlapping or nested edits. If two changes "
                        "touch the same block or nearby lines, merge them into "
                        "one edit instead."
                    ),
                },
            },
        },
    },
    "write": {
        "description": (
            "Write content to a file. Creates the file if it doesn't exist, "
            "overwrites if it does. Automatically creates parent directories."
        ),
        "prompt_snippet": "Create or overwrite files",
        "prompt_guidelines": (
            "Use write only for new files or complete rewrites.",
        ),
        "input_schema": {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (relative or absolute)",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
        },
    },
}


def build_pi_aligned_system_prompt(
    cwd: str,
    *,
    selected_tools: tuple[str, ...] = PI_DEFAULT_TOOL_NAMES,
    context_files: tuple[tuple[str, str], ...] | None = None,
) -> str:
    """Reproduce Pi 0.82.1's default model-visible system prompt."""

    prompt_cwd = str(cwd).replace("\\", "/")
    package_dir = _pi_reference_package_dir()
    tool_lines = [
        f"- {name}: {PI_TOOL_CONTRACTS[name]['prompt_snippet']}"
        for name in selected_tools
    ]
    guidelines: list[str] = []
    if "bash" in selected_tools and not {"grep", "find", "ls"}.intersection(
        selected_tools
    ):
        guidelines.append("Use bash for file operations like ls, rg, find")
    for name in selected_tools:
        for guideline in PI_TOOL_CONTRACTS[name]["prompt_guidelines"]:
            if guideline not in guidelines:
                guidelines.append(guideline)
    guidelines.extend(
        [
            "Be concise in your responses",
            "Show file paths clearly when working with files",
        ]
    )
    prompt = (
        "You are an expert coding assistant operating inside pi, a coding agent "
        "harness. You help users by reading files, executing commands, editing "
        "code, and writing new files.\n\n"
        "Available tools:\n"
        + "\n".join(tool_lines)
        + "\n\nIn addition to the tools above, you may have access to other "
        "custom tools depending on the project.\n\nGuidelines:\n"
        + "\n".join(f"- {guideline}" for guideline in guidelines)
        + "\n\nPi documentation (read only when the user asks about pi itself, "
        "its SDK, extensions, themes, skills, or TUI):\n"
        f"- Main documentation: {package_dir / 'README.md'}\n"
        f"- Additional docs: {package_dir / 'docs'}\n"
        f"- Examples: {package_dir / 'examples'} (extensions, custom tools, SDK)\n"
        "- When reading pi docs or examples, resolve docs/... under Additional "
        "docs and examples/... under Examples, not the current working directory\n"
        "- When asked about: extensions (docs/extensions.md, examples/extensions/), "
        "themes (docs/themes.md), skills (docs/skills.md), prompt templates "
        "(docs/prompt-templates.md), TUI components (docs/tui.md), keybindings "
        "(docs/keybindings.md), SDK integrations (docs/sdk.md), custom providers "
        "(docs/custom-provider.md), adding models (docs/models.md), pi packages "
        "(docs/packages.md), environment variables (docs/environment-variables.md)\n"
        "- When working on pi topics, read the docs and examples, and follow .md "
        "cross-references before implementing\n"
        "- Always read pi .md files completely and follow links to related docs "
        "(e.g., tui.md for TUI API details)"
    )
    resolved_context = (
        load_pi_project_context_files(cwd)
        if context_files is None
        else context_files
    )
    if resolved_context:
        prompt += "\n\n<project_context>\n\n"
        prompt += "Project-specific instructions and guidelines:\n\n"
        for file_path, content in resolved_context:
            prompt += (
                f'<project_instructions path="{file_path}">\n'
                f"{content}\n"
                "</project_instructions>\n\n"
            )
        prompt += "</project_context>\n"
    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def load_pi_project_context_files(
    cwd: str,
    *,
    agent_dir: str | None = None,
) -> tuple[tuple[str, str], ...]:
    """Load Pi's global and ancestor context files in the same order."""

    resolved_cwd = Path(cwd).expanduser().resolve()
    resolved_agent_dir = Path(
        agent_dir
        or os.environ.get("PI_CODING_AGENT_DIR")
        or (Path.home() / ".pi" / "agent")
    ).expanduser().resolve()
    global_context = _load_context_file_from_dir(resolved_agent_dir)
    seen: set[str] = set()
    if global_context is not None:
        seen.add(os.path.normcase(global_context[0]))
    ancestors: list[tuple[str, str]] = []
    current = resolved_cwd
    while True:
        item = _load_context_file_from_dir(current)
        if item is not None and os.path.normcase(item[0]) not in seen:
            ancestors.insert(0, item)
            seen.add(os.path.normcase(item[0]))
        parent = current.parent
        if parent == current:
            break
        current = parent
    result = [global_context] if global_context is not None else []
    result.extend(ancestors)
    return tuple(result)


def _load_context_file_from_dir(directory: Path) -> tuple[str, str] | None:
    for name in PI_CONTEXT_FILE_NAMES:
        candidate = directory / name
        if candidate.is_file():
            resolved = candidate.resolve()
            return str(resolved), resolved.read_bytes().decode("utf-8")
    return None


def _pi_reference_package_dir() -> Path:
    configured = os.environ.get("PI_PACKAGE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidate = (
            Path(appdata)
            / "npm"
            / "node_modules"
            / "@earendil-works"
            / "pi-coding-agent"
        )
        if candidate.is_dir():
            return candidate.resolve()
    return (
        Path.home()
        / ".pi"
        / "reference"
        / "@earendil-works"
        / "pi-coding-agent"
        / "0.82.1"
    ).resolve()


def pi_tool_api_spec(name: str) -> dict[str, Any]:
    contract = PI_TOOL_CONTRACTS[name]
    return {
        "name": name,
        "description": contract["description"],
        "input_schema": contract["input_schema"],
    }


__all__ = [
    "PI_BEHAVIOR_REFERENCE",
    "PI_BEHAVIOR_SOURCE",
    "PI_CONTEXT_FILE_NAMES",
    "PI_DEFAULT_TOOL_NAMES",
    "PI_TOOL_CONTRACTS",
    "build_pi_aligned_system_prompt",
    "load_pi_project_context_files",
    "pi_tool_api_spec",
]

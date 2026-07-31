# [OMNI] origin=codex domain=services/agent ts=2026-07-16 type=infrastructure
# [OMNI] material_id="material:core.agent.direct_material_workspace_agent.py"
"""Direct, EventBus-native workspace Agent.

This is an Agent facility, not a content pipeline. One Agent receives one task
plus a workspace boundary, uses native tools, edits real files, and runs the
task's deterministic checks in the same conversation.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.agent.configurable import (
    AgentSpec,
    ConfigurableAgent,
)
from omnicompany.packages.services._core.agent.pi_behavior import (
    PI_DEFAULT_TOOL_NAMES,
    build_pi_aligned_system_prompt,
)
from omnicompany.packages.services._core.agent.routers.pi_tools import (
    PiBashRouter,
    PiEditRouter,
    PiReadRouter,
    PiWriteRouter,
)
from omnicompany.packages.services._core.agent.routers.pi_context import (
    PiContextTransformRouter,
    PiSessionCompactor,
)
from omnicompany.packages.services._core.agent.routers.prompt_builder import (
    PromptBuilderRouter,
)
from omnicompany.runtime.agent.agent_loop_config import (
    DISABLED_COMPACT,
    LoopConfig,
    RetryConfig,
)


class _PiAlignedPromptBuilder(PromptBuilderRouter):
    """Build the pinned Pi prompt while retaining Omni Router/EventBus I/O."""

    def render_system_prompt(self, input_data: dict) -> str:
        raw_root = input_data.get("workspace_root") or input_data.get("cwd")
        if not raw_root:
            raise ValueError("Pi-aligned Agent requires workspace_root")
        return build_pi_aligned_system_prompt(str(Path(str(raw_root)).resolve()))

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        task = input_data.get("task") or input_data.get("instruction")
        if task is None:
            return super().build_initial_messages(input_data)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": str(task)}
        ]
        for raw_path in input_data.get("image_paths") or []:
            path = Path(str(raw_path)).expanduser().resolve()
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    },
                }
            )
        return [{"role": "user", "content": content}]


class MaterialWorkspaceAgent(ConfigurableAgent):
    """General file-and-command Agent with fail-closed workspace boundaries."""

    TOOL_ROUTERS = [PiReadRouter, PiBashRouter, PiEditRouter, PiWriteRouter]
    LOOP_CONFIG = LoopConfig(
        max_turns=None,
        compact=DISABLED_COMPACT,
        retry=RetryConfig(
            max_retries=3,
            base_delay_ms=2_000,
            max_delay_ms=60_000,
            jitter_factor=0.0,
        ),
    )
    AUTO_FINISH_TOOL = False
    ENABLE_TOOL_FUSE = False
    ENABLE_IDENTICAL_FAILURE_FUSE = False
    PARALLEL_TOOL_EXECUTION = True
    MODEL_USER_CONTENT_BLOCKS = True
    LLM_MAX_CONTINUATION_RETRIES = 0
    LLM_PREFIX_TOOL_ERRORS = False

    SPEC = AgentSpec(
        id="services.agent.material_workspace",
        name="MaterialWorkspaceAgent",
        domain="services/agent",
        llm_model="qwen3.7-max",
        llm_max_tokens=32000,
        llm_max_turns=None,
        llm_timeout_seconds=None,
        # Qwen needs reasoning for multi-file work, but leaving its native
        # reasoning stream unbounded can spend minutes producing tens of
        # thousands of hidden characters before the next tool call. Keep a
        # native budget rather than imposing a wall-clock timeout.
        llm_extra_body={"enable_thinking": True, "thinking_budget": 8192},
        prompt_path="",
        tools=PI_DEFAULT_TOOL_NAMES,
        workspace={"mode": "runtime-declared"},
        allow_custom_code=True,
    )

    def build_prompt_builder(self, *, bus: Any | None = None) -> PromptBuilderRouter:
        return _PiAlignedPromptBuilder(bus=bus)

    def build_context_compact(self, *, bus: Any) -> PiContextTransformRouter:
        return PiContextTransformRouter(bus=bus)

    def build_session_compactor(
        self,
        *,
        model: str | None,
        role: str | None,
        retry: RetryConfig,
        extra_body: dict[str, Any] | None,
        bus: Any,
    ) -> PiSessionCompactor:
        return PiSessionCompactor(
            model=model,
            role=role,
            retry=retry,
            extra_body=extra_body,
            bus=bus,
        )

    def build_tool_context(self, *, input_data: dict, turn: int, trace_id: str) -> dict:
        context = super().build_tool_context(
            input_data=input_data,
            turn=turn,
            trace_id=trace_id,
        )
        raw_root = input_data.get("workspace_root") or input_data.get("cwd")
        if not raw_root:
            raise ValueError("MaterialWorkspaceAgent requires workspace_root")
        root = Path(str(raw_root)).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"workspace_root must be an existing directory: {root}")

        write_paths = _resolve_scoped_paths(root, input_data.get("allowed_write_paths") or [])
        write_roots = _resolve_scoped_paths(root, input_data.get("allowed_write_roots") or [])
        read_roots = _resolve_scoped_paths(
            root,
            input_data.get("allowed_read_roots") or [str(root)],
        )
        readonly = bool(input_data.get("readonly", False))
        if not readonly and not write_paths and not write_roots:
            # Direct work defaults to the explicitly declared workspace, never
            # to the repository or current process directory by accident.
            write_roots = (str(root),)

        command_prefixes = tuple(
            str(item) for item in (input_data.get("allowed_bash_command_prefixes") or []) if str(item)
        )
        bash_roots = _resolve_scoped_paths(
            root,
            input_data.get("allowed_bash_roots")
            or ([] if readonly else [str(root)]),
        )
        context.update(
            {
                "cwd": str(root),
                "project_root": str(root),
                "allowed_read_roots": read_roots,
                "allowed_write_paths": write_paths,
                "allowed_write_roots": write_roots,
                "allowed_bash_roots": bash_roots,
                "allowed_bash_command_prefixes": command_prefixes,
                "origin": str(input_data.get("origin") or "omnicompany"),
                "domain": str(input_data.get("domain") or "services/agent"),
                "agent_name": str(input_data.get("agent_name") or type(self).__name__),
                "current_task_id": str(input_data.get("task_id") or trace_id),
                "pi_session_id": str(input_data.get("session_id") or trace_id),
                "pi_provider": str(input_data.get("model_provider") or ""),
                "pi_model": str(input_data.get("model") or ""),
                "pi_reasoning_level": str(
                    input_data.get("reasoning_level") or ""
                ),
                # This Agent already emits every write to the EventBus. Avoid
                # duplicating that audit trail inside the user's workspace.
                "record_file_ownership": False,
            }
        )
        return context


def _resolve_scoped_paths(root: Path, values: list[Any] | tuple[Any, ...]) -> tuple[str, ...]:
    resolved: list[str] = []
    for item in values:
        raw = Path(str(item)).expanduser()
        path = (raw if raw.is_absolute() else root / raw).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"workspace path must stay under {root}: {path}") from exc
        resolved.append(str(path))
    return tuple(resolved)


__all__ = ["MaterialWorkspaceAgent"]

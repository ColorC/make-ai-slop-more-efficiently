"""Omni-owned launch profiles for replaceable AgentRuntime implementations."""

from __future__ import annotations

from collections.abc import Sequence
import json
import os
from pathlib import Path
import shutil
import tempfile

from .process_invoker import AgentRuntimeProcessSpec, OneShotAgentRuntimeProcessInvoker


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def build_the_company_dsh_readonly_invoker(
    workspace_cwd: str | os.PathLike[str],
    *,
    allowed_tools: Sequence[str] = (),
) -> OneShotAgentRuntimeProcessInvoker:
    """Build the production JSON-RPC DSH profile without loading secrets itself."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("external AgentRuntime selected but node is not on PATH")
    if not os.environ.get("THE_COMPANY_API_KEY", "").strip():
        raise RuntimeError("external AgentRuntime selected but THE_COMPANY_API_KEY is not configured")

    repo_root = _repo_root()
    service_root = repo_root / "services" / "agent-runtime-dsh"
    host = service_root / "dist" / "stdio-host.js"
    runtime_bin = (
        service_root
        / "node_modules"
        / "@deepseek-ai"
        / "dsh-sdk-jsonrpc-demo"
        / "lib"
        / "bin.js"
    )
    profile = service_root / "profiles" / "readonly-the_company-workspace.cordis.yml"
    for required in (host, runtime_bin, profile):
        if not required.is_file():
            raise RuntimeError(f"external AgentRuntime artifact is missing: {required}")

    workspace = Path(workspace_cwd).resolve()
    session_root = (
        Path(tempfile.gettempdir()) / "omnicompany-agent-runtime-dsh" / "sessions"
    ).resolve()
    environment = {
        **os.environ,
        "OMNI_DSH_RUNTIME_ID": "dsh-the_company-readonly",
        "OMNI_DSH_RUNTIME_COMMAND": node,
        "OMNI_DSH_RUNTIME_ARGS_JSON": json.dumps([str(runtime_bin), str(profile)]),
        "OMNI_DSH_ALLOWED_TOOLS_JSON": json.dumps(list(allowed_tools)),
        "OMNI_DSH_REQUEST_TIMEOUT_MS": "120000",
        "DSH_CORDIS_CONFIG": str(profile),
        "DSH_CWD": str(workspace),
        "DSH_SESSION_ROOT": str(session_root),
    }
    return OneShotAgentRuntimeProcessInvoker(
        AgentRuntimeProcessSpec(
            command=(node, str(host)),
            cwd=str(workspace),
            env=environment,
        )
    )


__all__ = ["build_the_company_dsh_readonly_invoker"]

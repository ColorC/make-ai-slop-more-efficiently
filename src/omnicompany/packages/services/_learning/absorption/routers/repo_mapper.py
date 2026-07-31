# [OMNI] origin=claude-code domain=services/absorption/routers ts=2026-04-20T00:00:00Z type=shim
# [OMNI] material_id="material:learning.absorption.router_shim.repo_mapper.py"
"""compat shim: redirect to workers/v3/repo_mapper.py."""
from __future__ import annotations

from ..workers.v3.repo_mapper import RepoMapperWorker


RepoMapperRouter = RepoMapperWorker


__all__ = ["RepoMapperRouter", "RepoMapperWorker"]

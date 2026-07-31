# [OMNI] origin=human domain=services/_authoring type=module agent=ai-ide-2b20d28d ts=2026-07-26T09:07:26Z
# [OMNI] summary="Runtime policy for production-grade Chinese copy."
# [OMNI] why="_authoring 服务组件"
# [OMNI] tags=_authoring,module
"""Runtime policy for production-grade Chinese copy.

The public policy document is ``config/publication_common/production-copy-authorship.md``.
This module supplies a small deterministic gate for legacy entry points that still
invoke a writer outside the current policy or pass an editorially interpreted brief.
"""

from __future__ import annotations

from typing import Any


PRODUCTION_COPY_MODEL = "qwen3.7-max"
PRODUCTION_COPY_MODELS = ("qwen3.7-max", "kimi-k2.6", "k3")
POLICY_PATH = "config/publication_common/production-copy-authorship.md"
LEGACY_PIPELINE_DISABLED_CODE = "legacy_production_copy_pipeline_retired"


def legacy_pipeline_rejection(*, pipeline: str) -> dict[str, Any]:
    """Return the standard fail-closed result for a retired writing entry point."""

    return {
        "ok": False,
        "code": LEGACY_PIPELINE_DISABLED_CODE,
        "pipeline": pipeline,
        "required_writer_model": PRODUCTION_COPY_MODEL,
        "allowed_writer_models": list(PRODUCTION_COPY_MODELS),
        "policy_path": POLICY_PATH,
        "diagnosis": (
            "This legacy production-copy entry point is retired. Use one continuous "
            "registered production-copy author session with purpose, standards, "
            "and neutral source manifests."
        ),
        "events_count": 0,
        "events_summary": [],
    }


__all__ = [
    "LEGACY_PIPELINE_DISABLED_CODE",
    "POLICY_PATH",
    "PRODUCTION_COPY_MODEL",
    "PRODUCTION_COPY_MODELS",
    "legacy_pipeline_rejection",
]

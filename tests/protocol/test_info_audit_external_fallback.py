from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from omnicompany.runtime.info_audit.fallback import (
    FallbackConfig,
    UniversalFallbackLoop,
)


def _report(evidence: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "final": {
            "status": "found",
            "summary": "The missing contract was found in a canonical source.",
            "report_markdown": (
                "# Fallback Report\n\n## Trigger\nMissing contract.\n\n"
                "## Investigation\nInspected canonical source.\n\n"
                "## Findings\nThe contract exists.\n\n## Next Steps\nUse the source.\n"
            ),
            "evidence_paths": [str(evidence)],
        },
        "turn_count": 3,
        "error": "",
    }


@pytest.mark.asyncio
async def test_external_fallback_keeps_write_authority_in_omni(tmp_path: Path) -> None:
    evidence = Path(__file__).resolve()
    calls: list[dict[str, Any]] = []

    async def runner(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _report(evidence)

    output_dir = tmp_path / "scratch"
    result = await UniversalFallbackLoop(report_runner=runner).handle(
        FallbackConfig(
            trigger={
                "trace_id": "trace-1",
                "node_id": "source-review",
                "sufficiency": "partial",
                "missing_critical": ["canonical contract"],
            },
            scratch_dir=output_dir,
        )
    )

    assert result.status == "found"
    assert result.turns_used == 3
    output = output_dir / "source-review.md"
    assert result.output_path == str(output)
    assert output.read_text(encoding="utf-8").startswith("# Fallback Report")
    assert len(calls) == 1
    assert calls[0]["caller"] == "info-audit-fallback"
    assert Path(calls[0]["workspace_cwd"]).resolve() == evidence.parents[2]
    assert str(output_dir) not in calls[0]["task"]
    assert calls[0]["result_schema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_external_fallback_rejects_untrusted_evidence_before_write(
    tmp_path: Path,
) -> None:
    async def runner(**_kwargs: Any) -> dict[str, Any]:
        response = _report(Path(__file__).resolve())
        response["final"]["evidence_paths"] = ["../outside-workspace.txt"]
        return response

    output_dir = tmp_path / "scratch"
    result = await UniversalFallbackLoop(report_runner=runner).handle(
        FallbackConfig(
            trigger={"trace_id": "trace-2", "node_id": "review"},
            scratch_dir=output_dir,
        )
    )

    assert result.status == "error"
    assert "escapes the granted workspace" in result.summary
    assert not (output_dir / "review.md").exists()


@pytest.mark.asyncio
async def test_external_fallback_surfaces_runtime_failure_without_legacy_loop(
    tmp_path: Path,
) -> None:
    async def runner(**_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "final": None, "error": "provider unavailable"}

    result = await UniversalFallbackLoop(report_runner=runner).handle(
        FallbackConfig(
            trigger={"trace_id": "trace-3", "node_id": "review"},
            scratch_dir=tmp_path,
        )
    )

    assert result.status == "error"
    assert result.output_path == ""
    assert "provider unavailable" in result.summary


@pytest.mark.asyncio
async def test_external_fallback_rejects_undeclared_custom_tools(
    tmp_path: Path,
) -> None:
    called = False

    async def runner(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return _report(Path(__file__).resolve())

    result = await UniversalFallbackLoop(report_runner=runner).handle(
        FallbackConfig(
            trigger={"trace_id": "trace-4", "node_id": "review"},
            scratch_dir=tmp_path,
            custom_tools=[object()],
        )
    )

    assert result.status == "error"
    assert "custom_tools are unsupported" in result.summary
    assert called is False


@pytest.mark.asyncio
async def test_external_fallback_has_a_real_wall_clock_timeout(tmp_path: Path) -> None:
    async def runner(**_kwargs: Any) -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    result = await UniversalFallbackLoop(report_runner=runner).handle(
        FallbackConfig(
            trigger={"trace_id": "trace-5", "node_id": "review"},
            scratch_dir=tmp_path,
            wall_clock_s=0.01,
        )
    )

    assert result.status == "timeout"
    assert result.output_path == ""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .models import ArtifactRef, BenchmarkTask, RunResult, TargetInfo, TraceEvent


class BenchmarkBundleWriter:
    """Materialize one run into the stable v0.2 benchmark interchange layout."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def write(
        self,
        task: BenchmarkTask,
        target: TargetInfo,
        result: RunResult,
        *,
        trace: list[TraceEvent] | None = None,
        artifacts: list[ArtifactRef] | None = None,
        report_fragments: list[dict[str, Any]] | None = None,
    ) -> Path:
        bundle = self.root / result.id
        artifact_dir = bundle / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(bundle / "task.json", task.model_dump(mode="json"))
        self._write_json(bundle / "target.json", target.model_dump(mode="json"))
        self._write_json(bundle / "objective-result.json", result.model_dump(mode="json"))
        self._write_json(bundle / "report-fragments.json", report_fragments or [])

        events = trace or [
            TraceEvent(
                seq=1,
                run_id=result.id,
                event_type="objective_result",
                observation_artifact_ids=result.artifact_ids,
                result={
                    "status": result.status,
                    "checks": [item.model_dump(mode="json") for item in result.checks],
                    "error": result.error,
                },
            )
        ]
        (bundle / "trace.jsonl").write_text(
            "".join(item.model_dump_json() + "\
" for item in events), encoding="utf-8"
        )

        manifest: list[dict[str, Any]] = []
        for artifact in artifacts or []:
            source = Path(artifact.path)
            copied_path: str | None = None
            if source.is_file():
                destination = artifact_dir / f"{artifact.id}{source.suffix.lower()}"
                if not destination.exists():
                    shutil.copy2(source, destination)
                copied_path = str(destination.relative_to(bundle)).replace("\\", "/")
            manifest.append(
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "sha256": artifact.sha256,
                    "media_type": artifact.media_type,
                    "path": copied_path,
                    "source_available": source.is_file(),
                }
            )
        self._write_json(artifact_dir / "manifest.json", manifest)
        return bundle
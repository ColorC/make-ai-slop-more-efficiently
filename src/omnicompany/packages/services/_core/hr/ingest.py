# [OMNI] material_id="material:core.hr.ingest.py" origin=codex ts=2026-08-18 type=module status=active agent=codex
# [OMNI] summary="五家 CLI 会话的正式增量采集入口，追加 HR 事件并原子推进多源水位线"
# [OMNI] tags=hr,ingest,attribution,watermark
"""Incremental production ingest for every supported CLI transcript source."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .claude_source import collect_claude_sessions, collect_qoder_sessions
from .codebuddy_source import collect_codebuddy_sessions
from .codex_source import collect_codex_sessions
from .grok_source import collect_grok_sessions
from .kiro_source import collect_kiro_sessions
from .schema import CollectResult
from .store import append_events, load_watermarks, save_watermarks

Collector = Callable[..., CollectResult]
Source = tuple[str, Collector, Path]


@dataclass
class IngestReport:
    events_collected: int = 0
    rows_appended: int = 0
    files_seen: int = 0
    files_failed: int = 0
    lines_scanned: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_sources(home: Path | None = None) -> list[Source]:
    home = Path(home) if home is not None else Path.home()
    sources: list[Source] = [
        ("qoder", collect_qoder_sessions, home / ".qoder"),
        ("codebuddy", collect_codebuddy_sessions, home / ".codebuddy"),
        ("kiro", collect_kiro_sessions, home / ".kiro"),
        ("grok", collect_grok_sessions, home / ".grok" / "sessions"),
    ]
    # 两个官方 Claude 账号同构(claude_2 = 另一个 CLAUDE_CONFIG_DIR), 与 codex/codex-2 同办法。
    for directory in (".claude", ".claude-2"):
        root = home / directory
        if root.is_dir():
            sources.insert(0, ("claude", collect_claude_sessions, root))
    for directory in (".codex", ".codex-2"):
        root = home / directory
        if root.is_dir():
            sources.append(("codex", collect_codex_sessions, root))
    return sources


def ingest_all(
    *,
    data_dir: Path,
    sources: list[Source] | None = None,
    provider: str | None = None,
) -> IngestReport:
    """Collect, append, then atomically advance watermarks; reruns are idempotent."""
    data_dir = Path(data_dir)
    old_watermarks = load_watermarks(data_dir)
    merged_watermarks = dict(old_watermarks)
    report = IngestReport()

    for source_provider, collector, root in (sources or default_sources()):
        if provider is not None and source_provider != provider:
            continue
        result = collector(Path(root), watermark_state=old_watermarks)
        appended = append_events(result.events, data_dir=data_dir)
        # Collectors return a full copy of the input state plus their own updates.
        # Only merge keys this source actually changed, otherwise a later source
        # can overwrite an earlier source's freshly advanced watermark with the
        # shared old snapshot.
        for key, value in result.watermark.items():
            if key not in old_watermarks or old_watermarks[key] != value:
                merged_watermarks[key] = value
        report.events_collected += len(result.events)
        report.rows_appended += appended
        report.files_seen += result.files_seen
        report.files_failed += result.files_failed
        report.lines_scanned += result.lines_scanned
        report.sources.append({
            "provider": source_provider,
            "root": str(root),
            "events_collected": len(result.events),
            "rows_appended": appended,
            "files_seen": result.files_seen,
            "files_parsed": result.files_parsed,
            "files_empty": result.files_empty,
            "files_failed": result.files_failed,
            "lines_scanned": result.lines_scanned,
            "coverage": result.coverage,
            "failure_samples": result.failures[:5],
        })

    save_watermarks(merged_watermarks, data_dir=data_dir)
    return report


__all__ = ["IngestReport", "Source", "default_sources", "ingest_all"]

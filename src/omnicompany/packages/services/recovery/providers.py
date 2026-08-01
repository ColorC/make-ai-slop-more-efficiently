# [OMNI] origin=claude-code created_by=omni-recover intent=configure-read-only-session-providers
"""Provider registry and non-executable local extension manifests."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Iterable, Protocol

from .evidence import EvidenceRecord


ENTRY_POINT_GROUP = "omni_recover.providers"
CONFIG_ENV = "OMNI_RECOVERY_PROVIDER_CONFIG"


class ProviderAdapter(Protocol):
    """A parser plugin. Implementations must be read-only and deterministic."""

    name: str

    def iter_evidence(self, source: Path) -> Iterable[EvidenceRecord]: ...


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    roots: tuple[str, ...]
    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    origin: str = "builtin"

    @classmethod
    def from_json(cls, payload: dict[str, object], origin: str) -> "ProviderDefinition":
        name = str(payload.get("name") or "").strip().lower()
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"invalid provider name in {origin}: {name!r}")

        def strings(key: str) -> tuple[str, ...]:
            value = payload.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{origin}: {key} must be a string array")
            return tuple(value)

        return cls(
            name=name,
            roots=strings("roots"),
            includes=strings("includes"),
            excludes=strings("excludes"),
            origin=origin,
        )


BUILTIN_DEFINITIONS = (
    ProviderDefinition(
        name="codex",
        roots=("%USERPROFILE%/.codex",),
        includes=(
            "sessions/**", "archived_sessions/**", "history.jsonl",
            "session_index.jsonl", "transcription-history.jsonl", "memories/**",
            "rollout_summaries/**", "attachments/**", "generated_images/**",
            "computer-use/**", "visualizations/**", "logs_2.sqlite*",
            "state_5.sqlite*", "goals_1.sqlite*", "memories_1.sqlite*",
            ".codex-global-state.json*",
        ),
        excludes=("auth.json", "secrets/**", "cache/**", ".sandbox-secrets/**"),
    ),
    ProviderDefinition(
        name="claude",
        roots=("%USERPROFILE%/.claude",),
        includes=(
            "projects/**", "file-history/**", "history.jsonl", "sessions/**",
            "backups/**", "jobs/**", "plans/**", "shell-snapshots/**", "tasks/**",
            "session-env/**", "ide/**",
        ),
        excludes=("credentials/**", "cache/**", "daemon.log"),
    ),
    ProviderDefinition(
        name="kimi",
        roots=(
            "%USERPROFILE%/.kimi-code",
            "%APPDATA%/kimi-desktop/daimon-share/daimon/runtime/kimi-code/home",
        ),
        includes=(
            "sessions/**", "session_index.jsonl", "workspaces.json", "user-history/**",
            "logs/**",
        ),
        excludes=(
            "credentials/**", "bin/**", "telemetry/**", "updates/**",
            "config.toml*", "device_id", "plugins/**",
        ),
    ),
    ProviderDefinition(
        name="opencode",
        roots=("%USERPROFILE%/.local/share/opencode",),
        includes=("opencode.db", "tool-output/**", "snapshot/**", "repos/**", "log/**", "exports/**"),
        # SQLite is archived through its online-backup API; live WAL/SHM files
        # are never copied as independent, potentially inconsistent evidence.
        excludes=("*.db-wal", "*.db-shm", "cache/**"),
    ),
)


class ProviderRegistry:
    """Definitions plus parser plugins, with explicit duplicate protection."""

    def __init__(self) -> None:
        self._definitions: dict[str, ProviderDefinition] = {}
        self._adapters: dict[str, ProviderAdapter] = {}

    @property
    def definitions(self) -> dict[str, ProviderDefinition]:
        return dict(self._definitions)

    @property
    def adapters(self) -> dict[str, ProviderAdapter]:
        return dict(self._adapters)

    def register_definition(self, definition: ProviderDefinition, *, replace: bool = False) -> None:
        if definition.name in self._definitions and not replace:
            raise ValueError(f"provider definition already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def register_adapter(self, adapter: ProviderAdapter, *, replace: bool = False) -> None:
        name = str(adapter.name).strip().lower()
        if not name:
            raise ValueError("provider adapter name is required")
        if name in self._adapters and not replace:
            raise ValueError(f"provider adapter already registered: {name}")
        self._adapters[name] = adapter

    def load_manifest_dir(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if "$schema" in payload and "name" not in payload:
                continue
            definition = ProviderDefinition.from_json(payload, str(path))
            self.register_definition(definition, replace=bool(payload.get("replace", False)))

    def load_entry_points(self) -> None:
        for entry_point in importlib.metadata.entry_points().select(group=ENTRY_POINT_GROUP):
            factory = entry_point.load()
            adapter = factory() if callable(factory) else factory
            self.register_adapter(adapter)


def _config_dirs(extra: Iterable[Path] = ()) -> list[Path]:
    rows = [Path.cwd() / "config" / "recovery" / "providers.d"]
    raw = os.environ.get(CONFIG_ENV, "")
    if raw:
        rows.extend(Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip())
    rows.append(Path.home() / ".config" / "omni-recover" / "providers.d")
    rows.extend(extra)
    seen: set[str] = set()
    result: list[Path] = []
    for path in rows:
        key = str(path.resolve(strict=False)).casefold()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def default_registry(
    *,
    config_dirs: Iterable[Path] = (),
    load_entry_points: bool = True,
) -> ProviderRegistry:
    registry = ProviderRegistry()
    for definition in BUILTIN_DEFINITIONS:
        registry.register_definition(definition)
    for directory in _config_dirs(config_dirs):
        registry.load_manifest_dir(directory)
    from .adapters import builtin_adapters

    for adapter in builtin_adapters():
        registry.register_adapter(adapter)
    if load_entry_points:
        registry.load_entry_points()
    return registry


def source_specs(registry: ProviderRegistry | None = None) -> dict[str, dict[str, list[str]]]:
    selected = registry or default_registry()
    return {
        name: {
            "roots": list(definition.roots),
            "includes": list(definition.includes),
            "excludes": list(definition.excludes),
        }
        for name, definition in selected.definitions.items()
    }

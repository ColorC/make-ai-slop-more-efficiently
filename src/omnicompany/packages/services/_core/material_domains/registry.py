"""Pointer-only formal-domain registry."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..material_core import MaterialGraphValidationError, assert_material_graph, material_map


class DomainRegistryError(ValueError):
    pass


class DomainNotFoundError(DomainRegistryError):
    pass


class DomainRegistry:
    """Loads ``material_domains.yaml`` without copying domain content into it."""

    def __init__(self, registry_path: Path, *, workspace_root: Path) -> None:
        self.registry_path = Path(registry_path)
        self.workspace_root = Path(workspace_root).resolve()

    def _read(self) -> list[dict[str, str]]:
        try:
            payload = yaml.safe_load(self.registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DomainRegistryError(f"registry not found: {self.registry_path}") from exc
        except yaml.YAMLError as exc:
            raise DomainRegistryError(f"invalid registry YAML: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("domains"), list):
            raise DomainRegistryError("registry must contain schema_version: 1 and domains array")
        seen: set[str] = set()
        entries: list[dict[str, str]] = []
        for item in payload["domains"]:
            if not isinstance(item, dict) or set(item) != {"material_id", "graph_ref"}:
                raise DomainRegistryError("each domain must contain only material_id and graph_ref")
            material_id, graph_ref = item.get("material_id"), item.get("graph_ref")
            if not isinstance(material_id, str) or not material_id or not isinstance(graph_ref, str) or not graph_ref:
                raise DomainRegistryError("domain material_id and graph_ref must be non-empty strings")
            if material_id in seen:
                raise DomainRegistryError(f"duplicate material_id: {material_id}")
            seen.add(material_id)
            entries.append({"material_id": material_id, "graph_ref": graph_ref})
        return entries

    def _path_for_ref(self, graph_ref: str) -> Path:
        prefix, sep, relative = graph_ref.partition(":")
        if not sep or prefix not in {"workspace", "omnicompany"} or not relative:
            raise DomainRegistryError(f"unsupported graph_ref: {graph_ref}")
        base = self.workspace_root if prefix == "workspace" else self.workspace_root / "omnicompany"
        candidate = (base / relative.lstrip("/\\")).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError as exc:
            raise DomainRegistryError(f"graph_ref escapes {prefix} root: {graph_ref}") from exc
        return candidate

    def _resolve_entry(self, entry: dict[str, str]) -> dict[str, Any]:
        path = self._path_for_ref(entry["graph_ref"])
        try:
            import json
            graph = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DomainRegistryError(f"domain graph not found: {entry['graph_ref']}") from exc
        except ValueError as exc:
            raise DomainRegistryError(f"invalid domain graph JSON: {entry['graph_ref']}") from exc
        try:
            assert_material_graph(graph)
        except MaterialGraphValidationError as exc:
            raise DomainRegistryError(f"invalid domain graph: {entry['graph_ref']}: {exc}") from exc
        material = material_map(graph).get(entry["material_id"])
        if material is None or not isinstance(material.get("formal_domain"), dict):
            raise DomainRegistryError(f"registered material is not a formal domain: {entry['material_id']}")
        domain = material["formal_domain"]
        return {
            **entry,
            "graph_path": str(path),
            "domain_ref": {"material_id": material["id"], "revision_ref": material["revision_ref"]},
            "formal_domain": domain,
        }

    def list(self) -> list[dict[str, Any]]:
        return [self._resolve_entry(entry) for entry in self._read()]

    def show(self, material_id: str) -> dict[str, Any]:
        for entry in self._read():
            if entry["material_id"] == material_id:
                return self._resolve_entry(entry)
        raise DomainNotFoundError(f"unknown formal domain: {material_id}")

    context = show
    resolve = show

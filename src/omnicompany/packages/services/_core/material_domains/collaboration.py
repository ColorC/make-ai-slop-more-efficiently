"""Optional collaboration-context governance for formal domains."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .governance import GovernanceFinding


class CollaborationContextRule:
    """Require collaboration references only when a domain opts in via extensions."""

    rule_id = "material.collaboration-context"

    @staticmethod
    def _required(domain: Mapping[str, Any]) -> bool:
        formal_domain = domain.get("formal_domain", domain)
        extensions = formal_domain.get("extensions", {}) if isinstance(formal_domain, Mapping) else {}
        if not isinstance(extensions, Mapping):
            return False
        collaboration = extensions.get("collaboration")
        return extensions.get("require_collaboration_context") is True or (
            isinstance(collaboration, Mapping) and collaboration.get("required") is True
        )

    @staticmethod
    def _reference(value: object) -> bool:
        return isinstance(value, Mapping) and isinstance(value.get("kind"), str) and bool(value.get("kind")) and isinstance(value.get("ref"), str) and bool(value.get("ref"))

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        formal = record.get("formalization", record)
        sources = formal.get("sources", []) if isinstance(formal, Mapping) else []
        target = {"material_id": record.get("id", "formalization"), "revision_ref": record.get("revision_ref", {})}
        if not self._required(domain):
            return GovernanceFinding(self.rule_id, "passed", target, "collaboration context is not required by this domain", list(sources) if isinstance(sources, list) else [], False)
        extensions = formal.get("extensions", {}) if isinstance(formal, Mapping) else {}
        missing = [name for name in ("workspace_ref", "actor_ref", "access_policy_ref") if not self._reference(extensions.get(name) if isinstance(extensions, Mapping) else None)]
        if missing:
            return GovernanceFinding(self.rule_id, "failed", target, "required collaboration context is missing: " + ", ".join(missing), list(sources) if isinstance(sources, list) else [], True)
        return GovernanceFinding(self.rule_id, "passed", target, "required collaboration context is complete", list(sources) if isinstance(sources, list) else [], False)


__all__ = ["CollaborationContextRule"]

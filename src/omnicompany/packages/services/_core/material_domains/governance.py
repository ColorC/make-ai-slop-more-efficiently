"""Product-neutral governance rules and physical artifact inspection."""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ArtifactInspection:
    exists: bool
    revision: str | None = None
    structure_ok: bool = False
    indexable: bool = False


class ArtifactInspector(Protocol):
    def inspect(self, artifact: dict[str, Any]) -> ArtifactInspection: ...


class FileArtifactInspector:
    """Filesystem inspector; its native version is mtime-ns, never a content hash."""
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def inspect(self, artifact: dict[str, Any]) -> ArtifactInspection:
        ref = artifact.get("ref")
        if not isinstance(ref, str):
            return ArtifactInspection(False)
        path = (self.root / ref.lstrip("/\\")).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return ArtifactInspection(False)
        if not path.exists():
            return ArtifactInspection(False)
        return ArtifactInspection(True, f"mtime-ns:{path.stat().st_mtime_ns}", path.is_file() or path.is_dir(), path.is_file())


class GitArtifactInspector:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def inspect(self, artifact: dict[str, Any]) -> ArtifactInspection:
        ref = artifact.get("ref")
        if not isinstance(ref, str):
            return ArtifactInspection(False)
        try:
            revision = subprocess.run(["git", "log", "-1", "--format=%H", "--", ref], cwd=self.root, capture_output=True, text=True, timeout=5)
            exists = subprocess.run(["git", "cat-file", "-e", f"HEAD:{ref}"], cwd=self.root, capture_output=True, timeout=5)
        except OSError:
            return ArtifactInspection(False)
        native = revision.stdout.strip() if revision.returncode == 0 else None
        return ArtifactInspection(exists.returncode == 0, native, exists.returncode == 0, exists.returncode == 0)


class CompositeArtifactInspector:
    """Dispatches inspection by artifact kind first, then revision provider.

    An unregistered artifact is deliberately uninspectable; it must not inherit a
    random inspector merely because another artifact in the record is inspectable.
    """
    def __init__(self, *, by_kind: dict[str, ArtifactInspector] | None = None, by_provider: dict[str, ArtifactInspector] | None = None) -> None:
        self.by_kind = dict(by_kind or {})
        self.by_provider = dict(by_provider or {})

    @classmethod
    def with_defaults(cls, root: Path) -> "CompositeArtifactInspector":
        file_inspector = FileArtifactInspector(root)
        git_inspector = GitArtifactInspector(root)
        return cls(by_kind={"file": file_inspector, "filesystem": file_inspector, "git": git_inspector}, by_provider={"git": git_inspector})

    def inspect(self, artifact: dict[str, Any]) -> ArtifactInspection:
        kind = artifact.get("kind")
        provider = artifact.get("revision_ref", {}).get("provider") if isinstance(artifact.get("revision_ref"), dict) else None
        inspector = self.by_kind.get(kind) if isinstance(kind, str) else None
        inspector = inspector or (self.by_provider.get(provider) if isinstance(provider, str) else None)
        return inspector.inspect(artifact) if inspector is not None else ArtifactInspection(False)


@dataclass(frozen=True)
class GovernanceFinding:
    rule_id: str
    status: str
    target_ref: dict[str, Any]
    message: str
    evidence: list[dict[str, Any]]
    blocking: bool


@dataclass(frozen=True)
class GovernanceReport:
    domain_ref: dict[str, Any]
    record_ref: dict[str, Any]
    rule_set_ref: dict[str, Any]
    findings: list[GovernanceFinding]
    accepted: bool
    contract_version: str = "1.0"

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "findings": [asdict(item) for item in self.findings]}


class DomainRule(Protocol):
    """A product-owned rule that returns the shared governance finding model."""
    rule_id: str

    def evaluate(
        self, domain: dict[str, Any], record: dict[str, Any]
    ) -> GovernanceFinding | list[GovernanceFinding]: ...


class GovernanceEngine:
    RULE_IDS = (
        "material.addressable", "material.exact-version", "material.provenance", "material.responsibility",
        "material.unique-authority", "material.type", "material.causality", "material.physical-structure",
        "material.indexable", "material.explicit-failure",
    )

    def __init__(self, *, inspector: ArtifactInspector | None = None, finding_archive: Any | None = None, domain_rules: list[DomainRule] | None = None) -> None:
        self.inspector, self.finding_archive = inspector, finding_archive
        self.domain_rules = list(domain_rules or [])

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any], *, authority: Any | None = None) -> GovernanceReport:
        fd = domain["formal_domain"] if "formal_domain" in domain else domain
        formal = record["formalization"] if "formalization" in record else record
        target = {"material_id": record.get("id", "formalization"), "revision_ref": record.get("revision_ref", {})}
        findings: list[GovernanceFinding] = []
        def add(rule: str, passed: bool, message: str, *, stale: bool = False) -> None:
            findings.append(GovernanceFinding(rule, "stale" if stale else ("passed" if passed else "failed"), target, message, list(formal.get("sources", [])), not passed))
        add(self.RULE_IDS[0], all(bool(x.get("material_id")) for x in (formal.get("domain_ref", {}), formal.get("input_material", {}))) and bool(record.get("id")), "stable material identities required")
        artifacts = formal.get("artifacts", [])
        inspections = [self.inspector.inspect(item) for item in artifacts] if self.inspector else [ArtifactInspection(False) for _ in artifacts]
        revisions = [formal.get("domain_ref", {}).get("revision_ref"), formal.get("input_material", {}).get("revision_ref")] + [x.get("revision_ref") for x in artifacts]
        digest_providers = {"digest", "hash", "sha", "sha1", "sha-1", "sha256", "sha-256"}
        extension_sources = (domain.get("extensions", {}), fd.get("extensions", {}))
        digest_fallback = any(
            isinstance(extensions, dict)
            and any(extensions.get(name) is True for name in ("allow_digest_fallback", "allow_revision_digest_fallback", "allow_unversioned_revision_fallback"))
            for extensions in extension_sources
        )
        exact = all(isinstance(x, dict) and all(x.get(k) for k in ("provider", "object", "revision")) for x in revisions)
        if any(isinstance(item, dict) and str(item.get("provider", "")).lower() in digest_providers for item in revisions):
            exact = exact and digest_fallback
        stale = any(inspection.exists and inspection.revision is not None and artifact["revision_ref"]["revision"] != inspection.revision for artifact, inspection in zip(artifacts, inspections, strict=True))
        add(self.RULE_IDS[1], exact and not stale, "artifact revision is stale" if stale else "native exact revisions required", stale=stale)
        add(self.RULE_IDS[2], bool(formal.get("sources")) and all(x.get("evidence") for x in formal.get("rule_results", [])), "sources and rule evidence required")
        def actor_ref(value: object) -> bool:
            return (
                isinstance(value, dict)
                and isinstance(value.get("kind"), str)
                and bool(value["kind"].strip())
                and isinstance(value.get("ref"), str)
                and bool(value["ref"].strip())
            )
        add(self.RULE_IDS[3], all(actor_ref(formal.get("responsibility", {}).get(role)) for role in ("execution", "content", "maintenance")), "all responsibilities required")
        effect = formal.get("authority_effect", {})
        conflict = bool(authority and authority.would_conflict(formal, record))
        add(self.RULE_IDS[4], not conflict, "authority action conflicts with current projection" if conflict else "authority scope is unique")
        allowed = set(fd.get("formal_artifact_kinds", []))
        add(self.RULE_IDS[5], bool(artifacts) and all(item.get("kind") in allowed for item in artifacts), "artifact kinds must be allowed by domain")
        action, operation = effect.get("action"), formal.get("operation")
        operation_matches_effect = not ((action == "establish" and operation != "enter") or (action == "replace" and operation not in {"revise", "supersede"}) or (action == "withdraw" and operation != "withdraw"))
        registered_domain_ref = domain.get("domain_ref") or {"material_id": domain.get("id"), "revision_ref": domain.get("revision_ref")}
        def same_version(left: object, right: object) -> bool:
            if not isinstance(left, dict) or not isinstance(right, dict):
                return False
            left_revision, right_revision = left.get("revision_ref"), right.get("revision_ref")
            return (
                left.get("material_id") == right.get("material_id")
                and isinstance(left_revision, dict)
                and isinstance(right_revision, dict)
                and all(left_revision.get(key) == right_revision.get(key) for key in ("provider", "object", "revision"))
            )
        domain_revision_matches = same_version(formal.get("domain_ref"), registered_domain_ref)
        causal = operation_matches_effect and domain_revision_matches
        causality_message = "operation and authority action must agree" if not operation_matches_effect else "formalization domain_ref must match the registered exact domain revision"
        add(self.RULE_IDS[6], causal, causality_message)
        add(self.RULE_IDS[7], bool(inspections) and all(item.exists and item.structure_ok for item in inspections), "artifact inspector must confirm physical structure")
        declared_scope_keys = {
            (scope.get("object"), scope.get("purpose"), scope.get("scope"))
            for scope in fd.get("authority_scopes", [])
            if isinstance(scope, dict)
        }
        effect_scopes = effect.get("scopes", [])
        scopes_declared = bool(effect_scopes) and all(
            isinstance(scope, dict) and (scope.get("object"), scope.get("purpose"), scope.get("scope")) in declared_scope_keys
            for scope in effect_scopes
        )
        add(self.RULE_IDS[8], bool(fd.get("index_policy")) and scopes_declared and all(item.indexable for item in inspections), "authority scopes must be declared and indexable")
        for rule in self.domain_rules:
            result = rule.evaluate(domain, record)
            rows = result if isinstance(result, list) else [result]
            for finding in rows:
                if not isinstance(finding, GovernanceFinding) or finding.status not in {
                    "passed", "failed", "stale", "waived", "not_applicable",
                }:
                    raise TypeError(f"domain rule {getattr(rule, 'rule_id', rule)!r} must return GovernanceFinding")
                findings.append(finding)
        # explicit failure is deliberately last, so it sees product-rule blockers too.
        earlier_blocking = any(item.blocking and item.status != "passed" for item in findings)
        add(self.RULE_IDS[9], formal.get("outcome") == "succeeded" and not earlier_blocking, "blocking findings prevent acceptance")
        accepted = formal.get("outcome") == "succeeded" and not any(
            item.blocking and item.status != "passed" for item in findings
        )
        report = GovernanceReport(domain.get("domain_ref", {"material_id": domain.get("id"), "revision_ref": domain.get("revision_ref")}), target, fd.get("rule_set_ref", {}), findings, accepted)
        self._archive(report)
        return report

    def _archive(self, report: GovernanceReport) -> None:
        if self.finding_archive is None:
            return
        for finding in report.findings:
            if finding.status == "passed":
                continue
            self.finding_archive.append_finding({"entity_id": report.record_ref.get("material_id", "formalization"), "entity_kind": "material", "finding_kind": "material-domain-governance", "evidence": str(finding.evidence), "commentary": finding.message, "concern": finding.rule_id})

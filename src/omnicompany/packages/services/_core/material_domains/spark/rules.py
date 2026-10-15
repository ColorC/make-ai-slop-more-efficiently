"""Product-owned governance rules for the five Spark formal domains."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..governance import GovernanceFinding
from .p4 import IgameP4ArtifactChecker, P4_ARTIFACT_KINDS


COPY_SOURCE_KINDS = frozenset({"过往正式", "包装", "通用"})
_PUBLIC_NAME_SEPARATORS = re.compile(r"[\s_\-·—:：/\\]+")


def _formal_domain(domain: Mapping[str, Any]) -> Mapping[str, Any]:
    value = domain.get("formal_domain", domain)
    return value if isinstance(value, Mapping) else {}


def _formalization(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("formalization", record)
    return value if isinstance(value, Mapping) else {}


def _spark_profile(domain: Mapping[str, Any]) -> Mapping[str, Any]:
    extensions = _formal_domain(domain).get("extensions")
    spark = extensions.get("spark") if isinstance(extensions, Mapping) else None
    return spark if isinstance(spark, Mapping) else {}


def _is_spark_domain(domain: Mapping[str, Any]) -> bool:
    product_ref = _formal_domain(domain).get("product_ref")
    return (
        isinstance(product_ref, Mapping)
        and product_ref.get("ref") == "spark:game"
        and bool(_spark_profile(domain))
    )


def _target(record: Mapping[str, Any]) -> dict[str, Any]:
    revision_ref = record.get("revision_ref")
    return {
        "material_id": str(record.get("id") or "formalization"),
        "revision_ref": dict(revision_ref) if isinstance(revision_ref, Mapping) else {},
    }


def _sources(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = _formalization(record).get("sources")
    return [dict(item) for item in values if isinstance(item, Mapping)] if isinstance(values, list) else []


def _runtime_interface_contract(record: Mapping[str, Any]) -> Mapping[str, Any]:
    extensions = _formalization(record).get("extensions")
    value = extensions.get("spark_runtime_interface") if isinstance(extensions, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _observation_source(record: Mapping[str, Any]) -> str:
    value = _runtime_interface_contract(record).get("observation_source")
    return value if isinstance(value, str) else ""


class SparkArtifactRequirementsRule:
    rule_id = "spark.artifact-requirements"

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        if not _is_spark_domain(domain):
            return _not_applicable(self.rule_id, record, "非 Spark 正式域")
        profile = _spark_profile(domain)
        formal = _formalization(record)
        artifacts = formal.get("artifacts")
        artifact_rows = [item for item in artifacts if isinstance(item, Mapping)] if isinstance(artifacts, list) else []
        kinds = {str(item.get("kind")) for item in artifact_rows if item.get("kind")}
        failures: list[str] = []

        requirements = profile.get("artifact_requirements")
        if not isinstance(requirements, Mapping):
            failures.append("Profile 缺少 artifact_requirements")
            requirements = {}
        all_of = _string_set(requirements.get("all_of"))
        any_of = _string_set(requirements.get("any_of"))
        observation_source = _observation_source(record)
        source_all_of = requirements.get("source_all_of")
        if isinstance(source_all_of, Mapping) and observation_source:
            all_of.update(_string_set(source_all_of.get(observation_source)))
        source_any_of = requirements.get("source_any_of")
        if isinstance(source_any_of, Mapping) and observation_source:
            any_of.update(_string_set(source_any_of.get(observation_source)))
        missing_all = sorted(all_of - kinds)
        if missing_all:
            failures.append(f"缺少必须产物类型：{', '.join(missing_all)}")
        if any_of and not any_of & kinds:
            failures.append(f"至少需要一种产物类型：{', '.join(sorted(any_of))}")
        if profile.get("p4_required") is True and not kinds & P4_ARTIFACT_KINDS:
            failures.append("该域必须包含可由 demogame-p4 核查的 P4 产物")

        results = formal.get("rule_results")
        result_rows = [item for item in results if isinstance(item, Mapping)] if isinstance(results, list) else []
        passed_results = {
            str(item.get("rule_id"))
            for item in result_rows
            if item.get("status") == "passed" and _has_evidence(item.get("evidence"))
        }
        required_results = _string_set(profile.get("required_rule_results"))
        conditional = profile.get("conditional_rule_results")
        if isinstance(conditional, Mapping):
            for artifact_kind, rule_ids in conditional.items():
                if artifact_kind in kinds:
                    required_results.update(_string_set(rule_ids))
        source_required = profile.get("source_required_rule_results")
        if isinstance(source_required, Mapping) and observation_source:
            required_results.update(_string_set(source_required.get(observation_source)))
        missing_results = sorted(required_results - passed_results)
        if missing_results:
            failures.append(f"缺少已通过且有证据的规则结果：{', '.join(missing_results)}")

        return GovernanceFinding(
            self.rule_id,
            "failed" if failures else "passed",
            _target(record),
            "；".join(failures) if failures else "Spark 域产物组合与验证证据完整",
            _sources(record),
            bool(failures),
        )


class SparkP4VerifiabilityRule:
    rule_id = "spark.p4-verifiable"

    def __init__(self, checker: IgameP4ArtifactChecker) -> None:
        self.checker = checker

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        if not _is_spark_domain(domain):
            return _not_applicable(self.rule_id, record, "非 Spark 正式域")
        profile = _spark_profile(domain)
        artifacts = _formalization(record).get("artifacts")
        artifact_rows = artifacts if isinstance(artifacts, list) else []
        p4_artifacts = [
            item
            for item in artifact_rows
            if isinstance(item, Mapping)
            and item.get("kind") in P4_ARTIFACT_KINDS
        ]
        if not p4_artifacts:
            required_sources = _string_set(profile.get("p4_required_sources"))
            required = profile.get("p4_required") is True or _observation_source(record) in required_sources
            return GovernanceFinding(
                self.rule_id,
                "failed" if required else "not_applicable",
                _target(record),
                "该域要求 P4 产物，但记录中没有" if required else "该正式域不以 P4 产物为准入对象",
                _sources(record),
                required,
            )

        checks = [self.checker.check(item) for item in p4_artifacts]
        failed = [check for check in checks if not check.verifiable]
        evidence = [entry for check in checks for entry in check.evidence]
        if failed:
            detail = "；".join(f"{check.status}：{check.message}" for check in failed)
            return GovernanceFinding(
                self.rule_id,
                "failed",
                _target(record),
                f"P4 产物不可正式化：{detail}",
                evidence,
                True,
            )
        return GovernanceFinding(
            self.rule_id,
            "passed",
            _target(record),
            "全部 P4 产物已由 demogame-p4 核对到声明的最新 CL",
            evidence,
            False,
        )


class SparkRuntimeInterfaceObservationRule:
    """Keep Unity, WebTwin and demo observations under one interface identity."""

    rule_id = "spark.runtime-interface-observation"
    _sources = frozenset({"unity", "webtwin", "demo"})
    _interface_id = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        domain_ref = _formal_domain(domain).get("rule_set_ref")
        if not (
            _is_spark_domain(domain)
            and isinstance(domain_ref, Mapping)
            and domain_ref.get("material_id") == "spark.rule-set.runtime-interface"
        ):
            return _not_applicable(self.rule_id, record, "not a Spark runtime-interface domain")

        formal = _formalization(record)
        contract = _runtime_interface_contract(record)
        interface_id = contract.get("interface_id")
        source = contract.get("observation_source")
        revision = contract.get("source_revision")
        observation_key = contract.get("observation_key")
        manifest_ref = contract.get("manifest_ref")
        input_material = formal.get("input_material")
        failures: list[str] = []

        if not isinstance(interface_id, str) or not self._interface_id.fullmatch(interface_id):
            failures.append("interface_id must be a stable View class or chain id")
        if source not in self._sources:
            failures.append("observation_source must be one of unity, webtwin, demo")
        if not isinstance(revision, str) or not revision:
            failures.append("source_revision is required")
        expected_material_id = (
            f"spark.domain.runtime-interface/{interface_id}"
            if isinstance(interface_id, str) and interface_id
            else None
        )
        actual_material_id = input_material.get("material_id") if isinstance(input_material, Mapping) else None
        if expected_material_id and actual_material_id != expected_material_id:
            failures.append(f"input material must use stable identity {expected_material_id}")
        expected_key = (
            f"{interface_id}/{source}/{revision}"
            if isinstance(interface_id, str)
            and isinstance(source, str)
            and isinstance(revision, str)
            and interface_id
            and revision
            else None
        )
        if expected_key and observation_key != expected_key:
            failures.append(f"observation_key must equal {expected_key}")
        if not _source_ref(manifest_ref):
            failures.append("manifest_ref must be an explicit source reference")

        return GovernanceFinding(
            self.rule_id,
            "failed" if failures else "passed",
            _target(record),
            "; ".join(failures) if failures else "three-source observation shares one interface identity",
            [dict(manifest_ref)] if _source_ref(manifest_ref) else _sources(record),
            bool(failures),
        )


class SparkCopySourceRule:
    rule_id = "spark.copy-source"

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        if not _is_spark_domain(domain) or _spark_profile(domain).get("domain_kind") != "界面与文案":
            return _not_applicable(self.rule_id, record, "非 Spark 界面与文案域")
        formal = _formalization(record)
        artifacts = formal.get("artifacts")
        artifact_rows = artifacts if isinstance(artifacts, list) else []
        copy_refs = {
            str(item.get("ref"))
            for item in artifact_rows
            if isinstance(item, Mapping)
            and item.get("kind") == "spark-p4-copy"
            and item.get("ref")
        }
        if not copy_refs:
            return GovernanceFinding(
                self.rule_id,
                "not_applicable",
                _target(record),
                "本次只有界面产物，没有文案产物",
                _sources(record),
                False,
            )

        extensions = formal.get("extensions")
        copy_contract = extensions.get("spark_copy") if isinstance(extensions, Mapping) else None
        failures: list[str] = []
        evidence = _sources(record)
        if not isinstance(copy_contract, Mapping):
            failures.append("缺少 formalization.extensions.spark_copy")
            copy_contract = {}

        forbidden = copy_contract.get("forbidden_names")
        forbidden_values: dict[str, list[str]] = {}
        for name_kind in ("开发名", "需求临时名"):
            values = forbidden.get(name_kind) if isinstance(forbidden, Mapping) else None
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                failures.append(f"forbidden_names.{name_kind} 必须是字符串数组，可为空")
                forbidden_values[name_kind] = []
            else:
                forbidden_values[name_kind] = values

        items = copy_contract.get("items")
        rows = [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []
        if not rows or len(rows) != len(items or []):
            failures.append("spark_copy.items 必须是非空对象数组")
        declared_refs: set[str] = set()
        for index, item in enumerate(rows):
            prefix = f"items[{index}]"
            artifact_ref = item.get("artifact_ref")
            text = item.get("text")
            source_kind = item.get("source_kind")
            if not isinstance(artifact_ref, str) or artifact_ref not in copy_refs:
                failures.append(f"{prefix}.artifact_ref 未指向本次 spark-p4-copy 产物")
            else:
                declared_refs.add(artifact_ref)
            if not isinstance(text, str) or not text.strip():
                failures.append(f"{prefix}.text 必须是非空文案")
                continue
            if source_kind not in COPY_SOURCE_KINDS:
                failures.append(f"{prefix}.source_kind 只能是过往正式、包装或通用")
            source_ref = item.get("source_ref")
            if source_kind in {"过往正式", "包装"}:
                if not _source_ref(source_ref):
                    failures.append(f"{prefix} 的 {source_kind} 文案必须给 source_ref")
                else:
                    evidence.append(dict(source_ref))
            for name_kind, names in forbidden_values.items():
                if any(_contains_name(text, name) for name in names):
                    failures.append(f"{prefix}.text 命中禁止外发的{name_kind}")
        missing_declarations = sorted(copy_refs - declared_refs)
        if missing_declarations:
            failures.append(f"文案产物缺少来源声明：{', '.join(missing_declarations)}")

        return GovernanceFinding(
            self.rule_id,
            "failed" if failures else "passed",
            _target(record),
            "；".join(failures) if failures else "文案来源明确，且未命中开发名或需求临时名",
            evidence,
            bool(failures),
        )


class SparkUserReviewRule:
    rule_id = "spark.user-review"

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        if not _is_spark_domain(domain):
            return _not_applicable(self.rule_id, record, "非 Spark 正式域")
        policy = _spark_profile(domain).get("user_review_policy")
        if policy not in {"optional", "required"}:
            return GovernanceFinding(
                self.rule_id,
                "failed",
                _target(record),
                "Spark Profile 必须把 user_review_policy 声明为 optional 或 required",
                _sources(record),
                True,
            )
        if policy == "optional":
            return GovernanceFinding(
                self.rule_id,
                "passed",
                _target(record),
                "该 Spark 正式域没有把用户审核设为准入前置",
                _sources(record),
                False,
            )

        formal_extensions = _formalization(record).get("extensions")
        review = formal_extensions.get("user_review") if isinstance(formal_extensions, Mapping) else None
        reviewer = review.get("reviewer") if isinstance(review, Mapping) else None
        evidence = review.get("evidence") if isinstance(review, Mapping) else None
        passed = (
            isinstance(review, Mapping)
            and review.get("status") == "passed"
            and _source_ref(reviewer)
            and _has_evidence(evidence)
        )
        review_evidence = [dict(item) for item in evidence if isinstance(item, Mapping)] if isinstance(evidence, list) else []
        return GovernanceFinding(
            self.rule_id,
            "passed" if passed else "failed",
            _target(record),
            "域要求的用户审核证据完整" if passed else "该域明确要求用户审核，但记录没有完整审核证据",
            review_evidence or _sources(record),
            not passed,
        )


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _has_evidence(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_source_ref(item) for item in value)


def _source_ref(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("kind"), str)
        and bool(value["kind"])
        and isinstance(value.get("ref"), str)
        and bool(value["ref"])
    )


def _contains_name(text: str, forbidden: str) -> bool:
    normalized_text = _PUBLIC_NAME_SEPARATORS.sub("", text).casefold()
    normalized_forbidden = _PUBLIC_NAME_SEPARATORS.sub("", forbidden).casefold()
    return bool(normalized_forbidden) and normalized_forbidden in normalized_text


def _not_applicable(
    rule_id: str,
    record: Mapping[str, Any],
    message: str,
) -> GovernanceFinding:
    return GovernanceFinding(
        rule_id,
        "not_applicable",
        _target(record),
        message,
        _sources(record),
        False,
    )

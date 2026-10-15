# [OMNI] origin=codex created_by=codex intent=observatory-configuration-invariants
"""Configuration loading for the AI learning observatory."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from omnicompany.core.config import omni_workspace_root


DEFAULT_CONFIG_PATH = omni_workspace_root() / "config" / "ai_learning_observatory.yaml"


class ObservatoryConfigError(ValueError):
    """Raised when the service configuration is incomplete or inconsistent."""


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    configured = path or os.environ.get("OMNI_AI_LEARNING_CONFIG") or DEFAULT_CONFIG_PATH
    config_path = Path(configured).expanduser().resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ObservatoryConfigError(f"cannot read observatory config: {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ObservatoryConfigError("observatory config root must be a mapping")
    cfg = deepcopy(raw)
    _validate_config(cfg)
    cfg["_config_path"] = str(config_path)
    return cfg


def _validate_config(cfg: dict[str, Any]) -> None:
    portfolio = cfg.get("portfolio")
    collection = cfg.get("collection")
    evaluation = cfg.get("evaluation")
    if not isinstance(portfolio, dict):
        raise ObservatoryConfigError("portfolio must be a mapping")
    if not isinstance(collection, dict):
        raise ObservatoryConfigError("collection must be a mapping")
    if not isinstance(evaluation, dict):
        raise ObservatoryConfigError("evaluation must be a mapping")

    minimum = int(portfolio.get("minimum_projects") or 0)
    maximum = int(portfolio.get("maximum_projects") or 0)
    if minimum < 1 or maximum < minimum:
        raise ObservatoryConfigError("portfolio minimum/maximum projects are invalid")
    preferred = portfolio.get("preferred_projects") or []
    if not isinstance(preferred, list) or len(preferred) < minimum:
        raise ObservatoryConfigError("preferred_projects must cover minimum_projects")
    preferred_ids = [str(item.get("id") or "") for item in preferred if isinstance(item, dict)]
    if len(preferred_ids) != len(set(preferred_ids)) or any(not item for item in preferred_ids):
        raise ObservatoryConfigError("preferred project ids must be unique and non-empty")

    categories = collection.get("source_categories") or []
    if not isinstance(categories, list) or len(categories) < 8:
        raise ObservatoryConfigError("collection must declare the broad source categories")
    if str(evaluation.get("model") or "") != "gpt-5.6-terra":
        raise ObservatoryConfigError("default evaluator must be gpt-5.6-terra")
    if str(evaluation.get("provider") or "") != "the_company":
        raise ObservatoryConfigError("default evaluator provider must be the_company")
    if str(evaluation.get("harness") or "") != "codex":
        raise ObservatoryConfigError("default evaluator harness must be codex")
    if not str(evaluation.get("output_schema_path") or "").strip():
        raise ObservatoryConfigError("default evaluator requires output_schema_path")
    if int(evaluation.get("timeout_seconds") or 0) <= 0:
        raise ObservatoryConfigError("default evaluator timeout_seconds must be positive")

    profiles = cfg.get("evaluation_profiles")
    if not isinstance(profiles, dict):
        raise ObservatoryConfigError("evaluation_profiles must be a mapping")
    expected_dimensions = {
        "time",
        "token_cost",
        "user_expectation",
        "objective_outcome",
        "human_attention",
    }
    dimensions = profiles.get("dimensions") or []
    if set(dimensions) != expected_dimensions or len(dimensions) != 5:
        raise ObservatoryConfigError(
            "evaluation_profiles must declare the five independent dimensions exactly once"
        )
    for key in (
        "correctness_is_gate",
        "no_composite_score",
        "missing_is_unknown",
        "absence_is_not_success",
    ):
        if profiles.get(key) is not True:
            raise ObservatoryConfigError(f"evaluation_profiles.{key} must be true")
    for key in ("root", "markdown_root", "schema_path"):
        if not str(profiles.get(key) or "").strip():
            raise ObservatoryConfigError(f"evaluation_profiles requires {key}")

    profile_analysis = cfg.get("profile_analysis")
    if not isinstance(profile_analysis, dict):
        raise ObservatoryConfigError("profile_analysis must be a mapping")
    if profile_analysis.get("recurring") is not False:
        raise ObservatoryConfigError("profile_analysis must remain non-recurring")
    if str(profile_analysis.get("harness") or "") != "codex":
        raise ObservatoryConfigError("profile_analysis harness must be codex")
    if str(profile_analysis.get("provider") or "") != "openai":
        raise ObservatoryConfigError("profile_analysis provider must be openai")
    if str(profile_analysis.get("model") or "") != "gpt-5.6-luna":
        raise ObservatoryConfigError("profile_analysis model must be gpt-5.6-luna")
    if not str(profile_analysis.get("authorized_date") or "").strip():
        raise ObservatoryConfigError("profile_analysis requires authorized_date")

    attribution = cfg.get("conversation_attribution")
    if attribution is not None:
        if not isinstance(attribution, dict):
            raise ObservatoryConfigError("conversation_attribution must be a mapping")
        if str(attribution.get("harness") or "") != "codex":
            raise ObservatoryConfigError("conversation attribution harness must be codex")
        if str(attribution.get("provider") or "") != "the_company":
            raise ObservatoryConfigError("conversation attribution provider must be the_company")
        if str(attribution.get("model") or "") != "deepseek-v4.1-flash":
            raise ObservatoryConfigError(
                "conversation attribution model must be deepseek-v4.1-flash"
            )
        for key in (
            "model_catalog_path",
            "catalog_schema_path",
            "binding_schema_path",
            "output_root",
        ):
            if not str(attribution.get(key) or "").strip():
                raise ObservatoryConfigError(
                    f"conversation attribution requires {key}"
                )
        for key in ("timeout_seconds",):
            if int(attribution.get(key) or 0) <= 0:
                raise ObservatoryConfigError(
                    f"conversation attribution {key} must be positive"
                )


__all__ = ["DEFAULT_CONFIG_PATH", "ObservatoryConfigError", "load_config"]

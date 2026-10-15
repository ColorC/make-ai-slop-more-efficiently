"""Compatibility import for the canonical Hypothesis V1 upgrader module name."""

from .hypothesis_upgrader import (
    HypothesisV1Upgrader,
    HypothesisV1UpgradeResult,
    UpgradedHypothesis,
    main,
)

__all__ = [
    "HypothesisV1Upgrader",
    "HypothesisV1UpgradeResult",
    "UpgradedHypothesis",
    "main",
]

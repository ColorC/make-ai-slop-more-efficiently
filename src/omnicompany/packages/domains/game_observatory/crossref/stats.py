"""Small dependency-free statistics used by the generator weight rules."""

from __future__ import annotations

import math
from dataclasses import dataclass


_MAX_ITERATIONS = 400
_EPSILON = 1e-12


def _lower_series(shape: float, value: float) -> float:
    """Regularised lower incomplete gamma P(shape, value) by series expansion."""

    term = 1.0 / shape
    total = term
    index = 0
    while index < _MAX_ITERATIONS:
        index += 1
        term *= value / (shape + index)
        total += term
        if abs(term) < abs(total) * _EPSILON:
            break
    return total * math.exp(-value + shape * math.log(value) - math.lgamma(shape))


def _upper_fraction(shape: float, value: float) -> float:
    """Regularised upper incomplete gamma Q(shape, value) by continued fraction."""

    tiny = 1e-300
    b = value + 1.0 - shape
    c = 1.0 / tiny
    d = 1.0 / b if b else 1.0 / tiny
    result = d
    for index in range(1, _MAX_ITERATIONS + 1):
        an = -index * (index - shape)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < _EPSILON:
            break
    return result * math.exp(-value + shape * math.log(value) - math.lgamma(shape))


def chi_square_sf(statistic: float, degrees_of_freedom: int) -> float:
    """Upper tail probability of the chi-square distribution."""

    if degrees_of_freedom <= 0:
        return 1.0
    if statistic <= 0:
        return 1.0
    shape = degrees_of_freedom / 2.0
    value = statistic / 2.0
    if value < shape + 1.0:
        return max(0.0, min(1.0, 1.0 - _lower_series(shape, value)))
    return max(0.0, min(1.0, _upper_fraction(shape, value)))


@dataclass(frozen=True)
class GoodnessOfFit:
    """Chi-square goodness-of-fit outcome for one categorical sample."""

    sample_size: int
    statistic: float
    degrees_of_freedom: int
    p_value: float
    expected: dict[str, float]
    observed: dict[str, int]
    off_list: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "sample_size": self.sample_size,
            "statistic": round(self.statistic, 6),
            "degrees_of_freedom": self.degrees_of_freedom,
            "p_value": round(self.p_value, 9),
            "expected": {key: round(value, 6) for key, value in sorted(self.expected.items())},
            "observed": dict(sorted(self.observed.items())),
            "off_list": dict(sorted(self.off_list.items())),
        }


def goodness_of_fit(
    observed: dict[str, int], weights: dict[str, float]
) -> GoodnessOfFit | None:
    """Compare a categorical sample against declared weights.

    ``observed`` counts every produced category; ``weights`` holds the declared
    relative weights. Categories outside ``weights`` are reported separately in
    ``off_list`` and excluded from the statistic, because the statistic is only
    defined for categories with a positive expected count.
    """

    total_weight = sum(value for value in weights.values() if value > 0)
    if total_weight <= 0:
        return None
    on_list = {key: count for key, count in observed.items() if weights.get(key, 0) > 0}
    off_list = {key: count for key, count in observed.items() if weights.get(key, 0) <= 0}
    sample_size = sum(on_list.values())
    if sample_size <= 0:
        return None
    expected = {
        key: sample_size * weight / total_weight
        for key, weight in weights.items()
        if weight > 0
    }
    statistic = sum(
        (on_list.get(key, 0) - value) ** 2 / value for key, value in expected.items()
    )
    degrees_of_freedom = max(0, len(expected) - 1)
    return GoodnessOfFit(
        sample_size=sample_size,
        statistic=statistic,
        degrees_of_freedom=degrees_of_freedom,
        p_value=chi_square_sf(statistic, degrees_of_freedom),
        expected=expected,
        observed=dict(on_list),
        off_list=off_list,
    )

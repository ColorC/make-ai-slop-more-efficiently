"""Small standard-library compatibility surface for supported Python versions."""

from __future__ import annotations

from datetime import timezone

UTC = timezone.utc

try:
    from typing import NotRequired, TypedDict
except ImportError:  # Python 3.10 needs both PEP 655 markers and its TypedDict backport.
    from typing_extensions import NotRequired, TypedDict

try:
    from enum import StrEnum
except ImportError:  # Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """String enum with Python 3.11's construction and lower-case auto values."""

        def __new__(cls, *values):
            if len(values) > 3:
                raise TypeError("too many arguments for str(): %r" % (values,))
            if len(values) == 1 and not isinstance(values[0], str):
                raise TypeError("%r is not a string" % (values[0],))
            if len(values) >= 2 and not isinstance(values[1], str):
                raise TypeError("encoding must be a string, not %r" % (values[1],))
            if len(values) == 3 and not isinstance(values[2], str):
                raise TypeError("errors must be a string, not %r" % (values[2],))
            value = str(*values)
            member = str.__new__(cls, value)
            member._value_ = value
            return member

        __str__ = str.__str__
        __format__ = str.__format__

        @staticmethod
        def _generate_next_value_(name, start, count, last_values):
            return name.lower()


__all__ = ["UTC", "NotRequired", "TypedDict", "StrEnum"]

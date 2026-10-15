"""Compatibility alias for :mod:`omnicompany.core.safety_installer`.

Keep the module object shared so existing imports and monkeypatches use the
same implementation as non-Dashboard consumers.
"""

import sys

from omnicompany.core import safety_installer as _implementation

sys.modules[__name__] = _implementation

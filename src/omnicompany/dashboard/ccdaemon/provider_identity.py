"""Compatibility alias for :mod:`omnicompany.core.provider_identity`.

Keep the module object shared so existing imports and monkeypatches use the
same implementation as non-Dashboard consumers.
"""

import sys

from omnicompany.core import provider_identity as _implementation

sys.modules[__name__] = _implementation

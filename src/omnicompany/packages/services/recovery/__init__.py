"""Cross-provider AI-session evidence recovery.

The public package is deliberately small and stable. Provider-specific parsing
lives behind the registry in :mod:`omnicompany.packages.services.recovery.providers`; the legacy
CLI remains available through :mod:`omnicompany.packages.services.recovery.entrypoint` while its
commands are migrated onto the normalized evidence model.
"""

from .evidence import EvidenceRecord, RecoveryPlan, plan_recovery
from .providers import ProviderRegistry, default_registry

__all__ = [
    "EvidenceRecord",
    "ProviderRegistry",
    "RecoveryPlan",
    "default_registry",
    "plan_recovery",
]

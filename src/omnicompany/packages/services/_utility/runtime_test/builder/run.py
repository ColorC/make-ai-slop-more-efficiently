# [OMNI] origin=claude-code domain=services/runtime_test_builder/run ts=2026-04-27T00:00:00Z type=config
# [OMNI] material_id="material:utility.runtime_test.builder.binding_composer.config.py"
"""runtime_test_builder · build_bindings (真 meta 层 v2)."""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.format import create_builtin_registry

from .formats import register_formats
from .workers.target_explorer import TargetExplorerWorker
from .workers.hypothesis_proposer import HypothesisProposerWorker
from .workers.hypothesis_verifier_dispatcher import HypothesisVerifierDispatcherWorker
from .workers.portrait_assembler import PortraitAssemblerWorker


def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:
    registry = create_builtin_registry()
    register_formats(registry)
    model = str((input_dict or {}).get("model") or "").strip() or None
    return {
        "TargetExplorerWorker": TargetExplorerWorker(model=model),
        "HypothesisProposerWorker": HypothesisProposerWorker(model=model),
        "HypothesisVerifierDispatcherWorker": HypothesisVerifierDispatcherWorker(),
        "PortraitAssemblerWorker": PortraitAssemblerWorker(),
    }

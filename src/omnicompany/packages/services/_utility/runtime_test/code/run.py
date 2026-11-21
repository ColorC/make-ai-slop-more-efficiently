# [OMNI] origin=claude-code domain=services/code_runtime_test/run ts=2026-04-26T00:00:00Z type=config
# [OMNI] material_id="material:utility.runtime_test.code.binding_composer.config.py"
"""code_runtime_test Team · build_bindings."""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.format import create_builtin_registry

from .formats import register_formats
from .workers.target_ingress import TargetIngressWorker
from .workers.golden_runner import GoldenContractRunnerWorker
from .workers.error_path_runner import ErrorPathRunnerWorker
from .workers.reproducibility_runner import ReproducibilityRunnerWorker
from .workers.portrait_assembler import PortraitAssemblerWorker


def build_bindings(input_dict: dict | None = None) -> dict[str, Worker]:
    registry = create_builtin_registry()
    register_formats(registry)
    return {
        "TargetIngressWorker": TargetIngressWorker(),
        "GoldenContractRunnerWorker": GoldenContractRunnerWorker(),
        "ErrorPathRunnerWorker": ErrorPathRunnerWorker(),
        "ReproducibilityRunnerWorker": ReproducibilityRunnerWorker(),
        "PortraitAssemblerWorker": PortraitAssemblerWorker(),
    }

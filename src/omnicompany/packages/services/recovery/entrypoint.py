# [OMNI] origin=claude-code created_by=omni-recover intent=run-packaged-recovery-cli
"""Installed ``omni-recover`` entry point with source-checkout compatibility."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType


def _legacy_cli() -> ModuleType:
    try:
        from ._legacy import session_recovery_cli

        return session_recovery_cli
    except ImportError:
        # Editable/source checkout: scripts are intentionally kept as direct
        # compatibility entry points for existing incident runbooks.
        script = next(
            (
                candidate / "scripts" / "session_recovery_cli.py"
                for candidate in Path(__file__).resolve().parents
                if (candidate / "scripts" / "session_recovery_cli.py").is_file()
            ),
            None,
        )
        if script is None:
            raise RuntimeError("packaged recovery CLI module is missing")
        script_dir = str(script.parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        spec = importlib.util.spec_from_file_location("session_recovery_cli", script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load recovery CLI from {script}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def main() -> int:
    """Run the compatibility CLI from an installed wheel or source checkout."""

    return int(_legacy_cli().main())

"""Make the standalone distribution self-contained in and outside the monorepo."""

from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Select monorepo sources directly, or the copies embedded in an sdist."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        monorepo = root.parents[1]
        recovery = monorepo / "src" / "omnicompany" / "packages" / "services" / "recovery"
        session_cli = monorepo / "scripts" / "session_recovery_cli.py"
        baseline_cli = monorepo / "scripts" / "recovery_baseline_cli.py"

        if self.target_name == "sdist":
            destinations = {
                recovery: "_vendor_src/omnicompany/packages/services/recovery",
                session_cli: "_vendor_src/omnicompany/packages/services/recovery/_legacy/session_recovery_cli.py",
                baseline_cli: "_vendor_src/omnicompany/packages/services/recovery/_legacy/recovery_baseline_cli.py",
            }
        else:
            embedded = root / "_vendor_src" / "omnicompany" / "packages" / "services" / "recovery"
            if embedded.is_dir():
                destinations = {embedded: "omnicompany/packages/services/recovery"}
            else:
                destinations = {
                    recovery: "omnicompany/packages/services/recovery",
                    session_cli: "omnicompany/packages/services/recovery/_legacy/session_recovery_cli.py",
                    baseline_cli: "omnicompany/packages/services/recovery/_legacy/recovery_baseline_cli.py",
                }

        missing = [str(path) for path in destinations if not path.exists()]
        if missing:
            raise FileNotFoundError(f"omni-recover build sources are missing: {missing}")
        build_data.setdefault("force_include", {}).update(
            {str(source): destination for source, destination in destinations.items()}
        )

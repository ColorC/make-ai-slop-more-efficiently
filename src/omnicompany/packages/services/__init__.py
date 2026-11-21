# [OMNI] origin=claude-code domain=omnicompany/packages/services ts=2026-04-08T05:00:00Z
# [OMNI] material_id="material:packages.services_namespace.exports.py"
"""omnicompany.packages.services — see docs/archmap.yaml for layer rules."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Public compatibility path promised by the service docs: e.g.
# `omnicompany.packages.services.omnicompany`, `.selftest`, and `.doctor`
# resolve to the single implementation under an internal service stratum.
# Extending __path__ keeps imports lazy and avoids duplicate service packages.
for _stratum in ("_core", "_diagnosis", "_governance", "_learning", "_utility", "_authoring"):
    _path = str(Path(__file__).with_name(_stratum))
    if _path not in __path__:
        __path__.append(_path)


def _alias_module(public_name: str, internal_name: str) -> None:
    sys.modules.setdefault(public_name, importlib.import_module(internal_name))


# `Worker` identity must be stable across public and internal imports; otherwise
# issubclass checks see two class objects loaded from the same file.
_alias_module(
    __name__ + ".omnicompany",
    "omnicompany.packages.services._core.omnicompany",
)
for _submodule in (
    "agent_team_demo",
    "formats",
    "llm_client",
    "material_dispatcher",
    "material_events",
    "worker",
):
    _alias_module(
        __name__ + ".omnicompany." + _submodule,
        "omnicompany.packages.services._core.omnicompany." + _submodule,
    )

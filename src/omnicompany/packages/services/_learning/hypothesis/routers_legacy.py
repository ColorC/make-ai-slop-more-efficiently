"""Deprecated import path for the external hypothesis Experimenter.

The Python-owned inner loop and its arbitrary Bash tool were removed during the
AgentRuntime externalization.  New code imports from ``workers.experimenter``;
this alias exists only for callers that still use the historical module path.
"""

from .workers.experimenter import ExperimenterWorker as ExperimenterRouter

__all__ = ["ExperimenterRouter"]

from __future__ import annotations

from omnicompany.packages.domains.game_observatory.agent_plugins import AgentPluginRegistry


def test_agent_plugin_registry_keeps_unverified_candidates_non_passing(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_AUTOGLM_HOME", raising=False)
    monkeypatch.delenv("OPEN_AUTOGLM_MODEL_ENDPOINT", raising=False)
    monkeypatch.delenv("CRADLE_HOME", raising=False)
    monkeypatch.delenv("CRADLE_MODEL_ENDPOINT", raising=False)
    monkeypatch.setattr(
        AgentPluginRegistry,
        "_package_version",
        staticmethod(lambda name: "1.4.3" if name == "airtest" else None),
    )
    plugins = {item.id: item for item in AgentPluginRegistry().probe()}
    assert plugins["airtest"].status == "runnable"
    assert plugins["maaframework"].status == "not_installed"
    assert plugins["open-autoglm"].status == "blocked"
    assert plugins["cradle"].status == "blocked"
    assert plugins["open-autoglm"].blocker
from omnicompany.dashboard.ccdaemon.provider_identity import AGENT_WORKER_PROVIDER_IDS
from omnicompany.dashboard.ccdaemon.pty_routes import agent_worker_catalog


def test_agent_worker_catalog_is_complete_and_has_no_retired_launchers() -> None:
    catalog = agent_worker_catalog()
    provider_options = [item for item in catalog["options"] if item["kind"] == "agent_worker"]

    assert catalog["schema_version"] == 1
    assert [item["provider"] for item in provider_options] == list(AGENT_WORKER_PROVIDER_IDS)
    assert all(item["id"] == f"provider:{item['provider']}" for item in provider_options)
    assert all(isinstance(item["models"], list) and isinstance(item["efforts"], list) for item in provider_options)
    by_provider = {item["provider"]: item for item in provider_options}
    assert by_provider["codex_the_company"]["default_model"] == "deepseek-v4.1-flash"
    assert "gpt-5.6-terra" in by_provider["codex_the_company"]["models"]
    assert "xhigh" in by_provider["codex_the_company"]["model_efforts"]["gpt-5.6-terra"]
    assert by_provider["codex_glm"]["default_model"] == "glm-5.3"
    assert by_provider["dsh"]["models"]
    assert {"gemini", "zcode"}.isdisjoint({item["provider"] for item in provider_options})
    shell_options = [item for item in catalog["options"] if item["kind"] == "shell"]
    assert {item["id"] for item in shell_options} == {
        "shell:plain",
        "shell:powershell",
    }
    assert all(item["cmd"][0] == "pwsh" for item in shell_options)
    assert catalog["assistant"] == {
        "label": "Codex CLI（the_company API）",
        "provider": "codex_the_company",
        "model": "deepseek-v4-flash",
        "effort": "medium",
    }

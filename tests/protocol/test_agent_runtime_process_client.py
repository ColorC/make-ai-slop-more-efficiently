from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from omnicompany.runtime.agent_runtime import (
    AgentRuntimeProcessClient,
    AgentRuntimeProcessError,
)


ROOT = Path(__file__).resolve().parents[2]
RUN_FIXTURE = json.loads(
    (ROOT / "contracts/agent-runtime/v0/fixtures/valid/run-start-readonly.json").read_text(
        encoding="utf-8"
    )
)


def test_default_schema_prefers_packaged_contract(tmp_path: Path, monkeypatch) -> None:
    from omnicompany.runtime.agent_runtime import client as client_module

    repo = tmp_path / "repo"
    module_file = repo / "src/omnicompany/runtime/agent_runtime/client.py"
    packaged = module_file.parent / "contracts/v0/agent-runtime.schema.json"
    packaged.parent.mkdir(parents=True)
    packaged.write_text(json.dumps({
        "type": "object",
        "required": ["kind"],
        "properties": {"kind": {"const": "packaged-contract"}},
        "additionalProperties": False,
    }), encoding="utf-8")
    source = repo / "contracts/agent-runtime/v0/agent-runtime.schema.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"type": "null"}', encoding="utf-8")
    monkeypatch.setattr(client_module, "__file__", str(module_file))

    assert client_module._default_schema_path() == packaged
    client = AgentRuntimeProcessClient(["unused-fixture-runtime"])
    client.validate({"kind": "packaged-contract"})
    with pytest.raises(AgentRuntimeProcessError, match="contract violation"):
        client.validate(None)
    assert client._process is None


def test_default_schema_falls_back_to_source_contract(tmp_path: Path, monkeypatch) -> None:
    from omnicompany.runtime.agent_runtime import client as client_module

    repo = tmp_path / "repo"
    module_file = repo / "src/omnicompany/runtime/agent_runtime/client.py"
    source = repo / "contracts/agent-runtime/v0/agent-runtime.schema.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"type": "null"}', encoding="utf-8")
    monkeypatch.setattr(client_module, "__file__", str(module_file))

    assert client_module._default_schema_path() == source
    client = AgentRuntimeProcessClient(["unused-fixture-runtime"])
    client.validate(None)
    with pytest.raises(AgentRuntimeProcessError, match="contract violation"):
        client.validate({"kind": "packaged-contract"})
    assert client._process is None


FAKE_HOST = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message['kind'] == 'runtime.capabilities.request':
        print(json.dumps({
            'kind': 'runtime.capabilities',
            'protocol_version': '0.1.0',
            'request_id': message['request_id'],
            'runtime': {
                'runtime_id': 'python-fixture-host',
                'implementation': 'contract-fixture',
                'implementation_version': '1',
            },
            'supported_protocol_versions': ['0.1.0'],
            'features': {
                'streaming': False,
                'session_resume': False,
                'context_fork': False,
                'approval': False,
                'cancel': 'process',
                'parallel_runs': False,
            },
            'limits': {'max_parallel_runs': 1},
        }), flush=True)
    elif message['kind'] == 'run.start':
        base = {
            'protocol_version': '0.1.0',
            'run_id': message['run_id'],
            'sequence': 0,
            'timestamp': '2026-08-14T00:00:00Z',
        }
        print(json.dumps({**base, 'kind': 'run.state', 'state': 'starting'}), flush=True)
        print(json.dumps({
            'kind': 'run.result',
            'protocol_version': '0.1.0',
            'request_id': message['request_id'],
            'run_id': message['run_id'],
            'session_id': message['session_id'],
            'status': 'succeeded',
            'final_output': {'text': 'fixture host complete'},
            'material_refs': [],
            'artifact_refs': [],
            'usage': {'duration_ms': 1},
        }), flush=True)
"""

FAKE_CANCEL_HOST = r"""
import json, sys
active = {}
for line in sys.stdin:
    message = json.loads(line)
    if message['kind'] == 'run.start':
        active[message['run_id']] = message
        print(json.dumps({
            'kind': 'run.state',
            'protocol_version': '0.1.0',
            'run_id': message['run_id'],
            'sequence': 0,
            'timestamp': '2026-08-14T00:00:00Z',
            'state': 'running',
        }), flush=True)
    elif message['kind'] == 'run.cancel':
        started = active.pop(message['run_id'])
        print(json.dumps({
            'kind': 'run.result',
            'protocol_version': '0.1.0',
            'request_id': started['request_id'],
            'run_id': started['run_id'],
            'session_id': started['session_id'],
            'status': 'cancelled',
            'final_output': {'text': ''},
            'material_refs': [],
            'artifact_refs': [],
            'usage': {'duration_ms': 1},
            'error': {
                'code': 'cancelled',
                'message': 'cancelled by fixture host',
                'retryable': False,
            },
        }), flush=True)
"""


@pytest.mark.asyncio
async def test_process_client_multiplexes_capabilities_events_and_result(tmp_path: Path) -> None:
    events: list[dict] = []
    request = json.loads(json.dumps(RUN_FIXTURE))
    request["workspace_cwd"] = str(tmp_path)
    request["capability_grant"]["allowed_read_roots"] = [str(tmp_path)]

    async with AgentRuntimeProcessClient([sys.executable, "-u", "-c", FAKE_HOST]) as client:
        capabilities = await client.capabilities("cap-client-test")
        result = await client.run(request, events.append)

    assert capabilities["runtime"]["runtime_id"] == "python-fixture-host"
    assert [event["kind"] for event in events] == ["run.state"]
    assert result["status"] == "succeeded"
    assert result["final_output"]["text"] == "fixture host complete"


@pytest.mark.asyncio
async def test_process_client_rejects_invalid_request_before_spawn(tmp_path: Path) -> None:
    request = json.loads(json.dumps(RUN_FIXTURE))
    request["workspace_cwd"] = str(tmp_path)
    request["capability_grant"]["allowed_read_roots"] = [str(tmp_path)]
    request["capability_grant"]["allowed_write_roots"] = [str(tmp_path)]
    client = AgentRuntimeProcessClient(["definitely-missing-agent-runtime"])

    with pytest.raises(AgentRuntimeProcessError, match="contract violation"):
        await client.run(request)

    await client.close()


@pytest.mark.asyncio
async def test_process_client_sends_cancel_while_run_is_active(tmp_path: Path) -> None:
    import asyncio

    request = json.loads(json.dumps(RUN_FIXTURE))
    request["request_id"] = "request-client-cancel"
    request["run_id"] = "run-client-cancel"
    request["session_id"] = "session-client-cancel"
    request["workspace_cwd"] = str(tmp_path)
    request["capability_grant"]["allowed_read_roots"] = [str(tmp_path)]
    running = asyncio.Event()

    async def observe(event: dict) -> None:
        if event["kind"] == "run.state" and event["state"] == "running":
            running.set()

    async with AgentRuntimeProcessClient(
        [sys.executable, "-u", "-c", FAKE_CANCEL_HOST]
    ) as client:
        result_task = asyncio.create_task(client.run(request, observe))
        await asyncio.wait_for(running.wait(), timeout=5)
        await client.cancel(request["run_id"], "test cancellation")
        result = await asyncio.wait_for(result_task, timeout=5)

    assert result["status"] == "cancelled"
    assert result["error"]["code"] == "cancelled"

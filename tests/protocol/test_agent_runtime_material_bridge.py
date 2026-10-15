from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from omnicompany.runtime.agent_runtime.material_bridge import (
    AgentRuntimeMaterialBridge,
    MaterialBridgeError,
    MaterialProjection,
)


class _Reader:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def read(self, material_ref: str) -> MaterialProjection:
        self.reads.append(material_ref)
        return MaterialProjection(
            material_ref=material_ref,
            revision="revision:1",
            content={"body": "canonical only"},
        )


def _grant(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "grant_id": "grant:material-test",
        "policy_ref": "policy:material-readonly",
        "allowed_material_refs": ["material:allowed"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    value.update(overrides)
    return value


def _read(
    url: str,
    token: str,
    *,
    material_ref: str = "material:allowed",
    grant_id: str = "grant:material-test",
    policy_ref: str = "policy:material-readonly",
) -> tuple[int, dict[str, object]]:
    request = Request(
        url,
        data=json.dumps({"material_ref": material_ref}).encode(),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "x-omni-grant-id": grant_id,
            "x-omni-policy-ref": policy_ref,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310 - loopback test URL
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_bridge_reads_direct_authority_with_exact_grant() -> None:
    reader = _Reader()
    with AgentRuntimeMaterialBridge(reader) as bridge:
        access = bridge.grant(_grant())
        status, body = _read(access.bridge_url, access.bridge_token)

    assert status == 200
    assert body == {
        "material_ref": "material:allowed",
        "revision": "revision:1",
        "content": {"body": "canonical only"},
    }
    assert reader.reads == ["material:allowed"]
    assert access.bridge_url.startswith("http://127.0.0.1:")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"token": "wrong"}, "invalid_token"),
        ({"grant_id": "grant:wrong"}, "unknown_grant"),
        ({"policy_ref": "policy:wrong"}, "policy_mismatch"),
        ({"material_ref": "material:outside"}, "material_ref_not_granted"),
    ],
)
def test_bridge_denies_before_authority_read(changes: dict[str, str], error: str) -> None:
    reader = _Reader()
    with AgentRuntimeMaterialBridge(reader) as bridge:
        access = bridge.grant(_grant())
        status, body = _read(
            access.bridge_url,
            changes.get("token", access.bridge_token),
            material_ref=changes.get("material_ref", "material:allowed"),
            grant_id=changes.get("grant_id", "grant:material-test"),
            policy_ref=changes.get("policy_ref", "policy:material-readonly"),
        )

    assert status == 403
    assert body == {"error": error}
    assert reader.reads == []


def test_bridge_rejects_expired_grant_before_start() -> None:
    bridge = AgentRuntimeMaterialBridge(_Reader())
    with pytest.raises(MaterialBridgeError, match="expired"):
        bridge.grant(
            _grant(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        )
    bridge.close()


def test_bridge_revocation_is_immediate() -> None:
    reader = _Reader()
    with AgentRuntimeMaterialBridge(reader) as bridge:
        access = bridge.grant(_grant())
        bridge.revoke("grant:material-test")
        status, body = _read(access.bridge_url, access.bridge_token)

    assert status == 403
    assert body == {"error": "unknown_grant"}
    assert reader.reads == []

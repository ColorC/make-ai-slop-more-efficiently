"""Native LOFA device-identity carryover for LAN access approvals."""
from __future__ import annotations

from typing import Any


def _bind_pending_lofa_device_id(
    device: dict[str, Any],
    pending: dict[str, Any],
) -> None:
    """Carry the native LOFA identity from an approved registration request."""
    if str(pending.get("source") or "") != "lofa_native_register":
        return
    fingerprint = (
        pending.get("fingerprint")
        if isinstance(pending.get("fingerprint"), dict)
        else {}
    )
    native_device_id = str(fingerprint.get("native_device_id") or "").strip()
    if not native_device_id:
        return
    device_ids = device.setdefault("lofa_device_ids", [])
    if not isinstance(device_ids, list):
        device_ids = []
        device["lofa_device_ids"] = device_ids
    if native_device_id not in device_ids:
        device_ids.append(native_device_id)

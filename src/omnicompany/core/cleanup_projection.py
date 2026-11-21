"""Authenticated, exact-target archive input for a remote normal cleanup CLI.

This is a task input, never a project registry. Native Git identifies its source;
an ephemeral Ed25519 signature protects the transferred scope from modification.
The caller pins the separately delivered public key, not a key inside the input.
"""
from __future__ import annotations

import base64
import json
import ntpath
import os
import re
import socket
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from omnicompany.core import dir_archive as da

SCHEMA = "omni.cleanup-archive-projection/1"


def machine_identity() -> dict[str, str]:
    if os.name != "nt":
        raise ValueError("Cleanup projections currently require Windows")
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                       0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        guid, _ = winreg.QueryValueEx(key, "MachineGuid")
    return {"computerName": socket.gethostname().casefold(), "machineGuid": str(guid).casefold()}


def _path(raw: str) -> str:
    if not isinstance(raw, str) or not da._is_abs_windows(raw) or any(c in raw for c in "*?%$`"):
        raise ValueError("Projection paths must be literal absolute Windows paths")
    if ".." in raw.replace("/", "\\").split("\\"):
        raise ValueError("Projection paths cannot contain parent traversal")
    return ntpath.normcase(ntpath.normpath(raw))


def _time(raw: str) -> datetime:
    result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Projection time must include timezone")
    return result


def validate_execution(binding: dict, target: str) -> None:
    if _path(target) != _path(binding["target"]):
        raise ValueError("Projection target mismatch")
    if not binding.get("transportNode") or binding.get("declaredHost") in (None, "", "local"):
        raise ValueError("Missing remote node/declared host binding")
    if machine_identity() != binding["machine"]:
        raise ValueError("Projection Windows machine identity mismatch")
    now = datetime.now(timezone.utc)
    if not _time(binding["createdUTC"]) <= now < _time(binding["expiresUTC"]):
        raise ValueError("Projection is not yet valid or has expired")
    if da._ancestor_links(target):
        raise ValueError("Projection target or ancestor is a link")
    record = Path(ntpath.dirname(target)) / "record.json"
    manifest = json.loads(record.read_text(encoding="utf-8-sig"))
    if (manifest.get("project") != binding["source"]["project"]
            or manifest.get("role") not in da.DELETABLE_BY_ROLE
            or not manifest.get("producer") or not manifest.get("lifecycle")):
        raise ValueError("Projection staging record ownership/role/lifecycle mismatch")
    if manifest.get("human_evidence"):
        expiry = da._parse_time(manifest.get("expires"))
        if expiry is None or expiry > now:
            raise ValueError("Projection staging record holds unexpired human evidence")


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="strict", timeout=30)
    if result.returncode:
        raise ValueError(f"Native Git failed: {result.stderr.strip()}")
    return result.stdout.strip()


def export_projection(index: Path, project: str, declared_host: str, transport_node: str,
                      machine: dict, target: str, expires: str, source_commit: str) -> tuple[dict, str]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    index = index.absolute()
    top = Path(_git(["rev-parse", "--show-toplevel"], index.parent))
    relative = index.relative_to(top).as_posix()
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("An exact native source Git commit is required")
    commit = _git(["rev-parse", "--verify", f"{source_commit}^{{commit}}"], top)
    source_text = _git(["show", f"{commit}:{relative}"], top)
    import yaml
    from omnicompany.core.projects_registry import _validate_directory_contract

    match = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|$)", source_text, re.DOTALL)
    if match is None:
        raise ValueError("Canonical index has no frontmatter")
    fm = yaml.safe_load(match.group(1))
    if not isinstance(fm, dict) or fm.get("omni_project") != project:
        raise ValueError("Canonical index project identity/schema mismatch")
    layout = _validate_directory_contract(fm, index_dir=index.parent, require_layout=True)
    if not layout.get("ok"):
        raise ValueError(f"Invalid project directory contract: {layout}")
    declarations = [da._declaration_from_entry(entry, layer="external", owner=project,
                    declared_in=f"{index}@{commit} :: directory_contract.external_roots[{i}]",
                    base=fm["directory_contract"]["canonical_root"])
                    for i, entry in enumerate(fm["directory_contract"].get("external_roots", []))]
    # Use only the exact parent declaration, never a broad ancestor permission.
    parent = ntpath.dirname(_path(target))
    selected = [d for d in declarations if d.layer == "external" and d.host == declared_host
                and _path(d.path) == parent]
    if len(selected) != 1 or selected[0].role not in da.DELETABLE_BY_ROLE:
        raise ValueError("Target parent must have one exact external tmp/cache declaration")
    declaration = selected[0]
    if declaration.status != "active" or not declaration.lifecycle:
        raise ValueError("Projection requires an active declaration with a lifecycle")
    relevant = [d for d in declarations if d.host == declared_host
                and (da._within(_path(target), _path(d.path)) or da._within(_path(d.path), _path(target)))]
    if da._conflicts(relevant) or any(d.role in da.FORMAL_ROLES and
            da._within(_path(d.path), _path(target)) for d in relevant):
        raise ValueError("Projection target conflicts with a formal directory declaration")
    now = datetime.now(timezone.utc)
    expiry = _time(expires)
    if not 0 < (expiry-now).total_seconds() <= 86400:
        raise ValueError("Projection validity must be positive and at most 24 hours")
    if set(machine) != {"computerName", "machineGuid"} or not all(machine.values()):
        raise ValueError("Both actual Windows machine name and MachineGuid are required")
    payload = {"schema": SCHEMA, "source": {"indexPath": str(index), "gitRoot": str(top),
               "gitCommit": commit, "gitRelativePath": relative, "project": project},
               "declaration": asdict(declaration),
               "contextDeclarations": [asdict(d) for d in relevant if d != declaration], "binding": {
               "target": target, "declaredHost": declared_host, "transportNode": transport_node,
               "machine": {k: v.casefold() for k, v in machine.items()},
               "createdUTC": now.isoformat(), "expiresUTC": expiry.isoformat()}}
    key = Ed25519PrivateKey.generate()
    signature = base64.b64encode(key.sign(_canonical(payload))).decode("ascii")
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return {"payload": payload, "signature": signature}, public


def load_projection(path: Path, public_key: str, target: str, transport_node: str) -> da.Archive:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if da._ancestor_links(str(path.absolute())):
        raise ValueError("Projection input or its ancestor is a link")
    envelope = json.loads(path.read_text(encoding="utf-8-sig"))
    if set(envelope) != {"payload", "signature"}:
        raise ValueError("Unexpected projection envelope fields")
    payload = envelope["payload"]
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            base64.b64decode(envelope["signature"], validate=True), _canonical(payload))
    except Exception as error:
        raise ValueError("Projection signature does not match the independently pinned key") from error
    if payload.get("schema") != SCHEMA:
        raise ValueError("Unsupported projection schema")
    binding = payload["binding"]
    if _path(target) != _path(binding["target"]):
        raise ValueError("Projection target mismatch")
    if binding["transportNode"] != transport_node:
        raise ValueError("Projection transport node mismatch")
    source = payload["source"]
    if not re.fullmatch(r"[0-9a-f]{40}", source.get("gitCommit", "")):
        raise ValueError("Projection source requires a native Git commit")
    declaration = da.Declaration(**payload["declaration"])
    if (declaration.host != binding["declaredHost"] or declaration.layer != "external"
            or declaration.owner != source["project"] or declaration.role not in da.DELETABLE_BY_ROLE
            or declaration.status != "active" or not declaration.lifecycle
            or _path(declaration.path) != ntpath.dirname(_path(target))):
        raise ValueError("Projection declaration does not authorize the exact target parent")
    binding = dict(binding, source=source, projectionPath=str(path.absolute()))
    validate_execution(binding, target)
    context = [da.Declaration(**d) for d in payload.get("contextDeclarations", [])]
    if any(d.host != declaration.host or d.owner != declaration.owner or
           not (da._within(_path(target), _path(d.path)) or da._within(_path(d.path), _path(target)))
           for d in context):
        raise ValueError("Projection context is outside the bound source scope")
    return da.Archive([*context, declaration], [], str(path.absolute().parent), execution_binding=binding)

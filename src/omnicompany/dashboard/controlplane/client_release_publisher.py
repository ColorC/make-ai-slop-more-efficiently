"""Build-output validator and atomic publisher for Omnicompany Desktop."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clients_core import CATALOG_SCHEMA_VERSION, semver_key


class DesktopPublishError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authenticode_status(path: Path) -> dict[str, str | None]:
    script = """
$signature = Get-AuthenticodeSignature -LiteralPath $env:OMNI_AUTHENTICODE_TARGET
[pscustomobject]@{
  status = [string]$signature.Status
  publisher = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
  thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }
} | ConvertTo-Json -Compress
"""
    environment = os.environ.copy()
    environment["OMNI_AUTHENTICODE_TARGET"] = str(path.resolve())
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    if result.returncode != 0:
        raise DesktopPublishError(
            f"Get-AuthenticodeSignature failed for {path.name}: {result.stderr.strip()}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DesktopPublishError(
            f"Get-AuthenticodeSignature returned invalid JSON for {path.name}"
        ) from exc
    return {
        "status": str(value.get("status") or "UnknownError"),
        "publisher": str(value.get("publisher") or "") or None,
        "thumbprint": str(value.get("thumbprint") or "") or None,
    }


def desktop_version(desktop_root: Path) -> tuple[str, str, str]:
    package = json.loads((desktop_root / "package.json").read_text(encoding="utf-8"))
    tauri = json.loads(
        (desktop_root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    cargo_text = (desktop_root / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    cargo_version = ""
    in_package = False
    for line in cargo_text.splitlines():
        if line.strip() == "[package]":
            in_package = True
            continue
        if in_package and line.startswith("["):
            break
        if in_package and line.strip().startswith("version ="):
            cargo_version = line.split("=", 1)[1].strip().strip('"')
            break
    versions = (str(package.get("version") or ""), str(tauri.get("version") or ""), cargo_version)
    for version in versions:
        semver_key(version)
    if len(set(versions)) != 1:
        raise DesktopPublishError(
            "desktop package.json, tauri.conf.json and Cargo.toml versions must match"
        )
    return versions


def _single_artifact(directory: Path, version: str, suffix: str) -> Path:
    candidates = [
        path
        for path in directory.glob(f"*{suffix}")
        if version in path.name and not path.name.endswith(".sig")
    ]
    if len(candidates) != 1:
        raise DesktopPublishError(
            f"expected exactly one {suffix} artifact for {version}, found {len(candidates)}"
        )
    artifact = candidates[0]
    signature = Path(f"{artifact}.sig")
    if not signature.is_file() or not signature.read_text(encoding="utf-8").strip():
        raise DesktopPublishError(f"Tauri updater signature is missing for {artifact.name}")
    return artifact


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _atomic_write_catalog(output_root: Path, catalog: dict[str, Any]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = output_root / f".catalog.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_root / "catalog.json")


def _asset_record(artifact: Path, destination_dir: Path) -> dict[str, Any]:
    signature_source = Path(f"{artifact}.sig")
    signature_name = f"{artifact.name}.sig"
    _atomic_copy(artifact, destination_dir / artifact.name)
    _atomic_copy(signature_source, destination_dir / signature_name)
    return {
        "filename": artifact.name,
        "signatureFilename": signature_name,
        "size": artifact.stat().st_size,
        "sha256": sha256_file(artifact),
        "updaterSignature": signature_source.read_text(encoding="utf-8").strip(),
        "authenticode": authenticode_status(artifact),
    }


def publish_desktop_release(
    *,
    desktop_root: Path,
    bundle_root: Path,
    output_root: Path,
    channel: str,
    updater_public_key: str,
    notes: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if channel not in {"stable", "canary"}:
        raise DesktopPublishError("desktop release channel must be stable or canary")
    if not updater_public_key.strip():
        raise DesktopPublishError("updater public key is required")
    version = desktop_version(desktop_root)[0]
    catalog_file = output_root / "catalog.json"
    existing: dict[str, Any] = {}
    if catalog_file.is_file():
        try:
            existing = json.loads(catalog_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DesktopPublishError("existing desktop release catalog is invalid") from exc
        old_key = str(existing.get("updaterPublicKey") or "")
        if old_key and old_key != updater_public_key.strip():
            raise DesktopPublishError("updater public key rotation requires an explicit migration")
        old_channel = str(existing.get("channel") or "")
        if old_channel and old_channel != channel:
            raise DesktopPublishError("release channels require separate publication roots")
        if any(
            isinstance(item, dict) and item.get("version") == version
            for item in existing.get("releases", [])
        ):
            raise DesktopPublishError(
                f"desktop release {version} is already published and immutable; bump the version"
            )

    nsis = _single_artifact(bundle_root / "nsis", version, ".exe")
    msi = _single_artifact(bundle_root / "msi", version, ".msi")
    published_at = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    destination_dir = output_root / channel / version
    release = {
        "version": version,
        "publishedAt": published_at,
        "notes": notes,
        "status": "active",
        "assets": {
            "nsis": _asset_record(nsis, destination_dir),
            "msi": _asset_record(msi, destination_dir),
        },
    }

    releases = [
        item
        for item in existing.get("releases", [])
        if isinstance(item, dict) and item.get("version") != version
    ]
    releases.append(release)
    releases.sort(key=lambda item: semver_key(str(item["version"])), reverse=True)
    catalog = {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "channel": channel,
        "generatedAt": published_at,
        "updaterPublicKey": updater_public_key.strip(),
        "releases": releases,
    }
    _atomic_write_catalog(output_root, catalog)
    return catalog


def withdraw_desktop_releases(
    *,
    output_root: Path,
    versions: list[str],
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Withdraw eligibility without deleting or mutating immutable release assets."""
    if not versions:
        raise DesktopPublishError("at least one desktop release version is required")
    if not reason.strip():
        raise DesktopPublishError("desktop release withdrawal reason is required")
    for version in versions:
        semver_key(version)
    catalog_file = output_root / "catalog.json"
    if not catalog_file.is_file():
        raise DesktopPublishError("desktop release catalog has not been published")
    try:
        catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DesktopPublishError("existing desktop release catalog is invalid") from exc
    releases = catalog.get("releases")
    if not isinstance(releases, list):
        raise DesktopPublishError("existing desktop release catalog has no releases")
    requested = set(versions)
    found = {
        str(item.get("version"))
        for item in releases
        if isinstance(item, dict) and str(item.get("version")) in requested
    }
    missing = sorted(requested - found, key=semver_key)
    if missing:
        raise DesktopPublishError(
            f"desktop release versions were not published: {', '.join(missing)}"
        )
    withdrawn_at = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    for item in releases:
        if not isinstance(item, dict) or str(item.get("version")) not in requested:
            continue
        item["status"] = "withdrawn"
        item["withdrawnAt"] = withdrawn_at
        item["withdrawalReason"] = reason.strip()
    catalog["generatedAt"] = withdrawn_at
    _atomic_write_catalog(output_root, catalog)
    return catalog

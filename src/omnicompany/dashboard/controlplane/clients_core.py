"""Validated, file-backed release catalog for downloadable clients.

The publisher is the only writer.  Dashboard requests only read the atomically
published catalog and serve files named by that catalog; it never scans a
directory and never accepts a filesystem path from the caller.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CATALOG_SCHEMA_VERSION = 1
SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_CHANNELS = {"stable", "canary"}
ALLOWED_ASSETS = {"nsis", "msi"}
ALLOWED_RELEASE_STATUSES = {"active", "withdrawn"}
ALLOWED_PRODUCTS = {"desktop", "litework-desktop"}


class ClientCatalogError(ValueError):
    """The release catalog is absent or violates the on-disk contract."""


def release_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "data" / "services" / "client_releases" / "desktop"


def product_release_root(product: str = "desktop") -> Path:
    if product not in ALLOWED_PRODUCTS:
        raise ClientCatalogError("desktop product is invalid")
    root = release_root()
    return root if product == "desktop" else root.parent / product


def catalog_path(product: str = "desktop") -> Path:
    return product_release_root(product) / "catalog.json"


def semver_key(version: str) -> tuple[Any, ...]:
    match = SEMVER_RE.fullmatch(str(version).strip())
    if not match:
        raise ClientCatalogError(f"invalid semantic version: {version!r}")
    prerelease = match.group("pre")
    pre_key: tuple[tuple[int, int | str], ...] = ()
    if prerelease:
        pre_key = tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in prerelease.split(".")
        )
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        prerelease is None,
        pre_key,
    )


def _validated_asset(asset_kind: str, value: object) -> dict[str, Any]:
    if asset_kind not in ALLOWED_ASSETS or not isinstance(value, dict):
        raise ClientCatalogError(f"invalid desktop asset: {asset_kind}")
    filename = str(value.get("filename") or "")
    if not filename or filename != Path(filename).name:
        raise ClientCatalogError("asset filename must be a basename")
    sha256 = str(value.get("sha256") or "").lower()
    if not SHA256_RE.fullmatch(sha256):
        raise ClientCatalogError("asset sha256 is invalid")
    signature = str(value.get("updaterSignature") or "").strip()
    if not signature:
        raise ClientCatalogError("Tauri updater signature is required")
    size = int(value.get("size") or 0)
    if size <= 0:
        raise ClientCatalogError("asset size must be positive")
    authenticode = value.get("authenticode")
    if not isinstance(authenticode, dict) or not str(authenticode.get("status") or ""):
        raise ClientCatalogError("asset Authenticode status is required")
    return {
        "filename": filename,
        "size": size,
        "sha256": sha256,
        "updaterSignature": signature,
        "authenticode": {
            "status": str(authenticode["status"]),
            "publisher": str(authenticode.get("publisher") or "") or None,
            "thumbprint": str(authenticode.get("thumbprint") or "") or None,
        },
    }

def load_catalog(product: str = "desktop") -> dict[str, Any]:
    path = catalog_path(product)
    if not path.is_file():
        raise ClientCatalogError("desktop release catalog has not been published")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientCatalogError("desktop release catalog is unreadable") from exc
    if not isinstance(raw, dict) or raw.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
        raise ClientCatalogError("desktop release catalog schema is unsupported")
    channel = str(raw.get("channel") or "")
    if channel not in ALLOWED_CHANNELS:
        raise ClientCatalogError("desktop release channel is invalid")
    releases_raw = raw.get("releases")
    if not isinstance(releases_raw, list) or not releases_raw:
        raise ClientCatalogError("desktop release catalog is empty")

    releases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in releases_raw:
        if not isinstance(item, dict):
            raise ClientCatalogError("desktop release entry is invalid")
        version = str(item.get("version") or "")
        semver_key(version)
        if version in seen:
            raise ClientCatalogError("desktop release version is duplicated")
        seen.add(version)
        assets_raw = item.get("assets")
        if not isinstance(assets_raw, dict) or "nsis" not in assets_raw:
            raise ClientCatalogError("desktop release requires a signed NSIS asset")
        assets = {
            kind: _validated_asset(kind, value)
            for kind, value in assets_raw.items()
        }
        status = str(item.get("status") or "active")
        if status not in ALLOWED_RELEASE_STATUSES:
            raise ClientCatalogError("desktop release status is invalid")
        withdrawn_at = str(item.get("withdrawnAt") or "") or None
        withdrawal_reason = str(item.get("withdrawalReason") or "") or None
        if status == "withdrawn" and (not withdrawn_at or not withdrawal_reason):
            raise ClientCatalogError("withdrawn desktop release requires audit metadata")
        releases.append({
            "version": version,
            "publishedAt": str(item.get("publishedAt") or ""),
            "notes": str(item.get("notes") or ""),
            "status": status,
            "withdrawnAt": withdrawn_at,
            "withdrawalReason": withdrawal_reason,
            "assets": assets,
        })
    releases.sort(key=lambda item: semver_key(item["version"]), reverse=True)
    return {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "channel": channel,
        "generatedAt": str(raw.get("generatedAt") or ""),
        "updaterPublicKey": str(raw.get("updaterPublicKey") or ""),
        "releases": releases,
    }


def active_releases(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [release for release in catalog["releases"] if release["status"] == "active"]


def find_release(
    catalog: dict[str, Any], version: str, *, include_withdrawn: bool = False
) -> dict[str, Any]:
    for release in catalog["releases"]:
        if release["version"] == version:
            if release["status"] == "withdrawn" and not include_withdrawn:
                raise ClientCatalogError("desktop release was withdrawn")
            return release
    raise ClientCatalogError("desktop release version was not published")


def release_asset_path(
    catalog: dict[str, Any], version: str, asset_kind: str, product: str = "desktop"
) -> tuple[dict[str, Any], Path]:
    if asset_kind not in ALLOWED_ASSETS:
        raise ClientCatalogError("desktop release asset kind is invalid")
    release = find_release(catalog, version)
    asset = release["assets"].get(asset_kind)
    if not asset:
        raise ClientCatalogError("desktop release asset was not published")
    path = product_release_root(product) / catalog["channel"] / version / asset["filename"]
    if not path.is_file() or path.stat().st_size != asset["size"]:
        raise ClientCatalogError("desktop release asset is missing or has changed")
    return asset, path


def _public_release(catalog: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    public_assets: dict[str, Any] = {}
    for kind, asset in release["assets"].items():
        public_assets[kind] = {
            **asset,
            "downloadUrl": (
                f"/api/clients/desktop/{catalog['channel']}/{release['version']}/{kind}"
            ),
            "signatureVerifiedByClient": True,
        }
    return {
        "version": release["version"],
        "publishedAt": release["publishedAt"],
        "notes": release["notes"],
        "assets": public_assets,
    }


def public_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    eligible = active_releases(catalog)
    if not eligible:
        raise ClientCatalogError("desktop release catalog has no active release")
    releases = [_public_release(catalog, release) for release in eligible[:2]]
    return {
        "ok": True,
        "desktop": {
            "available": True,
            "channel": catalog["channel"],
            "generatedAt": catalog["generatedAt"],
            "latest": releases[0],
            "previous": releases[1] if len(releases) > 1 else None,
            "retainedReleaseCount": len(catalog["releases"]),
            "withdrawnReleaseCount": len(catalog["releases"]) - len(eligible),
            "updaterSignatureRequired": True,
        },
    }

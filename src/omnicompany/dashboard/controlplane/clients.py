"""Download catalog and signed Tauri updater endpoints for first-party clients."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .clients_core import (
    ALLOWED_CHANNELS,
    ClientCatalogError,
    active_releases,
    find_release,
    load_catalog,
    public_catalog,
    release_asset_path,
    semver_key,
)


clients_router = APIRouter(prefix="/api/clients", tags=["client-releases"])


def _desktop_asset_response(
    catalog: dict, version: str, asset_kind: str, product: str = "desktop"
) -> Response:
    asset, path = release_asset_path(catalog, version, asset_kind, product)
    media_type = (
        "application/x-msdownload"
        if asset_kind == "nsis"
        else "application/x-msi"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=asset["filename"],
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "X-Omnicompany-SHA256": asset["sha256"],
        },
    )


@clients_router.get("/catalog")
def client_catalog() -> JSONResponse:
    try:
        payload = public_catalog(load_catalog())
    except ClientCatalogError as exc:
        payload = {
            "ok": True,
            "desktop": {
                "available": False,
                "reason": str(exc),
                "latest": None,
                "previous": None,
                "updaterSignatureRequired": True,
            },
        }
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@clients_router.get("/desktop/update/{channel}/{target}/{arch}/{current_version}")
def desktop_update(
    request: Request,
    channel: str,
    target: str,
    arch: str,
    current_version: str,
    version: str | None = Query(default=None),
) -> Response:
    return _desktop_update_response(
        request, "desktop", channel, target, arch, current_version, version
    )


def _desktop_update_response(
    request: Request,
    product: str,
    channel: str,
    target: str,
    arch: str,
    current_version: str,
    version: str | None,
) -> Response:
    if channel not in ALLOWED_CHANNELS or target != "windows" or arch != "x86_64":
        return Response(status_code=204)
    try:
        catalog = load_catalog(product)
        if catalog["channel"] != channel:
            return Response(status_code=204)
        semver_key(current_version)
        eligible = active_releases(catalog)
        if not eligible:
            return Response(status_code=204)
        if version:
            try:
                release = find_release(catalog, version)
            except ClientCatalogError as exc:
                if "withdrawn" in str(exc):
                    return Response(status_code=204)
                raise
        else:
            release = eligible[0]
        if release["version"] == current_version:
            return Response(status_code=204)
        if version is None and semver_key(release["version"]) <= semver_key(current_version):
            return Response(status_code=204)
        asset, _path = release_asset_path(
            catalog, release["version"], "nsis", product
        )
        relative_url = (
            f"api/clients/{product}/{channel}/{release['version']}/nsis"
        )
        payload = {
            "version": release["version"],
            "pub_date": release["publishedAt"] or None,
            "url": f"{str(request.base_url).rstrip('/')}/{relative_url}",
            "signature": asset["updaterSignature"],
            "notes": release["notes"],
        }
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    except ClientCatalogError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@clients_router.get(
    "/litework-desktop/update/{channel}/{target}/{arch}/{current_version}"
)
def litework_desktop_update(
    request: Request,
    channel: str,
    target: str,
    arch: str,
    current_version: str,
    version: str | None = Query(default=None),
) -> Response:
    return _desktop_update_response(
        request, "litework-desktop", channel, target, arch, current_version, version
    )


@clients_router.api_route("/desktop/latest/nsis", methods=["GET", "HEAD"])
def desktop_latest_download() -> Response:
    try:
        catalog = load_catalog()
        releases = active_releases(catalog)
        if not releases:
            raise ClientCatalogError("desktop release catalog has no active release")
        return _desktop_asset_response(catalog, releases[0]["version"], "nsis")
    except ClientCatalogError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@clients_router.api_route(
    "/desktop/{channel}/{version}/{asset_kind}", methods=["GET", "HEAD"]
)
def desktop_download(channel: str, version: str, asset_kind: str) -> Response:
    try:
        catalog = load_catalog()
        if catalog["channel"] != channel:
            raise ClientCatalogError("desktop release channel was not published")
        return _desktop_asset_response(catalog, version, asset_kind)
    except ClientCatalogError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@clients_router.api_route(
    "/litework-desktop/{channel}/{version}/{asset_kind}", methods=["GET", "HEAD"]
)
def litework_desktop_download(
    channel: str, version: str, asset_kind: str
) -> Response:
    try:
        catalog = load_catalog("litework-desktop")
        if catalog["channel"] != channel:
            raise ClientCatalogError("LiTeWork desktop release channel was not published")
        return _desktop_asset_response(
            catalog, version, asset_kind, "litework-desktop"
        )
    except ClientCatalogError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

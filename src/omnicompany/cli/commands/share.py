"""CLI client for the Slip content-share manager.

The command talks to the same HTTP API as the Dashboard instead of importing
the manager store, so CLI behavior matches real clients and service failures
stay visible.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import click


DEFAULT_MANAGER_URL = "http://127.0.0.1:8210"
DEFAULT_TIMEOUT_SECONDS = 2.0
TYPE_CODES = ("ui", "pg", "tm", "sn", "cd", "cv", "wt", "mt")
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ShareRequestError(RuntimeError):
    """A bounded manager request failed."""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status: int | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status = status
        self.response = response

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "error": "manager_request_failed",
            "message": str(self),
            "url": self.url,
        }
        if self.status is not None:
            result["status"] = self.status
        if self.response is not None:
            result["response"] = self.response
        return result


def _manager_url(value: str | None = None) -> str:
    return (value or os.environ.get("OMNI_CONTENT_SHARE_URL") or DEFAULT_MANAGER_URL).strip().rstrip("/")


def _api_base(manager_url: str) -> str:
    suffix = "/api/content-share/v1"
    return manager_url if manager_url.endswith(suffix) else manager_url + suffix


def _request_json(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
) -> Any:
    url = _api_base(config["manager_url"]) + path
    headers = {"Accept": "application/json"}
    data = body
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _DIRECT_OPENER.open(request, timeout=config["timeout"]) as response:  # noqa: S310 - configured manager
            raw = response.read()
    except urllib.error.HTTPError as error:
        raw = error.read()
        parsed = _parse_response(raw)
        detail = _error_message(parsed) or str(error.reason or error)
        raise ShareRequestError(
            f"manager returned HTTP {error.code}: {detail}",
            url=url,
            status=error.code,
            response=parsed,
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise ShareRequestError(f"cannot reach content-share manager: {reason}", url=url) from error
    if not raw:
        return {}
    parsed = _parse_response(raw)
    if isinstance(parsed, str):
        raise ShareRequestError("manager returned a non-JSON response", url=url, response=parsed[:500])
    return parsed


def _parse_response(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")


def _error_message(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    detail = value.get("detail", value)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or "") or None
    return str(detail) if detail else None


def _share_context(ctx: click.Context) -> dict[str, Any]:
    current: click.Context | None = ctx
    while current is not None:
        if isinstance(current.obj, dict) and "manager_url" in current.obj:
            return current.obj
        current = current.parent
    return {}


def _as_json(ctx: click.Context, local: bool) -> bool:
    return bool(local or _share_context(ctx).get("json"))


def _config(ctx: click.Context) -> dict[str, Any]:
    return _share_context(ctx)


def _emit(value: Any, *, as_json: bool, human: str | None = None) -> None:
    if as_json:
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))
    elif human is not None:
        click.echo(human)
    elif isinstance(value, str):
        click.echo(value)
    else:
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _fail(ctx: click.Context, error: Exception, *, as_json: bool) -> None:
    if isinstance(error, ShareRequestError):
        payload = error.as_dict()
    else:
        payload = {"ok": False, "error": "invalid_input", "message": str(error)}
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(f"Error: {payload['message']}", err=True)
    ctx.exit(1)


def _share_reference(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if "s" not in parts:
            raise ValueError("share URL must contain /s/<share-id>")
        index = parts.index("s")
        if index + 1 >= len(parts):
            raise ValueError("share URL is missing its share id")
        return urllib.parse.unquote(parts[index + 1])
    return value.strip()


def _resource_key(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() or char in "._-" else "-" for char in value)
    normalized = normalized.strip("-._") or "primary"
    if not normalized[0].isalnum():
        normalized = "primary-" + normalized
    return normalized[:128]


def _guess_media_type(path: Path) -> str:
    stable_types = {
        ".csv": "text/csv",
        ".htm": "text/html",
        ".html": "text/html",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".md": "text/markdown",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".txt": "text/plain",
    }
    return stable_types.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _deterministic_share_id(item: dict[str, Any]) -> str:
    type_code = str(item.get("type") or "")
    producer = item.get("producer") if isinstance(item.get("producer"), dict) else {}
    source_ref = str(producer.get("source_ref") or item.get("source_ref") or "")
    if type_code not in TYPE_CODES or not source_ref:
        raise ValueError("manifest items without share_id require type and producer.source_ref")
    digest = hashlib.sha256(f"{type_code}\0{source_ref}".encode("utf-8")).hexdigest()[:24]
    return f"{type_code}-{digest}"


def _normalize_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read manifest: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON manifest at line {error.lineno}, column {error.colno}") from error

    defaults: dict[str, Any] = {}
    if isinstance(document, list):
        raw_items = document
    elif isinstance(document, dict):
        defaults = document.get("defaults") if isinstance(document.get("defaults"), dict) else {}
        raw_items = document.get("shares", document.get("items"))
    else:
        raw_items = None
    if not isinstance(raw_items, list):
        raise ValueError("manifest must be a JSON array or an object with shares/items")

    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"manifest item {index} must be an object")
        item = dict(defaults)
        item.update(raw)
        default_producer = defaults.get("producer") if isinstance(defaults.get("producer"), dict) else {}
        raw_producer = raw.get("producer") if isinstance(raw.get("producer"), dict) else {}
        if default_producer or raw_producer:
            item["producer"] = {**default_producer, **raw_producer}
        if not item.get("share_id") and not item.get("opaque_id"):
            item["share_id"] = _deterministic_share_id(item)
        items.append(item)
    return items


_TRUNCATED_CHARS_RE = re.compile(r"\[truncated:\s*(\d+)\s+chars omitted\]", re.IGNORECASE)
_ORIGINAL_LENGTH_RE = re.compile(r"原长\s*(\d+)")


def _capture_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root().parent / "captures"


def _capture_title(path: Path, content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()[:200] or path.stem
    return path.stem[:200]


def _capture_migration_item(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = path.read_bytes()
    content = raw.decode("utf-8")
    lowered = content.lower()
    type_code = "pg" if any(
        marker in lowered
        for marker in ("页面 dom 结构", "页面文本快照", "dom_snapshot", "page_snapshot")
    ) else "ui"
    digest = hashlib.sha256(raw).hexdigest()
    missing_segments = [int(value) for value in _TRUNCATED_CHARS_RE.findall(content)]
    original_lengths = [int(value) for value in _ORIGINAL_LENGTH_RE.findall(content)]
    integrity = "unrecoverable" if missing_segments or original_lengths else "complete-as-stored"
    share_id = f"{type_code}-capture-{hashlib.sha256(path.name.encode('utf-8')).hexdigest()[:20]}"
    metadata = {
        "migration_state": "unrecoverable" if integrity == "unrecoverable" else "migrated",
        "integrity": integrity,
        "original_filename": path.name,
        "missing_segments": missing_segments,
        "original_lengths": original_lengths,
        "points_to_missing": "points_to=none" in content,
        "content_sha256": digest,
    }
    payload = {
        "type": type_code,
        "share_id": share_id,
        "title": _capture_title(path, content),
        "summary": (
            "Historical capture preserved with known irreversible truncation."
            if integrity == "unrecoverable"
            else "Historical capture migrated without rewriting its source file."
        ),
        "status": "capturing",
        "access": "lan-readonly",
        "source_revision": f"sha256:{digest}",
        "producer": {
            "product": "omnicompany",
            "adapter": "omnicompany.capture-migration",
            "resolver": "omni-content-share-manager",
            "source_ref": f"capture:{path.name}",
            "mode": "frozen",
        },
        "aliases": [f"capture:{path.name}", path.name],
        "metadata": metadata,
    }
    report = {
        "file": path.name,
        "share_id": share_id,
        "migration_state": metadata["migration_state"],
        "integrity": integrity,
        "missing_segments": missing_segments,
        "original_lengths": original_lengths,
        "points_to_missing": metadata["points_to_missing"],
        "content_sha256": digest,
        "bytes": len(raw),
    }
    return payload, report


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


@click.group("share")
@click.option("--url", "manager_url", envvar="OMNI_CONTENT_SHARE_URL", default=DEFAULT_MANAGER_URL, show_default=True, help="Content-share manager origin or API base.")
@click.option("--timeout", type=click.FloatRange(min=0.05), default=DEFAULT_TIMEOUT_SECONDS, show_default=True, help="Per-request timeout in seconds.")
@click.option("--json", "group_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def cmd_share(ctx: click.Context, manager_url: str, timeout: float, group_json: bool) -> None:
    """Publish, resolve, inspect, and maintain Slip shares."""
    ctx.ensure_object(dict)
    ctx.obj.update(manager_url=_manager_url(manager_url), timeout=timeout, json=group_json)


@cmd_share.command("publish")
@click.argument("type_code_arg", required=False, type=click.Choice(TYPE_CODES))
@click.argument("source_ref_arg", required=False)
@click.option("--type", "type_code_opt", type=click.Choice(TYPE_CODES), help="Share type code.")
@click.option("--source-ref", help="Stable producer-owned source reference.")
@click.option("--title", help="Human-readable title; defaults to the file name or source reference.")
@click.option("--product", default="omnicompany", show_default=True)
@click.option("--adapter", default="omnicompany.cli", show_default=True)
@click.option("--mode", type=click.Choice(("live", "frozen", "ephemeral")), default="frozen", show_default=True)
@click.option("--access", type=click.Choice(("lan-readonly", "private", "local-only")), default="lan-readonly", show_default=True)
@click.option("--share-id", help="Explicit stable share id.")
@click.option("--opaque-id", help="Opaque id used by the manager to construct the share id.")
@click.option("--alias", "aliases", multiple=True, help="Legacy alias to resolve to this share; repeatable.")
@click.option("--summary")
@click.option("--human-target")
@click.option("--machine-target")
@click.option("--file", "primary_file", type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True), help="Upload this file as primary content and finalize ready.")
@click.option("--media-type", help="Primary MIME type; guessed from --file when omitted.")
@click.option("--key", default="primary", show_default=True, help="Primary representation key.")
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def publish(
    ctx: click.Context,
    type_code_arg: str | None,
    source_ref_arg: str | None,
    type_code_opt: str | None,
    source_ref: str | None,
    title: str | None,
    product: str,
    adapter: str,
    mode: str,
    access: str,
    share_id: str | None,
    opaque_id: str | None,
    aliases: tuple[str, ...],
    summary: str | None,
    human_target: str | None,
    machine_target: str | None,
    primary_file: Path | None,
    media_type: str | None,
    key: str,
    local_json: bool,
) -> None:
    """Reserve a share; with --file, upload primary content and mark it ready.

    TYPE and SOURCE_REF may be positional or supplied as --type/--source-ref.
    """
    as_json = _as_json(ctx, local_json)
    type_code = type_code_opt or type_code_arg
    resolved_source_ref = source_ref or source_ref_arg
    if not type_code or not resolved_source_ref:
        _fail(ctx, ValueError("TYPE and SOURCE_REF are required"), as_json=as_json)
        return
    if type_code_opt and type_code_arg and type_code_opt != type_code_arg:
        _fail(ctx, ValueError("positional TYPE and --type disagree"), as_json=as_json)
        return
    if source_ref and source_ref_arg and source_ref != source_ref_arg:
        _fail(ctx, ValueError("positional SOURCE_REF and --source-ref disagree"), as_json=as_json)
        return

    payload: dict[str, Any] = {
        "type": type_code,
        "title": title or (primary_file.name if primary_file else resolved_source_ref),
        "status": "capturing" if primary_file is not None else "reserved",
        "access": access,
        "producer": {
            "product": product,
            "adapter": adapter,
            "resolver": "omni-content-share-manager",
            "source_ref": resolved_source_ref,
            "mode": mode,
        },
        "aliases": list(aliases),
    }
    for field, value in (
        ("share_id", share_id),
        ("opaque_id", opaque_id),
        ("summary", summary),
        ("human_target", human_target),
        ("machine_target", machine_target),
    ):
        if value is not None:
            payload[field] = value

    try:
        envelope = _request_json(_config(ctx), "POST", "/shares", payload=payload)
        if primary_file is not None:
            content = primary_file.read_bytes()
            primary_key = _resource_key(key)
            resolved_media_type = media_type or _guess_media_type(primary_file)
            query = urllib.parse.urlencode({"title": primary_file.name, "media_type": resolved_media_type})
            share_ref = urllib.parse.quote(str(envelope["share_id"]), safe="")
            envelope = _request_json(
                _config(ctx),
                "PUT",
                f"/shares/{share_ref}/content/primary/{urllib.parse.quote(primary_key, safe='')}?{query}",
                body=content,
                content_type=resolved_media_type,
            )
            envelope = _request_json(
                _config(ctx),
                "PUT",
                f"/shares/{share_ref}",
                payload={"status": "ready"},
            )
    except (ShareRequestError, OSError, KeyError) as error:
        _fail(ctx, error, as_json=as_json)
        return
    _emit(envelope, as_json=as_json, human=str(envelope.get("canonical_url") or envelope.get("share_id")))


@cmd_share.command("resolve")
@click.argument("reference")
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def resolve(ctx: click.Context, reference: str, local_json: bool) -> None:
    """Resolve a share id, alias, or canonical /s/ URL."""
    as_json = _as_json(ctx, local_json)
    try:
        share_ref = _share_reference(reference)
        envelope = _request_json(_config(ctx), "GET", f"/shares/{urllib.parse.quote(share_ref, safe='')}")
    except (ShareRequestError, ValueError) as error:
        _fail(ctx, error, as_json=as_json)
        return
    _emit(envelope, as_json=as_json, human=str(envelope.get("canonical_url") or envelope.get("share_id")))


@cmd_share.command("list")
@click.option("--type", "type_code", type=click.Choice(TYPE_CODES))
@click.option("--status")
@click.option("--producer", help="Filter by producer product or adapter.")
@click.option("--limit", type=click.IntRange(min=1), default=100, show_default=True)
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def list_shares(ctx: click.Context, type_code: str | None, status: str | None, producer: str | None, limit: int, local_json: bool) -> None:
    """List shares, newest first."""
    as_json = _as_json(ctx, local_json)
    try:
        response = _request_json(_config(ctx), "GET", "/shares")
        items = response.get("items", []) if isinstance(response, dict) else []
        if type_code:
            items = [item for item in items if item.get("type") == type_code]
        if status:
            items = [item for item in items if item.get("status") == status]
        if producer:
            items = [
                item
                for item in items
                if producer
                in {
                    item.get("producer", {}).get("product"),
                    item.get("producer", {}).get("adapter"),
                }
            ]
        items = items[:limit]
        result = {"items": items, "count": len(items)}
    except ShareRequestError as error:
        _fail(ctx, error, as_json=as_json)
        return
    if as_json:
        _emit(result, as_json=True)
        return
    if not items:
        click.echo("No shares.")
        return
    for item in items:
        click.echo(f"{item.get('share_id', '-')}\t{item.get('status', '-')}\t{item.get('title', '-')}")


@cmd_share.command("inspect")
@click.argument("reference")
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def inspect(ctx: click.Context, reference: str, local_json: bool) -> None:
    """Show a complete ShareEnvelope for an id, alias, or URL."""
    as_json = _as_json(ctx, local_json)
    try:
        share_ref = _share_reference(reference)
        envelope = _request_json(_config(ctx), "GET", f"/shares/{urllib.parse.quote(share_ref, safe='')}")
    except (ShareRequestError, ValueError) as error:
        _fail(ctx, error, as_json=as_json)
        return
    _emit(envelope, as_json=as_json)


@cmd_share.command("revoke")
@click.argument("reference")
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def revoke(ctx: click.Context, reference: str, local_json: bool) -> None:
    """Revoke a share while preserving its tombstone."""
    as_json = _as_json(ctx, local_json)
    try:
        share_ref = _share_reference(reference)
        envelope = _request_json(_config(ctx), "POST", f"/shares/{urllib.parse.quote(share_ref, safe='')}/revoke")
    except (ShareRequestError, ValueError) as error:
        _fail(ctx, error, as_json=as_json)
        return
    _emit(envelope, as_json=as_json, human=f"Revoked {envelope.get('share_id', share_ref)}")


@cmd_share.command("migrate")
@click.argument("manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True))
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def migrate(ctx: click.Context, manifest: Path, local_json: bool) -> None:
    """Idempotently reserve every share in a JSON manifest."""
    as_json = _as_json(ctx, local_json)
    try:
        items = _normalize_manifest(manifest)
    except ValueError as error:
        _fail(ctx, error, as_json=as_json)
        return

    results: list[dict[str, Any]] = []
    failed = 0
    for index, item in enumerate(items):
        try:
            envelope = _request_json(_config(ctx), "POST", "/shares", payload=item)
            results.append(
                {
                    "index": index,
                    "ok": True,
                    "share_id": envelope.get("share_id"),
                    "canonical_url": envelope.get("canonical_url"),
                }
            )
        except ShareRequestError as error:
            failed += 1
            results.append({"index": index, **error.as_dict()})
    result = {
        "ok": failed == 0,
        "manifest": str(manifest),
        "total": len(items),
        "reserved": len(items) - failed,
        "failed": failed,
        "items": results,
    }
    if as_json:
        _emit(result, as_json=True)
    else:
        click.echo(f"Reserved {result['reserved']}/{result['total']} shares from {manifest}")
        for item in results:
            if not item["ok"]:
                click.echo(f"  [{item['index']}] {item['message']}", err=True)
    if failed:
        ctx.exit(1)


@cmd_share.command("backfill-captures")
@click.option("--root", type=click.Path(path_type=Path, file_okay=False, exists=True), help="Capture directory; defaults to the workspace captures root.")
@click.option("--report", type=click.Path(path_type=Path, dir_okay=False), help="Write the complete migration report as JSON.")
@click.option("--limit", type=click.IntRange(min=1), help="Process only the first N capture Markdown files.")
@click.option("--dry-run", is_flag=True, help="Classify captures without publishing them.")
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def backfill_captures(
    ctx: click.Context,
    root: Path | None,
    report: Path | None,
    limit: int | None,
    dry_run: bool,
    local_json: bool,
) -> None:
    """Publish existing capture Markdown files without rewriting their sources."""
    as_json = _as_json(ctx, local_json)
    capture_root = (root or _capture_root()).resolve()
    paths = sorted(capture_root.glob("*.md"), key=lambda item: item.name.lower())
    if limit is not None:
        paths = paths[:limit]

    results: list[dict[str, Any]] = []
    failed = 0
    for path in paths:
        try:
            payload, item_report = _capture_migration_item(path)
            if dry_run:
                results.append({**item_report, "ok": True, "published": False})
                continue
            envelope = _request_json(_config(ctx), "POST", "/shares", payload=payload)
            if envelope.get("status") in {"revoked", "expired"}:
                results.append({
                    **item_report,
                    "ok": True,
                    "published": False,
                    "retained_status": envelope.get("status"),
                    "canonical_url": envelope.get("canonical_url"),
                })
                continue
            content = path.read_bytes()
            share_ref = urllib.parse.quote(str(envelope["share_id"]), safe="")
            query = urllib.parse.urlencode({"title": path.name, "media_type": "text/markdown"})
            _request_json(
                _config(ctx),
                "PUT",
                f"/shares/{share_ref}/content/primary/capture.md?{query}",
                body=content,
                content_type="text/markdown",
            )
            envelope = _request_json(
                _config(ctx),
                "PUT",
                f"/shares/{share_ref}",
                payload={"status": "ready", "metadata": payload["metadata"], "source_revision": payload["source_revision"]},
            )
            results.append({
                **item_report,
                "ok": True,
                "published": True,
                "canonical_url": envelope.get("canonical_url"),
                "status": envelope.get("status"),
            })
        except (OSError, UnicodeError, KeyError, ShareRequestError, ValueError) as error:
            failed += 1
            error_payload = error.as_dict() if isinstance(error, ShareRequestError) else {
                "error": "capture_migration_failed",
                "message": str(error),
            }
            results.append({"file": path.name, "ok": False, **error_payload})

    counts = {
        "migrated": sum(item.get("migration_state") == "migrated" for item in results),
        "unrecoverable": sum(item.get("migration_state") == "unrecoverable" for item in results),
        "points_to_missing": sum(bool(item.get("points_to_missing")) for item in results),
    }
    output = {
        "ok": failed == 0,
        "dry_run": dry_run,
        "root": str(capture_root),
        "total": len(paths),
        "failed": failed,
        "counts": counts,
        "items": results,
    }
    if report is not None:
        _write_json_atomic(report.resolve(), output)
        output["report"] = str(report.resolve())
    if as_json:
        _emit(output, as_json=True)
    else:
        verb = "Classified" if dry_run else "Published"
        click.echo(
            f"{verb} {len(paths) - failed}/{len(paths)} captures "
            f"({counts['unrecoverable']} unrecoverable, {counts['points_to_missing']} points_to=none)"
        )
        if report is not None:
            click.echo(f"Report: {report.resolve()}")
    if failed:
        ctx.exit(1)


@cmd_share.command("doctor")
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def doctor(ctx: click.Context, local_json: bool) -> None:
    """Check manager reachability, registry integrity, aliases, and attachments."""
    as_json = _as_json(ctx, local_json)
    started = time.monotonic()
    try:
        health = _request_json(_config(ctx), "GET", "/doctor")
    except ShareRequestError as error:
        _fail(ctx, error, as_json=as_json)
        return
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    ok = bool(isinstance(health, dict) and health.get("ok") and health.get("service") == "content-share")
    result = {
        "ok": ok,
        "manager_url": _config(ctx)["manager_url"],
        "elapsed_ms": elapsed_ms,
        "health": health,
    }
    human = (
        f"OK content-share ({elapsed_ms} ms, {health.get('shares', '?')} shares)"
        if ok
        else "FAIL unexpected manager health response"
    )
    _emit(result, as_json=as_json, human=human)
    if not ok:
        ctx.exit(1)


@cmd_share.command("gc")
@click.option("--older-than-days", type=click.IntRange(min=0), default=30, show_default=True)
@click.option("--apply", "apply_changes", is_flag=True, help="Apply a previously reviewed preview.")
@click.option("--preview-token", help="Exact preview token returned by a prior GC preview.")
@click.option("--cutoff", help="Exact cutoff timestamp returned by the same preview.")
@click.option("--json", "local_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def gc(
    ctx: click.Context,
    older_than_days: int,
    apply_changes: bool,
    preview_token: str | None,
    cutoff: str | None,
    local_json: bool,
) -> None:
    """Preview GC, or apply the exact candidate set from a prior preview."""
    as_json = _as_json(ctx, local_json)
    if apply_changes and (not preview_token or not cutoff):
        _fail(
            ctx,
            ValueError("--apply requires --preview-token and --cutoff from the same preview"),
            as_json=as_json,
        )
        return
    payload: dict[str, Any] = {"action": "apply" if apply_changes else "preview"}
    if apply_changes:
        payload.update(preview_token=preview_token, cutoff=cutoff)
    else:
        payload["older_than_days"] = older_than_days
    try:
        result = _request_json(_config(ctx), "POST", "/gc", payload=payload)
    except ShareRequestError as error:
        _fail(ctx, error, as_json=as_json)
        return
    if as_json:
        _emit(result, as_json=True)
        return
    if apply_changes:
        click.echo(f"GC applied: {result.get('deleted', 0)} share(s) reduced to tombstones")
    else:
        click.echo(f"GC preview: {result.get('candidate_count', 0)} candidate(s)")
        click.echo(f"Cutoff: {result.get('cutoff', '-')}")
        click.echo(f"Preview token: {result.get('preview_token', '-')}")
    for item in result.get("candidates", result.get("tombstones", [])):
        click.echo(f"{item.get('share_id', '-')}\t{item.get('status', '-')}\t{item.get('updated_at', '-')}")


__all__ = ["cmd_share"]

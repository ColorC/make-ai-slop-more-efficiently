# [OMNI] origin=codex domain=cli/material ts=2026-08-02T00:00:00+08:00 type=cli status=active
# [OMNI] summary="omni material wiki：材料本体 create/revise/derive/supersede/reject/restore/link/get 的统一命令入口"
# [OMNI] why="人和 Agent 都必须先经同一个 profile 校验与本体写入服务，再让审阅、索引和报告消费稳定引用"
# [OMNI] tags=material-wiki,cli,canonical,mutation
# [OMNI] material_id="material:cli.material_wiki.command.py"
"""Material Wiki canonical mutation commands."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Callable

import click

from omnicompany.packages.services._core.material_wiki.adapters.markdown import (
    MarkdownWikiAdapter,
    MaterialWikiStorageError,
    render_document,
)
from omnicompany.packages.services._core.material_wiki.profiles import load_profile_from_wiki
from omnicompany.packages.services._core.material_wiki.schema import (
    MaterialSourceRef,
    MaterialVersionRef,
    MaterialWikiContractError,
)
from omnicompany.packages.services._core.material_wiki.service import MaterialWikiService

from .._access import any_caller


def _service_options(function: Callable[..., Any]) -> Callable[..., Any]:
    options = [
        click.option(
            "--profile-file",
            type=click.Path(path_type=Path, exists=True, dir_okay=False),
            required=True,
            help="含 material_profile frontmatter 的项目 Wiki 权威页。",
        ),
        click.option(
            "--wiki-root",
            type=click.Path(path_type=Path, file_okay=False),
            required=True,
            help="Canonical Markdown 条目根目录。",
        ),
        click.option(
            "--canonical-prefix",
            required=True,
            help="稳定引用前缀，例如 project:vilo/wiki/materials。",
        ),
        click.option(
            "--facility-root",
            type=click.Path(path_type=Path, file_okay=False),
            help="投影运行根；默认 Omnicompany 根。测试或独立设施可显式隔离。",
        ),
    ]
    for option in reversed(options):
        function = option(function)
    return function


def _mutation_options(function: Callable[..., Any]) -> Callable[..., Any]:
    options = [
        click.option(
            "--source",
            "source_values",
            multiple=True,
            required=True,
            help="来源 KIND=REF；可重复。",
        ),
        click.option("--idempotency-key", required=True, help="调用方稳定幂等键。"),
        click.option("--dry-run", is_flag=True, help="只校验并预览，不写本体。"),
        click.option("--json", "as_json", is_flag=True, help="输出 JSON 回执。"),
    ]
    for option in reversed(options):
        function = option(function)
    return function


def _body_options(function: Callable[..., Any]) -> Callable[..., Any]:
    options = [
        click.option("--body", help="Markdown 正文。"),
        click.option(
            "--body-file",
            type=click.Path(path_type=Path, exists=True, dir_okay=False),
            help="从 UTF-8 文件读取 Markdown 正文。",
        ),
    ]
    for option in reversed(options):
        function = option(function)
    return function


def _service(
    profile_file: Path,
    wiki_root: Path,
    canonical_prefix: str,
    facility_root: Path | None,
) -> MaterialWikiService:
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.packages.services._core.material_wiki.projections import (
        build_default_projection_coordinator,
    )

    root = (facility_root or omni_workspace_root()).resolve()
    isolated = facility_root is not None
    return MaterialWikiService(
        load_profile_from_wiki(profile_file),
        MarkdownWikiAdapter(wiki_root, canonical_prefix=canonical_prefix),
        projection_coordinator=build_default_projection_coordinator(
            state_root=root / "data" / "services" / "material_wiki" / "projections",
            semantic_root=root,
            registry_dir=root / "data" / "services" / "registry",
            isolated_events_root=(
                root / "data" / "events" / "material_wiki" if isolated else None
            ),
        ),
    )


def _sources(values: tuple[str, ...]) -> tuple[MaterialSourceRef, ...]:
    result: list[MaterialSourceRef] = []
    for value in values:
        kind, separator, ref = value.partition("=")
        if not separator or not kind.strip() or not ref.strip():
            raise click.ClickException(f"--source 必须是 KIND=REF，实际为 {value!r}")
        result.append(MaterialSourceRef(kind.strip(), ref.strip()))
    return tuple(result)


def _body(body: str | None, body_file: Path | None) -> str:
    if (body is None) == (body_file is None):
        raise click.ClickException("--body 与 --body-file 必须且只能提供一个")
    return body if body is not None else body_file.read_text(encoding="utf-8")


def _placement(value: str) -> tuple[str, ...]:
    parts = tuple(item.strip() for item in value.split("/") if item.strip())
    if not parts:
        raise click.ClickException("--placement 不能为空")
    return parts


def _emit(receipt: Any, as_json: bool) -> None:
    payload = asdict(receipt)
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    replay = "（幂等重放）" if receipt.idempotent_replay else ""
    preview = " [dry-run]" if receipt.dry_run else ""
    click.echo(
        f"{receipt.operation}{preview}{replay}: {receipt.material_id}@{receipt.version} "
        f"status={receipt.status}\n{receipt.canonical_ref}"
    )


def _execute(function: Callable[[], Any], as_json: bool) -> None:
    try:
        _emit(function(), as_json)
    except (MaterialWikiContractError, MaterialWikiStorageError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@click.group("material")
def cmd_material() -> None:
    """材料身份、Wiki 本体与投影入口。"""


from .material_profile import cmd_material_profile

cmd_material.add_command(cmd_material_profile)


@cmd_material.group("wiki")
def cmd_material_wiki() -> None:
    """先写 canonical Wiki 本体的版本化操作。"""


def _domain_options(function: Callable[..., Any]) -> Callable[..., Any]:
    for option in reversed((click.option("--registry", type=click.Path(path_type=Path, dir_okay=False)), click.option("--workspace-root", type=click.Path(path_type=Path, file_okay=False)))):
        function = option(function)
    return function


def _formalization_options(function: Callable[..., Any]) -> Callable[..., Any]:
    for option in reversed((click.option("--registry", type=click.Path(path_type=Path, dir_okay=False)), click.option("--records", type=click.Path(path_type=Path, file_okay=False)), click.option("--authority", type=click.Path(path_type=Path, dir_okay=False)), click.option("--workspace-root", type=click.Path(path_type=Path, file_okay=False)))):
        function = option(function)
    return function


def _material_domain_service(registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None) -> Any:
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.packages.services._core.material_domains.runtime import (
        build_formalization_service,
    )

    repo = omni_workspace_root().resolve()
    workspace = workspace_root.resolve() if workspace_root else repo.parent
    isolated_repo = workspace / "omnicompany"
    if workspace_root and isolated_repo.is_dir():
        repo = isolated_repo
    return build_formalization_service(
        omnicompany_root=repo,
        workspace_root=workspace,
        registry_path=registry,
        records_path=records,
        authority_path=authority,
    )


def _domain_payload(status: str, *, value: Any = None, errors: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"schema_version": 1, "status": status, "errors": errors or []}
    if value is not None:
        payload["result"] = value
    return payload


def _read_graph(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read graph {path}: {exc}") from exc


def _domain_run(as_json: bool, action: Callable[[], dict[str, Any]]) -> None:
    try:
        click.echo(json.dumps(action(), ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        if as_json:
            click.echo(json.dumps(_domain_payload("error", errors=[str(exc)]), ensure_ascii=False, indent=2))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc


@cmd_material.group("domain")
def cmd_material_domain() -> None:
    """Inspect registered formal domains."""


@cmd_material_domain.command("list")
@click.option("--json", "as_json", is_flag=True)
@_domain_options
@any_caller
def material_domain_list(registry: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    _domain_run(as_json, lambda: _domain_payload("ok", value={"domains": _material_domain_service(registry, None, None, workspace_root).registry.list()}))


def _domain_lookup_command(operation: str) -> Callable[..., None]:
    def command(material_id: str, registry: Path | None, workspace_root: Path | None, as_json: bool) -> None:
        _domain_run(as_json, lambda: _domain_payload("ok", value=getattr(_material_domain_service(registry, None, None, workspace_root).registry, operation)(material_id)))
    return command


for _operation in ("show", "context", "resolve"):
    _command = _domain_lookup_command(_operation)
    _command = click.argument("material_id")(_command)
    _command = click.option("--json", "as_json", is_flag=True)(_command)
    _command = _domain_options(_command)
    _command = any_caller(_command)
    cmd_material_domain.command(_operation)(_command)


def _material_migration_manifest(manifest: Path | None) -> Path:
    from omnicompany.core.config import omni_workspace_root

    return manifest.resolve() if manifest else omni_workspace_root().resolve() / "config" / "material_migrations.yaml"


def _migration_report(manifest: Path | None) -> Any:
    from omnicompany.packages.services._core.material_domains.migrations import (
        check_migration_manifest,
    )

    return check_migration_manifest(_material_migration_manifest(manifest))


@cmd_material.group("migration")
def cmd_material_migration() -> None:
    """Read-only migration compatibility and retirement gates."""


@cmd_material_migration.command("check")
@click.option("--manifest", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--json", "as_json", is_flag=True)
@any_caller
def material_migration_check(manifest: Path | None, as_json: bool) -> None:
    """Validate the migration manifest without changing any migration state."""

    try:
        report = _migration_report(manifest)
    except Exception as exc:
        if as_json:
            click.echo(json.dumps(_domain_payload("error", errors=[str(exc)]), ensure_ascii=False, indent=2))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc
    payload = _domain_payload(
        "ok" if report.valid else "invalid",
        value={"manifest": str(_material_migration_manifest(manifest)), **report.as_dict()},
        errors=[issue.message for issue in report.issues],
    )
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if not report.valid:
        raise click.exceptions.Exit(1)


@cmd_material_migration.command("list")
@click.option("--manifest", type=click.Path(path_type=Path, dir_okay=False))
@click.option(
    "--status",
    "status_filter",
    type=click.Choice(["pending", "compatibility", "retired"]),
)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def material_migration_list(
    manifest: Path | None,
    status_filter: str | None,
    as_json: bool,
) -> None:
    """List migration states and blockers without writing records or authority."""

    try:
        report = _migration_report(manifest)
    except Exception as exc:
        if as_json:
            click.echo(json.dumps(_domain_payload("error", errors=[str(exc)]), ensure_ascii=False, indent=2))
            raise click.exceptions.Exit(1) from exc
        raise click.ClickException(str(exc)) from exc
    if not report.valid:
        payload = _domain_payload(
            "invalid",
            value={"manifest": str(_material_migration_manifest(manifest)), **report.as_dict()},
            errors=[issue.message for issue in report.issues],
        )
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        raise click.exceptions.Exit(1)
    items = [
        item for item in report.items if status_filter is None or item["status"] == status_filter
    ]
    click.echo(
        json.dumps(
            _domain_payload(
                "ok",
                value={
                    "manifest": str(_material_migration_manifest(manifest)),
                    "count": len(items),
                    "items": items,
                },
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


@cmd_material.group("formalization")
def cmd_material_formalization() -> None:
    """Check and persist formalization Material Graphs."""


def _source_matches(graph: object, values: tuple[str, ...], key: str) -> None:
    from omnicompany.packages.services._core.material_domains.service import FormalizationService
    formal = FormalizationService._material(graph)["formalization"]
    if formal.get("idempotency_key") != key:
        raise ValueError("--idempotency-key must exactly match formalization.idempotency_key")
    available = {f"{item.get('kind')}={item.get('ref')}" for item in formal.get("sources", []) if isinstance(item, dict)}
    missing = [value for value in values if value not in available]
    if missing:
        raise ValueError("--source must exist in formalization.sources: " + ", ".join(missing))


@cmd_material_formalization.command("check")
@click.option("--graph", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_formalization_check(graph: Path, registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    def action() -> dict[str, Any]:
        report = _material_domain_service(registry, records, authority, workspace_root).check(_read_graph(graph))
        return _domain_payload("accepted" if report.accepted else "rejected", value=report.as_dict())
    _domain_run(as_json, action)


@cmd_material_formalization.command("report-check")
@click.option("--report", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_formalization_report_check(report: Path, registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    """Validate an Agent report and run governance without recording it."""
    def action() -> dict[str, Any]:
        from omnicompany.packages.services._core.material_domains.agent_reports import AgentFormalizationReport

        prepared = AgentFormalizationReport.from_mapping(_read_graph(report))
        checked = prepared.check(_material_domain_service(registry, records, authority, workspace_root))
        return _domain_payload("accepted", value={"report": checked.as_dict(), "formalization_graph": prepared.to_formalization_input()})
    _domain_run(as_json, action)


@cmd_material_formalization.command("record")
@click.option("--graph", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--idempotency-key", required=True)
@click.option("--source", "source_values", multiple=True, required=True, help="Must exactly match graph source KIND=REF; repeatable.")
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_formalization_record(graph: Path, idempotency_key: str, source_values: tuple[str, ...], registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    def action() -> dict[str, Any]:
        payload = _read_graph(graph); _source_matches(payload, source_values, idempotency_key)
        result = _material_domain_service(registry, records, authority, workspace_root).record(payload)
        report = result["report"]
        return _domain_payload("recorded" if result["stored"] else "rejected", value={"report": report.as_dict() if report else None, "stored": result["stored"], "authority": result["authority"]})
    _domain_run(as_json, action)


@cmd_material_formalization.command("get")
@click.argument("material_id")
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_formalization_get(material_id: str, registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    def action() -> dict[str, Any]:
        service = _material_domain_service(registry, records, authority, workspace_root)
        for graph in service.store.list():
            material = service._material(graph)
            if material["id"] == material_id:
                return _domain_payload("ok", value={"graph": graph, "record_ref": {"material_id": material["id"], "revision_ref": material["revision_ref"]}})
        raise ValueError(f"unknown formalization record: {material_id}")
    _domain_run(as_json, action)


@cmd_material_formalization.command("list")
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_formalization_list(registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    def action() -> dict[str, Any]:
        service = _material_domain_service(registry, records, authority, workspace_root)
        result = [{"material_id": service._material(graph)["id"], "revision_ref": service._material(graph)["revision_ref"], "graph": graph} for graph in service.store.list()]
        return _domain_payload("ok", value={"records": result, "count": len(result)})
    _domain_run(as_json, action)


@cmd_material.group("authority")
def cmd_material_authority() -> None:
    """Query or rebuild the authority projection."""


def _scope(object_value: str, purpose: str, scope: str) -> dict[str, str]:
    return {"object": object_value, "purpose": purpose, "scope": scope}


@cmd_material_authority.command("get")
@click.option("--object", "object_value", required=True)
@click.option("--purpose", required=True)
@click.option("--scope", required=True)
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_authority_get(object_value: str, purpose: str, scope: str, registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    def action() -> dict[str, Any]:
        value = _material_domain_service(registry, records, authority, workspace_root).authority.get(_scope(object_value, purpose, scope), include_conflict=True)
        return _domain_payload(value["status"], value=value)
    _domain_run(as_json, action)


@cmd_material_authority.command("history")
@click.option("--object", "object_value")
@click.option("--purpose")
@click.option("--scope")
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_authority_history(object_value: str | None, purpose: str | None, scope: str | None, registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    def action() -> dict[str, Any]:
        supplied = (object_value, purpose, scope)
        if any(item is not None for item in supplied) and not all(item is not None for item in supplied): raise ValueError("--object, --purpose and --scope must be supplied together")
        service = _material_domain_service(registry, records, authority, workspace_root)
        return _domain_payload("ok", value={"history": service.authority.history(_scope(object_value, purpose, scope) if object_value is not None else None)})
    _domain_run(as_json, action)


@cmd_material_authority.command("rebuild")
@click.option("--json", "as_json", is_flag=True)
@_formalization_options
@any_caller
def material_authority_rebuild(registry: Path | None, records: Path | None, authority: Path | None, workspace_root: Path | None, as_json: bool) -> None:
    _domain_run(as_json, lambda: _domain_payload("rebuilt", value={"projection": _material_domain_service(registry, records, authority, workspace_root).rebuild_authority()}))


@cmd_material_wiki.command("create")
@click.option("--title", required=True)
@click.option("--placement", required=True, help="profile id 路径，以 / 分隔。")
@click.option("--status", required=True)
@click.option("--material-id")
@click.option("--version", default="v1", show_default=True)
@_body_options
@_mutation_options
@_service_options
@any_caller
def material_wiki_create(
    title: str,
    placement: str,
    status: str,
    material_id: str | None,
    version: str,
    body: str | None,
    body_file: Path | None,
    source_values: tuple[str, ...],
    idempotency_key: str,
    dry_run: bool,
    as_json: bool,
    profile_file: Path,
    wiki_root: Path,
    canonical_prefix: str,
    facility_root: Path | None,
) -> None:
    service = _service(profile_file, wiki_root, canonical_prefix, facility_root)
    _execute(
        lambda: service.create(
            title=title,
            placement_path=_placement(placement),
            body=_body(body, body_file),
            status=status,
            sources=_sources(source_values),
            idempotency_key=idempotency_key,
            material_id=material_id,
            version=version,
            dry_run=dry_run,
        ),
        as_json,
    )


def _revise_command(operation: str) -> Callable[..., None]:
    def command(
        material_id: str,
        status: str,
        expected_version: str,
        version: str | None,
        body: str | None,
        body_file: Path | None,
        source_values: tuple[str, ...],
        idempotency_key: str,
        dry_run: bool,
        as_json: bool,
        profile_file: Path,
        wiki_root: Path,
        canonical_prefix: str,
        facility_root: Path | None,
    ) -> None:
        service = _service(profile_file, wiki_root, canonical_prefix, facility_root)
        method = service.supersede if operation == "supersede" else service.revise
        _execute(
            lambda: method(
                material_id,
                body=_body(body, body_file),
                status=status,
                sources=_sources(source_values),
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                version=version,
                dry_run=dry_run,
            ),
            as_json,
        )

    return command


for _operation in ("revise", "supersede"):
    _command = _revise_command(_operation)
    _command = click.argument("material_id")(_command)
    _command = click.option("--status", required=True)(_command)
    _command = click.option("--expected-version", required=True)(_command)
    _command = click.option("--version")(_command)
    _command = _body_options(_command)
    _command = _mutation_options(_command)
    _command = _service_options(_command)
    _command = any_caller(_command)
    cmd_material_wiki.command(_operation)(_command)


@cmd_material_wiki.command("derive")
@click.argument("source_material_id")
@click.option("--source-version", required=True)
@click.option("--title", required=True)
@click.option("--placement", required=True)
@click.option("--status", required=True)
@click.option("--material-id")
@_body_options
@_mutation_options
@_service_options
@any_caller
def material_wiki_derive(
    source_material_id: str,
    source_version: str,
    title: str,
    placement: str,
    status: str,
    material_id: str | None,
    body: str | None,
    body_file: Path | None,
    source_values: tuple[str, ...],
    idempotency_key: str,
    dry_run: bool,
    as_json: bool,
    profile_file: Path,
    wiki_root: Path,
    canonical_prefix: str,
    facility_root: Path | None,
) -> None:
    service = _service(profile_file, wiki_root, canonical_prefix, facility_root)
    _execute(
        lambda: service.derive(
            source_material_id,
            source_version=source_version,
            title=title,
            placement_path=_placement(placement),
            body=_body(body, body_file),
            status=status,
            sources=_sources(source_values),
            idempotency_key=idempotency_key,
            material_id=material_id,
            dry_run=dry_run,
        ),
        as_json,
    )


def _status_command(operation: str) -> Callable[..., None]:
    def command(
        material_id: str,
        target_version: str,
        status: str | None,
        expected_version: str,
        source_values: tuple[str, ...],
        idempotency_key: str,
        dry_run: bool,
        as_json: bool,
        profile_file: Path,
        wiki_root: Path,
        canonical_prefix: str,
        facility_root: Path | None,
    ) -> None:
        service = _service(profile_file, wiki_root, canonical_prefix, facility_root)
        kwargs: dict[str, Any] = {
            "target_version": target_version,
            "sources": _sources(source_values),
            "expected_version": expected_version,
            "idempotency_key": idempotency_key,
            "dry_run": dry_run,
        }
        if operation == "restore":
            if not status:
                raise click.ClickException("restore 必须提供 --status")
            kwargs["status"] = status
        _execute(lambda: getattr(service, operation)(material_id, **kwargs), as_json)

    return command


for _operation in ("reject", "restore"):
    _command = _status_command(_operation)
    _command = click.argument("material_id")(_command)
    _command = click.option("--target-version", required=True)(_command)
    _command = click.option("--status")(_command)
    _command = click.option("--expected-version", required=True)(_command)
    _command = _mutation_options(_command)
    _command = _service_options(_command)
    _command = any_caller(_command)
    cmd_material_wiki.command(_operation)(_command)


@cmd_material_wiki.command("link")
@click.argument("material_id")
@click.option("--target-version", required=True)
@click.option("--relation", type=click.Choice(["supersedes", "derived_from", "related"]), required=True)
@click.option("--related-material-id", required=True)
@click.option("--related-version", required=True)
@click.option("--expected-version", required=True)
@_mutation_options
@_service_options
@any_caller
def material_wiki_link(
    material_id: str,
    target_version: str,
    relation: str,
    related_material_id: str,
    related_version: str,
    expected_version: str,
    source_values: tuple[str, ...],
    idempotency_key: str,
    dry_run: bool,
    as_json: bool,
    profile_file: Path,
    wiki_root: Path,
    canonical_prefix: str,
    facility_root: Path | None,
) -> None:
    service = _service(profile_file, wiki_root, canonical_prefix, facility_root)
    _execute(
        lambda: service.link(
            material_id,
            target_version=target_version,
            relation=relation,
            related=MaterialVersionRef(related_material_id, related_version),
            sources=_sources(source_values),
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
        ),
        as_json,
    )


@cmd_material_wiki.command("get")
@click.argument("material_id")
@click.option("--raw", is_flag=True, help="输出完整 canonical Markdown。")
@click.option("--json", "as_json", is_flag=True)
@_service_options
@any_caller
def material_wiki_get(
    material_id: str,
    raw: bool,
    as_json: bool,
    profile_file: Path,
    wiki_root: Path,
    canonical_prefix: str,
    facility_root: Path | None,
) -> None:
    try:
        document = _service(profile_file, wiki_root, canonical_prefix, facility_root).get(material_id)
    except (MaterialWikiContractError, MaterialWikiStorageError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if raw:
        click.echo(render_document(document), nl=False)
    elif as_json:
        click.echo(json.dumps(asdict(document), ensure_ascii=False, indent=2))
    else:
        current = document.current
        click.echo(
            f"{document.material_id}@{document.head_version} current={document.current_version}\n"
            f"{document.canonical_ref}\n\n{current.body if current else '(无当前有效版本)'}"
        )


@cmd_material_wiki.command("context")
@click.argument("project_id")
@click.option("--offset", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--limit", type=click.IntRange(min=1, max=500), default=50, show_default=True)
@click.option("--include-terminal", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@any_caller
def material_wiki_context(
    project_id: str,
    offset: int,
    limit: int,
    include_terminal: bool,
    as_json: bool,
) -> None:
    """读取 Agent 开工所需的唯一计划、项目合同、当前目录和提交入口。"""
    from omnicompany.packages.services._core.material_wiki.agent_context import (
        build_agent_context,
    )

    try:
        payload = build_agent_context(
            _runtime(),
            project_id,
            offset=offset,
            limit=limit,
            include_terminal=include_terminal,
        )
    except (MaterialWikiContractError, MaterialWikiStorageError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    current = payload["current_materials"]
    click.echo(
        f"plan: {payload['authority']['overall_plan_ref']}\n"
        f"profile: {payload['authority']['profile_ref']}\n"
        f"project: {project_id} ({payload['project']['profile_id']})\n"
        f"current: {current['total']}，本页 {current['returned']}\n"
        f"submit: {payload['submission']['api_base']}"
    )


def _payload_sources(values: Any) -> tuple[MaterialSourceRef, ...]:
    if not isinstance(values, list):
        raise click.ClickException("payload.sources 必须是数组")
    result: list[MaterialSourceRef] = []
    for item in values:
        if not isinstance(item, dict):
            raise click.ClickException("payload.sources 的每项必须是对象")
        result.append(
            MaterialSourceRef(
                str(item.get("kind") or ""),
                str(item.get("ref") or ""),
                str(item["note"]) if item.get("note") else None,
            )
        )
    return tuple(result)


@cmd_material_wiki.command("submit")
@click.argument("project_id")
@click.option(
    "--payload",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
    help="UTF-8 JSON mutation 请求；operation 决定其余字段。",
)
@click.option("--dry-run", is_flag=True, help="强制只校验，不写本体。")
@any_caller
def material_wiki_submit(project_id: str, payload: Path, dry_run: bool) -> None:
    """通过项目名录和唯一服务提交一项材料 mutation。"""
    try:
        request = json.loads(payload.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"无法读取 payload：{exc}") from exc
    if not isinstance(request, dict):
        raise click.ClickException("payload 顶层必须是对象")
    operation = str(request.get("operation") or "")
    sources = _payload_sources(request.get("sources"))
    common = {
        "sources": sources,
        "idempotency_key": str(request.get("idempotency_key") or ""),
        "dry_run": bool(dry_run or request.get("dry_run", False)),
    }
    service = _runtime().service(project_id)

    def execute() -> Any:
        if operation == "create":
            return service.create(
                title=str(request.get("title") or ""),
                placement_path=tuple(request.get("placement_path") or ()),
                body=str(request.get("body") or ""),
                status=str(request.get("status") or ""),
                material_id=request.get("material_id"),
                version=str(request.get("version") or "v1"),
                **common,
            )
        material_id = str(request.get("material_id") or "")
        if operation in {"revise", "supersede"}:
            return getattr(service, operation)(
                material_id,
                body=str(request.get("body") or ""),
                status=str(request.get("status") or ""),
                expected_version=str(request.get("expected_version") or ""),
                version=request.get("version"),
                **common,
            )
        if operation == "derive":
            return service.derive(
                str(request.get("source_material_id") or ""),
                source_version=str(request.get("source_version") or ""),
                title=str(request.get("title") or ""),
                placement_path=tuple(request.get("placement_path") or ()),
                body=str(request.get("body") or ""),
                status=str(request.get("status") or ""),
                material_id=request.get("material_id"),
                **common,
            )
        if operation in {"reject", "restore"}:
            kwargs: dict[str, Any] = {
                "target_version": str(request.get("target_version") or ""),
                "expected_version": str(request.get("expected_version") or ""),
                **common,
            }
            if operation == "restore":
                kwargs["status"] = str(request.get("status") or "")
            return getattr(service, operation)(material_id, **kwargs)
        if operation == "link":
            return service.link(
                material_id,
                target_version=str(request.get("target_version") or ""),
                relation=str(request.get("relation") or ""),
                related=MaterialVersionRef(
                    str(request.get("related_material_id") or ""),
                    str(request.get("related_version") or ""),
                ),
                expected_version=str(request.get("expected_version") or ""),
                **common,
            )
        raise ValueError(
            "operation 必须是 create/revise/derive/supersede/reject/restore/link"
        )

    _execute(execute, True)


@cmd_material_wiki.command("rebuild")
@click.argument("project_id")
@click.option("--json", "as_json", is_flag=True)
@any_caller
def material_wiki_rebuild(project_id: str, as_json: bool) -> None:
    """从 canonical Wiki 本体重建该项目的全部派生投影。"""
    try:
        summary = _runtime().rebuild(project_id)
    except (MaterialWikiContractError, MaterialWikiStorageError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = asdict(summary)
    if as_json:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        click.echo(
            f"rebuild {project_id}: documents={summary.documents} "
            f"versions={summary.versions} pending={summary.pending}"
        )
    if summary.pending:
        raise click.ClickException(f"仍有 {summary.pending} 个版本存在待重放投影")


@cmd_material_wiki.group("migration")
def material_wiki_migration() -> None:
    """盘点、dry-run、迁移和可恢复回滚。"""


def _runtime() -> Any:
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.packages.services._core.material_wiki.runtime import MaterialWikiRuntime

    return MaterialWikiRuntime(omnicompany_root=omni_workspace_root())


@material_wiki_migration.command("inventory")
@click.option("--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), required=True)
@any_caller
def material_wiki_migration_inventory(manifest: Path, output: Path) -> None:
    from omnicompany.packages.services._core.material_wiki.migration import write_inventory

    runtime = _runtime()
    payload = write_inventory(manifest, output, omnicompany_root=runtime.omnicompany_root)
    click.echo(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


@material_wiki_migration.command("apply")
@click.option("--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--inventory", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--receipt", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--dry-run", is_flag=True)
@any_caller
def material_wiki_migration_apply(
    manifest: Path,
    inventory: Path | None,
    receipt: Path | None,
    dry_run: bool,
) -> None:
    from omnicompany.packages.services._core.material_wiki.migration import run_migration

    try:
        payload = run_migration(
            manifest,
            runtime=_runtime(),
            dry_run=dry_run,
            receipt_path=receipt,
            inventory_path=inventory,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["conflicts"]:
        raise click.ClickException(f"迁移存在 {len(payload['conflicts'])} 个冲突")


@material_wiki_migration.command("audit")
@click.option("--manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--inventory", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@any_caller
def material_wiki_migration_audit(manifest: Path, inventory: Path) -> None:
    from omnicompany.packages.services._core.material_wiki.migration import audit_inventory

    payload = audit_inventory(
        manifest,
        inventory,
        omnicompany_root=_runtime().omnicompany_root,
    )
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["ok"]:
        raise click.ClickException(f"inventory 审计存在 {len(payload['errors'])} 个错误")


@material_wiki_migration.command("ledger")
@click.option("--inventory", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--output", type=click.Path(path_type=Path, dir_okay=False), required=True)
@click.option("--receipt", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@any_caller
def material_wiki_migration_ledger(
    inventory: Path,
    output: Path,
    receipt: Path | None,
) -> None:
    """从 inventory 和迁移回执生成完整弃用归档视图。"""
    from omnicompany.packages.services._core.material_wiki.migration import (
        write_deprecation_ledger,
    )

    payload = write_deprecation_ledger(inventory, output, receipt_path=receipt)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@material_wiki_migration.command("rollback")
@click.option("--receipt", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--dry-run", is_flag=True)
@any_caller
def material_wiki_migration_rollback(receipt: Path, dry_run: bool) -> None:
    from omnicompany.packages.services._core.material_wiki.migration import rollback_migration

    payload = rollback_migration(receipt, runtime=_runtime(), dry_run=dry_run)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["errors"]:
        raise click.ClickException(f"回滚存在 {len(payload['errors'])} 个错误")

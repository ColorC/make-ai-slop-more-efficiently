# [OMNI] summary="Explicit read-only Guardian session and footprint commands."
# [OMNI] why="Expose bounded protected-asset detection through the existing Guardian group."
import json
import time
import sqlite3
from pathlib import Path
import click
from omnicompany.packages.services._core.guardian.session_asset_readonly_audit import audit, Budget
from contextlib import closing
from omnicompany.packages.services._core.guardian.asset_footprint_readonly import inventory, ResumableFootprint

@click.group("assets")
def cmd_guardian_assets():
    """Bounded read-only asset evidence; all assets stay protected."""

def emit(result, summary=False, budget=None):
    if summary:
        result.pop("records", None)
    if budget is not None:
        with budget.phase("serialization"):
            result["coverage"].update(budget.telemetry())
            text = json.dumps(result, ensure_ascii=False, indent=2)
        if time.monotonic()-budget.started >= budget.max_seconds:
            result["coverage"]["complete_within_declared_scope"] = False
            result["coverage"].setdefault("incomplete_reasons", []).append("time during serialization")
        result["coverage"].update(budget.telemetry())
        with budget.phase("serialization_final"):
            text = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(result, ensure_ascii=False, indent=2)
    started = time.monotonic()
    try:
        click.echo(text)
    except OSError as exc:
        click.echo(json.dumps({"read_only": True, "output_error": type(exc).__name__, "complete_within_declared_scope": False}), err=True)
        raise click.exceptions.Exit(2)
    output_seconds = time.monotonic()-started
    if budget is not None:
        elapsed = time.monotonic()-budget.started
        exceeded = elapsed >= budget.max_seconds
        result["coverage"]["complete_within_declared_scope"] &= not exceeded
        click.echo(json.dumps({"guardian_assets_output": True, "read_only": True,
            "output_seconds": round(output_seconds, 6), "elapsed_seconds": round(elapsed, 6),
            "stage_seconds": budget.telemetry()["stage_seconds"],
            "time_budget_exceeded": exceeded,
            "complete_within_declared_scope": result["coverage"]["complete_within_declared_scope"]}), err=True)
    if not result["coverage"]["complete_within_declared_scope"]:
        raise click.exceptions.Exit(2)


def run_readonly(function, budget, summary=False):
    try:
        result = function()
    except (OSError, ValueError, sqlite3.Error) as exc:
        result = {"read_only": True, "automatic_cleanup_allowed": False,
            "error": type(exc).__name__, "coverage": {"complete_within_declared_scope": False}}
    emit(result, summary, budget)

@cmd_guardian_assets.command("sessions")
@click.option("--db",type=click.Path(exists=True,dir_okay=False,path_type=Path),required=True)
@click.option("--native-root",type=click.Path(path_type=Path),multiple=True,required=True)
@click.option("--archive-root",type=click.Path(path_type=Path),multiple=True)
@click.option("--archive-manifest",type=click.Path(path_type=Path),multiple=True)
@click.option("--max-entries",type=click.IntRange(min=1,max=100000),default=15000)
@click.option("--max-seconds",type=click.FloatRange(min=0.001,max=60),default=10.0)
@click.option("--max-header-bytes",type=click.IntRange(min=0,max=4194304),default=262144)
@click.option("--max-rows",type=click.IntRange(min=1,max=10000),default=5000)
@click.option("--max-depth",type=click.IntRange(min=0,max=32),default=4)
@click.option("--max-sql-steps",type=click.IntRange(min=100,max=1000000),default=100000)
@click.option("--summary",is_flag=True)
def sessions(db,native_root,archive_root,archive_manifest,max_entries,max_seconds,max_header_bytes,max_rows,max_depth,max_sql_steps,summary):
    """Reconcile native paths with declared archives; no restoration or cleanup."""
    budget = Budget(max_entries,max_seconds,max_header_bytes,max_sql_steps=max_sql_steps)
    run_readonly(lambda: audit(db,list(native_root),list(archive_root),list(archive_manifest),
               budget,max_rows,max_depth),budget,summary)

@cmd_guardian_assets.command("footprint")
@click.option("--root",type=click.Path(path_type=Path),multiple=True,required=True)
@click.option("--protected-path",type=click.Path(path_type=Path),multiple=True)
@click.option("--max-entries",type=click.IntRange(min=1,max=100000),default=30000)
@click.option("--max-seconds",type=click.FloatRange(min=0.001,max=60),default=10.0)
@click.option("--max-depth",type=click.IntRange(min=0,max=32),default=8)
@click.option("--min-copy-mib",type=click.IntRange(min=1),default=50)
@click.option("--checkpoint",type=click.Path(path_type=Path))
@click.option("--exclude-path",type=click.Path(path_type=Path),multiple=True)
@click.option("--max-pending",type=click.IntRange(min=1,max=10000),default=256)
@click.option("--discover-only",is_flag=True)
@click.option("--focus-path",type=click.Path(path_type=Path))
def footprint(root,protected_path,max_entries,max_seconds,max_depth,min_copy_mib,checkpoint,exclude_path,max_pending,discover_only,focus_path):
    """Logical bytes and media/copy candidates; partial sizes are lower bounds."""
    budget = Budget(max_entries,max_seconds)
    if checkpoint is not None:
        def scan():
            with closing(ResumableFootprint(checkpoint,root,excluded_paths=exclude_path)) as scanner:
                result = scanner.scan_chunk(budget=budget,max_depth=max_depth,max_pending=max_pending,
                    discover_only=discover_only,focus=focus_path)
                result["protected_anchors"] = [str(p.absolute()) for p in protected_path]
                return result
        run_readonly(scan,budget)
    else:
        if exclude_path or discover_only or focus_path:
            raise click.UsageError("Exclusion/discovery/focus options require a declared metadata checkpoint")
        run_readonly(lambda: inventory(root,protected_path,budget,max_depth,min_copy_mib*1024**2),budget)

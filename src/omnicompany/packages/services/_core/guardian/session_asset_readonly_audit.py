# [OMNI] summary="Bounded read-only native-session asset reconciliation."
# [OMNI] why="Missing indexed paths may have archived copies; expose uncertainty without authorizing cleanup."
"""Guardian utility; reads existing indexes/manifests, emits stdout JSON.
No scheduling, credential loading, transcript copying, repair, or deletion.
"""
import argparse
from collections import Counter
from contextlib import closing, contextmanager, ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

class UnsafePath(ValueError):
    pass

class Limit(RuntimeError):
    pass

@dataclass
class Budget:
    max_entries: int = 15000
    max_seconds: float = 10
    max_header_bytes: int = 262144
    entries: int = 0
    header_bytes: int = 0
    started: float = field(default_factory=lambda: time.monotonic())
    max_sql_steps: int = 100000
    sql_steps: int = 0
    stage: str = "initialization"
    stopped: str | None = None
    stages: dict = field(default_factory=dict)
    operations: dict = field(default_factory=dict)
    def check(self):
        if self.stopped:
            raise Limit(self.stopped)
        if time.monotonic() - self.started >= self.max_seconds:
            self.stopped = "time: " + self.stage
            raise Limit(self.stopped)
    def entry(self):
        self.check()
        if self.entries >= self.max_entries:
            self.stopped = "entries: " + self.stage
            raise Limit(self.stopped)
        self.entries += 1
    @contextmanager
    def phase(self, name):
        previous, self.stage = self.stage, name
        started = time.monotonic()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + time.monotonic() - started
            self.stage = previous
    @contextmanager
    def operation(self, name):
        self.check()
        started = time.monotonic()
        try:
            yield
        finally:
            duration = time.monotonic() - started
            item = self.operations.setdefault(name, {"calls": 0, "seconds": 0.0, "max_seconds": 0.0})
            item["calls"] += 1
            item["seconds"] += duration
            item["max_seconds"] = max(item["max_seconds"], duration)
        self.check()
    def sql_progress(self):
        self.sql_steps += 100
        try:
            self.check()
            if self.sql_steps >= self.max_sql_steps:
                self.stopped = "SQL instruction budget"
                return 1
        except Limit:
            return 1
        return 0
    def telemetry(self):
        return {"stage_seconds": {k: round(v, 6) for k, v in self.stages.items()},
                "slowest_operations": [{"operation": k, "calls": v["calls"],
                    "seconds": round(v["seconds"], 6), "max_seconds": round(v["max_seconds"], 6)}
                    for k, v in sorted(self.operations.items(), key=lambda x:x[1]["seconds"], reverse=True)[:12]],
                "sql_steps_approx": self.sql_steps, "stopped": self.stopped,
                "elapsed_seconds": round(time.monotonic() - self.started, 6)}

def absolute(p):
    return Path(os.path.abspath(p))

def checked_stat(path, budget=None):
    for part in [*reversed(path.parents), path]:
        if budget is None:
            info = part.lstat()
        else:
            with budget.operation("lstat"):
                info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise UnsafePath(str(part))
    return info

def inside(path, roots):
    return any(path.is_relative_to(root) for root in roots)

def header_id(path, budget):
    budget.check()
    if budget.header_bytes + 1024 > budget.max_header_bytes:
        budget.stopped = "header bytes: " + budget.stage
        raise Limit(budget.stopped)
    checked_stat(path, budget)
    with budget.operation("header_open_read_close"):
        with path.open("rb") as stream:
            prefix = stream.readline(1024)
        budget.header_bytes += len(prefix)
    if not re.search(rb'"type"\s*:\s*"session_meta"', prefix):
        return None
    metadata = prefix.split(b'"instructions"', 1)[0]
    ids = {v.decode("ascii").lower() for v in re.findall(rb'"(?:id|session_id)"\s*:\s*"([0-9a-f-]{36})"', metadata, re.I)}
    return next(iter(ids)) if len(ids) == 1 else None


def audit(db, native_roots, archive_roots=(), manifests=(), budget=None, max_rows=5000, max_depth=4):
    budget = budget or Budget()
    if not (1 <= max_rows <= 10000 and 0 <= max_depth <= 32 and
            1 <= budget.max_entries <= 100000 and 0 < budget.max_seconds <= 60 and
            0 <= budget.max_header_bytes <= 4*1024**2 and 100 <= budget.max_sql_steps <= 1000000):
        raise ValueError("Invalid bounded scan budget")
    if not native_roots or max(len(native_roots), len(archive_roots), len(manifests)) > 32:
        raise ValueError("Declare 1-32 native roots and at most 32 archives/manifests")
    db = absolute(db)
    roots = [absolute(p) for p in native_roots]
    archive_roots = [absolute(p) for p in archive_roots]
    records, errors, incomplete, skipped, index_rows = [], [], [], [], []
    index_complete, more_rows = False, False
    # LIMIT plus fetchmany bounds memory; progress_handler bounds SQLite CPU work.
    # No COUNT(*) scan; exact total is unknown whenever index retrieval is partial.
    with budget.phase("sql_index"):
        try:
            checked_stat(db, budget)
            budget.check()
            with ExitStack() as stack:
                with budget.operation("sqlite_connect"):
                    con = stack.enter_context(closing(sqlite3.connect(db.as_uri()+"?mode=ro", uri=True, timeout=0.1)))
                con.set_progress_handler(budget.sql_progress, 100)
                with budget.operation("sqlite_setup"):
                    con.execute("PRAGMA query_only=ON")
                with budget.operation("sqlite_execute"):
                    cursor = con.execute("SELECT id,rollout_path FROM threads ORDER BY id LIMIT ?", (max_rows+1,))
                with closing(cursor):
                    while len(index_rows) <= max_rows:
                        with budget.operation("sqlite_fetchmany"):
                            page = cursor.fetchmany(min(64, max_rows+1-len(index_rows)))
                        if not page:
                            index_complete = True
                            break
                        index_rows.extend(page)
                        if len(index_rows) > max_rows:
                            more_rows = True
                            incomplete.append("index row limit")
                            break
                budget.check()
        except Limit as exc:
            incomplete.append(str(exc))
        except sqlite3.Error as exc:
            if budget.stopped:
                incomplete.append(budget.stopped)
            else:
                errors.append({"path": str(db), "type": type(exc).__name__})
                incomplete.append("index query failed")
        except (OSError, UnsafePath) as exc:
            errors.append({"path": str(db), "type": type(exc).__name__})
            incomplete.append("index unavailable")
    rows_seen = len(index_rows)
    selected_rows = index_rows[:max_rows]
    with budget.phase("registered_paths"):
        for native_id, registered in selected_rows:
            try:
                budget.check()
            except Limit as exc:
                incomplete.append(str(exc))
                break
            row = {"native_id": native_id, "registered_path": registered, "protected": True}
            records.append(row)
            if (not isinstance(native_id, str) or not UUID.fullmatch(native_id) or
                    not isinstance(registered, str) or not registered or len(registered)>4096):
                row["status"] = "invalid_index_metadata"
                incomplete.append(row["status"])
                continue
            path = absolute(registered)
            if not inside(path, roots):
                row["status"] = "registered_path_outside_declared_roots"
                incomplete.append(row["status"])
                continue
            try:
                budget.entry()
                info = checked_stat(path, budget)
                row["status"] = "registered_present" if stat.S_ISREG(info.st_mode) else "registered_not_regular_file"
                if row["status"] != "registered_present":
                    incomplete.append(row["status"])
            except FileNotFoundError:
                row["status"] = "pending"
            except PermissionError:
                row["status"] = "permission_blocked"
                incomplete.append(row["status"])
            except UnsafePath:
                row["status"] = "link_or_reparse_blocked"
                incomplete.append(row["status"])
            except Limit as exc:
                row["status"] = "budget_exhausted"
                incomplete.append(str(exc))
                break
            except OSError as exc:
                row["status"] = "metadata_error"
                errors.append({"path": str(path), "type": type(exc).__name__})
                incomplete.append(row["status"])
    if len(records)<len(selected_rows):
        incomplete.append("index rows not audited")
    wanted = {r["native_id"].lower() for r in records if r["status"] == "pending"}
    candidates = {}
    with budget.phase("native_enumeration"):
        for root in roots if wanted and not budget.stopped else ():
            todo = [(root, 0)]
            try:
                while todo:
                    parent, depth = todo.pop()
                    checked_stat(parent, budget)
                    with ExitStack() as stack:
                        with budget.operation("scandir_open"):
                            entries = stack.enter_context(os.scandir(parent))
                        while True:
                            with budget.operation("scandir_next"):
                                entry = next(entries, None)
                            if entry is None:
                                break
                            budget.entry()
                            with budget.operation("entry_stat"):
                                info = entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                                skipped.append(entry.path)
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                if depth < max_depth:
                                    todo.append((Path(entry.path), depth+1))
                                else:
                                    incomplete.append("depth")
                            elif stat.S_ISREG(info.st_mode) and entry.name.endswith(".jsonl"):
                                match = UUID.search(entry.name)
                                if match and match.group().lower() in wanted:
                                    candidates.setdefault(match.group().lower(), set()).add(Path(entry.path))
            except (OSError, UnsafePath) as exc:
                errors.append({"path": str(root), "type": type(exc).__name__})
                incomplete.append("native root unavailable")
            except Limit as exc:
                incomplete.append(str(exc))
                break
    with budget.phase("archive_manifests"):
        for manifest in manifests if wanted and not budget.stopped else ():
            path = absolute(manifest)
            try:
                budget.entry()
                if checked_stat(path, budget).st_size > 2*1024**2:
                    incomplete.append("manifest exceeds 2MiB")
                    continue
                with budget.operation("manifest_read_parse"):
                    with path.open("rb") as stream:
                        raw = stream.read(2*1024**2+1)
                    if len(raw)>2*1024**2:
                        raise ValueError("Manifest grew beyond bound")
                    rows = json.loads(raw)
                if not isinstance(rows, list):
                    raise ValueError("Archive manifest must use the existing list schema")
                for item in rows:
                    budget.entry()
                    if not isinstance(item, dict):
                        continue
                    native_id = str(item.get("session_id", "")).lower()
                    archived = item.get("archive_path")
                    if native_id not in wanted or not isinstance(archived, str):
                        continue
                    candidate = absolute(archived)
                    if inside(candidate, archive_roots):
                        candidates.setdefault(native_id, set()).add(candidate)
                    else:
                        errors.append({"path": str(candidate), "type": "manifest_path_outside_declared_roots"})
                        incomplete.append("manifest scope")
            except (OSError, ValueError) as exc:
                errors.append({"path": str(path), "type": type(exc).__name__})
                incomplete.append("manifest unavailable")
            except Limit as exc:
                incomplete.append(str(exc))
                break
    with budget.phase("candidate_headers"):
        for row in records:
            if row["status"] != "pending":
                continue
            verified, uncertain, denied = [], [], False
            for candidate in sorted(candidates.get(row["native_id"].lower(), set())):
                if budget.stopped:
                    break
                try:
                    budget.entry()
                    if header_id(candidate, budget) == row["native_id"].lower():
                        verified.append(str(candidate))
                    else:
                        uncertain.append(str(candidate))
                except FileNotFoundError:
                    continue
                except PermissionError:
                    denied = True
                    incomplete.append("candidate permission blocked")
                    break
                except (OSError, UnsafePath) as exc:
                    errors.append({"path": str(candidate), "type": type(exc).__name__})
                    uncertain.append(str(candidate))
                except Limit as exc:
                    incomplete.append(str(exc))
                    break
            row["verified_candidates"], row["unverified_candidates"] = verified, uncertain
            row["candidate_search_complete"] = not incomplete and not skipped and not budget.stopped
            row["status"] = (
                "permission_blocked" if denied else
                "located_multiple_candidates" if len(verified)>1 else
                "located_alternate_native_id" if verified else
                "candidate_identity_unverified" if uncertain else
                "lookup_incomplete" if incomplete or skipped or budget.stopped else
                "not_located_in_declared_roots")
    with budget.phase("statistics"):
        if time.monotonic()-budget.started >= budget.max_seconds:
            incomplete.append("time before statistics")
        counts = dict(Counter(r["status"] for r in records))
        result = {"schema": "guardian.session_asset_readonly_audit.v2",
            "observed_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
            "automatic_cleanup_allowed": False,
            "protection": "All native session assets protected; no deletion, repair, or restoration eligibility inferred.",
            "identity_key": "declared native account home + native UUID; copies not assumed identical",
            "indexed_rows": rows_seen if index_complete else None,
            "indexed_rows_lower_bound": rows_seen, "audited_rows": len(records), "counts": counts,
            "coverage": {"native_roots": [str(p) for p in roots], "archive_roots": [str(p) for p in archive_roots],
                "index_complete": index_complete, "index_has_more_rows": more_rows,
                "index_rows_not_audited": len(selected_rows)-len(records),
                "complete_within_declared_scope": index_complete and not incomplete and not errors and not skipped,
                "incomplete_reasons": sorted(set(incomplete)), "errors": errors, "skipped_links": skipped,
                "entries": budget.entries, "header_bytes": budget.header_bytes,
                "budget": {"entries": budget.max_entries, "seconds": budget.max_seconds,
                    "header_bytes": budget.max_header_bytes, "rows": max_rows, "depth": max_depth,
                    "sql_steps": budget.max_sql_steps, "sql_page_rows": 64},
                "body_or_restore_verification": False,
                "time_budget_semantics": "soft wall budget including SQL/metadata/header/statistics/serialization/output; blocking OS I/O and output cannot be interrupted in process; finalization of bounded partial results may overrun",
                "output_completion_reported_on_stderr": True}, "records": records}
    result["coverage"].update(budget.telemetry())
    return result

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, action="append", required=True)
    parser.add_argument("--archive-root", type=Path, action="append", default=[])
    parser.add_argument("--archive-manifest", type=Path, action="append", default=[])
    parser.add_argument("--max-entries", type=int, default=15000)
    parser.add_argument("--max-seconds", type=float, default=10)
    parser.add_argument("--max-header-bytes", type=int, default=262144)
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--max-sql-steps", type=int, default=100000)
    a = parser.parse_args(argv)
    try:
        budget = Budget(a.max_entries, a.max_seconds, a.max_header_bytes, max_sql_steps=a.max_sql_steps)
        result = audit(a.db, a.native_root, a.archive_root, a.archive_manifest, budget, a.max_rows, a.max_depth)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"read_only": True, "automatic_cleanup_allowed": False, "error": type(exc).__name__}))
        return 2
    from omnicompany.cli.commands.guardian_asset_audit import emit
    from click.exceptions import Exit
    try:
        emit(result, a.summary, budget)
    except Exit as exc:
        return exc.exit_code
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

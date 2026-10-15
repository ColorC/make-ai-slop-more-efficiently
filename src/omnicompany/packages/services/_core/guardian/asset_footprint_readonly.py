# [OMNI] summary="Bounded Guardian directory and recording metadata inventory."
# [OMNI] why="Attribute storage pressure and candidate copies without cleanup eligibility."
"""Explicit roots only; file metadata, no content hashing or destructive actions."""
from collections import defaultdict
from contextlib import ExitStack
import os
from pathlib import Path
import stat
import time
from datetime import datetime, timezone

try:
    from .session_asset_readonly_audit import Budget, Limit, UnsafePath, absolute, checked_stat
except ImportError:
    from session_asset_readonly_audit import Budget, Limit, UnsafePath, absolute, checked_stat

VIDEO = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
IMAGE = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
FRAME_LEDGERS = {"video-frames.ndjson"}

def inventory(roots, protected_paths=(), budget=None, max_depth=8, min_copy_bytes=50*1024**2):
    budget = budget or Budget()
    roots = [absolute(p) for p in roots]
    protected = [absolute(p) for p in protected_paths]
    if not (0 <= max_depth <= 32 and min_copy_bytes >= 1 and 1 <= budget.max_entries <= 100000 and 0 < budget.max_seconds <= 60) or not 1 <= len(roots) <= 32 or len(protected)>64:
        raise ValueError("Invalid bounded footprint declaration")
    reports, candidates, largest = [], defaultdict(list), []
    # Overlapping declarations must not double count the same physical tree.
    accepted = []
    with budget.phase("directory_enumeration"):
        for root in roots:
            if budget.stopped:
                reports.append({"root": str(root), "complete": False, "not_scanned": True, "reason": budget.stopped})
                continue
            if any(root == r or root.is_relative_to(r) or r.is_relative_to(root) for r in accepted):
                reports.append({"root": str(root), "complete": False, "reason": "overlapping declared roots; skipped"})
                continue
            accepted.append(root)
            buckets = defaultdict(lambda: {"files": 0, "logical_bytes": 0, "video_files": 0, "video_bytes": 0, "image_files": 0, "frame_ledger_files": 0, "frame_ledger_bytes": 0})
            report = {"root": str(root), "complete": True, "files": 0, "logical_bytes": 0,
                      "links_skipped": [], "hardlinked_files": 0, "errors": [], "depth_limited": 0,
                      "protected_anchors": [str(p) for p in protected if p == root or p.is_relative_to(root) or root.is_relative_to(p)]}
            reports.append(report)
            stack = [(root, 0)]
            try:
                checked_stat(root, budget)
                while stack:
                    path, depth = stack.pop()
                    budget.check()
                    checked_stat(path, budget)
                    try:
                        with ExitStack() as resources:
                            with budget.operation("scandir_open"):
                                entries = resources.enter_context(os.scandir(path))
                            while True:
                                with budget.operation("scandir_next"):
                                    entry = next(entries, None)
                                if entry is None:
                                    break
                                budget.entry()
                                with budget.operation("entry_stat"):
                                    info = entry.stat(follow_symlinks=False)
                                if stat.S_ISLNK(info.st_mode) or getattr(info,"st_file_attributes",0) & 0x400:
                                    report["links_skipped"].append(entry.path)
                                    report["complete"] = False
                                    continue
                                if stat.S_ISDIR(info.st_mode):
                                    if depth < max_depth:
                                        stack.append((Path(entry.path), depth+1))
                                    else:
                                        report["depth_limited"] += 1
                                        report["complete"] = False
                                elif stat.S_ISREG(info.st_mode):
                                    relative = Path(entry.path).relative_to(root)
                                    key = relative.parts[0] if len(relative.parts)>1 else "."
                                    bucket = buckets[key]
                                    size = info.st_size
                                    report["files"] += 1
                                    report["logical_bytes"] += size
                                    bucket["files"] += 1
                                    bucket["logical_bytes"] += size
                                    if getattr(info, "st_nlink", 1)>1:
                                        report["hardlinked_files"] += 1
                                    suffix = Path(entry.name).suffix.lower()
                                    if suffix in VIDEO:
                                        bucket["video_files"] += 1
                                        bucket["video_bytes"] += size
                                    if suffix in IMAGE:
                                        bucket["image_files"] += 1
                                    is_frame_ledger = entry.name.casefold() in FRAME_LEDGERS
                                    if is_frame_ledger:
                                        bucket["frame_ledger_files"] += 1
                                        bucket["frame_ledger_bytes"] += size
                                    if size >= min_copy_bytes:
                                        item = {"path": entry.path, "bytes": size, "video_like": suffix in VIDEO, "known_frame_ledger_name": is_frame_ledger, "protected": True}
                                        candidates[(entry.name.casefold(), size)].append(item)
                                        largest.append(item)
                    except (OSError, UnsafePath) as exc:
                        report["errors"].append({"path": str(path), "type": type(exc).__name__})
                        report["complete"] = False
            except (OSError, UnsafePath) as exc:
                report["errors"].append({"path": str(root), "type": type(exc).__name__})
                report["complete"] = False
            except Limit as exc:
                report["budget_exhausted"] = str(exc)
                report["complete"] = False
            report["first_level"] = sorted(({"name":k, **v} for k,v in buckets.items()), key=lambda r:r["logical_bytes"], reverse=True)[:20]
            report["size_semantics"] = "logical-byte sum; lower bound when partial; not physical allocation or reclaimable bytes"
    with budget.phase("statistics"):
        duplicates = [{"name":name, "bytes_per_file":size, "candidates":items[:8], "candidate_count":len(items),
                       "confidence":"same basename and size only; contents and provenance unverified; no deletion eligibility"}
                      for (name,size),items in candidates.items() if len(items)>1]
        duplicates.sort(key=lambda x:x["bytes_per_file"]*x["candidate_count"], reverse=True)
    result = {"schema":"guardian.asset_footprint_readonly.v2",
            "observed_at":datetime.now(timezone.utc).isoformat(),
            "read_only":True, "automatic_cleanup_allowed":False,
            "coverage":{"entries":budget.entries,"elapsed_seconds":round(time.monotonic()-budget.started,4),
                        "complete_within_declared_scope":all(r.get("complete") for r in reports) and time.monotonic()-budget.started < budget.max_seconds,
                        "budget":{"entries":budget.max_entries,"seconds":budget.max_seconds,"depth":max_depth},
                        "time_budget_semantics":"soft wall budget; blocking OS I/O/output cannot be interrupted in process; bounded statistics and partial-result finalization can overrun",
                        "incomplete_reasons": sorted({r.get("budget_exhausted", r.get("reason", "partial root")) for r in reports if not r.get("complete")} | ({"time during finalization"} if time.monotonic()-budget.started>=budget.max_seconds else set())),
                        "output_completion_reported_on_stderr":True},
            "roots":reports, "largest_files":sorted(largest,key=lambda x:x["bytes"],reverse=True)[:20],
            "possible_copies":duplicates[:10],
            "interpretation":"Extensions count media-shaped files only; recording purpose, ownership, references and recovery are not inferred."}

    result["coverage"].update(budget.telemetry())
    return result


class ResumableFootprint:
    """Source metadata only; SQLite is a caller-declared output checkpoint.

    Directory rows are never removed before enumeration finishes. Seen entry
    keys and aggregate updates commit together, so interruption cannot lose a
    consumed child or double-count a resumed prefix. No filesystem snapshot,
    physical allocation, content comparison or cleanup claim is made.
    """
    SCHEMA = "guardian.footprint.checkpoint.v1"

    def __init__(self, checkpoint, roots, *, excluded_paths=()):
        import json
        import sqlite3
        self.checkpoint = absolute(checkpoint)
        accepted = []
        for root in sorted(set(absolute(p) for p in roots), key=lambda p: (len(p.parts), os.path.normcase(str(p)))):
            if not any(root == p or root.is_relative_to(p) for p in accepted):
                accepted.append(root)
        if not 1 <= len(accepted) <= 32 or len(excluded_paths) > 64:
            raise ValueError("Declare 1-32 nonoverlapping roots and at most64 exclusions")
        self.roots = accepted
        self.excluded = [absolute(p) for p in excluded_paths]
        if any(root==p or root.is_relative_to(p) for root in accepted for p in self.excluded):
            raise ValueError("A measured root is within an explicit exclusion")
        if any(self.checkpoint == p or self.checkpoint.is_relative_to(p) for p in accepted):
            raise ValueError("Checkpoint must be outside measured roots")
        for part in [*reversed(self.checkpoint.parents), self.checkpoint]:
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise UnsafePath("Checkpoint ancestor is a link/reparse point")
        if not self.checkpoint.parent.is_dir():
            raise ValueError("Checkpoint parent must already exist")
        config = json.dumps({"schema": self.SCHEMA,
            "roots": [str(p) for p in accepted],
            "excluded_paths": sorted(str(p) for p in self.excluded)}, sort_keys=True)
        if self.checkpoint.exists():
            from contextlib import closing
            with closing(sqlite3.connect(self.checkpoint.as_uri()+"?mode=ro", uri=True)) as check:
                row = check.execute("SELECT value FROM metadata WHERE key='config'").fetchone()
                if not row or row[0] != config:
                    raise ValueError("Checkpoint scope/schema differs; use a distinct output checkpoint")
        self.db = sqlite3.connect(self.checkpoint, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS directories(path TEXT PRIMARY KEY,root TEXT,bucket TEXT,
                depth INTEGER,state TEXT,reason TEXT);
            CREATE INDEX IF NOT EXISTS directory_state ON directories(state,depth);
            CREATE INDEX IF NOT EXISTS directory_root_bucket ON directories(root,bucket,state);
            CREATE TABLE IF NOT EXISTS entries(path TEXT PRIMARY KEY,root TEXT,bucket TEXT,
                kind TEXT,logical_bytes INTEGER,reason TEXT);
            CREATE INDEX IF NOT EXISTS entry_size ON entries(logical_bytes DESC);
            CREATE TABLE IF NOT EXISTS groups(bucket TEXT PRIMARY KEY,root TEXT,files INTEGER DEFAULT 0,
                logical_bytes INTEGER DEFAULT 0,entry_errors INTEGER DEFAULT 0,
                links INTEGER DEFAULT 0,excluded INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS group_root ON groups(root);
        """)
        if not self.db.execute("SELECT 1 FROM metadata WHERE key='config'").fetchone():
            self.db.execute("INSERT INTO metadata VALUES('config',?)", (config,))
            self.db.execute("INSERT INTO metadata VALUES('started_at',?)", (datetime.now(timezone.utc).isoformat(),))
            for root in accepted:
                key = self.key(root)
                self.db.execute("INSERT INTO directories VALUES(?,?,?,?,?,NULL)", (key,key,key,0,"queued"))
                self.db.execute("INSERT INTO groups(bucket,root) VALUES(?,?)", (key,key))
        self.db.commit()
        self.current = None
        self.cursor = None
        self.entry = None
        self.last_reason = None

    @staticmethod
    def key(path):
        return os.path.normcase(str(absolute(path)))

    def close(self):
        self._close_cursor()
        self.db.close()

    def _close_cursor(self):
        if self.cursor is not None:
            self.cursor.close()
        self.current = self.cursor = self.entry = None

    def _pending(self):
        return self.db.execute("SELECT COUNT(*) FROM directories WHERE state IN ('queued','active')").fetchone()[0]

    def _promote(self, maximum, max_depth, focus=None):
        slots = maximum-self._pending()
        if slots <= 0:
            return
        clause, values = self._focus_clause(focus)
        rows = self.db.execute("SELECT path FROM directories WHERE state IN ('deferred-capacity','deferred-depth') AND depth<=?"+clause+" ORDER BY depth DESC LIMIT?", [max_depth,*values,slots]).fetchall()
        self.db.executemany("UPDATE directories SET state='queued',reason=NULL WHERE path=?", [(r[0],) for r in rows])

    def _focus_clause(self, focus):
        if not focus:
            return "", []
        target = absolute(focus)
        owner = next((root for root in self.roots if target==root or target.is_relative_to(root)), None)
        if owner is None:
            raise ValueError("Focus is outside declared roots")
        key = self.key(target)
        ancestors = [self.key(parent) for parent in target.parents if parent==owner or parent.is_relative_to(owner)]
        prefix = key+os.sep
        clause = " AND root=? AND (path=? OR (path>=? AND path<?)"
        args = [self.key(owner),key,prefix,prefix+chr(0x10ffff)]
        if ancestors:
            clause += " OR path IN ("+",".join("?" for _ in ancestors)+")"
            args += ancestors
        return clause+")", args

    def _pick(self, *, discover_only, focus):
        sql = "SELECT * FROM directories WHERE state IN ('queued','active')"
        args = []
        if discover_only:
            sql += " AND depth=0"
        clause, values = self._focus_clause(focus)
        sql += clause
        args += values
        sql += " ORDER BY depth DESC,path DESC LIMIT 1"
        return self.db.execute(sql,args).fetchone()

    def _record(self, path, root, bucket, kind, size=0, reason=None):
        inserted = self.db.execute("INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?)",
            (self.key(path),root,bucket,kind,size,reason)).rowcount
        if not inserted:
            return
        self.db.execute("INSERT OR IGNORE INTO groups(bucket,root) VALUES(?,?)", (bucket,root))
        self.db.execute("UPDATE groups SET files=files+?,logical_bytes=logical_bytes+?,entry_errors=entry_errors+?,links=links+?,excluded=excluded+? WHERE bucket=?",
            (int(kind=="file"),size,int(kind=="error"),int(kind=="link"),int(kind=="excluded"),bucket))

    def scan_chunk(self, *, budget=None, max_depth=32, max_pending=256, discover_only=False, focus=None):
        budget = budget or Budget(max_entries=4000,max_seconds=3)
        if not 0<=max_depth<=32 or not 1<=max_pending<=10000 or not 1<=budget.max_entries<=100000 or not 0<budget.max_seconds<=60:
            raise ValueError("Invalid bounded resume declaration")
        enumerated = skipped = 0
        # Entry quota is for newly processed metadata. Reopened seen prefixes
        # have a separate finite quota and share the same wall-time budget.
        enumeration_limit = min(100000,max(4096,budget.max_entries*4))
        reason = None
        self._focus_clause(focus)  # Validate before mutating checkpoint state.
        compatible = True
        if self.current and focus:
            current = Path(self.current['path']); target = absolute(focus)
            compatible = current==target or current.is_relative_to(target) or target.is_relative_to(current)
        if self.current and (self.last_reason=="frontier-capacity" or
                (discover_only and self.current['depth']!=0) or not compatible):
            self._close_cursor()
        try:
            if focus:
                clause, values = self._focus_clause(focus)
                # Even an entirely deferred target must release unrelated slots.
                # Use the scope predicate directly; no unbounded path list.
                self.db.execute("UPDATE directories SET state='deferred-capacity',reason='focus postponed' WHERE state IN ('queued','active') AND NOT ("+clause.removeprefix(" AND ")+")", values)
            self.db.execute("UPDATE directories SET state='queued' WHERE state='active'"+(" AND path!=?" if self.current else ""), ([self.current['path']] if self.current else []))
            active = self.db.execute("SELECT COUNT(*) FROM directories WHERE state='active'").fetchone()[0]
            excess = self.db.execute("SELECT path FROM directories WHERE state='queued' ORDER BY depth DESC LIMIT -1 OFFSET?", (max(0,max_pending-active),)).fetchall()
            self.db.executemany("UPDATE directories SET state='deferred-capacity',reason='max_pending' WHERE path=?", [(r[0],) for r in excess])
            self._promote(max_pending,max_depth,focus)
            while True:
                budget.check()
                if self.current is None:
                    self.current = self._pick(discover_only=discover_only,focus=focus)
                    if self.current is None:
                        break
                row = self.current
                path = Path(row['path'])
                if self.cursor is None:
                    # State retained on budget interruption before/during open.
                    self.db.execute("UPDATE directories SET state='active' WHERE path=?", (row['path'],))
                    try:
                        checked_stat(path,budget)
                        with budget.operation("resume_scandir_open"):
                            self.cursor = os.scandir(path)
                    except (OSError,UnsafePath) as error:
                        self.db.execute("UPDATE directories SET state='blocked',reason=? WHERE path=?", (type(error).__name__,row['path']))
                        self._close_cursor()
                        self._promote(max_pending,max_depth,focus)
                        continue
                if self.entry is None:
                    if enumerated>=enumeration_limit:
                        raise Limit("reopened-prefix enumeration quota")
                    try:
                        with budget.operation("resume_scandir_next"):
                            self.entry = next(self.cursor,None)
                    except OSError as error:
                        self.db.execute("UPDATE directories SET state='blocked',reason=? WHERE path=?", (type(error).__name__,row['path']))
                        self._close_cursor()
                        continue
                    if self.entry is None:
                        self.db.execute("UPDATE directories SET state='done',reason=NULL WHERE path=?", (row['path'],))
                        self._close_cursor()
                        self._promote(max_pending,max_depth,focus)
                        continue
                    enumerated += 1
                entry = self.entry
                key = self.key(entry.path)
                if self.db.execute("SELECT 1 FROM entries WHERE path=?", (key,)).fetchone():
                    skipped += 1
                    self.entry = None
                    continue
                budget.entry()
                child = Path(entry.path)
                relative = child.relative_to(Path(row['root']))
                bucket = self.key(Path(row['root'])/relative.parts[0]) if len(relative.parts)>1 else row['root']
                if any(child==p or child.is_relative_to(p) for p in self.excluded):
                    bucket = self.key(Path(row['root'])/relative.parts[0])
                    self._record(child,row['root'],bucket,"excluded",reason="declared exclusion; subtree not entered")
                    self.entry = None
                    continue
                try:
                    with budget.operation("resume_entry_stat"):
                        info = entry.stat(follow_symlinks=False)
                except OSError as error:
                    self._record(child,row['root'],bucket,"error",reason=type(error).__name__)
                    self.entry = None
                    continue
                if len(relative.parts)==1 and stat.S_ISDIR(info.st_mode):
                    bucket = self.key(child)
                if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                    self._record(child,row['root'],bucket,"link",reason="target not followed")
                elif stat.S_ISDIR(info.st_mode):
                    depth = row['depth']+1
                    state = "deferred-depth" if depth>max_depth else ("queued" if self._pending()<max_pending else "deferred-capacity")
                    # Child and seen key committed atomically; capacity does
                    # not silently discard any discovered subtree.
                    self.db.execute("INSERT OR IGNORE INTO directories VALUES(?,?,?,?,?,?)", (key,row['root'],bucket,depth,state,"max_depth" if state=="deferred-depth" else "max_pending" if state=="deferred-capacity" else None))
                    self._record(child,row['root'],bucket,"directory")
                    if state=="deferred-capacity":
                        self.entry = None
                        reason = "frontier-capacity"
                        break
                elif stat.S_ISREG(info.st_mode):
                    self._record(child,row['root'],bucket,"file",info.st_size)
                else:
                    self._record(child,row['root'],bucket,"other")
                self.entry = None
        except Limit as limit:
            reason = str(limit)
        finally:
            self.last_reason = reason
            self.db.commit()
        result = self.snapshot()
        result['coverage'].update({'slice_new_entries':budget.entries,'slice_enumerated_entries':enumerated,
            'slice_seen_prefix_skips':skipped,'slice_stop_reason':reason,
            'slice_seconds':round(time.monotonic()-budget.started,4),
            'budget':{'new_entries':budget.max_entries,'seconds':budget.max_seconds,'max_enumerated_entries':enumeration_limit,'depth':max_depth,'max_pending':max_pending},
            'time_budget_semantics':'soft wall deadline; blocking OS I/O and checkpoint/report finalization may overrun; seen prefix replay explicitly bounded'})
        result['coverage'].update(budget.telemetry())
        return result

    def snapshot(self):
        roots = []
        overall_pending = overall_errors = overall_links = overall_excluded = 0
        for root in self.roots:
            key = self.key(root)
            states = dict(self.db.execute("SELECT state,COUNT(*) FROM directories WHERE root=? GROUP BY state", (key,)).fetchall())
            sums = self.db.execute("SELECT COALESCE(SUM(files),0),COALESCE(SUM(logical_bytes),0),COALESCE(SUM(entry_errors),0),COALESCE(SUM(links),0),COALESCE(SUM(excluded),0) FROM groups WHERE root=?", (key,)).fetchone()
            pending = sum(n for s,n in states.items() if s not in ('done','blocked'))
            errors = sums[2]+states.get('blocked',0)
            groups = []
            for group in self.db.execute("SELECT * FROM groups WHERE root=? ORDER BY logical_bytes DESC LIMIT 256", (key,)):
                counts = dict(self.db.execute("SELECT state,COUNT(*) FROM directories WHERE root=? AND bucket=? GROUP BY state", (key,group['bucket'])).fetchall())
                groups.append({**dict(group),'directory_states':counts,
                    'complete_within_declared_scope':not any(s!='done' and n for s,n in counts.items()) and not group['entry_errors'],
                    'whole_namespace_complete':not any(s!='done' and n for s,n in counts.items()) and not (group['entry_errors'] or group['links'] or group['excluded'])})
            roots.append({'root':str(root),'files':sums[0],'logical_bytes':sums[1],
                'directory_states':states,'pending_subtrees':pending,'read_errors':errors,'links_skipped':sums[3],'excluded_subtrees':sums[4],
                'root_directory_enumerated':self.db.execute("SELECT state FROM directories WHERE path=?",(key,)).fetchone()[0]=='done',
                'complete_within_declared_scope':not (pending or errors),
                'whole_namespace_complete':not (pending or errors or sums[3] or sums[4]),
                'first_level':groups,'first_level_total':self.db.execute("SELECT COUNT(*) FROM groups WHERE root=?",(key,)).fetchone()[0]})
            overall_pending += pending;overall_errors += errors;overall_links += sums[3];overall_excluded += sums[4]
        deferred = [dict(row) for row in self.db.execute("SELECT path,root,bucket,depth,state,reason FROM directories WHERE state!='done' ORDER BY depth DESC LIMIT 32")]
        return {'schema':self.SCHEMA,'observed_at':datetime.now(timezone.utc).isoformat(),
            'started_at':self.db.execute("SELECT value FROM metadata WHERE key='started_at'").fetchone()[0],
            'read_only':True,'writes_only_declared_metadata_checkpoint':str(self.checkpoint),
            'automatic_cleanup_allowed':False,
            'size_semantics':'observed logical namespace bytes; not allocated, reclaimable or hardlink-deduplicated physical bytes; not an instantaneous filesystem snapshot',
            'coverage':{'complete_within_declared_scope':not (overall_pending or overall_errors),
                'whole_namespace_complete':not (overall_pending or overall_errors or overall_links or overall_excluded),
                'pending_subtrees':overall_pending,'read_errors':overall_errors,'links_skipped':overall_links,'excluded_subtrees':overall_excluded,
                'unprocessed_subtree_examples':deferred,'all_unprocessed_paths_persisted_in_checkpoint':True,
                'resume_semantics':'active directory rows retained; live cursor kept across slices; restart reopens and skips committed entry keys; prefix replay may need a larger bounded slice'},
            'roots':roots,
            'largest_files':[dict(row) for row in self.db.execute("SELECT path,root,logical_bytes FROM entries WHERE kind='file' ORDER BY logical_bytes DESC LIMIT 20")]}

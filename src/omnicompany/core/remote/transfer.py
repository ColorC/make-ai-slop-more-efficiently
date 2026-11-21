# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote push/pull/share: 白名单、覆盖前备份、从不删除、写后直接比对字节（沿用 rnode 的做法, 去掉写死的机器常量；不算摘要）。"
"""``omni remote push`` / ``pull``.

Rules carried over from ``demogame-autochess/tools/rnode/rnode.py``:

* push writes only under the profile's ``write_roots`` (checked here *and* on the node);
* an existing file with different bytes is copied to ``backup_root\\<op_id>\\`` first;
* nothing is ever deleted at the destination; every write is read back and compared byte for byte;
* no digests anywhere (C:/workspace/AGENTS.md「版本依据」forbids hash-based "same content" judgements
  and digest gates); transport payloads are checked for truncation by byte count only;
* transport stages are removed entry by entry from an exact inventory, never recursively.
"""
from __future__ import annotations

import json
import ntpath
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

from .backends import TransportError
from .ops import _deliver, _native_failure, _stage
from .profiles import NodeProfile
from .pstext import RemoteRefused, ps_quote, render
from .session import Session
from .store import LocalBatch, assert_local_plain, home


def under_roots(path: str, roots: list[str] | tuple[str, ...]) -> bool:
    target = ntpath.normcase(ntpath.normpath(path)).rstrip("\\")
    for root in roots:
        base = ntpath.normcase(ntpath.normpath(root)).rstrip("\\")
        if target == base or target.startswith(base + "\\"):
            return True
    return False


def _local_files(root: Path) -> list[Path]:
    files = []
    assert_local_plain(root)
    for current, directories, names in os.walk(root, followlinks=False):
        for name in directories:
            assert_local_plain(Path(current) / name)
        for name in names:
            path = assert_local_plain(Path(current) / name)
            if not path.is_file():
                raise RemoteRefused("input", f"不是普通文件, 拒绝传输: {path}")
            files.append(path)
    return sorted(files)


def _danger_scan_scripts(profile: NodeProfile, files: list[Path], rels: list[str]) -> list[dict[str, Any]]:
    """Pushed scripts get the same static danger rules as run/spawn bodies (block refuses the whole push)."""
    from . import danger

    warnings: list[dict[str, Any]] = []
    for path, rel in zip(files, rels):
        if path.suffix.lower() not in danger.SCRIPT_SUFFIXES or path.stat().st_size > 4 * 1024 * 1024:
            continue
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        for hit in danger.enforce(text, op="push", node=profile.name, what=f"推送脚本 {rel} ",
                                  write_roots=profile.write_roots, owned_roots=profile.owned_roots):
            warnings.append({**hit, "file": rel})
    return warnings


def push(profile: NodeProfile, backend: Any, sources: list[Path], remote_dir: str, *, into: bool = False) -> dict[str, Any]:
    srcs = [assert_local_plain(Path(p)) for p in sources]
    for path in srcs:
        if not path.exists():
            raise RemoteRefused("input", f"本地不存在: {path}")
    if len(srcs) > 1:
        if not all(p.is_file() for p in srcs):
            raise RemoteRefused("input", "一次给多个本地路径时只能是文件；推目录请单独给目录")
        names = [p.name.casefold() for p in srcs]
        if len(set(names)) != len(names):
            raise RemoteRefused("input", "多个本地文件同名, 远端会互相覆盖")
    single = srcs[0] if len(srcs) == 1 else None
    dest = ntpath.normpath(remote_dir.replace("/", "\\"))
    tail = ntpath.basename(dest.rstrip("\\"))
    if single and single.is_file() and tail.lower() == single.name.lower():
        dest = ntpath.dirname(dest)
    if single and single.is_dir() and not into and tail.lower() != single.name.lower():
        raise RemoteRefused("input", f"推目录 {single.name} 时远端路径要以同名目录结尾, 例如 {dest}\\{single.name}；"
                            f"确实要把内容直接放进 {tail}, 加 --into。什么都没写。")
    if not under_roots(dest, profile.write_roots):
        raise RemoteRefused("write_roots", f"远端目标 {dest} 不在节点 {profile.name} 的写入白名单 {list(profile.write_roots)} 内, 什么都没写",
                            dest=dest, write_roots=list(profile.write_roots))
    if single and single.is_dir():
        files = _local_files(single)
        rels = [p.relative_to(single).as_posix() for p in files]
    else:
        files, rels = srcs, [p.name for p in srcs]
    return _push_files(profile, backend, files, rels, dest, op="push")


def share(profile: NodeProfile, backend: Any, sources: list[Path], *, name: str | None = None) -> dict[str, Any]:
    """Put local files/dirs where the user can open them on that machine: ``<share_root>\\<name>\\``.

    Files land by their own name, dirs keep their folder name and layout. Sharing the same name again
    syncs in place with push's rules (unchanged skipped, changed backed up first, nothing deleted).
    """
    if not profile.share_root:
        raise RemoteRefused("profile", f"节点 {profile.name} 没配 share_root（config/remote_nodes.toml），不知道往哪放；什么都没写")
    srcs = [assert_local_plain(Path(p)) for p in sources]
    for path in srcs:
        if not path.exists():
            raise RemoteRefused("input", f"本地不存在: {path}")
    folder = (name or srcs[0].stem).strip()
    if not folder or any(ch in folder for ch in '\\/:*?"<>|') or folder in {".", ".."}:
        raise RemoteRefused("input", f"共享名只能是一层目录名: {folder!r}")
    files: list[Path] = []
    rels: list[str] = []
    for path in srcs:
        if path.is_dir():
            inner = _local_files(path)
            files += inner
            rels += [f"{path.name}/{p.relative_to(path).as_posix()}" for p in inner]
        else:
            files.append(path)
            rels.append(path.name)
    if not files:
        raise RemoteRefused("input", "没有可共享的文件")
    keys = [rel.casefold() for rel in rels]
    if len(set(keys)) != len(keys):
        dup = sorted({rel for rel in rels if keys.count(rel.casefold()) > 1})
        raise RemoteRefused("input", f"共享后会重名互相覆盖: {dup[:5]}")
    dest = ntpath.join(profile.share_root, folder)
    return _push_files(profile, backend, files, rels, dest, op="share")


def _push_files(profile: NodeProfile, backend: Any, files: list[Path], rels: list[str], dest: str, *, op: str) -> dict[str, Any]:
    manifest = [{"rel": rel, "size": p.stat().st_size} for p, rel in zip(files, rels)]
    danger_warnings = _danger_scan_scripts(profile, files, rels)
    session = Session(profile, backend, op, label=f"{op} -> {dest}")
    batch = LocalBatch(session.op_id, "push payload")
    zip_path = batch.path("payload.zip")
    manifest_path = batch.path("manifest.json")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, rel in zip(files, rels):
            archive.write(path, rel)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    stage = session.r(ntpath.join(profile.staging_root, "in", session.op_id))
    backup = session.r(ntpath.join(profile.backup_root, session.op_id))
    tokens = {"DEST": ps_quote(session.r(dest)), "STAGE": ps_quote(stage), "BACKUP": ps_quote(backup),
              "ZIP_BYTES": str(zip_path.stat().st_size), "MANIFEST_BYTES": str(len(manifest_bytes))}
    transport_name = None
    try:
        with backend.transport() as transport:
            transport_name = transport.name
            if transport.kind == "in-task":
                fetch = "\n".join([
                    f"    Invoke-WebRequest -UseBasicParsing -Uri {ps_quote(transport.offer(zip_path))} -OutFile $zip",
                    f"    Invoke-WebRequest -UseBasicParsing -Uri {ps_quote(transport.offer(manifest_path))} -OutFile $manifestPath",
                ])
                got = session.exec(render("push.ps1", {**tokens, "MODE": ps_quote("http"), "FETCH": fetch}), timeout_s=1800, label=op)
            else:
                _deliver(session, transport, batch, session.r(ntpath.join(profile.staging_root, "in")), stage)
                got = session.exec(render("push.ps1", {**tokens, "MODE": ps_quote("prestaged"), "FETCH": ""}), timeout_s=1800, label=op)
    except (TransportError, OSError, ValueError) as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "first_error": {"message": str(error)},
                               "transport": transport_name, "local_cleanup": batch.cleanup()})
    data = got["data"] if isinstance(got["data"], dict) else None
    cleanup = batch.cleanup()
    if data is None:
        phase, code = _native_failure(got)
        return session.finish({"ok": False, "phase": phase, "exit_code": code, "transport": transport_name, "local_cleanup": cleanup,
                               "first_error": {"message": (got["native"].get("stderr") or "")[-2000:]}})
    ok = bool(data.get("ok"))
    return session.finish({
        "ok": ok, "phase": "completed" if ok else "failed", "exit_code": 0 if ok else 1, "transport": transport_name,
        "first_error": None if ok else {"message": data.get("error")}, "data": data, "local_cleanup": cleanup,
        "danger_warnings": danger_warnings,
        "paths": {"remote_dest": data.get("dest"), "remote_backup": data.get("backup"), "remote_stage": stage},
    }, ledger_extra={"files": len(manifest), "new": data.get("new"), "updated": data.get("updated"), "unchanged": data.get("unchanged")})


def _zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    for info in archive.infolist():
        rel = info.filename.replace("\\", "/")
        parts = rel.rstrip("/").split("/")
        if not rel or rel.startswith("/") or any(part in {"", ".", ".."} or ":" in part for part in parts):
            raise ValueError(f"omni-remote: unsafe zip entry: {info.filename}")
        if rel.endswith("/"):
            continue
        key = rel.casefold()
        if key in seen:
            raise ValueError(f"omni-remote: duplicate/case-colliding zip entry: {info.filename}")
        seen.add(key)
        members[rel] = info
    return members


def pull(profile: NodeProfile, backend: Any, remote_src: str, local_dir: Path) -> dict[str, Any]:
    local_dir = assert_local_plain(Path(local_dir))
    if local_dir.exists() and not local_dir.is_dir():
        raise RemoteRefused("input", f"本地目标不是目录: {local_dir}")
    session = Session(profile, backend, "pull", label=f"pull {remote_src}")
    batch = LocalBatch(session.op_id, "pull payload")
    zip_path = batch.path("payload.zip")
    batch.path("payload.zip.part")
    stage = session.r(ntpath.join(profile.staging_root, "out", session.op_id))
    tokens = {"SRC": ps_quote(session.r(remote_src)), "STAGE": ps_quote(stage)}
    transport_name = None
    received = False
    try:
        with backend.transport() as transport:
            transport_name = transport.name
            if transport.kind == "in-task":
                key, url = transport.expect(zip_path)
                send = f"  Invoke-WebRequest -UseBasicParsing -Method Put -InFile $zip -Uri {ps_quote(url)} | Out-Null\n  $out.sent = $true"
                got = session.exec(render("pull.ps1", {**tokens, "MODE": ps_quote("http"), "SEND": send}), timeout_s=1800, label="pull")
                received = transport.got(key)
            else:
                got = session.exec(render("pull.ps1", {**tokens, "MODE": ps_quote("keep"), "SEND": ""}), timeout_s=1800, label="pull")
                if isinstance(got["data"], dict) and got["data"].get("ok"):
                    try:
                        receipt = transport.copy_file_out(ntpath.join(stage, "payload.zip"), zip_path)
                        session.calls.append({"n": len(session.calls) + 1, "label": f"{transport.name}-out", "transport": receipt})
                        received = True
                    finally:
                        _stage(session, "cleanup", "", stage, ["payload.zip"])
    except (TransportError, OSError, ValueError) as error:
        return session.finish({"ok": False, "phase": "transport", "exit_code": None, "first_error": {"message": str(error)},
                               "transport": transport_name, "local_cleanup": batch.cleanup()})
    data = got["data"] if isinstance(got["data"], dict) else None
    if data is None or not data.get("ok") or not received:
        phase, code = _native_failure(got) if data is None else ("failed", 1)
        message = (data or {}).get("error") or (got["native"].get("stderr") or "")[-2000:] or f"payload received={received}"
        return session.finish({"ok": False, "phase": phase, "exit_code": code, "transport": transport_name, "data": data,
                               "first_error": {"message": message}, "local_cleanup": batch.cleanup()})
    try:
        files = _apply_pull(session, data, zip_path, local_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        return session.finish({"ok": False, "phase": "local-apply", "exit_code": 1, "transport": transport_name, "data": data,
                               "first_error": {"message": str(error)}, "local_cleanup": batch.cleanup()})
    counts = {k: sum(1 for f in files if f["action"] == k) for k in ("new", "updated", "unchanged")}
    backup = home() / "backup" / session.op_id
    data_out = {**data, "local_files": files, **counts}
    return session.finish({
        "ok": True, "phase": "completed", "exit_code": 0, "transport": transport_name, "data": data_out,
        "local_cleanup": batch.cleanup(),
        "paths": {"local_dest": str(local_dir), "local_backup": str(backup) if counts["updated"] else None, "remote_src": data.get("src")},
    }, ledger_extra={"files": len(files), **counts})


def _apply_pull(session: Session, data: dict[str, Any], zip_path: Path, local_dir: Path) -> list[dict[str, Any]]:
    if zip_path.stat().st_size != data.get("zip_bytes"):
        raise ValueError(f"omni-remote: received payload is {zip_path.stat().st_size} bytes, the node packed {data.get('zip_bytes')}")
    manifest = {str(f["rel"]): f for f in data.get("files") or []}
    remote_name = ntpath.basename(str(data.get("src") or "").rstrip("\\"))
    dest_root = local_dir
    if data.get("kind") == "dir" and remote_name and local_dir.name.lower() != remote_name.lower():
        dest_root = local_dir / remote_name
    backup_root = home() / "backup" / session.op_id
    plan = []
    with zipfile.ZipFile(zip_path) as archive:
        members = _zip_members(archive)
        if set(members) != set(manifest):
            raise ValueError(f"omni-remote: payload entries differ from the node's manifest: {sorted(set(members) ^ set(manifest))[:5]}")
        for rel, info in members.items():
            content = archive.read(info)
            if len(content) != manifest[rel]["size"]:
                raise ValueError(f"omni-remote: entry size differs from the node's manifest: {rel}")
            target = assert_local_plain(dest_root / rel)
            if dest_root.resolve() not in target.resolve().parents:
                raise ValueError(f"omni-remote: entry escapes destination: {rel}")
            if target.exists() and not target.is_file():
                raise ValueError(f"omni-remote: received file would replace a directory: {target}")
            action = "new"
            if target.exists():
                action = "unchanged" if target.read_bytes() == content else "updated"
            backup = assert_local_plain(backup_root / rel) if action == "updated" else None
            plan.append((rel, target, content, action, backup))
    files = []
    for rel, target, content, action, backup in plan:
        if action == "updated" and backup is not None:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        if action != "unchanged":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if target.read_bytes() != content:
                raise ValueError(f"omni-remote: local write verification failed: {target}")
        files.append({"rel": rel, "target": str(target), "action": action, "size": len(content),
                      "backup": str(backup) if backup else None})
    return files

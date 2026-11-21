# [OMNI] origin=claude-code domain=dashboard/boss_sight ts=2026-06-13T09:00:00+08:00 type=infra status=active
"""filetree_diff_v1 生成器：把一组改动按文件聚合成可附加到审阅材料的树形 diff payload。

四源：git ref/range | 目录快照 | 时间窗 | 手动路径(校验)。前端 FileTreeDiffView 用 buildTree 重建树。
diff 解析是一个聚焦的小实现(避免把 13k 行的 controlplane/catalogue 拉进 boss_sight 提交路径)，
算法与 catalogue._team_builder_split_unified_diff_by_file 同源。
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv", ".idea", "data", ".pytest_cache"}
_DIFF_CAP = 16000

_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
_IMG_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon",
}
_HTML_EXTS = {".html", ".htm"}
_PREVIEW_FILE_CAP = 400_000
_PREVIEW_TOTAL_CAP = 1_800_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_ab(p: str) -> str:
    p = p.strip().strip('"')
    for pre in ("a/", "b/"):
        if p.startswith(pre):
            return p[len(pre):]
    return p


def _is_git_repo(root: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:  # noqa: BLE001
        return False


def _git_diff(root: Path, ref: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(root), "diff", "--unified=3", ref],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
    )
    return r.stdout or ""


def _cap_diff(text: str) -> str:
    if len(text) > _DIFF_CAP:
        return text[:_DIFF_CAP] + f"\n… (diff 截断, 原长 {len(text)} 字符)\n"
    return text


def _parse_git_diff(diff_text: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    blocks = re.split(r"(?m)^(?=diff --git )", diff_text)
    for block in blocks:
        if not block.startswith("diff --git"):
            continue
        lines = block.splitlines()
        m = re.match(r"diff --git a/(.+?) b/(.+)", lines[0])
        a_path = m.group(1) if m else ""
        b_path = m.group(2) if m else ""
        path = (b_path or a_path).replace("\\", "/")
        status = "modified"
        old_path: str | None = None
        additions = deletions = 0
        for ln in lines[1:]:
            if ln.startswith("new file"):
                status = "added"
            elif ln.startswith("deleted file"):
                status = "deleted"
            elif ln.startswith("rename from "):
                old_path = ln[len("rename from "):].strip()
                status = "renamed"
            elif ln.startswith("rename to "):
                path = ln[len("rename to "):].strip().replace("\\", "/")
            elif ln.startswith("+++ "):
                p = ln[4:].strip()
                if p == "/dev/null":
                    status = "deleted"
                else:
                    path = _strip_ab(p).replace("\\", "/")
            elif ln.startswith("--- "):
                if ln[4:].strip() == "/dev/null":
                    status = "added"
            elif ln.startswith("+") and not ln.startswith("+++"):
                additions += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                deletions += 1
        files.append({
            "path": path, "old_path": old_path, "status": status,
            "additions": additions, "deletions": deletions, "diff": _cap_diff(block),
        })
    return files


def _walk_files(root: Path, since: float | None, until: float | None) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if since is not None or until is not None:
            mt = p.stat().st_mtime
            if since is not None and mt < since:
                continue
            if until is not None and mt > until:
                continue
        out.append(p)
    return out


def _added_entry(root: Path, p: Path, inline_diff: bool) -> dict[str, Any]:
    rel = p.relative_to(root).as_posix()
    diff = None
    if inline_diff:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            diff = _cap_diff("".join(f"+{line}\n" for line in content.splitlines()))
        except OSError:
            diff = None
    return {"path": rel, "old_path": None, "status": "added", "additions": diff.count("\n") if diff else 0, "deletions": 0, "diff": diff}


def _sibling_unchanged(root: Path, changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changed_paths = {f["path"] for f in changed}
    dirs = {str(Path(f["path"]).parent).replace("\\", "/") for f in changed}
    extra: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in dirs:
        dpath = root / d if d and d != "." else root
        if not dpath.is_dir():
            continue
        for child in dpath.iterdir():
            if not child.is_file():
                continue
            rel = child.relative_to(root).as_posix()
            if rel in changed_paths or rel in seen:
                continue
            seen.add(rel)
            extra.append({"path": rel, "old_path": None, "status": "unchanged", "additions": 0, "deletions": 0, "diff": None})
    return extra


def _attach_previews(root: Path, files: list[dict[str, Any]]) -> None:
    """给图片/html 文件内嵌可直接预览的内容(viewer 不依赖文件服务器)。图片 base64, html 文本; 限额。"""
    import base64
    budget = _PREVIEW_TOTAL_CAP
    for f in files:
        if f["status"] in ("deleted", "unchanged"):
            continue
        ext = Path(f["path"]).suffix.lower()
        p = root / f["path"]
        if ext in _IMG_EXTS:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > _PREVIEW_FILE_CAP or size > budget:
                f["preview"] = {"kind": "image", "oversized": True}
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            budget -= len(data)
            mime = _IMG_MIME.get(ext, "application/octet-stream")
            f["preview"] = {"kind": "image", "data_url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}
        elif ext in _HTML_EXTS:
            try:
                f["preview"] = {"kind": "html", "content": p.read_text(encoding="utf-8", errors="replace")[:_PREVIEW_FILE_CAP]}
            except OSError:
                continue


def build_filetree_diff(
    *,
    root: str,
    mode: str,
    ref: str | None = None,
    since: str | None = None,
    until: str | None = None,
    paths: list[str] | None = None,
    include_unchanged: bool = False,
    inline_diff: bool = True,
    embed_preview: bool = True,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ValueError(f"root 不是目录: {root}")
    is_git = _is_git_repo(root_path)
    files: list[dict[str, Any]] = []

    if mode == "git_ref":
        if not is_git:
            raise ValueError(f"--ref 需要 git 仓: {root} 不是 git 仓库, 改用 --dir / --paths-file")
        files = _parse_git_diff(_git_diff(root_path, ref or "HEAD"))
    elif mode == "manual":
        invalid: list[str] = []
        valid: list[Path] = []
        for raw in (paths or []):
            p = (root_path / raw).resolve()
            if not str(p).startswith(str(root_path)) or not p.is_file():
                invalid.append(raw)
            else:
                valid.append(p)
        if invalid:
            raise ValueError(f"非法/不存在的手动路径: {', '.join(invalid)}")
        if is_git and valid:
            rels = [p.relative_to(root_path).as_posix() for p in valid]
            diff_text = _git_diff_paths(root_path, rels)
            parsed = {f["path"]: f for f in _parse_git_diff(diff_text)}
            for p in valid:
                rel = p.relative_to(root_path).as_posix()
                files.append(parsed.get(rel) or _added_entry(root_path, p, inline_diff))
        else:
            files = [_added_entry(root_path, p, inline_diff) for p in valid]
    elif mode == "directory":
        files = [_added_entry(root_path, p, inline_diff) for p in _walk_files(root_path, None, None)]
    elif mode == "time_window":
        s = _parse_ts(since)
        u = _parse_ts(until)
        files = [_added_entry(root_path, p, inline_diff) for p in _walk_files(root_path, s, u)]
    else:
        raise ValueError(f"unknown mode {mode!r}")

    if not inline_diff:
        for f in files:
            f["diff"] = None
    if include_unchanged and mode in ("git_ref", "manual"):
        files = files + _sibling_unchanged(root_path, files)
    if embed_preview:
        _attach_previews(root_path, files)

    counts = {"added": 0, "modified": 0, "deleted": 0, "renamed": 0, "unchanged": 0, "total": len(files)}
    for f in files:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return {
        "schema": "filetree_diff_v1",
        "source": {
            "mode": mode,
            "root": str(root_path).replace("\\", "/"),
            "ref": ref,
            "since": since,
            "until": until,
            "is_git": is_git,
            "generated_at": _now_iso(),
        },
        "counts": counts,
        "files": files,
    }


def _git_diff_paths(root: Path, rels: list[str]) -> str:
    r = subprocess.run(
        ["git", "-C", str(root), "diff", "--unified=3", "HEAD", "--", *rels],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
    )
    return r.stdout or ""


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None

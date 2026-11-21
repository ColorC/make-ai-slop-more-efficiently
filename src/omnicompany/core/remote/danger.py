# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-24T01:40:00+08:00 type=lib status=active
# [OMNI] summary="omni remote 下发前静态拦截：按名杀进程、碰桌面壳、进程注入、写系统目录/白名单外。规则在 config/remote_danger_rules.json（GuardianRule 同名字段）。"
"""Static checks on scripts before ``omni remote`` sends them (run / ps / spawn bodies, pushed scripts).

Rules live in ``config/remote_danger_rules.json``; their id/name/severity/disposition/
message_template/certainty fields mean the same as Guardian's ``GuardianRule`` so Guard can
load the same file (:func:`as_guardian_rules`). ``block`` refuses before anything is sent,
``warn`` is recorded in the receipt and printed.
"""
from __future__ import annotations

import fnmatch
import json
import ntpath
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .pstext import RemoteRefused

SCRIPT_SUFFIXES = {".ps1", ".psm1", ".bat", ".cmd"}
_PATH_LITERAL = re.compile(r"""(?i)'([a-z]:[\\/][^'\r\n]*)'|"([a-z]:[\\/][^"\r\n]*)"|(?<![\w$'"])([a-z]:[\\/][^\s'"`|;<>,)]*)""")
_DEST_FLAG = re.compile(r"(?i)-(?:Destination|DestinationPath|LiteralPath|Path|FilePath)\s+")


def rules_path() -> Path:
    raw = os.environ.get("OMNI_REMOTE_DANGER_RULES", "").strip()
    if raw:
        return Path(raw)
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "config" / "remote_danger_rules.json"


@dataclass(frozen=True)
class Hit:
    rule_id: str
    name: str
    severity: str
    disposition: str
    line: int
    excerpt: str
    note: str
    fix: str
    target: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RuleSet:
    path: str
    rules: list[dict[str, Any]]
    write_verbs: list[str] = field(default_factory=list)
    copy_verbs: list[str] = field(default_factory=list)


@lru_cache(maxsize=8)
def _load(path_text: str, mtime: float) -> RuleSet:
    data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    rules = []
    for rule in data.get("rules") or []:
        compiled = []
        for item in rule.get("patterns") or []:
            compiled.append((re.compile(item["regex"], re.IGNORECASE), item.get("note") or ""))
        unless = [re.compile(item["regex"], re.IGNORECASE) for item in rule.get("unless") or []]
        rules.append({**rule, "_compiled": compiled, "_unless": unless})
    return RuleSet(path_text, rules, list(data.get("write_verbs") or []), list(data.get("copy_verbs") or []))


def load_rules(path: Path | None = None) -> RuleSet:
    path = path or rules_path()
    if not path.is_file():
        raise RemoteRefused("danger-rules", f"危险操作规则文件不存在: {path}")
    return _load(str(path), path.stat().st_mtime)


def strip_comments(text: str) -> list[tuple[int, str]]:
    """Return (line number, code) pairs with PowerShell/batch comments blanked (block <# #>, line #, REM/::)."""
    out: list[tuple[int, str]] = []
    in_block = False
    for number, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        line = raw
        if in_block:
            end = line.find("#>")
            if end < 0:
                out.append((number, ""))
                continue
            line = line[end + 2:]
            in_block = False
        while "<#" in line:
            start = line.find("<#")
            end = line.find("#>", start + 2)
            if end < 0:
                line = line[:start]
                in_block = True
                break
            line = line[:start] + line[end + 2:]
        stripped = line.lstrip()
        low = stripped.lower()
        if stripped.startswith("#") or low.startswith("rem ") or low == "rem" or stripped.startswith("::"):
            line = ""
        else:
            # trailing "# comment" outside quotes (best effort: only when no quote follows)
            hash_at = re.search(r"\s#(?![^'\"]*['\"])", line)
            if hash_at:
                line = line[:hash_at.start()]
        out.append((number, line))
    return out


def _norm(path: str) -> str:
    return ntpath.normpath(path.strip().rstrip("\\")).lower()


def _under(path: str, roots: Iterable[str]) -> bool:
    p = _norm(path)
    for root in roots:
        if not root:
            continue
        r = _norm(root)
        if "*" in r:
            # glob over whole path segments ("C:\Users\*\AppData\...")
            depth = r.count("\\") + 1
            head = "\\".join(p.split("\\")[:depth])
            if fnmatch.fnmatchcase(head, r):
                return True
            continue
        if p == r or p.startswith(r + "\\"):
            return True
    return False


def _literals(line: str, start: int = 0) -> list[str]:
    found = []
    for match in _PATH_LITERAL.finditer(line, start):
        text = (match.group(1) or match.group(2) or match.group(3) or "").replace("/", "\\").rstrip(" .")
        if len(text) > 3:
            found.append(text)
    return found


def _write_targets(line: str, rs: RuleSet) -> list[str]:
    """Literal absolute paths a write verb on this line writes to. Only paths after the verb count (inputs
    piped into Out-File / Set-Content come before it); copy-like verbs write only their destination."""
    low = line.lower()
    positions = [(low.find(v.lower()), v) for v in rs.write_verbs if v.lower() in low]
    if not positions:
        return []
    start, verb = min(positions)
    literals = _literals(line, start)
    if not literals:
        return []
    copy_like = {c.lower() for c in rs.copy_verbs}
    if verb.lower() in copy_like:
        dest = re.compile(r"(?i)-Destination(?:Path)?\s+").search(line, start)
        if dest:
            return _literals(line, dest.end())[:1] or literals[-1:]
        return literals[-1:]
    return literals[:1]


def scan(text: str, *, op: str, write_roots: Iterable[str] = (), owned_roots: Iterable[str] = (),
         rules: RuleSet | None = None) -> list[Hit]:
    rs = rules or load_rules()
    lines = strip_comments(text)
    hits: list[Hit] = []
    allowed = [*write_roots, *owned_roots]
    for rule in rs.rules:
        if op not in (rule.get("applies_to") or [op]):
            continue
        disposition = (rule.get("disposition") or ["warn"])[0]
        kind = rule.get("kind") or "regex"
        for number, line in lines:
            if not line.strip():
                continue
            if kind == "regex":
                for pattern, note in rule["_compiled"]:
                    match = pattern.search(line)
                    if match and not any(u.search(line) for u in rule["_unless"]):
                        hits.append(Hit(rule["id"], rule.get("name", ""), rule.get("severity", "HIGH"), disposition,
                                        number, line.strip()[:240], note, rule.get("fix", "")))
                        break
            elif kind == "write-target":
                for target in _write_targets(line, rs):
                    system = rule.get("system_prefixes")
                    if system is not None:
                        if _under(target, system) and not _under(target, [*allowed, *(rule.get("allow_prefixes") or [])]):
                            hits.append(Hit(rule["id"], rule.get("name", ""), rule.get("severity", "HIGH"), disposition,
                                            number, line.strip()[:240], "写入目标在系统位置", rule.get("fix", ""), target))
                            break
                    else:
                        extra = rule.get("allow_prefixes") or []
                        if allowed and not _under(target, [*allowed, *extra]):
                            hits.append(Hit(rule["id"], rule.get("name", ""), rule.get("severity", "HIGH"), disposition,
                                            number, line.strip()[:240], "写入目标不在 write_roots / 自有目录", rule.get("fix", ""), target))
                            break
    # a system-dir hit already covers the generic outside-roots warning for the same line
    blocked_lines = {(h.line, h.target) for h in hits if h.rule_id == "REMOTE-SYSTEM-WRITE"}
    return [h for h in hits if not (h.rule_id == "REMOTE-WRITE-OUTSIDE-ROOTS" and (h.line, h.target) in blocked_lines)]


def enforce(text: str, *, op: str, node: str, what: str, write_roots: Iterable[str] = (),
            owned_roots: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Raise RemoteRefused on the first ``block`` hit; return ``warn`` hits as dicts for the receipt."""
    hits = scan(text, op=op, write_roots=write_roots, owned_roots=owned_roots)
    blocks = [h for h in hits if h.disposition == "block"]
    if blocks:
        first = blocks[0]
        rest = sorted({h.rule_id for h in blocks[1:]})
        others = "" if len(blocks) == 1 else f"（另有 {len(blocks) - 1} 处拦截命中：{', '.join(rest)}，见回执 detail.hits）"
        target = f"，目标 {first.target}" if first.target else ""
        raise RemoteRefused(
            f"danger:{first.rule_id}",
            f"节点 {node} 的{what}第 {first.line} 行命中「{first.name}」（{first.note}{target}）：{first.excerpt}{others}。"
            f"什么都没下发。{first.fix} 规则: {rules_path()}",
            hits=[h.as_dict() for h in blocks], warnings=[h.as_dict() for h in hits if h.disposition != "block"],
        )
    return [h.as_dict() for h in hits]


def as_guardian_rules(path: Path | None = None) -> list[Any]:
    """The same rules as Guardian ``GuardianRule`` objects (check = scan the file content as a pushed script)."""
    from omnicompany.packages.services._core.guardian.rules._base import GuardianRule

    rs = load_rules(path)
    out = []
    for rule in rs.rules:
        single = RuleSet(rs.path, [rule], rs.write_verbs, rs.copy_verbs)

        def check(ctx: Any, _single: RuleSet = single) -> bool:
            content = getattr(ctx, "content", None)
            suffix = Path(getattr(ctx, "path", "") or "").suffix.lower()
            if not content or suffix not in SCRIPT_SUFFIXES:
                return False
            # no node context here: the outside-write_roots warning needs a node profile and stays silent
            return bool(scan(content, op="push", rules=_single))

        out.append(GuardianRule(
            id=rule["id"], name=rule.get("name", rule["id"]), severity=rule.get("severity", "HIGH"),
            description=rule.get("description", ""), check=check, disposition=list(rule.get("disposition") or ["warn"]),
            message_template=rule.get("message_template", "{path}"), certainty=rule.get("certainty", "absolute"),
        ))
    return out

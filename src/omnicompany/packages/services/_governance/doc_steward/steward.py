# [OMNI] origin=claude-code domain=services/_governance/doc_steward ts=2026-06-13T08:10:00Z type=router
# [OMNI] material_id="material:governance.doc_steward.freshness_pipeline.py"
"""文档时效性治理管线 — plan / report / 规范 的维护。

背景(2026-06-13 用户): "内部应当建立管线去维护计划, 报告 —— 尤其是规范的时效性。"
方法遵 docs/standards/concepts/governance_semantic_first.md: **语义判断用性价比模型为主,
确定性规律(如断链)用代码扫**。本部门两层:

1. 引用完整性(确定性, 无需 LLM): 扫 markdown 链接 / 行锚 指向**已不存在的文件**。
   便宜、不误报、可单测。这是"断链/陈旧指针"这类批量规律的结晶(规则化的那一半)。
2. 时效性(语义, 性价比模型): 逐篇判规范是否**过期/被取代/自相矛盾/另立权威**。
   走 runtime/llm/structured.call_json + runtime/llm/batch.run_parallel_items。

产物落 data/governance/doc_steward/, 只报 findings 不自动改文档(改文档是另一种危险操作)。
消费方: omni governance docs-* CLI; 后续可上 dashboard。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

# ── 目标发现 ────────────────────────────────────────────────────────

_TARGET_GLOBS: dict[str, tuple[str, ...]] = {
    "standard": ("docs/standards/**/*.md",),
    "plan": ("docs/plans/**/plan.md",),
    "report": ("docs/reports/**/*.md",),
    "project_index": ("docs/projects/**/PROJECT_INDEX.md",),
}
_SKIP_DIR_PARTS = ("_archive", "_graveyard", "__pycache__")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_dir() -> Path:
    d = omni_workspace_root() / "data" / "governance" / "doc_steward"
    d.mkdir(parents=True, exist_ok=True)
    return d


def discover_targets(kinds: tuple[str, ...] | None = None, root: Path | None = None) -> list[tuple[str, Path]]:
    """返回 (kind, 绝对路径) 列表。跳过归档/坟场。"""
    base = root or omni_workspace_root()
    wanted = kinds or tuple(_TARGET_GLOBS.keys())
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for kind in wanted:
        for pat in _TARGET_GLOBS.get(kind, ()):  # noqa: B007
            for p in base.glob(pat):
                if not p.is_file():
                    continue
                if any(part in _SKIP_DIR_PARTS for part in p.parts):
                    continue
                if p in seen:
                    continue
                seen.add(p)
                out.append((kind, p))
    return out


# ── 第一层: 引用完整性(确定性) ──────────────────────────────────────

@dataclass
class DocFinding:
    doc: str            # 相对仓库根的文档路径
    kind: str           # standard | plan | report
    category: str       # broken_ref | broken_anchor | stale_pointer | timeliness | ...
    detail: str
    target: str = ""    # 指向的(失效)目标
    by: str = "doc_steward"

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc": self.doc, "kind": self.kind, "category": self.category,
            "detail": self.detail, "target": self.target, "by": self.by,
        }


# markdown 链接 [text](target) — target 不含空格/右括号
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")  # 围栏代码块起止
_INLINE_CODE_RE = re.compile(r"`[^`]+`")    # 行内反引号代码(链接语法示例藏这里)
_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")  # d:/ 或 C:\ 这类绝对外部路径
_LINE_ANCHOR_RE = re.compile(r"^L(\d+)")    # 行锚 #L123 / #L123-L130


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "//", "#", "file:")) or target.startswith("data:")


def _line_count(p: Path) -> int | None:
    try:
        return len(p.read_text(encoding="utf-8", errors="replace").split("\n"))
    except OSError:
        return None


def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def scan_references(abs_path: Path, root: Path | None = None) -> list[DocFinding]:
    """确定性扫一篇文档里**真正断掉的** markdown 链接/行锚。

    判定边界(范本见 tests/governance/test_doc_steward_references.py):
    - **跳过**: 围栏代码块内的示例、`<占位符>`、wikilink `[[]]`、外链(http/#)、
      绝对外部路径(`d:/` `C:\\`)、`file.py::Symbol` 的符号后缀(只验文件); `%5B` 等 URL 编码先解码。
    - **broken_ref**: 指向仓库内不存在的文件(含相对深度写错)。
    - **broken_anchor**: 文件存在但 `#Lnnn` 行号超出文件行数(标题锚不校验, 避开 slug 规则误报)。
    """
    base = root or omni_workspace_root()
    try:
        rel = str(abs_path.relative_to(base)).replace("\\", "/")
    except ValueError:
        rel = str(abs_path)
    kind = _classify_kind(rel)
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings: list[DocFinding] = []
    in_fence = False
    fence_marker = ""
    for line in text.split("\n"):
        fence = _FENCE_RE.match(line)
        if fence:  # 进入/离开围栏代码块, 里面的链接是示例不校验
            mk = fence.group(1)[:3]
            if not in_fence:
                in_fence, fence_marker = True, mk
            elif line.lstrip().startswith(fence_marker):
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        # 行内反引号里的链接是"语法示例"(如演示反模式), 不算真链接
        for raw in _MD_LINK_RE.findall(_INLINE_CODE_RE.sub("", line)):
            target = raw.strip()
            if not target or _is_external(target):
                continue
            if "<" in target or ">" in target:   # 模板占位 <路径>/<子路径>
                continue
            if target.startswith("[["):          # wikilink, 非文件链接
                continue
            if "|" in target or re.search(r"\\[wsdbWSDB]|\?=|\*", target):
                continue  # 正则/glob 片段被误当链接(\w+ / ?=\s|$ 之类), 非真路径
            if "#" in target:
                file_part, anchor = target.split("#", 1)
            else:
                file_part, anchor = target, ""
            if "::" in file_part:                 # file.py::Symbol → 只验文件部分
                file_part = file_part.split("::", 1)[0]
            file_part = urllib.parse.unquote(file_part)  # 解 %5B%5D 等 URL 编码
            if not file_part:
                continue
            if _DRIVE_RE.match(file_part) or file_part.startswith("\\\\"):
                continue  # 绝对/UNC 外部路径: 环境相关, 不归仓库引用治理
            cand_doc = (abs_path.parent / file_part).resolve()
            cand_root = (base / file_part.lstrip("/")).resolve()
            if not _under(cand_doc, base) and not _under(cand_root, base):
                continue  # 链接逃出仓库根(../../../参考项目/ 这类)→ 外部引用, 不归仓库治理
            tgt = cand_doc if cand_doc.exists() else (cand_root if cand_root.exists() else None)
            if tgt is None:
                findings.append(DocFinding(
                    doc=rel, kind=kind, category="broken_ref",
                    detail=f"链接目标文件不存在: {target}", target=target))
                continue
            am = _LINE_ANCHOR_RE.match(anchor)  # 文件在: 只校验确定性的行锚
            if am:
                n = _line_count(tgt)
                if n is not None and int(am.group(1)) > n:
                    findings.append(DocFinding(
                        doc=rel, kind=kind, category="broken_anchor",
                        detail=f"行锚超出文件行数(共{n}行): {target}", target=target))
    return findings


def _classify_kind(rel: str) -> str:
    if rel.startswith("docs/standards/"):
        return "standard"
    if rel.startswith("docs/plans/"):
        return "plan"
    if rel.startswith("docs/reports/"):
        return "report"
    if rel.startswith("docs/projects/") and rel.endswith("PROJECT_INDEX.md"):
        return "project_index"
    return "doc"


def run_reference_audit(kinds: tuple[str, ...] | None = None, root: Path | None = None,
                        write: bool = True) -> dict[str, Any]:
    """全量跑确定性引用完整性审计, 返回 {findings, counts, ...}, 可落盘。"""
    base = root or omni_workspace_root()
    targets = discover_targets(kinds, root=base)
    all_findings: list[DocFinding] = []
    for _kind, p in targets:
        all_findings.extend(scan_references(p, root=base))
    payload = {
        "kind": "reference_audit",
        "generated_at": _now(),
        "scanned_docs": len(targets),
        "findings": [f.to_dict() for f in all_findings],
        "counts": {"broken_ref": sum(1 for f in all_findings if f.category == "broken_ref"),
                   "broken_anchor": sum(1 for f in all_findings if f.category == "broken_anchor")},
    }
    if write:
        out = report_dir() / "reference_audit.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["_written"] = str(out)
    return payload


# ── 引用修复(确定性): 把断链重指到目标在仓内的当前真实位置 ───────────

_INDEX_SKIP_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__", "data",
    "_archive", "_graveyard", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", ".pytest_tmp",
    # 这些含源码树副本/隔离物, 会把真实 src 匹配打成"歧义", 必须排除:
    "temp", ".omni", "quarantine",
}


def _repo_index(base: Path) -> dict[str, list[tuple[str, bool]]]:
    """basename → [(rel_posix_path, is_dir)], 仓内文件+目录(剪掉噪声目录)。"""
    index: dict[str, list[tuple[str, bool]]] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _INDEX_SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, base).replace("\\", "/")
        for d in dirnames:
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            index.setdefault(d, []).append((rel, True))
        for fn in filenames:
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            index.setdefault(fn, []).append((rel, False))
    return index


def _suffix_overlap(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x == y:
            n += 1
        else:
            break
    return n


def _encode_md_target(path_str: str) -> str:
    """markdown 链接 target 里的 [ ] ( ) 空格 要转义, 否则破坏链接解析(尤其 [日期] 目录)。"""
    return (path_str.replace("[", "%5B").replace("]", "%5D")
            .replace("(", "%28").replace(")", "%29").replace(" ", "%20"))


def plan_repairs(root: Path | None = None) -> dict[str, Any]:
    """为每条 broken_ref 在仓内按 basename + 最长路径后缀 找当前真实位置, 算出修复目标。

    返回 {fixes:[{doc,old,new}], ambiguous:[{doc,target,candidates}], unfixable:[{doc,target,reason}]}。
    只对**唯一**最佳候选给 fix; 多候选并列→ambiguous; 仓内无此名→unfixable(真删/真改名)。
    """
    base = root or omni_workspace_root()
    index = _repo_index(base)
    findings = run_reference_audit(root=base, write=False)["findings"]
    fixes: list[dict[str, str]] = []
    ambiguous: list[dict[str, Any]] = []
    unfixable: list[dict[str, Any]] = []
    for f in findings:
        target = f["target"]
        if f.get("category") != "broken_ref":
            unfixable.append({"doc": f["doc"], "target": target, "reason": "行锚问题需人工核行号"})
            continue
        core, anchor = (target.split("#", 1) + [""])[:2]
        anchor = f"#{anchor}" if "#" in target else ""
        sym = ""
        if "::" in core:
            core, sym = core.split("::", 1)
            sym = f"::{sym}"
        core = urllib.parse.unquote(core)
        segs = [s for s in core.replace("\\", "/").split("/") if s not in ("", ".", "..")]
        if not segs:
            unfixable.append({"doc": f["doc"], "target": target, "reason": "空路径"})
            continue
        cands = index.get(segs[-1], [])
        if not cands:
            unfixable.append({"doc": f["doc"], "target": target, "reason": "仓内无此名(真删/真改名)"})
            continue
        scored = sorted(((_suffix_overlap(segs, rel.split("/")), rel, is_dir)
                         for rel, is_dir in cands), key=lambda t: (t[0], -len(t[1])), reverse=True)
        top = scored[0][0]
        best = [s for s in scored if s[0] == top]
        if len(best) != 1:
            ambiguous.append({"doc": f["doc"], "target": target,
                              "candidates": [b[1] for b in best[:6]]})
            continue
        _, rel_target, is_dir = best[0]
        newrel = os.path.relpath(base / rel_target, (base / f["doc"]).parent).replace("\\", "/")
        if is_dir and not newrel.endswith("/"):
            newrel += "/"
        new_target = _encode_md_target(newrel) + sym + anchor
        if new_target != target:
            fixes.append({"doc": f["doc"], "old": target, "new": new_target})
    return {"fixes": fixes, "ambiguous": ambiguous, "unfixable": unfixable}


def apply_repairs(plan: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """把 plan_repairs 的 fixes 写回文档(围栏代码块内不动, 精确替换 `](old)`→`](new)`)。"""
    base = root or omni_workspace_root()
    by_doc: dict[str, list[dict[str, str]]] = {}
    for fx in plan.get("fixes", []):
        by_doc.setdefault(fx["doc"], []).append(fx)
    docs_changed = 0
    links_changed = 0
    for doc, fixes in by_doc.items():
        p = base / doc
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        fmap = {fx["old"]: fx["new"] for fx in fixes}
        in_fence = False
        fence_marker = ""
        touched = False
        for i, line in enumerate(lines):
            fence = _FENCE_RE.match(line)
            if fence:
                mk = fence.group(1)[:3]
                if not in_fence:
                    in_fence, fence_marker = True, mk
                elif line.lstrip().startswith(fence_marker):
                    in_fence, fence_marker = False, ""
                continue
            if in_fence:
                continue
            for old, new in fmap.items():
                token = f"]({old})"
                if token in line:
                    lines[i] = line.replace(token, f"]({new})")
                    links_changed += line.count(token)
                    touched = True
                    line = lines[i]
        if touched:
            p.write_text("\n".join(lines), encoding="utf-8")
            docs_changed += 1
    return {"docs_changed": docs_changed, "links_changed": links_changed}


# ── 第二层: 时效性(语义, 性价比模型) ────────────────────────────────

SYSTEM_PROMPT = """你是 omnicompany 仓库的规范/文档时效性治理员。给你一篇文档(规范/计划/报告)的节选,
判定它是否还反映现状。只输出 JSON, 不要其它文字。

判据(每条独立给, 没有就不给):
- superseded: 这份被更新的规范取代了, 却仍标 active(例如旧 DESIGN 七节模板 vs 新三件套规范)。
- outdated: 描述的接口/路径/机制已经变了(指向的代码搬家或删除、流程已重写)。
- conflict: 与另一份现行规范冲突, 没有谁服从谁的声明。
- competing_authority: 这份在另立一套"唯一权威", 没有指回已确立的权威文件。
不确定时不要报。证据要引文档里的具体句子或指向。

输出: {"findings": [{"category": "superseded|outdated|conflict|competing_authority", "detail": "<=40字", "evidence": "引原文/指向"}]}"""

TIMELINESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["category", "detail"],
                "properties": {
                    "category": {"type": "string",
                                 "enum": ["superseded", "outdated", "conflict", "competing_authority"]},
                    "detail": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        },
    },
}

_EXCERPT_CHARS = 2400


def _excerpt(abs_path: Path) -> str:
    try:
        return abs_path.read_text(encoding="utf-8", errors="replace")[:_EXCERPT_CHARS]
    except OSError:
        return ""


def judge_timeliness(abs_path: Path, *, model: str | None = None, root: Path | None = None) -> list[DocFinding]:
    """对单篇文档跑语义时效性判断(性价比模型)。"""
    from omnicompany.runtime.llm.structured import call_json
    base = root or omni_workspace_root()
    try:
        rel = str(abs_path.relative_to(base)).replace("\\", "/")
    except ValueError:
        rel = str(abs_path)
    excerpt = _excerpt(abs_path)
    if not excerpt.strip():
        return []
    user = f"文档路径: {rel}\n\n节选:\n{excerpt}"
    result = call_json(system=SYSTEM_PROMPT, user=user, schema=TIMELINESS_SCHEMA,
                       model=model, caller="doc_steward.judge_timeliness", max_tokens=1500)
    out: list[DocFinding] = []
    for f in (result or {}).get("findings", []) or []:
        out.append(DocFinding(
            doc=rel, kind=_classify_kind(rel), category=str(f.get("category", "timeliness")),
            detail=str(f.get("detail", ""))[:200], target=str(f.get("evidence", ""))[:200],
        ))
    return out


def run_timeliness(*, kinds: tuple[str, ...] = ("standard",), model: str | None = None,
                   limit: int | None = None, workers: int = 4, root: Path | None = None,
                   write: bool = True, echo: Any = None) -> dict[str, Any]:
    """批量跑语义时效性治理(默认只扫规范)。失败按项隔离, 走通用批量执行器。"""
    from omnicompany.runtime.llm.batch import run_parallel_items
    base = root or omni_workspace_root()
    targets = [p for _k, p in discover_targets(kinds, root=base)]
    if limit:
        targets = targets[:limit]

    def _worker(p: Path) -> list[dict[str, Any]]:
        return [f.to_dict() for f in judge_timeliness(p, model=model, root=base)]

    result = run_parallel_items(
        targets, _worker, workers=workers, progress_label="doc_steward.timeliness",
        item_label=lambda i, p: p.name, echo=echo,
    )
    findings: list[dict[str, Any]] = []
    for r in result.results:
        if r:
            findings.extend(r)
    payload = {
        "kind": "timeliness",
        "generated_at": _now(),
        "model": model or "default",
        "scanned_docs": len(targets),
        "failed_docs": len(result.failures),
        "findings": findings,
    }
    if write:
        stamp = _now().replace(":", "").replace("-", "")[:15]
        out = report_dir() / f"timeliness-{stamp}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (report_dir() / "timeliness-latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["_written"] = str(out)
    return payload


def latest_findings() -> dict[str, Any]:
    """读最近一次治理产物(引用审计 + 时效性), 供 CLI/报告。"""
    d = report_dir()
    out: dict[str, Any] = {"reference_audit": None, "timeliness": None}
    ref = d / "reference_audit.json"
    if ref.is_file():
        out["reference_audit"] = json.loads(ref.read_text(encoding="utf-8"))
    tl = d / "timeliness-latest.json"
    if tl.is_file():
        out["timeliness"] = json.loads(tl.read_text(encoding="utf-8"))
    return out

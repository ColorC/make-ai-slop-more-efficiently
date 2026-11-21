# [OMNI] origin=claude-code domain=services/_governance/progress_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="进度型自述确定性探针(轨一里程碑一, 无 LLM)。词表正则圈出 plan.md/文档/代码注释里的进度型措辞候选段 + 复用 doc_steward 引用存活检查, 只标不改。"
# [OMNI] why="进度只该在 whatnow 一处管;手抄进度快照写一周就 stale。先用最便宜的确定性方式拿到候选清单看信噪比, 语义精判(里程碑二)再压误报。"
# [OMNI] tags=governance,progress-ssot,deterministic,probe
# [OMNI] material_id="material:governance.progress_steward.probe.py"
"""进度型自述探针 — 确定性圈候选(无 LLM)。

判据(确定性可判的前四条; 第五条"与 whatnow 当前状态冲突"留里程碑二 LLM):
  ① 完成/进行/计划态动词(已完成/正在做/下一步/done/wip…)
  ② 引用会变的真源对象(百分比/完成度/端口/版本/commit/文件路径)→ has_ref 加强信号
  ③ 相对时间/里程碑词(目前/暂时/本期/截至/currently/for now)
  ④ 引用目标已失效(复用 doc_steward.scan_references 的 broken_ref/broken_anchor)

只报候选不改文件。产物落 data/governance/progress_steward/progress_scan.json。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

# 复用 doc_steward 的目标发现 + 围栏检测 + 引用存活, 不另造。
from omnicompany.packages.services._governance.doc_steward.steward import (
    _FENCE_RE,
    _INLINE_CODE_RE,
    discover_targets,
    scan_references,
)

# ── 进度型措辞词表(中英) ─────────────────────────────────────────────
# 注: 词表是"批量规律的结晶", 命中=候选(不是定罪); 三态由里程碑二语义精判。
_DONE = ["已完成", "完成了", "已实现", "实现了", "已通过", "已跑通", "跑通了", "跑通",
         "已修复", "已修", "已落地", "落地了", "已上线", "已发布", "已建好", "已搭好",
         "已接通", "已收尾", "搞定", "done", "completed", "finished", "shipped", "landed"]
_INPROGRESS = ["正在做", "正在", "在做", "推进中", "进行中", "当前在", "目前在", "正在推进",
               "施工中", "wip", "in progress", "ongoing", "work in progress"]
_PLANNED = ["下一步", "待办", "待做", "待实施", "待补", "待跑", "待接", "准备做", "计划做",
            "尚未", "还没", "todo", "to-do", "to do", "next step", "planned", "not yet"]
_RELTIME = ["目前", "暂时", "当前", "现在", "眼下", "本期", "这版", "这一版", "本轮",
            "截至", "截止", "as of", "currently", "for now", "right now", "at the moment", "so far"]

# 把词表编成"整词/子串"正则。中文无词界, 用子串; 英文加词界避免 done 命中 abandoned。
def _compile(words: list[str]) -> list[tuple[str, re.Pattern]]:
    out = []
    for w in words:
        if re.fullmatch(r"[a-zA-Z][a-zA-Z \-]*", w):
            out.append((w, re.compile(r"(?<![a-zA-Z])" + re.escape(w) + r"(?![a-zA-Z])", re.I)))
        else:
            out.append((w, re.compile(re.escape(w))))
    return out

_CAT_PATTERNS = {
    "progress_done": _compile(_DONE),
    "progress_inprogress": _compile(_INPROGRESS),
    "progress_planned": _compile(_PLANNED),
    "relative_time": _compile(_RELTIME),
}

# ② 引用会变真源的指示物: 百分比 / 完成度N / 端口 / 版本 / commit / 文件路径
_METRIC_RE = re.compile(r"\d{1,3}\s*%|完成度\s*[:：]?\s*\d+|进度\s*[:：]?\s*\d+")
_REF_RE = re.compile(
    r"material:[\w.\-]+"                       # material_id
    r"|:8[0-9]{3}\b"                           # 本地端口 :8xxx
    r"|\bv\d+\.\d+"                            # 版本号
    r"|\b[0-9a-f]{7,40}\b"                     # commit hash
    r"|[\w\-./]+\.(?:py|md|ts|tsx|json|yaml|yml|rs|lua)\b"  # 文件路径
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_dir() -> Path:
    d = omni_workspace_root() / "data" / "governance" / "progress_steward"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class ProgressFinding:
    doc: str                 # 相对仓库根
    line: int                # 行号(broken_ref 为 0)
    category: str            # progress_done|progress_inprogress|progress_planned|relative_time|metric|broken_ref|broken_anchor
    markers: list[str] = field(default_factory=list)   # 命中的词
    snippet: str = ""        # 该行原文(截断)
    has_ref: bool = False    # 同行是否带"会变真源"指示物(指涉式信号更强)
    target: str = ""         # broken_ref 的失效目标
    by: str = "progress_steward.probe"

    def to_dict(self) -> dict[str, Any]:
        return {"doc": self.doc, "line": self.line, "category": self.category,
                "markers": self.markers, "snippet": self.snippet,
                "has_ref": self.has_ref, "target": self.target, "by": self.by}


def scan_progress(abs_path: Path, root: Path | None = None) -> list[ProgressFinding]:
    """确定性扫一篇文档的进度型措辞候选 + 引用存活。"""
    base = root or omni_workspace_root()
    try:
        rel = str(abs_path.relative_to(base)).replace("\\", "/")
    except ValueError:
        rel = str(abs_path)
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings: list[ProgressFinding] = []
    in_fence = False
    fence_marker = ""
    for lineno, raw_line in enumerate(text.split("\n"), start=1):
        fence = _FENCE_RE.match(raw_line)
        if fence:
            mk = fence.group(1)[:3]
            if not in_fence:
                in_fence, fence_marker = True, mk
            elif raw_line.lstrip().startswith(fence_marker):
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        # OMNI 头注本身是元数据自我陈述, 不算"指涉别处进度", 跳过
        if raw_line.lstrip().startswith(("<!-- [OMNI]", "# [OMNI]")):
            continue
        line = _INLINE_CODE_RE.sub("", raw_line)  # 行内反引号代码里的词是示例
        if not line.strip():
            continue
        has_ref = bool(_REF_RE.search(line)) or bool(_METRIC_RE.search(line))
        for cat, pats in _CAT_PATTERNS.items():
            hit = [w for w, pat in pats if pat.search(line)]
            if hit:
                findings.append(ProgressFinding(
                    doc=rel, line=lineno, category=cat, markers=hit,
                    snippet=raw_line.strip()[:160], has_ref=has_ref))
        if _METRIC_RE.search(line):
            findings.append(ProgressFinding(
                doc=rel, line=lineno, category="metric",
                markers=[m.group(0) for m in _METRIC_RE.finditer(line)][:4],
                snippet=raw_line.strip()[:160], has_ref=True))
    # ④ 引用存活(复用 doc_steward): 死链/坏行锚 = 最确定的一类腐化
    for df in scan_references(abs_path, root=base):
        findings.append(ProgressFinding(
            doc=rel, line=0, category=df.category, markers=[], snippet=df.detail[:160],
            has_ref=True, target=df.target))
    return findings


# ── 代码注释里的进度型自述(可选, --code) ────────────────────────────
_COMMENT_RE = re.compile(r"^\s*#(.*)$")


def scan_code_comments(abs_path: Path, root: Path | None = None) -> list[ProgressFinding]:
    """扫 .py 注释行里的进度型措辞(排除 OMNI 头 + 纯 TODO 自标)。"""
    base = root or omni_workspace_root()
    try:
        rel = str(abs_path.relative_to(base)).replace("\\", "/")
    except ValueError:
        rel = str(abs_path)
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[ProgressFinding] = []
    for lineno, raw in enumerate(text.split("\n"), start=1):
        m = _COMMENT_RE.match(raw)
        if not m:
            continue
        body = m.group(1)
        if "[OMNI]" in body or not body.strip():
            continue
        has_ref = bool(_REF_RE.search(body)) or bool(_METRIC_RE.search(body))
        # 代码注释只关心"指涉别处进度"(has_ref) + 完成/进行态; 纯 "# TODO: 自己要做X" 是自我陈述, 放过
        for cat in ("progress_done", "progress_inprogress"):
            hit = [w for w, pat in _CAT_PATTERNS[cat] if pat.search(body)]
            if hit and has_ref:
                out.append(ProgressFinding(
                    doc=rel, line=lineno, category=cat, markers=hit,
                    snippet=raw.strip()[:160], has_ref=True))
    return out


_CODE_SKIP = {"__pycache__", "_archive", "_graveyard", ".git", "venv", ".venv",
              "node_modules", "dist", "build", "data"}


def run_progress_scan(*, include_docs: bool = False, include_code: bool = False,
                      include_projects: bool = False,
                      root: Path | None = None, write: bool = True,
                      limit: int | None = None) -> dict[str, Any]:
    """全量确定性进度型扫描。默认只扫 plan.md;--docs 加 docs/**;--code 加 src/**/*.py 注释;
    --projects 把 project_index kind(docs/projects/**/PROJECT_INDEX.md)的目标并入探测面
    (进度圈出扩面到项目索引正文, 批4)。"""
    base = root or omni_workspace_root()
    targets: list[Path] = [p for _k, p in discover_targets(("plan",), root=base)]
    if include_docs:
        for p in (base / "docs").rglob("*.md"):
            if any(part in ("_archive", "_graveyard") for part in p.parts):
                continue
            if p.name == "plan.md":
                continue
            targets.append(p)
    if include_projects:
        seen = {p.resolve() for p in targets}
        for _k, p in discover_targets(("project_index",), root=base):
            if p.resolve() not in seen:
                targets.append(p)
                seen.add(p.resolve())
    if limit:
        targets = targets[:limit]
    all_f: list[ProgressFinding] = []
    for p in targets:
        all_f.extend(scan_progress(p, root=base))
    code_scanned = 0
    if include_code:
        src = base / "src"
        for p in src.rglob("*.py"):
            if any(part in _CODE_SKIP for part in p.parts):
                continue
            code_scanned += 1
            all_f.extend(scan_code_comments(p, root=base))
            if limit and code_scanned >= limit:
                break
    # 候选段去重(同 doc+line 合并 markers)
    counts: dict[str, int] = {}
    for f in all_f:
        counts[f.category] = counts.get(f.category, 0) + 1
    payload = {
        "kind": "progress_scan",
        "generated_at": _now(),
        "scanned_docs": len(targets),
        "scanned_code": code_scanned,
        "total_candidates": len(all_f),
        "with_ref": sum(1 for f in all_f if f.has_ref),
        "counts": counts,
        "findings": [f.to_dict() for f in all_f],
    }
    if write:
        out = report_dir() / "progress_scan.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["_written"] = str(out)
    return payload


def latest_scan() -> dict[str, Any] | None:
    p = report_dir() / "progress_scan.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return None

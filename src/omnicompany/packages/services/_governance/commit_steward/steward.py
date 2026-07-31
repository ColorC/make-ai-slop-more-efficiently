# [OMNI] origin=claude-code domain=services/_governance/commit_steward ts=2026-06-13T09:30:00Z type=router
# [OMNI] material_id="material:governance.commit_steward.batched_commit_pipeline.py"
"""性价比模型 git 提交治理管线 — 把一堆未提交改动整理成合理分批的 commit。

背景(2026-06-13 用户): "提交也是高频操作…应该由性价比模型做 git 提交…要严格、大量阅读、
不可完整批量提交。所有重复性低的明文文件理论上都要读, 重复性高的可以不读。"

设计(便宜模型能力低 → 用流程兜底):
1. scan_changes(): 确定性扫 git status, 每个改动文件按内容类型判 read|skip 策略。
   - read(低重复明文): .py/.md/.ts/.tsx/.yaml 等 — 必须读 diff 才能进批, 读不到就留着不提交。
   - skip(高重复): 二进制/截图/数据账本(ARCH-CHANGES.jsonl/REGISTRY.md)/生成物/lockfile/超大 diff
     — 不逐行读, 给确定性标签, 仍可提交。
2. MAP(性价比模型, 逐 read 文件并发): 读该文件 diff → 一句"改了什么 + 主题归类"。这是"大量阅读"那一步。
3. REDUCE(性价比模型): 把全部一句话摘要聚成若干**逻辑批次**, 每批一条带文件级说明的 commit message。
4. apply_batches(): 逐批 `git add <显式文件清单>` + `git commit -F` — **禁 git add -A 盲目全量**。
   未进任何批次的文件留在工作区并报告。pre-commit 卫士逐批兜底。

产物落 data/governance/commit_steward/。CLI: omni governance commit-run [--dry-run]。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

# ── 内容类型策略 ────────────────────────────────────────────────────

# 低重复明文(必读才能进批)
_READ_EXTS = {
    ".py", ".md", ".ts", ".tsx", ".js", ".jsx", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".css", ".scss", ".html", ".sh", ".txt", ".rst",
}
# 高重复/二进制(不逐行读, 给确定性标签)
_SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".svg", ".pdf",
    ".xlsm", ".xlsx", ".db", ".sqlite", ".pyc", ".lock", ".bin", ".woff", ".woff2", ".ttf",
}
# 按名字判高重复(数据账本/生成物/锁文件)
_SKIP_NAME_HINTS = (
    "ARCH-CHANGES.jsonl", "REGISTRY.md", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "material_id_index.json", "batch_status.json",
)
_SKIP_DIR_HINTS = ("__pycache__/", "node_modules/", "/dist/", "/build/", "/.omni/quarantine/")
# read 文件 diff 超过这么多行也降级为 skip(超大改动逐行读无意义)
_MAX_READ_DIFF_LINES = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_dir() -> Path:
    d = omni_workspace_root() / "data" / "governance" / "commit_steward"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_root() -> Path:
    return omni_workspace_root()


def _git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(_repo_root()),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip()[:300]}")
    return proc.stdout


@dataclass
class ChangeFile:
    path: str               # 相对仓库根, posix
    status: str             # M | A | D | R | ?? (git status --porcelain 首列归一)
    added: int = 0
    deleted: int = 0
    policy: str = "read"    # read | skip
    reason: str = ""        # 为何 skip / 分类依据
    summary: str = ""       # MAP 阶段填: 这文件改了什么(一句)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "status": self.status, "added": self.added,
                "deleted": self.deleted, "policy": self.policy, "reason": self.reason,
                "summary": self.summary}


def classify_change(path: str, added: int, deleted: int) -> tuple[str, str]:
    """确定性判一个改动文件该 read 还是 skip, 返回 (policy, reason)。"""
    p = path.replace("\\", "/")
    low = p.lower()
    for d in _SKIP_DIR_HINTS:
        if d.strip("/") in low:
            return "skip", f"高重复目录({d})"
    name = p.rsplit("/", 1)[-1]
    if name in _SKIP_NAME_HINTS:
        return "skip", "数据账本/生成物(确定性跳过逐行读)"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext in _SKIP_EXTS:
        return "skip", f"二进制/高重复类型({ext})"
    if ext in _READ_EXTS:
        if added + deleted > _MAX_READ_DIFF_LINES:
            return "skip", f"明文但超大改动({added + deleted} 行, 降级跳读)"
        return "read", "低重复明文"
    return "read", "未知类型(保守按明文读)"


def scan_changes(include_untracked: bool = True) -> list[ChangeFile]:
    """确定性扫工作区改动(已跟踪改动 + 可选未跟踪), 返回带策略分类的清单。"""
    out: list[ChangeFile] = []
    numstat: dict[str, tuple[int, int]] = {}
    for line in _git("diff", "--numstat").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            a = int(parts[0]) if parts[0].isdigit() else 0
            d = int(parts[1]) if parts[1].isdigit() else 0
            numstat[parts[2].replace("\\", "/")] = (a, d)
    porcelain = _git("status", "--porcelain", "-z")
    for entry in porcelain.split("\0"):
        if not entry.strip():
            continue
        xy, _, path = entry[:2], entry[2], entry[3:]
        path = path.replace("\\", "/")
        if not path:
            continue
        if xy.strip() == "??":
            if not include_untracked:
                continue
            status = "??"
        else:
            status = (xy.strip() or "M")[0]
        a, d = numstat.get(path, (0, 0))
        policy, reason = classify_change(path, a, d)
        if status == "??":
            # 未跟踪明文也要读才能进批; 但删除/二进制照旧
            pass
        out.append(ChangeFile(path=path, status=status, added=a, deleted=d,
                              policy=policy, reason=reason))
    return out


def _file_diff(cf: ChangeFile, max_chars: int = 4000) -> str:
    """取单文件 diff(未跟踪文件取内容前若干行)供模型阅读。"""
    if cf.status == "??":
        fp = _repo_root() / cf.path
        try:
            return fp.read_text(encoding="utf-8", errors="replace")[:max_chars]
        except OSError:
            return ""
    try:
        return _git("diff", "--", cf.path, check=False)[:max_chars]
    except RuntimeError:
        return ""


# ── MAP: 逐文件读 → 一句摘要(性价比模型) ────────────────────────────

_MAP_SYSTEM = """你在帮 git 提交治理读单个改动文件。给你文件路径和它的 diff(或新文件内容),
用一句话(<=30字)说"改了什么 + 属于哪个主题方向"。只输出 JSON: {"summary": "..."}。"""

_MAP_SCHEMA = {"type": "object", "required": ["summary"],
               "properties": {"summary": {"type": "string"}}}


def _summarize_one(cf: ChangeFile, model: str | None) -> ChangeFile:
    if cf.policy == "skip":
        cf.summary = f"[{cf.reason}] {cf.status} {cf.path.rsplit('/',1)[-1]}"
        return cf
    from omnicompany.runtime.llm.structured import call_json
    diff = _file_diff(cf)
    if not diff.strip():
        cf.summary = f"[空diff/读不到] {cf.status} {cf.path}"
        cf.policy = "skip"
        cf.reason = "读不到内容(留工作区不提交)"
        return cf
    user = f"路径: {cf.path}\n状态: {cf.status}  +{cf.added}/-{cf.deleted}\n\ndiff/内容:\n{diff}"
    try:
        res = call_json(system=_MAP_SYSTEM, user=user, schema=_MAP_SCHEMA,
                        model=model, caller="commit_steward.map", max_tokens=300)
        cf.summary = str((res or {}).get("summary", "")).strip()[:120] or f"{cf.status} {cf.path}"
    except Exception as e:  # noqa: BLE001
        cf.summary = ""
        cf.policy = "skip"
        cf.reason = f"摘要失败({type(e).__name__}), 留工作区不提交"
    return cf


# ── REDUCE: 聚成批次 + 写 commit message(性价比模型) ────────────────

_GROUP_SYSTEM = """你在做 git 提交治理。给你一批改动文件(路径 + 状态 + 一句话摘要),
把它们聚成若干**逻辑批次**, 每批是一个内聚的 commit(同一主题/同一目的)。要求:
- 只用给定文件路径, 一个文件只进一个批次; 不要臆造文件。
- 每批 message: 第一行是 type(scope): 简短主题(<=50字); 空一行; 正文用 1-N 行说清"改了什么、为什么",
  涉及多个关键文件时点名。
- 宁可多分几批保持内聚, 不要把不相关的混进一批。
只输出 JSON: {"batches": [{"subject": "...", "body": "...", "files": ["path", ...]}]}。"""

_GROUP_SCHEMA = {
    "type": "object", "required": ["batches"],
    "properties": {"batches": {"type": "array", "items": {
        "type": "object", "required": ["subject", "files"],
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
        }}}},
}


@dataclass
class CommitBatch:
    subject: str
    body: str
    files: list[str] = field(default_factory=list)

    def message(self) -> str:
        return f"{self.subject}\n\n{self.body}".rstrip() + "\n"


def _is_committable(c: ChangeFile) -> bool:
    """有摘要 且 不是读失败留工作区的, 才可进批。skip(账本/二进制)带启发式摘要也可提交。"""
    if not c.summary:
        return False
    return "留工作区" not in c.reason and "读不到" not in c.reason


def plan_commit_batches(changes: list[ChangeFile], model: str | None = None) -> list[CommitBatch]:
    """REDUCE: 把已带摘要的改动聚成 commit 批次。"""
    from omnicompany.runtime.llm.structured import call_json
    committable = [c for c in changes if _is_committable(c)]
    if not committable:
        return []
    lines = [f"- {c.path} [{c.status}] {c.summary}" for c in committable]
    valid_paths = {c.path for c in committable}
    user = "改动文件清单:\n" + "\n".join(lines)
    res = call_json(system=_GROUP_SYSTEM, user=user, schema=_GROUP_SCHEMA,
                    model=model, caller="commit_steward.group", max_tokens=4000)
    batches: list[CommitBatch] = []
    for b in (res or {}).get("batches", []) or []:
        files = [f.replace("\\", "/") for f in (b.get("files") or []) if f.replace("\\", "/") in valid_paths]
        if not files:
            continue
        batches.append(CommitBatch(subject=str(b.get("subject", "")).strip(),
                                   body=str(b.get("body", "")).strip(), files=files))
    return batches


# ── APPLY: 逐批显式 add + commit(禁 git add -A) ─────────────────────

def apply_batches(batches: list[CommitBatch], *, dry_run: bool = True) -> dict[str, Any]:
    """逐批 git add 显式文件 + commit。dry_run 只返回计划不动 git。"""
    results: list[dict[str, Any]] = []
    for i, b in enumerate(batches):
        rec = {"batch": i, "subject": b.subject, "files": b.files, "committed": False}
        if dry_run:
            results.append(rec)
            continue
        try:
            _git("add", "--", *b.files)
            msg_file = report_dir() / f"_commitmsg_{i}.txt"
            msg_file.write_text(b.message(), encoding="utf-8")
            _git("commit", "-F", str(msg_file))
            rec["committed"] = True
            rec["sha"] = _git("rev-parse", "--short", "HEAD").strip()
            msg_file.unlink(missing_ok=True)
        except RuntimeError as e:
            rec["error"] = str(e)[:300]
            # 失败批回滚 staged, 不影响他批
            _git("reset", "--", *b.files, check=False)
        results.append(rec)
    return {"dry_run": dry_run, "batches": results}


def run_commit(*, model: str | None = None, dry_run: bool = True, workers: int = 4,
               echo: Any = None) -> dict[str, Any]:
    """端到端: 扫改动 → 逐文件读摘要 → 聚批 → (dry_run 出计划 | 真提交)。"""
    from omnicompany.runtime.llm.batch import run_parallel_items
    changes = scan_changes()
    if not changes:
        return {"changes": 0, "message": "工作区干净, 无可提交改动"}
    to_read = [c for c in changes if c.policy == "read"]
    result = run_parallel_items(to_read, lambda c: _summarize_one(c, model),
                                workers=workers, progress_label="commit_steward.map",
                                item_label=lambda i, c: c.path.rsplit("/", 1)[-1], echo=echo)
    # 把 map 结果写回(run_parallel_items 返回新对象, 原 changes 里的 read 文件用 map 结果替换)
    summarized = {c.path: c for c in result.results if c}
    merged: list[ChangeFile] = []
    for c in changes:
        merged.append(summarized.get(c.path) if c.policy == "read" and c.path in summarized else c)
        if c.policy == "skip":
            c.summary = c.summary or f"[{c.reason}] {c.status} {c.path.rsplit('/',1)[-1]}"
    batches = plan_commit_batches(merged, model=model)
    applied = apply_batches(batches, dry_run=dry_run)
    committed_files = {f for b in applied["batches"] for f in b["files"]}
    left = [c.path for c in merged if c.path not in committed_files]
    payload = {
        "generated_at": _now(), "dry_run": dry_run, "model": model or "default",
        "changes": len(changes), "batches": len(batches),
        "map_failed": len(result.failures),
        "uncommitted_left": left,
        "plan": [{"subject": b.subject, "body": b.body, "files": b.files} for b in batches],
        "applied": applied["batches"],
    }
    out = report_dir() / ("commit_plan_dryrun.json" if dry_run else "commit_last.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["_written"] = str(out)
    return payload

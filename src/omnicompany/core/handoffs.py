# [OMNI] origin=claude-code domain=core ts=2026-08-10T00:00:00Z type=core status=active
# [OMNI] summary="跨会话/跨 CLI 交接: 一份人写的交接文档 + 一条机器记录 + 继任会话谱系"
# [OMNI] why="预算/上下文见底时要在 claude 与 codex 之间来回交接; 手写散落的 handoff.md 没有索引、没有校验、接手方找不到入口"
# [OMNI] tags=handoff,session,intake,inheritance,cross-cli
# [OMNI] material_id="material:core.session_handoff.record_store.py"
"""跨会话交接(handoff)与接手(intake)的唯一存储、骨架与校验。

一条记录 = 一份 Markdown 文档(唯一权威叙述, 进 git) + 一条机器记录
(谁交给谁、文档在哪、继任会话是谁、有没有被真的接手)。

本模块**不生成叙述内容**: 骨架和机器事实由这里预填, 正文必须由当事会话自己
写 —— 只有它知道语境、试过什么、哪些结论有实证。校验(`verify_doc`)负责挡住
"骨架没填完就交出去"和"必读入口里的路径根本不存在"。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root


KINDS = ("handoff", "intake")

#: 交接方必填段落。顺序即文档顺序; 校验按标题精确匹配。
HANDOFF_SECTIONS: tuple[str, ...] = (
    "一句话状态",
    "用户约束与口径",
    "已完成且有实证",
    "未完成与下一步",
    "坑与已知失败",
    "复验命令",
    "必读入口",
)

#: 接手方必填段落(被交接方主动深读后写的初始文档)。
INTAKE_SECTIONS: tuple[str, ...] = (
    "一句话理解",
    "我读了什么",
    "我核实与推翻了什么",
    "未知与风险",
    "我的下一步",
    "必读入口",
)

SECTION_HINTS: dict[str, str] = {
    "一句话状态": "现在到哪一步了, 一句话说清; 别写目标, 写状态。",
    "用户约束与口径": "稳定约束与判据: 不许做什么、什么算完成、术语口径。",
    "已完成且有实证": "每条结论后面跟证据入口(文件路径 / 命令 / 日志 / 截图); 没实证的写进下一段而不是这里。",
    "未完成与下一步": "有序动作清单, 每条能被直接执行; 写清第一步。",
    "坑与已知失败": "试过什么、为什么不行、哪条路已被否。接手方靠这段不重复踩。",
    "复验命令": "接手方能直接跑、用来自证当前状态的命令(fenced code block)。",
    "必读入口": "文件 / 计划 / 规范路径清单, 用反引号包路径; 校验会逐条检查存在性。",
    "一句话理解": "我理解的当前状态, 一句话; 与上一棒说法不同的地方要点出来。",
    "我读了什么": "真读过的真源入口(transcript / 代码 / 计划 / 运行产物), 用反引号包路径。",
    "我核实与推翻了什么": "亲手验过的结论、以及被推翻的说法 + 证据。",
    "未知与风险": "还不知道的、可能出错的、需要用户拍板的。",
    "我的下一步": "有序动作清单, 第一步写清。",
}

#: 只列路径的段落: 合格判据是"路径真存在", 不是字数。
_PATH_SECTIONS = frozenset({"必读入口", "我读了什么"})

_TODO_MARK = "TODO(填写)"
_ID_RE = re.compile(r"^(ho|in)-\d{8}T\d{6}Z(-[a-z0-9-]+)?$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_PATH_ROOTS = frozenset(
    {"docs", "src", "tests", "data", "config", "scripts", "packages",
     "templates", "tools", "var", "services", "skills", "omnicompany"}
)


# ── 时间 / id / 路径 ─────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def slugify(text: str, limit: int = 40) -> str:
    slug = _SLUG_RE.sub("-", str(text or "").strip().lower()).strip("-")
    return slug[:limit].strip("-")


def new_id(kind: str, title: str = "", now: datetime | None = None) -> str:
    prefix = "in" if kind == "intake" else "ho"
    slug = slugify(title, 24)
    return f"{prefix}-{stamp(now)}" + (f"-{slug}" if slug else "")


def store_root() -> Path:
    override = os.environ.get("OMNI_HANDOFF_ROOT")
    if override:
        return Path(override)
    # data/_runtime/ 是 archmap 认可的 agent 可写运行时层; 文档才是权威, 记录只是索引。
    return omni_workspace_root() / "data" / "_runtime" / "handoffs"


def records_dir() -> Path:
    return store_root() / "records"


def _record_path(handoff_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(handoff_id))[:120]
    if not safe:
        raise ValueError("handoff id 不能为空")
    return records_dir() / f"{safe}.json"


def workspace_root() -> Path:
    return omni_workspace_root()


def to_rel(path: Path | str) -> str:
    """能落在工作区内就存相对路径(跟着仓库走), 否则存绝对路径。"""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(workspace_root().resolve())).replace(os.sep, "/")
    except (ValueError, OSError):
        return str(p)


def to_abs(path: Path | str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (workspace_root() / p)


# ── 记录读写 ─────────────────────────────────────────────────────────────────


def save_record(record: dict[str, Any]) -> dict[str, Any]:
    handoff_id = str(record.get("id") or "").strip()
    if not handoff_id:
        raise ValueError("record 缺 id")
    record["updated_at"] = _now()
    path = _record_path(handoff_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return record


def _read_one(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("id") else None


def list_records(
    *,
    kind: str | None = None,
    status: str | None = None,
    plan: str | None = None,
    project: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    directory = records_dir()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            row = _read_one(path)
            if row is None:
                continue
            binding = row.get("binding") or {}
            if kind and row.get("kind") != kind:
                continue
            if status and row.get("status") != status:
                continue
            if plan and str(binding.get("plan") or "") != plan:
                continue
            if project and str(binding.get("project") or "") != project:
                continue
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[: max(1, int(limit))] if limit else rows


def get_record(handoff_id: str) -> dict[str, Any] | None:
    """精确 id 优先; 否则按前缀唯一匹配(允许只敲前几位)。"""
    key = str(handoff_id or "").strip()
    if not key:
        return None
    exact = _read_one(_record_path(key))
    if exact is not None:
        return exact
    hits = [row for row in list_records(limit=0) if str(row["id"]).startswith(key)]
    return hits[0] if len(hits) == 1 else None


# ── 机器事实 ─────────────────────────────────────────────────────────────────


def transcript_path(provider: str | None, session_id: str | None) -> str | None:
    """provider 原生 transcript 文件 —— 接手方要深读就读这个, 别只看摘要。"""
    if not session_id:
        return None
    home = Path.home()
    provider = str(provider or "").strip().lower()
    patterns: tuple[str, ...]
    if provider in {"claude_code", "claude"}:
        patterns = (f".claude/projects/*/{session_id}.jsonl",)
    elif provider == "codex":
        patterns = (f".codex/sessions/*/*/*/rollout-*{session_id}.jsonl",)
    else:
        patterns = (
            f".claude/projects/*/{session_id}.jsonl",
            f".codex/sessions/*/*/*/rollout-*{session_id}.jsonl",
        )
    for pattern in patterns:
        for hit in sorted(home.glob(pattern)):
            return str(hit)
    return None


def _git_facts(cwd: str) -> dict[str, Any]:
    def _run(*args: str) -> str:
        try:
            done = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    head = _run("rev-parse", "--short", "HEAD")
    if not head:
        return {}
    dirty = [line[3:].strip() for line in _run("status", "--porcelain").splitlines() if line.strip()]
    return {
        "branch": _run("rev-parse", "--abbrev-ref", "HEAD"),
        "head": head,
        "dirty_count": len(dirty),
        "dirty_sample": dirty[:12],
    }


def machine_facts(cwd: str | None = None) -> dict[str, Any]:
    """自动可得的事实。每一项都 best-effort: 拿不到就缺, 绝不编。"""
    facts: dict[str, Any] = {"collected_at": _now()}
    session: dict[str, Any] = {}
    try:
        from omnicompany.packages.services._core.identity import current_session_meta

        session = dict(current_session_meta())
    except Exception:  # noqa: BLE001 - 事实采集永不阻断交接
        session = {}
    work_cwd = str(cwd or session.get("cwd") or os.getcwd())
    facts["session"] = {
        "provider": session.get("provider"),
        "trace_id": session.get("trace_id"),
        "session_id": session.get("session_id"),
        "pty_id": session.get("pty_id"),
        "started_at": session.get("started_at"),
        "cwd": work_cwd,
    }
    facts["binding"] = {
        "plan": session.get("active_plan"),
        "project": session.get("project"),
        "workstream": session.get("workstream"),
        "task": session.get("task_id"),
        "topic": session.get("topic"),
    }
    facts["transcript"] = transcript_path(session.get("provider"), session.get("session_id"))
    facts["git"] = _git_facts(work_cwd)

    plan_id = facts["binding"]["plan"]
    if plan_id:
        try:
            from omnicompany.packages.services.boss_sight.progress import list_entries

            facts["progress"] = [
                {"at": str(e.get("created_at") or "")[:19], "text": e.get("text")}
                for e in (list_entries("plan", plan_id) or [])[-6:]
            ]
        except Exception:  # noqa: BLE001
            facts["progress"] = []
    workstream = facts["binding"]["workstream"]
    if workstream:
        try:
            from omnicompany.core.workstreams import recent_checkpoints

            facts["checkpoints"] = [
                {"at": str(e.get("created_at") or "")[:19], "kind": e.get("kind"), "summary": e.get("summary")}
                for e in recent_checkpoints(workstream, limit=5)
            ]
        except Exception:  # noqa: BLE001
            facts["checkpoints"] = []
    return facts


# ── 文档落位与骨架 ───────────────────────────────────────────────────────────


def doc_target(kind: str, *, plan: str | None, slug: str, now: datetime | None = None) -> Path:
    """有计划就落在计划目录(沿用既有实践), 没计划落集中区。"""
    root = workspace_root()
    when = now or datetime.now(timezone.utc)
    base = f"{'intake' if kind == 'intake' else 'handoff'}-{when.strftime('%Y%m%d-%H%M')}"
    name = f"{base}-{slug}.md" if slug else f"{base}.md"
    if plan:
        plan_dir = root / "docs" / "plans" / plan
        if plan_dir.is_dir():
            return plan_dir / name
    return root / "docs" / "handoffs" / when.strftime("%Y-%m") / name


def _fact_lines(facts: dict[str, Any]) -> list[str]:
    session = facts.get("session") or {}
    binding = facts.get("binding") or {}
    git = facts.get("git") or {}
    lines = [
        f"- 交接方会话: `{session.get('provider') or '?'}` · trace `{session.get('trace_id') or '-'}`"
        f" · session `{session.get('session_id') or '-'}` · pty `{session.get('pty_id') or '-'}`",
        f"- 工作目录: `{session.get('cwd') or '-'}`",
        f"- 绑定: plan `{binding.get('plan') or '-'}` · project `{binding.get('project') or '-'}`"
        f" · workstream `{binding.get('workstream') or '-'}` · task `{binding.get('task') or '-'}`",
    ]
    if git:
        lines.append(
            f"- git: branch `{git.get('branch') or '-'}` · HEAD `{git.get('head')}`"
            f" · 未提交 {git.get('dirty_count')} 个"
        )
        for item in git.get("dirty_sample") or []:
            lines.append(f"  - `{item}`")
    transcript = facts.get("transcript")
    if transcript:
        size = ""
        try:
            size = f" ({Path(transcript).stat().st_size / 1048576:.1f} MB)"
        except OSError:
            pass
        lines.append(f"- 原始对话真源(要深读读这个, 不以摘要代替): `{transcript}`{size}")
    else:
        lines.append("- 原始对话真源: 未定位到 transcript 文件(接手方按 `omni convos list` 自行找)")
    for row in facts.get("progress") or []:
        lines.append(f"- 进度 {row['at']}: {row['text']}")
    for row in facts.get("checkpoints") or []:
        lines.append(f"- checkpoint {row['at']} [{row['kind']}]: {row['summary']}")
    return lines


def sections_for(kind: str) -> tuple[str, ...]:
    return INTAKE_SECTIONS if kind == "intake" else HANDOFF_SECTIONS


def render_draft(record: dict[str, Any], facts: dict[str, Any]) -> str:
    kind = str(record.get("kind") or "handoff")
    session = facts.get("session") or {}
    binding = record.get("binding") or {}
    front = {
        "handoff_id": record["id"],
        "kind": kind,
        "title": record.get("title") or record["id"],
        "status": record.get("status") or "draft",
        "reason": record.get("reason") or "",
        "created_at": record.get("created_at") or _now(),
        "from_provider": session.get("provider") or "",
        "from_trace_id": session.get("trace_id") or "",
        "from_session_id": session.get("session_id") or "",
        "cwd": session.get("cwd") or "",
        "plan": binding.get("plan") or "",
        "project": binding.get("project") or "",
        "workstream": binding.get("workstream") or "",
        "task": binding.get("task") or "",
    }
    if record.get("of"):
        front["of"] = record["of"]

    out = ["---"]
    out += [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in front.items()]
    out += ["---", ""]
    label = "接手理解文档" if kind == "intake" else "交接文档"
    out += [
        f"# {front['title']}",
        "",
        f"> {label} · `omni handoff draft` 起草骨架, 正文由本会话补齐, 再 `omni handoff seal {record['id']}` 过校验。",
        f"> 接手方入口: `omni handoff show {record['id']}`(或在继任会话里 `omni handoff current`)。",
        "",
        "## 机器事实(自动采集, 别手改)",
        "",
    ]
    out += _fact_lines(facts)
    out.append("")
    for name in sections_for(kind):
        out += [f"## {name}", "", f"{_TODO_MARK}: {SECTION_HINTS.get(name, '')}", ""]
    return "\n".join(out).rstrip() + "\n"


# ── 校验 ─────────────────────────────────────────────────────────────────────


def _split_sections(text: str) -> dict[str, str]:
    marks = list(_SECTION_RE.finditer(text))
    out: dict[str, str] = {}
    for index, match in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        out[match.group(1).strip()] = text[match.end():end].strip()
    return out


def looks_like_path(token: str) -> bool:
    value = str(token or "").strip().strip("<>()[]").rstrip(".,;:")
    if not value or " -> " in value:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    if value.startswith(("./", "../")):
        return True
    if "/" not in value and "\\" not in value:
        return False
    if value.startswith(("http://", "https://", "omni ", "/")):
        return False
    first = re.split(r"[\\/]", value, maxsplit=1)[0]
    if first in _PATH_ROOTS:
        return True
    return bool(re.search(r"\.[A-Za-z0-9]{1,6}$", value))


def _path_exists(token: str, *, extra_roots: Iterable[Path] = ()) -> bool:
    value = str(token).strip().strip("<>()[]").rstrip(".,;:")
    candidates = [Path(value)] if Path(value).is_absolute() else []
    if not candidates:
        roots = [workspace_root(), workspace_root() / "docs" / "plans", *extra_roots]
        candidates = [root / value for root in roots]
    for candidate in candidates:
        try:
            if candidate.exists():
                return True
        except OSError:
            continue
    return False


def verify_doc(
    doc_path: Path | str,
    kind: str = "handoff",
    *,
    record: dict[str, Any] | None = None,
    min_body_chars: int = 20,
) -> list[str]:
    """返回问题清单; 空列表 = 合格。不合格就别交出去。"""
    path = to_abs(doc_path)
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"读不到文档: {path} ({exc})"]

    if record and record.get("id") and f'handoff_id: "{record["id"]}"' not in text:
        problems.append(f"frontmatter 的 handoff_id 与记录 {record['id']} 不一致")

    sections = _split_sections(text)
    extra_roots: list[Path] = []
    cwd = str(((record or {}).get("from") or {}).get("cwd") or "")
    if cwd:
        extra_roots.append(Path(cwd))

    for name in sections_for(kind):
        if name not in sections:
            problems.append(f"缺段落: ## {name}")
            continue
        body = sections[name]
        if _TODO_MARK in body:
            problems.append(f"段落未填写(还留着 {_TODO_MARK}): ## {name}")
            continue
        # 入口清单类段落靠"路径存在性"判合格, 一条短路径也算填了, 不卡字数。
        if name not in _PATH_SECTIONS and len(re.sub(r"\s+", "", body)) < min_body_chars:
            problems.append(f"段落正文过短(<{min_body_chars} 字): ## {name}")

    entry_body = sections.get("必读入口", "")
    tokens = [tok for tok in _BACKTICK_RE.findall(entry_body) if looks_like_path(tok)]
    if not tokens:
        problems.append("必读入口没有一条可检查的路径(用反引号包住文件/计划路径)")
    for token in tokens:
        if not _path_exists(token, extra_roots=extra_roots):
            problems.append(f"必读入口指向不存在的路径: `{token}`")

    if kind != "intake":
        verify_body = sections.get("复验命令", "")
        if len(_FENCE_RE.findall(verify_body)) < 2:
            problems.append("复验命令缺 fenced code block(接手方要能直接复制去跑)")
    return problems


# ── seed prompt(继任会话的第一句话) ─────────────────────────────────────────


def seed_prompt(
    record: dict[str, Any],
    *,
    doc_abs: str,
    slice_text: str | None = None,
    max_chars: int = 11000,
) -> str:
    """继任会话的开场指令。只给入口不搬正文 —— 文档在磁盘上, 让它自己去读。"""
    binding = record.get("binding") or {}
    origin = record.get("from") or {}
    scope = slice_text.strip() if slice_text and slice_text.strip() else "文档「未完成与下一步」里的全部条目, 按顺序推进"
    lines = [
        f"你是这条工作的接手方(继承会话)。上一棒因「{record.get('reason') or '交接'}」交出, "
        f"交接记录 id `{record['id']}`。",
        "",
        "按顺序做, 别跳:",
        f"1. 完整读交接文档(唯一权威, 别只读摘要): {doc_abs}",
        f"2. 登记接手: omni handoff adopt {record['id']}",
        "3. 从文档「复验命令」里至少跑一条, 自己确认当前状态 —— 别信结论就动手",
        f"4. 文档与实际不符 / 有分歧: 先 omni handoff note {record['id']} \"...\" 记下来再继续",
        "",
        f"本棒范围: {scope}",
    ]
    binding_bits = [f"{key}={value}" for key, value in
                    (("project", binding.get("project")), ("plan", binding.get("plan")),
                     ("workstream", binding.get("workstream")), ("task", binding.get("task")))
                    if value]
    if binding_bits:
        lines.append("绑定: " + " ".join(binding_bits))
    if origin.get("cwd"):
        lines.append(f"工作目录: {origin['cwd']}")
    if origin.get("provider") or origin.get("session_id"):
        lines.append(f"上一棒会话: {origin.get('provider') or '?'}:{origin.get('session_id') or '-'}"
                     f" · trace {origin.get('trace_id') or '-'}")
    if origin.get("transcript"):
        lines.append(f"上一棒原始 transcript(要深读时读它, 不以摘要代替): {origin['transcript']}")
    lines += [
        "",
        "纪律: 不推翻文档里已判定的坑除非拿到新证据; 不扩范围; "
        f"这一棒也要交出去时用 omni handoff draft 写下一份, 别口头交接。",
    ]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


# ── 记录生命周期 ─────────────────────────────────────────────────────────────


def create_record(
    *,
    kind: str,
    title: str,
    reason: str = "",
    facts: dict[str, Any] | None = None,
    plan: str | None = None,
    project: str | None = None,
    workstream: str | None = None,
    task: str | None = None,
    of: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if kind not in KINDS:
        raise ValueError(f"kind 只能是 {KINDS}")
    clean_title = " ".join(str(title or "").split())
    if not clean_title:
        raise ValueError("title 不能为空")
    facts = facts if facts is not None else machine_facts()
    session = facts.get("session") or {}
    auto = facts.get("binding") or {}
    record = {
        "id": new_id(kind, clean_title, now),
        "kind": kind,
        "title": clean_title,
        "status": "draft",
        "reason": " ".join(str(reason or "").split()),
        "created_at": _now(),
        "of": of or None,
        "doc": "",
        "from": {
            "provider": session.get("provider"),
            "trace_id": session.get("trace_id"),
            "session_id": session.get("session_id"),
            "pty_id": session.get("pty_id"),
            "cwd": session.get("cwd"),
            "transcript": facts.get("transcript"),
        },
        "binding": {
            "plan": plan or auto.get("plan") or None,
            "project": project or auto.get("project") or None,
            "workstream": workstream or auto.get("workstream") or None,
            "task": task or auto.get("task") or None,
        },
        "successors": [],
        "adopted_by": [],
        "intake_docs": [],
        "notes": [],
    }
    return record, facts


def attach_successor(record: dict[str, Any], successor: dict[str, Any]) -> dict[str, Any]:
    record.setdefault("successors", []).append({**successor, "spawned_at": _now()})
    if record.get("status") in {"ready", "draft"}:
        record["status"] = "handed"
    return save_record(record)


def mark_adopted(record: dict[str, Any], *, adopter: dict[str, Any], note: str = "") -> dict[str, Any]:
    record.setdefault("adopted_by", []).append({**adopter, "at": _now(), "note": note})
    record["status"] = "adopted"
    return save_record(record)


def add_note(record: dict[str, Any], text: str, *, by: str = "") -> dict[str, Any]:
    clean = " ".join(str(text or "").split())
    if not clean:
        raise ValueError("note 不能为空")
    record.setdefault("notes", []).append({"at": _now(), "by": by, "text": clean[:1200]})
    return save_record(record)


def resolve_current(meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """本会话该读哪一份交接: 先按继任谱系, 再按计划, 最后按工作目录。"""
    if meta is None:
        try:
            from omnicompany.packages.services._core.identity import current_session_meta

            meta = dict(current_session_meta())
        except Exception:  # noqa: BLE001
            meta = {}
    rows = [row for row in list_records(kind="handoff", limit=0)
            if row.get("status") in {"ready", "handed", "adopted"}]
    if not rows:
        return None
    ids = {str(meta.get(key) or "") for key in ("pty_id", "trace_id", "session_id")} - {""}
    for row in rows:
        for successor in row.get("successors") or []:
            hit = {str(successor.get(key) or "") for key in ("pty_id", "worker_id", "provider_session_id")}
            if hit & ids:
                return row
    plan = str(meta.get("active_plan") or "")
    if plan:
        for row in rows:
            if str((row.get("binding") or {}).get("plan") or "") == plan:
                return row
    cwd = str(meta.get("cwd") or "")
    if cwd:
        for row in rows:
            if str((row.get("from") or {}).get("cwd") or "") == cwd:
                return row
    return None


__all__ = [
    "HANDOFF_SECTIONS",
    "INTAKE_SECTIONS",
    "KINDS",
    "add_note",
    "attach_successor",
    "create_record",
    "doc_target",
    "get_record",
    "list_records",
    "looks_like_path",
    "machine_facts",
    "mark_adopted",
    "new_id",
    "render_draft",
    "resolve_current",
    "save_record",
    "sections_for",
    "seed_prompt",
    "slugify",
    "store_root",
    "to_abs",
    "to_rel",
    "transcript_path",
    "verify_doc",
    "workspace_root",
]

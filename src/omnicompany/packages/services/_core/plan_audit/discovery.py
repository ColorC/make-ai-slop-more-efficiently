# [OMNI] origin=claude-code domain=services/plan_audit ts=2026-06-19T00:00:00Z type=service
# [OMNI] material_id="material:core.plan_audit.conversation_discovery.locator.py"
"""plan_audit.discovery — 对话取数 + plan↔对话关联挖掘.

复用 dashboard.ccdaemon.import_routes 的扫盘 / transcript 抽取, 但:
- load_full_transcript: 用更大的 cap (默认 600K / 单条 30K), 完整审计要看全对话, 不是 60K 截断版.
- find_conversation_by_session_id: 给定 session_id 在 scan_claude/scan_codex 里匹配拿 file 路径.
- discover_plan_conversations: 给 --against-plan 用. 三层:
    1. data/cc_sessions.json 里 active_plan==plan_id 的会话(跑过 ccdaemon 的, 少).
    2. 兜底: grep 全部 ~/.claude/projects/** + ~/.codex/sessions/** jsonl 找提到该 plan
       路径 / 标题 / dir 名的对话.
    3. 真"在执行/起草"的进一步筛选交给 PlanAuditor agent(它读上下文判断).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# 对话取数 (复用 import_routes, 不重写)
# ────────────────────────────────────────────────────────────────────────


def find_conversation_by_session_id(session_id: str, provider: str | None = None) -> dict | None:
    """在本机 ~/.claude / ~/.codex 历史里按 session_id 匹配一条对话.

    返回 import_routes 扫描条目 dict {provider, session_id, cwd, mtime, preview, file}, 找不到 None.
    provider 可选 ('claude_code'|'codex'), 缩小匹配范围.
    """
    from omnicompany.dashboard.ccdaemon.api import scan_claude, scan_codex

    items: list[dict] = []
    if provider in (None, "claude_code"):
        try:
            items.extend(scan_claude())
        except Exception:  # noqa: BLE001
            logger.debug("scan_claude failed", exc_info=True)
    if provider in (None, "codex"):
        try:
            items.extend(scan_codex())
        except Exception:  # noqa: BLE001
            logger.debug("scan_codex failed", exc_info=True)

    # 精确匹配优先, 再前缀匹配 (session_id 可能被用户截短)
    for it in items:
        if it.get("session_id") == session_id:
            return it
    for it in items:
        sid = it.get("session_id") or ""
        if sid.startswith(session_id) or session_id.startswith(sid):
            return it
    # 文件 stem 兜底 (claude session_id == jsonl stem)
    for it in items:
        try:
            if Path(it.get("file", "")).stem == session_id:
                return it
        except Exception:  # noqa: BLE001
            continue
    # _scan 有 60 文件 / 90 天上限(import_routes), 窗口外的老会话/小会话扫不到 —
    # 全量 rglob 直接按 session_id 定位兜底, 保证任意历史会话都能审.
    return _direct_find_by_session_id(session_id, provider)


def _cwd_from_jsonl(f: Path) -> str:
    """从 jsonl 头几行捞 cwd(claude 每行带 cwd; codex 在 payload.cwd/working_directory)."""
    try:
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(8):
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                cwd = obj.get("cwd")
                if not cwd:
                    payload = obj.get("payload")
                    if isinstance(payload, dict):
                        cwd = payload.get("cwd") or payload.get("working_directory")
                if cwd:
                    return str(cwd)
    except OSError:
        pass
    return ""


def _direct_find_by_session_id(session_id: str, provider: str | None) -> dict | None:
    """绕过 _scan 上限, 全量 rglob ~/.claude + ~/.codex 按 session_id 定位 jsonl."""
    roots: list[tuple[Path, str]] = []
    if provider in (None, "claude_code"):
        roots.append((Path.home() / ".claude" / "projects", "claude_code"))
    if provider in (None, "codex"):
        roots.append((Path.home() / ".codex" / "sessions", "codex"))
    for root, prov in roots:
        if not root.is_dir():
            continue
        try:
            for f in root.rglob("*.jsonl"):
                if _session_id_from_file(prov, f) == session_id or f.stem == session_id or session_id in f.name:
                    try:
                        mt = f.stat().st_mtime
                    except OSError:
                        mt = 0.0
                    return {
                        "provider": prov, "session_id": session_id,
                        "cwd": _cwd_from_jsonl(f), "file": str(f), "mtime": mt, "preview": "",
                    }
        except OSError:
            continue
    return None


def load_full_transcript(
    provider: str,
    file_path: str | Path,
    *,
    cap_chars: int = 600_000,
    per_msg: int = 30_000,
) -> tuple[list[dict], bool]:
    """读一条对话的完整 transcript, 用比 import_routes 默认更大的 cap.

    完整审计要看整段对话(用户每条指示 + agent 落地动作), 60K 截断会丢尾部指示.
    返回 (messages, truncated). messages=[{role:'user'|'assistant', text}].
    """
    from omnicompany.dashboard.ccdaemon.api import extract_transcript

    prov = "codex" if provider == "codex" else "claude"
    return extract_transcript(prov, Path(file_path), cap_chars=cap_chars, per_msg=per_msg)


# ────────────────────────────────────────────────────────────────────────
# plan ↔ 对话关联挖掘 (输入(2))
# ────────────────────────────────────────────────────────────────────────


def _cc_sessions_for_plan(plan_id: str) -> list[dict]:
    """第一层: data/cc_sessions.json 里 active_plan==plan_id 的会话(跑过 ccdaemon 的)."""
    from omnicompany.core.config import omni_workspace_root

    out: list[dict] = []
    path = omni_workspace_root() / "data" / "cc_sessions.json"
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    rows = data.values() if isinstance(data, dict) else (data if isinstance(data, list) else [])
    for s in rows:
        if not isinstance(s, dict):
            continue
        if s.get("active_plan") == plan_id:
            sid = s.get("claude_session_id") or s.get("id") or ""
            out.append({
                "provider": s.get("provider") or "claude_code",
                "session_id": sid,
                "cwd": s.get("cwd") or "",
                "match_reason": "cc_sessions.active_plan",
            })
    return out


def _plan_search_terms(plan_id: str) -> list[str]:
    """从 plan_id 抽用于 grep 的搜索词: 完整 id / dir 名 / 去日期的 topic 名."""
    terms: set[str] = {plan_id}
    # 末段 dir 名, 例 "[2026-06-18]GITHUB-PRODUCTIZATION"
    last = plan_id.rstrip("/").split("/")[-1]
    if last:
        terms.add(last)
        # 去 [date] 前缀的 topic 名
        m = re.match(r"^\[\d{4}-\d{2}-\d{2}\](.+)$", last)
        if m:
            topic = m.group(1).strip()
            if len(topic) >= 4:
                terms.add(topic)
    return [t for t in terms if t]


def _grep_transcripts_for_plan(plan_id: str, max_files: int = 400) -> list[dict]:
    """第二层兜底: 扫所有 jsonl 找提到 plan 路径/dir/topic 的对话.

    只读文本子串匹配(不解 json 每行, 求快). 命中即记 (provider, session_id, file, match_reason).
    """
    terms = _plan_search_terms(plan_id)
    terms_lower = [t.lower() for t in terms]
    roots = [
        (Path.home() / ".claude" / "projects", "claude_code"),
        (Path.home() / ".codex" / "sessions", "codex"),
    ]
    seen: set[str] = set()
    out: list[dict] = []
    for root, provider in roots:
        if not root.is_dir():
            continue
        files: list[tuple[float, Path]] = []
        try:
            for p in root.rglob("*.jsonl"):
                try:
                    files.append((p.stat().st_mtime, p))
                except OSError:
                    continue
        except OSError:
            continue
        files.sort(key=lambda x: x[0], reverse=True)
        for _mt, f in files[:max_files]:
            key = str(f)
            if key in seen:
                continue
            try:
                # 读全文(jsonl 单会话通常 < 数 MB), 子串匹配
                blob = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            blob_lower = blob.lower()
            hit_term = next((t for t in terms_lower if t in blob_lower), None)
            if hit_term is None:
                continue
            seen.add(key)
            out.append({
                "provider": provider,
                "session_id": _session_id_from_file(provider, f),
                "file": str(f),
                "match_reason": f"transcript_mentions:{hit_term}",
            })
    return out


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _session_id_from_file(provider: str, f: Path) -> str:
    if provider == "claude_code":
        return f.stem
    m = _UUID_RE.search(f.name)
    return m.group(0) if m else f.stem


def discover_plan_conversations(plan_id: str) -> list[dict]:
    """合并三层来源, 去重, 返回候选对话列表(每条带 file 路径供后续读 transcript).

    每条: {provider, session_id, file, cwd?, match_reason}.
    真"在执行/起草该 plan"的进一步筛选交给 PlanAuditor agent(读上下文判断).
    """
    candidates: list[dict] = []
    candidates.extend(_cc_sessions_for_plan(plan_id))
    candidates.extend(_grep_transcripts_for_plan(plan_id))

    # 给第一层(cc_sessions)的条目补 file 路径(它只有 session_id)
    for c in candidates:
        if not c.get("file") and c.get("session_id"):
            hit = find_conversation_by_session_id(c["session_id"], c.get("provider"))
            if hit:
                c["file"] = hit.get("file", "")
                c.setdefault("cwd", hit.get("cwd", ""))

    # 去重(按 file 路径, 没 file 用 session_id)
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in candidates:
        key = c.get("file") or c.get("session_id") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        # 只保留真能读到 transcript 的
        if c.get("file") and Path(c["file"]).is_file():
            deduped.append(c)
    return deduped


def load_plan_md(plan_id: str) -> tuple[str, dict]:
    """读 plan.md 原文 + frontmatter(含 exit_criteria). 复用 controlplane.plans 解析器.

    返回 (plan_md_text, frontmatter_dict). 找不到返回 ("", {}).
    plan_id 形如 `<cat>/[date]NAME` 或 `<cat>/<proj>/[date]NAME`.
    """
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.core.plans_catalogue import parse_plan_frontmatter

    plans_root = omni_workspace_root() / "docs" / "plans"
    plan_md = plans_root / plan_id / "plan.md"
    if not plan_md.is_file():
        # 容错: plan_id 可能已含 /plan.md, 或就是个目录
        alt = plans_root / plan_id
        if alt.is_file():
            plan_md = alt
        elif (alt / "plan.md").is_file():
            plan_md = alt / "plan.md"
        else:
            return "", {}
    try:
        text = plan_md.read_text(encoding="utf-8")
    except OSError:
        return "", {}
    fm = parse_plan_frontmatter(plan_md)
    return text, fm

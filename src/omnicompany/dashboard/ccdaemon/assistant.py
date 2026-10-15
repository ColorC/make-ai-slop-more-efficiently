# [OMNI] origin=ai-ide ts=2026-09-05 type=dashboard
# [OMNI] material_id="material:dashboard.ccdaemon.assistant_provider_switch.py"
"""Assistant(机主的助理)会话: 落脚目录 + 模型阶梯 + 额度问题检测 + 历史索引。

2026-09-05 起 assistant 的定位从"只读的会话与材料管家"改为"机主的助理":
替机主记事、盯全机进度、把活派给正式会话或 subagent，自己不干活。

三个原则落在代码里:

* **规矩不预注入。** 助理的约束全部写在落脚目录 ``C:/workspace/assistant``
  的 ``AGENTS.md`` / ``CLAUDE.md`` 里(与 LiTeWork 里的「assistant」工作区同一个目录)。
  面板起会话时只播一句"开工", 不再把整段职责说明塞进第一条消息。
* **模型按阶梯挑, 随 provider 变。** Codex 的 Astra 最优先, 其次 Claude 的 Fable,
  再次 Claude 的 Opus, 最后才是 the_company API 的 Sol。每一档都先看这个 provider
  当前能不能用(模型在不在该账号的目录里、最近有没有报额度用尽), 不能用就往下走。
  额度用尽只在真的报错时才提醒机主, 平时不查、不提。
* **历任会话可查。** 起会话前把历任助理会话的记录文件位置刷进
  ``history/index.md``, 后来者接手先看它。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from omnicompany.core.config import omni_workspace_root

logger = logging.getLogger(__name__)

# ── 身份与落脚目录 ─────────────────────────────────────────────────────────

ASSISTANT_TOPIC = "assistant"

#: 覆盖落脚目录用的环境变量(测试和非标准部署用)。
ASSISTANT_DIR_ENV = "OMNI_ASSISTANT_DIR"


def assistant_dir() -> Path:
    """助理的落脚目录: 默认是全机工作区根下的 ``assistant``。"""
    configured = os.environ.get(ASSISTANT_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return omni_workspace_root().parent / "assistant"


# ── 模型阶梯 ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AssistantRung:
    provider: str
    model: str
    #: 助理会话用的推理档位; 机主定的是一律 high。
    effort: str = "high"


#: 从高到低。同一个模型在两个账号上各占一档, 前一个账号额度用完自动落到后一个。
ASSISTANT_MODEL_LADDER: tuple[AssistantRung, ...] = (
    AssistantRung("codex", "gpt-6-astra"),
    AssistantRung("claude_2", "fable"),
    AssistantRung("claude_code", "fable"),
    AssistantRung("claude_2", "claude-opus-5-5"),
    AssistantRung("claude_code", "claude-opus-5-5"),
    AssistantRung("codex_the_company", "gpt-6.1-sol"),
)

#: 兜底(阶梯全部不可用时): 阶梯最后一档。
FALLBACK_PROVIDER = ASSISTANT_MODEL_LADDER[-1].provider
FALLBACK_MODEL: str | None = ASSISTANT_MODEL_LADDER[-1].model

#: 某个 provider 报过额度用尽后, 多久之内不再排它。
EXHAUSTED_TTL_S = 12 * 3600

_CODEX_FAMILY = {"codex", "codex_2"}


def _provider_state_path() -> Path:
    return omni_workspace_root() / "data" / "assistant_provider_state.json"


def _read_provider_state() -> dict[str, Any]:
    path = _provider_state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exhausted": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("exhausted"), dict):
        return {"exhausted": {}}
    return raw


def _write_provider_state(state: dict[str, Any]) -> None:
    path = _provider_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:  # 状态文件只是优化, 写不进去不影响起会话
        logger.warning("assistant provider state not persisted: %s", exc)


def mark_provider_exhausted(provider: str, *, reason: str = "") -> None:
    """记一笔: 这个 provider 刚报了额度用尽, 一段时间内挑模型时跳过它。"""
    state = _read_provider_state()
    state["exhausted"][provider] = {"at": time.time(), "reason": reason[:300]}
    _write_provider_state(state)


def exhausted_providers(*, now: float | None = None) -> set[str]:
    """最近报过额度用尽、还没过期的 provider。"""
    moment = time.time() if now is None else now
    state = _read_provider_state()
    live: set[str] = set()
    changed = False
    for provider, row in list(state["exhausted"].items()):
        at = float(row.get("at") or 0) if isinstance(row, dict) else 0.0
        if moment - at < EXHAUSTED_TTL_S:
            live.add(provider)
        else:
            state["exhausted"].pop(provider, None)
            changed = True
    if changed:
        _write_provider_state(state)
    return live


def _codex_home(provider: str) -> Path:
    """某个 Codex 账号的家目录(第二账号在环境变量里)。"""
    try:
        from .provider_identity import second_account_env

        env = second_account_env(provider)
    except Exception:  # noqa: BLE001 - 身份表读不到就按主账号算
        env = {}
    # 主账号固定看 ~/.codex, 不读进程环境里的 CODEX_HOME:
    # ccdaemon 自己可能跑在第二账号环境下, 那会把两个账号看成同一个目录。
    configured = env.get("CODEX_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".codex"


def codex_model_available(provider: str, model: str) -> bool:
    """这个 Codex 账号的模型目录里有没有该模型。

    目录文件(``models_cache.json``)是 CLI 登录后拉回来的; 文件不存在时不下结论,
    当作可用, 让真实调用去说话。
    """
    cache = _codex_home(provider) / "models_cache.json"
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    rows = raw.get("models") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return True
    slugs = {
        str(row.get("slug") or row.get("id") or "")
        for row in rows
        if isinstance(row, dict)
    }
    # Existing Codex homes may still have the legacy catalog slug while the
    # canonical model ID is GPT-6.1 Sol. Treat that cache entry as equivalent
    # until the CLI refreshes models_cache.json.
    if model == "gpt-6.1-sol" and "gpt-6-sol" in slugs:
        return True
    return model in slugs


#: 最近多少小时内、最近几份 Codex 对话记录里出现过"额度用尽"就当这一档不可用。
_USAGE_LIMIT_LOOKBACK_S = 12 * 3600
_USAGE_LIMIT_RECENT_FILES = 6


def codex_usage_limit_recent(provider: str, *, now: float | None = None) -> bool:
    """这个 Codex 账号最近的对话记录里有没有"usage limit"报错。

    Codex 把每轮的错误写进家目录 ``sessions/`` 下的记录文件, 账号额度用尽时
    ``codex_error_info`` 是 ``usage_limit_exceeded``。只看最近几份、最近几小时
    的文件, 不做任何网络调用。
    """
    sessions = _codex_home(provider) / "sessions"
    if not sessions.is_dir():
        return False
    moment = time.time() if now is None else now
    try:
        files = sorted(sessions.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return False
    for file in files[:_USAGE_LIMIT_RECENT_FILES]:
        try:
            if moment - file.stat().st_mtime > _USAGE_LIMIT_LOOKBACK_S:
                break
            tail = file.read_bytes()[-64 * 1024:].decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in tail.splitlines():
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            payload = row.get("payload") if isinstance(row, dict) else None
            if not (
                row.get("type") == "event_msg"
                and isinstance(payload, dict)
                and payload.get("type") == "task_complete"
            ):
                continue
            error = payload.get("error")
            if isinstance(error, dict) and error.get("codex_error_info") == "usage_limit_exceeded":
                return True
    return False


def rung_available(rung: AssistantRung, *, exhausted: set[str]) -> bool:
    if rung.provider in exhausted:
        return False
    if rung.provider in _CODEX_FAMILY:
        if not codex_model_available(rung.provider, rung.model):
            return False
        if codex_usage_limit_recent(rung.provider):
            return False
    return True


def resolve_assistant_launch(*, exclude: Iterable[str] = ()) -> AssistantRung:
    """按阶梯挑出当前该用的 provider/model; 全部不可用时退到最后一档。"""
    skip = set(exclude) | exhausted_providers()
    for rung in ASSISTANT_MODEL_LADDER:
        if rung_available(rung, exhausted=skip):
            return rung
    return ASSISTANT_MODEL_LADDER[-1]


# ── 开场与压缩后的最小提示(不是规矩, 规矩在 AGENTS.md) ─────────────────────

ASSISTANT_BOOTSTRAP_TASK = (
    "开工。先按本目录 AGENTS.md 写的开工顺序把自己带起来, "
    "然后用三五句话告诉机主: 整台机器现在有哪些事在跑、待办里最要紧的是什么。"
)

# 压缩后只提醒"规矩在哪", 一句话, 不复述任何规则。
ASSISTANT_COMPACT_CONTEXT = (
    "这是机主的助理会话。规矩全在工作目录的 AGENTS.md 里; 压缩后先重读它和 TODO.md 再继续。"
)


# ── 额度问题检测 ────────────────────────────────────────────────────────────

_BILLING_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"insufficient\s+(balance|quota|credit|funds)",
        r"402\s+(payment\s+required|insufficient)",
        r"quota\s+((has\s+been|is)\s+)?(exceeded|exhausted|reached)",
        r"billing\s+(error|failed|issue)",
        r"balance\s+(insufficient|too\s+low|exhausted)",
        r"credit[s]?\s+(insufficient|exhausted|not\s+enough)",
        r"out\s+of\s+(credit|quota|balance)",
        r"no\s+remaining\s+(credit|quota|balance)",
        r"usage\s+limit\s+(reached|exceeded|hit)",
        r"you('ve| have)\s+hit\s+your\s+(usage|rate)\s+limit",
        r"余额不足|欠费|费用不足|请充值|额度已用完|额度用尽",
    )
)


def detect_billing_issue(text: str) -> bool:
    """在输出文本里识别额度问题(欠费/配额/余额类报错)。"""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _BILLING_PATTERNS)


# ── 交接开场(换 provider 后的新会话第一句) ───────────────────────────────────

def assistant_handoff_task(
    *,
    previous_session_url: str,
) -> str:
    """换 provider 后只交付上一个会话的可点击链接。"""
    return previous_session_url


# ── 历史索引 ───────────────────────────────────────────────────────────────
#
# 助理的会话可能从 omnicompany 面板起(topic=assistant, 记在 ccdaemon 台账里),
# 也可能从 LiTeWork 的「assistant」工作区起(只在 Codex/Claude 的家目录里有记录)。
# 两边都以落脚目录为 cwd, 所以按 cwd 扫各家目录, 再并上台账里 topic=assistant 的
# 老会话(它们的 cwd 是旧位置 C:/workspace/), 就是完整的历任清单。

_HOME_DIRS: tuple[tuple[str, str], ...] = (
    ("codex", ".codex"),
    ("codex_2", ".codex-2"),
    ("claude_code", ".claude"),
    ("claude_2", ".claude-2"),
)


def _norm_path(value: str | os.PathLike[str] | None) -> str:
    if not value:
        return ""
    return str(value).replace("\\", "/").rstrip("/").lower()


def _claude_project_slug(path: Path) -> str:
    """Claude Code 按 cwd 建目录名: 盘符、分隔符和点都换成 '-'。"""
    text = str(path).replace("\\", "/")
    return re.sub(r"[^A-Za-z0-9]", "-", text)


def _human_text(text: str) -> str:
    """过滤 CLI 自己塞进第一条消息的东西(AGENTS.md 正文、环境标签等), 只留人写的话。"""
    stripped = text.strip()
    if stripped.startswith(("# AGENTS.md", "<", "# CLAUDE.md")):
        return ""
    return stripped


def _first_user_text_codex(path: Path) -> tuple[str, str, str]:
    """返回 (cwd, 第一条用户消息, 会话开始时间)。只读文件头几十行。"""
    cwd = ""
    first = ""
    started = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 80 and cwd and first:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                kind = row.get("type")
                payload = row.get("payload") or {}
                if kind == "session_meta":
                    cwd = str(payload.get("cwd") or "")
                    started = str(payload.get("timestamp") or row.get("timestamp") or "")
                elif kind == "event_msg" and payload.get("type") == "user_message" and not first:
                    first = _human_text(str(payload.get("message") or ""))
                elif kind == "response_item" and not first:
                    if payload.get("role") == "user":
                        content = payload.get("content") or []
                        texts = [c.get("text") for c in content if isinstance(c, dict) and c.get("text")]
                        first = _human_text(" ".join(t for t in texts if t))
                if cwd and first:
                    break
    except OSError:
        pass
    return cwd, first, started


def _first_user_text_claude(path: Path) -> tuple[str, str]:
    """返回 (第一条用户消息, 会话开始时间)。"""
    first = ""
    started = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 200:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") != "user":
                    continue
                message = row.get("message") or {}
                content = message.get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                    )
                if text.strip() and not text.startswith("<"):
                    first = text
                    started = str(row.get("timestamp") or "")
                    break
    except OSError:
        pass
    return first, started


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _scan_provider_homes(target: Path) -> list[dict[str, Any]]:
    target_key = _norm_path(target)
    rows: list[dict[str, Any]] = []
    home = Path.home()
    for provider, dir_name in _HOME_DIRS:
        base = home / dir_name
        if not base.is_dir():
            continue
        if provider.startswith("codex"):
            sessions = base / "sessions"
            if not sessions.is_dir():
                continue
            for file in sessions.rglob("rollout-*.jsonl"):
                cwd, first, started = _first_user_text_codex(file)
                if _norm_path(cwd) != target_key:
                    continue
                match = re.search(r"rollout-\S+?-([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl$", file.name)
                rows.append({
                    "source": "provider_home",
                    "provider": provider,
                    "session_id": match.group(1) if match else file.stem,
                    "file": str(file),
                    "cwd": cwd,
                    "started_at": started,
                    "mtime": file.stat().st_mtime,
                    "first_message": _clip(first),
                })
        else:
            project_dir = base / "projects" / _claude_project_slug(target)
            if not project_dir.is_dir():
                continue
            for file in project_dir.glob("*.jsonl"):
                first, started = _first_user_text_claude(file)
                rows.append({
                    "source": "provider_home",
                    "provider": provider,
                    "session_id": file.stem,
                    "file": str(file),
                    "cwd": str(target),
                    "started_at": started,
                    "mtime": file.stat().st_mtime,
                    "first_message": _clip(first),
                })
    return rows


def _scan_ledger() -> list[dict[str, Any]]:
    """ccdaemon 台账里 topic=assistant 的会话(含旧位置的历任)。"""
    # 与 ccdaemon.pty 的 meta_store 同一条路径规则(pty_parts 不是可 import 的包)。
    state_dir = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    hot = Path(state_dir) / "cc_sessions.json" if state_dir else omni_workspace_root() / "data" / "cc_sessions.json"
    rows: list[dict[str, Any]] = []
    for path in (hot, hot.with_name("cc_sessions.archive.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = raw if isinstance(raw, list) else (raw.get("sessions") or raw.get("items") or list(raw.values()))
        if isinstance(items, dict):
            items = list(items.values())
        for item in items:
            if not isinstance(item, dict) or item.get("topic") != ASSISTANT_TOPIC:
                continue
            created = item.get("created_at") or item.get("started_at") or 0
            rows.append({
                "source": "ccdaemon_ledger",
                "ledger_id": item.get("id"),
                "provider": item.get("provider"),
                "model": item.get("model"),
                "session_id": item.get("claude_session_id") or item.get("provider_session_id") or "",
                "file": "",
                "cwd": item.get("cwd") or "",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(created))) if created else "",
                "mtime": float(created or 0),
                "first_message": _clip(str(item.get("name") or item.get("last_user_preview") or "")),
            })
    return rows


def _merge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一个 provider 会话可能既在台账又在家目录: 按 session_id 合并, 家目录的文件路径优先。"""
    by_id: dict[str, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get("session_id") or "")
        if not sid:
            orphans.append(row)
            continue
        current = by_id.get(sid)
        if current is None:
            by_id[sid] = dict(row)
            continue
        merged = {**current, **{k: v for k, v in row.items() if v}}
        if current.get("file"):
            merged["file"] = current["file"]
        merged["ledger_id"] = current.get("ledger_id") or row.get("ledger_id")
        by_id[sid] = merged
    merged_rows = list(by_id.values()) + orphans
    merged_rows.sort(key=lambda r: float(r.get("mtime") or 0), reverse=True)
    return merged_rows


def _resolve_ledger_files(rows: list[dict[str, Any]]) -> None:
    """台账里的会话若家目录扫描没命中(旧位置), 按 session_id 到各家目录找记录文件。"""
    home = Path.home()
    for row in rows:
        if row.get("file") or not row.get("session_id"):
            continue
        sid = str(row["session_id"])
        provider = str(row.get("provider") or "")
        candidates: list[Path] = []
        if provider.startswith("codex"):
            for _p, dir_name in _HOME_DIRS:
                sessions = home / dir_name / "sessions"
                if sessions.is_dir():
                    candidates.extend(sessions.rglob(f"rollout-*{sid}.jsonl"))
        else:
            for _p, dir_name in _HOME_DIRS:
                projects = home / dir_name / "projects"
                if projects.is_dir():
                    candidates.extend(projects.glob(f"*/{sid}.jsonl"))
        if candidates:
            row["file"] = str(candidates[0])
            if not row.get("first_message"):
                if provider.startswith("codex"):
                    _cwd, first, _started = _first_user_text_codex(candidates[0])
                else:
                    first, _started = _first_user_text_claude(candidates[0])
                row["first_message"] = _clip(first)


def refresh_history_index(target: Path | None = None) -> Path:
    """重建 ``history/index.md`` 与 ``history/sessions.jsonl``; 返回 index.md 路径。"""
    root = target or assistant_dir()
    history = root / "history"
    history.mkdir(parents=True, exist_ok=True)

    rows = _merge_rows(_scan_provider_homes(root) + _scan_ledger())
    _resolve_ledger_files(rows)

    (history / "sessions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    lines = [
        "# 历任助理会话",
        "",
        f"刷新时间: {time.strftime('%Y-%m-%d %H:%M')}。由面板起会话时自动刷新; 手动刷新运行 `python history/refresh.py`。",
        "",
        "最新的在最上面。「记录文件」是 Codex/Claude 落在家目录里的原始对话记录, 想看细节就打开它, 或者 fork 那个会话让它自己讲。",
        "",
        "| 开始时间 | 来源 | 模型/provider | 会话 id | 记录文件 | 第一句 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        started = str(row.get("started_at") or "")[:16].replace("T", " ")
        source = "面板" if row.get("ledger_id") else "LiTeWork/CLI"
        if _norm_path(row.get("cwd")) != _norm_path(root):
            source += "(旧位置)"
        model = str(row.get("model") or "")
        prov = str(row.get("provider") or "")
        label = f"{model} @ {prov}" if model else prov
        sid = str(row.get("session_id") or row.get("ledger_id") or "")
        file = str(row.get("file") or "(未找到)")
        first = str(row.get("first_message") or "").replace("|", "\\|")
        lines.append(f"| {started} | {source} | {label} | {sid} | {file} | {first} |")
    if not rows:
        lines.append("| (还没有记录) | | | | | |")
    lines.append("")
    index = history / "index.md"
    index.write_text("\n".join(lines), encoding="utf-8")
    return index


__all__ = [
    "ASSISTANT_BOOTSTRAP_TASK",
    "ASSISTANT_COMPACT_CONTEXT",
    "ASSISTANT_DIR_ENV",
    "ASSISTANT_MODEL_LADDER",
    "ASSISTANT_TOPIC",
    "AssistantRung",
    "FALLBACK_MODEL",
    "FALLBACK_PROVIDER",
    "assistant_dir",
    "assistant_handoff_task",
    "codex_model_available",
    "codex_usage_limit_recent",
    "detect_billing_issue",
    "exhausted_providers",
    "mark_provider_exhausted",
    "refresh_history_index",
    "resolve_assistant_launch",
]

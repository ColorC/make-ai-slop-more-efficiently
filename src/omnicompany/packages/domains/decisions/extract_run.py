# [OMNI] origin=ai-ide domain=decisions ts=2026-06-19T00:00:00Z type=runner status=active
# [OMNI] summary="存量对话炼化 runner:断点续跑/增量。扫会话→精简→分块→便宜模型按v2(带证据+严格归位)抽→去重并库→checkpoint。用 omni LLM 网关(HTTP,不派生子进程,EDR 安全),不烧主对话 token。"
# [OMNI] why="用户要后台持续炼化存量、打标记、可断点重跑/增量/定期跑;之前每批烧 opus 100万token 不可持续。改用便宜模型(qwen3.6/gpt-5.3-codex)+ checkpoint。"
# [OMNI] tags=decisions,extraction,backlog,checkpoint,incremental
"""存量决策炼化 runner —— 断点续跑 + 增量。

checkpoint(data/domains/decisions/runs/extracted_sessions.json):
  {session_id: {extracted_at, n_added, source, status}}
跑一批(run_batch):取未炼会话 top-N → 每个 condense→chunk→便宜模型抽 observations(带证据+严格归位)
  → catalog.find 去重 → record 并库 → 标 checkpoint。挂了的标 error 下次跳过(不卡整批)。
定期跑:omni 调度器调 run_batch(增量:新会话自动进 pending)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import catalog
from ._paths import RUNS_ROOT, ensure_dirs
from .sources import conversation as cv

CHECKPOINT = RUNS_ROOT / "extracted_sessions.json"

# 显式钉一个确认可用的便宜-中端模型;别用 "default" 角色(它会路由到失效的 deepseek-v4-pro 401)。
# 用户点名 gpt-5.5 / codex;--model 可覆盖(如 qwen3.6-plus 更省)。
DEFAULT_MODEL = "gpt-5.5"

# 领域范围(严格归位;对不上填 needs-review)。与 v2 工作流一致。
_TAXONOMY = (
    "vilo=个人叙事卡牌游戏(薇洛想知道):世界观/卡牌/recipe/文风/密教式开局/又一天/核心欲望卡。"
    "demogame=公司日间游戏:GvE/公会对决/沙盘演兵/GRaid/StdLv/战斗公平化/配表/期数/公会等级/figma工作台。"
    "omnicompany=个人实验室/AI编排基建:决策记录系统/dashboard/governance/研究吸收/决策设施/aiworkspace系统组/网页转figma。"
    "tabletop=通用桌游底座/13原型(五件套/golden-layout)。anniv-fest=周年庆Demo3增量游戏(仓鼠/订单/轻工厂)。"
    "aigc=图像生成设施(aigc-lab/gen矩阵/审阅台)。resume=简历与求职管线。web-company=作品集公开发布(colorc.cc)。walker=自走棋/回合战棋。"
)

_EXTRACT_SYS = (
    "你从一段对话切片里提炼用户做过的、有长期价值的决策/判断/偏好/可证伪猜想(belief)。两条铁律:"
    "(1) 每条 evidence 必填:该决策对应的真实对话原文摘录(用户/助手原话+前后文,够判领域+决策真做过),不许只写一句结论、不许改写。"
    "(2) project 只由该条 evidence 严格判,对不上任何已知领域填 needs-review,绝不按'切片里提到过某项目'去猜。"
    "只记真实表达过的,别臆造;一次性闲聊/状态汇报/纯执行细节不记。"
)

_OBS_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["decision", "belief", "comment"]},
                    "statement": {"type": "string"},
                    "evidence": {"type": "string"},
                    "project": {"type": "string"},
                    "domain_signal": {"type": "string"},
                    "track_id": {"type": "string"},
                    "rationale": {"type": "string"},
                    "chosen": {"type": "array", "items": {"type": "string"}},
                    "rejected": {"type": "array", "items": {"type": "string"}},
                    "risk_if_wrong": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["kind", "statement", "evidence", "project"],
            },
        }
    },
    "required": ["observations"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── checkpoint ───────────────────────────────────────────────────────────────

def load_ckpt() -> dict:
    if not CHECKPOINT.is_file():
        return {}
    try:
        return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_ckpt(d: dict) -> None:
    ensure_dirs()
    CHECKPOINT.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def mark(session_id: str, n_added: int, *, source: str = "claude", status: str = "done", error: str = "") -> None:
    d = load_ckpt()
    d[session_id] = {"extracted_at": _now(), "n_added": n_added, "source": source, "status": status}
    if error:
        d[session_id]["error"] = error[:300]
    _save_ckpt(d)


def seed_done(session_ids: list[str], *, source: str = "claude") -> int:
    """把已经炼过的会话标进 checkpoint(让 runner 跳过),n_added 标 -1 表示历史批。"""
    d = load_ckpt()
    n = 0
    for sid in session_ids:
        if sid not in d:
            d[sid] = {"extracted_at": _now(), "n_added": -1, "source": source, "status": "done(legacy)"}
            n += 1
    _save_ckpt(d)
    return n


# ── pending ──────────────────────────────────────────────────────────────────

def pending_sessions(*, include_codex: bool = False, min_bytes: int = 3000) -> list[dict]:
    """未炼会话(不在 checkpoint 里),按大小升序(先炼小的、便宜先出活)。"""
    done = set(load_ckpt().keys())
    out = [s for s in cv.scan_claude_sessions()
           if s["session_id"] not in done and s.get("size", 0) >= min_bytes]
    out.sort(key=lambda s: s.get("size", 0))
    return out


# ── 抽取 ─────────────────────────────────────────────────────────────────────

def _extract_chunk(chunk: str, model: str | None) -> list[dict]:
    """抽一块。LLM 失败时**抛异常**(不静默吞成 0)——让上层把该会话标 error 下次重炼,
    而不是把'模型挂了'误当成'没决策'。"""
    from omnicompany.runtime.llm.structured import call_json

    res = call_json(
        system=_EXTRACT_SYS,
        user=json.dumps({"领域范围": _TAXONOMY, "对话切片": chunk}, ensure_ascii=False),
        schema=_OBS_SCHEMA, model=model or DEFAULT_MODEL,
        caller="decisions.extract_run", max_tokens=4000,
    )
    return (res or {}).get("observations", []) or []


def _is_dup(statement: str) -> bool:
    for h in catalog.find(statement, allow_semantic=False):
        if h.get("statement"):
            return True
    return False


def _upsert_obs(o: dict, session_id: str) -> bool:
    from . import record as record_one

    stmt = (o.get("statement") or "").strip()
    if not stmt or _is_dup(stmt):
        return False
    ds = ([{"option": x, "chosen": True, "why": ""} for x in (o.get("chosen") or [])]
          + [{"option": x, "chosen": False, "why": ""} for x in (o.get("rejected") or [])])
    ev = (o.get("evidence") or "").strip()
    fields = {
        "project": o.get("project") or "needs-review",
        "track": {"kind": "plan", "id": o.get("track_id") or "misc"},
        "rationale": o.get("rationale") or "",
        "authority": "derived",
        "origin": {"channel": "claude", "session_ref": session_id},
        "anchor": {"kind": "note", "ref": f"session:{session_id}", "excerpt": ev[:600]},
        "evidence": [{"ref": f"session:{session_id}", "note": ev}] if ev else None,
    }
    if ds:
        fields["decision_space"] = ds
    if o.get("risk_if_wrong"):
        fields["risk_if_wrong"] = o["risk_if_wrong"]
    record_one(o.get("kind") or "decision", stmt, **{k: v for k, v in fields.items() if v is not None})
    return True


def extract_session(path: str, session_id: str, *, model: str | None = None,
                    chunk_chars: int = 12000, overlap: int = 800) -> int:
    text = cv.condense_text(path)
    if not text.strip():
        return 0
    step = max(1, chunk_chars - overlap)
    added = 0
    for i in range(0, len(text), step):
        for o in _extract_chunk(text[i:i + chunk_chars], model):
            if _upsert_obs(o, session_id):
                added += 1
    return added


def run_batch(*, limit: int = 3, model: str | None = None, include_codex: bool = False) -> dict:
    """炼一批未炼会话(top-limit,小的先)。每个标 checkpoint;挂的标 error 不卡整批。"""
    pend = pending_sessions(include_codex=include_codex)
    processed = []
    for s in pend[:limit]:
        try:
            n = extract_session(s["path"], s["session_id"], model=model)
            mark(s["session_id"], n)
            processed.append({"session": s["session_id"], "added": n, "mb": round(s.get("size", 0) / 1e6, 1)})
        except Exception as e:  # noqa: BLE001 — 单会话失败不该卡整批
            mark(s["session_id"], 0, status="error", error=str(e))
            processed.append({"session": s["session_id"], "added": 0, "error": str(e)[:120]})
    if processed:
        catalog.rebuild_index()
    return {"processed": processed, "remaining": max(0, len(pend) - len(processed))}


def status() -> dict:
    ck = load_ckpt()
    pend = pending_sessions()
    errs = [k for k, v in ck.items() if v.get("status") == "error"]
    return {"done": len(ck), "remaining": len(pend), "errors": len(errs),
            "remaining_mb": round(sum(s.get("size", 0) for s in pend) / 1e6, 1)}

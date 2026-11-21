# [OMNI] origin=kimi-code domain=services/tech_debt ts=2026-07-26T00:00:00Z
# [OMNI] material_id="material:diagnosis.tech_debt.autotriage.py"
"""tech_debt.autotriage — 技术债自动分诊（LLM 判断 + 确定性 playbook 执行）。

背景（2026-07-26）：人工+agent 大清账后沉淀了处置模式，本模块把它自动化：

  1. 取候选：REGISTRY §活跃违规 中 status=open 且 OMNI- 开头、且
     var/tech_debt/autotriage_state.json 未标记已分诊的条目（--max N，默认 200/天）。
  2. 分诊：批量（~20 条/调用）喂便宜模型（gpt-5.6-terra）判
     action ∈ {playbook_fix / false_positive / needs_human} + 一句 reason + playbook 名。
  3. 执行：只对低风险机械类做确定性执行（模型不给自由改文件的机会）：
       - stamp_header  → omnicompany.core.omnimark.stamp_file（OMNI-001）
       - rename        → git mv + 同仓文本引用修复（OMNI-030，目标名模型给、代码校验）
       - add_kind_tag  → formats.py/materials.py 补 kind.* tag（OMNI-037）
     其余 playbook（035h 迁 data/删、024 迁移或 ALLOW、055 迁 src/归档、007 迁移）
     本轮只判不执行：判为可修也降级 needs_human（理由=执行面未建），等下一轮扩展。
  4. 销账：执行成功 → resolve_row(reason=autotriage:<playbook>)；
     false_positive → resolve_row(reason=autotriage:false-positive ...)；
     执行失败 / needs_human → 只记状态文件，不销账。
  5. digest：跑完自动刷新 data/services/tech_debt/digest-<date>.md。

成本护栏：单次运行 LLM 调用上限 MAX_LLM_CALLS、每条输入截断、
meta 记录 tokens（LLMMeter caller_prefix=tech_debt.autotriage）。

dry-run（默认）：不调执行、不写状态文件、不销账；只调 LLM 分诊并打印去向。
状态文件只在 apply 模式写 → 重跑幂等（已分诊条目跳过）。

不做：
  - 扫描（guardian 的职责）
  - path_gone / not_hit / 白名单销账（reconciler 的职责，先于本模块跑）
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .registry_io import (
    SECTION_SPECS,
    load_registry,
    resolve_row,
)

logger = logging.getLogger(__name__)

_STATE_RELPATH = "var/tech_debt/autotriage_state.json"
_DIGEST_DIR_RELPATH = "data/services/tech_debt"
_RECONCILE_RUNS_DIR = "docs/tech_debt/reconcile-runs"

# 用户指定 the_company/gpt-5.6-terra（便宜档 $0.25/M input）；ModelRegistry 注册名
# 为裸名 gpt-5.6-terra（"the_company/" 前缀是 opencode agent 配置的写法），
# call_json(model=...) 走注册名。
TRIAGE_MODEL = "gpt-5.6-terra"

DEFAULT_MAX_ENTRIES = 200     # 每天默认分诊上限
BATCH_SIZE = 20               # 每次 LLM 调用喂多少条
MAX_LLM_CALLS = 30            # 成本护栏：单次运行 LLM 调用上限
PATH_PROMPT_LIMIT = 200       # 每条输入 path 截断长度
_ACTIONS = ("playbook_fix", "false_positive", "needs_human")

# 本轮有确定性执行面的 playbook（其余只判不执行）
EXECUTABLE_PLAYBOOKS = ("stamp_header", "rename", "add_kind_tag")

# 分诊 prompt 用的规则简述 + playbook 判据（2026-07-26 清账沉淀）。
# key 规则未列出时模型按通用判据（needs_human / false_positive）判。
RULE_PLAYBOOK_BRIEF: dict[str, str] = {
    "OMNI-001": (
        "packages/ 下 Python 文件缺 [OMNI] 身份头。机械可修：playbook=stamp_header "
        "（调既有 stamp_file 补头）。文件若已删除或位于快照/归档树则 false_positive。"
    ),
    "OMNI-030": (
        "文件名含版本/状态标记（_v1/_old/_final/中段 v数字 等），版本应由 git 控制。"
        "机械可修：playbook=rename，target 给去掉版本标记后的新文件名（仅文件名，"
        "不含目录）。若该路径是版本化工作流产物（如 episodes 分集）或已不存在 → "
        "false_positive。"
    ),
    "OMNI-037": (
        "formats.py / materials.py 含 Material/Format 定义但整体缺 kind.* tag。"
        "机械可修：playbook=add_kind_tag，target 给应补的 tag 值 "
        "（kind.source=外部输入 / kind.internal=Worker 间流转 / kind.sink=终态产物，"
        "按该文件 Material 的角色三选一）。拿不准角色 → needs_human。"
    ),
    "OMNI-035h": (
        "docs/ 子目录出现 .json/.jsonl 数据产物，应迁 data/ 或删除。"
        "本轮执行面未建：确实该迁/删的也判 needs_human 并在 reason 里写明建议；"
        "若该 json 是文档性示例/契约样本 → false_positive。"
    ),
    "OMNI-024": (
        "Router 子类定义不在 routers.py / routers/ 标准位置。应迁移或加 ALLOW 豁免注释。"
        "本轮执行面未建：判 needs_human；若是测试夹具/伪 Router 误报 → false_positive。"
    ),
    "OMNI-055": (
        "data/ 域含可执行代码（.py/.sh/.ps1/.bat/.js/.ts），应在 src/ 下。"
        "本轮执行面未建：判 needs_human；若是数据产物的快照副本/归档样本 → "
        "false_positive。"
    ),
    "OMNI-007": (
        "src/ 下出现非预期 .md/.json/.yaml 杂散文件，应迁走或删除。"
        "本轮执行面未建：判 needs_human；若是就近标准资产（SKILL.md/prompt 素材/"
        "i18n/fixtures 等已放行类别）→ false_positive。"
    ),
}

_TRIAGE_SYSTEM = """你是技术债分诊员。输入是仓库技术债登记处的 open 违规条目（JSON 数组，
每条含 id/rule_id/path/rule_brief）。对每条判一个处置 action：

- playbook_fix：机械可修，按 rule_brief 指定的 playbook 执行（给 playbook 名；
  rename 时 target=新文件名，add_kind_tag 时 target=kind tag 值）。
- false_positive：误报（路径已不在、属已豁免/已放行类别、是快照或样本等）。
- needs_human：要人判（执行面未建、语义不明、风险高、拿不准一律落这里）。

判据要点：
- 拿不准一律 needs_human，不勉强 playbook_fix，不轻易 false_positive。
- path 形如 "旧路径\\t新路径" 的是改名记录，按新路径判。
- reason 一句话中文，说明判定依据。

对输入数组里每一条都要给出 verdict，id 原样带回，不要漏条。"""

TRIAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "action": {"type": "string", "enum": list(_ACTIONS)},
                    "playbook": {"type": "string"},
                    "reason": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["id", "action", "reason"],
            },
        },
    },
    "required": ["verdicts"],
}


# ─── 状态文件 ────────────────────────────────────────────────────

def load_triage_state(root: Path) -> dict[str, Any]:
    """读分诊状态；文件不存在/损坏返回空状态。"""
    path = root / _STATE_RELPATH
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"triaged": {}}
    if not isinstance(state, dict) or not isinstance(state.get("triaged"), dict):
        return {"triaged": {}}
    return state


def _save_triage_state(root: Path, state: dict[str, Any]) -> None:
    path = root / _STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ─── 候选提取 ────────────────────────────────────────────────────

def collect_candidates(root: Path, max_n: int, state: dict[str, Any]) -> list[dict[str, str]]:
    """REGISTRY §活跃违规 中 open 且 OMNI- 开头、未分诊过的条目，最多 max_n 条。"""
    snapshot = load_registry(root)
    triaged = state.get("triaged", {})
    out: list[dict[str, str]] = []
    for row in snapshot.sections.get("activity", []):
        if len(out) >= max_n:
            break
        if row.status != "open":
            continue
        rule_id = row.fields.get("rule_id", "")
        if not rule_id.startswith("OMNI-"):
            continue
        if row.id in triaged:
            continue
        out.append({
            "id": row.id,
            "rule_id": rule_id,
            "path": row.fields.get("path", ""),
            "severity": row.fields.get("severity", ""),
        })
    return out


# ─── LLM 分诊 ────────────────────────────────────────────────────

def _normalize_path(p: str) -> str:
    return p.strip().strip('"').strip("'").strip().replace("\\", "/")


def build_triage_prompt(batch: list[dict[str, str]]) -> tuple[str, str]:
    """构造 (system, user)。user 是 JSON 数组字符串，每条附 rule_brief。"""
    items = []
    for c in batch:
        items.append({
            "id": c["id"],
            "rule_id": c["rule_id"],
            "path": _normalize_path(c.get("path", ""))[:PATH_PROMPT_LIMIT],
            "rule_brief": RULE_PLAYBOOK_BRIEF.get(
                c["rule_id"],
                "无专项 playbook：按通用判据判 needs_human 或 false_positive。",
            ),
        })
    return _TRIAGE_SYSTEM, json.dumps(items, ensure_ascii=False, indent=1)


def call_triage_llm(batch: list[dict[str, str]], *, model: str = TRIAGE_MODEL) -> dict[str, dict]:
    """调统一结构化 LLM 槽位分诊一批条目。返回 {row_id: verdict}。"""
    from omnicompany.runtime.llm import call_json

    system, user = build_triage_prompt(batch)
    res = call_json(
        system=system,
        user=user,
        schema=TRIAGE_SCHEMA,
        model=model,
        caller="tech_debt.autotriage.triage",
        max_tokens=4000,
    )
    out: dict[str, dict] = {}
    for v in (res or {}).get("verdicts") or []:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("id", "")).strip()
        action = str(v.get("action", "")).strip()
        if not vid or action not in _ACTIONS:
            continue
        out[vid] = {
            "action": action,
            "playbook": str(v.get("playbook", "")).strip(),
            "reason": str(v.get("reason", "")).strip()[:200],
            "target": str(v.get("target", "")).strip(),
        }
    return out


# ─── playbook 确定性执行 ─────────────────────────────────────────

def _exec_stamp_header(root: Path, rel_path: str, target: str) -> tuple[bool, str]:
    """OMNI-001：调既有 stamp_file 补 OmniMark 头。"""
    from omnicompany.core.omnimark import stamp_file

    p = root / rel_path
    if not p.exists():
        return False, f"文件不存在: {rel_path}"
    if p.suffix != ".py":
        return False, f"非 .py 文件不补头: {rel_path}"
    ok = stamp_file(p, origin="tech_debt-autotriage")
    return (True, "已补 OmniMark 头") if ok else (False, "stamp_file 写入失败")


_VERSION_MARK_RE = re.compile(
    r"(_v\d+|_old|_new|_final|_bak|_backup|_copy|_draft|_temp|_tmp)(?=\.|_|-|$)",
    re.IGNORECASE,
)


def suggest_rename_target(basename: str) -> str:
    """确定性去掉文件名里的版本/状态标记（模型没给 target 时的兜底）。"""
    stem, dot, suffix = basename.rpartition(".")
    if not dot:
        stem, suffix = basename, ""
    new_stem = _VERSION_MARK_RE.sub("", stem)
    new_stem = re.sub(r"[_\-]{2,}", "_", new_stem).strip("_-")
    if not new_stem:
        return ""
    return new_stem + (("." + suffix) if suffix else "")


# 引用修复覆盖的文本扩展名与跳过目录（保守：同仓文本引用）
_REF_TEXT_EXTS = {".md", ".py", ".yaml", ".yml", ".toml", ".json", ".jsonl",
                  ".ts", ".tsx", ".js", ".txt", ".cfg", ".ini"}
_REF_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__",
                  "data", "_archive", "_graveyard", ".omni"}
_REF_FILE_SIZE_CAP = 1_000_000


def _fix_references(root: Path, old_rel: str, new_rel: str) -> int:
    """把同仓文本文件里的 old 路径/文件名引用改成 new。返回改动文件数。"""
    old_base = old_rel.rsplit("/", 1)[-1]
    new_base = new_rel.rsplit("/", 1)[-1]
    changed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _REF_SKIP_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if fp.suffix.lower() not in _REF_TEXT_EXTS:
                continue
            try:
                if fp.stat().st_size > _REF_FILE_SIZE_CAP:
                    continue
                text = fp.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            new_text = text.replace(old_rel, new_rel)
            if old_base != new_base:
                new_text = new_text.replace(old_base, new_base)
            if new_text != text:
                try:
                    fp.write_text(new_text, encoding="utf-8")
                    changed += 1
                except OSError:
                    continue
    return changed


def _exec_rename(root: Path, rel_path: str, target: str) -> tuple[bool, str]:
    """OMNI-030：改名（git mv 优先）+ 同仓文本引用修复。target 只接受文件名。"""
    rel_path = _normalize_path(rel_path)
    p = root / rel_path
    if not p.exists():
        return False, f"文件不存在: {rel_path}"
    old_base = rel_path.rsplit("/", 1)[-1]
    new_base = Path(target.strip()).name if target.strip() else ""
    if not new_base:
        new_base = suggest_rename_target(old_base)
    if not new_base or new_base == old_base:
        return False, f"无法确定合法新名（target={target!r}）"
    if _VERSION_MARK_RE.search(new_base):
        return False, f"新名仍含版本标记: {new_base}"
    new_rel = rel_path[: len(rel_path) - len(old_base)] + new_base
    new_p = root / new_rel
    if new_p.exists():
        return False, f"目标已存在: {new_rel}"

    try:
        proc = subprocess.run(
            ["git", "mv", "--", rel_path, new_rel],
            cwd=root, capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            p.rename(new_p)  # 未跟踪文件 git mv 失败 → 普通改名
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"改名失败: {e}"
    if not new_p.exists():
        return False, "改名后目标文件不存在"

    n_refs = _fix_references(root, rel_path, new_rel)
    return True, f"改名 {old_base} → {new_base}（修引用 {n_refs} 个文件）"


_KIND_TAG_VALUES = ("kind.source", "kind.internal", "kind.sink")
_TAGS_LIST_RE = re.compile(r"tags\s*=\s*\[", re.MULTILINE)


def _exec_add_kind_tag(root: Path, rel_path: str, target: str) -> tuple[bool, str]:
    """OMNI-037：往 formats.py / materials.py 第一个 tags=[...] 里补 kind.* tag。"""
    tag = target.strip().strip('"').strip("'")
    if tag not in _KIND_TAG_VALUES:
        return False, f"非法 kind tag: {target!r}（须为 {'/'.join(_KIND_TAG_VALUES)}）"
    p = root / _normalize_path(rel_path)
    if not p.exists():
        return False, f"文件不存在: {rel_path}"
    try:
        content = p.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as e:
        return False, f"读文件失败: {e}"
    if f'"{tag}"' in content or f"'{tag}'" in content:
        return True, f"已含 {tag}（无需改动）"
    m = _TAGS_LIST_RE.search(content)
    if m is None:
        return False, "找不到 tags=[ 锚点，无法确定性插入"
    insert_at = m.end()
    new_content = content[:insert_at] + f' "{tag}",' + content[insert_at:]
    try:
        p.write_text(new_content, encoding="utf-8")
    except OSError as e:
        return False, f"写文件失败: {e}"
    return True, f"已补 tag {tag}"


_PLAYBOOK_EXECUTORS: dict[str, Callable[[Path, str, str], tuple[bool, str]]] = {
    "stamp_header": _exec_stamp_header,
    "rename": _exec_rename,
    "add_kind_tag": _exec_add_kind_tag,
}


# ─── 主流程 ──────────────────────────────────────────────────────

def _meter_summary() -> dict[str, Any]:
    """读 LLMMeter 里本模块 caller 的 tokens/成本（槽位有计量则记录）。"""
    try:
        from omnicompany.runtime.llm.llm import LLMMeter

        return LLMMeter.get_instance().summary(caller_prefix="tech_debt.autotriage")
    except Exception:  # noqa: BLE001 — 计量失败不影响主流程
        return {}


def run_autotriage(
    project_root: str | Path,
    *,
    apply: bool = False,
    max_n: int = DEFAULT_MAX_ENTRIES,
    model: str = TRIAGE_MODEL,
    llm_fn: Callable[..., dict[str, dict]] | None = None,
    refresh_digest: bool = True,
) -> dict[str, Any]:
    """自动分诊主入口。

    dry-run（apply=False，默认）：调 LLM 分诊并统计去向，不改文件/状态/REGISTRY。
    apply：执行可执行 playbook、销账、写状态文件、刷新当天 digest。

    llm_fn：可注入的分诊函数（测试用），签名同 call_triage_llm。
    """
    root = Path(project_root)
    llm_fn = llm_fn or call_triage_llm
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "apply": apply,
        "model": model,
        "candidates": 0,
        "triaged": 0,
        "llm_calls": 0,
        "llm_errors": 0,
        "by_action": {"playbook_fix": 0, "false_positive": 0, "needs_human": 0},
        "executed": {},          # playbook → 成功数
        "exec_failed": 0,
        "resolved": 0,
        "downgraded": 0,         # 执行面未建而降级 needs_human 的条数
        "verdicts": [],          # 全量判定（digest/打印用）
        "digest_file": None,
        "meter": {},
        "error": None,
    }

    state = load_triage_state(root)
    try:
        candidates = collect_candidates(root, max_n, state)
    except FileNotFoundError as e:
        result["error"] = str(e)
        return result
    result["candidates"] = len(candidates)

    batches = [candidates[i: i + BATCH_SIZE]
               for i in range(0, len(candidates), BATCH_SIZE)]
    verdict_by_id: dict[str, dict] = {}
    for batch in batches:
        if result["llm_calls"] >= MAX_LLM_CALLS:
            break
        result["llm_calls"] += 1
        try:
            verdict_by_id.update(llm_fn(batch, model=model))
        except Exception as e:  # noqa: BLE001 — 单批失败只丢这批，下轮重试
            result["llm_errors"] += 1
            logger.warning("autotriage: LLM 分诊批失败（%d 条）: %s", len(batch), e)

    by_id = {c["id"]: c for c in candidates}
    for vid, verdict in verdict_by_id.items():
        cand = by_id.get(vid)
        if cand is None:
            continue
        action = verdict["action"]
        playbook = verdict["playbook"]
        reason = verdict["reason"]
        outcome = action  # 最终落地动作（可能因执行面/执行失败改变）

        # 护栏(2026-07-26 裁决口径): _archive/_scratch/快照树路径不允 terra 直接
        # false_positive 销账——归档区违规要走 rule_fixed/白名单的显式裁决通道,
        # terra 判 fp 也降级 needs_human, 防止"归档目录非现行执行面"一刀切。
        _p = str(cand.get("path") or "").replace("\\", "/")
        if action == "false_positive" and any(
            seg in _p for seg in ("/_archive/", "/_scratch/", "/_graveyard/")
        ):
            outcome = "needs_human"
            result["downgraded"] += 1
            reason = f"{reason}（归档/快照路径护栏: FP 需显式裁决, 不自动销账）".strip("（） ")

        # 执行面未建的 playbook：判为可修也降级 needs_human
        if action == "playbook_fix" and playbook not in EXECUTABLE_PLAYBOOKS:
            outcome = "needs_human"
            result["downgraded"] += 1
            reason = f"{reason}（执行面未建，本轮不自动执行）".strip("（） ")

        exec_note = ""
        if apply:
            if outcome == "playbook_fix":
                ok, exec_note = _PLAYBOOK_EXECUTORS[playbook](
                    root, cand["path"], verdict["target"],
                )
                if ok:
                    r = resolve_row(
                        root, vid,
                        reason=f"autotriage:{playbook} — {exec_note}",
                        resolved_by="tech_debt-autotriage",
                    )
                    if r.ok:
                        result["resolved"] += 1
                        result["executed"][playbook] = (
                            result["executed"].get(playbook, 0) + 1
                        )
                    else:
                        outcome = "needs_human"
                        exec_note = f"销账失败: {r.error}"
                else:
                    outcome = "needs_human"
                    exec_note = f"执行失败: {exec_note}"
                if outcome == "needs_human":
                    result["exec_failed"] += 1
            elif outcome == "false_positive":
                r = resolve_row(
                    root, vid,
                    reason=f"autotriage:false-positive — {reason}",
                    resolved_by="tech_debt-autotriage",
                )
                if r.ok:
                    result["resolved"] += 1
                else:
                    outcome = "needs_human"
                    exec_note = f"销账失败: {r.error}"

        result["by_action"][outcome] = result["by_action"].get(outcome, 0) + 1
        result["triaged"] += 1
        entry = {
            "id": vid,
            "rule_id": cand["rule_id"],
            "path": _normalize_path(cand["path"])[:PATH_PROMPT_LIMIT],
            "action": outcome,
            "llm_action": action,
            "playbook": playbook,
            "reason": reason,
            "exec_note": exec_note,
        }
        result["verdicts"].append(entry)
        if apply:
            state["triaged"][vid] = {
                "rule_id": cand["rule_id"],
                "path": entry["path"],
                "action": outcome,
                "playbook": playbook,
                "reason": reason,
                "ts": now_iso,
            }

    if apply and result["triaged"]:
        state["last_run"] = {"ts": now_iso, "triaged": result["triaged"],
                             "resolved": result["resolved"]}
        try:
            _save_triage_state(root, state)
        except OSError as e:
            result["error"] = f"状态文件写入失败: {e}"

    result["meter"] = _meter_summary()

    if refresh_digest and (apply or result["triaged"]):
        try:
            result["digest_file"] = write_digest(root, run_result=result, date=today)
        except OSError as e:
            logger.warning("autotriage: digest 写入失败: %s", e)

    return result


# ─── digest ──────────────────────────────────────────────────────

def _recent_reconcile_runs(root: Path, n: int = 3) -> list[dict[str, Any]]:
    """读最近 n 份 reconcile 明细（新增趋势）。"""
    runs_dir = root / _RECONCILE_RUNS_DIR
    out: list[dict[str, Any]] = []
    try:
        files = sorted(runs_dir.glob("reconcile-*.json"))[-n:]
    except OSError:
        return out
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            out.append({
                "ts": data.get("ts", fp.stem),
                "resolved_total": data.get("resolved_total", 0),
            })
        except (OSError, ValueError):
            continue
    return out


def build_digest_markdown(
    root: Path,
    *,
    date: str,
    run_result: dict[str, Any] | None = None,
) -> str:
    """生成一屏 markdown digest：本轮统计 + needs_human 清单 + 新增趋势。"""
    state = load_triage_state(root)
    lines: list[str] = [
        f"# 技术债自动分诊 digest（{date}）",
        "",
    ]

    if run_result is not None:
        mode = "APPLY" if run_result.get("apply") else "DRY-RUN"
        lines += [
            f"## 本轮处置（{mode}）",
            "",
            f"- 候选 {run_result.get('candidates', 0)} 条 · "
            f"分诊 {run_result.get('triaged', 0)} 条 · "
            f"LLM 调用 {run_result.get('llm_calls', 0)} 次"
            f"（失败 {run_result.get('llm_errors', 0)} 批）",
        ]
        meter = run_result.get("meter") or {}
        if meter.get("call_count"):
            lines.append(
                f"- tokens: in={meter.get('total_input_tokens', 0)} "
                f"out={meter.get('total_output_tokens', 0)} "
                f"· 成本约 ${meter.get('total_cost_usd', 0):.4f}"
            )
        ba = run_result.get("by_action") or {}
        lines.append(
            f"- 去向: playbook_fix {ba.get('playbook_fix', 0)} · "
            f"false_positive {ba.get('false_positive', 0)} · "
            f"needs_human {ba.get('needs_human', 0)}"
        )
        if run_result.get("apply"):
            executed = run_result.get("executed") or {}
            ex_txt = " · ".join(f"{k}={v}" for k, v in sorted(executed.items())) or "无"
            lines.append(
                f"- 执行: {ex_txt} · 销账 {run_result.get('resolved', 0)} 条 · "
                f"执行失败降级 {run_result.get('exec_failed', 0)} 条 · "
                f"执行面未建降级 {run_result.get('downgraded', 0)} 条"
            )
        lines.append("")

    # needs_human 清单：本轮 verdicts 优先，否则状态文件里的 needs_human
    nh_entries: list[dict[str, Any]] = []
    if run_result is not None:
        nh_entries = [v for v in run_result.get("verdicts", [])
                      if v.get("action") == "needs_human"]
    else:
        for vid, rec in (state.get("triaged") or {}).items():
            if rec.get("action") == "needs_human":
                nh_entries.append({"id": vid, **rec})

    lines += ["## needs_human 清单", ""]
    if not nh_entries:
        lines.append("（无）")
    else:
        by_rule: dict[str, list[dict[str, Any]]] = {}
        for e in nh_entries:
            by_rule.setdefault(e.get("rule_id", "?"), []).append(e)
        for rule_id, entries in sorted(by_rule.items(), key=lambda x: -len(x[1])):
            lines.append(f"### {rule_id}（{len(entries)} 条）")
            for e in entries[:3]:
                sample = e.get("path") or e.get("id", "")
                reason = (e.get("reason") or "").strip()
                lines.append(f"- {e.get('id', '')} `{sample}` — {reason}")
            if len(entries) > 3:
                lines.append(f"- … 其余 {len(entries) - 3} 条略")
            lines.append("")

    lines += ["## 新增趋势（reconcile 最近几轮）", ""]
    runs = _recent_reconcile_runs(root)
    if not runs:
        lines.append("（无 reconcile 记录）")
    else:
        for r in runs:
            lines.append(f"- {r['ts']}: 销账 {r['resolved_total']} 条")
    lines.append("")
    return "\n".join(lines)


def write_digest(
    project_root: str | Path,
    *,
    run_result: dict[str, Any] | None = None,
    date: str | None = None,
) -> str:
    """生成 digest 并落盘 data/services/tech_debt/digest-<date>.md。返回相对路径。"""
    root = Path(project_root)
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md = build_digest_markdown(root, date=date, run_result=run_result)
    out_dir = root / _DIGEST_DIR_RELPATH
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"digest-{date}.md"
    out_path.write_text(md, encoding="utf-8")
    return str(out_path.relative_to(root)).replace("\\", "/")


__all__ = [
    "BATCH_SIZE",
    "DEFAULT_MAX_ENTRIES",
    "EXECUTABLE_PLAYBOOKS",
    "MAX_LLM_CALLS",
    "RULE_PLAYBOOK_BRIEF",
    "TRIAGE_MODEL",
    "TRIAGE_SCHEMA",
    "build_digest_markdown",
    "build_triage_prompt",
    "call_triage_llm",
    "collect_candidates",
    "load_triage_state",
    "run_autotriage",
    "suggest_rename_target",
    "write_digest",
]

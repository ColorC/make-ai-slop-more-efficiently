# [OMNI] origin=claude-code domain=services/_governance ts=2026-07-04T15:00:00Z type=router
# [OMNI] summary="项目索引自动维护 — 每日确定性体检(缺失/契约/死链自动修/quick_actions/新鲜度戳)+ 语义补漏候选区(性价比模型)。薄编排层, 全复用 doc_steward/projects_registry/actions-check 既有件。"
# [OMNI] why="批4:project_index.md 契约现成、断链修复引擎现成(doc_steward)、actions-check 现成; 缺一条把它们串成日常体检+补漏的编排。"
# [OMNI] tags=governance,project-index,cron
# [OMNI] material_id="material:governance.project_index_steward.py"
"""项目索引自动维护 — omni governance project-index-check / project-index-review。

  omni governance project-index-check [--apply] [--json]
      五项体检(顺序执行, 聚合成一份报告):
        1. 缺失检查   docs/projects/<x>/ 下必须有 PROJECT_INDEX.md
        2. 契约检查   parse_index_file 必填四键(不自动补 —— 语义内容不可编造)
        3. 死链体检+自动修  纳入 doc_steward 引用审计(project_index kind)
                       + frontmatter yaml 字段(roots/entry_points/quick_actions.where)
                       死路径体检(唯一匹配才修, 复用 _repo_index/_suffix_overlap)
        4. quick_actions 体检  复用 actions-check 核心逻辑
        5. 新鲜度戳   全绿(无 finding)且 --apply 时写 last_verified: <今日>

  omni governance project-index-review [PROJECT] [--json]
      语义补漏(gpt-5.5 性价比模型): 对每个项目找"新资产未进 index"/"权威已迁移"两类候选,
      写进 index 正文固定候选区(去重追加, 绝不碰 frontmatter/其余正文)。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

# ── 复用既有件, 不重写 ──────────────────────────────────────────────
from omnicompany.packages.services._governance.doc_steward.steward import (
    _repo_index,
    _suffix_overlap,
    apply_repairs,
    plan_repairs,
    run_reference_audit,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def report_dir() -> Path:
    d = omni_workspace_root() / "data" / "services" / "governance" / "project_index"
    d.mkdir(parents=True, exist_ok=True)
    return d


def projects_dir(root: Path | None = None) -> Path:
    return (root or omni_workspace_root()) / "docs" / "projects"


# ── findings 数据结构(五项体检共用) ─────────────────────────────────

@dataclass
class IndexFinding:
    project: str              # docs/projects/<x> 目录名(missing_index 时没有 index 文件可指)
    kind: str                 # missing_index | contract_violation | broken_ref | broken_field | quick_action
    detail: str
    index_path: str = ""      # 相对仓库根(缺失时为空)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"project": self.project, "kind": self.kind, "detail": self.detail,
               "index_path": self.index_path}
        if self.extra:
            out.update(self.extra)
        return out


# ── ① 缺失检查 ──────────────────────────────────────────────────────

def check_missing(root: Path | None = None) -> list[IndexFinding]:
    base = root or omni_workspace_root()
    pdir = projects_dir(base)
    out: list[IndexFinding] = []
    if not pdir.is_dir():
        return out
    for d in sorted(pdir.iterdir()):
        if not d.is_dir():
            continue
        idx = d / "PROJECT_INDEX.md"
        if not idx.is_file():
            out.append(IndexFinding(project=d.name, kind="missing_index",
                                    detail=f"docs/projects/{d.name}/ 下无 PROJECT_INDEX.md"))
    return out


# ── ② 契约检查(必填四键) ────────────────────────────────────────────

def check_contract(root: Path | None = None) -> list[IndexFinding]:
    from omnicompany.core.projects_registry import INDEX_REQUIRED_KEYS, parse_index_file
    base = root or omni_workspace_root()
    pdir = projects_dir(base)
    out: list[IndexFinding] = []
    if not pdir.is_dir():
        return out
    for d in sorted(pdir.iterdir()):
        if not d.is_dir():
            continue
        idx = d / "PROJECT_INDEX.md"
        if not idx.is_file():
            continue  # 缺失已由 check_missing 报
        rel = str(idx.relative_to(base)).replace("\\", "/")
        parsed = parse_index_file(idx)
        if parsed.get("ok"):
            continue
        missing = [k for k in INDEX_REQUIRED_KEYS if k not in (parsed.get("data") or {})]
        detail = parsed.get("error") or "frontmatter 不合法"
        out.append(IndexFinding(project=d.name, kind="contract_violation",
                                detail=detail, index_path=rel,
                                extra={"missing_keys": missing} if missing else {}))
    return out


# ── ③ 死链体检 + 自动修 ──────────────────────────────────────────────
# ③a: markdown 正文链接 —— 直接复用 doc_steward 的 run_reference_audit/plan_repairs/apply_repairs
#     (project_index kind 已在 doc_steward._TARGET_GLOBS 里注册, 见 steward.py)。
# ③b: frontmatter yaml 路径字段(roots[].path / entry_points[].path / quick_actions[].where) ——
#     这些不是 markdown `](...)` 链接, plan_repairs 的 token 替换不适用; 用同款
#     _repo_index/_suffix_overlap 匹配逻辑另写一层, 只对"在仓库根之下的相对/绝对路径"生效
#     (仓外绝对路径如 d:/P4/... 环境相关, 不归仓库体检管——与 doc_steward 对 d:/ C:\ 的处理一致)。

_YAML_PATH_FIELDS = (  # (frontmatter 顶层键, 是否列表, 取值用的字段名)
    ("roots", "path"),
    ("entry_points", "path"),
    ("quick_actions", "where"),
)


def _is_external_path(p: str) -> bool:
    """判定该路径字符串是否"环境相关/仓外", 不归仓库死链体检管(与 doc_steward 对绝对外部路径一致)。"""
    if not p:
        return True
    if re.match(r"^[a-zA-Z]:[\\/]", p) or p.startswith("\\\\"):
        return True  # 盘符绝对路径 / UNC —— 仓外环境路径, 体检其存在性但不参与仓内 basename 修复
    return False


def _resolve_field_path(p: str, base: Path) -> Path:
    """frontmatter 路径字段可能是仓内绝对路径(E:/WindowsWorkspace/omnicompany/...)或相对路径。"""
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return (base / p).resolve()


def check_yaml_field_paths(root: Path | None = None) -> tuple[list[IndexFinding], dict[str, Any]]:
    """体检 frontmatter 路径字段的存在性; 返回 (findings, repair_context)。

    repair_context 供 apply 阶段的 _repair_yaml_fields 消费(避免体检和修复各扫一遍)。
    """
    from omnicompany.core.projects_registry import parse_index_file
    base = root or omni_workspace_root()
    pdir = projects_dir(base)
    findings: list[IndexFinding] = []
    dead: list[dict[str, Any]] = []  # {index_path(abs Path), project, field_key, idx_in_list, value}
    if not pdir.is_dir():
        return findings, {"dead": dead}
    for d in sorted(pdir.iterdir()):
        if not d.is_dir():
            continue
        idx = d / "PROJECT_INDEX.md"
        if not idx.is_file():
            continue
        parsed = parse_index_file(idx)
        if not parsed.get("ok"):
            continue  # 契约违规已单独报, 避免重复
        fm = parsed["data"]
        rel = str(idx.relative_to(base)).replace("\\", "/")
        for list_key, val_key in _YAML_PATH_FIELDS:
            items = fm.get(list_key)
            if not isinstance(items, list):
                continue
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                val = it.get(val_key)
                if not val or not isinstance(val, str):
                    continue
                target = _resolve_field_path(val, idx.parent)
                if target.exists():
                    continue
                findings.append(IndexFinding(
                    project=d.name, kind="broken_field",
                    detail=f"{list_key}[{i}].{val_key} 指向不存在的路径: {val}",
                    index_path=rel, extra={"field": f"{list_key}[{i}].{val_key}", "value": val}))
                dead.append({"index_abs": idx, "project": d.name, "list_key": list_key,
                            "val_key": val_key, "i": i, "value": val})
    return findings, {"dead": dead}


def _plan_yaml_field_repairs(dead: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """为每条死 yaml 路径字段找仓内唯一 basename+后缀匹配候选(复用 doc_steward 的索引/打分函数)。"""
    index = _repo_index(root)
    fixes: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unfixable: list[dict[str, Any]] = []
    for d in dead:
        val = str(d["value"])
        if _is_external_path(val):
            unfixable.append({**d, "reason": "仓外绝对路径(环境相关), 不做仓内 basename 修复"})
            continue
        segs = [s for s in val.replace("\\", "/").split("/") if s not in ("", ".", "..")]
        if not segs:
            unfixable.append({**d, "reason": "空路径"})
            continue
        cands = index.get(segs[-1], [])
        if not cands:
            unfixable.append({**d, "reason": "仓内无此名(真删/真改名)"})
            continue
        scored = sorted(((_suffix_overlap(segs, rel.split("/")), rel, is_dir)
                         for rel, is_dir in cands), key=lambda t: (t[0], -len(t[1])), reverse=True)
        top = scored[0][0]
        best = [s for s in scored if s[0] == top]
        if len(best) != 1:
            ambiguous.append({**d, "candidates": [b[1] for b in best[:6]]})
            continue
        _, rel_target, is_dir = best[0]
        new_val = str(root / rel_target)
        if is_dir and not new_val.endswith(("/", "\\")):
            new_val += "/"
        if new_val != val:
            fixes.append({**d, "new_value": new_val})
    return {"fixes": fixes, "ambiguous": ambiguous, "unfixable": unfixable}


def _jsonsafe_yaml_plan(plan: dict[str, Any], base: Path) -> dict[str, Any]:
    """yaml 字段修复计划里的 index_abs 是 Path 对象(供 _apply_yaml_field_repairs 内部按文件分组用),
    进报告前换成仓库相对路径字符串, 否则 json.dumps 直接崩(TypeError: WindowsPath 不可序列化)。"""
    def _one(d: dict[str, Any]) -> dict[str, Any]:
        out = dict(d)
        idx_abs = out.pop("index_abs", None)
        if idx_abs is not None:
            try:
                out["index_path"] = str(Path(idx_abs).relative_to(base)).replace("\\", "/")
            except ValueError:
                out["index_path"] = str(idx_abs)
        return out
    return {k: [_one(x) for x in v] for k, v in plan.items()}


def _apply_yaml_field_repairs(plan: dict[str, Any]) -> dict[str, Any]:
    """把 yaml 字段修复写回 frontmatter —— 最小手术: 只替换该字段那一行的值, 不重序列化整份 yaml。"""
    by_file: dict[Path, list[dict[str, Any]]] = {}
    for fx in plan.get("fixes", []):
        by_file.setdefault(fx["index_abs"], []).append(fx)
    docs_changed = 0
    fields_changed = 0
    for idx_path, fixes in by_file.items():
        try:
            text = idx_path.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.split("\n")
        touched = False
        for fx in fixes:
            old_val = str(fx["value"])
            new_val = str(fx["new_value"])
            # 精确定位: 该字段那一行以 "<val_key>: <old_val>" 结尾(允许引号)。只替换首个匹配行
            # 且要求该行紧邻在其所属列表键之后区段内 —— 简化为: 全文里唯一一处
            # "<val_key>: <old_val>"(unquoted) 或 "<val_key>: \"<old_val>\"" 文本替换, 逐行扫描
            # 找到即换, 保序保注释安全(不碰其余行)。
            val_key = fx["val_key"]
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not (stripped.startswith(f"{val_key}:") or stripped.startswith(f"- {val_key}:")):
                    continue
                candidates = (f"{val_key}: {old_val}", f'{val_key}: "{old_val}"', f"{val_key}: '{old_val}'")
                if any(line.rstrip().endswith(c) or c in line for c in candidates):
                    if f'"{old_val}"' in line:
                        lines[i] = line.replace(f'"{old_val}"', f'"{new_val}"')
                    elif f"'{old_val}'" in line:
                        lines[i] = line.replace(f"'{old_val}'", f"'{new_val}'")
                    elif old_val in line:
                        lines[i] = line.replace(old_val, new_val)
                    else:
                        continue
                    touched = True
                    fields_changed += 1
                    break
        if touched:
            idx_path.write_text("\n".join(lines), encoding="utf-8")
            docs_changed += 1
    return {"docs_changed": docs_changed, "fields_changed": fields_changed}


# ── ④ quick_actions 体检(复用 actions-check 核心逻辑) ────────────────

def compute_actions_check() -> dict[str, Any]:
    """actions-check 的可复用核心(CLI cmd_actions_check 原样保留, 调这个函数)。"""
    from omnicompany.core.projects_registry import list_projects, parse_index_file

    skill_dirs = [
        Path.home() / ".claude" / "skills",
        omni_workspace_root() / ".claude" / "skills",
    ]
    known = {d.name for sd in skill_dirs if sd.is_dir() for d in sd.iterdir() if d.is_dir()}

    rows: list[dict[str, Any]] = []
    for p in list_projects():
        if not p.get("index_path"):
            continue
        parsed = parse_index_file(p["index_path"])
        for a in ((parsed.get("data") or {}).get("quick_actions") or []) if parsed.get("ok") else []:
            skill = a.get("skill")
            rows.append({
                "project": p["id"],
                "label": a.get("label"),
                "skill": skill,
                "skill_exists": (skill in known) if skill else None,
            })
    return {"known_skills": sorted(known), "actions": rows}


def check_quick_actions(root: Path | None = None) -> list[IndexFinding]:
    res = compute_actions_check()
    out: list[IndexFinding] = []
    for r in res["actions"]:
        if r["skill"] and not r["skill_exists"]:
            out.append(IndexFinding(project=r["project"], kind="quick_action",
                                    detail=f"quick_action '{r['label']}' 绑定不存在的 skill: {r['skill']}"))
    return out


# ── ⑤ 新鲜度戳 ──────────────────────────────────────────────────────

_LAST_VERIFIED_RE = re.compile(r"^(last_verified:).*$", re.MULTILINE)


def _stamp_last_verified(idx_path: Path) -> bool:
    """frontmatter 最小手术写/更新 last_verified: <今日>。只增/替换这一行, 不重序列化。"""
    try:
        text = idx_path.read_text(encoding="utf-8")
    except OSError:
        return False
    m = re.match(r"\A(---\s*\n)(.*?\n)(---\s*\n)", text, re.DOTALL)
    if not m:
        return False
    fm_body = m.group(2)
    stamp_line = f"last_verified: {_today()}"
    if _LAST_VERIFIED_RE.search(fm_body):
        new_fm_body = _LAST_VERIFIED_RE.sub(stamp_line, fm_body, count=1)
    else:
        new_fm_body = fm_body.rstrip("\n") + "\n" + stamp_line + "\n"
    if new_fm_body == fm_body:
        return False
    new_text = m.group(1) + new_fm_body + m.group(3) + text[m.end():]
    idx_path.write_text(new_text, encoding="utf-8")
    return True


# ── 顶层编排: 五项体检聚合成一份报告 ──────────────────────────────────

def run_check(*, apply: bool = False, root: Path | None = None, write: bool = True) -> dict[str, Any]:
    """五项体检顺序执行, 聚合成一份结构化报告; --apply 时死链自动修 + 新鲜度戳。"""
    base = root or omni_workspace_root()

    missing = check_missing(base)
    contract = check_contract(base)

    # ③a markdown 正文死链(project_index kind 已挂 doc_steward._TARGET_GLOBS)
    ref_audit = run_reference_audit(kinds=("project_index",), root=base, write=False)
    ref_findings = [IndexFinding(
        project=_project_of_doc(f["doc"], base), kind="broken_ref",
        detail=f"{f['category']}: {f['detail']}", index_path=f["doc"],
        extra={"target": f["target"]},
    ) for f in ref_audit["findings"]]
    ref_repair_plan = plan_repairs(root=base)
    # plan_repairs() 是全仓扫描(全部 kind), 这里只取落在 project_index 文档上的修复项,
    # 不误触 standard/plan/report 的死链(纪律: 只动本节 spec 允许的文件面)。
    idx_rel_paths = {rel for _k, p in _discover_project_index_paths(base)
                    for rel in [str(p.relative_to(base)).replace("\\", "/")]}
    ref_repair_plan = {
        "fixes": [f for f in ref_repair_plan["fixes"] if f["doc"] in idx_rel_paths],
        "ambiguous": [f for f in ref_repair_plan["ambiguous"] if f["doc"] in idx_rel_paths],
        "unfixable": [f for f in ref_repair_plan["unfixable"] if f["doc"] in idx_rel_paths],
    }

    # ③b yaml 字段死路径
    yaml_findings, yaml_ctx = check_yaml_field_paths(base)
    yaml_repair_plan = _plan_yaml_field_repairs(yaml_ctx["dead"], base)

    quick_action_findings = check_quick_actions(base)

    applied: dict[str, Any] = {}
    if apply:
        ref_repair_res = apply_repairs(ref_repair_plan, root=base) if ref_repair_plan["fixes"] else {
            "docs_changed": 0, "links_changed": 0}
        yaml_repair_res = _apply_yaml_field_repairs(yaml_repair_plan) if yaml_repair_plan["fixes"] else {
            "docs_changed": 0, "fields_changed": 0}
        applied = {"ref_repair": ref_repair_res, "yaml_repair": yaml_repair_res}
        # 重新体检死链(修复后应清零), 保证报告反映修后真实状态
        ref_audit = run_reference_audit(kinds=("project_index",), root=base, write=False)
        ref_findings = [IndexFinding(
            project=_project_of_doc(f["doc"], base), kind="broken_ref",
            detail=f"{f['category']}: {f['detail']}", index_path=f["doc"],
            extra={"target": f["target"]},
        ) for f in ref_audit["findings"] if f["doc"] in idx_rel_paths]
        yaml_findings, _ = check_yaml_field_paths(base)

    all_findings = missing + contract + ref_findings + yaml_findings + quick_action_findings

    stamped: list[str] = []
    if apply:
        findings_by_project: dict[str, list[IndexFinding]] = {}
        for f in all_findings:
            findings_by_project.setdefault(f.project, []).append(f)
        for d in sorted(projects_dir(base).iterdir()) if projects_dir(base).is_dir() else []:
            if not d.is_dir():
                continue
            idx = d / "PROJECT_INDEX.md"
            if not idx.is_file():
                continue  # 无 frontmatter 的文件跳过盖戳(已有 contract_violation finding)
            if findings_by_project.get(d.name):
                continue  # 有 finding 的不盖戳
            if _stamp_last_verified(idx):
                stamped.append(d.name)

    payload = {
        "kind": "project_index_check",
        "generated_at": _now_iso(),
        "applied": apply,
        "counts": {
            "missing_index": len(missing),
            "contract_violation": len(contract),
            "broken_ref": len(ref_findings),
            "broken_field": len(yaml_findings),
            "quick_action": len(quick_action_findings),
        },
        "findings": [f.to_dict() for f in all_findings],
        "repair_plan": {"ref": ref_repair_plan, "yaml_field": _jsonsafe_yaml_plan(yaml_repair_plan, base)},
        "stamped_fresh": stamped,
    }
    if apply:
        payload["repairs_applied"] = applied
    if write:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        out = report_dir() / f"check-{ts}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (report_dir() / "latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["_written"] = str(out)
    return payload


def _discover_project_index_paths(base: Path) -> list[tuple[str, Path]]:
    from omnicompany.packages.services._governance.doc_steward.steward import discover_targets
    return discover_targets(("project_index",), root=base)


def _project_of_doc(rel_doc: str, base: Path) -> str:
    """docs/projects/<x>/PROJECT_INDEX.md → <x>。"""
    parts = rel_doc.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "docs" and parts[1] == "projects":
        return parts[2]
    return rel_doc


# ── 语义补漏(gpt-5.5): candidates 固定候选区 ─────────────────────────

_CANDIDATE_BEGIN = "<!-- projidx-candidates:begin -->"
_CANDIDATE_END = "<!-- projidx-candidates:end -->"
_CANDIDATE_HEADING = "## 自动补漏候选(机器生成,并入正文或删除即可)"

REVIEW_NODE_PROMPT = """你是 omnicompany 仓库的项目索引补漏评审员(只读)。给你一个项目的
PROJECT_INDEX.md 内容 + 该项目 roots 目录近 30 天 git log 摘要。对照 index 现有内容与
近期变更, 只找两类:
① missing_asset —— 新出现的重要资产没进 index(新的重要目录/新工具/新权威文档)。
② moved_authority —— index 里指的权威已迁移/被取代(git log 里能看出旧路径已废弃)。
只列证据不打分, 每条必须引用具体路径或提交摘要。没有把握就不报(宁缺毋滥)。
whatnow/进度相关内容(完成度/百分比/下一步计划)不属于补漏范围, 不要报这类候选。
只用 finish 返回 JSON: {"candidates": [{"kind": "missing_asset"|"moved_authority",
"evidence": "...", "suggestion": "..."}]}"""

REVIEW_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "evidence", "suggestion"],
                "properties": {
                    "kind": {"type": "string", "enum": ["missing_asset", "moved_authority"]},
                    "evidence": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
            },
        },
    },
}


def _git_log_summary(paths: list[str], root: Path, days: int = 30, max_lines: int = 80) -> str:
    """项目 roots 目录近 N 天 git log 摘要(截 max_lines 行); git 不可用/超时优雅退化空串。"""
    import subprocess
    out_lines: list[str] = []
    no_window = 0x08000000 if os.name == "nt" else 0
    for p in paths:
        pp = Path(p)
        if not pp.exists():
            continue
        try:
            # encoding/errors 显式指定: git log 的 commit subject(%s) 常含中文标点,
            # Windows 默认区域(GBK)解不出这些字节会让读管线线程崩(r.stdout 变 None) ——
            # 2026-07-04 实测坐实(doc_steward._git_commit_ts 只取 %cI 时间戳侥幸没撞上,
            # 这里带 %s commit 消息文本必须显式 utf-8 + replace)。
            top = subprocess.run(["git", "-C", str(pp), "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True, timeout=5, creationflags=no_window,
                                 encoding="utf-8", errors="replace")
            if top.returncode != 0:
                continue
            r = subprocess.run(
                ["git", "-C", top.stdout.strip(), "log", f"--since={days} days ago",
                 "--format=%ad %s", "--date=short", "--", str(pp)],
                capture_output=True, text=True, timeout=8, creationflags=no_window,
                encoding="utf-8", errors="replace")
            if r.returncode == 0 and r.stdout:
                out_lines.extend(ln for ln in r.stdout.splitlines() if ln.strip())
        except Exception:  # noqa: BLE001 — git 不可达/超时不该挡住语义补漏, 退化成空摘要
            continue
        if len(out_lines) >= max_lines:
            break
    return "\n".join(out_lines[:max_lines])


def _existing_candidate_lines(text: str) -> list[str]:
    """区块存在时, 返回区块内(含历史)已出现过的候选行原文(去重对比用)。"""
    m = re.search(re.escape(_CANDIDATE_BEGIN) + r"(.*?)" + re.escape(_CANDIDATE_END), text, re.DOTALL)
    if not m:
        return []
    return [ln.rstrip() for ln in m.group(1).split("\n") if ln.strip().startswith("- [")]


# 路径/文件名 token(basename): 反引号包裹的代码/路径片段, 或裸的 a/b/c、a.py、
# E:/x/y 这类(可选 Windows 盘符前缀 —— 2026-07-04 实测坐实: gpt-5.5 常直接写
# 不带反引号的绝对路径如 "E:/WindowsWorkspace/omnicompany/...", 漏收盘符前缀会让
# 正则从冒号处断开, 整条路径 token 抽取失败, 去重形同虚设)。
# 这是最强去重信号——LLM 每轮复述同一条发现措辞会大幅改写(整句都不同, 纯文本/整句
# Jaccard 命中率极低), 但引用的具体路径/文件名/commit 摘要(来自同一份只读 git log
# 输入)在复述间几乎逐字保留。
_PATH_TOKEN_RE = re.compile(
    r"`([^`]+)`"
    r"|(?<![\w/\\])((?:[A-Za-z]:[/\\])?[\w.\-]+(?:[/\\][\w.\-]+)+[/\\]?)(?![\w/\\])")


def _basenames_of(text: str) -> frozenset[str]:
    out: set[str] = set()
    for m in _PATH_TOKEN_RE.finditer(text):
        raw = m.group(1) or m.group(2) or ""
        raw = raw.strip().rstrip("/\\.,;:，。；：")
        if not raw:
            continue
        parts = [p for p in re.split(r"[/\\]", raw) if p]
        if parts:
            out.add(parts[-1].lower())
        # 反引号里可能不是路径而是一句话(如 "feat(x): ..." commit 摘要), 也整体存一份,
        # 短句复述时仍会原样引用同一条 commit subject。
        if raw and (" " not in raw or ":" in raw):
            out.add(raw.lower())
    return out


def _line_fingerprint(line: str) -> frozenset[str]:
    """候选行(suggestion + 证据)→ basename/路径/commit摘要 token 集合, 供近似去重。"""
    return _basenames_of(line)


# "强" token 判据: 长度达标(短 basename 如 plan.md/exploration 太通用, 两条毫不相关的
# 建议也常共享同一个短文件名; 但完整路径 / 完整 commit subject 这类长字符串在同一份
# git log 输入下, 不同措辞复述间几乎逐字重现, 是可靠的"同一条发现"信号)。
# 路径类(含 / 或 \)本身就唯一指向同一份资产, 共享 1 个即够; 无斜杠的长散文片段
# (如 commit subject)单条共享偶尔巧合, 要求至少共享 2 条才判同一发现。
_STRONG_TOKEN_MIN_LEN = 12


def _is_duplicate_line(new_line: str, existing_fps: list[frozenset[str]],
                       min_prose_shared: int = 2) -> bool:
    """新候选行是否与某条已有候选行判为同一发现的复述, 不重复追加(错误样本②)。

    2026-07-04 实测坐实: 同一发现两次措辞复述, 整句/全量 token Jaccard 比例仅 ~0.13
    (被"index 现有内容"这类大段共同背景文字稀释), 但双方引用的具体长路径 / commit
    摘要几乎逐字重现——故不用比例, 直接看"强 token"原样重现: 路径类(含 / 或 \\)共享
    1 个即判同一发现; 无斜杠的长散文片段(commit subject 等)要求共享 >= min_prose_shared 条。
    """
    new_fp = _line_fingerprint(new_line)
    new_strong = {t for t in new_fp if len(t) >= _STRONG_TOKEN_MIN_LEN}
    if not new_strong:
        return False  # 抽不出强信号, 保守起见不去重(宁可多留, 不误删)
    new_path = {t for t in new_strong if "/" in t or "\\" in t}
    new_prose = new_strong - new_path
    for fp in existing_fps:
        strong = {t for t in fp if len(t) >= _STRONG_TOKEN_MIN_LEN}
        path = {t for t in strong if "/" in t or "\\" in t}
        prose = strong - path
        if new_path & path:
            return True
        if len(new_prose & prose) >= min_prose_shared:
            return True
    return False


def _upsert_candidate_block(text: str, new_lines: list[str]) -> tuple[str, list[str]]:
    """把去重后的新候选行插入固定候选区(不存在则文末创建); 返回 (新正文, 实际新增的行)。

    去重判据: 新候选行与区块内(含历史, 已勾选/删除的行不会再出现在当前正文里, 这是
    天然的"不复活"边界)任一已有候选行共享的路径/文件名/commit摘要 token 达阈值,
    即视为同一条发现的复述, 不重复追加(错误样本②: 同一条每轮重复堆积=判失败)。
    """
    existing_lines = _existing_candidate_lines(text)
    existing_fps = [_line_fingerprint(ln) for ln in existing_lines]
    added: list[str] = []
    for ln in new_lines:
        if _is_duplicate_line(ln, existing_fps):
            continue
        added.append(ln)
        existing_fps.append(_line_fingerprint(ln))

    m = re.search(re.escape(_CANDIDATE_BEGIN) + r"(.*?)" + re.escape(_CANDIDATE_END), text, re.DOTALL)
    if m:
        if not added:
            return text, []
        inner = m.group(1)
        new_inner = inner.rstrip("\n") + "\n" + "\n".join(added) + "\n"
        new_text = text[:m.start(1)] + new_inner + text[m.end(1):]
        return new_text, added
    if not added:
        return text, []
    block = "\n\n" + _CANDIDATE_HEADING + "\n" + _CANDIDATE_BEGIN + "\n" + "\n".join(added) + "\n" + _CANDIDATE_END + "\n"
    return text.rstrip("\n") + block, added


def run_review(project_id: str | None = None, *, model: str = "gpt-5.5",
               root: Path | None = None, write: bool = True) -> dict[str, Any]:
    """语义补漏: 对每个(或指定)项目跑一次 run_json_agent, 候选写进 index 固定候选区。"""
    import asyncio
    from omnicompany.core.projects_registry import list_projects
    from omnicompany.packages.services._core.agent.launch import run_json_agent

    base = root or omni_workspace_root()
    projects = [p for p in list_projects() if p.get("index_path")]
    if project_id:
        projects = [p for p in projects if p["id"] == project_id]
        if not projects:
            return {"kind": "project_index_review", "generated_at": _now_iso(),
                    "results": [], "error": f"未知项目或无 index_path: {project_id}"}

    results: list[dict[str, Any]] = []
    for p in projects:
        idx_path = Path(p["index_path"])
        if not idx_path.is_file():
            results.append({"project": p["id"], "ok": False, "error": "index 文件不存在"})
            continue
        roots = [r.get("path") if isinstance(r, dict) else r for r in (p.get("roots") or [])]
        roots = [r for r in roots if r]
        log_summary = _git_log_summary(roots, base) if roots else ""
        text = idx_path.read_text(encoding="utf-8")
        task = (f"项目 index 绝对路径: {idx_path}\n\n"
                f"index 当前内容:\n{text[:6000]}\n\n"
                f"该项目 roots 目录近 30 天 git log 摘要(可能为空):\n{log_summary or '(无 git 记录或不可达)'}")
        agent_result = asyncio.run(run_json_agent(
            task=task, node_prompt=REVIEW_NODE_PROMPT, model=model,
            result_schema=REVIEW_RESULT_SCHEMA, project_root=str(base),
            caller="projidx.review",
        ))
        if not agent_result["ok"]:
            results.append({"project": p["id"], "ok": False,
                            "error": agent_result.get("error") or "run_json_agent 失败"})
            continue
        candidates = (agent_result["final"] or {}).get("candidates", [])
        new_lines = [
            f"- [ ] ({_today()}) {c.get('suggestion', '').strip()} — 证据: {c.get('evidence', '').strip()}"
            for c in candidates if c.get("suggestion")
        ]
        new_text, added = _upsert_candidate_block(text, new_lines)
        if write and new_text != text:
            idx_path.write_text(new_text, encoding="utf-8")
        results.append({
            "project": p["id"], "ok": True, "candidates_found": len(candidates),
            "candidates_added": len(added), "added_lines": added,
        })
    return {"kind": "project_index_review", "generated_at": _now_iso(), "results": results}


__all__ = [
    "IndexFinding",
    "report_dir", "projects_dir",
    "check_missing", "check_contract", "check_yaml_field_paths", "check_quick_actions",
    "compute_actions_check", "run_check", "run_review",
]

# [OMNI] origin=claude-code domain=services/_governance ts=2026-07-04T00:00:00+08:00 type=module status=active agent=claude
# [OMNI] summary="计划完成硬闸判定层(只读): 计划转完成态前查其绑定软件的 testmap 台账自开工(registered_at)以来有无变更, 没有则拒转。只做判定, 不写任何数据, 供 CLI(plan_gate.py)与 whatnow patch_task 的 run_cli 胶水消费。"
# [OMNI] why="批3 §A: 完成流转唯一物理收口在 whatnow patch_task, 判定逻辑留 Python(复用 plan_bindings + testmap 发现), Rust 只加薄胶水调 CLI。执行方自认完成不算数, 用可机械核验的台账变更时间做硬闸。"
# [OMNI] tags=governance,gate,whatnow,plan-bindings,testmap
"""计划完成硬闸 · 判定函数。

evaluate(plan_id) -> {allow, reason, details}:
    - 无 binding → allow(未纳管计划不闸, 不适用)。
    - binding 无 targets(非功能性/纯文档计划) → allow(错误样本①: 必须放行)。
    - 功能性但未声明 testmaps → allow, reason 建议补声明(默认向前)。
    - 声明的 app 在 discover_testmaps 里找不到 → refuse。
    - 声明的 app 存在: testmap.yaml 本身或任一登记测试锚, 自 registered_at 之后有变更
      (git 最后提交时间或工作区 mtime, 取 max; git 失败容错用 mtime) → 该 app 视为"动过";
      任一 app 动过即 allow; 全部未动 → refuse。

只读: 不修改 plan_bindings.json、不修改 testmap.yaml、不落任何报告文件。
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.registry.plan_bindings import get_binding
from omnicompany.packages.services._governance import testmap as testmap_lib

# Windows 隐藏子进程窗口(禁止前台跳控制台窗口铁律)。非 Windows 取 0。
_BG_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

GitTimeFn = Callable[[Path], Optional[float]]


def _parse_iso(ts: str) -> Optional[float]:
    if not ts:
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _git_last_commit_ts(path: Path, *, cwd: Optional[Path] = None) -> Optional[float]:
    """该路径最后一次提交的时间(epoch 秒)。git 不可用/路径不在仓内/无历史 → None(容错交调用方)。"""
    if not path.exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(path)],
            cwd=str(cwd or path.parent),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=_BG_FLAGS,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 or not out:
            return None
        return float(out)
    except Exception:  # noqa: BLE001 — git 只是辅助信号, 失败容错用 mtime
        return None


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _latest_change_ts(path: Path, git_time_fn: GitTimeFn) -> Optional[float]:
    """git 最后提交时间与工作区 mtime 取 max(git 失败容错用 mtime)。两者都拿不到 → None。"""
    git_ts = git_time_fn(path)
    mt = _mtime(path)
    candidates = [t for t in (git_ts, mt) if t is not None]
    return max(candidates) if candidates else None


def _app_changed_since(
    tm: "testmap_lib.Testmap", registered_ts: float, git_time_fn: GitTimeFn,
) -> tuple[bool, list[str]]:
    """该 testmap 自 registered_ts 以来是否"动过": testmap.yaml 本身或任一登记测试锚变更。

    返回 (changed, evidence) —— evidence 是人可读的变更点摘要, 用于 reason 文案。
    """
    evidence: list[str] = []
    changed = False

    yaml_ts = _latest_change_ts(tm.path, git_time_fn)
    if yaml_ts is not None and yaml_ts > registered_ts:
        changed = True
        evidence.append(f"testmap.yaml 本身({tm.path})")

    seen_anchors: set[str] = set()
    for feat in tm.features:
        for anchor in feat.tests:
            if anchor.file in seen_anchors:
                continue
            seen_anchors.add(anchor.file)
            anchor_path = (tm.base_dir / anchor.file).resolve()
            anchor_ts = _latest_change_ts(anchor_path, git_time_fn)
            if anchor_ts is not None and anchor_ts > registered_ts:
                changed = True
                evidence.append(f"测试锚 {anchor.file}")

    return changed, evidence


def evaluate(
    plan_id: str,
    *,
    workspace_root: Optional[Path] = None,
    binding: Optional[dict[str, Any]] = None,
    discover_fn: Optional[Callable[[Path], "testmap_lib.DiscoverResult"]] = None,
    git_time_fn: Optional[GitTimeFn] = None,
) -> dict[str, Any]:
    """判定 plan_id 是否允许转完成态。返回 {allow: bool, reason: str, details: dict}。

    注入点(测试用, 均可省略走真实实现):
        workspace_root — 默认 omni_workspace_root()。
        binding — 默认 get_binding(plan_id) 真读注册表。
        discover_fn — 默认 testmap_lib.discover_testmaps。
        git_time_fn — 默认 _git_last_commit_ts(真跑 git log, 隐藏窗口)。
    """
    root = Path(workspace_root) if workspace_root is not None else Path(omni_workspace_root())
    discover = discover_fn or testmap_lib.discover_testmaps
    git_fn = git_time_fn or _git_last_commit_ts

    rec = binding if binding is not None else get_binding(plan_id)

    if rec is None:
        return {
            "allow": True,
            "reason": "未纳管计划(绑定注册表无登记), 完成硬闸不适用。",
            "details": {"plan_id": plan_id, "binding_found": False},
        }

    declared_testmaps: list[str] = list(rec.get("testmaps") or [])
    # "无 targets" 视为非功能性/纯文档计划(错误样本①: 必须放行)。targets 是 plan.md
    # frontmatter binding 块的字段, 未经结构化解析路径落进 plan_bindings.json 前不可查——
    # 因此以 write_targets 为代理信号: write_targets 与 testmaps 均为空 → 视为非功能性计划。
    # (write_targets 是绑定注册表里"这计划会写哪些文件/面"的既有字段, 与 frontmatter targets
    # 同源但已结构化落盘, 是判定"功能性 vs 纯文档"的可查信号。)
    has_write_targets = bool(rec.get("write_targets"))

    if not declared_testmaps:
        if not has_write_targets:
            return {
                "allow": True,
                "reason": "计划无 write_targets 登记(视为非功能性/纯文档计划), 完成硬闸不适用。",
                "details": {"plan_id": plan_id, "binding_found": True, "testmaps": []},
            }
        return {
            "allow": True,
            "reason": (
                "binding 未声明 testmaps, 闸不适用; 建议按四件登记补声明"
                "(omni governance bind set <plan_id> --testmap <app>)。"
            ),
            "details": {"plan_id": plan_id, "binding_found": True, "testmaps": []},
        }

    registered_at = rec.get("registered_at") or ""
    registered_ts = _parse_iso(registered_at)
    if registered_ts is None:
        return {
            "allow": True,
            "reason": f"registered_at 无法解析({registered_at!r}), 闸不阻断(数据异常按放行处理)。",
            "details": {"plan_id": plan_id, "binding_found": True, "testmaps": declared_testmaps},
        }

    result = discover(root)
    by_app = {tm.app: tm for tm in result.testmaps}

    missing_apps = [a for a in declared_testmaps if a not in by_app]
    if missing_apps:
        return {
            "allow": False,
            "reason": (
                f"计划声明动了 {missing_apps} 的台账, 但当前发现不到这些 app 的 testmap"
                "(声明的台账不存在)——完成判定不通过, 先确认 testmap.yaml 是否已登记/可发现。"
            ),
            "details": {
                "plan_id": plan_id, "binding_found": True,
                "testmaps": declared_testmaps, "missing_apps": missing_apps,
            },
        }

    per_app_changed: dict[str, bool] = {}
    per_app_evidence: dict[str, list[str]] = {}
    for app in declared_testmaps:
        tm = by_app[app]
        changed, evidence = _app_changed_since(tm, registered_ts, git_fn)
        per_app_changed[app] = changed
        per_app_evidence[app] = evidence

    any_changed = any(per_app_changed.values())
    details = {
        "plan_id": plan_id, "binding_found": True,
        "testmaps": declared_testmaps, "registered_at": registered_at,
        "per_app_changed": per_app_changed, "per_app_evidence": per_app_evidence,
    }

    if any_changed:
        changed_apps = [a for a, c in per_app_changed.items() if c]
        return {
            "allow": True,
            "reason": f"台账已在 registered_at({registered_at}) 之后变更: {changed_apps}。",
            "details": details,
        }

    return {
        "allow": False,
        "reason": (
            f"计划声明动了 {declared_testmaps} 的台账, 但自 {registered_at} 纳管以来 "
            "testmap 与其测试锚均无变更——完成判定不通过, 先补台账登记。"
        ),
        "details": details,
    }


__all__ = ["evaluate"]

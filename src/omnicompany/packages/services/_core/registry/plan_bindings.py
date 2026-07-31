# [OMNI] origin=claude-code domain=services/_core/registry ts=2026-07-03T00:00:00Z type=module status=active agent=claude-code
# [OMNI] summary="绑定注册表 v1: 计划(plan_id)↔whatnow任务↔测试文件↔评审记录的结构化登记, 单 JSON 按 plan_id 为键。set/get/list/write_skeleton 四个纯函数, 路径写死不接受调用方传参(与 ledger/store.py 同范式)。binding_status() 是完整性判定单一来源, dashboard(plans.py)与 guardian 扫描共用同一口径(件级 worst-of)。"
# [OMNI] why="overnight-run.md 第六节验收锚: 四件登记从计划里的锚点段升级为结构化登记, 巡检可查缺锚/登记不完整/悬空。"
# [OMNI] tags=registry,plan-bindings,acceptance-anchor,semantic-os
# [OMNI] material_id="material:core.registry.plan_bindings.py"
"""绑定注册表 v1 —— 计划-进度-测试-评审四件登记的结构化落点。

权威: docs/plans/[2026-07-02]SEMANTIC-OS-MAP/overnight-run.md 第六节
"绑定注册表化的验收锚"。

设计要点:
    - 落盘固定为仓根 data/registry/plan_bindings.json, 单文件按 plan_id 为键,
      历史沿革走 git, 不额外滚动/归档。不接受调用方传入自定义路径(与
      ledger/store.py 同一铁律: "记录工具本身不接受路径参数")。测试通过
      monkeypatch 模块级 PLAN_BINDINGS_PATH 常量隔离, 不通过构造参数。
    - whatnow_task 提供时经注入的 resolver(默认统一引用解析器)verify;
      解析失败不阻断写入, 但显式标 _whatnow_verified=False + _whatnow_verify_note,
      悬空可查不是阻断式硬失败。
    - review.mode == "exempt" 时 review.reason 必填, 否则拒绝(ValueError)——
      这是唯一会阻断写入的校验, 其余一律"如实登记, 不阻断"。
    - registered_at 首次写入生成, 不随后续 set_binding 调用改变; updated_at
      每次写入刷新。
    - write_skeleton() 供 plans-sync 新计划纳管时调用: 只写 plan_id/whatnow_task/
      registered_at 的最小骨架, 幂等——若该 plan_id 已有记录不覆盖已有字段。
    - testmaps(可选键, 2026-07-04 完成硬闸批新增): 计划声明自己动了哪些软件的
      testmap 台账(app 字符串列表, 对应 testmap.yaml 的 app 字段), 来源是 plan.md
      frontmatter binding 块; 未声明不受影响(闸判定按"未声明"处理, 见
      plan_completion_gate.py)。
    - binding_status() 是"绑定完整性判定"的单一来源: dashboard controlplane/plans.py
      的 _binding_status 与 guardian/rules/plan_bindings_guardian.py 的扫描判定都必须
      调这份逻辑, 不得各自重写一套(2026-07-03 复验打回: 旧版 plans.py 只看顶层
      error_samples 非空就判 complete, 对有 items[] 件级缺失的记录会误判)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ── 路径(单点真源, 与 config/ledgers.yaml plan-bindings 条登记的位置一致) ──────
# plan_bindings.py → registry[0]/_core[1]/services[2]/packages[3]/omnicompany[4]/src[5]/仓根[6]
_OMNI_ROOT = Path(__file__).resolve().parents[6]
PLAN_BINDINGS_PATH = _OMNI_ROOT / "data" / "registry" / "plan_bindings.json"

_VALID_REVIEW_MODES = {"tests", "panel", "exempt"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_all() -> dict[str, dict[str, Any]]:
    if not PLAN_BINDINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(PLAN_BINDINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, dict[str, Any]]) -> None:
    PLAN_BINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_BINDINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_review(review: Optional[dict[str, Any]]) -> None:
    if not review:
        return
    mode = review.get("mode")
    if mode is not None and mode not in _VALID_REVIEW_MODES:
        raise ValueError(f"review.mode 非法: {mode!r}, 允许值: {sorted(_VALID_REVIEW_MODES)}")
    if mode == "exempt" and not (review.get("reason") or "").strip():
        raise ValueError("review.mode=exempt 时 review.reason 必填")


def _default_resolver(task_id: str):
    """默认注入点: 统一引用解析器, 走真实 verify(不真连也会显式报服务未运行)。"""
    from .resolver import resolve_reference

    return resolve_reference(task_id, verify=True)


def _verify_whatnow(whatnow_task: Optional[str], resolver: Optional[Callable[[str], Any]]) -> tuple[Optional[bool], str]:
    if not whatnow_task:
        return None, ""
    fn = resolver or _default_resolver
    try:
        result = fn(whatnow_task)
    except Exception as e:  # noqa: BLE001 — resolver 炸了视为验证失败, 不阻断写入
        return False, f"resolver 调用异常(视为悬空): {e}"
    verified = getattr(result, "verified", None)
    note = getattr(result, "verify_note", "") or ""
    return bool(verified), note


def set_binding(
    plan_id: str,
    *,
    whatnow_task: Optional[str] = None,
    tests: Optional[list[dict[str, Any]]] = None,
    error_samples: Optional[list[dict[str, Any]]] = None,
    write_targets: Optional[list[str]] = None,
    review: Optional[dict[str, Any]] = None,
    items: Optional[list[dict[str, Any]]] = None,
    testmaps: Optional[list[str]] = None,
    resolver: Optional[Callable[[str], Any]] = None,
) -> dict[str, Any]:
    """写入(新建或更新)一条绑定记录, 返回写入后的完整记录。

    review.mode == "exempt" 且缺 reason 时抛 ValueError(唯一阻断式校验)。
    whatnow_task 提供时经 resolver verify, 失败不阻断, 但标 _whatnow_verified=False。
    testmaps 是计划完成硬闸(plan_completion_gate.py)读取的可选字段: 计划声明自己
    动了哪些软件的 testmap 台账(app 字符串列表)。
    """
    _validate_review(review)

    all_bindings = _read_all()
    existing = all_bindings.get(plan_id)
    registered_at = (existing or {}).get("registered_at") or _now_iso()

    record: dict[str, Any] = {
        "plan_id": plan_id,
        "whatnow_task": whatnow_task,
        "tests": tests or [],
        "error_samples": error_samples or [],
        "write_targets": write_targets or [],
        "review": review or {},
        "items": items or [],
        "testmaps": testmaps if testmaps is not None else (existing or {}).get("testmaps") or [],
        "registered_at": registered_at,
        "updated_at": _now_iso(),
    }

    if whatnow_task:
        verified, note = _verify_whatnow(whatnow_task, resolver)
        record["_whatnow_verified"] = verified
        record["_whatnow_verify_note"] = note

    all_bindings[plan_id] = record
    _write_all(all_bindings)
    return record


def get_binding(plan_id: str) -> Optional[dict[str, Any]]:
    return _read_all().get(plan_id)


def list_bindings() -> dict[str, dict[str, Any]]:
    return _read_all()


def _is_exempt_block(review: Optional[dict[str, Any]]) -> bool:
    # 存量注册表曾出现过 review="tests" 这类旧形态。巡检/看板必须把它
    # 当作“未豁免的脏数据”继续报告，而不能因一次 .get() 让全局扫描崩溃。
    review = review if isinstance(review, dict) else {}
    return review.get("mode") == "exempt" and bool((review.get("reason") or "").strip())


def binding_status(binding: Optional[dict[str, Any]]) -> str:
    """绑定完整性判定单一来源: 'complete'|'incomplete'|'exempt'。

    与 guardian/rules/plan_bindings_guardian.py::scan_plan_binding_violations 的
    件级 worst-of 判定语义一致(打回硬化㈡口径), 两边必须调同一份逻辑, 不得各写一套:
        - 整体豁免(顶层 review.mode == "exempt" 且 reason 非空) → "exempt"。
        - 有 items 时: 以件级最差者为准。任一 item 的 error_samples 为空且该 item
          未豁免(item.review.mode == "exempt" 且 reason 非空)→ "incomplete";
          若所有 item 要么豁免要么 error_samples 非空 → "complete"。
        - 无 items 时: 退回顶层 error_samples 是否非空(非空 → "complete",
          否则 "incomplete")。

    binding is None(未登记/缺锚)不在本函数职责内, 调用方(plans.py._binding_status)
    自行先判 missing。
    """
    if binding is None:
        return "incomplete"
    review = binding.get("review") or {}
    if _is_exempt_block(review):
        return "exempt"

    items = binding.get("items") or []
    if items:
        for item in items:
            if not isinstance(item, dict):
                continue
            if _is_exempt_block(item.get("review")):
                continue
            if not item.get("error_samples"):
                return "incomplete"
        return "complete"

    return "complete" if binding.get("error_samples") else "incomplete"


def write_skeleton(plan_id: str, *, whatnow_task: Optional[str] = None) -> dict[str, Any]:
    """写最小骨架登记(供 plans-sync 新计划纳管调用)。幂等: 已有记录不覆盖。"""
    all_bindings = _read_all()
    existing = all_bindings.get(plan_id)
    if existing is not None:
        return existing

    record: dict[str, Any] = {
        "plan_id": plan_id,
        "whatnow_task": whatnow_task,
        "tests": [],
        "error_samples": [],
        "write_targets": [],
        "review": {},
        "items": [],
        "testmaps": [],
        "registered_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    all_bindings[plan_id] = record
    _write_all(all_bindings)
    return record


__all__ = [
    "PLAN_BINDINGS_PATH",
    "set_binding",
    "get_binding",
    "list_bindings",
    "write_skeleton",
    "binding_status",
]

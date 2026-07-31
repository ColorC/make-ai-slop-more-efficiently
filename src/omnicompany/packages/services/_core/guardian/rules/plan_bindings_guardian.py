# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-07-03T00:00:00Z type=config
# [OMNI] summary="Guardian 规则 OMNI-099 · 绑定注册表巡检: 非归档新计划(目录名日期>=存量截止线2026-07-03)超3天无登记=缺锚, 已纳管但骨架未补全(顶层+件级 worst-of 判定)=登记不完整, 测试文件不存在/whatnow回指失败=悬空(顶层与 items[] 件级同查, 违规明细带件名), 显式豁免+理由不点名。"
# [OMNI] why="overnight-run.md 第六节验收锚 + 首轮验收打回硬化㈡: 巡检要能点名'缺锚/登记不完整/悬空', 且必须递归 items[] 件级子锚(顶层占位不得掩盖件级缺失); ㊃存量截止线定义=目录名日期>=2026-07-03才受缺锚点名, 存量计划(无日期或早于截止)跳过。"
# [OMNI] tags=guardian,plan-bindings,acceptance-anchor,OMNI-099
# [OMNI] material_id="material:core.guardian.rules.plan_bindings_guardian.py"
"""Guardian 规则 · OMNI-099 · 绑定注册表巡检。

本家族与 runtime_hygiene 同款: **不走标准 per-file FileContext**, 走目录级扫描。
真实扫描由 patrol/worker 直接调 scan_plan_binding_violations, RULES 里的
check 用 _noop_check 占位; 真实执行路径挂在
guardian/workers/plan_bindings_scan_worker.py::PlanBindingsScanWorker
(仿 HygieneScanWorker 样板, 由 CLI `omni guardian plan-bindings scan` 与
定时任务 guard-plan-bindings-daily 触发, 打回硬化㈠)。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ._base import FileContext, GuardianRule

_GRACE_PERIOD_DAYS = 3.0
_PLAN_DIR_SKIP_NAMES = {"_archive", "_scratch"}

# 存量截止线(打回硬化前已在原始验收锚㊃里定案, 2026-07-03 首轮验收打回后再次实证要求真正实现):
# 只有目录名含日期且日期 >= 本线的"新"计划才受 missing_anchor 点名; 无日期或早于此线的
# 存量计划一律跳过(补登记随第四批安排, 见 overnight-run.md 第六节㊃)。
_MISSING_ANCHOR_CUTOFF = datetime(2026, 7, 3, tzinfo=timezone.utc).timestamp()
_DATE_IN_NAME_RE = re.compile(r"\[(\d{4})-(\d{2})-(\d{2})\]")


def _extract_dir_date_ts(rel_dir: str) -> Optional[float]:
    """从计划目录相对路径任意一段里提取形如 [YYYY-MM-DD] 的日期, 转 epoch 秒(UTC 零点)。

    取路径中第一个匹配段(计划目录惯例把日期段放在自己那层, 如
    'guardian/[2026-06-26]SOME-PLAN'); 无日期段返回 None(=存量口径, 跳过点名)。
    """
    m = _DATE_IN_NAME_RE.search(rel_dir)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _discover_plan_dirs(project_root: Path) -> list[tuple[str, Path]]:
    """遍历 docs/plans 下所有含 plan.md 的目录(排除 _archive/_scratch), 口径同 sync.py _discover_plans。"""
    plans_root = project_root / "docs" / "plans"
    out: list[tuple[str, Path]] = []
    if not plans_root.is_dir():
        return out
    for dp in plans_root.rglob("plan.md"):
        rel_dir = dp.parent.relative_to(plans_root).as_posix()
        parts = rel_dir.split("/")
        if any(p in _PLAN_DIR_SKIP_NAMES for p in parts):
            continue
        out.append((rel_dir, dp.parent))
    return out


def _is_exempt(binding: Optional[dict[str, Any]]) -> bool:
    if not binding:
        return False
    review = binding.get("review") or {}
    return review.get("mode") == "exempt" and bool((review.get("reason") or "").strip())


def _has_error_samples(binding: dict[str, Any]) -> bool:
    return bool(binding.get("error_samples"))


def _check_tests_dangling(
    project_root: Path, plan_id: str, tests: list[Any], *, item_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    """检查 tests[].file 是否存在, 违规明细按需带 item 名(打回硬化㈡)。"""
    out: list[dict[str, Any]] = []
    for t in tests or []:
        file_rel = t.get("file") if isinstance(t, dict) else None
        if not file_rel:
            continue
        if not (project_root / file_rel).is_file():
            prefix = f"[件 {item_name}] " if item_name else ""
            out.append({
                "plan_id": plan_id,
                "category": "dangling_test_file",
                "detail": f"{prefix}登记的测试文件不存在: {file_rel}",
            })
    return out


def scan_plan_binding_violations(
    project_root: Path,
    *,
    bindings: Optional[dict[str, dict[str, Any]]] = None,
    now_ts: Optional[float] = None,
) -> list[dict[str, Any]]:
    """扫描绑定注册表巡检违规, 返回 [{plan_id, category, detail}, ...]。

    category ∈ {missing_anchor, incomplete, dangling_test_file, dangling_whatnow, empty_error_samples}。
    exempt(review.mode=="exempt" 且 reason 非空) → 该 plan_id 完全不出现在返回列表中。

    打回硬化㈡(2026-07-03): 有 items[] 件级子锚时递归检查每件的
    error_samples/tests[].file/whatnow 引用, 违规明细带件名; 完整性口径=
    有件级时以件级最差者为准, 顶层占位(如顶层 error_samples 非空但只是泛泛占位)
    不得掩盖件级缺失——因此当 items 非空时, "登记不完整"改由件级判定驱动,
    不再单看顶层 error_samples 是否非空就判定 complete。

    打回硬化㈢(存量截止线): missing_anchor 只对目录名含日期且日期 >= 2026-07-03
    的计划生效; 无日期或早于截止线的存量计划跳过(不点名, 补登记随第四批安排)。
    """
    now = now_ts if now_ts is not None else time.time()
    if bindings is None:
        from ...registry.plan_bindings import list_bindings

        bindings = list_bindings()

    violations: list[dict[str, Any]] = []
    plan_dirs = _discover_plan_dirs(project_root)

    # ── ㊃ 非归档新计划超 3 天无登记 → 缺锚(存量截止线: 目录名日期 >= 2026-07-03 才生效) ──
    for plan_id, plan_dir in plan_dirs:
        binding = bindings.get(plan_id)
        if binding is not None:
            continue  # 已有登记(哪怕只是骨架), 走下面 incomplete 判定, 不算缺锚
        dir_date_ts = _extract_dir_date_ts(plan_id)
        if dir_date_ts is None or dir_date_ts < _MISSING_ANCHOR_CUTOFF:
            continue  # 无日期段或早于存量截止线 → 存量计划, 不点名缺锚
        plan_md = plan_dir / "plan.md"
        try:
            mtime = plan_md.stat().st_mtime
        except OSError:
            continue
        age_days = (now - mtime) / 86400.0
        if age_days > _GRACE_PERIOD_DAYS:
            violations.append({
                "plan_id": plan_id,
                "category": "missing_anchor",
                "detail": f"计划已存在 {age_days:.1f} 天, 绑定注册表内无登记(超 {_GRACE_PERIOD_DAYS} 天宽限期)。",
            })

    # ── 已登记的记录: 逐条判 incomplete / dangling_* / empty_error_samples, exempt 直接跳过 ──
    for plan_id, binding in bindings.items():
        if _is_exempt(binding):
            continue

        review = binding.get("review") or {}
        review_mode = review.get("mode")
        top_error_samples_empty = not _has_error_samples(binding)
        items = binding.get("items") or []

        # ㊀ 顶层 error_samples 清单为空且未豁免 → 点名(与件级判定并行, 各自独立)
        if top_error_samples_empty:
            violations.append({
                "plan_id": plan_id,
                "category": "empty_error_samples",
                "detail": "error_samples 为空且未豁免。",
            })

        # ── 件级子锚递归(打回硬化㈡): 逐件查 error_samples 是否空 + tests[].file 是否悬空 ──
        any_item_incomplete = False
        for item in items:
            if not isinstance(item, dict):
                continue
            item_name = item.get("id") or item.get("name") or "(未命名件)"
            # 兼容旧登记里的 review="tests" 等非对象形态；脏数据不享受豁免，
            # 但也不能阻断其他计划的巡检结果。
            raw_item_review = item.get("review")
            item_review = raw_item_review if isinstance(raw_item_review, dict) else {}
            item_exempt = (
                item_review.get("mode") == "exempt"
                and bool((item_review.get("reason") or "").strip())
            )
            item_error_samples_empty = not item.get("error_samples")

            if item_exempt:
                pass  # 件级显式豁免+理由 → 该件不参与 incomplete/empty_error_samples 判定
            else:
                if item_error_samples_empty:
                    any_item_incomplete = True
                    violations.append({
                        "plan_id": plan_id,
                        "category": "empty_error_samples",
                        "detail": f"[件 {item_name}] error_samples 为空且未豁免。",
                    })

            violations.extend(
                _check_tests_dangling(project_root, plan_id, item.get("tests") or [], item_name=item_name)
            )

            # 件级 whatnow 引用回指失败(件自身若显式落了 _whatnow_verified=False)
            if item.get("_whatnow_verified") is False:
                note = item.get("_whatnow_verify_note") or "whatnow 回指失败"
                violations.append({
                    "plan_id": plan_id,
                    "category": "dangling_whatnow",
                    "detail": f"[件 {item_name}] whatnow_task={item.get('whatnow_task')!r} 回指失败: {note}",
                })

        # ㊃ 完整性口径: 单一来源 registry/plan_bindings.py::binding_status(件级
        # worst-of, 顶层占位不得掩盖件级缺失; 无 items 时退回顶层判定)。dashboard
        # controlplane/plans.py 的 _binding_status 调的是同一份函数, 两边不得各写一套。
        from ...registry.plan_bindings import binding_status as _shared_binding_status

        incomplete = _shared_binding_status(binding) == "incomplete"
        assert incomplete == (any_item_incomplete if items else top_error_samples_empty), (
            "共享判定函数与本文件件级递归明细生成逻辑口径分叉, 需同步修"
        )
        if incomplete and review_mode != "exempt":
            violations.append({
                "plan_id": plan_id,
                "category": "incomplete",
                "detail": (
                    "已纳管但件级子锚未补全(至少一件 error_samples 空且非豁免)。"
                    if items else
                    "已纳管但骨架未补全(error_samples 空且 review.mode 非 exempt)。"
                ),
            })

        # ㊂ 顶层 tests[].file 不存在 → dangling_test_file
        violations.extend(_check_tests_dangling(project_root, plan_id, binding.get("tests") or []))

        # ㊂ 顶层 whatnow 任务回指失败(落盘期已标记 _whatnow_verified=False) → dangling_whatnow
        if binding.get("_whatnow_verified") is False:
            note = binding.get("_whatnow_verify_note") or "whatnow 回指失败"
            violations.append({
                "plan_id": plan_id,
                "category": "dangling_whatnow",
                "detail": f"whatnow_task={binding.get('whatnow_task')!r} 回指失败: {note}",
            })

    return violations


def _noop_check(ctx: FileContext) -> bool:
    """占位 check: 本规则走目录级扫描, 不走 per-file FileContext。

    真实扫描由 patrol/worker 直接调 scan_plan_binding_violations()。
    """
    return False


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-099",
        name="plan-bindings-audit",
        severity="MEDIUM",
        description=(
            "绑定注册表(data/registry/plan_bindings.json)巡检: 非归档新计划(目录名日期>=2026-07-03 存量截止线)"
            "超 3 天无登记=缺锚; 已纳管但骨架未补全(件级 worst-of, 顶层占位不掩盖件级缺失)=登记不完整;"
            "测试文件不存在或 whatnow 任务回指失败=悬空(顶层与 items[] 件级同查);"
            "显式豁免(review.mode=exempt + reason)不点名。真实扫描执行路径见"
            "guardian/workers/plan_bindings_scan_worker.py::PlanBindingsScanWorker。"
        ),
        check=_noop_check,
        disposition=["warn"],
        message_template=(
            "{plan_id}: {category} — {detail}\n"
            "  登记走 omni governance bind set <plan_id> ...; 件级走 omni governance bind item set <plan_id> <item_id> ...; "
            "豁免走 --review-mode exempt --review-reason '...'。"
        ),
        certainty="absolute",
    ),
]

__all__ = ["RULES", "scan_plan_binding_violations"]

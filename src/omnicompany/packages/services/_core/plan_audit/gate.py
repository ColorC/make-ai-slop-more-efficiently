# [OMNI] origin=claude-code domain=services/_core/plan_audit ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="plan 完成度硬门: 覆盖矩阵规则检查(NEEDS CLARIFICATION/exit_criteria/需求有验收/产物有完成判定/task覆盖), 不通过硬阻断 dispatch"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-gate: plan 仅在含全部执行细节时可投递; 开源界全是软门, 这里做硬门"
# [OMNI] tags=plan,gate,completeness,coverage,hard-block
# [OMNI] material_id="material:services._core.plan_audit.gate.py"
"""plan 完成度硬门 (确定性规则, 永不靠 LLM 拍脑袋)。

设计 (对齐 spec-kit analyze 覆盖矩阵, 但做成硬阻断):
- **完成度** check_plan_completeness: 读 plan.md 文本判
  1. 无 `[NEEDS CLARIFICATION]` 残留
  2. frontmatter 有非空 exit_criteria
  3. 需求清单每条有"验收"
  4. 产物清单每行有"完成判定"(表末列非空)
- **派发门** check_plan_dispatch_gate: 完成度 + (若已拆 task) 覆盖矩阵
  5. plan 已拆成 ≥1 个 task
  6. 每个 task 有 testStrategy + 自包含 details (无 NEEDS CLARIFICATION)

返回 {ok, blocks:[{kind, detail, remediation}], summary, checks}。
被 `omni plan dispatch` / `omni plan gate` 调用; 不通过 → 非 0 退出 / 拒绝派发。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from omnicompany.core.plans_catalogue import _plans_root, parse_plan_frontmatter

# 真占位符 = 带冒号问题的 `[NEEDS CLARIFICATION: <问题>]` (spec-kit 约定);
# 纯 `[NEEDS CLARIFICATION]`(无冒号, 文档里讲这个标记本身) 不算未澄清残留。
NEEDS_CLARIFICATION_RE = re.compile(r"\[NEEDS CLARIFICATION:", re.IGNORECASE)
# 代码片段(行内 `...` 与围栏 ```...```) 里出现的标记是"引用"不是真占位, 扫描前剥掉。
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# 引用式占位 (问题正文就是字面省略号 ...) 也不算真未决。
_PLACEHOLDER_ELLIPSIS_RE = re.compile(r"\[NEEDS CLARIFICATION:\s*\.\.\.\s*\]", re.IGNORECASE)


def _real_needs_clarification(text: str) -> list[str]:
    """返回真未决占位(已剥代码片段 + 排除字面省略号引用)。"""
    stripped = _CODE_FENCE_RE.sub(" ", text)
    stripped = _INLINE_CODE_RE.sub(" ", stripped)
    stripped = _PLACEHOLDER_ELLIPSIS_RE.sub(" ", stripped)
    return NEEDS_CLARIFICATION_RE.findall(stripped)


def _plan_md_path(plan_id: str) -> Path:
    return _plans_root() / plan_id / "plan.md"


def _section(text: str, *titles: str) -> str:
    """抓某个 markdown 章节正文 (从标题行到下一个同级/更高级标题)。titles 任一命中即可。"""
    lines = text.splitlines()
    start = None
    start_level = 0
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if not m:
            continue
        heading = m.group(2)
        if any(t in heading for t in titles):
            start = i + 1
            start_level = len(m.group(1))
            break
    if start is None:
        return ""
    out: list[str] = []
    for ln in lines[start:]:
        m = re.match(r"^(#{1,6})\s+", ln)
        if m and len(m.group(1)) <= start_level:
            break
        out.append(ln)
    return "\n".join(out)


def _requirement_items(req_section: str) -> list[str]:
    """需求清单里的编号条目 (1. / 2. / - **X**: ...)。"""
    items: list[str] = []
    for ln in req_section.splitlines():
        s = ln.strip()
        if re.match(r"^(\d+[.、)]|[-*])\s+", s):
            items.append(s)
    return items


def _product_rows(prod_section: str) -> list[list[str]]:
    """产物清单 markdown 表格的数据行 (跳表头/分隔行)。"""
    rows: list[list[str]] = []
    for ln in prod_section.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        joined = " ".join(cells).lower()
        if set("".join(cells)) <= set("-: ") and cells:  # 分隔行 |---|---|
            continue
        if joined.startswith("id ") or cells[0].lower() in {"id", "id "}:  # 表头
            continue
        rows.append(cells)
    return rows


def check_plan_completeness(plan_id: str) -> dict[str, Any]:
    """plan.md 内容级完成度检查 (不依赖 task 存储)。"""
    plan_md = _plan_md_path(plan_id)
    blocks: list[dict[str, str]] = []
    if not plan_md.is_file():
        return {
            "ok": False,
            "plan_id": plan_id,
            "blocks": [{"kind": "missing", "detail": f"plan.md 不存在: {plan_md}",
                        "remediation": "确认 plan_id 正确, 或先 omni notes promote 生成"}],
            "summary": "plan.md 不存在",
            "checks": {},
        }
    text = plan_md.read_text(encoding="utf-8")
    fm = parse_plan_frontmatter(plan_md)
    checks: dict[str, Any] = {}

    # 1. NEEDS CLARIFICATION 残留 (剥代码片段 + 排除字面省略号引用)
    nc = _real_needs_clarification(text)
    checks["needs_clarification"] = len(nc)
    if nc:
        blocks.append({
            "kind": "needs_clarification",
            "detail": f"plan 残留 {len(nc)} 处 [NEEDS CLARIFICATION] 未澄清",
            "remediation": "澄清并删除所有 [NEEDS CLARIFICATION] 占位",
        })

    # 2. exit_criteria 非空
    ec = fm.get("exit_criteria") or []
    checks["exit_criteria_count"] = len(ec) if isinstance(ec, list) else 0
    if not (isinstance(ec, list) and len(ec) > 0):
        blocks.append({
            "kind": "exit_criteria",
            "detail": "frontmatter 缺非空 exit_criteria",
            "remediation": "在 plan.md frontmatter 补 exit_criteria 列表",
        })

    # 3. 需求清单每条有验收
    req_sec = _section(text, "需求清单", "需求")
    reqs = _requirement_items(req_sec)
    checks["requirement_count"] = len(reqs)
    if not reqs:
        blocks.append({
            "kind": "requirements",
            "detail": "找不到需求清单条目",
            "remediation": "补『需求清单』章节, 每条含 ID + 验收",
        })
    else:
        missing_acc = [r[:40] for r in reqs if ("验收" not in r and "acceptance" not in r.lower())]
        checks["requirements_missing_acceptance"] = len(missing_acc)
        if missing_acc:
            blocks.append({
                "kind": "requirement_no_acceptance",
                "detail": f"{len(missing_acc)} 条需求缺『验收』: " + "; ".join(missing_acc[:3]),
                "remediation": "每条需求补可对账的验收条件",
            })

    # 4. 产物清单每行有完成判定
    prod_sec = _section(text, "产物清单", "产物")
    rows = _product_rows(prod_sec)
    checks["product_count"] = len(rows)
    if not rows:
        blocks.append({
            "kind": "products",
            "detail": "找不到产物清单表格行",
            "remediation": "补『产物清单』表格, 每行含 path + 完成判定",
        })
    else:
        bad = [r for r in rows if not r[-1].strip() or r[-1].strip() in {"-", "—", "待定", "TBD"}]
        checks["products_missing_done_criteria"] = len(bad)
        if bad:
            blocks.append({
                "kind": "product_no_done_criteria",
                "detail": f"{len(bad)} 行产物缺完成判定(表末列空/占位)",
                "remediation": "每行产物补 agent 能查的完成判定",
            })

    ok = len(blocks) == 0
    return {
        "ok": ok,
        "plan_id": plan_id,
        "blocks": blocks,
        "summary": "✅ 完成度通过" if ok else f"❌ {len(blocks)} 处完成度缺口",
        "checks": checks,
    }


def _load_tasks_for_plan(plan_id: str) -> list[dict[str, Any]] | None:
    """拿该 plan 的 task 列表 (task 存储, stage 2)。task 设施未就绪时返回 None。"""
    try:
        from omnicompany.packages.services._core.lifecycle.task import TaskStore
    except Exception:
        return None
    try:
        store = TaskStore()
        return [t.to_dict() for t in store.list_tasks(plan_id=plan_id)]
    except Exception:
        return None


def check_plan_dispatch_gate(plan_id: str, *, require_tasks: bool = True) -> dict[str, Any]:
    """派发硬门 = 完成度 + 覆盖矩阵 (task 覆盖 + 每 task 有 testStrategy)。"""
    res = check_plan_completeness(plan_id)
    blocks = list(res["blocks"])
    checks = dict(res["checks"])

    tasks = _load_tasks_for_plan(plan_id)
    if tasks is None:
        checks["task_store"] = "unavailable"
        if require_tasks:
            blocks.append({
                "kind": "no_task_store",
                "detail": "task 设施未就绪或该 plan 未拆分成 task",
                "remediation": "先 omni plan split <plan_id> 拆出 task 树",
            })
    else:
        checks["task_count"] = len(tasks)
        if require_tasks and not tasks:
            blocks.append({
                "kind": "no_tasks",
                "detail": "该 plan 还没有拆出任何 task",
                "remediation": "omni plan split <plan_id>",
            })
        no_test = [t.get("id") for t in tasks if not (t.get("test_strategy") or "").strip()]
        if no_test:
            checks["tasks_missing_test_strategy"] = len(no_test)
            blocks.append({
                "kind": "task_no_test_strategy",
                "detail": f"{len(no_test)} 个 task 缺 testStrategy: {', '.join(str(x) for x in no_test[:5])}",
                "remediation": "每个 task 补 test_strategy (怎么验证它做完了)",
            })
        nc_tasks = [t.get("id") for t in tasks
                    if _real_needs_clarification(t.get("details") or "")]
        if nc_tasks:
            checks["tasks_needs_clarification"] = len(nc_tasks)
            blocks.append({
                "kind": "task_needs_clarification",
                "detail": f"{len(nc_tasks)} 个 task 的 details 残留 NEEDS CLARIFICATION",
                "remediation": "补全 task details, 让它自包含全部执行细节",
            })
        # 「动手前先写好」: 每个 task 必须有 file_scope + expected_outputs(否则无法判并行/无法对账产出)
        no_scope = [t.get("id") for t in tasks if not (t.get("file_scope") or [])]
        if no_scope:
            checks["tasks_missing_file_scope"] = len(no_scope)
            blocks.append({
                "kind": "task_no_file_scope",
                "detail": f"{len(no_scope)} 个 task 没写 file_scope(文件范围): {', '.join(str(x) for x in no_scope[:5])}",
                "remediation": "每个 task 写明会碰哪些文件/目录(也是判能否并行的依据)",
            })
        no_out = [t.get("id") for t in tasks if not (t.get("expected_outputs") or [])]
        if no_out:
            checks["tasks_missing_expected_outputs"] = len(no_out)
            blocks.append({
                "kind": "task_no_expected_outputs",
                "detail": f"{len(no_out)} 个 task 没写 expected_outputs(预计产出): {', '.join(str(x) for x in no_out[:5])}",
                "remediation": "每个 task 写明预计产出什么(文件/命令/物料)",
            })

    ok = len(blocks) == 0
    return {
        "ok": ok,
        "plan_id": plan_id,
        "blocks": blocks,
        "summary": "✅ 派发门通过, plan 含全部执行细节" if ok else f"❌ 派发被拒: {len(blocks)} 处缺口",
        "checks": checks,
    }


__all__ = ["check_plan_completeness", "check_plan_dispatch_gate"]

# [OMNI] origin=claude-code domain=services/_governance/plan_steward ts=2026-06-12T12:00:00Z type=router
# [OMNI] material_id="material:governance.plan_steward.classify_pipeline.py"
"""计划治理管线 — 给全仓 docs/plans 的每个计划: 归属项目 / 中文标题 / 格式检查。

背景(2026-06-12 用户): KB-INGEST 类计划曾出现在错误项目下, 根因是上一轮**人工拍脑袋**把
plan id 写进项目类目; 且类目前缀匹配会把物理放错文件夹的计划一起带进来。本部门用便宜
模型(deepseek-v4-pro)对每个计划逐一判定, 产出**显式覆盖表** data/registry/plan_governance.json:

    {"plans": {"<plan_id>": {"project": "<项目id|null>", "title_zh": "...", ...}}}

消费方: core/projects_registry.resolve_project_plans(覆盖优先, 未治理的退回前缀规则) +
controlplane/plans(/api/plans 浮出 title_zh)。计划物理位置不动 — 挪文件夹是另一种危险
操作, 报告里只给"位置与归属不一致"清单供人/总控决策。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.core.projects_registry import list_projects, plan_governance_path
from omnicompany.runtime.llm.batch import run_parallel_items
from omnicompany.runtime.llm.structured import call_json, default_structured_model

BATCH_SIZE = 10
EXCERPT_CHARS = 600
_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)

SYSTEM_PROMPT = """你是 omnicompany 仓库的计划治理员。对每个计划(plan)判定归属项目并起一个完整的中文标题。
规则:
1. project 只能取给定项目清单里的 id, 或 null(不属于任何已注册项目, 例如 omnicompany 自身的框架/实验/知识吸收类计划)。不确定时宁可 null。
2. 归属 = 这个计划是该项目的**工作内容本身**。仅仅"与项目主题相关"不算归属(例如"给某游戏业务建知识库"的基建计划属于知识吸收基建, 不属于该游戏项目)。
3. title_zh: 一个**完整、读得通**的中文标题, 完整概括计划主题。不要硬压到十几个字、不要砍成名词堆; 也别啰嗦, 一般 10-24 个汉字。不含日期、编号、英文缩写堆砌。**不要自己加【】方括号或项目名前缀** —— 项目短称由系统自动接在前面, 你只产标题正文。
4. 判断依据优先级: 摘录内容 > 目录名 > 所在类目目录。类目目录可能放错(这正是治理对象); hint_categories 只是目录结构对应提示, 绝不能作为归属个别计划的依据。
5. 输出严格 JSON, 不要任何其它文字:
{"plans": [{"id": "...", "project": "<项目id或null>", "title_zh": "...", "confidence": "high|medium|low", "reason": "不超过25字"}]}"""

PLAN_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["plans"],
    "properties": {
        "plans": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "project", "title_zh"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "project": {"type": ["string", "null"]},
                    "title_zh": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_dir() -> Path:
    d = omni_workspace_root() / "data" / "governance" / "plan_steward"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── benchmark: 金标签机制 (2026-06-12 用户: "不要过分相信(便宜模型), 建立benchmark
# 机制, 你亲自操作的结果其实是要更准确的——只要你真的去看了") ──────────────────
# 金标签 = 主力模型/人**亲自读过计划内容**后判定的归属, 权威高于便宜模型;
# run_governance 合并时强制覆盖, 重跑不回退。一致率 = 便宜模型质量的常态化度量。


def benchmark_path() -> Path:
    return report_dir() / "benchmark.json"


def load_gold() -> dict[str, dict[str, Any]]:
    p = benchmark_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        labels = raw.get("labels") if isinstance(raw, dict) else None
        return labels if isinstance(labels, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _apply_gold(merged: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """金标签压制模型结论; 返回一致率统计(以模型原判 model_project 计)。"""
    gold = load_gold()
    agree = disagree = 0
    mismatches: list[str] = []
    for pid, label in gold.items():
        entry = merged.get(pid)
        if entry is None:
            continue
        model_says = entry.get("model_project", entry.get("project"))
        entry["model_project"] = model_says
        entry["gold"] = True
        entry["project"] = label.get("project")
        if label.get("title_zh"):
            entry["title_zh"] = label["title_zh"]
        if model_says == label.get("project"):
            agree += 1
        else:
            disagree += 1
            mismatches.append(f"{pid}: 模型={model_says} 金标={label.get('project')}")
    total = agree + disagree
    return {"gold_total": len(gold), "gold_compared": total,
            "agreement": (agree / total) if total else None,
            "gold_mismatches": mismatches}


def benchmark_report(apply: bool = False) -> dict[str, Any]:
    """便宜模型 vs 金标签的一致率。apply=True 时把金标签持久化进覆盖表(立即生效)。"""
    gov_path = plan_governance_path()
    if not gov_path.is_file():
        return {"error": "还没有治理覆盖表"}
    raw = json.loads(gov_path.read_text(encoding="utf-8")) or {}
    merged = {k: dict(v) for k, v in (raw.get("plans") or {}).items()}
    stats = _apply_gold(merged)
    if apply:
        raw["plans"] = merged
        raw["gold_applied_at"] = _now()
        gov_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        stats["applied"] = True
    return stats


def _plan_catalogue() -> list[dict[str, Any]]:
    from omnicompany.core.plans_catalogue import _scan

    return _scan()


def _excerpt(folder_path: str, files: list[str]) -> str:
    """plan.md 开头(去 frontmatter)优先; 没有就拿首个 md 文件开头。读不到给空。"""
    base = omni_workspace_root() / folder_path
    candidates = ["plan.md"] + [f for f in files if f != "plan.md"][:1]
    for rel in candidates:
        p = base / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _FRONTMATTER_RE.sub("", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text[:EXCERPT_CHARS]
    return ""


def _project_catalog() -> list[dict[str, Any]]:
    out = []
    for p in list_projects():
        out.append({
            "id": p["id"],
            "name": p.get("name") or p["id"],
            "short": p.get("short") or "",
            "group": p.get("group"),
            "desc": (p.get("desc") or "")[:120],
            "hint_categories": p.get("plan_categories") or [],
        })
    return out


# ── 标题短称前缀 (2026-06-22 用户: 全部 plan 标题中文完整化并接项目短称) ──────────
# 格式 = 【短称】完整中文标题。短称由项目注册表 short 字段给(确定性, 不靠模型);
# 模型只产"完整中文标题正文", 前缀由本模块按归属项目确定性拼接 — 归属变了前缀自动跟着变。
# 无归属(project=null)的框架/实验类计划: 按类目目录映射一个短称, 兜底【综合】。

# 类目目录 → 短称 (null-project 计划用; key 取 plan_id 第一段或类目)
_CATEGORY_SHORT: dict[str, str] = {
    "agent-framework": "框架",
    "reasoning-network": "推理",
    "stage-experiments": "实验",
    "diagnosis": "队医",
    "omnicompany-调研吸收": "学习",
    "cli": "CLI",
    "webworks": "网页活",
    "dashboard": "驾驶舱",
    "guardian": "守护",
}
_FALLBACK_SHORT = "综合"


def _strip_existing_bracket(title: str) -> str:
    """去掉历史标题已带的【...】前缀, 避免重刷时套娃。"""
    t = (title or "").strip()
    m = re.match(r"^[【\[][^】\]]{1,8}[】\]]\s*", t)
    return t[m.end():].strip() if m else t


def _short_for(project_id: str | None, plan_id: str,
               projects_by_id: dict[str, dict[str, Any]]) -> str:
    """这个计划标题该接哪个短称: 有归属项目用其 short; 否则按类目映射兜底。"""
    if project_id and project_id in projects_by_id:
        s = (projects_by_id[project_id].get("short") or "").strip()
        if s:
            return s
    head = (plan_id or "").split("/", 1)[0]
    return _CATEGORY_SHORT.get(head, _FALLBACK_SHORT)


def _bracketed_title(project_id: str | None, plan_id: str, body: str,
                     projects_by_id: dict[str, dict[str, Any]]) -> str:
    short = _short_for(project_id, plan_id, projects_by_id)
    return f"【{short}】{_strip_existing_bracket(body)}"


def _prefix_projects(plan_id: str, projects: list[dict[str, Any]]) -> list[str]:
    """旧前缀规则下这个计划会被哪些项目命中(用于'位置与归属不一致'报告)。"""
    hit = []
    for p in projects:
        cats = [c.strip().rstrip("/") for c in (p.get("hint_categories") or []) if c]
        if any(plan_id == c or plan_id.startswith(c + "/") for c in cats):
            hit.append(p["id"])
    return hit


def _classify_batch(batch: list[dict[str, Any]], projects: list[dict[str, Any]],
                    model: str) -> dict[str, dict[str, Any]]:
    user = (
        "## 已注册项目\n" + json.dumps(projects, ensure_ascii=False)
        + "\n\n## 待治理计划(本批)\n" + json.dumps(batch, ensure_ascii=False)
    )
    res = call_json(system=SYSTEM_PROMPT, user=user, schema=PLAN_CLASSIFICATION_SCHEMA, model=model,
                    caller="governance.plan_steward", max_tokens=6000)
    valid_ids = {p["id"] for p in projects}
    batch_ids = {b["id"] for b in batch}
    out: dict[str, dict[str, Any]] = {}
    for row in res.get("plans") or []:
        pid = str(row.get("id") or "")
        if pid not in batch_ids:
            continue
        proj = row.get("project")
        issues = []
        if proj is not None and proj not in valid_ids:
            issues.append(f"模型给了未注册项目 {proj!r}, 已置 null")
            proj = None
        # 模型只产标题正文(无【】前缀); 前缀由 run_governance 按归属项目确定性拼接。
        body = _strip_existing_bracket(str(row.get("title_zh") or ""))[:40]
        out[pid] = {
            "project": proj,
            "title_body": body,
            "title_zh": body,  # run_governance 会覆盖为【短称】正文; 这里先放正文兜底
            "confidence": row.get("confidence") or "low",
            "reason": str(row.get("reason") or "")[:60],
            "issues": issues,
        }
    return out


def run_governance(*, model: str | None = None, limit: int | None = None,
                   only_missing: bool = False, workers: int = 4,
                   dry_run: bool = False, echo: Any = None) -> dict[str, Any]:
    """全量治理: 扫描 → 便宜模型分批分类 → 格式检查 → 写覆盖表 + 报告。

    only_missing: 只处理覆盖表里还没有的计划(增量, 新计划补登记用)。
    """
    log = echo or (lambda s: None)
    model = model or default_structured_model()
    catalogue = _plan_catalogue()
    projects = _project_catalog()
    gov_path = plan_governance_path()
    existing: dict[str, Any] = {}
    if gov_path.is_file():
        try:
            existing = (json.loads(gov_path.read_text(encoding="utf-8")) or {}).get("plans") or {}
        except (OSError, json.JSONDecodeError):
            existing = {}

    todo = [it for it in catalogue if not (only_missing and it["id"] in existing)]
    if limit:
        todo = todo[:limit]
    log(f"计划总数 {len(catalogue)}, 本次治理 {len(todo)}, 模型 {model}, 并发 {workers}")

    # 组装任务(含摘录, 纯 IO)
    tasks = []
    for it in todo:
        tasks.append({
            "id": it["id"],
            "category": it.get("category"),
            "topic": it.get("topic"),
            "date": it.get("date"),
            "archived": bool(it.get("archived")),
            "files": (it.get("files") or [])[:8],
            "excerpt": _excerpt(it.get("folder_path") or "", it.get("files") or []),
        })

    batches = [tasks[i:i + BATCH_SIZE] for i in range(0, len(tasks), BATCH_SIZE)]
    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []

    def _work(batch: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return _classify_batch(batch, projects, model)

    batch_run = run_parallel_items(
        batches,
        _work,
        workers=workers,
        item_label=lambda _i, b: f"批次({b[0]['id']}..)" if b else "批次(empty)",
        echo=log,
        progress_label="批次",
        progress_every=1,
        status_run_id="governance.plan_steward",
    )
    for item_result in batch_run.results:
        results.update(item_result)
    failures.extend(batch_run.failures)

    # 确定性格式检查 + 位置一致性 + 标题短称前缀
    by_id = {it["id"]: it for it in catalogue}
    projects_by_id = {p["id"]: p for p in projects}
    for pid, entry in results.items():
        it = by_id.get(pid) or {}
        issues = list(entry.get("issues") or [])
        if not it.get("archived"):
            if not it.get("has_plan_md"):
                issues.append("缺 plan.md")
            if not it.get("file_count"):
                issues.append("空目录(无 md 文件)")
        old_hits = _prefix_projects(pid, projects)
        new_proj = entry.get("project")
        if old_hits and new_proj not in old_hits:
            issues.append(f"位置与归属不一致(旧前缀规则命中 {','.join(old_hits)})")
        if len(old_hits) > 1:
            issues.append(f"多项目前缀命中: {','.join(old_hits)}")
        # 标题 = 【短称】完整中文标题。前缀按归属项目确定性拼接(归属变了前缀跟着变)。
        body = entry.get("title_body") or _strip_existing_bracket(entry.get("title_zh") or "")
        entry["title_body"] = body
        entry["title_zh"] = _bracketed_title(new_proj, pid, body, projects_by_id)
        entry["issues"] = issues
        entry["category"] = it.get("category")
        entry["model"] = model
        entry["ts"] = _now()

    merged = {**existing, **results}
    # 自愈: 丢弃磁盘上已不存在的孤儿治理条目 + 确保每条标题都带【短称】前缀(幂等)。
    catalogue_ids = set(by_id)
    for pid in [k for k in merged if k not in catalogue_ids]:
        del merged[pid]
    bracket_re = re.compile(r"^[【\[][^】\]]{1,8}[】\]]")
    for pid, e in merged.items():
        if not bracket_re.match(str(e.get("title_zh") or "")):
            body = e.get("title_body") or _strip_existing_bracket(e.get("title_zh") or "")
            e["title_body"] = body
            e["title_zh"] = _bracketed_title(e.get("project"), pid, body, projects_by_id)
    gold_stats = _apply_gold(merged)
    summary = _summarize(merged, projects, failures)
    summary.update(gold_stats)

    if not dry_run:
        gov_path.parent.mkdir(parents=True, exist_ok=True)
        gov_path.write_text(json.dumps(
            {"version": 1, "generated_at": _now(), "model": model, "plans": merged},
            ensure_ascii=False, indent=1), encoding="utf-8")
        rp = report_dir() / f"report-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
        rp.write_text(_render_report(merged, summary), encoding="utf-8")
        summary["governance_file"] = str(gov_path)
        summary["report"] = str(rp)
    return summary


def _summarize(merged: dict[str, Any], projects: list[dict[str, Any]],
               failures: list[str]) -> dict[str, Any]:
    per_project: dict[str, int] = {}
    low_conf, mismatch, fmt_issues = [], [], []
    for pid, e in merged.items():
        key = e.get("project") or "(无归属)"
        per_project[key] = per_project.get(key, 0) + 1
        if e.get("confidence") == "low":
            low_conf.append(pid)
        for iss in e.get("issues") or []:
            if iss.startswith("位置与归属不一致"):
                mismatch.append(f"{pid} → {e.get('project')} ({iss})")
            elif iss in ("缺 plan.md", "空目录(无 md 文件)"):
                fmt_issues.append(f"{pid}: {iss}")
    return {
        "total_governed": len(merged),
        "per_project": dict(sorted(per_project.items(), key=lambda kv: -kv[1])),
        "low_confidence": low_conf,
        "relocation_suggested": mismatch,
        "format_issues_count": len(fmt_issues),
        "format_issues": fmt_issues,
        "failures": failures,
    }


def _render_report(merged: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        f"# 计划治理报告 — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"已治理计划: {summary['total_governed']}; 分类失败批次: {len(summary['failures'])}",
        "",
        "## 归属分布",
        "",
    ]
    for k, v in summary["per_project"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## 位置与归属不一致(建议人工/总控决定是否挪文件夹)", ""]
    lines += [f"- {m}" for m in summary["relocation_suggested"]] or ["- 无"]
    lines += ["", "## 低置信(建议复核)", ""]
    lines += [f"- {p} → {merged[p].get('project')} 「{merged[p].get('title_zh')}」 {merged[p].get('reason')}"
              for p in summary["low_confidence"]] or ["- 无"]
    lines += ["", f"## 格式问题({summary['format_issues_count']})", ""]
    lines += [f"- {m}" for m in summary["format_issues"]] or ["- 无"]
    if summary["failures"]:
        lines += ["", "## 失败批次", ""] + [f"- {f}" for f in summary["failures"]]
    return "\n".join(lines) + "\n"


def governance_summary() -> dict[str, Any]:
    """读现有覆盖表给摘要(不调模型)。"""
    gov_path = plan_governance_path()
    if not gov_path.is_file():
        return {"exists": False}
    try:
        raw = json.loads(gov_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"exists": True, "error": str(e)}
    merged = raw.get("plans") or {}
    s = _summarize(merged, _project_catalog(), [])
    s.update(exists=True, generated_at=raw.get("generated_at"), model=raw.get("model"))
    # 摘要场景不需要全文列表
    s["format_issues"] = s["format_issues"][:20]
    return s

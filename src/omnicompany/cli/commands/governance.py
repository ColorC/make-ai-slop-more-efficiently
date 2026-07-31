# [OMNI] origin=claude-code ts=2026-06-12 type=cli
# [OMNI] material_id="material:cli.governance.steward_and_history_verbs.py"
"""omni governance — 治理部门 CLI (计划治理 + 工作历史整理, 便宜模型干活)。

  omni governance plans-run       # deepseek 全量分类计划→项目 + 中文标题 + 格式检查
  omni governance plans-status    # 读覆盖表摘要(不调模型)
  omni governance history-run     # 抽 claude/codex 用户消息 → 重复需求/重复指正
  omni governance history-report  # 打印最近一次工作历史报告
  omni governance actions-check   # PROJECT_INDEX quick_actions 的 skill 存在性体检(确定性)
  omni governance docs-refs       # 文档引用完整性(断链/失效行锚, 确定性, 不调模型)
  omni governance docs-timeliness # 规范/计划/报告时效性(过期/被取代/冲突, 性价比模型为主)
  omni governance docs-report     # 打印最近一次文档治理摘要
  omni governance commit-run      # 性价比模型严格分批 git 提交(默认 dry-run, --apply 真提交)
  omni governance decisions-run   # 标记 llm_input 的札记 → 结构化决策(进总控 ctx)
  omni governance catalog         # 列出所有治理管线 + 档期 + 上次跑(唯一发现面)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from omnicompany.runtime.llm.structured import DEFAULT_STRUCTURED_MODEL, DEFAULT_STRUCTURED_MODEL_ENV

from .._access import any_caller, external_or_controller

# commit_steward 是隐私排除域外的治理服务, 直接取常量给 help 文案用(服务本体仍在命令体内惰性导入)
from omnicompany.packages.services._governance.commit_steward.steward import MAX_FILES_ENV


def _optional_service(mod: str):
    """隐私排除的可选服务(work_history/resume_steward/job_steward 不进公开白名单, 见
    scripts/build_public_platform.py EXCLUDES): 动态导入, 公开发行版缺失时人话报错退出。"""
    import importlib
    try:
        return importlib.import_module(mod)
    except ModuleNotFoundError:
        click.echo(f"该命令依赖的可选服务未随本发行版提供: {mod}")
        raise SystemExit(3)

@click.group("governance")
def cmd_governance() -> None:
    """治理部门: 计划治理(plan_steward) / 工作历史整理(work_history)。"""


@cmd_governance.command("plans-run")
@external_or_controller
@click.option("--model", default=None, show_default=f"{DEFAULT_STRUCTURED_MODEL_ENV} or {DEFAULT_STRUCTURED_MODEL}")
@click.option("--limit", type=int, default=None, help="只处理前 N 个(冒烟用)")
@click.option("--only-missing", is_flag=True, help="只补登记覆盖表里还没有的计划(增量)")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--dry-run", is_flag=True, help="只分类不落盘")
def cmd_plans_run(model: str | None, limit: int | None, only_missing: bool, workers: int, dry_run: bool) -> None:
    """全量计划治理: 归属分类 + 汉化 + 格式检查 → data/registry/plan_governance.json。"""
    from omnicompany.packages.services._governance.plan_steward import run_governance
    summary = run_governance(model=model, limit=limit, only_missing=only_missing,
                             workers=workers, dry_run=dry_run, echo=click.echo)
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@cmd_governance.command("plans-sync")
@external_or_controller
@click.option("--all", "all_", is_flag=True, help="不只处理变动的, 全量重评估(贵)")
@click.option("--limit", type=int, default=None, help="只处理前 N 个(冒烟用)")
@click.option("--dry-run", is_flag=True, help="只列出待新建/刷新的计划, 不评估不落地")
@click.option("--audit", is_flag=True, help="同步完成后立即跑进度唯一真源审计并提交变化后的合并审阅材料")
def cmd_plans_sync(all_: bool, limit: int | None, dry_run: bool, audit: bool) -> None:
    """计划→whatnow 同步: 新计划自动建 task, 进度过时的重评估刷新(进度集中在 whatnow 一处管)。"""
    from omnicompany.packages.services._focus.plan_progress_recorder.sync import run_sync
    summary = run_sync(only_changed=not all_, limit=limit, dry_run=dry_run, echo=click.echo)
    output: dict[str, Any] = {"sync": summary}
    if audit and not dry_run:
        from omnicompany.packages.services._governance.progress_steward.ssot import run_progress_ssot_audit
        output["ssot"] = run_progress_ssot_audit(submit_review=True, fix=True)
    click.echo(json.dumps(output if audit else summary, ensure_ascii=False, indent=2))


@cmd_governance.group("bind")
def cmd_bind() -> None:
    """绑定注册表 v1: 计划-进度-测试-评审四件登记(data/registry/plan_bindings.json)。

    位置写死, 不接受任何路径类选项(--path/--registry-path/--root 等)——见
    overnight-run.md 第六节验收锚错误样本㊁。
    """


def _parse_test_option(raw: str) -> dict[str, str]:
    """`file[:node][:kind]` → {file, node?, kind}。kind 默认 positive。"""
    parts = raw.split(":")
    file_ = parts[0]
    node = parts[1] if len(parts) > 1 and parts[1] else None
    kind = parts[2] if len(parts) > 2 and parts[2] else "positive"
    d: dict[str, str] = {"file": file_, "kind": kind}
    if node:
        d["node"] = node
    return d


def _parse_error_sample_option(raw: str) -> dict[str, str]:
    """`DESC[|TEST_REF]` → {desc, test_ref?}。"""
    if "|" in raw:
        desc, test_ref = raw.split("|", 1)
        return {"desc": desc, "test_ref": test_ref}
    return {"desc": raw}


def _parse_review_record_option(raw: str) -> dict[str, str]:
    """`WHO|VERDICT[|NOTE]` → {who, verdict, note?}。评审记录回填用, 打回硬化㈢。"""
    parts = raw.split("|")
    d: dict[str, str] = {"who": parts[0]}
    if len(parts) > 1:
        d["verdict"] = parts[1]
    if len(parts) > 2:
        d["note"] = "|".join(parts[2:])
    return d


def _build_review(
    review_mode: str | None,
    review_standard: str | None,
    review_reason: str | None,
    review_records_raw: tuple[str, ...],
    *,
    existing_review: dict | None = None,
) -> dict[str, object] | None:
    """组装 review dict, records 采用"追加到既有"语义(评审记录回填不覆盖历史记录)。"""
    existing_review = existing_review or {}
    existing_records = list(existing_review.get("records") or [])
    new_records = [_parse_review_record_option(r) for r in review_records_raw]
    records = existing_records + new_records

    if not (review_mode or review_standard or review_reason or records):
        return None

    review: dict[str, object] = dict(existing_review)
    if review_mode:
        review["mode"] = review_mode
    if review_standard:
        review["standard"] = review_standard
    if review_reason:
        review["reason"] = review_reason
    if records:
        review["records"] = records
    return review


@cmd_bind.command("set")
@external_or_controller
@click.argument("plan_id")
@click.option("--whatnow-task", default=None)
@click.option("--test", "tests_raw", multiple=True, help="file[:node][:kind], 可重复")
@click.option("--error-sample", "error_samples_raw", multiple=True, help="DESC[|TEST_REF], 可重复")
@click.option("--write-target", "write_targets", multiple=True)
@click.option("--review-mode", type=click.Choice(["tests", "panel", "exempt"]), default=None)
@click.option("--review-standard", default=None)
@click.option("--review-reason", default=None)
@click.option("--review-record", "review_records_raw", multiple=True,
              help="评审记录回填 WHO|VERDICT[|NOTE], 可重复; 追加不覆盖历史记录")
@click.option("--testmap", "testmaps_raw", multiple=True,
              help="计划声明自己动的软件 testmap app 名(omni testmap list 里的 app 字段), 可重复")
@click.option("--json-output", is_flag=True)
def cmd_bind_set(
    plan_id: str,
    whatnow_task: str | None,
    tests_raw: tuple[str, ...],
    error_samples_raw: tuple[str, ...],
    write_targets: tuple[str, ...],
    review_mode: str | None,
    review_standard: str | None,
    review_reason: str | None,
    review_records_raw: tuple[str, ...],
    testmaps_raw: tuple[str, ...],
    json_output: bool,
) -> None:
    """登记/更新一条绑定记录(顶层四件登记; 件级子锚用 `bind item set`)。"""
    from omnicompany.packages.services._core.registry.plan_bindings import get_binding, set_binding

    existing = get_binding(plan_id) or {}
    review = _build_review(
        review_mode, review_standard, review_reason, review_records_raw,
        existing_review=existing.get("review"),
    )

    try:
        rec = set_binding(
            plan_id,
            whatnow_task=whatnow_task or existing.get("whatnow_task"),
            tests=[_parse_test_option(t) for t in tests_raw] or (existing.get("tests") or None),
            error_samples=[_parse_error_sample_option(e) for e in error_samples_raw] or (existing.get("error_samples") or None),
            write_targets=list(write_targets) or (existing.get("write_targets") or None),
            review=review,
            items=existing.get("items") or None,
            testmaps=list(testmaps_raw) or (existing.get("testmaps") or None),
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    if json_output:
        click.echo(json.dumps(rec, ensure_ascii=False, indent=2))
    else:
        click.echo(f"绑定已登记: {plan_id}")


@cmd_bind.command("show")
@any_caller
@click.argument("plan_id")
@click.option("--json-output", is_flag=True)
def cmd_bind_show(plan_id: str, json_output: bool) -> None:
    """查看一条绑定记录。"""
    from omnicompany.packages.services._core.registry.plan_bindings import get_binding

    rec = get_binding(plan_id)
    if rec is None:
        if json_output:
            click.echo(json.dumps({"found": False, "plan_id": plan_id}, ensure_ascii=False))
        else:
            click.echo(f"未登记: {plan_id}")
        raise SystemExit(1)
    if json_output:
        click.echo(json.dumps(rec, ensure_ascii=False, indent=2))
    else:
        click.echo(json.dumps(rec, ensure_ascii=False, indent=2))


@cmd_bind.command("list")
@any_caller
@click.option("--json-output", is_flag=True)
def cmd_bind_list(json_output: bool) -> None:
    """列出全部绑定记录。"""
    from omnicompany.packages.services._core.registry.plan_bindings import list_bindings

    all_bindings = list_bindings()
    if json_output:
        click.echo(json.dumps(list(all_bindings.values()), ensure_ascii=False, indent=2))
    else:
        for plan_id in sorted(all_bindings):
            click.echo(plan_id)


# ─── bind item: 件级子锚写入(打回硬化㈢, 2026-07-03) ─────────────────────────
# 唯一写入方名副其实: 件级 items[] 字段也必须能经 CLI 全量写出, 不能只留给
# 直接调 set_binding() 函数(那只该是测试专用路径)。位置仍写死, 不接受路径参数。

@cmd_bind.group("item")
def cmd_bind_item() -> None:
    """绑定注册表件级子锚(items[] 数组)登记: 计划里的每个工作项各自的 tests/error_samples/write_targets/review。"""


@cmd_bind_item.command("set")
@external_or_controller
@click.argument("plan_id")
@click.argument("item_id")
@click.option("--test", "tests_raw", multiple=True, help="file[:node][:kind], 可重复")
@click.option("--error-sample", "error_samples_raw", multiple=True, help="DESC[|TEST_REF], 可重复")
@click.option("--write-target", "write_targets", multiple=True)
@click.option("--review-mode", type=click.Choice(["tests", "panel", "exempt"]), default=None)
@click.option("--review-standard", default=None)
@click.option("--review-reason", default=None)
@click.option("--review-record", "review_records_raw", multiple=True,
              help="评审记录回填 WHO|VERDICT[|NOTE], 可重复; 追加不覆盖历史记录")
@click.option("--json-output", is_flag=True)
def cmd_bind_item_set(
    plan_id: str,
    item_id: str,
    tests_raw: tuple[str, ...],
    error_samples_raw: tuple[str, ...],
    write_targets: tuple[str, ...],
    review_mode: str | None,
    review_standard: str | None,
    review_reason: str | None,
    review_records_raw: tuple[str, ...],
    json_output: bool,
) -> None:
    """登记/更新 plan_id 下 item_id 这一件的子锚(upsert 进 items[] 数组, 保留其余件不变)。"""
    from omnicompany.packages.services._core.registry.plan_bindings import get_binding, set_binding

    existing = get_binding(plan_id) or {}
    items: list[dict] = list(existing.get("items") or [])
    idx = next((i for i, it in enumerate(items) if isinstance(it, dict) and it.get("id") == item_id), None)
    existing_item = items[idx] if idx is not None else {}

    review = _build_review(
        review_mode, review_standard, review_reason, review_records_raw,
        existing_review=existing_item.get("review"),
    )

    new_item: dict[str, object] = dict(existing_item)
    new_item["id"] = item_id
    if tests_raw:
        new_item["tests"] = [_parse_test_option(t) for t in tests_raw]
    else:
        new_item.setdefault("tests", existing_item.get("tests") or [])
    if error_samples_raw:
        new_item["error_samples"] = [_parse_error_sample_option(e) for e in error_samples_raw]
    else:
        new_item.setdefault("error_samples", existing_item.get("error_samples") or [])
    if write_targets:
        new_item["write_targets"] = list(write_targets)
    else:
        new_item.setdefault("write_targets", existing_item.get("write_targets") or [])
    if review is not None:
        new_item["review"] = review
    else:
        new_item.setdefault("review", existing_item.get("review") or {})

    if idx is not None:
        items[idx] = new_item
    else:
        items.append(new_item)

    try:
        rec = set_binding(
            plan_id,
            whatnow_task=existing.get("whatnow_task"),
            tests=existing.get("tests") or None,
            error_samples=existing.get("error_samples") or None,
            write_targets=existing.get("write_targets") or None,
            review=existing.get("review") or None,
            items=items,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    if json_output:
        click.echo(json.dumps(rec, ensure_ascii=False, indent=2))
    else:
        click.echo(f"件级绑定已登记: {plan_id} :: {item_id}")


@cmd_bind_item.command("show")
@any_caller
@click.argument("plan_id")
@click.argument("item_id")
@click.option("--json-output", is_flag=True)
def cmd_bind_item_show(plan_id: str, item_id: str, json_output: bool) -> None:
    """查看 plan_id 下某一件(item_id)的子锚登记。"""
    from omnicompany.packages.services._core.registry.plan_bindings import get_binding

    rec = get_binding(plan_id) or {}
    items = rec.get("items") or []
    item = next((it for it in items if isinstance(it, dict) and it.get("id") == item_id), None)
    if item is None:
        if json_output:
            click.echo(json.dumps({"found": False, "plan_id": plan_id, "item_id": item_id}, ensure_ascii=False))
        else:
            click.echo(f"未登记: {plan_id} :: {item_id}")
        raise SystemExit(1)
    click.echo(json.dumps(item, ensure_ascii=False, indent=2))


@cmd_governance.command("progress-scan")
@any_caller
@click.option("--docs", "include_docs", is_flag=True, help="也扫 docs/**/*.md(默认只 plan.md)")
@click.option("--code", "include_code", is_flag=True, help="也扫 src/**/*.py 注释里指涉别处进度的句子")
@click.option("--projects", "include_projects", is_flag=True,
             help="也扫 docs/projects/**/PROJECT_INDEX.md(进度圈出扩面到项目索引)")
@click.option("--limit", type=int, default=None, help="只处理前 N 个(冒烟用)")
@click.option("--summary", is_flag=True, help="只打印计数摘要, 不打印每条候选")
def cmd_progress_scan(include_docs: bool, include_code: bool, include_projects: bool,
                      limit: int | None, summary: bool) -> None:
    """轨一·里程碑一: 确定性圈出 plan.md/文档/注释/项目索引里的进度型自述候选(只标不改, 无 LLM)。"""
    from omnicompany.packages.services._governance.progress_steward.probe import run_progress_scan
    payload = run_progress_scan(include_docs=include_docs, include_code=include_code,
                                include_projects=include_projects, limit=limit)
    if summary:
        click.echo(json.dumps({k: payload[k] for k in
                               ("scanned_docs", "scanned_code", "total_candidates", "with_ref", "counts", "_written")
                               if k in payload}, ensure_ascii=False, indent=2))
    else:
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@cmd_governance.command("progress-ssot")
@any_caller
@click.option("--summary", is_flag=True, help="只打印分类计数与报告路径")
@click.option("--review/--no-review", "submit_review", default=True,
              help="有变化时提交一份合并审阅材料; 内容不变不重复提交")
@click.option("--fix", is_flag=True, help="自动移除 OmniMark/YAML 中的进度副本; 正文候选绝不自动删除")
def cmd_progress_ssot(summary: bool, submit_review: bool, fix: bool) -> None:
    """确定性检查 plan.md 是否复制了 WhatNow 当前进度真源。"""
    from omnicompany.packages.services._governance.progress_steward.ssot import run_progress_ssot_audit
    payload = run_progress_ssot_audit(submit_review=submit_review, fix=fix)
    if summary:
        payload = {key: payload.get(key) for key in (
            "scan_state", "clean", "scanned_plans", "authority_tasks",
            "violation_count", "counts", "authority_source", "_latest", "fix", "review_material",
        )}
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@cmd_governance.command("progress-review")
@external_or_controller
@click.option("--limit", type=int, default=None, help="只精判前 N 个文档(冒烟用)")
@click.option("--model", default=None, help="覆盖模型(默认性价比模型)")
@click.option("--only-ref", is_flag=True, help="只判死链(确定性, 不调模型)")
@click.option("--no-review", is_flag=True, help="只出报告, 不提交合并审阅材料")
def cmd_progress_review(limit: int | None, model: str | None, only_ref: bool, no_review: bool) -> None:
    """轨一·里程碑二: 对探针候选三态精判, 可处置项合并进审阅台。"""
    from omnicompany.packages.services._governance.progress_steward.review import run_progress_review
    payload = run_progress_review(limit=limit, model=model, only_ref=only_ref,
                                  submit_review=not no_review, echo=click.echo)
    click.echo(json.dumps({k: payload[k] for k in
                           ("reviewed_docs", "failed_docs", "drift_total", "decision_total", "review_material")
                           if k in payload},
                          ensure_ascii=False, indent=2))


@cmd_governance.command("progress-freshness")
@any_caller
@click.argument("plan_id")
def cmd_progress_freshness(plan_id: str) -> None:
    """读 plan.md 的 last_verified / authority(进度新鲜度元数据)。"""
    from omnicompany.packages.services._governance.progress_steward.freshness import read_freshness
    click.echo(json.dumps(read_freshness(plan_id), ensure_ascii=False, indent=2))


@cmd_governance.command("progress-mark-fresh")
@external_or_controller
@click.argument("plan_id")
def cmd_progress_mark_fresh(plan_id: str) -> None:
    """人一键"续期": 把 plan.md 的 last_verified 刷成今天 + authority=whatnow。"""
    from omnicompany.packages.services._governance.progress_steward.freshness import mark_fresh
    click.echo(json.dumps(mark_fresh(plan_id), ensure_ascii=False, indent=2))


@cmd_governance.command("progress-strip")
@external_or_controller
@click.argument("plan_id")
@click.option("--lines", required=True, help="要处置的行号, 逗号分隔 (例 12,34,56)")
@click.option("--annotate", is_flag=True, help="只标注'以 whatnow 为准'(保留原文); 默认是剥离(整行注释)")
def cmd_progress_strip(plan_id: str, lines: str, annotate: bool) -> None:
    """人一键"剥离/标注"进度漂移行(处置完自动续期)。"""
    from omnicompany.packages.services._governance.progress_steward.freshness import (
        annotate_lines, strip_lines)
    nos = [int(x) for x in lines.replace("，", ",").split(",") if x.strip().isdigit()]
    fn = annotate_lines if annotate else strip_lines
    click.echo(json.dumps(fn(plan_id, nos), ensure_ascii=False, indent=2))


@cmd_governance.command("prose-lang")
@external_or_controller
@click.option("--code", "include_code", is_flag=True, help="也扫 src/**/*.py 注释")
@click.option("--limit", type=int, default=None, help="只扫前 N 个文件(冒烟用)")
@click.option("--model", default=None, help="覆盖模型(默认性价比模型)")
@click.option("--review", "submit_review", is_flag=True, help="把有证据的发现合并成一份审阅材料")
def cmd_prose_lang(include_code: bool, limit: int | None, model: str | None, submit_review: bool) -> None:
    """轨二·里程碑四: 非中文泄漏(中文段里的非白名单英文)→ LLM 判该保留/改中文。只报不改。"""
    from omnicompany.packages.services._governance.prose_steward.lang import run_lang_scan
    payload = run_lang_scan(include_code=include_code, limit=limit, model=model,
                            submit_review=submit_review, echo=click.echo)
    click.echo(json.dumps({k: payload[k] for k in
                           ("scanned_files", "candidate_tokens", "counts", "review_material")
                           if k in payload},
                          ensure_ascii=False, indent=2))


@cmd_governance.command("prose-term")
@any_caller
@click.option("--code", "include_code", is_flag=True, help="也扫 src/**/*.py 注释")
@click.option("--limit", type=int, default=None)
@click.option("--no-gen", is_flag=True, help="不重新生成 Vale/CSpell 配置")
def cmd_prose_term(include_code: bool, limit: int | None, no_gen: bool) -> None:
    """轨二·里程碑五: 术语不一致/代称/易过时(确定性命中) + 从单一真源生成 Vale/CSpell/reject。"""
    from omnicompany.packages.services._governance.prose_steward.term import run_term_scan
    payload = run_term_scan(include_code=include_code, limit=limit, gen_configs=not no_gen, echo=click.echo)
    click.echo(json.dumps({"scanned_files": payload["scanned_files"], "counts": payload["counts"],
                           "lint_generated": payload["lint_generated"]}, ensure_ascii=False, indent=2))


@cmd_governance.command("prose-compress")
@external_or_controller
@click.option("--code", "include_code", is_flag=True)
@click.option("--limit", type=int, default=None)
@click.option("--model", default=None)
@click.option("--no-llm", is_flag=True, help="只做确定性(缩写命中+可疑段筛), 不调 LLM")
def cmd_prose_compress(include_code: bool, limit: int | None, model: str | None, no_llm: bool) -> None:
    """轨二·里程碑六: 惜字如金(确定性展开已知缩写+筛可疑段, LLM 只建议)。"""
    from omnicompany.packages.services._governance.prose_steward.compress import run_compress_scan
    payload = run_compress_scan(include_code=include_code, limit=limit, model=model,
                                llm_judge=not no_llm, echo=click.echo)
    click.echo(json.dumps({"scanned_files": payload["scanned_files"], "counts": payload["counts"]},
                          ensure_ascii=False, indent=2))


@cmd_governance.command("suppress")
@external_or_controller
@click.argument("facility", type=click.Choice(["progress_steward", "prose_steward"]))
@click.option("--add", "add_key", default=None, help="加抑制 key(形如 doc:line:state 或 token:<x>)")
@click.option("--remove", "rm_key", default=None, help="移除抑制 key")
@click.option("--note", default="", help="抑制原因")
def cmd_suppress(facility: str, add_key: str | None, rm_key: str | None, note: str) -> None:
    """语义空间健康治理 · 抑制名单(人已判可接受的点不再报, 防定时刷旧噪声)。"""
    from omnicompany.packages.services._governance.health_suppress import (
        add_suppression, remove_suppression, load_suppressions)
    if add_key:
        click.echo(json.dumps(add_suppression(facility, add_key, note), ensure_ascii=False, indent=2))
    elif rm_key:
        click.echo(json.dumps(remove_suppression(facility, rm_key), ensure_ascii=False, indent=2))
    else:
        click.echo(json.dumps(load_suppressions(facility), ensure_ascii=False, indent=2))


@cmd_governance.command("progress-benchmark")
@external_or_controller
@click.option("--model", default=None, help="被测性价比模型(默认)")
def cmd_progress_benchmark(model: str | None) -> None:
    """金标 benchmark: 性价比模型 vs 人手标的进度三态金标, 报一致率(证据列表不打分)。"""
    from omnicompany.packages.services._governance.health_benchmark import run_progress_benchmark
    payload = run_progress_benchmark(model=model, echo=click.echo)
    click.echo(json.dumps({"agreement": payload["agreement"], "model": payload["model"]},
                          ensure_ascii=False, indent=2))


@cmd_governance.command("plans-status")
@any_caller
def cmd_plans_status() -> None:
    """覆盖表摘要(不调模型)。"""
    from omnicompany.packages.services._governance.plan_steward import governance_summary
    click.echo(json.dumps(governance_summary(), ensure_ascii=False, indent=2))


@cmd_governance.command("plans-benchmark")
@any_caller
@click.option("--apply", "apply_", is_flag=True, help="把金标签持久化进覆盖表(立即生效)")
def cmd_plans_benchmark(apply_: bool) -> None:
    """便宜模型 vs 金标签一致率(金标签=主力模型/人亲读内容后的判定, benchmark.json)。"""
    from omnicompany.packages.services._governance.plan_steward.steward import benchmark_report
    click.echo(json.dumps(benchmark_report(apply=apply_), ensure_ascii=False, indent=2))


@cmd_governance.command("history-run")
@external_or_controller
@click.option("--days", type=int, default=45, show_default=True)
@click.option("--source", type=click.Choice(["all", "claude", "codex"]), default="all", show_default=True)
@click.option("--model", default=None, show_default=f"{DEFAULT_STRUCTURED_MODEL_ENV} or {DEFAULT_STRUCTURED_MODEL}")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--limit-chunks", type=int, default=None, help="只跑前 N 块(冒烟用)")
@click.option("--from-signals", is_flag=True, help="从上次落盘的信号续跑(跳过最贵的 map 段)")
def cmd_history_run(days: int, source: str, model: str | None, workers: int,
                    limit_chunks: int | None, from_signals: bool) -> None:
    """工作历史整理: 用户消息 → 重复需求 / 重复指正 → data/governance/work_history/。"""
    run_mining = _optional_service("omnicompany.packages.services._governance.work_history").run_mining
    summary = run_mining(days=days, source=source, model=model, workers=workers,
                         limit_chunks=limit_chunks, from_signals=from_signals, echo=click.echo)
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@cmd_governance.command("history-assign")
@external_or_controller
@click.option("--model", default=None, show_default="OMNI_STRUCTURED_ASSIGN_MODEL or glm-5.1")
def cmd_history_assign(model: str | None) -> None:
    """把最近一次 findings 的重复需求/指正分配到注册项目(项目详情页「历史证据」消费)。"""
    assign_projects = _optional_service("omnicompany.packages.services._governance.work_history.miner").assign_projects
    click.echo(json.dumps(assign_projects(model=model, echo=click.echo), ensure_ascii=False, indent=2))


@cmd_governance.command("history-report")
@any_caller
def cmd_history_report() -> None:
    """打印最近一次工作历史整理报告。"""
    out_dir = _optional_service("omnicompany.packages.services._governance.work_history.miner").out_dir
    ptr = out_dir() / "latest.json"
    if not ptr.is_file():
        click.echo("还没跑过 history-run。")
        raise SystemExit(1)
    meta = json.loads(ptr.read_text(encoding="utf-8"))
    click.echo((out_dir() / meta["report"]).read_text(encoding="utf-8"))


@cmd_governance.command("actions-check")
@any_caller
@click.option("--json-output", is_flag=True)
def cmd_actions_check(json_output: bool) -> None:
    """各项目 PROJECT_INDEX 的 quick_actions 体检: 绑定的 skill 是否真实存在(确定性, 不调模型)。"""
    from omnicompany.packages.services._governance.project_index_steward import compute_actions_check
    res = compute_actions_check()
    rows = res["actions"]
    if json_output:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    bad = [r for r in rows if r["skill"] and not r["skill_exists"]]
    none_ = [r for r in rows if not r["skill"]]
    click.echo(f"quick_actions 共 {len(rows)} 条; 绑定不存在 skill 的 {len(bad)} 条; 未绑定 skill 的 {len(none_)} 条")
    for r in bad:
        click.echo(f"  [虚构skill] {r['project']}: {r['label']} → /{r['skill']}")
    for r in none_:
        click.echo(f"  [待建技能] {r['project']}: {r['label']}")


@cmd_governance.command("project-index-check")
@any_caller
@click.option("--apply", "do_apply", is_flag=True, help="真自动修死链 + 全绿项目盖 last_verified 戳")
@click.option("--json-output", is_flag=True)
def cmd_project_index_check(do_apply: bool, json_output: bool) -> None:
    """项目索引每日确定性体检: 缺失/契约/死链(含 frontmatter 路径字段)/quick_actions/新鲜度戳。"""
    from omnicompany.packages.services._governance.project_index_steward import run_check
    res = run_check(apply=do_apply)
    if json_output:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    c = res["counts"]
    click.echo(f"缺失索引 {c['missing_index']} · 契约违规 {c['contract_violation']} · "
               f"死链 {c['broken_ref']} · 字段死路径 {c['broken_field']} · quick_action违规 {c['quick_action']}")
    for f in res["findings"]:
        click.echo(f"  [{f['kind']}] {f['project']}: {f['detail']}")
    if do_apply:
        rp = res.get("repairs_applied", {})
        click.echo(f"\n已修: 正文链接 {rp.get('ref_repair', {}).get('links_changed', 0)} 条 / "
                   f"yaml字段 {rp.get('yaml_repair', {}).get('fields_changed', 0)} 条; "
                   f"盖新鲜度戳 {len(res.get('stamped_fresh', []))} 个: {', '.join(res.get('stamped_fresh', [])) or '无'}")
    click.echo(f"\n产物: {res.get('_written')}")


@cmd_governance.command("project-index-review")
@external_or_controller
@click.argument("project", required=False)
@click.option("--model", default="gpt-5.5", show_default=True)
@click.option("--json-output", is_flag=True)
def cmd_project_index_review(project: str | None, model: str, json_output: bool) -> None:
    """项目索引语义补漏: 找新资产未进 index / 权威已迁移, 写进 index 正文固定候选区(去重追加)。"""
    from omnicompany.packages.services._governance.project_index_steward import run_review
    res = run_review(project_id=project, model=model)
    if json_output:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if res.get("error"):
        click.echo(res["error"])
        raise SystemExit(1)
    for r in res["results"]:
        if not r["ok"]:
            click.echo(f"{r['project']}: ✗ {r['error']}")
            continue
        click.echo(f"{r['project']}: 候选 {r['candidates_found']} 条, 新增 {r['candidates_added']} 条")
        for ln in r["added_lines"]:
            click.echo(f"  {ln}")


@cmd_governance.command("docs-refs")
@any_caller
@click.option("--json-output", is_flag=True)
def cmd_docs_refs(json_output: bool) -> None:
    """文档引用完整性体检(确定性, 不调模型): 扫规范/计划/报告里指向已不存在文件的链接/行锚。"""
    from omnicompany.packages.services._governance.doc_steward import run_reference_audit
    res = run_reference_audit(write=True)
    if json_output:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    click.echo(f"扫描 {res['scanned_docs']} 篇; 断链 {res['counts']['broken_ref']} / 失效行锚 {res['counts']['broken_anchor']}")
    for f in res["findings"][:40]:
        click.echo(f"  [{f['category']}] {f['doc']} → {f['target']}")
    if len(res["findings"]) > 40:
        click.echo(f"  … 还有 {len(res['findings']) - 40} 条, 见 {res.get('_written')}")


@cmd_governance.command("docs-refs-fix")
@any_caller
@click.option("--apply", "do_apply", is_flag=True, help="真写回文档(默认 dry-run 只报告)")
@click.option("--json-output", is_flag=True)
def cmd_docs_refs_fix(do_apply: bool, json_output: bool) -> None:
    """把断链重指到目标在仓内的当前真实位置(确定性: basename + 最长路径后缀匹配)。默认 dry-run。"""
    from omnicompany.packages.services._governance.doc_steward.steward import apply_repairs, plan_repairs
    plan = plan_repairs()
    if json_output:
        click.echo(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        click.echo(f"可修(唯一候选) {len(plan['fixes'])} · 有歧义(多候选) {len(plan['ambiguous'])} · 无解(真删/改名) {len(plan['unfixable'])}")
        for fx in plan["fixes"][:25]:
            click.echo(f"  {fx['doc']}\n      {fx['old']}\n   →  {fx['new']}")
        if len(plan["fixes"]) > 25:
            click.echo(f"  … 还有 {len(plan['fixes']) - 25} 条可修")
    if do_apply:
        res = apply_repairs(plan)
        click.echo(f"\n✓ 已写回: {res['docs_changed']} 篇文档 / {res['links_changed']} 条链接。重跑 docs-refs 核对。")
    elif not json_output:
        click.echo("\n(dry-run; 加 --apply 真写回)")


@cmd_governance.command("docs-timeliness")
@any_caller
@click.option("--model", default=None, help="覆盖默认性价比模型")
@click.option("--kind", default="standard", type=click.Choice(["standard", "plan", "report"]))
@click.option("--limit", type=int, default=None)
@click.option("--workers", type=int, default=4)
def cmd_docs_timeliness(model: str | None, kind: str, limit: int | None, workers: int) -> None:
    """文档时效性语义治理(性价比模型为主): 判规范是否过期/被取代/冲突/另立权威。"""
    from omnicompany.packages.services._governance.doc_steward import run_timeliness
    res = run_timeliness(kinds=(kind,), model=model, limit=limit, workers=workers, echo=click.echo)
    click.echo(f"扫描 {res['scanned_docs']} 篇(失败 {res['failed_docs']}); 时效性 findings {len(res['findings'])} 条")
    for f in res["findings"][:40]:
        click.echo(f"  [{f['category']}] {f['doc']}: {f['detail']}")
    click.echo(f"产物: {res.get('_written')}")


@cmd_governance.command("docs-report")
@any_caller
def cmd_docs_report() -> None:
    """打印最近一次文档治理摘要(引用审计 + 时效性)。"""
    from omnicompany.packages.services._governance.doc_steward import latest_findings
    data = latest_findings()
    ref = data.get("reference_audit")
    tl = data.get("timeliness")
    if ref:
        click.echo(f"引用审计({ref['generated_at']}): {ref['scanned_docs']} 篇, 断链 {len(ref['findings'])} 条")
    else:
        click.echo("还没跑过 docs-refs。")
    if tl:
        click.echo(f"时效性({tl['generated_at']}, 模型 {tl['model']}): {tl['scanned_docs']} 篇, findings {len(tl['findings'])} 条")
    else:
        click.echo("还没跑过 docs-timeliness。")


@cmd_governance.command("commit-run")
@external_or_controller
@click.option("--model", default=None, help="覆盖默认性价比模型")
@click.option("--apply", "apply_", is_flag=True, help="真提交(默认 dry-run 只出批次计划)")
@click.option("--workers", type=int, default=4)
@click.option("--max-files", "max_files", type=int, default=None,
              help=f"每轮只处理前 N 个文件(也读 {MAX_FILES_ENV}; 积压期分批稳步清, 不设限省略)")
def cmd_commit_run(model: str | None, apply_: bool, workers: int, max_files: int | None) -> None:
    """性价比模型严格分批 git 提交: 低重复明文必读、禁盲目全量、逐批显式 add+commit。

    默认 dry-run 只出批次计划供抽查; 加 --apply 才真提交(pre-commit 卫士逐批兜底)。
    积压上千文件时务必给 --max-files: 一轮只处理前 N 个并完整跑完, 判过留工作区的
    进暂缓名单下轮跳过, 让积压每天稳步下降而不是每次全量 map 撞超时被掐。
    """
    from omnicompany.packages.services._governance.commit_steward import run_commit
    res = run_commit(model=model, dry_run=not apply_, workers=workers, max_files=max_files,
                     echo=click.echo)
    if res.get("changes") == 0:
        click.echo(res.get("message", "工作区干净"))
        return
    backlog = f", 积压余 {res['backlog_remaining']}" if res.get("backlog_remaining") else ""
    click.echo(f"改动 {res['changes']} 文件 → {res['batches']} 批"
               f"(map 失败 {res['map_failed']}{backlog}); {'真提交' if apply_ else 'DRY-RUN 计划'}")
    for b in res["plan"]:
        click.echo(f"\n■ {b['subject']}  ({len(b['files'])} 文件)")
        if b.get("body"):
            click.echo("  " + b["body"].replace("\n", "\n  "))
    if res.get("uncommitted_left"):
        click.echo(f"\n留工作区未提交(读不到/判不准) {len(res['uncommitted_left'])} 个:")
        for f in res["uncommitted_left"][:20]:
            click.echo(f"  - {f}")
    if apply_:
        ok = sum(1 for a in res["applied"] if a.get("committed"))
        click.echo(f"\n已提交 {ok}/{len(res['applied'])} 批; 计划见 {res.get('_written')}")
    else:
        click.echo(f"\n计划落盘: {res.get('_written')}  (确认无误后加 --apply 真提交)")


@cmd_governance.command("decisions-run")
@external_or_controller
@click.option("--model", default=None, help="覆盖默认性价比模型(默认 qwen3.6-plus)")
@click.option("--reextract", is_flag=True, help="重提全部(默认只提新增/失败的 llm_input 札记)")
@click.option("--no-collect", is_flag=True, help="跳过长 prompt 自动采集步, 只跑既有炼化")
@click.option("--collect-window-days", type=int, default=7, show_default=True,
             help="采集步: 只收最近 N 天用户消息(洪水闸, 见批3开工锚)")
@click.option("--collect-daily-cap", type=int, default=30, show_default=True,
             help="采集步: 单日采集上限, 超限收最长的+余量写待收清单")
def cmd_decisions_run(model: str | None, reextract: bool, no_collect: bool,
                      collect_window_days: int, collect_daily_cap: int) -> None:
    """决策提取: (采集步: 长 prompt→llm_input 札记) + 标记 llm_input 的札记 → 结构化决策
    → 统一决策库 data/domains/decisions/library/records.jsonl(M2 起唯一落点;旧 json 已归档)。

    采集步(批3折进本管线, 不新起): 扫 claude/codex 用户消息, 长度达标的自动补成 llm_input
    札记(extra.source=auto-collected), 幂等不重复; 洪水闸=首跑只收最近 7 天、单日上限 30
    条(超限收最长的, 余量写 data/governance/decisions/collect_waitlist.jsonl)。
    炼化侧(extract_decisions)零改动 —— 采集只是上游补充数据源。

    手动 = 直接跑此 verb; 定期 = scheduler 的 gov-decisions-daily 每日调同函数。
    产物经 cockpit_workflow 的 ctx_summary.decisions 段进总控首轮上下文。
    """
    from omnicompany.core.config import omni_workspace_root
    from omnicompany.dashboard.boss_sight.authored.extract import extract_decisions

    collect_res = None
    if not no_collect:
        from omnicompany.dashboard.boss_sight.authored.collect import collect_long_prompts
        waitlist_path = omni_workspace_root() / "data" / "governance" / "decisions" / "collect_waitlist.jsonl"
        collect_res = collect_long_prompts(
            window_days=collect_window_days, daily_cap=collect_daily_cap,
            waitlist_path=waitlist_path,
        )

    kw: dict = {"reextract": reextract}
    if model:
        kw["model"] = model
    res = extract_decisions(**kw)
    if collect_res is not None:
        res = {"collect": collect_res, **res}
    click.echo(json.dumps(res, ensure_ascii=False, indent=2))


@cmd_governance.command("resume-run")
@external_or_controller
@click.option("--model", default=None, show_default=f"{DEFAULT_STRUCTURED_MODEL_ENV} or {DEFAULT_STRUCTURED_MODEL}")
@click.option("--sources", default="p4,git", show_default=True, help="逗号分隔: p4,git,meego,lark")
@click.option("--p4-limit", type=int, default=None, help="P4 changelist 上限(冒烟用)")
@click.option("--git-limit-per-repo", type=int, default=None, help="每个 git 仓 commit 上限")
@click.option("--p4-since", default=None, help="P4 起始日期, 形如 2026/01/01")
@click.option("--stage-tag", default="full", show_default=True, help="staging 文件标签")
@click.option("--workers", type=int, default=4, show_default=True)
@click.option("--dry-run", is_flag=True, help="不落 findings/run 文件")
def cmd_resume_run(model: str | None, sources: str, p4_limit: int | None,
                   git_limit_per_repo: int | None, p4_since: str | None,
                   stage_tag: str, workers: int, dry_run: bool) -> None:
    """简历资料库: 多源采集 → 归属闸 → 泛化摘要 → 能力矩阵+成就时间线。"""
    run_resume = _optional_service("omnicompany.packages.services._governance.resume_steward").run_resume
    srcs = tuple(s.strip() for s in sources.split(",") if s.strip())
    res = run_resume(sources=srcs, model=model, p4_limit=p4_limit,
                     git_limit_per_repo=git_limit_per_repo, p4_since=p4_since,
                     stage_tag=stage_tag, workers=workers, dry_run=dry_run, echo=click.echo)
    click.echo(json.dumps(res, ensure_ascii=False, indent=2))


@cmd_governance.command("resume-gold")
@external_or_controller
@click.option("--source-tag", default="smoke", show_default=True, help="benchmark 样本的 staging 标签")
@click.option("--model", default=None, show_default="OMNI_RESUME_BASELINE_MODEL or claude-sonnet-4-6")
@click.option("--workers", type=int, default=3, show_default=True)
def cmd_resume_gold(source_tag: str, model: str | None, workers: int) -> None:
    """基准模型亲读样本产金标(benchmark.json), 权威高于便宜模型。"""
    produce_gold = _optional_service("omnicompany.packages.services._governance.resume_steward").produce_gold
    res = produce_gold(source_tag=source_tag, model=model, workers=workers, echo=click.echo)
    click.echo(json.dumps(res, ensure_ascii=False, indent=2))


@cmd_governance.command("resume-benchmark")
@any_caller
@click.option("--source-tag", default="smoke", show_default=True)
@click.option("--cheap-model", default=None, show_default=f"{DEFAULT_STRUCTURED_MODEL_ENV} or {DEFAULT_STRUCTURED_MODEL}")
@click.option("--judge-model", default=None, show_default="OMNI_RESUME_BASELINE_MODEL or claude-sonnet-4-6")
@click.option("--workers", type=int, default=3, show_default=True)
def cmd_resume_benchmark(source_tag: str, cheap_model: str | None,
                         judge_model: str | None, workers: int) -> None:
    """便宜模型 vs 金标一致率(attribution 精确 + 能力重叠 + 摘要基准裁判语义等价)。"""
    benchmark_report = _optional_service("omnicompany.packages.services._governance.resume_steward").benchmark_report
    res = benchmark_report(source_tag=source_tag, cheap_model=cheap_model,
                           judge_model=judge_model, workers=workers, echo=click.echo)
    click.echo(json.dumps(res, ensure_ascii=False, indent=2))


@cmd_governance.command("resume-reduce")
@external_or_controller
@click.option("--stage-tag", default="all", show_default=True, help="从该 staging 的 MAP 缓存重算")
def cmd_resume_reduce(stage_tag: str) -> None:
    """从 MAP 缓存重算 REDUCE → findings(迭代聚合/合并逻辑而不重跑昂贵的 MAP)。"""
    rebuild_findings = _optional_service("omnicompany.packages.services._governance.resume_steward").rebuild_findings
    res = rebuild_findings(stage_tag=stage_tag, echo=click.echo)
    click.echo(json.dumps(res, ensure_ascii=False, indent=2))


@cmd_governance.command("resume-report")
@any_caller
def cmd_resume_report() -> None:
    """打印最近一次简历资料库的能力矩阵 + 成就时间线摘要。"""
    latest = _optional_service("omnicompany.packages.services._governance.resume_steward").latest
    data = latest()
    if not data:
        click.echo("还没跑过 resume-run。")
        raise SystemExit(1)
    click.echo(f"能力 {len(data.get('capabilities') or [])} 项, 成就 {len(data.get('accomplishments') or [])} 条"
               f"(本人 {data.get('mine')}/{data.get('units')}, 待复核 {data.get('by_attribution', {}).get('review_needed', 0)})")
    for c in (data.get("capabilities") or [])[:20]:
        click.echo(f"  [能力] {c.get('name')} ×{c.get('evidence_count')} {c.get('sources')}")
    for a in (data.get("accomplishments") or [])[:20]:
        click.echo(f"  [成就] {a.get('title')} ({a.get('timespan')}) — {a.get('summary')}")


@cmd_governance.command("job-run")
@any_caller
@click.option("--model", default=None, show_default="qwen3.6-plus")
@click.option("--workers", type=int, default=12, show_default=True)
def cmd_job_run(model: str | None, workers: int) -> None:
    """求职 Phase 0: 大厂官网公开 API 抓岗 → 按画像匹配排序 → job applications可投清单。"""
    run_discovery = _optional_service("omnicompany.packages.services._governance.job_steward").run_discovery
    res = run_discovery(model=model, workers=workers, echo=click.echo)
    click.echo(json.dumps(res, ensure_ascii=False, indent=2))


# 治理管线目录: 唯一可枚举面 (agent/总控一条命令即知有哪些治理管线、该不该跑)
_GOVERNANCE_CATALOG = [
    {"verb": "plans-run", "what": "计划→项目归属 + 中文标题 + 格式检查", "cadence": "每日(--only-missing)", "kind": "语义"},
    {"verb": "plans-sync", "what": "计划→whatnow 同步: 新计划自动建 task + 进度过时则重评估刷新", "cadence": "每日", "kind": "语义"},
    {"verb": "progress-scan", "what": "轨一: 确定性圈出 plan.md/注释里的进度型自述候选(进度归 whatnow)", "cadence": "每日", "kind": "确定性"},
    {"verb": "progress-ssot", "what": "计划进度唯一真源: 对照 whatnow 报本地状态副本/冲突→合并审阅材料", "cadence": "每日(plans-sync --audit)", "kind": "确定性"},
    {"verb": "progress-review", "what": "轨一: 进度型候选三态精判(漂移/决策/误报)→ 合并审阅材料", "cadence": "每周", "kind": "语义"},
    {"verb": "prose-lang", "what": "轨二: 非中文泄漏(中文段里非白名单英文)→ LLM 判保留/改中文", "cadence": "每周", "kind": "语义"},
    {"verb": "prose-term", "what": "轨二: 术语不一致/代称/易过时 + 从单一真源生成 Vale/CSpell", "cadence": "每周", "kind": "确定性"},
    {"verb": "prose-compress", "what": "轨二: 惜字如金(缩写展开 + 可疑段 LLM 建议)", "cadence": "每周", "kind": "语义"},
    {"verb": "history-run", "what": "对话里重复需求/指正提取", "cadence": "每周", "kind": "语义"},
    {"verb": "docs-refs", "what": "文档引用完整性(断链/失效行锚)", "cadence": "每日", "kind": "确定性"},
    {"verb": "docs-timeliness", "what": "规范/计划/报告时效性(过期/被取代/冲突)", "cadence": "每周", "kind": "语义"},
    {"verb": "commit-run", "what": "性价比模型严格分批 git 提交", "cadence": "定时/大改后", "kind": "语义+确定性"},
    {"verb": "decisions-run", "what": "标记 llm_input 的札记 → 结构化决策(进总控 ctx)", "cadence": "每日", "kind": "语义"},
    {"verb": "actions-check", "what": "PROJECT_INDEX quick_actions 的 skill 存在性体检", "cadence": "按需", "kind": "确定性"},
    {"verb": "project-index-check", "what": "项目索引五项体检(缺失/契约/死链/quick_actions/新鲜度戳)", "cadence": "每日(--apply)", "kind": "确定性"},
    {"verb": "project-index-review", "what": "项目索引语义补漏(新资产未进 index/权威已迁移)→ 正文候选区", "cadence": "每周+按工作量", "kind": "语义"},
    {"verb": "resume-run", "what": "多源(P4/git/Meego/Lark)采集 → 归属+泛化摘要 → 简历资料库", "cadence": "按需", "kind": "语义"},
    {"verb": "job-run", "what": "大厂官网公开API抓岗 → 按画像匹配 → job applications可投清单(Phase 0)", "cadence": "按需/每日", "kind": "语义"},
]


@cmd_governance.command("catalog")
@any_caller
@click.option("--json-output", is_flag=True)
def cmd_catalog(json_output: bool) -> None:
    """列出所有治理管线 + 档期 + 上次跑时间(agent/总控发现可用治理操作的唯一面)。"""
    import json as _json
    from pathlib import Path

    from omnicompany.core.config import omni_workspace_root
    gov = omni_workspace_root() / "data" / "governance"
    last_runs = {
        "plans-run": gov / "plan_steward",
        "plans-sync": omni_workspace_root() / "data" / "services" / "whatnow" / "whatnow.json",
        "progress-scan": gov / "progress_steward" / "progress_scan.json",
        "progress-ssot": gov / "progress_steward" / "progress_ssot-latest.json",
        "progress-review": gov / "progress_steward" / "progress_review-latest.json",
        "prose-lang": gov / "prose_steward" / "prose_lang-latest.json",
        "prose-term": gov / "prose_steward" / "prose_term-latest.json",
        "prose-compress": gov / "prose_steward" / "prose_compress-latest.json",
        "history-run": gov / "work_history" / "latest.json",
        "docs-refs": gov / "doc_steward" / "reference_audit.json",
        "docs-timeliness": gov / "doc_steward" / "timeliness-latest.json",
        "commit-run": gov / "commit_steward" / "commit_last.json",
        "decisions-run": gov / "decisions" / "extract_last.json",
        "resume-run": gov / "resume_steward" / "latest.json",
    }
    rows = []
    for item in _GOVERNANCE_CATALOG:
        ptr = last_runs.get(item["verb"])
        last = ""
        if isinstance(ptr, Path) and ptr.exists():
            try:
                last = datetime.fromtimestamp(ptr.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                last = "?"
        rows.append({**item, "last_run": last or "未跑过"})
    if json_output:
        click.echo(_json.dumps(rows, ensure_ascii=False, indent=2))
        return
    click.echo("治理管线目录 (omni governance <verb>):")
    for r in rows:
        click.echo(f"  {r['verb']:<16} [{r['kind']:<8}] {r['what']}")
        click.echo(f"  {'':<16} 档期 {r['cadence']} · 上次 {r['last_run']}")


@cmd_governance.command("cron-tick")
@external_or_controller
@click.option("--dry-run", is_flag=True, help="只列到期任务不执行")
@click.option("--ensure", is_flag=True, help="先补建标准治理 cron 任务再跑")
def cmd_cron_tick(dry_run: bool, ensure: bool) -> None:
    """跑一遍治理定时任务(由 OS cron/sentinel 每隔几分钟调一次, 分发到期的治理管线)。"""
    from omnicompany.packages.services._governance.scheduler import ensure_governance_tasks, tick
    if ensure:
        created = ensure_governance_tasks()
        if created:
            click.echo(f"补建治理 cron 任务: {', '.join(created)}")
    res = tick(dry_run=dry_run)
    if not res["ran"]:
        click.echo("无到期治理任务。")
        return
    click.echo(f"到期任务 {res['due_count']} 个 ({'DRY-RUN' if dry_run else '已执行'}):")
    for r in res["ran"]:
        mark = "would-run" if r.get("would_run") else ("ok" if r.get("ran") else r.get("skipped") or r.get("error") or "?")
        click.echo(f"  {r['name']}: {r['command']}  → {mark}")


@cmd_governance.command("cron-list")
@any_caller
def cmd_cron_list() -> None:
    """列出治理定时任务及其下次是否到期。"""
    from omnicompany.packages.services._governance.scheduler import is_due, load_tasks
    tasks = load_tasks()
    if not tasks:
        click.echo("还没有 cron 任务(omni governance cron-tick --ensure 可补建标准治理任务)。")
        return
    for t in tasks:
        due = "★到期" if is_due(t) else "已跑过"
        click.echo(f"  {t.get('name'):<26} {t.get('schedule','?'):<10} {due}  上次 {t.get('last_run_at') or '从未'}")
        click.echo(f"  {'':<26} {t.get('command') or t.get('description','')}")

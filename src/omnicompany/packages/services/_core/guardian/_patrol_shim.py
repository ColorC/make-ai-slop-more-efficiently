# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.patrol_shim.pipeline_orchestrator.py"
"""Guardian Patrol 新入口 (Worker 架构) + 向后兼容 shim.

提供两个函数:
  - run_guardian(scan_request)  : 新入口, 接受 material 形式参数
  - run_patrol(...)              : 向后兼容 shim, 参数同原 patrol_runner.run_patrol

内部只串真实 patrol worker:
  GitDiffScan → RuleEngine

LLM 综合判定职能并入 doctor _hypothesis/. guardian 留纯规则.
needs_judgment 候选 (规则判不准的) 直接当确认违规处理 (保守报); 调用方 use_agent=True 时
仍走 GuardianAgent 复核 (judge_agent.py 留, 跟 worker 砍的范围分开).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from omnicompany.core.config import omni_workspace_root

# 兼容 re-export: 外部测试 / 旧代码 `from ...guardian import RuleEngine, Violation, FileContext, RULES`
from .rules import FileContext, GuardianRule, Violation, parse_omnimark, RULES  # noqa: F401

from .workers import (
    GitDiffScanWorker,
    RuleEngineWorker,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# RuleEngine 兼容类 (原 patrol.py::RuleEngine, 供测试和外部代码兼容使用)
# ══════════════════════════════════════════════════════════════════════


class RuleEngine:
    """对 FileContext 列表运行所有规则, 产出 Violation 列表。

    保留作为向后兼容 API (测试 / sentinel / tow_truck / auto_comment 等依赖)。
    逻辑内联自原 `patrol.py::RuleEngine` (已归档到 `_archive/patrol_legacy.py`)。

    Phase 1 设计原则:
    - 纯计算, 不触及文件系统
    - 每条规则独立执行, 单条异常不影响其他规则
    - 违规计数全局唯一, 便于跨 scan 追踪
    """

    def __init__(self, rules: list[GuardianRule] = RULES):
        self._rules = rules
        self._counter = 0

    def evaluate(self, files: list[FileContext]) -> list[Violation]:
        """运行所有规则, 返回确认违规列表 (向后兼容)。"""
        result = self.evaluate_split(files)
        return result["confirmed"] + result["needs_judgment"]

    def evaluate_split(self, files: list[FileContext]) -> dict[str, list[Violation]]:
        """运行所有规则, 按 certainty 分流返回。"""
        now = datetime.now(timezone.utc).isoformat()
        date_str = now[:10]
        confirmed: list[Violation] = []
        needs_judgment: list[Violation] = []

        for ctx in files:
            for rule in self._rules:
                try:
                    if rule.check(ctx):
                        self._counter += 1
                        ticket_id = f"TICKET-{date_str}-{self._counter:03d}"
                        msg = rule.message_template.format(path=ctx.path)
                        v = Violation(
                            ticket_id=ticket_id, rule_id=rule.id,
                            severity=rule.severity, path=ctx.path,
                            message=msg, disposition=rule.disposition,
                            confidence=1.0, detected_at=now,
                        )
                        if rule.certainty == "needs_judgment":
                            needs_judgment.append(v)
                        else:
                            confirmed.append(v)
                except Exception as e:
                    logger.debug("Rule %s failed on %s: %s", rule.id, ctx.path, e)

        return {"confirmed": confirmed, "needs_judgment": needs_judgment}

# ── patrol 非阻塞锁(2026-07-04 密度分层批1) ─────────────────────────────
# patrol 提到心跳级(@every15m)后, 会话手动跑与 tick 顺带跑可能并发, 存在无锁并发
# 写注册表/日志的窗口。这是"跳过锁"不是"排队锁"——巡逻是幂等增量活, 跳过一轮无损。
_PATROL_LOCK_STALE_S = 900  # 15min 视为僵死(单次 patrol 不该跑这么久)


def _patrol_lock_path(project_root: str | Path) -> Path:
    d = Path(project_root) / ".omni" / "guardian"
    d.mkdir(parents=True, exist_ok=True)
    return d / ".patrol.lock"


def _acquire_patrol_lock(project_root: str | Path) -> bool:
    """非阻塞获取 patrol 锁; 已有新鲜锁(<15min)返回 False(跳过本次)。"""
    p = _patrol_lock_path(project_root)
    if p.exists():
        try:
            held = json.loads(p.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(held["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - started).total_seconds() < _PATROL_LOCK_STALE_S:
                return False  # 有活 patrol 在跑
        except Exception:  # noqa: BLE001
            pass  # 锁损坏/过期 → 接管
    p.write_text(json.dumps({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()},
                            ensure_ascii=False), encoding="utf-8")
    return True


def _release_patrol_lock(project_root: str | Path) -> None:
    try:
        _patrol_lock_path(project_root).unlink()
    except OSError:
        pass


def _count_by_severity(violations: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for violation in violations:
        severity = str(violation.get("severity") or "UNKNOWN")
        counts[severity] = counts.get(severity, 0) + 1
    return counts


# ── 结构化落盘(2026-07-04 修: 恢复提醒链路的断头) ──────────────────────────
# _governance/testmap.py::collect_reminders 读 `logs/patrol/*.json` 的 OMNI-100
# violations 做"改源码没更新台账"提醒; 2026-05-05 诊断重制后本模块只调
# sync_patrol_result_to_registry, 从未写过这个目录, 提醒链路生产上是断的。
# 这里补写一份, 字段形态照 collect_reminders 的读法对齐: 顶层 scan_ts/scan_mode,
# violations[] 保留 rule_id/path/message/detected_at 等键(Violation dataclass 原样)。
def _patrol_log_dir(project_root: str | Path) -> Path:
    return Path(project_root) / "logs" / "patrol"


def _persist_patrol_log(
    *,
    project_root: str | Path,
    scan_ts: str | None,
    scan_mode: str | None,
    violations: list[dict[str, Any]],
) -> None:
    """把本轮巡逻结果写一份 `logs/patrol/patrol-<UTC时间戳>.json`。

    violations 为空不落盘(心跳级每 15 分钟一跑, 全绿不留垃圾)。写失败容错不抛
    (try/except 记 debug), 目录不存在则建。
    """
    if not violations:
        return
    try:
        log_dir = _patrol_log_dir(project_root)
        log_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        safe_ts = now.strftime("%Y%m%dT%H%M%S%f")
        log_path = log_dir / f"patrol-{safe_ts}.json"

        detected_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "scan_ts": scan_ts or detected_at,
            "scan_mode": scan_mode or "diff",
            "violations": [
                {
                    **v,
                    "detected_at": v.get("detected_at") or detected_at,
                }
                for v in violations
            ],
        }
        log_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001 — 落盘失败不阻断巡逻主流程
        logger.debug("run_patrol: 结构化日志落盘失败(非致命): %s", e)


def _summarize_judged_violations(
    *,
    scan_ts: str | None,
    scan_mode: str | None,
    violations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "scan_ts": scan_ts or "",
        "scan_mode": scan_mode or "diff",
        "violations_found": len(violations),
        "by_severity": _count_by_severity(violations),
    }


# ══════════════════════════════════════════════════════════════════════
# 新入口 (material-driven)
# ══════════════════════════════════════════════════════════════════════


def run_guardian(scan_request: dict[str, Any]) -> dict[str, Any]:
    """启动一次 guardian job, 串真实 worker, 返回巡检摘要.

    LLMJudge 已移除 (2026-05-05 诊断重制 step 8) — needs_judgment 候选合并成确认违规.
    """
    w1 = GitDiffScanWorker()
    v1 = w1.run({"guardian.scan_request": scan_request})
    fcs = v1.output

    w2 = RuleEngineWorker()
    v2 = w2.run(fcs)

    # 跳过 LLMJudge — vsj 直接由 vs 构造 (confirmed + needs_judgment 都进 violations)
    vs = v2.output
    vsj = {"violations": vs.get("confirmed", []) + vs.get("needs_judgment", [])}
    violations = vsj["violations"]

    return _summarize_judged_violations(
        scan_ts=fcs.get("scan_ts") or vs.get("scan_ts"),
        scan_mode=fcs.get("scan_mode") or vs.get("scan_mode") or scan_request.get("scan_mode"),
        violations=violations,
    )


# ══════════════════════════════════════════════════════════════════════
# 向后兼容 shim (替代 patrol_runner.run_patrol)
# ══════════════════════════════════════════════════════════════════════


def run_patrol(
    project_root: str | Path | None = None,
    full_scan: bool = False,
    committed: bool = True,
    uncommitted: bool = True,
    n_commits: int = 1,
    use_llm: bool = False,
    llm_new_only: bool = True,
    llm_pilot_paths: tuple[str, ...] | None = None,
    use_agent: bool = False,
    auto_tow: bool = True,
    tow_phase2: bool = False,
    staged_only: bool = False,
    since_ts: Optional[str] = None,
) -> dict:
    """向后兼容 shim(带非阻塞锁): 保留原 patrol_runner.run_patrol 的参数签名和返回结构。

    锁: 心跳级(@every15m)后会话手动跑与 tick 顺带跑可能并发, 本函数入口非阻塞取锁——
    已有新鲜锁(<15min)直接跳过不等待(幂等增量活, 跳过一轮无损); ≥15min 视为僵死接管。
    正常/异常退出都释放(try/finally)。

    project_root 缺省 (None) 时懒解析 omni_workspace_root()。
    """
    if project_root is None:
        project_root = omni_workspace_root()
    if not _acquire_patrol_lock(project_root):
        return {
            "skipped": "patrol already running",
            "scan_ts": None,
            "scan_mode": "full" if full_scan else ("staged" if staged_only else "diff"),
            "files_scanned": 0,
            "violations_found": 0,
            "violations": [],
            "by_severity": {},
        }
    try:
        return _run_patrol_impl(
            project_root=project_root, full_scan=full_scan, committed=committed,
            uncommitted=uncommitted, n_commits=n_commits, use_llm=use_llm,
            llm_new_only=llm_new_only, llm_pilot_paths=llm_pilot_paths,
            use_agent=use_agent, auto_tow=auto_tow, tow_phase2=tow_phase2,
            staged_only=staged_only, since_ts=since_ts,
        )
    finally:
        _release_patrol_lock(project_root)


def _run_patrol_impl(
    project_root: str | Path | None = None,
    full_scan: bool = False,
    committed: bool = True,
    uncommitted: bool = True,
    n_commits: int = 1,
    use_llm: bool = False,
    llm_new_only: bool = True,
    llm_pilot_paths: tuple[str, ...] | None = None,
    use_agent: bool = False,
    auto_tow: bool = True,
    tow_phase2: bool = False,
    staged_only: bool = False,
    since_ts: Optional[str] = None,
) -> dict:
    """原 run_patrol 实现体(内部 delegate 到 run_guardian(scan_request))。

    原 patrol_runner.py 已归档到 _archive/patrol_runner_legacy.py。
    project_root 缺省 (None) 时懒解析 omni_workspace_root()。
    """
    if project_root is None:
        project_root = omni_workspace_root()
    if full_scan:
        scan_mode = "full"
    elif staged_only:
        scan_mode = "staged"
    else:
        scan_mode = "diff"

    req = {
        "scan_mode": scan_mode,
        "project_root": str(project_root),
        "n_commits": n_commits,
        "committed": committed,
        "uncommitted": uncommitted,
        "use_llm": use_llm,
        "use_agent": use_agent,
        "auto_tow": auto_tow,
    }

    # 手动串 4 Worker, 收集中间产物以构造旧 run_patrol() 返回结构
    # Protocol 约定: verdict.output = FORMAT_OUT 对应 Format 的 payload (平铺字段)
    # Worker 间喂数据用 {FORMAT_IN_id: previous.output} 包装
    w1 = GitDiffScanWorker()
    v1 = w1.run({"guardian.scan_request": req})
    fcs = v1.output  # = guardian.file_context_set 的内容

    w2 = RuleEngineWorker()
    v2 = w2.run({"guardian.file_context_set": fcs})
    vs = v2.output  # = guardian.violation_set

    # LLMJudge 已移除 (2026-05-05 诊断重制 step 8) — vsj 直接由 vs 构造
    # (confirmed + needs_judgment 都进 violations 当确认报)
    vsj = {"violations": list(vs.get("confirmed", [])) + list(vs.get("needs_judgment", []))}

    # 2026-04-24: 接通 GuardianAgent 复核 needs_judgment 候选 (use_agent=True 时)
    # 走"合法入口白名单"范式: LLM 读代码判 "是否合法绕过"
    # 分批: max_turns=12 一次只能稳判 5~8 个候选, 大批分组送
    # 接入 GuardianAuditStore: 先查 cache → 未命中才跑 LLM → 结果写 record
    if use_agent and vs.get("needs_judgment"):
        try:
            import asyncio
            from .judge_agent import GuardianAgent
            from .audit_store import (
                GuardianAuditStore, AuditRecord,
                compute_file_sha16, compute_prompt_sha8, compute_rule_version,
            )
            from .rules import RULES as _ALL_RULES

            BATCH_SIZE = 6  # 经验值: max_turns=12 / 候选 ~ 2 turns 探查

            store = GuardianAuditStore(project_root)
            prompt_sha = compute_prompt_sha8(GuardianAgent.SYSTEM_PROMPT)
            rule_version_map = {r.id: compute_rule_version(r) for r in _ALL_RULES}
            batch_tag = f"patrol-{fcs.get('scan_ts', '')}"

            cache_hits: list[dict] = []
            to_review: list[dict] = []
            all_candidates = vs["needs_judgment"]
            for nv in all_candidates:
                rel = nv["path"]
                abs_path = Path(project_root) / rel
                file_sha = compute_file_sha16(abs_path) if abs_path.exists() else ""
                rule_ver = rule_version_map.get(nv["rule_id"], "v1")
                # 查缓存
                hit = store.lookup_latest(
                    target_path=rel,
                    rule_id=nv["rule_id"],
                    file_sha16=file_sha,
                    rule_version=rule_ver,
                    prompt_sha8=prompt_sha,
                )
                if hit is not None:
                    # 缓存命中: 按 hit.verdict 处理
                    cache_hits.append({
                        "path": hit.target_path,
                        "rule_id": hit.rule_id,
                        "verdict": hit.verdict,
                        "confidence": hit.confidence,
                        "reasoning": hit.reasoning,
                        "suggestion": hit.suggestion,
                        "from_cache": True,
                    })
                    continue
                to_review.append({
                    "nv": nv,
                    "file_sha16": file_sha,
                    "rule_version": rule_ver,
                    "prompt_sha8": prompt_sha,
                })

            logger.info(
                "[run_patrol] cache hits=%d · 需 LLM 复核=%d", len(cache_hits), len(to_review),
            )

            all_judgments: list[dict] = list(cache_hits)
            confirmed_count = 0
            new_records: list[AuditRecord] = []

            # confirmed 的 cache_hits 先回填
            for j in cache_hits:
                if j.get("verdict") != "confirmed":
                    continue
                for nv in all_candidates:
                    if nv["path"] == j["path"] and nv["rule_id"] == j["rule_id"]:
                        vsj["violations"].append({
                            **nv,
                            "reviewed_by": "GuardianAgent (cache)",
                            "review_confidence": j.get("confidence", 0.5),
                            "review_reasoning": j.get("reasoning", ""),
                            "review_suggestion": j.get("suggestion", ""),
                        })
                        confirmed_count += 1
                        break

            # 对未命中缓存的送 LLM
            for batch_idx in range(0, len(to_review), BATCH_SIZE):
                batch_items = to_review[batch_idx:batch_idx + BATCH_SIZE]
                batch_payload = [
                    {"path": b["nv"]["path"], "rule_id": b["nv"]["rule_id"],
                     "message": b["nv"]["message"]}
                    for b in batch_items
                ]
                logger.info(
                    "[run_patrol] GuardianAgent 批 %d/%d (%d 候选, 缓存外)",
                    batch_idx // BATCH_SIZE + 1,
                    (len(to_review) + BATCH_SIZE - 1) // BATCH_SIZE,
                    len(batch_items),
                )
                agent = GuardianAgent()
                agent_verdict = asyncio.run(agent.run({
                    "project_root": str(project_root),
                    "candidates": batch_payload,
                }))
                batch_js = agent_verdict.output.get("judgments", [])
                all_judgments.extend(batch_js)

                # 写 audit record + 回填 violations
                for j in batch_js:
                    # 找对应的批元信息
                    match = next(
                        (b for b in batch_items
                         if b["nv"]["path"] == j.get("path")
                         and b["nv"]["rule_id"] == j.get("rule_id")),
                        None,
                    )
                    if match is None:
                        continue
                    new_records.append(AuditRecord(
                        target_path=match["nv"]["path"],
                        file_sha16=match["file_sha16"],
                        rule_id=match["nv"]["rule_id"],
                        rule_version=match["rule_version"],
                        prompt_sha8=match["prompt_sha8"],
                        verdict=j.get("verdict", "uncertain"),
                        confidence=float(j.get("confidence", 0.0)),
                        reasoning=j.get("reasoning", ""),
                        suggestion=j.get("suggestion", ""),
                        source_batch=batch_tag,
                    ))
                    if j.get("verdict") == "confirmed":
                        vsj["violations"].append({
                            **match["nv"],
                            "reviewed_by": "GuardianAgent",
                            "review_confidence": j.get("confidence", 0.5),
                            "review_reasoning": j.get("reasoning", ""),
                            "review_suggestion": j.get("suggestion", ""),
                        })
                        confirmed_count += 1

            # 批量写 records (一次 IO)
            if new_records:
                store.append_many(new_records)
                logger.info(
                    "[run_patrol] 写 GuardianAuditStore: %d new records · path=%s",
                    len(new_records), store.records_path,
                )

            vsj["agent_reviewed"] = len(all_judgments)
            vsj["agent_confirmed"] = confirmed_count
            vsj["agent_judgments"] = all_judgments
            vsj["cache_hits"] = len(cache_hits)
            vsj["new_records"] = len(new_records)
            logger.info(
                "[run_patrol] GuardianAgent %d judgments (cache=%d, new=%d) → %d confirmed",
                len(all_judgments), len(cache_hits), len(new_records), confirmed_count,
            )
        except Exception as e:
            logger.warning("[run_patrol] GuardianAgent 复核失败 (非致命): %s", e, exc_info=True)
            vsj["agent_reviewed"] = 0
            vsj["agent_error"] = str(e)

    # 构造兼容旧 run_patrol() 返回结构
    judged = vsj.get("violations", [])
    result = {
        "scan_ts": fcs.get("scan_ts"),
        "scan_mode": fcs.get("scan_mode"),
        "files_scanned": len(fcs.get("files", [])),
        "violations_found": len(judged),
        "agent_reviewed": vsj.get("agent_reviewed", 0),
        "agent_confirmed": vsj.get("agent_confirmed", 0),
        "violations": judged,
        "by_severity": _count_by_severity(judged),
    }

    # 结构化落盘(2026-07-04 修: 恢复提醒链路的断头, 见 _persist_patrol_log 顶部注释)。
    # 仍在 run_patrol 的锁包装内(本函数只被持锁的 run_patrol 调用)。
    _persist_patrol_log(
        project_root=project_root,
        scan_ts=result["scan_ts"],
        scan_mode=result["scan_mode"],
        violations=judged,
    )

    # 自动同步到 REGISTRY.md §活跃违规 + ARCH-CHANGES.jsonl (2026-04-23 修复:
    # 迁移到 _patrol_shim 时丢了这一步, sentinel 跑完不登记到 REGISTRY).
    try:
        from .registry_updater import sync_patrol_result_to_registry
        sync_summary = sync_patrol_result_to_registry(result, Path(project_root))
        result["registry_sync"] = sync_summary
    except Exception as e:
        logger.warning("run_patrol: REGISTRY 同步失败 (非致命): %s", e)
        result["registry_sync"] = {"error": str(e)}

    return result


# ══════════════════════════════════════════════════════════════════════
# format_patrol_report — 终端报告格式化 (内联自原 patrol_runner.py)
# ══════════════════════════════════════════════════════════════════════

_SEVERITY_COLOR = {
    "CRITICAL": "\033[91m",  # 亮红
    "HIGH":     "\033[91m",  # 亮红
    "MEDIUM":   "\033[93m",  # 黄
    "LOW":      "\033[96m",  # 青
    "INFO":     "\033[96m",  # 青
}
_RESET = "\033[0m"


def format_patrol_report(result: dict, color: bool = True) -> str:
    """将 run_patrol 结果格式化为终端可读字符串 (供 CLI `omni guardian patrol` 使用)。"""
    lines: list[str] = []
    mode_label = "全量扫描" if result.get("scan_mode") == "full" else "差量扫描(git diff)"
    lines.append(f"OmniPatrol 巡逻报告 — {result.get('scan_ts', '')}")
    lines.append(f"  模式: {mode_label}  |  扫描文件: {result['files_scanned']}  |  发现违规: {result['violations_found']}")
    lines.append("")

    if not result["violations"]:
        lines.append("  [OK] 未发现违规")
        return "\n".join(lines)

    by_sev: dict[str, list[dict]] = {}
    for v in result["violations"]:
        by_sev.setdefault(v["severity"], []).append(v)

    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
        group = by_sev.get(sev, [])
        if not group:
            continue
        prefix = _SEVERITY_COLOR.get(sev, "") if color else ""
        suffix = _RESET if color else ""
        lines.append(f"  {prefix}[{sev}]{suffix}  {len(group)} 条")
        for v in group:
            lines.append(f"    {v['ticket_id']}  {v['rule_id']}  {v['path']}")
            lines.append(f"      {v['message']}")
        lines.append("")

    bsev = result["by_severity"]
    summary_parts = []
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        n = bsev.get(sev, 0)
        if n:
            summary_parts.append(f"{sev}:{n}")
    lines.append(f"  汇总: {' | '.join(summary_parts) or '无违规'}")
    return "\n".join(lines)

# [OMNI] origin=claude-code domain=runtime/info_audit/pipeline_health ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.info_audit.pipeline_health_aggregator.implementation.py"
"""pipeline_health — 信息充分性体检钩子 + 运行健康汇聚.

提供三个功能:

1. maybe_probe_baseline(pipeline, domain)
   dispatch() 调用: 仅在 OMNICOMPANY_AUTO_PROBE_BASELINE=1 时做 LLM probe,
   结果缓存 7 天. 默认关闭，避免真实任务启动时发生隐藏模型调用.

2. append_pipeline_health(...)
   runner.run() 末尾调用: 把 post_hoc 审计结果写入
   data/domains/<domain>/pipeline_health.jsonl, 供 Doctor 未来消费.

3. read_pipeline_health(domain, pipeline_id, last_n)
   Doctor / CLI 调用: 读最近 N 次 health 快照.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from omnicompany.core.config import resolve_domain_data_dir

logger = logging.getLogger(__name__)

_BASELINE_TTL_DAYS = 7
_CRITICAL_WARN_THRESHOLD = 2  # 节点 n_critical >= 此值时打 warning


# ────────────────────────────────────────────────────────────────────────────
# 路径辅助
# ────────────────────────────────────────────────────────────────────────────

def _probe_baseline_path(domain: str) -> Path:
    return resolve_domain_data_dir(domain) / "probe_baseline.json"


def _pipeline_health_path(domain: str) -> Path:
    return resolve_domain_data_dir(domain) / "pipeline_health.jsonl"


def _domain_from_pipeline_id(pipeline_id: str) -> str:
    """从 pipeline_id 推断 domain.

    支持多种分隔符:
      'absorption-module-driven'  → 'absorption'
      'absorption.v3'  → 'absorption'
      'mydomain-learn' → 'mydomain'
      'doctor-format'  → 'doctor'
    """
    if not pipeline_id:
        return "unknown"
    import re
    return re.split(r"[-.]", pipeline_id, maxsplit=1)[0]


# ────────────────────────────────────────────────────────────────────────────
# 接入点 1: dispatch → probe 体检钩子
# ────────────────────────────────────────────────────────────────────────────

def maybe_probe_baseline(pipeline: Any, *, domain: str) -> None:
    """dispatch() 里的 probe 体检钩子.

    显式 opt-in 后，首次跑时做 probe baseline, 结果缓存 7 天.
    缓存期内只读缓存并打 warning (若存在 critical 缺口).
    永不抛异常, 永不阻塞主流程.

    Args:
        pipeline: TeamSpec 对象 (有 nodes / id 属性).
        domain:   pipeline 所属 domain (来自 PipelineEntry.domain).
    """
    if os.environ.get("OMNICOMPANY_AUTO_PROBE_BASELINE", "").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logger.debug(
            "[probe] %s: automatic LLM baseline disabled; run it explicitly or set "
            "OMNICOMPANY_AUTO_PROBE_BASELINE=1",
            getattr(pipeline, "id", "?"),
        )
        return

    try:
        baseline_path = _probe_baseline_path(domain)
        existing: dict[str, Any] | None = None

        # 检查缓存是否有效 (7 天 TTL)
        if baseline_path.exists():
            age_days = (time.time() - baseline_path.stat().st_mtime) / 86400
            if age_days < _BASELINE_TTL_DAYS:
                try:
                    existing = json.loads(baseline_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = None

        if existing is None:
            # 缓存不存在或过期 → 重跑 probe
            logger.info("[probe] %s: 体检缓存不存在或已过期，开始节点信息充分性体检...",
                        getattr(pipeline, "id", "?"))
            from omnicompany.runtime.info_audit.startup_baseline import run_pipeline_probe_baseline
            existing = run_pipeline_probe_baseline(
                pipeline,
                output_dir=str(resolve_domain_data_dir(domain)),
                include_kinds=("ANCHOR", "LLM", "SOFT"),
            )
            logger.info("[probe] %s: 体检完成, %d 节点, 结果已缓存到 %s",
                        getattr(pipeline, "id", "?"),
                        existing.get("summary", {}).get("total", 0),
                        baseline_path)

        # 读取体检结果并打 warning
        _warn_from_baseline(existing, pipeline_id=getattr(pipeline, "id", domain))

    except Exception as e:
        # 永不阻塞主流程
        logger.debug("[probe] 体检跳过: %s", e)


def _warn_from_baseline(baseline: dict[str, Any], pipeline_id: str) -> None:
    """扫描 baseline, 对 critical 缺口多的节点打 warning.

    兼容两种存储格式:
      - 旧: {"n_critical": int} (append_pipeline_health 格式)
      - 新: {"missing_info": [{..., "critical": bool}, ...]} (startup_baseline 格式)
    """
    per_node = baseline.get("per_node") or {}
    flagged: list[str] = []
    for node_id, info in per_node.items():
        # 优先读 n_critical 字段（health log 格式），否则从 missing_info 计算
        n_crit = info.get("n_critical")
        if n_crit is None:
            missing_info = info.get("missing_info") or []
            n_crit = sum(1 for m in missing_info if m.get("critical"))
        if n_crit >= _CRITICAL_WARN_THRESHOLD:
            flagged.append(f"{node_id}[critical={n_crit}]")

    if flagged:
        logger.warning(
            "[probe] %s 节点信息缺口: %s — FORMAT/DESCRIPTION 层可能不完整, "
            "详见 %s",
            pipeline_id, ", ".join(flagged),
            _probe_baseline_path(_domain_from_pipeline_id(pipeline_id)),
        )
    else:
        logger.debug("[probe] %s: 所有节点 critical 缺口 < %d, 体检通过",
                     pipeline_id, _CRITICAL_WARN_THRESHOLD)


# ────────────────────────────────────────────────────────────────────────────
# 接入点 2: runner → pipeline_health.jsonl
# ────────────────────────────────────────────────────────────────────────────

def append_pipeline_health(
    *,
    pipeline_id: str,
    domain: str,
    trace_id: str,
    node_reports: dict[str, Any],
) -> None:
    """runner.run() 末尾: 把本次跑的节点审计结果追加到 pipeline_health.jsonl.

    Args:
        pipeline_id:  管线 ID (如 "absorption-module-driven").
        domain:       所属 domain (如 "absorption").
        trace_id:     本次 run 的 trace_id (ULID).
        node_reports: runner.node_audit_reports — {node_id: InfoAuditReport}.
    """
    try:
        per_node: dict[str, Any] = {}
        for node_id, report in node_reports.items():
            suff = getattr(report, "sufficiency", None)
            per_node[node_id] = {
                "sufficiency": suff.value if hasattr(suff, "value") else str(suff),
                "confidence": getattr(report, "confidence_self", None),
                "n_missing": len(getattr(report, "missing_info", []) or []),
                "n_critical": sum(
                    1 for m in (getattr(report, "missing_info", []) or [])
                    if getattr(m, "critical", False)
                ),
            }

        record = {
            "ts": time.time(),
            "pipeline_id": pipeline_id,
            "trace_id": trace_id,
            "per_node": per_node,
        }

        health_path = _pipeline_health_path(domain)
        health_path.parent.mkdir(parents=True, exist_ok=True)
        with health_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug("[health] %s: health 记录已追加 → %s", pipeline_id, health_path)

    except Exception as e:
        # 永不阻塞主流程
        logger.debug("[health] health 记录写入失败: %s", e)


# ────────────────────────────────────────────────────────────────────────────
# 供 Doctor / CLI 消费: 读最近 N 次 health 记录
# ────────────────────────────────────────────────────────────────────────────

def read_pipeline_health(
    domain: str,
    pipeline_id: str | None = None,
    last_n: int = 10,
) -> list[dict[str, Any]]:
    """读 pipeline_health.jsonl, 返回最近 last_n 条记录.

    Args:
        domain:      所属 domain.
        pipeline_id: 若指定则只返回匹配的记录.
        last_n:      最多返回条数.
    """
    health_path = _pipeline_health_path(domain)
    if not health_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in health_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if pipeline_id and rec.get("pipeline_id") != pipeline_id:
                continue
            records.append(rec)
    except Exception:
        return []
    # 返回最近 N 条 (文件是 append-only, 最后 N 条最新)
    return records[-last_n:]


def health_summary(domain: str, pipeline_id: str | None = None, last_n: int = 10) -> str:
    """返回可读的 health 汇总字符串, 供 CLI / Doctor 直接打印."""
    records = read_pipeline_health(domain, pipeline_id=pipeline_id, last_n=last_n)
    if not records:
        return f"[health] {domain}: 无历史记录"
    lines = [f"[health] {domain} 最近 {len(records)} 次运行:"]
    for rec in records:
        ts = time.strftime("%m-%d %H:%M", time.localtime(rec.get("ts", 0)))
        pid = rec.get("pipeline_id", "?")
        per = rec.get("per_node") or {}
        bad = [f"{n}:{v.get('sufficiency','?')}" for n, v in per.items()
               if v.get("sufficiency") not in ("sufficient",)]
        status = ("⚠ " + ", ".join(bad)) if bad else "✓ all sufficient"
        lines.append(f"  {ts} [{pid}] {status}")
    return "\n".join(lines)

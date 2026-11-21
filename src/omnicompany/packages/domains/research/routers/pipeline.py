# [OMNI] origin=ai-ide domain=research/routers ts=2026-06-14T00:00:00Z type=router status=active
# [OMNI] summary="管线首尾两个 RULE 节点: TopicIntake(归一化+查重门) 与 LibraryWrite(去重累积落统一库+渲报告)。"
# [OMNI] why="中间的拆题/并行子研究/综合/核源已升级拆到 deep.py / synth.py(对齐 SOTA)。本文件只留确定性首尾。"
# [OMNI] tags=research,router,worker,intake,library
"""research.run 首尾 RULE 节点。

当前三节点：intake(查重门) → native(外部 Codex 原生搜索) → library_write(落统一库)。
交互式调研不走这条 Team，而由前台 agent 原生搜索后调用 check/save。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .. import library
from .._paths import RUNS_ROOT, ensure_dirs


DEFAULT_TOTAL_TIMEOUT_S = 600.0
DEFAULT_IDLE_TIMEOUT_S = 60.0
DEFAULT_MAX_RESULTS = 12


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


# ── 节点 1: 入题 + 查重门(拆题交给下游 planner)──────────────────────────
class TopicIntake(Router):
    """归一化题目、建 run_dir、查库(同题已有则带出)、透传跑参。"""

    DESCRIPTION = "入题: 归一化 + 查重门(同题带出增量)"
    FORMAT_IN = "research.request"
    FORMAT_OUT = "research.intake"
    REQUIRED_CONTEXT = ["topic"]

    def run(self, input_data: Any) -> Verdict:
        req = input_data if isinstance(input_data, dict) else {}
        topic = str(req.get("topic", "")).strip()
        if not topic:
            return Verdict(kind=VerdictKind.FAIL, output=req, diagnosis="topic 为空")
        topic_norm = library.normalize_topic(topic)

        ensure_dirs()
        run_dir = RUNS_ROOT / ("run_" + datetime.now().strftime("%Y-%m-%dT%H-%M-%S"))
        run_dir.mkdir(parents=True, exist_ok=True)

        existing = library.lookup_by_topic(topic_norm)  # 查重门
        timeout_s = _positive_float(req.get("timeout_s"), DEFAULT_TOTAL_TIMEOUT_S)
        idle_timeout_s = _positive_float(req.get("idle_timeout_s"), DEFAULT_IDLE_TIMEOUT_S)
        max_results = _positive_int(req.get("max_results"), DEFAULT_MAX_RESULTS)
        (run_dir / "intake.json").write_text(
            json.dumps({"topic": topic, "topic_norm": topic_norm,
                        "existing_record_id": (existing or {}).get("record_id"),
                        "timeout_s": timeout_s, "idle_timeout_s": idle_timeout_s,
                        "max_results": max_results},
                       ensure_ascii=False, indent=2), encoding="utf-8")

        diag = f"入题 '{topic}'"
        if existing:
            diag += f" · 库内已有同题 {existing.get('record_id')}(将增量合并)"
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic": topic, "topic_norm": topic_norm, "run_dir": str(run_dir),
                "existing": existing, "timeout_s": timeout_s,
                "idle_timeout_s": idle_timeout_s, "max_results": max_results,
            },
            diagnosis=diag,
            granted_tags=["domain.research", "stage.intake"],
        )


# ── 节点末: 落统一研究库(去重累积)+ 渲报告 ───────────────────────────────
class LibraryWrite(Router):
    """组装 record,upsert 进统一库(同题增量),渲 report.md。findings 已带核源 support。"""

    DESCRIPTION = "落库: 去重累积 upsert + 渲 report.md(管线 sink)"
    FORMAT_IN = "research.verified"
    FORMAT_OUT = "research.record"
    REQUIRED_CONTEXT = ["topic", "run_dir"]

    def run(self, input_data: Any) -> Verdict:
        out = input_data if isinstance(input_data, dict) else {}
        topic = out["topic"]
        run_dir = Path(out["run_dir"])

        synthesis = out.get("synthesis") or {}
        sources = out.get("sources") or []
        if out.get("research_ok") is not True or not synthesis.get("summary"):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"topic": topic, "run_dir": str(run_dir), "saved": False},
                diagnosis="上游调研失败或为空，拒绝写入研究库",
            )

        snapshot_texts = {
            str(source.get("url") or "").strip(): source["snapshot_text"]
            for source in sources
            if str(source.get("url") or "").strip()
            and isinstance(source.get("snapshot_text"), str)
            and source["snapshot_text"].strip()
        }
        # The run-local native.json owns the full captured text.  The unified
        # record keeps its SHA and snapshot_path, avoiding duplicate large text.
        library_sources = [
            {key: value for key, value in source.items() if key != "snapshot_text"}
            for source in sources
        ]

        # 落库核心三路共用(library.save_research_record): 组装 + upsert + catalog 投影 + 渲 report。
        saved, is_dup, report_path = library.save_research_record(
            topic,
            synthesis,
            library_sources,
            coverage=out.get("coverage") or {},
            run_id=run_dir.name,
            snapshot_texts=snapshot_texts or None,
        )
        # run_dir 内也留一份(单次产物自带)
        (run_dir / "report.md").write_text(library.render_report(saved), encoding="utf-8")

        n_unsup = sum(1 for f in (saved.get("findings") or []) if f.get("support") == "unsupported")
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "record_id": saved["record_id"], "report": str(report_path),
                "run_dir": str(run_dir), "dup": is_dup, "richness": saved.get("richness", 0),
                "unsupported": n_unsup,
            },
            diagnosis=f"{'增量更新' if is_dup else '新建'}研究记录 {saved['record_id']} "
                      f"(丰富度 {saved.get('richness', 0)}, {n_unsup} 条无源支撑)",
            granted_tags=["domain.research", "stage.record", "kind.sink"],
        )

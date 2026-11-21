# [OMNI] origin=ai-ide domain=research/routers ts=2026-06-30T00:00:00Z type=router status=active
# [OMNI] summary="NativeResearch: 无人值守执行器。codex exec(原生 web_search)按调研协议搜+读+综合,输出受 schema 约束的研究记录。"
# [OMNI] why="2026-06-30 校准:弃外部搜索 API + 便宜模型编排,改用带原生搜索的 frontier agent。交互式由前台 agent 跑(SKILL),无人值守由本节点驱动 codex,二者同协议同 library.save_research_record 落点。"
# [OMNI] tags=research,router,native,codex,websearch
"""NativeResearch —— 无人值守调研执行器(codex 原生搜索)。

intake(research.intake)→ native(research.verified)→ library_write(research.record)。
本节点把「明确需求 → 原生 WebSearch/WebFetch 搜读 → 全面性核查 → 综合带源」整个交给 codex
(开 tools.web_search,readonly 沙箱,--output-schema 强约束产出),不再用便宜模型编排哑搜索。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

from .pipeline import DEFAULT_IDLE_TIMEOUT_S, DEFAULT_MAX_RESULTS, DEFAULT_TOTAL_TIMEOUT_S

# codex --output-schema 走 OpenAI strict 模式: 每个 object 必须 additionalProperties:false
# 且 required 列全所有 property(没有"可选字段"概念,空就给空串/空数组)。
RESEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "description": "2-4 句客观概述,不打分"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string", "description": "一句具体结论"},
                    "source_url": {"type": "string", "description": "该结论依据的页 url"},
                    "support": {
                        "type": "string",
                        "enum": ["supported", "partial", "unsupported", "unverified"],
                        "description": "回读来源页后的支撑判定,默认从严",
                    },
                },
                "required": ["claim", "source_url", "support"],
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "snippet": {"type": "string"},
                    "platform": {
                        "type": "string",
                        "description": "页面所属平台；无法从页面确认时留空，禁止猜测",
                    },
                    "publisher": {
                        "type": "string",
                        "description": "发布主体/账号；无法从页面确认时留空，禁止猜测",
                    },
                    "author": {
                        "type": "string",
                        "description": "署名作者；页面未署名时留空",
                    },
                    "published_at": {
                        "type": "string",
                        "description": "页面明确标出的发布时间 ISO-8601；未知留空",
                    },
                    "updated_at": {
                        "type": "string",
                        "description": "页面明确标出的更新时间 ISO-8601；未知留空",
                    },
                    "game_version": {
                        "type": "string",
                        "description": "原文明确适用的游戏版本；未知留空",
                    },
                    "season": {
                        "type": "string",
                        "description": "原文明确适用的赛季；未知留空",
                    },
                    "server_stage": {
                        "type": "string",
                        "description": "原文明确适用的服务器阶段；未知留空",
                    },
                    "citation_locator": {
                        "type": "string",
                        "description": "精确到标题/段落/时间码的引用位置",
                    },
                    "snapshot_text": {
                        "type": "string",
                        "description": "从来源页回读的、足以复核结论的原文快照",
                    },
                    "source_summary": {
                        "type": "string",
                        "description": "只概括本来源明确陈述的内容，不混入其他来源",
                    },
                    "fresh_until": {
                        "type": "string",
                        "description": "原文明示的有效截止时间 ISO-8601；未知留空",
                    },
                },
                "required": [
                    "title", "url", "snippet", "platform", "publisher", "author",
                    "published_at", "updated_at", "game_version", "season",
                    "server_stage", "citation_locator", "snapshot_text",
                    "source_summary", "fresh_until"
                ],
            },
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "aliases": {"type": "array", "items": {"type": "string"}, "description": "别名/同义说法,供日后查重召回"},
        "perspectives_covered": {"type": "array", "items": {"type": "string"}},
        "perspectives_open": {"type": "array", "items": {"type": "string"}, "description": "还没覆盖的角度,诚实留白"},
    },
    "required": ["summary", "findings", "sources", "keywords", "aliases",
                 "perspectives_covered", "perspectives_open"],
}


def _normalize_source_artifacts(
    sources: list[dict[str, Any]],
    *,
    retrieved_at: str,
) -> list[dict[str, Any]]:
    """Add system-observed capture fields without inventing page metadata."""

    normalized: list[dict[str, Any]] = []
    for item in sources:
        source = dict(item)
        for key, value in list(source.items()):
            if isinstance(value, str):
                stripped = value.strip()
                source[key] = stripped or None
        snapshot_text = source.get("snapshot_text")
        source["retrieved_at"] = retrieved_at
        source["snapshot_sha256"] = (
            hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
            if isinstance(snapshot_text, str) and snapshot_text
            else None
        )
        # Keep the old combined field for existing research/guide consumers.
        source["author_or_publisher"] = source.get("publisher") or source.get("author")
        source["summary"] = source.pop("source_summary", None)
        normalized.append(source)
    return normalized


def _ensure_isolated_audit_repo(run_dir: Path) -> None:
    """Keep readonly git auditing scoped to this run, not the shared dirty repo.

    The run directory is an untracked child of the main repository. Without a
    nested git root, Codex's readonly adapter compares the entire shared
    worktree and can blame unrelated scheduler/user edits on this worker.
    Existing run artifacts stay untracked on both sides of the baseline; any
    new child-created path is still observable, while the read-only sandbox is
    the primary write barrier for existing files.
    """

    if (run_dir / ".git").exists():
        return
    completed = subprocess.run(
        ["git", "init", "--quiet"],
        cwd=run_dir,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git init failed").strip()
        raise RuntimeError(f"failed to isolate research readonly audit: {detail}")


def _build_prompt(topic: str, existing: dict | None, max_results: int) -> str:
    base = f"""你是无人值守公开调研员。对题目做联网调研,只用你自己的原生 web 搜索能力(web_search 工具),禁自写爬虫。全程中文。

运行边界（必须遵守）：intake 已完成本地查重。不要读取任何本地 SKILL/AGENTS/仓库文件，不要运行 shell、PowerShell、omni、refs 或其他本地命令，不要写入任何文件；直接使用 web_search 搜读公开来源并返回 JSON。

题目:{topic}

步骤:
1. 明确需求:先想清楚这题到底要回答什么、范围/时间;列出这个概念的别名/同义说法(中英、行话、产品名 vs 通称)和要覆盖的多视角(机制/对比选型/反对与替代/落地与坑/基础覆盖),至少含一个冷门或替代视角。
2. 搜+读:逐角度搜,同一角度换几种说法各搜一次(覆盖第 1 步的别名清单);读有料的页,抽出带 source_url 的发现(claim 必须能在某个来源页找到支撑)。
3. 全面性核查:对着第 1 步的别名/角度清单逐项核——每个同义词、每个角度都真发起过搜索了吗?缺口能补就再搜,暂不深挖的写进 perspectives_open(诚实留白)。
4. 核源:每条 finding 标 support——回读它声称的来源页,看得到明确支撑才给 supported,沾边不充分给 partial,找不到/矛盾给 unsupported,没核到给 unverified;默认从严。
5. 只认页面里有的,绝不编造;客观、给证据、不打分。

结果预算：最多保留 {max_results} 条 findings 和 {max_results} 个 sources。达到预算并覆盖题目主问题后立即综合输出，不继续扩展相邻主题。

来源留痕：每个 source 都要回读来源页，并逐项填写平台、发布主体、作者、发布时间/更新时间、适用版本/赛季/服务器阶段、精确引用位置、原文快照和只基于该来源的摘要。页面没有明确写出的字段一律给空串；不得从搜索摘要、网址、常识或其他来源猜测。原文快照必须是来源页中实际可见、可支持摘要的连续内容。fresh_until 只有原文明示截止时间时才填写，否则留空。抓取时间与快照 SHA 由运行器补写，不由你生成。

只输出符合 output schema 的 JSON(summary/findings/sources/keywords/aliases/perspectives_covered/perspectives_open),不要别的话。"""
    if max_results <= 3:
        base += (
            "\n\n【快速受控模式】本次结果预算很小，覆盖主问题优先于扩展广度。"
            "最多调用 2 次 web_search；无需为每个同义词重复搜索，也无需补冷门视角。"
            "获得 1—3 个一手来源后立即核源并输出最终 JSON，不继续搜索。"
        )
    if existing:
        covered = existing.get("perspectives_covered") or []
        open_p = existing.get("perspectives_open") or []
        base += (
            f"\n\n【增量】库内已有同题记录,已覆盖角度:{covered};还缺:{open_p}。"
            "重点补还缺的角度与新进展,别重复已覆盖的;findings/sources 给新增的即可(系统会自动与旧记录合并)。"
        )
    return base


class NativeResearch(Router):
    """无人值守执行器:codex(原生 web_search)按调研协议产出受 schema 约束的研究记录。"""

    DESCRIPTION = "原生调研: codex 原生 web 搜索按协议搜+读+核源+综合(无外部搜索 API)"
    FORMAT_IN = "research.intake"
    FORMAT_OUT = "research.verified"
    REQUIRED_CONTEXT = ["topic", "run_dir"]

    @staticmethod
    def _write_status(run_dir: Path, **payload: Any) -> None:
        (run_dir / "native_status.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def run(self, input_data: Any) -> Verdict:
        from omnicompany.packages.services._core.agent.external_workers import (
            ExternalAgentPermissionMode,
            ExternalAgentRunRequest,
            run_external_agent_request,
        )

        ctx = input_data if isinstance(input_data, dict) else {}
        topic = ctx["topic"]
        run_dir = Path(ctx["run_dir"])
        existing = ctx.get("existing")
        timeout_s = float(ctx.get("timeout_s") or DEFAULT_TOTAL_TIMEOUT_S)
        idle_timeout_s = float(ctx.get("idle_timeout_s") or DEFAULT_IDLE_TIMEOUT_S)
        max_results = int(ctx.get("max_results") or DEFAULT_MAX_RESULTS)
        run_id = f"research-{run_dir.name}"

        schema_path = run_dir / "output_schema.json"
        schema_path.write_text(json.dumps(RESEARCH_OUTPUT_SCHEMA, ensure_ascii=False), encoding="utf-8")
        _ensure_isolated_audit_repo(run_dir)

        started_at = datetime.now(timezone.utc).isoformat()
        self._write_status(
            run_dir,
            state="running",
            started_at=started_at,
            parent_pid=os.getpid(),
            run_id=run_id,
            timeout_s=timeout_s,
            idle_timeout_s=idle_timeout_s,
            note="交互式调研不应使用本 worker；这是可选的无人值守批量通道。",
        )
        print(
            f"[research.native] start run={run_id} total_timeout={timeout_s:g}s "
            f"idle_timeout={idle_timeout_s:g}s status={run_dir / 'native_status.json'}",
            flush=True,
        )

        request = ExternalAgentRunRequest(
            provider="codex",
            prompt=_build_prompt(topic, existing, max_results),
            cwd=str(run_dir),
            run_id=run_id,
            permission_mode=ExternalAgentPermissionMode.READONLY,
            model_policy="none",  # 调研要好脑子,别降便宜档
            timeout_s=timeout_s,
            output_schema_path=schema_path,
            trace_id=run_id,
            metadata={
                "codex_config": {"tools.web_search": "true"},
                "idle_timeout_s": idle_timeout_s,
                "research_run_dir": str(run_dir),
                "event_log_path": str(run_dir / "native_events.jsonl"),
            },
        )
        try:
            result = asyncio.run(run_external_agent_request(request))
        except BaseException as exc:
            self._write_status(
                run_dir,
                state="interrupted",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                parent_pid=os.getpid(),
                run_id=run_id,
                timeout_s=timeout_s,
                idle_timeout_s=idle_timeout_s,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        result_status = getattr(result, "status", "unknown")
        result_status = getattr(result_status, "value", result_status)
        finished_at = datetime.now(timezone.utc).isoformat()
        self._write_status(
            run_dir,
            state="finished",
            started_at=started_at,
            finished_at=finished_at,
            parent_pid=os.getpid(),
            run_id=run_id,
            timeout_s=timeout_s,
            idle_timeout_s=idle_timeout_s,
            result_status=str(result_status),
            event_count=len(getattr(result, "events", None) or []),
            exit_code=getattr(result, "exit_code", None),
            error=getattr(result, "error", ""),
        )
        print(
            f"[research.native] finish run={run_id} status={result_status} "
            f"events={len(getattr(result, 'events', None) or [])}",
            flush=True,
        )

        data = getattr(result, "structured_output", None)
        if not isinstance(data, dict):
            # 兜底:从 final_text 里捞 JSON
            try:
                data = json.loads((getattr(result, "final_text", "") or "").strip())
            except (json.JSONDecodeError, ValueError):
                data = None

        # codex 的错误信封也是 dict(如 {"type":"error","status":400,...})——必须识别,
        # 否则会被当空结果落库成"干净"的 0 发现记录。要求出现协议关键键才算真产出。
        def _is_record(d: Any) -> bool:
            return isinstance(d, dict) and "summary" in d and "findings" in d and "type" not in d

        if not _is_record(data):
            err = ""
            if isinstance(data, dict):
                err = (data.get("error") or {}).get("message") or str(data)[:300]
            (run_dir / "native_raw.txt").write_text(
                (getattr(result, "final_text", "") or "") + ("\n\n" + err if err else ""),
                encoding="utf-8")
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**ctx, "research_ok": False,
                        "synthesis": {"summary": "(codex 调研未产出合法记录)", "findings": []},
                        "sources": [], "coverage": {}},
                diagnosis=f"codex 调研失败 status={getattr(result, 'status', '?')}: {err[:160]}"
                          "(原文存 native_raw.txt)",
            )

        synthesis = {
            "summary": data.get("summary", ""),
            "findings": data.get("findings") or [],
            "keywords": data.get("keywords") or [],
            "aliases": data.get("aliases") or [],
            "perspectives_open": data.get("perspectives_open") or [],
        }
        sources = _normalize_source_artifacts(
            data.get("sources") or [],
            retrieved_at=finished_at,
        )
        data["sources"] = sources
        coverage = {"covered": data.get("perspectives_covered") or []}

        (run_dir / "native.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        n = len(synthesis["findings"])
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "topic": topic, "topic_norm": ctx.get("topic_norm", ""), "run_dir": str(run_dir),
                "research_ok": True,
                "synthesis": synthesis, "sources": sources, "coverage": coverage,
                "existing": existing,
            },
            confidence=1.0 if n else 0.0,
            diagnosis=f"codex 原生调研: {n} 条发现 · {len(sources)} 源 · 覆盖 {len(coverage['covered'])} 视角",
            granted_tags=["domain.research", "stage.verified"],
        )

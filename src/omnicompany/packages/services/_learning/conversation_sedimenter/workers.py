# [OMNI] origin=claude-code domain=services/_learning ts=2026-06-23T00:00:00Z type=worker
# [OMNI] material_id="material:services._learning.conversation_sedimenter.workers.py"
"""conversation-operation-sedimenter 的三个 Worker。

  convop.request → [TraceReader 确定性: .jsonl 流式压成紧凑轨迹] → convop.trace
                 → [Miner 脑子: gpt-5.5 tool-agent 聚类常见操作]   → convop.operations
                 → [Proposer 确定性: 装 team 骨架 + 写草稿]         → convop.team_skeleton(sink)

SOFT(Miner) 夹在确定性 reader 与确定性 proposer 之间(P-04 满足)。
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.agent.launch import run_json_agent  # 统一 AgentNodeLoop 启动器
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

# TraceReader 流式上限（防 600MB .jsonl 撑爆）
_MAX_LINES = 300_000
_MAX_LINE_BYTES = 1_000_000
_STORE_CAP = 400


def _target_brief(tool: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    for k in ("file_path", "path", "notebook_path"):
        if inp.get(k):
            return str(inp[k])[:100]
    if inp.get("command"):
        return "$ " + str(inp["command"])[:90]
    if inp.get("pattern"):
        return "grep:" + str(inp["pattern"])[:70]
    if inp.get("prompt"):
        return "prompt:" + str(inp["prompt"])[:70]
    for v in inp.values():
        if isinstance(v, str) and v:
            return v[:90]
    return ""


def _resolve_transcript(req: dict) -> Path | None:
    tp = str(req.get("transcript_path", "")).strip()
    if tp:
        p = Path(tp)
        return p if p.is_file() else None
    sid = str(req.get("session_id", "")).strip()
    if sid:
        base = Path(os.environ.get("USERPROFILE", "")) / ".claude" / "projects"
        if base.is_dir():
            for f in base.rglob(f"{sid}*.jsonl"):
                return f
    return None


class ConversationTraceReaderWorker(Worker):
    """确定性: .jsonl 流式解析压成紧凑动作轨迹（省 token 的关键）。"""

    DESCRIPTION = (
        "对话轨迹读取器(确定性)。纯 Python 流式解析 claude-code/codex .jsonl(防大文件 OOM), "
        "抽每轮 assistant 的 tool_use(工具名+目标摘要)压成 events[] + tool_histogram, "
        "把上 MB 对话压成几 KB 轨迹。产 convop.trace。读不到/无动作则 FAIL。"
    )
    FORMAT_IN = "convop.request"
    FORMAT_OUT = "convop.trace"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get("convop.request") or {}
        path = _resolve_transcript(req)
        if path is None:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis="找不到对话 .jsonl(给 transcript_path 或 session_id)")
        events: list[dict] = []
        hist: Counter = Counter()
        n_lines = 0
        n_events = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    n_lines += 1
                    if n_lines > _MAX_LINES:
                        break
                    if len(line) > _MAX_LINE_BYTES:
                        continue  # 跳超长行(多半是大 tool_result, 非 tool_use)
                    try:
                        o = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(o, dict) or o.get("type") != "assistant":
                        continue
                    msg = o.get("message") or {}
                    for b in (msg.get("content") or []):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tool = str(b.get("name", "?"))
                            tgt = _target_brief(tool, b.get("input") or {})
                            hist[tool] += 1
                            n_events += 1
                            if len(events) < _STORE_CAP:
                                events.append({"turn": n_lines, "tool": tool, "target": tgt})
        except OSError as e:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"读 .jsonl 失败: {e}")
        if n_events == 0:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"对话里没解析到 tool_use 动作(n_lines={n_lines})")
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "events": events,
                "tool_histogram": dict(hist.most_common()),
                "n_events": n_events,
                "n_lines_scanned": n_lines,
                "meta": {"path": str(path), "source": req.get("source", "claude-code")},
            },
        )


OPERATIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["operations"],
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "steps"],
                "properties": {
                    "name": {"type": "string"},
                    "trigger": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "frequency": {"type": "integer"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "summary": {"type": "string"},
    },
}

_MINE_PROMPT = """你是对话操作挖掘员(只读)。给你一段工作对话压缩出的「动作轨迹」(工具直方图 + 动作序列样本)。
请聚类出其中**反复出现的常见操作**(可被沉淀成可复用 team 的那种), 而非逐条罗列。
必要时可用 read_file/grep 回查 project_root 下的原 .jsonl 片段佐证。

完成时调 finish 返回满足 schema 的 JSON:
- operations: 3-6 条常见操作, 每条:
  - name: 操作名(动宾, 如 "搜索定位再改一处"、"配表 dry-run 后 LIVE 出 changelist"、"读计划评估记进度")
  - trigger: 什么时候会做这操作
  - steps: 有序步骤(每步对应一类工具动作, 2-5 步)
  - frequency: 这操作在本对话出现的大致次数(整数, 据直方图/序列估)
  - evidence: 2-4 条佐证(指向 target/工具组合, 如 "Grep→Read→Edit 连续出现多次")
- summary: 一句话这段工作在干嘛
按 frequency 从高到低排。只用 finish 返回 JSON, 别臆造没在轨迹里的工具。"""


class ConversationOperationMinerWorker(Worker):
    """脑子: 统一 AgentNodeLoop(gpt-5.5) 从轨迹聚类常见操作（SOFT）。"""

    DESCRIPTION = (
        "常见操作挖掘器(SOFT)。把紧凑轨迹喂**统一** AgentNodeLoop(run_json_agent, gpt-5.5), 聚类出反复"
        "出现的常见操作(name/trigger/steps/frequency/evidence), 可用 read/grep 回查原 .jsonl 佐证。产 convop.operations。"
    )
    FORMAT_IN = "convop.trace"
    FORMAT_OUT = "convop.operations"

    async def run(self, input_data: dict[str, Any]) -> Verdict:
        tr = input_data.get("convop.trace") or {}
        events = tr.get("events") or []
        # 取代表性样本(前 140 + 后 60)控 prompt 体量
        sample = events[:140] + (events[-60:] if len(events) > 200 else [])
        ev_lines = "\n".join(f"  {e.get('turn')}/{e.get('tool')}/{e.get('target', '')}" for e in sample)
        meta = tr.get("meta") or {}
        tdir = str(Path(meta.get("path", ".")).parent)
        task = (
            f"对话来源={meta.get('source', 'claude-code')}\n"
            f"工具直方图(工具→次数)={json.dumps(tr.get('tool_histogram', {}), ensure_ascii=False)}\n"
            f"总动作数={tr.get('n_events', 0)}, 扫描行数={tr.get('n_lines_scanned', 0)}\n"
            f"动作序列样本(turn/tool/target):\n{ev_lines[:6000]}"
        )
        res = await run_json_agent(
            task=task, node_prompt=_MINE_PROMPT, result_schema=OPERATIONS_SCHEMA,
            project_root=tdir, model="gpt-5.5", caller="convop.miner", max_turns=10,
        )
        if not res["ok"] or not isinstance(res["final"], dict) or not res["final"].get("operations"):
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"AgentNodeLoop 聚类失败: {res.get('error') or '无 operations'}")
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "operations": res["final"].get("operations", []),
                "summary": str(res["final"].get("summary", "")),
                "_agent_turns": res.get("turn_count", 0),
            },
        )


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    if s:
        return s[:40]
    # 全中文操作名 ascii 清洗后为空 → 用稳定短哈希避免每条都叫 "operation" 撞目录
    import hashlib
    return "op_" + hashlib.md5((name or "op").encode("utf-8")).hexdigest()[:6]


def _validate_skeleton(sk: dict) -> dict:
    mats = sk.get("materials", [])
    workers = sk.get("workers", [])
    kinds = {m.get("kind") for m in mats}
    findings: list[str] = []
    if "source" not in kinds:
        findings.append("缺 source material")
    if "sink" not in kinds:
        findings.append("缺 sink material")
    for w in workers:
        if not w.get("format_in") or not w.get("format_out"):
            findings.append(f"worker {w.get('id')} 缺 FORMAT_IN/OUT")
    for i in range(len(workers) - 1):
        if workers[i].get("format_out") != workers[i + 1].get("format_in"):
            findings.append(f"链断裂: {workers[i].get('id')}.out != {workers[i+1].get('id')}.in")
    return {"ok": not findings, "findings": findings, "n_workers": len(workers), "n_materials": len(mats)}


class TeamSkeletonProposerWorker(Worker):
    """确定性: 取频次最高的操作装成 team 骨架并写草稿（HARD, 紧跟 Miner）。"""

    DESCRIPTION = (
        "team 骨架提议器(HARD/确定性)。取频次最高的操作, 按'每步→worker, 步间产物→internal material, "
        "首尾→source/sink'确定性映射成 team 骨架, 做 doctor-lite 自检(source/sink 齐/worker 有 IO/链连通), "
        "把骨架+草稿写进限定目录 data/_workspaces/conversation_sedimenter/<slug>/。产 convop.team_skeleton(sink)。"
    )
    FORMAT_IN = "convop.operations"
    FORMAT_OUT = "convop.team_skeleton"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        ops = (input_data.get("convop.operations") or {}).get("operations") or []
        if not ops:
            return Verdict(kind=VerdictKind.FAIL, diagnosis="无 operations 可装配")
        ops = sorted(ops, key=lambda o: int(o.get("frequency", 1) or 1), reverse=True)
        top = ops[0]
        steps = [str(s) for s in (top.get("steps") or []) if str(s).strip()]
        if not steps:
            return Verdict(kind=VerdictKind.FAIL, diagnosis=f"频次最高操作 '{top.get('name')}' 无 steps")
        slug = _slug(top.get("name", ""))

        materials = [{"id": f"{slug}.request", "kind": "source", "desc": f"{top.get('name')} 的输入请求"}]
        for i in range(len(steps) - 1):
            materials.append({"id": f"{slug}.s{i+1}", "kind": "internal", "desc": f"第{i+1}步产物"})
        materials.append({"id": f"{slug}.result", "kind": "sink", "desc": f"{top.get('name')} 的最终产物"})

        workers = []
        for i, step in enumerate(steps):
            fin = f"{slug}.request" if i == 0 else f"{slug}.s{i}"
            fout = f"{slug}.s{i+1}" if i < len(steps) - 1 else f"{slug}.result"
            workers.append({
                "id": f"{slug}_step{i+1}_worker", "purpose": step,
                "format_in": fin, "format_out": fout,
            })

        skeleton = {
            "name": slug.replace("_", "-"),
            "candidate_op": top.get("name"),
            "trigger": top.get("trigger", ""),
            "materials": materials, "workers": workers,
            "entry": f"{slug}.request",
            "topology": [f"{w['format_in']} → {w['id']} → {w['format_out']}" for w in workers],
        }
        validation = _validate_skeleton(skeleton)
        if not validation["ok"]:
            return Verdict(kind=VerdictKind.FAIL,
                           diagnosis=f"骨架不合法: {validation['findings']}",
                           output={"proposed_team": skeleton, "validation": validation})

        draft_dir = Path(omni_workspace_root()) / "data" / "_workspaces" / "conversation_sedimenter" / slug
        draft_rel = os.path.join("data", "_workspaces", "conversation_sedimenter", slug)
        draft_path = ""
        try:
            from omnicompany.core.guarded_write import write_file
            # writer=internal-engine: data/ 只允许 internal-engine/guardian 写; 本 team worker 即引擎写运行态草稿
            write_file(draft_dir / "skeleton.json",
                       json.dumps({"skeleton": skeleton, "all_operations": ops,
                                   "summary": (input_data.get("convop.operations") or {}).get("summary", "")},
                                  ensure_ascii=False, indent=2),
                       origin="claude-code", writer="internal-engine", domain="services/_learning",
                       is_temp=True, purpose="conversation-operation-sedimenter 产出的可沉淀 team 骨架草稿")
            md = _skeleton_md(skeleton, ops)
            write_file(draft_dir / "proposed_team.md", md, origin="claude-code", writer="internal-engine",
                       domain="services/_learning", is_temp=True, purpose="可沉淀 team 骨架草稿(人读版)")
            draft_path = str(draft_dir)
        except Exception as e:  # noqa: BLE001 - 写草稿失败不阻断 sink 产出, 如实记 note
            draft_path = f"[写草稿失败: {e}] (建议位置 {draft_rel})"

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "candidate_op": top.get("name"),
                "proposed_team": skeleton,
                "draft_path": draft_path,
                "validation": validation,
            },
        )


def _skeleton_md(sk: dict, ops: list) -> str:
    lines = [f"# 可沉淀 team 骨架草稿: {sk['name']}", "",
             f"> 候选操作: **{sk.get('candidate_op')}** | 触发: {sk.get('trigger', '')}",
             "> 由 conversation-operation-sedimenter 从一段对话自动提议, **草稿**, 待人/team-builder 接力硬化。", "",
             "## Materials", ""]
    for m in sk["materials"]:
        lines.append(f"- `{m['id']}` (kind={m['kind']}) — {m['desc']}")
    lines += ["", "## Workers", ""]
    for w in sk["workers"]:
        lines.append(f"- `{w['id']}`: {w['purpose']}  \n  FORMAT_IN=`{w['format_in']}` → FORMAT_OUT=`{w['format_out']}`")
    lines += ["", "## 拓扑", "", f"entry = `{sk['entry']}`", ""]
    for t in sk["topology"]:
        lines.append(f"- {t}")
    lines += ["", "## 本对话其余常见操作(供选别的候选)", ""]
    for o in ops[1:6]:
        lines.append(f"- {o.get('name')} (freq≈{o.get('frequency', '?')}): {' → '.join(o.get('steps', [])[:4])}")
    return "\n".join(lines) + "\n"


ALL_WORKERS = [ConversationTraceReaderWorker, ConversationOperationMinerWorker, TeamSkeletonProposerWorker]

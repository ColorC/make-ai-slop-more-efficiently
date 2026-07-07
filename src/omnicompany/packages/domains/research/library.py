# [OMNI] origin=ai-ide domain=research ts=2026-06-14T00:00:00Z type=library status=active
# [OMNI] summary="统一研究库:累积/查重/增量合并/渲染报告。所有调研产物的唯一落点。"
# [OMNI] why="用户痛点=调研重复、产物没存下来。库以 topic_norm 为查重键,同题再跑=增量合并不重复;墓碑+丰富度去重(对标 material_registry)。"
# [OMNI] tags=research,library,dedup,knowledge
"""统一研究库 —— 公开调研的累积存储 + 开跑前查重。

存储: records.jsonl(append-only,每行一条 record,最新行权威) + index.json(topic_norm → record_id)。
查重: 先确定性(topic_norm 精确 + 关键词集合命中);语义查重(便宜模型判 same/partial)留给入口节点按需调。
去重: 同 record_id 取最新写入(upsert 已把历史 findings/sources 并进来,richness 单增)。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

from ._paths import LIBRARY_ROOT, RECORDS_PATH, SNAPSHOTS_ROOT, ensure_dirs


class LibraryWriteError(Exception):
    """落库前自动校验不过(strict 模式)抛出。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_snapshot(url: str, text: str, max_bytes: int = 200_000) -> str | None:
    """把抓到的源原文落本地快照(内容寻址 snapshots/<sha1(url)>.txt,≤200KB 截断)。

    返回相对 LIBRARY_ROOT 的路径(利迁移),供 source.snapshot_path 指过去。
    原 URL 改版/失效后仍能回源验证 —— 满足"源完完整整原本地记录"。
    """
    if not url or not text:
        return None
    ensure_dirs()
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    p = SNAPSHOTS_ROOT / f"{h}.txt"
    # 按字节截断(原 text[:max_bytes] 是按字符,CJK 体积约 3 倍于声明上限)
    body = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    content = f"# source: {url}\n# saved: {now_iso()}\n\n{body}"
    # 原子写: 临时文件 + os.replace(同卷原子)。orchestrator 多线程子研究可能同 url 并发写同一路径,
    # 直接 write_text 非原子会产生半截/交错的损坏快照。
    tmp = p.with_name(f"{h}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None
    try:
        return str(p.relative_to(LIBRARY_ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def normalize_topic(topic: str) -> str:
    """归一化题目作精确查重键: 去首尾空白、小写、合并空白、去常见标点。中文保留。"""
    t = (topic or "").strip().lower()
    t = re.sub(r"[\s　]+", " ", t)
    t = re.sub(r"[?？。.,，、!！:：;；\"'`()（）\[\]【】]+", "", t)
    return t.strip()


def record_id_for(topic_norm: str) -> str:
    h = hashlib.sha1(topic_norm.encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^a-z0-9一-鿿]+", "-", topic_norm)[:32].strip("-") or "topic"
    return f"res:{slug}:{h}"


def richness(record: dict[str, Any]) -> int:
    """去重取胜分: 源数 + 断言数 + 覆盖角度数。越全越权威。"""
    return (
        len(record.get("sources") or [])
        + len(record.get("findings") or [])
        + len(record.get("perspectives_covered") or [])
    )


def _read_lines() -> list[dict]:
    if not RECORDS_PATH.is_file():
        return []
    out: list[dict] = []
    for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def fold() -> dict[str, dict]:
    """折叠成当前态: 每个 record_id 取最新写入的那行(upsert 保证最新=最全)。"""
    folded: dict[str, dict] = {}
    for rec in _read_lines():
        rid = rec.get("record_id")
        if rid:
            folded[rid] = rec
    return folded


def active_records() -> list[dict]:
    return [r for r in fold().values() if r.get("status") != "deleted"]


def lookup_by_topic(topic_norm: str) -> dict | None:
    rid = record_id_for(topic_norm)
    rec = fold().get(rid)
    if rec and rec.get("status") != "deleted":
        return rec
    return None


def _union_findings(old: list[dict], new: list[dict]) -> list[dict]:
    """按 claim 文本合并。命中重复时**新版本覆盖旧**——新一轮带最新核源 support,
    不能让旧的 unsupported/过时判定压住新的 supported(反之亦然)。空 claim 不去重、全保留。"""
    merged: list[dict] = []
    idx_by_claim: dict[str, int] = {}
    for f in list(old) + list(new):
        c = (f.get("claim") or "").strip()
        if c and c in idx_by_claim:
            merged[idx_by_claim[c]] = f  # 后来者(new 排在后)覆盖,保最新 support
        else:
            if c:
                idx_by_claim[c] = len(merged)
            merged.append(f)
    return merged


def _union_sources(old: list[dict], new: list[dict]) -> list[dict]:
    seen = {(s.get("url") or "").strip() for s in old}
    merged = list(old)
    for s in new:
        u = (s.get("url") or "").strip()
        if u and u not in seen:
            seen.add(u)
            merged.append(s)
    return merged


def _union_strs(old: list[str], new: list[str]) -> list[str]:
    seen = {x.strip() for x in old}
    out = list(old)
    for x in new or []:
        if x and x.strip() not in seen:
            seen.add(x.strip())
            out.append(x)
    return out


def validate_record(record: dict) -> list[str]:
    """落库前自动校验合法性: schema(research.record_full)+ 源完整性(每源有 url、声明的快照存在)。"""
    from omnicompany.runtime.llm.structured import validate_json_schema

    from .formats import RESEARCH_RECORD_FULL

    issues = [f"{i.path}: {i.message}"
              for i in validate_json_schema(record, RESEARCH_RECORD_FULL.json_schema)]
    for i, s in enumerate(record.get("sources") or []):
        if not (s.get("url") or "").strip():
            issues.append(f"sources[{i}]: 缺 url")
        sp = s.get("snapshot_path")
        if sp and not (LIBRARY_ROOT / sp).is_file():
            issues.append(f"sources[{i}]: 快照文件缺失 {sp}")
    return issues


def upsert(record: dict) -> tuple[dict, bool]:
    """写入研究库。同题已存在→增量合并(并 findings/sources/keywords,richness 单增),返 (record, is_dup)。"""
    ensure_dirs()
    rid = record["record_id"]
    existing = fold().get(rid)
    is_dup = bool(existing and existing.get("status") != "deleted")

    if is_dup:
        merged = dict(existing)
        merged["summary"] = record.get("summary") or existing.get("summary", "")
        merged["findings"] = _union_findings(existing.get("findings") or [], record.get("findings") or [])
        merged["sources"] = _union_sources(existing.get("sources") or [], record.get("sources") or [])
        merged["keywords"] = _union_strs(existing.get("keywords") or [], record.get("keywords") or [])
        merged["aliases"] = _union_strs(existing.get("aliases") or [], record.get("aliases") or [])
        merged["perspectives_covered"] = _union_strs(
            existing.get("perspectives_covered") or [], record.get("perspectives_covered") or []
        )
        merged["perspectives_open"] = record.get("perspectives_open") or existing.get("perspectives_open") or []
        merged["status"] = "active"
        merged["created_at"] = existing.get("created_at") or record.get("created_at")
        merged["updated_at"] = now_iso()
        merged["run_ids"] = _union_strs(existing.get("run_ids") or [], record.get("run_ids") or [])
        merged["richness"] = richness(merged)
        record = merged
    else:
        record.setdefault("created_at", now_iso())
        record["updated_at"] = record["created_at"]
        record["status"] = "active"
        record["richness"] = richness(record)

    # 落库前自动校验合法性。默认: 不过也落库但打 validation 标记(不丢花钱调研的产物);
    # OMNI_RESEARCH_STRICT_WRITE=1: 直接抛 LibraryWriteError(CI/冒烟逼早暴露)。
    issues = validate_record(record)
    record["validation"] = {"ok": not issues, "issues": issues, "checked_at": now_iso()}
    if issues and os.environ.get("OMNI_RESEARCH_STRICT_WRITE") == "1":
        raise LibraryWriteError(f"record {rid} 校验不过: {issues}")

    with RECORDS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record, is_dup


def save_research_record(
    topic: str,
    synthesis: dict[str, Any],
    sources: list[dict] | None = None,
    *,
    coverage: dict[str, Any] | None = None,
    run_id: str | None = None,
    snapshot_texts: dict[str, str] | None = None,
) -> tuple[dict, bool, str]:
    """三路(原生交互/codex 无人值守/旧管线)共用的落库核心。

    组装 record → upsert 进统一库(同题增量) → 投影进 catalog → 渲 report.md。
    `synthesis` = {summary, findings[{claim,source_url,support?}], keywords[], aliases[], perspectives_open[]}。
    `snapshot_texts` = {url: 正文} —— 给了就落本地快照并回填 source.snapshot_path(源本地留底,原页失效仍可回源)。
    返回 (saved_record, is_dup, report_path)。
    """
    from ._paths import REPORTS_ROOT

    topic = (topic or "").strip()
    topic_norm = normalize_topic(topic)
    synthesis = synthesis or {}
    sources = [dict(s) for s in (sources or [])]

    if snapshot_texts:
        for s in sources:
            url = (s.get("url") or "").strip()
            txt = snapshot_texts.get(url)
            if url and txt and not s.get("snapshot_path"):
                sp = save_snapshot(url, txt)
                if sp:
                    s["snapshot_path"] = sp

    record = {
        "record_id": record_id_for(topic_norm),
        "topic": topic,
        "topic_norm": topic_norm,
        "summary": synthesis.get("summary", ""),
        "findings": synthesis.get("findings") or [],
        "keywords": synthesis.get("keywords") or [],
        "aliases": synthesis.get("aliases") or [],
        "perspectives_covered": (coverage or {}).get("covered") or [],
        "perspectives_open": synthesis.get("perspectives_open") or [],
        "sources": sources,
        "run_ids": [run_id] if run_id else [],
    }
    saved, is_dup = upsert(record)

    # 落库即投影进统一资产 catalog(研究记录这半永远新鲜,不靠定时)
    try:
        from . import catalog

        catalog.upsert_item({
            "id": saved["record_id"], "kind": "research_record",
            "name": saved.get("topic", ""), "path": "",
            "description": (saved.get("summary") or "")[:300],
            "aliases": sorted(set((saved.get("aliases") or []) + (saved.get("keywords") or []))),
            "source_url": "", "tags": ["research_record"],
            "status": saved.get("status", "active"),
        })
    except Exception:  # noqa: BLE001 — catalog 投影失败不阻断落库
        pass

    ensure_dirs()
    report_md = render_report(saved)
    report_path = REPORTS_ROOT / f"{saved['record_id'].replace(':', '_')}.md"
    report_path.write_text(report_md, encoding="utf-8")
    return saved, is_dup, str(report_path)


def render_report(record: dict) -> str:
    """把一条 record 渲成人读 markdown(待发布产物)。"""
    lines = [f"# 调研:{record.get('topic', '')}", ""]
    lines.append(f"> record_id `{record['record_id']}` · 更新 {record.get('updated_at', '')} · "
                 f"丰富度 {record.get('richness', 0)} · 来源 {len(record.get('sources') or [])} 条")
    lines.append("")
    if record.get("summary"):
        lines += ["## 摘要", "", record["summary"], ""]
    findings = record.get("findings") or []
    srcs = record.get("sources") or []
    # 唯一 url → 连续编号(对齐来源清单),内联引用 [n]
    url_no = {s.get("url", ""): i for i, s in enumerate(srcs, 1) if s.get("url")}
    if findings:
        lines += ["## 发现(带来源)", ""]
        # 支撑态标记: unsupported 显眼标出(对抗核源结果)
        mark = {"supported": "", "partial": " `存疑`", "unsupported": " ⚠`无源支撑`",
                "unverified": " `未核`"}
        for f in findings:
            tag = mark.get(f.get("support", ""), "")
            url = f.get("source_url") or ""
            n = url_no.get(url)
            cite = f" [{n}]" if n else (f" —— [来源]({url})" if url else "")
            lines.append(f"- {f.get('claim', '')}{cite}{tag}")
        lines.append("")
    kws = record.get("keywords") or []
    if kws:
        lines += ["## 关键词", "", ", ".join(kws), ""]
    open_p = record.get("perspectives_open") or []
    if open_p:
        lines += ["## 还没覆盖的角度", "", "\n".join(f"- {p}" for p in open_p), ""]
    srcs = record.get("sources") or []
    if srcs:
        lines += ["## 来源清单", ""]
        for i, s in enumerate(srcs, 1):
            lines.append(f"{i}. [{s.get('title') or s.get('url', '')}]({s.get('url', '')})")
        lines.append("")
    return "\n".join(lines)

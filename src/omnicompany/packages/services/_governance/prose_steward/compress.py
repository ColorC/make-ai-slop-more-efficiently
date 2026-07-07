# [OMNI] origin=claude-code domain=services/_governance/prose_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="惜字如金检查器(轨二里程碑六, 最难最后做)。规则只做两件确定的事:展开已知固定缩写(abbrev_expansions) + 筛异常过短/密度高的可疑段;真正判'是否过度压缩+口语化改写'交性价比模型, 默认只建议不自动改。"
# [OMNI] why="纯正则判不了惜字如金(语境依赖), 硬做会误杀正常简洁表达;所以确定性只碰已知缩写, 开放判断交 LLM 且只建议。"
# [OMNI] tags=governance,conciseness,llm-suggest-only
# [OMNI] material_id="material:governance.prose_steward.compress.py"
"""惜字如金检查器(轨二 · 第三类, 最难)。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from . import terms as T
from .lang import _is_chinese_segment, discover_targets, report_dir

_CJK_RE = re.compile(r"[一-鿿]")
# 常见中文功能词/助词: 多=口语自然; 少=可能压缩成"电报体"
_FUNCTION_WORDS = ["的", "了", "是", "在", "和", "与", "把", "被", "为", "对", "从",
                   "到", "就", "都", "也", "这", "那", "个", "可以", "进行", "一个",
                   "我们", "它", "他", "她", "并", "且", "或", "而"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan_abbrev(abs_path: Path, abbrevs: list[dict], *, root: Path) -> list[dict]:
    """确定性: 命中已知固定缩写 → 建议展开。"""
    try:
        rel = str(abs_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(abs_path)
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    in_fence = False
    for lineno, raw in enumerate(text.split("\n"), start=1):
        if raw.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or raw.lstrip().startswith(("<!-- [OMNI]", "# [OMNI]")):
            continue
        for ab in abbrevs:
            short = ab.get("short", "")
            if short and short in raw:
                out.append({"doc": rel, "line": lineno, "category": "known_abbrev",
                            "short": short, "full": ab.get("full", ""), "snippet": raw.strip()[:120]})
    return out


def _suspicious_segments(abs_path: Path, *, root: Path) -> list[dict]:
    """启发式筛'电报体'可疑段: 中文为主、较短、功能词极少 → 信息密度异常高。"""
    try:
        rel = str(abs_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(abs_path)
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    in_fence = False
    for lineno, raw in enumerate(text.split("\n"), start=1):
        s = raw.strip()
        if s.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or s.startswith(("#", "<!--", "-", "*", "|", ">", "1.", "2.")):
            continue  # 跳标题/列表/表格/注释(它们本就简短)
        cjk = len(_CJK_RE.findall(s))
        if cjk < 12 or cjk > 40:
            continue  # 太短无意义判/太长不算单句压缩
        if not _is_chinese_segment(s):
            continue
        # 必须是"一句连续密文": 标点切分块极少(电报体不分句)
        if len(re.findall(r"[，,。.；;、）)！!？?]", s)) > 1:
            continue
        func = sum(s.count(w) for w in _FUNCTION_WORDS)
        density = func / cjk            # 功能词占比: 越低越像电报体
        distinct = len(set(_CJK_RE.findall(s))) / cjk  # 去重字密度: 越高越信息密集
        if density < 0.04 and distinct > 0.85:
            out.append({"doc": rel, "line": lineno, "category": "suspicious_terse",
                        "cjk": cjk, "func_ratio": round(density, 3),
                        "distinct": round(distinct, 2), "snippet": s[:140]})
    return out


_SYS = """你是中文写作"惜字如金/电报体"判别员。给你若干疑似过度压缩的句子。\
"惜字如金"指: 用一个字代两个字、用只有作者懂的短代称、用压缩语法替正常口语(例 "好恶词" 应为 "表达了用户喜好或厌恶的词汇")。\
对每句判断:
- over_compressed: 确实过度压缩, 给一句口语化、完整的改写建议。
- acceptable: 正常简洁/专业表达, 放过。
只输出 JSON, 只建议不强制。"""

_SCHEMA = {
    "type": "object", "required": ["judgments"],
    "properties": {"judgments": {"type": "array", "items": {
        "type": "object", "required": ["line", "verdict"],
        "properties": {"line": {"type": "integer"},
                       "verdict": {"type": "string", "enum": ["over_compressed", "acceptable"]},
                       "rewrite": {"type": "string"}, "reason": {"type": "string"}}}}},
}


def run_compress_scan(*, include_code: bool = False, limit: int | None = None,
                      model: str | None = None, llm_judge: bool = True,
                      root: Path | None = None, echo: Any = None) -> dict[str, Any]:
    base = root or omni_workspace_root()
    model = model or "qwen3.6-plus"  # 本机实测稳
    abbrevs = T.abbrev_expansions(base)
    targets = discover_targets(include_code=include_code, root=base)
    if limit:
        targets = targets[:limit]
    abbrev_hits: list[dict] = []
    suspects: list[dict] = []
    for p in targets:
        abbrev_hits.extend(scan_abbrev(p, abbrevs, root=base))
        suspects.extend(_suspicious_segments(p, root=base))

    # 可疑段按"最像电报体"(func_ratio 最低)优先, 截断喂 LLM 上限(透明记数)
    _MAX_SUSPECT = 80
    suspects.sort(key=lambda s: s.get("func_ratio", 1.0))
    suspects_capped = len(suspects) > _MAX_SUSPECT
    suspects_llm = suspects[:_MAX_SUSPECT]

    over_compressed: list[dict] = []
    if llm_judge and suspects_llm:
        from omnicompany.runtime.llm.structured import call_json
        # 按 doc 分组, 每块 ≤10 句
        by_doc: dict[str, list[dict]] = {}
        for s in suspects_llm:
            by_doc.setdefault(s["doc"], []).append(s)
        for doc, segs in by_doc.items():
            for i in range(0, len(segs), 10):
                chunk = segs[i:i + 10]
                items = "\n".join(f"- L{c['line']}: {c['snippet']}" for c in chunk)
                try:
                    res = call_json(system=_SYS, user=f"文档 {doc}:\n{items}", schema=_SCHEMA,
                                    model=model, caller="prose_steward.compress",
                                    max_tokens=2500, max_corrections=2)
                    for j in (res or {}).get("judgments", []):
                        if j.get("verdict") == "over_compressed":
                            over_compressed.append({"doc": doc, "line": j.get("line"),
                                                    "rewrite": j.get("rewrite", ""),
                                                    "reason": j.get("reason", "")})
                except Exception as e:  # noqa: BLE001
                    if echo:
                        echo(f"  LLM 失败 {doc} 块{i//10}: {str(e)[:80]}")

    payload = {"kind": "prose_compress", "generated_at": _now(), "model": model,
               "scanned_files": len(targets), "abbrev_hits": abbrev_hits,
               "suspicious_segments": len(suspects), "suspects_judged": len(suspects_llm),
               "suspects_capped": suspects_capped, "over_compressed": over_compressed,
               "counts": {"known_abbrev": len(abbrev_hits), "suspicious": len(suspects),
                          "over_compressed": len(over_compressed)}}
    stamp = _now().replace(":", "").replace("-", "")[:15]
    (report_dir() / f"prose_compress-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir() / "prose_compress-latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if echo:
        echo(f"[prose-compress] 扫 {len(targets)} | 已知缩写 {len(abbrev_hits)} | 可疑段 {len(suspects)} | 过度压缩 {len(over_compressed)}")
    return payload

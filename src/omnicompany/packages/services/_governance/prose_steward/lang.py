# [OMNI] origin=claude-code domain=services/_governance/prose_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="非中文泄漏检查器(轨二里程碑四)。CJK 占比定'本段应为中文', 白名单滤掉技术专名, 剩余非白名单英文 token 送性价比模型判该保留/改中文/无法判断, 把'凡英文皆报'收敛到'非必要英文才报'。只报不改。"
# [OMNI] why="该用中文处混英文会让后续场景突然变英文。误报最大源=凡英文皆报;白名单+LLM 两段降误报(对齐 DocPrism local-categorize+external-filter)。"
# [OMNI] tags=governance,language-leak,llm-semantic,deterministic-prefilter
# [OMNI] material_id="material:governance.prose_steward.lang.py"
"""非中文泄漏检查器(轨二 · 第一类, 误报最可控先起步)。

判定链:
  1. 确定性: 按段算 CJK 占比, 定"本段应为中文"; 段内取非白名单英文 token 作候选; forbidden_aliases 直接命中(给建议中文, 不走 LLM)。
  2. 性价比模型: 对去重后的候选 token(+样本上下文)逐条判 keep / change_to_chinese / unsure + 理由 + 建议中文。
  3. 只报 findings(不自动改); change_to_chinese 的可进 human-inbox。
单一真源 = docs/standards/prose_terms.yaml(经 terms.py)。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from . import terms as T

_CJK_RE = re.compile(r"[一-鿿]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_TOKEN_RE = re.compile(r"[A-Za-z]{3,15}")  # 纯字母 3-15(滤掉 ID/版本/缩写碎片)
# 非中文泄漏默认用 qwen3.6-plus(本机实测稳; deepseek 对长 batch 偶发返非法 JSON)
_DEFAULT_PROSE_MODEL = "qwen3.6-plus"
_MAX_CANDIDATES = 60  # 单次 LLM 判的去重 token 上限(频次优先; 超出记日志下轮再判, 不静默吞)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_URL_RE = re.compile(r"https?://\S+")
_SKIP_DIR = ("_archive", "_graveyard", "__pycache__", "node_modules", ".git", "venv", "data")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_dir() -> Path:
    d = omni_workspace_root() / "data" / "governance" / "prose_steward"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_chinese_segment(line: str) -> bool:
    """本段应为中文: 含 ≥2 CJK 字, 且 CJK 占字母+CJK 的比例 > 0.25。"""
    cjk = len(_CJK_RE.findall(line))
    if cjk < 2:
        return False
    latin = len(_LATIN_RE.findall(line))
    return cjk / (cjk + latin + 1e-9) > 0.25


def _file_is_chinese_prose(abs_path: Path) -> bool:
    """文件级闸: 整体 CJK 占比 > 0.5 才算'本该中文'的文档。

    SKILL.md/技术规格这类天生中英混排、英文密集的不进 token 级泄漏检测(否则误报爆炸)。
    """
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return False
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    return cjk >= 30 and cjk / (cjk + latin + 1e-9) > 0.5


def discover_targets(*, include_code: bool, root: Path) -> list[Path]:
    out: list[Path] = []
    for p in (root / "docs").rglob("*.md"):
        if not any(part in _SKIP_DIR for part in p.parts):
            out.append(p)
    for p in (root / "src").rglob("SKILL.md"):
        if not any(part in _SKIP_DIR for part in p.parts):
            out.append(p)
    if include_code:
        for p in (root / "src").rglob("*.py"):
            if not any(part in _SKIP_DIR for part in p.parts):
                out.append(p)
    return out


def scan_leaks(abs_path: Path, whitelist: set[str], forbidden: dict[str, str],
               *, root: Path, code_only_comments: bool = False) -> tuple[list[dict], dict[str, dict]]:
    """返回 (forbidden 确定性命中列表, 候选 token→{token,sample,locs} 待 LLM)。"""
    try:
        rel = str(abs_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(abs_path)
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], {}
    is_py = abs_path.suffix == ".py"
    forbidden_hits: list[dict] = []
    cand: dict[str, dict] = {}
    in_fence = False
    for lineno, raw in enumerate(text.split("\n"), start=1):
        if raw.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = raw
        if is_py:
            if "#" not in raw:
                continue
            line = raw.split("#", 1)[1]  # 只看注释部分
            if "[OMNI]" in line:
                continue
        line = _URL_RE.sub(" ", _INLINE_CODE_RE.sub(" ", line))  # 去行内代码/URL
        if not _is_chinese_segment(line):
            continue
        for m in _TOKEN_RE.finditer(line):
            tok = m.group(0)
            low = tok.lower()
            if low in whitelist:
                continue
            if low in forbidden:
                forbidden_hits.append({"doc": rel, "line": lineno, "token": tok,
                                       "suggest_zh": forbidden[low], "snippet": raw.strip()[:140]})
                continue
            c = cand.setdefault(low, {"token": tok, "sample": raw.strip()[:140], "locs": []})
            c["locs"].append({"doc": rel, "line": lineno})
    return forbidden_hits, cand


_JUDGE_SYS = """你是中文技术文档的"非中文泄漏"判别员。给你若干在中文句子里出现的英文 token + 样本上下文。\
判断每个 token 该不该保留英文:
- keep: 技术专名/标识符/缩写/产品名/代码符号, 中文表达反而别扭 → 保留。
- change_to_chinese: 本该用中文却写了英文的普通词(动词/形容词/普通名词), 给出建议中文。
- unsure: 拿不准 → 归 unsure(宁可不动)。
每条给一句理由。只输出 JSON。"""

_JUDGE_SCHEMA = {
    "type": "object", "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "required": ["token", "verdict"],
        "properties": {"token": {"type": "string"},
                       "verdict": {"type": "string", "enum": ["keep", "change_to_chinese", "unsure"]},
                       "suggest_zh": {"type": "string"}, "reason": {"type": "string"}}}}},
}


def run_lang_scan(*, include_code: bool = False, limit: int | None = None,
                  model: str | None = None, push_inbox: bool = False,
                  root: Path | None = None, echo: Any = None) -> dict[str, Any]:
    """非中文泄漏全量扫描 + LLM 复判。"""
    from omnicompany.runtime.llm.structured import call_json
    base = root or omni_workspace_root()
    model = model or _DEFAULT_PROSE_MODEL
    whitelist = T.english_whitelist(base)
    forbidden = T.forbidden_aliases(base)
    raw_targets = discover_targets(include_code=include_code, root=base)
    # 文件级闸: 只留'本该中文'的文档(英文密集的 SKILL/技术规格跳过)
    targets = [p for p in raw_targets if include_code and p.suffix == ".py" or _file_is_chinese_prose(p)]
    skipped_english = len(raw_targets) - len(targets)
    if limit:
        targets = targets[:limit]

    all_forbidden: list[dict] = []
    merged: dict[str, dict] = {}
    for p in targets:
        fh, cand = scan_leaks(p, whitelist, forbidden, root=base)
        all_forbidden.extend(fh)
        for low, c in cand.items():
            if low not in merged:
                merged[low] = c
            else:
                merged[low]["locs"].extend(c["locs"])

    # LLM 逐条判去重候选(分块 ≤20)。出现频次高的优先(更可能是真泄漏词)。
    tokens_all = sorted(merged, key=lambda t: -len(merged[t]["locs"]))
    capped = len(tokens_all) > _MAX_CANDIDATES
    tokens = tokens_all[:_MAX_CANDIDATES]
    verdicts: dict[str, dict] = {}
    if echo:
        echo(f"[prose-lang] 扫 {len(targets)} 文件(跳过英文密集 {skipped_english}) | "
             f"forbidden 命中 {len(all_forbidden)} | 候选 token {len(tokens_all)}"
             + (f" → 本次判前 {_MAX_CANDIDATES}(其余下轮)" if capped else ""))
    for i in range(0, len(tokens), 15):
        chunk = tokens[i:i + 15]
        items = "\n".join(f"- {merged[t]['token']} | 上下文: {merged[t]['sample']}" for t in chunk)
        try:
            res = call_json(system=_JUDGE_SYS, user=f"判断这些 token:\n{items}",
                            schema=_JUDGE_SCHEMA, model=model, caller="prose_steward.lang",
                            max_tokens=2500, max_corrections=2)
            for v in (res or {}).get("verdicts", []):
                verdicts[str(v.get("token", "")).lower()] = v
        except Exception as e:  # noqa: BLE001
            if echo:
                echo(f"  LLM 失败(块 {i//20}): {str(e)[:100]}")

    leaks: list[dict] = []
    for low, c in merged.items():
        v = verdicts.get(low) or verdicts.get(c["token"].lower()) or {}
        if v.get("verdict") == "change_to_chinese":
            leaks.append({"token": c["token"], "suggest_zh": v.get("suggest_zh", ""),
                          "reason": v.get("reason", ""), "occurrences": len(c["locs"]),
                          "locs": c["locs"][:8], "sample": c["sample"]})

    inbox_opened = 0
    if push_inbox and (leaks or all_forbidden):
        from omnicompany.runtime.buses import HumanBus, HumanKind
        hb = HumanBus()
        body = ["【非中文泄漏·建议改中文】"]
        body += [f"  {x['token']} → {x['suggest_zh']}  ({x['occurrences']}处) {x['reason'][:40]}" for x in leaks[:15]]
        if all_forbidden:
            body.append("【禁用英文代称(确定性)】")
            body += [f"  {x['token']} → {x['suggest_zh']}  @ {x['doc']}:{x['line']}" for x in all_forbidden[:10]]
        hb.ask(question="语言治理(非中文泄漏): " + "\n".join(body), kind=HumanKind.HUMAN_BLOCKING,
               context={"facility": "prose_steward.lang", "leaks": leaks, "forbidden": all_forbidden},
               source="prose_steward.lang")
        inbox_opened = 1

    payload = {
        "kind": "prose_lang", "generated_at": _now(), "model": model,
        "scanned_files": len(targets), "skipped_english_files": skipped_english,
        "forbidden_hits": all_forbidden, "candidate_tokens": len(tokens_all),
        "judged_tokens": len(tokens), "capped": capped, "leaks": leaks, "inbox_opened": inbox_opened,
        "counts": {"forbidden": len(all_forbidden), "leak_change": len(leaks),
                   "kept_or_unsure": len(tokens) - len(leaks)},
    }
    stamp = _now().replace(":", "").replace("-", "")[:15]
    (report_dir() / f"prose_lang-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir() / "prose_lang-latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload

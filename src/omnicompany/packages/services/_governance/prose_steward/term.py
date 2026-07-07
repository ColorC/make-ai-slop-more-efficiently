# [OMNI] origin=claude-code domain=services/_governance/prose_steward ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="术语一致/代称混乱/易过时引用检查器(轨二里程碑五)。从单一真源 prose_terms.yaml 确定性命中变体→建议统一到 canonical、易过时词→提示换稳定指代;并把同一真源生成 Vale/CSpell/reject.txt 规则(全派生不在多处各写)。"
# [OMNI] why="术语漂移靠规则可高确信命中;规则必须从单一真源生成, 否则三处各写一份又漂移。"
# [OMNI] tags=governance,terminology,vale,cspell,single-source-of-truth
# [OMNI] material_id="material:governance.prose_steward.term.py"
"""术语一致 / 代称 / 易过时检查器 + 从真源生成 Vale/CSpell(轨二 · 第二类)。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from . import terms as T
from .lang import _is_chinese_segment, _SKIP_DIR, discover_targets, report_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan_terms(abs_path: Path, consistency: list[dict], outdated: list[dict],
               *, root: Path) -> list[dict]:
    """确定性: 命中术语变体(建议统一) + 易过时词(建议换稳定指代)。"""
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
        line = raw
        for grp in consistency:
            canon = grp.get("canonical", "")
            for var in grp.get("variants", []) or []:
                if var and var in line and canon:
                    out.append({"doc": rel, "line": lineno, "category": "term_inconsistent",
                                "found": var, "canonical": canon, "note": grp.get("note", ""),
                                "snippet": raw.strip()[:120]})
        if _is_chinese_segment(line):
            for od in outdated:
                pat = od.get("pattern", "")
                if pat and pat in line:
                    out.append({"doc": rel, "line": lineno, "category": "easily_outdated",
                                "found": pat, "note": od.get("note", ""), "snippet": raw.strip()[:120]})
    return out


def generate_lint_configs(root: Path | None = None) -> dict[str, Any]:
    """从单一真源 prose_terms.yaml 生成 Vale style + CSpell flagWords + reject.txt。

    证明"同一份表生成三处"(SSOT), 不在三处各写。产物落 data/governance/prose_steward/lint/。
    Vale 二进制装没装无所谓—— scan_terms 已用 Python 做了同样的确定性命中; 这些配置是给装了 Vale/CSpell 的环境复用。
    """
    base = root or omni_workspace_root()
    consistency = T.term_consistency(base)
    forbidden = T.forbidden_aliases(base)
    outdated = T.easily_outdated(base)
    out_dir = report_dir() / "lint"
    vale_style = out_dir / "styles" / "Omni"
    vale_style.mkdir(parents=True, exist_ok=True)

    # 1. Vale 术语一致(substitution: 变体 → canonical)
    swap = {}
    for grp in consistency:
        for var in grp.get("variants", []) or []:
            swap[var] = grp.get("canonical", "")
    vale_term = {
        "extends": "substitution", "message": "术语用 '%s' 而非 '%s'(统一到规范名)",
        "level": "warning", "ignorecase": False, "swap": swap,
    }
    (vale_style / "Terminology.yml").write_text(
        json.dumps(vale_term, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. Vale 易过时(existence)
    vale_outdated = {
        "extends": "existence", "message": "'%s' 是易过时指代, 换绝对日期/版本/稳定指代",
        "level": "suggestion", "ignorecase": False,
        "tokens": [od.get("pattern", "") for od in outdated if od.get("pattern")],
    }
    (vale_style / "Outdated.yml").write_text(
        json.dumps(vale_outdated, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3. CSpell flagWords(forbidden en → 建议)
    flag_words = [f"{en}:{zh}" if zh else en for en, zh in forbidden.items()]
    cspell = {"version": "0.2", "language": "en", "flagWords": flag_words,
              "words": sorted(T.english_whitelist(base))}
    (out_dir / "cspell.json").write_text(
        json.dumps(cspell, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. reject.txt(forbidden + 所有变体, 一行一个)
    reject = sorted(set(list(forbidden) + list(swap)))
    (out_dir / "reject.txt").write_text("\n".join(reject) + "\n", encoding="utf-8")

    # 5. .vale.ini
    (out_dir / ".vale.ini").write_text(
        "StylesPath = styles\n\n[*.md]\nBasedOnStyles = Omni\n", encoding="utf-8")

    return {"out_dir": str(out_dir), "vale_swap_terms": len(swap),
            "vale_outdated_tokens": len(vale_outdated["tokens"]),
            "cspell_flagwords": len(flag_words), "reject_lines": len(reject)}


def run_term_scan(*, include_code: bool = False, limit: int | None = None,
                  gen_configs: bool = True, root: Path | None = None,
                  echo: Any = None) -> dict[str, Any]:
    base = root or omni_workspace_root()
    consistency = T.term_consistency(base)
    outdated = T.easily_outdated(base)
    targets = discover_targets(include_code=include_code, root=base)
    if limit:
        targets = targets[:limit]
    findings: list[dict] = []
    for p in targets:
        findings.extend(scan_terms(p, consistency, outdated, root=base))
    gen = generate_lint_configs(base) if gen_configs else None
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    payload = {"kind": "prose_term", "generated_at": _now(), "scanned_files": len(targets),
               "findings": findings, "counts": counts, "lint_generated": gen}
    stamp = _now().replace(":", "").replace("-", "")[:15]
    (report_dir() / f"prose_term-{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir() / "prose_term-latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if echo:
        echo(f"[prose-term] 扫 {len(targets)} 文件 | 命中 {len(findings)} | 生成 lint 配置: {gen}")
    return payload

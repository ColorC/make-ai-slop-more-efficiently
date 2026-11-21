# [OMNI] origin=claude-code ts=2026-06-20 type=script
# OMNI-PERSISTENT-SCRIPT owner=platform purpose="领域插件契约校验: 每个 domain 必须有合规 SKILL.md(name==目录名)"
"""validate_domains —— 领域插件契约校验器。

对齐 agentskills.io: 每个 `packages/domains/<name>/` 应有一个 `SKILL.md`, 其 frontmatter
`name` 必须等于目录名, `description` 非空。这是"通用核心 + bring-your-own-domain"平台的
插件契约——guardian 式的确定性检查, 不调模型。

用法:
    python scripts/validate_domains.py            # 校验所有域, 有问题退非零
    python scripts/validate_domains.py --json      # 机器可读
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAINS = REPO_ROOT / "src" / "omnicompany" / "packages" / "domains"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_DESC_RE = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


def _parse_skill(p: Path) -> tuple[str | None, str | None]:
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    m = _FRONTMATTER_RE.search(text)
    block = m.group(1) if m else text
    name = (_NAME_RE.search(block) or [None, None])[1] if _NAME_RE.search(block) else None
    desc = (_DESC_RE.search(block) or [None, None])[1] if _DESC_RE.search(block) else None
    return name, desc


def validate() -> list[dict]:
    results: list[dict] = []
    if not DOMAINS.is_dir():
        return results
    for d in sorted(DOMAINS.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")) or d.name == "__pycache__":
            continue
        issues: list[str] = []
        skill = d / "SKILL.md"
        if not skill.is_file():
            issues.append("缺 SKILL.md")
        else:
            name, desc = _parse_skill(skill)
            if not name:
                issues.append("SKILL.md frontmatter 缺 name")
            elif name != d.name:
                issues.append(f"name={name!r} != 目录名 {d.name!r}")
            if not desc:
                issues.append("SKILL.md frontmatter 缺 description")
            elif len(desc) < 20:
                issues.append("description 过短(<20 字符)")
        results.append({"domain": d.name, "ok": not issues, "issues": issues})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    results = validate()
    bad = [r for r in results if not r["ok"]]
    if args.json:
        print(json.dumps({"results": results, "ok": not bad}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "✅" if r["ok"] else "✗"
            print(f"  {mark} {r['domain']}" + ("" if r["ok"] else "  — " + "; ".join(r["issues"])))
        print(f"\n{len(results) - len(bad)}/{len(results)} 域合规" +
              ("" if not bad else f"; {len(bad)} 个有问题"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

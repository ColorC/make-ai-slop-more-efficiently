# [OMNI] origin=claude-code purpose=omni034-scan ts=2026-04-18
"""扫描所有 DESIGN.md 的 OMNI-034 合规状态（B 级收尾验证）。"""
import sys
from pathlib import Path

sys.path.insert(0, "e:/WindowsWorkspace/omnicompany/src")
from omnicompany.packages.services.guardian.rules.design_md import (
    _is_design_md, _extract_status, _has_omnimark, _missing_sections,
)


class Ctx:
    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.content = content


def main() -> None:
    ROOT = Path("e:/WindowsWorkspace/omnicompany/src/omnicompany")
    invalid: list[tuple[str, str]] = []
    stats: dict[str, int] = {}

    for p in ROOT.rglob("DESIGN.md"):
        rel = str(p.relative_to(ROOT.parent)).replace("\\", "/")
        content = p.read_text(encoding="utf-8")
        ctx = Ctx(rel, content)
        if not _is_design_md(ctx):
            continue
        if not _has_omnimark(content):
            invalid.append((rel, "missing OmniMark head"))
            continue
        status = _extract_status(content)
        if status not in {"skeleton", "design", "active", "deprecated"}:
            invalid.append((rel, f"invalid status={status!r}"))
            continue
        missing = _missing_sections(content)
        if missing:
            invalid.append((rel, f"missing sections: {missing}"))
            continue
        stats[status] = stats.get(status, 0) + 1

    total = sum(stats.values())
    print(f"OMNI-034 扫描结果（合规 {total} / 不合规 {len(invalid)}）：")
    for s in ("active", "design", "skeleton", "deprecated"):
        if s in stats:
            print(f"  {s:10} {stats[s]}")
    print()
    if invalid:
        print(f"不合规 {len(invalid)} 份：")
        for path, reason in invalid:
            print(f"  - {path} — {reason}")
    else:
        print("无不合规项")


if __name__ == "__main__":
    main()

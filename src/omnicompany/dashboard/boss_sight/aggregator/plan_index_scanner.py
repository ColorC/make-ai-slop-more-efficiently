# [OMNI] origin=ai-ide ts=2026-05-23 type=infra
# [OMNI] material_id="material:dashboard.boss_sight.aggregator.plan_index_scanner.py"
"""plan_index_scanner — 扫 docs/plans/ 出 plan 索引给总控 ctx 用.

落实 T1.3.1-2: plan 索引装载.

按 ground_truth § 7.3 的事实:
- plan 路径 docs/plans/{category}/[timestamp]ID/plan.md
- project.md 在 category 根, frontmatter `plans:` 自维护清单
- 没有全局索引文件

策略:
1. 扫 docs/plans/{category}/project.md, 读 frontmatter `plans:` 字段
2. 对每个 plan, 读 plan.md frontmatter + 顶部正文 (拿 todo 进度 / status)
3. 拼成 PlanIndexEntry 列表

不修改任何文件, 纯读.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from omnicompany.dashboard.controlplane.plans import parse_plan_frontmatter
from omnicompany.packages.services._core.omnicompany.formats import PLAN

_log = logging.getLogger(__name__)


# ------------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------------


@dataclass
class PlanIndexEntry:
    """单条 plan 的索引信息."""

    plan_id: str                  # category/[timestamp]ID 形式
    category: str                 # voxelcraft / dashboard / cli 等
    plan_path: str                # 相对 workspace 根的路径
    project_path: str | None = None  # 关联 project.md (如有)
    title: str | None = None
    status: str | None = None     # 从 frontmatter 读
    todo_done: int = 0
    todo_total: int = 0
    last_modified_ts: str | None = None  # ISO 8601
    extra: dict = field(default_factory=dict)
    format_id: str = PLAN.id

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------------
# Scanner
# ------------------------------------------------------------------------


# frontmatter 解析改用 controlplane.plans 的 yaml 权威 (parse_plan_frontmatter);
# 本 scanner 只保留 todo 计数这层自家聚合逻辑。
_TODO_LINE_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+", re.MULTILINE)


def _count_todos(text: str) -> tuple[int, int]:
    """从 plan.md 正文里数 markdown todo. 返 (done, total)."""
    done = 0
    total = 0
    for m in _TODO_LINE_RE.finditer(text):
        total += 1
        if m.group(1).lower() == "x":
            done += 1
    return done, total


class PlanIndexScanner:
    """扫 docs/plans/ 出索引."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root)
        self.plans_dir = self.workspace_root / "docs" / "plans"

    def scan(self) -> list[PlanIndexEntry]:
        if not self.plans_dir.is_dir():
            _log.warning("plans_dir 不存在: %s", self.plans_dir)
            return []

        out: list[PlanIndexEntry] = []
        for category_dir in self.plans_dir.iterdir():
            if not category_dir.is_dir():
                continue
            project_path = category_dir / "project.md"
            project_path_str = (
                project_path.relative_to(self.workspace_root).as_posix()
                if project_path.is_file()
                else None
            )
            for plan_dir in category_dir.iterdir():
                if not plan_dir.is_dir():
                    continue
                plan_md = plan_dir / "plan.md"
                # 也支持 brief.md 当 entry (有些只写 brief)
                entry_md = plan_md if plan_md.is_file() else (plan_dir / "brief.md")
                if not entry_md.is_file():
                    continue
                try:
                    text = entry_md.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                fm = parse_plan_frontmatter(entry_md)
                done, total = _count_todos(text)
                try:
                    stat = entry_md.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                except OSError:
                    mtime = None
                entry = PlanIndexEntry(
                    plan_id=f"{category_dir.name}/{plan_dir.name}",
                    category=category_dir.name,
                    plan_path=entry_md.relative_to(self.workspace_root).as_posix(),
                    project_path=project_path_str,
                    title=fm.get("title") or plan_dir.name,
                    status=fm.get("status"),
                    todo_done=done,
                    todo_total=total,
                    last_modified_ts=mtime,
                )
                out.append(entry)

        # 按最近修改倒序
        out.sort(key=lambda e: e.last_modified_ts or "", reverse=True)
        return out

    def to_material_payload(self, entries: Iterable[PlanIndexEntry]) -> dict:
        """转成 ctx 注入 Material 的 payload 形式 (平铺 dict)."""
        return {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "total": len(list(entries)) if not isinstance(entries, list) else len(entries),
            "plans": [e.to_dict() for e in entries],
        }


__all__ = ["PlanIndexEntry", "PlanIndexScanner"]

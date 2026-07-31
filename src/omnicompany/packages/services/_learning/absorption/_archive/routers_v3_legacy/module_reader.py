# [OMNI] origin=claude-code domain=services/absorption ts=2026-04-13T00:00:00Z type=router
# [OMNI] material_id="material:learning.absorption.v3_legacy.module_content_reader.router.py"
"""module_reader — V3 ModuleReaderRouter（RULE）

输入：absorption.important-modules（模块清单 + detail_view）
输出：absorption.module.code（每个模块的实际代码内容）

策略：
  - 优先用 detail_view（已有符号树，省文件预算）
  - 若 detail_view 太短（<200行信息）或模块 P0，追加 local_read 完整内容
  - 最多读 15 个不同文件（防止预算爆炸）

设计文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md §三.Format 3
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

_MAX_READ_FILES = 15
_DETAIL_LINES_THRESHOLD = 30   # detail_view 行数少于此值时补充 local_read
_MAX_FILE_BYTES = 512 * 1024   # 512KB 上限


def _local_read(repo_local_path: str, rel_path: str, max_lines: int = 600) -> str:
    """读取文件内容，带行号，限制最大行数。"""
    try:
        target = Path(repo_local_path) / rel_path
        if not target.exists() or not target.is_file():
            return f"[ERROR: {rel_path} not found]"
        if target.stat().st_size > _MAX_FILE_BYTES:
            return f"[SKIPPED: {rel_path} too large ({target.stat().st_size} bytes)]"
        content = target.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total = len(lines)
        segment = lines[:max_lines]
        numbered = "\n".join(f"{i + 1:5d}\t{line}" for i, line in enumerate(segment))
        suffix = f"\n... ({total - max_lines} more lines)" if total > max_lines else ""
        return f"=== {rel_path} ({total} lines) ===\n{numbered}{suffix}"
    except Exception as e:
        return f"[ERROR reading {rel_path}: {e}]"


class ModuleReaderRouter(Router):
    """V3 模块读取节点（RULE）。

    将 important-modules 里每个模块的 detail_view 和/或 local_read 内容
    整合为 module.code 格式，每个模块一条记录。
    """

    DESCRIPTION = (
        "V3 模块读取：RULE 节点，按模块展开 detail_view，"
        "P0 或符号信息不足时补充 local_read，≤15 文件预算"
    )
    FORMAT_IN = "absorption.important-modules"
    FORMAT_OUT = "absorption.module.code"

    def run(self, input_data: Any) -> Verdict:
        repo_name = input_data.get("repo_name", "unknown")
        repo_local_path = input_data.get("repo_local_path", "")
        modules: list[dict] = input_data.get("modules") or []

        if not modules:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=dict(input_data),
                diagnosis="ModuleReader: modules 为空",
            )

        files_read: list[str] = []
        module_readings: list[dict] = []

        for m in modules:
            path = m.get("path", "")
            gap_id = m.get("gap_id", "?")
            priority = m.get("priority", "P2")
            detail_view = m.get("detail_view", "")
            detail_lines = len(detail_view.splitlines()) if detail_view else 0

            # 决定读取方式
            need_local = (
                priority == "P0"
                or detail_lines < _DETAIL_LINES_THRESHOLD
                or not detail_view
            )
            budget_ok = len(files_read) < _MAX_READ_FILES

            if need_local and budget_ok and repo_local_path and path:
                full_content = _local_read(repo_local_path, path)
                if path not in files_read:
                    files_read.append(path)
                if detail_view:
                    content = f"## 符号树\n{detail_view}\n\n## 完整代码\n{full_content}"
                    read_method = "detail_view+local_read"
                else:
                    content = full_content
                    read_method = "local_read"
            elif detail_view:
                content = f"## 符号树\n{detail_view}"
                read_method = "detail_view"
            else:
                content = f"[No content available for {path}]"
                read_method = "none"

            line_count = len(content.splitlines())
            module_readings.append({
                "path": path,
                "gap_id": gap_id,
                "priority": priority,
                "content": content,
                "line_count": line_count,
                "read_method": read_method,
            })

        n_local = sum(1 for r in module_readings if "local_read" in r["read_method"])
        n_detail = sum(1 for r in module_readings if r["read_method"] == "detail_view")

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "repo_name": repo_name,
                "module_readings": module_readings,
                "files_read": files_read,
            },
            confidence=1.0,
            diagnosis=(
                f"ModuleReader: {len(module_readings)} 模块 "
                f"({n_local} local_read, {n_detail} detail_view_only)"
            ),
            granted_tags=["domain.absorption", "stage.v3.reader"],
        )

# [OMNI] origin=claude-code domain=runtime/agent_crystallize/pending_queue ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.pending_queue.disk_writer.py"
"""pending_queue — SpecPatch 落盘到 data/crystallize/pending/<target_router>/<patch_id>.md

人审流程:
  1. crystallizer 产出 patch → write_pending_patch()
  2. 人读 .md, 决定批准 / 拒绝
  3. 批准: 移动到 approved/ 或人工编辑 Router 源码
  4. 拒绝: 移动到 rejected/
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from omnicompany.core.guarded_write import write_file as _guarded_write

from .protocol import SpecPatch


def _crystallize_root() -> Path:
    """统一根目录: data/_runtime/crystallize/ (2026-04-21 B4 从 data/crystallize/ 迁移)."""
    from omnicompany.core.config import resolve_runtime_data_dir
    return resolve_runtime_data_dir("crystallize")


def write_pending_patch(
    patch: SpecPatch,
    output_dir: str | None = None,
) -> Path:
    """落盘 SpecPatch 到 pending/ 队列.

    Args:
        patch: 要落盘的 patch.
        output_dir: 若指定则使用此目录的 pending/, 否则走 data/crystallize/pending/.

    Returns:
        写入的 .md 路径.
    """
    if output_dir:
        root = Path(output_dir)
    else:
        root = _crystallize_root()
    pending_dir = root / "pending" / (patch.target_router or "unknown")
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / f"{patch.patch_id}_{patch.crystallizer}.md"

    ts = datetime.fromtimestamp(patch.created_ts).strftime("%Y-%m-%d %H:%M:%S")

    current_preview = _preview_value(patch.current_value)
    proposed_preview = _preview_value(patch.proposed_value)

    md = [
        f"# SpecPatch — {patch.title or '(no title)'}",
        "",
        f"- **patch_id**: `{patch.patch_id}`",
        f"- **crystallizer**: `{patch.crystallizer}`",
        f"- **target_router**: `{patch.target_router}`",
        f"- **patch_type**: `{patch.patch_type}`",
        f"- **confidence**: {patch.confidence:.2f}",
        f"- **created**: {ts}",
        "",
        "## 建议理由",
        "",
        patch.rationale or "(无)",
        "",
        "## 证据 (from agent trace)",
        "",
    ]
    for e in patch.evidence:
        md.append(f"- {e}")
    if not patch.evidence:
        md.append("(无)")

    md.extend([
        "",
        "## 当前值",
        "",
        "```",
        current_preview,
        "```",
        "",
        "## 建议值",
        "",
        "```",
        proposed_preview,
        "```",
        "",
        "## 审批",
        "",
        "- [ ] 批准 (移动此文件到 `approved/`, 由人工应用到 Router 源码)",
        "- [ ] 拒绝 (移动此文件到 `rejected/`, 标注原因在此文档末尾)",
        "",
    ])

    _guarded_write(
        path, "\n".join(md),
        writer="internal-engine",
        domain="crystallize",
        purpose="pending SpecPatch awaiting human review",
    )
    return path


def list_pending_patches(output_dir: str | None = None) -> list[Path]:
    """列出当前 pending 队列里所有 patch md 路径."""
    if output_dir:
        root = Path(output_dir) / "pending"
    else:
        root = _crystallize_root() / "pending"
    if not root.exists():
        return []
    return sorted(root.rglob("*.md"))


def _preview_value(v: Any) -> str:
    """把 patch value 转为可读预览字符串."""
    if v is None:
        return "(无)"
    if isinstance(v, str):
        return v[:2000]
    try:
        return json.dumps(v, ensure_ascii=False, indent=2)[:2000]
    except Exception:
        return str(v)[:2000]

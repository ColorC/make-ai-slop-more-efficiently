# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.patch_applier.file_writer.py"
"""PatchApplierWorker — Repair Team Worker (Router 修复分组 · #8).

Worker 协议:
  FORMAT_IN  = diag.repair.validated-patch
  FORMAT_OUT = diag.repair.applied

职责: 备份源文件 → 将 diff 写入源文件 → 保存修改记录 (applied/)。
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import _APPLIED_DIR, _BACKUP_DIR, apply_diff_to_source


class PatchApplierWorker(Worker):
    """备份源文件 → 将 diff 写入源文件 → 保存修改记录 (applied/)。

    三步:
      1. backup: data/doctor/repair/backups/<RouterClass>_<ts>.py
      2. apply: 解析 diff, 对源文件做 old→new 替换, 写入
      3. record: data/doctor/repair/applied/<RouterClass>.md (含 diff + 修改理由)
    """

    DESCRIPTION = (
        "将 LLM 生成的修复 diff 直接写入源文件（备份原版本 + 保存修改记录），"
        "不等待人类审批；若 diff 应用失败则 FAIL 并保留备份"
    )
    FORMAT_IN = "diag.repair.validated-patch"
    FORMAT_OUT = "diag.repair.applied"

    def __init__(self, applied_dir: Path | None = None, backup_dir: Path | None = None):
        self._applied_dir = applied_dir or _APPLIED_DIR
        self._backup_dir = backup_dir or _BACKUP_DIR

    def run(self, input_data: Any) -> Verdict:
        diff: str | None = input_data.get("diff")
        router_class: str = input_data.get("router_class", "UnknownRouter")
        b_class_issues: list = input_data.get("b_class_issues", [])
        source_file: str = input_data.get("source_file", "")

        if not diff or not b_class_issues:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "applied": False, "apply_note": "无 diff，跳过"},
                           diagnosis=f"PatchApplier: {router_class} 无 diff，跳过")

        src_path = Path(source_file)
        if not src_path.exists():
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False},
                           diagnosis=f"PatchApplier: 源文件不存在 {source_file}")

        original = src_path.read_text(encoding="utf-8", errors="ignore")

        # ── Step 1: 备份 ──
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = self._backup_dir / f"{router_class}_{ts}.py"
        try:
            backup_path.write_text(original, encoding="utf-8")
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False},
                           diagnosis=f"PatchApplier: 备份失败 {e}")

        # ── Step 2: 应用 diff ──
        new_source, errors = apply_diff_to_source(original, diff)

        if errors:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False,
                                   "apply_errors": errors, "backup_path": str(backup_path)},
                           diagnosis=f"PatchApplier: {router_class} diff 应用失败: {errors}")

        if new_source == original:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False,
                                   "backup_path": str(backup_path)},
                           diagnosis=f"PatchApplier: {router_class} diff 应用后源文件未变化（可能已应用过）")

        try:
            src_path.write_text(new_source, encoding="utf-8")
        except Exception as e:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "applied": False},
                           diagnosis=f"PatchApplier: 写入源文件失败 {e}")

        # ── Step 3: 保存修改记录 ──
        self._applied_dir.mkdir(parents=True, exist_ok=True)
        pipeline_purpose = input_data.get("pipeline_purpose", "")
        pipeline_brief = input_data.get("pipeline_brief") or {}
        pipeline_node_desc = input_data.get("pipeline_node_desc", "")
        issues_md = "\n".join(
            f"- **[{i.get('check_id')}]** ({i.get('severity')}): {i.get('observation', '')}"
            for i in b_class_issues
        )
        diff_sections = []
        desc_d = input_data.get("desc_diff")
        fail_d = input_data.get("fail_diff")
        tags_d = input_data.get("tags_diff")
        if desc_d:
            diff_sections.append(f"### R-01 DESCRIPTION 补全\n```diff\n{desc_d}\n```")
        if fail_d:
            diff_sections.append(f"### R-05 FAIL 路径补充\n```diff\n{fail_d}\n```")
        if tags_d:
            diff_sections.append(f"### R-07 granted_tags 添加\n```diff\n{tags_d}\n```")
        if not diff_sections:
            diff_sections.append(f"### 合并修改\n```diff\n{diff}\n```")

        record = f"""# 修改记录: {router_class}

**应用时间**: {ts}
**源文件**: `{source_file}`
**备份**: `{backup_path}`
**所属管线**: `{pipeline_brief.get('pipeline_id', '未知')}`

## 管线业务目标
{pipeline_purpose or "(未记录)"}

## 节点 Validator 描述
{pipeline_node_desc or "(未提取到)"}

## 修复的问题
{issues_md}

## 应用的修改（按问题类型分节）

{chr(10).join(diff_sections)}
"""
        try:
            record_path = self._applied_dir / f"{router_class}.md"
            record_path.write_text(record, encoding="utf-8")
        except Exception:
            pass

        return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                       output={**input_data, "applied": True,
                               "backup_path": str(backup_path),
                               "record_path": str(self._applied_dir / f"{router_class}.md")},
                       diagnosis=f"PatchApplier: {router_class} 已写入 {source_file}")

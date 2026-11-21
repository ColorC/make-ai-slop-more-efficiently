# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:diagnosis.semantic_auditor.finding_persistor.worker.python"
"""FindingWriterWorker — SemanticAuditor Team Worker #5 (sink 产出).

Worker 协议:
  FORMAT_IN  = semantic_auditor.finding-set
  FORMAT_OUT = semantic_auditor.finding-written  (kind.sink)

职责: 验证 Finding 字段 → append REGISTRY.md §语义合规待审 + ARCH-CHANGES.jsonl。

铁律:
  - 去重键: (standard_id, target_path). 已存在 open 条目不重复写
  - confidence < 0.7 → status=needs_human_review, 不进入主 open 流
  - 任何异常只产 FAIL Verdict, 不抛 (保证 pipeline 不崩)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.packages.services._core.omnicompany import Worker

from ..standards_loader import load_standards_index

logger = logging.getLogger(__name__)


_REGISTRY_RELPATH = "docs/tech_debt/REGISTRY.md"
_ARCH_RELPATH = "docs/ARCH-CHANGES.jsonl"
_SECTION_HEADER = "## §语义合规待审"
_TABLE_HEADER_PREFIX = "| ID |"
_SA_ID_PATTERN = re.compile(r"^SA-(\d+)$")

_CONF_HUMAN_REVIEW = 0.7  # <0.7 → status=needs_human_review
_REQUIRED_FINDING_FIELDS = (
    "standard_id", "target_path", "description",
    "confidence", "recommended_action",
)


def _validate_finding(f: dict, known_standard_ids: set[str]) -> str | None:
    """返回错误原因 str（未通过）或 None（通过）。"""
    for k in _REQUIRED_FINDING_FIELDS:
        v = f.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            return f"缺字段 {k}"
    sid = f["standard_id"]
    if sid not in known_standard_ids:
        return f"未知 standard_id={sid}"
    try:
        c = float(f["confidence"])
    except (TypeError, ValueError):
        return "confidence 非数字"
    if not 0.0 <= c <= 1.0:
        return "confidence 超出 [0,1]"
    return None


def _next_sa_id(existing_ids: list[str]) -> str:
    max_n = 0
    for s in existing_ids:
        m = _SA_ID_PATTERN.match(s.strip())
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return f"SA-{max_n + 1:03d}"


def _find_semantic_section(lines: list[str]) -> tuple[int, int] | None:
    """定位 §语义合规待审 表格范围，返回 (table_header_idx, table_end_idx)。"""
    section_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(_SECTION_HEADER):
            section_start = i
            break
    if section_start is None:
        return None

    table_header = None
    for i in range(section_start + 1, len(lines)):
        line = lines[i].strip()
        if line.startswith("## ") or line == "---":
            break
        if line.startswith(_TABLE_HEADER_PREFIX):
            table_header = i
            break
    if table_header is None:
        return None

    data_start = table_header + 2
    table_end = data_start
    for i in range(data_start, len(lines)):
        line = lines[i].rstrip("\n").strip()
        if line.startswith("|"):
            table_end = i + 1
        else:
            break
    return (table_header, table_end)


def _parse_sa_row(line: str) -> dict | None:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) != 7:
        return None
    return {
        "id": cells[0],
        "standard_id": cells[1],
        "target_path": cells[2],
        "description": cells[3],
        "confidence": cells[4],
        "disposition": cells[5],
        "status": cells[6],
    }


def _format_sa_row(row: dict) -> str:
    def esc(s: str) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ").strip()

    return (
        f"| {esc(row['id'])} | {esc(row['standard_id'])} | "
        f"{esc(row['target_path'])} | {esc(row['description'])} | "
        f"{esc(row['confidence'])} | {esc(row['disposition'])} | "
        f"{esc(row['status'])} |"
    )


class FindingWriterWorker(Worker):
    """验证 Finding 字段 → append 到 REGISTRY.md §语义合规待审 + ARCH-CHANGES.jsonl。

    去重键：(standard_id, target_path)。已存在 open 条目不重复写（仅 log）。
    confidence < 0.7 → status=needs_human_review，不进入主 open 流。
    任何异常只产 FAIL Verdict，不抛（保证 pipeline 不崩）。
    """

    INPUT_KEYS = ["findings"]
    DESCRIPTION = (
        "验证 Finding 字段，append 到 REGISTRY.md §语义合规待审，"
        "同步写 ARCH-CHANGES.jsonl event_type=finding-generated"
    )
    FORMAT_IN = "semantic_auditor.finding-set"
    FORMAT_OUT = "semantic_auditor.finding-written"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict) or "findings" not in input_data:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "input_data 需含 findings 字段"},
            )

        findings = input_data["findings"]
        if not isinstance(findings, list):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "findings 必须是 list"},
            )

        root = Path(input_data.get("project_root", "."))
        try:
            index = load_standards_index(root)
            known_ids = {s.id for s in index.standards}
        except (FileNotFoundError, ValueError) as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"加载 standards-index 失败: {e}"},
            )

        registry_path = root / _REGISTRY_RELPATH
        arch_path = root / _ARCH_RELPATH

        try:
            content = registry_path.read_text(encoding="utf-8")
        except OSError as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"读 REGISTRY.md 失败: {e}"},
            )

        lines = content.splitlines()
        span = _find_semantic_section(lines)
        if span is None:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": "REGISTRY.md 未找到 §语义合规待审 表格"},
            )
        header_idx, end_idx = span
        data_start = header_idx + 2

        existing_rows: list[dict] = []
        for i in range(data_start, end_idx):
            r = _parse_sa_row(lines[i])
            if r is not None:
                existing_rows.append(r)

        dedup_index: dict[tuple[str, str], int] = {}
        for idx, r in enumerate(existing_rows):
            if r["status"] in ("open", "needs_human_review"):
                dedup_index[(r["standard_id"], r["target_path"])] = idx

        existing_ids = [r["id"] for r in existing_rows]

        added = 0
        rejected: list[dict[str, str]] = []
        deduped = 0
        new_rows_for_arch: list[dict] = []

        for f in findings:
            if not isinstance(f, dict):
                rejected.append({"reason": "finding 非 dict"})
                continue
            err = _validate_finding(f, known_ids)
            if err is not None:
                rejected.append({
                    "standard_id": str(f.get("standard_id", "")),
                    "target_path": str(f.get("target_path", "")),
                    "reason": err,
                })
                continue

            key = (f["standard_id"], f["target_path"])
            if key in dedup_index:
                deduped += 1
                continue

            conf = float(f["confidence"])
            status = "needs_human_review" if conf < _CONF_HUMAN_REVIEW else "open"
            new_id = _next_sa_id(existing_ids + [r["id"] for r in new_rows_for_arch])

            line_hint = f.get("line_hint")
            desc_with_line = f["description"]
            if line_hint is not None:
                desc_with_line = f"{desc_with_line} (L{line_hint})"
            disposition = f.get("recommended_action", "")

            row = {
                "id": new_id,
                "standard_id": f["standard_id"],
                "target_path": f["target_path"],
                "description": desc_with_line,
                "confidence": f"{conf:.2f}",
                "disposition": disposition,
                "status": status,
            }
            existing_rows.append(row)
            new_rows_for_arch.append(row)
            dedup_index[key] = len(existing_rows) - 1
            added += 1

        if added == 0:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    "added": 0,
                    "deduped": deduped,
                    "rejected": rejected,
                    "arch_events": 0,
                },
            )

        new_table_lines = [_format_sa_row(r) for r in existing_rows]
        new_lines = lines[:data_start] + new_table_lines + lines[end_idx:]
        new_content = "\n".join(new_lines)
        if content.endswith("\n") and not new_content.endswith("\n"):
            new_content += "\n"

        try:
            registry_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={"reason": f"写 REGISTRY.md 失败: {e}"},
            )

        arch_events = self._append_arch_events(new_rows_for_arch, arch_path)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "added": added,
                "deduped": deduped,
                "rejected": rejected,
                "arch_events": arch_events,
                "new_ids": [r["id"] for r in new_rows_for_arch],
            },
        )

    @staticmethod
    def _append_arch_events(new_rows: list[dict], arch_path: Path) -> int:
        """Phase C4：改用 tech_debt.events.append_event 统一 schema。

        arch_path 参数保留是因为旧接口兼容；内部反推 root = arch_path.parents[1]
        （假设 arch_path = root/docs/ARCH-CHANGES.jsonl）。
        """
        if not new_rows:
            return 0

        from omnifactory.packages.services._diagnosis.tech_debt import append_event

        # arch_path = root/docs/ARCH-CHANGES.jsonl → parents[1] = root
        try:
            root = arch_path.parents[1]
            arch_relpath = arch_path.relative_to(root).as_posix()
        except (IndexError, ValueError):
            logger.warning("FindingWriter: 反推 root 失败，arch_path=%s", arch_path)
            return 0

        count = 0
        for row in new_rows:
            ev = append_event(
                root,
                event_type="finding-generated",
                initiator="semantic_auditor",
                drawer="services/semantic_auditor",
                related_pipeline="semantic_auditor.baseline",
                change=(
                    f"{row['id']} {row['standard_id']} {row['status']} "
                    f"{row['target_path']}"
                ),
                arch_relpath=arch_relpath,
            )
            if ev is not None:
                count += 1
        return count

# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:core.guardian.registry_updater.violation_sync.py"
"""Guardian patrol → docs/tech_debt/REGISTRY.md 同步器。

Phase A2 实施：每次 run_patrol 结束后把 OMNI-NNN 违规增量追加到
`docs/tech_debt/REGISTRY.md` 的 §活跃违规 表，并在 `docs/ARCH-CHANGES.jsonl`
记录 `violation-found` 事件。

设计原则：
  - 只处理 rule_id 以 OMNI- 开头的违规（人工条目如 OVERSEER/ARCH 不碰）
  - 去重键：(rule_id, path)。已存在 status=open → 持续扫描数 +1
  - 新条目：ID 自增，首现=今日，持续扫描数=1，状态=open
  - 文件损坏/结构异常 → 静默跳过，不阻塞 patrol 主流程
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


_SECTION_HEADER = "## §活跃违规"
_TABLE_HEADER_PREFIX = "| ID |"
_TABLE_SEPARATOR_PREFIX = "|---"
_ID_PATTERN = re.compile(r"^D-(\d+)$")


def _read_file_safe(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _write_file_safe(path: Path, content: str) -> bool:
    try:
        path.write_text(content, encoding="utf-8")
        return True
    except OSError as e:
        logger.warning("registry_updater: 写入失败 %s: %s", path, e)
        return False


def _find_active_violations_table(lines: list[str]) -> tuple[int, int] | None:
    """定位 §活跃违规 表格范围。

    返回 (table_start_idx, table_end_idx)：
      - table_start_idx：表头行 `| ID | ... |` 的索引
      - table_end_idx：表格末尾之后的第一个非表格行索引（开区间）
    未找到返回 None。
    """
    # 先找 §活跃违规 section
    section_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(_SECTION_HEADER):
            section_start = i
            break
    if section_start is None:
        return None

    # section 内找表头
    table_header = None
    for i in range(section_start + 1, len(lines)):
        line = lines[i].strip()
        # 下个 section 或 --- 分隔符 → 终止
        if line.startswith("## ") or line == "---":
            break
        if line.startswith(_TABLE_HEADER_PREFIX):
            table_header = i
            break
    if table_header is None:
        return None

    # 表头下两行是 |---|（分隔符），数据行从 table_header+2 开始
    # 找表格末尾：第一个不以 | 开头的行
    data_start = table_header + 2  # 表头 + 分隔符
    table_end = data_start
    for i in range(data_start, len(lines)):
        line = lines[i].rstrip("\n").strip()
        if line.startswith("|"):
            table_end = i + 1
        else:
            break

    return (table_header, table_end)


def _parse_row(line: str) -> dict | None:
    """解析一行表格数据。

    返回 {"id", "rule_id", "path", "severity", "first_seen", "scan_count", "status"}，
    解析失败返回 None。
    """
    line = line.rstrip("\n").strip()
    if not line.startswith("|"):
        return None
    # 去掉首尾 | 后按 | 切列
    cells = [c.strip() for c in line.strip("|").split("|")]
    if len(cells) != 7:
        return None
    return {
        "id": cells[0],
        "rule_id": cells[1],
        "path": cells[2],
        "severity": cells[3],
        "first_seen": cells[4],
        "scan_count": cells[5],
        "status": cells[6],
    }


def _format_row(row: dict) -> str:
    return (
        f"| {row['id']} | {row['rule_id']} | {row['path']} | "
        f"{row['severity']} | {row['first_seen']} | {row['scan_count']} | "
        f"{row['status']} |"
    )


def _next_id(existing_ids: list[str]) -> str:
    """给出现有 D-NNN ID 列表，返回下一个可用 D-NNN。"""
    max_n = 0
    for id_str in existing_ids:
        m = _ID_PATTERN.match(id_str.strip())
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return f"D-{max_n + 1:03d}"


def append_violations_to_registry(
    violations: list[dict],
    root: Path,
    registry_relpath: str = "docs/tech_debt/REGISTRY.md",
) -> dict:
    """把 OMNI-NNN 违规同步到 REGISTRY §活跃违规。

    Args:
        violations: patrol_runner.run_patrol 返回的 `result["violations"]` 列表
        root: 项目根
        registry_relpath: REGISTRY.md 相对路径

    Returns:
        {"added": int, "bumped": int, "skipped": int, "new_rows": [...]}
        new_rows 每项是一个 dict，供 ARCH-CHANGES.jsonl 使用
    """
    registry_path = root / registry_relpath
    content = _read_file_safe(registry_path)
    if content is None:
        logger.warning("registry_updater: 读不到 %s，跳过", registry_path)
        return {"added": 0, "bumped": 0, "skipped": len(violations), "new_rows": []}

    lines = content.splitlines()
    span = _find_active_violations_table(lines)
    if span is None:
        logger.warning("registry_updater: §活跃违规 表格未找到，跳过")
        return {"added": 0, "bumped": 0, "skipped": len(violations), "new_rows": []}

    table_header_idx, table_end_idx = span
    data_start_idx = table_header_idx + 2

    # 解析现有行
    existing_rows: list[dict] = []
    for i in range(data_start_idx, table_end_idx):
        row = _parse_row(lines[i])
        if row is not None:
            existing_rows.append(row)

    existing_ids = [r["id"] for r in existing_rows]

    # 构建 (rule_id, path) → row 索引（只看 status=open 的 OMNI 行）
    open_omni_index: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(existing_rows):
        if row["status"] != "open":
            continue
        if not row["rule_id"].startswith("OMNI-"):
            continue
        open_omni_index[(row["rule_id"], row["path"])] = idx

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    added = 0
    bumped = 0
    skipped = 0
    new_rows: list[dict] = []

    for v in violations:
        rule_id = v.get("rule_id", "")
        path = v.get("path", "")
        if not rule_id.startswith("OMNI-"):
            skipped += 1
            continue
        if not path:
            skipped += 1
            continue

        key = (rule_id, path)
        if key in open_omni_index:
            idx = open_omni_index[key]
            old_count = existing_rows[idx]["scan_count"]
            try:
                new_count = int(old_count) + 1
            except ValueError:
                new_count = 1
            existing_rows[idx]["scan_count"] = str(new_count)
            bumped += 1
        else:
            new_id = _next_id(existing_ids + [r["id"] for r in new_rows])
            severity = v.get("severity", "MEDIUM")
            row = {
                "id": new_id,
                "rule_id": rule_id,
                "path": path,
                "severity": severity,
                "first_seen": today,
                "scan_count": "1",
                "status": "open",
            }
            existing_rows.append(row)
            new_rows.append(row)
            open_omni_index[key] = len(existing_rows) - 1
            added += 1

    if added == 0 and bumped == 0:
        return {"added": 0, "bumped": 0, "skipped": skipped, "new_rows": []}

    # 重建 §活跃违规 表格
    new_table_lines = [_format_row(r) for r in existing_rows]
    new_lines = (
        lines[:data_start_idx]
        + new_table_lines
        + lines[table_end_idx:]
    )
    new_content = "\n".join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"

    if not _write_file_safe(registry_path, new_content):
        return {"added": 0, "bumped": 0, "skipped": skipped, "new_rows": []}

    return {
        "added": added,
        "bumped": bumped,
        "skipped": skipped,
        "new_rows": new_rows,
    }


# ─── ARCH-CHANGES.jsonl 事件（Phase C4：统一走 tech_debt.events） ─────


def append_violation_found_events(
    new_rows: list[dict],
    root: Path,
    arch_relpath: str = "docs/ARCH-CHANGES.jsonl",
) -> int:
    """把新违规行写入 ARCH-CHANGES.jsonl，每行一条 event_type=violation-found。

    Phase C4：内部改用 tech_debt.events.append_event（schema 统一所有者）。
    接口保持不变（new_rows / root / arch_relpath），测试兼容。

    Returns: 写入条数。
    """
    if not new_rows:
        return 0

    from omnicompany.packages.services._diagnosis.tech_debt import append_event

    count = 0
    for row in new_rows:
        ev = append_event(
            root,
            event_type="violation-found",
            initiator="guardian",
            drawer="services/guardian",
            related_pipeline="guardian.patrol",
            change=(
                f"{row['id']} {row['rule_id']} {row['severity']} {row['path']}"
            ),
            arch_relpath=arch_relpath,
        )
        if ev is not None:
            count += 1
    return count


def sync_patrol_result_to_registry(result: dict, root: Path) -> dict:
    """一次性把 patrol 结果同步到 REGISTRY + ARCH-CHANGES。

    run_patrol 结束后调用。不抛异常，失败时返回 {"added": 0, ...}。
    """
    try:
        violations = result.get("violations", [])
        sync_result = append_violations_to_registry(violations, root)
        events_written = append_violation_found_events(sync_result["new_rows"], root)
        return {
            "added": sync_result["added"],
            "bumped": sync_result["bumped"],
            "skipped": sync_result["skipped"],
            "arch_events": events_written,
        }
    except Exception as e:
        logger.warning("registry_updater: 同步失败: %s", e)
        return {"added": 0, "bumped": 0, "skipped": 0, "arch_events": 0}

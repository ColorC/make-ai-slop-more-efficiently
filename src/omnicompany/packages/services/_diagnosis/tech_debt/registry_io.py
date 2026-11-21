# [OMNI] origin=claude-code domain=services/tech_debt ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.tech_debt.registry_parser_and_resolver.py"
"""tech_debt.registry_io — docs/tech_debt/REGISTRY.md 的统一读视图 + resolve 操作。

见 DESIGN.md D1/D4：
  - producer（guardian/semantic_auditor）写 §活跃违规 / §语义合规待审
  - 本模块读全视图 + 把条目从原 section 移到 §已解决
  - resolve 同步写 docs/ARCH-CHANGES.jsonl event_type=violation-resolved

不做：
  - 扫描（guardian / semantic_auditor 的职责）
  - 自动修复
  - 向原 section 追加新条目（那是 producer 的事）
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_REGISTRY_RELPATH = "docs/tech_debt/REGISTRY.md"
_ARCH_RELPATH = "docs/ARCH-CHANGES.jsonl"
_RESOLVED_SECTION = "## §已解决"


# ─── Section 规格表 ──────────────────────────────────────────────
# 每个 section 声明：标题、id 前缀、列名（用于 parse / format）
# 定义此表 → 新增 section 只需加一项（即满足 D1 owner 原则）

@dataclass(frozen=True)
class SectionSpec:
    name: str                     # 如 "activity" / "semantic_pending"
    header: str                   # "## §活跃违规（...）" 的前缀匹配串（不含括号）
    id_prefix: str                # "D-" / "SA-" / "DR-" / "P-" / "G-"
    columns: tuple[str, ...]      # 列名顺序（与 markdown 表格列对齐）


SECTION_SPECS: tuple[SectionSpec, ...] = (
    SectionSpec(
        name="activity",
        header="## §活跃违规",
        id_prefix="D-",
        columns=("id", "rule_id", "path", "severity", "first_seen", "scan_count", "status"),
    ),
    SectionSpec(
        name="semantic_pending",
        header="## §语义合规待审",
        id_prefix="SA-",
        columns=("id", "standard_id", "target_path", "description", "confidence", "disposition", "status"),
    ),
    SectionSpec(
        name="doc_drift",
        header="## §文档漂移",
        id_prefix="DR-",
        columns=("id", "kind", "target", "last_change", "last_update", "drift_days", "status"),
    ),
    SectionSpec(
        name="plan_merge",
        header="## §计划回流欠债",
        id_prefix="P-",
        columns=("id", "archived_plan", "target_design_md", "status"),
    ),
    SectionSpec(
        name="capability_gap",
        header="## §能力缺口",
        id_prefix="G-",
        columns=("id", "description", "priority", "status"),
    ),
)

_RESOLVED_COLUMNS = ("id", "kind", "resolved_date", "how")


# ─── 数据结构 ────────────────────────────────────────────────────

@dataclass
class RegistryRow:
    """一行条目的解析后视图。"""
    section: str                       # SectionSpec.name
    id: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """按约定返回 status 字段（已解决 section 的 status 恒为 resolved）。"""
        if self.section == "resolved":
            return "resolved"
        return self.fields.get("status", "")


@dataclass
class RegistrySnapshot:
    project_root: Path
    sections: dict[str, list[RegistryRow]]   # name → rows
    resolved_rows: list[RegistryRow]         # §已解决 的条目

    def all_rows(self) -> list[RegistryRow]:
        out: list[RegistryRow] = []
        for rows in self.sections.values():
            out.extend(rows)
        out.extend(self.resolved_rows)
        return out


@dataclass
class ResolveResult:
    ok: bool
    row_id: str
    section_from: str = ""
    reason: str = ""
    error: str = ""
    arch_event_id: str = ""


# ─── markdown 表解析 / 格式化 ──────────────────────────────────────

_TABLE_HEADER_PREFIX = "| "
_TABLE_SEPARATOR_PREFIX = "|---"


def _find_section_table(lines: list[str], header: str) -> tuple[int, int] | None:
    """定位指定 section 下第一个表的范围 (table_header_idx, end_idx)。"""
    section_start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(header):
            section_start = i
            break
    if section_start is None:
        return None

    table_header_idx = None
    for i in range(section_start + 1, len(lines)):
        stripped = lines[i].strip()
        # 下个 section 或 --- 分隔符 → 终止
        if stripped.startswith("## ") or stripped == "---":
            break
        if stripped.startswith(_TABLE_HEADER_PREFIX) and "---" not in stripped:
            table_header_idx = i
            break
    if table_header_idx is None:
        return None

    data_start = table_header_idx + 2  # header + separator
    end_idx = data_start
    for i in range(data_start, len(lines)):
        line = lines[i].rstrip("\n").strip()
        if line.startswith("|"):
            end_idx = i + 1
        else:
            break
    return (table_header_idx, end_idx)


def _parse_row_generic(line: str, columns: tuple[str, ...]) -> dict[str, str] | None:
    stripped = line.rstrip("\n").strip()
    if not stripped.startswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if len(cells) != len(columns):
        return None
    return dict(zip(columns, cells))


def _format_row_generic(row_fields: dict[str, str], columns: tuple[str, ...]) -> str:
    def esc(s: str) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ").strip()

    return "| " + " | ".join(esc(row_fields.get(c, "")) for c in columns) + " |"


# ─── 公共 API：load ───────────────────────────────────────────────

def load_registry(project_root: str | Path) -> RegistrySnapshot:
    """读取 REGISTRY.md 并解析所有已知 section。

    Raises:
        FileNotFoundError: REGISTRY.md 不存在
    """
    root = Path(project_root)
    path = root / _REGISTRY_RELPATH
    if not path.exists():
        raise FileNotFoundError(f"REGISTRY.md 不存在: {path}")

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()

    sections: dict[str, list[RegistryRow]] = {}
    for spec in SECTION_SPECS:
        rows: list[RegistryRow] = []
        span = _find_section_table(lines, spec.header)
        if span is not None:
            header_idx, end_idx = span
            for i in range(header_idx + 2, end_idx):
                fields = _parse_row_generic(lines[i], spec.columns)
                if fields is None:
                    continue
                rid = fields.get("id", "")
                if not rid.startswith(spec.id_prefix):
                    # 允许：表头可能不严格带前缀；只要 id 字段存在
                    if not rid:
                        continue
                rows.append(RegistryRow(section=spec.name, id=rid, fields=fields))
        sections[spec.name] = rows

    # §已解决
    resolved_rows: list[RegistryRow] = []
    span = _find_section_table(lines, _RESOLVED_SECTION)
    if span is not None:
        header_idx, end_idx = span
        for i in range(header_idx + 2, end_idx):
            fields = _parse_row_generic(lines[i], _RESOLVED_COLUMNS)
            if fields is None:
                continue
            resolved_rows.append(RegistryRow(
                section="resolved", id=fields.get("id", ""), fields=fields,
            ))

    return RegistrySnapshot(
        project_root=root,
        sections=sections,
        resolved_rows=resolved_rows,
    )


# ─── 公共 API：list_rows / compute_stats ─────────────────────────

def list_rows(
    snapshot: RegistrySnapshot,
    *,
    section: str | None = None,
    status: str | None = None,
) -> list[RegistryRow]:
    """过滤条目。section=None 含全部 section（不含 resolved 除非 status=resolved）。"""
    rows: list[RegistryRow] = []
    if section == "resolved" or (section is None and status == "resolved"):
        rows.extend(snapshot.resolved_rows)
        if section == "resolved":
            return rows

    if section is None:
        for s_name in snapshot.sections:
            rows.extend(snapshot.sections[s_name])
    else:
        rows.extend(snapshot.sections.get(section, []))

    if status is not None and status != "all":
        rows = [r for r in rows if r.status == status]

    return rows


def compute_stats(snapshot: RegistrySnapshot) -> dict[str, Any]:
    """按 section / status / severity / rule_id 聚合。"""
    stats: dict[str, Any] = {
        "total_rows": 0,
        "by_section": {},
        "by_status": {},
        "by_severity": {},
        "by_rule_id": {},
        "resolved_count": len(snapshot.resolved_rows),
    }
    for s_name, rows in snapshot.sections.items():
        stats["by_section"][s_name] = len(rows)
        stats["total_rows"] += len(rows)
        for r in rows:
            st = r.status or "unknown"
            stats["by_status"][st] = stats["by_status"].get(st, 0) + 1
            sev = r.fields.get("severity")
            if sev:
                stats["by_severity"][sev] = stats["by_severity"].get(sev, 0) + 1
            rid = r.fields.get("rule_id") or r.fields.get("standard_id")
            if rid:
                stats["by_rule_id"][rid] = stats["by_rule_id"].get(rid, 0) + 1
    stats["total_rows"] += len(snapshot.resolved_rows)
    return stats


# ─── 公共 API：append_row（通用写入 + dedup） ─────────────────────

@dataclass
class AppendResult:
    ok: bool
    action: str            # "added" | "deduped" | "error"
    row_id: str = ""
    error: str = ""


def append_row(
    project_root: str | Path,
    section_name: str,
    fields: dict,
    *,
    dedup_keys: Iterable[str] = (),
    allow_resolved_match: bool = False,
) -> AppendResult:
    """向指定 section 追加一条新行。供 drift_checker / omni debt add / 外部 agent 使用。

    Args:
        project_root: 项目根
        section_name: SectionSpec.name（"activity" / "semantic_pending" / "doc_drift" / ...）
        fields: 不含 id 的字段字典；缺失字段按空字符串填入
        dedup_keys: 去重键字段名；命中任一现有非 resolved 行 → 跳过，不新增 ID
        allow_resolved_match: True 时 resolved 条目也参与去重（默认 False，允许"再次登记"）

    避重语义：
        - 默认只对 status ∉ {"resolved"} 的条目做去重
        - dedup 命中：返回 action="deduped" + 已有 row_id，不改文件
        - 未命中：ID 自增 + 写入，返回 action="added" + 新 row_id
    """
    root = Path(project_root)
    path = root / _REGISTRY_RELPATH
    if not path.exists():
        return AppendResult(ok=False, action="error", error=f"REGISTRY.md 不存在: {path}")

    spec = next((s for s in SECTION_SPECS if s.name == section_name), None)
    if spec is None:
        known = [s.name for s in SECTION_SPECS]
        return AppendResult(
            ok=False, action="error",
            error=f"未知 section name '{section_name}'，已知: {known}",
        )

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    span = _find_section_table(lines, spec.header)
    if span is None:
        return AppendResult(
            ok=False, action="error",
            error=f"section 表格未找到: {spec.header}",
        )

    header_idx, end_idx = span
    data_start = header_idx + 2

    # 解析现有行
    existing_rows: list[dict[str, str]] = []
    for i in range(data_start, end_idx):
        r = _parse_row_generic(lines[i], spec.columns)
        if r is not None:
            existing_rows.append(r)

    # 去重
    dedup_key_list = list(dedup_keys)
    if dedup_key_list:
        for r in existing_rows:
            if not allow_resolved_match and r.get("status") == "resolved":
                continue
            match = all(
                r.get(k, "").strip() == str(fields.get(k, "")).strip()
                for k in dedup_key_list
            )
            if match:
                return AppendResult(ok=True, action="deduped", row_id=r.get("id", ""))

    # 生成新 ID（扫 spec.id_prefix 前缀最大序号）
    id_pat = re.compile(rf"^{re.escape(spec.id_prefix)}(\d+)$")
    max_n = 0
    for r in existing_rows:
        m = id_pat.match(r.get("id", "").strip())
        if m:
            n = int(m.group(1))
            max_n = max(max_n, n)
    new_id = f"{spec.id_prefix}{max_n + 1:03d}"

    # 构造新行
    new_fields = {"id": new_id, **{c: str(fields.get(c, "")) for c in spec.columns if c != "id"}}
    if "status" in spec.columns and not new_fields.get("status"):
        new_fields["status"] = "open"

    new_row_line = _format_row_generic(new_fields, spec.columns)

    new_lines = lines[:end_idx] + [new_row_line] + lines[end_idx:]
    new_content = "\n".join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"

    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        return AppendResult(ok=False, action="error", error=f"写 REGISTRY.md 失败: {e}")

    return AppendResult(ok=True, action="added", row_id=new_id)


# ─── 公共 API：resolve_row ───────────────────────────────────────

def _lookup_section_by_id_prefix(row_id: str) -> SectionSpec | None:
    for spec in SECTION_SPECS:
        if row_id.startswith(spec.id_prefix):
            return spec
    return None


def _append_resolved_event(
    arch_path: Path,
    row_id: str,
    section_name: str,
    reason: str,
    resolved_by: str,
) -> str:
    now_iso = datetime.now(timezone.utc).isoformat()
    today = now_iso[:10]

    # 找同日最大序号
    max_n = 0
    prefix = f"ARCH-{today}-"
    if arch_path.exists():
        try:
            for line in arch_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = ev.get("change_id", "")
                if cid.startswith(prefix):
                    try:
                        n = int(cid[len(prefix):])
                        if n > max_n:
                            max_n = n
                    except ValueError:
                        pass
        except OSError:
            pass

    change_id = f"ARCH-{today}-{max_n + 1:03d}"
    event = {
        "change_id": change_id,
        "ts": now_iso,
        "initiator": resolved_by,
        "event_type": "violation-resolved",
        "drawer": "services/tech_debt",
        "related_pipeline": "",
        "change": f"{row_id} resolved from {section_name}: {reason}",
    }
    try:
        arch_path.parent.mkdir(parents=True, exist_ok=True)
        with arch_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("tech_debt: ARCH-CHANGES 写入失败: %s", e)
        return ""
    return change_id


def resolve_row(
    project_root: str | Path,
    row_id: str,
    *,
    reason: str,
    resolved_by: str = "human",
) -> ResolveResult:
    """把 row_id 对应的条目从原 section 移到 §已解决，并写 ARCH-CHANGES 事件。

    Returns: ResolveResult（ok=True 表示移动成功；否则 error 带原因）。
    """
    if not reason.strip():
        return ResolveResult(ok=False, row_id=row_id, error="reason 必填")

    root = Path(project_root)
    path = root / _REGISTRY_RELPATH
    if not path.exists():
        return ResolveResult(ok=False, row_id=row_id, error=f"REGISTRY.md 不存在: {path}")

    spec = _lookup_section_by_id_prefix(row_id)
    if spec is None:
        return ResolveResult(
            ok=False, row_id=row_id,
            error=f"ID 前缀未知（已知: {[s.id_prefix for s in SECTION_SPECS]}）",
        )

    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # 1. 找原 section 并删除该行
    src_span = _find_section_table(lines, spec.header)
    if src_span is None:
        return ResolveResult(
            ok=False, row_id=row_id, section_from=spec.name,
            error=f"原 section {spec.header} 表格未找到",
        )
    src_header, src_end = src_span
    src_data_start = src_header + 2

    target_line_idx = None
    target_row_fields: dict[str, str] | None = None
    for i in range(src_data_start, src_end):
        fields = _parse_row_generic(lines[i], spec.columns)
        if fields is None:
            continue
        if fields.get("id", "").strip() == row_id:
            target_line_idx = i
            target_row_fields = fields
            break

    if target_line_idx is None:
        return ResolveResult(
            ok=False, row_id=row_id, section_from=spec.name,
            error=f"在 {spec.header} 未找到 ID={row_id}",
        )

    # 2. 找 §已解决 表末尾准备追加
    resolved_span = _find_section_table(lines, _RESOLVED_SECTION)
    if resolved_span is None:
        return ResolveResult(
            ok=False, row_id=row_id, section_from=spec.name,
            error="REGISTRY.md 未找到 §已解决 表格",
        )
    _, resolved_end = resolved_span

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    resolved_row_fields = {
        "id": row_id,
        "kind": spec.name,
        "resolved_date": today,
        "how": reason.strip(),
    }
    resolved_line = _format_row_generic(resolved_row_fields, _RESOLVED_COLUMNS)

    # 3. 执行变换：
    #    先删原行（high index），再在 resolved_end 插入（low index）
    #    注意：删原行会让 resolved_end 的位置变化吗？
    #    原行在 src_end <= resolved_start；删除后所有 resolved_* 下标要减 1
    new_lines = list(lines)
    if target_line_idx < resolved_end:
        # 删除原行 → resolved_end 减 1
        del new_lines[target_line_idx]
        insert_at = resolved_end - 1
    else:
        del new_lines[target_line_idx]
        insert_at = resolved_end
    new_lines.insert(insert_at, resolved_line)

    new_content = "\n".join(new_lines)
    if content.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"

    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        return ResolveResult(
            ok=False, row_id=row_id, section_from=spec.name,
            error=f"写 REGISTRY.md 失败: {e}",
        )

    # 4. 写 ARCH-CHANGES 事件
    arch_path = root / _ARCH_RELPATH
    change_id = _append_resolved_event(
        arch_path, row_id, spec.name, reason.strip(), resolved_by,
    )

    return ResolveResult(
        ok=True, row_id=row_id, section_from=spec.name,
        reason=reason.strip(), arch_event_id=change_id,
    )

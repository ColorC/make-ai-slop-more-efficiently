# [OMNI] origin=claude-code domain=services/registry ts=2026-04-11T00:00:00Z
# [OMNI] material_id="material:core.registry.health_archive.jsonl_timeline.py"
"""
HealthArchive — 健康档案（病历本）

存储格式：JSONL，每个实体一个文件，位于 data/registry/health/{type}/{entity_safe_id}.jsonl
每行 = 一个 HealthSnapshot，按时间追加，不覆盖历史。

JSONL 的优势：
  - append-only：新诊断追加一行，旧记录保留
  - git diffable：每次 commit 后若健康状态变化，diff 显示新增的那一行
  - 可 grep：`grep '"grade": "D"' data/registry/health/router/*.jsonl`

HealthSnapshot 绑定到 git commit hash：
  - 如果在 git repo 中，自动获取 HEAD hash
  - 否则使用空字符串（不强制依赖 git）
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head_hash(cwd: Path | None = None) -> str:
    """获取当前 git HEAD 的短 hash，失败时返回空字符串。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(cwd) if cwd else None,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _safe_filename(entity_id: str) -> str:
    """将 entity_id 转换为安全文件名（.jsonl 后缀）。"""
    import re
    return re.sub(r"[:/\\]", "_", entity_id) + ".jsonl"


# ── HealthSnapshot ───────────────────────────────────────────────────────────

@dataclass
class HealthSnapshot:
    """某实体在某时刻的健康状态快照。一行 JSONL。"""

    entity_id: str
    """注册体系的实体 ID（如 router:demogame.team_table.SchemaAssembler）。"""

    timestamp: str = field(default_factory=_now_iso)
    """诊断时间（ISO 8601）。"""

    commit_hash: str = ""
    """git HEAD 短 hash（在 git repo 中自动填入）。"""

    # 契约变更 #02 (2026-04-25): 去 grade/score · 用 v2 schema 语义字段替代
    schema_version: int = 2
    """Snapshot schema version · v2 = 去 grade/score · 用户 2026-04-25 铁律不做 v1 兼容读."""

    verdict: str = "uncertain"
    """健康判词 · healthy | unhealthy | uncertain."""

    passed: bool = False
    """binary gate · counts['critical'] == 0."""

    counts: dict = field(default_factory=lambda: {"critical": 0, "major": 0, "minor": 0,
                                                   "total_checks": 0, "passed_checks": 0})
    """v2 类别计数 · 非加权求和分数."""

    failures_by_severity: dict = field(default_factory=lambda: {"critical": [], "major": [], "minor": []})
    """v2 按归一 severity 分组的失败 (3 档 critical/major/minor · INFO 丢)."""

    issues: list[dict] = field(default_factory=list)
    """保留: 失败 check 的结构化列表 (check_id + severity + observation)."""

    llm_audit: dict | None = None
    """LLM 语义审计结果（overall_grade / key_findings / improvement_suggestions / detailed_report）。
    来自 RouterContextualAuditRouter 或 FormatContextualAuditRouter 的 contextual_audit check detail。
    passed=True 的 check 不进 issues，但完整 audit 数据需单独存档。"""

    summary: str = ""
    """一句话摘要。"""

    diagnosed_by: str = ""
    """诊断器版本或名称（如 'RouterDoctor/v1'）。"""

    def to_jsonl_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "HealthSnapshot":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── HealthArchive ─────────────────────────────────────────────────────────────

class HealthArchive:
    """基于 JSONL 的健康档案存储。

    目录结构：
        {archive_dir}/{type}/{safe_entity_id}.jsonl

    例：data/registry/health/router/router_demogame.team_table.SchemaAssembler.jsonl
    """

    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = Path(archive_dir)
        self._commit_cache: dict[str, str] = {}  # repo_root → commit hash 缓存

    def _entity_path(self, entity_id: str) -> Path:
        type_name = entity_id.split(":")[0]
        return self.archive_dir / type_name / _safe_filename(entity_id)

    def _get_commit(self, source_file: str = "") -> str:
        """获取 git HEAD hash，按 source_file 的仓库根目录缓存。"""
        # 尝试推断 repo 根（向上找 .git）
        cwd: Path | None = None
        if source_file:
            p = Path(source_file)
            for parent in [p] + list(p.parents):
                if (parent / ".git").exists():
                    cwd = parent
                    break
        key = str(cwd) if cwd else "__default__"
        if key not in self._commit_cache:
            self._commit_cache[key] = _git_head_hash(cwd)
        return self._commit_cache[key]

    # ── 写操作 ──────────────────────────────────────────────────────────────

    def write_snapshot(self, snapshot: HealthSnapshot) -> None:
        """追加一条健康快照（不覆盖历史）。"""
        path = self._entity_path(snapshot.entity_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(snapshot.to_jsonl_line() + "\n")

    # ── 读操作 ──────────────────────────────────────────────────────────────

    def read_latest(self, entity_id: str) -> Optional[HealthSnapshot]:
        """读取最新一条健康快照，不存在则返回 None。"""
        path = self._entity_path(entity_id)
        if not path.exists():
            return None
        last_line = ""
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        last_line = line
        except Exception:
            return None
        if not last_line:
            return None
        try:
            return HealthSnapshot.from_dict(json.loads(last_line))
        except Exception:
            return None

    def read_history(self, entity_id: str) -> list[HealthSnapshot]:
        """读取该实体的全部历史快照（时间升序）。"""
        path = self._entity_path(entity_id)
        if not path.exists():
            return []
        snapshots = []
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        snapshots.append(HealthSnapshot.from_dict(json.loads(line)))
                    except Exception:
                        continue
        except Exception:
            pass
        return snapshots

    def iter_type(self, type_name: str) -> Iterator[tuple[str, HealthSnapshot]]:
        """迭代某类型下所有实体的最新快照。yield (entity_id, snapshot)。"""
        type_dir = self.archive_dir / type_name
        if not type_dir.exists():
            return
        for f in sorted(type_dir.glob("*.jsonl")):
            try:
                last_line = ""
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            last_line = line
                if last_line:
                    snap = HealthSnapshot.from_dict(json.loads(last_line))
                    yield snap.entity_id, snap
            except Exception:
                continue

    # ── 分析查询 ──────────────────────────────────────────────────────────

    def summary_by_type(self, type_name: str) -> dict:
        """按等级统计该类型实体的健康分布。"""
        grade_counts: dict[str, int] = {}
        total = 0
        for _, snap in self.iter_type(type_name):
            g = snap.grade
            grade_counts[g] = grade_counts.get(g, 0) + 1
            total += 1
        return {"type": type_name, "total": total, "by_grade": grade_counts}

    def regressions_since(
        self,
        entity_ids: list[str],
        reference_commit: str,
    ) -> list[dict]:
        """找出自 reference_commit 以来等级下降的实体。

        返回 [{"entity_id": ..., "before_grade": ..., "after_grade": ...}]
        """
        regressions = []
        for entity_id in entity_ids:
            history = self.read_history(entity_id)
            if len(history) < 2:
                continue
            # 找到 reference_commit 之前的最后一条
            before: HealthSnapshot | None = None
            after: HealthSnapshot | None = None
            for snap in history:
                if snap.commit_hash and snap.commit_hash == reference_commit:
                    before = snap
                elif before is not None:
                    after = snap
                    break
            if before is None:
                # 没找到 reference_commit，用第一条 vs 最后一条
                before, after = history[0], history[-1]
            if before and after:
                grade_order = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0, "?": -1}
                b_score = grade_order.get(before.grade, -1)
                a_score = grade_order.get(after.grade, -1)
                if a_score < b_score:
                    regressions.append({
                        "entity_id": entity_id,
                        "before_grade": before.grade,
                        "after_grade": after.grade,
                        "before_commit": before.commit_hash,
                        "after_commit": after.commit_hash,
                    })
        return regressions


# ── 便捷工厂方法 ──────────────────────────────────────────────────────────────

def make_router_snapshot(
    entity_id: str,
    health_record: dict,
    source_file: str = "",
    archive: "HealthArchive | None" = None,
) -> HealthSnapshot:
    """从 RouterHealthWriterRouter 的 health_record 构建 HealthSnapshot。

    不直接依赖 archive，方便测试。如果提供 archive，则立即写入。
    """
    checks = health_record.get("checks", [])
    failed_issues = [
        {
            "check_id": c.get("check", "?"),
            "severity": c.get("severity", ""),
            "observation": c.get("observation", ""),
        }
        for c in checks
        if c.get("passed") is False
    ]
    # LLM 语义审计数据单独保留（passed=True 被 failed_issues 过滤掉，但 detail 不应丢失）
    audit_check = next((c for c in checks if c.get("check") == "contextual_audit"), None)
    llm_audit = audit_check.get("detail") if audit_check else None

    commit = archive._get_commit(source_file) if archive else _git_head_hash()

    snapshot = HealthSnapshot(
        entity_id=entity_id,
        commit_hash=commit,
        verdict=health_record.get("verdict", "uncertain"),
        passed=bool(health_record.get("passed", False)),
        counts=health_record.get("counts", {}),
        failures_by_severity=health_record.get("failures_by_severity", {}),
        issues=failed_issues,
        llm_audit=llm_audit,
        summary=health_record.get("summary", ""),
        diagnosed_by="RouterDoctor/v2",
    )

    if archive is not None:
        archive.write_snapshot(snapshot)

    return snapshot


def make_format_snapshot(
    entity_id: str,
    health_record: dict,
    source_file: str = "",
    archive: "HealthArchive | None" = None,
) -> HealthSnapshot:
    """从 HealthWriterRouter（Format Doctor）的 health_record 构建 HealthSnapshot (v2)."""
    checks = health_record.get("checks", [])
    failed_issues = [
        {
            "check_id": c.get("check", "?"),
            "severity": c.get("severity", ""),
            "observation": c.get("observation", ""),
        }
        for c in checks
        if c.get("passed") is False
    ]
    audit_check = next((c for c in checks if c.get("check") == "contextual_audit"), None)
    llm_audit = audit_check.get("detail") if audit_check else None

    commit = archive._get_commit(source_file) if archive else _git_head_hash()

    snapshot = HealthSnapshot(
        entity_id=entity_id,
        commit_hash=commit,
        verdict=health_record.get("verdict", "uncertain"),
        passed=bool(health_record.get("passed", False)),
        counts=health_record.get("counts", {}),
        failures_by_severity=health_record.get("failures_by_severity", {}),
        issues=failed_issues,
        llm_audit=llm_audit,
        summary=health_record.get("summary", ""),
        diagnosed_by="FormatDoctor/v2",
    )

    if archive is not None:
        archive.write_snapshot(snapshot)

    return snapshot


# ── 就近写入 ──────────────────────────────────────────────────────────────────

def write_proximity_snapshot(
    source_file: str,
    entity_type: str,
    entity_name: str,
    snapshot: "HealthSnapshot",
) -> "Path | None":
    """将健康快照追加写入就近 .omni/health/ 目录（与源码同目录）。

    路径格式：<source_file_dir>/.omni/health/<entity_type>/<entity_name>.jsonl
    例（Router）：  doctor/.omni/health/routers/RouterSignatureRouter.jsonl
    例（Format）：  doctor/.omni/health/formats/diag.rtr.sig-checked.jsonl

    Args:
        source_file:  源码文件绝对路径（routers.py 或 formats.py）
        entity_type:  "routers" 或 "formats"
        entity_name:  路由器类名或 Format ID（直接用作文件名 stem）
        snapshot:     HealthSnapshot 实例

    Returns:
        写入路径（Path），失败时返回 None（静默，不抛出）。
    """
    if not source_file:
        return None
    try:
        path = Path(source_file).parent / ".omni" / "health" / entity_type / f"{entity_name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(snapshot.to_jsonl_line() + "\n")
        return path
    except Exception:
        return None

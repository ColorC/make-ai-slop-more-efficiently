# [OMNI] origin=ai-ide domain=services/registry ts=2026-05-06T00:55:00Z type=router status=skeleton agent=ai-ide-current
# [OMNI] summary="FindingArchive — registry 接通 doctor.health_finding 的存档层. JSONL 每实体一份, 跟 HealthArchive 同思路但 finding 形态不同 (无 severity 打分, 走 commentary+concern 自然语言)"
# [OMNI] why="阶段 2 后续 4: doctor 4 诊断 agent 都产 finding, 当前只 yaml 落 data/services/doctor/findings/. 接通 registry 让查询统一, 历史可追"
# [OMNI] tags=registry,health,finding,archive,jsonl,doctor,skeleton
# [OMNI] material_id="material:core.registry.finding_archive.jsonl_timeline.py"
"""FindingArchive — 接通 doctor.health_finding 的存档层 (V0 骨架).

跟 HealthArchive 区别:
- HealthArchive: 一实体一份 V2 HealthSnapshot (verdict + counts + failures_by_severity 多 check 汇总)
- FindingArchive: 一 finding 一行 (单条诊断证据, 无 severity 打分, 自然语言三字段 evidence/commentary/concern)

存储格式: JSONL, 按 entity_kind 分桶
  data/registry/findings/{entity_kind}/{entity_safe_id}.jsonl

每行 = 一个 doctor.health_finding 实例. append-only.

V0 接入路径:
  doctor write_finding 工具 → yaml 落盘 (data/services/doctor/findings/<task_id>/<finding_id>.yaml)
                            → 同时调 FindingArchive.append_finding 落 JSONL

查询场景 (V0 提供):
- `read_history_for_entity(entity_id)` — 看一实体所有 finding
- `iter_kind(entity_kind)` — 看某 kind 全部 entity 的 latest finding
- `iter_by_finding_kind(finding_kind)` — 看 spec/hypothesis/exemplar/plan 各方法的 finding
- `latest_findings_in_window(since_iso)` — 某时间窗口内的 finding (轻量审计用)

## 待做 (V0 → V1)

[ ] **bus 事件接入**: 当前走直接 append, V1 改 doctor 走 SQLiteBus publish doctor.health_finding event,
    registry 立 FindingIngestWorker 订阅事件写 archive (跟 V3 已有 health-record sink 思路一致)
[ ] **commit_hash 自动填**: 类似 HealthArchive._get_commit
[x] **finding 聚合 snapshot**: 2026-05-06 加 aggregate_to_snapshot 方法 (schema_version=3, 拒打分, 不跟 V2 兼容 — V2 含 severity 打分跟铁律冲突, 双轨独立)
[ ] **测试基线**: 红绿样本
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


def _safe_filename(entity_id: str) -> str:
    """entity_id (常含 path 分隔符 / : 等) → 安全文件名."""
    return re.sub(r"[:/\\]", "_", entity_id) + ".jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head_short_hash(cwd: Path | None = None) -> str:
    """获取当前 git HEAD 的短 hash, 失败返回空字符串. 跨模块复用 (write_finding / write_hypothesis 都用)."""
    import subprocess
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


class FindingArchive:
    """JSONL 健康 finding 存档. 一实体一份, append-only.

    目录结构:
        {archive_dir}/{entity_kind}/{safe_entity_id}.jsonl
    例:
        data/registry/findings/worker/src_omnicompany_packages_services__diagnosis_doctor_workers_blackboard_format_in_mode_checker.py.jsonl
    """

    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = Path(archive_dir)

    def _entity_path(self, entity_kind: str, entity_id: str) -> Path:
        return self.archive_dir / entity_kind / _safe_filename(entity_id)

    # ── 写 ─────────────────────────────────────────────────

    def append_finding(self, finding: dict[str, Any]) -> Path:
        """追加一条 finding 到对应 entity 的 JSONL.

        Args:
            finding: doctor.health_finding 实例 dict. 必含 entity_id / entity_kind /
                finding_kind / evidence / commentary / concern. 其他字段透传.

        Returns:
            写入路径 (Path).
        """
        for required in ("entity_id", "entity_kind", "finding_kind", "evidence", "commentary", "concern"):
            if not (finding.get(required) or "").strip() if isinstance(finding.get(required), str) else not finding.get(required):
                raise ValueError(
                    f"FindingArchive.append_finding missing required field {required!r}. "
                    f"finding: {finding!r}"
                )

        finding = dict(finding)
        if "ts" not in finding:
            finding["ts"] = _now_iso()

        path = self._entity_path(finding["entity_kind"], finding["entity_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(finding, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("FindingArchive.append_finding failed: %s. finding_id=%s", e, finding.get("finding_id"))
            raise
        return path

    # ── 查询 ─────────────────────────────────────────────────

    def read_history_for_entity(self, entity_kind: str, entity_id: str) -> list[dict]:
        """读一实体的所有 finding 历史 (时间升序)."""
        path = self._entity_path(entity_kind, entity_id)
        if not path.exists():
            return []
        findings = []
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        findings.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning("FindingArchive.read_history_for_entity failed: %s", e)
        return findings

    def latest_finding_for_entity(self, entity_kind: str, entity_id: str) -> Optional[dict]:
        """读一实体最近一条 finding."""
        history = self.read_history_for_entity(entity_kind, entity_id)
        return history[-1] if history else None

    def iter_kind(self, entity_kind: str) -> Iterator[tuple[str, dict]]:
        """迭代某 entity_kind 下所有实体的最新 finding. yield (entity_id, finding)."""
        kind_dir = self.archive_dir / entity_kind
        if not kind_dir.exists():
            return
        for f in sorted(kind_dir.glob("*.jsonl")):
            try:
                last_line = ""
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            last_line = line
                if last_line:
                    finding = json.loads(last_line)
                    yield finding.get("entity_id", f.stem), finding
            except (json.JSONDecodeError, OSError):
                continue

    def iter_by_finding_kind(self, finding_kind: str) -> Iterator[dict]:
        """跨 entity 找 finding_kind=X 的全部 finding (例 全部 spec finding 或 全部 plan finding)."""
        if not self.archive_dir.exists():
            return
        for f in sorted(self.archive_dir.glob("*/*.jsonl")):
            try:
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            finding = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if finding.get("finding_kind") == finding_kind:
                            yield finding
            except OSError:
                continue

    def find_findings_referencing_hypothesis(self, hypothesis_id: str) -> list[str]:
        """跨 entity 查 applied_hypotheses 含 hypothesis_id 的全部 finding_id.

        反向链接 — 假设 yaml 升级时拿这查询填 related_finding_ids 字段.
        修 V1 留议 (hypothesis_v1_upgrade_report 7.4 第一项).

        Args:
            hypothesis_id: 假设 ID, 例 'H-2026-05-05-001'.

        Returns:
            匹配的 finding_id 字符串 list, 按 ts 升序去重. 没匹返空 list.
        """
        if not self.archive_dir.exists() or not hypothesis_id:
            return []
        seen: set[str] = set()
        ordered: list[tuple[str, str]] = []  # (ts, finding_id)
        for f in sorted(self.archive_dir.glob("*/*.jsonl")):
            try:
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            finding = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        applied = finding.get("applied_hypotheses") or []
                        if hypothesis_id in applied:
                            fid = finding.get("finding_id")
                            if fid and fid not in seen:
                                seen.add(fid)
                                ordered.append((finding.get("ts") or "", fid))
            except OSError:
                continue
        ordered.sort(key=lambda t: t[0])
        return [fid for _, fid in ordered]

    def latest_findings_in_window(self, since_iso: str) -> list[dict]:
        """某时间窗口内的 finding (轻量审计用). since_iso 同 ts 格式."""
        results = []
        if not self.archive_dir.exists():
            return results
        for f in sorted(self.archive_dir.glob("*/*.jsonl")):
            try:
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            finding = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if (finding.get("ts") or "") >= since_iso:
                            results.append(finding)
            except OSError:
                continue
        return results

    # ── 聚合 ─────────────────────────────────────────────────

    def aggregate_to_snapshot(self, entity_kind: str, entity_id: str) -> dict:
        """把一实体的所有 finding 聚合成一份 schema v3 snapshot.

        跟 V2 HealthSnapshot 双轨独立 — V2 含 severity 打分 (critical/major/minor),
        v3 拒打分 (用户铁律), 两份 schema 不互转.

        v3 schema 字段 (无 severity, 无打分):
        - schema_version=3 / entity_id / entity_kind / aggregated_at (ISO ts)
        - finding_count (int)
        - by_finding_kind (dict[finding_kind → count]): 按诊断方法分桶, 不分 severity
        - latest_finding_ts (str, 最近 finding 的 ts)
        - latest_agent_ids (list[str]): 最近 5 finding 的 agent_id 去重
        - applied_standards / applied_hypotheses / applied_exemplars: union 全部 finding 的引用集合
        - findings_summary (list[dict]): 每条 finding 的 finding_id / finding_kind / ts / agent_id / commentary 前 200 字 (不含 evidence/concern 全文, 全文走 read_history_for_entity)
        - source_path (本聚合查的源 path)

        本聚合不打分, 不写 verdict='healthy/unhealthy', 不汇总 issues/severity_buckets.
        判定健康靠人/agent 自己读 commentary + concern, 不靠数字.

        Args:
            entity_kind: 'worker' / 'material' / 'team' / 'agent' / 'hook' / 'tool' / 'plan'
            entity_id: 实体 ID (path 或 identifier)

        Returns:
            v3 snapshot dict. 若没 finding, 返 finding_count=0 + 空字段, 不抛异常.
        """
        findings = self.read_history_for_entity(entity_kind, entity_id)
        path = self._entity_path(entity_kind, entity_id)
        source_path = str(path).replace("\\", "/") if path.exists() else None

        if not findings:
            return {
                "schema_version": 3,
                "entity_id": entity_id,
                "entity_kind": entity_kind,
                "aggregated_at": _now_iso(),
                "finding_count": 0,
                "by_finding_kind": {},
                "latest_finding_ts": None,
                "latest_agent_ids": [],
                "applied_standards": [],
                "applied_hypotheses": [],
                "applied_exemplars": [],
                "findings_summary": [],
                "source_path": source_path,
            }

        by_kind: dict[str, int] = {}
        for f in findings:
            k = f.get("finding_kind", "unknown")
            by_kind[k] = by_kind.get(k, 0) + 1

        # 最近 5 finding agent 去重
        recent = findings[-5:]
        agent_ids: list[str] = []
        for f in reversed(recent):
            aid = f.get("agent_id")
            if aid and aid not in agent_ids:
                agent_ids.append(aid)

        # union refs
        std_set: set[str] = set()
        hyp_set: set[str] = set()
        exe_set: set[str] = set()
        for f in findings:
            for s in (f.get("applied_standards") or []):
                std_set.add(s)
            for h in (f.get("applied_hypotheses") or []):
                hyp_set.add(h)
            for e in (f.get("applied_exemplars") or []):
                exe_set.add(e)

        summary = []
        for f in findings:
            commentary = (f.get("commentary") or "")[:200]
            summary.append({
                "finding_id": f.get("finding_id"),
                "finding_kind": f.get("finding_kind"),
                "ts": f.get("ts"),
                "agent_id": f.get("agent_id"),
                "commentary_excerpt": commentary,
            })

        return {
            "schema_version": 3,
            "entity_id": entity_id,
            "entity_kind": entity_kind,
            "aggregated_at": _now_iso(),
            "finding_count": len(findings),
            "by_finding_kind": by_kind,
            "latest_finding_ts": findings[-1].get("ts"),
            "latest_agent_ids": agent_ids,
            "applied_standards": sorted(std_set),
            "applied_hypotheses": sorted(hyp_set),
            "applied_exemplars": sorted(exe_set),
            "findings_summary": summary,
            "source_path": source_path,
        }


# ── 默认实例 ─────────────────────────────────────────

def _project_root() -> Path:
    """omnicompany 项目根 (含 src/omnicompany + docs). find-up 不依赖具体层级数."""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[6] if len(here.parents) > 6 else here.parent


_DEFAULT_ARCHIVE_DIR = _project_root() / "data" / "registry" / "findings"
_default_archive: Optional[FindingArchive] = None


def get_finding_archive(archive_dir: Path | None = None) -> FindingArchive:
    """获取默认 FindingArchive 实例 (单例).

    Args:
        archive_dir: 可指定其他目录 (主要测试用). None 用项目默认 data/registry/findings/.
    """
    global _default_archive
    if archive_dir is not None:
        return FindingArchive(archive_dir)
    if _default_archive is None:
        _default_archive = FindingArchive(_DEFAULT_ARCHIVE_DIR)
    return _default_archive

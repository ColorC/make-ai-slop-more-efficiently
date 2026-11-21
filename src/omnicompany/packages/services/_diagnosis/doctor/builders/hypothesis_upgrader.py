# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/builders ts=2026-05-07T07:30:00Z type=router status=active agent=ai-ide
# [OMNI] summary="HypothesisV1Upgrader — 据 V0 假设 dict 跟 standards_authority_map 派生 V1 metadata 默认值. 含 source_kind=code → 'code-derived' 类别"
# [OMNI] why="hypothesis_v1_upgrade_report 5.7 V2 待办 '给 source_kind=code 加 code-derived authority 类别'. 升 V1: 实装这个类别 + 把脚本进 git (上一轮 _scratch/upgrade_hypotheses_to_v1.py 丢了 — _scratch aging 周期清理)"
# [OMNI] tags=builder,hypothesis-upgrader,v0-to-v1,structured,no-llm
# [OMNI] material_id="material:diagnosis.doctor.builders.hypothesis_v1_upgrader.py"
"""HypothesisV1Upgrader · V0 → V1 假设升级 (纯函数, 不读盘).

跟 PytestSkeletonBuilder + HypothesisAgentPromptBuilder 同形态 — 不用 LLM, 不直接落盘,
据输入产新 dict 给调用方决定怎么处置.

V1 metadata 字段 (按 hypothesis_system_schema.md V1 schema):
- confidence_level (high/medium/low) — 默认 low (新生成未真验)
- source_authority (HIGH/MEDIUM/LOW/code-derived/unknown) — 据 source_kind + standards_authority_map 派生
- verification_status (untested) — 默认 untested
- risk_if_wrong (high/medium/low) — 据 statement 关键词派生
- dependent_hypotheses / challenge_log / related_finding_ids / related_anti_pattern_ids — 默认空 list

新增类别 'code-derived' (V1 2026-05-07): 当 source_kind=code 时, source_authority='code-derived'
表示假设来自代码反推, 权威度跟"代码自洽性" 相关 (不是文档权威). 之前混在 unknown 里
(15 个假设撞这个), 看不出真实分布.

调用方 (前 _scratch/upgrade_hypotheses_to_v1.py 现已丢失):
    upgrader = HypothesisV1Upgrader(authority_map=load_yaml('canonical_anchors/standards_authority_map.yaml'))
    result = upgrader.upgrade_batch(load_all_hypothesis_yamls())
    for upgraded in result.upgraded:
        write_yaml(upgraded.target_path, upgraded.dict)
    print(result.summary)
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path


# 关键词派生表 (statement 含哪个词 → risk 哪档)
_HIGH_RISK_KEYWORDS = ("必须", "不得", "应当", "一律", "禁止", "永远不")
# SHOULD 同义词集 (规范类语言): 应该 / 应优先 / 宜 / 最好 / 建议 / 推荐
# (2026-05-07 真重跑 25 假设发现 H-2026-05-06-024 含"应优先使用"被判 low, 应判 medium)
_MEDIUM_RISK_KEYWORDS = ("应该", "应优先", "宜", "最好", "建议", "推荐")

# 实战验证阈值: red_green_pass + 实战 ≥ N → real_world_validated (按 schema §三步骤 5)
REAL_WORLD_VALIDATION_THRESHOLD = 3


@dataclass
class UpgradedHypothesis:
    """升级后假设 + 升级元信息."""
    hypothesis_id: str
    upgraded_dict: dict          # 升级后 dict (含 V0 字段 + V1 新字段)
    derived_authority: str       # 'HIGH'/'MEDIUM'/'LOW'/'code-derived'/'unknown'
    derived_risk: str            # 'high'/'medium'/'low'
    derivation_notes: list[str]  # 派生过程记录 (例 'source_path 在 LOW 档' / 'source_kind=code → code-derived')


@dataclass
class HypothesisV1UpgradeResult:
    upgraded: list[UpgradedHypothesis] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)
    by_authority: dict = field(default_factory=dict)
    by_risk: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return (
            f"upgraded {len(self.upgraded)}, skipped {len(self.skipped)} | "
            f"by_authority: {self.by_authority} | by_risk: {self.by_risk}"
        )


class HypothesisV1Upgrader:
    """V0 → V1 假设升级器.

    输入: V0 假设 dict (含 id/source_kind/source_path/statement).
    输出: V1 dict (V0 字段保留 + V1 metadata 字段填默认值).

    Args:
        authority_map: standards_authority_map.yaml 的解析结果 dict, 形态:
            {
              "authority_levels": {
                "HIGH": {"documents": [{"path": "..."}, ...]},
                "MEDIUM": {"documents": [...]},
                "LOW": {"documents": [...]}
              }
            }
        finding_archive: 可选 FindingArchive 实例. 传入时 upgrade 反向查
            applied_hypotheses 含本假设的 finding, 填 related_finding_ids 字段.
            duck-typed: 任何含 find_findings_referencing_hypothesis(hid) 方法的对象皆可.
            修 V1 留议 (hypothesis_v1_upgrade_report 7.4 第一项 "FindingArchive 接通").
    """

    def __init__(self, authority_map: dict | None = None, finding_archive: object | None = None):
        self.authority_map = authority_map or {}
        self.finding_archive = finding_archive

    def upgrade(self, hyp: dict) -> UpgradedHypothesis | None:
        """升级单个假设. 返 None 时跳过 (无 id)."""
        hid = hyp.get("id")
        if not hid:
            return None

        notes: list[str] = []
        source_kind = hyp.get("source_kind", "")
        source_path = hyp.get("source_path", "")
        statement = hyp.get("statement", "") or ""

        # source_authority 派生
        if source_kind == "code":
            authority = "code-derived"
            notes.append("source_kind=code → code-derived (V1 2026-05-07 新类别)")
        else:
            authority = self._lookup_authority(source_path)
            notes.append(f"source_path '{source_path}' 在 authority_map → {authority}")

        # risk_if_wrong 派生 (关键词)
        risk = self._derive_risk(statement)
        notes.append(f"statement 关键词派生 risk_if_wrong={risk}")

        # 升级 dict
        upgraded_dict = dict(hyp)
        upgraded_dict.setdefault("confidence_level", "low")
        upgraded_dict.setdefault("source_authority", authority)
        upgraded_dict.setdefault("verification_status", "untested")
        upgraded_dict.setdefault("risk_if_wrong", risk)
        upgraded_dict.setdefault("dependent_hypotheses", [])
        upgraded_dict.setdefault("challenge_log", [])
        upgraded_dict.setdefault("related_finding_ids", [])
        upgraded_dict.setdefault("related_anti_pattern_ids", [])

        # 反向查 finding archive 填 related_finding_ids (V1 2026-05-07 接通)
        if self.finding_archive is not None and hasattr(self.finding_archive, "find_findings_referencing_hypothesis"):
            existing_fids = set(upgraded_dict.get("related_finding_ids") or [])
            try:
                fids = self.finding_archive.find_findings_referencing_hypothesis(hid)
                # 合并不重复, 保序 — 先现有再新查
                for fid in fids:
                    if fid not in existing_fids:
                        existing_fids.add(fid)
                        upgraded_dict["related_finding_ids"] = list(upgraded_dict.get("related_finding_ids") or []) + [fid]
                if fids:
                    notes.append(f"FindingArchive 反向查到 {len(fids)} finding (related_finding_ids 填上)")
            except Exception as e:  # noqa: BLE001 — archive 查询失败不阻塞升级
                notes.append(f"FindingArchive 查询失败 (跳过反向链): {type(e).__name__}: {e}")

        # dogfood 历史升级 verification_status (V2 → V1 2026-05-07 接通)
        # 按 hypothesis_system_schema §三步骤 5: red_green_pass + 实战 ≥3 次 → real_world_validated, confidence=high
        related_count = len(upgraded_dict.get("related_finding_ids") or [])
        if (upgraded_dict.get("verification_status") == "red_green_pass"
                and related_count >= REAL_WORLD_VALIDATION_THRESHOLD):
            upgraded_dict["verification_status"] = "real_world_validated"
            upgraded_dict["confidence_level"] = "high"
            notes.append(
                f"dogfood 历史升级: verification_status red_green_pass → real_world_validated "
                f"(实战 finding {related_count} 次 ≥ {REAL_WORLD_VALIDATION_THRESHOLD} 阈值), "
                f"confidence_level → high (按 schema §三步骤 5)"
            )

        return UpgradedHypothesis(
            hypothesis_id=hid,
            upgraded_dict=upgraded_dict,
            derived_authority=authority,
            derived_risk=risk,
            derivation_notes=notes,
        )

    def upgrade_batch(self, hypotheses: list[dict]) -> HypothesisV1UpgradeResult:
        """批量升级 + 统计分布."""
        result = HypothesisV1UpgradeResult()
        for hyp in hypotheses:
            if not isinstance(hyp, dict):
                result.skipped.append(("<non-dict>", f"input 非 dict: {type(hyp).__name__}"))
                continue
            up = self.upgrade(hyp)
            if up is None:
                result.skipped.append((hyp.get("id") or "<no-id>", "缺 id"))
                continue
            result.upgraded.append(up)
            result.by_authority[up.derived_authority] = result.by_authority.get(up.derived_authority, 0) + 1
            result.by_risk[up.derived_risk] = result.by_risk.get(up.derived_risk, 0) + 1
        return result

    def _lookup_authority(self, source_path: str) -> str:
        """source_path 在 standards_authority_map 哪档."""
        if not source_path:
            return "unknown"
        levels = self.authority_map.get("authority_levels", {})
        for tier in ("HIGH", "MEDIUM", "LOW"):
            docs = levels.get(tier, {}).get("documents", []) or []
            for doc in docs:
                if isinstance(doc, dict) and doc.get("path") == source_path:
                    return tier
                if isinstance(doc, str) and doc == source_path:
                    return tier
        return "unknown"

    @staticmethod
    def _derive_risk(statement: str) -> str:
        """据 statement 关键词派生 risk_if_wrong."""
        for kw in _HIGH_RISK_KEYWORDS:
            if kw in statement:
                return "high"
        for kw in _MEDIUM_RISK_KEYWORDS:
            if kw in statement:
                return "medium"
        return "low"


# ── CLI 入口 (V1 2026-05-07 加, 配 V1 留议第二项) ─────────────────────────────

def _load_yaml(path: Path) -> dict | list | None:
    """读 yaml 文件返 dict/list. 文件不存在返 None."""
    import yaml  # local import — V1Upgrader 类本身不依赖 yaml, 只 CLI 部分依赖
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _dump_yaml(data: dict, path: Path) -> None:
    """写 yaml (UTF-8, 不强行 ASCII)."""
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def _iter_hypothesis_yamls(directory: Path) -> list[Path]:
    """遍历 directory 下所有 .yaml/.yml 文件."""
    if not directory.exists() or not directory.is_dir():
        return []
    paths: list[Path] = []
    for ext in ("*.yaml", "*.yml"):
        paths.extend(sorted(directory.glob(ext)))
    return paths


def main(argv: list[str] | None = None) -> int:
    """CLI 入口 — 批量升级假设 yaml.

    用法:
        python -m omnicompany.packages.services._diagnosis.doctor.builders.hypothesis_v1_upgrader \\
          --hypotheses-dir data/services/doctor/hypotheses/ \\
          --map-path docs/plans/.../canonical_anchors/standards_authority_map.yaml \\
          [--output-dir <out>]            # 不传写回原 dir
          [--dry-run]                     # 只看分布不写盘
          [--no-finding-archive]          # 跳过 FindingArchive 反向链接 (默认接通)

    V5.2 (2026-05-07) 默认接通 FindingArchive — 修 V5 dogfood 暴露的'CLI 不接 archive'
    漏洞. 跑后 related_finding_ids 真自动填.

    返:
        0 = 成功, 1 = 输入路径错, 2 = 加载 map 失败.
    """
    parser = argparse.ArgumentParser(prog="hypothesis_v1_upgrader")
    parser.add_argument("--hypotheses-dir", required=True, help="假设 yaml 所在目录")
    parser.add_argument("--map-path", required=True, help="standards_authority_map.yaml 路径")
    parser.add_argument("--output-dir", default=None, help="输出目录 (默认写回原 dir)")
    parser.add_argument("--dry-run", action="store_true", help="只看分布不写盘")
    parser.add_argument("--no-finding-archive", action="store_true",
                        help="跳过 FindingArchive 反向链接 (默认接通 — 升级时填 related_finding_ids)")
    args = parser.parse_args(argv)

    hyp_dir = Path(args.hypotheses_dir)
    map_path = Path(args.map_path)
    out_dir = Path(args.output_dir) if args.output_dir else hyp_dir

    if not hyp_dir.exists():
        print(f"ERROR: hypotheses-dir 不存在: {hyp_dir}", file=sys.stderr)
        return 1

    authority_map = _load_yaml(map_path)
    if authority_map is None:
        print(f"ERROR: map-path 加载失败: {map_path}", file=sys.stderr)
        return 2

    yamls = _iter_hypothesis_yamls(hyp_dir)
    if not yamls:
        print(f"WARNING: hypotheses-dir 无 yaml 文件: {hyp_dir}")
        return 0

    # V5.2 默认接通 FindingArchive — 反向查 related_finding_ids
    finding_archive = None
    if not args.no_finding_archive:
        try:
            from omnicompany.packages.services._core.registry.finding_archive import (
                get_finding_archive,
            )
            finding_archive = get_finding_archive()
            print("FindingArchive 接通 (data/registry/findings/)")
        except Exception as e:  # noqa: BLE001 — archive 加载失败不阻塞 CLI 主路径
            print(f"WARNING: FindingArchive 加载失败 (跳过反向链接): {type(e).__name__}: {e}",
                  file=sys.stderr)

    upgrader = HypothesisV1Upgrader(authority_map=authority_map, finding_archive=finding_archive)
    hypotheses: list[tuple[Path, dict]] = []
    for path in yamls:
        data = _load_yaml(path)
        if isinstance(data, dict):
            hypotheses.append((path, data))
        else:
            print(f"SKIP non-dict yaml: {path}")

    result = upgrader.upgrade_batch([d for _, d in hypotheses])
    print(result.summary)

    if args.dry_run:
        print("(--dry-run 不写盘)")
        return 0

    # 写回 (匹原 path → 输出 path)
    written = 0
    for (orig_path, _), upgraded in zip(hypotheses, result.upgraded):
        out_path = out_dir / orig_path.name if out_dir != hyp_dir else orig_path
        _dump_yaml(upgraded.upgraded_dict, out_path)
        written += 1
    print(f"wrote {written} yamls to {out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

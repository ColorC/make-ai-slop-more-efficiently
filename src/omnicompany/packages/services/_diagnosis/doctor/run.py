# [OMNI] origin=omnicompany domain=omnicompany/doctor ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:diagnosis.doctor.diagnosis_pipeline.orchestrator.py"
# OMNI-024 ALLOW: _PassthroughRouter 等辅助 Router 是 bindings 构建的内部实现细节，与 run.py 绑定构建紧耦合
"""doctor.run — Bindings 构建 + 批量诊断入口

单格式：
    python -m omnicompany.packages.services._diagnosis.doctor.run guardian.check-request

批量模式（多 ID）：
    python -m omnicompany.packages.services._diagnosis.doctor.run --ids guardian.check-request,bw.epic

批量模式（目录）：
    python -m omnicompany.packages.services._diagnosis.doctor.run --folder src/omnicompany/packages/services/guardian

批量模式（文件）：
    python -m omnicompany.packages.services._diagnosis.doctor.run --file src/omnicompany/packages/services/guardian/formats.py

批量模式（域前缀）：
    python -m omnicompany.packages.services._diagnosis.doctor.run --domain bw

通用 CLI 用法（通过 omni run format-diagnosis）：
    省略 --ids/--folder/--file/--domain 时走单 material_id 管线
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker


# ─── 内部工具：LLM 节点的 Hard-mode 替代 (继承 Worker, bindings 内部细节) ──

class _FormatLLMPassthroughWorker(Worker):
    """Hard 诊断模式下替代 FormatContextualAuditRouter 的直通 Router。

    输出结构与 FormatContextualAuditRouter 对齐，check_llm_audit.passed=None 表示跳过。
    """
    DESCRIPTION = "Hard 诊断模式下替代 FormatContextualAuditRouter（不调用 LLM，输出跳过标记）"
    FORMAT_IN = "doctor.material.extracted"
    FORMAT_OUT = "doctor.material.check.llm-audit"

    def run(self, input_data: Any) -> Any:
        from omnicompany.protocol.anchor import Verdict, VerdictKind
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={
                "material_id": input_data.get("material_id", ""),
                "source_root": input_data.get("source_root", ""),
                "sig_diff_ok": input_data.get("sig_diff_ok", True),
                "extracted": input_data.get("extracted", {}),
                "check_llm_audit": {
                    "check": "contextual_audit",
                    "passed": None,
                    "severity": "INFO",
                    "detail": "LLM 节点已跳过（hard 诊断模式）",
                    "grade": "N/A",
                    "audit_path": None,
                    "sub_checks": [],
                },
            },
            diagnosis="FormatContextualAudit: skipped (hard mode)",
        )


class _PassthroughWorker(Worker):
    """Hard 诊断模式下替代 Router 管线 LLM 节点的直通 Worker（原样透传 input）。"""
    DESCRIPTION = "Hard 诊断模式下的 LLM 节点直通占位（input 原样输出，不调用 LLM）"
    FORMAT_IN = "diag.worker.det-checks"
    FORMAT_OUT = "diag.worker.audit"

    def run(self, input_data: Any) -> Any:
        from omnicompany.protocol.anchor import Verdict, VerdictKind
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=input_data,
            diagnosis="LLM 节点已跳过（hard 诊断模式）",
        )


class _NarrativePassthroughWorker(Worker):
    """Hard 诊断模式下替代 TeamNarrativeChecker 的直通 Worker。"""
    DESCRIPTION = "Hard 诊断模式下的叙事审计跳过占位（passed=None，不调用 LLM）"
    FORMAT_IN = "diag.team.extracted"
    FORMAT_OUT = "diag.team.check.narrative"

    def run(self, input_data: Any) -> Any:
        from omnicompany.protocol.anchor import Verdict, VerdictKind
        output = dict(input_data)
        output["check_narrative"] = {
            "check": "narrative",
            "passed": None,
            "severity": "INFO",
            "detail": "L4 叙事审计已跳过（hard 诊断模式）",
            "findings": [],
        }
        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis="PipelineNarrativeChecker: skipped (hard mode)",
        )


# ─── 管线 Bindings ────────────────────────────────────────────


def build_bindings(input_dict: dict[str, Any] | None = None,
                   run_llm: bool = True) -> dict[str, Worker]:
    """构建 format-diagnosis 管线的节点→Worker 绑定。

    Args:
        input_dict: 含 source_root / model 的输入字典（可选）。
        run_llm:    False 时用 _FormatLLMPassthroughWorker 替代 MaterialContextualAuditWorker，
                    跳过 LLM 调用（hard 诊断模式）。
    """
    from omnicompany.packages.services._diagnosis.doctor.workers import (
        MaterialExtractorWorker,
        MaterialSignatureDiffWorker,
        MaterialFiveElementCheckWorker,
        MaterialTagCoverageWorker,
        MaterialParentChainWorker,
        MaterialCompositeCheckWorker,
        MaterialExamplePresenceWorker,
        MaterialContextualAuditWorker,
        MaterialHealthWriterWorker,
    )

    source_root = None
    model = None
    if input_dict:
        source_root = input_dict.get("source_root")
        model = input_dict.get("model")

    return {
        "format_extractor": MaterialExtractorWorker(source_root=source_root),
        "signature_diff": MaterialSignatureDiffWorker(),
        "five_element_check": MaterialFiveElementCheckWorker(),
        "tag_coverage": MaterialTagCoverageWorker(),
        "parent_chain": MaterialParentChainWorker(),
        "composite_format_check": MaterialCompositeCheckWorker(),
        "example_presence": MaterialExamplePresenceWorker(),
        "desc_eval": MaterialContextualAuditWorker(model=model) if run_llm else _FormatLLMPassthroughWorker(),
        "health_writer": MaterialHealthWriterWorker(),
    }


def build_router_bindings(run_llm: bool = True) -> dict[str, Worker]:
    """构建 router-diagnosis 管线的节点→Worker 绑定。

    Args:
        run_llm: False 时用 _PassthroughWorker 替代 WorkerContextualAuditor。
    """
    from omnicompany.packages.services._diagnosis.doctor.workers import (
        WorkerAnatomyExtractor,
        WorkerSignatureAnchor,
        WorkerContextCollector,
        WorkerRuleChecker,
        WorkerContextualAuditor,
        WorkerHealthWriter,
    )
    return {
        "rtr_extractor": WorkerAnatomyExtractor(),
        "rtr_signature": WorkerSignatureAnchor(),
        "rtr_context_collector": WorkerContextCollector(),
        "rtr_det_checker": WorkerRuleChecker(),
        "rtr_contextual_audit": WorkerContextualAuditor() if run_llm else _PassthroughWorker(),
        "rtr_health_writer": WorkerHealthWriter(),
    }


# ─── 格式 ID 发现工具 ─────────────────────────────────────────


def discover_ids_in_file(formats_file: Path) -> list[str]:
    """从单个 formats.py 提取所有 Format ID。"""
    try:
        content = formats_file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content)
    except Exception:
        return []
    ids = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not ((isinstance(func, ast.Name) and func.id == "Format") or
                (isinstance(func, ast.Attribute) and func.attr == "Format")):
            continue
        for kw in node.keywords:
            if kw.arg == "id":
                try:
                    ids.append(ast.literal_eval(kw.value))
                except Exception:
                    pass
    return ids


def discover_ids_in_folder(folder: Path) -> list[str]:
    """递归扫描 folder 下所有 formats.py，返回所有 Format ID（去重保序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for fmt_file in sorted(folder.rglob("formats.py")):
        if "__pycache__" in str(fmt_file) or "_graveyard" in str(fmt_file):
            continue
        for fid in discover_ids_in_file(fmt_file):
            if fid not in seen:
                seen.add(fid)
                result.append(fid)
    return result


def discover_ids_by_domain(source_root: Path, domain: str) -> list[str]:
    """返回以 domain 为前缀的所有 Format ID。"""
    all_ids = discover_ids_in_folder(source_root)
    prefix = domain.rstrip(".") + "."
    return [fid for fid in all_ids if fid == domain or fid.startswith(prefix)]


# ─── 内部：TeamRunner 异步驱动 ──────────────────────────


async def _run_format_pipeline_async(
    material_id: str,
    source_root: str,
    run_llm: bool = False,
    model: str | None = None,
) -> dict:
    """通过 TeamRunner + SQLiteBus 运行 Format 诊断链。

    所有节点的 input/output 均经过事件总线记录，保证完整可审计。
    run_llm=False 时 desc_eval 节点被替换为 _PassthroughRouter。
    """
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.core.config import resolve_db_dir
    from omnicompany.runtime.exec.runner import TeamRunner
    from omnicompany.packages.services._diagnosis.doctor.pipeline import build_pipeline

    db_path = resolve_db_dir("doctor") / "events.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pipeline = build_pipeline()
    bindings = build_bindings(
        input_dict={"source_root": source_root, "model": model},
        run_llm=run_llm,
    )

    async with SQLiteBus(db_path) as bus:
        runner = TeamRunner(
            pipeline=pipeline,
            bindings=bindings,
            bus=bus,
            source="doctor-fmt",
        )
        result = await runner.run({"material_id": material_id, "source_root": source_root})

    if hasattr(result, "output"):
        return result.output if isinstance(result.output, dict) else {}
    return result if isinstance(result, dict) else {}


async def _run_router_pipeline_async(
    worker_class: str,
    source_file: str,
    source_root: str,
    run_llm: bool = False,
) -> dict:
    """通过 TeamRunner + SQLiteBus 运行 Router 诊断链。

    所有节点的 input/output 均经过事件总线记录，保证完整可审计。
    run_llm=False 时 rtr_contextual_audit 节点被替换为 _PassthroughRouter。
    """
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.core.config import resolve_db_dir
    from omnicompany.runtime.exec.runner import TeamRunner
    from omnicompany.packages.services._diagnosis.doctor.pipeline import build_router_pipeline

    db_path = resolve_db_dir("doctor") / "events.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pipeline = build_router_pipeline()
    bindings = build_router_bindings(run_llm=run_llm)

    async with SQLiteBus(db_path) as bus:
        runner = TeamRunner(
            pipeline=pipeline,
            bindings=bindings,
            bus=bus,
            source="doctor-rtr",
        )
        result = await runner.run({
            "worker_class": worker_class,
            "source_file": source_file,
            "source_root": source_root,
        })

    if hasattr(result, "output"):
        return result.output if isinstance(result.output, dict) else {}
    return result if isinstance(result, dict) else {}


# ─── 公开同步接口（供 CLI / 同步调用方使用）─────────────────


def _run_hard_diagnosis(material_id: str, source_root: str) -> dict:
    """跑完整 HARD 节点诊断链（跳过 LLM contextual_audit），返回 health_record。

    通过 TeamRunner + SQLiteBus 执行，所有节点 I/O 均记录到事件总线。
    """
    return asyncio.run(_run_format_pipeline_async(material_id, source_root, run_llm=False))


def _run_full_diagnosis(material_id: str, source_root: str, model: str | None = None) -> dict:
    """跑完整诊断链（含 LLM FormatContextualAuditRouter），返回 health_record。

    通过 TeamRunner + SQLiteBus 执行，所有节点 I/O 均记录到事件总线。
    需要 THE_COMPANY_API_KEY 环境变量。
    """
    return asyncio.run(_run_format_pipeline_async(material_id, source_root, run_llm=True, model=model))


def run_router_diagnosis(
    worker_class: str,
    source_file: str,
    source_root: str,
    run_llm: bool = False,
) -> dict:
    """对单个 Router 运行诊断链，返回 health_record。

    通过 TeamRunner + SQLiteBus 执行，所有节点 I/O 均记录到事件总线。
    run_llm=True 时附加 LLM 语义审计，需要 API key。
    """
    return asyncio.run(_run_router_pipeline_async(worker_class, source_file, source_root, run_llm=run_llm))


def build_pipeline_topo_bindings(run_llm: bool = False) -> dict[str, Worker]:
    """构建 pipeline-topology-diagnosis 管线的节点→Worker 绑定。

    Args:
        run_llm: True 时包含 TeamNarrativeChecker（L4 LLM 审计），
                 False 时用 _NarrativePassthroughWorker 替代（跳过 LLM 调用）。
    """
    from omnicompany.packages.services._diagnosis.doctor.workers import (
        TeamSpecLoader,
        TeamStructuralCheck,
        TeamMaterialContractCheck,
        TeamMaturityCheck,
        TeamSoftHardCheck,
        TeamNarrativeChecker,
        TeamTopoHealthWriter,
    )
    return {
        "pipeline_spec_loader":       TeamSpecLoader(),
        "pipeline_structural_check":  TeamStructuralCheck(),
        "pipeline_format_contract":   TeamMaterialContractCheck(),
        "pipeline_maturity_check":    TeamMaturityCheck(),
        "pipeline_soft_hard_check":   TeamSoftHardCheck(),
        "pipeline_narrative_check":   TeamNarrativeChecker() if run_llm else _NarrativePassthroughWorker(),
        "pipeline_topo_health_writer": TeamTopoHealthWriter(),
    }


async def _run_pipeline_topology_async(
    pipeline_file: str,
    pipeline_id: str | None = None,
    run_llm: bool = False,
) -> dict:
    """通过 TeamRunner + SQLiteBus 运行 Pipeline 拓扑诊断管线（fan-out/fan-in 架构）。

    7 个 Router 节点（1 个 Anchor Loader + 5 个并行检查器 + 1 个 HealthWriter），
    input/output 完整记录到事件总线，全量可审计。
    run_llm=True 时启用 L4 叙事审计（PipelineNarrativeCheckerRouter），需要 API key。
    """
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.core.config import resolve_db_dir
    from omnicompany.runtime.exec.runner import TeamRunner
    from omnicompany.packages.services._diagnosis.doctor.pipeline import build_pipeline_topology_pipeline

    db_path = resolve_db_dir("doctor") / "events.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pipeline = build_pipeline_topology_pipeline()
    bindings = build_pipeline_topo_bindings(run_llm=run_llm)

    async with SQLiteBus(db_path) as bus:
        runner = TeamRunner(
            pipeline=pipeline,
            bindings=bindings,
            bus=bus,
            source="doctor-pipeline-topo",
        )
        result = await runner.run({
            "pipeline_file": pipeline_file,
            "pipeline_id":   pipeline_id,
        })

    if hasattr(result, "output"):
        return result.output if isinstance(result.output, dict) else {}
    return result if isinstance(result, dict) else {}


def run_pipeline_topology_check(
    pipeline_file: str,
    pipeline_id: str | None = None,
    run_llm: bool = False,
) -> dict:
    """对 pipeline.py 文件执行 7 节点拓扑诊断管线，返回健康档案 dict。

    通过 TeamRunner + SQLiteBus 执行（fan-out/fan-in 架构），
    5 类检查（结构合法性/Format契约/成熟度一致性/软硬配对/L4叙事审计）均有独立 Router 节点，
    input/output 均记录到事件总线，全量可审计。

    pipeline_id: 若提供，只检查指定 pipeline；否则检查文件内所有 build_*() 管线。
    run_llm:     True 时启用 L4 叙事审计（PipelineNarrativeCheckerRouter），需要 API key。
    """
    return asyncio.run(_run_pipeline_topology_async(pipeline_file, pipeline_id, run_llm=run_llm))


async def _run_pipeline_lineage_async(
    source_root: str,
    material_id: str | None,
    pipeline_id: str | None,
) -> dict:
    """通过 TeamRunner + SQLiteBus 运行 Pipeline Lineage 提取（B2）。"""
    from omnicompany.bus.sqlite import SQLiteBus
    from omnicompany.core.config import resolve_db_dir
    from omnicompany.runtime.exec.runner import TeamRunner
    from omnicompany.protocol.team import (
        TeamSpec, TeamNode, NodeKind, NodeMaturity,
    )
    from omnicompany.protocol.anchor import TransformerSpec, TransformMethod
    from omnicompany.packages.services._diagnosis.doctor.workers.team import TeamLineageExtractor

    db_path = resolve_db_dir("doctor") / "events.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    lineage_pipeline = TeamSpec(
        id="doctor-pipeline-lineage",
        name="Pipeline Lineage Extraction",
        description="扫描所有注册管线，提取跨管线 format 产消图（B2）",
        nodes=[
            TeamNode(
                id="pipeline_lineage",
                kind=NodeKind.TRANSFORMER,
                transformer=TransformerSpec(
                    id="doctor-pipeline-lineage",
                    name="PipelineLineage",
                    from_format="diag.lineage.request",
                    to_format="diag.lineage.report",
                    method=TransformMethod.RULE,
                    description="提取跨管线 format 产消图",
                ),
                maturity=NodeMaturity.GROWING,
            ),
        ],
        edges=[],
        entry="pipeline_lineage",
    )
    bindings = {"pipeline_lineage": TeamLineageExtractor()}

    async with SQLiteBus(db_path) as bus:
        runner = TeamRunner(
            pipeline=lineage_pipeline,
            bindings=bindings,
            bus=bus,
            source="doctor-pipeline",
        )
        result = await runner.run({
            "source_root": source_root,
            "material_id":   material_id,
            "pipeline_id": pipeline_id,
        })

    if hasattr(result, "output"):
        return result.output if isinstance(result.output, dict) else {}
    return result if isinstance(result, dict) else {}


def run_pipeline_lineage(
    source_root: str = "src/omnicompany",
    material_id: str | None = None,
    pipeline_id: str | None = None,
) -> dict:
    """提取跨管线 format 产消图（B2），返回 lineage 报告 dict。

    通过 TeamRunner + SQLiteBus 执行，input/output 均记录到事件总线。
    source_root: 源码扫描根目录（默认 src/omnicompany）
    material_id:   只展示涉及此 Format 的条目（None = 全量）
    pipeline_id: 只展示指定管线（None = 全量）
    """
    return asyncio.run(_run_pipeline_lineage_async(source_root, material_id, pipeline_id))


def run_batch(
    format_ids: list[str],
    source_root: str = "src/omnicompany",
    filter_grades: set[str] | None = None,
    skip_llm: bool = True,
) -> list[dict]:
    """批量诊断，返回每个 Format 的结果 dict 列表。"""
    results = []
    for fid in format_ids:
        if skip_llm:
            r = _run_hard_diagnosis(fid, source_root)
        else:
            # 完整管线（含 LLM desc_eval）— 需要 API key
            r = _run_hard_diagnosis(fid, source_root)  # 暂时同 skip_llm
        # 契约变更 #02 (2026-04-25): 读 v2 字段 verdict + counts, 去 grade/score
        verdict = r.get("verdict", "uncertain")
        counts = r.get("counts", {})
        # filter_grades 改为 filter_verdicts (向后一致但 CLI 参数可能需要适配 · 主 CLI 入口别处)
        if filter_grades and verdict not in filter_grades:
            continue
        results.append({
            "material_id": fid,
            "verdict": verdict,
            "counts": counts,
            "fails": [c["check"] for c in r.get("checks", []) if not c.get("passed")],
        })
    return results


def print_batch_table(results: list[dict]) -> None:
    """打印批量诊断结果表格 (v2 · 不打分)."""
    verdict_counts: dict[str, int] = {}
    for r in results:
        verdict_counts[r["verdict"]] = verdict_counts.get(r["verdict"], 0) + 1

    print(f"\n{'verdict':<10} {'counts':<30} {'Format ID':<50} 失败项")
    print("-" * 120)
    verdict_order = {"unhealthy": 0, "uncertain": 1, "healthy": 2}
    for r in sorted(results, key=lambda x: (verdict_order.get(x["verdict"], 9), x["material_id"])):
        c = r.get("counts", {})
        counts_str = f"crit={c.get('critical',0)} major={c.get('major',0)} minor={c.get('minor',0)}"
        fails_str = "|".join(r["fails"]) if r["fails"] else "-"
        print(f"{r['verdict']:<10} {counts_str:<30} {r['material_id']:<50} {fails_str}")

    print()
    print("统计:", end=" ")
    for v in ("healthy", "uncertain", "unhealthy"):
        n = verdict_counts.get(v, 0)
        if n:
            print(f"{v}={n}", end="  ")
    print(f"共 {sum(verdict_counts.values())} 个 Format")


# ─── CLI 入口 ─────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Format 健康诊断（单 Format 或批量）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # 批量模式（互斥）
    group = parser.add_mutually_exclusive_group()
    group.add_argument("material_id", nargs="?", help="单个 Format ID（如 guardian.check-request）")
    group.add_argument("--ids", help="多个 Format ID，逗号分隔（如 guardian.check-request,bw.epic）")
    group.add_argument("--folder", help="扫描指定目录下所有 formats.py（如 src/omnicompany/packages/services/guardian）")
    group.add_argument("--file", help="扫描指定 formats.py 文件")
    group.add_argument("--domain", help="扫描指定域前缀的所有 Format（如 bw、guardian）")

    parser.add_argument(
        "--source-root",
        default="src/omnicompany",
        help="omnicompany 源码根目录（默认 src/omnicompany）",
    )
    parser.add_argument(
        "--grade",
        help="只显示指定等级（逗号分隔，如 C,D,F）",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="启用 LLM 全语境审计（FormatContextualAuditRouter），需要 THE_COMPANY_API_KEY",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="覆盖 LLM 模型（默认 qwen3.6-plus）",
    )
    args = parser.parse_args()

    source_root = args.source_root
    filter_grades = set(args.grade.upper().split(",")) if args.grade else None

    # ── 确定待诊断的 Format ID 列表 ──
    if args.ids:
        format_ids = [fid.strip() for fid in args.ids.split(",") if fid.strip()]
    elif args.folder:
        folder = Path(args.folder)
        if not folder.exists():
            print(f"错误：目录不存在: {folder}", file=sys.stderr)
            sys.exit(1)
        format_ids = discover_ids_in_folder(folder)
        print(f"发现 {len(format_ids)} 个 Format in {folder}", file=sys.stderr)
    elif args.file:
        fmt_file = Path(args.file)
        if not fmt_file.exists():
            print(f"错误：文件不存在: {fmt_file}", file=sys.stderr)
            sys.exit(1)
        format_ids = discover_ids_in_file(fmt_file)
        print(f"发现 {len(format_ids)} 个 Format in {fmt_file}", file=sys.stderr)
    elif args.domain:
        source_root_path = Path(source_root)
        format_ids = discover_ids_by_domain(source_root_path, args.domain)
        print(f"发现 {len(format_ids)} 个 Format with domain={args.domain}", file=sys.stderr)
    elif args.material_id:
        format_ids = [args.material_id]
    else:
        parser.print_help()
        sys.exit(0)

    if not format_ids:
        print("未找到任何 Format ID。", file=sys.stderr)
        sys.exit(1)

    # ── 单格式：走完整管线并输出完整健康档案 ──
    if len(format_ids) == 1 and not (args.ids or args.folder or args.file or args.domain):
        import json
        if args.audit:
            r = _run_full_diagnosis(format_ids[0], source_root, model=args.model)
        else:
            r = _run_hard_diagnosis(format_ids[0], source_root)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        # 契约变更 #02 (2026-04-25): 读 v2 verdict + counts + passed
        verdict = r.get("verdict", "uncertain")
        counts = r.get("counts", {})
        passed = bool(r.get("passed", False))
        print(f"\n{format_ids[0]}: verdict={verdict}  "
              f"crit={counts.get('critical',0)} major={counts.get('major',0)} minor={counts.get('minor',0)}")
        if args.audit:
            for c in r.get("checks", []):
                if c.get("check") == "contextual_audit" and c.get("audit_path"):
                    print(f"审计报告: {c['audit_path']}")
        sys.exit(0 if passed else 1)

    # ── 批量：表格输出 ──
    results = run_batch(format_ids, source_root, filter_grades=filter_grades)
    print_batch_table(results)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))
    main()

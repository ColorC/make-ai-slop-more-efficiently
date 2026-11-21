# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-05T21:30:00Z type=router status=skeleton agent=ai-ide-current
# [OMNI] summary="WriteFindingRouter 业务工具 V0 — doctor agent 用它产出 doctor.health_finding 实例 (yaml 落盘 + bus 发事件)"
# [OMNI] why="诊断重制阶段 2 step 5: SpecDiagnosticAgent 等诊断 agent 需要把 finding 持久化. 走业务工具不走 write_file 是为了 schema 校验跟 bus 事件统一"
# [OMNI] tags=tool,doctor,write-finding,skeleton
# [OMNI] material_id="material:diagnosis.doctor.tools.write_finding.skeleton.py"
"""WriteFindingRouter · doctor 业务工具 (V0 骨架)

诊断 agent (SpecDiagnosticAgent / 后续 Hypothesis / Exemplar / Plan) 调本工具产出
`doctor.health_finding` Material 实例.

落三份 (2026-05-06 补 registry 接通):
  1. yaml 文件: data/services/doctor/findings/<task_id>/<finding_id>.yaml (诊断本会话归档, 按 task_id 分桶)
  2. JSONL 实体档: data/registry/findings/<entity_kind>/<entity_safe_id>.jsonl (registry 跨会话查询用, 按实体分桶)
  3. (待 V1) bus 事件: 走 SQLiteBus 发 doctor.health_finding 事件

V0 行为: yaml + JSONL 双落 + 返 finding_id. registry 写失败不影响主路径 (degraded log).

## 待做 (V0 → V1)

[x] **registry HealthArchive 接通**: 2026-05-06 完, 走 FindingArchive.append_finding (registry/finding_archive.py)
[ ] **bus 事件**: V1 加 SQLiteBus.publish 发 doctor.health_finding 事件 (现走直接 archive 调用, 不走 bus)
[ ] **finding_id 生成规则**: 当前 timestamp + uuid4 短码. 应跟 omnicompany ID 规范对齐 (registry 命名空间)
[ ] **schema 校验**: doctor.health_finding Material schema 在 doctor/formats.py 定义, 写入前应校验字段齐
[ ] **任务上下文**: task_id 当前从 ToolContext 读, 缺失退化 'unknown'. 应跟 Worker 的 trace_id 对齐
[ ] **测试**: 红绿样本 (finding 字段全 / 缺关键字段时报错指引)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import yaml

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    """omnicompany 项目根 (含 src/omnicompany + docs). find-up 不依赖具体层级数, 鲁棒于文件挪位."""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    # 兜底: 往上 6 层 (cli/commands/file.py → cli → omnicompany → src → omnicompany_root)
    return here.parents[6] if len(here.parents) > 6 else here.parent


_PROJECT_ROOT = _project_root()


def _generate_finding_id() -> str:
    """生成 finding 唯一 id (UTC 时间戳 + 短 uuid)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"F-{ts}-{short}"


class WriteFindingRouter(SingleToolRouter):
    """落一条 doctor.health_finding 实例 (yaml + bus 事件 [bus 待]).

    使用: agent SPEC.tools 加 "write_finding". agent 用工具时 LLM 产 finding 字段,
    工具校验 + 落 yaml + 返 finding_id.
    """

    TOOL_NAME: ClassVar[str] = "write_finding"
    DESCRIPTION: ClassVar[str] = (
        "Persist a doctor.health_finding instance (one diagnostic finding). Each call writes one yaml file.\n"
        "用户铁律 (2026-05-05): 拒打分拥评论, 拒数字要来龙去脉. 不要 severity=critical/major/minor 这种打分; "
        "也不要 confidence 数字. 用 commentary (评论) + concern (来龙去脉) 写自然语言. "
        "你不需要给问题贴 critical/major/minor 标签 — 把'为什么这是个问题, 不修会怎样, 修起来代价' 这类来龙去脉写在 concern 里, "
        "下游汇总时 LLM 自己读语义判, 不需要数字聚合.\n"
        "required: entity_id (path/identifier), entity_kind (worker|material|team|agent|hook|tool|plan), "
        "finding_kind (spec|hypothesis|exemplar|plan), evidence (natural-language sentence quoting concrete location), "
        "commentary (自然语言评论, 一两段, 引规范跟代码具体证据说明这件事是什么), "
        "concern (来龙去脉: 为什么这是个问题, 不修会怎样, 修起来代价多大, 当前优先级如何). "
        "optional: applied_standards (list of standards path:section), applied_hypotheses (hypothesis ids), agent_id. "
        "Returns the assigned finding_id."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Path or identifier of the entity being diagnosed",
            },
            "entity_kind": {
                "type": "string",
                "enum": ["worker", "material", "team", "agent", "hook", "tool", "plan"],
                "description": "Kind of the diagnosed entity",
            },
            "finding_kind": {
                "type": "string",
                "enum": ["spec", "hypothesis", "exemplar", "plan"],
                "description": "Diagnostic method that produced this finding (sourced from /standards or /hypotheses or /exemplars or /plans)",
            },
            "evidence": {
                "type": "string",
                "description": "Natural-language sentence quoting concrete code/doc location (file:line or function/class) supporting the finding",
            },
            "commentary": {
                "type": "string",
                "description": (
                    "自然语言评论 (一两段). 引用规范段落跟代码具体证据, 说明这件事是什么. "
                    "不打分 (不要 critical/major/minor 这种标签). 写完整中文句子, 不堆代号."
                ),
            },
            "concern": {
                "type": "string",
                "description": (
                    "来龙去脉. 解释: 为什么这是问题 (或为什么是值得记的合规 case). "
                    "不修会怎样 (后果). 修起来代价多大. 当前优先级如何 (跟其他事的相对位置). "
                    "完整中文句子, 给读者画面感, 不打分."
                ),
            },
            "applied_standards": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Standards consulted (e.g. ['docs/standards/concepts/worker.md#R-01'])",
                "default": [],
            },
            "applied_hypotheses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hypothesis ids consulted (e.g. ['H-2026-05-05-001'])",
                "default": [],
            },
            "applied_exemplars": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exemplar ids consulted (e.g. ['E-worker-csv_reader-2026-05-05'])",
                "default": [],
            },
            "uncertainty_note": {
                "type": "string",
                "description": (
                    "Optional 自然语言. 表达对本 finding 的不确定性 (例 '规范表述模糊, 此判定基于 LLM 解读'). "
                    "用户铁律: 信息不足时显式说不确定, 不强行二元. 跟 evidence/commentary/concern 共存"
                ),
                "default": "",
            },
            "agent_id": {
                "type": "string",
                "description": "Producing agent identifier (e.g. 'doctor.spec_diagnostic'). Optional",
                "default": "",
            },
        },
        "required": [
            "entity_id", "entity_kind", "finding_kind",
            "evidence", "commentary", "concern",
        ],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = False

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = (
        "meta_io.fs.create_file",
    )

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        # 必填字段已被 INPUT_SCHEMA 校验, 这里组装 finding 对象 + 落盘
        finding_id = _generate_finding_id()
        task_id = getattr(ctx, "current_task_id", None) or "unknown"
        trace_id = getattr(ctx, "trace_id", None) or ""
        # agent_id 优先用 ctx 注入 (agent 框架自动填), 没有时退回 LLM args 提供 (兼容直接调工具的场景)
        agent_id = getattr(ctx, "agent_id", None) or args.get("agent_id") or ""

        # commit_hash 自动填 (self_audit B-3 修复: 老 contextual_auditor 知识找回)
        # 让同对象在不同代码版本的诊断可对比追溯, 关联 git head 锚
        try:
            from omnicompany.packages.services._core.registry import git_head_short_hash
            commit_hash = git_head_short_hash(_PROJECT_ROOT)
        except Exception:
            commit_hash = ""

        finding = {
            "finding_id": finding_id,
            "entity_id": args["entity_id"],
            "entity_kind": args["entity_kind"],
            "finding_kind": args["finding_kind"],
            "evidence": args["evidence"],
            "commentary": args["commentary"],
            "concern": args["concern"],
            "applied_standards": args.get("applied_standards") or [],
            "applied_hypotheses": args.get("applied_hypotheses") or [],
            "applied_exemplars": args.get("applied_exemplars") or [],
            "uncertainty_note": (args.get("uncertainty_note") or "").strip(),
            "agent_id": agent_id,
            "task_id": task_id,
            "trace_id": trace_id,
            "commit_hash": commit_hash,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        # ── 落 1: yaml 文件 (按 task_id 分桶, 诊断本会话归档) ──
        out_dir = _PROJECT_ROOT / "data" / "services" / "doctor" / "findings" / task_id
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{finding_id}.yaml"
            with open(out_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(finding, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            raise ToolExecutionError(
                f"write_finding failed to persist finding {finding_id}: {e}. "
                f"Target: {out_dir}. Check disk / permissions / yaml availability."
            )

        # ── 落 2: JSONL 实体档 (按 entity 分桶, registry 跨会话查询用) ──
        # 失败 degraded 不阻断主路径 — 主路径已成 (yaml 落盘), JSONL 失败只是查询档影响
        archive_path = None
        try:
            from omnicompany.packages.services._core.registry import get_finding_archive
            archive = get_finding_archive()
            archive_path = archive.append_finding(finding)
        except Exception as e:
            logger.warning(
                "[write_finding] registry archive append failed (degraded): %s. finding_id=%s. yaml 已落, 不阻断.",
                e, finding_id,
            )

        # ── 落 3: tech_debt 接通 (self_audit B-8 修) ──
        # finding.concern 含"技术债"/"债务"/"未实现"/"待补" 关键字 → 写 tech_debt semantic_pending
        # 失败 degraded 不阻断
        debt_id = None
        concern_text = finding["concern"]
        debt_keywords = ("技术债", "债务", "未实现", "待补", "走债")
        if any(kw in concern_text for kw in debt_keywords):
            try:
                from omnicompany.packages.services._diagnosis.tech_debt import append_row
                applied = (finding["applied_standards"] or finding["applied_hypotheses"] or finding["applied_exemplars"])
                standard_id = applied[0] if applied else f"doctor.{args['finding_kind']}"
                description = finding["evidence"][:100] + " | " + finding["commentary"][:120]
                result = append_row(
                    project_root=_PROJECT_ROOT,
                    section_name="semantic_pending",
                    fields={
                        "standard_id": standard_id,
                        "target_path": finding["entity_id"],
                        "description": description,
                        "confidence": "",  # 用户铁律: 拒打分, 留空
                        "disposition": f"finding_id={finding_id}",
                        "status": "pending",
                    },
                    dedup_keys=("target_path", "standard_id"),
                )
                if result.ok:
                    debt_id = result.row_id
            except Exception as e:
                logger.warning(
                    "[write_finding] tech_debt append failed (degraded): %s. finding_id=%s. yaml+archive 已落, 不阻断.",
                    e, finding_id,
                )

        logger.info(
            "[write_finding] %s entity=%s kind=%s task=%s archive_path=%s",
            finding_id, args["entity_id"], args["finding_kind"], task_id, archive_path,
        )

        msg = (
            f"Finding {finding_id} persisted "
            f"(kind={args['finding_kind']}, entity={args['entity_id']}). "
            f"yaml: data/services/doctor/findings/{task_id}/{finding_id}.yaml"
        )
        if archive_path:
            rel = str(archive_path).replace(str(_PROJECT_ROOT) + "/", "").replace("\\", "/")
            msg += f"; archive: {rel}"
        if debt_id:
            msg += f"; tech_debt: {debt_id} (semantic_pending)"
        return msg

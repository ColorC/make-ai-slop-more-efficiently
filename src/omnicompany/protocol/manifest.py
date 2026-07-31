# [OMNI] origin=omnicompany domain=protocol ts=2026-04-12T00:00:00Z
# [OMNI] material_id="material:protocol.pipeline_manifest.declaration_model.py"
"""Pipeline 综合声明档案（Manifest）协议

PipelineManifest 补充 TeamSpec 不包含的"意图"信息：
  - purpose / design_rationale：面向 LLM 诊断和自动修复工具的意图声明
  - boundaries：入口/出口 Format + SubPipeline 划分
  - current_status：当前成熟度 / 已知问题 / 最后验证时间
  - health_policy：何时触发重检 / blocking 阈值

为什么需要这个：
  TeamSpec 声明结构（是什么），manifest 声明意图（为什么这样设计）。
  没有意图声明，L4 PipelineNarrativeChecker 只能看到结构，看不到意图，
  无法判断"结构是否服务了设计意图"这一核心问题。

存储约定：
  每条管线在源文件旁的 .omni/manifest.yaml 中维护。
  manifest.yaml 格式等价于 PipelineManifest.model_dump()，可直接 load/dump。

用法：
  from omnicompany.protocol.manifest import PipelineManifest, load_manifest, dump_manifest_yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# 子结构
# ══════════════════════════════════════════════════════════════════════════════

class SubPipelineBoundary(BaseModel):
    """SubPipeline 边界声明。"""

    id: str
    """SubPipeline 唯一标识（对应 pipeline 节点 id）"""

    purpose: str
    """本 SubPipeline 的业务目标（一两句话）"""

    entry_format: str = ""
    """入口 Format ID（与 TeamNode.format_in 一致）"""

    exit_format: str = ""
    """出口 Format ID（与 SubPipeline 终端节点 format_out 一致）"""

    notes: str = ""
    """设计注意事项或边界划分理由"""


class CurrentStatus(BaseModel):
    """管线当前状态快照。"""

    maturity: str = "growing"
    """整体成熟度（对齐 NodeMaturity：hypothetical/growing/mature/crystallized）"""

    last_verified: str = ""
    """最后完整验证时间（YYYY-MM-DD）"""

    known_issues: list[str] = Field(default_factory=list)
    """已知问题列表（自由文本，供 LLM 诊断参考）"""

    notes: str = ""
    """其他状态备注"""


class HealthPolicy(BaseModel):
    """健康检查策略声明。"""

    recheck_on: list[str] = Field(default_factory=lambda: ["commit", "format_change", "router_change"])
    """触发重检的条件（commit / format_change / router_change / manual）"""

    blocking_threshold: str = "any_blocking_finding"
    """判定为 blocking 的阈值（默认：任意 blocking Finding 即触发）"""

    skip_checks: list[str] = Field(default_factory=list)
    """豁免的检查 ID（如 ['purpose_quality']）"""

    notes: str = ""
    """健康策略备注"""


class PipelineBoundaries(BaseModel):
    """管线边界声明。"""

    entry_format: str = ""
    """管线入口 Format ID"""

    exit_format: str = ""
    """管线出口 Format ID"""

    sub_pipelines: list[SubPipelineBoundary] = Field(default_factory=list)
    """SubPipeline 边界列表（按执行顺序）"""


# ══════════════════════════════════════════════════════════════════════════════
# PipelineManifest — 主体
# ══════════════════════════════════════════════════════════════════════════════

class PipelineManifest(BaseModel):
    """Pipeline 综合声明档案。

    补充 TeamSpec 的意图维度，供 LLM 诊断工具（PipelineNarrativeChecker 等）
    理解管线的设计背景和业务目标。

    生成骨架：
        python -m omnicompany.packages.services._diagnosis.doctor.run manifest init <pipeline_file>

    示例（demogame-table-learning）：
        id: demogame.table-learning
        purpose: >
          从历史配表数据中学习字段生成规则，产出可重复执行的配表脚本。
          输入：原始 xlsm + ground truth CSV diff；
          输出：可执行的 Python 配表脚本 + 字段分类档案。
        design_rationale: >
          采用 learn→validate→feedback 循环而非单次 LLM 生成，
          因为配表字段类型多样（formula/MI/auto_derivable），
          单次推断准确率不足以满足业务要求（目标 >95%）。
        boundaries:
          entry_format: demogame.csv.raw
          exit_format: demogame.generated_script
        current_status:
          maturity: growing
          last_verified: "2026-04-11"
          known_issues:
            - "BenchmarkValidatorRouter 的 FORMAT_IN 已修复，但测试覆盖待补"
    """

    id: str
    """管线 ID（对应 TeamSpec.id）"""

    purpose: str = ""
    """管线业务目标（面向 LLM 诊断和人类阅读）。
    回答：这条管线要完成什么业务价值？输入是什么，输出是什么？
    建议 ≥ 50 字，使 L4 叙事审计有足够上下文。
    """

    design_rationale: str = ""
    """设计理由（为什么这样设计，而不是其他方式）。
    面向未来的维护者和自动修复工具。关键决策点、已评估的替代方案、约束条件。
    """

    boundaries: PipelineBoundaries = Field(default_factory=PipelineBoundaries)
    """入口/出口 Format + SubPipeline 划分"""

    current_status: CurrentStatus = Field(default_factory=CurrentStatus)
    """当前状态快照（成熟度/已知问题/最后验证）"""

    health_policy: HealthPolicy = Field(default_factory=HealthPolicy)
    """健康检查策略"""

    tags: list[str] = Field(default_factory=list)
    """附加标签（可与 TeamSpec.tags 对齐）"""

    extra: dict[str, Any] = Field(default_factory=dict)
    """扩展字段（保留向前兼容）"""


# ══════════════════════════════════════════════════════════════════════════════
# 加载 / 保存
# ══════════════════════════════════════════════════════════════════════════════

_MANIFEST_FILENAME = "manifest.yaml"
_OMNI_DIR = ".omni"


def manifest_path(pipeline_file: str | Path) -> Path:
    """返回与 pipeline_file 对应的 manifest.yaml 路径。

    约定：manifest 存放在 pipeline_file 同级目录的 .omni/ 子目录中。
    例：packages/domains/demogame/table_learning/table_learning_pipeline.py
      → packages/domains/demogame/table_learning/.omni/manifest.yaml
    """
    return Path(pipeline_file).parent / _OMNI_DIR / _MANIFEST_FILENAME


def load_manifest(pipeline_file: str | Path) -> PipelineManifest | None:
    """从 pipeline_file 旁的 .omni/manifest.yaml 加载 PipelineManifest。

    Returns:
        PipelineManifest 实例，若文件不存在返回 None。
    """
    path = manifest_path(pipeline_file)
    if not path.exists():
        return None
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return PipelineManifest.model_validate(data or {})
    except Exception:
        return None


def dump_manifest_yaml(manifest: PipelineManifest) -> str:
    """将 PipelineManifest 序列化为 YAML 字符串。"""
    try:
        import yaml  # type: ignore
        data = manifest.model_dump(exclude_none=True, exclude_defaults=False)
        return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except ImportError:
        import json
        return json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2)


def save_manifest(manifest: PipelineManifest, pipeline_file: str | Path) -> Path:
    """将 PipelineManifest 写入 .omni/manifest.yaml。

    Returns:
        写入的文件路径。
    """
    path = manifest_path(pipeline_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_manifest_yaml(manifest), encoding="utf-8")
    return path


def generate_manifest_skeleton(pipeline_spec: Any, pipeline_file: str | Path) -> PipelineManifest:
    """从 TeamSpec 半自动生成 manifest 骨架。

    自动填充：id / purpose（来自 TeamSpec.purpose）/ boundaries（entry_format + exit_format）
    需要人工补充：design_rationale / current_status.known_issues / sub_pipelines
    """
    spec_id: str = getattr(pipeline_spec, "id", "unknown")
    spec_purpose: str = getattr(pipeline_spec, "purpose", "") or ""

    # 推断 entry_format
    entry_format = ""
    nodes = getattr(pipeline_spec, "nodes", [])
    entry_id: str = getattr(pipeline_spec, "entry", "")
    if entry_id and nodes:
        entry_node = next((n for n in nodes if n.id == entry_id), None)
        if entry_node:
            try:
                entry_format = entry_node.format_in
            except Exception:
                pass

    # 推断 exit_format（无出边的节点）
    exit_format = ""
    edges = getattr(pipeline_spec, "edges", [])
    nodes_with_out = {e.source for e in edges}
    terminals = [n for n in nodes if n.id not in nodes_with_out]
    if terminals:
        try:
            exit_format = terminals[0].format_out
        except Exception:
            pass

    return PipelineManifest(
        id=spec_id,
        purpose=spec_purpose or f"（待填写）{spec_id} 管线的业务目标",
        design_rationale="（待填写）为什么采用这种设计，而不是其他方案？",
        boundaries=PipelineBoundaries(
            entry_format=entry_format,
            exit_format=exit_format,
        ),
        current_status=CurrentStatus(
            maturity="growing",
            last_verified="",
            known_issues=["（待填写）已知问题"],
        ),
    )

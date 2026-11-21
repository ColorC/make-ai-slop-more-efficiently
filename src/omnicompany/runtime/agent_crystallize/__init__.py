# [OMNI] origin=claude-code domain=runtime/agent_crystallize ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.package_aggregator.entrypoint.py"
"""AgentNodeLoop 经验沉淀管线 (可拔插).

设计见 docs/plans/[2026-04-14]INFO-SUFFICIENCY/FOUR_TIER_PLAN.md §四.

核心理念:
  AgentNodeLoop 救火时的行为 (调了哪些工具 / 访问了哪些外部节点输出 /
  反复查了什么数据) 蕴含"这个节点其实需要什么信息"的线索.
  不记录 = 活人干完活就消散, 下次死流水线撞同一堵墙.

本包提供:
  protocol:  ExperienceCrystallizer 插件接口 + AgentLoopTrace 数据结构
  trace:     把 AgentNodeLoop 的消息历史/事件转为结构化 trace
  summarizer: TraceSummarizer (阶段 A) — 记录工具使用模式
  format_edge_inferrer: (阶段 C) 推断缺失的 Format 组件
  description_refiner: (阶段 C) LLM 生成 DESCRIPTION 改进建议
  pending_queue: (阶段 D) SpecPatch 落盘到 pending/ 人审队列

拔插控制:
  OMNICOMPANY_CRYSTALLIZE=off         # 全局关
  OMNICOMPANY_CRYSTALLIZE=trace       # 只开 TraceSummarizer
  OMNICOMPANY_CRYSTALLIZE=trace,format,description  # 按名启用
  (默认 off, 观察期手动开)
"""

from .protocol import (
    AgentLoopTrace,
    ExperienceCrystallizer,
    SpecPatch,
    CrystallizerObservation,
)
from .trace import build_agent_loop_trace
from .summarizer import TraceSummarizer
from .format_edge_inferrer import FormatEdgeInferrer
from .description_refiner import DescriptionRefiner
from .pending_queue import write_pending_patch, list_pending_patches

__all__ = [
    "AgentLoopTrace",
    "ExperienceCrystallizer",
    "SpecPatch",
    "CrystallizerObservation",
    "build_agent_loop_trace",
    "TraceSummarizer",
    "FormatEdgeInferrer",
    "DescriptionRefiner",
    "write_pending_patch",
    "list_pending_patches",
    "get_enabled_crystallizers",
    "run_crystallize",
]


def get_enabled_crystallizers() -> list[ExperienceCrystallizer]:
    """按 env var 解析启用的 crystallizer 列表.

    OMNICOMPANY_CRYSTALLIZE 取值:
      off / (unset)    → []
      all              → [trace, format, description]
      trace            → [trace]
      trace,format     → [trace, format]
    """
    import os
    raw = os.environ.get("OMNICOMPANY_CRYSTALLIZE", "").strip().lower()
    if not raw or raw in ("off", "false", "0"):
        return []

    names: set[str]
    if raw in ("all", "on", "true", "1"):
        names = {"trace", "format", "description"}
    else:
        names = {n.strip() for n in raw.split(",") if n.strip()}

    out: list[ExperienceCrystallizer] = []
    if "trace" in names:
        out.append(TraceSummarizer())
    if "format" in names:
        out.append(FormatEdgeInferrer())
    if "description" in names:
        out.append(DescriptionRefiner())
    return out


def run_crystallize(
    crystallizers: list[ExperienceCrystallizer],
    trace: AgentLoopTrace,
    *,
    downstream_eval: dict | None = None,
    output_dir: str | None = None,
) -> list[SpecPatch]:
    """运行所有启用的 crystallizer, 返回合并的 SpecPatch 列表.

    Args:
        crystallizers: 启用的插件实例.
        trace: AgentNodeLoop 运行产出的 trace.
        downstream_eval: 可选下游评价 (下游节点是否 PASS / 质量分).
        output_dir: 若指定, 把所有 SpecPatch 落盘到 <output_dir>/pending/.

    Returns:
        扁平化的 SpecPatch 列表.
    """
    all_patches: list[SpecPatch] = []
    for cz in crystallizers:
        try:
            obs = cz.observe(trace)
            patches = cz.propose(obs, downstream_eval or {})
            for p in patches:
                p.crystallizer = cz.name
            all_patches.extend(patches)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "crystallizer %s failed: %s", cz.name, e, exc_info=True
            )

    if output_dir and all_patches:
        for p in all_patches:
            try:
                write_pending_patch(p, output_dir)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "pending patch write failed for %s", p.patch_id, exc_info=True
                )
    return all_patches

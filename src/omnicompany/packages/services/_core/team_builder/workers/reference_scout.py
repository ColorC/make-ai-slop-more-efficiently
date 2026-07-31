# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-23T00:00:00Z type=worker
# [OMNI] material_id="material:team_builder.workers.reference_scanner.standards_collector.py"
"""ReferenceScoutWorker — agent-first 第二阶段 (2026-04-23).

Worker 协议:
  FORMAT_IN  = team_builder.material.origin_request
  FORMAT_OUT = team_builder.material.team_references

**职责**: 独立上下文 · 扫 standards/similar_team/skill/memory, 列本次 Team 的相关参考清单.

**独立上下文理由** (agent-first 方法论):
  - 与 IntentAnalyzer 认知独立, 可**并行** (二者共享同一 origin_request 输入)
  - 专注"图书管理员" 角色: 找参考 + 列原因, 不深解读
  - 需要文件搜索 + 读多份文档, 但本版先用简单 SOFT (固定扫清单, 不 agent loop)

**本版: SOFT 起步 · 观测后按需升级 AGENT**:
  - 先做简单模式: 启发式扫 docs/standards + 最近 similar teams, 不走 LLM 判断相关性
  - 观测到信息不足 → 升级为 AGENT (grep / read 工具集成 · agent loop)

**实现状态** (agent-first 骨架):
  - V0: 简单 SOFT worker (启发式扫固定路径)
  - 本版实现 stub + 路径清单, 具体文件列表占位
"""
from __future__ import annotations

from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


# 默认扫描范围 (启发式 · 不走 LLM)
# ───────────────────────────────────────
# 只登记当前存在的 canonical 路径；ReferenceScout 不能把已归档或已迁移路径
# 当作新 Team 的设计依据。
_CORE_STANDARDS = (
    ("docs/standards/concepts/team.md", "canonical TeamSpec 与 Team 术语权威", "standard"),
    ("docs/standards/concepts/worker.md", "Worker 职责与复用边界", "standard"),
    ("docs/standards/concepts/material.md", "Material 契约与来源边界", "standard"),
    ("docs/standards/concepts/workspace.md", "workspace 权威 · write 紧 read 宽", "standard"),
    ("docs/standards/concepts/agent_first.md", "是否需要 Agent 循环的判断依据", "standard"),
    ("docs/standards/_global/single_source_thin_wrap.md", "单一真源与薄适配器规则", "standard"),
    ("docs/standards/_global/distributed-docs.md", "OMNI-034 DESIGN.md 七节", "standard"),
    ("docs/standards/_global/terminology.md", "Material、Worker、Team 命名权威", "standard"),
    ("docs/standards/_global/llm_first.md", "LLM 使用边界", "standard"),
    ("docs/standards/cli/llm_infrastructure.md", "统一模型设施入口", "standard"),
)

_SIMILAR_TEAMS = (
    (
        "src/omnicompany/packages/services/_core/agent/external_workers/routers/workflow_node.py",
        "现有 TeamRunner Agent Worker 节点；可直接引用，不重复生成 Agent 启动器",
        "similar_team",
    ),
    (
        "src/omnicompany/packages/services/_focus/meegle_dispatch.py",
        "现有 Meegle 到 Project、Skill、Agent 的薄路由桥",
        "similar_team",
    ),
    (
        "src/omnicompany/packages/services/_core/lifecycle/claim_route.py",
        "Task 到 Team 岗位的唯一原子认领入口",
        "similar_team",
    ),
)

_BUS_INFRA = (
    ("src/omnicompany/runtime/buses/", "ServiceBus 家族 · 产出 Team 必须走此出口", "standard"),
    ("src/omnicompany/bus/", "EventBus async 底座 · 审计落盘终点", "standard"),
)


class ReferenceScoutWorker(Worker):
    """独立上下文 · 扫相关 standards/similar_team/bus 参考, 列出清单."""

    DESCRIPTION = (
        "agent-first 第二阶段 · 独立上下文扫 docs/standards + similar_team + bus infra, "
        "产出 team_references 清单供 TeamArchitect 消费. 与 IntentAnalyzer 可并行."
    )
    FORMAT_IN = "team_builder.material.origin_request"
    FORMAT_OUT = "team_builder.material.team_references"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(
                kind=VerdictKind.FAIL,
                output={},
                diagnosis=f"input_data must be dict, got {type(input_data).__name__}",
            )

        # ─── 扫描参考 (V0 · 当前 canonical 固定清单) ────────
        # TODO(A3 后续): 升级为 AGENT worker
        #   - grep 工具: 按 intent_analysis.domain 关键词找 similar teams
        #   - read 工具: 读 DESIGN.md / manifest.yaml
        #   - LLM 判: 每条参考的相关性评分 + 采纳原因
        # 本版先用启发式清单, 让骨架跑通 material 链路.
        references = []
        for source_path, reason, kind in _CORE_STANDARDS + _SIMILAR_TEAMS + _BUS_INFRA:
            references.append(
                {
                    "source_path": source_path,
                    "reason": reason,
                    "kind": kind,
                }
            )
        # ────────────────────────────────────────────────────

        body_path_hint = "<generated_pkg>/.omni/references.yaml  # 由 Registrar 最终落盘"

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "references": references,
                "body_path": body_path_hint,
                "_meta": {
                    "worker": "ReferenceScoutWorker",
                    "stage": "v0_canonical_inventory",
                    "count": len(references),
                    "note": "固定读取当前 canonical Team、Worker、路由与认领入口",
                },
            },
        )

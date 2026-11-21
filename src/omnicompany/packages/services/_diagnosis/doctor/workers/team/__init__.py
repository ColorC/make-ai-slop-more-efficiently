# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=config
# [OMNI] material_id="material:diagnosis.doctor.worker.team.package_aggregate.py"
"""doctor Team 诊断子域 · 9 Worker (Stage 3 Clean Migration · 命名规范化完成).

订阅拓扑 (详见 ../../team.py build_team_topology_pipeline):
  spec_loader ─(PASS fan-out)→ 5 个 check: structural / material_contract /
                                            maturity / soft_hard / narrative (LLM)
              └─(FAIL EMIT)→ 最小档案
  → fan-in → topo_health_writer

外加两个独立 Worker (原 pipeline_topology.py):
  TeamTopologyCheck     — 一站式拓扑诊断 (旧接口兼容)
  TeamLineageExtractor  — 跨 Team material 产消图 (Lineage B2)

术语说明: 本子域诊断 Team (原 Pipeline). Protocol 层权威类为 TeamSpec (别名 PipelineSpec
仍在 protocol/team.py 尾部保留作迁移期兼容, 新代码直接用 TeamSpec).
业务叙述层统一用 Team. Class 去 "Worker" suffix 避免重复.

旧 class 名 (PipelineXxxWorker) deprecation alias 已在 2026-04-22 清理, 外部代码请用新名.
"""
from __future__ import annotations

from omnicompany.packages.services._core.omnicompany import Worker

from .spec_loader import TeamSpecLoader
from .structural_check import TeamStructuralCheck
from .material_contract_check import TeamMaterialContractCheck
from .maturity_check import TeamMaturityCheck
from .soft_hard_check import TeamSoftHardCheck
from .topo_health_writer import TeamTopoHealthWriter
from .narrative_checker import TeamNarrativeChecker
from .topology_check import TeamTopologyCheck
from .lineage_extractor import TeamLineageExtractor


ALL_WORKERS_TEAM: list[type[Worker]] = [
    TeamSpecLoader,
    TeamStructuralCheck,
    TeamMaterialContractCheck,
    TeamMaturityCheck,
    TeamSoftHardCheck,
    TeamTopoHealthWriter,
    TeamNarrativeChecker,
    TeamTopologyCheck,
    TeamLineageExtractor,
]


__all__ = [
    "TeamSpecLoader",
    "TeamStructuralCheck",
    "TeamMaterialContractCheck",
    "TeamMaturityCheck",
    "TeamSoftHardCheck",
    "TeamTopoHealthWriter",
    "TeamNarrativeChecker",
    "TeamTopologyCheck",
    "TeamLineageExtractor",
    "ALL_WORKERS_TEAM",
]

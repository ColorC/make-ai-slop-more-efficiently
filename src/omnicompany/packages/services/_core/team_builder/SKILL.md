---
name: team_builder
description: omnicompany 元 Team 产 Team - 输入自然语言需求, 跑 11 阶段 agent-first 工作流, 产合规 L3.5 Team 包.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/team_builder ts=2026-05-04T16:32:00Z type=doc status=active agent=ai-ide belongs_to_service=team_builder -->
<!-- [OMNI] summary="team_builder 操作手册 — 跑 meta team 建新 Team 的步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] tags=skill,team_builder,how-to,core,meta-team,agent-first -->
<!-- [OMNI] material_id="material:services._core.team_builder.skill.operations_manual.md"-->

# team_builder · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

## 适用范围

**用我**: 给自然语言需求自动产合规 Team 包 (DESIGN+Worker+Material+Workspace+契约+代码+注册).
**不用我**: 业务正确性测试 / Worker 内业务逻辑 / 跨 Team 协调 / 已有 Team 的修改 (找 [repair](../repair/SKILL.md)).

## 前置条件

- omnicompany 已装 + `THE_COMPANY_API_KEY` + bus 必传
- 知道你想要的新 Team 的自然语言描述 (越具体越好)
- workspace 自动扩到新 package (但记得**显式扩**, 不自动)

## 操作步骤

### 场景 A · CLI 触发建新 Team

```bash
omni run team_builder --text "我要给 demogame 加一个赛季手册自动生成 Team. 输入是赛季配置, 输出是手册 markdown."
```

V1 当前只到草图深度 (DESIGN 七节标题 + Worker/Material 一行 brief). V2 加深化, V3 加代码生成.

### 场景 B · 库调用

```python
from omnicompany.packages.services._core.team_builder.run import run_team_builder

result = run_team_builder(
    text="我要给 ...",
    job_id="...",
)
```

### 场景 C · 大需求递归拆分

如果 `ScaleAssessor` 判 size=large, 走 §3.2 递归路径: 拆子 team → 每个子 team 跑 team_builder → 父组合层合成最终 team_design 链接子 team. 契约 material 作子 team 间 FORMAT 接口.

## 入口清单

| 入口 | 用途 |
|---|---|
| `omni run team_builder --text "..."` | 自然语言触发 |
| `run_team_builder(text, job_id)` (Python) | 库调用 |
| `from .workers import OriginRequestLoader, IntentAnalyzer, ...` | 单 Worker 调用 (测试用) |

## 故障排查

| 现象 | 修 |
|---|---|
| V1 产出只到草图深度 | 当前局限, V2 加 WorkerDesigner/MaterialDesigner 深化 |
| LLM 规范合规失败 (workspace 路径错 / impl_type 自拟) | 当前局限, 需 V2 HARD ContractAuditor 兜底 |
| ReferenceScout 给的参考不准 | V0 启发式 11 条硬编, 升级到 AGENT (grep+read+LLM 判相关性) 待做 |
| 大需求拆分不收敛 | Phase 2 未完, V2 落 ScaleAssessor + DecompositionPlanner |
| 代码生成不自动 | Phase 8 未做, V3 对接 CodeGeneratorLoop |

## 想了解更多

- [README.md](README.md) / [DESIGN.md](DESIGN.md) (11 阶段表 + V1/V2/V3 状态)
- agent-first 哲学 → [docs/standards/concepts/agent_first.md](../../../../../../docs/standards/concepts/agent_first.md)
- workflow → .omni/build_workflow.md
- workspace → .omni/workspace.yaml
- 旧实现 (Diamond 归档) → [_archive/](_archive/)

---
name: doctor
description: omnicompany 管线级健康诊断 service - 跑 Format/Worker/Team/Blackboard 四子域诊断管线, 拿结构化 Finding (blocking/degrading/advisory) + 健康档案.
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/doctor ts=2026-05-04T11:05:00Z type=doc status=active agent=ai-ide belongs_to_service=doctor -->
<!-- [OMNI] summary="doctor 操作手册 — 跑 4 子域诊断管线 (Format/Router/Pipeline/Blackboard) 的操作步骤 + omni run/health/diagnose 入口清单 + 故障排查" -->
<!-- [OMNI] why="DESIGN.md 偏架构, 缺'怎么用'段. 用户/agent 想跑诊断管线得拼 omni run + 管线 ID + input schema, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,doctor,how-to,diagnosis -->
<!-- [OMNI] material_id="material:services._diagnosis.doctor.skill.operations_manual.md"-->

# doctor · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想知道某个 Format / Worker / Team 是否合规 / 健康 (跑独立诊断管线拿 Finding)
- 看现有健康档案 (跨 commit 跟踪回归)
- 跑订阅图级合规检查 (Material kind / FORMAT_IN_MODE / orphan / unconsumed / 子 job)
- 把 doctor 当库调入其他 service / 测试 (不只 CLI)

**不用我**:
- 想直接修代码 → 找 [services/repair/](../../) (doctor 只产 Finding 不动代码)
- 想扫源码合规 (一次性 lint) → 找 [services/_core/guardian/](../../_core/guardian/) (guardian 扫源码 / doctor 跑健康档案多次)
- 想执行业务管线 → 找 [runtime/exec/PipelineRunner](../../../../runtime/exec/) (doctor 自己也是被 runner 跑的)

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (LLM 检查器调 qwen-3.6-plus, 走 the_company 聚合 API)
- 待诊断对象的源码已存在 (诊断不是给"未来代码"用的)
- 跑管线诊断需 target Team 的 `pipeline.py` 路径

## 操作步骤

### 场景 A · 诊断单个 Material (Format)

```bash
omni run doctor.material -i format_id="<id>" -i source_root="<repo_root>"
```

**例**: 诊断 `guardian.file_context_set`:
```bash
omni run doctor.material -i format_id="guardian.file_context_set" -i source_root="."
```

**验证**: 输出含 `health-record` 含 grade (A/B/C/D/F) + Finding 列表 (level + 位置 + 现象). 健康档案落 `data/health/formats/<format_id>.json`.

### 场景 B · 诊断单个 Worker

```bash
omni run doctor.router -i router_id="<class_name>" -i source_root="<repo_root>"
```

**例**:
```bash
omni run doctor.router -i router_id="ManifestAuthorWorker" -i source_root="."
```

**验证**: 健康档案落 `data/health/routers/<router_id>.json`.

### 场景 C · 诊断 Team 拓扑

```bash
omni run doctor.pipeline-topology -i pipeline_py_path="<path/to/pipeline.py>"
```

**例** 诊断 docauthor team:
```bash
omni run doctor.pipeline-topology -i pipeline_py_path="src/omnicompany/packages/services/_authoring/docauthor/team.py"
```

**验证**: 11 条检查 (no_entry / isolated / dead_end / cycle / format_break / soft_hard / maturity / narrative / 等), 输出 Finding 含 check_id. 健康档案落 `data/health/pipelines/<pipe_id>.json`.

### 场景 D · 跑 Blackboard 订阅图诊断 (新世界 V3)

```bash
omni run doctor.blackboard -i team_module="<module.path>"
```

**用途**: 检查 Team 的订阅图合规 — Material kind 标对了 / FORMAT_IN list 是否声明 mode / Verdict.output 是否平铺 / 有没有 orphan Worker / 有没有 unconsumed Material / 子 job 发射是否合规.

**验证**: 6 个独立报告 (各 Worker 一份), 异常会列具体违规位置.

### 场景 E · 看健康档案 / 看回归

```bash
omni health                                # 全体健康概况 (走 health 入口)
omni diagnose --trace-id=<id> --domain=<>  # 给某 trace 跑诊断
ls data/health/formats/                    # 看现有 Format 健康档案
ls data/health/pipelines/                  # 看 Team 健康档案
```

**库调用** (作为其他 service 的依赖):

```python
from omnicompany.packages.services._diagnosis.doctor.run import build_bindings
# 或直接调诊断管线
from omnicompany.packages.services._diagnosis.doctor.pipeline import (
    build_pipeline,                  # Format 诊断 PipelineSpec
    build_pipeline_topology_pipeline,  # Team 拓扑诊断 PipelineSpec
    build_router_pipeline,           # Worker 诊断 PipelineSpec
)
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni run doctor.material` | Format 诊断管线 | `-i format_id` `-i source_root` |
| `omni run doctor.router` | Worker 诊断管线 | `-i router_id` `-i source_root` |
| `omni run doctor.pipeline-topology` | Team 拓扑诊断管线 | `-i pipeline_py_path` |
| `omni run doctor.blackboard` | 订阅图诊断 | `-i team_module` |
| `omni health` | 健康概况 | (顶层命令, `--help` 看选项) |
| `omni diagnose` | trace 级诊断 | `--trace-id` `--domain` |
| `build_pipeline` 等 (Python) | 库调用 | 见 `pipeline.py` 三个 build_*_pipeline 函数 |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| Format 诊断报 "Format 未找到定义" | 该 format_id 在源码里没有正式定义 | 先去 `formats.py` 加定义, 或检查 format_id 拼写 |
| LLM 检查器 (desc_eval / narrative) 跑得慢 / 贵 | LLM 检查器调 1-2 次 qwen-3.6-plus, 大管线开销大 | 按 ID 关闭某些 LLM 检查 (注册表设计支持), 或加缓存 (Phase 2 计划) |
| Pipeline 拓扑报 cycle 但实际是 feedback 管线 | cycle 检查对 feedback 管线误报 | 用 `disabled=["cycle"]` 关掉这条检查 |
| Router 诊断说 "deterministic 但有 LLM 调用" | RouterDeterministicCheck 当前只看是否有 LLM 调用 | 当前局限 (D5/D7 局限 3), 准确判定需要 run 两次比较输出 |
| 健康档案找不到 / 集中在 `data/health/` 不在包里 | Phase 2 `.omni/health/` 就近写盘未完成 | 当前局限 (局限 1), 升级路径在 DESIGN |
| Blackboard 诊断说 orphan Worker | Worker 订阅了无 producer 且非 `kind.source` 的 Material | 检查 Material 的 kind 标是否对, 或补 producer Worker |
| 多次诊断同一 Format 都没变 | health_record 没缓存机制, 每次重跑 | 当前局限, Phase 2 计划加 7 天缓存 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D7 决策 / 三条管线拓扑 / Clean Migration V2) → [DESIGN.md](DESIGN.md)
- Material 五要素规范 → [docs/standards/material.md](../../../../../../docs/standards/concepts/material.md)
- Worker 设计单 / R-18 粒度 → [docs/standards/worker.md](../../../../../../docs/standards/concepts/worker.md)
- Team 叙事检查标准 → docs/standards/pipeline-narrative.md (如有)
- guardian (源码合规) — 跟 doctor 互补 → [../../_core/guardian/](../../_core/guardian/)

---
name: repair
description: omnicompany 修理员 - 消费 doctor Finding 迭代调 LLM 产 Format/Worker 修复补丁并应用, 12 Worker 分两子管线 (Format 修复 / Worker 修复).
user-invocable: false
disable-model-invocation: false
---

<!-- [OMNI] origin=ai-ide domain=services/repair ts=2026-05-04T14:00:00Z type=doc status=active agent=ai-ide belongs_to_service=repair -->
<!-- [OMNI] summary="repair 操作手册 — 跑 Format/Worker 修复管线的操作步骤 + 入口清单 + 故障排查" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §六 模板严格写. DESIGN 偏架构, 缺'怎么用'段, 抽出独立 SKILL 让操作可定位" -->
<!-- [OMNI] tags=skill,repair,how-to,core -->
<!-- [OMNI] material_id="material:services._core.repair.skill.operations_manual.md"-->

# repair · 操作手册

> 设计目的请看 [README.md](README.md). 内部架构请看 [DESIGN.md](DESIGN.md).

---

## 适用范围

**用我**:
- 想给某个 Format / Worker 自动修 doctor 报的 B 类问题 (description 不全 / tags 缺 / parent 断 / FAIL 路径缺 / 等)
- 已经跑过 doctor 拿到 Finding (repair 不自己诊断)
- 接受迭代式修复 (LLM 产 patch → 应用 → 重诊断 → 不通过再产)

**不用我**:
- 想诊断 (产 Finding) → 找 [doctor](../../_diagnosis/doctor/SKILL.md)
- 想修 A/C 类问题 (异步 Worker / FORMAT_IN list[str]) — 当前不支持 (Phase 2 backlog)
- 想修 Pipeline edges → 当前不支持
- 想修业务代码 → 各 domain Team 自己修, 不归 repair

## 前置条件

- omnicompany 已装 (`omni --help` 确认)
- 有 `THE_COMPANY_API_KEY` (LLM 产 patch 走 qwen-3.6-plus)
- 待修 Format / Worker 在源码内 (相对 source_root)
- 跑 doctor 拿到 Finding (repair 内部会重跑 doctor 验证, 但用户操作时通常先看 doctor 报告才决定要不要修)

## 操作步骤

### 场景 A · 修单个 Format

```bash
omni run repair.fmt -i format_id="docauthor.manifest-request" -i source_root="."
```

**参数**:
- `format_id`: 要修的 Format ID (从 doctor 报告里挑)
- `source_root`: 源码根 (默认 `.`)
- `max_iterations`: 默认 3 (迭代上限, 超过即放弃返回失败)

**验证**: 输出 `repair.fmt.report` 含 `initial_grade` / `final_grade` / `iterations` / `success`. final_grade=A → 修成功; iterations=max + success=False → 放弃, 手动介入.

### 场景 B · 修 Worker (B 类问题: R-01 description / R-05 FAIL / R-07 granted_tags)

```bash
# Worker 修复子管线当前通过 run_router_repair() 辅助函数驱动 (未进 build_pipeline 主 Team)
python -c "
from omnicompany.packages.services._core.repair.routers import run_router_repair
result = run_router_repair(
    router_id='ManifestAuthorWorker',
    source_root='.',
    issues=['R-01', 'R-05', 'R-07'],  # 哪些 B 类问题要修
)
print(result)
"
```

**注意**: Worker 修复子管线当前**没进 build_pipeline 主 Team** (D2 局限), 只能通过 Python 调用. Phase 1 提升为第二条 pipeline 后才能 `omni run repair.router`.

### 场景 C · 库调用 (Format 修复)

```python
from omnicompany.packages.services._core.repair.run import build_bindings
from omnicompany.packages.services._core.repair.pipeline import build_pipeline
from omnicompany.runtime.exec import PipelineRunner

team = build_pipeline()
runner = PipelineRunner(team, bindings=build_bindings())
result = runner.run({"format_id": "...", "source_root": "."})
```

## 入口清单

| 入口 | 用途 | 主要参数 |
|---|---|---|
| `omni run repair.fmt` | Format 修复 (1 节点 AgentLoop 包迭代) | `-i format_id` `-i source_root` `-i max_iterations` |
| `run_router_repair()` (Python) | Worker 修复 (9 Worker 线性, 未进主 Team) | `router_id` / `source_root` / `issues` |
| `build_pipeline()` (Python) | Format 修复 Team | 见 [pipeline.py](pipeline.py) |
| 单 Worker 调用 (测试用) | 跑某段 | 见 [workers/](workers/) |

详细 CLI 规范: docs/standards/cli/omnicompany_cli.md

## 故障排查

| 现象 | 可能原因 | 怎么修 |
|---|---|---|
| 报 THE_COMPANY_API_KEY 缺失 | 环境变量没设 | 配 `~/.env` `THE_COMPANY_API_KEY=...` |
| Format 修复 final_grade 仍 D / F | LLM 多次修都没修对, 问题可能结构性 (不只 description) | iterations 跑满 success=False, 手动介入; 看 `repair.fmt.report` 里 LLM 产的 delta 历史诊断 |
| LLM 产的 delta 改动很大但语义偏移 (D7 局限 4 LLM 幻觉) | LLM 没真懂 Format 用途 | 升级 Diagnosis Agent Worker (R-21) 先质疑 Finding 再修 (Phase 2 backlog) |
| Worker 修复跑不起来 | 没用 Python 调 `run_router_repair()`, 用了 `omni run` | 当前 D2 局限, Worker 修复未进主 Team, 用 Python 调 |
| Patch 应用后代码报错 | `PatchValidator` 漏验某种问题, AST 检查不全 | 看 `data/repair/applied/<id>/` 里的 backup, 还原后改 PatchValidator |
| 修完文件失踪 / 内容错乱 | `PatchApplier` 写入失败 | 检查 `data/repair/applied/` backup 是否存在; 还原后看错误日志 |
| 改完不重诊断 (Format 修复) | `RediagnoseWorker` 当前未进 Worker 修复主线 (局限 3) | 手工跑 `omni run doctor.material -i format_id="..."` 重诊断 |
| 想跑超过 max_iterations=3 | 默认硬上限是 3, 业务上够 | 改参数 `-i max_iterations=10`, 但通常超过 3 没改对说明结构问题 |

## 想了解更多

- 设计目的 → [README.md](README.md)
- 内部架构 (D1-D6 决策 / 12 Worker 数据流 / Format 修复 vs Worker 修复) → [DESIGN.md](DESIGN.md)
- 上游 doctor → [../../_diagnosis/doctor/SKILL.md](../../_diagnosis/doctor/SKILL.md)
- Worker 设计单 R-01/R-05/R-07 → [docs/standards/worker.md](../../../../../../docs/standards/concepts/worker.md)
- Format 五要素 F-01/F-06/F-08 → [docs/standards/material.md](../../../../../../docs/standards/concepts/material.md)

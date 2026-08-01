<!-- [OMNI] origin=ai-ide domain=services/registry ts=2026-05-04T10:00:00Z type=doc status=active agent=ai-ide belongs_to_service=registry -->
<!-- [OMNI] summary="registry service 自我叙事 README — 这是什么/为什么/规划. omnicompany 户籍系统, 扫源码注册所有实体提供查询跟健康档案" -->
<!-- [OMNI] why="DESIGN.md 核心目的段抽到 README, 让设计目的归 README, 架构归 DESIGN, 操作归 SKILL. 三件套样例首份 service 级落地" -->
<!-- [OMNI] tags=readme,registry,service,self-narrative -->
<!-- [OMNI] material_id="material:services._core.registry.readme.self_narrative.md"-->

# registry · 行政部户籍系统

> omnicompany 实体普查中心 — 扫源码注册所有 Material / Worker / Team / Agent / Tool / Hook, 提供查询 + 健康档案 + 增量扫描. 让"系统里有什么"有真权威源, 不靠 grep 不靠拼.

---

## 这是什么

registry 是 omnicompany 的**户籍系统**. 它定期扫描 src/ 源码, 把所有受管实体 (六元类型: Material/Worker/Team/Agent Worker/Tool/Hook) 注册到统一存储, 暴露链式查询 API + 健康快照历史, 给所有其他服务消费.

形态上 registry **不是 Team** — 没有 Material 流水, 没有 FORMAT_IN/OUT 管线. 它是**行政部元服务库** (shared infrastructure), 供所有 Team / agent / CLI 调用. 跟 [terminology.md §2 行政部](../../../../../../docs/standards/_global/terminology.md) 概念对齐.

## 解决什么 / 不解决什么

**解决**:
- 实体普查 — 系统里当前有多少 Team / Worker / Material / Agent / Tool / Hook
- 多维度查询 — 按 package / stage / tag / kind 找匹配实体
- 健康档案 — 跨 scan 跟踪回归 (HealthSnapshot 绑 git commit hash)
- 增量扫描 — git diff 驱动, CI 快速健康检查
- G2 索引 — `material_id` ↔ 文件路径双向映射 (CORE-SELF-STABILITY 第一阶段加)

**不解决**:
- 运行时调度 (那是 [runtime/exec/](../../../../runtime/exec/) 的 PipelineRunner 职责)
- Material 的 stock 存储 (那是 [bus/](../../../../bus/) 职责)
- Team / Worker 行为正确性 (那是 [doctor service](../../doctor/) 职责)
- 写入注册 (那是 [register CLI](../../../../../../docs/standards/cli/registration.md) 跟 registration service 职责 — registry 是查, register 是写)

## 设计目的与最终目标

**设计目的**: 让 omnicompany 有真"户籍" — 任何 agent / 用户问"系统里有多少 X / X 在哪" 都能从 registry 拿到权威答案. 替代"grep 拼凑" 这种不可靠模式.

**最终目标** (当下能认知的): registry 是自我画像的**实例索引基座**. CORE-SELF-STABILITY plan 第二阶段建"自我认知 service" 时, registry 提供它消费的实例数据 (1612 条 G2 索引 + 健康快照). 让自我画像能从"实例 → 实现文件 → 所属 service → 提供能力 → 系统层主题"端到端 traverse.

## 规划

- **当前 V1**: 六元注册到位 + 健康档案 + 增量扫描 + G2 索引 (1612 条 material_id ↔ 路径映射)
- **下一步**: Phase 1 新 runtime 到位后加 `agent_worker` 类型, 取代过渡期的 `agent_loop` 类型
- **远景**: 跟自我认知 service 接通, 成自我画像的实例索引承载

进度细节: docs/PROGRESS.md (项目级进度) + [DESIGN.md `## 状态`](DESIGN.md) (本 service 状态).

## 构成

- 元类型注册 → [meta.py](meta.py) — `MetaTypeRegistry` (六元 + `register_type()` 开放扩展)
- 实例存储 → [instance.py](instance.py) — `InstanceRegistry` (六元各一份 JSONL)
- 源码扫描 → [scanner.py](scanner.py) — AST 扫源码产 `InstanceEntry`
- 查询 API → [query.py](query.py) — `RegistryQuery` 链式查询
- 健康档案 → [archive.py](archive.py) — `HealthArchive` 跨 scan 历史
- 增量扫描 → [incremental.py](incremental.py) — `IncrementalDiagnosis` git diff 驱动
- G2 索引 → [material_index.py](material_index.py) — `MaterialIdIndex` (CORE-SELF-STABILITY 第一阶段加)

技术架构详述见 [DESIGN.md](DESIGN.md), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 命名 + omnicompany 概念映射 → [docs/standards/terminology.md](../../../../../../docs/standards/_global/terminology.md)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)

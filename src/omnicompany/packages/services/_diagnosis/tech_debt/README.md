<!-- [OMNI] origin=ai-ide domain=services/tech_debt ts=2026-05-04T12:15:00Z type=doc status=active agent=ai-ide belongs_to_service=tech_debt -->
<!-- [OMNI] summary="tech_debt service 自我叙事 README — REGISTRY.md 共享数据源的 I/O 跟管理层. 跟 guardian 分工 (生产者 vs 消费者), producer 三线 + DriftChecker, omni debt 命令族管 list/stats/resolve/scan/add" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写. 抽分工铁律 + producer 三线设计意图到 README, DESIGN 留架构内部" -->
<!-- [OMNI] tags=readme,tech_debt,diagnosis,self-narrative -->
<!-- [OMNI] material_id="material:services._diagnosis.tech_debt.readme.self_narrative.md"-->

# tech_debt · 技术债登记处管理层

> `docs/tech_debt/REGISTRY.md` 共享数据源的 **I/O + 管理层**. 跟 guardian 分工 (生产者 vs 消费者): guardian 扫规则写 §活跃违规, tech_debt 读全 section + 管 resolve / list / stats. 加 DriftChecker 周期性产 §文档漂移条目.

---

## 这是什么

tech_debt 是 omnicompany 的 **REGISTRY.md 共享数据源 owner service**. 它读写 `docs/tech_debt/REGISTRY.md` (各 producer 写不同 section, 不交叉), 提供查询 + 解决 + 统计 + 漂移检查 + 主动登记五件事.

形态: **不是 Team**, 是**数据层 owner 服务**. 没有 Material 流水 / FORMAT_IN/OUT 管线. 是 producer (guardian / semantic_auditor / DriftChecker) 跟 consumer (omni debt CLI / 外部 agent) 的中间层.

跟 guardian 的**分工铁律** (2026-04-18 用户明示):

| 维度 | omni guardian | omni debt |
|---|---|---|
| 视角 | 违规**生产者** (扫规则) | 债务**消费者** (管库存) |
| 主动作 | patrol / daemon / stamp / zombies | list / stats / resolve |
| 写什么 | §活跃违规 (Guardian 自己 append) | §已解决 (人工 resolve 移条目) |
| 命令族 | 扫描 / 识别 / 纠正 | 回看 / 统计 / 归结 |

producer 三条线 + 主动登记入口都写 REGISTRY.md 的不同 section, 不交叉:
- **Guardian** → §活跃违规 (快速确定性, 每次 commit / 触发)
- **SemanticAuditor** → §语义合规待审 (LLM 按需 / 周期)
- **tech_debt.DriftChecker** → §文档漂移 (周期性时间维度检查 DESIGN.md + plan.md)
- **外部 agent / 人工** (`omni debt add` / 直接编辑 markdown) → 任意 section

## 解决什么 / 不解决什么

**解决**:
- 当前仓库有多少债 (按 section / rule_id / status / severity 统计)
- 哪些还没处理 (按 status=open 列)
- 谁该处理什么 (按 severity / target / drift kind)
- 怎么标记解决 (resolve 软移到 §已解决 + 写 ARCH-CHANGES 事件)
- 文档漂移检测 (DESIGN.md / plan.md 时间维度)

**不解决**:
- 扫描违规 (那是 [guardian](../../_core/guardian/) / [semantic_auditor](../semantic_auditor/) 职责)
- 修复 (那是 [services/repair/](../../) 或人工)
- 自造新条目 (conditions 必须由 producer 或人工手写, tech_debt 不凭空产)

## 设计目的与最终目标

**设计目的**: REGISTRY.md 的读写已经出现在三处 (guardian / semantic_auditor / omni debt), **共享数据源需要独立 owner 包**, 否则三处各自维护 I/O 模板 → 格式漂移. tech_debt 是这个 owner.

**理论锚点**: 跟 omnicompany 主轴第三件能力"自维护 / 自诊断 / 自认知" 对接. tech_debt 是"自认知" 的载体之一 — 系统知道自己有多少债 / 哪些待处理.

**最终目标** (当下能认知的):
- Phase C5: Sentinel 后台集成 (git hook / 定时器), 让漂移检查自动化跑
- 阈值可配置 (现 14/14/30 天硬编码)
- ARCH-CHANGES.jsonl schema 统一 (当前各 producer 字段略有不同)
- 接入 CORE-SELF-STABILITY 第二阶段 自我画像漂移检测 — DriftChecker 升级为画像-现实漂移监测的统一入口

## 规划

- **当前 V1 Phase C4** (active, 2026-04-18): REGISTRY + DriftChecker + 主动登记 + scan 协调 + 统一 ARCH-CHANGES writer
- **下一步 Phase C5**: Sentinel 后台集成; 按需把阈值做成可配置
- **远景**: 跟自我画像漂移检测协作

进度细节: docs/PROGRESS.md (项目级) + [DESIGN.md `## 状态`](DESIGN.md) (本 service 状态).

## 构成

tech_debt 不是 Team 形态. 三个核心模块:

- **registry_io** → [registry_io.py](registry_io.py) — REGISTRY.md 解析 / 写入 / 查询 / resolve
  - `RegistrySnapshot` / `load_registry` / `list_rows` / `compute_stats` / `resolve_row` / `append_row`
- **drift_checker** → [drift_checker.py](drift_checker.py) — 周期性时间维度漂移检查
  - `check_design_md_drift` (代码 mtime > DESIGN.md mtime + 14 天)
  - `check_plan_drift` (active plan stale 14 天 / non-archived old 30 天)
  - `run_drift_audit` (统一入口写 §文档漂移)
- **events** → [events.py](events.py) — ARCH-CHANGES.jsonl 唯一 schema owner (Phase C4)
  - `append_event` (统一写入, guardian / semantic_auditor 内部都调它)
  - `read_events`

CLI: [`cli/commands/debt.py`](../../../../cli/commands/debt.py) — omni debt 命令族 (5 个子命令).

技术架构详述见 [DESIGN.md](DESIGN.md) (含 D1-D8 决策 + 共享数据源拓扑), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 数据源 → docs/tech_debt/REGISTRY.md
- 事件流 → docs/ARCH-CHANGES.jsonl
- producer (guardian) → [../../_core/guardian/README.md](../../_core/guardian/README.md)
- producer (semantic_auditor) → [../semantic_auditor/](../semantic_auditor/)
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)

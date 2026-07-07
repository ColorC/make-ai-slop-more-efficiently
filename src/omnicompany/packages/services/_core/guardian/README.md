<!-- [OMNI] origin=ai-ide domain=services/guardian ts=2026-05-04T11:30:00Z type=doc status=active agent=ai-ide belongs_to_service=guardian -->
<!-- [OMNI] summary="guardian service 自我叙事 README — 源码/文档/架构合规自动巡逻, 83 条规则实例/66 个基础编号(2026-07-02 实测) OMNI-001~035, 跟 Doctor 互补 (Guardian 静态/Doctor 运行时)" -->
<!-- [OMNI] why="DESIGN.md 285 行混了核心目的+架构. Guardian 是核心层四件武器之一+自稳第二阶段地基. README/DESIGN/SKILL 拆开让目的+架构+操作各管一段" -->
<!-- [OMNI] tags=readme,guardian,core,self-narrative -->
<!-- [OMNI] material_id="material:services._core.guardian.readme.self_narrative.md"-->

# guardian · 源码合规自动巡逻

> 本仓每次改动是否仍合规? OMNI-NNN 规则家族扫源码 / 文档 / 架构边界. Phase 1 全部 warn 不杀生, Phase 2 逐条升级到 fix/block. 是 omnicompany 核心层四件武器之一.
> 核心设施统一的方向权威见 authority-confirmation.md, 长程门禁见 autonomous-execution-rules.md; guardian 只负责用 OMNI-093 防漂移, 不复制第二套决断。

---

## 这是什么

guardian 是 omnicompany 的**源码 / 文档 / 架构规范的自动巡逻 service**. 它在 git hook / CLI / 定时器 / 阈值触发下扫源码, 把违规标 `Violation` (含 OMNI-NNN 编号 / 路径 / 严重度 / 修复建议), 同步到 docs/tech_debt/REGISTRY.md 跟 ARCH-CHANGES.jsonl.

跟 [doctor](../../_diagnosis/doctor/) 分工互补:
- **doctor** = 运行时健康诊断 (Format / Worker / Team 是否语义正确)
- **guardian** = 源码 / 文档静态合规 (文件位置 / 命名 / OmniMark / 架构边界 / 分布式文档)

形态: 既是哨兵 (sentinel daemon 常驻巡逻) 又是 git hook (pre-commit 拦截) 又是 CLI (`omni guardian patrol`). 多触发源汇聚到同一规则引擎.

## 解决什么 / 不解决什么

**解决**:
- 文件位置规范 (OMNI-007 src 不放文档 / OMNI-014 非法 drawer / OMNI-015 仓库根禁区)
- 命名规范 (OMNI-030 版本号不进文件名 / OMNI-031 test_*.py 前缀 / OMNI-033 别名禁用)
- OmniMark 头存在性 + 字段合规 (OMNI-001)
- 分布式文档 v2 合规 (OMNI-035 系列)
- DESIGN.md 七节齐全 (OMNI-034)
- 架构边界 (OMNI-002~006/013 — 包不互越界 / SDK 集成边界)
- 唯一权威收束防漂移 (OMNI-093 — 确认表 active、执行规范不断链、分散入口只做锚点)
- 自动罚单 + 7 天逾期升级 evolve-signal (相比 doctor 的健康档案, guardian 关心"违反" 而非"评分")

**不解决**:
- 运行时诊断 → 找 [doctor service](../../_diagnosis/doctor/)
- 修复 → guardian 主要 warn, 修是 [services/repair/](../../) 跟 [tow_truck.py](tow_truck.py) (轻量改名/挪位) 的事
- 语义正确性 → 主干仍是字符串 / AST 规则, 语义判断走 needs_judgment + Guardian Agent LLM 复核 (规模化未铺开)

## 设计目的与最终目标

**设计目的**: 让"项目结构合规" 不靠人记每条规则, 不靠 commit 时被人挑刺. 机器扫一次给出 N 条违规清单 + 改法建议. L2 看清单 / agent 自查 / pre-commit 拦杀.

**理论锚点**: guardian 是 控制结构.md §三 核心层四件武器 之一 ("Guardian / WorkflowFactory / Overseer 未落地 / Doctor 雏形"). 它跟 自稳定主轴第三件能力 — "架构自维护 / 自诊断 / 自认知" — 直接对应 — 是"架构自维护" 的自动化执行者.

**最终目标** (当下能认知的):
- needs_judgment 规则规模化, 让 LLM 复核成主流 (不是字符串硬规则)
- 跟 CORE-SELF-STABILITY 第二阶段 协作 — 升级到含语义级规则 (例 "OmniMark 头的 implements_capability 跟代码 import 关系应一致" 这种漂移检测)
- 接入自我画像 (CORE-SELF-STABILITY 第一阶段铆钉的 belongs_to_service 字段) 做合规校验
- pre-commit 拦截集合扩展 (当前 BLOCK_RULES 含 OMNI-014~018 / 035f~i HIGH, 后续逐条加)

## 规划

- **当前 V2** (active): 规则拆家族模块 (10 个家族文件) + certainty 双轨 (absolute / needs_judgment) + Guardian Agent LLM 复核雏形 + OMNI-034/035 全系列加入
- **下一步**: OMNI-034d/e TBD 检测从字符串迁到 LLM (M1 计划); auto_check 时间/变更阈值完全接通; 统一技术债登记处
- **远景**: needs_judgment 规模化 + 自稳第二阶段语义级规则

## 构成

guardian 不是 Team 形态 (虽然有"巡逻管线"), 是**多功能合规 service**. 关键组件:

| 组件 | 职责 | 入口 |
|---|---|---|
| 巡逻管线 (3 节点) | 扫文件 → 跑规则 → 写报告 | [pipeline.py](pipeline.py) + [workers/](workers/) |
| 规则家族 | OMNI-NNN 规则定义, 按语义家族组织; OMNI-093 守护唯一权威收束 | [rules/](rules/) |
| 规则引擎 | FileContext / GuardianRule / Violation | [rules/_base.py](rules/_base.py) |
| Guardian Agent (LLM 复核) | needs_judgment 规则二次裁定 | judge_agent.py + [llm_judge_agent.py](llm_judge_agent.py) |
| Sentinel (常驻巡逻) | 增量巡逻 + 唤醒机制 + 罚单逾期升级 | [sentinel.py](sentinel.py) |
| 拖车 (修复) | warn-only 之外的轻量改动 | [tow_truck.py](tow_truck.py) |
| auto_check (阈值触发) | commit 数 / 时间 / 变更行数阈值 | [auto_check.py](auto_check.py) |
| Audit 留档 | append-only 五元组 (path+rule+version+prompt+sha) 防重跑 | [audit_store.py](audit_store.py) |
| Hook 安装 | git pre-commit 幂等安装 (5 态管理 + marker) | [hook_installer.py](hook_installer.py) |
| Whitelist 豁免 | hygiene scan 白名单 (带 expires) | [hygiene_whitelist.py](hygiene_whitelist.py) |
| Registry 同步 | patrol 结果同步到 tech_debt + ARCH-CHANGES | [registry_updater.py](registry_updater.py) |
| Evolve signal | 罚单 7 天逾期升级 | [evolve_signal.py](evolve_signal.py) + [guardian_hook.py](guardian_hook.py) |

技术架构详述见 [DESIGN.md](DESIGN.md) (含 D1-D10 决策 + 83 条规则实例/66 个基础编号(2026-07-02 实测) 规则家族表 + 巡逻管线拓扑), 操作手册见 [SKILL.md](SKILL.md).

## 想了解更多

- 架构 → [DESIGN.md](DESIGN.md)
- 操作手册 → [SKILL.md](SKILL.md)
- 跟 doctor 互补 → [../../_diagnosis/doctor/](../../_diagnosis/doctor/)
- 自稳第二阶段道路 → [docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md](../../../../../../docs/plans/guardian/[2026-05-04]CORE-SELF-STABILITY/plan.md)
- 控制结构 (核心层四件武器) → docs/控制结构.md
- 项目根叙事 → [../../../../../README.md](../../../../../../README.md)

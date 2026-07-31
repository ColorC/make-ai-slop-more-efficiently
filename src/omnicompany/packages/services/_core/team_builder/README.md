<!-- [OMNI] origin=ai-ide domain=services/team_builder ts=2026-05-04T16:30:00Z type=doc status=active agent=ai-ide belongs_to_service=team_builder -->
<!-- [OMNI] summary="team_builder service 自我叙事 README — Team of Teams 元 Team. agent-first 设计, 输入自然语言需求 → 产出合规 L3.5 Team 包 (DESIGN+Worker+Material+Workspace+契约审计+代码生成+注册)" -->
<!-- [OMNI] why="按 self_narrative_three_files.md §四 模板严格写, 快速推进" -->
<!-- [OMNI] tags=readme,team_builder,core,meta-team,agent-first,self-narrative -->
<!-- [OMNI] material_id="material:services._core.team_builder.readme.self_narrative.md"-->

# team_builder · Team of Teams (元 Team)

> **agent-first meta team** — 输入自然语言需求, 产出合规 L3.5 Team 包. 11 阶段工作流 (V1 草图 V2 深化 V3 代码生成 + 注册). 2026-04-23 由 workflow_factory 改名而来, agent-first 启动期, Diamond 归档作参考.

## 这是什么

team_builder 是 omnicompany 的**元 Team** — **产出其他 Team 包的 Team**. 用户给自然语言需求 (例 "我要给 demogame 加一个赛季手册自动生成 Team"), team_builder 跑 11 阶段 agent-first 工作流, 产出 L3.5 合规的 Team 包 (DESIGN.md 七节 + Workers + Materials + Workspace + 契约审计 + 代码 + 注册).

跟其他元 service 的边界:
- **team_builder** 产 Team 包 (业务 Team)
- **omnicompany** 提供 Worker/Material/Team **基类** (而不是产 Team 包)
- **docauthor** 产 manifest/DESIGN **草稿** (而不是完整 Team 包)
- **workflow_factory** 是本 service 的 deprecated shim (改名前路径)

agent-first 哲学 (`docs/standards/concepts/agent_first.md`): **先搭完整 workspace** (信息库, 宁滥毋缺) → agent 探针 → 观测建档 → 按需提炼固化. **不预设理想管线** 让 agent 照走.

## 解决什么 / 不解决什么

**解决**: 自然语言需求 → 合规 Team 包 (含 7 节 DESIGN / Workers / Materials / 契约审计 / 代码生成 / 注册).

**不解决**: 业务正确性 (各 Team 自己负责); Worker 内部业务逻辑 (基础架构搭好后人工或专业 agent 填业务); 跨 Team 协调 (走 MaterialDispatcher 不在本 service).

## 设计目的与最终目标

**设计目的**: 让 omnicompany 自己能造新 Team — 不靠 AI IDE 手工建目录写文件, 让一个 meta agent 跑这事. 跟 [batch_work_use_omnicompany_agent](../../../../) memory 对齐.

**最终目标** (当下能认知的):
- V2 完成 Phase 2/4/5/6/7 (7 新 worker)
- V3 对接 Phase 8 CodeGenerator + Phase 10 Registrar
- 歧义回当前 agent 对话: intent.ambiguities → needs_user_input
- 接 self_repair (A4): design_validation_report FAIL → core_diagnose
- 多轮探针观测后, 把稳定流程固化成 HARD worker (按 agent-first Step 4)

## 规划

- **当前 V3 agent-first 启动期** (active, 2026-04-23)
- **下一步**: agent worker 阶段图设计 (几阶段未定, 等探针跑起来观测)
- **远景**: Phase 8 CodeGenerator + Phase 10 Registrar 完整闭环

## 构成

- 入口 → [run.py](run.py) (CLI `--text` 入口)
- 11 阶段 Worker (V1 部分实现, V2/V3 待) → [workers/](workers/)
  - Phase 0 OriginRequestLoader / Phase 1 IntentAnalyzer / Phase 1' ReferenceScout (V1 ✅)
  - Phase 2 ScaleAssessor + DecompositionPlanner (V2 ⏳)
  - Phase 3 TeamArchitect (V1 ✅, 草图深度)
  - Phase 4 WorkerDesigner × N + 4' MaterialDesigner × M + 5 WorkspaceDesigner + 6 ContractAuditor + 7 DesignValidator (V2 ⏳)
  - Phase 8 CodeGeneratorLoop (AgentNodeLoop, V3 ⏳)
  - Phase 9 Doctor 三套自检 (既有 L3 组件, ✅)
  - Phase 10 Registrar (V3 ⏳)
- Materials (V1 9 类 → V2 16 类) → [formats.py](formats.py)
- workflow 详述 → .omni/build_workflow.md
- workspace → .omni/workspace.yaml
- 旧 workflow_factory 实现 → [routers_legacy.py](routers_legacy.py) (3076 行 Diamond 实现, 2026-07-26 迁出 _archive; 用户明示**不拆**, 作回退路径 + 观测对照组)
- compat shim → [../workflow_factory/](../workflow_factory/) (deprecated, import 路径兼容)

## 想了解更多

- [DESIGN.md](DESIGN.md) (含 11 阶段表 + V1/V2/V3 状态)
- [SKILL.md](SKILL.md)
- agent-first 哲学 → [docs/standards/concepts/agent_first.md](../../../../../../docs/standards/concepts/agent_first.md)
- workflow_factory shim → [../workflow_factory/README.md](../workflow_factory/README.md)
- omnicompany 基类 → [../omnicompany/README.md](../omnicompany/README.md)
- 项目根 → [../../../../../README.md](../../../../../../README.md)

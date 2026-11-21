<!-- [OMNI] origin=claude-code domain=domains/software_engineering/implement ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.implement.design_doc.architecture.py" -->

# implement · 设计文档

## 状态
- **版本**: V1 (初始实现版 · 路由分发/团队拓扑/语义格式定型)
- **成熟度**: active
- **下一步**: 将 `ContextJudgeRouter` 的上下文充分性判定从启发式阈值迁移至 LLM Agent 评估 (见局限 1)；对接 `verify` 域的自动测试注入管线。

## 核心目的
本包负责软件工程中**“实现阶段”（代码编写 + 本地初步验证）的独立编排与分发**。接收上游规划域产出的实施任务与项目快照，通过上下文收集回路逐步读取关键源码，触发 LLM 实施器生成变更集，最终输出标准化报告。
**不解决**：需求分析/架构设计（属 `plan`/`design` 域）、运行期调试/修复（属 `debugger`/`tdd` 域）、最终验收测试（属 `verify`/`equiv_test` 域）。

## 核心接口
- **`team.py`**: `build_team() -> TeamSpec` — 定义 5 节点 DAG 及上下文回路拓扑 ([team.py](team.py))
- **`run.py`**: `build_bindings(input_dict: dict | None = None) -> dict[str, Router]` — 构建节点名到 Router 实例的运行时绑定 ([run.py](run.py))
- **`routers.py`**:
  - `ReqParserRouter` — 需求解析与格式校验 ([routers.py](routers.py))
  - `CodebaseScannerRouter` — 代码库扫描与批次生成 ([routers.py](routers.py))
  - `ContextJudgeRouter` — 上下文充分性判定与回路控制 ([routers.py](routers.py))
  - `ImplementorRouter` — LLM 驱动的代码变更生成 ([routers.py](routers.py))
  - `ReportEmitterRouter` — 实施报告组装与落盘 ([routers.py](routers.py))
- **`formats.py`**: `DOMAIN = "sw_implement"` + `FORMATS` 列表 — 定义 `sw_implement.{task, snapshot, context-state, change-set, report}` 语义格式 ([formats.py](formats.py))
- **`pipeline.py`**: `build_pipeline` — 兼容 shim，已重定向至 `team.py` ([pipeline.py](pipeline.py))

## 架构决策
### D1 · 采用 1-上下文收集回路 (Context Collection Loop) 替代单次全量扫描
**决策**: 实施管线不一次性加载全量代码库，而是通过 `codebase_scanner` 按批次输出文件，经 `context_judge` 评估是否充分 (`sufficient` 标记)。若 `PARTIAL` 则回路重新扫描下一批，若 `PASS` 则放行至 `implementor`。
**理由**: 大模型上下文窗口有限，全量扫描易导致关键逻辑被截断或上下文污染。回路设计确保按需、迭代式收集与实施高度相关的文件，兼顾精度与 Token 经济性。

### D2 · 节点职责严格分层：HARD 规则 / SOFT 判定 / DETERMINISTIC 输出
**决策**: 管线节点按确定性分级：`req_parser` 与 `codebase_scanner` 为 HARD (规则/正则驱动)；`context_judge` 为 SOFT (启发式阈值+可扩展)；`implementor` 为 SOFT/LLM；`report_emitter` 为 DETERMINISTIC。
**理由**: 明确划分规则与 AI 的边界，便于独立替换、调试与降级。HARD 节点保证输入输出结构合规，LLM 节点专注语义生成，确定性节点确保最终产物可追溯。

### D3 · 格式体系继承自 `_shared/common_formats`
**决策**: `sw_implement` 的 Format 定义 (`formats.py`) 严格继承 `sw.task-input`, `sw.project-snapshot`, `agent-state` 等共享基类，并通过截断/大小限制常量与 `_shared` 保持契约一致。
**理由**: 避免各 SE 域重复定义相同的数据契约。统一格式基类确保跨域管线 (`plan` → `implement` → `verify`) 的数据流转无需额外适配层。

## 数据流 / 拓扑
```
[上游 plan 域]
      │
      ▼ (sw_implement.task)
┌─────────────┐
│ req_parser  │ (HARD) 解析需求 → 提取范围/相关文件
└──────┬──────┘
       ▼ (sw_implement.snapshot)
┌─────────────────┐
│ codebase_scanner│ (HARD) 扫描目录 → 输出 file_batch
└──────┬──────────┘
       │                  ↑ (PARTIAL 回路)
       ▼                  │
┌──────────────┐──────────┘
│ context_judge│ (SOFT)  评估 sufficient?
└──────┬───────┘
       │ PASS
       ▼ (sw.change-set schema context)
┌──────────────┐
│ implementor  │ (SOFT/LLM) 生成补丁/代码
└──────┬───────┘
       ▼ (sw.change-set)
┌────────────────┐
│ report_emitter │ (DETERMINISTIC) 组装报告 → EMIT
└────────────────┘
       ▼
[下游 verify/tdd 域]
```

## 已知局限
- **`ContextJudgeRouter` 当前依赖启发式阈值判断上下文充分性**：仅通过已读文件数/字节数或关键字匹配触发 `PASS`，缺乏对“业务逻辑覆盖率”的语义理解，可能导致过早生成或无效循环。· 升级路径：将 `context_judge` 升级为 AgentNodeLoop 模式，引入轻量 LLM 评估器，基于任务需求比对当前上下文摘要，输出语义化的 `sufficient` 判定。
- **缺乏与测试框架的自动联动**：当前管线仅产出 `change-set`，未内置单元测试生成或等价测试脚手架注入逻辑，依赖下游域手动触发。· 升级路径：在 `report_emitter` 后增加可选分支节点 `test_scaffold_generator`，根据变更集自动匹配对应测试模板并输出至 `data/domains/software_engineering/implement/tests/`。
- **LLM `implementor` 的输出格式强依赖 prompt 工程**：变更集解析使用正则/JSON 提取，若模型输出格式漂移会导致管道崩溃。· 升级路径：引入 `omnicompany.protocol.anchor` 的 `VerdictKind.SCHEMA_VALIDATE` 强制校验层，或在 `implementor` 前挂载结构化输出约束中间件。

## 参考资料
- 关联域格式定义：`src/omnicompany/packages/domains/software_engineering/_shared/common_formats.py`
- 上游规划域：`src/omnicompany/packages/domains/software_engineering/plan/DESIGN.md`
- 下游验证域：`src/omnicompany/packages/domains/software_engineering/verify/DESIGN.md` / `tdd/DESIGN.md`
- 路由基础设施：`src/omnicompany/runtime/routing/router.py`
- 团队协议：`src/omnicompany/protocol/team.py`
- 规范文档：`docs/standards/distributed-docs.md` (§八 OMNI-034)
<!-- [OMNI] origin=claude-code domain=domains/software_engineering/debugger ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.debugger.design_specification.md" -->

# debugger · 设计文档

## 状态
- **版本**: V1 (2026-04-25 · 填充核心接口/架构决策/数据流，固化假设驱动调试拓扑)
- **成熟度**: active
- **下一步**: 将 Router 内部硬编码的 Prompt/工具绑定迁移至动态模型路由协议，接入真实多模型降级策略以完成端到端自动化调试管线验证。

## 核心目的
提供跨语言的“假设-证据-修正”循环调试工作流。将非线性的调试过程（读错→根因分析→生成假设→设计探测→执行验证→修复复测）结构化为声明式 DAG 管线，通过累积 `debug-context` 避免探索过程中的状态断裂与信息丢失。
本包**不解决**：不提供底层语言级单步执行/寄存器查看能力（属传统调试器职责）；不处理编译/构建系统的配置修复；不替代静态语法检查（仅消费其输出的 `ErrorReport`）；不负责最终补丁的 Git 提交操作（由兄弟包 implement 接管）。

## 核心接口
- **[formats.py](formats.py)** · `FORMATS: list[Format]` — 定义调试管线语义契约（`debug.error-report` → `debug.test-feedback` 等），贯穿循环的 `DOMAIN = "debug"`
- **[team.py](team.py)** · `build_team() -> TeamSpec` — 返回包含 9 个节点与明确反馈边的调试管线拓扑声明
- **[routers.py](routers.py)** · 10 个 Router 实现类：`ErrorAnalyzerRouter`, `ContextInitRouter`, `HypothesisGeneratorRouter`, `ProbeDesignerRouter`, `ProbeExecutorRouter`, `EvidenceCollectorRouter`, `FixerRouter`, `TesterRouter`, `RegressionAnalyzerRouter`, `RegressionToContextRouter`（均继承 `omnicompany.runtime.routing.router.Router`）
- **[routers.py](routers.py)** · `_empty_context() -> dict` — 初始化累积上下文结构（errors/hypotheses/patches/excluded_files）
- **[run.py](run.py)** · `build_bindings(input_dict: dict[str, Any] | None = None) -> dict[str, Router]` — 构建路由器名称到实例的映射字典，支持传入 `model` 动态注入 LLM 能力
- **[pipeline.py](pipeline.py)** · 兼容 Shim：`build_pipeline = build_team`（保留旧调用约定）

## 架构决策
### D1 · 非循环 DAG 显式建模调试反馈环，放弃隐式 `while` 控制流
**决策**: 调试管线拓扑通过 `TeamSpec` 声明式定义，严格区分 3 类确定性 Transformer、5 个 SOFT/LLM 语义节点与 2 个 HARD 执行节点。
**理由**: 调试本质是高度不确定的探索性循环。声明式拓扑使管线状态可观测、可编排，便于后续单独替换特定节点（如切换 probe 策略或更换验证器）而不破坏整体状态机流转逻辑。

### D2 · `debug-context` 作为跨节点累积上下文，而非纯消息链式传递
**决策**: 所有节点共享并读写同一个结构化的 `debug-context` 字典，而非依赖临时局部变量传递状态。
**理由**: 假设验证过程高度依赖历史假设与证据的回溯。纯消息传递会导致上下文碎片化，增加节点间耦合。集中式累积上下文保证 `evidence_collector` 能高效收敛所有分支信息，并支持失败回溯。

### D3 · Router 职责按“软/硬/确定性”严格隔离执行边界
**决策**: LLM 节点仅负责语义推理与假设生成；HARD 节点 (`ProbeExecutorRouter`, `TesterRouter`) 负责子进程执行与断言校验；Transformer 节点负责纯数据格式转换与路由分发。
**理由**: 混合职责会导致测试困难与重试逻辑混乱。隔离后，HARD 节点可独立集成 Mock 沙箱环境，SOFT 节点可独立进行 Prompt 调优与温度控制，确定性节点保障管线基础流转不依赖外部不稳定服务。

### D4 · Format 类型体系显式标注循环依赖与状态标签
**决策**: `formats.py` 中的 `Format` 定义通过 `parent` 字段与 `tags`（如 `analyzed`, `hypothesized`）建立父子与循环关系，而非扁平列表。
**理由**: 对齐调试工作流的非本质线性特征。格式契约强制要求产出物附带循环所需的状态标记，防止下游节点误处理过期数据或跳过必要的验证阶段，提升管线容错率。

## 数据流 / 拓扑
```text
[ErrorInput] → error_analyzer (SOFT) → 产出 ErrorAnalysis
                      ↓
                 context_init (TRANS) → 初始化 debug-context (清空旧假设/记录原始错误)
                      ↓
           hypothesis_generator (SOFT) ───────────────→ probe_designer (SOFT)
                      ↑                                    ↓
         evidence_collector (TRANS)              probe_executor (HARD)
               ↑         ↑                            ↙       ↓
               │         │                       (证否)    (证实)
               │         │                                ↓
               │  regression_analyzer (SOFT) ←─────── fixer (SOFT)
               │             ↑                           ↓
               └─────── tester (HARD) ← (PASS / FAIL) ───┘
                          ↓
                   (PASS) → 输出 verified-fix 并终止管线
                   (FAIL) → 回归至 evidence_collector 累积新证据与失败模式，更新 hypothesis 状态后重入生成循环
evidence_collector 是所有回路的归一点，负责去重、过滤无关文件并收敛上下文。
```

## 已知局限
- **Router 内部 Prompt 与工具绑定缺乏动态路由能力**：当前 `build_bindings` 仅接收基础 `model` 字符串，SOFT 节点内的 Prompt 模板与工具调用逻辑硬编码在类内部，不支持多模型降级或任务级热切换。 · **升级路径**: 引入 `omnicompany.runtime` 的模型路由协议，将 `model` 字段扩展为路由策略对象；在 SOFT 节点中注入配置化的 Prompt 模板注册表，实现基于任务类型与失败率的自动降级切换。
- **`debug-context` 为纯内存结构，长链路调试易丢失状态快照**：当前管线运行在进程内存中，复杂多文件修复或超长探测循环可能导致中间状态无法持久化，不利于断点续调或人工审计介入。 · **升级路径**: 为 `debug-context` 引入版本化快照机制（如 Git commit 或独立 JSONL 落盘），在 `EvidenceCollectorRouter` 中增加 `checkpoint()` 接口，支持从指定快照恢复管线状态，并与 `.omni/` 清单对齐落盘策略。

## 参考资料
- 源码实现: [formats.py](formats.py) · [routers.py](routers.py) · [team.py](team.py) · [run.py](run.py) · [pipeline.py](pipeline.py)
- 规范对齐: docs/standards/distributed-docs.md (DESIGN.md 七节结构与包边界)
- 兄弟包边界: [../implement/DESIGN.md](../implement/DESIGN.md) (本包负责调试定位/假设验证/修复草案生成；implement 负责最终代码注入与补丁应用)
- 运行时基座: [../../../../runtime/routing/router.py](../../../../runtime/routing/router.py) (Router 继承契约与路由基类)
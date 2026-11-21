<!-- [OMNI] origin=claude-code domain=runtime/signals ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.signals.design_specification.documentation.md" -->

# signals · 设计文档

## 状态
- **版本**: V1 (2026-04-25 从 skeleton 升级：依据源码填充核心接口/决策/数据流，明确 legacy 模块迁移边界)
- **成熟度**: active
- **下一步**: 将 `pain_system.py` 历史 import 彻底剥离至 `semantic_router.py` 信号总线；统一 `RewardSignal` 权重结构暴露给元进化管线动态注入。

## 核心目的
本包提供 Agent 运行时所需的**内省与反馈信号原语**，构建“管线执行”与“动力演化”的耦合层。
解决：
- 提供六维综合奖励计算（`reward.py`），指导元进化与路由权重调整。
- 提供系统自我认知生成与缓存（`mirror_node.py`），支撑 `self_awareness_score` 与 AST 自省。
- 提供 Agent 陷入循环/无效推理的检测（`stuck.py`），触发软中断与痛觉前置信号。
- 注册系统内部状态（路由图/AST/自认知）为一等公民 `Format`（`self_types.py`），实现代码即数据同构。
不解决：
- 不直接执行痛觉反向传播（已迁移至 `semantic_router.py`，本包保留历史类为兼容占位）。
- 不管理业务域信号（如 `absorption` 或 `demogame` 特定指标），仅处理引擎级通用信号。
- 不替代 `EventBus` 本身，而是定义其承载的 payload schema 与无状态计算逻辑。

## 核心接口
- **`RewardSignal`** ([reward.py](reward.py)): 七维奖励信号数据类，内置权重常量（`W_TOKEN`, `W_SEMANTIC` 等），提供标量归一化输出，同时作为进化方向指引。
- **`MirrorNode`** ([mirror_node.py](mirror_node.py)): 自我认知引擎。核心方法 `scan_src()`, `get_current_concept() -> str`, `invalidate() -> None`。按关键模块源码哈希缓存，避免重复调用 LLM。
- **`StuckDetector`** ([stuck.py](stuck.py)): 循环检测器。核心方法 `analyze_loop(trace_history) -> StuckAnalysis | None`。支持意图语义指纹匹配、硬循环保底、独白循环三类模式。
- **`SYSTEM_FORMATS`** ([self_types.py](self_types.py)): `list[Format]`，向 `FormatRegistry` 注册 `omnicompany.json.route_graph_dump`, `omnicompany.python.module_ast`, `omnicompany.markdown.self_concept` 等系统级格式。
- **(已废弃)** `PainEvent` / `PainClassifier` / `PainPropagator` ([pain_system.py](pain_system.py)): 标记 `DEPRECATED`，值恒为 0.0 且不参与生产计算，实际逻辑已下沉至 `runtime/semantic_router.py`。

## 架构决策
### D1 · 动力与管线解耦（标量信号不干预执行流）
**决策**: 信号原语（Reward/Pain/Stuck）仅产出结构化标量或枚举，不直接修改路由表、不阻塞 LLM 调用。路由决策交由 `semantic_router.py` 消费。
**理由**: 遵循“管线定义数据流转，动力定义流转原因”的架构原则。保持信号层纯粹性，便于独立测试、插拔元进化算法或替换评估器。
### D2 · 自我认知按源码哈希缓存
**决策**: `MirrorNode` 不每次运行都调用 LLM，而是计算关键模块 AST 的哈希，命中缓存则直接返回序列化文档。
**理由**: LLM 生成自认知成本高且源码不变时内容必然相同。哈希缓存使 `self_awareness_score` 在稳定期开销为 0，仅在代码演进或热重载时刷新，符合冷启动资源约束。
### D3 · 循环检测采用启发式指纹而非硬编码步数
**决策**: `StuckDetector` 使用停用词过滤后的意图语义指纹匹配连续意图，配合宽松的工具调用完全一致检测作为保底。
**理由**: 纯步数阈值易误杀长推理链；语义指纹能精准识别“原地打转但话术微调”的 LLM 幻觉模式，降低假阳性中断，同时豁免正常重试与高频合法工具调用。
### D4 · 系统内部产物一等公民化（代码即数据）
**决策**: 通过 `self_types.py` 将路由图 JSON 快照、模块 AST、算子源码、自认知 Markdown 注册为 `Format`，复用全局 `FormatRegistry` 机制。
**理由**: 消除“元数据”与“业务数据”的割裂。使得系统的自省输出可直接参与 `Crystallizer` 管线或 Guardian 审计，无需特殊序列化或路径硬编码。
### D5 · 痛觉系统显式废弃与平滑降级
**决策**: `pain_system.py` 标记 `DEPRECATED` 并冻结所有值为 `0.0`，实际传播逻辑迁移至 `record_outcome()`。不直接删除旧文件以保留 import 兼容性。
**理由**: 历史版本与旧 `route_graph.db` 强耦合。直接删除会引发大量 `ImportError`。冻结降级确保旧调用不崩溃，同时通过模块级 Docstring 警告引导迁移。
### D6 · 奖励权重硬编码冷启动，预留元进化挂钩
**决策**: `RewardSignal` 的权重常量当前为手写经验值，但类设计暴露结构化字典，未来可由元进化模块动态覆盖。
**理由**: 冷启动阶段缺乏真实交互数据训练权重模型。保留结构一致性确保未来接入 `evolve_signal.py` 时无需重构 Reward 计算管线，符合渐进演进策略。

## 数据流 / 拓扑
```
[Agent Loop Trace] ──┐
                     ↓
              ┌─ StuckDetector ──(loop_detected?)──┐
              │                                    ↓
[Source Code]─┤                          Pain/Interruption Signal
              │                                    │
              └─ MirrorNode ───(hash check/cache)──┼──► self_awareness_score
                               ↓                   │
                     omnicompany.markdown.self_concept
                                                    ↓
[Pipeline Metrics] ───► RewardSignal.compute(total_tokens, time, errors, pain_delta, awareness_score)
                                                    ↓
                                         Scalar Reward Vector [0.0 ~ 1.0]×7
                                                    ↓
                                       [Semantic Router / Evolver / Event Bus]
```
核心流向：运行时产生 Trace/指标 → 信号层无状态计算 → 产出标量奖励/中断信号 → 总线转发至路由权重更新或元进化模块。`SYSTEM_FORMATS` 在启动期一次性注册至全局注册表，不参与运行时热流。

## 已知局限
- **痛觉模块历史债务未彻底清理**：`pain_system.py` 仍存在于代码树中，虽已冻结但占用 import 心智模型。· 升级路径: 在 `runner.py` 全面验证 `record_outcome()` 稳定后，移除旧模块，将 `PainEvent` schema 迁移至 `protocol/` 层统一管理。
- **奖励权重缺乏动态反馈调优**：当前 `W_*` 为经验常量，未与历史任务成功率做贝叶斯优化或强化学习对齐。· 升级路径: 接入 `agent_crystallize/` 的离线评估管线，收集任务终态数据，使用网格搜索/进化算法拟合最优权重，并支持运行时热更新。
- **Stuck 检测未集成上下文衰减**：当前仅检测最近 N 轮，不随对话长度动态放宽阈值，长上下文任务可能被误判为循环。· 升级路径: 引入滑动窗口衰减因子（`decay_window`），结合 `semantic_richness` 指标做动态阈值判定。

## 接收意愿
- **接收**: 新增信号原语（如 `novelty_score`, `constraint_violation_rate`）需严格遵循 `dataclass` + 标量输出范式；提供自定义 `StuckDetector` 指纹提取算法的实现。
- **不接收**: 业务域特定指标（如某 `domain` 的 ROI、用户满意度）混入本包；直接修改路由权重或调用 LLM 的逻辑（违反动力/管线解耦原则）。
- **边界信号**: 若新增类包含 `self.db.save()` / `self.llm.generate()` / 依赖特定 `domain` 配置，说明已越界进入 `runtime/exec` 或业务服务层，应被拒绝。

## 参考资料
- 理论依据: `docs/theory/` (03§动力层、终点§自认知架构)
- 痛觉迁移: `src/omnicompany/runtime/semantic_router.py` (`record_outcome`, `pain_score` 计算)
- 进化管线: `src/omnicompany/runtime/agent_crystallize/DESIGN.md` (Reward 消费端)
- 格式注册: `src/omnicompany/protocol/format.py` (`FormatRegistry` 基类)
- 关联运行时: `src/omnicompany/runtime/exec/DESIGN.md`, `src/omnicompany/runtime/routing/DESIGN.md`
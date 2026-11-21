<!-- [OMNI] origin=claude-code domain=runtime/agent_crystallize ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:runtime.agent_crystallize.package_design.document.md" -->

# runtime/agent_crystallize · 设计文档

## 状态
- **版本**: V1（M3 信息充分性四层中的第 4 层）
- **成熟度**: active
- **下一步**: "agent 自建 skill" 对标 hermes skill_manager（目前只到 SpecPatch 人审，未到 skill 自动落盘可调用）

## 核心目的

`runtime/agent_crystallize/` 是 Agent 经验沉淀机制。回答的问题：

> **Agent 循环里发生的事（调了什么工具 / 读了什么文件 / 得出什么结论），能不能自动提炼为下次可复用的改进提案？**

运行时捕捉 agent trace → 生成 SpecPatch 候选 → 落 pending 目录 → 人审批后（未来自动）应用到 Router 规范（DESCRIPTION / FORMAT_IN / components）。

本目录**不**解决的问题：
- 不改 Agent 本身（Agent 在 `runtime/agent/`）
- 不自动修改源码（只产 patch，等人审）
- 不存储跨会话记忆（那是未来 memory provider 的工作）

## 核心接口

- **`AgentLoopTrace`** — Agent 运行的结构化快照 — [protocol.py](protocol.py)
- **`build_agent_loop_trace(loop, *, node_id, format_in, format_out, description, input_data)`** — 从 finished AgentNodeLoop 实例提取 trace — [trace.py](trace.py)
- **`ExperienceCrystallizer`** — 插件协议（observe + propose 两阶段）— [protocol.py](protocol.py)
- **`TraceSummarizer`** — 统计型 crystallizer（工具使用分布 / 访问的外部节点 / 重复模式）— [summarizer.py](summarizer.py)
- **`FormatEdgeInferrer`** — 推断 Format components 增补（Agent 跨节点访问 → Format 应声明 composite）— [format_edge_inferrer.py](format_edge_inferrer.py)
- **`DescriptionRefiner`** — LLM-based DESCRIPTION 精化器 + self-judge 门禁 — [description_refiner.py](description_refiner.py)
- **`patch_self_judge.run_patch_self_judge(patch, ...)`** — SpecPatch 自判断门禁（工具白名单 + 阈值）— [patch_self_judge.py](patch_self_judge.py)
- **`SpecPatch`** — 提议的规范变更候选 — [protocol.py](protocol.py)
- **`pending_queue.write_pending_patch(patch)`** — 落盘到 pending — [pending_queue.py](pending_queue.py)
- **`trace_accumulator.increment_trace_count(pipeline_id, node_id)`** — N≥3 自动开启驱动 — [trace_accumulator.py](trace_accumulator.py)

## 架构决策

### D1 — 插件化 crystallizer

`ExperienceCrystallizer` Protocol 有 `.observe(trace) → Observation` + `.propose(obs, downstream_eval) → list[SpecPatch]` 两个方法。

为什么插件化：
- 不同 crystallizer 关注不同信号（trace summarizer 看工具分布 / format inferrer 看访问模式 / description refiner 用 LLM）
- 各自独立开关（env var `OMNICOMPANY_CRYSTALLIZE=trace,format,description`）
- 未来可加新类型（ToolManualAppender / PatternExtractor 等），不改核心

_验证来源: [code] `src/omnicompany/runtime/agent_crystallize/__init__.py` ExperienceCrystallizer Protocol + env var 分派_

### D2 — N≥3 自动开启

默认行为：agent loop 跑 1-2 次只记录 TraceSummarizer（不产 patch），≥3 次自动开全套（含 DescriptionRefiner LLM 调用）。

理由：
- 单次运行噪音大（同一 repo 不同跑法可能提不同 patch）
- ≥3 次才自动开，确保信号稳定
- env var 可显式覆盖（测试 / 调优用）

数据落盘：`data/crystallize/trace_counts.json`（`{pipeline_id}:{node_id}` → count）。

_验证来源: [experiment] Exp B crystallize（`docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md` §B）+ [code] `runtime/exec/runner.py` 触发点_

### D3 — SpecPatch 走 pending queue 人审，绝不自动改源码

```
crystallize 产出 SpecPatch
    ↓ write_pending_patch
data/crystallize/pending/<target_router>/<patch_id>_<crystallizer>.md
    ↓ (等人审)
  人 approve → 手动应用到 Router / Format
  人 reject → 移到 rejected/
```

理由：crystallize 是启发式，未经人审的 patch 直接改源码会污染规范。人审成本低（读 md 勾选）。

_验证来源: [归纳] 从 2026-04 提案管线设计经验得出 — 人审闸门在风险性改动上必设_

### D4 — DescriptionRefiner 有 self-judge 门禁

DescriptionRefiner 产出一个 DESCRIPTION 改进候选后，立即调用 `run_patch_self_judge`：
- 工具白名单检查（proposed 文本里提到的 tool 必须在 actual_tools 里，否则是幻觉）
- 阈值收紧（score ≥ 0.65 + verdict=approve 才通过；borderline 不通过）

Exp F 实验：纯 LLM 自判断准确率 40%；加 white-list + threshold 升到 100%。

_验证来源: [experiment] `data/domains/absorption/scratch/run_exp_f_self_judge.py`（P5 web_search 幻觉修复 5/5 准确）_

### D5 — trace 从 loop._messages 读，不拦截实时事件

Agent loop 跑的时候，runtime/agent/agent_node_loop.py 在每轮末尾 `self._messages = messages`。loop 结束后，`build_agent_loop_trace(loop)` 从这里读完整消息历史。

不用实时事件流拦截，理由：
- 简单（无订阅机制）
- loop 结束后再处理，不影响运行时性能
- 消息历史完整（含工具调用 + 结果 + 压缩痕迹）

_验证来源: [code] `src/omnicompany/runtime/agent/agent_node_loop.py::self._messages` 赋值点 + `build_agent_loop_trace` 消费点_

### D6 — outer_router_class 回传（避免 patch target 指向 inner class）

很多 Router 实现模式是"外部 Router 类 + 内部 AgentNodeLoop 子类"。如 `ModuleExplorerRouter.run()` 构造内部 `_ExplorerLoop` 实例。

如果 crystallize 用 `type(loop).__name__`，target_router 会是 `_ExplorerLoop`（inner），不是 `ModuleExplorerRouter`（人审时不知道改哪）。

修：外部 Router 在调 `loop.run()` 前设 `loop._outer_router_class = type(self).__name__`。crystallize 读这个字段。

_验证来源: [code] ModuleExplorerRouter / LearningExtractorRouter 等多处 `_outer_router_class` 赋值实现_

## 数据流 / 拓扑

```
[Agent loop 运行完毕]
    runner._execute_single_node()
      ├── await router.run(input_data) 完成
      ├── 识别这是 AgentNodeLoop 或其外层 wrapper
      ├── trace_accumulator.increment_trace_count(pipeline_id, node_id) → count
      ├── crystallizers = []
      │   - env var 显式 → env 指定的
      │   - 否则 count ≥ 3 → 全套 [Trace, FormatEdge, Description]
      │   - 否则 count ≥ 1 → [Trace] 只记录
      │   - 否则 []
      │
      └── if crystallizers:
           trace = build_agent_loop_trace(loop, ...)
           patches = run_crystallize(crystallizers, trace, downstream_eval)
             ├── 对每个 cz: obs = cz.observe(trace)
             └── 对每个 cz: patches += cz.propose(obs, downstream_eval)
                   [DescriptionRefiner 内嵌 self_judge 门禁]
           for p in patches: write_pending_patch(p)
           
[人审环节（手动）]
    data/crystallize/pending/<router>/<patch_id>_<cz>.md
       ↓ 人读 + 决定
    批准 → 手动改 Router 源码 + 归档（_applied/）
    拒绝 → 移 _rejected/（留原因）
```

## 已知局限

1. **Format 层 patch 未实现 auto-apply** — FormatEdgeInferrer 产出 `format_components_add` 类型 patch，但目前只是 markdown 提案。未来要：auto-generate Format 定义片段 + FormatRegistry 注册新 composite。

2. **downstream_eval 目前只传 node verdict** — 理想应该传"下游节点是否因这个修改受益"（需要 re-run）。目前无此能力。

3. **未接入 gap 档案的自动写入** — crystallize 产的 patch 如果是"OmniCompany 当前不知道的新能力维度"（orphan），应该写到 `docs/gaps/` 新建 G<N>。目前只写 pending/。

4. **DescriptionRefiner 的 LLM 成本** — 每次 trigger 调一次 LLM（生成候选）+ 再调一次（self judge），有成本。N≥3 阈值缓解但不消除。

5. **TraceSummarizer / FormatEdgeInferrer 是纯规则启发式** — 对"重复访问同目录"等模式的识别偏严格。Exp B 实测 FormatEdgeInferrer 产出率较低（仅 hot_path_prefixes 匹配），可能漏掉微妙信号。

## 参考资料

- 关联 agent：[runtime/agent/DESIGN.md](../agent/DESIGN.md)（trace 来源）
- 关联 info_audit：[runtime/info_audit/DESIGN.md](../info_audit/DESIGN.md)（crystallize 是第 4 层）
- 关联 plan：`docs/plans/[2026-04-14]INFO-SUFFICIENCY/FOUR_TIER_PLAN.md`（M3 设计）
- 关联 plan：`docs/plans/[2026-04-14]INFO-SUFFICIENCY/EXPERIMENT_REPORT.md`（Exp B/F 实验结果）
- 关联 gap：docs/gaps/G2_learning_distill.md

## 接收意愿

- **welcome_themes**:
  - 经验蒸馏新范式（从 agent trace 抽取更高阶的模式，超出 TraceSummarizer 统计维度）
  - trace 压缩 / 结构化理论（精炼 trace 到更小可推理单位）
  - 可执行 skill 沉淀（对标 hermes skill_manager：自建 skill 进可调用库）
  - 轨迹 ShareGPT-like / 标准序列化格式（便于跨 agent / 跨会话复用）
  - downstream_eval 新判据（agent 修改是否真正让下游受益）
  - crystallize 与 gap 档案自动联动（orphan patch → 自动建 G<N>）
  - FormatEdgeInferrer / DescriptionRefiner 之外的第 N 类 crystallizer
- **hard_constraints**:
  - 必须走 pending queue 人审（不直接写源码 / 不自动改 DESCRIPTION/FORMAT_IN）
  - 不得绕过 patch_self_judge 门禁
  - 所有 crystallizer 必须实现 ExperienceCrystallizer Protocol（observe + propose 两阶段）
- **soft_preferences**:
  - 偏好增量蒸馏（累积多次 trace 后出 patch）而非单次激进改动
  - 偏好能 replay / 可验证的 SpecPatch（带 rationale + 证据链）
- **maturity_preference**: `stable_only`（经验沉淀影响 Router 规范，需成熟机制）

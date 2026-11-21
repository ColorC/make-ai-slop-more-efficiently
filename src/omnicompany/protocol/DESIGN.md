<!-- [OMNI] origin=claude-code domain=protocol ts=2026-04-17T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:protocol.layer_design.documentation.md" -->

# protocol · 设计文档

## 状态
- **版本**: V1.3
- **成熟度**: active
- **下一步**: Format composite/parent 关系的运行时检查已实现，长期方向是 Format Schema 强化（让 agent 能从 FORMAT_IN 字符串字面量反查结构）

## 核心目的

`protocol/` 是 OmniCompany 的**契约层**——定义系统里所有参与者（Router / Anchor / Transformer / Format / Pipeline）的**形状与协议**，**不包含任何执行逻辑**。

这里的每个类都是数据声明（Pydantic BaseModel 或 dataclass），是 runtime/ 消费的类型定义。改 protocol/ = 改契约 = 可能打破一切实现，所以变动要保守。

它解决的问题：
- 让管线节点之间的**类型关系**可机读（Format ID 字符串 + parent/components 引用）
- 让 Router 的**职责范围**可声明（FORMAT_IN/OUT + DESCRIPTION）
- 让管线的**拓扑**可序列化（PipelineSpec JSON/YAML 往返）
- 让**判定结果**统一可路由（Verdict.kind → Route.action）

它不解决的问题：
- 不执行任何 Router/Transformer/Pipeline（execution 在 runtime/exec/ 和 runtime/routing/）
- 不做类型检查的 runtime 部分（FormatRegistry 验证 component 存在性在 runtime 层）
- 不定义节点间数据流的具体拼接（runner 的 `_merge_inputs` 等）

## 核心接口

LAP 六元原语 + Format 类型系统：

- **`Verdict`** — 节点输出判定（`kind: VerdictKind` + `output: Any` + `info_audit?`）— [anchor.py](anchor.py)
- **`Route`** — Verdict 出来后怎么走（`action: RouteAction` + `target?`）— [anchor.py](anchor.py)
- **`ValidatorSpec`** — 判定器声明（`kind: HARD|SOFT`）— [anchor.py](anchor.py)
- **`AnchorSpec`** — 锚点 = format_in → validator → format_out + 路由表 — [anchor.py](anchor.py)
- **`TransformerSpec`** — 类型转换器（LLM / RULE / HYBRID）— [anchor.py](anchor.py)
- **`Format`** — 语义类型（id + description + parent + components + required_tags）— [format.py](format.py)
- **`FormatRegistry`** — 全局 Format 注册表（含 parent/component 循环检测）— [format.py](format.py)
- **`PipelineSpec`** / **`PipelineNode`** / **`PipelineEdge`** — 管线拓扑声明 — [pipeline.py](pipeline.py)
- **`InfoAuditReport`** — 信息充分性报告（probe/piggyback/post_hoc 产出的统一结构）— [info_audit.py](info_audit.py)
- **`FactoryEvent`** — 事件总线统一事件模型 — [events.py](events.py)

## 架构决策

### D1 — 契约层必须是纯数据，不许夹逻辑

协议类**只描述"是什么"**，不包含"怎么做"。所有执行逻辑（Router.run / runner / bus）都在 runtime/ 和 packages/。

理由：
- 契约独立于实现 → 可以有多种 runtime（async/sync、本地/远程）
- 契约可被第三方消费（如 Doctor 读 Format/Router 声明做诊断）
- 改 runtime 不影响契约，改契约必慎重

反例：如果 `Format.validate()` 在 Format 里直接跑正则，那就违反本原则——验证逻辑应在 `FormatRegistry.register()` 或 runtime 的 CompositeFormatCheckRouter 里。

_验证来源: [归纳] 从 LAP 协议演化归纳；[code] `protocol/anchor.py` + `format.py` 类定义均为纯 Pydantic / dataclass_

### D2 — Format 有双重关系：parent（is-a）+ components（has-a）

Format 不只是字符串 ID，有两种类型关系：

- **parent** — 语义继承。`Code <: Spec` 意味 `Code` 满足 `Spec` 能去的地方。单向链。
- **components** — 结构组合。`absorption.proposal.context` 由 `[report.v3, learning]` 共同构成。多路汇聚。

两者合用：
- 单上游节点，`format_in=str`，简单路径
- fan-in 多上游节点，`format_in=str(composite)` 或 `format_in=list[str]`

Runtime 侧 (`runner._merge_inputs`) 在 composite Format 时用 component ID 作 key，而非不透明的 `_from_{src_id}`。

_验证来源: [code] `protocol/format.py::Format.parent` + `Format.components` 字段 + `FormatRegistry` 循环检测_

### D3 — AnchorSpec 自带路由表，不外置

为什么 Route 在 AnchorSpec 里而不是 PipelineEdge：
- 节点的判定结果（PASS/FAIL/PARTIAL/RETRY/EMIT/HALT）对路由的影响是节点内属性，不是拓扑属性
- PipelineEdge 只管"上下游连接"，不管"根据 verdict 选哪条路"
- 好处：节点可独立测试（脱离管线也知道 FAIL 该 HALT 还是 RETRY）

_验证来源: [归纳] 早期 LAP 设计取舍的归纳；[code] `protocol/anchor.py::AnchorSpec.routes` 字段_

### D4 — Verdict.kind 的枚举有限，不允许业务自造

`VerdictKind = {PASS, FAIL, PARTIAL, RETRY, EMIT, HALT}` 固定六元。

理由：路由表 keyed by VerdictKind，若业务自造新 kind，runner 不知道怎么处理。如果某个业务需要"PASS_WITH_WARNING" 这种语义，建议用 `Verdict.kind=PASS + Verdict.info_audit=...`，不是扩枚举。

_验证来源: [code] `protocol/anchor.py::VerdictKind` 枚举定义（六元封闭）_

### D5 — InfoAuditReport 跨 protocol 边界

信息充分性审计是四层机制（probe/piggyback/post_hoc/crystallize）的统一产出。报告结构在 protocol/ 定义，确保：
- LLMClient 的 piggyback 和 probe 用同一 schema
- runner 的 post_hoc 产出同一 schema
- Verdict.info_audit 能承载同一 schema
- agent_crystallize 消费同一 schema

修这个结构要极慎重（跨层依赖）。

_验证来源: [code] `protocol/info_audit.py::InfoAuditReport`（被 llm / runner / crystallize 共享消费）_

### D6 — Registry 和 Format 查询统一从 FormatRegistry 去

全局单例 `FormatRegistry.instance()`。注册时检查：
- parent 存在（循环引用报错）
- components 存在（编译期保证 composite 可解析）
- 文档（description）非空

查询时优先本进程注册表，再到"就近" `.omni/manifest.yaml` 兜底。

_验证来源: [code] `protocol/format.py::FormatRegistry.instance()` + `register()` 循环检测实现_

## 数据流 / 拓扑

```
声明期：
   Router 类              (Python class with FORMAT_IN/OUT/DESCRIPTION)
       ↓ 读字段组装
   AnchorSpec            (声明这个节点接什么输出什么)
       ↓ 聚合
   PipelineSpec          (若干 AnchorSpec + edges + entry)
       ↓ register_all()
   core/registry.py      (PipelineEntry 可被 dispatch 查到)

运行期：
   dispatch(pipeline_id, input)
       ↓
   PipelineRunner(spec, bindings={router_id: RouterInstance}, bus)
       ↓ runner.run()
   每节点 Router.run(input_data) → Verdict
       ↓
   Route.action 驱动下一步（NEXT / EMIT / HALT / JUMP / RETRY）
       ↓
   FactoryEvent 写到 SQLiteBus
```

InfoAudit 侧流：

```
LLMClient.call(info_audit=True)
  ├── piggyback tool 注入 → 响应里含 tool_use(info_audit)
  ├── parse → InfoAuditReport
  └── 填到 result.info_audit 属性
       ↓
runner 读取 → verdict.info_audit
       ↓
self.node_audit_reports[node_id] = report
       ↓
post_hoc 可在 ia is None 时触发独立 probe 补审计
```

## 已知局限

1. **Format 描述仍是自由文本** — description 字段是字符串，未强制 schema（"每个 G 必须有 X Y Z"）。probe 能扫出"描述不充分"，但无法阻止"写了但写得模糊"。升级路径：Format description 加结构化字段（参考 DSPy Signature）。

2. **Router 的 FORMAT_IN 只是字符串字面量** — 无法从字符串反查到具体的 Format 实例做类型检查。目前是"约定"级别，Doctor 有检测但不是编译期错误。升级路径：Format 体系从字符串 ID 升级为"带 schema 的类型对象"，Router 能做严格匹配。

3. **Verdict.output 是 Any** — 没有类型约束，下游节点能看到什么全靠上游节点自律。Format 声明了预期 structure 但运行时不验证。升级路径：Format 体系加 Pydantic schema，Verdict.output 按 format_out 的 schema 校验。

4. **InfoAuditReport 的多模式融合尚不统一** — piggyback/post_hoc/probe 的 report 字段细节不一致（例如 piggyback 有 `should_exist_but_absent`，其他没有）。统一性弱。

## 参考资料

- 关联标准：docs/standards/material.md / router.md / pipeline.md
- runtime 对应：[runtime/exec/DESIGN.md](../runtime/exec/DESIGN.md)（PipelineRunner 消费这些 spec）
- InfoAudit 说明：[runtime/info_audit/DESIGN.md](../runtime/info_audit/DESIGN.md)
- 历史 plan：`docs/plans/` 下多个 Format composition / Router audit 相关计划

## 接收意愿

protocol/ 是**架构契约的类型定义层** (Format / Router / Pipeline / Verdict / Anchor / Material / Worker / Team 等). 对外接收意愿:

- **接收**: 新的**架构级协议类型**提案 (如新的 SubPipeline 类型、新的 Route 动作、新的 Material kind). 新协议类型必须先在此定义后再实装
- **不接收**: 具体服务或 domain 的类型 (它们用本 protocol 的类型但不反向扩展); runtime 的执行逻辑 (归 runtime/exec); 业务 enum (归对应 service)
- **边界信号**: 若某 protocol 类型只被一个 service 使用, 疑似过度泛化; 若 protocol 字段含业务字面量 (如 `"demogame"`, `"voxelcraft"`), 属违规 — 协议必须是**结构性**的

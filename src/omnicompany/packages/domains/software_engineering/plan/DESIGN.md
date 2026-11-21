<!-- [OMNI] origin=claude-code domain=software_engineering/plan ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.plan.design_spec.md" -->

# plan · 设计文档

## 状态
- **版本**: V1 (2026-04-25 完整 DAG 管线与双回路机制落地)
- **成熟度**: active
- **下一步**: 将 `self_reviewer` 占位逻辑替换为 LLM 语义审查，并接入 AST 依赖图实现回路1的确定性收敛判定。

## 核心目的
将模糊的需求/设计文档转化为结构化、可执行的 TDD 实施计划，为 `implement` 域提供精确的文件修改映射与测试用例步骤。
**解决**：需求语义解析、代码库快照与上下文累积、上下文充分性动态判定、TDD 计划草案生成、计划结构自审。
**不解决**：实际代码生成与写入（由 `implement` 负责）、运行时调试与故障注入（由 `debugger` 负责）、最终功能验收与等价性测试（由 `verify` / `equiv_test` 负责）。

## 核心接口
- **[team.py:build_team()]** → `TeamSpec`：声明 8 节点 DAG 拓扑、节点成熟度（HARD/SOFT）与双回路 VerdictKind 路由策略。
- **[run.py:build_bindings()]** → `dict[str, Router]`：构建节点名至具体 Router 实例的映射，供 runtime 调度器消费。
- **[routers.py:SpecLoaderRouter]** → `sw_plan.spec`：解析文件/文本需求为标准化 Material。
- **[routers.py:CodebaseScannerRouter] & [routers.py:FileReaderRouter]** → `sw_plan.codebase-scan` / 累积上下文：执行目录树遍历与关键文件内容提取。
- **[routers.py:ContextJudgeRouter]** → `Verdict(PASS|PARTIAL)`：判定代码上下文是否充分，驱动回路1补充扫描。
- **[routers.py:FileMapperRouter] & [routers.py:PlanDrafterRouter]** → `sw_plan.draft`：LLM 驱动生成文件变更映射与 TDD 分步计划。
- **[routers.py:SelfReviewerRouter] & [routers.py:PlanEmitterRouter]** → `Verdict(PASS|FAIL)` / `EMIT`：结构校验与终态计划固化输出。
- **[formats.py:FORMATS]**：定义 `sw_plan.*` 系列 Format 契约（spec, codebase-scan, code-context, plan-draft, plan-final）及父子继承关系。

## 架构决策
### D1 · 八节点 DAG 双回路拓扑替代线性 Pipeline
**决策**: 放弃传统串行执行模型，采用 `context_judge`（回路1）与 `self_reviewer`（回路2）构建条件分支 DAG。上下文不足时回退至扫描节点，结构校验失败时回退至计划起草节点。
**理由**: 实施计划高度依赖仓库上下文完整性。单次扫描极易遗漏隐式依赖，无脑全量扫描会导致 Token 爆炸。双回路在“上下文充分性”与“计划结构合规性”两个关键断点提供精确回退路径，兼顾生成质量与资源开销。
### D2 · 拓扑声明 (TeamSpec) 与业务实现 (Router) 严格解耦
**决策**: `team.py` 仅负责声明节点拓扑、路由条件与成熟度标签；`routers.py` 承载具体业务逻辑；`pipeline.py` 标记 DEPRECATED 并重定向至 `team.py`。`run.py` 仅做胶水绑定。
**理由**: 符合 OmniCompany runtime 演进规范。拓扑声明可被 Guardian 静态审计、被协议层直接校验；具体 Router 可独立替换（如 Hard 规则升级至 LLM Agent）而不破坏 DAG 契约。解耦后支持 `TeamSpec` 跨域复用与可视化渲染。
### D3 · 规划产物隔离落盘与 Guardian 契约化管理 (D1 · manifest)
**决策**: 规划期所有中间态、追踪日志与终版计划严格落盘至 `data/plan/`，与 `src/` 源码物理隔离。通过 `.omni/manifest.yaml` 声明老化策略与体积上限。
**理由**: PLAN agent loop 产生高频中间文件。隔离可避免污染代码树，便于 Guardian 独立执行 TTL 回收（`.tmp` 7天 / `.log` 30天）与体积熔断（512MB），严格遵循 OMNI-005 数据落盘规范。
## 数据流 / 拓扑
```
[输入] Spec/需求文本/路径
   ↓
spec_loader (HARD) ──→ sw_plan.spec
   ↓
codebase_scanner (HARD) ──→ sw_plan.codebase-scan
   ↓
file_reader (HARD) ──→ 累积 code_context
   ↓
context_judge (LLM/HARD)
   ├─ PARTIAL → [回路1] 回退至 codebase_scanner 补充扫描
   └─ PASS ──→ sw_plan.code-context (充分)
              ↓
         file_mapper (LLM) ──→ 文件修改映射
              ↓
         plan_drafter (LLM) ──→ TDD 分步草案 (sw_plan.draft)
              ↓
         self_reviewer (Validator)
              ├─ FAIL → [回路2] 携带错误提示回退至 plan_drafter 重写
              └─ PASS ──→ plan_emitter (确定性)
                          ↓
                     [输出] sw_plan.final (EMIT)
```

## 已知局限
- **局限 1**: `self_reviewer` 当前为占位符 + 基础结构验证，缺乏对 TDD 用例完备性、边界条件覆盖与依赖注入策略的深度语义审查。 · 升级路径: 替换为 `LLMReviewRouter`，注入 `implement/DESIGN.md` 的验收契约，增加对测试用例覆盖率与 Mock 策略的静态分析，失败时携带具体修复提示回传 `plan_drafter`。
- **局限 2**: `context_judge` 的回路终止条件依赖硬编码启发式规则，在超大型仓库中易陷入“扫描-补充”振荡或过早判定 PASS。 · 升级路径: 引入基于 AST/Imports 图的依赖收敛算法（参考 `_shared/DESIGN.md` 的依赖解析工具），将“充分性”判定从文本匹配迁移为图遍历可达性验证，实现确定性收敛与 Token 截断。
## 参考资料
- 关联管线定义: [team.py](team.py), [routers.py](routers.py), [run.py](run.py)
- 格式契约: [formats.py](formats.py) (FORMATS 列表与双回路语义定义)
- 数据落盘策略: .omni/manifest.yaml (§aging_policy / §size_limits)
- 兼容迁移记录: [pipeline.py](pipeline.py) (2026-04-21 标记 DEPRECATED，重定向至 `team.py`)
- 相邻域边界: `src/omnicompany/packages/domains/software_engineering/implement/DESIGN.md` (计划消费方与 TDD 验收标准)
- 规范对齐: `docs/standards/distributed-docs.md` (OMNI-034 结构合规 / §四 src 允许位置)
<!-- [OMNI] origin=claude-code domain=software_engineering/lang_rewrite_verifier ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.lang_rewrite_verifier.design_doc.architecture.py" -->

# lang_rewrite_verifier · 设计文档

## 状态
- **版本**: V1 (2026-04-25 骨架填充：补齐冒烟管线拓扑 / 格式定义 / 路由器绑定与已知重构计划)
- **成熟度**: active
- **下一步**: 执行 `docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md` P1 任务，将 `SmokeTestGeneratorRouter` 从旧 `AgentNodeLoop` 基类迁移至 `packages.services.agent` 新实现，消除 `LLMClient` 直调违规。

## 核心目的
本包负责跨语言代码翻译后的**冒烟测试生成与自动验证**。接收 `rewrite.verified-code`，调用 LLM 生成针对性测试套件 (`smoke.test-suite`)，本地执行并判定结果。若全通过则产出 `smoke.result`；若失败则无缝转交 `debugger` 子包进行错误定位与自动修复。
**不解决**：
- 不执行静态代码分析或形式化验证（交由 `_shared` 或外部工具）。
- 不负责翻译策略本身（属 `lang_rewrite` 职责）。
- 不替代全量单元测试/集成测试套件生成（仅做快速冒烟与致命阻断检查）。

## 核心接口
- **格式注册**：`formats.register_formats(registry: FormatRegistry)` — [formats.py](formats.py)
  - `smoke.test-suite`: 冒烟测试计划 (含 work_dir、compile_command、test_cases 列表)
  - `smoke.result`: 验证通过结果 (含 passed_cases、smoke_passed=True)
- **路由器**：
  - `SmokeTestGeneratorRouter` — [routers.py#L51](routers.py#L51) (AgentNodeLoop，当前待协议迁移)
  - `SmokeRunnerRouter` — [routers.py#L80](routers.py#L80) (HARD Router，本地子进程执行器)
- **管线构建**：`team.build_team() -> PipelineSpec` — [team.py](team.py) (12 节点拓扑定义)
- **绑定组装**：`run.build_bindings(input_dict) -> dict[str, Router]` — [run.py](run.py) (延迟加载本包与 debugger 复用路由)
- **兼容层**：`pipeline.build_pipeline` — [pipeline.py](pipeline.py) (已标记 DEPRECATED，仅 alias 至 `team.py`)

## 架构决策
### D1 · 冒烟验证与错误修复管线复用 (Reuse vs Duplicate)
**决策**: 本包不重复实现调试器逻辑。当 `smoke_runner` 失败时，直接通过 `debug.error-report` 格式触发 `debugger` 子包的 11 个节点 (`error_analyzer` → `context_init` → `hypothesis_generator` → ...)。
**理由**: 翻译后的运行时错误本质与常规代码错误一致，复用 `debugger` 避免维护两套“假设-验证-修复”循环，降低域耦合，符合 `_shared` 模块的“一次定义，多处消费”原则。

### D2 · 两阶段执行：LLM 生成 + 本地硬化运行 (AgentNodeLoop + HARD Router)
**决策**: `smoke_gen` 使用 `AgentNodeLoop` (LLM 多轮推理生成代码)，而 `smoke_runner` 采用纯本地子进程 `HARD Router` (顺序执行，遇 fatal 立即中止并打包报告)。
**理由**: 测试执行需要确定性与环境控制，LLM 不适合做稳定执行器。分离后，`smoke_runner` 可严格管控超时、退出码与日志流，确保 `debug.error-report` 包含足够上下文供后续调试器分析。

## 数据流 / 拓扑
```
[输入] rewrite.verified-code
     ↓
smoke_gen (SmokeTestGeneratorRouter / AgentNodeLoop)
     ├─ LLM 全局阅读翻译后项目，按难度递增生成用例
     └─ 产出 → smoke.test-suite
     ↓
smoke_runner (SmokeRunnerRouter / HARD Router)
     ├─ 顺序执行 test_cases (本地 bash/compile)
     ├─ [PASS] → 产出 smoke.result (smoke_passed=True) → ✅ 管线结束
     └─ [FAIL] → 产出 debug.error-report
          ↓
     [降级至 debugger 子包复用管线]
     error_analyzer → context_init ───────────────────────┐
          │              ↓                                │
     evidence_collector ←─ hypothesis_generator ← probe_designer ← probe_executor
          │              │               │                    │
          └──── regression_to_context ← regression_analyzer ← tester ← fixer
     ↓
[修复后产出] → 回归验证 / 重新进入 smoke_gen 或 emit 最终结果
```

## 已知局限
- **局限 1**: `SmokeTestGeneratorRouter` 仍继承旧版 `AgentNodeLoop`，存在直调 `LLMClient` 与 `list[dict]` 传参的架构违规，违反新协议边界。
  **升级路径**: 严格对齐 `docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md` P1 任务，重写为符合新协议的路由器，通过 FormatRegistry 与 EventBus 通信，移除硬编码直调。
- **局限 2**: `smoke_runner` 当前依赖裸 `subprocess` 直接执行，未实现沙箱隔离或资源限额（内存/网络/磁盘），存在翻译后恶意代码或死循环耗尽宿主资源的风险。
  **升级路径**: 引入 `runtime/sandbox` 模块或依赖容器化执行器；在 `HARD Router` 执行前增加 `cgroups`/`nsjail` 资源限制配置，替换直接调用并统一捕获标准错误流。

## 参考资料
- 关联管线拓扑: [team.py](team.py)
- 关联格式定义: [formats.py](formats.py)
- 关联路由器实现: [routers.py](routers.py)
- 关联绑定组装: [run.py](run.py)
- 迁移计划: `docs/plans/[2026-04-18]AGENT-NODE-LOOP-ROUTERIZATION/plan.md` (P1 待办 #9)
- 复用调试器: `src/omnicompany/packages/domains/software_engineering/debugger/DESIGN.md`
- 上游翻译模块: `src/omnicompany/packages/domains/software_engineering/lang_rewrite/DESIGN.md`
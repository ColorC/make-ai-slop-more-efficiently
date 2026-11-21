<!-- [OMNI] origin=claude-code domain=packages/domains/software_engineering/equiv_test ts=2026-04-25T00:00:00Z type=doc status=design -->
<!-- [OMNI] material_id="material:domains.software_engineering.equiv_test.design_specification.md" -->

# equiv_test · 设计文档

## 状态
- **版本**: V1 (2026-04-25 · 基于 V2 Router/Team 架构重写, 标记 Experimental 阶段)
- **成熟度**: design
- **下一步**: 接入 `lang_rewrite` 主流程验证端到端等价性; 补充多语言后端 (Rust) 执行适配器与 Mock 隔离层

## 核心目的
解决跨语言重构(如 Python → TypeScript/Rust)后的**行为等价性验证**问题。采用 Golden File 模式:Python 实际执行录制基准输出 → 目标语言实现相同接口 → 逐 Key JSON 比对 → LLM 诊断不匹配根因。相比纯 LLM 裁判更严格, 适用于可独立执行、有明确输入输出契约的模块。
**不解决**: 非等价语义需求(如性能优化带来的逻辑变更); 无明确 API 边界的全局系统重构; 依赖外部非确定性状态(网络请求/随机数/未 mock 数据库)的等价判定。

## 核心接口
- **`build_team() -> TeamSpec`** ([team.py](team.py)): 定义等价测试团队拓扑与边(7 节点流水线)
- **`build_bindings(input_dict) -> dict[str, Router]`** ([run.py](run.py)): 实例化各阶段 Router 并注入 `model`/`ts_dir` 配置
- **`TestDesignerRouter`, `GoldenRecorderRouter`, `BaselineCheckRouter`, `TSTestGeneratorRouter`, `TSExecutorRouter`, `ResultComparatorRouter`, `FailureAnalyzerRouter`** ([routers.py](routers.py)): 核心路由节点, 封装 LLM 提示工程与确定性执行逻辑
- **`FORMATS` 列表** ([formats.py](formats.py)): 定义 `equiv.test-spec` → `equiv.test-suite` → `equiv.execution-result` → `equiv.comparison-report` → `equiv.diagnosed-report` 语义格式树, 继承自 `omnicompany.protocol.format`
- **`pipeline.py`** ([pipeline.py](pipeline.py)): DEPRECATED shim, 重定向至 `team.build_team`

## 架构决策
### D1 · Golden File + Baseline 红绿双验证模式
**决策**: 流水线强制包含 `BaselineCheck` 节点(空 stub 跑 TS 确认红灯)与 `GoldenRecorder` 节点(Python 真实执行产出 JSON)。
**理由**: 纯 LLM 生成测试极易产生"幻觉通过"(假阳性)。Baseline 红灯验证确保 TS 环境/运行链路就绪; Golden 录制提供确定性基准。两者结合构成"红-绿"安全网, 杜绝空跑比对与未初始化状态导致的误判。

### D2 · LLM 与确定性执行器严格分层 (Router 混合架构)
**决策**: 7 步管线中, 设计(`TestDesigner`)/录制(`GoldenRecorder`)/生成(`TSTestGenerator`)/诊断(`FailureAnalyzer`) 使用 LLM Router; 基线校验(`BaselineCheck`)/执行(`TSExecutor`)/对比(`ResultComparator`) 使用纯 Python/Shell 确定性 Router。
**理由**: LLM 负责高创造性与模糊语义理解(用例设计、代码生成、根因分析); 确定性执行器负责可重复运行与精确比对。职责隔离避免 LLM 幻觉污染执行状态, 同时便于 `TSExecutor` 替换为不同语言后端而不影响整体拓扑。

### D3 · 渐进式等价判定与 LLM 诊断回退
**决策**: 默认走 `ResultComparator` 逐 key 比对; 仅当比对发现不匹配时, 才触发 `FailureAnalyzer` (LLM) 分析差异并附加诊断报告, 最终统一 EMIT。
**理由**: 全量 LLM 诊断成本高昂且易过度解释。先走确定性对比过滤已等价用例; 仅对真差异投入 LLM 算力进行根因分类(如浮点精度/类型转换/路径差异), 实现算力成本与诊断准确率的最优平衡。

## 数据流 / 拓扑
```
[输入: py_path, ts_path, api_interfaces]
     │
     ├─(TestDesigner Router, LLM)─→ 生成等价测试用例清单 (test_spec)
     │
     ├─(GoldenRecorder Router, LLM+Run)─→ 执行 Python 脚本 → 产出 Golden JSON
     │
     ├─(BaselineCheck Router, Deterministic)─→ 空 Stub 执行 TS → 验证环境红灯 (FAIL)
     │   ├─ 红灯失败 → 继续流水线
     │   └─ 绿灯通过 → 阻断 (环境未隔离风险)
     │
     ├─(TSTestGenerator Router, LLM)─→ 结合 Golden Keys + TS 代码 → 生成对比测试脚本
     │
     ├─(TSExecutor Router, Deterministic)─→ 执行 TS 测试脚本 → 产出 TS JSON
     │
     └─(ResultComparator Router, Deterministic)─→ Golden vs TS JSON 逐 key 比对
         ├─ 全匹配 (PASS) → 直接 EMIT comparison_report
         └─ 存在差异 → 触发 FailureAnalyzer Router (LLM)
             └─ 产出 diagnosed_report (含差异根因/修复建议) → EMIT
```

## 已知局限
- **局限 1**: 当前仅支持 Python → TypeScript 翻译链, Rust 后端未集成。`TSExecutor` 硬编码了 Node.js/npm 调用逻辑。
  **升级路径**: 抽象 `LanguageBackend` 协议, 将 `TSExecutor` 重构为多态适配器; 在 `formats.py` 新增 `equiv.rust-executor` 格式定义, 通过 `run.py` 的 `input_dict` 注入目标语言标识动态路由。
- **局限 2**: `GoldenRecorder` 对 Python 脚本的外部依赖(第三方库/系统路径)未做自动 Mock, 录制可能受环境干扰导致 Golden File 不稳定。
  **升级路径**: 在 `TestDesigner` 阶段引入依赖分析步骤, 自动生成 `pytest` mock fixtures 或 `unittest.mock` 补丁代码注入录制脚本; 同步增加 `execution-result` 格式中的 `environment_hash` 字段, 便于版本回溯。
- **局限 3**: `ResultComparator` 采用严格 JSON 等值比较, 对浮点数精度误差或无序数组/集合容忍度低, 易产生大量误报差异。
  **升级路径**: 替换为结构化比对器(基于 `deepdiff` 或自定义规则), 支持 `epsilon` 配置、数组忽略顺序模式; 将比对规则下沉至 `test-spec` 格式, 由 `TestDesigner` 根据数据类型自动配置比对策略。

## 参考资料
- 关联源码: [routers.py](routers.py) · [team.py](team.py) · [formats.py](formats.py) · [run.py](run.py)
- 兼容管线: [pipeline.py](pipeline.py) (已废弃, 保留 shim 重定向)
- 协议依赖: `src/omnicompany/protocol/anchor.py` (Verdict/AnchorSpec) · `src/omnicompany/runtime/routing/router.py`
- 团队拓扑: `src/omnicompany/protocol/team.py` (TeamSpec/TeamNode/TeamEdge)
- 兄弟包边界: [lang_rewrite/DESIGN.md](../lang_rewrite/DESIGN.md) (上游翻译模块) · [lang_rewrite_verifier/DESIGN.md](../lang_rewrite_verifier/DESIGN.md) (纯 LLM 裁判验证)
- 规范依据: `docs/standards/distributed-docs.md` (§四 代码内文档与就近设计) · `docs/standards/design_md_template.md` (OMNI-034 七节结构)
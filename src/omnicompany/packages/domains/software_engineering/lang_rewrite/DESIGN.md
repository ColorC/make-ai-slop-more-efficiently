<!-- [OMNI] origin=claude-code domain=packages/domains/software_engineering/lang_rewrite ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.lang_rewrite.design_doc.architecture.py" -->

# lang_rewrite · 设计文档

## 状态
- **版本**: V1 (从 skeleton 升级为 active，完成 14 节点 DAG、Routers 实现与四层验证闭环集成)
- **成熟度**: active
- **下一步**: 接入真实 LLM 会话持久化与中间产物落盘；将验证失败反馈路径（agent_fixer/style_fixer）的 retry 策略从硬编码切换为可配置策略路由，支持按目标语言特性动态调整重试上限与退避逻辑。

## 核心目的
提供 Python 引擎层模块向 TypeScript / Rust 的自动化跨语言改写管线。通过 14 节点有向无环图（DAG）串联依赖分析、语义提取、LLM 惯用翻译、静态检查、接口比对与行为测试，实现“六元语义等价”验证闭环。
**不解决**：
- 不处理 UI/前端框架的转换（仅针对纯逻辑/引擎层模块）
- 不替代人工架构评审（仅输出等价性验证报告与待修代码）
- 不管理跨仓库的批量迁移编排（需依赖外部 Orchestrator 或 RepoAbsorption 计划）
- 不负责运行时热重载或部署（属 runtime/deploy 职责）

## 核心接口
- **`build_pipeline() -> TeamSpec`** — 构建 14 节点 DAG 拓扑，定义节点依赖与验证回路（底层委托 `team.build_team`） · 源码: [team.py](team.py)
- **`build_bindings(input_dict: dict | None = None) -> dict[str, Router]`** — 按节点 ID 延迟加载对应 Router 实例，支持注入 model / work_dir / 目标路径参数 · 源码: [run.py](run.py) / [__init__.py](__init__.py)
- **`DOMAIN = "rewrite"` & `FORMATS: list[Format]`** — 注册域标识与 10 种语义数据格式（从 SourceModule 到 VerifiedCode） · 源码: [formats.py](formats.py)
- **`register_formats(registry: FormatRegistry)`** — 将本域格式批量注册至全局 FormatRegistry，供运行时总线消费 · 源码: [formats.py](formats.py)

## 架构决策
### D1 · 分层验证闭环代替单次 LLM 翻译
**决策**: 管线不依赖单次 Prompt 输出最终代码，而是拆分为 `翻译 → L1 编译检查(tsc/cargo) → L2 风格检查(biome/rustfmt) → L3 签名/行为比对 → L4 LLM 等价裁判`，失败节点触发对应的 `fixer` Router 重试。
**理由**: 跨语言惯用法映射存在天然歧义。单层生成极易产生可编译但语义偏移的代码。四层硬检查+LLM裁判的架构（借鉴 OpenRewrite/Vert 行业经验）能以确定规则拦截 80% 语法/风格错误，将 LLM 算力集中在语义对齐与边界修复上，显著降低幻觉率。

### D2 · 依赖图拓扑排序驱动并发扇出 (Fan-out)
**决策**: `dependency_mapper` 输出全量依赖图后，`demand_extractor` 与 `supply_scanner` 并发执行，结果汇入 `idiom_translator` 上下文。
**理由**: 翻译质量高度依赖“下游怎么用”(Demand) 与“上游提供什么”(Supply)。并发扫描避免串行阻塞，且在 DAG 中保持数据流清晰。拓扑排序确保被依赖模块优先改写，下游模块在翻译时可直接引用已验证的对外接口签名，切断跨文件上下文幻觉。

### D3 · Router 实现按目标语言解耦映射表
**决策**: `routers.py` 内部维护 `_PYTHON_TO_TS` / `_PYTHON_TO_RS` 映射字典与外部工具路径发现逻辑（如 `_rust_env`），而非为 TS 和 Rust 拆分支线子包。
**理由**: 当前阶段改写策略高度相似（均依赖 AST 分析 → 上下文构建 → LLM 翻译 → 编译验证）。映射表集中管理降低维护成本，且便于统一接入新目标语言时仅需扩展字典与校验器，无需重构管线骨架与节点绑定逻辑。

## 数据流 / 拓扑
```
[输入: Python 源码树 / work_dir]
      ↓
(1) SourceAnalyzerRouter ─→ source-module (AST/元数据/公开接口)
      ↓
(2) DependencyMapperRouter ─→ dependency-graph (拓扑排序/移植顺序)
      ├─(fan-out)→ (3) DemandExtractorRouter ─→ demand-set (下游调用签名)
      └─(fan-out)→ (4) SupplyScannerRouter ───→ supply-map (上游导出契约)
                      ↓
          (5) IdiomTranslatorRouter (LLM) ─→ translation-context
                      ↓ (FAIL ↺ agent_fixer 反馈修复)
          (6) TypeCheckerRouter (tsc/cargo check) ─→ checked-code (L1 硬编译)
                      ↓ (FAIL ↺ style_fixer 反馈修复)
          (7) StyleCheckerRouter (biome/rustfmt) ─→ checked-code (L2 硬风格)
                      ↓
          (8) InterfaceExtractorRouter ─→ AST 接口快照
                      ↓
          (9) SignatureComparatorRouter ─→ 签名等价报告 (L3a)
                      ↓
         (10) BehavioralTesterRouter ───→ 测试用例执行报告 (L3b)
                      ↓
         (11) EquivalenceJudgeRouter (LLM) → verified-code (L4 最终裁判)
                      ↓
[输出: 目标语言工程目录 + 等价性验证报告]
```

## 已知局限
- **硬编码的修复重试上限未暴露为策略配置** — 当前 `agent_fixer` 与 `style_fixer` 的重试次数与退避逻辑硬编码在 Router 循环内，缺乏基于验证错误类型的动态策略（如类型错误优先重试签名修复，风格错误仅重试一次）。**升级路径**: 引入 `RetryPolicy` 数据类与策略路由，将 `max_turns` 和 `backoff_strategy` 配置下沉至 `build_bindings` 的 `input_dict`，使调用方可按目标语言特性定制重试行为。
- **中间编译产物未落盘，调试链路过长** — L1/L2 检查器的 `subprocess` 错误流仅打印至日志，未持久化为结构化诊断报告。当 L4 裁判判定失败时，无法回溯具体是编译警告还是链接错误导致。**升级路径**: 在 `routers.py` 中为 `TypeCheckerRouter` 和 `StyleCheckerRouter` 增加 `DiagnosticArtifact` 输出格式，将编译器 stdout/stderr 解析为结构化 JSON 写入工作区，供 `EquivalenceJudgeRouter` 作为额外上下文输入，实现可追溯的失败归因。

## 参考资料
- 关联管线拓扑: [team.py](team.py) (替代已废弃的 pipeline.py)
- 关联格式定义: [formats.py](formats.py) (DOMAIN, FORMATS, register_formats)
- 关联 Router 实现: [routers.py](routers.py) (全部 14 个节点路由逻辑与语言映射表)
- 关联绑定构建: [run.py](run.py) (延迟加载与输入参数注入)
- 关联计划: `docs/plans/_archive/[2026-04-08]REPO-ABSORPTION-WORKFLOW/01_PRIOR_ART_AND_LANDSCAPE.md` (lang_rewrite 验证骨架复用说明)
- 关联兄弟包: `src/omnicompany/packages/domains/software_engineering/lang_rewrite_verifier/DESIGN.md` (独立验证器扩展)
- 规范: `docs/standards/distributed-docs.md` (OMNI-034 设计文档结构要求)
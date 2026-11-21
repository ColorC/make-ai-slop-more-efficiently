<!-- [OMNI] origin=claude-code domain=tools ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:tools.step_runner_design.specification.md" -->

# tools · 设计文档

## 状态
- **版本**: V1 (2026-04-25 · 初始实现 StepRunner 单点调试能力)
- **成熟度**: active
- **下一步**: 扩展为通用工具集（如 Mock 数据生成器 / 节点耗时分析器），统一落盘目录为 `data/tools/scratch/` 以避免跨包命名空间污染；补充 CLI 快捷命令包装。

## 核心目的
`omnicompany.tools` 提供**管线开发与调试阶段的轻量级辅助能力**。核心解决“不启动完整管线即可单步执行节点、观察中间状态、复用 fixture 覆盖”的痛点，使开发者能聚焦单节点输入输出契约。
**不解决**：
- 生产环境调度、重试与容错（属 `runtime` 职责）
- 业务数据清洗、格式转换与校验（属 `core` / `packages` 职责）
- 全量性能压测与容量规划（需独立压测服务）
- 长期状态存储或审计留痕（属 `bus` / `tracing` 职责）

## 核心接口
- **`StepRunner`** ([step_runner.py](step_runner.py)) — 逐节点步进执行器。核心方法 `async run_step(node_id: str, input_data: Any, fixture_overrides: dict[str, Any] | None = None, from_step: str | None = None)`。支持状态恢复与动态依赖注入。
- **`_safe_serialize(obj: Any)`** ([step_runner.py](step_runner.py#L23)) — 递归序列化拦截器。将不可 JSON 化的对象（函数、闭包、自定义实例等）安全降级为描述字符串，保障快照落盘不中断。
- **快照路径约定** — 中间态默认落盘至 `data/<domain>/scratch/steps/<node_id>.json`。由 `StepRunner` 内部 `Path` 逻辑按需创建目录，调试结束后可人工查看或 `rm`。

## 架构决策
### D1 · 快照采用扁平 JSON 而非持久化数据库/对象存储
**决策**: 调试中间态直接序列化为本地 JSON 文件，拒绝 SQLite/Redis 等外部依赖。
**理由**: 工具包定位为“快速排查与离线回放”。JSON 可直接被 `grep`、`diff`、IDE 预览打开，开发者可手动编辑快照后喂给下一步。零外部依赖保证在任何开发环境开箱即用。

### D2 · fixture_overrides 运行时注入而非静态配置驱动
**决策**: `run_step` 允许传入 `fixture_overrides: dict`，在节点执行前动态替换原管线依赖（如 Mock 外部 API、替换数据拉取函数）。
**理由**: 调试场景多变，硬编码或修改全局配置文件并重启动管线的成本过高。内存注入保证“一次覆盖，仅限当前调用”，绝不污染全局注册表与生产路由。

### D3 · 不可序列化值降级为描述字符串而非抛出异常
**决策**: `_safe_serialize` 遇到非标准类型时，递归替换为 `<non-serializable>` 占位符，严格阻断 `TypeError`。
**理由**: 节点上下文常携带 logger、连接池、lambda 等不可序列化对象。调试快照的目的是“看数据结构与关键字段”，强保序列化会频繁打断调试流。降级策略在可观测性与容错间取得工程平衡。

### D4 · 独立轻量上下文，不强制加载完整 PipelineSpec
**决策**: `StepRunner` 实例化仅需 `pipeline_name` 与 `domain`，按需解析目标节点入口，不加载全量 `PipelineSpec`。
**理由**: 完整管线初始化慢且隐式依赖复杂。单步调试需要“轻量沙箱”，隔离主调度逻辑的副作用，实现秒级启动与资源最小化。

### D5 · 纯异步模型，复用调用方 Event Loop
**决策**: 所有对外接口采用 `async def`，内部不创建或管理独立 `asyncio.EventLoop`，完全依赖调用方调度。
**理由**: 避免与框架主 runtime 或 Jupyter/测试框架的 loop 发生冲突（`RuntimeError: This event loop is already running`）。降低调试上下文切换成本，符合现代 Python 异步库设计范式。

## 数据流 / 拓扑
```
[调用方 Python/Notebook]
   ↓ await runner.run_step(node_id="A", input_data=..., fixture_overrides={...})
   ↓
[StepRunner.run_step]
   ├─ 检查 from_step 参数 → 若有：从 data/<domain>/scratch/steps/<prev>.json 反序列化状态作为基座输入
   ├─ 合并 input_data 与 快照恢复数据 → 构造最终执行上下文
   ├─ 动态注入 fixture_overrides 至目标节点上下文（替换原依赖）
   ├─ 执行目标节点入口 (async call)
   ├─ 捕获原始 result 与异常
   ↓
[StepRunner._safe_serialize]
   ├─ 递归遍历 result dict/list
   └─ 替换不可序列化字段为 "<non-serializable>"
   ↓
[落盘] → data/<domain>/scratch/steps/<node_id>.json (幂等写入)
   ↓
[返回] → 清洗后的 result dict 返回给调用方，供断言或传入下一节点
```

## 已知局限
- **工具散落在包根目录，缺乏按功能子包的组织** · 升级路径: 引入 `__init__.py` 显式 `__all__` 导出，后续新增工具（如耗时分析/数据 Mock）按语义划分子包（`tools/snapshot/`, `tools/mock/`），并更新 `__init__.py` 统一入口。
- **快照文件缺乏自动过期清理与版本管理** · 升级路径: 当前依赖开发者手动 `rm`。V2 将提供 `StepRunner.cleanup(max_age_days: int)` 静态方法，并在 CI 调试后自动清理陈旧快照，避免 `scratch/` 目录无限膨胀。
- **`fixture_overrides` 仅支持单次内存传递，无法跨用例复用** · 升级路径: 计划引入 `fixture_profiles.yaml` 定义层，支持通过 `profile_name` 一键加载预设 Mock 链。当前阶段保持 API 极简，后续以向后兼容方式扩展参数签名。
- **不支持分布式断点同步** · 升级路径: `StepRunner` 仅针对单机/同进程节点。若未来管线节点跨进程/容器，将基于 `protocol` 层实现远程状态拉取协议（`RemoteStepRunner`），当前阶段明确标注为单机调试专用。

## 参考资料
- 模块源码: [__init__.py](__init__.py) / [step_runner.py](step_runner.py)
- 框架调度边界: src/omnicompany/runtime/DESIGN.md
- 核心数据契约: [src/omnicompany/core/DESIGN.md](../core/DESIGN.md)
- 分布式文档规范: docs/standards/distributed-docs.md (OMNI-034 设计文档结构要求)
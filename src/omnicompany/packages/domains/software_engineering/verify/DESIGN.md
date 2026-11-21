<!-- [OMNI] origin=claude-code domain=packages/domains/software_engineering/verify ts=2026-04-25T00:00:00Z type=doc status=design -->
<!-- [OMNI] material_id="material:domains.software_engineering.verify.design_spec.md" -->

# verify · 设计文档

## 状态
- **版本**: V1 (2026-04-25 升级 · 从 skeleton 补齐核心逻辑/拓扑/决策)
- **成熟度**: design
- **下一步**: 接入 E2E 集成测试管线验证 `supplemental_designer` 回路收敛性；按实际落盘产物补全 `.omni/manifest.yaml` 的 `allowed_subdirs` 声明。

## 核心目的
为软件工程域提供标准化的“声称验证（Verify）”集成管线。接收待验证的 Claim，自动化执行环境检查、命令执行、结果分析与报告生成；在证据不足时动态设计补充测试并回路重试，直至得出 CONFIRMED 或 REFUTED 结论。
**不解决**: 代码实现与生成（属 `implement` 域）；单元测试等价性比对（属 `equiv_test` 域）；运行时调试与故障定位（属 `debugger` 域）。

## 核心接口
- **`run.build_bindings() -> dict[str, Router]`** ([run.py](run.py)) — 构建节点名到 Router 实例的绑定映射，管线入口装配。
- **`team.build_team() -> TeamSpec`** ([team.py](team.py)) — 定义验证管线 DAG 拓扑（7 节点 1 回路）与节点路由策略。
- **`routers.ClaimParserRouter`** ([routers.py](routers.py)) — 解析 `sw_verify.claim` 输出 `env-check` 上下文。
- **`routers.EnvCheckerRouter`** ([routers.py](routers.py)) — HARD 节点：检查工作目录与命令可执行性。
- **`routers.CmdExecutorRouter`** ([routers.py](routers.py)) — HARD 节点：执行验证命令并捕获 stdout/stderr/exit_code。
- **`routers.OutputAnalyzerRouter`** ([routers.py](routers.py)) — SOFT 节点：比对输出与期望模式，产出 `VerdictKind`。
- **`routers.SupplementalDesignerRouter`** ([routers.py](routers.py)) — SOFT 节点：UNCERTAIN 时生成补充验证命令。
- **`routers.ReportEmitterRouter`** ([routers.py](routers.py)) — 确定性节点：汇总全链路上下文生成 `sw_verify.report`。
- **`formats.FORMATS`** ([formats.py](formats.py)) — 注册 6 种 `Format` (`claim`, `env-check`, `execution`, `analysis`, `supplemental`, `report`) 及流转关系。

## 架构决策
### D1 · 确定性 Transformer 与 LLM 节点混合编排
**决策**: 管线严格区分确定性执行节点 (`claim_parser`, `env_checker`, `cmd_executor`, `report_emitter`) 与 SOFT/LLM 节点 (`output_analyzer`, `supplemental_designer`)。
**理由**: 环境探测与命令执行必须可重放、零歧义；结果语义判定与补充用例生成需上下文理解。混合编排避免“全 LLM”导致的不可控成本，同时保留关键链路的确定性。

### D2 · UNCERTAIN 态显式回路 (Loop)
**决策**: `output_analyzer` 产出 `UNCERTAIN` 时不中断，而是路由至 `supplemental_designer` 生成新验证命令，跳回 `cmd_executor` 形成执行回路，直至收敛为 CONFIRMED/REFUTED。
**理由**: 软件验证常遇“证据不足但非反证”场景。单次执行易误判，显式回路允许管线动态收集补充上下文，在验证严格度与执行次数间取得平衡。

### D3 · 上下文扁平化传递 (`_empty_context`)
**决策**: 节点间通过扁平字典 `_empty_context()` 传递 `verify-context`，聚合 `claim`、`env_ok`、`executions`、`analyses`、`supplementals`。
**理由**: 避免深层嵌套带来的序列化/反序列化性能损耗；字典结构原生兼容 JSON，便于 `report_emitter` 直接遍历全量轨迹生成结构化报告。

## 数据流 / 拓扑
DAG 拓扑与数据流转（基于 [team.py](team.py) 与 [formats.py](formats.py)）:
```
[输入] sw_verify.claim (claim + verify_cmd + work_dir + expect_pattern)
  ↓ claim_parser (确定性)
[上下文] sw_verify.env-check (解析后上下文)
  ↓ env_checker (HARD)
[就绪] env_ok=True / False (失败则短路)
  ↓ cmd_executor (HARD)
[执行结果] sw_verify.execution (stdout/stderr/exit_code/elapsed)
  ↓ output_analyzer (SOFT/LLM 语义判定)
  ├─→ CONFIRMED / REFUTED ─→ report_emitter → [输出] sw_verify.report
  └─→ UNCERTAIN ─→ supplemental_designer (SOFT/LLM)
                     ↓ 生成新 verify_cmd + expect_pattern
                     └─→ [回路] cmd_executor (继续执行)
```

## 已知局限
- **局限 1**: `supplemental_designer` 回路缺乏显式退出预算控制，当前依赖隐式硬编码限制。若 LLM 持续生成低效/无效命令，将导致算力浪费与管线挂起。· **升级路径**: 引入基于 Token 消耗或执行次数的动态预算阈值（如 `max_retries=3` 可配置化），超限时强制 `REFUTED` 并记录 `budget_exhausted` 标记。
- **局限 2**: `CmdExecutorRouter` 当前假设工作目录本地可写且无沙箱隔离，直接执行外部 `verify_cmd` 存在潜在安全越权风险。· **升级路径**: 在 `env_checker` 阶段增加沙箱环境就绪性预检，并接入 `subprocess` 隔离适配器（如 nsjail 或容器化 runtime），确保验证执行边界可控。

## 参考资料
- 关联实现: `routers.py` (6 个 Router 实现) · `team.py` (拓扑定义) · `run.py` (绑定装配)
- 格式协议: `formats.py` (Format 注册) · `omnicompany.protocol.anchor.Verdict`
- 兄弟包: `../implement/DESIGN.md` (前置实现域) · `../equiv_test/DESIGN.md` (等价性测试域)
- 规范依据: `docs/standards/distributed-docs.md` (OMNI-034 七节结构) · `.omni/manifest.yaml` (本包数据布局声明)
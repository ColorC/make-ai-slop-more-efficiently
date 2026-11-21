<!-- [OMNI] origin=claude-code domain=packages/domains/software_engineering/tdd ts=2026-04-25T00:00:00Z type=doc status=active -->
<!-- [OMNI] material_id="material:domains.software_engineering.tdd.design_spec.md" -->

# tdd · 设计文档

## 状态
- **版本**: V1 (2026-04-25 填充 skeleton 为完整实现, 团队拓扑与路由绑定闭环)
- **成熟度**: active
- **下一步**: 接入真实 LLM Agent Loop 替换当前单次代码生成逻辑, 实现 max_steps 动态迭代控制 (依赖 runtime 升级)

## 核心目的
本包定义并执行测试驱动开发 (TDD) 的标准自动化工作流。解决如何从高层计划自动生成 failing test、执行测试、在失败时生成实现代码、循环至通过并输出最终报告。提供确定性的红绿循环拓扑与 LLM/硬逻辑节点的路由契约。
不解决: 高层任务计划生成 (由 plan 域负责)、非 TDD 场景的直接代码实现 (由 implement 域负责)、测试代码的语义等价验证 (由 equiv_test 域负责)、运行期断点调试 (由 debugger 域负责)。

## 核心接口
- **格式定义** ([formats.py](formats.py)): `sw_tdd.plan`, `sw_tdd.test-code`, `sw_tdd.test-result`, `sw_tdd.impl-code`
- **管线拓扑构建** ([team.py](team.py)): `build_team() -> TeamSpec` (返回 5 节点 DAG 与 FAIL/PASS 路由规则)
- **路由绑定工厂** ([run.py](run.py)): `build_bindings(input_dict) -> dict[str, Router]`
- **核心 Routers** ([routers.py](routers.py)):
  - `PlanLoaderRouter` (解析输入计划)
  - `TestWriterRouter` (生成测试文件与命令)
  - `TestRunnerRouter` (执行测试并捕获 exit_code/stdout/stderr)
  - `ImplWriterRouter` (失败时生成修复实现)
  - `ReportEmitterRouter` (聚合终态报告)
- **上下文初始化** ([routers.py](routers.py)): `_empty_context()` (构建 TDD 会话状态结构)

## 架构决策
### D1 · DAG 内建红绿循环 (Red-Green Loop) 而非外部调度
**决策**: TDD 的“写测试→跑测试→失败改代码→再跑”循环硬编码在 team.py 的 Route 路由规则中, 通过 test_runner 节点的 VerdictKind 直接决定下一步是 report_emitter 还是 impl_writer。
**理由**: TDD 是高度确定性的状态机。将循环下沉到 TeamSpec 定义层避免外部 L1/L2 编排器过度干预, 使工作流具备自收敛能力, 严格对齐声明即执行的契约。

### D2 · Agent Loop 职责分离与单次生成设计
**决策**: test_writer 与 impl_writer 在当前版本仅执行单次 LLM 代码生成, 不内置递归重试; 多步探索与自我修正交由外层 TeamRunner 的 max_steps 或后续接入的 agent_loop 基础设施。
**理由**: 保持 Domain 包轻量。TDD 域只负责产出物格式与节点流转, LLM 的对话历史管理与 Self-Correction 属于 runtime 通用能力, 避免业务域重复造轮子。

### D3 · 严格空集数据白名单阻断隐式落盘
**决策**: 本包不声明任何 data/ 子目录, 不持久化中间测试产物或覆盖率快照, 所有状态仅在内存 context 中流转, 最终报告以 Verdict 形式向上游交付。
**理由**: TDD 管线是瞬态执行流, 实际代码产物直接写入用户 project_dir。遵循 OMNI-051 未声明即污染原则, 降低仓库膨胀风险并简化健康扫描。

## 数据流 / 拓扑
```
[输入: sw_tdd.plan]
       ↓
1. plan_loader (ANCHOR) ── 解析计划, 提取项目路径与步骤
       ↓ (产出: sw_tdd.test-code)
2. test_writer (LLM) ──── 生成测试文件与运行命令
       ↓
3. test_runner (HARD) ── 执行命令, 捕获 exit_code / stdout / stderr
       ├── PASS ────────────────────────── 4. report_emitter → [EMIT 成功报告]
       ↓ (Verdict=FAIL, 上限 3 轮回路)
5. impl_writer (LLM) ──── 读取测试报错, 生成修复代码
       ↓ (产出: sw_tdd.impl-code)
└────────── 回路回跳至 3. test_runner ──┘
```

## 已知局限
- 当前 test_writer / impl_writer 仅做单次 Prompt 生成, 缺乏上下文积累与测试用例自适应调整能力 · 升级路径: 在 runtime 升级支持动态 Material 传递后, 将 Router 改造为消费 sw_tdd.test-result 作为上下文输入, 并接入 AgentLoop 基础设施实现多轮 Self-Refine。
- 测试执行依赖主机环境 subprocess, 无沙箱隔离, 存在恶意代码执行或环境污染风险 · 升级路径: 接入 omnicompany.core/sandbox 或容器执行器替代直连 subprocess, 在 TestRunnerRouter 中替换执行后端, 保持路由接口不变。

## 参考资料
- 关联拓扑与路由: [team.py](team.py), [run.py](run.py), [routers.py](routers.py)
- 关联格式契约: [formats.py](formats.py)
- 兼容旧入口: [pipeline.py](pipeline.py) (已 DEPRECATED, 转发至 team.py)
- 兄弟域边界: `../plan/DESIGN.md` (上游输入), `../implement/DESIGN.md` (非 TDD 实现), `../equiv_test/DESIGN.md` (等价验证)
- 规范约束: `docs/standards/distributed-docs.md` (六域放置规则), `.omni/manifest.yaml` (数据布局白名单)
<!-- [OMNI] origin=codex domain=services/code_runtime_test ts=2026-08-09T00:00:00Z type=design status=active -->
<!-- [OMNI] material_id="material:utility.runtime_test.code.design_specification.md" -->

# code-runtime-test 设计

## 状态

`code-runtime-test` 是当前保留的通用确定性代码产物测试管线。它只验证存在明确 ground truth 的代码型产物；已退役的 `absorption-runtime-test` 与 runtime-test builder 不再是入口，也不得据此恢复。

团队级运行健康检查由 `team-supervisor` 承担。本设施不负责持续学习编排，不是 AI Learning Observatory 的替代主线。

## 核心接口

输入为 `code_runtime_test.target_spec`：

```json
{
  "target_team_id": "csv-to-md",
  "test_cases": [
    {
      "name": "case_1_basic",
      "kind": "success",
      "input": {"path": "..."},
      "expected_path": "..."
    },
    {
      "name": "error_file_not_found",
      "kind": "error",
      "input": {"path": "..."},
      "expected_verdict": "FAIL",
      "diagnosis_keywords": ["not found"]
    },
    {
      "name": "case_repro",
      "kind": "reproducibility",
      "input": {"path": "..."}
    }
  ],
  "output_extractor": "report_markdown"
}
```

输出为 `code_runtime_test.portrait`，至少包含：

- success case 的 byte-exact 通过率与差异计数；
- error case 的 verdict 与诊断关键词命中率；
- 同输入重复执行的 byte-identical 结果；
- 可复核的执行耗时与证据位置。

## 架构决策

- 全部验证器为 HARD worker，不使用 LLM 代替确定性断言。
- 目标程序通过受控 subprocess/dispatch 执行，验证逻辑与目标实现隔离。
- portrait 使用自然语言结论和物理度量，不生成抽象好坏标签。
- 只有存在稳定预期输出或契约的代码产物才能接入。

## 数据流 / 拓扑

```text
TargetIngressWorker
  ├─> GoldenContractRunner
  ├─> ErrorPathRunner
  └─> ReproducibilityRunner
          │
          └─> PortraitAssemblerWorker
```

关键 material：

| material | producer | consumer |
|---|---|---|
| `code_runtime_test.target_spec` | 外部调用方 | TargetIngressWorker |
| `code_runtime_test.target_metadata` | TargetIngressWorker | 三个验证器 |
| `code_runtime_test.golden_evidence` | GoldenContractRunner | PortraitAssemblerWorker |
| `code_runtime_test.error_evidence` | ErrorPathRunner | PortraitAssemblerWorker |
| `code_runtime_test.reproducibility_evidence` | ReproducibilityRunner | PortraitAssemblerWorker |
| `code_runtime_test.portrait` | PortraitAssemblerWorker | 调用方 |

## 已知局限

- 不适合开放式研究、文案质量、模型判断或缺乏唯一预期输出的任务。
- ground truth 变更必须显式版本化，否则结果不可跨版本比较。
- 本设计只定义测试语义；具体目标团队仍需维护 fixture、expected output 与超时边界。
- 已退役的 repo absorption 历史实现只允许作为归档证据读取，不得注册为新目标或新管线。

## 参考资料

- AI Learning Observatory 唯一主线：`docs/projects/ai-learning-observatory/PROJECT_INDEX.md`
- 持续学习退役策略：`docs/projects/ai-learning-observatory/docs/RETIREMENT_POLICY.md`
- 通用团队健康检查：`team-supervisor` skill 与同名管线

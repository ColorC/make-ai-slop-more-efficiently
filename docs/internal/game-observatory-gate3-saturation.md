# Game Observatory Gate 3 有限域饱和账本

该账本是 Game Observatory 的内部 QA 合同，用于回答一个窄问题：声明的游戏界面有限域是否已经完成可复核的交互普查。数据不会进入公开 catalog、事实反推案或阅读器；公开层也不应引用账本中的 state、candidate、review 等内部 ID。

## 合同入口

Pydantic 合同和确定性校验器位于：

- `src/omnicompany/packages/domains/game_observatory/saturation.py`
- 根对象：`FiniteDomainSaturationLedger`
- 校验结果：`SaturationValidationResult`
- schema：`game-observatory.finite-domain-saturation.v1`

账本包含以下部分：

1. `scope`：游戏、build、有限域边界、入口状态和安全边界。
2. `state_nodes`：每个界面或可区分状态的完整画面证据与可见候选清单。
3. `candidates`：原图坐标框、归一化动作、安全分类、裁决、执行证据、子状态、返回或回滚。
4. `clean_reviews`：从干净起点进行的独立复查；`state_evidence` 要为每个状态分别绑定至少一条属于该 review evidence run 的 passed step，同一 step 不能重复证明多个状态，也不能被两次 clean review 复用。两次复查必须使用不同 evidence run 和互不重叠的 step 集合。
5. `human_omission_review`：人工逐画面检查明显遗漏区域。
6. `answerability`：机制、资源、状态三个维度的可回答事实检查。

候选裁决只允许：

- `verified_transition`
- `verified_state_change`
- `verified_no_change`
- `forbidden`
- `deferred`

安全候选必须得到前三种验证结论。`forbidden` 用于隔离边界内明确禁止执行的动作；`deferred` 用于缺少授权的动作，并需要写明原因。所有证据 step ID 都会回查 canonical ObservatoryStore，字符串本身不算证据。

返回／回滚动作遵循动作类型的几何语义：`tap`、`swipe`、`pinch`、`two_finger_swipe` 必须声明 `target_bounds`，并与 recorder step 的原图坐标一致；`two_finger_swipe` 的两条手指路径都必须处于原图视口和目标区域内；`back`、`home`、`wait` 等无可见目标的系统动作可以省略 `target_bounds`。

## 最小校验命令

在 `E:\WindowsWorkspace\omnicompany` 执行：

```powershell
$env:PYTHONPATH='src'
venv\Scripts\python.exe -m omnicompany.packages.domains.game_observatory.cli saturation-check `
  --root data/domains/game_observatory `
  --file data/domains/game_observatory/benchmarks/gate3/<domain>/saturation-ledger.json `
  --destination data/domains/game_observatory/benchmarks/gate3/<domain>/saturation-validation.json
```

进程退出码为 `0` 时，`saturation_pass` 才会为 `true`。任何失败项都会按 `code`、`path`、`message` 输出，常见阻断包括：

- 安全候选未裁决或仍处于 deferred；
- 子状态缺失、未完成候选枚举或无法从入口到达；
- 已验证候选缺少 canonical passed evidence step；
- 发布候选的 evidence step 不满足原子证据质量，或 evidence run 尚未完成；
- 禁止／待授权动作存在执行 step；
- 交互缺少返回路径，状态变化缺少回滚；
- 少于两次不同 evidence run 的全域 clean review、任一状态缺少独立 review step，或不同 review 复用了 step；
- 人工明显遗漏复核未通过；
- 机制、资源、状态任一维度无法由事实记录回答。

校验器采用 fail-closed 语义。账本解析失败、证据存储不可用、证据与 scope/build 不一致都会阻断饱和通过。

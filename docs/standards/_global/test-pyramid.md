<!-- [OMNI] origin=claude-code domain=docs/standards ts=2026-05-02T00:00:00Z type=standard status=active -->
<!-- [OMNI] material_id="material:standards.global.test_pyramid_statemachine_invariant.md" -->

# 三层测试金字塔铁律 · Test Pyramid

> **立档时间**: 2026-05-02 · 由 autochess demo 99 浅断言测漏 8 真 bug 实战踩出
>
> **适用范围**: 凡有"代码 → 自动化测试 → 用户验收" 链路的工程, 测试体系必须按本文三层结构 + 状态机驱动 + 红绿对照建.
>
> **配套铁律**: `validation_calibration_red_green_gradient.md`, `cross-end-board-consistency.md`, `wysiwyg-drag-drop.md`.

---

## 一 · 问题来源 (实测漏洞)

2026-05-01 autochess demo 现状:

| 指标 | 数 | 备注 |
|---|---|---|
| 测试总数 | 279 (vitest 197 + playwright 82) | 全过 |
| 用户实测真 bug | 12 项 → 修后还有 8 项 | 测试**完全没捕** |
| Branch coverage | 未测 (估 ~50%) | 没装工具 |
| Mutation score | 未测 (估 ~30%) | 没装 Stryker |
| 红绿对照 | 仅 13 个状态转移测有 | 其余 266 测是浅断言 |

**症状**: 99 测全过但用户一动就 8 真 bug. 浅断言测试看代码自洽, 不看玩家路径; LLM 写测试容易写"组件存在 / 按钮可点" 这类自满足条件.

**根因 (用户原话)**: 测试用例本身没那么需要 LLM, 现实中的 demo 团队是手工写的. AI IDE 写测试漏掉真路径是因为没立"测试必须有判别力" 的硬约束 — 软 prompt 引导"写好一点" 不够.

---

## 二 · 铁律 · 三层金字塔 + 状态机驱动 + 红绿对照

### 2.1 三层金字塔结构

| 层 | 占比目标 | 责任 | 工具 | 速度 |
|---|---|---|---|---|
| **单元** (Unit) | ~70% | 单文件 / 单函数 / 单组件的输入-输出契约 | vitest / jest | ms 级 |
| **集成** (Integration) | ~20% | 多文件协作 / 状态机转移 / store + component 联动 | vitest + happy-dom / @testing-library | ~100ms 级 |
| **e2e** (端到端) | ~10% | 真浏览器 / 真用户路径 / 跨端不变量 / 视觉回归 | playwright + Pixelmatch | s 级 |

底重顶轻. 反例: 99 测全是 e2e (跑得慢且不查内部 bug) 或全是单元 (盖不到 user flow).

### 2.2 状态机驱动 (核心要求)

**每个有状态的组件 / 模块必配 statemachine.md**, 列三件:
- 状态枚举 (states)
- 状态转移 (transitions, 触发事件 → 新状态)
- 不变量 (invariants, 任何状态下必满足的事实)

**测试文件按 statemachine.md 逐条写**:
- 每条转移 → 至少 1 个 Given/When/Then 测试
- 每条不变量 → 至少 1 个状态机覆盖测试 (在转移序列后断言不变量保持)

**模板**:

```markdown
<!-- tests/state-machines/<Component>.statemachine.md -->

## 状态 (states)
- closed
- opening
- opened
- closing

## 转移 (transitions)
- closed --open()--> opening
- opening --(animation done)--> opened
- opened --close() | Esc | backdrop click--> closing
- closing --(animation done)--> closed

## 不变量 (invariants)
- I-1: 同时只 1 个 .modal-backdrop 在 DOM
- I-2: opened 状态时 store.modals.<name> !== null
- I-3: closed 状态时 store.modals.<name> === null
```

**配套测试**:

```js
describe('<Component> · 状态机驱动', () => {
  it('转移 closed --open()--> opening', () => { /* Given/When/Then */ });
  it('转移 opening --(done)--> opened', () => { /* */ });
  it('转移 opened --Esc--> closing', () => { /* */ });
  it('不变量 I-1 同时只 1 backdrop', () => {
    // 模拟 open + open (重复), 断言 .modal-backdrop 数 === 1
  });
  // ...
});
```

### 2.3 红绿对照 (硬约束)

**每个测试必须经过两阶段, 才计入有效测试数**:

1. **红期** — 注入一个**已知错误** (例: 把 `expect(unit.position).toBe(toHexKey)` 改成 `toBe('wrong')`, 或注入源码 bug), 测试**必须 fail**. 证明测试有判别力.
2. **绿期** — 修复, 测试通过.

**没经过红期的测试一律视为浅断言**, 不计入覆盖. 这是 mutation testing 的人/agent 版本, 即使没装 Stryker 也强制走.

**反例**:
- `expect(component).toBeTruthy()` — 永远不 fail (除非 component 没渲染), 浅断言
- `expect(buttons.length).toBeGreaterThan(0)` — 太弱, 不查具体数
- `expect(elem.textContent).toContain('something')` — 不查精确文本

**正例**:
- `expect(unit.position).toBe('5,3')` — 注入 bug 时会 fail
- `expect(buttons).toHaveLength(3)` — 精确数
- `expect(elem.textContent).toBe('开始战斗')` — 精确文本

### 2.4 工具链硬要求

| 工具 | 干什么 | 阈值 |
|---|---|---|
| vitest --coverage (v8) | branch / line / function 覆盖率 | branch ≥ 80% |
| Stryker mutation | 自动注入代码 bug 验测试判别力 | mutation score ≥ 60% |
| Pixelmatch + playwright | 视觉回归 | diff < 5% pixel |
| ssim.js | 跨端视觉不变量 | SSIM ≥ 0.95 |
| axe-core | 可访问性 (aria / 键盘 / 对比度) | 0 violation |
| 自写 lint | 状态机覆盖 (statemachine.md 列的转移 + 不变量都对应至少 1 测) | 100% |

每条挂 CI, 任意一条 fail → PR 不可合.

---

## 三 · 实现 (本次 autochess demo 落地)

### 3.1 测试文件骨架

```
autochess-ui-agent-v7/
  vitest.config.js                          # coverage v8 配置, threshold 80%
  stryker.conf.js                           # mutation 配置, threshold 60%
  playwright.config.js                      # viewport 480x1040 固定 (deterministic)

  tests/
    state-machines/                         # 状态机文档 (按 statemachine.md 模板)
      Battlefield.statemachine.md
      MainBoardPrepare.statemachine.md
      ShopDrawer.statemachine.md
      Bench.statemachine.md
      UnitDetailModal.statemachine.md
      ... (11 组件 / 5 screen)

    components/                             # 单元 (~70%) + 集成 (~20%)
      <Component>.test.js                   # 按状态机逐条转移 / 不变量写

    e2e/                                    # e2e (~10%)
      <user-flow>.spec.js                   # 真用户路径
      cross-end-board.spec.js               # 跨端 SSIM 不变量
      visual-regression.spec.js             # Pixelmatch 视觉回归

    visual-baseline/                        # 视觉 baseline png
      main_board_prepare.png
      battle_view.png
      difficulty.png
      end_game.png

    lint/                                   # 自写 lint
      statemachine-coverage.js              # 抓 statemachine.md 转移/不变量是否都有测
```

### 3.2 红绿对照执行流程

每写一个测试:
1. 写绿期 (假设修复后通过)
2. 临时注入 bug (改源码或改测试期望) 跑红期, 验证 fail
3. 把 bug 改回, 跑绿期, 验证 pass
4. 提交时附红期截图 / 命令记录, **不附则视为未走红期, 浅断言, 重写**

可程序化版本: 用 Stryker 自动跑红期 (mutation testing), 替代手工.

### 3.3 测试文件命名约定

| 命名 | 类型 |
|---|---|
| `<Component>.test.js` | 单元 (vitest happy-dom) |
| `<Component>-state-transitions.test.js` | 集成 (状态机转移密集) |
| `<flow>.spec.js` | e2e (playwright 真浏览器) |
| `<topic>-visual.spec.js` | 视觉回归 e2e |
| `<topic>-cross-end.spec.js` | 跨端不变量 e2e |

### 3.4 CI Gate 顺序

```
PR push
  ↓
1. ESLint (含 no-restricted-syntax 锁字段)
  ↓
2. tsc --checkJs (类型校验)
  ↓
3. madge (依赖循环检测)
  ↓
4. vitest run --coverage (单元 + 集成 + 覆盖率)
   gate: branch ≥ 80%
  ↓
5. statemachine-coverage lint (转移 + 不变量都有测)
   gate: 100%
  ↓
6. playwright test (e2e + 视觉回归)
   gate: 全过 + Pixelmatch diff < 5% + SSIM ≥ 0.95
  ↓
7. axe-core (a11y)
   gate: 0 violation
  ↓
8. (可选, 异步) Stryker mutation
   gate: ≥ 60% (PR 不阻, 报告挂)
  ↓
合并
```

---

## 四 · 反例 (本次实测)

### 反例 1 · 浅断言占大头

```js
// 错: 99 测里大量这种, 永远不 fail
expect(component).toBeTruthy();
expect(buttons.length).toBeGreaterThan(0);
expect(elem.textContent).toContain('开');
```

**修法**: 改成精确断言 (toHaveLength(3) / toBe('开始战斗')), 注入 bug 应 fail.

### 反例 2 · 没装覆盖率工具

99 测全过, 但 branch / line 覆盖率没人知道. 估 ~50%, 实际可能 30%, 大片代码完全没测到.

**修法**: 装 `@vitest/coverage-v8`, threshold 80%, CI gate.

### 反例 3 · 没装 mutation testing

测试有判别力没人验. LLM 写"测试" 跟"源码" 用同一思路, 容易出"测试匹配源码错误实现" 的伪通过.

**修法**: 装 Stryker, mutation score ≥ 60%.

### 反例 4 · 状态机隐式

每个组件的状态在代码里, 没文档化, 测试 owner 凭感觉写. 漏掉转移 / 不变量没人发现.

**修法**: 每组件配 `<Name>.statemachine.md`, 测试按文档逐条写, 自写 lint 抓覆盖.

### 反例 5 · 测试用例 LLM 写, 没人审

AI IDE 自己写测试自己跑自己宣称 PASS, 用户实测才暴露漏洞. LLM 倾向写自满足条件 ("只要这个组件存在就行").

**修法**: 测试用例**手工写**或**LLM 写 + 强制红绿对照**. 没经过红期的测试不计入有效.

---

## 五 · 配套机制

| 铁律 | 联动点 |
|---|---|
| `validation_calibration_red_green_gradient.md` | 红绿对照同源, 本铁律是测试场景的具体化 |
| `cross-end-board-consistency.md` | 跨端 SSIM 不变量是 e2e 层硬要求 |
| `wysiwyg-drag-drop.md` | 玩家视角不变量 (I-1 ~ I-4) 必须挂 statemachine.md |
| `feedback_skeleton_planning_over_soft_prompt.md` (memory) | "每组件配 statemachine.md" 在规划骨架阶段锁死, 不靠 prompt 引导测试 owner 自觉 |
| `feedback_test_is_hypothesis_method.md` (memory) | 测试是假设法, 不变量是必要不充分条件 |
| `feedback_connected_is_not_discriminating.md` (memory) | "通过" ≠ "真有判别力", 必走红绿对照 |

---

## 六 · 一句话总览

测试体系三层 (单元 70 / 集成 20 / e2e 10) + 状态机驱动 (每组件配 statemachine.md, 测按转移和不变量逐条写) + 红绿对照 (每测必走红期证判别力) + 工具链硬阈 (branch ≥ 80% / mutation ≥ 60% / Pixelmatch / SSIM / a11y). 全部挂 CI, 软 prompt 不可靠, 用工具自动化锁住"测试有判别力" 这件 100% 必做的事.

<!-- [OMNI] origin=claude-code domain=docs/standards ts=2026-05-02T00:00:00Z type=standard status=active -->
<!-- [OMNI] material_id="material:standards.global.user_action_driven_testing_invariant.md" -->

# 用户操作驱动测试铁律 · User-Action-Driven Testing

> **立档时间**: 2026-05-02 · 由 autochess demo 99 浅测漏 8 真 bug 实战 + 用户原话"列清楚玩家可能做出的每一件事情" 立
>
> **适用范围**: 凡有"用户 / 玩家 / 调用方" 视角的工程, 包括 demo 项目 + omnicompany 管线 (用户明示"管线也要遵守"). 不只是 UI, API / CLI / 任何外部接口都按本铁律.
>
> **配套铁律**: `test-pyramid.md` (本铁律是其驱动方式的具体化), `validation_calibration_red_green_gradient.md` (红绿对照), `cross-end-board-consistency.md`.

---

## 一 · 问题来源 (实测漏洞)

2026-05-01 autochess demo 现状:

| 指标 | 数 |
|---|---|
| 测试总数 | 279 全过 |
| 用户实测真 bug | 12 项 |
| 测试**完全没捕**的 bug | 8 项 (商店买卡后没消失 / 拖到 A 出现在 B / 模态点 ✕ 关不掉 ...) |

**症状**: 测试盯"组件存在 / 按钮可点", 不盯"用户操作完成后看到的是合理的".

**用户原话** (2026-05-02): "我之前感觉就根本没有测过上阵, 点开详情, 关闭, 购买什么的, 只要是一个寻常人类, 都会对那个表现感到疑惑, 关键在于 LLM 包括你能否看得出来不符合设计".

这指出**软件工程行业普遍偏差**: 嘴上 BDD / user story, 实际看 line coverage 数字. 覆盖率高不等于用户操作有测.

---

## 二 · 铁律

### 2.1 测试由用户操作清单驱动

任何工程必出一份 `user-actions.md` (或 `user-stories.md`) 系统枚举:
- 用户 / 玩家 / 调用方能做的**每一件事**
- 每件事的"主路径 + 失败路径 + 边界路径" 三档
- 每件事**配 1 个 e2e 测**

**驱动方向**:
- ❌ 看代码看哪行没盖到 → 补测 (覆盖率驱动, 反模式)
- ✓ 看用户能做什么操作 → 每条 1 个测 (用户驱动)

覆盖率 / mutation score 是**工具副产物**, 是辅助参考, 不是验收标准. 验收看用户操作清单是否全覆盖.

### 2.2 测试断言精确

- ❌ `expect(component).toBeTruthy()` (永远不 fail, 浅断言)
- ❌ `expect(buttons.length).toBeGreaterThan(0)` (太弱)
- ❌ `expect(elem.textContent).toContain('开')` (不查精确文本)
- ✓ `expect(buttons).toHaveLength(3)` (精确数)
- ✓ `expect(elem.textContent).toBe('开始战斗')` (精确文本)
- ✓ `expect(unit.position).toBe('5,3')` (精确状态)

注 bug 应 fail, 修 bug 应 pass — 红绿对照基线.

### 2.3 多维度断言 (操作完成后看三处)

每条用户操作的 e2e 测必同时验:
1. **状态层** — store / 数据模型 (例 `board.size === 1`, `gold === 5`)
2. **DOM 层** — 元素出现 / 消失 / class 变化 (例 `.shop-card` 数量, `.modal--open`)
3. **视觉层** — 关键页面截图跟 baseline 对照 (Pixelmatch + 我看图判断)

三层都对才算操作真做对了. 只验状态层会漏视觉 bug, 只验 DOM 会漏状态不一致.

### 2.4 LLM 能看出不符合设计

测试输出要包含**语义信息**, 让 LLM (本框架 agent / AI IDE / 后续接手 agent) 跑测时能 spot anomalies, 不只看 PASS/FAIL:

- 测试名字写清楚"该是什么样" (Given / When / Then 完整说出预期)
- 失败时报告含具体期望 vs 实际 (例 "expected 3 cards, got 5")
- 关键页面有 baseline 截图归档, 跟现状对比时 LLM 能直接说"这个 toast 颜色不对"
- 反馈断言: 操作后 toast / hint / 错误信息符合人类期望 (不是 "Error: undefined")

---

## 三 · 实现 (本铁律落地清单)

### 3.1 工程必出文件

```
<project-root>/
  _skeleton/                          # 协作目录 (跟 skeleton-first 协议同源)
    user-actions.md                   # 用户操作清单 (本铁律核心产物)
    deferred.md                       # 延后清单 (跨项目铁律 deferred-tracked)
  tests/
    state-machines/<Component>.statemachine.md   # 组件级状态机 (test-pyramid §2.2)
    visual-baseline/                  # 视觉 baseline 截图
    e2e/<user-flow>.spec.js           # 按 user-actions 逐条写
```

### 3.2 user-actions.md 模板

```markdown
# <Project> · 用户操作清单

## 一 · 进入 (entry)

### A-1 · 用户操作: 打开页面看见首屏
- 主路径: 首页 GET 200, 首屏元素全见, 没 console error
- 失败路径: backend 挂 → 显示错误信息不空白
- 边界: 网速慢 → loading 状态显示
- e2e: tests/e2e/entry.spec.js · "首页加载" (lines 12-25)
- 状态: covered ✓ / backlog [issue-N]

### A-2 · 用户操作: 点 X 按钮
- ...
```

### 3.3 omnicompany 管线适用

用户明示"管线也要遵守". omnicompany 管线的"用户" 是调用方 (workflow auto-learn / diagnosis & repair / 业务 team), 不是终端 UI 玩家. 但同款思路:
- 管线**调用方能发起的每一种调用** → 列入 user-actions (调用清单)
- 每种调用的"主路径 / 失败 / 边界" 三档
- 每种配 1 个 integration / e2e 测
- 不只验"调用没崩", 验"调用结果符合契约" (Material schema / event log / 状态变化)

### 3.4 跟覆盖率工具的关系

可以装 vitest coverage v8 / Stryker mutation 当**辅助工具**:
- coverage 看哪些代码没跑到 (查盲区, 不是 gate)
- mutation 看测有没有判别力 (查浅断言, 不是 gate)

**禁止**把 80% / 60% 当作 PR gate. 真 gate 是 user-actions.md 全覆盖 + 红绿对照.

---

## 四 · 反例

### 反例 1 · 浅断言占大头 (autochess 99 测漏 8 真 bug)

```js
// 错: 99 测里大量这种, 永远不 fail
expect(component).toBeTruthy();
expect(buttons.length).toBeGreaterThan(0);
expect(elem.textContent).toContain('开');
```

**用户视角检验**: 这个测不能告诉我"商店买卡后真消失了吗 / 棋子真出现在我拖的 hex 上吗 / 模态真关掉了吗".

**修法**: 按 user-actions 重写, 每条玩家操作 1 个 Given/When/Then 测.

### 反例 2 · 覆盖率驱动补测

PR 加 50 行新功能, CI 报 coverage 从 82% 降到 78%, 开发者补 5 个测让 coverage 回 80%, 但补的测都是 "function exists". user-actions.md 没新增任何条目.

**修法**: 按新功能反推"用户能从此做什么操作", 加进 user-actions.md, 每条配 e2e.

### 反例 3 · 单维度断言

```js
// 错: 只验 store, 不验 DOM 跟视觉
const before = store.gold;
store.actions.SHOP_BUY('card-1');
expect(store.gold).toBe(before - 1);
// 漏: 卡有没有从 DOM 消失? 视觉上抽屉看着对吗?
```

**修法**: 加 DOM 断言 (`expect(shopCards).toHaveLength(4)`) + 视觉断言 (Pixelmatch baseline).

### 反例 4 · 失败信息不语义化

```js
expect(result).toBe(true);
// fail 时输出: "expected false to be true". LLM 看不出哪里错.
```

**修法**:
```js
expect(result, '上阵后 board 应有 unit, 但 board.size === 0').toBe(true);
// fail 时输出语义化提示, LLM 能 spot anomaly.
```

---

## 五 · 配套机制

| 铁律 | 联动点 |
|---|---|
| `test-pyramid.md` | 三层 (单元 / 集成 / e2e) 不变, 但驱动方式从覆盖率 → 用户操作清单 |
| `validation_calibration_red_green_gradient.md` | 每测必走红期 + 绿期 + 梯度 |
| `feedback_skeleton_planning_over_soft_prompt.md` | user-actions.md 是骨架的一部分, 不是软 prompt |
| `feedback_deferred_items_must_be_tracked.md` | 操作清单里没测的标 backlog, 跟 deferred 同款管理 |
| `cross-end-board-consistency.md` | 跨端不变量是用户视角能感知的事 (棋盘视觉一致), 也是用户操作 |

---

## 六 · 一句话总览

测试由"用户能做的每件事" 清单驱动 (`user-actions.md`), 不由代码 / 覆盖率指标驱动. 每操作配 e2e + 精确断言 + 多维度 (状态 + DOM + 视觉) + 红绿对照. 覆盖率工具是辅助参考, 不是验收 gate. omnicompany 管线同样适用 (调用方视角).

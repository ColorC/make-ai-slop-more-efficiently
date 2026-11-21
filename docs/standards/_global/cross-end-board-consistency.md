<!-- [OMNI] origin=claude-code domain=docs/standards ts=2026-05-02T00:00:00Z type=standard status=active -->
<!-- [OMNI] material_id="material:standards.global.cross_end_board_consistency_invariant.md" -->

# 跨端棋盘一致性铁律 · Cross-End Board Consistency

> **立档时间**: 2026-05-02 · 由 autochess demo 三端棋盘各自渲染实战踩出 · 用户 2026-05-01 立档级别
>
> **适用范围**: 凡有"同一份逻辑数据 (棋盘 / 棋子 / 地图 / 网格 / 形状) 在多个端 (前台 / 后台 / 不同应用 / iframe / demo) 渲染" 的场景, 都受本文铁律约束.
>
> **配套铁律**: `wysiwyg-drag-drop.md` (玩家视角不变量), `test-pyramid.md` (跨端不变量怎么测).

---

## 一 · 问题来源 (实测漏洞)

2026-05-01 autochess demo 用户实测发现"准备阶段棋盘和战斗阶段棋盘不是一个棋盘, 跟 battle-sim demo 棋盘也不是一个棋盘", 现状:

| 端 | 文件 | hexPos 公式 | HEX_SIZE | 渲染代码 |
|---|---|---|---|---|
| autochess 准备 | `autochess src/components/Battlefield.js` | y = ±1.5 * row (改过两次) | 28 hardcode | ~280 行独立 |
| autochess 战斗 (iframe 跑 battle-sim-web) | `battle-sim-web frontend/app.js` | y = -1.5 * row | 28 默认 | ~150 行独立 |
| battle-sim-web 独立 demo | 同上文件 | 同上 | viewport 动态 | 同上 |

三处独立 hexPos / 独立 hex polygon 渲染 / 独立 unit overlay / 独立 drop listener. spec 没说必须共享, 没人提"先去 demogame 看已有", AI IDE 写每端时都从零写. 用户拖到位置 A unit 出现在位置 B / 备战切战斗棋盘视觉反转 / 跨端视觉风格漂移 — 全是这条铁律缺失的衍生症状.

**根因**: 没立"共享同一组件" 的硬约束. 软 prompt (引导 / 提醒 / 元规则) 不可靠, 总有 agent 漏掉 (用户 2026-05-01 戳穿).

---

## 二 · 铁律 · 三端必须共享单一权威源

### 定义

任何"同一份数据多端渲染" 场景, 必须**抽出单一 package** 承担全部渲染逻辑, 所有端通过 import 使用, **不允许**任何端自写同款渲染代码.

### 硬规则

1. **单一权威源** — 一份 hexPos 公式 / 一份 HEX_SIZE / 一份 polygon 渲染 / 一份 unit overlay / 一份 drop listener. 全部归在 `packages/<shared>/` 子目录.

2. **下游端只 import 不重写** — 消费方文件 (例 `autochess Battlefield.js`, `battle-sim-web app.js`) 的渲染段必须是 `import { createBoard } from '@xxx/shared-board'` 调用, 不允许自写 hex 几何 / SVG polygon / unit overlay 代码.

3. **shared 包不依赖任何下游** — 单向依赖, shared-board 不许 import autochess / battle-sim-web 任何文件 (避免循环).

4. **入参契约锁死** — shared 包公开的 API 入参 (BoardOpts / Cell / Unit) schema 必须在类型文件 (`types.d.ts` 或 JSDoc) 显式声明, 下游端按 schema 传参.

5. **跨端不变量 CI 强制** — 视觉一致性 (SSIM ≥ 0.95) / 几何一致性 (同 cellmap 输入下两端 hex 中心坐标完全一致) 必须有 CI gate 抓.

### 实现形式

| 场景 | shared 包形式 |
|---|---|
| 多端 web 工程 | npm workspace package (`packages/<name>/`) |
| 跨语言 (前后端共享数据形状) | proto schema + 各语言代码生成 |
| 跨平台 (web / 桌面 / 移动) | 抽 core 逻辑层 + 平台 adapter 层 |
| 配置数据 | 单一 csv / yaml 源 + 各端读 |

---

## 三 · 实现 (本次 autochess demo 落地)

### 文件骨架

```
packages/shared-board/
  package.json               # npm workspace 包定义
  README.md                  # 三端使用示例 + 不变量列表
  src/
    index.js                 # 公开 export: createBoard, hexKey, parseHexKey, TILE_TYPE
    types.d.ts               # BoardOpts, HexKey, Unit, CellMapEntry, BoardEvents
    hex.js                   # 私有: hexPos / HEX_SIZE / hexPoints
    render.js                # 私有: SVG polygon + side class + wall 标识
    overlay.js               # 私有: unit overlay (依 unit.position 锚 hexKey)
    drag.js                  # 私有: drag/drop listener + 派 hex 锚事件
  tests/                     # shared 包自身的单元测
```

### 公开 API 锁死

```js
// packages/shared-board/src/index.js
export function createBoard(opts) → BoardInstance;
export const TILE_TYPE = { empty: 0, self: 1, oppo: 2, wall: 51, banned: 52 };
export function hexKey(col, row) → string;       // 例 "0,0" / "-12,-13"
export function parseHexKey(key) → { col, row };

// BoardOpts 字段锁死 (见 types.d.ts)
{ mode: 'prepare' | 'battle' | 'demo',
  cellmap: CellMapEntry[],
  units: Map<HexKey, Unit>,
  hexSize?: number,         // 默认 28, 三端必须一致
  interactive?: boolean,
  height?: number,          // viewport 比例
  onCellDrop?: (e) => void,
  onCellHover?: (e) => void }

// BoardInstance 出口
{ el: HTMLElement,
  setUnits(units),          // 反应性更新, 不重渲染整个棋盘
  setCellmap(cellmap),
  destroy() }
```

### 下游端使用模板

```js
// autochess Battlefield.js (重构后)
import { createBoard } from '@autochess/shared-board';

export function createBattlefield({ cellmap, units, mode, onCellDrop }) {
  return createBoard({
    mode, cellmap, units,
    interactive: mode === 'prepare',
    height: mode === 'prepare' ? 0.38 : 0.83,
    onCellDrop,
  });
}
// 全文件不许出现 Math.sqrt(3) / hexPos / hexPoints 这些低层 API
```

### CI Gate

| Gate | 抓什么 | 工具 |
|---|---|---|
| 禁字段使用 | autochess `Battlefield.js` 不允许 `Math.sqrt(3)` 或 `hexPos` 函数定义 | ESLint no-restricted-syntax |
| 依赖循环 | shared-board 不能 import autochess / battle-sim-web | madge / dependency-cruiser |
| 跨端视觉 | 同 cellmap 输入下 autochess 准备截图 vs battle-sim-web 战斗截图 SSIM ≥ 0.95 | Pixelmatch + ssim.js |
| 几何一致 | hexKey("0,0") 在 autochess 跟 battle-sim-web 的 SVG 中心 px 坐标完全相等 | 单元测 (跨包 import 验证) |

---

## 四 · 反例 (本次三漏洞)

### 反例 1 · 三端独立 hexPos 公式

`autochess Battlefield.js:47` 跟 `battle-sim-web frontend/app.js` 各自 `hexPos(col, row)` 函数, 甚至公式都不一致 — autochess 一开始 `y = +1.5 * row` (self 在屏顶), battle-sim-web `y = -1.5 * row` (self 在屏底). 切准备页到战斗页玩家感觉"棋盘反转".

**错误补丁**: 我 (AI IDE) 把 autochess 公式改成 `y = -1.5 * row` 跟 battle-sim-web 同步. 治标 — 下次再加一个端还会再出.

**铁律做法**: 删两份公式, 抽到 `shared-board/src/hex.js` 单一一份, 两端 import.

### 反例 2 · 三端独立 polygon SVG 渲染

`autochess Battlefield.js` 自己拼 SVG polygon, `battle-sim-web app.js` 也自己拼 SVG polygon. 两端 hex 边长 / stroke 宽度 / 颜色 token 各自定. 玩家视觉感受不一致.

**铁律做法**: 一份 polygon 渲染代码归 `shared-board/src/render.js`, CSS token 归 `shared-board` 自己的 css 文件, 下游端只能套用不能覆写.

### 反例 3 · 三端独立 drop listener

`autochess MainBoardPrepare.js` 自己绑 drop listener + 自己算"玩家拖的 px 坐标对应哪个 hex", `battle-sim-web` 不绑 (只展示战斗结果). autochess 实现里 px → hex 反推有 bug, 拖到任意位置都判到 board[0].

**铁律做法**: drop listener 归 `shared-board/src/drag.js`, px → hex 反推一份代码, 派出 `onCellDrop({ hexKey, cellEntry })` 高级语义事件, 下游端只接事件不算几何.

---

## 五 · 配套机制 (跟其他铁律联动)

| 铁律 | 联动点 |
|---|---|
| `wysiwyg-drag-drop.md` | shared-board 派出的事件含 `hexKey`, 下游 board 模型用 `Map<HexKey, Unit>` 接收, 玩家视角不变量靠 hexKey 锚 |
| `test-pyramid.md` | 跨端不变量测 (SSIM ≥ 0.95) 是 e2e 层硬要求, 不是单元层 |
| `feedback_skeleton_planning_over_soft_prompt.md` (memory) | "shared 包归属哪个 owner / 下游端只 import 不重写" 在规划骨架阶段锁死, 不靠 prompt 引导 |
| `feedback_100pct_required_goes_to_skeleton.md` (memory) | "100% 必做的事写进骨架固定环节" — 单一权威源是骨架硬约束, 不是 LLM 主观抉择 |

---

## 六 · 一句话总览

同一份数据多端渲染, 必须抽 shared 包成单一权威源, 下游只 import 不重写, 跨端不变量 (视觉 / 几何) 用 CI gate 强制. 软 prompt 不可靠, 用文件归属 + import 单向依赖 + lint 禁字段 + 视觉 SSIM gate 四件硬约束锁住.

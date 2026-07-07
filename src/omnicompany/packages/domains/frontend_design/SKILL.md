---
name: frontend_design
description: 前端设计与制作管线 —— 让前端服从一份持久稳定的规范(标尺→确定性门禁→VLM相对评审→改进闭环), 并把设计决策思路沉淀成可复用决策。两条平级子分支:dashboard 类网页(frontend_design.dashboard)与 webgame UI(frontend_design.webgame), 共用方法脊柱、各自标尺。触发词含 前端设计/前端规范/UI审查/设计门禁/dashboard设计/游戏UI/webgame设计/frontend design。
---

# frontend_design

前端**设计与制作管线**。不是一次次手改救火, 是把"让前端符合规范 + 把设计决策沉下来复用"做成可跑的管线。

## 何时用

- 要审一个前端界面是否服从设计标尺(溢出/文案/层级/密度/无障碍…) → 跑对应分支。
- 做了一次设计判断(去啰嗦文案 / 拆杂物箱 / 变体≠状态)想沉成可复用决策 → 走本域接 decisions。
- 要给某类前端立/改持久标尺 → 在对应分支的标尺真源上改, 本域只引用不复制。

## 两条子分支(共用方法, 不同标尺)

```bash
omni run frontend_design.dashboard   # dashboard 类网页(驾驶舱/poof/lofa); 标尺 = docs/projects/frontend-design/dashboard
omni run frontend_design.webgame     # webgame UI; 标尺 = tabletop-engine/README + walker specs
```

## 方法脊柱(两分支共用)

标尺 → 确定性门禁(可判定规则, 证据列表不打分) → VLM 相对评审(对基准图成对比较, 列证据不打分) → 改进闭环 → 决策沉淀(接 decisions 域, 靠 project 分流)。

## 配置

dashboard 标尺已迁入 docs/projects/frontend-design/dashboard(frostpane 仓已删); webgame 标尺留 walker/tabletop-engine 指针。决策进统一 decisions 库, 不另造。

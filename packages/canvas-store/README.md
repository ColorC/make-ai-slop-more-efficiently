# Canvas Store

画布札记存储的**唯一 TS 权威实现**（2026-08-16 彻底 TS 化裁决）。

## 职责

material-note 画布文档的文件布局与并发语义：

```
<root>/canvases/<document_id>.json   画布文档（StoredDocument 同形）
<root>/<textDir>/<text_id>.md        材料正文（分居链）
```

- 单调 revision + base_revision CAS + 幂等 mutation id + 实体级 `_entityRevisions` 冲突检测
- tmp+rename 原子写
- 卡片 material 化（命名空间 = `productRef`，写盘前经 `assertMaterialGraph` 校验）
- 与 `@omni/material-core/note` 的 `applyCanvasMutation`/`parseCanvasDocument` 同源

## 消费方（禁止第二份实现）

| 消费方 | 配置 |
|---|---|
| workshop server | `productRef: 'workshop'`, `textDir: 'texts'`, `workspaceCheck: 'strict'` |
| omnidashboard builtin 域 | `productRef: 'omnidashboard'`, `textDir: 'materials'`, `workspaceCheck: 'off'`（Python 桥历史文档无 workspace 绑定，首写认领） |

原 dashboard Python 桥内约 600 行画布契约复刻已删除，`notes_canvas_*` 改为透明转发到本实现的 HTTP 面。

## 测试

```
node --experimental-strip-types --test test/*.test.mjs
```

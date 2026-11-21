# Material Core

`Material` 是唯一一级内容对象；View 与有向关系也都是 Material。Composition、Artifact、
文档、综述等通过 labels/profile 表达。一个 Material 可以持有多个 representation。

Core 只保证结构可解析，并提供 source/expanded View resolver。过期、唯一权威、类型、
物理存储与因果正确性属于独立的 Material Governance，不是本包运行前提。

版本优先使用已有设施的原生 revision：Git commit、P4 changelist、Yjs state vector、数据库
revision 等。digest 仅可作为完整性校验或无版本设施时的降级，不是默认版本身份。

## Preset 与合并

项目自持 preset profile，Core 只提供唯一的通用展开入口：

```js
const material = applyMaterialPreset(materialWithPresetRef, projectProfile)
```

## Format protocol and lint (0.3)

`media_type` is the transport MIME type. `format_tag` is the canonical
frontend page protocol and is what selects a renderer. This distinction is
required when several representations share JSON (for example `table`,
`table-diff`, `process`, `tutorial`, and raw `json`). The registry is in `formats.js` and
includes the supported MIME/content-format combinations and renderer ids.

```js
import { lintMaterialGraph, lintCanvasDocument } from '@omni/material-core'

const materialResult = lintMaterialGraph(graph, {
  // Keep false only while reading pre-0.3 data during migration.
  requireFormatTag: true,
  // New 0.3 writes must carry classification_ref and the copy-free context
  // pointer emitted by `omni material profile show/classify`.
  requireClassification: true,
  // Host renderer descriptors may add formatTags/acceptsFormatTags.
  renderers: rendererCatalog,
})
const canvasResult = lintCanvasDocument(canvasDocument)
```

The lint is intentionally separate from `validateMaterialGraph`: schema
validation checks shape, while lint checks renderer support, MIME/tag
consistency, one primary representation, retired `material_kind` fields,
formal classification pointers/read context, and Canvas material/slice/relation
references. `renderer_hint` and Canvas `adapter` remain readable for migration
but must not become a new taxonomy.

调用方只选择带版本的 `preset_ref`；分类、首要 representation 默认值和展示默认值由 profile 展开。显式分类与 preset 冲突时拒绝，不做隐式覆盖。Profile 必须为每个 preset 提供定义来源，并拒绝重复定义。

多 Material 合并只使用 `mergeMaterialPayloads(sources, recipe)`。每个输入必须带精确 `revision_ref`，每个输出字段必须有显式策略；默认策略遇到冲突即拒绝。返回值同时包含字段级 provenance、所有输入 revision 和 display recipe，渲染端不再单独猜合并结果如何显示。

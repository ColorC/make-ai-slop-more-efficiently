# blocks — BSIDE_BLOCK 内容块契约（唯一正本）

一内核·多壳：**块标记文法只有这一份**，壳层（workshop Vue / dashboard React / …）只保留
各自的渲染器注册表，把组件挂到占位符上。渲染器是插槽，不是复制内核。

## 文法

三类标记，均以 HTML 注释形态内嵌 markdown：

1. **节级块标记**（切分文档为 prose / 块交替）：

   ```
   <!--BSIDE_BLOCK:<URI 编码的 JSON 属性>-->\n…正文…\n<!--/BSIDE_BLOCK-->
   ```

   - 属性 JSON：`{ id: string(必填，trim 后非空), size?: 'compact'|'medium'|'large'|'full',
     filter?, kind?, source?, sourceId?, sources?: [{ id, label, source, version?, status? }] }`。
   - 属性载荷不得含 `>`（文法用 `[^>]+` 截断）；`size` 非法回退 `medium`；`sources`
     条目缺 `id/label/source` 即丢弃，过滤后为空则整个字段省略。
   - **不支持嵌套**：闭合取首个 `<!--/BSIDE_BLOCK-->`（非贪婪），内层开标记按字面留在正文。
   - 属性畸形（坏 JSON / 坏 URI 编码 / 缺 id）→ 该块退回 prose 节，内容不丢。
2. **行内标记**（块内/文内的占位符，交给壳的渲染器注册表）：

   ```
   <!--MARKER:payload-->
   ```

   - `MARKER` = `[A-Z][A-Z0-9_]+`（如 `BSIDE_STORY_SIMULATOR`、`WORKSHOP_MERMAID`、
     `BSIDE_DEMO`、`BSIDE_DEMO_EMBED`、`*_JSON_TABLE`——marker 种类不设上限，注册表决定）。
   - `payload` 不得含 `>`；约定为 URI 编码（JSON 或裸串，解码由各渲染器的 parse 自理）。
   - 未注册的标记不切分，原样留在 markdown 文段里。
3. **来源边界标记**（渲染前剥除，不产出任何占位符）：

   ```
   <!--BSIDE_BLOCK_SOURCE[:payload]--> / <!--/BSIDE_BLOCK_SOURCE[:payload]-->
   ```

**围栏映射**：` ```mermaid ` 代码围栏等价于 `WORKSHOP_MERMAID:<URI 编码的围栏代码(trim 后)>`
标记，进同一占位符管线。

## markdown 渲染管线集成方式

本模块采用**前置切分 + 分段渲染（后处理位）**，而不是 markdown-it 插件：

- `renderBlockDocument(content, { prefix, title, resolveMarker, renderMarkdown })` 先按文法
  把文档切成节与段，标记**不进入** markdown-it 的解析链；
- markdown-it 只渲染纯文段（`renderMarkdown(source, title?)` 回调由壳提供，可自带
  footnote/typographer/表格包装等外观配置——这属于壳的定制层）；
- 壳拿到 `{ key, block?, segments[] }`，对 `type: 'marker'` 段查注册表挂组件。

选择理由：标记保持 HTML 注释原文、由正则契约统一处理，markdown-it 侧零侵入，
消费方（含免打包器宿主）不必共享同一个 markdown-it 实例或插件版本。

## API

| 导出 | 用途 |
|---|---|
| `decodeBlockSpec(encoded)` | 解码节级块属性；畸形返回 `null` |
| `parseContentSections(content, prefix)` | 节切分（prose/块交替，含畸形回退） |
| `stripSourceMarkers(text)` | 剥除 BSIDE_BLOCK_SOURCE 开/闭标记 |
| `expandMermaidFences(content)` | mermaid 围栏 → WORKSHOP_MERMAID 标记 |
| `splitMarkerSegments(body, opts)` | 行内标记切分（注册表插槽 `resolveMarker`） |
| `renderBlockDocument(content, opts)` | 整文档占位符管线（节切分 + 行内切分） |

## 兼容性

只增不改：不触碰 render.js / obsidian-md 既有行为；walker-game / tabletop-engine 等
既有消费方零影响（不引入新依赖，纯函数模块）。

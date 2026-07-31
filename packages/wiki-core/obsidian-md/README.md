---
tags:
  - 格式/readme
  - 操作对象/kb-md
  - 操作对象/html-app
  - 使用者/AI开发者
---

# @user/obsidian-md

obsidian 风格 markdown 渲染 + vault 索引. 浏览器可跑. 给 `tool/wiki-viewer` 跟以后任何要消费 AIWorkSpace markdown wiki 的工具用.

## 提供什么

| 模块 | 用途 | 跑在 |
|------|------|------|
| `createRenderer(opts)` | 配好的 markdown-it 实例, 吃 obsidian 风格语法吐 HTML | 浏览器 + Node |
| `buildIndex({ roots })` | 扫一个或多个 vault 目录, 出 JSON 索引 (文件清单 / 反链 / 标签 / 头部锚点 / outgoing 链接) | Node only |
| `obsidianSyntax(md, opts)` | markdown-it 插件, 上面 renderer 内部用的, 也可单独装到自己的 markdown-it 上 | 浏览器 + Node |
| `mermaidFence(md)` | markdown-it 插件, 把 ```mermaid 块改成 `<pre class="mermaid">` 给前端 mermaid.js 接 | 浏览器 + Node |

## 支持的 obsidian 语法

| 语法 | 例 | 说明 |
|------|---|------|
| wikilink | `[[页面]]` `[[页面\|别名]]` `[[页面#标题]]` `[[页面#^block-id]]` | 通过 `resolveLink` 回调解析到 href |
| 嵌入 | `![[image.png]]` `![[image.png\|400x300]]` | 图片/视频/音频/PDF 各按扩展名 |
| 嵌入笔记 | `![[other-note]]` `![[other-note#section]]` | 留 `<div class="transclude">` 占位, 调用方拉取目标内容填充 |
| callout | `> [!note]` `> [!warning]+` `> [!tldr]- title` | 折叠/展开 标记保留 |
| 高亮 | `==text==` | → `<mark>` |
| 注释 | `%% 不渲染 %%` | 直接清掉 |
| 标签 | `#tag` `#嵌套/标签` | 通过 `resolveTag` 回调 |
| 块引用 | 行尾 ` ^block-id` | 渲染成锚点 `<a id="block-X" class="block-anchor">` |
| 脚注 | `[^1]` + `[^1]: note` | markdown-it-footnote |
| 任务列表 | `- [ ]` `- [x]` | markdown-it-task-lists |
| 数学 | `$x$` `$$x$$` | KaTeX |
| 代码块 | ```` ```lang ```` | markdown-it 内置 + mermaid 特例 |
| Mermaid | ```` ```mermaid ```` | 输出 `<pre class="mermaid">`, 前端调 `mermaid.run()` |

## API

### createRenderer

```js
import { createRenderer } from '@user/obsidian-md';

const md = createRenderer({
  resolveLink: (name, anchor, alias) => ({
    href: `#/page/${encodeURIComponent(name)}${anchor}`,
    text: name,
    broken: !indexHas(name),
  }),
  resolveAsset: (path) => ({ href: `/api/asset?path=${encodeURIComponent(path)}` }),
  resolveEmbed: (name, anchor) => ({ href: `#/page/${name}${anchor}`, targetSlug: name }),
  resolveTag: (tag) => ({ href: `#/tag/${encodeURIComponent(tag)}` }),
});

const html = md.render(rawMarkdown);
```

### buildIndex

```js
import { buildIndex } from '@user/obsidian-md';

const index = await buildIndex({
  roots: [
    { name: '系统组', path: 'C:/workspace/AIWorkSpace/策划通用/系统组/docs/wiki' },
    { name: '任务组', path: 'C:/workspace/AIWorkSpace/策划通用/任务组/docs/wiki' },
  ],
  exclude: ['_audit', '.obsidian'],
});

// index.vaults[0].files       — 全文件列表 + frontmatter / headings / outgoing / tags
// index.vaults[0].byBasename  — basename → [path] 用于 [[X]] 解析
// index.vaults[0].tags        — tag → [path]
// index.vaults[0].backlinks   — path → [caller path] (反链)
// index.vaults[0].stats       — { mdCount, assetCount, totalLinks, brokenLinks }
```

## 不做什么

- DataView / DataViewJS — 太重 + 实时性硬要客户端跑全 vault 索引, 不在范围
- Bases (obsidian 1.9+) — 同上, 等 obsidian 生态稳定再考虑
- canvas `.canvas` 文件 — 暂未支持 (未来可加)
- 编辑器功能 — 这是只读阅读器

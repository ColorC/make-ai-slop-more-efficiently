// wiki-core 渲染烟测：Obsidian 语法逐项断言（wikilink/别名/锚点/嵌入/callout/高亮/标签/任务列表）。
// 跑: node test/render-smoke.mjs
import matter from "gray-matter";
import { createRenderer } from "../obsidian-md/index.js";

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  console.log(`${cond ? "✓" : "✗"} ${name}${cond ? "" : `  → ${extra ?? ""}`}`);
  if (cond) pass++; else fail++;
}

const md = createRenderer({
  resolveLink: (name, anchor, alias) => ({
    href: `#/wiki/${encodeURIComponent(name)}${anchor ? `?h=${encodeURIComponent(anchor)}` : ""}`,
    text: alias ?? name,
    broken: name === "不存在的页",
  }),
  resolveAsset: (path) => ({ href: `/assets/${path}` }),
  resolveEmbed: (name) => ({ href: `#/wiki/${encodeURIComponent(name)}`, targetSlug: name, broken: false }),
  resolveTag: (tag) => ({ href: `#tag/${encodeURIComponent(tag)}` }),
});

const sample = `---
tags:
  - 测试/烟测
---

# 标题一

普通 [[愿]] 双链、别名 [[愿|老板娘]]、带锚点 [[酒馆#出发门]]，断链 [[不存在的页]]。

==高亮文本== 与 %%隐藏注释%% 与 #酒馆 标签。

> [!note] 提示
> callout 正文。

![[tavern.png|200x100]]

- [x] 已完成任务
- [ ] 未完成任务
`;

// 消费契约（与 wiki-viewer 一致）：渲染前用 gray-matter 剥离 frontmatter，元数据另行展示。
const { content, data } = matter(sample);
const html = md.render(content);

ok("wikilink 渲染为链接", html.includes('href="#/wiki/%E6%84%BF"') && html.includes(">愿<"));
ok("别名显示文本", html.includes(">老板娘<"));
ok("锚点拼进 href", /酒馆[^"]*\?h=/.test(decodeURIComponent(html)));
ok("断链有 broken 标记", /broken/.test(html), html.match(/class="[^"]*broken[^"]*"/)?.[0]);
ok("==高亮== 渲染", /<mark>高亮文本<\/mark>/.test(html));
ok("%%注释%% 不出现在输出", !html.includes("隐藏注释"));
ok("#标签 渲染为链接", html.includes('href="#tag/'));
ok("callout 容器渲染", /callout/.test(html) && html.includes("callout 正文"));
ok("图片嵌入走 resolveAsset", html.includes('src="/assets/tavern.png"'));
ok("任务列表渲染 checkbox", /checkbox/.test(html));
ok("frontmatter 不泄漏进正文", !html.includes("tags:"));
ok("frontmatter 解析为元数据", Array.isArray(data.tags) && data.tags[0] === "测试/烟测");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

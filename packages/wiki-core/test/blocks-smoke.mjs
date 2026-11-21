// blocks 扩展烟测：BSIDE_BLOCK 文法逐项断言（正常/畸形/嵌套/转义/占位符管线）。
// 跑: node test/blocks-smoke.mjs
import {
  decodeBlockSpec, expandMermaidFences, parseContentSections, renderBlockDocument,
  splitMarkerSegments, stripSourceMarkers,
} from "../blocks/index.js";

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  console.log(`${cond ? "✓" : "✗"} ${name}${cond ? "" : `  → ${extra ?? ""}`}`);
  if (cond) pass++; else fail++;
}
function eq(name, actual, expected) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  ok(name, a === e, `actual=${a} expected=${e}`);
}

const enc = (spec) => encodeURIComponent(JSON.stringify(spec));
const md = (source, title) => `<md:${JSON.stringify(source)}|title=${JSON.stringify(title ?? null)}>`;
const handler = { id: "story", parse: (payload) => ({ raw: payload }) };
const resolve = (marker) => (marker === "BSIDE_STORY_SIMULATOR" ? handler : undefined);

// —— 节级块：正常 ——
{
  const content = `前文\n\n<!--BSIDE_BLOCK:${enc({ id: "A·机制图", size: "large" })}-->\n块正文\n<!--/BSIDE_BLOCK-->\n\n后文`;
  const sections = parseContentSections(content, "p");
  eq("正常：prose/块/prose 三节", sections.map((s) => [s.key, s.block?.id ?? null]), [
    ["p-prose-0", null], ["p-block-1", "A·机制图"], ["p-prose-2", null],
  ]);
  eq("正常：块节携带解码属性", sections[1]?.block, { id: "A·机制图", size: "large" });
  eq("正常：块正文原样保留", sections[1]?.body, "\n块正文\n");
}

// —— 属性解码：字段与回退 ——
{
  eq("属性：size 非法回退 medium", decodeBlockSpec(enc({ id: "x", size: "huge" }))?.size, "medium");
  eq("属性：size 缺省为 medium", decodeBlockSpec(enc({ id: "x" }))?.size, "medium");
  eq("属性：id 两端空白被 trim", decodeBlockSpec(enc({ id: "  x  " }))?.id, "x");
  eq("属性：可选字符串字段透传", decodeBlockSpec(enc({ id: "x", kind: "story-simulator", source: "TsConfig/A.json", sourceId: "s1", filter: "f" })),
    { id: "x", size: "medium", filter: "f", kind: "story-simulator", source: "TsConfig/A.json", sourceId: "s1" });
  eq("属性：sources 合法条目通过", decodeBlockSpec(enc({ id: "x", sources: [
    { id: "a", label: "甲", source: "s://a", version: "v1", status: "active" },
    { id: "b", label: "乙", source: "s://b", status: "bogus" },
  ] }))?.sources, [
    { id: "a", label: "甲", source: "s://a", version: "v1", status: "active" },
    { id: "b", label: "乙", source: "s://b" },
  ]);
  eq("属性：sources 全部不合法则省略", decodeBlockSpec(enc({ id: "x", sources: [{ id: "a" }, "junk", 3] })), { id: "x", size: "medium" });
  eq("属性：非对象条目被丢弃", decodeBlockSpec(enc({ id: "x", sources: [null, [], 42, { id: "ok", label: "l", source: "s" }] }))?.sources, [{ id: "ok", label: "l", source: "s" }]);
}

// —— 畸形回退 ——
{
  const bad = `前\n<!--BSIDE_BLOCK:not-json-->块正文\n<!--/BSIDE_BLOCK-->\n后`;
  eq("畸形：坏 JSON 退回 prose（标记仍成节、正文保留）", parseContentSections(bad, "p").map((s) => s.key),
    ["p-prose-0", "p-prose-1", "p-prose-2"]);
  eq("畸形：坏 JSON 的节正文是块内体（标记文本被消费）", parseContentSections(bad, "p")[1]?.body, "块正文\n");
  const badUri = `<!--BSIDE_BLOCK:%7B%22id%22%%zz-->x<!--/BSIDE_BLOCK-->`;
  eq("畸形：坏 URI 编码退回 prose", parseContentSections(badUri, "p").length, 1);
  eq("畸形：缺 id 退回 prose", parseContentSections(`<!--BSIDE_BLOCK:${enc({ size: "large" })}-->x<!--/BSIDE_BLOCK-->`, "p")[0]?.block, undefined);
  eq("畸形：空 id 退回 prose", parseContentSections(`<!--BSIDE_BLOCK:${enc({ id: "  " })}-->x<!--/BSIDE_BLOCK-->`, "p")[0]?.block, undefined);
  eq("畸形：JSON 标量载荷退回 prose", parseContentSections(`<!--BSIDE_BLOCK:${encodeURIComponent("42")}-->x<!--/BSIDE_BLOCK-->`, "p")[0]?.block, undefined);
  eq("畸形：缺闭合标记不匹配（整体一节）", parseContentSections(`<!--BSIDE_BLOCK:${enc({ id: "x" })}-->没有闭合`, "p").length, 1);
  const gt = `<!--BSIDE_BLOCK:${enc({ id: "x" })}>截断-->y<!--/BSIDE_BLOCK-->`;
  ok("文法：属性载荷含 > 则不构成节标记", parseContentSections(gt, "p").every((s) => !s.block));
}

// —— 嵌套（首个闭合生效，不支持语义嵌套） ——
{
  const nested = `A<!--BSIDE_BLOCK:${enc({ id: "外" })}-->内开头<!--BSIDE_BLOCK:${enc({ id: "内" })}-->重叠<!--/BSIDE_BLOCK-->尾部`;
  const sections = parseContentSections(nested, "p");
  eq("嵌套：外层块吃掉首个闭合", [sections[1]?.block?.id, sections[1]?.body], ["外", "内开头<!--BSIDE_BLOCK:" + enc({ id: "内" }) + "-->重叠"]);
  eq("嵌套：闭合后的余文是 prose", sections[2]?.key, "p-prose-2");
  const unclosedInner = `A<!--BSIDE_BLOCK:${enc({ id: "外" })}-->前<!--BSIDE_BLOCK:${enc({ id: "内" })}-->后<!--/BSIDE_BLOCK-->B`;
  eq("嵌套：未闭合内层开标记按字面留在块正文", parseContentSections(unclosedInner, "p")[1]?.body?.includes("<!--BSIDE_BLOCK:"), true);
}

// —— 来源边界标记剥除 ——
{
  eq("来源标记：开/闭/带载荷全剥", stripSourceMarkers("a<!--BSIDE_BLOCK_SOURCE-->b<!--/BSIDE_BLOCK_SOURCE-->c<!--BSIDE_BLOCK_SOURCE:x-->d"), "abcd");
  eq("来源标记：载荷含 > 不剥（与原行为一致）", stripSourceMarkers("a<!--BSIDE_BLOCK_SOURCE:x>y-->b"), "a<!--BSIDE_BLOCK_SOURCE:x>y-->b");
}

// —— mermaid 围栏映射 ——
{
  eq("围栏：mermaid → WORKSHOP_MERMAID（trim）", expandMermaidFences("```mermaid\n  graph TD; A-->B;  \n```"),
    `<!--WORKSHOP_MERMAID:${encodeURIComponent("graph TD; A-->B;")}-->`);
  eq("围栏：非 mermaid 围栏不动", expandMermaidFences("```js\nx\n```"), "```js\nx\n```");
  eq("围栏：多围栏逐个映射", (expandMermaidFences("```mermaid\na\n```\n文字\n```mermaid\nb\n```").match(/WORKSHOP_MERMAID/g) ?? []).length, 2);
  eq("围栏：围栏内的 --> 不越界截断载荷", decodeRoundtrip(), "flowchart TD\n    A[x] --> B");
  function decodeRoundtrip() {
    const m = expandMermaidFences("```mermaid\nflowchart TD\n    A[x] --> B\n```").match(/WORKSHOP_MERMAID:([^>]+)-->/);
    return m ? decodeURIComponent(m[1] ?? "") : null;
  }
}

// —— 行内标记切分（占位符契约） ——
{
  const body = "导语\n\n<!--BSIDE_STORY_SIMULATOR:payload1-->\n\n收尾";
  const segs = splitMarkerSegments(body, { prefix: "s", title: "T", resolveMarker: resolve, renderMarkdown: md });
  eq("行内：md/marker/md 三段", segs.map((s) => [s.type, s.key]), [["markdown", "s-md-0"], ["marker", "s-story-1"], ["markdown", "s-md-2"]]);
  eq("行内：首个文段携带 title，其余不带", segs.filter((s) => s.type === "markdown").map((s) => s.html),
    [md("导语\n\n", "T"), md("\n\n收尾", undefined)]);
  eq("行内：marker 段携带标记名与 parse 产物", segs[1], { type: "marker", key: "s-story-1", marker: "BSIDE_STORY_SIMULATOR", props: { raw: "payload1" } });

  const markerFirst = "<!--BSIDE_STORY_SIMULATOR:p1-->之后";
  eq("行内：节以 marker 开头则首个文段不携带 title", splitMarkerSegments(markerFirst, { prefix: "s", title: "T", resolveMarker: resolve, renderMarkdown: md })
    .filter((s) => s.type === "markdown").map((s) => s.html), [md("之后", undefined)]);

  const unknown = "前<!--UNKNOWN_MARKER:xxx-->后<!--BSIDE_STORY_SIMULATOR:p2-->";
  const segs2 = splitMarkerSegments(unknown, { prefix: "s", resolveMarker: resolve, renderMarkdown: md });
  ok("行内：未注册标记留在文段内", segs2[0]?.type === "markdown" && segs2[0].html.includes("<!--UNKNOWN_MARKER:xxx-->"));
  eq("行内：未注册标记不推进切分游标", segs2.map((s) => s.type), ["markdown", "marker"]);

  eq("行内：相邻 marker 之间无文段", splitMarkerSegments("<!--BSIDE_STORY_SIMULATOR:a--><!--BSIDE_STORY_SIMULATOR:b-->", { prefix: "s", resolveMarker: resolve, renderMarkdown: md }).length, 2);
  eq("行内：无 marker 纯文（title 生效）", splitMarkerSegments("只有文", { prefix: "s", title: "T", resolveMarker: resolve, renderMarkdown: md }),
    [{ type: "markdown", key: "s-md-0", html: md("只有文", "T") }]);
  eq("行内：payload 含 > 不构成标记", splitMarkerSegments("a<!--BSIDE_STORY_SIMULATOR:x>y-->b", { prefix: "s", resolveMarker: resolve, renderMarkdown: md }).length, 1);
  eq("行内：mermaid 围栏并入切分", splitMarkerSegments("```mermaid\nA-->B\n```", { prefix: "s", resolveMarker: (m) => (m === "WORKSHOP_MERMAID" ? { id: "mermaid", parse: (p) => ({ code: decodeURIComponent(p) }) } : undefined), renderMarkdown: md })[0]?.props,
    { code: "A-->B" });
}

// —— 整文档占位符管线 ——
{
  const content = `# T\n\n导语\n\n<!--BSIDE_BLOCK:${enc({ id: "块", kind: "story-simulator" })}-->\n<!--BSIDE_STORY_SIMULATOR:in-->块内\n<!--/BSIDE_BLOCK-->\n`;
  const out = renderBlockDocument(content, { prefix: "m-0", title: "T", resolveMarker: resolve, renderMarkdown: md });
  eq("管线：节结构与块属性", [out.length, out[1]?.key, out[1]?.block], [2, "m-0-block-1", { id: "块", size: "medium", kind: "story-simulator" }]);
  eq("管线：每节各自切分", out[1]?.segments.map((s) => s.type), ["marker", "markdown"]);
  ok("管线：marker 打头的节内文段不带 title（与壳内原行为一致）", out[1]?.segments[1]?.html === md("块内\n", undefined));
  ok("管线：prose 打头的节首个文段带 title", out[0]?.segments[0]?.html === md("# T\n\n导语\n\n", "T"));
  eq("管线：空文档回退单 prose 节（零段）", renderBlockDocument("", { prefix: "p", renderMarkdown: md }), [{ key: "p-prose-0", block: undefined, segments: [] }]);
}

console.log(fail ? `\n${fail} FAILED (${pass} passed)` : `\nAll ${pass} blocks assertions passed`);
process.exit(fail ? 1 : 0);

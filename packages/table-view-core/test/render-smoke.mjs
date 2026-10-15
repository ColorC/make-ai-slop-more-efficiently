import assert from "node:assert/strict";
import { semanticDiff } from "../diff.js";
import { renderTableHtml, TABLE_VIEW_LIMITS, TABLE_VIEW_UI } from "../render.js";
const TABLE_VIEW_UI_KEYS = Object.keys(TABLE_VIEW_UI);

function doc(rows, extra = {}) {
  return {
    schema: "omni.table-doc/v1",
    source: { adapter: "test", locator: "mem://render?probe=<&>\u2028\u2029" },
    table: {
      name: "RenderFixture",
      header_layout: { rows: 3, roles: ["label", "type", "name"], data_start: 4 },
      columns: [
        { name: "ID", label: "编号", type: "KN", key: true },
        { name: "Value", label: "普通值", type: "N" },
        { name: "Formula", label: "公式", type: "N" },
        { name: "Result", label: "公式结果", type: "N" },
        { name: "Source", label: "源侧表示", type: "S" },
      ],
      key_fields: ["ID"],
      rows,
      ...extra,
    },
  };
}

const before = doc([
  { cells: {
    ID: { v: 1 },
    Value: { v: 10 },
    Formula: { v: 20, f: "=B1*2", fv: 20 },
    Result: { v: 30, f: "=B1*3", fv: 30 },
    Source: { v: 1, sv: "01" },
  } },
  { cells: { ID: { v: 2 }, Value: { v: 5 } } },
]);

const after = doc([
  { cells: {
    ID: { v: 1 },
    Value: { v: 11 },
    Formula: { v: 20, f: "=B1+10", fv: 20 },
    Result: { v: 33, f: "=B1*3", fv: 33 },
    Source: { v: 1, sv: "1" },
  } },
  { cells: { ID: { v: 3 }, Value: { v: 8 }, Formula: { v: 16, f: "=B3*2", fv: 16 } } },
]);

const diff = semanticDiff(before, after);
assert.deepEqual(
  Object.values(diff.updated[0].changes).map((change) => change.kind).sort(),
  ["formula", "formula-result", "source", "value"].sort(),
);

const html = renderTableHtml({
  id: "render-fixture-17",
  written_at: "2026-08-18T20:00:00+08:00",
  title: "渲染冒烟 <安全>",
  docs: [{
    doc: before,
    diff,
    references: {
      table: { label: 'RenderFixture', material_ref: 'table:demogame/RenderFixture', href: '/materials?id=table%3Aigame%2FRenderFixture' },
      fields: { Value: { label: 'RenderFixture.Value', material_ref: 'value:demogame/RenderFixture/Value', href: '/materials?id=value%3Aigame%2FRenderFixture%2FValue', description: '普通值字段说明' } },
    },
  }],
});

assert.match(html, /^<!doctype html>/);
assert.ok(html.includes("render-fixture-17"), "serialized report keeps the program-written report ID as provenance");
assert.ok(html.includes("2026-08-18T20:00:00+08:00"), "serialized report keeps the program-written timestamp as provenance");
assert.ok(!html.includes("哈希"), "review UI does not expose implementation hashes");
assert.ok(!html.includes("tvc-topbar"), "diff renderer has no report/meta/mode top panel");
assert.ok(html.includes("tvc-mode") && html.includes("ui.diffOnly") && html.includes("ui.allRows"), "diff renderer keeps only the compact diff/full switch");
assert.ok(html.includes("state.showAll"), "diff/full mode is tracked per table");
const defaultAllHtml = renderTableHtml({ title: "default-all", default_mode: "all", docs: [{ doc: before, diff }] });
assert.ok(defaultAllHtml.includes('"default_mode":"all"'), "reports can request the contextual full-table view by default");
// 2026-09-16: 初始模式判据搬进 initialShowAllIndices（同时处理「带 diff 壳但零变更标记」的表，
// 见 diff-shell-smoke）。行为不变，这条源码断言跟着实现对齐。
assert.ok(defaultAllHtml.includes("initialShowAllIndices(docs, data.default_mode)"), "runtime derives each table's initial mode from the report");
assert.ok(defaultAllHtml.includes('defaultMode === "all"'), "runtime initializes every table in full mode when requested");
assert.ok(defaultAllHtml.includes("isEmptyDiffShell"), "runtime ships the empty-diff-shell guard so real rows are never hidden");
assert.ok(html.includes("table:demogame/RenderFixture") && html.includes("value:demogame/RenderFixture/Value"), "table and field Wiki Material references survive standalone rendering");
assert.ok(html.includes("tvc-wiki-field") && html.includes("reference.description"), "field headers consume Wiki references for jump and hover text");
assert.ok(html.includes('roles: ["name", "type"]'), "field headers render canonical names followed by source-declared types");
assert.ok(!html.includes("tvc-reference-bar") && !html.includes("tvc-field-reference") && !html.includes("tvc-column-toggle"), "diff surface has no guide or column-control panels");
assert.ok(html.includes('column.name !== "__p4_key"'), "internal P4 identity column is never rendered");
assert.ok(html.includes("tvc-fx-badge"), "HTML carries the formula badge renderer");
for (const kind of ["value", "formula", "formula-result", "source"]) {
  assert.ok(html.includes(`tvc-kind-${kind}`), `HTML carries ${kind} diff class`);
  assert.ok(html.includes(`\"kind\":\"${kind}\"`), `inline REPORT carries ${kind} diff data`);
}
assert.ok(!html.includes(["__", "PLACEHOLDER", "__"].join("")), "no unresolved placeholder remains");
assert.ok(!html.includes("渲染冒烟 <安全>"), "script-sensitive title characters are escaped");
assert.ok(html.includes("mem://render?probe=\\u003c\\u0026\\u003e\\u2028\\u2029"), "inline JSON escapes &, <, >, U+2028, and U+2029");
assert.ok(!html.includes("\u2028") && !html.includes("\u2029"), "inline scripts contain no raw JavaScript line separators");
assert.equal(renderTableHtml({ id: "stable-1", written_at: "2026-08-18T20:00:00+08:00", title: "stable", docs: [{ doc: before, diff }] }), renderTableHtml({ id: "stable-1", written_at: "2026-08-18T20:00:00+08:00", title: "stable", docs: [{ doc: before, diff }] }), "renderer is deterministic and pure");
assert.ok(!/\b(?:src|href)=["']https?:/i.test(html), "standalone HTML has no external URL");
assert.ok(!/\bfetch\s*\(/.test(html), "standalone HTML performs no fetch");

const tooManyRows = Array.from({ length: TABLE_VIEW_LIMITS.rows + 1 }, (_, index) => ({
  cells: { ID: { v: index + 1 }, Value: { v: index } },
}));
const gated = renderTableHtml({ title: "capacity", docs: [{ doc: doc(tooManyRows) }] });
assert.ok(gated.includes('"capacity_error":{"rows":10001,"cells":50005}'), "capacity gate is serialized as an error card model");
assert.ok(!gated.includes('"v":10000'), "over-capacity rows are not embedded into standalone HTML");

console.log("render-smoke: all assertions passed");

// ── 单元格引用元数据（onCellSelect 的 DOM 地基）──────────────────────
{
  const html2 = renderTableHtml({ id: "meta-1", title: "meta", docs: [{ doc: after, diff }] });
  // 静态页内嵌的是运行时函数源码（DOM 在浏览器构建），断言打在源码上
  if (!html2.includes("dataset.field")) throw new Error("runtime must stamp cell dataset.field");
  if (!html2.includes("dataset.key")) throw new Error("runtime must stamp row dataset.key");
  if (!html2.includes("dataset.changeKind")) throw new Error("runtime must stamp change kind");
  console.log("render-smoke: cell reference metadata ok");
}

// ── 表格标题：基于哪个版本 + 右键复制本机 / P4 链接（2026-09-17）──────────
{
  const titled = {
    ...before,
    source: {
      adapter: "spark-config-preview", locator: "RedDot",
      basis: "基于 //streams/main/Design/excel/RedDot.xlsm#11（CL 15706）",
      links: [
        { kind: "local", label: "复制本机路径（L-HOST）", value: "E:\P4\spark-user\Design\excel\RedDot.xlsm" },
        { kind: "p4", label: "复制 P4 路径", value: "//streams/main/Design/excel/RedDot.xlsm#11" },
      ],
    },
  };
  const html3 = renderTableHtml({ id: "title-1", title: "title", docs: [{ doc: titled, diff }] });
  if (!html3.includes("CL 15706")) throw new Error("table basis must survive standalone rendering");
  if (!html3.includes("//streams/main/Design/excel/RedDot.xlsm#11")) throw new Error("table links must survive standalone rendering");
  if (!html3.includes("hasTitleBar")) throw new Error("single-table reports with a basis must still show the title bar");
  if (!html3.includes('"contextmenu"') || !html3.includes("tvc-context-menu")) throw new Error("tab title must open a copy menu on right click");
  if (!html3.includes("ui.copied") || !TABLE_VIEW_UI_KEYS.includes("copied")) throw new Error("copy feedback string must exist");
  console.log("render-smoke: table title basis + copy links ok");
}

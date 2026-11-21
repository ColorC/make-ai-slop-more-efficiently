// 2026-09-16 回归：用户裁定「没有变更行不是坏了，但这不合理」。
// 带 diff 壳、行却一条变更标记都没有的表，原先停在「仅差异」被过滤成空，
// 界面手里有行却显示"当前表没有变更行"。判据必须把这种表初始落到「全量」。
import assert from "node:assert/strict";
import { createTableViewRuntime, renderTableHtml } from "../render.js";

const host = { document: { createElement: () => ({ classList: { add() {} }, appendChild() {}, style: {} }) } };
const { isEmptyDiffShell, initialShowAllIndices } = createTableViewRuntime(host, {});

const planRows = [
  { cells: { 表: { v: "Activity" }, 主键: { v: "109" }, 字段: { v: "RedDotMain" } } },
  { cells: { 表: { v: "RedDot" }, 主键: { v: "入口+两个子节点" }, 字段: { v: "AffectRedDot" } } },
];
const table = { name: "计划写入（尚未落地）", columns: [{ name: "表" }, { name: "主键" }, { name: "字段" }], rows: planRows };

// 空 diff 壳 + 有行 → 判为 true，初始全量
const emptyShell = { diff: { created: [], changed: [], deleted: [] }, doc: { table } };
assert.equal(isEmptyDiffShell(emptyShell), true, "空 diff 壳且有行应判 true");
assert.deepEqual(initialShowAllIndices([emptyShell]), [0], "空 diff 壳应初始为全量");

// 真有变更标记 → 不动，仍按「仅差异」
const realDiff = { diff: { created: [], changed: [{ key: { 表: { v: "RedDot" } }, changes: {} }], deleted: [] }, doc: { table } };
assert.equal(isEmptyDiffShell(realDiff), false, "有变更标记不应判为空壳");
assert.deepEqual(initialShowAllIndices([realDiff]), [], "有变更标记应保持仅差异");

// 没有 diff 的普通表 → 不是空壳（本来就显示全部行）
assert.equal(isEmptyDiffShell({ doc: { table } }), false, "无 diff 的表不是空壳");

// 空 diff 壳但一行都没有 → 不是空壳（该显示「当前表没有数据」）
assert.equal(
  isEmptyDiffShell({ diff: { created: [], changed: [], deleted: [] }, doc: { table: { ...table, rows: [] } } }),
  false,
  "没有行时不应判为空壳",
);

// default_mode=all 仍然全部全量
assert.deepEqual(initialShowAllIndices([realDiff, emptyShell], "all"), [0, 1], "default_mode=all 应全部全量");

// 独立 HTML 仍可生成：运行时被 toString 序列化，判据必须在闭包内，否则那份 HTML 会 ReferenceError
const html = renderTableHtml({ title: "diff shell", docs: [emptyShell] });
assert.ok(html.includes("isEmptyDiffShell"), "判据必须随运行时序列化进独立 HTML");
assert.ok(html.includes("initialShowAllIndices"), "初始模式判据必须随运行时序列化");

console.log("diff-shell-smoke: all assertions passed");

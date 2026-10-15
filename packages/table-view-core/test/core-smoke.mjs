import assert from "node:assert/strict";
import {
  applyChangeSet,
  canonicalJson,
  ChangeSetError,
  classifyCellChange,
  contractHash,
  DiffError,
  diffsExactlyEqual,
  equals,
  expectedDiff,
  isEmptyDiff,
  keyString,
  normalizeChangeSet,
  semanticDiff,
  verifyRoundTrip,
} from "../index.js";

function doc(rows, extra = {}) {
  return {
    schema: "omni.table-doc/v1",
    source: { adapter: "test", locator: "mem://t" },
    table: {
      name: "T",
      columns: [
        { name: "ID", label: "编号", type: "KN", key: true },
        { name: "Price", label: "价格", type: "N" },
        { name: "Name", label: "名称", type: "S" },
      ],
      key_fields: ["ID"],
      rows,
      ...extra,
    },
  };
}

// ── equals：唯一相等语义 ────────────────────────────────────────────────
assert.equal(equals(1, 1.0), true, "数值跨 int/float 相等");
assert.equal(equals(1, "1"), false, "数值不与字符串互转");
assert.equal(equals(true, 1), false, "布尔不等于 1");
assert.equal(equals(null, undefined), true, "null ≡ undefined");
assert.equal(equals({ a: 1, b: undefined }, { a: 1 }), true, "undefined 成员视同缺席");
assert.equal(equals([1, [2, "x"]], [1, [2, "x"]]), true, "深比较");
assert.throws(() => equals(NaN, 1), TypeError, "NaN 非法");
assert.equal(keyString(1.0), "1", "key canonical: 1.0 → \"1\"");

// ── canonicalJson / hash ───────────────────────────────────────────────
assert.equal(canonicalJson({ b: 1, a: [2, "文"] }), '{"a":[2,"文"],"b":1}');
assert.equal(canonicalJson({ x: -0 }), '{"x":0}');
const h1 = await contractHash({ a: 1, b: 2 });
const h2 = await contractHash({ b: 2, a: 1 });
assert.equal(h1, h2, "hash 与键序无关");
assert.match(h1, /^[0-9a-f]{64}$/);

// ── 三层+源侧 diff 分类 ────────────────────────────────────────────────
assert.equal(classifyCellChange({ v: 1 }, { v: 2 }).kind, "value");
assert.equal(classifyCellChange({ v: 3, f: "=A1*3" }, { v: 3, f: "=A1+2" }).kind, "formula");
assert.equal(
  classifyCellChange({ v: 3, f: "=A1*3", fv: 3 }, { v: 6, f: "=A1*3", fv: 6 }).kind,
  "formula-result",
  "公式没变结果变 = 依赖驱动",
);
assert.equal(classifyCellChange({ v: 1, sv: "1" }, { v: 1, sv: "01" }).kind, "source");
assert.equal(classifyCellChange({ v: 3, f: "=A1*3" }, { v: 5 }).kind, "formula", "值覆写公式格按 formula 报");
assert.equal(classifyCellChange({ v: 1 }, { v: 1 }), null);

// ── semanticDiff：主键对齐 + 增删改 ────────────────────────────────────
const before = doc([
  { cells: { ID: { v: 1 }, Price: { v: 30, f: "=B1*3", fv: 30 }, Name: { v: "甲" } } },
  { cells: { ID: { v: 2 }, Price: { v: 10 }, Name: { v: "乙" } } },
]);
const after = doc([
  { cells: { ID: { v: 1 }, Price: { v: 33, f: "=B1*3", fv: 33 }, Name: { v: "甲" } } },
  { cells: { ID: { v: 3 }, Price: { v: 5 }, Name: { v: "丙" } } },
]);
const d = semanticDiff(before, after);
assert.deepEqual(d.counts, { created: 1, updated: 1, deleted: 1 });
assert.deepEqual(d.created[0].key, { ID: 3 });
assert.deepEqual(d.deleted[0].key, { ID: 2 });
assert.equal(d.updated[0].changes.Price.kind, "formula-result");
assert.equal(d.updated[0].changes.Name, undefined, "未变字段不进 changes");

// 主键 1 与 1.0 对齐同一行
const d2 = semanticDiff(doc([{ cells: { ID: { v: 1 } } }]), doc([{ cells: { ID: { v: 1.0 } } }]));
assert.equal(isEmptyDiff(d2), true, "1 与 1.0 对齐同一行且无差异");

// 重复主键 fail closed
assert.throws(
  () => semanticDiff(doc([{ cells: { ID: { v: 1 } } }, { cells: { ID: { v: "1" } } }]), doc([])),
  DiffError,
  "字符串化后撞 key 必须报错",
);

// ── ChangeSet：normalize / apply / expectedDiff ───────────────────────
const cs = {
  schema: "omni.table-changeset/v1",
  operations: [
    { crud: "Update", key: { ID: 1 }, field: "Price", target: 40, target_projection: 40 },
    { crud: "Delete", key: { ID: 2 }, field: "*" },
    { crud: "Create", key: { ID: 9 }, cells: { ID: 9, Price: 1, Name: "新" } },
  ],
};
const exp = expectedDiff(before, cs);
assert.deepEqual(exp.counts, { created: 1, updated: 1, deleted: 1 });
assert.equal(exp.updated[0].changes.Price.kind, "formula", "值覆写公式格，公式消失要如实报");
assert.deepEqual(exp.created[0].key, { ID: 9 });

// current 由核心覆写
const norm = normalizeChangeSet(before, {
  operations: [{ crud: "Update", key: { ID: 1 }, field: "Price", target: 40, current: "编造值" }],
});
assert.equal(norm.ops[0].current, 30, "current 从 TableDoc 覆写，不采信调用方");
assert.equal(norm.ops[0].current_formula, "=B1*3", "公式随 current 一起带出");

// fail closed 族
assert.throws(() => applyChangeSet(before, { operations: [{ crud: "Create", key: { ID: 1 }, cells: { ID: 1 } }] }), ChangeSetError, "Create 撞已有 key");
assert.throws(() => applyChangeSet(before, { operations: [{ crud: "Update", key: { ID: 99 }, field: "Price", target: 1 }] }), ChangeSetError, "Update 缺 key");
assert.throws(
  () =>
    applyChangeSet(before, {
      operations: [
        { crud: "Update", key: { ID: 1 }, field: "Price", target: 1 },
        { crud: "Update", key: { ID: 1 }, field: "Price", target: 2 },
      ],
    }),
  ChangeSetError,
  "(key, field) 去重",
);
assert.throws(() => applyChangeSet(before, { operations: [{ crud: "Update", key: { Wrong: 1 }, field: "Price", target: 1 }] }), ChangeSetError, "key 字段集合必须等于 key_fields");

// 列级删除不需要 key
const colDel = expectedDiff(before, { operations: [{ crud: "Delete", field: "Name", delete_scope: "column" }] });
assert.equal(colDel.counts.updated, 2, "列删除横扫所有行");

// ── 双向闸 ────────────────────────────────────────────────────────────
const actual = semanticDiff(before, applyChangeSet(before, cs));
assert.equal(diffsExactlyEqual(exp, actual).ok, true, "期望⟷实际全等放行");
const drifted = semanticDiff(before, doc([{ cells: { ID: { v: 1 }, Price: { v: 41 }, Name: { v: "甲" } } }]));
assert.equal(diffsExactlyEqual(exp, drifted).ok, false, "漂移拦截");

// ── round-trip harness ────────────────────────────────────────────────
const store = new Map();
const jsonPlugin = {
  read: (src) => (typeof src === "string" ? JSON.parse(store.get(src) ?? src) : src),
  write: (docIn) => {
    store.set("mem://out", JSON.stringify(docIn));
    return "mem://out";
  },
};
store.set("mem://in", JSON.stringify(before));
const rt = await verifyRoundTrip(jsonPlugin, "mem://in");
assert.equal(rt.ok, true, "JSON 插件 round-trip 通过");

const lossyPlugin = {
  read: jsonPlugin.read,
  write: (docIn) => {
    const clone = JSON.parse(JSON.stringify(docIn));
    for (const row of clone.table.rows) {
      for (const cell of Object.values(row.cells)) delete cell.f; // 丢公式
    }
    store.set("mem://lossy", JSON.stringify(clone));
    return "mem://lossy";
  },
};
const rt2 = await verifyRoundTrip(lossyPlugin, "mem://in");
assert.equal(rt2.ok, false, "丢公式的 writer 必须被 harness 抓住");
assert.equal(rt2.diff.updated[0].changes.Price.kind, "formula");

console.log("core-smoke: all assertions passed");

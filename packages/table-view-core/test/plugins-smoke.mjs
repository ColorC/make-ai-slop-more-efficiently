import assert from "node:assert/strict";
import { verifyRoundTrip } from "../roundtrip.js";
import * as jsonPlugin from "../plugins/json.js";
import { createCsvPlugin, read as readCsv } from "../plugins/csv.js";
import * as luaPlugin from "../plugins/lua.js";

const tableDoc = {
  schema: "omni.table-doc/v1",
  source: { adapter: "fixture", locator: "memory://json-fixture", capabilities: ["formulas"] },
  table: {
    name: "JsonFixture",
    columns: [
      { name: "ID", type: "number", key: true },
      { name: "Name", type: "string" },
      { name: "Total", type: "number" },
    ],
    key_fields: ["ID"],
    rows: [
      { cells: { ID: { v: 1 }, Name: { v: "alpha" }, Total: { v: 4, sv: "04", f: "=2+2", fv: 4 } } },
      { cells: { ID: { v: 2 }, Name: { v: "beta" }, Total: { v: 5 } } },
    ],
  },
};

const jsonRoundTrip = await verifyRoundTrip(jsonPlugin, JSON.stringify(tableDoc));
assert.equal(jsonRoundTrip.ok, true, "JSON preserves the complete TableDoc cell model");

const bareJson = jsonPlugin.read('[{"ID":1,"Label":"one"},{"ID":2,"Label":"two"}]', {
  name: "BareRows",
  key_fields: ["ID"],
});
assert.deepEqual(bareJson.table.key_fields, ["ID"]);
assert.deepEqual(bareJson.table.rows[0].cells.Label, { v: "one" });
assert.throws(() => jsonPlugin.read('[{"ID":1}]'), /name option/);

const wrappedJson = jsonPlugin.read({
  name: "WrappedRows",
  columns: [{ name: "Code", key: true }, { name: "Payload", type: "object" }],
  datas: [{ Code: "a", Payload: { nested: [1, true, null] } }],
});
assert.deepEqual(wrappedJson.table.key_fields, ["Code"]);
assert.deepEqual(wrappedJson.table.rows[0].cells.Payload.v, { nested: [1, true, null] });

const csv = createCsvPlugin({
  name: "CsvFixture",
  key_fields: ["ID"],
  types: { ID: "number", Enabled: "boolean", Note: "string" },
});
const csvSource = 'ID,Enabled,Note\r\n1,true,"comma, quote ""and""\r\nnewline"\r\n2,false,plain\n';
const csvRoundTrip = await verifyRoundTrip(csv, csvSource);
assert.equal(csvRoundTrip.ok, true, "CSV preserves explicitly typed scalar values");
assert.equal(csvRoundTrip.doc.table.rows[0].cells.Note.v, 'comma, quote "and"\r\nnewline');
const csvWritten = csv.write(csvRoundTrip.doc);
assert.match(csvWritten, /"comma, quote ""and""\r\nnewline"/);

const allStrings = readCsv("ID,LooksNumeric,LooksBoolean\n01,42,true\n", { key_fields: ["ID"] });
assert.deepEqual(
  Object.values(allStrings.table.rows[0].cells).map((cell) => typeof cell.v),
  ["string", "string", "string"],
  "CSV does not guess types",
);
assert.throws(
  () => readCsv("ID,Enabled\n1,yes\n", { types: { ID: "number", Enabled: "boolean" } }),
  /line 2/,
);

const luaSource = String.raw`-- supported table-literal subset
return {
  [1] = { ID = 1, Name = "a\nline", Enabled = true, Meta = { score = 1.5, empty = nil } },
  [2] = { ID = 2, Name = 'single\'quote', Enabled = false, Meta = { score = -2e1 } },
}`;
const luaOptions = { name: "LuaFixture", key_fields: ["ID"] };
const luaFirst = luaPlugin.read(luaSource, luaOptions);
const luaSecond = luaPlugin.read(luaSource, luaOptions);
assert.deepEqual(luaFirst, luaSecond, "Lua reads are deterministic");
assert.equal(luaFirst.table.rows[0].cells.Name.v, "a\nline");
assert.deepEqual(luaFirst.table.rows[0].cells.Meta.v, { score: 1.5, empty: null });

const luaListShape = luaPlugin.read("{ { Key = 1, Value = 'x' }, { Key = 2, Value = 'y' } }", {
  name: "LuaList",
  key_fields: ["Key"],
});
assert.equal(luaListShape.table.rows[1].cells.Value.v, "y");

assert.throws(
  () => luaPlugin.read("return {\n  { ID = 1, Value = function() end }\n}", { key_fields: ["ID"] }),
  (error) => error instanceof SyntaxError && /line 2/.test(error.message),
  "Lua syntax outside the subset fails closed with a line number",
);

console.log("plugins-smoke: all assertions passed");

// manifest: {"id":"json-core","formats":["json"],"direction":["read","write"],"capabilities":["round-trip"],"version":"1"}

export const manifest = Object.freeze({
  id: "json-core",
  formats: Object.freeze(["json"]),
  direction: Object.freeze(["read", "write"]),
  capabilities: Object.freeze(["round-trip"]),
  version: "1",
});

const TABLE_DOC_SCHEMA = "omni.table-doc/v1";

function fail(message) {
  throw new TypeError(`json-core: ${message}`);
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseSource(source) {
  if (typeof source === "string") {
    try {
      return JSON.parse(source);
    } catch (error) {
      throw new SyntaxError(`json-core: invalid JSON: ${error.message}`);
    }
  }
  if (source instanceof Uint8Array) {
    return parseSource(new TextDecoder().decode(source));
  }
  return source;
}

function requireName(options) {
  if (typeof options.name !== "string" || options.name.length === 0) {
    fail("bare row arrays require a non-empty name option");
  }
  return options.name;
}

function requireKeyFields(keyFields, columnNames, context) {
  if (!Array.isArray(keyFields) || keyFields.length === 0 || keyFields.some((field) => typeof field !== "string" || field.length === 0)) {
    fail(`${context} requires a non-empty key_fields array`);
  }
  if (new Set(keyFields).size !== keyFields.length) fail("key_fields contains duplicates");
  const known = new Set(columnNames);
  for (const field of keyFields) {
    if (!known.has(field)) fail(`key field ${JSON.stringify(field)} is not a declared column`);
  }
  return [...keyFields];
}

function collectColumns(rows) {
  const names = [];
  const seen = new Set();
  for (const [index, row] of rows.entries()) {
    if (!isRecord(row)) fail(`row ${index + 1} must be an object`);
    for (const name of Object.keys(row)) {
      if (!seen.has(name)) {
        seen.add(name);
        names.push(name);
      }
    }
  }
  return names;
}

function toRows(rows, columnNames) {
  const known = new Set(columnNames);
  return rows.map((row, index) => {
    if (!isRecord(row)) fail(`row ${index + 1} must be an object`);
    const cells = {};
    for (const [field, value] of Object.entries(row)) {
      if (!known.has(field)) fail(`row ${index + 1} contains undeclared field ${JSON.stringify(field)}`);
      Object.defineProperty(cells, field, { value: { v: value }, enumerable: true, configurable: true, writable: true });
    }
    return { cells };
  });
}

function makeDoc(name, columns, keyFields, rows, options) {
  return {
    schema: TABLE_DOC_SCHEMA,
    source: {
      adapter: manifest.id,
      locator: typeof options.locator === "string" ? options.locator : "memory://json",
      capabilities: [...manifest.capabilities],
    },
    table: { name, columns, key_fields: keyFields, rows },
  };
}

function readBareRows(rows, options) {
  const name = requireName(options);
  const columnNames = collectColumns(rows);
  const keyFields = requireKeyFields(options.key_fields, columnNames, "bare row arrays");
  const columns = columnNames.map((name) => ({ name, ...(keyFields.includes(name) ? { key: true } : {}) }));
  return makeDoc(name, columns, keyFields, toRows(rows, columnNames), options);
}

function normalizeWrappedColumns(input) {
  if (!Array.isArray(input) || input.length === 0) fail("{columns, datas} requires a non-empty columns array");
  const columns = input.map((column, index) => {
    if (typeof column === "string" && column.length > 0) return { name: column };
    if (!isRecord(column)) fail(`column ${index + 1} must be an object or string`);
    const name = typeof column.name === "string" && column.name.length > 0
      ? column.name
      : typeof column.key === "string" && column.key.length > 0
        ? column.key
        : null;
    if (!name) fail(`column ${index + 1} requires a non-empty name`);
    const out = { ...column, name };
    if (typeof out.key === "string") delete out.key;
    return out;
  });
  const names = columns.map((column) => column.name);
  if (new Set(names).size !== names.length) fail("columns contains duplicate names");
  return columns;
}

function readWrapped(value, options) {
  if (!Array.isArray(value.datas)) fail("{columns, datas} requires datas to be an array");
  const columns = normalizeWrappedColumns(value.columns);
  const names = columns.map((column) => column.name);
  const inferredKeys = columns.filter((column) => column.key === true).map((column) => column.name);
  const keyFields = requireKeyFields(options.key_fields ?? inferredKeys, names, "{columns, datas}");
  const keyedColumns = columns.map((column) => keyFields.includes(column.name) ? { ...column, key: true } : column);
  const name = options.name ?? value.name ?? "JSON";
  if (typeof name !== "string" || name.length === 0) fail("table name must be a non-empty string");
  return makeDoc(name, keyedColumns, keyFields, toRows(value.datas, names), options);
}

function assertTableDoc(value) {
  if (!isRecord(value) || value.schema !== TABLE_DOC_SCHEMA || !isRecord(value.source) || !isRecord(value.table)) {
    fail(`expected ${TABLE_DOC_SCHEMA}, a bare row array, or a {columns, datas} wrapper`);
  }
  return value;
}

export function read(source, options = {}) {
  const value = parseSource(source);
  if (Array.isArray(value)) return readBareRows(value, options);
  if (isRecord(value) && Object.hasOwn(value, "columns") && Object.hasOwn(value, "datas")) {
    return readWrapped(value, options);
  }
  return assertTableDoc(value);
}

export function write(doc) {
  assertTableDoc(doc);
  try {
    return JSON.stringify(doc);
  } catch (error) {
    throw new TypeError(`json-core: TableDoc is not JSON serializable: ${error.message}`);
  }
}

export default Object.freeze({ manifest, read, write });

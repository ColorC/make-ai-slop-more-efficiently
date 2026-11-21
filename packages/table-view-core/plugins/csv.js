// manifest: {"id":"csv-core","formats":["csv"],"direction":["read","write"],"capabilities":["round-trip","rfc4180"],"version":"1"}

export const manifest = Object.freeze({
  id: "csv-core",
  formats: Object.freeze(["csv"]),
  direction: Object.freeze(["read", "write"]),
  capabilities: Object.freeze(["round-trip", "rfc4180"]),
  version: "1",
});

const ALLOWED_TYPES = new Set(["number", "string", "boolean"]);
const CELL_KEYS = ["v", "sv", "f", "fv"];

function fail(message, line) {
  const where = line === undefined ? "" : ` at line ${line}`;
  throw new SyntaxError(`csv-core${where}: ${message}`);
}

function sourceText(source) {
  if (typeof source === "string") return source;
  if (source instanceof Uint8Array) return new TextDecoder().decode(source);
  throw new TypeError("csv-core: source must be a string or Uint8Array");
}

function parseRecords(text) {
  const records = [];
  let record = [];
  let field = "";
  let quoted = false;
  let closedQuote = false;
  let line = 1;
  let recordLine = 1;

  const finishField = () => {
    record.push(field);
    field = "";
    closedQuote = false;
  };
  const finishRecord = () => {
    finishField();
    records.push({ fields: record, line: recordLine });
    record = [];
    recordLine = line;
  };

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          quoted = false;
          closedQuote = true;
        }
      } else {
        field += char;
        if (char === "\n") line += 1;
        else if (char === "\r" && text[i + 1] !== "\n") line += 1;
      }
      continue;
    }

    if (closedQuote && char !== "," && char !== "\r" && char !== "\n") {
      fail("unexpected character after closing quote", line);
    }
    if (char === '"') {
      if (field.length !== 0 || closedQuote) fail("quote must begin a field", line);
      quoted = true;
    } else if (char === ",") {
      finishField();
    } else if (char === "\n") {
      finishRecord();
      line += 1;
      recordLine = line;
    } else if (char === "\r") {
      if (text[i + 1] !== "\n") fail("bare CR outside a quoted field", line);
      finishRecord();
      i += 1;
      line += 1;
      recordLine = line;
    } else {
      field += char;
    }
  }
  if (quoted) fail("unterminated quoted field", line);
  if (field.length > 0 || record.length > 0 || closedQuote) finishRecord();
  return records;
}

function normalizeTypes(types, headers) {
  if (types === undefined) return Object.fromEntries(headers.map((field) => [field, "string"]));
  if (types === null || typeof types !== "object" || Array.isArray(types)) {
    throw new TypeError("csv-core: types must be an object");
  }
  for (const [field, type] of Object.entries(types)) {
    if (!headers.includes(field)) throw new TypeError(`csv-core: type declared for unknown field ${JSON.stringify(field)}`);
    if (!ALLOWED_TYPES.has(type)) throw new TypeError(`csv-core: unsupported type ${JSON.stringify(type)} for ${JSON.stringify(field)}`);
  }
  return Object.fromEntries(headers.map((field) => [field, types[field] ?? "string"]));
}

function parseValue(value, type, field, line) {
  if (type === "string") return value;
  if (type === "boolean") {
    if (value === "true") return true;
    if (value === "false") return false;
    fail(`field ${JSON.stringify(field)} must be "true" or "false"`, line);
  }
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)) {
    fail(`field ${JSON.stringify(field)} is not a valid number`, line);
  }
  const number = Number(value);
  if (!Number.isFinite(number)) fail(`field ${JSON.stringify(field)} is not a finite number`, line);
  return number;
}

function normalizeOptions(options, headers) {
  const name = options.name ?? "CSV";
  if (typeof name !== "string" || name.length === 0) throw new TypeError("csv-core: name must be a non-empty string");
  const keyFields = options.key_fields ?? [headers[0]];
  if (!Array.isArray(keyFields) || keyFields.length === 0 || keyFields.some((field) => typeof field !== "string" || !headers.includes(field))) {
    throw new TypeError("csv-core: key_fields must be a non-empty array of header names");
  }
  if (new Set(keyFields).size !== keyFields.length) throw new TypeError("csv-core: key_fields contains duplicates");
  return { name, keyFields, types: normalizeTypes(options.types, headers) };
}

export function read(source, options = {}) {
  const text = sourceText(source);
  const records = parseRecords(text);
  if (records.length === 0) fail("missing header row", 1);
  const headers = [...records[0].fields];
  if (headers[0]?.charCodeAt(0) === 0xfeff) headers[0] = headers[0].slice(1);
  if (headers.length === 0 || headers.some((header) => header.length === 0)) fail("header names must be non-empty", records[0].line);
  if (new Set(headers).size !== headers.length) fail("header names must be unique", records[0].line);
  const config = normalizeOptions(options, headers);
  const rows = records.slice(1).map(({ fields, line }) => {
    if (fields.length !== headers.length) fail(`expected ${headers.length} fields, got ${fields.length}`, line);
    const cells = {};
    for (let i = 0; i < headers.length; i += 1) {
      const field = headers[i];
      cells[field] = { v: parseValue(fields[i], config.types[field], field, line) };
    }
    return { cells };
  });
  return {
    schema: "omni.table-doc/v1",
    source: {
      adapter: manifest.id,
      locator: typeof options.locator === "string" ? options.locator : "memory://csv",
      capabilities: [...manifest.capabilities],
    },
    table: {
      name: config.name,
      columns: headers.map((field) => ({ name: field, type: config.types[field], ...(config.keyFields.includes(field) ? { key: true } : {}) })),
      key_fields: config.keyFields,
      rows,
    },
  };
}

function cellValue(cell, field, rowNumber) {
  if (cell !== null && typeof cell === "object" && !Array.isArray(cell)) {
    for (const key of ["sv", "f", "fv"]) {
      if (cell[key] !== undefined && cell[key] !== null) {
        throw new TypeError(`csv-core: row ${rowNumber} field ${JSON.stringify(field)} has ${key}; CSV cannot preserve it`);
      }
    }
    return cell.v;
  }
  return cell;
}

function encodeField(value, field, rowNumber) {
  if (value === undefined || value === null || typeof value === "object" || typeof value === "symbol" || typeof value === "function" || typeof value === "bigint") {
    throw new TypeError(`csv-core: row ${rowNumber} field ${JSON.stringify(field)} is not a lossless CSV scalar`);
  }
  if (typeof value === "number" && !Number.isFinite(value)) {
    throw new TypeError(`csv-core: row ${rowNumber} field ${JSON.stringify(field)} is not finite`);
  }
  const text = typeof value === "boolean" ? (value ? "true" : "false") : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function write(doc) {
  const table = doc?.table;
  if (doc?.schema !== "omni.table-doc/v1" || !table || !Array.isArray(table.columns) || !Array.isArray(table.rows)) {
    throw new TypeError("csv-core: write expects a TableDoc");
  }
  const headers = table.columns.map((column) => column?.name);
  if (headers.length === 0 || headers.some((header) => typeof header !== "string" || header.length === 0) || new Set(headers).size !== headers.length) {
    throw new TypeError("csv-core: columns must have unique, non-empty names");
  }
  const known = new Set(headers);
  const lines = [headers.map((header) => encodeField(header, header, 0)).join(",")];
  for (const [index, row] of table.rows.entries()) {
    const cells = row?.cells;
    if (!cells || typeof cells !== "object" || Array.isArray(cells)) throw new TypeError(`csv-core: row ${index + 1} has no cells object`);
    for (const field of Object.keys(cells)) {
      if (!known.has(field)) throw new TypeError(`csv-core: row ${index + 1} contains undeclared field ${JSON.stringify(field)}`);
    }
    lines.push(headers.map((field) => {
      if (!Object.hasOwn(cells, field)) throw new TypeError(`csv-core: row ${index + 1} is missing field ${JSON.stringify(field)}`);
      return encodeField(cellValue(cells[field], field, index + 1), field, index + 1);
    }).join(","));
  }
  return `${lines.join("\r\n")}\r\n`;
}

export function createCsvPlugin(options = {}) {
  return Object.freeze({
    manifest,
    read: (source) => read(source, options),
    write,
  });
}

export const createPlugin = createCsvPlugin;
export default Object.freeze({ manifest, read, write, createCsvPlugin, createPlugin });

// manifest: {"id":"lua-core","formats":["lua"],"direction":["read"],"capabilities":["table-literal-subset","deterministic"],"version":"1"}

export const manifest = Object.freeze({
  id: "lua-core",
  formats: Object.freeze(["lua"]),
  direction: Object.freeze(["read"]),
  capabilities: Object.freeze(["table-literal-subset", "deterministic"]),
  version: "1",
});

function syntax(message, line) {
  throw new SyntaxError(`lua-core: ${message} at line ${line}`);
}

function sourceText(source) {
  if (typeof source === "string") return source;
  if (source instanceof Uint8Array) return new TextDecoder().decode(source);
  throw new TypeError("lua-core: source must be a string or Uint8Array");
}

class Lexer {
  constructor(text) {
    this.text = text;
    this.index = 0;
    this.line = 1;
    this.buffer = [];
  }

  peek(offset = 0) {
    while (this.buffer.length <= offset) this.buffer.push(this.scan());
    return this.buffer[offset];
  }

  take() {
    return this.buffer.length > 0 ? this.buffer.shift() : this.scan();
  }

  skipIgnored() {
    while (this.index < this.text.length) {
      const char = this.text[this.index];
      if (/\s/.test(char)) {
        if (char === "\n") this.line += 1;
        else if (char === "\r") {
          if (this.text[this.index + 1] === "\n") this.index += 1;
          this.line += 1;
        }
        this.index += 1;
        continue;
      }
      if (char === "-" && this.text[this.index + 1] === "-") {
        this.index += 2;
        while (this.index < this.text.length && this.text[this.index] !== "\n" && this.text[this.index] !== "\r") this.index += 1;
        continue;
      }
      break;
    }
  }

  scan() {
    this.skipIgnored();
    const line = this.line;
    if (this.index >= this.text.length) return { kind: "eof", line };
    const char = this.text[this.index];
    if ("{}[]=,;".includes(char)) {
      this.index += 1;
      return { kind: char, value: char, line };
    }
    if (char === '"' || char === "'") return this.scanString(char, line);

    const rest = this.text.slice(this.index);
    const number = rest.match(/^-?(?:(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)/)?.[0];
    if (number) {
      this.index += number.length;
      const value = Number(number);
      if (!Number.isFinite(value)) syntax("non-finite number", line);
      return { kind: "number", value, line };
    }
    const identifier = rest.match(/^[A-Za-z_][A-Za-z0-9_]*/)?.[0];
    if (identifier) {
      this.index += identifier.length;
      return { kind: "identifier", value: identifier, line };
    }
    syntax(`unsupported token ${JSON.stringify(char)}`, line);
  }

  scanString(quote, line) {
    this.index += 1;
    let value = "";
    while (this.index < this.text.length) {
      const char = this.text[this.index++];
      if (char === quote) return { kind: "string", value, line };
      if (char === "\n" || char === "\r") syntax("newline in short string", this.line);
      if (char !== "\\") {
        value += char;
        continue;
      }
      if (this.index >= this.text.length) syntax("unterminated string escape", this.line);
      const escape = this.text[this.index++];
      const simple = { a: "\x07", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t", v: "\x0b", "\\": "\\", '"': '"', "'": "'" };
      if (Object.hasOwn(simple, escape)) {
        value += simple[escape];
      } else if (escape === "x") {
        const hex = this.text.slice(this.index, this.index + 2);
        if (!/^[0-9A-Fa-f]{2}$/.test(hex)) syntax("invalid hexadecimal string escape", this.line);
        value += String.fromCodePoint(Number.parseInt(hex, 16));
        this.index += 2;
      } else if (/\d/.test(escape)) {
        const tail = this.text.slice(this.index).match(/^\d{0,2}/)[0];
        const decimal = escape + tail;
        const code = Number.parseInt(decimal, 10);
        if (code > 255) syntax("decimal string escape exceeds 255", this.line);
        value += String.fromCodePoint(code);
        this.index += tail.length;
      } else if (escape === "z") {
        while (this.index < this.text.length && /\s/.test(this.text[this.index])) {
          if (this.text[this.index] === "\n") this.line += 1;
          else if (this.text[this.index] === "\r") {
            if (this.text[this.index + 1] === "\n") this.index += 1;
            this.line += 1;
          }
          this.index += 1;
        }
      } else {
        syntax(`unsupported string escape \\${escape}`, this.line);
      }
    }
    syntax("unterminated string", line);
  }
}

class Parser {
  constructor(text) {
    this.lexer = new Lexer(text);
  }

  expect(kind) {
    const token = this.lexer.take();
    if (token.kind !== kind) syntax(`expected ${JSON.stringify(kind)}, got ${this.describe(token)}`, token.line);
    return token;
  }

  describe(token) {
    return token.kind === "eof" ? "end of input" : JSON.stringify(token.value ?? token.kind);
  }

  parse() {
    if (this.lexer.peek().kind === "identifier" && this.lexer.peek().value === "return") this.lexer.take();
    const value = this.parseValue();
    const tail = this.lexer.take();
    if (tail.kind !== "eof") syntax(`unexpected token ${this.describe(tail)}`, tail.line);
    return value;
  }

  parseValue() {
    const token = this.lexer.take();
    if (token.kind === "{") return this.parseTable(token.line);
    if (token.kind === "string" || token.kind === "number") return token.value;
    if (token.kind === "identifier") {
      if (token.value === "true") return true;
      if (token.value === "false") return false;
      if (token.value === "nil") return null;
      syntax(`unsupported bare identifier ${JSON.stringify(token.value)}`, token.line);
    }
    syntax(`expected a table value, got ${this.describe(token)}`, token.line);
  }

  parseTable(openLine) {
    const entries = new Map();
    let nextArrayIndex = 1;
    while (this.lexer.peek().kind !== "}") {
      if (this.lexer.peek().kind === "eof") syntax("unterminated table", openLine);
      let key;
      let value;
      if (this.lexer.peek().kind === "[") {
        this.lexer.take();
        key = this.parseValue();
        this.expect("]");
        this.expect("=");
        value = this.parseValue();
      } else if (this.lexer.peek().kind === "identifier" && this.lexer.peek(1).kind === "=") {
        key = this.lexer.take().value;
        this.lexer.take();
        value = this.parseValue();
      } else {
        key = nextArrayIndex;
        nextArrayIndex += 1;
        value = this.parseValue();
      }
      if ((typeof key !== "string" && typeof key !== "number") || (typeof key === "number" && (!Number.isInteger(key) || key < 1))) {
        syntax("table keys must be strings or positive integers", this.lexer.peek().line);
      }
      const identity = `${typeof key}:${String(key)}`;
      if (entries.has(identity)) syntax(`duplicate table key ${JSON.stringify(key)}`, this.lexer.peek().line);
      entries.set(identity, { key, value });

      const separator = this.lexer.peek();
      if (separator.kind === "," || separator.kind === ";") {
        this.lexer.take();
        if (this.lexer.peek().kind === "}") break;
      } else if (separator.kind !== "}") {
        syntax(`expected a field separator or }, got ${this.describe(separator)}`, separator.line);
      }
    }
    this.expect("}");
    return this.materialize(entries);
  }

  materialize(entries) {
    const values = [...entries.values()];
    if (values.every(({ key }) => typeof key === "number")) {
      const sorted = values.toSorted((a, b) => a.key - b.key);
      if (sorted.every(({ key }, index) => key === index + 1)) return sorted.map(({ value }) => value);
    }
    const out = {};
    for (const { key, value } of values) {
      Object.defineProperty(out, String(key), { value, enumerable: true, configurable: true, writable: true });
    }
    return out;
  }
}

function rowsToDoc(rows, options) {
  if (!Array.isArray(rows)) throw new TypeError("lua-core: top-level table must be a contiguous row array at line 1");
  const columns = [];
  const seen = new Set();
  for (const [index, row] of rows.entries()) {
    if (row === null || typeof row !== "object" || Array.isArray(row)) {
      throw new TypeError(`lua-core: row ${index + 1} must be a keyed table at line 1`);
    }
    for (const field of Object.keys(row)) {
      if (!seen.has(field)) {
        seen.add(field);
        columns.push(field);
      }
    }
  }
  if (columns.length === 0) throw new TypeError("lua-core: rows must declare at least one field at line 1");
  const keyFields = options.key_fields ?? [columns[0]];
  if (!Array.isArray(keyFields) || keyFields.length === 0 || keyFields.some((field) => typeof field !== "string" || !seen.has(field))) {
    throw new TypeError("lua-core: key_fields must be a non-empty array of parsed field names at line 1");
  }
  if (new Set(keyFields).size !== keyFields.length) throw new TypeError("lua-core: key_fields contains duplicates at line 1");
  const name = options.name ?? "Lua";
  if (typeof name !== "string" || name.length === 0) throw new TypeError("lua-core: name must be a non-empty string at line 1");
  return {
    schema: "omni.table-doc/v1",
    source: {
      adapter: manifest.id,
      locator: typeof options.locator === "string" ? options.locator : "memory://lua",
      capabilities: [...manifest.capabilities],
    },
    table: {
      name,
      columns: columns.map((field) => ({ name: field, ...(keyFields.includes(field) ? { key: true } : {}) })),
      key_fields: [...keyFields],
      rows: rows.map((row) => {
        const cells = {};
        for (const [field, value] of Object.entries(row)) cells[field] = { v: value };
        return { cells };
      }),
    },
  };
}

export function read(source, options = {}) {
  const value = new Parser(sourceText(source)).parse();
  return rowsToDoc(value, options);
}

export default Object.freeze({ manifest, read });

import test from "node:test";
import assert from "node:assert/strict";
import { isRectangularClipboardText, parseClipboardTsv } from "../clipboard-grid.js";

test("parses Windows TSV without leaking carriage returns", () => {
  assert.deepEqual(parseClipboardTsv("a\tb\r\nc\t"), [["a", "b"], ["c", ""]]);
});

test("supports quoted tabs, newlines and escaped quotes", () => {
  assert.deepEqual(parseClipboardTsv('"a\tb"\t"line 1\r\nline 2"\r\n"say ""yes"""\tx'), [
    ["a\tb", "line 1\r\nline 2"],
    ['say "yes"', "x"],
  ]);
});

test("keeps empty cells while dropping only a synthetic trailing row", () => {
  assert.deepEqual(parseClipboardTsv("\t\r\n"), [["", ""]]);
  assert.deepEqual(parseClipboardTsv("a\tb\r\n"), [["a", "b"]]);
  assert.equal(isRectangularClipboardText("one"), false);
  assert.equal(isRectangularClipboardText("one\ttwo"), true);
});

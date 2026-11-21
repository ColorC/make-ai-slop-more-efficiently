/**
 * Round-trip 法则的机器形态（计划 §3.1.7）：
 *   read(source) → D1；write(D1) → source'；read(source') → D2；
 *   semantic_equal(D1, D2) 必须为真（含公式层）。
 * 插件不过本 harness 不得标可用。
 */

import { isEmptyDiff, semanticDiff } from "./diff.js";

/**
 * @param {{read: (src: any) => Promise<object>|object, write: (doc: object) => Promise<any>|any}} plugin
 * @param {*} source reader 能理解的定位子
 * @param {object} [opts] {typesys}
 * @returns {Promise<{ok: boolean, diff: object, doc: object, rereadDoc: object}>}
 */
export async function verifyRoundTrip(plugin, source, opts = {}) {
  if (typeof plugin?.read !== "function" || typeof plugin?.write !== "function") {
    throw new TypeError("verifyRoundTrip requires a plugin with read() and write()");
  }
  const doc = await plugin.read(source);
  const written = await plugin.write(doc);
  const rereadDoc = await plugin.read(written);
  const diff = semanticDiff(doc, rereadDoc, opts);
  return { ok: isEmptyDiff(diff), diff, doc, rereadDoc };
}

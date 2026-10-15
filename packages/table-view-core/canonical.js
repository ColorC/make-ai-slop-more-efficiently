/**
 * canonical JSON 与 hash：审批对象是 hash，HTML/页面只是 hash 的可视化。
 * 与参考实现对齐：sort_keys + 无空白 + 保留非 ASCII 原文。
 */

/**
 * 确定性 JSON 序列化：对象键排序、无空白、拒绝 NaN/Infinity/-0 歧义。
 * @param {*} value
 * @returns {string}
 */
export function canonicalJson(value) {
  return serialize(value);
}

function serialize(value) {
  if (value === undefined || value === null) return "null";
  const t = typeof value;
  if (t === "number") {
    if (!Number.isFinite(value)) throw new TypeError("canonicalJson: non-finite number");
    if (Object.is(value, -0)) return "0";
    return JSON.stringify(value);
  }
  if (t === "boolean" || t === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map(serialize).join(",")}]`;
  }
  if (t === "object") {
    const keys = Object.keys(value).sort();
    const parts = [];
    for (const key of keys) {
      const v = value[key];
      if (v === undefined) continue; // 与 JSON.stringify 行为一致：undefined 成员不进载荷
      parts.push(`${JSON.stringify(key)}:${serialize(v)}`);
    }
    return `{${parts.join(",")}}`;
  }
  throw new TypeError(`canonicalJson: unsupported type ${t}`);
}

/**
 * sha256（hex）。Node ≥18 与现代浏览器共用 WebCrypto。
 * @param {string | Uint8Array} data
 * @returns {Promise<string>}
 */
export async function sha256Hex(data) {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * 审批 hash：sha256(canonicalJson(payload))。
 * payload 应当是"最小审批载荷"（结构指纹 + 操作），大体量 rows 用 contextFingerprint 折叠。
 * @param {object} payload
 * @returns {Promise<string>}
 */
export async function contractHash(payload) {
  return sha256Hex(canonicalJson(payload));
}

/**
 * 上下文指纹：结构（columns/key_fields/header_layout） + 源文件字节 hash 的复合。
 * 让审批 hash 小巧但仍覆盖"结构 + 当前数据 + 来源身份"。
 * @param {object} structure  形如 {columns, key_fields, header_layout}
 * @param {string} sourceBytesSha256  reader 插件报告的源字节 sha256
 * @returns {Promise<string>}
 */
export async function contextFingerprint(structure, sourceBytesSha256) {
  return sha256Hex(`${canonicalJson(structure)}:${sourceBytesSha256}`);
}

// BSIDE_BLOCK 内容块契约 —— 一内核·多壳的唯一正本。
// 从 spark.workshop 的 contentBlocks.ts / MaterialDocumentReader.vue 收编（行为逐条对齐，
// 语料快照见消费方测试）；壳层只保留渲染器注册表（Vue/React 组件是插槽，不是内核）。
// 文法与管线集成方式见同目录 README.md。
const SECTION_BLOCK = /<!--BSIDE_BLOCK:([^>]+)-->([\s\S]*?)<!--\/BSIDE_BLOCK-->/g;
const SOURCE_MARKER = /<!--\/?BSIDE_BLOCK_SOURCE(?::[^>]*)?-->/g;
const MERMAID_FENCE = /```mermaid\s*\n([\s\S]*?)```/g;
const SIZE_PATTERN = /^(compact|medium|large|full)$/;
const SOURCE_STATUS = ["active", "intermediate", "historical", "discarded"];

/** 解码 `<!--BSIDE_BLOCK:…-->` 的属性载荷（URI 编码的 JSON）；畸形/缺 id 返回 null。 */
export function decodeBlockSpec(encoded) {
  try {
    const value = JSON.parse(decodeURIComponent(encoded));
    if (typeof value?.["id"] !== "string" || !value["id"].trim()) return null;
    const size = typeof value["size"] === "string" && SIZE_PATTERN.test(value["size"])
      ? value["size"] : "medium";
    const sources = Array.isArray(value["sources"])
      ? value["sources"].flatMap((candidate) => {
          if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return [];
          if (typeof candidate["id"] !== "string" || typeof candidate["label"] !== "string"
            || typeof candidate["source"] !== "string") return [];
          return [{
            id: candidate["id"], label: candidate["label"], source: candidate["source"],
            ...(typeof candidate["version"] === "string" ? { version: candidate["version"] } : {}),
            ...(SOURCE_STATUS.includes(String(candidate["status"]))
              ? { status: candidate["status"] } : {}),
          }];
        }) : undefined;
    return {
      id: value["id"].trim(), size,
      ...(typeof value["filter"] === "string" ? { filter: value["filter"] } : {}),
      ...(typeof value["kind"] === "string" ? { kind: value["kind"] } : {}),
      ...(typeof value["source"] === "string" ? { source: value["source"] } : {}),
      ...(typeof value["sourceId"] === "string" ? { sourceId: value["sourceId"] } : {}),
      ...(sources?.length ? { sources } : {}),
    };
  } catch { return null; }
}

/** 把文档切成 prose/块 交替的节列表；块属性畸形时按原样退回 prose（不丢内容）。 */
export function parseContentSections(content, prefix) {
  const text = String(content ?? "");
  const sections = [];
  let cursor = 0;
  let index = 0;
  for (const match of text.matchAll(SECTION_BLOCK)) {
    const at = match.index ?? 0;
    const before = text.slice(cursor, at);
    if (before.trim()) sections.push({ key: `${prefix}-prose-${index++}`, body: before });
    const body = match[2] ?? "";
    const block = decodeBlockSpec(match[1] ?? "");
    sections.push(block
      ? { key: `${prefix}-block-${index++}`, body, block }
      : { key: `${prefix}-prose-${index++}`, body });
    cursor = at + match[0].length;
  }
  const after = text.slice(cursor);
  if (after.trim()) sections.push({ key: `${prefix}-prose-${index++}`, body: after });
  return sections.length ? sections : [{ key: `${prefix}-prose-0`, body: text }];
}

/** 剥离 BSIDE_BLOCK_SOURCE 开/闭标记（块来源边界的成对提示，不参与展示）。 */
export function stripSourceMarkers(text) {
  return String(text ?? "").replace(SOURCE_MARKER, "");
}

/** ```mermaid 围栏 → WORKSHOP_MERMAID 标记（特效脚本块进同一占位符管线）。 */
export function expandMermaidFences(content) {
  return String(content ?? "").replace(MERMAID_FENCE, (_all, code) =>
    `<!--WORKSHOP_MERMAID:${encodeURIComponent(String(code ?? "").trim())}-->`);
}

/**
 * 行内标记切分：`<!--MARKER:payload-->`（MARKER = [A-Z][A-Z0-9_]+，payload 不得含 `>`）。
 * 未注册的标记留在 markdown 文段里原样输出；首个文段（且节以文开头时）携带 title 提示。
 * resolveMarker 是壳的渲染器插槽：返回 { id, parse(payload) } 或空以跳过。
 */
export function splitMarkerSegments(body, options) {
  const prefix = options.prefix;
  const title = options.title;
  const resolveMarker = options.resolveMarker;
  const renderMarkdown = options.renderMarkdown;
  const expanded = expandMermaidFences(String(body ?? ""));
  const marker = /<!--([A-Z][A-Z0-9_]+):([^>]+)-->/g;
  const result = [];
  let cursor = 0;
  let match;
  let index = 0;
  let pushed = 0;
  while ((match = marker.exec(expanded))) {
    const handler = resolveMarker ? resolveMarker(match[1] ?? "") : undefined;
    if (!handler) continue;
    const before = expanded.slice(cursor, match.index);
    if (before.trim()) {
      result.push({
        type: "markdown",
        key: `${prefix}-md-${index++}`,
        html: renderMarkdown(before, pushed === 0 ? title : undefined),
      });
      pushed++;
    }
    result.push({
      type: "marker",
      key: `${prefix}-${handler.id}-${index++}`,
      marker: match[1] ?? "",
      props: handler.parse(match[2] ?? ""),
    });
    pushed++;
    cursor = marker.lastIndex;
  }
  const after = expanded.slice(cursor);
  if (after.trim()) {
    result.push({
      type: "markdown",
      key: `${prefix}-md-${index}`,
      html: renderMarkdown(after, pushed === 0 ? title : undefined),
    });
  }
  return result;
}

/**
 * 整文档占位符管线：节切分 + 行内标记切分。壳层只需提供
 * resolveMarker（渲染器注册表）与 renderMarkdown（文段渲染），即可拿到的
 * { key, block?, segments[] } 列表逐段挂组件。
 */
export function renderBlockDocument(content, options) {
  return parseContentSections(String(content ?? ""), options.prefix).map((section) => ({
    key: section.key,
    block: section.block,
    segments: splitMarkerSegments(section.body, {
      prefix: section.key,
      title: options.title,
      resolveMarker: options.resolveMarker,
      renderMarkdown: options.renderMarkdown,
    }),
  }));
}

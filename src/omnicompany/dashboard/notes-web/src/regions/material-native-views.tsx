import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { semanticDiff, type TableDiff, type TableDoc } from "@omni/table-view-core";
// Browser-safe display-plugin resolver from Material Core (the package barrel also exports Node-only validators).
// @ts-expect-error plugin-slots.js is intentionally a low-level, untyped browser entry.
import { resolveDisplayPlugin } from "../../node_modules/@omni/material-core/plugin-slots.js";
// table-view-core intentionally keeps the browser widget separate from its semantic core.
// @ts-expect-error The package has no declaration for its internal browser widget yet.
import { mountTableView } from "../../node_modules/@omni/table-view-core/widget.js";

type UnknownRecord = Record<string, unknown>;

interface MaterialRenderer {
  id: string;
  priority?: number;
  match: {
    renderer_hint?: string | string[];
    media_type?: string | string[];
    labels?: string | string[];
    when?: () => boolean;
  };
  render: (context: {
    material: UnknownRecord;
    representation: UnknownRecord;
    content?: string;
    onOpenMaterial: () => void;
  }) => ReactNode;
}

const list = (value: string | string[] | undefined): string[] => value === undefined ? [] : Array.isArray(value) ? value : [value];

function selectMaterialRenderer(renderers: MaterialRenderer[], material: UnknownRecord, representation: UnknownRecord): MaterialRenderer | undefined {
  const candidates = renderers.filter((entry) => entry.match.when?.() !== false);
  const definitions = candidates.map((entry) => ({
    id: entry.id,
    hints: list(entry.match.renderer_hint),
    mediaTypes: list(entry.match.media_type).filter((value) => !value.includes("*")),
    labels: list(entry.match.labels),
  }));
  const resolved = resolveDisplayPlugin({ ...material, representations: [representation] }, definitions);
  return resolved.kind === "plugin" ? candidates.find((entry) => entry.id === resolved.plugin_id) : undefined;
}

export interface NativeMaterialBlock {
  material?: {
    id?: unknown;
    title?: unknown;
    summary?: unknown;
    labels?: unknown;
    revision_ref?: unknown;
    metadata?: unknown;
    representations?: Array<{
      id?: unknown;
      media_type?: unknown;
      renderer_hint?: unknown;
      source_ref?: { kind?: unknown; ref?: unknown } | null;
      title?: unknown;
    }>;
  };
  representation?: {
    id?: unknown;
    media_type?: unknown;
    content_format?: unknown;
    renderer_hint?: unknown;
    source_ref?: { kind?: unknown; ref?: unknown } | null;
    title?: unknown;
  } | null;
}

interface NativeMaterialViewProps {
  block?: NativeMaterialBlock;
  content: string;
  renderMarkdown: (content: string) => string;
  resolveSource: (ref: string) => string;
}

function record(value: unknown): UnknownRecord | null {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord : null;
}

function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function parseJson(content: string): UnknownRecord | null {
  try { return record(JSON.parse(content)); } catch { return null; }
}

export function materialPrimaryKind(block?: NativeMaterialBlock): string {
  const metadata = record(block?.material?.metadata);
  const classification = record(metadata?.classification_ref);
  const path = Array.isArray(classification?.primary_path) ? classification.primary_path : [];
  const leaf = path.length ? path[path.length - 1] : undefined;
  return typeof leaf === "string" ? leaf : "";
}

function materialPurpose(block?: NativeMaterialBlock): string {
  const metadata = record(block?.material?.metadata);
  const classification = record(metadata?.classification_ref);
  const facets = record(classification?.facets);
  const paths = Array.isArray(facets?.purpose) ? facets.purpose : [];
  const first = Array.isArray(paths[0]) ? paths[0] : [];
  const leaf = first.length ? first[first.length - 1] : undefined;
  return typeof leaf === "string" ? leaf : "";
}

function sourcePluginId(block?: NativeMaterialBlock): string {
  return string(block?.representation?.source_ref?.kind) || "inline";
}

function cellValue(value: unknown): unknown {
  if (value === undefined) return null;
  return value;
}

function tableKeyFields(columns: string[], rows: UnknownRecord[]): string[] {
  const knownPairs = [["GuideID", "Step"], ["ActivityID", "ID"], ["TutorialID"], ["ID"]];
  const exact = knownPairs.find((fields) => fields.every((field) => columns.includes(field)
    && rows.every((row) => row[field] !== undefined)));
  if (exact) return exact;
  const declared = columns.filter((column) => /(^|_)(id|key)$/i.test(column)
    && rows.every((row) => row[column] !== undefined));
  return declared.slice(0, 2);
}

function rowsTableDoc(table: UnknownRecord, index: number, source = "material-output"): TableDoc | null {
  const columns = Array.isArray(table.columns)
    ? table.columns.filter((item): item is string => typeof item === "string") : [];
  const rows = Array.isArray(table.rows)
    ? table.rows.map(record).filter((item): item is UnknownRecord => Boolean(item)) : [];
  if (!columns.length) return null;
  const keyFields = tableKeyFields(columns, rows);
  const allColumns = keyFields.length ? columns : ["__row", ...columns];
  return {
    schema: "omni.table-doc/v1",
    source: {
      adapter: source,
      locator: string(table.name) || `Table ${index + 1}`,
      capabilities: ["read", "diff"],
    },
    table: {
      name: string(table.name) || `Table ${index + 1}`,
      header_layout: { rows: 2, roles: ["label", "type"], data_start: 1 },
      columns: allColumns.map((name) => ({
        name,
        label: name === "__row" ? "行" : name,
        type: name === "__row" ? "N" : typeof rows.find((row) => row[name] !== undefined)?.[name] === "number" ? "N" : "S",
        key: keyFields.includes(name) || name === "__row",
      })),
      key_fields: keyFields.length ? keyFields : ["__row"],
      rows: rows.map((row, rowIndex) => ({
        cells: Object.fromEntries(allColumns.map((column) => [column, { v: column === "__row" ? rowIndex + 1 : cellValue(row[column]) }])),
      })),
    },
  };
}

function keyFromChange(value: unknown): UnknownRecord {
  const direct = record(value);
  if (direct) return direct;
  if (typeof value !== "string") return {};
  return Object.fromEntries(value.split(",").flatMap((part) => {
    const [rawKey, ...rawValue] = part.split("=");
    const key = rawKey?.trim();
    const text = rawValue.join("=").trim();
    if (!key) return [];
    const numeric = Number(text);
    return [[key, text !== "" && Number.isFinite(numeric) ? numeric : text]];
  }));
}

function cloneDoc(doc: TableDoc): TableDoc {
  return structuredClone(doc);
}

function previewDocs(value: UnknownRecord): Array<{ doc: TableDoc; diff?: TableDiff }> {
  const tables = Array.isArray(value.tables) ? value.tables.map(record).filter((item): item is UnknownRecord => Boolean(item)) : [];
  const fieldChanges = Array.isArray(value.field_changes)
    ? value.field_changes.map(record).filter((item): item is UnknownRecord => Boolean(item)) : [];
  return tables.flatMap((table, index) => {
    const after = rowsTableDoc(table, index, "spark-config-preview");
    if (!after) return [];
    const matching = fieldChanges.filter((change) => string(change.table || change.sheet) === after.table.name.split(" · ")[0]);
    if (!matching.length) return [{ doc: after }];
    const before = cloneDoc(after);
    for (const change of matching) {
      const key = keyFromChange(change.key);
      const row = before.table.rows.find((candidate) => before.table.key_fields.every((field) => {
        const cell = record(candidate.cells[field]);
        return (cell?.v ?? candidate.cells[field]) === key[field];
      }));
      const field = string(change.field);
      if (row && field && Object.prototype.hasOwnProperty.call(row.cells, field)) row.cells[field] = { v: cellValue(change.before) };
    }
    return [{ doc: before, diff: semanticDiff(before, after) }];
  });
}

function directDocs(value: UnknownRecord): Array<{ doc: TableDoc; diff?: TableDiff }> {
  if (Array.isArray(value.docs)) {
    return value.docs.flatMap((entry) => {
      const item = record(entry);
      const doc = record(item?.doc);
      if (doc?.schema !== "omni.table-doc/v1") return [];
      const diff = record(item?.diff);
      return [{ doc: doc as unknown as TableDoc, ...(diff?.schema === "omni.table-diff/v1" ? { diff: diff as unknown as TableDiff } : {}) }];
    });
  }
  if (value.schema === "omni.table-doc/v1") return [{ doc: value as unknown as TableDoc }];
  if (Array.isArray(value.tables)) return value.tables.flatMap((table, index) => {
    const doc = record(table) ? rowsTableDoc(table as UnknownRecord, index) : null;
    return doc ? [{ doc }] : [];
  });
  const doc = rowsTableDoc(value, 0);
  return doc ? [{ doc }] : [];
}

function SharedTableView({ docs, title, renderer }: {
  docs: Array<{ doc: TableDoc; diff?: TableDiff }>;
  title: string;
  renderer: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const report = useMemo(() => ({ id: renderer, title, docs }), [docs, renderer, title]);
  useEffect(() => {
    if (host.current) mountTableView(host.current, report);
  }, [report]);
  return <section className="material-shared-table-view" data-material-renderer={renderer} ref={host} />;
}

function TablePayload({ value, title, diff }: { value: UnknownRecord; title: string; diff: boolean }) {
  const docs = useMemo(() => diff ? previewDocs(value) : directDocs(value), [diff, value]);
  if (!docs.length) return <StructuredError renderer={diff ? "table-diff" : "table"} message="结构化表格数据无法转换为 omni.table-doc/v1。" />;
  return <SharedTableView docs={docs} title={title} renderer={diff ? "table-diff" : "table"} />;
}

function ActualDiffPayload({ value, title }: { value: UnknownRecord; title: string }) {
  const docs = useMemo(() => directDocs(value), [value]);
  return <div className="material-actual-diff" data-material-renderer="actual-diff">
    <header><strong>{string(value.revision) || `CL ${String(value.change ?? "—")}`}</strong><span>{string(value.state)}</span></header>
    {string(value.description) && <p>{string(value.description)}</p>}
    {docs.length
      ? <SharedTableView docs={docs} title={title} renderer="actual-diff-table" />
      : <StructuredError renderer="actual-diff" message="实际 Diff 不含 omni.table-doc/v1 + omni.table-diff/v1。" />}
  </div>;
}

function PlanningPayload({ value }: { value: UnknownRecord }) {
  const inputs = Array.isArray(value.inputs) ? value.inputs.map(record).filter((item): item is UnknownRecord => Boolean(item)) : [];
  if (!inputs.length) return <StructuredError renderer="planning-form" message="规划 Material 缺少结构化 inputs。" />;
  return <section className="material-planning-form" data-material-renderer="planning-form">
    <header><strong>配置规划输入</strong><span>{inputs.length} 项关键量</span></header>
    <dl>{inputs.map((input, index) => <div key={`${string(input.name)}-${index}`}>
      <dt>{string(input.name) || `输入 ${index + 1}`}</dt>
      <dd><output>{formatValue(input.value)}</output><p>{string(input.meaning)}</p><small>{string(input.source)}</small></dd>
    </div>)}</dl>
  </section>;
}

function ProcessPayload({ value }: { value: UnknownRecord }) {
  const nodes = (Array.isArray(value.nodes) ? value.nodes : Array.isArray(value.nodeStates) ? value.nodeStates : [])
    .map(record).filter((item): item is UnknownRecord => Boolean(item));
  return <section className="material-process-view" data-material-renderer="process">
    <header><strong>{string(value.title) || string(value.stage) || "流程"}</strong><span>{string(value.status) || string(value.verdict)}</span></header>
    {nodes.length ? <ol>{nodes.map((node, index) => <li key={string(node.id) || index} data-status={string(node.status)}>
      <strong>{string(node.title) || string(node.id) || `步骤 ${index + 1}`}</strong><span>{string(node.status)}</span>
    </li>)}</ol> : <p>{string(value.behavior) || string(value.note) || "该流程没有可显示的步骤。"}</p>}
  </section>;
}

function RelationshipPayload({ value }: { value: UnknownRecord }) {
  return <section className="material-relationship-view" data-material-renderer="relationship">
    <code>{string(value.from)}</code><strong>{string(value.predicate) || "关联"}</strong><code>{string(value.to)}</code>
  </section>;
}

function ViewPayload({ value }: { value: UnknownRecord }) {
  const members = Array.isArray(value.members) ? value.members.map(record).filter((item): item is UnknownRecord => Boolean(item)) : [];
  return <section className="material-collection-view" data-material-renderer="material-view"><header><strong>Material 视图</strong><span>{members.length} 项</span></header>
    <ul>{members.map((member, index) => <li key={string(member.material_id) || index}>{string(member.material_id) || `Material ${index + 1}`}</li>)}</ul>
  </section>;
}

function StructuredError({ renderer, message }: { renderer: string; message: string }) {
  return <div className="material-preview-message error" data-material-renderer={renderer}>{message} 已阻止降级成 Markdown/原始 JSON。</div>;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "（空字符串）";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function materialForRenderer(block?: NativeMaterialBlock) {
  const representation = block?.representation ?? {};
  return {
    material: {
      id: string(block?.material?.id) || "material:canvas/native",
      title: string(block?.material?.title) || "Material",
      summary: string(block?.material?.summary),
      labels: Array.isArray(block?.material?.labels) ? block!.material!.labels!.filter((item): item is string => typeof item === "string") : [],
      revision_ref: record(block?.material?.revision_ref) ?? { provider: "canvas", object: "native", revision: "current" },
      representations: [representation],
      metadata: record(block?.material?.metadata) ?? {},
    },
    representation: {
      id: string(representation.id) || "primary",
      media_type: string(representation.media_type) || "text/plain",
      role: "primary",
      source_ref: {
        kind: string(representation.source_ref?.kind) || "inline",
        ref: string(representation.source_ref?.ref) || "inline",
      },
      ...(string(representation.renderer_hint) ? { renderer_hint: string(representation.renderer_hint) } : {}),
    },
  };
}

export function NativeMaterialView({ block, content, renderMarkdown, resolveSource }: NativeMaterialViewProps) {
  const value = useMemo(() => parseJson(content), [content]);
  const kind = materialPrimaryKind(block);
  const purpose = materialPurpose(block);
  const hint = string(block?.representation?.renderer_hint).toLowerCase();
  const mediaType = string(block?.representation?.media_type).toLowerCase();
  const sourcePlugin = sourcePluginId(block);
  const { material, representation } = useMemo(() => materialForRenderer(block), [block]);
  const renderers = useMemo<MaterialRenderer[]>(() => [
    {
      id: "spark.config.actual-diff",
      priority: 100,
      match: { renderer_hint: ["p4-changelist", "p4-shelve-list"], when: () => kind === "diff" && hint.startsWith("p4-") },
      render: () => value ? <ActualDiffPayload value={value} title={material.title} /> : <StructuredError renderer="actual-diff" message="实际 Diff 数据不是合法 JSON。" />,
    },
    {
      id: "spark.config.preview-diff",
      priority: 90,
      match: { renderer_hint: ["config-preview", "table-diff"], when: () => kind === "diff" || purpose === "candidate-change" },
      render: () => value ? <TablePayload value={value} title={material.title} diff /> : <StructuredError renderer="table-diff" message="配置预演数据不是合法 JSON。" />,
    },
    {
      id: "spark.config.planning-form",
      priority: 80,
      match: { renderer_hint: ["config-planning", "planning-forms", "planning-source"], when: () => kind === "form" || purpose === "planning-source" },
      render: () => value ? <PlanningPayload value={value} /> : <StructuredError renderer="planning-form" message="规划数据不是合法 JSON。" />,
    },
    {
      id: "omni.table.standard",
      priority: 70,
      match: { renderer_hint: ["data-table", "table", "config"], when: () => kind === "table" },
      render: () => value ? <TablePayload value={value} title={material.title} diff={false} /> : <StructuredError renderer="table" message="表格数据不是合法 JSON。" />,
    },
    {
      id: "omni.process",
      match: { media_type: ["application/json", "application/*+json"], when: () => kind === "process" || hint.startsWith("workflow-") },
      render: () => value ? <ProcessPayload value={value} /> : <StructuredError renderer="process" message="流程数据不是合法 JSON。" />,
    },
    {
      id: "omni.relation",
      match: { media_type: ["application/vnd.omni.relationship+json"], when: () => kind === "relation" },
      render: () => value ? <RelationshipPayload value={value} /> : <StructuredError renderer="relationship" message="关系数据不是合法 JSON。" />,
    },
    {
      id: "omni.view",
      match: { media_type: ["application/vnd.omni.view+json"], when: () => kind === "view" },
      render: () => value ? <ViewPayload value={value} /> : <StructuredError renderer="material-view" message="视图定义不是合法 JSON。" />,
    },
    {
      id: "omni.image",
      match: { media_type: ["image/*"], when: () => kind === "image" || mediaType.startsWith("image/") },
      render: () => <img className="material-media-preview" data-material-renderer="image" src={resolveSource(representation.source_ref.ref)} alt={material.title} />,
    },
    {
      id: "omni.demo",
      match: { media_type: ["text/html"], when: () => kind === "demo" },
      render: () => <iframe className="material-frame-preview" data-material-renderer="demo" src={resolveSource(representation.source_ref.ref)} title={material.title} />,
    },
    {
      id: "omni.document",
      match: { media_type: ["text/markdown", "text/plain"], when: () => kind === "document" || !kind },
      render: () => <div className="material-markdown-preview" data-material-renderer="document" dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }} />,
    },
  ], [content, hint, kind, material.title, mediaType, purpose, renderMarkdown, representation.source_ref.ref, resolveSource, value]);
  const selected = selectMaterialRenderer(renderers, material as unknown as UnknownRecord, representation as unknown as UnknownRecord);
  const rendered: ReactNode = selected?.render({
    material: material as unknown as UnknownRecord,
    representation: representation as unknown as UnknownRecord,
    content,
    onOpenMaterial: () => undefined,
  });
  if (!selected || rendered === undefined || rendered === null) {
    return <StructuredError renderer="unsupported-material-type" message={`Material 类型 ${kind || "未声明"} 没有可用显示插件。`} />;
  }
  return <div className="material-native-plugin-host" data-display-plugin={selected.id} data-source-plugin={sourcePlugin}>{rendered}</div>;
}

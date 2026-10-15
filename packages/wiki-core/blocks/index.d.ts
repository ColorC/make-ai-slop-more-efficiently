// BSIDE_BLOCK 内容块契约类型（与 index.js 运行时逐条对齐）。
export type ContentBlockSizeMode = 'compact' | 'medium' | 'large' | 'full';
export type ContentBlockSourceStatus = 'active' | 'intermediate' | 'historical' | 'discarded';

export interface ContentBlockSourceRef {
  id: string;
  label: string;
  source: string;
  version?: string;
  status?: ContentBlockSourceStatus;
}

export interface ContentBlockSpec {
  id: string;
  filter?: string;
  size: ContentBlockSizeMode;
  kind?: string;
  source?: string;
  sourceId?: string;
  sources?: ContentBlockSourceRef[];
}

export interface ContentSection {
  key: string;
  body: string;
  block?: ContentBlockSpec;
}

/** 壳层渲染器插槽：注册表项只需满足此契约（Vue/React 组件由各壳自持）。 */
export interface MarkerHandler {
  id: string;
  parse(payload: string): Record<string, unknown>;
}

export interface MarkdownSegment {
  type: 'markdown';
  key: string;
  html: string;
}

export interface MarkerSegment {
  type: 'marker';
  key: string;
  marker: string;
  props: Record<string, unknown>;
}

export type BlockSegment = MarkdownSegment | MarkerSegment;

export interface BlockSplitOptions {
  prefix: string;
  title?: string;
  resolveMarker?: (marker: string) => MarkerHandler | null | undefined;
  renderMarkdown(source: string, title?: string): string;
}

export interface RenderedContentSection {
  key: string;
  block?: ContentBlockSpec;
  segments: BlockSegment[];
}

export function decodeBlockSpec(encoded: string): ContentBlockSpec | null;
export function parseContentSections(content: string, prefix: string): ContentSection[];
export function stripSourceMarkers(text: string): string;
export function expandMermaidFences(content: string): string;
export function splitMarkerSegments(body: string, options: BlockSplitOptions): BlockSegment[];
export function renderBlockDocument(content: string, options: BlockSplitOptions): RenderedContentSection[];

export interface AnnotationMechanism {
  id: string;
  anchorRefs: string[];
  text: string;
}

export interface AnnotationAnchor {
  id: string;
  stageId: string;
  targetPath: string;
  x: number;
  y: number;
  width: number;
  height: number;
  mechanisms: AnnotationMechanism[];
}

export interface AnnotationStage {
  id: string;
  anchors: AnnotationAnchor[];
}

export interface AnnotationDocument {
  stages: AnnotationStage[];
  mechanisms: AnnotationMechanism[];
}

const decodeHtml = (value: string): string => value
  .replace(/&quot;/gi, '"')
  .replace(/&#39;|&apos;/gi, "'")
  .replace(/&lt;/gi, '<')
  .replace(/&gt;/gi, '>')
  .replace(/&amp;/gi, '&')
  .replace(/&#(\d+);/g, (_match, code: string) => String.fromCodePoint(Number(code)))
  .replace(/&#x([0-9a-f]+);/gi, (_match, code: string) => String.fromCodePoint(Number.parseInt(code, 16)));

function attribute(source: string, name: string): string | null {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`(?:^|\\s)${escaped}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, 'i').exec(source);
  return match ? decodeHtml(match[1] ?? match[2] ?? '') : null;
}

function percent(style: string, property: string): number | null {
  const match = new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*(-?(?:\\d+(?:\\.\\d+)?|\\.\\d+))%`, 'i').exec(style);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value / 100 : null;
}

function plainText(html: string): string {
  return decodeHtml(html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p\s*>|<\/li\s*>|<\/h[1-6]\s*>/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s+/g, '\n')
    .trim());
}

/**
 * Consume the five-attribute annotation contract without deriving or validating
 * the producer's hash. Invalid/incomplete markers are ignored as legacy content.
 */
export function parseAnnotationDocument(source: string): AnnotationDocument {
  const mechanisms: AnnotationMechanism[] = [];
  const sectionPattern = /<section\b([^>]*)>([\s\S]*?)<\/section\s*>/gi;
  for (const match of source.matchAll(sectionPattern)) {
    const id = attribute(match[1] ?? '', 'data-mechanism-id');
    const refs = attribute(match[1] ?? '', 'data-anchor-refs');
    if (!id || refs === null) continue;
    mechanisms.push({
      id,
      anchorRefs: refs.split(/\s+/).filter(Boolean),
      text: plainText(match[2] ?? ''),
    });
  }

  const stages: AnnotationStage[] = [];
  const figurePattern = /<figure\b([^>]*)>([\s\S]*?)<\/figure\s*>/gi;
  for (const figure of source.matchAll(figurePattern)) {
    const stageId = attribute(figure[1] ?? '', 'data-stage-id');
    if (!stageId) continue;
    const anchors: AnnotationAnchor[] = [];
    const spanPattern = /<span\b([^>]*)>/gi;
    for (const span of (figure[2] ?? '').matchAll(spanPattern)) {
      const attrs = span[1] ?? '';
      const id = attribute(attrs, 'data-anchor-id');
      const targetPath = attribute(attrs, 'data-target-path');
      const style = attribute(attrs, 'style') ?? '';
      const x = percent(style, 'left');
      const y = percent(style, 'top');
      const width = percent(style, 'width');
      const height = percent(style, 'height');
      if (!id || !targetPath || x === null || y === null || width === null || height === null) continue;
      anchors.push({
        id,
        stageId,
        targetPath,
        x,
        y,
        width,
        height,
        mechanisms: mechanisms.filter((mechanism) => mechanism.anchorRefs.includes(id)),
      });
    }
    stages.push({ id: stageId, anchors });
  }
  return { stages, mechanisms };
}

export function annotationStageId(representation: {
  id?: unknown;
  title?: unknown;
  source_ref?: { ref?: unknown } | null;
} | null | undefined): string | null {
  const title = typeof representation?.title === 'string' ? representation.title.trim() : '';
  if (title) return title;
  const id = typeof representation?.id === 'string' ? representation.id : '';
  const shot = /^shot-(.+)$/.exec(id);
  if (shot?.[1]) return shot[1];
  const ref = typeof representation?.source_ref?.ref === 'string' ? representation.source_ref.ref : '';
  const filename = /(?:^|\/)(?:get-like-)?(.+)\.(?:png|jpe?g|webp)$/i.exec(ref.replace(/\\/g, '/'));
  return filename?.[1] ?? null;
}

import type { CanvasSliceBox, CanvasSliceRef } from "./materialCanvasModel.ts";

export interface InlineCanvasSlice extends CanvasSliceRef {
  note?: string;
  scene?: string;
  specialDisplay?: string;
  targetPath?: string;
  createdBy?: string;
  createdAt?: string;
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

const boxOf = (value: unknown): CanvasSliceBox | undefined => {
  if (!value || typeof value !== "object") return undefined;
  const box = value as Partial<CanvasSliceBox>;
  if (![box.x, box.y, box.width, box.height].every(finite)) return undefined;
  if ((box.width ?? 0) <= 0 || (box.height ?? 0) <= 0) return undefined;
  return {
    x: Math.max(0, Math.min(1, box.x as number)),
    y: Math.max(0, Math.min(1, box.y as number)),
    width: Math.max(0, Math.min(1, box.width as number)),
    height: Math.max(0, Math.min(1, box.height as number)),
  };
};

export function inlineCanvasSlices(material: Record<string, unknown> | undefined): InlineCanvasSlice[] {
  const metadata = material?.metadata;
  if (!metadata || typeof metadata !== "object") return [];
  const candidates = (metadata as Record<string, unknown>).canvas_slices;
  if (!Array.isArray(candidates)) return [];
  return candidates.flatMap((candidate): InlineCanvasSlice[] => {
    if (!candidate || typeof candidate !== "object") return [];
    const value = candidate as Record<string, unknown>;
    const kind = value.kind === "element" ? "element" : value.kind === "region" ? "region" : null;
    const ref = typeof value.ref === "string" ? value.ref.trim() : "";
    const box = boxOf(value.box);
    if (!kind || !ref || !box) return [];
    const text = (key: string) => typeof value[key] === "string" && value[key] ? String(value[key]) : undefined;
    return [{
      kind,
      ref,
      box,
      ...(text("representationId") ? { representationId: text("representationId") } : {}),
      ...(text("label") ? { label: text("label") } : {}),
      ...(value.emphasis === "normal" || value.emphasis === "highlight" ? { emphasis: value.emphasis } : {}),
      ...(text("note") ? { note: text("note") } : {}),
      ...(text("scene") ? { scene: text("scene") } : {}),
      ...(text("specialDisplay") ? { specialDisplay: text("specialDisplay") } : {}),
      ...(text("targetPath") ? { targetPath: text("targetPath") } : {}),
      ...(text("createdBy") ? { createdBy: text("createdBy") } : {}),
      ...(text("createdAt") ? { createdAt: text("createdAt") } : {}),
    }];
  });
}

export function withInlineCanvasSlice(
  material: Record<string, unknown> | undefined,
  slice: InlineCanvasSlice,
): Record<string, unknown> {
  const current = material ?? {};
  const metadata = current.metadata && typeof current.metadata === "object"
    ? current.metadata as Record<string, unknown>
    : {};
  const slices = inlineCanvasSlices(current).filter((item) => !(item.kind === slice.kind && item.ref === slice.ref));
  return { ...current, metadata: { ...metadata, canvas_slices: [...slices, slice] } };
}

export function normalizedDragBox(
  start: { x: number; y: number },
  end: { x: number; y: number },
): CanvasSliceBox | null {
  const left = Math.max(0, Math.min(1, Math.min(start.x, end.x)));
  const top = Math.max(0, Math.min(1, Math.min(start.y, end.y)));
  const right = Math.max(0, Math.min(1, Math.max(start.x, end.x)));
  const bottom = Math.max(0, Math.min(1, Math.max(start.y, end.y)));
  const width = right - left;
  const height = bottom - top;
  if (width < 0.012 || height < 0.012) return null;
  return { x: left, y: top, width, height };
}

export function desiredTextCardHeight(bodyScrollHeight: number): number {
  if (!Number.isFinite(bodyScrollHeight)) return 220;
  return Math.max(220, Math.min(960, Math.ceil(bodyScrollHeight + 56)));
}

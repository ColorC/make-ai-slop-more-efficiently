import { fitCardToIntrinsicRatio, type CanvasIntrinsicSize } from "./materialCanvasModel.ts";

export interface CanvasCardForCompaction {
  kind?: string;
  adapter?: string;
  material?: unknown;
}

export interface CanvasCardDimensions {
  width: number;
  height: number;
}

const MIN_WIDTH = 260;
const MIN_HEIGHT = 180;

function metadataOf(card: CanvasCardForCompaction): Record<string, unknown> {
  const envelope = card.material && typeof card.material === "object"
    ? card.material as Record<string, unknown>
    : undefined;
  return envelope?.metadata && typeof envelope.metadata === "object"
    ? envelope.metadata as Record<string, unknown>
    : {};
}

function capDimensions(current: CanvasCardDimensions, width: number, height: number): CanvasCardDimensions {
  return {
    width: Math.max(MIN_WIDTH, Math.min(current.width, width)),
    height: Math.max(MIN_HEIGHT, Math.min(current.height, height)),
  };
}

function fitDimensions(current: CanvasCardDimensions, width: number, height: number): CanvasCardDimensions {
  const scale = Math.min(1, width / current.width, height / current.height);
  if (scale >= 1) return current;
  const snap = (value: number) => Math.round(value / 10) * 10;
  return {
    width: Math.max(MIN_WIDTH, snap(current.width * scale)),
    height: Math.max(MIN_HEIGHT, snap(current.height * scale)),
  };
}

/**
 * 材料自身的固有尺寸。业主 2026-08-20：「所有图片和 demo 类尽量收缩到图片本身的比例」，
 * 「如果一个图片有尺寸，卡片自动和相同尺寸，不要进一步留出缺口」。
 *
 * 界面截图的固有尺寸来自采集真源（hierarchy JSON 的 screen_width/height，1080x1920），
 * 因此这里读 metadata 上的既有字段，不去猜、也不从当前卡片尺寸反推。
 */
export function intrinsicSizeOf(card: CanvasCardForCompaction): CanvasIntrinsicSize | null {
  const metadata = metadataOf(card);
  const pairs: Array<[unknown, unknown]> = [
    [metadata.intrinsic_width, metadata.intrinsic_height],
    [metadata.screen_width, metadata.screen_height],
    [metadata.natural_width, metadata.natural_height],
  ];
  for (const [width, height] of pairs) {
    if (typeof width === "number" && typeof height === "number" && width > 0 && height > 0) {
      return { width, height };
    }
  }
  // `capture_resolution: "1080x1920"` 是界面身份卡上已有的写法。
  const resolution = metadata.capture_resolution;
  if (typeof resolution === "string") {
    const match = /^(\d+)\s*[x×]\s*(\d+)$/.exec(resolution.trim());
    if (match) {
      const width = Number(match[1]);
      const height = Number(match[2]);
      if (width > 0 && height > 0) return { width, height };
    }
  }
  return null;
}

/** 有固有比例就按比例定尺（零留白），没有才回落到形态默认框。 */
function ratioOrBox(
  card: CanvasCardForCompaction,
  current: CanvasCardDimensions,
  boxWidth: number,
  boxHeight: number,
): CanvasCardDimensions {
  const intrinsic = intrinsicSizeOf(card);
  if (!intrinsic) return fitDimensions(current, boxWidth, boxHeight);
  // 目标面积取形态默认框的面积：整体体量不变，只把形状换成材料真实的形状。
  // minSide 与 core 的 resizeCanvasCard 下限(120x80)对齐：低于它会被另一处夹回去，
  // 比例又被悄悄改掉。两处下限必须一致，否则"零留白"只在没人拖动时成立。
  return fitCardToIntrinsicRatio(intrinsic, boxWidth * boxHeight, { minSide: 120, maxSide: 1400 });
}

/**
 * Smart layout is also the canvas' deterministic compacting command.
 * It only shrinks oversized cards; deliberately small/manual sizes are preserved.
 */
export function compactDimensionsForCard(
  card: CanvasCardForCompaction,
  current: CanvasCardDimensions,
): CanvasCardDimensions {
  if (card.kind === "group") return current;
  const metadata = metadataOf(card);
  const materialKind = typeof metadata.material_kind === "string" ? metadata.material_kind : "";
  const displayRole = typeof metadata.display_role === "string" ? metadata.display_role : "";

  if (card.adapter === "image" || materialKind === "image") return ratioOrBox(card, current, 360, 260);
  if (materialKind === "demo" || displayRole === "demonstration") return ratioOrBox(card, current, 360, 240);
  if (card.adapter === "video") return fitDimensions(current, 400, 280);
  if (card.adapter === "audio") return capDimensions(current, 360, 180);
  if (materialKind === "process" || ["agent-step", "script-step", "process-step"].includes(displayRole)) {
    return capDimensions(current, 340, 220);
  }
  if (["diff", "table", "form"].includes(materialKind)) return capDimensions(current, 500, 320);
  if (card.adapter === "pdf" || card.adapter === "web" || materialKind === "document") {
    return capDimensions(current, 420, 300);
  }
  if (card.kind === "text" || card.adapter === "text") return capDimensions(current, 360, 240);
  return capDimensions(current, 400, 280);
}

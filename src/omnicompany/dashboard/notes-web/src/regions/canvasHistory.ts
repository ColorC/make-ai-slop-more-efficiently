export interface CanvasHistoryItem {
  document_id: string;
  session_id: string;
  title: string;
  canvas_title: string;
  session_title: string;
  updated_at: string;
  card_count: number;
  favorite: boolean;
  current: boolean;
  snippet: string;
}

export interface CanvasHistoryResult {
  source: string;
  items: CanvasHistoryItem[];
  count: number;
  limit: number;
  current_id?: string;
  favorites?: string[];
}

export interface CanvasResolveResult {
  canvas_id: string;
  session_id: string;
  reason: string;
}

export type CanvasHistoryInvoke = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;

export function formatCanvasUpdatedAt(value: string): string {
  if (!value || value === "unknown") return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function canvasHistoryHref(currentHref: string, sessionId: string, canvasId?: string): string {
  const url = new URL(currentHref);
  const params = url.searchParams;
  if (params.has("session_id")) params.set("session_id", sessionId);
  else if (params.has("sid")) params.set("sid", sessionId);
  else params.set("session", sessionId);
  if (canvasId) params.set("canvas", canvasId);
  else params.delete("canvas");
  return url.toString();
}

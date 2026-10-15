import {
  canvasStorageId,
  parseCanvasDocument,
  type MaterialAdapter,
  type MaterialSource,
} from "./materialCanvasModel.ts";

export type MaterialLinkInvoke = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;

export interface ResolvedLinkCard {
  title: string;
  source: Extract<MaterialSource, { kind: "link" }>;
  adapter: MaterialAdapter;
  width: number;
  height: number;
  material: Record<string, unknown>;
}

export interface MaterialLinkResolverOptions {
  fetchImpl?: typeof fetch;
  now?: () => Date;
}

const importedRepresentationCache = new Map<string, Promise<string>>();

export function clearMaterialLinkResolverCache(): void {
  importedRepresentationCache.clear();
}

const importedMaterialEnvelope = (
  resolved: Record<string, unknown>,
  sourceUrl: string,
  resolver: string,
  attachedTo: string,
  now: () => Date,
) => ({
  material_ref: resolved.id,
  resolved_material: resolved,
  provenance: { resolver, imported_from: sourceUrl, imported_at: now().toISOString() },
  attached_to: { material_id: attachedTo },
});

const externalMaterial = (url: URL, mediaType: string, hint: string, title: string) => ({
  id: `material:external/url/${encodeURIComponent(url.href).slice(0, 180)}`,
  title,
  labels: ["外部材料"],
  revision_ref: { provider: "external-url", object: url.href, revision: "live" },
  representations: [{
    id: "primary",
    media_type: mediaType,
    role: "primary",
    source_ref: { kind: "external-url", ref: url.href },
    renderer_hint: hint,
  }],
});

const adapterForResolvedUrl = (url: URL): MaterialAdapter => {
  const path = url.pathname.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg|avif)$/.test(path)) return "image";
  if (/\.pdf$/.test(path)) return "pdf";
  if (/\.(mp3|wav|ogg|m4a|flac)$/.test(path)) return "audio";
  if (/\.(mp4|webm|mov|m4v)$/.test(path)) return "video";
  const currentOrigin = globalThis.location?.origin;
  return currentOrigin && url.origin === currentOrigin ? "web" : "link";
};

const diagnosticSnapshotId = (url: URL): string | null => {
  const match = url.pathname.match(/\/api\/diagnostics\/snapshots\/(snap-[a-z0-9]+-[a-f0-9]{8})\/?$/);
  return match?.[1] ?? null;
};

const finitePositive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

const snapshotCanvasSlice = (
  snapshot: Record<string, unknown>,
  snapshotId: string,
): Record<string, unknown> | null => {
  const viewport = snapshot.viewport as Record<string, unknown> | undefined;
  const selection = snapshot.selection as Record<string, unknown> | undefined;
  const rect = selection?.rect as Record<string, unknown> | undefined;
  if (!viewport || !selection || !rect
    || !finitePositive(viewport.width) || !finitePositive(viewport.height)
    || typeof rect.left !== "number" || typeof rect.top !== "number"
    || !finitePositive(rect.width) || !finitePositive(rect.height)) return null;
  const clamp = (value: number) => Math.max(0, Math.min(1, value));
  const x = clamp(rect.left / viewport.width);
  const y = clamp(rect.top / viewport.height);
  const width = clamp(Math.min(rect.width, viewport.width - rect.left) / viewport.width);
  const height = clamp(Math.min(rect.height, viewport.height - rect.top) / viewport.height);
  if (width <= 0 || height <= 0) return null;
  const text = typeof selection.text === "string" ? selection.text.trim() : "";
  const selector = typeof selection.selector === "string" ? selection.selector.trim() : "";
  const tag = typeof selection.tag === "string" ? selection.tag.trim() : "";
  return {
    kind: selection.mode === "element" ? "element" : "region",
    ref: `snapshot-selection-${snapshotId}`,
    box: { x, y, width, height },
    label: text.slice(0, 80) || tag || "快照选区",
    note: text ? `从 WebTwin 快照导入的已选元素：${text.slice(0, 240)}` : "从 WebTwin 快照导入的已选区域",
    ...(selector ? { targetPath: selector } : {}),
    emphasis: "highlight",
    createdBy: "workshop-page-capture",
    ...(typeof snapshot.capturedAt === "string" ? { createdAt: snapshot.capturedAt } : {}),
  };
};

export function wikiLocator(url: URL): { project: string; materialId?: string } | null {
  let project = url.searchParams.get("project") || url.searchParams.get("project_id") || "";
  let materialId = url.searchParams.get("material") || url.searchParams.get("material_id")
    || url.searchParams.get("wiki") || url.searchParams.get("open") || "";
  const hash = decodeURIComponent(url.hash.replace(/^#/, ""));
  const hashMatch = hash.match(/(?:^|[/:])(?:material-wiki|wiki):(.+)$/);
  if (!materialId && hashMatch) materialId = hashMatch[1];
  const pathMatch = decodeURIComponent(url.pathname).match(/\/(?:material-wiki|projects)\/([^/]+)\/(?:wiki\/)?(.+)$/);
  if (pathMatch) {
    project ||= pathMatch[1];
    materialId ||= pathMatch[2];
  }
  if (!project || (!materialId && !url.pathname.includes("wiki"))) return null;
  return { project, ...(materialId ? { materialId } : {}) };
}

export function canvasLocator(url: URL): { sessionId: string; focus?: string } | null {
  const canvasPath = url.pathname.includes("/bside/canvas")
    || url.pathname.includes("/api/embed/notes-web")
    || url.pathname.endsWith("/notes-web.html");
  if (!canvasPath) return null;
  let sessionId = url.searchParams.get("session_id") || url.searchParams.get("session") || "";
  if (!sessionId) {
    const pathMatch = decodeURIComponent(url.pathname).match(/\/bside\/canvas\/([^/?#]+)/);
    if (pathMatch) sessionId = pathMatch[1].startsWith("canvas:") ? pathMatch[1] : `canvas:${pathMatch[1]}`;
  }
  if (!sessionId) return null;
  const hash = decodeURIComponent(url.hash.replace(/^#/, ""));
  const focus = url.searchParams.get("focus") || hash.match(/(?:card=)?([^&]+)/)?.[1] || "";
  return { sessionId, ...(focus ? { focus } : {}) };
}

export async function resolveMaterialLink(
  rawUrl: string,
  invokeCommand: MaterialLinkInvoke,
  attachedTo: string,
  options: MaterialLinkResolverOptions = {},
): Promise<ResolvedLinkCard> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const now = options.now ?? (() => new Date());
  const url = new URL(rawUrl);
  const snapshotId = diagnosticSnapshotId(url);
  if (snapshotId) {
    const response = await fetchImpl(rawUrl, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`WebTwin 快照 ${response.status}`);
    const snapshot = await response.json() as Record<string, unknown>;
    const selection = snapshot.selection as Record<string, unknown> | undefined;
    const selectedText = typeof selection?.text === "string" ? selection.text.trim() : "";
    const title = selectedText ? `界面快照｜${selectedText.slice(0, 48)}` : "界面快照选区";
    const slice = snapshotCanvasSlice(snapshot, snapshotId);
    const resolved = {
      id: `material:workshop/snapshot/${snapshotId}`,
      title,
      labels: ["WebTwin 快照", slice ? "已含切片" : "页面快照"],
      revision_ref: { provider: "workshop-diagnostic-snapshot", object: snapshotId, revision: "captured" },
      representations: [{
        id: "snapshot-selection",
        media_type: "application/json",
        role: "primary",
        source_ref: { kind: "workshop-diagnostic-snapshot", ref: rawUrl },
        renderer_hint: "config",
        content: JSON.stringify({
          page: snapshot.page,
          capturedAt: snapshot.capturedAt,
          selection: snapshot.selection,
        }, null, 2),
        content_format: "json",
      }],
    };
    const material = importedMaterialEnvelope(resolved, rawUrl, "workshop-diagnostic-snapshot", attachedTo, now);
    return {
      title,
      source: { kind: "link", url: rawUrl },
      adapter: "text",
      width: 560,
      height: 420,
      material: {
        ...material,
        metadata: {
          snapshot_id: snapshotId,
          snapshot_page: snapshot.page,
          capture_resolution: finitePositive((snapshot.viewport as Record<string, unknown> | undefined)?.width)
            && finitePositive((snapshot.viewport as Record<string, unknown> | undefined)?.height)
            ? `${(snapshot.viewport as Record<string, unknown>).width}x${(snapshot.viewport as Record<string, unknown>).height}`
            : undefined,
          canvas_slices: slice ? [slice] : [],
        },
      },
    };
  }
  const canvas = canvasLocator(url);
  if (canvas) {
    const storageId = canvasStorageId(canvas.sessionId);
    const raw = await invokeCommand<string | null>("notes_canvas_get", { id: storageId }).catch(() => null);
    const document = parseCanvasDocument(raw, canvas.sessionId);
    const card = canvas.focus ? document?.cards.find((item) => item.id === canvas.focus) : undefined;
    const title = card?.title || document?.title || "Canvas 画布";
    const resolved = {
      id: card?.material && typeof card.material.id === "string"
        ? card.material.id : `material:omnidashboard/canvas/${storageId}${card ? `/${card.id}` : ""}`,
      title,
      labels: ["Canvas", card ? "内容块" : "画布"],
      revision_ref: { provider: "material-note", object: card ? `${storageId}#card=${card.id}` : storageId, revision: `r${document?.revision || 0}` },
      representations: [{
        id: card ? "canvas-card" : "canvas-document",
        media_type: card ? "text/markdown" : "application/vnd.omni.canvas+json",
        role: "primary",
        source_ref: { kind: "session-canvas", ref: `${storageId}${card ? `#card=${card.id}` : ""}` },
        renderer_hint: card ? "material-note-markdown" : "material-canvas-summary",
        content: card?.text || (document ? `${document.cards.length} 个材料 · ${document.relations.length} 条关系` : "Canvas 材料引用"),
        content_format: "text",
      }],
    };
    return {
      title,
      source: { kind: "link", url: rawUrl },
      adapter: "text",
      width: 520,
      height: 360,
      material: importedMaterialEnvelope(resolved, rawUrl, "session-canvas", attachedTo, now),
    };
  }

  const wiki = wikiLocator(url);
  if (wiki) {
    const graphResponse = await fetchImpl(`/api/boss-sight/material-wiki/projects/${encodeURIComponent(wiki.project)}/graph`);
    if (!graphResponse.ok) throw new Error(`Material Wiki ${graphResponse.status}`);
    const graph = await graphResponse.json() as { active_view_id?: string; materials?: Array<Record<string, unknown>> };
    const materials = graph.materials || [];
    const selected = materials.find((item) => item.id === wiki.materialId || item.id === `material-wiki:${wiki.materialId}`)
      || materials.find((item) => typeof item.id === "string" && wiki.materialId && item.id.endsWith(`/${wiki.materialId}`))
      || materials.find((item) => item.id === graph.active_view_id)
      || materials.find((item) => Array.isArray(item.representations));
    if (!selected) throw new Error("Material Wiki 链接未定位到材料");
    const resolved = structuredClone(selected);
    const representations = Array.isArray(resolved.representations) ? resolved.representations as Array<Record<string, unknown>> : [];
    const primary = representations.find((item) => item.role === "primary") || representations[0];
    const sourceRef = primary?.source_ref as { kind?: string; ref?: string } | undefined;
    if (primary && sourceRef?.kind === "material-wiki-api" && typeof sourceRef.ref === "string") {
      const revision = resolved.revision_ref as { provider?: string; object?: string; revision?: string } | undefined;
      const cacheKey = [resolved.id, revision?.provider, revision?.object, revision?.revision, primary.id].join("\n");
      if (!importedRepresentationCache.has(cacheKey)) {
        importedRepresentationCache.set(cacheKey, fetchImpl(sourceRef.ref).then(async (response) => {
          if (!response.ok) throw new Error(`Material Wiki 正文 ${response.status}`);
          const payload = await response.json() as { versions?: Array<{ body?: string }> };
          const body = payload.versions?.[0]?.body;
          if (typeof body !== "string") throw new Error("Material Wiki 正文为空");
          return body;
        }));
      }
      primary.content = await importedRepresentationCache.get(cacheKey);
      primary.content_format = "markdown";
    }
    const title = typeof resolved.title === "string" ? resolved.title : "Material Wiki 文档";
    return {
      title,
      source: { kind: "link", url: rawUrl },
      adapter: "text",
      width: 560,
      height: 420,
      material: importedMaterialEnvelope(resolved, rawUrl, "material-wiki", attachedTo, now),
    };
  }

  const isFeishu = /(^|\.)feishu\.cn$|(^|\.)larksuite\.com$/.test(url.hostname)
    && /^\/(docx|docs|wiki|sheets|base)\//.test(url.pathname);
  if (isFeishu) {
    const resolved = externalMaterial(url, "application/vnd.feishu.document", "feishu-document", "collab platform云文档");
    resolved.labels.push("collab platform");
    resolved.representations[0].source_ref.kind = "feishu-document";
    return {
      title: "collab platform云文档",
      source: { kind: "link", url: rawUrl },
      adapter: "link",
      width: 480,
      height: 280,
      material: importedMaterialEnvelope(resolved, rawUrl, "feishu-document", attachedTo, now),
    };
  }

  const adapter = adapterForResolvedUrl(url);
  const mediaType = adapter === "image" ? "image/*" : adapter === "pdf" ? "application/pdf"
    : adapter === "audio" ? "audio/*" : adapter === "video" ? "video/*" : "text/uri-list";
  const title = url.hostname || rawUrl;
  const resolved = externalMaterial(url, mediaType, adapter === "link" ? "external-link" : adapter, title);
  return {
    title,
    source: { kind: "link", url: rawUrl },
    adapter,
    width: adapter === "link" ? 420 : 560,
    height: adapter === "link" ? 250 : 420,
    material: importedMaterialEnvelope(resolved, rawUrl, adapter === "link" ? "generic-url" : "media-url", attachedTo, now),
  };
}

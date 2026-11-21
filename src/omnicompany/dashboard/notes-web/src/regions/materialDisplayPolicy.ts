interface MaterialDisplayCard {
  kind?: unknown;
  adapter?: unknown;
  source?: { kind?: unknown; url?: unknown } | null;
  material?: Record<string, unknown> | null;
}

/** Local HTML reports are files, but their semantic adapter is a web surface. */
export function usesHtmlFilePreview(card: MaterialDisplayCard, mime: unknown): boolean {
  if (card.kind !== "material" || card.source?.kind !== "file") return false;
  const normalizedMime = typeof mime === "string" ? mime.toLowerCase().split(";", 1)[0].trim() : "";
  return card.adapter === "web" && normalizedMime === "text/html";
}

/** Canonical Material references delegate rendering to their representation plugin host. */
export function usesMaterialDisplaySurface(card: MaterialDisplayCard): boolean {
  if (card.kind !== "material" || card.source?.kind !== "link") return false;
  if (card.adapter !== "web" && card.adapter !== "pdf") return false;
  const envelope = card.material;
  return Boolean(
    envelope
    && typeof envelope.material_ref === "string"
    && envelope.material_ref
    && typeof envelope.representation_id === "string"
    && envelope.representation_id,
  );
}

/** Workspace Material URLs expose a document payload that can be rendered without a nested app. */
export function usesNativeMaterialDocument(card: MaterialDisplayCard): boolean {
  if (!usesMaterialDisplaySurface(card)) return false;
  const url = card.source?.url;
  return typeof url === "string" && /(?:^|\/)materials(?:[/?#]|$)/.test(url);
}

const INTERNAL_HOSTS = new Set(["localhost", "127.0.0.1"]);
const HOST_ROOT_SURFACES = ["/spark-webtwin"];

export function bridgePrefix(search: string): string {
  const candidate = new URLSearchParams(search).get("bridge_prefix") ?? "";
  if (!/^\/(?:[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*)?$/.test(candidate)) return "";
  if (candidate.split("/").includes("..")) return "";
  return candidate.replace(/\/$/, "");
}

/**
 * Resolve a URL that is about to leave notes-web and enter another browser surface.
 * Persisted canvases may still contain direct 5460/localhost links or root-absolute
 * Workshop paths. Both must follow the host iframe's bridge_prefix on 8210.
 */
export function resolveBrowserNavigationUrl(
  rawUrl: string,
  currentHref = typeof location === "undefined" ? "http://localhost/" : location.href,
  search = typeof location === "undefined" ? "" : location.search,
): string {
  if (!rawUrl.trim()) return rawUrl;
  let current: URL;
  let target: URL;
  try {
    current = new URL(currentHref);
    target = new URL(rawUrl, current);
  } catch {
    return rawUrl;
  }
  if (target.protocol !== "http:" && target.protocol !== "https:") return rawUrl;

  const absolute = /^[A-Za-z][A-Za-z0-9+.-]*:/.test(rawUrl);
  const internalAbsolute = absolute && (target.origin === current.origin || INTERNAL_HOSTS.has(target.hostname));
  const internalPath = !absolute || rawUrl.startsWith("/");
  if (!internalAbsolute && !internalPath) return rawUrl;

  const prefix = bridgePrefix(search);
  let pathname = target.pathname.startsWith("/") ? target.pathname : `/${target.pathname}`;
  // WebTwin is an 8210 host-level product surface, not a child of the canvas
  // API bridge. Prefixing it with /bside or /workshop produces a valid page
  // shell but no #stage, so a playable state card appears to do nothing.
  if (HOST_ROOT_SURFACES.some((root) => pathname === root || pathname.startsWith(`${root}/`))) {
    return `${pathname}${target.search}${target.hash}`;
  }
  if (prefix && pathname !== prefix && !pathname.startsWith(`${prefix}/`)) pathname = `${prefix}${pathname}`;
  return `${pathname}${target.search}${target.hash}`;
}

export function materialRepresentationFileUrl(
  sourceRef: string,
  currentHref = typeof location === "undefined" ? "http://localhost/" : location.href,
  search = typeof location === "undefined" ? "" : location.search,
): string {
  return resolveBrowserNavigationUrl(
    `/api/material-workspace/representation-file?ref=${encodeURIComponent(sourceRef)}`,
    currentHref,
    search,
  );
}

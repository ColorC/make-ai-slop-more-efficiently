import { resolveBrowserNavigationUrl } from "./browserNavigation.ts";

/**
 * Material cards may have been persisted from either the /workshop or /bside
 * mount. The document API must follow the mount of the notes-web iframe that
 * is rendering the card; deriving it from the persisted link can accidentally
 * create /bside/workshop/api/... and return the Workshop HTML shell.
 */
export function nativeMaterialDocumentUrl(
  materialId: string,
  representationId = "",
  currentHref = typeof location === "undefined" ? "http://localhost/" : location.href,
  search = typeof location === "undefined" ? "" : location.search,
): URL {
  const resolved = resolveBrowserNavigationUrl(
    "/api/material-workspace/document",
    currentHref,
    search,
  );
  const endpoint = new URL(resolved, currentHref);
  endpoint.searchParams.set("id", materialId);
  endpoint.searchParams.set("mode", "expanded");
  if (representationId) endpoint.searchParams.set("representation", representationId);
  return endpoint;
}

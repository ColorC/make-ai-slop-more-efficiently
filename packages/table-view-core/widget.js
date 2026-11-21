import { createTableViewRuntime, TABLE_VIEW_CSS, TABLE_VIEW_UI } from "./render.js";

const STYLE_ATTRIBUTE = "data-omni-table-view-core";

function ensureStyles(document) {
  if (document.head.querySelector(`style[${STYLE_ATTRIBUTE}]`)) return;
  const style = document.createElement("style");
  style.setAttribute(STYLE_ATTRIBUTE, "");
  style.textContent = TABLE_VIEW_CSS;
  document.head.appendChild(style);
}

/**
 * Mount the shared vanilla DOM renderer into an existing browser element.
 * Calling it again on the same element replaces the prior view.
 */
export function mountTableView(element, report) {
  if (!element || element.nodeType !== 1 || !element.ownerDocument) {
    throw new TypeError("mountTableView(element, report) requires a DOM element");
  }
  ensureStyles(element.ownerDocument);
  const host = element.ownerDocument.defaultView || globalThis;
  return createTableViewRuntime(host, TABLE_VIEW_UI).mount(element, report);
}

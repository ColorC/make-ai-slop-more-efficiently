/** Console ring + snapshot / inspect bridge for the workshop 画布 thin bar. */

export type NotesLogEntry = { t: number; level: "error" | "warn"; message: string };

const logs: NotesLogEntry[] = [];
const MAX_LOGS = 80;
let inspectOn = false;
let overlay: HTMLDivElement | null = null;

function pushLog(level: NotesLogEntry["level"], parts: unknown[]): void {
  const message = parts.map((part) => {
    if (part instanceof Error) return part.stack || part.message;
    try { return typeof part === "string" ? part : JSON.stringify(part); } catch { return String(part); }
  }).join(" ");
  logs.push({ t: Date.now(), level, message: message.slice(0, 4000) });
  if (logs.length > MAX_LOGS) logs.shift();
}

function cssPath(el: Element): string {
  const parts: string[] = [];
  let node: Element | null = el;
  while (node && node.nodeType === 1 && parts.length < 8) {
    const id = node.id ? `#${node.id}` : "";
    const cls = [...node.classList].slice(0, 2).map((name) => `.${name}`).join("");
    parts.unshift(`${node.tagName.toLowerCase()}${id}${cls}`);
    node = node.parentElement;
  }
  return parts.join(" > ");
}

function pickFromPoint(x: number, y: number): Record<string, unknown> {
  const stack = document.elementsFromPoint(x, y).filter((el) => el !== overlay && !el.closest("[data-notes-inspect]"));
  const el = stack[0];
  if (!el) return { x, y };
  const iframe = el instanceof HTMLIFrameElement ? el : el.closest("iframe");
  let inner: Record<string, unknown> | undefined;
  if (iframe instanceof HTMLIFrameElement) {
    try {
      const doc = iframe.contentDocument;
      if (doc) {
        const rect = iframe.getBoundingClientRect();
        const nested = doc.elementFromPoint(x - rect.left, y - rect.top);
        if (nested) inner = { tag: nested.tagName.toLowerCase(), text: (nested.textContent || "").trim().slice(0, 240), selector: cssPath(nested) };
      }
    } catch { /* cross-origin */ }
  }
  const rect = el.getBoundingClientRect();
  return {
    x, y,
    tag: el.tagName.toLowerCase(),
    text: (el.textContent || "").trim().slice(0, 240),
    selector: cssPath(el),
    rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
    iframeSrc: iframe instanceof HTMLIFrameElement ? iframe.getAttribute("src") || iframe.src : undefined,
    inner,
  };
}

export function collectNotesSnapshot(pick?: Record<string, unknown>): Record<string, unknown> {
  const root = document.querySelector("[data-material-canvas]");
  return {
    at: new Date().toISOString(),
    url: location.href,
    theme: document.documentElement.dataset.notesTheme || "",
    state: root?.getAttribute("data-material-canvas") || "",
    title: document.querySelector(".material-canvas-menu-trigger strong")?.textContent || document.title,
    logs: logs.slice(-MAX_LOGS),
    pick: pick ?? null,
    html: (document.querySelector(".material-notes-workspace")?.outerHTML || document.body.innerHTML).slice(0, 180_000),
  };
}

function stopInspect(): void {
  inspectOn = false;
  overlay?.remove();
  overlay = null;
}

function startInspect(): void {
  if (inspectOn) return;
  inspectOn = true;
  overlay = document.createElement("div");
  overlay.dataset.notesInspect = "1";
  overlay.style.cssText = "position:fixed;inset:0;z-index:20000;cursor:crosshair;background:transparent;";
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;border:2px solid #A85E38;background:rgba(168,94,56,.12);pointer-events:none;";
  overlay.appendChild(box);
  const move = (event: PointerEvent) => {
    const hit = document.elementsFromPoint(event.clientX, event.clientY).find((el) => el !== overlay && el !== box);
    if (!(hit instanceof Element)) return;
    const rect = hit.getBoundingClientRect();
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
  };
  overlay.addEventListener("pointermove", move);
  overlay.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const pick = pickFromPoint(event.clientX, event.clientY);
    stopInspect();
    const payload = collectNotesSnapshot(pick);
    if (window.parent !== window) window.parent.postMessage({ type: "workshop-canvas-snapshot-result", kind: "pick", payload }, "*");
  });
  window.addEventListener("keydown", function onKey(event) {
    if (event.key !== "Escape") return;
    window.removeEventListener("keydown", onKey);
    stopInspect();
  });
  document.body.appendChild(overlay);
}

export function installNotesDiagnostics(): void {
  const wrap = (level: "error" | "warn") => {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      pushLog(level, args);
      original(...args);
    };
  };
  wrap("error");
  wrap("warn");
  window.addEventListener("error", (event) => {
    pushLog("error", [event.message, event.filename, event.lineno]);
    if (window.parent !== window) {
      window.parent.postMessage({ type: "omni-notes-error", error: event.message || String(event.error || "error") }, "*");
    }
  });
  window.addEventListener("unhandledrejection", (event) => {
    pushLog("error", [event.reason]);
    if (window.parent !== window) {
      window.parent.postMessage({ type: "omni-notes-error", error: String(event.reason) }, "*");
    }
  });
  window.addEventListener("message", (event) => {
    const data = event.data as { type?: string } | null;
    if (!data || typeof data !== "object") return;
    if (data.type === "workshop-canvas-snapshot") {
      const payload = collectNotesSnapshot();
      if (window.parent !== window) window.parent.postMessage({ type: "workshop-canvas-snapshot-result", kind: "snapshot", payload }, "*");
    }
    if (data.type === "workshop-canvas-inspect") startInspect();
  });
}

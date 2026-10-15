import "./tauri-web-shim";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MaterialNotesWorkspace } from "./regions/MaterialNotesWorkspace";
import { applyNotesAppearance } from "./regions/notesAppearance";
import { installNotesDiagnostics } from "./regions/notesDiagnostics";

applyNotesAppearance();
installNotesDiagnostics();

class NotesErrorBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(error: Error) { return { error: error.message || String(error) }; }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[notes-web]", error, info.componentStack);
    if (window.parent !== window) {
      window.parent.postMessage({ type: "omni-notes-error", error: error.message || String(error) }, "*");
    }
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="material-canvas-boot error" data-material-canvas="error">
        <strong>画布渲染失败</strong>
        <span>{this.state.error}</span>
        <button type="button" onClick={() => window.location.reload()}>重试</button>
      </div>
    );
  }
}

export interface MaterialNotesMountOptions {
  sessionId?: string;
  sessionTitle?: string;
  canvasId?: string;
  onReady?: () => void;
  onError?: (error: string) => void;
}

export function notesCanvasUrl(query: Record<string, string | undefined> = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value) params.set(key, value);
  }
  const suffix = params.toString();
  return `/lofa/overlay/app/notes-web.html${suffix ? `?${suffix}` : ""}`;
}

export function mountMaterialNotes(
  container: HTMLElement,
  options: MaterialNotesMountOptions = {},
): () => void {
  const root: Root = createRoot(container);
  root.render(
    <NotesErrorBoundary>
      <MaterialNotesWorkspace
        sessionId={options.sessionId}
        sessionTitle={options.sessionTitle}
        canvasId={options.canvasId}
        onReady={options.onReady}
        onError={options.onError}
      />
    </NotesErrorBoundary>,
  );
  return () => root.unmount();
}

/** Dashboard notes-embed still imports this name. */
export const mountWorkshopCanvas = mountMaterialNotes;

const app = document.getElementById("app");
if (app) {
  const params = new URLSearchParams(window.location.search);
  mountMaterialNotes(app, {
    sessionId: params.get("session") || params.get("session_id") || params.get("sid") || undefined,
    sessionTitle: params.get("session_title") || undefined,
    canvasId: params.get("canvas") || undefined,
  });
}

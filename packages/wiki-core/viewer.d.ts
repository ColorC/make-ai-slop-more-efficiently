export interface WikiViewerHandle {
  open(pageOrPath: string): void;
  refresh(): Promise<void>;
  getCurrent(): { path: string; content: string } | null;
  destroy(): void;
}
import type { CommentStore } from "./comments";

export interface WikiViewerOptions {
  apiBase?: string;
  assetBase?: string;
  page?: string;
  editable?: boolean;
  comments?: CommentStore;
  onState?: (state: { page: string; missing: boolean }) => void;
  /** 文档内 demo://<tourId>#<stepId> 链接被点：宿主跳到对应演示步。 */
  onDemoLink?: (tourId: string, stepId: string | null) => void;
  /** 文档内 mat://<mat_id>[#frag] 链接被点：宿主打开该审阅材料。 */
  onMaterialLink?: (materialId: string, fragment: string | null) => void;
}
export declare function mountWikiViewer(rootEl: HTMLElement, opts?: WikiViewerOptions): WikiViewerHandle;

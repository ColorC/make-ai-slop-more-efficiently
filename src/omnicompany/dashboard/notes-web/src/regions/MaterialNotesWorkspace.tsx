import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  ConnectionMode,
  Controls,
  EdgeLabelRenderer,
  EdgeToolbar,
  Handle,
  MarkerType,
  MiniMap,
  NodeResizer,
  NodeToolbar,
  Position,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  getBezierPath,
  getSmoothStepPath,
  getStraightPath,
  getViewportForBounds,
  reconnectEdge,
  useReactFlow,
  useUpdateNodeInternals,
  useViewport,
  type Connection,
  type Edge,
  type EdgeChange,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  ArrowUpRight,
  FilePlus2,
  FileText,
  Link2,
  Loader2,
  Maximize2,
  Minimize2,
  Pencil,
  Play,
  Redo2,
  RotateCcw,
  Search,
  Send,
  Snowflake,
  Sun,
  Copy,
  Crop,
  Group,
  ChevronDown,
  LayoutGrid,
  Lock,
  LockOpen,
  ImageIcon,
  File,
  GitBranch,
  Table2,
  Menu as MenuIcon,
  History,
  Trash2,
  Undo2,
  Ungroup,
  X,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import "./material-notes.css";
import { NativeMaterialView } from "./material-native-views";
import {
  applyCanvasMutation,
  adapterForLink,
  canvasPatchIsEmpty,
  canvasStorageId,
  diffCanvasDocuments,
  emptyCanvasDocument,
  formatCanvasCardReference,
  normalizeLink,
  parseCanvasDocument,
  repairDisplayText,
  safeRandomId,
  type CanvasCardRecord,
  type CanvasMutationPatch,
  type CanvasLabelPosition,
  type CanvasPoint,
  canvasSliceHandleId,
  resolveCardPresentation,
  titleVisibleAtZoom,
  type CanvasRelationRecord,
  type CanvasSliceRef,
  type CardSubmission,
  type MaterialAdapter,
  type MaterialCanvasDocument,
  type MaterialSource,
  type SubmissionState,
} from "./materialCanvasModel";
import { resolveMaterialLink, type ResolvedLinkCard } from "./materialLinkResolver";
import { materialRepresentationFileUrl, resolveBrowserNavigationUrl } from "./browserNavigation";
import { nativeMaterialDocumentUrl } from "./nativeMaterialDocument";
import { usesHtmlFilePreview, usesMaterialDisplaySurface, usesNativeMaterialDocument } from "./materialDisplayPolicy";
import {
  annotationStageId,
  parseAnnotationDocument,
  type AnnotationAnchor,
} from "./annotationAnchors";
import { registerCanvasHandler, type CanvasOpArgs } from "./canvasOps";
import { lookupCanvasSlice, publishCanvasSlices } from "./canvasSliceRegistry";
import { MaterialDocView, type DocCard, type DocRelation } from "./MaterialDocView";
import { notesReviewEnabled, resolveNotesAppearance } from "./notesAppearance";
import { layoutMaterialCanvas } from "./materialCanvasLayout";
import { compactDimensionsForCard } from "./materialCanvasCompaction";
import {
  desiredTextCardHeight,
  inlineCanvasSlices,
  normalizedDragBox,
  withInlineCanvasSlice,
  type InlineCanvasSlice,
} from "./manualCanvasSlices";
import {
  ReviewThumb,
  SubmissionVerdictBar,
  streamUrl,
  type CanvasHead,
  type CanvasMutationResult,
  type ReviewItemData,
} from "./reviewShared";
import { webNotesInvoke } from "../webNotesInvoke";
import { copyPlainText } from "../webRuntimeCompat";
import { renderMarkdownSafe } from "../lib/sanitizeHtml";
import { CanvasHistoryPanel } from "./CanvasHistoryPanel";
import type { CanvasHistoryItem, CanvasResolveResult } from "./canvasHistory";

type ResizeDirection =
  | "top"
  | "right"
  | "bottom"
  | "left"
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right";

interface CardData extends Record<string, unknown>, CanvasCardRecord {
  draft?: boolean;
}

/** 标题外置时为那行字预留的上方净空（px, 画布坐标）。与 CSS 的 bottom: calc(100% + 6px) 一致。 */
const TITLE_ABOVE_BAND = 24;

type CanvasNode = Node<CardData, "materialCard" | "groupCard">;
type AutoLayoutScope = { kind: "all" } | { kind: "top-level" } | { kind: "group"; groupId: string };
interface RelationData extends Record<string, unknown> {
  path: "curve" | "straight" | "orthogonal";
  arrow: "closed" | "open" | "none";
  /** M2 文档投影分类: continues 主线 / references 旁证 / derived 派生; 缺省未定 */
  kind?: "continues" | "references" | "derived";
  programLabel?: string;
  userLabel?: string;
  routePoints?: CanvasPoint[];
  programLabelPosition?: CanvasLabelPosition;
  userLabelPosition?: CanvasLabelPosition;
  /**
   * 端点切片（"从这张界面图里的这个按钮出发"）。它是端点权威，handle 只是派生缓存。
   * 自动排布看到它就不再按 dx/dy 改写端点 —— 此前那一步把切片端点一律推平成最近的一边。
   */
  sourceSlice?: CanvasSliceRef;
  targetSlice?: CanvasSliceRef;
}
type CanvasEdge = Edge<RelationData, "materialRelation">;

interface CanvasSnapshot {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  /** 视口(平移/缩放也可撤回); 老快照没有此字段 → restore 时跳过 */
  viewport?: { x: number; y: number; zoom: number };
}

// CanvasHead / CanvasMutationResult / streamUrl / ReviewItemData / ReviewThumb /
// SubmissionVerdictBar 已抽到 ./reviewShared(2026-08-14, dashboard 审阅台同源复用)。

interface FileHit {
  kind: "file" | "folder";
  name: string;
  path: string;
  open_token: string;
  mime?: string;
}

interface LegacyNote {
  id: string;
  title: string;
  updatedDate?: number;
  hasMarkdown: boolean;
}

export interface MaterialNotesWorkspaceProps {
  sessionId?: string;
  sessionTitle?: string;
  /** Explicit canvas document id for this session window; resolved if omitted. */
  canvasId?: string;
  /** 首开聚焦卡(M1b 深链/M4 内嵌宿主跳转); 缺省读 URL ?focus= 参数 */
  focusCardId?: string;
  onReady?: () => void;
  onError?: (error: string) => void;
  invokeCommand?: MaterialNotesInvoke;
}

export type MaterialNotesInvoke = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;

const tauriInvoke: MaterialNotesInvoke = (command, args) => invoke(command, args);
const InvokeContext = createContext<MaterialNotesInvoke>(tauriInvoke);

const importedMaterialEnvelope = (
  resolved: Record<string, unknown>,
  sourceUrl: string,
  resolver: string,
  attachedTo: string,
) => ({
  material_ref: resolved.id,
  resolved_material: resolved,
  provenance: { resolver, imported_from: sourceUrl, imported_at: new Date().toISOString() },
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

interface TextMaterialPayload extends FileHit {
  editableId: string;
  content?: string;
}

interface FileInspection extends FileHit {
  preview: "text" | "image" | "pdf" | "audio" | "video" | "directory" | "none";
  content?: string;
  truncated?: boolean;
  items?: FileHit[];
}

interface CardActions {
  activateCard: (id: string) => void;
  changeText: (id: string, value: string) => void;
  hydrateText: (id: string, material: TextMaterialPayload) => void;
  beginTextEdit: () => void;
  finishTextEdit: (id: string) => void;
  editingTextId: string | null;
  startTextEditing: (id: string) => void;
  stopTextEditing: (id?: string) => void;
  setTextSize: (id: string, fontSize: number) => void;
  fitTextCardHeight: (id: string, bodyScrollHeight: number, persist: boolean) => void;
  togglePositionLock: (id: string) => void;
  removeCard: (id: string) => void;
  toggleFullscreen: (id: string) => void;
  /** 触屏长按等价右键: 在 (x, y) 打开该卡菜单。 */
  openMenu: (id: string, x: number, y: number) => void;
  freezeCard: (id: string) => Promise<unknown>;
  unfreezeCard: (id: string) => { created: string };
  copyCardRef: (id: string) => Promise<string>;
  renameGroup: (id: string, title: string) => void;
  setSamePositionVariant: (id: string, variantKey: string) => void;
  sliceEditingId: string | null;
  beginRegionSlice: (id: string) => void;
  cancelRegionSlice: () => void;
  addRegionAnnotation: (id: string, draft: RegionAnnotationDraft) => void;
  finishGroupRename: (id: string) => void;
  resizeGroup: (id: string, params: { x: number; y: number; width: number; height: number } | null, phase: "start" | "move" | "end") => void;
  submitForReview: (id: string) => void;
  verdictCard: (id: string, pass: boolean, reason?: string) => void;
  reviewEnabled: boolean;
  editGroupBody: (id: string) => void;
  freezingId: string | null;
  beginResize: (
    id: string,
    direction: ResizeDirection,
    event: ReactPointerEvent<HTMLDivElement>,
    zoom: number
  ) => void;
}

interface RegionAnnotationDraft {
  box: NonNullable<CanvasSliceRef["box"]>;
  note: string;
  scene: string;
  specialDisplay: string;
}

interface RelationActions {
  update: (id: string, patch: {
    userLabel?: string;
    routePoints?: CanvasPoint[];
    userLabelPosition?: CanvasLabelPosition;
    path?: RelationData["path"];
    arrow?: RelationData["arrow"];
    kind?: RelationData["kind"] | null;
  }) => void;
  select: (id: string) => void;
  finish: (id: string) => void;
  editingId: string | null;
}

interface CardHoverState {
  id: string | null;
  hold: (id: string, active: boolean) => void;
}

interface LiveWindowRequest {
  url: string;
  title: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

const CardActionsContext = createContext<CardActions | null>(null);
const RelationActionsContext = createContext<RelationActions | null>(null);
// omni canvas highlight op 的聚光灯卡 id(不进 node data, 避免渗进持久化文档)
const SpotlightContext = createContext<string | null>(null);
const HoveredCardContext = createContext<CardHoverState | null>(null);
const LiveWindowContext = createContext<((request: Pick<LiveWindowRequest, "url" | "title">) => void) | null>(null);
const RESIZE_DIRECTIONS: ResizeDirection[] = [
  "top-left", "top", "top-right", "right",
  "bottom-right", "bottom", "bottom-left", "left",
];

function sessionIdentity(): { id: string; label: string } {
  const embedded = document.querySelector<HTMLMetaElement>('meta[name="omni-notes-session"]')?.content;
  if (embedded) {
    const external = decodeURIComponent(embedded);
    const title = document.querySelector<HTMLMetaElement>('meta[name="omni-notes-title"]')?.content;
    return { id: `external:${external}`, label: title || "会话札记" };
  }
  const params = new URLSearchParams(window.location.search);
  const external = params.get("session_id") || params.get("session") || params.get("sid");
  if (external) {
    return { id: `external:${external}`, label: params.get("session_title") || "会话札记" };
  }
  const key = "overlay-material-canvas-session";
  let fallback = sessionStorage.getItem(key);
  if (!fallback) {
    fallback = safeRandomId("browser:");
    sessionStorage.setItem(key, fallback);
  }
  return { id: fallback, label: "札记" };
}

function cloneNodes(nodes: CanvasNode[]): CanvasNode[] {
  return nodes.map((node) => ({
    ...node,
    data: { ...node.data, source: node.data.source ? { ...node.data.source } : undefined },
    position: { ...node.position },
    style: { ...node.style },
    selected: false,
  }));
}

function snapshotOf(nodes: CanvasNode[], edges: CanvasEdge[], viewport?: { x: number; y: number; zoom: number }): CanvasSnapshot {
  return {
    nodes: cloneNodes(nodes),
    edges: edges.map((edge) => ({ ...edge, selected: false })),
    ...(viewport ? { viewport: { ...viewport } } : {}),
  };
}

function dimensionsOf(node: CanvasNode): { width: number; height: number } {
  return {
    width: Math.max(260, Number(node.style?.width) || node.measured?.width || node.width || 440),
    height: Math.max(180, Number(node.style?.height) || node.measured?.height || node.height || 320),
  };
}

function nodeFromCard(card: CanvasCardRecord, draft = false): CanvasNode {
  return {
    id: card.id,
    type: card.kind === "group" ? "groupCard" : "materialCard",
    draggable: !card.positionLock?.locked,
    position: { x: card.x, y: card.y },
    // 子卡: store 里 x/y 已是相对组卡的坐标, 直接进 position; extent=parent 钳在组内
    ...(card.parentId ? { parentId: card.parentId, extent: "parent" as const } : {}),
    style: { width: card.width, height: card.height },
    data: { ...card, draft },
  };
}

function edgeFromRelation(relation: CanvasRelationRecord): CanvasEdge {
  const arrow = relation.arrow || "closed";
  return {
    id: relation.id,
    type: "materialRelation",
    source: relation.source,
    target: relation.target,
    sourceHandle: relation.sourceHandle,
    targetHandle: relation.targetHandle,
    data: {
      path: relation.path || "curve",
      arrow,
      kind: relation.kind,
      programLabel: relation.programLabel,
      userLabel: relation.userLabel ?? relation.label,
      routePoints: relation.routePoints,
      programLabelPosition: relation.programLabelPosition,
      userLabelPosition: relation.userLabelPosition,
      sourceSlice: relation.sourceSlice,
      targetSlice: relation.targetSlice,
    },
    markerEnd: relationMarker(arrow),
  };
}

// React Flow 要求父节点在数组里先于子节点(载入与新建组后都要满足)。
// M2: 真正的多层拓扑——祖父先于父先于子; parentId 悬空/成环的节点按 0 层处理不死循环。
function parentsFirst(nodes: CanvasNode[]): CanvasNode[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const depthCache = new Map<string, number>();
  const depthOf = (node: CanvasNode): number => {
    const cached = depthCache.get(node.id);
    if (cached !== undefined) return cached;
    depthCache.set(node.id, 0); // 占位防环
    let depth = 0;
    let current = node;
    const visited = new Set<string>([node.id]);
    while (current.parentId) {
      const parent = byId.get(current.parentId);
      if (!parent || visited.has(parent.id)) break;
      visited.add(parent.id);
      depth += 1;
      current = parent;
    }
    depthCache.set(node.id, depth);
    return depth;
  };
  return [...nodes].sort((a, b) => depthOf(a) - depthOf(b)); // 稳定排序, 同层保持原相对序
}

/** 同位成员只跟随所属容器的一个共享筛选器；普通子卡与容器本身始终显示。 */
function hiddenSamePositionNodeIds(nodes: CanvasNode[]): Set<string> {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const hidden = new Set<string>();
  const isHidden = (node: CanvasNode, visiting = new Set<string>()): boolean => {
    if (hidden.has(node.id)) return true;
    if (!node.parentId || visiting.has(node.id)) return false;
    const parent = byId.get(node.parentId);
    if (!parent) return false;
    const nextVisiting = new Set(visiting).add(node.id);
    if (isHidden(parent, nextVisiting)) {
      hidden.add(node.id);
      return true;
    }
    const container = parent.data.samePositionContainer;
    const member = node.data.samePositionMember;
    if (container && member && member.variantKey !== container.activeVariant) {
      hidden.add(node.id);
      return true;
    }
    return false;
  };
  nodes.forEach((node) => { void isHidden(node); });
  return hidden;
}

// 严重退化判定: >80% 节点 x 相同(全默认位/未排布) → 加载后自动排一次
function isDegenerateLayout(nodes: CanvasNode[]): boolean {
  if (nodes.length < 4) return false;
  const byX = new Map<number, number>();
  for (const node of nodes) byX.set(node.position.x, (byX.get(node.position.x) ?? 0) + 1);
  const maxSame = Math.max(...byX.values());
  return maxSame / nodes.length > 0.8;
}

function allowsAutoLayout(node: CanvasNode): boolean {
  const kind = node.data.positionSource?.kind;
  return kind === "seed" || kind === "auto-layout";
}

function draftNode(x = 80, y = 72): CanvasNode {
  return nodeFromCard({
    id: safeRandomId("text-"),
    kind: "text",
    title: "随手记",
    text: "",
    adapter: "text",
    x,
    y,
    width: 440,
    height: 320,
    positionSource: { kind: "user", actor: "browser:new-card" },
  }, true);
}

function persistedDocument(
  sessionId: string,
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  title?: string,
  base?: Pick<MaterialCanvasDocument, "catalog" | "display" | "spacing">,
): MaterialCanvasDocument {
  // 组卡(kind=group)无 source 也要持久化; draft 与无 source 的文字卡不落盘
  const persistedNodes = parentsFirst(nodes.filter((node) =>
    !node.data.draft && (node.data.kind === "material" || node.data.kind === "group" || Boolean(node.data.source))
  ));
  const ids = new Set(persistedNodes.map((node) => node.id));
  return {
    version: 1,
    sessionId,
    ...(title && title.trim() ? { title: title.trim() } : {}),
    ...(base?.catalog ? { catalog: base.catalog } : {}),
    ...(base?.display ? { display: base.display } : {}),
    // 间距档必须随文档保留：丢了它，界面簇画布每次落盘都退回 comfortable，
    // 用户看到的就是"紧凑了一下又自己松开"。
    ...(base?.spacing ? { spacing: base.spacing } : {}),
    revision: 0,
    updatedAt: new Date().toISOString(),
    cards: persistedNodes.map((node) => {
      const size = dimensionsOf(node);
      const { draft: _draft, text: _text, parentId: _staleParent, ...data } = node.data;
      return {
        ...data,
        // 父子关系以 React Flow 节点上的 parentId 为准(组内坐标本来就是相对的)
        parentId: node.parentId,
        x: node.position.x,
        y: node.position.y,
        width: size.width,
        height: size.height,
      };
    }),
    relations: edges
      .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
      .map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        programLabel: edge.data?.programLabel,
        userLabel: edge.data?.userLabel,
        routePoints: edge.data?.routePoints,
        programLabelPosition: edge.data?.programLabelPosition,
        userLabelPosition: edge.data?.userLabelPosition,
        sourceSlice: edge.data?.sourceSlice,
        targetSlice: edge.data?.targetSlice,
        sourceHandle: edge.sourceHandle || undefined,
        targetHandle: edge.targetHandle || undefined,
        path: edge.data?.path || "curve",
        arrow: edge.data?.arrow || "closed",
        kind: edge.data?.kind || undefined,
      })),
  };
}

// ---- 图片清晰度换档: 单调棘轮, 只升不降 ----
// 显示永远 = 已加载完成的最清晰档; zoom 停稳后目标档更高才后台预加载+decode 后覆盖;
// 目标档更低时什么都不做(高清版继续显示, 浏览器自行缩小渲染, 绝不切回糊档/绝不发低清请求)。
// tier 序号: 0=基准 480 档(source.token), 1=1200 档(tiers[0]), 2=原图档(tiers[1])
const TIER_UP_MID = 0.65;   // 480 → 1200 升档线
const TIER_UP_RAW = 1.0;    // 1200 → 原图 升档线
const TIER_DEBOUNCE_MS = 350;

function tierSrcAt(source: Extract<MaterialSource, { kind: "file" }>, tier: number): string {
  const tiers = source.tiers ?? [];
  if (tier <= 0 || !tiers.length) return streamUrl(source.token);
  return streamUrl(tiers[Math.min(tier - 1, tiers.length - 1)].token);
}

function maxTierOf(source: Extract<MaterialSource, { kind: "file" }>): number {
  return Math.min(2, (source.tiers ?? []).length);
}

// 只判升档线: zoom 对应的理想档(与当前档无关; 棘轮在调用方取 max)
function tierTargetForZoom(zoom: number, maxTier: number): number {
  return Math.min(zoom >= TIER_UP_RAW ? 2 : zoom >= TIER_UP_MID ? 1 : 0, maxTier);
}

function useTieredImageSrc(source: Extract<MaterialSource, { kind: "file" }>, zoom: number, hd: boolean): string {
  const maxTier = maxTierOf(source);
  // 首帧永远基准档(480): 最小、最快、多半已缓存——绝不在挂载时读 zoom 直接上原图
  // (挂载瞬间 fitView 未跑, zoom 恒为默认值 1 → 会误判 RAW, 73 张卡同时拉 3MB 原图 = 长时间黑屏的根因)
  const [tier, setTier] = useState(0);
  const tierRef = useRef(0); // = 已加载完成的最清晰档(棘轮, 只升不降)
  useEffect(() => {
    // 棘轮: 目标档 = max(已加载档, zoom 理想档); 不更高就什么都不做
    const target = hd ? maxTier : Math.max(tierRef.current, tierTargetForZoom(zoom, maxTier));
    if (target <= tierRef.current) return;
    let dead = false;
    // 全屏立刻预加载(已加载档先铺上, 不黑屏等待); 卡片走防抖, 停稳才判定
    const timer = window.setTimeout(() => {
      const src = tierSrcAt(source, target);
      const probe = new Image();
      probe.onload = () => {
        // onload 只保证下载完; 大图先 decode 再换, 杜绝切换瞬间的解码黑帧
        const done = () => {
          if (dead) return;
          tierRef.current = target;
          setTier(target);
        };
        if (typeof probe.decode === "function") probe.decode().then(done, done);
        else done();
      };
      probe.onerror = () => { /* 加载失败保持当前档 */ };
      probe.src = src;
    }, hd ? 0 : TIER_DEBOUNCE_MS);
    return () => { dead = true; window.clearTimeout(timer); };
  }, [zoom, hd, source, maxTier]);
  return tierSrcAt(source, tier);
}

function FilePreview({ source, adapter, zoom = 1, hd = false }: {
  source: Extract<MaterialSource, { kind: "file" }>;
  adapter: MaterialAdapter;
  zoom?: number;
  hd?: boolean;
}) {
  const invokeCommand = useContext(InvokeContext);
  const [inspection, setInspection] = useState<FileInspection | null>(null);
  const [error, setError] = useState("");
  const imageSrc = useTieredImageSrc(source, zoom, hd);
  useEffect(() => {
    let live = true;
    setInspection(null);
    setError("");
    void invokeCommand<FileInspection>("file_inspect", { token: source.token })
      .then((value) => { if (live) setInspection(value); })
      .catch((reason) => { if (live) setError(String(reason)); });
    return () => { live = false; };
  }, [invokeCommand, source.token]);

  if (error) return <div className="material-preview-message error">文件暂时不可读：{error}</div>;
  if (!inspection) return <div className="material-preview-message"><Loader2 className="spin" size={16} />读取材料…</div>;
  if (usesHtmlFilePreview({ kind: "material", adapter, source }, inspection.mime)) {
    return <iframe
      className="material-frame-preview"
      src={streamUrl(source.token)}
      title={inspection.name}
      sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox"
    />;
  }
  if (inspection.preview === "text") {
    return <pre className="material-text-preview">{inspection.content || "（空文件）"}</pre>;
  }
  if (inspection.preview === "image") {
    // 预加载+decode 完成前 src 不变(useTieredImageSrc 保证), 不换 key 不重挂载 → 无白屏无尺寸跳动
    return <img className="material-media-preview" src={imageSrc} alt={inspection.name} decoding="async" />;
  }
  if (inspection.preview === "pdf") {
    return <iframe className="material-frame-preview" src={streamUrl(source.token)} title={inspection.name} />;
  }
  if (inspection.preview === "audio") {
    return <audio className="material-audio-preview" src={streamUrl(source.token)} controls />;
  }
  if (inspection.preview === "video") {
    return <video className="material-media-preview" src={streamUrl(source.token)} controls />;
  }
  if (inspection.preview === "directory") {
    return (
      <div className="material-directory-preview">
        {(inspection.items || []).slice(0, 80).map((item) => (
          <div key={item.path}><span>{item.kind === "folder" ? "▸" : "·"}</span>{item.name}</div>
        ))}
        {inspection.truncated && <small>目录较大，仅显示前一部分</small>}
      </div>
    );
  }
  return <div className="material-preview-message">{inspection.mime || "文件"}<br />{inspection.path}</div>;
}

function LegacyPreview({ source }: { source: Extract<MaterialSource, { kind: "legacy-note" }> }) {
  const invokeCommand = useContext(InvokeContext);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    void invokeCommand<{ content?: string }>("notes_legacy_get", { id: source.id })
      .then((value) => { if (live) setContent(value.content || "（旧札记没有可展示的 Markdown）"); })
      .catch((reason) => { if (live) setError(String(reason)); });
    return () => { live = false; };
  }, [invokeCommand, source.id]);
  if (error) return <div className="material-preview-message error">旧札记读取失败：{error}</div>;
  if (content === null) return <div className="material-preview-message"><Loader2 className="spin" size={16} />读取旧札记…</div>;
  return <pre className="material-text-preview legacy">{content}</pre>;
}

function LinkPreview({ source, adapter, interactive = false }: {
  source: Extract<MaterialSource, { kind: "link" }>;
  adapter: MaterialAdapter;
  interactive?: boolean;
}) {
  const navigationUrl = resolveBrowserNavigationUrl(source.url);
  // Decode only the visible label.  Navigation keeps the canonical URL byte-for-byte,
  // so a friendlier Chinese path/query can never change where the card opens.
  let displayUrl = repairDisplayText(source.url);
  for (let pass = 0; pass < 2; pass += 1) {
    try {
      const decoded = decodeURI(displayUrl);
      if (decoded === displayUrl) break;
      displayUrl = decoded;
    } catch {
      break;
    }
  }
  if (adapter === "image") return <img className="material-media-preview" src={navigationUrl} alt="链接图片" />;
  if ((adapter === "pdf" || adapter === "web") && interactive) {
    return <iframe className="material-frame-preview" src={navigationUrl} title={displayUrl} />;
  }
  if (adapter === "audio") return <audio className="material-audio-preview" src={navigationUrl} controls />;
  if (adapter === "video") return <video className="material-media-preview" src={navigationUrl} controls />;
  let host = source.url;
  try { host = new URL(source.url).host; } catch { /* keep raw */ }
  return (
    <button className="material-link-preview" type="button" onClick={() => window.open(navigationUrl, "_blank", "noopener") }>
      <Link2 size={24} />
      <strong>{host}</strong>
      <span title={source.url}>{displayUrl}</span>
      <small>在新窗口打开 <ArrowUpRight size={12} /></small>
    </button>
  );
}

/** live 入口：卡片态永远只显示静态 representation；全屏后才挂载可玩内容。 */
function liveEntryOf(data: CardData): string | null {
  const material = data.material as Record<string, unknown> | undefined;
  const direct = material?.["live_entry"];
  if (typeof direct === "string" && direct) return direct;
  const metadata = material?.["metadata"] as Record<string, unknown> | undefined;
  const fallback = metadata?.["playable_url"];
  if (typeof fallback !== "string" || !fallback) return null;
  return fallback.startsWith('/') ? `..${fallback}` : fallback;
}

function LiveFormPreview({ url }: { url: string }) {
  const mountedUrl = resolveBrowserNavigationUrl(url);
  return (
    <div className="material-live-preview">
      <iframe className="material-frame-preview" src={mountedUrl} title="实机" />
      <button
        className="material-live-open"
        type="button"
        onClick={(event) => { event.stopPropagation(); window.open(mountedUrl, "_blank", "noopener"); }}
      >
        <Play size={12} /> 新窗口打开
      </button>
    </div>
  );
}

function MovableLiveWindow({ value, onChange, onClose }: {
  value: LiveWindowRequest;
  onChange: (next: LiveWindowRequest) => void;
  onClose: () => void;
}) {
  const drag = useRef<{ pointerId: number; clientX: number; clientY: number; x: number; y: number } | null>(null);
  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const x = Math.max(0, Math.min(window.innerWidth - 180, current.x + event.clientX - current.clientX));
    const y = Math.max(0, Math.min(window.innerHeight - 44, current.y + event.clientY - current.clientY));
    onChange({ ...value, x, y });
  };
  return <section
    className="material-live-window"
    data-testid="material-live-window"
    style={{ left: value.x, top: value.y, width: value.width, height: value.height }}
    role="dialog"
    aria-label={value.title}
  >
    <header
      className="material-live-window__bar"
      onPointerDown={(event) => {
        if ((event.target as Element).closest("button")) return;
        drag.current = { pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY, x: value.x, y: value.y };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={onPointerMove}
      onPointerUp={(event) => {
        drag.current = null;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={() => { drag.current = null; }}
    >
      <Play size={14} /><strong title={value.title}>{value.title}</strong><span>WebTwin · iframe</span>
      <button type="button" title="在浏览器新标签打开" onClick={() => window.open(value.url, "_blank", "noopener")}><ArrowUpRight size={14} /></button>
      <button type="button" title="关闭" onClick={onClose}><X size={15} /></button>
    </header>
    <iframe src={value.url} title={value.title} />
  </section>;
}

function FileOrLiveBody({ data, zoom, hd }: { data: CardData; zoom: number; hd: boolean }) {
  const url = liveEntryOf(data);
  if (!url) return <FilePreview source={data.source as Extract<MaterialSource, { kind: "file" }>} adapter={data.adapter} zoom={zoom} hd={hd} />;
  if (!hd) return <FilePreview source={data.source as Extract<MaterialSource, { kind: "file" }>} adapter={data.adapter} zoom={zoom} hd={false} />;
  return <LiveFormPreview url={url} />;
}

function ImportedMaterialPreview({ data }: { data: CardData }) {
  const envelope = data.material as Record<string, unknown> | undefined;
  const resolved = envelope?.resolved_material as Record<string, unknown> | undefined;
  const representations = Array.isArray(resolved?.representations)
    ? resolved.representations as Array<Record<string, unknown>> : [];
  const primary = representations.find((item) => item.role === "primary") || representations[0];
  if (typeof primary?.content !== "string") return null;
  const sourceRef = primary.source_ref as { kind?: string } | undefined;
  return (
    <div className="material-imported-preview">
      <div className="material-imported-preview__meta">
        <span>{sourceRef?.kind === "material-wiki-api" ? "Material Wiki" : sourceRef?.kind === "session-canvas" ? "Canvas Material" : "Material"}</span>
        <small>{typeof primary.media_type === "string" ? primary.media_type : "text/plain"}</small>
      </div>
      <pre>{primary.content}</pre>
    </div>
  );
}

interface NativeMaterialDocumentBlock {
  material?: {
    title?: unknown;
    summary?: unknown;
    labels?: unknown;
    metadata?: unknown;
    representations?: Array<{
      id?: unknown;
      media_type?: unknown;
      renderer_hint?: unknown;
      source_ref?: { ref?: unknown } | null;
      title?: unknown;
    }>;
  };
  representation?: {
    id?: unknown;
    media_type?: unknown;
    content_format?: unknown;
    renderer_hint?: unknown;
    source_ref?: { ref?: unknown } | null;
    title?: unknown;
  } | null;
  content?: unknown;
}

interface NativeMaterialDocument {
  id?: unknown;
  title?: unknown;
  summary?: unknown;
  blocks?: NativeMaterialDocumentBlock[];
}

const nativeMaterialDocumentCache = new Map<string, Promise<NativeMaterialDocument>>();

function NativeRepresentationPreview({ block, content }: { block?: NativeMaterialDocumentBlock; content: string }) {
  return <NativeMaterialView
    block={block}
    content={content}
    renderMarkdown={renderMarkdownSafe}
    resolveSource={materialRepresentationFileUrl}
  />;
}

function loadNativeMaterialDocument(data: CardData, requestedRepresentationId?: string): Promise<NativeMaterialDocument> {
  const envelope = data.material as Record<string, unknown> | undefined;
  const materialId = typeof envelope?.material_ref === 'string' ? envelope.material_ref : '';
  const representationId = requestedRepresentationId
    ?? (typeof envelope?.representation_id === 'string' ? envelope.representation_id : '');
  const revisionRef = envelope?.referenced_revision_ref as Record<string, unknown> | undefined;
  const revision = typeof revisionRef?.revision === 'string' ? revisionRef.revision : '';
  const linkUrl = data.source?.kind === 'link' ? data.source.url : '';
  if (!materialId || !linkUrl) return Promise.reject(new Error('Material 引用不完整'));
  const cacheKey = `${materialId}@${revision || 'current'}#${representationId || 'preferred'}`;
  const cached = nativeMaterialDocumentCache.get(cacheKey);
  if (cached) return cached;
  const request = (async () => {
    const endpoint = nativeMaterialDocumentUrl(materialId, representationId);
    const response = await fetch(endpoint, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json() as NativeMaterialDocument;
  })();
  nativeMaterialDocumentCache.set(cacheKey, request);
  request.catch(() => nativeMaterialDocumentCache.delete(cacheKey));
  return request;
}

function AnnotationHotspots({ cardId, data, actions }: {
  cardId: string;
  data: CardData;
  actions: CardActions;
}) {
  const flow = useReactFlow<CanvasNode, CanvasEdge>();
  const updateNodeInternals = useUpdateNodeInternals();
  const envelope = data.material as Record<string, unknown> | undefined;
  const materialId = typeof envelope?.material_ref === 'string' ? envelope.material_ref : '';
  const representationId = typeof envelope?.representation_id === 'string' ? envelope.representation_id : '';
  const [anchors, setAnchors] = useState<AnnotationAnchor[]>([]);
  const inlineSlices = useMemo(() => inlineCanvasSlices(envelope), [envelope]);

  useEffect(() => {
    let active = true;
    setAnchors([]);
    if (!materialId || !representationId || data.adapter !== 'image' || data.source?.kind !== 'link') return undefined;
    void loadNativeMaterialDocument(data)
      .then(async (document) => {
        const block = document.blocks?.find((item) => item.representation?.id === representationId) ?? document.blocks?.[0];
        const annotation = block?.material?.representations?.find((item) => item.id === 'interface-annotation'
          && item.renderer_hint === 'interface-annotation');
        const stageId = annotationStageId(block?.representation);
        if (!annotation || !stageId) return [];
        const annotationDocument = await loadNativeMaterialDocument(data, 'interface-annotation');
        const annotationBlock = annotationDocument.blocks?.find((item) => item.representation?.id === 'interface-annotation')
          ?? annotationDocument.blocks?.[0];
        const content = typeof annotationBlock?.content === 'string' ? annotationBlock.content : '';
        return parseAnnotationDocument(content).stages.find((stage) => stage.id === stageId)?.anchors ?? [];
      })
      .then((value) => { if (active) setAnchors(value); })
      .catch(() => { if (active) setAnchors([]); });
    return () => { active = false; };
  }, [data, materialId, representationId]);

  useEffect(() => {
    if (!anchors.length && !inlineSlices.length) return undefined;
    const frame = window.requestAnimationFrame(() => updateNodeInternals(cardId));
    return () => window.cancelAnimationFrame(frame);
  }, [anchors, cardId, inlineSlices, updateNodeInternals]);

  /**
   * 把这一阶段的框选登记成 element 切片。
   *
   * ref 取**完整层级 path**，不取锚点 id —— 这才是"界面是底层元素"成立的地方：
   * btn_menu 在 5 个业务里是同一个 ref，而锚点 id 每篇块各算一次、互不相干。
   * 同时把锚点 id 作为别名登记，让已经落库的存量关系仍查得到切片（别名不改身份）。
   */
  useEffect(() => {
    if (!anchors.length && !inlineSlices.length) return undefined;
    const slices: CanvasSliceRef[] = [...inlineSlices];
    const aliases: Record<string, CanvasSliceRef> = {};
    for (const anchor of anchors) {
      const slice: CanvasSliceRef = {
        kind: 'element',
        ref: anchor.targetPath,
        representationId: representationId || undefined,
        box: { x: anchor.x, y: anchor.y, width: anchor.width, height: anchor.height },
        label: anchor.targetPath.split('/').pop() || anchor.targetPath,
      };
      slices.push(slice);
      aliases[anchor.id] = slice;
    }
    return publishCanvasSlices(cardId, slices, aliases);
  }, [anchors, cardId, inlineSlices, representationId]);

  if (!anchors.length && !inlineSlices.length) return null;
  return (
    <div className="material-annotation-layer" data-annotation-stage={anchors[0]?.stageId || "inline-planning"}>
      {anchors.map((anchor, index) => {
        const edge = flow.getEdges().find((candidate) => candidate.source === cardId && candidate.sourceHandle === anchor.id);
        const mechanismText = anchor.mechanisms.map((mechanism) => mechanism.text).filter(Boolean);
        return (
          <div
            key={anchor.id}
            className={`material-annotation-hotspot${edge ? ' linked' : ''}`}
            style={{
              left: `${anchor.x * 100}%`,
              top: `${anchor.y * 100}%`,
              width: `${anchor.width * 100}%`,
              height: `${anchor.height * 100}%`,
            }}
          >
            <button
              type="button"
              className="material-annotation-target nodrag nopan"
              title={[anchor.targetPath, ...mechanismText].join('\n\n')}
              aria-label={`${anchor.targetPath}${edge ? '，跳转到关联卡片' : '，尚未连接目标卡片'}`}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                actions.activateCard(edge?.target ?? cardId);
              }}
            >
              <span>{index + 1}</span>
              {anchor.mechanisms.length > 1 && <small>{anchor.mechanisms.length}</small>}
            </button>
            <Handle
              id={anchor.id}
              type="source"
              position={Position.Right}
              className="material-annotation-handle nodrag nopan"
              title={`从 ${anchor.targetPath} 连到目标卡片`}
            />
            {anchor.mechanisms.length > 0 && (
              <div className="material-annotation-tooltip" role="tooltip">
                {anchor.mechanisms.map((mechanism) => (
                  <p key={mechanism.id}><strong>{mechanism.id}</strong>{mechanism.text}</p>
                ))}
              </div>
            )}
          </div>
        );
      })}
      {inlineSlices.map((slice, index) => {
        if (!slice.box) return null;
        const handleId = canvasSliceHandleId(slice);
        const edge = flow.getEdges().find((candidate) => candidate.source === cardId
          && (candidate.sourceHandle === handleId || candidate.data?.sourceSlice?.ref === slice.ref));
        const detail = [slice.label, slice.note, slice.scene && `场景：${slice.scene}`,
          slice.specialDisplay && `特别显示：${slice.specialDisplay}`].filter(Boolean).join('\n');
        return (
          <div
            key={`${slice.kind}:${slice.ref}`}
            className={`material-manual-slice emphasis-${slice.emphasis || "quiet"}${edge ? " linked" : ""}`}
            style={{
              left: `${slice.box.x * 100}%`,
              top: `${slice.box.y * 100}%`,
              width: `${slice.box.width * 100}%`,
              height: `${slice.box.height * 100}%`,
            }}
          >
            <button
              type="button"
              className="material-manual-slice-target nodrag nopan"
              title={detail || slice.ref}
              aria-label={`${slice.label || `切片 ${index + 1}`}${edge ? "，跳转到注释" : "，尚未连接注释"}`}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                actions.activateCard(edge?.target ?? cardId);
              }}
            >
              <span>{slice.label || `切片 ${index + 1}`}</span>
            </button>
            <Handle
              id={handleId}
              type="source"
              position={Position.Right}
              className="material-annotation-handle nodrag nopan"
              title={`从 ${slice.label || slice.ref} 连到目标卡片`}
            />
          </div>
        );
      })}
    </div>
  );
}

function ManualSliceEditor({ active, onCancel, onCommit }: {
  active: boolean;
  onCancel: () => void;
  onCommit: (draft: RegionAnnotationDraft) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [box, setBox] = useState<NonNullable<CanvasSliceRef["box"]> | null>(null);
  const [note, setNote] = useState("");
  const [scene, setScene] = useState("");
  const [specialDisplay, setSpecialDisplay] = useState("");

  useEffect(() => {
    if (active) return;
    setStart(null);
    setBox(null);
    setNote("");
    setScene("");
    setSpecialDisplay("");
  }, [active]);

  if (!active) return null;
  const pointOf = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = hostRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  };
  const commit = () => {
    if (!box || !note.trim()) return;
    onCommit({ box, note: note.trim(), scene: scene.trim(), specialDisplay: specialDisplay.trim() });
    setBox(null);
    setNote("");
    setScene("");
    setSpecialDisplay("");
  };
  return (
    <div
      ref={hostRef}
      className="material-slice-editor nodrag nopan nowheel"
      data-testid="material-slice-editor"
      onPointerDown={(event) => {
        if ((event.target as Element).closest(".material-slice-editor-form")) return;
        const point = pointOf(event);
        if (!point) return;
        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.setPointerCapture(event.pointerId);
        setStart(point);
        setBox(null);
      }}
      onPointerMove={(event) => {
        if (!start) return;
        const point = pointOf(event);
        if (!point) return;
        const next = normalizedDragBox(start, point);
        if (next) setBox(next);
      }}
      onPointerUp={(event) => {
        if (!start) return;
        const point = pointOf(event);
        setStart(null);
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        if (!point) return;
        setBox(normalizedDragBox(start, point));
      }}
    >
      <div className="material-slice-editor-hint">拖拽圈选；松开后填写注释、场景与特别显示</div>
      {box && <div className="material-slice-editor-box" style={{
        left: `${box.x * 100}%`, top: `${box.y * 100}%`,
        width: `${box.width * 100}%`, height: `${box.height * 100}%`,
      }} />}
      {box && !start && (
        <div className="material-slice-editor-form" role="dialog" aria-label="切片注释">
          <textarea autoFocus value={note} onChange={(event) => setNote(event.currentTarget.value)} placeholder="这里要说什么内容？" />
          <input value={scene} onChange={(event) => setScene(event.currentTarget.value)} placeholder="要构造什么场景？" />
          <input value={specialDisplay} onChange={(event) => setSpecialDisplay(event.currentTarget.value)} placeholder="有什么特别显示？（可空）" />
          <div><button type="button" onClick={commit} disabled={!note.trim()}>创建切片与注释</button><button type="button" onClick={() => setBox(null)}>重画</button><button type="button" onClick={onCancel}>退出</button></div>
        </div>
      )}
      {!box && <button type="button" className="material-slice-editor-cancel" onClick={onCancel}>退出圈选</button>}
    </div>
  );
}

function NativeMaterialPreview({ data, hd = false }: { data: CardData; hd?: boolean }) {
  const envelope = data.material as Record<string, unknown> | undefined;
  const materialId = typeof envelope?.material_ref === 'string' ? envelope.material_ref : '';
  const representationId = typeof envelope?.representation_id === 'string' ? envelope.representation_id : '';
  const revisionRef = envelope?.referenced_revision_ref as Record<string, unknown> | undefined;
  const revision = typeof revisionRef?.revision === 'string' ? revisionRef.revision : '';
  const linkUrl = data.source?.kind === 'link' ? data.source.url : '';
  const [document, setDocument] = useState<NativeMaterialDocument | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setError('');
    void loadNativeMaterialDocument(data)
      .then((value) => { if (active) setDocument(value); })
      .catch((reason) => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [data, linkUrl, materialId, representationId, revision]);
  const blocks = Array.isArray(document?.blocks) ? document.blocks : [];
  const block = blocks.find((item) => item.representation?.id === representationId) || blocks[0];
  const content = typeof block?.content === 'string' ? block.content : '';
  const summary = typeof document?.summary === 'string'
    ? document.summary
    : typeof block?.material?.summary === 'string' ? block.material.summary : '';
  return (
    <div className={`material-native-preview${hd ? ' hd' : ''}`}>
      {!document && !error && <div className='material-preview-message'><Loader2 className='spin' size={16} />读取 Material 内容…</div>}
      {error && <div className='material-preview-message error'>Material 内容读取失败：{error}</div>}
      {document && (content
        ? <NativeRepresentationPreview block={block} content={content} />
        : <div className='material-native-preview__summary'>{summary || '该 Material 没有可展示的正文表示。'}</div>)}
    </div>
  );
}

function MaterialBody({ data, zoom = 1, hd = false }: { data: CardData; zoom?: number; hd?: boolean }) {
  if (data.frozen && data.source?.kind === "file") return <FrozenPreview source={data.source} frozen={data.frozen} />;
  const imported = <ImportedMaterialPreview data={data} />;
  const envelope = data.material as Record<string, unknown> | undefined;
  const resolved = envelope?.resolved_material as Record<string, unknown> | undefined;
  const hasImportedContent = Array.isArray(resolved?.representations)
    && (resolved.representations as Array<Record<string, unknown>>).some((item) => typeof item.content === "string");
  if (hasImportedContent) return imported;
  if (!data.source) return <div className="material-preview-message">材料引用缺失</div>;
  if (data.source.kind === "file") return <FileOrLiveBody data={data} zoom={zoom} hd={hd} />;
  if (data.source.kind === "legacy-note") return <LegacyPreview source={data.source} />;
  if (usesNativeMaterialDocument(data)) return <NativeMaterialPreview data={data} hd={hd} />;
  const materialSurface = usesMaterialDisplaySurface(data);
  const live = liveEntryOf(data);
  if (live && hd) return <LiveFormPreview url={live} />;
  return <LinkPreview
    source={data.source}
    adapter={data.adapter}
    interactive={hd || materialSurface}
  />;
}

/** 冻结快照: 顶部横幅标明"这是死的"(时间+活源), 正文 sandbox 空值=禁脚本禁表单(双保险, 烘焙时已剥 script)。 */
function FrozenPreview({ source, frozen }: {
  source: Extract<MaterialSource, { kind: "file" }>;
  frozen?: CardData["frozen"];
}) {
  const fromUrl = frozen?.from.kind === "link" ? frozen.from.url : "";
  let host = fromUrl;
  try { host = fromUrl ? new URL(fromUrl).host : ""; } catch { /* 保留原始串 */ }
  const when = frozen?.capturedAt ? frozen.capturedAt.replace("T", " ").slice(0, 16) : "";
  return (
    <div className="material-frozen-preview">
      <div className="material-frozen-banner" title={fromUrl ? `活源: ${fromUrl}` : "冻结快照"}>
        <Snowflake size={12} />
        <span>静态快照{when ? ` · ${when}` : ""}{host ? ` · 源 ${host}` : ""}</span>
      </div>
      <iframe className="material-frame-preview" sandbox="" src={streamUrl(source.token)} title="冻结快照" />
    </div>
  );
}

function EditableText({ id, data, actions, zoom = 1 }: { id: string; data: CardData; actions: CardActions; zoom?: number }) {
  const invokeCommand = useContext(InvokeContext);
  const editableId = data.source?.kind === "file" ? data.source.editableId : undefined;
  const [loading, setLoading] = useState(Boolean(editableId && data.text === undefined));
  const [error, setError] = useState("");
  const editing = actions.editingTextId === id;
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fontSize = Math.min(42, (data.fontSize || 14) * Math.max(1, Math.min(3, 0.72 / Math.max(zoom, 0.01))));
  const markdownHtml = useMemo(() => renderMarkdownSafe(data.text || ""), [data.text]);
  const fitEditingHeight = useCallback((persist: boolean) => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    actions.fitTextCardHeight(id, textarea.scrollHeight, persist);
  }, [actions, id]);
  useEffect(() => {
    if (!editing) return undefined;
    const frame = window.requestAnimationFrame(() => fitEditingHeight(false));
    return () => window.cancelAnimationFrame(frame);
  }, [data.text, editing, fitEditingHeight]);
  useEffect(() => {
    if (!editableId || data.text !== undefined) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    setError("");
    void invokeCommand<TextMaterialPayload>("notes_text_get", { id: editableId })
      .then((material) => { if (live) actions.hydrateText(id, material); })
      .catch((reason) => { if (live) setError(String(reason)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [actions, data.text, editableId, id, invokeCommand]);
  if (loading && !editing) return <div className="material-preview-message"><Loader2 className="spin" size={16} />读取文字材料…</div>;
  if (error && !editing) return <div className="material-preview-message error">文字材料读取失败：{error}</div>;
  if (!editing) {
    return (
      <div className="material-markdown-shell">
        <div
          className={`material-markdown-preview${data.text?.trim() ? "" : " empty"}`}
          style={{ fontSize: `${fontSize}px` }}
          title="双击编辑"
          onDoubleClick={(event) => {
            event.stopPropagation();
            actions.startTextEditing(id);
          }}
          onClick={(event) => {
            const anchor = event.target instanceof Element ? event.target.closest("a") : null;
            if (!(anchor instanceof HTMLAnchorElement) || !anchor.href) return;
            event.preventDefault();
            window.open(resolveBrowserNavigationUrl(anchor.href), "_blank", "noopener");
          }}
          dangerouslySetInnerHTML={{ __html: markdownHtml || "<p>双击写点什么……有内容后才保存</p>" }}
        />
      </div>
    );
  }
  return (
    <div className="material-markdown-shell editing">
      <textarea
        ref={textareaRef}
        autoFocus
        value={data.text || ""}
        style={{ fontSize: `${fontSize}px` }}
        placeholder="使用 Markdown 写点什么……有内容后才保存"
        spellCheck
        onFocus={() => {
          actions.beginTextEdit();
          window.requestAnimationFrame(() => fitEditingHeight(false));
        }}
        onBlur={() => {
          fitEditingHeight(true);
          actions.finishTextEdit(id);
        }}
        onChange={(event) => {
          actions.changeText(id, event.currentTarget.value);
          window.requestAnimationFrame(() => fitEditingHeight(false));
        }}
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            actions.stopTextEditing(id);
          } else {
            event.stopPropagation();
          }
        }}
      />
      <button
        className="material-markdown-mode-button done"
        type="button"
        title="完成编辑并预览 Markdown"
        onPointerDown={(event) => event.preventDefault()}
        onClick={() => {
          fitEditingHeight(true);
          actions.stopTextEditing(id);
        }}
      >
        <FileText size={14} /><span>预览</span>
      </button>
    </div>
  );
}

function relationMarker(arrow: RelationData["arrow"]) {
  if (arrow === "none") return undefined;
  return {
    type: arrow === "open" ? MarkerType.Arrow : MarkerType.ArrowClosed,
    width: 20,
    height: 20,
  };
}

const RELATION_LABEL_ZH: Record<string, string> = {
  "transitions-to": "转化为",
  "represented-by": "由此呈现",
  "derived-from": "派生自",
  "part-of": "属于",
  "validated-by": "由此验证",
  requires: "依赖",
  implements: "实现",
  references: "引用",
  derived: "派生",
  continues: "后续",
  demo: "演示",
  state: "状态",
};

function visibleRelationLabel(value: string | undefined): string {
  const trimmed = value?.trim() || "";
  return RELATION_LABEL_ZH[trimmed.toLowerCase()] || trimmed;
}

function pointAtProgress(points: CanvasPoint[], progress: number): CanvasPoint {
  if (points.length < 2) return points[0] || { x: 0, y: 0 };
  const lengths = points.slice(1).map((point, index) => Math.hypot(point.x - points[index].x, point.y - points[index].y));
  const total = lengths.reduce((sum, length) => sum + length, 0);
  if (!total) return points[0];
  let remaining = Math.min(1, Math.max(0, progress)) * total;
  for (let index = 0; index < lengths.length; index += 1) {
    const length = lengths[index];
    if (remaining <= length || index === lengths.length - 1) {
      const ratio = length ? remaining / length : 0;
      return {
        x: points[index].x + (points[index + 1].x - points[index].x) * ratio,
        y: points[index].y + (points[index + 1].y - points[index].y) * ratio,
      };
    }
    remaining -= length;
  }
  return points[points.length - 1];
}

function nearestProgress(points: CanvasPoint[], target: CanvasPoint): number {
  if (points.length < 2) return 0.5;
  const lengths = points.slice(1).map((point, index) => Math.hypot(point.x - points[index].x, point.y - points[index].y));
  const total = lengths.reduce((sum, length) => sum + length, 0) || 1;
  let walked = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  let bestProgress = 0.5;
  for (let index = 0; index < lengths.length; index += 1) {
    const start = points[index];
    const end = points[index + 1];
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const squared = dx * dx + dy * dy;
    const ratio = squared ? Math.min(1, Math.max(0, ((target.x - start.x) * dx + (target.y - start.y) * dy) / squared)) : 0;
    const projected = { x: start.x + dx * ratio, y: start.y + dy * ratio };
    const distance = Math.hypot(target.x - projected.x, target.y - projected.y);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestProgress = (walked + lengths[index] * ratio) / total;
    }
    walked += lengths[index];
  }
  return bestProgress;
}

const MaterialRelation = memo(function MaterialRelation(props: EdgeProps<CanvasEdge>) {
  const actions = useContext(RelationActionsContext);
  const flow = useReactFlow<CanvasNode, CanvasEdge>();
  const { zoom } = useViewport();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(props.data?.userLabel || "");
  const [routeDraft, setRouteDraft] = useState<CanvasPoint[]>(props.data?.routePoints || []);
  const [userPosition, setUserPosition] = useState<CanvasLabelPosition>(props.data?.userLabelPosition || { t: 0.62, offsetY: 16 });
  const draggingRef = useRef(false);
  useEffect(() => {
    if (!editing) setDraft(props.data?.userLabel || "");
  }, [editing, props.data?.userLabel]);
  useEffect(() => { if (!draggingRef.current) setRouteDraft(props.data?.routePoints || []); }, [props.data?.routePoints]);
  useEffect(() => { if (!draggingRef.current) setUserPosition(props.data?.userLabelPosition || { t: 0.62, offsetY: 16 }); }, [props.data?.userLabelPosition]);

  const externallyEditing = actions?.editingId === props.id;
  const pathStyle = props.data?.path || "curve";
  const routedPoints = [{ x: props.sourceX, y: props.sourceY }, ...routeDraft, { x: props.targetX, y: props.targetY }];
  const pathResult = pathStyle === "straight"
    ? getStraightPath(props)
    : pathStyle === "orthogonal"
      ? getSmoothStepPath({ ...props, borderRadius: 0 })
      : getBezierPath(props);
  const edgePath = routeDraft.length
    ? routedPoints.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ")
    : pathResult[0];
  const programPosition = props.data?.programLabelPosition || { t: 0.42, offsetY: -16 };
  const programAnchor = pointAtProgress(routedPoints, programPosition.t);
  const userAnchor = pointAtProgress(routedPoints, userPosition.t);
  const programLabel = visibleRelationLabel(props.data?.programLabel);
  const visibleUserLabel = visibleRelationLabel(draft);
  const labelVisible = Boolean(props.selected || editing || externallyEditing || (visibleUserLabel && zoom >= 0.2));

  const commit = () => {
    setEditing(false);
    actions?.update(props.id, { userLabel: draft.trim() });
    actions?.finish(props.id);
  };

  const dragRoutePoint = (index: number, event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    actions?.select(props.id);
    const initial = routeDraft.length ? [...routeDraft] : [pointAtProgress(routedPoints, 0.5)];
    draggingRef.current = true;
    setRouteDraft(initial);
    const move = (pointer: PointerEvent) => {
      const point = flow.screenToFlowPosition({ x: pointer.clientX, y: pointer.clientY });
      setRouteDraft((current) => current.map((item, itemIndex) => itemIndex === index ? point : item));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      draggingRef.current = false;
      setRouteDraft((current) => {
        actions?.update(props.id, { routePoints: current, path: "orthogonal" });
        return current;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  };

  const dragUserLabel = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    actions?.select(props.id);
    draggingRef.current = true;
    const move = (pointer: PointerEvent) => {
      const point = flow.screenToFlowPosition({ x: pointer.clientX, y: pointer.clientY });
      setUserPosition({ t: nearestProgress(routedPoints, point), offsetY: 16 });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      draggingRef.current = false;
      setUserPosition((current) => {
        actions?.update(props.id, { userLabelPosition: current });
        return current;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  };

  return (
    <>
      <BaseEdge id={props.id} path={edgePath} markerEnd={props.markerEnd} style={props.style} interactionWidth={24} />
      <EdgeLabelRenderer>
        {programLabel && (
          <div
            className="material-relation-label program nodrag nopan"
            style={{ transform: `translate(-50%, -50%) translate(${programAnchor.x + (programPosition.offsetX || 0)}px, ${programAnchor.y + (programPosition.offsetY || 0)}px)` }}
            title="程序关系标签（只读）"
          >
            {programLabel}
          </div>
        )}
        {labelVisible && (
          <div
            className="material-relation-label user nodrag nopan"
            style={{ transform: `translate(-50%, -50%) translate(${userAnchor.x + (userPosition.offsetX || 0)}px, ${userAnchor.y + (userPosition.offsetY || 0)}px)` }}
          >
            {props.selected || editing || externallyEditing ? (
              <input
                autoFocus={Boolean(editing || externallyEditing)}
                value={draft}
                aria-label="用户关系标签"
                placeholder="写用户标签"
                onFocus={() => { setEditing(true); actions?.select(props.id); }}
                onChange={(event) => setDraft(event.currentTarget.value)}
                onBlur={commit}
                onKeyDown={(event) => {
                  event.stopPropagation();
                  if (event.key === "Enter") event.currentTarget.blur();
                  if (event.key === "Escape") {
                    setDraft(props.data?.userLabel || "");
                    event.currentTarget.blur();
                  }
                }}
              />
            ) : (
              <button type="button" title="拖动标签；双击编辑" onPointerDown={dragUserLabel} onDoubleClick={() => setEditing(true)}>
                {visibleUserLabel}
              </button>
            )}
          </div>
        )}
        {props.selected && (routeDraft.length ? routeDraft : [pointAtProgress(routedPoints, 0.5)]).map((point, index) => (
          <button
            key={`${props.id}:route:${index}`}
            type="button"
            className="material-route-point nodrag nopan"
            style={{ transform: `translate(-50%, -50%) translate(${point.x}px, ${point.y}px)` }}
            title={routeDraft.length ? "拖动调整线路" : "拖动添加线路控制点"}
            onPointerDown={(event) => dragRoutePoint(index, event)}
          />
        ))}
      </EdgeLabelRenderer>
    </>
  );
});

const COMMON_FONT_SIZES = [8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 36, 40, 48, 56, 64, 72];

function FontSizeCombobox({ id, value, actions }: { id: string; value: number; actions: CardActions }) {
  const [draft, setDraft] = useState(String(value));
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => setDraft(String(value)), [value]);
  const commit = (nextDraft = draft) => {
    const parsed = Number(nextDraft);
    if (!Number.isFinite(parsed)) {
      setDraft(String(value));
      return;
    }
    const normalized = Math.min(72, Math.max(8, Math.round(parsed)));
    setDraft(String(normalized));
    actions.setTextSize(id, normalized);
  };
  const listId = `material-font-sizes-${id}`;
  return (
    <div
      ref={rootRef}
      className="material-font-size-combobox"
      role="combobox"
      aria-label="字号"
      aria-expanded={open}
      aria-controls={listId}
      title="字号（可输入 8–72，或从列表选择）"
      onBlur={(event) => {
        if (event.relatedTarget instanceof Node && rootRef.current?.contains(event.relatedTarget)) return;
        commit();
        setOpen(false);
      }}
    >
      <input
        value={draft}
        inputMode="numeric"
        aria-label="字号"
        aria-autocomplete="list"
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setDraft(event.currentTarget.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (event.key === "ArrowDown") setOpen(true);
          if (event.key === "Enter") {
            commit();
            setOpen(false);
            event.currentTarget.blur();
          }
          if (event.key === "Escape") {
            setDraft(String(value));
            setOpen(false);
            event.currentTarget.blur();
          }
        }}
      />
      <button
        type="button"
        className="material-font-size-toggle"
        aria-label="展开字号列表"
        tabIndex={-1}
        onPointerDown={(event) => event.preventDefault()}
        onClick={() => setOpen((current) => !current)}
      >
        <ChevronDown size={13} />
      </button>
      {open && (
        <div id={listId} className="material-font-size-options" role="listbox">
          {COMMON_FONT_SIZES.map((size) => (
            <button
              key={size}
              type="button"
              role="option"
              aria-selected={size === value}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => {
                setDraft(String(size));
                commit(String(size));
                setOpen(false);
              }}
            >
              {size}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}


function ReviewPanel({ scope, onScopeChange, items, currentCanvasKey, onOpenCard, onVerdict, fetchText }: {
  scope: "session" | "global";
  onScopeChange: (scope: "session" | "global") => void;
  items: ReviewItemData[];
  currentCanvasKey: string;
  onOpenCard: (item: ReviewItemData) => void;
  onVerdict: (item: ReviewItemData, pass: boolean, reason?: string) => void;
  fetchText: (id: string) => Promise<string>;
}) {
  // 2026-08-14 定稿: 默认本会话未审队列; 「全部会话」小开关切整体列表; 已裁决折叠到底部
  const activeBuckets: [SubmissionState, string][] = [
    ["unreviewed", "未看"],
    ["seen", "已看"],
  ];
  const resolved = items.filter((item) => item.submission.state === "fully-passed" || item.submission.state === "not-fully-passed");
  const renderItem = (item: ReviewItemData) => {
    const external = item.canvasKey !== currentCanvasKey;
    const jumpUrl = `/lofa/overlay/app/?session_id=${encodeURIComponent(item.sessionId)}&focus=${encodeURIComponent(item.cardId)}`;
    return (
      <article key={item.key} className="material-review-item">
        <button type="button" className="material-review-open" onClick={() => onOpenCard(item)} title={external ? "新标签页打开对应画布并聚焦" : "回画布聚焦这张卡"}>
          <ReviewThumb item={item} fetchText={fetchText} />
          <strong>{item.title}</strong>
          <small>
            提审 {item.submission.submittedAt.slice(0, 10)} · {item.submission.submittedBy}
            {scope === "global" ? ` · ${item.sessionLabel}「${item.canvasTitle}」` : ""}
          </small>
          {item.submission.reason ? <small className="reason">{item.submission.reason}</small> : null}
        </button>
        <div className="material-review-item-actions">
          <a
            className="material-review-jump"
            href={jumpUrl}
            target="_blank"
            rel="noopener noreferrer"
            title="跳回来源札记"
            onClick={(event) => event.stopPropagation()}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M15 4h5v5" /><path d="M20 4 10 14" /><path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5" />
            </svg>
            跳回札记
          </a>
          {(item.submission.state === "unreviewed" || item.submission.state === "seen") && (
            <SubmissionVerdictBar submission={item.submission} onVerdict={(pass, reason) => onVerdict(item, pass, reason)} />
          )}
        </div>
      </article>
    );
  };
  return (
    <div>
      <div className="material-review-toolbar">
        <button
          type="button"
          className={`material-review-scope-toggle${scope === "global" ? " active" : ""}`}
          onClick={() => onScopeChange(scope === "session" ? "global" : "session")}
          title={scope === "session" ? "切到整体列表（全部会话的提审卡）" : "切回本会话队列"}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.7 2.6 4 5.6 4 9s-1.3 6.4-4 9c-2.7-2.6-4-5.6-4-9s1.3-6.4 4-9z" />
          </svg>
          {scope === "session" ? "全部会话" : "本会话"}
        </button>
        <a
          className="material-review-scope-toggle"
          href="/lofa/overlay/app/?review=global"
          target="_blank"
          rel="noopener noreferrer"
          title="新标签页打开全体会话审阅台（深链 ?review=global）"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 4h5v5" /><path d="M20 4 10 14" /><path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5" />
          </svg>
          全体会话审阅台
        </a>
      </div>
      {!items.length && (
        <div className="material-review-empty">
          {scope === "session" ? "这张画布还没有提审卡。悬停卡片工具条或右键菜单 →「提交审阅」。" : "所有画布都没有提审卡。"}
        </div>
      )}
      <div className="material-review-buckets">
        {activeBuckets.map(([state, label]) => {
          const bucketItems = items.filter((item) => item.submission.state === state);
          return (
            <section key={state} className={`material-review-bucket s-${state}`}>
              <header>{label}<small>{bucketItems.length}</small></header>
              {bucketItems.map(renderItem)}
            </section>
          );
        })}
      </div>
      {resolved.length > 0 && (
        <details className="material-review-resolved">
          <summary>已裁决 {resolved.length} 条</summary>
          <div className="material-review-buckets">
            <section className="material-review-bucket">
              {resolved.map(renderItem)}
            </section>
          </div>
        </details>
      )}
    </div>
  );
}

function MaterialTypeBadge({ data }: { data: CardData }) {
  const openLiveWindow = useContext(LiveWindowContext);
  const live = liveEntryOf(data);
  if (live) {
    const mountedLive = resolveBrowserNavigationUrl(live);
    return (
      <button
        type="button"
        className="material-type-badge playable nodrag nopan"
        data-live-entry={live}
        title="打开可交互 Demo"
        aria-label="游玩"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          openLiveWindow?.({ url: mountedLive, title: data.title || "WebTwin" });
        }}
      >
        <Play size={13} /> 游玩
      </button>
    );
  }
  const materialMetadata = (data.material as Record<string, unknown> | undefined)?.metadata as Record<string, unknown> | undefined;
  const materialType = typeof materialMetadata?.material_type === "string" ? materialMetadata.material_type : "";
  const materialTypeBadge = {
    "source-data": { icon: <Table2 size={14} />, label: "原始数据" },
    planning: { icon: <GitBranch size={14} />, label: "规划" },
    specification: { icon: <FileText size={14} />, label: "规范" },
    preview: { icon: <Search size={14} />, label: "预演" },
    changelist: { icon: <GitBranch size={14} />, label: "Changelist" },
    "shelve-list": { icon: <GitBranch size={14} />, label: "Shelve List" },
  }[materialType];
  if (materialTypeBadge) {
    const p4Revision = typeof materialMetadata?.p4_revision === "string" ? materialMetadata.p4_revision : "";
    const label = p4Revision ? `${materialTypeBadge.label} · ${p4Revision}` : materialTypeBadge.label;
    return <span className="material-type-badge nodrag nopan" title={label}>{materialTypeBadge.icon}<span>{label}</span></span>;
  }
  if (data.kind === "text") return null;
  const displayRole = typeof materialMetadata?.display_role === 'string' ? materialMetadata.display_role : '';
  if (displayRole === 'configuration') return <span className="material-type-badge nodrag nopan"><Table2 size={14} /><span>配置</span></span>;
  if (displayRole === 'workflow') return <span className="material-type-badge nodrag nopan"><GitBranch size={14} /><span>流程</span></span>;
  const icon = data.adapter === "image" ? <ImageIcon size={14} /> : <File size={14} />;
  const label = data.adapter === "image" ? "图片"
    : data.adapter === "pdf" ? "PDF"
      : data.adapter === "audio" ? "音频"
        : data.adapter === "video" ? "视频"
          : data.source?.kind === "legacy-note" ? "札记" : "文件";
  return <span className="material-type-badge nodrag nopan" title={label}>{icon}<span>{label}</span></span>;
}

const FIT_VIEW_OPTIONS = { padding: 0.18, maxZoom: 1 };

/** One handle per side. ConnectionMode.Loose already treats each as source and target.
 *  Same-id source+target twins on one node make xyflow's handle lookup oscillate and
 *  can trip React error #185 (maximum update depth) as soon as a connection starts. */
function CardSideHandles() {
  return (
    <>
      <Handle id="left" type="source" position={Position.Left} className="material-card-handle left nodrag nopan" onClick={(event) => event.stopPropagation()} />
      <Handle id="right" type="source" position={Position.Right} className="material-card-handle right nodrag nopan" onClick={(event) => event.stopPropagation()} />
      <Handle id="top" type="source" position={Position.Top} className="material-card-handle top nodrag nopan" onClick={(event) => event.stopPropagation()} />
      <Handle id="bottom" type="source" position={Position.Bottom} className="material-card-handle bottom nodrag nopan" onClick={(event) => event.stopPropagation()} />
    </>
  );
}

const MaterialCard = memo(function MaterialCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const actions = useContext(CardActionsContext);
  const spotlight = useContext(SpotlightContext);
  const cardHover = useContext(HoveredCardContext);
  const openLiveWindow = useContext(LiveWindowContext);
  const { zoom } = useViewport();
  const [deleteArmed, setDeleteArmed] = useState(false);
  const touchStartRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const lastTouchTapRef = useRef<{ at: number; x: number; y: number } | null>(null);
  const ignoreSyntheticDoubleClickUntilRef = useRef(0);
  const longPressTimerRef = useRef<number | null>(null);
  const touchFiredRef = useRef(0);
  const clearLongPress = () => {
    if (longPressTimerRef.current !== null) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  };
  useEffect(() => clearLongPress, []);
  // 触屏下 click 合成不可靠(工具条按钮单击失灵): pointerup 直接执行, click 若随后补发由守卫去重。
  const fireTool = (event: ReactPointerEvent<HTMLButtonElement>, run: () => void) => {
    if (event.pointerType === "mouse") return;
    touchFiredRef.current = performance.now();
    run();
  };
  const clickTool = (run: () => void) => {
    if (performance.now() - touchFiredRef.current < 400) return;
    run();
  };
  const hovered = cardHover?.id === id;
  useEffect(() => { if (!hovered) setDeleteArmed(false); }, [hovered]);
  if (!actions) return null;
  const kindLabel = data.kind === "text"
    ? (data.draft ? "未保存" : "文字")
    : data.frozen ? "快照 · 已冻结"
    : data.source?.kind === "legacy-note" ? "旧札记 · 只读" : "材料";
  const openUrl = data.source?.kind === "link"
    ? resolveBrowserNavigationUrl(data.source.url)
    : data.source?.kind === "file" && ["image", "pdf", "audio", "video"].includes(data.adapter)
      ? streamUrl(data.source.token)
      : null;
  const liveUrl = liveEntryOf(data);
  const mountedLiveUrl = liveUrl ? resolveBrowserNavigationUrl(liveUrl) : null;
  const isInteractiveTarget = (target: EventTarget | null) => target instanceof Element
    && Boolean(target.closest("button, input, select, a, iframe, audio, video"));
  const onCardPointerDown = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.pointerType === "mouse" || isInteractiveTarget(event.target)) return;
    touchStartRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    // 触屏长按 600ms = 右键菜单(平板上没有右键; 拖动超过 14px 则取消)
    clearLongPress();
    const startX = event.clientX;
    const startY = event.clientY;
    longPressTimerRef.current = window.setTimeout(() => {
      longPressTimerRef.current = null;
      touchStartRef.current = null;
      actions.openMenu(id, startX, startY);
    }, 600);
  };
  const onCardPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const start = touchStartRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 14) {
      touchStartRef.current = null;
      clearLongPress();
    }
  };
  const onCardPointerUp = (event: ReactPointerEvent<HTMLElement>) => {
    clearLongPress();
    const start = touchStartRef.current;
    touchStartRef.current = null;
    if (!start || start.pointerId !== event.pointerId || isInteractiveTarget(event.target)) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 14) return;
    const now = performance.now();
    const previous = lastTouchTapRef.current;
    if (previous && now - previous.at <= 440 && Math.hypot(event.clientX - previous.x, event.clientY - previous.y) <= 42) {
      lastTouchTapRef.current = null;
      ignoreSyntheticDoubleClickUntilRef.current = now + 800;
      event.preventDefault();
      actions.activateCard(id);
      return;
    }
    lastTouchTapRef.current = { at: now, x: event.clientX, y: event.clientY };
  };
  // 外观由卡自己声明（core 的 presentation）：界面阵列要的是无框、标题外置、缩小时藏标题。
  const presentation = resolveCardPresentation(data.presentation);
  const showTitle = titleVisibleAtZoom(data.presentation, zoom);
  return (
    <article
      className={`material-canvas-card${selected ? " selected" : ""}${spotlight === id ? " spotlight" : ""}${data.frozen ? " frozen" : ""}${data.positionLock?.locked ? " position-locked" : ""}${data.submission ? ` submission-${data.submission.state}` : ""} frame-${presentation.frame} title-${presentation.title} emphasis-${presentation.emphasis}`}
      data-presentation-frame={presentation.frame}
      data-presentation-title={presentation.title}
      onPointerDownCapture={onCardPointerDown}
      onPointerMoveCapture={onCardPointerMove}
      onPointerUpCapture={onCardPointerUp}
      onPointerCancelCapture={() => { touchStartRef.current = null; clearLongPress(); }}
      onContextMenuCapture={(event) => {
        if (performance.now() >= ignoreSyntheticDoubleClickUntilRef.current) return;
        event.preventDefault();
        event.stopPropagation();
      }}
      onDoubleClick={(event) => {
        if (isInteractiveTarget(event.target) || performance.now() < ignoreSyntheticDoubleClickUntilRef.current) return;
        if (data.kind === "text") {
          event.stopPropagation();
          actions.startTextEditing(id);
          return;
        }
        actions.activateCard(id);
      }}
    >
      <NodeToolbar
        isVisible={Boolean(selected || hovered)}
        position={Position.Top}
        offset={8}
        className="material-node-toolbar nodrag nopan"
        onMouseEnter={() => cardHover?.hold(id, true)}
        onMouseLeave={() => cardHover?.hold(id, false)}
      >
        <strong title={data.title}>{data.title}</strong>
        {data.kind === "text" && (
          <FontSizeCombobox id={id} value={data.fontSize || 14} actions={actions} />
        )}
        <button
          type="button"
          title={data.positionLock?.locked ? "解除位置锁定" : "锁定位置（自动排布和 AI 默认不可移动）"}
          onPointerUp={(event) => fireTool(event, () => actions.togglePositionLock(id))}
          onClick={() => clickTool(() => actions.togglePositionLock(id))}
        >
          {data.positionLock?.locked ? <Lock size={15} /> : <LockOpen size={15} />}
        </button>
        {openUrl && (
          <button
            type="button"
            title="打开源材料"
            onPointerUp={(event) => fireTool(event, () => window.open(openUrl, "_blank", "noopener"))}
            onClick={() => clickTool(() => window.open(openUrl, "_blank", "noopener"))}
          >
            <ArrowUpRight size={15} />
          </button>
        )}
        {mountedLiveUrl && (
          <button
            type="button"
            className="material-live-tool"
            title="实机 · 打开 live 形态"
            onPointerUp={(event) => fireTool(event, () => openLiveWindow?.({ url: mountedLiveUrl, title: data.title || "WebTwin" }))}
            onClick={() => clickTool(() => openLiveWindow?.({ url: mountedLiveUrl, title: data.title || "WebTwin" }))}
          >
            <Play size={15} />
          </button>
        )}
        {data.adapter === "image" && (
          <button
            type="button"
            className={actions.sliceEditingId === id ? "active" : ""}
            title={actions.sliceEditingId === id ? "退出图片圈选" : "在图片上圈选并创建注释"}
            onPointerUp={(event) => fireTool(event, () => actions.sliceEditingId === id
              ? actions.cancelRegionSlice() : actions.beginRegionSlice(id))}
            onClick={() => clickTool(() => actions.sliceEditingId === id
              ? actions.cancelRegionSlice() : actions.beginRegionSlice(id))}
          >
            <Crop size={15} />
          </button>
        )}
        {!data.frozen && data.kind === "material" && data.source?.kind === "link" && data.adapter === "web" && (
          <button
            type="button" disabled={actions.freezingId === id}
            title="冻结成静态快照"
            onPointerUp={(event) => fireTool(event, () => void actions.freezeCard(id).catch((reason) => console.error("[material-canvas] freeze failed", reason)))}
            onClick={() => clickTool(() => void actions.freezeCard(id).catch((reason) => console.error("[material-canvas] freeze failed", reason)))}
          >
            {actions.freezingId === id ? <Loader2 className="spin" size={15} /> : <Snowflake size={15} />}
          </button>
        )}
        {data.frozen && (
          <button
            type="button"
            title="从活源新建一张卡"
            onPointerUp={(event) => fireTool(event, () => actions.unfreezeCard(id))}
            onClick={() => clickTool(() => actions.unfreezeCard(id))}
          >
            <Sun size={15} />
          </button>
        )}
        <button
          type="button"
          title="全屏查看"
          onPointerUp={(event) => fireTool(event, () => actions.toggleFullscreen(id))}
          onClick={() => clickTool(() => actions.toggleFullscreen(id))}
        >
          <Maximize2 size={15} />
        </button>
        {actions.reviewEnabled && !data.submission && data.kind !== "group" && (
          <button
            type="button"
            title="提交审阅"
            onPointerUp={(event) => fireTool(event, () => actions.submitForReview(id))}
            onClick={() => clickTool(() => actions.submitForReview(id))}
          >
            <Send size={15} />
          </button>
        )}
        <button
          className={`material-delete${deleteArmed ? " armed" : ""}`}
          type="button"
          title={deleteArmed ? "再次点击确认移除" : "从画布移除"}
          onBlur={() => setDeleteArmed(false)}
          onPointerUp={(event) => fireTool(event, () => {
            if (deleteArmed) actions.removeCard(id);
            else {
              setDeleteArmed(true);
              window.setTimeout(() => setDeleteArmed(false), 2500);
            }
          })}
          onClick={() => clickTool(() => {
            if (deleteArmed) actions.removeCard(id);
            else {
              setDeleteArmed(true);
              window.setTimeout(() => setDeleteArmed(false), 2500);
            }
          })}
        >
          {deleteArmed ? <span>确认</span> : <Trash2 size={15} />}
        </button>
      </NodeToolbar>
      <CardSideHandles />
      {selected && RESIZE_DIRECTIONS.map((direction) => (
        <div
          key={direction}
          className={`material-resize-handle nodrag nopan ${direction}`}
          data-direction={direction}
          style={{ scale: `${1 / Math.max(zoom, 0.01)}` }}
          onPointerDown={(event) => actions.beginResize(id, direction, event, zoom)}
          aria-label={`调整卡片尺寸：${direction}`}
        />
      ))}
      {/* 标题外置：画在卡外上方的一行粗体纯文字（collab platform交互画板的做法），
          它不是卡内头部，所以不参与卡片的内容布局，也不吃掉图片的高度。 */}
      {presentation.title === "above" && showTitle && (
        <div className="material-card-title-above nodrag" title={data.title}>{data.title}</div>
      )}
      {/* header 同时是拖拽面（body 挂了 nodrag），所以**永远渲染**；
          标题不在卡内时它只塌成一条透明窄条，靠 CSS 处理，不能整块删掉，
          否则无框卡就拖不动了。 */}
      <header className={`material-card-drag${presentation.title === "inside" && showTitle ? "" : " bare"}`}>
        {presentation.title === "inside" && showTitle && <>
        {data.kind === "text" ? <FileText size={14} /> : <Link2 size={14} />}
        <strong title={data.title}>{data.title}</strong>
        <small>{kindLabel}</small>
        {data.submission && (
          <small className={`submission-badge s-${data.submission.state}`} title={data.submission.reason || `提审 ${data.submission.submittedAt.slice(0, 10)}`}>
            {data.submission.state === "unreviewed" ? "待看"
              : data.submission.state === "seen" ? "已看"
              : data.submission.state === "fully-passed" ? "✓ 通过" : "✗ 未过"}
          </small>
        )}
        </>}
      </header>
      <div className="material-card-body nodrag nopan nowheel">
        {data.kind === "text" ? (
          <EditableText id={id} data={data} actions={actions} zoom={zoom} />
        ) : <>
          <MaterialBody data={data} zoom={zoom} />
          <AnnotationHotspots cardId={id} data={data} actions={actions} />
          <ManualSliceEditor
            active={actions.sliceEditingId === id}
            onCancel={actions.cancelRegionSlice}
            onCommit={(draft) => actions.addRegionAnnotation(id, draft)}
          />
        </>}
      </div>
      <MaterialTypeBadge data={data} />
    </article>
  );
});

/** 编组容器卡(React Flow subflow 父节点): 半透明底+可改名组名; 子卡 parentId 指向它, 拖组=整组移动。
 *  选中时露 ✎: 编辑组描述(M3c, 正文走 materials/<卡id>.md 分居链)。 */
const GroupCard = memo(function GroupCard({ id, data, selected }: NodeProps<CanvasNode>) {
  const actions = useContext(CardActionsContext);
  if (!actions) return null;
  return (
    <div className={`material-group-card${selected ? " selected" : ""}`}>
      <NodeResizer
        isVisible={Boolean(selected)}
        minWidth={260}
        minHeight={180}
        onResizeStart={() => actions.resizeGroup(id, null, "start")}
        onResize={(_event, params) => actions.resizeGroup(id, params, "move")}
        onResizeEnd={(_event, params) => actions.resizeGroup(id, params, "end")}
      />
      <input
        className="material-group-name nodrag nopan"
        value={data.title}
        aria-label="编组名(可改, 失焦保存)"
        onChange={(event) => actions.renameGroup(id, event.currentTarget.value)}
        onBlur={() => actions.finishGroupRename(id)}
        onKeyDown={(event) => {
          if (event.key === "Enter") (event.target as HTMLInputElement).blur();
          if (event.key !== "Escape") event.stopPropagation();
        }}
        onPointerDown={(event) => event.stopPropagation()}
      />
      {data.samePositionContainer && (
        <label className="material-group-variant nodrag nopan">
          <span>{data.samePositionContainer.dimension}</span>
          <select
            value={data.samePositionContainer.activeVariant}
            aria-label={`${data.samePositionContainer.dimension}切换`}
            onChange={(event) => actions.setSamePositionVariant(id, event.currentTarget.value)}
            onPointerDown={(event) => event.stopPropagation()}
          >
            {data.samePositionContainer.variants.map((variant) => (
              <option key={variant.key} value={variant.key}>{variant.label}</option>
            ))}
          </select>
        </label>
      )}
      <CardSideHandles />
      {selected && (
        <>
          <button
            type="button"
            className="material-group-edit nodrag nopan"
            title="编辑组描述"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => actions.editGroupBody(id)}
          >
            <Pencil size={12} />
          </button>
          <button
            type="button"
            className="material-group-edit nodrag nopan"
            title={data.positionLock?.locked ? "解除组位置锁定" : "锁定组位置"}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => actions.togglePositionLock(id)}
          >
            {data.positionLock?.locked ? <Lock size={12} /> : <LockOpen size={12} />}
          </button>
        </>
      )}
    </div>
  );
});

const nodeTypes = { materialCard: MaterialCard, groupCard: GroupCard };
const edgeTypes = { materialRelation: MaterialRelation };

function CanvasDialog({ title, children, onClose }: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="material-dialog-backdrop" role="presentation" onPointerDown={onClose}>
      <section className="material-dialog" role="dialog" aria-modal="true" aria-label={title} onPointerDown={(event) => event.stopPropagation()}>
        <header><strong>{title}</strong><button type="button" onClick={onClose}><X size={17} /></button></header>
        {children}
      </section>
    </div>
  );
}

export function MaterialNotesWorkspace({
  sessionId,
  sessionTitle,
  canvasId,
  focusCardId,
  onReady,
  onError,
  invokeCommand = tauriInvoke,
}: MaterialNotesWorkspaceProps = {}) {
  const session = useMemo(
    () => sessionId
      // 已带命名空间前缀(external:/whiteboard:/canvas:…)的会话原样使用 —— workshop
      // 自持画布的 storage id 即由这类原生 sessionId 派生；裸 id 才视为 pty external 会话。
      ? { id: sessionId.includes(":") ? sessionId : `external:${sessionId}`, label: sessionTitle || "会话札记" }
      : sessionIdentity(),
    [sessionId, sessionTitle],
  );
  const fallbackStorageId = useMemo(() => canvasStorageId(session.id), [session.id]);
  const requestedCanvasId = canvasId || new URLSearchParams(window.location.search).get("canvas") || "";
  const [resolvedStorageId, setResolvedStorageId] = useState<string | null>(null);
  const [identityReady, setIdentityReady] = useState(false);
  const storageId = resolvedStorageId || fallbackStorageId;
  const [historyOpen, setHistoryOpen] = useState(false);
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [edges, setEdges] = useState<CanvasEdge[]>([]);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  nodesRef.current = nodes;
  edgesRef.current = edges;
  const flowRef = useRef<ReactFlowInstance<CanvasNode, CanvasEdge> | null>(null);
  const stageRef = useRef<HTMLElement | null>(null);
  const past = useRef<CanvasSnapshot[]>([]);
  const future = useRef<CanvasSnapshot[]>([]);
  const dragSnapshot = useRef<CanvasSnapshot | null>(null);
  const saveTimer = useRef<number | null>(null);
  const saveRevision = useRef(0);
  const baseRevision = useRef(0);  // 服务端单调 revision；不再以客户端时间戳排序
  const baseDocument = useRef<MaterialCanvasDocument | null>(null);
  const textWriteTimers = useRef(new Map<string, number>());
  const textWriteRevisions = useRef(new Map<string, number>());
  const [historyRevision, setHistoryRevision] = useState(0);
  const [booting, setBooting] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saveState, setSaveState] = useState<"empty" | "dirty" | "saving" | "saved" | "error">("empty");
  const [saveErrorDetail, setSaveErrorDetail] = useState("");
  const saveStateRef = useRef(saveState);
  saveStateRef.current = saveState;
  const [dialog, setDialog] = useState<"material" | "legacy" | null>(null);
  const [materialInput, setMaterialInput] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [hits, setHits] = useState<FileHit[]>([]);
  const [legacyNotes, setLegacyNotes] = useState<LegacyNote[]>([]);
  const [legacyLoading, setLegacyLoading] = useState(false);
  const [fullscreenId, setFullscreenId] = useState<string | null>(null);
  const [liveWindow, setLiveWindow] = useState<LiveWindowRequest | null>(null);
  const [focusedCardId, setFocusedCardId] = useState<string | null>(null);
  const [canvasZoom, setCanvasZoom] = useState(1);
  const [canvasMenuOpen, setCanvasMenuOpen] = useState(false);
  // M4: ?review=global 深链 —— 直接落在审阅面「全部会话」整体列表(全体会话审阅台深链形态)
  const reviewEnabled = useMemo(() => notesReviewEnabled(resolveNotesAppearance()), []);
  const initialReviewGlobal = useMemo(() => new URLSearchParams(window.location.search).get("review") === "global", []);
  const [view, setView] = useState<"canvas" | "review" | "doc">(initialReviewGlobal && reviewEnabled ? "review" : "canvas");
  const outlineToggleRef = useRef<(() => void) | null>(null); // DocView 挂载时注册的大纲开关
  const [freezingId, setFreezingId] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; kind: "pane" | "card" | "edge"; id?: string } | null>(null);
  const [sliceEditingId, setSliceEditingId] = useState<string | null>(null);
  // 触屏画布长按 = 画布空白右键菜单(panOnDrag 只吃鼠标键, 单指按住不会平移, 可安全借位给长按)
  // 长按抬起会合成 mousedown: 关闭监听在打开后 500ms 内忽略, 菜单不会被自己关掉。
  const menuOpenedAtRef = useRef(0);
  const paneHoldRef = useRef<{ timer: number; x: number; y: number; pointerId: number } | null>(null);
  const clearPaneHold = () => {
    if (paneHoldRef.current) {
      window.clearTimeout(paneHoldRef.current.timer);
      paneHoldRef.current = null;
    }
  };
  useEffect(() => clearPaneHold, []);
  const onStagePointerDown = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.pointerType === "mouse") return;
    const target = event.target as Element | null;
    if (target?.closest(".react-flow__node, .react-flow__edge, button, input, select, a, textarea")) return;
    clearPaneHold();
    const x = event.clientX;
    const y = event.clientY;
    const pointerId = event.pointerId;
    paneHoldRef.current = {
      pointerId, x, y,
      timer: window.setTimeout(() => {
        paneHoldRef.current = null;
        menuOpenedAtRef.current = performance.now();
        setCanvasMenuOpen(false);
        setMenu({ x, y, kind: "pane" });
      }, 600),
    };
  };
  const onStagePointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const hold = paneHoldRef.current;
    if (!hold || hold.pointerId !== event.pointerId) return;
    if (Math.hypot(event.clientX - hold.x, event.clientY - hold.y) > 14) clearPaneHold();
  };
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [hoveredCardId, setHoveredCardId] = useState<string | null>(null);
  const hoverClearTimer = useRef<number | null>(null);
  const [editingRelationId, setEditingRelationId] = useState<string | null>(null);
  const [editingTextId, setEditingTextId] = useState<string | null>(null);
  const editingTextIdRef = useRef<string | null>(null);
  const highlightRef = useRef<string | null>(null);
  const highlightTimer = useRef<number | null>(null);
  const [canvasTitle, setCanvasTitle] = useState("");
  const canvasTitleRef = useRef("");
  const [canvasRevision, setCanvasRevision] = useState(0);
  const [linkFeedback, setLinkFeedback] = useState("");
  const linkFeedbackTimer = useRef<number | null>(null);
  const [layoutState, setLayoutState] = useState<"idle" | "running" | "error">("idle");
  const autoLayoutRef = useRef<(() => void | Promise<void>) | null>(null);
  const moveSnapshot = useRef<CanvasSnapshot | null>(null); // (保留字段, 视口历史改用 stableViewport 方案)
  const skipMoveHistory = useRef(false); // 程序化 setViewport/fitView 不产生历史
  const skipMoveTimer = useRef<number | null>(null);
  // 上一个稳定视口(上一段手势结束时的值)。wheel 平移的 onMoveStart 拿到的已是移动后视口
  // (xyflow panOnScroll 先 translateBy 再发 start), 所以撤销用的 pre 必须来自这里。
  const stableViewport = useRef<{ x: number; y: number; zoom: number } | null>(null);
  // 程序化视口移动(撤回还原/排布后 fitView)不产生撤销历史。
  // 注意: 程序化 setViewport 可能不触发 onMove 事件(目标=当前视口时), 所以不能靠 onMoveEnd 复位, 用兜底定时器
  const markProgrammaticMove = useCallback(() => {
    skipMoveHistory.current = true;
    if (skipMoveTimer.current !== null) window.clearTimeout(skipMoveTimer.current);
    skipMoveTimer.current = window.setTimeout(() => {
      skipMoveHistory.current = false;
      stableViewport.current = flowRef.current?.getViewport() ?? stableViewport.current;
    }, 600);
  }, []);

  // 工具条由 React Flow portal 到卡片外部；离开卡片后延迟收起，让鼠标有时间进入工具条自身。
  const holdCardTools = useCallback((id: string, active: boolean) => {
    if (hoverClearTimer.current !== null) {
      window.clearTimeout(hoverClearTimer.current);
      hoverClearTimer.current = null;
    }
    if (active) {
      setHoveredCardId(id);
      return;
    }
    hoverClearTimer.current = window.setTimeout(() => {
      setHoveredCardId((current) => current === id ? null : current);
      hoverClearTimer.current = null;
    }, 180);
  }, []);
  useEffect(() => () => {
    if (hoverClearTimer.current !== null) window.clearTimeout(hoverClearTimer.current);
  }, []);

  const exitFullscreen = useCallback(() => {
    setFullscreenId(null);
    setFocusedCardId(null);
  }, []);

  const openLiveWindow = useCallback((request: Pick<LiveWindowRequest, "url" | "title">) => {
    const width = Math.min(720, Math.max(420, window.innerWidth - 96));
    const height = Math.min(860, Math.max(520, window.innerHeight - 96));
    setLiveWindow((current) => ({
      ...request,
      width: current?.width ?? width,
      height: current?.height ?? height,
      x: current ? Math.min(current.x, Math.max(0, window.innerWidth - 180)) : Math.max(24, Math.round((window.innerWidth - width) / 2)),
      y: current ? Math.min(current.y, Math.max(0, window.innerHeight - 44)) : Math.max(24, Math.round((window.innerHeight - height) / 2)),
    }));
  }, []);

  // React Flow may consume a node pointer sequence before a nested link receives click.
  useEffect(() => {
    const openPlayable = (event: PointerEvent) => {
      const target = event.target instanceof Element
        ? event.target.closest<HTMLButtonElement>("button.material-type-badge.playable")
        : null;
      const live = target?.dataset.liveEntry;
      if (!live) return;
      event.preventDefault();
      event.stopPropagation();
      const card = nodesRef.current.find((node) => node.data.material
        && liveEntryOf(node.data) === live);
      openLiveWindow({ url: resolveBrowserNavigationUrl(live), title: card?.data.title || "WebTwin" });
    };
    document.addEventListener("pointerdown", openPlayable, true);
    return () => document.removeEventListener("pointerdown", openPlayable, true);
  }, [openLiveWindow]);

  useEffect(() => {
    if (!liveWindow) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setLiveWindow(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [liveWindow]);

  // 卡片被移除时退出其全屏/阅读聚焦; Esc 随时退出全屏
  useEffect(() => {
    if (fullscreenId && !nodes.some((node) => node.id === fullscreenId)) setFullscreenId(null);
    if (focusedCardId && !nodes.some((node) => node.id === focusedCardId)) setFocusedCardId(null);
  }, [focusedCardId, fullscreenId, nodes]);
  useEffect(() => {
    if (!fullscreenId) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") exitFullscreen(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exitFullscreen, fullscreenId]);

  const refreshHistory = () => setHistoryRevision((value) => value + 1);
  const pushHistory = useCallback((value = snapshotOf(nodesRef.current, edgesRef.current, flowRef.current?.getViewport())) => {
    past.current.push(value);
    if (past.current.length > 100) past.current.shift();
    future.current = [];
    refreshHistory();
  }, []);

  // 把 canonical 文档应用到画布。正文是独立 Material，重载布局时保留已读正文与选择态。
  // syncBase=false 用于“服务端已提交 + 本地又有后续输入”的合并渲染；基线仍指向服务端回包。
  const applyDocument = useCallback((document: MaterialCanvasDocument | null, syncBase = true) => {
    const title = document?.title || session.label;
    canvasTitleRef.current = title;
    setCanvasTitle(title);
    if (!document || !document.cards.length) {
      const draft = draftNode();
      nodesRef.current = [draft];
      edgesRef.current = [];
      setNodes([draft]);
      setEdges([]);
      if (syncBase) {
        const empty = emptyCanvasDocument(session.id);
        baseDocument.current = empty;
        baseRevision.current = 0;
        setCanvasRevision(0);
        setSaveState("empty");
      }
      return;
    }
    const previous = new Map(nodesRef.current.map((node) => [node.id, node]));
    const loadedNodes = parentsFirst(document.cards.map((card) => {
      const node = nodeFromCard(card);
      const before = previous.get(card.id);
      if (!before) return node;
      return {
        ...node,
        selected: before.selected,
        data: {
          ...node.data,
          ...(typeof before.data.text === "string" ? { text: before.data.text } : {}),
        },
      };
    }));
    const loadedEdges = document.relations.map(edgeFromRelation);
    nodesRef.current = loadedNodes;
    edgesRef.current = loadedEdges;
    setNodes(loadedNodes);
    setEdges(loadedEdges);
    if (syncBase) {
      baseDocument.current = document;
      baseRevision.current = document.revision;
      setCanvasRevision(document.revision);
      setSaveState("saved");
    }
    // 坐标严重退化(几乎全在同一列) → 自动跑一次分层排布
    if (isDegenerateLayout(loadedNodes)
      && loadedNodes.every(allowsAutoLayout)
      && !loadedNodes.some((node) => node.data.positionLock?.locked)) {
      window.setTimeout(() => autoLayoutRef.current?.(), 0);
    }
  }, [session.label]);

  const writeDocument = useCallback((nextNodes: CanvasNode[], nextEdges: CanvasEdge[]) => {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    const revision = ++saveRevision.current;
    // 标题等于会话名回退值时不写 title(避免把缺省值固化进文档, 盖掉外部设置的画布名)
    const titleForDoc = canvasTitleRef.current === session.label ? "" : canvasTitleRef.current;
    const ownerSessionId = baseDocument.current?.sessionId || session.id;
    const baseline = baseDocument.current || emptyCanvasDocument(ownerSessionId);
    const document = persistedDocument(ownerSessionId, nextNodes, nextEdges, titleForDoc, baseline);
    setSaveState(document.cards.length ? "dirty" : "empty");
    saveTimer.current = window.setTimeout(() => {
      const patch = diffCanvasDocuments(baseline, document);
      if (canvasPatchIsEmpty(patch)) {
        if (saveRevision.current === revision) setSaveState(document.cards.length ? "saved" : "empty");
        return;
      }
      setSaveState("saving");
      const mutationId = safeRandomId("mutation-");
      void invokeCommand<CanvasMutationResult>("notes_canvas_mutate", {
        id: storageId,
        session_id: session.id,
        mutation_id: mutationId,
        actor: `browser:${session.id}`,
        workspace_ref: { kind: "note-session", ref: session.id },
        actor_ref: { kind: "browser", ref: session.id },
        base_revision: baseline.revision,
        patch,
      })
        .then((result) => {
          const canonical = parseCanvasDocument(result.json, session.id);
          if (!canonical) throw new Error("服务端返回了无效画布文档");
          if (saveRevision.current === revision) {
            applyDocument(canonical);
            return;
          }
          // 保存飞行期间又有输入：以 canonical 为新基线，把“发送后新增”的本地差异叠回去。
          const localNow = persistedDocument(
            canonical.sessionId || session.id,
            nodesRef.current,
            edgesRef.current,
            canvasTitleRef.current === session.label ? "" : canvasTitleRef.current,
            canonical,
          );
          const pendingPatch = diffCanvasDocuments(document, localNow);
          baseDocument.current = canonical;
          baseRevision.current = canonical.revision;
          setCanvasRevision(canonical.revision);
          applyDocument(applyCanvasMutation(canonical, pendingPatch), false);
          setSaveState("dirty");
        })
        .catch((reason) => {
          console.error("[material-canvas] save failed", reason);
          // 同一实体真冲突不自动 last-write-wins；保留本地内容并明确显示失败。
          if (saveRevision.current === revision) {
            setSaveState("error");
            setSaveErrorDetail(String(reason).slice(0, 300));
          }
        });
    }, 180);
  }, [invokeCommand, session.id, storageId, applyDocument]);

  const writeTextMaterial = useCallback((id: string, content: string) => {
    const existing = textWriteTimers.current.get(id);
    if (existing !== undefined) window.clearTimeout(existing);
    const revision = (textWriteRevisions.current.get(id) || 0) + 1;
    textWriteRevisions.current.set(id, revision);
    setSaveState("dirty");
    const timer = window.setTimeout(() => {
      setSaveState("saving");
      void invokeCommand<TextMaterialPayload>("notes_text_put", { id, content })
        .then((material) => {
          if (textWriteRevisions.current.get(id) !== revision) return;
          const source: MaterialSource = {
            kind: "file",
            path: material.path,
            token: material.open_token,
            mime: material.mime,
            editableId: material.editableId,
          };
          const next = nodesRef.current.map((node) => node.id === id ? {
            ...node,
            data: { ...node.data, source, draft: false },
          } : node);
          nodesRef.current = next;
          setNodes(next);
          writeDocument(next, edgesRef.current);
        })
        .catch((reason) => {
          console.error("[material-canvas] text save failed", reason);
          if (textWriteRevisions.current.get(id) === revision) setSaveState("error");
        });
    }, 180);
    textWriteTimers.current.set(id, timer);
  }, [invokeCommand, writeDocument]);

  useEffect(() => {
    let live = true;
    setIdentityReady(false);
    const requested = requestedCanvasId.trim();
    const args = { session_id: session.id, session_title: session.label };
    const op = requested
      ? invokeCommand<CanvasResolveResult>("notes_canvas_open", { ...args, canvas_id: requested })
      : invokeCommand<CanvasResolveResult>("notes_canvas_resolve", args);
    void op
      .then((result) => {
        if (!live) return;
        setResolvedStorageId(String(result?.canvas_id || fallbackStorageId));
        setIdentityReady(true);
      })
      .catch(() => {
        if (!live) return;
        setResolvedStorageId(requested || fallbackStorageId);
        setIdentityReady(true);
      });
    return () => { live = false; };
  }, [fallbackStorageId, invokeCommand, requestedCanvasId, session.id, session.label]);

  useEffect(() => {
    if (!identityReady) return;
    let live = true;
    setBooting(true);
    setLoadError("");
    const timeout = window.setTimeout(() => {
      if (!live) return;
      setLoadError("札记存储响应超时。请确认笔记桥可用后重试。");
      setBooting(false);
    }, 12_000);
    void invokeCommand<string | null>("notes_canvas_get", { id: storageId })
      .then((raw) => {
        if (!live) return;
        window.clearTimeout(timeout);
        applyDocument(parseCanvasDocument(raw, session.id));
        setBooting(false);
      })
      .catch((reason) => {
        if (!live) return;
        window.clearTimeout(timeout);
        setLoadError(String(reason));
        setBooting(false);
      });
    return () => {
      live = false;
      window.clearTimeout(timeout);
    };
  }, [identityReady, invokeCommand, session.id, storageId]);

  // 外部 Agent / 另一页面提交后，按服务端 revision 主动追踪；不再等待本页下一次保存撞冲突。
  // 本地 dirty/saving/error 时先不重载，交给原子 mutation 合并或显式冲突，避免吞掉未提交输入。
  useEffect(() => {
    if (booting || loadError) return;
    let disposed = false;
    let running = false;
    const refreshExternal = async () => {
      if (disposed || running || document.visibilityState !== "visible") return;
      if (!new Set(["saved", "empty"]).has(saveStateRef.current)) return;
      running = true;
      try {
        const head = await invokeCommand<CanvasHead | null>("notes_canvas_head", { id: storageId });
        if (!head || head.revision === baseRevision.current || disposed) return;
        const raw = await invokeCommand<string | null>("notes_canvas_get", { id: storageId });
        if (disposed) return;
        const canonical = parseCanvasDocument(raw, session.id);
        if (canonical && canonical.revision !== baseRevision.current) applyDocument(canonical);
      } catch (reason) {
        console.error("[material-canvas] external revision refresh failed", reason);
      } finally {
        running = false;
      }
    };
    void refreshExternal();
    const timer = window.setInterval(() => { void refreshExternal(); }, 1200);
    const onVisibility = () => { if (document.visibilityState === "visible") void refreshExternal(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [applyDocument, booting, invokeCommand, loadError, session.id, storageId]);

  useEffect(() => {
    if (booting) return;
    const message = loadError
      ? { type: "omni-notes-error", sessionId: session.id, error: loadError }
      : { type: "omni-notes-ready", sessionId: session.id };
    if (loadError) onError?.(loadError);
    else onReady?.();
    if (window.parent !== window) {
      const targetOrigin = new URL(document.baseURI).origin;
      window.parent.postMessage(message, targetOrigin);
    }
  }, [booting, loadError, onError, onReady, session.id]);

  useEffect(() => () => {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    for (const timer of textWriteTimers.current.values()) window.clearTimeout(timer);
  }, []);

  const setGraph = useCallback((nextNodes: CanvasNode[], nextEdges: CanvasEdge[], persist = true) => {
    nodesRef.current = nextNodes;
    edgesRef.current = nextEdges;
    setNodes(nextNodes);
    setEdges(nextEdges);
    if (persist) writeDocument(nextNodes, nextEdges);
  }, [writeDocument]);

  // 恢复快照: 节点/边回滚 + 视口(若有)一并还原——「任何移动和放缩」都可撤
  const restoreSnapshot = useCallback((snap: CanvasSnapshot) => {
    for (const node of snap.nodes) {
      if (node.data.kind === "text" && node.data.source?.kind === "file" && node.data.source.editableId && typeof node.data.text === "string") {
        writeTextMaterial(node.data.source.editableId, node.data.text);
      }
    }
    setGraph(cloneNodes(snap.nodes), snap.edges.map((edge) => ({ ...edge })));
    if (snap.viewport) {
      markProgrammaticMove();
      stableViewport.current = snap.viewport;
      window.setTimeout(() => void flowRef.current?.setViewport(snap.viewport!, { duration: 120 }), 0);
    }
  }, [setGraph, writeTextMaterial]);

  const undo = useCallback(() => {
    const previous = past.current.pop();
    if (!previous) return;
    future.current.push(snapshotOf(nodesRef.current, edgesRef.current, flowRef.current?.getViewport()));
    restoreSnapshot(previous);
    refreshHistory();
  }, [restoreSnapshot]);

  const redo = useCallback(() => {
    const following = future.current.pop();
    if (!following) return;
    past.current.push(snapshotOf(nodesRef.current, edgesRef.current, flowRef.current?.getViewport()));
    restoreSnapshot(following);
    refreshHistory();
  }, [restoreSnapshot]);

  // Ctrl+Z 撤回 / Ctrl+Y 与 Ctrl+Shift+Z 还原; 焦点在输入框/文本域/可编辑区时不劫持
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key !== "z" && key !== "y") return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.("input, textarea, [contenteditable='true']")) return;
      event.preventDefault();
      if (key === "y" || (key === "z" && event.shiftKey)) redo();
      else undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const addCard = useCallback((card: Omit<CanvasCardRecord, "x" | "y">) => {
    pushHistory();
    const count = nodesRef.current.length;
    const next = [...nodesRef.current, nodeFromCard({
      ...card,
      x: 80 + (count % 3) * 580,
      y: 72 + Math.floor(count / 3) * 460,
      positionSource: { kind: "user", actor: `browser:${session.id}`, updatedAt: new Date().toISOString() },
    })];
    setGraph(next, edgesRef.current);
    setDialog(null);
  }, [pushHistory, session.id, setGraph]);

  // 右键菜单"新建文字卡": 落在右键点的流坐标上(有空 draft 则只选中, 不堆空卡)
  const addTextAt = useCallback((clientX: number, clientY: number) => {
    const currentDraft = nodesRef.current.find((node) => node.data.kind === "text" && node.data.draft);
    if (currentDraft) {
      setNodes((current) => current.map((node) => ({ ...node, selected: node.id === currentDraft.id })));
      return;
    }
    pushHistory();
    const point = flowRef.current?.screenToFlowPosition({ x: clientX, y: clientY }) || { x: 100, y: 90 };
    setGraph([...nodesRef.current, draftNode(point.x, point.y)], edgesRef.current, false);
  }, [pushHistory, setGraph]);

  const addLink = useCallback((url: string) => {
    const attachedTo = `material:omnidashboard/canvas/${storageId}`;
    void resolveMaterialLink(url, invokeCommand, attachedTo)
      .catch(() => {
        const parsed = new URL(url);
        const adapter = adapterForLink(url);
        const resolved = externalMaterial(parsed, "text/uri-list", "external-link", parsed.hostname || url);
        return {
          title: parsed.hostname || url,
          source: { kind: "link" as const, url },
          adapter,
          width: adapter === "link" ? 420 : 560,
          height: adapter === "link" ? 250 : 420,
          material: importedMaterialEnvelope(resolved, url, "material-resolver-fallback", attachedTo),
        };
      })
      .then((resolved) => addCard({
        id: safeRandomId("material-"),
        kind: "material",
        ...resolved,
      }))
      .catch((reason) => setSearchError(`材料链接导入失败：${String(reason)}`));
  }, [addCard, invokeCommand, storageId]);

  const addFile = useCallback((hit: FileHit) => {
    addCard({
      id: safeRandomId("material-"),
      kind: "material",
      title: hit.name,
      source: { kind: "file", path: hit.path, token: hit.open_token, mime: hit.mime },
      adapter: "auto",
      width: 520,
      height: 400,
    });
  }, [addCard]);

  const addLegacy = useCallback((note: LegacyNote) => {
    addCard({
      id: safeRandomId("legacy-"),
      kind: "material",
      title: note.title,
      source: { kind: "legacy-note", id: note.id },
      adapter: "legacy-note",
      width: 520,
      height: 420,
    });
  }, [addCard]);

  const submitMaterial = useCallback(() => {
    const link = normalizeLink(materialInput);
    if (link) {
      addLink(link);
      setMaterialInput("");
      setHits([]);
      return;
    }
    const query = materialInput.trim();
    if (!query) return;
    setSearching(true);
    setSearchError("");
    void invokeCommand<FileHit[]>("search", { query, limit: 40 })
      .then((result) => {
        setHits(result);
        if (!result.length) setSearchError("没有找到文件；可换文件名、路径片段或关键词。");
      })
      .catch((reason) => setSearchError(String(reason)))
      .finally(() => setSearching(false));
  }, [addLink, invokeCommand, materialInput]);

  const openLegacy = useCallback(() => {
    setDialog("legacy");
    if (legacyNotes.length || legacyLoading) return;
    setLegacyLoading(true);
    void invokeCommand<LegacyNote[]>("notes_legacy_list")
      .then(setLegacyNotes)
      .catch((reason) => setSearchError(String(reason)))
      .finally(() => setLegacyLoading(false));
  }, [invokeCommand, legacyLoading, legacyNotes.length]);

  const activateCard = useCallback((id: string) => {
    const node = nodesRef.current.find((item) => item.id === id);
    if (!node || node.data.kind === "group") return;
    if (focusedCardId === id) {
      setFullscreenId(id);
      return;
    }
    setFocusedCardId(id);
    const flow = flowRef.current;
    if (!flow) return;
    const size = dimensionsOf(node);
    const stage = stageRef.current?.getBoundingClientRect();
    const zoom = Math.min(
      1.35,
      Math.max(0.45, Math.min(
        ((stage?.width || 1200) * 0.82) / size.width,
        ((stage?.height || 760) * 0.82) / size.height,
      )),
    );
    const absolute = flow.getInternalNode(node.id)?.internals.positionAbsolute ?? node.position;
    // 触屏浏览器会在 pointerup 后补发 click；若此处立刻移动卡片，click 会落到
    // 已经移到指针下方的画布空白处，进而清掉刚建立的二段聚焦状态。
    window.setTimeout(() => {
      markProgrammaticMove();
      setCanvasZoom(zoom);
      void flow.setCenter(
        absolute.x + size.width / 2,
        absolute.y + size.height / 2,
        { zoom, duration: 280 },
      );
    }, 0);
  }, [focusedCardId, markProgrammaticMove]);

  // ---- 组描述(M3c): 正文走 materials/<组卡id>.md 分居链, 卡上挂 source 引用 ----
  const [groupBodyEdit, setGroupBodyEdit] = useState<{ id: string; text: string; loading: boolean } | null>(null);
  const saveGroupBody = useCallback(async () => {
    const edit = groupBodyEdit;
    if (!edit) return;
    const material = await invokeCommand<TextMaterialPayload>("notes_text_put", { id: edit.id, content: edit.text });
    const source = {
      kind: "file" as const,
      path: material.path,
      token: material.open_token,
      mime: material.mime,
      editableId: edit.id,
    };
    const next = nodesRef.current.map((node) => node.id === edit.id ? { ...node, data: { ...node.data, source } } : node);
    nodesRef.current = next;
    setNodes(next);
    writeDocument(next, edgesRef.current);
    setGroupBodyEdit(null);
  }, [groupBodyEdit, invokeCommand, writeDocument]);

  // ---- 提审(M1a): 提交/裁决都走既有 writeDocument → diff → mutate 链 ----
  const setCardSubmission = useCallback((id: string, submission: CardSubmission) => {
    const next = nodesRef.current.map((node) => node.id === id ? { ...node, data: { ...node.data, submission } } : node);
    nodesRef.current = next;
    setNodes(next);
    writeDocument(next, edgesRef.current);
  }, [writeDocument]);

  const submitForReview = useCallback((id: string) => {
    if (!reviewEnabled) return;
    const node = nodesRef.current.find((item) => item.id === id);
    if (!node || node.data.kind === "group") return;
    const existing = node.data.submission;
    if (existing && (existing.state === "unreviewed" || existing.state === "seen")) return;
    setCardSubmission(id, {
      state: "unreviewed",
      submittedAt: new Date().toISOString(),
      submittedBy: `browser:${session.id}`,
    });
  }, [reviewEnabled, setCardSubmission, session.id]);

  const verdictCard = useCallback((id: string, pass: boolean, reason?: string) => {
    const node = nodesRef.current.find((item) => item.id === id);
    const sub = node?.data.submission;
    if (!node || !sub) return;
    setCardSubmission(id, {
      ...sub,
      state: pass ? "fully-passed" : "not-fully-passed",
      verdictAt: new Date().toISOString(),
      verdictBy: `browser:${session.id}`,
      ...(reason ? { reason } : {}),
    });
  }, [setCardSubmission, session.id]);

  // 「看过」= 点开该卡全屏/详情 → 自动置 seen
  useEffect(() => {
    if (!fullscreenId) return;
    const node = nodesRef.current.find((item) => item.id === fullscreenId);
    const sub = node?.data.submission;
    if (node && sub?.state === "unreviewed") {
      setCardSubmission(fullscreenId, { ...sub, state: "seen", seenAt: new Date().toISOString() });
    }
  }, [fullscreenId, setCardSubmission]);

  const cardActions = useMemo<CardActions>(() => ({
    activateCard,
    openMenu: (id, x, y) => {
      menuOpenedAtRef.current = performance.now();
      setCanvasMenuOpen(false);
      setMenu({ x, y, kind: "card", id });
    },
    submitForReview,
    verdictCard,
    reviewEnabled,
    editGroupBody: (id) => {
      setGroupBodyEdit({ id, text: "", loading: true });
      void invokeCommand<TextMaterialPayload>("notes_text_get", { id })
        .then((material) => setGroupBodyEdit({ id, text: material.content || "", loading: false }))
        .catch(() => setGroupBodyEdit({ id, text: "", loading: false }));
    },
    beginTextEdit: () => pushHistory(),
    finishTextEdit: (id) => {
      const node = nodesRef.current.find((item) => item.id === id);
      if (node && ((node.data.text || "").trim() || node.data.source?.kind === "file")) {
        writeTextMaterial(node.data.source?.kind === "file" && node.data.source.editableId
          ? node.data.source.editableId
          : id, node.data.text || "");
      }
    },
    editingTextId,
    startTextEditing: (id) => {
      const node = nodesRef.current.find((item) => item.id === id);
      if (!node) return;
      if (editingTextIdRef.current && editingTextIdRef.current !== id) {
        const previous = nodesRef.current.find((item) => item.id === editingTextIdRef.current);
        if (previous && ((previous.data.text || "").trim() || previous.data.source?.kind === "file")) {
          writeTextMaterial(previous.data.source?.kind === "file" && previous.data.source.editableId
            ? previous.data.source.editableId
            : previous.id, previous.data.text || "");
        }
      }
      pushHistory();
      editingTextIdRef.current = id;
      setEditingTextId(id);
    },
    stopTextEditing: (id) => {
      const currentId = id ?? editingTextIdRef.current;
      if (!currentId || (id && editingTextIdRef.current !== id)) return;
      const node = nodesRef.current.find((item) => item.id === currentId);
      if (node && ((node.data.text || "").trim() || node.data.source?.kind === "file")) {
        writeTextMaterial(node.data.source?.kind === "file" && node.data.source.editableId
          ? node.data.source.editableId
          : currentId, node.data.text || "");
      }
      editingTextIdRef.current = null;
      setEditingTextId(null);
    },
    hydrateText: (id, material) => {
      const source: MaterialSource = {
        kind: "file",
        path: material.path,
        token: material.open_token,
        mime: material.mime,
        editableId: material.editableId,
      };
      const current = nodesRef.current.find((node) => node.id === id);
      const currentSource = current?.data.source;
      const needsSourcePersist = currentSource?.kind !== "file"
        || currentSource.path !== source.path
        || currentSource.token !== source.token
        || currentSource.editableId !== source.editableId;
      const next = nodesRef.current.map((node) => node.id === id ? {
        ...node,
        data: { ...node.data, text: material.content || "", source, draft: false },
      } : node);
      nodesRef.current = next;
      setNodes(next);
      // 正文独立存储；已有 canonical source 时，hydrate 只补内存正文。
      // 否则多张文字卡并发回读会各自抢同一个 base_revision，放大成无意义 409。
      if (needsSourcePersist) writeDocument(next, edgesRef.current);
    },
    changeText: (id, value) => {
      const current = nodesRef.current.find((node) => node.id === id);
      const editableId = current?.data.source?.kind === "file" && current.data.source.editableId
        ? current.data.source.editableId
        : id;
      const next = nodesRef.current.map((node) => {
        if (node.id !== id) return node;
        const firstLine = value.trim().split(/\r?\n/, 1)[0]?.slice(0, 60);
        return {
          ...node,
          data: {
            ...node.data,
            text: value,
            title: firstLine || "随手记",
            draft: !value.trim() && !node.data.source,
          },
        };
      });
      nodesRef.current = next;
      setNodes(next);
      if (value.trim() || current?.data.source) writeTextMaterial(editableId, value);
      else writeDocument(next, edgesRef.current);
    },
    fitTextCardHeight: (id, bodyScrollHeight, persist) => {
      const current = nodesRef.current.find((node) => node.id === id);
      if (!current || current.data.kind !== "text") return;
      const height = desiredTextCardHeight(bodyScrollHeight);
      const size = dimensionsOf(current);
      if (Math.abs(size.height - height) < 1) return;
      const next = nodesRef.current.map((node) => node.id === id
        ? { ...node, style: { ...node.style, height } }
        : node);
      nodesRef.current = next;
      setNodes(next);
      if (persist) writeDocument(next, edgesRef.current);
    },
    setTextSize: (id, requestedSize) => {
      const current = nodesRef.current.find((node) => node.id === id);
      if (!current || current.data.kind !== "text") return;
      const fontSize = Math.min(72, Math.max(8, Math.round(requestedSize)));
      if (fontSize === (current.data.fontSize || 14)) return;
      pushHistory();
      const next = nodesRef.current.map((node) => node.id === id
        ? { ...node, data: { ...node.data, fontSize } }
        : node);
      nodesRef.current = next;
      setNodes(next);
      writeDocument(next, edgesRef.current);
    },
    togglePositionLock: (id) => {
      const current = nodesRef.current.find((node) => node.id === id);
      if (!current) return;
      pushHistory();
      const locked = !current.data.positionLock?.locked;
      const next = nodesRef.current.map((node) => node.id === id ? {
        ...node,
        draggable: !locked,
        data: {
          ...node.data,
          positionLock: {
            locked,
            actor: `browser:${session.id}`,
            updatedAt: new Date().toISOString(),
          },
        },
      } : node);
      nodesRef.current = next;
      setNodes(next);
      writeDocument(next, edgesRef.current);
    },
    removeCard: (id) => {
      pushHistory();
      const target = nodesRef.current.find((node) => node.id === id);
      let nextNodes = nodesRef.current.filter((node) => node.id !== id);
      // 删组卡: 子卡(含子组)parentId 上移一层挂到被删组的父级, 不留悬空 parentId
      if (target?.data.kind === "group") {
        nextNodes = nextNodes.map((node) => {
          if (node.parentId !== id) return node;
          const { parentId: _p, ...restData } = node.data;
          const grandParentId = target.parentId;
          return {
            ...node,
            parentId: grandParentId,
            extent: grandParentId ? node.extent : undefined,
            position: { x: target.position.x + node.position.x, y: target.position.y + node.position.y },
            data: grandParentId ? { ...restData, parentId: grandParentId } : restData,
          };
        });
      }
      const nextEdges = edgesRef.current.filter((edge) => edge.source !== id && edge.target !== id);
      setGraph(nextNodes.length ? nextNodes : [draftNode()], nextEdges);
    },
    toggleFullscreen: (id) => {
      setFocusedCardId(null);
      setFullscreenId(id);
    },
    freezeCard: async (id) => {
      const node = nodesRef.current.find((item) => item.id === id);
      if (!node) throw new Error(`没找到卡片 ${id}`);
      if (node.data.frozen) throw new Error("已是冻结快照卡");
      const source = node.data.source;
      if (node.data.kind !== "material" || source?.kind !== "link" || node.data.adapter !== "web") {
        throw new Error("只有 web 页面链接卡能冻结");
      }
      setFreezingId(id);
      try {
        // 捕获是秒级慢操作且只有 HTTP 桥有实现(桌面 Rust 侧无此命令), 直连 webNotesInvoke 给足 60s
        const payload = await webNotesInvoke<FileHit & { snapshotId: string; stats: Record<string, number> }>(
          "notes_snapshot_capture", { id, url: source.url }, 60_000);
        pushHistory();
        const frozen = { capturedAt: new Date().toISOString(), from: source, fromAdapter: node.data.adapter };
        const next = nodesRef.current.map((item) => item.id !== id ? item : {
          ...item,
          data: {
            ...item.data,
            frozen,
            source: { kind: "file" as const, path: payload.path, token: payload.open_token, mime: payload.mime },
          },
        });
        setGraph(next, edgesRef.current);
        return payload.stats;
      } finally {
        setFreezingId(null);
      }
    },
    unfreezeCard: (id) => {
      const node = nodesRef.current.find((item) => item.id === id);
      const frozen = node?.data.frozen;
      if (!node || !frozen) throw new Error("不是冻结快照卡");
      pushHistory();
      const size = dimensionsOf(node);
      const live = nodeFromCard({
        id: safeRandomId("material-"),
        kind: "material",
        title: node.data.title,
        source: frozen.from,
        adapter: frozen.fromAdapter,
        x: node.position.x + size.width + 60,
        y: node.position.y,
        width: size.width,
        height: size.height,
        positionSource: { kind: "user", actor: `browser:${session.id}`, updatedAt: new Date().toISOString() },
      });
      setGraph([...nodesRef.current, live], edgesRef.current);
      return { created: live.id };
    },
    copyCardRef: async (id) => {
      // 可被人和 Agent 一眼识别的跨应用引用；保留原始会话/卡片 id 以便精确寻址。
      const raw = session.id.startsWith("external:") ? session.id.slice("external:".length) : session.id;
      const ref = formatCanvasCardReference("omnicompany", raw, id);
      await copyPlainText(ref);
      return ref;
    },
    renameGroup: (id, title) => {
      const next = nodesRef.current.map((node) => node.id === id ? {
        ...node,
        data: { ...node.data, title },
      } : node);
      nodesRef.current = next;
      setNodes(next);
    },
    sliceEditingId,
    beginRegionSlice: (id) => {
      const node = nodesRef.current.find((item) => item.id === id);
      if (node?.data.adapter === "image") setSliceEditingId(id);
    },
    cancelRegionSlice: () => setSliceEditingId(null),
    addRegionAnnotation: (id, draft) => {
      const source = nodesRef.current.find((item) => item.id === id);
      if (!source || source.data.adapter !== "image" || !draft.box) return;
      pushHistory();
      const now = new Date().toISOString();
      const ref = safeRandomId("region-");
      const envelope = source.data.material as Record<string, unknown> | undefined;
      const representationId = typeof envelope?.representation_id === "string" ? envelope.representation_id : undefined;
      const label = draft.note.split(/[。！？\n]/, 1)[0]?.trim().slice(0, 30) || "规划切片";
      const slice: InlineCanvasSlice = {
        kind: "region",
        ref,
        ...(representationId ? { representationId } : {}),
        box: draft.box,
        label,
        note: draft.note,
        scene: draft.scene,
        specialDisplay: draft.specialDisplay,
        emphasis: "normal",
        createdBy: `browser:${session.id}`,
        createdAt: now,
      };
      const updatedSource = {
        ...source,
        data: { ...source.data, material: withInlineCanvasSlice(envelope, slice) },
      };
      const existingNotes = nodesRef.current.filter((node) => {
        const metadata = (node.data.material as Record<string, unknown> | undefined)?.metadata;
        return metadata && typeof metadata === "object"
          && (metadata as Record<string, unknown>).generated_from_slice_card === id;
      }).length;
      const sourceSize = dimensionsOf(source);
      const noteId = safeRandomId("slice-note-");
      const lines = [
        draft.note,
        draft.scene ? `\n- 场景构造：${draft.scene}` : "",
        draft.specialDisplay ? `\n- 特别显示：${draft.specialDisplay}` : "",
      ].filter(Boolean).join("");
      setSaveState("saving");
      void invokeCommand<TextMaterialPayload>("notes_text_put", { id: noteId, content: lines })
        .then((textMaterial) => {
          const note = nodeFromCard({
            id: noteId,
            kind: "text",
            title: `圈选注释｜${label}`,
            text: lines,
            adapter: "text",
            source: {
              kind: "file",
              path: textMaterial.path,
              token: textMaterial.open_token,
              mime: textMaterial.mime,
              editableId: textMaterial.editableId,
            },
            x: source.position.x + sourceSize.width + 50,
            y: source.position.y + existingNotes * 245,
            width: 340,
            height: desiredTextCardHeight(Math.max(150, lines.length * 1.2)),
            ...(source.parentId ? { parentId: source.parentId } : {}),
            material: { metadata: { generated_from_slice_card: id, generated_from_slice_ref: ref } },
            positionSource: { kind: "user", actor: `browser:${session.id}`, updatedAt: now },
          });
          const nextNodes = parentsFirst(nodesRef.current.map((node) => node.id === id ? updatedSource : node).concat(note));
          const relation = edgeFromRelation({
            id: safeRandomId("relation-"),
            source: id,
            target: noteId,
            label: "圈选注释",
            kind: "continues",
            path: "curve",
            arrow: "closed",
            sourceHandle: canvasSliceHandleId(slice),
            targetHandle: "left",
            sourceSlice: slice,
          });
          setSliceEditingId(null);
          setGraph(nextNodes, [...edgesRef.current, relation]);
        })
        .catch((reason) => {
          console.error("[material-canvas] slice note save failed", reason);
          setSaveState("error");
          setSaveErrorDetail(String(reason).slice(0, 300));
        });
    },
    setSamePositionVariant: (id, variantKey) => {
      const current = nodesRef.current.find((node) => node.id === id);
      const container = current?.data.samePositionContainer;
      if (!current || !container || container.activeVariant === variantKey) return;
      if (!container.variants.some((variant) => variant.key === variantKey)) return;
      pushHistory();
      const next = nodesRef.current.map((node) => node.id === id ? {
        ...node,
        data: {
          ...node.data,
          samePositionContainer: { ...container, activeVariant: variantKey },
        },
      } : node);
      nodesRef.current = next;
      setNodes(next);
      writeDocument(next, edgesRef.current);
    },
    finishGroupRename: (id) => {
      const node = nodesRef.current.find((item) => item.id === id);
      if (!node || node.data.kind !== "group") return;
      const title = node.data.title.trim() || "编组";
      if (title !== node.data.title) {
        const next = nodesRef.current.map((item) => item.id === id ? { ...item, data: { ...item.data, title } } : item);
        nodesRef.current = next;
        setNodes(next);
      }
      writeDocument(nodesRef.current, edgesRef.current);
    },
    // 组卡缩放(NodeResizer): start 入历史快照, move 实时改 style, end 落盘
    resizeGroup: (id, params, phase) => {
      if (phase === "start") { pushHistory(); return; }
      if (!params) return;
      const next = nodesRef.current.map((node) => node.id === id ? {
        ...node,
        position: { x: params.x, y: params.y },
        style: { ...node.style, width: params.width, height: params.height },
        data: {
          ...node.data,
          positionSource: { kind: "user", actor: `browser:${session.id}`, updatedAt: new Date().toISOString() },
        },
      } : node);
      nodesRef.current = next;
      setNodes(next);
      if (phase === "end") writeDocument(next, edgesRef.current);
    },
    freezingId,
    beginResize: (id, direction, event, zoom) => {
      event.preventDefault();
      event.stopPropagation();
      pushHistory();
      const target = event.currentTarget;
      const pointerId = event.pointerId;
      const startX = event.clientX;
      const startY = event.clientY;
      const startNode = nodesRef.current.find((node) => node.id === id);
      if (!startNode) return;
      const startSize = dimensionsOf(startNode);
      const start = { x: startNode.position.x, y: startNode.position.y, ...startSize };
      target.setPointerCapture(pointerId);
      const move = (pointer: PointerEvent) => {
        if (pointer.pointerId !== pointerId) return;
        const dx = (pointer.clientX - startX) / Math.max(zoom, 0.01);
        const dy = (pointer.clientY - startY) / Math.max(zoom, 0.01);
        const fromLeft = direction.includes("left");
        const fromRight = direction.includes("right");
        const fromTop = direction.includes("top");
        const fromBottom = direction.includes("bottom");
        const width = fromLeft ? Math.max(260, start.width - dx) : fromRight ? Math.max(260, start.width + dx) : start.width;
        const height = fromTop ? Math.max(180, start.height - dy) : fromBottom ? Math.max(180, start.height + dy) : start.height;
        const next = nodesRef.current.map((node) => node.id === id ? {
          ...node,
          position: {
            x: fromLeft ? start.x + start.width - width : start.x,
            y: fromTop ? start.y + start.height - height : start.y,
          },
          style: { ...node.style, width, height },
          data: {
            ...node.data,
            positionSource: { kind: "user", actor: `browser:${session.id}`, updatedAt: new Date().toISOString() },
          },
        } : node);
        nodesRef.current = next;
        setNodes(next);
      };
      const finish = (pointer: PointerEvent) => {
        if (pointer.pointerId !== pointerId) return;
        target.removeEventListener("pointermove", move);
        target.removeEventListener("pointerup", finish);
        target.removeEventListener("pointercancel", finish);
        if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId);
        writeDocument(nodesRef.current, edgesRef.current);
      };
      target.addEventListener("pointermove", move);
      target.addEventListener("pointerup", finish);
      target.addEventListener("pointercancel", finish);
    },
  }), [activateCard, submitForReview, verdictCard, reviewEnabled, invokeCommand, pushHistory, setGraph, writeDocument, writeTextMaterial, freezingId, session.id, sliceEditingId, editingTextId]);

  // omni canvas 活画布 ops(cb 文件队列桥, 见 canvasOps.ts): state/update/remove/viewport/focus/highlight
  useEffect(() => {
    const applyHighlight = (id: string | null, seconds = 6) => {
      if (highlightTimer.current !== null) {
        window.clearTimeout(highlightTimer.current);
        highlightTimer.current = null;
      }
      highlightRef.current = id;
      setHighlightId(id);
      if (id && seconds > 0) {
        highlightTimer.current = window.setTimeout(() => {
          highlightRef.current = null;
          setHighlightId(null);
          highlightTimer.current = null;
        }, seconds * 1000);
      }
    };
    return registerCanvasHandler(storageId, {
      state: () => ({
        canvas: storageId,
        viewport: flowRef.current ? flowRef.current.getViewport() : null,
        cards: nodesRef.current.length,
        selected: nodesRef.current.filter((node) => node.selected).map((node) => node.id),
        highlighted: highlightRef.current,
      }),
      update: (args: CanvasOpArgs) => {
        const id = String(args.card || "");
        if (!nodesRef.current.some((item) => item.id === id)) throw new Error(`没找到卡片 ${id}`);
        pushHistory();
        const next = nodesRef.current.map((item) => item.id !== id ? item : {
          ...item,
          position: {
            x: typeof args.x === "number" ? args.x : item.position.x,
            y: typeof args.y === "number" ? args.y : item.position.y,
          },
          style: {
            ...item.style,
            width: typeof args.width === "number" ? Math.max(260, args.width) : item.style?.width,
            height: typeof args.height === "number" ? Math.max(180, args.height) : item.style?.height,
          },
          data: {
            ...item.data,
            title: typeof args.title === "string" && args.title.trim() ? args.title : item.data.title,
            adapter: typeof args.adapter === "string" ? (args.adapter as MaterialAdapter) : item.data.adapter,
          },
        });
        setGraph(next, edgesRef.current);
        return { updated: id };
      },
      remove: (args: CanvasOpArgs) => {
        const id = String(args.card || "");
        if (!nodesRef.current.some((item) => item.id === id)) throw new Error(`没找到卡片 ${id}`);
        cardActions.removeCard(id);
        return { removed: id };
      },
      viewport: (args: CanvasOpArgs) => {
        const flow = flowRef.current;
        if (!flow) throw new Error("画布视图未就绪");
        const current = flow.getViewport();
        const next = {
          x: typeof args.x === "number" ? args.x : current.x,
          y: typeof args.y === "number" ? args.y : current.y,
          zoom: typeof args.zoom === "number" ? args.zoom : current.zoom,
        };
        void flow.setViewport(next, { duration: 200 });
        return { viewport: next };
      },
      focus: (args: CanvasOpArgs) => {
        const id = String(args.card || "");
        const node = nodesRef.current.find((item) => item.id === id);
        if (!node) throw new Error(`没找到卡片 ${id}`);
        const flow = flowRef.current;
        if (!flow) throw new Error("画布视图未就绪");
        const size = dimensionsOf(node);
        const zoom = typeof args.zoom === "number" ? args.zoom : flow.getViewport().zoom;
        void flow.setCenter(node.position.x + size.width / 2, node.position.y + size.height / 2, { zoom, duration: 300 });
        return { focused: id, zoom };
      },
      highlight: (args: CanvasOpArgs) => {
        if (args.clear) {
          applyHighlight(null);
          return { cleared: true };
        }
        const id = String(args.card || "");
        if (!nodesRef.current.some((item) => item.id === id)) throw new Error(`没找到卡片 ${id}`);
        const seconds = typeof args.seconds === "number" ? args.seconds : 6;
        applyHighlight(id, seconds);
        return { highlighted: id, seconds };
      },
      freeze: async (args: CanvasOpArgs) => {
        const id = String(args.card || "");
        const stats = await cardActions.freezeCard(id);
        return { frozen: id, stats };
      },
      unfreeze: (args: CanvasOpArgs) => ({ unfrozen: String(args.card || ""), ...cardActions.unfreezeCard(String(args.card || "")) }),
      say: (args: CanvasOpArgs) => {
        // AI 回写: 新建一块带内容的文字卡(走 changeText 既有落盘链, 标题取首行)
        const text = String(args.text || "").trim();
        if (!text) throw new Error("空内容");
        pushHistory();
        const count = nodesRef.current.length;
        const node = draftNode(80 + (count % 3) * 580, 72 + Math.floor(count / 3) * 460);
        setGraph([...nodesRef.current, node], edgesRef.current, false);
        cardActions.changeText(node.id, text);
        return { created: node.id };
      },
      rewrite: (args: CanvasOpArgs) => {
        // 覆写既有文字卡正文(如 @AI 回应覆写提问卡), 走 changeText 既有链
        const id = String(args.card || "");
        const node = nodesRef.current.find((item) => item.id === id);
        if (!node || node.data.kind !== "text") throw new Error(`不是文字卡或没找到: ${id}`);
        cardActions.beginTextEdit();
        cardActions.changeText(id, String(args.text ?? ""));
        return { rewritten: id };
      },
    });
  }, [storageId, cardActions, pushHistory, setGraph]);

  // 卡片内 Ctrl/Cmd+滚轮不触发画布缩放，也不冒泡成浏览器页面缩放。
  // 空白画布仍交给 React Flow：普通滚轮平移，Ctrl/Cmd+滚轮与触控板 pinch 按指针位置缩放。
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      const inCard = (event.target as Element | null)?.closest?.(".material-canvas-card");
      if (!inCard) return;
      event.preventDefault();
      event.stopPropagation();
    };
    stage.addEventListener("wheel", onWheel, { passive: false, capture: true });
    return () => stage.removeEventListener("wheel", onWheel, { capture: true });
  }, [booting]);

  const onNodesChange = useCallback((changes: NodeChange<CanvasNode>[]) => {
    const next = applyNodeChanges(changes, nodesRef.current);
    nodesRef.current = next;
    setNodes(next);
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange<CanvasEdge>[]) => {
    const next = applyEdgeChanges(changes, edgesRef.current);
    edgesRef.current = next;
    setEdges(next);
  }, []);

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || connection.source === connection.target) return;
    if (edgesRef.current.some((edge) => edge.source === connection.source && edge.target === connection.target)) return;
    pushHistory();
    // 从切片 handle 拖出来的边要把切片记进关系里，否则它只是个 handle 字符串，
    // 下一次自动排布就把"从这个按钮出发"擦掉了。
    const sourceSlice = lookupCanvasSlice(connection.source, connection.sourceHandle);
    const targetSlice = lookupCanvasSlice(connection.target, connection.targetHandle);
    const next = addEdge({
      ...connection,
      id: safeRandomId("relation-"),
      type: "materialRelation",
      data: {
        path: "curve", arrow: "closed", userLabel: "",
        ...(sourceSlice ? { sourceSlice } : {}),
        ...(targetSlice ? { targetSlice } : {}),
      },
      markerEnd: relationMarker("closed"),
    }, edgesRef.current);
    edgesRef.current = next;
    setEdges(next);
    writeDocument(nodesRef.current, next);
  }, [pushHistory, writeDocument]);

  const onReconnect = useCallback((oldEdge: CanvasEdge, connection: Connection) => {
    pushHistory();
    const next = reconnectEdge(oldEdge, connection, edgesRef.current);
    edgesRef.current = next;
    setEdges(next);
    writeDocument(nodesRef.current, next);
  }, [pushHistory, writeDocument]);

  const removeRelation = useCallback((edgeId: string) => {
    if (!edgesRef.current.some((edge) => edge.id === edgeId)) return;
    pushHistory();
    const next = edgesRef.current.filter((edge) => edge.id !== edgeId);
    edgesRef.current = next;
    setEdges(next);
    writeDocument(nodesRef.current, next);
  }, [pushHistory, writeDocument]);

  const relationActions = useMemo<RelationActions>(() => ({
    update: (id, patch) => {
      const current = edgesRef.current.find((edge) => edge.id === id);
      if (!current) return;
      const userLabel = patch.userLabel !== undefined ? patch.userLabel : current.data?.userLabel;
      const routePoints = patch.routePoints !== undefined ? patch.routePoints : current.data?.routePoints;
      const userLabelPosition = patch.userLabelPosition !== undefined
        ? patch.userLabelPosition : current.data?.userLabelPosition;
      const path = patch.path || current.data?.path || "curve";
      const arrow = patch.arrow || current.data?.arrow || "closed";
      const kind = patch.kind !== undefined ? (patch.kind ?? undefined) : current.data?.kind;
      if (userLabel === current.data?.userLabel
        && routePoints === current.data?.routePoints
        && userLabelPosition === current.data?.userLabelPosition
        && path === current.data?.path && arrow === current.data?.arrow && kind === current.data?.kind) return;
      pushHistory();
      const next = edgesRef.current.map((edge) => edge.id === id ? {
        ...edge,
        data: { ...edge.data, path, arrow, kind, userLabel, routePoints, userLabelPosition },
        markerEnd: relationMarker(arrow),
      } : edge);
      edgesRef.current = next;
      setEdges(next);
      writeDocument(nodesRef.current, next);
    },
    select: (id) => {
      setEditingRelationId(id);
      const nextNodes = nodesRef.current.map((node) => node.selected ? { ...node, selected: false } : node);
      const nextEdges = edgesRef.current.map((edge) => ({ ...edge, selected: edge.id === id }));
      nodesRef.current = nextNodes;
      edgesRef.current = nextEdges;
      setNodes(nextNodes);
      setEdges(nextEdges);
    },
    finish: (id) => setEditingRelationId((current) => current === id ? null : current),
    editingId: editingRelationId,
  }), [editingRelationId, pushHistory, writeDocument]);

  // 自动排布交给 ELK：按真实卡片尺寸做分层、交叉最小化、连通分量压缩和编组布局。
  // 只动坐标与组卡包围盒，不改内容、关系及关系文字。
  const runAutoLayout = useCallback(async (scope: AutoLayoutScope = { kind: "all" }) => {
    if (!nodesRef.current.length) return;
    if (layoutState === "running") return;
    setLayoutState("running");
    const scopedNodes = (allNodes: CanvasNode[]) => {
      if (scope.kind === "all") return allNodes;
      if (scope.kind === "top-level") return allNodes.filter((node) => !node.parentId);
      const included = new Set<string>([scope.groupId]);
      let changed = true;
      while (changed) {
        changed = false;
        for (const node of allNodes) {
          if (node.parentId && included.has(node.parentId) && !included.has(node.id)) {
            included.add(node.id);
            changed = true;
          }
        }
      }
      return allNodes.filter((node) => included.has(node.id));
    };
    const inputNodes = scopedNodes(nodesRef.current);
    if (!inputNodes.length) { setLayoutState("idle"); return; }
    const scopedIds = new Set(inputNodes.map((node) => node.id));
    const inputEdges = edgesRef.current.filter((edge) => scopedIds.has(edge.source) && scopedIds.has(edge.target));
    const inputById = new Map(nodesRef.current.map((node) => [node.id, node]));
    const movementLockedIds = new Set<string>();
    for (const node of inputNodes) {
      // “全部智能排布”是用户明确发出的重排命令；历史上的 user/imported
      // positionSource 不能因此被当成隐式锁，否则整张已编辑画布会 0 位移，
      // 看起来按钮执行成功、实际什么也没做。只有显式位置锁才禁止移动。
      if (!node.data.positionLock?.locked) continue;
      let current: CanvasNode | undefined = node;
      const visited = new Set<string>();
      while (current && !visited.has(current.id)) {
        visited.add(current.id);
        movementLockedIds.add(current.id);
        current = current.parentId ? inputById.get(current.parentId) : undefined;
      }
    }
    const layoutDimensionsOf = (node: CanvasNode) => movementLockedIds.has(node.id)
      ? dimensionsOf(node)
      : compactDimensionsForCard(node.data, dimensionsOf(node));
    // 标题外置那行字所占的上方净空。只进布局输入，写回卡片尺寸时必须扣回去，
    // 否则每跑一次排布，卡就被这条带子撑高一次（比例也就跟着坏了）。
    const titleBandOf = (node: CanvasNode) =>
      (resolveCardPresentation(node.data.presentation).title === "above" ? TITLE_ABOVE_BAND : 0);
    const layoutInputKey = (graphNodes: CanvasNode[], graphEdges: CanvasEdge[]) => `${graphNodes.map((node) => {
      const size = layoutDimensionsOf(node);
      const fixed = movementLockedIds.has(node.id) ? `:${node.position.x},${node.position.y}` : "";
      return `${node.id}:${node.parentId || ""}:${size.width}x${size.height}:${movementLockedIds.has(node.id)}${fixed}`;
    }).join("|")}::${graphEdges.map((edge) => `${edge.id}:${edge.source}:${edge.target}`).join("|")}`;
    const structureKey = layoutInputKey(inputNodes, inputEdges);
    try {
      const layout = await layoutMaterialCanvas(
        inputNodes.map((node) => {
          const size = layoutDimensionsOf(node);
          // 标题外置的卡要为那行字预留上方净空，否则紧凑档下标题会压到上一排卡片上。
          // 只加在布局输入里，不改卡片自身尺寸（卡还是图片的原比例）。
          const titleBand = titleBandOf(node);
          return {
            id: node.id,
            parentId: node.parentId,
            kind: node.data.kind,
            x: node.position.x,
            y: node.position.y,
            width: size.width,
            height: size.height + titleBand,
            locked: movementLockedIds.has(node.id),
          };
        }),
        inputEdges.map((edge) => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          labels: [edge.data?.programLabel, edge.data?.userLabel].filter((label): label is string => Boolean(label)),
          sliceBoundSource: Boolean(edge.data?.sourceSlice),
          sliceBoundTarget: Boolean(edge.data?.targetSlice),
        })),
        baseDocument.current?.spacing,
      );
      const currentScopedNodes = scopedNodes(nodesRef.current);
      const currentScopedIds = new Set(currentScopedNodes.map((node) => node.id));
      const currentScopedEdges = edgesRef.current.filter((edge) => currentScopedIds.has(edge.source) && currentScopedIds.has(edge.target));
      const currentStructureKey = layoutInputKey(currentScopedNodes, currentScopedEdges);
      if (currentStructureKey !== structureKey) throw new Error("排布期间画布结构已变化，请重试");
      pushHistory(snapshotOf(inputNodes, inputEdges, flowRef.current?.getViewport()));
      const layoutTimestamp = new Date().toISOString();
      const next = parentsFirst(nodesRef.current.map((node) => {
        const placement = layout.placements.get(node.id);
        if (!placement || movementLockedIds.has(node.id)) return node;
        const keepGroupOrigin = scope.kind === "group" && node.id === scope.groupId;
        return {
          ...node,
          position: keepGroupOrigin ? node.position : { x: placement.x, y: placement.y },
          data: {
            ...node.data,
            positionSource: { kind: "auto-layout", actor: "omnidashboard/elk", updatedAt: layoutTimestamp },
          },
          // 扣回标题带：布局为它预留了净空，但它不属于卡片本身的高度。
          style: { ...node.style, width: placement.width, height: placement.height - titleBandOf(node) },
        };
      }));
      const byId = new Map(next.map((node) => [node.id, node]));
      const absolutePosition = (id: string) => {
        const node = byId.get(id);
        if (!node) return null;
        let x = node.position.x;
        let y = node.position.y;
        let parentId = node.parentId;
        const visited = new Set<string>();
        while (parentId && !visited.has(parentId)) {
          visited.add(parentId);
          const parent = byId.get(parentId);
          if (!parent) break;
          x += parent.position.x;
          y += parent.position.y;
          parentId = parent.parentId;
        }
        return { x, y };
      };
      const absoluteCenter = (id: string) => {
        const node = byId.get(id);
        if (!node) return null;
        const size = dimensionsOf(node);
        const position = absolutePosition(id);
        return position ? { x: position.x + size.width / 2, y: position.y + size.height / 2 } : null;
      };
      const nextEdges = edgesRef.current.map((edge) => {
        const source = absoluteCenter(edge.source);
        const target = absoluteCenter(edge.target);
        if (!source || !target) return edge;
        const curveData: RelationData = {
          arrow: edge.data?.arrow ?? "closed",
          ...edge.data,
          path: "curve",
          routePoints: [],
          curveControl: undefined,
        };
        // 切片钉住的端点保持不动。这里是真正把"从这个按钮出发"推平成"从卡片右边出发"
        // 的那一步（布局模块只是跟着报了同样的 handle）；两处都要让位给切片。
        const keepSource = edge.data?.sourceSlice
          ? canvasSliceHandleId(edge.data.sourceSlice)
          : null;
        const keepTarget = edge.data?.targetSlice
          ? canvasSliceHandleId(edge.data.targetSlice)
          : null;
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const sideBySide = Math.abs(dx) >= Math.abs(dy);
        return {
          ...edge,
          sourceHandle: keepSource
            ?? (sideBySide ? (dx >= 0 ? "right" : "left") : (dy >= 0 ? "bottom" : "top")),
          targetHandle: keepTarget
            ?? (sideBySide ? (dx >= 0 ? "left" : "right") : (dy >= 0 ? "top" : "bottom")),
          data: curveData,
        };
      });
      nodesRef.current = next;
      edgesRef.current = nextEdges;
      setNodes(next);
      setEdges(nextEdges);
      writeDocument(next, nextEdges);
      markProgrammaticMove();
      window.setTimeout(() => void flowRef.current?.fitView({ padding: 0.1, maxZoom: 1 }), 60);
      setLayoutState("idle");
    } catch (reason) {
      console.error("[material-canvas] 自动排布失败", reason);
      setLayoutState("error");
      window.setTimeout(() => setLayoutState("idle"), 2400);
    }
  }, [layoutState, markProgrammaticMove, pushHistory, writeDocument]);
  autoLayoutRef.current = runAutoLayout;

  // 复制链接: 当前 origin + 当前页路径 + session_id 参数(sessionIdentity 加载时按参数选画布),
  // 局域网另一块屏打开同一 dashboard 反代地址即可看到同一张画布
  const openHistoryCanvas = useCallback((item: CanvasHistoryItem) => {
    setHistoryOpen(false);
    if (item.document_id === storageId) return;
    void invokeCommand<CanvasResolveResult>("notes_canvas_open", {
      session_id: session.id,
      canvas_id: item.document_id,
    }).catch(() => undefined);
    setResolvedStorageId(item.document_id);
    if (item.title) {
      canvasTitleRef.current = item.title;
      setCanvasTitle(item.title);
    }
  }, [invokeCommand, session.id, storageId]);

  const copyCanvasLink = useCallback(() => {
    const raw = session.id.startsWith("external:") ? session.id.slice("external:".length) : session.id;
    const url = `${window.location.origin}${window.location.pathname}?session_id=${encodeURIComponent(raw)}`;
    void copyPlainText(url)
      .then(() => setLinkFeedback("已复制"))
      .catch(() => setLinkFeedback("复制失败"));
    if (linkFeedbackTimer.current !== null) window.clearTimeout(linkFeedbackTimer.current);
    linkFeedbackTimer.current = window.setTimeout(() => setLinkFeedback(""), 2000);
  }, [session.id]);

  // 画布改名: 失焦/回车提交, 随下次写盘持久化进 store 文档 title 字段
  const commitTitle = useCallback(() => {
    const next = canvasTitleRef.current.trim() || session.label;
    canvasTitleRef.current = next;
    setCanvasTitle(next);
    writeDocument(nodesRef.current, edgesRef.current);
  }, [session.label, writeDocument]);

  // M2: 组内阅读顺序手动覆盖(数字, 升序; 空=清除回退 continues 拓扑/坐标)
  const setCardOrder = useCallback((id: string) => {
    const node = nodesRef.current.find((item) => item.id === id);
    if (!node) return;
    const current = node.data.order;
    const input = window.prompt("组内阅读顺序（数字，升序；留空清除）", current === undefined ? "" : String(current));
    if (input === null) return;
    const trimmed = input.trim();
    const value = trimmed === "" ? undefined : Number(trimmed);
    if (value !== undefined && !Number.isFinite(value)) return;
    pushHistory();
    const next = nodesRef.current.map((item) => item.id === id ? { ...item, data: { ...item.data, order: value } } : item);
    nodesRef.current = next;
    setNodes(next);
    writeDocument(next, edgesRef.current);
  }, [pushHistory, writeDocument]);

  // 审阅视图点卡: 切回画布并聚焦(只聚焦不进全屏; React Flow 保活未卸载, 视口/选中态不丢)
  const focusCardOnCanvas = useCallback((id: string) => {    setView("canvas");
    window.setTimeout(() => {
      const node = nodesRef.current.find((item) => item.id === id);
      const flow = flowRef.current;
      if (!node || !flow) return;
      const size = dimensionsOf(node);
      const absolute = flow.getInternalNode(id)?.internals.positionAbsolute ?? node.position;
      markProgrammaticMove();
      setFocusedCardId(id);
      void flow.setCenter(absolute.x + size.width / 2, absolute.y + size.height / 2, {
        zoom: Math.max(flow.getViewport().zoom, 0.6),
        duration: 280,
      });
    }, 80);
  }, [markProgrammaticMove]);

  // 深链(M1b): /lofa/overlay/app/?session_id=<pty>&focus=<cardId> —— 加载完成后聚焦目标卡
  const initialFocusCard = useMemo(
    () => focusCardId || new URLSearchParams(window.location.search).get("focus"),
    [focusCardId],
  );
  useEffect(() => {
    if (booting || !initialFocusCard) return;
    if (!nodesRef.current.some((node) => node.id === initialFocusCard)) return;
    focusCardOnCanvas(initialFocusCard);
  }, [booting, initialFocusCard, focusCardOnCanvas]);

  // ---- M2 文档视图: 稳定 props(数组/函数身份变了会让 DocView 预取 effect 空转) ----
  const docCards = useMemo<DocCard[]>(() => nodes.map((node) => {
    const { draft: _draft, text: _text, ...restData } = node.data;
    const size = dimensionsOf(node);
    return {
      id: node.id,
      kind: node.data.kind,
      title: node.data.title,
      parentId: node.parentId,
      order: node.data.order,
      submission: node.data.submission,
      adapter: node.data.adapter,
      source: node.data.source,
      width: size.width,
      height: size.height,
      x: node.position.x,
      y: node.position.y,
      // 无损回填记录: DocView 块编辑(改名/插入/删除)走 mutate upsert 时用
      record: { ...restData, parentId: node.parentId, x: node.position.x, y: node.position.y, width: size.width, height: size.height },
    };
  }), [nodes]);
  const docRelations = useMemo<DocRelation[]>(() => edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: typeof edge.label === "string" ? edge.label : undefined,
    kind: edge.data?.kind,
  })), [edges]);
  const docFetchText = useCallback(async (editableId: string) => {
    const material = await invokeCommand<TextMaterialPayload>("notes_text_get", { id: editableId });
    return material.content ?? "";
  }, [invokeCommand]);
  const docPutText = useCallback(async (editableId: string, content: string) => {
    return invokeCommand<TextMaterialPayload>("notes_text_put", { id: editableId, content });
  }, [invokeCommand]);
  // 文档块编辑直写 mutate(不走 writeDocument 防抖): 提交后按服务端回包重置基线
  const docMutate = useCallback(async (patch: CanvasMutationPatch) => {
    const result = await invokeCommand<CanvasMutationResult>("notes_canvas_mutate", {
      id: storageId,
      session_id: session.id,
      mutation_id: safeRandomId("doc-"),
      actor: `browser:${session.id}`,
      workspace_ref: { kind: "note-session", ref: session.id },
      actor_ref: { kind: "browser", ref: session.id },
      base_revision: baseRevision.current ?? 0,
      patch,
    });
    const canonical = parseCanvasDocument(result.json, session.id);
    if (canonical) applyDocument(canonical);
    return result;
  }, [invokeCommand, session.id, storageId, applyDocument]);

  // ---- 审阅(M1b 定稿): 默认本会话队列; 「全部会话」整体列表走 /lofa/overlay/review-queue 聚合 ----
  const [reviewScope, setReviewScope] = useState<"session" | "global">(initialReviewGlobal ? "global" : "session");
  const [globalReviewItems, setGlobalReviewItems] = useState<ReviewItemData[]>([]);
  const sessionReviewItems: ReviewItemData[] = nodes
    .filter((node) => node.data.kind !== "group" && node.data.submission)
    .map((node) => ({
      key: `${storageId}|${node.id}`,
      cardId: node.id,
      canvasKey: storageId,
      sessionId: session.id.startsWith("external:") ? session.id.slice("external:".length) : session.id,
      sessionLabel: session.label,
      canvasTitle: canvasTitleRef.current || session.label,
      title: node.data.title,
      kind: node.data.kind,
      adapter: node.data.adapter,
      mime: node.data.source?.kind === "file" ? String(node.data.source.mime ?? "") : "",
      fileToken: node.data.kind === "material" && node.data.source?.kind === "file" ? node.data.source.token : null,
      url: node.data.source?.kind === "link" && typeof node.data.source.url === "string" ? node.data.source.url : null,
      editableId: node.data.source?.kind === "file" ? (node.data.source.editableId ?? null) : null,
      submission: node.data.submission!,
    }))
    .sort((a, b) => String(b.submission.submittedAt).localeCompare(String(a.submission.submittedAt)));

  const loadGlobalReview = useCallback(async () => {
    // 真源(M4 后): GET /bside/api/review/inbox(workshop 画布存储, 同源代理); 取不到则 invoke 桥逐画布兜底。
    // 旧 /lofa/overlay/review-queue 读 dashboard 侧画布存储, M4 提交换源后恒空, 已弃。
    let rows: Record<string, unknown>[] | null = null;
    try {
      const resp = await fetch("/bside/api/review/inbox");
      if (resp.ok) {
        const payload = await resp.json();
        if (Array.isArray(payload?.unread)) {
          rows = payload.unread.map((entry: Record<string, unknown>) => {
            const canvas = String(entry.canvas ?? "");
            let pty = "";
            if (canvas.startsWith("session-")) {
              try {
                const decoded = atob(canvas.slice("session-".length).replace(/-/g, "+").replace(/_/g, "/"));
                if (decoded.startsWith("external:")) pty = decoded.slice("external:".length);
              } catch { /* 非标准编码 */ }
            }
            return {
              canvas,
              card: entry.cardId,
              title: entry.title,
              canvasTitle: entry.canvasTitle,
              session_id: pty,
              kind: "",
              adapter: "",
              source: null,
              submission: {
                state: entry.state ?? "unreviewed",
                submittedAt: entry.submittedAt,
                submittedBy: entry.origin ?? "",
              },
            };
          });
        }
      }
    } catch { /* 落桥兜底 */ }
    const items: ReviewItemData[] = [];
    if (rows) {
      // 端点没随行的 source(老进程): 有媒体预览需求的项回桥补 token
      const tokenCache = new Map<string, Promise<Record<string, unknown> | null>>();
      const canvasDoc = (canvasKey: string) => {
        if (!tokenCache.has(canvasKey)) {
          tokenCache.set(canvasKey, invokeCommand<string | null>("notes_canvas_get", { id: canvasKey })
            .then((raw) => { try { return raw ? JSON.parse(raw) : null; } catch { return null; } })
            .catch(() => null));
        }
        return tokenCache.get(canvasKey)!;
      };
      for (const row of rows) {
        const submission = row.submission as CardSubmission | undefined;
        if (!submission) continue;
        const source = (row.source ?? null) as { kind?: string; token?: string; mime?: string; editableId?: string } | null;
        let token = source?.kind === "file" && typeof source.token === "string" ? source.token : null;
        const adapter = String(row.adapter ?? "");
        const canvasKey = String(row.canvas ?? "");
        const cardId = String(row.card ?? "");
        if (!token && (adapter === "image" || adapter === "audio" || adapter === "video" || adapter === "pdf")) {
          const doc = await canvasDoc(canvasKey);
          const card = ((doc?.cards ?? []) as Record<string, unknown>[]).find((entry) => entry.id === cardId);
          const cardSource = card?.source as { kind?: string; token?: string } | undefined;
          if (cardSource?.kind === "file" && typeof cardSource.token === "string") token = cardSource.token;
        }
        const pty = String(row.session_id ?? "");
        items.push({
          key: `${canvasKey}|${cardId}`,
          cardId,
          canvasKey,
          sessionId: pty,
          sessionLabel: pty.slice(0, 8),
          canvasTitle: String(row.canvasTitle ?? ""),
          title: String(row.title ?? ""),
          kind: String(row.kind ?? ""),
          adapter,
          mime: String(source?.mime ?? ""),
          fileToken: token,
          url: typeof source?.url === "string" ? source.url : null,
          editableId: typeof source?.editableId === "string" ? source.editableId : null,
          submission,
        });
      }
    } else {
      // 桥兜底(逐画布 get)
      const keys = await invokeCommand<string[]>("notes_canvas_keys", {});
      for (const key of keys || []) {
        const raw = await invokeCommand<string | null>("notes_canvas_get", { id: key });
        if (!raw) continue;
        let doc: Record<string, unknown>;
        try { doc = JSON.parse(raw); } catch { continue; }
        let pty = key;
        try {
          const decoded = atob(key.slice("session-".length).replace(/-/g, "+").replace(/_/g, "/"));
          if (decoded.startsWith("external:")) pty = decoded.slice("external:".length);
        } catch { /* 保留原 key */ }
        for (const card of (doc.cards as Record<string, unknown>[] | undefined) ?? []) {
          const submission = card.submission as CardSubmission | undefined;
          if (!submission || card.kind === "group") continue;
          const source = card.source as { kind?: string; token?: string; mime?: string; editableId?: string } | undefined;
          items.push({
            key: `${key}|${String(card.id)}`,
            cardId: String(card.id),
            canvasKey: key,
            sessionId: pty,
            sessionLabel: pty.slice(0, 8),
            canvasTitle: typeof doc.title === "string" ? doc.title : "",
            title: String(card.title ?? ""),
            kind: String(card.kind ?? ""),
            adapter: String(card.adapter ?? ""),
            mime: String(source?.mime ?? ""),
            fileToken: card.kind === "material" && source?.kind === "file" && typeof source.token === "string" ? source.token : null,
            url: typeof source?.url === "string" ? source.url : null,
            editableId: typeof source?.editableId === "string" ? source.editableId : null,
            submission,
          });
        }
      }
    }
    items.sort((a, b) => String(b.submission.submittedAt).localeCompare(String(a.submission.submittedAt)));
    setGlobalReviewItems(items);
  }, [invokeCommand]);

  useEffect(() => {
    if (view === "review" && reviewScope === "global") void loadGlobalReview();
  }, [view, reviewScope, loadGlobalReview]);

  const openReviewItem = useCallback((item: ReviewItemData) => {
    if (item.canvasKey === storageId) {
      focusCardOnCanvas(item.cardId);
    } else {
      window.open(resolveBrowserNavigationUrl(`/lofa/overlay/app/?session_id=${encodeURIComponent(item.sessionId)}&focus=${encodeURIComponent(item.cardId)}`), "_blank", "noopener");
    }
  }, [storageId, focusCardOnCanvas]);

  // 裁决: 本画布走 verdictCard; 外部画布写回【该卡所属源画布】(head 取 revision → 对该 key mutate)
  const verdictReviewItem = useCallback(async (item: ReviewItemData, pass: boolean, reason?: string) => {
    if (item.canvasKey === storageId) {
      verdictCard(item.cardId, pass, reason);
      return;
    }
    const head = await invokeCommand<CanvasHead | null>("notes_canvas_head", { id: item.canvasKey });
    const raw = await invokeCommand<string | null>("notes_canvas_get", { id: item.canvasKey });
    const doc = raw ? JSON.parse(raw) : null;
    const cards = (doc?.cards ?? []) as Record<string, unknown>[];
    const card = cards.find((entry) => entry.id === item.cardId);
    if (!doc || !card || typeof card.submission !== "object" || !card.submission) return;
    card.submission = {
      ...(card.submission as Record<string, unknown>),
      state: pass ? "fully-passed" : "not-fully-passed",
      verdictAt: new Date().toISOString(),
      verdictBy: `browser:${session.id}`,
      ...(reason ? { reason } : {}),
    };
    await invokeCommand("notes_canvas_mutate", {
      id: item.canvasKey,
      session_id: doc.sessionId ?? `external:${item.sessionId}`,
      mutation_id: safeRandomId("web-review-"),
      actor: `browser:${session.id}`,
      workspace_ref: { kind: "note-session", ref: doc.sessionId ?? `external:${item.sessionId}` },
      actor_ref: { kind: "browser", ref: session.id },
      base_revision: head?.revision ?? (typeof doc.revision === "number" ? doc.revision : 0),
      patch: { upsert_cards: [card] },
    });
    await loadGlobalReview();
  }, [storageId, verdictCard, invokeCommand, session.id, loadGlobalReview]);

  // 保存失败(409 真冲突等)的两个恢复动作:
  // 「加载外部版本」放弃本地脏数据直接吃服务端最新; 「保留本地重试」把基线换成服务端最新,
  // 让 writeDocument 的 diff 以新 base 重放成本地 mutation——实体仍冲突则保持 error 并展示冲突清单。
  const reloadExternalVersion = useCallback(async () => {
    try {
      const raw = await invokeCommand<string | null>("notes_canvas_get", { id: storageId });
      applyDocument(parseCanvasDocument(raw, session.id));
      setSaveErrorDetail("");
    } catch (reason) {
      setSaveState("error");
      setSaveErrorDetail(String(reason).slice(0, 300));
    }
  }, [invokeCommand, applyDocument, session.id, storageId]);

  const retryKeepLocal = useCallback(async () => {
    try {
      const raw = await invokeCommand<string | null>("notes_canvas_get", { id: storageId });
      const canonical = parseCanvasDocument(raw, session.id);
      if (!canonical) throw new Error("服务端返回了无效画布文档");
      baseDocument.current = canonical;
      baseRevision.current = canonical.revision;
      setCanvasRevision(canonical.revision);
      setSaveErrorDetail("");
      writeDocument(nodesRef.current, edgesRef.current); // diff(新基线, 本地) → mutate 重放
    } catch (reason) {
      setSaveState("error");
      setSaveErrorDetail(String(reason).slice(0, 300));
    }
  }, [invokeCommand, session.id, storageId, writeDocument]);

  // ---- 批量选择 / 编组 ----
  const [selection, setSelection] = useState<{ cards: number; groups: number }>({ cards: 0, groups: 0 });
  // xyflow puts onSelectionChange in a useEffect dependency. An inline handler that
  // always setStates will re-render, recreate the handler, and throw React #185
  // (maximum update depth) as soon as a card is hovered or a connection starts.
  const onSelectionChange = useCallback(({ nodes: picked }: { nodes: CanvasNode[] }) => {
    const cards = picked.filter((node) => node.data?.kind !== "group" && !node.parentId).length;
    const groups = picked.filter((node) => node.data?.kind === "group").length;
    setSelection((current) => (current.cards === cards && current.groups === groups ? current : { cards, groups }));
  }, []);

  // 组合: 选中 ≥2 张未编组卡 → 新建组卡(尺寸=包围盒+padding), 子卡 parentId=组、坐标转相对
  const groupSelection = useCallback(() => {
    // M2 组套组: 允许选中已存在的组卡作为成员(新建组没有祖先, 嵌套天然无环);
    // 只圈顶层卡(组内卡先挪出再编); 同一棵子树被重复圈选时只保留最祖先那张
    const selected = nodesRef.current.filter((node) => node.selected && !node.parentId);
    const selectedIds = new Set(selected.map((node) => node.id));
    const byId = new Map(nodesRef.current.map((node) => [node.id, node]));
    const hasSelectedAncestor = (node: CanvasNode): boolean => {
      let cursor = node.parentId;
      const visited = new Set<string>();
      while (cursor && !visited.has(cursor)) {
        visited.add(cursor);
        if (selectedIds.has(cursor)) return true;
        cursor = byId.get(cursor)?.parentId;
      }
      return false;
    };
    const picked = selected.filter((node) => !hasSelectedAncestor(node));
    if (picked.length < 2) return;
    pushHistory();
    const PAD = 28;
    const HEAD = 34;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const node of picked) {
      const size = dimensionsOf(node);
      minX = Math.min(minX, node.position.x);
      minY = Math.min(minY, node.position.y);
      maxX = Math.max(maxX, node.position.x + size.width);
      maxY = Math.max(maxY, node.position.y + size.height);
    }
    const gid = safeRandomId("group-");
    const gx = minX - PAD;
    const gy = minY - PAD - HEAD;
    const gw = maxX - minX + PAD * 2;
    const gh = maxY - minY + PAD * 2 + HEAD;
    const groupNode: CanvasNode = {
      id: gid,
      type: "groupCard",
      position: { x: gx, y: gy },
      style: { width: gw, height: gh },
      data: { id: gid, kind: "group", title: "编组", adapter: "auto", x: gx, y: gy, width: gw, height: gh },
      selected: true,
    };
    const pickedIds = new Set(picked.map((node) => node.id));
    const rest = nodesRef.current.map((node) => {
      if (!pickedIds.has(node.id)) return node;
      return {
        ...node,
        parentId: gid,
        extent: "parent" as const,
        selected: false,
        position: { x: node.position.x - gx, y: node.position.y - gy },
        data: { ...node.data, parentId: gid },
      };
    });
    setGraph(parentsFirst([groupNode, ...rest]), edgesRef.current);
  }, [pushHistory, setGraph]);

  // 取消组合: 只解最外层——子卡(含子组)parentId 上移一层(挂到被解组的父级, 坐标换算到其父坐标系), 删组卡
  const ungroup = useCallback((gid?: string) => {
    const targets = gid
      ? [gid]
      : nodesRef.current.filter((node) => node.selected && node.data.kind === "group").map((node) => node.id);
    if (!targets.length) return;
    pushHistory();
    const byId = new Map(nodesRef.current.map((node) => [node.id, node]));
    const next = nodesRef.current
      .filter((node) => !targets.includes(node.id))
      .map((node) => {
        if (!node.parentId || !targets.includes(node.parentId)) return node;
        const parent = byId.get(node.parentId);
        if (!parent) return node;
        const { parentId: _p, ...restData } = node.data;
        const grandParentId = parent.parentId; // 上移一层: 可能仍是组(嵌套)或 undefined(顶层)
        return {
          ...node,
          parentId: grandParentId,
          extent: grandParentId ? node.extent : undefined,
          // parent.position 相对其父, node.position 相对 parent → 相对祖父(或绝对) = 两者相加
          position: { x: parent.position.x + node.position.x, y: parent.position.y + node.position.y },
          data: grandParentId ? { ...restData, parentId: grandParentId } : restData,
        };
      });
    setGraph(next, edgesRef.current);
  }, [pushHistory, setGraph]);

  // Esc 清空选择(全屏/菜单开着时不抢, 它们的 Esc 有既有语义)
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || fullscreenId || menu) return;
      if (!nodesRef.current.some((node) => node.selected) && !edgesRef.current.some((edge) => edge.selected)) return;
      const nextNodes = nodesRef.current.map((node) => node.selected ? { ...node, selected: false } : node);
      const nextEdges = edgesRef.current.map((edge) => edge.selected ? { ...edge, selected: false } : edge);
      nodesRef.current = nextNodes;
      edgesRef.current = nextEdges;
      setNodes(nextNodes);
      setEdges(nextEdges);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreenId, menu]);

  // Figma 式画布键盘手势：+/- 缩放，Shift+1 显示全部，Shift+2 适配所选卡。
  // 输入框、正文编辑与全屏材料保持原生键盘语义，不被画布快捷键劫持。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (view !== "canvas" || fullscreenId) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest?.("input, textarea, select, [contenteditable='true']")) return;
      const flow = flowRef.current;
      if (!flow) return;
      if (event.shiftKey && event.code === "Digit1") {
        event.preventDefault();
        markProgrammaticMove();
        void flow.fitView({ padding: 0.1, maxZoom: 1, duration: 220 });
        return;
      }
      if (event.shiftKey && event.code === "Digit2") {
        const selectedIds = Array.from(
          stageRef.current?.querySelectorAll<HTMLElement>('.react-flow__node.selected') ?? [],
        ).map((node) => node.dataset.id).filter((id): id is string => Boolean(id));
        if (!selectedIds.length) return;
        const flowElement = stageRef.current?.querySelector<HTMLElement>('.react-flow');
        if (!flowElement) return;
        const bounds = flow.getNodesBounds(selectedIds);
        const viewport = getViewportForBounds(
          bounds,
          flowElement.clientWidth,
          flowElement.clientHeight,
          0.08,
          1.5,
          0.18,
        );
        event.preventDefault();
        markProgrammaticMove();
        void flow.setViewport(viewport, { duration: 220 });
        return;
      }
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        void flow.zoomIn({ duration: 120 });
        return;
      }
      if (event.key === "-" || event.key === "_") {
        event.preventDefault();
        void flow.zoomOut({ duration: 120 });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreenId, markProgrammaticMove, view]);

  const samePositionHiddenIds = useMemo(() => hiddenSamePositionNodeIds(nodes), [nodes]);
  const renderedNodes = useMemo(() => nodes.map((node) => ({
    ...node,
    hidden: samePositionHiddenIds.has(node.id),
  })), [nodes, samePositionHiddenIds]);
  const renderedEdges = useMemo(() => edges.map((edge) => ({
    ...edge,
    hidden: samePositionHiddenIds.has(edge.source) || samePositionHiddenIds.has(edge.target),
  })), [edges, samePositionHiddenIds]);
  const fullscreenNode = fullscreenId ? nodes.find((node) => node.id === fullscreenId) : undefined;
  const cardHoverState = useMemo<CardHoverState>(() => ({ id: hoveredCardId, hold: holdCardTools }), [holdCardTools, hoveredCardId]);
  void historyRevision;

  // 右键菜单条目(画布空白/卡片/关系三类); 菜单是后续功能的扩展位
  const menuNode = menu?.kind === "card" ? nodesRef.current.find((node) => node.id === menu.id) : undefined;
  const menuItems: ({ label: string; icon?: React.ReactNode; disabled?: boolean; onSelect: () => void } | "divider")[] = [];
  if (menu?.kind === "pane") {
    menuItems.push(
      { label: "新建文字卡", icon: <FilePlus2 size={14} />, onSelect: () => addTextAt(menu.x, menu.y) },
      { label: "加入材料（链接/文件）…", icon: <Link2 size={14} />, onSelect: () => { setDialog("material"); setSearchError(""); } },
      { label: "接入旧札记…", icon: <FileText size={14} />, onSelect: openLegacy },
      "divider",
      { label: selection.cards >= 2 ? `组合选中的 ${selection.cards} 张卡` : "组合选中卡片", icon: <Group size={14} />, disabled: selection.cards < 2, onSelect: groupSelection },
      "divider",
      { label: "自动排布容器与独立卡片", icon: <LayoutGrid size={14} />, disabled: layoutState === "running", onSelect: () => void runAutoLayout({ kind: "top-level" }) },
      "divider",
      { label: "撤销", icon: <Undo2 size={14} />, disabled: !past.current.length, onSelect: undo },
      { label: "重做", icon: <Redo2 size={14} />, disabled: !future.current.length, onSelect: redo },
    );
  } else if (menu?.kind === "card" && menuNode && menuNode.data.kind === "group") {
    menuItems.push(
      { label: "自动排布容器内部", icon: <LayoutGrid size={14} />, disabled: layoutState === "running", onSelect: () => void runAutoLayout({ kind: "group", groupId: menuNode.id }) },
      "divider",
      { label: "取消组合", icon: <Ungroup size={14} />, onSelect: () => ungroup(menuNode.id) },
      "divider",
      { label: "从画布移除(子卡留下)", icon: <Trash2 size={14} />, onSelect: () => cardActions.removeCard(menuNode.id) },
    );
  } else if (menu?.kind === "card" && menuNode) {
    const cardId = menuNode.id;
    if (menuNode.parentId) {
      menuItems.push(
        { label: "取消所在组合", icon: <Ungroup size={14} />, onSelect: () => ungroup(menuNode.parentId) },
        "divider",
      );
    } else if (selection.cards >= 2) {
      menuItems.push(
        { label: `组合选中的 ${selection.cards} 张卡`, icon: <Group size={14} />, onSelect: groupSelection },
        "divider",
      );
    }
    menuItems.push(
      { label: "复制卡片引用", icon: <Copy size={14} />, onSelect: () => void cardActions.copyCardRef(cardId) },
      { label: `设置阅读顺序${menuNode.data.order !== undefined ? `（当前 ${menuNode.data.order}）` : ""}…`, icon: <Pencil size={14} />, onSelect: () => setCardOrder(cardId) },
      { label: "全屏查看", icon: <Maximize2 size={14} />, onSelect: () => cardActions.toggleFullscreen(cardId) },
    );
    if (menuNode.data.adapter === "image") {
      menuItems.push({
        label: sliceEditingId === cardId ? "退出图片圈选" : "在图片上圈选并创建注释",
        icon: <Crop size={14} />,
        onSelect: () => sliceEditingId === cardId ? cardActions.cancelRegionSlice() : cardActions.beginRegionSlice(cardId),
      });
    }
    if (menuNode.data.frozen) {
      menuItems.push({ label: "解冻出新卡", icon: <Sun size={14} />, onSelect: () => cardActions.unfreezeCard(cardId) });
    } else if (menuNode.data.kind === "material" && menuNode.data.source?.kind === "link" && menuNode.data.adapter === "web") {
      menuItems.push({ label: "冻结成快照", icon: <Snowflake size={14} />, disabled: freezingId === cardId, onSelect: () => void cardActions.freezeCard(cardId).catch(() => {}) });
    }
    if (reviewEnabled && !menuNode.data.submission) {
      menuItems.push({ label: "提交审阅", icon: <Send size={14} />, onSelect: () => cardActions.submitForReview(cardId) });
    }
    menuItems.push(
      "divider",
      { label: "从画布移除", icon: <Trash2 size={14} />, onSelect: () => cardActions.removeCard(cardId) },
    );
  } else if (menu?.kind === "edge" && menu.id) {
    const edgeId = menu.id;
    const relation = edgesRef.current.find((edge) => edge.id === edgeId);
    const path = relation?.data?.path || "curve";
    const arrow = relation?.data?.arrow || "closed";
    const kind = relation?.data?.kind;
    menuItems.push(
      { label: "编辑关系文字", onSelect: () => relationActions.select(edgeId) },
      "divider",
      { label: `${kind === "continues" ? "✓ " : ""}分类：主线串联(continues)`, onSelect: () => relationActions.update(edgeId, { kind: "continues" }) },
      { label: `${kind === "references" ? "✓ " : ""}分类：旁证(references)`, onSelect: () => relationActions.update(edgeId, { kind: "references" }) },
      { label: `${kind === "derived" ? "✓ " : ""}分类：派生(derived)`, onSelect: () => relationActions.update(edgeId, { kind: "derived" }) },
      { label: `${!kind ? "✓ " : ""}分类：未定`, onSelect: () => relationActions.update(edgeId, { kind: null }) },
      "divider",
      { label: `${path === "curve" ? "✓ " : ""}连线：曲线`, onSelect: () => relationActions.update(edgeId, { path: "curve" }) },
      { label: `${path === "straight" ? "✓ " : ""}连线：直线`, onSelect: () => relationActions.update(edgeId, { path: "straight" }) },
      { label: `${path === "orthogonal" ? "✓ " : ""}连线：折线`, onSelect: () => relationActions.update(edgeId, { path: "orthogonal" }) },
      "divider",
      { label: `${arrow === "closed" ? "✓ " : ""}箭头：实心`, onSelect: () => relationActions.update(edgeId, { arrow: "closed" }) },
      { label: `${arrow === "open" ? "✓ " : ""}箭头：线形`, onSelect: () => relationActions.update(edgeId, { arrow: "open" }) },
      { label: `${arrow === "none" ? "✓ " : ""}箭头：无`, onSelect: () => relationActions.update(edgeId, { arrow: "none" }) },
      "divider",
      { label: "移除这条关系", icon: <Trash2 size={14} />, onSelect: () => removeRelation(edgeId) },
    );
  }

  // 菜单全局关闭: 点别处/滚轮/Esc
  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setMenu(null); };
    const onDown = () => {
      // 长按抬指合成的 mousedown 在打开后 500ms 内到达: 忽略, 别让菜单自己关掉自己。
      if (performance.now() - menuOpenedAtRef.current < 500) return;
      setMenu(null);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("wheel", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("wheel", onDown, true);
    };
  }, [menu]);

  useEffect(() => {
    if (!canvasMenuOpen) return;
    const close = () => setCanvasMenuOpen(false);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [canvasMenuOpen]);

  if (booting) {
    return <div className="material-canvas-boot" data-material-canvas="loading"><Loader2 className="spin" />正在打开札记…</div>;
  }
  if (loadError) {
    return (
      <div className="material-canvas-boot error" data-material-canvas="error">
        <strong>札记存储暂时不可用</strong><span>{loadError}</span><button type="button" onClick={() => window.location.reload()}><RotateCcw size={15} />重试</button>
      </div>
    );
  }

  return (
    <InvokeContext.Provider value={invokeCommand}>
      <CardActionsContext.Provider value={cardActions}>
        <LiveWindowContext.Provider value={openLiveWindow}>
        <RelationActionsContext.Provider value={relationActions}>
        <HoveredCardContext.Provider value={cardHoverState}>
        <SpotlightContext.Provider value={highlightId}>
        <main className="material-notes-workspace" data-material-canvas="ready" data-notes-review={reviewEnabled ? "on" : "off"}>
        <header className="material-canvas-toolbar" onMouseDown={(event) => event.stopPropagation()}>
          <button
            className="material-canvas-menu-trigger"
            type="button"
            aria-haspopup="menu"
            aria-expanded={canvasMenuOpen}
            onClick={() => setCanvasMenuOpen((current) => !current)}
          >
            <MenuIcon size={15} />
            <strong title={canvasTitle}>{canvasTitle}</strong>
            <ChevronDown size={14} />
          </button>
          <button
            className="material-canvas-history-btn"
            type="button"
            title="历史画布"
            aria-label="历史画布"
            data-testid="canvas-history-open"
            onClick={() => { setCanvasMenuOpen(false); setHistoryOpen(true); }}
          >
            <History size={15} />
            <span>历史</span>
          </button>
          <div className="material-view-switch" role="tablist" aria-label="视图切换">
            <button type="button" role="tab" aria-selected={view === "canvas"} className={view === "canvas" ? "active" : ""} onClick={() => setView("canvas")} title="画布">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <circle cx="5" cy="6" r="2.4" /><circle cx="19" cy="6" r="2.4" /><circle cx="12" cy="18" r="2.4" />
                <path d="M7 7.5 10 16M17 7.5 14 16M7.4 6h9.2" />
              </svg>
            </button>
            {reviewEnabled && (
            <button type="button" role="tab" aria-selected={view === "review"} className={view === "review" ? "active" : ""} onClick={() => setView("review")}
              title={nodes.filter((n) => n.data.submission?.state === "unreviewed").length > 0
                ? `审阅（未看 ${nodes.filter((n) => n.data.submission?.state === "unreviewed").length}）` : "审阅"}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="9" /><path d="m8.5 12.2 2.4 2.4 4.6-5" />
              </svg>
            </button>
            )}
            <button type="button" role="tab" aria-selected={view === "doc"} className={view === "doc" ? "active" : ""} onClick={() => setView("doc")} title="文档">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M5 5h14M5 10h14M5 15h9M5 20h12" />
              </svg>
            </button>
            {view === "doc" && (
              <button type="button" className="material-outline-toggle" onClick={() => outlineToggleRef.current?.()} title="大纲（显示/隐藏）">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M9 5H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h4" /><path d="M9 3v18" /><path d="M13 8h6M13 12h6M13 16h4" />
                </svg>
              </button>
            )}
          </div>
          {canvasMenuOpen && (
            <div className="material-canvas-menu" role="menu">
              <label className="material-canvas-title-field">
                <span>画布名</span>
                <input
                  className="material-canvas-name"
                  value={canvasTitle}
                  onChange={(event) => {
                    canvasTitleRef.current = event.target.value;
                    setCanvasTitle(event.target.value);
                  }}
                  onBlur={commitTitle}
                  onKeyDown={(event) => {
                    event.stopPropagation();
                    if (event.key === "Enter") (event.target as HTMLInputElement).blur();
                  }}
                  aria-label="画布名（可改，自动保存）"
                />
              </label>
              <button
                type="button"
                onClick={() => {
                  setCanvasMenuOpen(false);
                  markProgrammaticMove();
                  const flow = flowRef.current;
                  if (!flow) return;
                  void flow.fitView({ padding: 0.1, maxZoom: 1, duration: 220 }).then(() => {
                    setCanvasZoom(flow.getViewport().zoom);
                  });
                }}
              >
                <Minimize2 size={15} /><span>显示全部</span>
              </button>
              <div className="material-context-divider" />
              <button
                type="button"
                disabled={layoutState === "running"}
                onClick={() => {
                  setCanvasMenuOpen(false);
                  void runAutoLayout({ kind: "all" });
                }}
              >
                {layoutState === "running" ? <Loader2 className="spin" size={15} /> : <LayoutGrid size={15} />}
                <span>{layoutState === "error" ? "排布失败，请重试" : layoutState === "running" ? "正在排布…" : "全部智能排布"}</span>
              </button>
              <button type="button" onClick={copyCanvasLink}>
                <Link2 size={15} /><span>{linkFeedback || "复制画布链接"}</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setCanvasMenuOpen(false);
                  setHistoryOpen(true);
                }}
              >
                <History size={15} /><span>历史画布</span>
              </button>
            </div>
          )}
          {saveState === "error" && (
            <div className="material-save-recovery" role="alert">
              <span className="material-save-recovery-text" title={saveErrorDetail || undefined}>保存失败</span>
              <button type="button" onClick={() => void reloadExternalVersion()} title="放弃本地未保存改动，载入服务端最新版本">
                <RotateCcw size={14} /><span>加载外部版本</span>
              </button>
              <button type="button" onClick={() => void retryKeepLocal()} title={saveErrorDetail ? `冲突详情: ${saveErrorDetail}` : "以服务端最新为基线重放本地改动；仍冲突会列出冲突卡"}>
                <Redo2 size={14} /><span>保留本地重试</span>
              </button>
            </div>
          )}
        </header>
        <section
          className="material-canvas-stage"
          ref={stageRef}
          hidden={view !== "canvas"}
          onPointerDownCapture={onStagePointerDown}
          onPointerMoveCapture={onStagePointerMove}
          onPointerUpCapture={clearPaneHold}
          onPointerCancelCapture={clearPaneHold}
        >
          <ReactFlow<CanvasNode, CanvasEdge>
            nodes={renderedNodes}
            edges={renderedEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onInit={(instance) => {
              flowRef.current = instance;
              // 初始 fitView 落定后记录稳定视口(首段用户手势的撤销 pre)
              window.setTimeout(() => {
                stableViewport.current = instance.getViewport();
                setCanvasZoom(instance.getViewport().zoom);
              }, 350);
            }}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onReconnect={onReconnect}
            onNodeMouseEnter={(_event, node) => holdCardTools(node.id, true)}
            onNodeMouseLeave={(_event, node) => holdCardTools(node.id, false)}
            onNodeClick={(_event, node) => {
              setFocusedCardId((current) => current && current !== node.id ? null : current);
              if (editingTextIdRef.current && editingTextIdRef.current !== node.id) {
                cardActions.stopTextEditing();
              }
              const nextNodes = nodesRef.current.map((item) => ({ ...item, selected: item.id === node.id }));
              const nextEdges = edgesRef.current.map((edge) => edge.selected ? { ...edge, selected: false } : edge);
              nodesRef.current = nextNodes;
              edgesRef.current = nextEdges;
              setNodes(nextNodes);
              setEdges(nextEdges);
            }}
            onEdgeClick={(_event, edge) => {
              setFocusedCardId(null);
              relationActions.select(edge.id);
            }}
            onNodeDragStart={() => {
              setFocusedCardId(null);
              dragSnapshot.current = snapshotOf(nodesRef.current, edgesRef.current);
            }}
            onNodeDragStop={(_event, dragged) => {
              if (dragSnapshot.current) pushHistory(dragSnapshot.current);
              dragSnapshot.current = null;
              const next = nodesRef.current.map((node) => node.id === dragged.id ? {
                ...node,
                data: {
                  ...node.data,
                  positionSource: { kind: "user", actor: `browser:${session.id}`, updatedAt: new Date().toISOString() },
                },
              } : node);
              nodesRef.current = next;
              setNodes(next);
              writeDocument(next, edgesRef.current);
            }}
            onPaneContextMenu={(event) => {
              event.preventDefault();
              setCanvasMenuOpen(false);
              setMenu({ x: event.clientX, y: event.clientY, kind: "pane" });
            }}
            onPaneClick={() => {
              setCanvasMenuOpen(false);
              setFocusedCardId(null);
              setEditingRelationId(null);
            }}
            onMoveEnd={(_event, vp) => {
              // 手势结束: pre 视口=上一段手势结束时的稳定值(onMoveStart 在 wheel 平移下拿到的是移动后的值, 不能用)
              const current = vp ?? flowRef.current?.getViewport() ?? null;
              if (current) setCanvasZoom(current.zoom);
              const pre = stableViewport.current;
              stableViewport.current = current;
              moveSnapshot.current = null;
              if (skipMoveHistory.current || !pre || !current) return;
              if (pre.x === current.x && pre.y === current.y && pre.zoom === current.zoom) return;
              past.current.push(snapshotOf(nodesRef.current, edgesRef.current, pre));
              if (past.current.length > 100) past.current.shift();
              future.current = [];
              refreshHistory();
            }}
            onNodeContextMenu={(event, node) => {
              event.preventDefault();
              setCanvasMenuOpen(false);
              setMenu({ x: event.clientX, y: event.clientY, kind: "card", id: node.id });
            }}
            onEdgeContextMenu={(event, edge) => {
              event.preventDefault();
              setCanvasMenuOpen(false);
              setMenu({ x: event.clientX, y: event.clientY, kind: "edge", id: edge.id });
            }}
            fitView
            fitViewOptions={FIT_VIEW_OPTIONS}
            minZoom={0.08}
            maxZoom={2.2}
            zoomOnDoubleClick={false}
            zoomOnPinch
            panOnScroll
            selectionOnDrag
            panOnDrag={[1, 2]}
            panActivationKeyCode="Space"
            multiSelectionKeyCode={["Shift", "Control", "Meta"]}
            onSelectionChange={onSelectionChange}
            deleteKeyCode={null}
            nodesConnectable
            connectionMode={ConnectionMode.Loose}
            nodesDraggable
            edgesFocusable
          >
            <Background variant={BackgroundVariant.Dots} gap={24} size={1.2} />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor={(node) => {
                const kind = (node as CanvasNode).data?.kind;
                // 蓝图 token 字面档(JS 侧无法引 var): --fp-text-3 / --fp-link / --fp-ok
                return kind === "group" ? "#6e8cb8" : kind === "text" ? "#6fa8ff" : "#7fd4a8";
              }}
            />
          </ReactFlow>
        </section>

        {reviewEnabled && view === "review" && (
          <section className="material-review-view">
            <ReviewPanel
              scope={reviewScope}
              onScopeChange={setReviewScope}
              items={reviewScope === "session" ? sessionReviewItems : globalReviewItems}
              currentCanvasKey={storageId}
              onOpenCard={openReviewItem}
              onVerdict={(item, pass, reason) => void verdictReviewItem(item, pass, reason)}
              fetchText={docFetchText}
            />
          </section>
        )}
        {view === "doc" && (
          <section className="material-review-view">
            <MaterialDocView
              cards={docCards}
              relations={docRelations}
              fetchText={docFetchText}
              putText={docPutText}
              mutate={docMutate}
              imageUrl={streamUrl}
              registerOutlineToggle={(fn) => { outlineToggleRef.current = fn; }}
            />
          </section>
        )}

        {menu && menuItems.length > 0 && (
          <div
            className="material-context-menu"
            data-testid="material-context-menu"
            style={{ left: menu.x, top: menu.y }}
            onMouseDown={(event) => event.stopPropagation()}
            onContextMenu={(event) => event.preventDefault()}
          >
            {menuItems.map((item, index) => item === "divider"
              ? <div key={`d${index}`} className="material-context-divider" />
              : (
                <button
                  key={item.label}
                  type="button"
                  disabled={item.disabled}
                  onClick={() => { setMenu(null); item.onSelect(); }}
                >
                  {item.icon}
                  <span>{item.label}</span>
                </button>
              ))}
          </div>
        )}

        {fullscreenNode && (          <div className="material-fullscreen-layer" data-testid="material-fullscreen">
            <header className="material-fullscreen-bar">
              {fullscreenNode.data.kind === "text" ? <FileText size={15} /> : <Link2 size={15} />}
              <strong title={fullscreenNode.data.title}>{fullscreenNode.data.title}</strong>
              <small>
                {fullscreenNode.data.kind === "text"
                  ? "文字"
                  : fullscreenNode.data.source?.kind === "legacy-note" ? "旧札记 · 只读" : "材料"}
              </small>
              <button type="button" title="退出全屏（Esc）" onClick={exitFullscreen}>
                <Minimize2 size={15} />退出全屏
              </button>
            </header>
            {reviewEnabled && fullscreenNode.data.submission && (
              <SubmissionVerdictBar
                submission={fullscreenNode.data.submission}
                onVerdict={(pass, reason) => verdictCard(fullscreenNode.id, pass, reason)}
              />
            )}
            <div className="material-fullscreen-body">
              {fullscreenNode.data.kind === "text"
                ? <EditableText id={fullscreenNode.id} data={fullscreenNode.data} actions={cardActions} />
                : <MaterialBody data={fullscreenNode.data} hd />}
            </div>
          </div>
        )}

        {liveWindow && <MovableLiveWindow
          value={liveWindow}
          onChange={setLiveWindow}
          onClose={() => setLiveWindow(null)}
        />}

        {dialog === "material" && (
          <CanvasDialog title="加入材料" onClose={() => setDialog(null)}>
            <p className="material-dialog-hint">粘贴链接会直接建立引用；输入文件名或路径片段会搜索本机材料。正文仍留在原文件或链接中。</p>
            <form className="material-search-form" onSubmit={(event) => { event.preventDefault(); submitMaterial(); }}>
              <input autoFocus value={materialInput} onChange={(event) => setMaterialInput(event.target.value)} placeholder="https://… 或 README.md / 文件路径" />
              <button type="submit" disabled={searching || !materialInput.trim()}>{searching ? <Loader2 className="spin" size={16} /> : <Search size={16} />}加入 / 搜索</button>
            </form>
            {searchError && <div className="material-dialog-error">{searchError}</div>}
            <div className="material-picker-list">
              {hits.map((hit) => (
                <button key={`${hit.path}:${hit.open_token}`} type="button" onClick={() => addFile(hit)}>
                  <FileText size={16} /><span><strong>{hit.name}</strong><small>{hit.path}</small></span>
                </button>
              ))}
            </div>
          </CanvasDialog>
        )}

        {historyOpen && (
          <CanvasHistoryPanel
            currentId={storageId}
            sessionId={session.id}
            invokeCommand={invokeCommand}
            onOpen={openHistoryCanvas}
            onClose={() => setHistoryOpen(false)}
          />
        )}

        {dialog === "legacy" && (
          <CanvasDialog title="接入旧札记" onClose={() => setDialog(null)}>
            <p className="material-dialog-hint">这里只建立只读引用，不复制旧正文；旧 BlockSuite 数据不会参与新画布启动。</p>
            {legacyLoading && <div className="material-preview-message"><Loader2 className="spin" size={16} />读取旧札记目录…</div>}
            <div className="material-picker-list legacy-list">
              {legacyNotes.map((note) => (
                <button key={note.id} type="button" disabled={!note.hasMarkdown} onClick={() => addLegacy(note)}>
                  <FileText size={16} /><span><strong>{note.title}</strong><small>{note.hasMarkdown ? note.id : "尚无可展示的 Markdown"}</small></span>
                </button>
              ))}
            </div>
          </CanvasDialog>
        )}

        {groupBodyEdit && (
          <CanvasDialog title="组描述（正文存 materials 分居链）" onClose={() => setGroupBodyEdit(null)}>
            <textarea
              className="material-group-body-editor"
              value={groupBodyEdit.text}
              disabled={groupBodyEdit.loading}
              placeholder={groupBodyEdit.loading ? "读取中…" : "写在组标题下的引言段…"}
              onChange={(event) => setGroupBodyEdit({ ...groupBodyEdit, text: event.currentTarget.value })}
              onKeyDown={(event) => event.stopPropagation()}
            />
            <div className="material-dialog-actions">
              <button type="button" disabled={groupBodyEdit.loading} onClick={() => void saveGroupBody()}>保存</button>
              <button type="button" onClick={() => setGroupBodyEdit(null)}>取消</button>
            </div>
          </CanvasDialog>
        )}
        </main>
        </SpotlightContext.Provider>
        </HoveredCardContext.Provider>
        </RelationActionsContext.Provider>
        </LiveWindowContext.Provider>
      </CardActionsContext.Provider>
    </InvokeContext.Provider>
  );
}

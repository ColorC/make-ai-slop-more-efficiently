export type MaterialSource =
  | { kind: "file"; path: string; token: string; mime?: string; editableId?: string; tiers?: ImageTier[] }
  | { kind: "link"; url: string }
  | { kind: "legacy-note"; id: string };

/** 图片清晰度分档: zoom ≥ minZoom 时换用该档(path+token 各自签名)。无 tiers=单源(旧卡回退)。 */
export interface ImageTier {
  minZoom: number;
  path: string;
  token: string;
}

/** comment = 评论（便利签）：评一张或多张卡、可接续；正文在 text 里，不走 materials 分居链。 */
export type CardKind = "text" | "material" | "group" | "comment";

/** 提审状态机(M1a): 缺省无 submission = 未提审; unreviewed→seen→fully-passed|not-fully-passed。 */
export type SubmissionState = "unreviewed" | "seen" | "fully-passed" | "not-fully-passed";

/** 总控主动推送标记(原 reviewstage pushed_to_user/pushed_reason/pushed_at)。 */
export interface SubmissionPushMark {
  toUser: boolean;
  reason?: string;
  at?: string;
}

export interface CardSubmission {
  state: SubmissionState;
  submittedAt: string;
  submittedBy: string;
  seenAt?: string;
  verdictAt?: string;
  verdictBy?: string;
  /** 未完全通过时的理由: 受控词表值(四类), 可带 ": 自由补充" 后缀; 词表外不允许新增分类。 */
  reason?: string;

  // ── 审阅库单一真源字段(2026-08-20 两套审阅体系融合) ──
  // 背景: 原 boss_sight/reviewstage 材料库与本 submission 长期并存, 两个入口数字对不上。
  // 用户裁定「彻底融合、不留两套痕迹、丰富度不丢」, 故旧库的全部一等字段收拢到这里,
  // submission 成为审阅的唯一真源。新字段一律 optional, 对 workshop 等既有域向后兼容。

  /** 软归档: 不删卡, 审阅台默认视图不列。归档是标记 —— 不能再用「缺 submission」表达,
   *  否则读侧无法区分「已归档」和「从未提审」(2026-08-15 那次迁移就是这样让 899 条消失的)。 */
  archived?: boolean;
  updatedAt?: string;
  /** 材料类型(markdown/demo/html/image/video/static-report/...), 与 card.kind 不同层。 */
  materialKind?: string;
  tier?: string;
  project?: string;
  track?: string;
  version?: number;
  versionFamily?: string;
  subjectId?: string;
  subjectType?: string;
  revision?: number;
  sourcePlanId?: string;
  sourceSubagentId?: string;
  annotationsAllowed?: boolean;
  /** {parent?, supersedes?: [], related?: []}, 值 = 卡 id。 */
  links?: Record<string, unknown>;
  extra?: Record<string, unknown>;
  reviewContext?: Record<string, unknown>;
  pushed?: SubmissionPushMark;
  annotations?: unknown[];
  comments?: unknown[];
  history?: unknown[];
  /** 迁移溯源(旧 material 原记录关键字段: matId/status/原 project/时间/本体相对路径)。 */
  legacy?: Record<string, unknown>;
}

/** 「未完全通过」受控理由词表(前端与 CLI 共用同一套值; M1b 全局队列按此分桶)。 */
export const SUBMISSION_FAIL_REASONS = ["模态/形态错误", "内容错误/需迭代", "方向错误", "不需要审阅"] as const;
/**
 * How a card's body is read.
 *
 * The union is the union of what the surfaces actually write. LiWork and the
 * React canvas each grew their own half of this list while both claimed to read
 * the same documents on disk, so a board authored in one could carry an adapter
 * the other could not name. Adding here is safe; removing is not.
 */
export type MaterialAdapter =
  | "auto"
  | "text"
  | "markdown"
  | "image"
  | "pdf"
  | "audio"
  | "video"
  | "html"
  | "web"
  | "code"
  | "link"
  | "file"
  | "legacy-note"
  | "unsupported";

export interface CanvasPositionLock {
  locked: boolean;
  actor?: string;
  reason?: string;
  updatedAt?: string;
}

export interface CanvasPositionSource {
  /** seed=生产方初始坐标；auto-layout=布局器生成；user=人工拖拽/迁移保护。 */
  kind: "seed" | "auto-layout" | "user";
  actor?: string;
  updatedAt?: string;
}

export interface CanvasPoint {
  x: number;
  y: number;
}

export interface CanvasLabelPosition {
  /** 0..1 path progress; offsets use canvas coordinates and scale with the viewport. */
  t: number;
  offsetX?: number;
  offsetY?: number;
}

export interface CanvasDisplayOverride {
  pluginId: string;
}

export type CanvasRenderMode = "auto" | "overview" | "detail";
export type CanvasVirtualizationMode = "auto" | "always" | "never";

export interface CanvasPerformanceBehavior {
  /** Viewport culling has a small fixed cost, so auto only enables it for large graphs. */
  virtualization?: CanvasVirtualizationMode;
  virtualizationThreshold?: number;
  /** Initial contextual-zoom mode. Users can still switch the current view locally. */
  defaultRenderMode?: CanvasRenderMode;
  /** Below this zoom, rich card bodies and multi-path edge decoration are suppressed. */
  overviewMaxZoom?: number;
  /** A card whose shorter screen edge is below this value uses the compact preview. */
  detailMinPixels?: number;
}

export interface CanvasWorkbenchLinkDisplay {
  placement?: "toolbar" | "menu" | "both";
  target?: "same" | "new";
  icon?: "map" | "material" | "external";
  tone?: "default" | "primary";
}

/** A host-neutral route to a specialized workbench for this Canvas or Material. */
export interface CanvasWorkbenchLink {
  id: string;
  label: string;
  href: string;
  query?: Record<string, string | number | boolean>;
  display?: CanvasWorkbenchLinkDisplay;
}

/** A checker id is resolved by the host registry; options remain checker-owned. */
export interface CanvasConstraintSpec {
  checker: string;
  severity?: "error" | "warning";
  options?: Record<string, unknown>;
}

export interface CanvasFoldingBehavior {
  enabled?: boolean;
  /** Empty means every semantic relation can contribute descendants. */
  relationKinds?: string[];
}

export interface CanvasBehavior {
  performance?: CanvasPerformanceBehavior;
  workbenchLinks?: CanvasWorkbenchLink[];
  checks?: CanvasConstraintSpec[];
  folding?: CanvasFoldingBehavior;
}

export interface CanvasCardFold {
  /** Producer-authored initial state. */
  defaultCollapsed?: boolean;
  /** User override; when absent the default is used. */
  collapsed?: boolean;
  /** Explicit fold members for non-group cards; groups default to their parentId descendants. */
  memberIds?: string[];
}

/** A group-level switch whose members occupy the same semantic slots. */
export interface CanvasSamePositionVariant {
  key: string;
  label: string;
  /** 以下为检索 / 徽标元数据（由驱动器写入，前端只读；缺省不显示）。 */
  status?: string;
  business?: string;
  owner?: string;
  created_at?: string;
  keywords?: string[];
}

export interface CanvasSamePositionContainer {
  dimension: string;
  variants: CanvasSamePositionVariant[];
  activeVariant: string;
}

/** A child card belongs to one variant and one stable slot in its container. */
export interface CanvasSamePositionMember {
  slotId: string;
  variantKey: string;
}

/**
 * 卡片外观。业主 2026-08-20：「还得加更多的卡片类型，有的卡片类型可以默认不显示标题
 * 或者放大到一定地步才显示标题甚至不带框，反正更多的样式用于界面标注和表示跳转」。
 *
 * 参照的是collab platform策划案里那张交互画板（武道大会）的视觉语言，实测特征：
 *   - 界面卡**没有任何框线、没有标题栏**，画布上就是那张图本身；
 *   - 标题是卡**外面上方**的一行粗体纯文字，不是卡内的头部；
 *   - 标注是贴在标题上下的小号要点，其中"考虑归倒单独作为玩法展开"那条是**黄底高亮**；
 *   - 中间还夹着一张**完全没有标题**、尺寸也不同的横幅图。
 *
 * 所以外观必须是**每张卡自己的属性**，不是画布级主题：同一张画布上要能同时有
 * 裸图卡、带外置标题的界面卡、无框高亮标注卡。
 */
export type CanvasCardFrame = "panel" | "none";
export type CanvasTitlePlacement = "inside" | "above" | "none";
export type CanvasCardEmphasis = "normal" | "highlight" | "muted";

export const CANVAS_CARD_FRAMES: readonly CanvasCardFrame[] = Object.freeze(["panel", "none"]);
export const CANVAS_TITLE_PLACEMENTS: readonly CanvasTitlePlacement[] = Object.freeze(["inside", "above", "none"]);
export const CANVAS_CARD_EMPHASES: readonly CanvasCardEmphasis[] = Object.freeze(["normal", "highlight", "muted"]);

export interface CanvasCardPresentation {
  /**
   * 收纳态：hidden = 从画布移出（顶栏「已隐藏」列表可找回，文档里卡与关系都在）；
   * iconified = 缩成图标只显标题（悬浮看完整标题）；缺省 = 正常显示。折叠另见 fold。
   */
  state?: 'hidden' | 'iconified';
  /** 框线与底板。none = 画布上只有内容本身（界面图、横幅图）。 */
  frame?: CanvasCardFrame;
  /** 标题位置。above = 画在卡外上方（collab platform那张交互图的做法）。 */
  title?: CanvasTitlePlacement;
  /**
   * 标题的显示缩放门槛：视口缩放 < 该值时隐藏标题。
   * 用途是"缩小看阵列、放大才看名字"，让密排的界面阵列不被标题淹没。
   * 只对 above/inside 有意义；缺省 = 任何缩放都显示。
   */
  titleMinZoom?: number;
  /** 强调级别。highlight 对应策划案里那条黄底备注。 */
  emphasis?: CanvasCardEmphasis;
  /**
   * 外观预设名。**core 不枚举它的取值** —— 与 0.3.0 的能力声明反转一致：
   * 由渲染端声明自己支持哪些预设，core 只校验它是个非空短字符串。
   * 预设只是上面几个字段的一组默认值，显式字段永远盖过预设。
   */
  style?: string;
}

/** 语义默认：界面图与标注天生不该带框，别让每个生产方各写一遍。 */
const PRESENTATION_BY_STYLE: Readonly<Record<string, CanvasCardPresentation>> = Object.freeze({
  // 界面阵列里的一张屏：无框，标题在外上方，缩小到看不清时不显示标题。
  screen: Object.freeze({ frame: "none", title: "above", titleMinZoom: 0.5 }),
  // 贴在某张屏上方的机制标注：无框、无标题，正文就是内容。
  annotation: Object.freeze({ frame: "none", title: "none" }),
  // 纯图（横幅、示意图）：无框无标题。
  artwork: Object.freeze({ frame: "none", title: "none" }),
  // 画布上的分区标签：无框，标题即内容。
  label: Object.freeze({ frame: "none", title: "inside" }),
});

export function canvasPresentationStyles(): string[] {
  return Object.keys(PRESENTATION_BY_STYLE);
}

/**
 * 解析卡片最终外观：预设提供默认值，卡上显式字段永远覆盖预设。
 * 未声明外观的老卡片得到 panel + inside —— 与 0.2.0 的现状完全一致，不改既有观感。
 */
export function resolveCardPresentation(
  presentation: CanvasCardPresentation | undefined,
): Required<Pick<CanvasCardPresentation, "frame" | "title" | "emphasis">> & { titleMinZoom?: number } {
  const preset = presentation?.style ? PRESENTATION_BY_STYLE[presentation.style] : undefined;
  const merged = { ...preset, ...presentation };
  return {
    frame: merged.frame ?? "panel",
    title: merged.title ?? "inside",
    emphasis: merged.emphasis ?? "normal",
    ...(typeof merged.titleMinZoom === "number" ? { titleMinZoom: merged.titleMinZoom } : {}),
  };
}

/** 当前缩放下标题是否该显示。位置为 none 时永远不显示。 */
export function titleVisibleAtZoom(presentation: CanvasCardPresentation | undefined, zoom: number): boolean {
  const resolved = resolveCardPresentation(presentation);
  if (resolved.title === "none") return false;
  if (typeof resolved.titleMinZoom !== "number") return true;
  return zoom >= resolved.titleMinZoom;
}

/** 结构校验。未知取值一律拒绝，不静默退回默认。 */
/** 收纳态封闭值集（隐藏 / 图标化）；折叠走 fold。 */
export const CANVAS_CARD_STATES = ['hidden', 'iconified'] as const;

export function validateCanvasCardPresentation(value: unknown, where = "presentation"): string[] {
  if (value === undefined) return [];
  if (!value || typeof value !== "object") return [`${where} 必须是对象`];
  const presentation = value as Partial<CanvasCardPresentation>;
  const problems: string[] = [];
  if (presentation.frame !== undefined && !CANVAS_CARD_FRAMES.includes(presentation.frame)) {
    problems.push(`${where}.frame 必须是 ${CANVAS_CARD_FRAMES.join(" / ")} 之一`);
  }
  if (presentation.title !== undefined && !CANVAS_TITLE_PLACEMENTS.includes(presentation.title)) {
    problems.push(`${where}.title 必须是 ${CANVAS_TITLE_PLACEMENTS.join(" / ")} 之一`);
  }
  if (presentation.emphasis !== undefined && !CANVAS_CARD_EMPHASES.includes(presentation.emphasis)) {
    problems.push(`${where}.emphasis 必须是 ${CANVAS_CARD_EMPHASES.join(" / ")} 之一`);
  }
  if (presentation.titleMinZoom !== undefined
    && (!Number.isFinite(presentation.titleMinZoom) || presentation.titleMinZoom <= 0)) {
    problems.push(`${where}.titleMinZoom 必须是正有限数`);
  }
  if (presentation.style !== undefined
    && (typeof presentation.style !== "string" || !presentation.style.trim() || presentation.style.length > 48)) {
    problems.push(`${where}.style 必须是 1~48 字符的非空字符串`);
  }
  if (presentation.state !== undefined && !CANVAS_CARD_STATES.includes(presentation.state)) {
    problems.push(`${where}.state 必须是 ${CANVAS_CARD_STATES.join(" / ")} 之一`);
  }
  return problems;
}

/**
 * 备注（业主第 4 条："我们没有研发一种用于备注的 material（comment），应该单独，
 * 这个还挺重要的"）。
 *
 * 立场：备注**不是"设了样式的文字卡"**。它是一句"关于某个东西的话"，所以三件事必须声明：
 *   subject —— 它在说谁（切片：某张界面里的某个元素）。没有主语的备注不是备注。
 *   stance  —— 这句话的**认识论身份**：观测 / 推断 / 缺口 / 裁定。
 *               它决定外观（缺口虚线、裁定高亮），而不是反过来由样式决定它是什么。
 *   authority —— 谁说的、凭什么。观测记录写它的来源，人写的写人。
 *
 * stance **不许从正文文字猜**。它由来源声明：screen-annotations 是从截帧提取的观测记录，
 * 所以生成器声明 observation；真源里逐条写了别的就按真源。猜出来的立场比没有更坏。
 */
export interface CanvasCardComment {
  subject: CanvasSliceRef;
  stance: CanvasCommentStance;
  /** 这句话的出处（"spark-workshop/screen-annotations"、某个人、某次会议）。 */
  authority?: string;
}

/**
 * 备注的认识论身份。四值封闭 —— 想不清是哪一种，就说明这句话还没想清楚。
 *   observation 直接看到的（有帧、有日志）
 *   inference   由观测推出来的（"证明…已经结算"）
 *   gap         已知的空白（"这一段没采到"）
 *   decision    人做的裁定（"这里就按 A 来"）
 */
export type CanvasCommentStance = "observation" | "inference" | "gap" | "decision";

const CANVAS_COMMENT_STANCES: readonly CanvasCommentStance[] = Object.freeze([
  "observation", "inference", "gap", "decision",
]);

export function isCanvasCommentStance(value: unknown): value is CanvasCommentStance {
  return typeof value === "string" && (CANVAS_COMMENT_STANCES as readonly string[]).includes(value);
}

/** 校验；未声明即拒绝，不做兜底。返回问题清单，空数组=合格。 */
export function validateCanvasComment(value: unknown): string[] {
  if (!value || typeof value !== "object") return ["comment 必须是对象"];
  const candidate = value as Partial<CanvasCardComment>;
  const problems: string[] = [];
  if (!isCanvasCommentStance(candidate.stance)) {
    problems.push(`comment.stance 必须是 ${CANVAS_COMMENT_STANCES.join(" / ")} 之一`);
  }
  problems.push(...validateCanvasSlice(candidate.subject).map((problem) => `comment.subject: ${problem}`));
  if (candidate.authority !== undefined && typeof candidate.authority !== "string") {
    problems.push("comment.authority 必须是字符串");
  }
  return problems;
}

/**
 * 外观由 stance 派生 —— 这就是"形状是语义的衍生"落到实处的样子。
 * 备注一律无框无标题（画布上只出现那句话）；强调级别按 stance 给。
 */
export function resolveCommentPresentation(comment: CanvasCardComment): CanvasCardPresentation {
  const emphasis: CanvasCardEmphasis = comment.stance === "decision" ? "highlight"
    : comment.stance === "gap" ? "muted"
    : "normal";
  return { frame: "none", title: "none", emphasis };
}

// ── 评论（便利签）与写入身份（2026-09-12） ────────────────────────────────────
//
// 评论不是备注（上面的 CanvasCardComment 指向一个切片、带认识论立场）；评论是**人或 agent
// 对一张或多张卡说的一段话**，可以接续，同一条基础评论的接续归为一个线程。它是一种卡
// （kind=comment），不是卡上的字段。

/** 评论的语义分类。人写的评论天然是「意见」，分类只是 AI 事后给意见贴的标签；封闭值集。 */
export type CanvasOpinionSemantic = "question" | "correction" | "decision" | "suggestion" | "approval" | "note";
export const CANVAS_OPINION_SEMANTICS: readonly CanvasOpinionSemantic[] = Object.freeze([
  "question", "correction", "decision", "suggestion", "approval", "note",
]);
export function isCanvasOpinionSemantic(value: unknown): value is CanvasOpinionSemantic {
  return typeof value === "string" && (CANVAS_OPINION_SEMANTICS as readonly string[]).includes(value);
}

/** 评论线程：这张评论卡评的是哪些卡、接在哪条评论后面。 */
export interface CanvasCommentThread {
  /** 被评的卡 id（至少一张）。接续沿用根评论的 targets。 */
  targets: string[];
  /** 线程根评论 id；根评论填自己的 id。 */
  root: string;
  /** 直接接续的那条评论 id；根评论缺省。 */
  replyTo?: string;
  /** AI 后补的语义分类。 */
  semantic?: CanvasOpinionSemantic;
  /** 评论被转正为正式内容后指向的那张卡。 */
  promotedTo?: string;
}

const nonEmptyString = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;

/** 校验评论线程；空数组=合格。selfId 给出时检查根评论自指的一致性。 */
export function validateCanvasCommentThread(value: unknown, selfId?: string): string[] {
  if (!value || typeof value !== "object") return ["commentThread 必须是对象"];
  const thread = value as Partial<CanvasCommentThread>;
  const problems: string[] = [];
  if (!Array.isArray(thread.targets) || !thread.targets.length || !thread.targets.every(nonEmptyString)) {
    problems.push("commentThread.targets 必须是至少一个卡 id 的数组");
  }
  if (!nonEmptyString(thread.root)) problems.push("commentThread.root 必须是卡 id");
  if (thread.replyTo !== undefined && !nonEmptyString(thread.replyTo)) problems.push("commentThread.replyTo 必须是卡 id");
  if (selfId && nonEmptyString(thread.root) && thread.root !== selfId && thread.replyTo === undefined) {
    problems.push("接续评论必须写 replyTo");
  }
  if (selfId && thread.replyTo !== undefined && thread.replyTo === selfId) problems.push("评论不能接续自己");
  if (thread.semantic !== undefined && !isCanvasOpinionSemantic(thread.semantic)) {
    problems.push(`commentThread.semantic 必须是 ${CANVAS_OPINION_SEMANTICS.join(" / ")} 之一`);
  }
  if (thread.promotedTo !== undefined && !nonEmptyString(thread.promotedTo)) problems.push("commentThread.promotedTo 必须是卡 id");
  return problems;
}

/**
 * 写入身份：一次写入是「谁、在哪台机器、哪个运行时、哪个会话、为了什么」。
 * agent 与人共用一张表：agent 侧 principal=agent，machine/runtime/session 由 hook 与 CLI
 * 被动采集（会话本身无感）；人侧 principal=human，human.input 记输入方式，machine 是浏览设备。
 * session.native_id 必须是厂商原生 id —— Claude 是 jsonl 文件名（去扩展名），Codex 是 thread id。
 */
export type WritePrincipal = "agent" | "human" | "system";
export const WRITE_PRINCIPALS: readonly WritePrincipal[] = Object.freeze(["agent", "human", "system"]);
export const WRITE_INPUT_METHODS = ["keyboard", "touch", "mouse", "pen", "paste", "drop", "voice", "drawing", "cli"] as const;
export type WriteInputMethod = (typeof WRITE_INPUT_METHODS)[number];
export const WRITE_CAPTURE_SOURCES = ["hook", "cli", "browser", "backfill", "product"] as const;
export type WriteCaptureSource = (typeof WRITE_CAPTURE_SOURCES)[number];

export interface WriteMachine {
  /** 机器自身标识：Windows 取 MachineGuid；浏览设备取本地实例 id。 */
  id: string;
  label?: string;
  /** 机主。机器绑定 owner 后，写入才算得上「某人手下的某台机器」。 */
  owner?: string;
  os?: string;
}
export interface WriteRuntime {
  /** claude_2 / codex / … */
  provider: string;
  label?: string;
  model?: string;
  version?: string;
}
export interface WriteSession {
  /** 厂商原生会话 id = 磁盘上那份 jsonl 的文件名（去扩展名）。 */
  native_id: string;
  /** 会话文件绝对路径（能定位到就写）。 */
  file?: string;
  trace_id?: string;
  pty_id?: string;
}
export interface WriteHuman {
  name: string;
  input: WriteInputMethod;
  device?: string;
}
export interface WriteIdentity {
  principal: WritePrincipal;
  machine: WriteMachine;
  runtime?: WriteRuntime;
  session?: WriteSession;
  human?: WriteHuman;
  /** 写入目的，可后补（omni canvas purpose / batch-summary）。 */
  purpose?: string;
  /** 一批正式写入的批号。 */
  batch?: string;
  captured_by: WriteCaptureSource;
  at: string;
}

/** 校验写入身份；空数组=合格。未知取值一律拒绝。 */
export function validateWriteIdentity(value: unknown): string[] {
  if (!value || typeof value !== "object") return ["identity 必须是对象"];
  const identity = value as Partial<WriteIdentity>;
  const problems: string[] = [];
  if (!(WRITE_PRINCIPALS as readonly string[]).includes(String(identity.principal))) {
    problems.push(`identity.principal 必须是 ${WRITE_PRINCIPALS.join(" / ")} 之一`);
  }
  if (!identity.machine || typeof identity.machine !== "object" || !nonEmptyString(identity.machine.id)) {
    problems.push("identity.machine.id 必填");
  }
  if (identity.runtime !== undefined && (!identity.runtime || typeof identity.runtime !== "object" || !nonEmptyString(identity.runtime.provider))) {
    problems.push("identity.runtime.provider 必填");
  }
  if (identity.session !== undefined && (!identity.session || typeof identity.session !== "object" || !nonEmptyString(identity.session.native_id))) {
    problems.push("identity.session.native_id 必填");
  }
  if (identity.human !== undefined) {
    if (!identity.human || typeof identity.human !== "object" || !nonEmptyString(identity.human.name)) problems.push("identity.human.name 必填");
    else if (!(WRITE_INPUT_METHODS as readonly string[]).includes(String(identity.human.input))) {
      problems.push(`identity.human.input 必须是 ${WRITE_INPUT_METHODS.join(" / ")} 之一`);
    }
  }
  if (identity.principal === "human" && identity.human === undefined) problems.push("principal=human 必须带 human");
  if (identity.principal === "agent" && identity.runtime === undefined) problems.push("principal=agent 必须带 runtime");
  if (!(WRITE_CAPTURE_SOURCES as readonly string[]).includes(String(identity.captured_by))) {
    problems.push(`identity.captured_by 必须是 ${WRITE_CAPTURE_SOURCES.join(" / ")} 之一`);
  }
  if (!nonEmptyString(identity.at) || Number.isNaN(Date.parse(identity.at))) problems.push("identity.at 必须是 ISO 时间");
  if (identity.purpose !== undefined && typeof identity.purpose !== "string") problems.push("identity.purpose 必须是字符串");
  if (identity.batch !== undefined && !nonEmptyString(identity.batch)) problems.push("identity.batch 必须是非空字符串");
  return problems;
}

const trimmed = (value: unknown): string | undefined => (nonEmptyString(value) ? value.trim().slice(0, 400) : undefined);

/** 规范化写入身份：去掉未知键与空值；不合契约返回 undefined（调用方决定拒绝还是忽略）。 */
export function normalizeWriteIdentity(value: unknown): WriteIdentity | undefined {
  if (validateWriteIdentity(value).length) return undefined;
  const identity = value as WriteIdentity;
  const machine: WriteMachine = { id: identity.machine.id.trim().slice(0, 200) };
  for (const key of ["label", "owner", "os"] as const) {
    const text = trimmed(identity.machine[key]);
    if (text) machine[key] = text;
  }
  const out: WriteIdentity = {
    principal: identity.principal,
    machine,
    captured_by: identity.captured_by,
    at: new Date(identity.at).toISOString(),
  };
  if (identity.runtime) {
    out.runtime = { provider: identity.runtime.provider.trim().slice(0, 120) };
    for (const key of ["label", "model", "version"] as const) {
      const text = trimmed(identity.runtime[key]);
      if (text) out.runtime[key] = text;
    }
  }
  if (identity.session) {
    out.session = { native_id: identity.session.native_id.trim().slice(0, 200) };
    for (const key of ["file", "trace_id", "pty_id"] as const) {
      const text = trimmed(identity.session[key]);
      if (text) out.session[key] = text;
    }
  }
  if (identity.human) {
    out.human = { name: identity.human.name.trim().slice(0, 120), input: identity.human.input };
    const device = trimmed(identity.human.device);
    if (device) out.human.device = device;
  }
  const purpose = typeof identity.purpose === "string" ? identity.purpose.trim().slice(0, 2000) : "";
  if (purpose) out.purpose = purpose;
  const batch = trimmed(identity.batch);
  if (batch) out.batch = batch;
  return out;
}

/** 由身份派生的兼容 actor_ref（旧读法只认一个字符串）。 */
export function writeIdentityActorRef(identity: WriteIdentity): MaterialNoteRef {
  const who = identity.principal === "human"
    ? identity.human?.name ?? "human"
    : identity.runtime?.provider ?? identity.principal;
  const where = identity.session?.native_id ? `/${identity.session.native_id}` : "";
  return { kind: identity.principal, ref: `${who}@${identity.machine.id}${where}`.slice(0, 220) };
}

const INPUT_METHOD_LABEL: Readonly<Record<WriteInputMethod, string>> = Object.freeze({
  keyboard: "键盘", touch: "触屏", mouse: "鼠标", pen: "手写笔", paste: "粘贴", drop: "拖入", voice: "语音", drawing: "绘画", cli: "命令行",
});

/** 人看的一句话：「maintainer · 触屏 · 平板」 / 「claude_2 · 会话 073260ab · L-2026」。 */
/**
 * 给人看的作者标签：只有"谁"。设备（平板 / 触屏）、输入方式、会话 id 对人没有意义
 * （业主 2026-09-13：评论不要写触屏平板这样的设备身份），那些留给写入来源面板与编辑历史。
 */
export function writeIdentityAuthorLabel(identity: WriteIdentity): string {
  if (identity.principal === "human") return identity.human?.name ?? "人";
  if (identity.principal === "agent") return "助手";
  return identity.runtime?.label ?? identity.runtime?.provider ?? identity.principal;
}

export function writeIdentityLabel(identity: WriteIdentity): string {
  if (identity.principal === "human") {
    const parts = [identity.human?.name ?? "人", INPUT_METHOD_LABEL[identity.human?.input ?? "keyboard"]];
    const device = identity.human?.device ?? identity.machine.label;
    if (device) parts.push(device);
    return parts.join(" · ");
  }
  const parts = [identity.runtime?.label ?? identity.runtime?.provider ?? identity.principal];
  if (identity.session?.native_id) parts.push(`会话 ${identity.session.native_id.slice(0, 8)}`);
  parts.push(identity.machine.label ?? identity.machine.id.slice(0, 8));
  return parts.join(" · ");
}

/** 卡片溯源：首次写入与最近一次写入的身份。由存储盖章，客户端不得自填。 */
export interface CanvasCardProvenance {
  created: WriteIdentity;
  updated: WriteIdentity;
}

/** 这张卡是从另一张卡派生的（人改 AI 内容 = 原地无损拷贝；评论转正）。 */
export interface CanvasCardDerivedFrom {
  card: string;
  revision: number;
  mode: "human-edit-copy" | "promotion";
}
export const CANVAS_DERIVED_MODES = ["human-edit-copy", "promotion"] as const;

export interface CanvasCardRecord {
  id: string;
  kind: CardKind;
  title: string;
  text?: string;
  /**
   * 可选的 Material Core 描述。画布只保存和投影，不在这里裁定权威。
   * 生产方应在写入前用共享 Material Core 校验；这里保持开放以免复制协议定义。
   */
  material?: Record<string, unknown>;
  source?: MaterialSource;
  adapter: MaterialAdapter;
  x: number;
  y: number;
  width: number;
  height: number;
  /** 当前视图里的正文基础字号；画布缩小时前端还会做有限的可读性补偿。 */
  fontSize?: number;
  /** 组合: 子卡所属组卡 id(React Flow subflow; 坐标转为相对组卡)。旧文档无此字段=未编组。 */
  parentId?: string;
  /** 组内阅读顺序(M2): 数字, 升序; 缺省走 continues 拓扑/坐标兜底。 */
  order?: number;
  /** 提审一等字段(M1a); 缺省=未提审。 */
  submission?: CardSubmission;
  /** 锁定后，人工拖动、自动布局和普通 Agent 更新都不得改变位置。 */
  positionLock?: CanvasPositionLock;
  /** 坐标来源。旧文档缺省时按 user 保护，禁止自动布局猜测并覆盖。 */
  positionSource?: CanvasPositionSource;
  /** 当前 Canvas/Group 对该成员显示插件的显式覆写。 */
  display?: CanvasDisplayOverride;
  /** Generic fold state. A collapsed root projects as a count badge and hides its descendants. */
  fold?: CanvasCardFold;
  /** 组卡的同位切换声明；所有视图任何时刻只投影 activeVariant。 */
  samePositionContainer?: CanvasSamePositionContainer;
  /** 子卡在同位容器中的稳定槽位和所属版本。 */
  samePositionMember?: CanvasSamePositionMember;
  /**
   * 这张卡是一句**备注**。声明了它，外观就由 stance 派生（见 resolveCommentPresentation），
   * 不再需要手工写 presentation.style。缺省 = 不是备注。
   */
  comment?: CanvasCardComment;
  /** kind=comment 必带：评的是哪些卡、接在哪条后面。其它 kind 不得带。 */
  commentThread?: CanvasCommentThread;
  /** 写入溯源，存储盖章。 */
  provenance?: CanvasCardProvenance;
  /** 派生关系（人改稿 / 转正）。 */
  derivedFrom?: CanvasCardDerivedFrom;
  /** 卡片外观（框线 / 标题位置 / 标题缩放门槛 / 强调）。缺省 = panel + inside，与旧观感一致。 */
  presentation?: CanvasCardPresentation;
  /** 冻结快照元数据: 存在即渲染为死快照; from/fromAdapter 记住活源供解冻新开一块。 */
  frozen?: { capturedAt: string; from: MaterialSource; fromAdapter: MaterialAdapter };
}

/**
 * 切片种类。封闭值集 —— 新增种类必须先改 core，宿主不得自造。
 *
 * 业主 2026-08-20 裁定：「card 之间的连接可以针对 material 内的任意切片，
 * 包括文字类的，交互 demo 类还有图片类的」。所以切片不是"图片专属"，
 * 它是**所有材料形态共用的子寻址单位**。
 *
 * element 与 region 故意分开，不合并成"图片切片"：
 *   - element 的身份来自外部真源（界面层级 path），**可跨业务共用**，box 是派生值；
 *   - region 是人手在这张图上画的，只属于这张图。
 * 合并这两者就会重演"把 px 除成百分比字符串再把真源丢掉"那条老路。
 */
export type CanvasSliceKind = "element" | "region" | "range" | "step";

export const CANVAS_SLICE_KINDS: readonly CanvasSliceKind[] = Object.freeze([
  "element",
  "region",
  "range",
  "step",
]);

/** element/region 必须给 box；range/step 必须不给。 */
const SLICE_KINDS_WITH_BOX: ReadonlySet<CanvasSliceKind> = new Set(["element", "region"]);

export interface CanvasSliceBox {
  /** 归一化 0..1（可越界，容器负责裁剪）。**不存 px，不存百分比字符串。** */
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CanvasSliceRef {
  kind: CanvasSliceKind;
  /**
   * 切片在其 representation 内的稳定标识：
   *   element → 界面元素的完整层级 path（底层元素身份，跨玩法共用的就是这一个值）
   *   region  → 人工圈选区域 id
   *   range   → 文本锚点 id
   *   step    → demo 内步骤/状态 id
   * 重复出现不靠往 ref 里拼 `#occurrence=N`，用下面的 occurrence 字段 —— 拼进字符串就又变成不可拆的结构。
   */
  ref: string;
  /** 落在卡片的哪个 representation 上；缺省 = primary。 */
  representationId?: string;
  /** 同一 ref 在同一 representation 内重复出现时的序号（0 起）。 */
  occurrence?: number;
  /** element/region 的归一化包围盒。 */
  box?: CanvasSliceBox;
  /** 给人看的短名；不参与身份。 */
  label?: string;
  /**
   * 这块框选该有多响。**不参与身份**（`canvasSliceHandleId` 不看它）。
   *
   * 业主第 7 条："框选地方应该保持简约，就是一个透明度更低的，鼠标悬浮显示名字，
   * 不要提前显示标点号什么的"。所以缺省是 quiet：只有一条淡描边，编号不上屏，
   * 指到才显名字与编号。要强调某一块（策划案里那种黄底那条）才声明 highlight。
   */
  emphasis?: CanvasSliceEmphasis;
}

/**
 * 框选的响度。缺省 quiet —— 界面阵列上一屏几十个框，默认全都很响就等于都不响。
 */
export type CanvasSliceEmphasis = "quiet" | "normal" | "highlight";

const CANVAS_SLICE_EMPHASES: readonly CanvasSliceEmphasis[] = Object.freeze(["quiet", "normal", "highlight"]);

export function isCanvasSliceEmphasis(value: unknown): value is CanvasSliceEmphasis {
  return typeof value === "string" && (CANVAS_SLICE_EMPHASES as readonly string[]).includes(value);
}

/**
 * 元素完整层级 path → 给人认的短名。
 *
 * 不能简单取最后一段：Unity 的界面层级里最后一段常是 `anim`/`layout`/`content` 这类
 * **通用容器**，取到手就是一串没有识别力的「anim」（实测悬浮提示就是这么来的）。
 * 规则是从右往左跳过通用段，取第一个真名；全是通用段时退回最后一段（有什么给什么，
 * 不编）。`#` 前缀是 Unity 层级里的匿名节点标记，去掉只为好读，不参与身份。
 */
const GENERIC_PATH_SEGMENTS: ReadonlySet<string> = new Set([
  "anim", "layout", "content", "root", "view", "anchor", "group", "container",
]);

export function shortElementName(path: string): string {
  const segments = path.split("/").map((segment) => segment.trim()).filter(Boolean);
  const last = segments[segments.length - 1];
  if (!last) return path;
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const bare = (segments[index] ?? "").replace(/^#/, "");
    if (bare && !GENERIC_PATH_SEGMENTS.has(bare)) return bare;
  }
  return last.replace(/^#/, "");
}

export function resolveSliceEmphasis(slice: Pick<CanvasSliceRef, "emphasis"> | null | undefined): CanvasSliceEmphasis {
  return isCanvasSliceEmphasis(slice?.emphasis) ? slice.emphasis : "quiet";
}

const SLICE_HANDLE_PREFIX: Readonly<Record<CanvasSliceKind, string>> = Object.freeze({
  // range 不加前缀：存量注释锚点早已直接用锚点 id 当 React Flow handle id，
  // 加前缀会让已经落库的那些关系一次性失配。
  range: "",
  element: "el:",
  region: "rg:",
  step: "st:",
});

/**
 * 由切片派生 React Flow 的 handle id。**这是唯一的派生方向。**
 * 消费者不得反向从 handle id 猜切片语义，也不得自己拼 handle id。
 */
export function canvasSliceHandleId(slice: CanvasSliceRef): string {
  const scope = slice.representationId ? `${slice.representationId}@` : "";
  const occurrence = typeof slice.occurrence === "number" && slice.occurrence > 0 ? `#${slice.occurrence}` : "";
  return `${scope}${SLICE_HANDLE_PREFIX[slice.kind] ?? ""}${slice.ref}${occurrence}`;
}

/** 结构校验。未声明即拒绝，不做兜底 —— 与 core 0.3.0 的其余部分同一条纪律。 */
export function validateCanvasSlice(slice: unknown, where = "slice"): string[] {
  const problems: string[] = [];
  if (!slice || typeof slice !== "object") return [`${where} 必须是对象`];
  const value = slice as Partial<CanvasSliceRef>;
  const kind = value.kind as CanvasSliceKind | undefined;
  if (!kind || !CANVAS_SLICE_KINDS.includes(kind)) {
    return [`${where}.kind 必须是 ${CANVAS_SLICE_KINDS.join(" / ")} 之一（值集封闭）`];
  }
  if (typeof value.ref !== "string" || !value.ref.trim()) problems.push(`${where}.ref 必须是非空字符串`);
  if (typeof value.ref === "string" && /#occurrence=/.test(value.ref)) {
    problems.push(`${where}.ref 里不许拼 "#occurrence="，重复序号用 occurrence 字段`);
  }
  if (value.representationId !== undefined
    && (typeof value.representationId !== "string" || !value.representationId.trim())) {
    problems.push(`${where}.representationId 给了就必须是非空字符串`);
  }
  if (value.occurrence !== undefined
    && (!Number.isInteger(value.occurrence) || (value.occurrence as number) < 0)) {
    problems.push(`${where}.occurrence 必须是 >=0 的整数`);
  }
  const needsBox = SLICE_KINDS_WITH_BOX.has(kind);
  if (needsBox && !value.box) problems.push(`${where}: kind=${kind} 必须给 box（归一化包围盒）`);
  if (!needsBox && value.box) problems.push(`${where}: kind=${kind} 不得给 box（它没有图内区域）`);
  if (value.box) {
    const box = value.box as Partial<CanvasSliceBox>;
    for (const key of ["x", "y", "width", "height"] as const) {
      if (!Number.isFinite(box[key])) problems.push(`${where}.box.${key} 必须是有限数`);
    }
    if (Number.isFinite(box.width) && (box.width as number) <= 0) problems.push(`${where}.box.width 必须 > 0`);
    if (Number.isFinite(box.height) && (box.height as number) <= 0) problems.push(`${where}.box.height 必须 > 0`);
  }
  return problems;
}

/**
 * 连线的层级语义。
 *
 * `under` 压在卡片下面、`content` 与卡片同层（按 DOM 顺序，卡片赢）、`over` 压在卡片上面。
 *
 * 读 @xyflow 的 base.css 才明白为什么非要显式声明：`.react-flow__edges` 与
 * `.react-flow__nodes` **两个容器都没有 z-index**，每条边是自己一个
 * `<svg style={{zIndex}}>`、每个节点是 `<div style={{zIndex: internals.z}}>`，
 * 于是两者在同一个层叠上下文里直接比较；basic 模式下节点 z=0、边默认也 0，
 * 平票时**后出现在 DOM 里的节点赢**。这就是业主第 6 条"所有注释的连线都在图下面 ——
 * 导致完全看不到"的机制。任意一条边只要 z ≥ 1 就翻上来。
 */
export type CanvasRelationLayer = "under" | "content" | "over";

const CANVAS_RELATION_LAYERS: readonly CanvasRelationLayer[] = Object.freeze(["under", "content", "over"]);

export function isCanvasRelationLayer(value: unknown): value is CanvasRelationLayer {
  return typeof value === "string" && (CANVAS_RELATION_LAYERS as readonly string[]).includes(value);
}

/**
 * 层级的缺省值由关系类型派生 —— 注释类默认压在内容上面，串联类默认与内容同层。
 * 未声明 kind 时不擅自抬升：那是"未定"，不是"注释"。
 */
export function resolveRelationLayer(relation: Pick<CanvasRelationRecord, "layer" | "kind">): CanvasRelationLayer {
  if (isCanvasRelationLayer(relation.layer)) return relation.layer;
  /*
   * **默认一律画在内容上面。** 业主：「然后线们都要在画面上（至少现在是）」。
   *
   * 原先只有注释类默认 over，串联与跳转是 content —— 而 `.react-flow__edges` 与
   * `.react-flow__nodes` 两个容器都没有 z-index，平票时 DOM 更靠后的节点赢，
   * 所以 content 实际就是"压在卡片下面"。一条被卡片盖住的线等于不存在。
   * 想把某条线压下去仍然可以，但要**显式**写 layer: 'under'，不再是默认行为。
   */
  return "over";
}

/**
 * 层级 → React Flow 的 zIndex。
 *
 * 节点在 manual 模式下拿的是 `node.zIndex ?? 0`，所以 `over` 只要给到 1 就够；
 * 留出 10 的间距是为了让边上的标签能取 z+1 而不撞到下一档。
 */
export function relationLayerZIndex(layer: CanvasRelationLayer): number {
  if (layer === "under") return -10;
  if (layer === "over") return 10;
  return 0;
}

/**
 * 关系类型。前三种是存量值（continues/references/derived），后三种是业主第 5/8 条要补的。
 * 值集封闭：想不出属于哪一种，说明这条线的语义还没立住，宁可留空。
 */
export type CanvasRelationKind =
  | "continues"
  | "transitions-to"
  | "annotates"
  | "references"
  | "derived"
  | "contradicts";

const CANVAS_RELATION_KINDS: readonly CanvasRelationKind[] = Object.freeze([
  "continues", "transitions-to", "annotates", "references", "derived", "contradicts",
]);

export function isCanvasRelationKind(value: unknown): value is CanvasRelationKind {
  return typeof value === "string" && (CANVAS_RELATION_KINDS as readonly string[]).includes(value);
}

/**
 * 这条关系**有方向吗**。
 *
 * 有方向的是"从这里到那里"：串联(continues)、跳转(transitions-to)、派生(derived)。
 * 没方向的是"这个说的是那个"：说明(annotates)、旁证(references)、冲突(contradicts) ——
 * 注释指着它注释的那个元素，两头不是先后关系，画箭头是在说一件不存在的事。
 *
 * 渲染层据此决定画不画方向（流动的箭标、以及高亮时的流动动画）；
 * 判据也导入这一条，不要在探针里照着 kind 名字重推一遍
 * —— 重推过一次，32 条 annotates 的层级全判错了。
 */
export function isDirectionalRelation(kind: CanvasRelationKind | undefined): boolean {
  return kind === "continues" || kind === "transitions-to" || kind === "derived";
}

/**
 * 关系的契约校验。目前只有一条硬规则，但它是业主第 8 条的可校验根治：
 * **`transitions-to` 必须带 trigger**。让"跳转没有文字标注"在入库时就不成立，
 * 而不是靠人记得写。
 */
export function validateCanvasRelationSemantics(
  relation: Pick<CanvasRelationRecord, "id" | "kind" | "trigger">,
): string[] {
  if (relation.kind !== "transitions-to") return [];
  const trigger = typeof relation.trigger === "string" ? relation.trigger.trim() : "";
  return trigger ? [] : [`关系 ${relation.id}: transitions-to 必须声明 trigger（点了什么才跳）`];
}

export interface CanvasRelationRecord {
  id: string;
  source: string;
  target: string;
  /**
   * 端点切片。**它是权威**，`sourceHandle`/`targetHandle` 只是它的派生缓存。
   * 自动布局只准改坐标与路由点，不准改这两个字段（否则"从这个按钮出发"会退化成"从卡片右边出发"）。
   */
  sourceSlice?: CanvasSliceRef;
  targetSlice?: CanvasSliceRef;
  /** 旧版单标签；首次编辑后迁移到 userLabel。 */
  label?: string;
  /** 程序事实/硬链接标签，只允许程序或 Agent 修改。 */
  programLabel?: string;
  /** 用户自定义标签，可编辑并沿边拖动。 */
  userLabel?: string;
  programLabelPosition?: CanvasLabelPosition;
  userLabelPosition?: CanvasLabelPosition;
  /**
   * 关系分类。业主第 5 条："应该研发更多的连线类型，更符合注释"。
   *
   * 六种都是**注释关系**，不是画笔：
   *   continues     主线串联（这一步之后是那一步）
   *   transitions-to 界面跳转 —— **必须带 trigger**（"点了什么才跳"）。缺 trigger
   *                  就不是跳转，只是"后面还有一张图"，那是 continues。
   *   annotates     这句话在说那个东西（备注 → 切片）
   *   references    旁证、引用
   *   derived       派生（由它算出来的）
   *   contradicts   与它冲突（两处观测打架，必须能画出来而不是私下抹掉）
   * 缺省=未定。旧文档只用过前三种里的 continues/references/derived，取值向后兼容。
   */
  kind?: CanvasRelationKind;
  /**
   * 跳转的触发条件（"点 btn_close"、"飞奖完成回调"）。
   * `transitions-to` **没有它就不合契约** —— 业主第 8 条要的"合适的文字标注"就是它，
   * 而不是让人往 label 里随手写一句。没有观测证据就别声明 transitions-to，留 continues。
   */
  trigger?: string;
  /**
   * 这条线**在语义上**处在内容的上面还是下面。不是"置顶按钮"，是关系自己的一个位。
   *
   * 为什么必须是语义而不是观感：注释线指的是图里的元素，它天然要压在图上面才叫注释；
   * 而"这一步之后是那一步"的串联线穿过卡片下方并不妨碍阅读。缺省由 kind 派生
   * （见 resolveRelationLayer），所以存量数据不用逐条补。
   *
   * 渲染层把它翻成 React Flow 的每边 zIndex，并要求 zIndexMode="manual" ——
   * 否则 RF 的自动层级（选中 +1000、父节点 z 叠加）会盖掉这里的声明。
   */
  layer?: CanvasRelationLayer;
  sourceHandle?: string;
  targetHandle?: string;
  /** Canvas relations use one endpoint-derived Bézier family. */
  path?: "curve";
  arrow?: "closed" | "open" | "none";
}

export type CanvasPurpose = "product" | "test";
export type CanvasLifecycle = "test" | "development" | "formal" | "archived";
export type CanvasRole = "working" | "demo";
export type CanvasWorkflowStatus = "todo" | "work-in-progress" | "ready";

/**
 * Catalog metadata is intentionally orthogonal: lifecycle controls the list partition,
 * while purpose, role, domain and labels remain independent descriptive facets.
 */
export interface CanvasCatalogMetadata {
  purpose: CanvasPurpose;
  lifecycle: CanvasLifecycle;
  role: CanvasRole;
  domain: string;
  labels?: string[];
  /**
   * 这张画布服务于**功能树上的哪个节点**（资料那棵树的节点 id，如
   * `view:spark-workshop/doc/business%2FTutorial`）。
   *
   * 业主 2026-09-18：「和资料里面共用一个分类逻辑不好吗」——画布不再自己写一套
   * 二级词（步骤引导 / 配置记录混轴那套）。值是引用，**值集在 taxonomy 清单里**，
   * 这里只校验形状，不枚举。性质（role / workflow_status / purpose）另有其位，不进树。
   */
  subject_ref?: string;
  /** Wiki-derived placement; Canvas does not maintain a second subject taxonomy. */
  wiki_path?: string[];
  /** Readiness is independent from archive/formal lifecycle. */
  workflow_status?: CanvasWorkflowStatus;
  /** 创建者（Workshop 账号 id / 显示名）。缺省 = 历史画布，视作共享。 */
  owner?: string;
  owner_name?: string;
  /**
   * 可见性（业主 2026-09-21）：新建默认 `personal`——只在创建者自己的列表/资料树里出现，别人不受打扰；
   * 这不是权限隔离，拿到直达链接照样能开。`shared` = 进大家的视野。缺省（历史画布）按 shared 处理。
   */
  visibility?: CanvasVisibility;
}
export type CanvasVisibility = "personal" | "shared";

/** Query/projection metadata for a Canvas View. Material facts stay at source. */
export interface CanvasViewDefinition {
  source: {
    kind: "material-graph" | "canvas";
    ref: string;
  };
  selector?: {
    rootRefs?: string[];
    relationKinds?: string[];
    direction?: "outgoing" | "incoming" | "both";
    maxDepth?: number;
    nodeLimit?: number;
    includeFrontier?: boolean;
  };
  narrative?: {
    hiddenCardIds?: string[];
    cardOrder?: string[];
    chapterOrder?: string[];
    slideBreakBefore?: string[];
    titleOverrides?: Record<string, string>;
  };
}

export const DEFAULT_CANVAS_CATALOG: CanvasCatalogMetadata = {
  purpose: "product",
  lifecycle: "test",
  role: "working",
  domain: "uncategorized",
  workflow_status: "todo",
};

export function normalizeCanvasCatalog(value: unknown): CanvasCatalogMetadata {
  const candidate = value && typeof value === "object" ? value as Partial<CanvasCatalogMetadata> : {};
  const purpose: CanvasPurpose = candidate.purpose === "test" ? "test" : "product";
  const lifecycle: CanvasLifecycle = candidate.lifecycle === "development" || candidate.lifecycle === "formal"
    || candidate.lifecycle === "archived" ? candidate.lifecycle : "test";
  const role: CanvasRole = candidate.role === "demo" ? "demo" : "working";
  const rawDomain = typeof candidate.domain === "string" ? candidate.domain.trim().toLowerCase() : "";
  const domain = rawDomain.replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64)
    || "uncategorized";
  const labels = Array.isArray(candidate.labels)
    ? [...new Set(candidate.labels
      .filter((item): item is string => typeof item === "string")
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 24))]
    : [];
  const workflow_status: CanvasWorkflowStatus = candidate.workflow_status === "ready"
    || candidate.workflow_status === "work-in-progress" ? candidate.workflow_status
    : lifecycle === "formal" ? "ready" : lifecycle === "development" ? "work-in-progress" : "todo";
  const wiki_path = Array.isArray(candidate.wiki_path)
    ? candidate.wiki_path.filter((item): item is string => typeof item === "string")
      .map((item) => item.trim()).filter(Boolean).slice(0, 8)
    : [];
  // 功能树节点引用：只认 view: / material: 两种前缀（taxonomy 里的节点 id 形状），
  // 其它一律丢弃 —— 自由文本会立刻长回第二套分类词。
  const rawSubject = typeof candidate.subject_ref === "string" ? candidate.subject_ref.trim() : "";
  const subject_ref = /^(view|material):[^\s]{1,240}$/.test(rawSubject) ? rawSubject : undefined;
  const owner = typeof candidate.owner === "string" && candidate.owner.trim() ? candidate.owner.trim().slice(0, 120) : undefined;
  const owner_name = typeof candidate.owner_name === "string" && candidate.owner_name.trim() ? candidate.owner_name.trim().slice(0, 80) : undefined;
  const visibility: CanvasVisibility | undefined = candidate.visibility === "personal" || candidate.visibility === "shared"
    ? candidate.visibility : undefined;
  return { purpose, lifecycle, role, domain, workflow_status,
    ...(subject_ref ? { subject_ref } : {}),
    ...(wiki_path.length ? { wiki_path } : {}), ...(labels.length ? { labels } : {}),
    ...(owner ? { owner } : {}), ...(owner_name ? { owner_name } : {}), ...(visibility ? { visibility } : {}) };
}

/** 这张画布对某个人是否应从列表/资料树里隐去：他人的 personal 件。缺 owner/visibility 的历史画布一律可见。 */
export function isCanvasHiddenFrom(catalog: CanvasCatalogMetadata | undefined, userId: string | null | undefined): boolean {
  if (!catalog || catalog.visibility !== "personal" || !catalog.owner) return false;
  return catalog.owner !== (userId ?? "");
}

/**
 * 一份画布的 lifecycle —— **catalog 缺失一律算 `test`**（业主 2026-09-20：
 * 「老是忘记删掉这些测试的」）。
 *
 * 在此之前消费侧读的是未规范化的 `catalog?.lifecycle`：缺 catalog 判不出 test，于是
 * 没声明过归属的画布直接混进正式资料树（`功能 2 · Canvas` / `功能 3 · Canvas` 就是这么
 * 进「开发中 ▸ 开发对象」的）。判据 fail-open 意味着「忘了声明」= 当成正式内容，
 * 与「忘了删测试件」是同一个病根。现在反过来：**没声明就是临时件**，要进正式树必须
 * 显式写 lifecycle。
 */
export function canvasLifecycleOf(document: { catalog?: unknown } | null | undefined): CanvasLifecycle {
  return normalizeCanvasCatalog(document?.catalog).lifecycle;
}

/** 临时/测试件：随时可删，不参与正式资料归类。归档件不算——它有过正式身份。 */
export function isTemporaryCanvas(document: { catalog?: CanvasCatalogMetadata } | null | undefined): boolean {
  if (canvasLifecycleOf(document) === "test") return true;
  return document?.catalog?.wiki_path?.[0] === "程序测试";
}

export interface MaterialCanvasDocument {
  version: 1;
  sessionId: string;
  /** 画布名(可改, 持久化; 旧文档缺省 → 前端回退到会话名) */
  title?: string;
  /** Stable catalog facets used by Canvas lists; absent on legacy documents. */
  catalog?: CanvasCatalogMetadata;
  /** Canvas 级默认显示插件覆写；成员/组覆写优先。 */
  display?: CanvasDisplayOverride;
  /** Host-neutral runtime behavior: large-graph LOD, workbench links, checks and folding. */
  behavior?: CanvasBehavior;
  /** 间距档；缺省 comfortable。界面簇这类"挨得近才有意义"的画布声明 compact。 */
  spacing?: CanvasSpacingProfile;
  /** 可选查询视图；只保存来源、选择器与软编排，不复制来源 Material 事实。 */
  view?: CanvasViewDefinition;
  /** 服务端分配的单调提交序列；旧画布缺省为 0。 */
  revision: number;
  updatedAt: string;
  lastMutation?: { id: string; actor: string; sequence: number; at: string; identity?: WriteIdentity };
  cards: CanvasCardRecord[];
  relations: CanvasRelationRecord[];
}

export interface CanvasMutationPatch {
  set_title?: boolean;
  title?: string | null;
  set_catalog?: boolean;
  catalog?: CanvasCatalogMetadata | null;
  set_display?: boolean;
  display?: CanvasDisplayOverride | null;
  set_behavior?: boolean;
  behavior?: CanvasBehavior | null;
  set_spacing?: boolean;
  spacing?: CanvasSpacingProfile | null;
  set_view?: boolean;
  view?: CanvasViewDefinition | null;
  upsert_cards?: CanvasCardRecord[];
  remove_card_ids?: string[];
  upsert_relations?: CanvasRelationRecord[];
  remove_relation_ids?: string[];
}

export interface MaterialNoteRef {
  kind: string;
  ref: string;
}

export interface MaterialNoteWriteContext {
  workspace_ref: MaterialNoteRef;
  actor_ref: MaterialNoteRef;
  /** 写入身份（2026-09-12 起）；缺省时存储只记旧的 actor 字符串，不伪造身份。 */
  identity?: WriteIdentity;
}

export interface CanvasMutationRequest extends MaterialNoteWriteContext {
  document_id: string;
  base_revision: number;
  idempotency_key: string;
  /** Explicit authority to move a card whose current position is locked. */
  position_lock_override?: boolean;
  patch: CanvasMutationPatch;
}

export interface MaterialNoteProvider {
  putText(id: string, content: string, context: MaterialNoteWriteContext): Promise<{
    path: string;
    mime: string;
    modified_at: string;
    open_token: string;
    editableId: string;
  }>;
  getCanvas(documentId: string, context: MaterialNoteWriteContext): Promise<string | null>;
  mutateCanvas(request: CanvasMutationRequest): Promise<{ revision: number; json?: string; duplicate?: boolean; merged?: boolean }>;
}

export function safeRandomId(prefix: string): string {
  const randomUuid = globalThis.crypto?.randomUUID?.bind(globalThis.crypto);
  if (randomUuid) return `${prefix}${randomUuid()}`;
  const random = Math.random().toString(36).slice(2);
  return `${prefix}${Date.now().toString(36)}-${random}`;
}

export function canvasStorageId(sessionId: string): string {
  if (sessionId.startsWith("canvas:")) {
    const slug = sessionId.slice("canvas:".length).replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
    if (!slug) throw new TypeError("Canvas 路径不能为空");
    return slug;
  }
  const bytes = new TextEncoder().encode(sessionId);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const encoded = btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `session-${encoded.slice(0, 180)}`;
}

function slugSpace(value: string): string {
  const slug = value.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  if (slug === "spark-workshop") return "workshop";
  return slug || "note";
}

function decodeSessionDocumentId(documentId: string): string {
  if (!documentId.startsWith("session-")) return documentId;
  try {
    const raw = documentId.slice("session-".length).replace(/-/g, "+").replace(/_/g, "/");
    const padded = raw + "=".repeat((4 - (raw.length % 4)) % 4);
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    const decoded = new TextDecoder().decode(bytes);
    if (decoded.startsWith("canvas:")) return decoded.slice("canvas:".length);
    if (decoded.startsWith("external:")) return decoded.slice("external:".length);
    return decoded;
  } catch {
    return documentId;
  }
}

/** 把内部 document id 收成可读路径，避免 session-base64 这种无法口播的引用。 */
export function readableNotePath(documentId: string): string {
  const value = decodeSessionDocumentId(documentId.trim());
  if (value.startsWith("canvas:")) return value.slice("canvas:".length);
  return value.replace(/^\/+/, "");
}

export interface NoteLocator {
  space: string;
  path: string;
  card?: string;
}

/**
 * 短路径引用：`workshop/draft-ab12#text-1`。
 * 也可读成 URL：`/bside/canvas/draft-ab12#text-1`。
 */
export function formatNoteLocator(locator: NoteLocator, asUrl = false): string {
  const space = slugSpace(locator.space);
  const path = readableNotePath(locator.path);
  if (!space || !path) throw new TypeError("卡片引用缺少空间或路径");
  const body = asUrl ? `/${space}/${path}` : `${space}/${path}`;
  return locator.card ? `${body}#${locator.card.trim()}` : body;
}

export function parseNoteLocator(value: string): NoteLocator {
  const text = value.trim();
  const legacy = text.match(/^\[omni-canvas-card:([^\]]+)\]\s+([^#\s]+)#(.+)$/);
  if (legacy?.[1] && legacy[2]) {
    return { space: slugSpace(legacy[1]), path: readableNotePath(legacy[2]), card: legacy[3] };
  }
  const hash = text.indexOf("#");
  const rawBody = hash >= 0 ? text.slice(0, hash) : text;
  const card = hash >= 0 ? text.slice(hash + 1) : undefined;
  const stripped = rawBody.replace(/^[a-z]+:\/\//, "").replace(/^[^/]*:\d+/, "");
  const workshopUrl = stripped.match(/^\/?(?:bside\/)?(?:canvas|notes)\/([^?#]+)$/);
  if (workshopUrl?.[1]) {
    return { space: "workshop", path: stripNotesPrefix(readableNotePath(workshopUrl[1])), card };
  }
  const body = stripped.replace(/^\/+/, "");
  const slash = body.indexOf("/");
  if (slash <= 0) throw new TypeError("卡片引用必须是 空间/路径[#卡片]");
  const space = slugSpace(body.slice(0, slash));
  const path = stripNotesPrefix(readableNotePath(body.slice(slash + 1)));
  return { space, path, card };
}

function stripNotesPrefix(path: string): string {
  return path.replace(/^(?:notes|canvas)\//, "");
}

/**
 * Reverse a published path (`46fa34003c9a409a`) to the on-disk ids
 * formatNoteLocator / MaterialNotesView may have stored
 * (`session-<btoa(external:SID)>`, `canvas:…` slug, or the path itself).
 */
export function candidateNoteStorageIds(publishedPath: string): string[] {
  const path = publishedPath.trim();
  if (!path) return [];
  const ids = new Set<string>([path]);
  const tryStore = (sessionId: string): void => {
    try {
      ids.add(canvasStorageId(sessionId));
    } catch {
      /* invalid slug */
    }
  };
  tryStore(path);
  tryStore(`external:${path}`);
  tryStore(`canvas:${path}`);
  return [...ids];
}

/**
 * 可复制、可被 Agent 直接识别的跨应用卡片引用。
 * 现式是短路径；旧的 `[omni-canvas-card:…]` 仍可被 parseNoteLocator 读回。
 */
export function formatCanvasCardReference(application: string, documentId: string, cardId: string): string {
  return formatNoteLocator({ space: application, path: documentId, card: cardId });
}

export function freezeCanvasCard(
  card: CanvasCardRecord,
  capturedAt = new Date().toISOString(),
): CanvasCardRecord {
  if (!card.source) throw new TypeError("没有活源的卡片不能冻结");
  if (card.frozen) throw new TypeError("卡片已经是冻结快照");
  return {
    ...card,
    frozen: { capturedAt, from: card.source, fromAdapter: card.adapter },
  };
}

export function unfreezeCanvasCard(card: CanvasCardRecord): CanvasCardRecord {
  if (!card.frozen) throw new TypeError("不是冻结快照卡");
  const { frozen, ...rest } = card;
  return { ...rest, source: frozen.from, adapter: frozen.fromAdapter };
}

export function resizeCanvasCard(card: CanvasCardRecord, width: number, height: number): CanvasCardRecord {
  return {
    ...card,
    width: Math.max(120, Math.round(width)),
    height: Math.max(80, Math.round(height)),
  };
}

export interface NoteThemePlugin {
  id: string;
  tokens: Record<string, string>;
}

export function cssVarsFromNoteTheme(theme: NoteThemePlugin): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const [key, value] of Object.entries(theme.tokens)) {
    vars[key.startsWith("--") ? key : `--note-${key}`] = value;
  }
  return vars;
}

export const workshopNoteTheme: NoteThemePlugin = {
  id: "workshop",
  tokens: { bg: "#f7f1e8", surface: "#fffaf3", accent: "#8a4b27", text: "#2a2118" },
};

export const omnicompanyNoteTheme: NoteThemePlugin = {
  id: "omnicompany",
  tokens: { bg: "#111113", surface: "#1b1b1f", accent: "#7aa2ff", text: "#ececf1" },
};

export interface NoteDocumentBlock {
  id: string;
  title: string;
  text: string;
  order: number;
}

export type NoteCardOrigin = "agent" | "rule" | "user";

export function noteCardOrigin(card: CanvasCardRecord): NoteCardOrigin {
  const metadata = card.material && typeof card.material === "object"
    ? (card.material as { metadata?: Record<string, unknown> }).metadata
    : undefined;
  const origin = metadata?.["origin"];
  if (origin === "agent" || origin === "rule" || origin === "user") return origin;
  const by = card.submission?.submittedBy ?? "";
  if (by.startsWith("agent") || by === "workshop-cli") return "agent";
  if (typeof card.title === "string" && /^【(Agent|实施|口径|接手)/.test(card.title)) return "agent";
  return "user";
}

export function isUnreadNoteCard(card: CanvasCardRecord): boolean {
  if (card.kind === "group") return false;
  const state = card.submission?.state;
  if (state === "seen" || state === "fully-passed" || state === "not-fully-passed") return false;
  if (state === "unreviewed") return true;
  return noteCardOrigin(card) !== "user";
}

export function projectCanvasToDocument(document: MaterialCanvasDocument): {
  title: string;
  blocks: NoteDocumentBlock[];
} {
  // 评论是意见不是内容，文档投影里不铺。
  const cards = document.cards.filter((card) => card.kind !== "group" && card.kind !== "comment");
  const ordered = [...cards].sort((left, right) => (left.order ?? 1e9) - (right.order ?? 1e9) || left.y - right.y || left.x - right.x);
  return {
    title: document.title || "未命名",
    blocks: ordered.map((card, index) => ({
      id: card.id,
      title: card.title,
      text: card.text || "",
      order: card.order ?? index,
    })),
  };
}

export interface UnifiedViewMember {
  id: string;
  title: string;
  body: string;
  order: number;
}

export interface UnifiedView {
  locator: string;
  title: string;
  members: UnifiedViewMember[];
}

export function persistCite(locator: NoteLocator | string, asUrl = false): string {
  const parsed = typeof locator === "string" ? parseNoteLocator(locator) : locator;
  return formatNoteLocator(parsed, asUrl);
}

export function canvasDocumentId(sessionId: string): string {
  return canvasStorageId(sessionId.startsWith("canvas:") || sessionId.includes(":") ? sessionId : `canvas:${sessionId}`);
}

export function projectCanvasToView(document: MaterialCanvasDocument, space = "workshop"): UnifiedView {
  const projected = projectCanvasToDocument(document);
  const path = canvasDocumentId(document.sessionId);
  return {
    locator: formatNoteLocator({ space, path }),
    title: projected.title,
    members: projected.blocks.map((block) => ({
      id: block.id,
      title: block.title,
      body: block.text,
      order: block.order,
    })),
  };
}

export function projectDocumentToCanvas(view: UnifiedView): MaterialCanvasDocument {
  const locator = persistCite(view.locator);
  const parsed = parseNoteLocator(locator);
  const sessionId = parsed.path.includes(":") ? parsed.path : `canvas:${parsed.path}`;
  return {
    ...emptyCanvasDocument(sessionId),
    title: view.title,
    cards: [...view.members]
      .sort((left, right) => left.order - right.order)
      .map((member, index) => ({
        id: member.id,
        kind: "text" as const,
        title: member.title,
        text: member.body,
        adapter: "text" as const,
        source: { kind: "file" as const, path: `${locator}#${member.id}`, token: locator },
        x: 40,
        y: 40 + index * 360,
        width: 440,
        height: 320,
        order: member.order,
      })),
    relations: [],
  };
}

export function viewMemberIds(view: UnifiedView): string[] {
  return [...view.members].sort((left, right) => left.order - right.order || left.id.localeCompare(right.id)).map((member) => member.id);
}

export function viewMembersForTheme(view: UnifiedView, _theme: NoteThemePlugin): UnifiedViewMember[] {
  return view.members.map((member) => ({ ...member }));
}

/** One View, two projections. Wiki document and Canvas both read and write this. */
export class UnifiedMaterialSurface {
  private readonly views = new Map<string, UnifiedView>();

  writeFromCanvas(document: MaterialCanvasDocument, space = "workshop"): UnifiedView {
    const view = projectCanvasToView(document, space);
    this.views.set(view.locator, view);
    return view;
  }

  writeFromDocument(view: UnifiedView): UnifiedView {
    const normalized: UnifiedView = {
      locator: persistCite(view.locator),
      title: view.title,
      members: [...view.members].sort((left, right) => left.order - right.order),
    };
    this.views.set(normalized.locator, normalized);
    return normalized;
  }

  open(cite: string): UnifiedView {
    const locator = persistCite(cite.includes("/") || cite.startsWith("/") ? cite : `workshop/${cite}`);
    const parsed = parseNoteLocator(locator);
    const found = this.views.get(locator)
      ?? [...this.views.values()].find((item) => parseNoteLocator(item.locator).path === parsed.path);
    if (!found) throw new TypeError(`材料不存在：${cite}`);
    return found;
  }

  asDocument(cite: string): { title: string; blocks: NoteDocumentBlock[] } {
    const view = this.open(cite);
    return {
      title: view.title,
      blocks: view.members.map((member) => ({
        id: member.id,
        title: member.title,
        text: member.body,
        order: member.order,
      })),
    };
  }

  asCanvas(cite: string): MaterialCanvasDocument {
    return projectDocumentToCanvas(this.open(cite));
  }
}

export interface CanvasPositionedItem {
  id: string;
  kind: string;
  x: number;
  y: number;
  width: number;
  height: number;
  parentId?: string;
}

export interface CanvasGroupBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** 画布共用的字号边界；宿主可以收窄范围，但不能各自发明取整规则。 */
/**
 * 间距档。业主 2026-08-20：「让画布在保持用曲线的情况下保持高度紧凑……界面和界面之间
 * 很依赖挨得近这件事情」。紧凑对界面成立，对长文字卡不成立，所以做成**按画布声明的档**，
 * 不是全局常量。
 *
 * 一份数值同时喂给布局器和布局后的二次推挤 —— 此前二次推挤用写死的 gap=48，
 * 把布局器刚压紧的结果重新撑开，是"改了间距却没变紧凑"的真因。
 */
export type CanvasSpacingProfile = "compact" | "comfortable";

export interface CanvasSpacing {
  /** 同层相邻卡片间距。 */
  nodeNode: number;
  /** 层与层之间（流向方向）的间距。 */
  betweenLayers: number;
  /** 互不相连的连通分量之间的间距。 */
  componentComponent: number;
  /** 边与卡片的最小净空。 */
  edgeNode: number;
}

export const CANVAS_SPACING: Readonly<Record<CanvasSpacingProfile, CanvasSpacing>> = Object.freeze({
  compact: Object.freeze({ nodeNode: 12, betweenLayers: 32, componentComponent: 20, edgeNode: 10 }),
  comfortable: Object.freeze({ nodeNode: 30, betweenLayers: 84, componentComponent: 40, edgeNode: 18 }),
});

export function canvasSpacing(profile: CanvasSpacingProfile | undefined): CanvasSpacing {
  return CANVAS_SPACING[profile ?? "comfortable"] ?? CANVAS_SPACING.comfortable;
}

export interface CanvasIntrinsicSize {
  width: number;
  height: number;
}

/**
 * 按材料自身比例定卡片尺寸。业主第 5 条：「如果一个图片有尺寸，卡片自动和相同尺寸，
 * 不要进一步留出缺口」。
 *
 * 此前图片卡走 `fitDimensions(current, 360, 260)` —— 固定的近横向框，
 * 9:16 的界面截图塞进去必然留白。这里改成：在给定"目标面积"下**保持原比例**求宽高。
 * 不设最小宽高下限去破坏比例；只吸附到 2px 网格避免亚像素抖动。
 */
/**
 * 卡片盒模型常数 —— 生成器、几何、CSS **共用这一份**。
 *
 * 之前 TS 侧只有 canvasEdgeGeometry 自己抄了一份，生成器压根不知道有边框和标题带，
 * 于是"按图片比例定尺"定的是**整卡**的比例：正文框比整卡少 2px 边框 + 10px 标题带，
 * 比例必然对不上，letterbox 一定存在（实测正文框 0.653 对素材 0.5625，
 * 图片左右各留 5.77px 白）。业主第 2 条"距离和 demo 或者图片紧密贴合还是有点差异"
 * 说的就是这个差。
 */
export const CANVAS_CARD_BOX = Object.freeze({
  /** `.material-canvas-card { border: 1px }`；frame-none 只把颜色改透明，边框仍占位。 */
  border: 1,
  /** `.material-card-drag.bare { height: 10px }` —— 标题外置时卡内那条透明拖拽带。 */
  bareBand: 10,
  /** `.material-canvas-card { grid-template-rows: 38px … }` —— 标题在卡内时的表头。 */
  titleBand: 38,
});

export type CanvasCardTitleMode = "inside" | "above" | "none";

/** 正文框相对整卡少掉的宽高（"卡壳"）。 */
export function canvasCardChrome(title: CanvasCardTitleMode): { width: number; height: number } {
  const top = title === "inside" ? CANVAS_CARD_BOX.titleBand : title === "above" ? CANVAS_CARD_BOX.bareBand : 0;
  return { width: CANVAS_CARD_BOX.border * 2, height: top + CANVAS_CARD_BOX.border };
}

export function fitCardToIntrinsicRatio(
  intrinsic: CanvasIntrinsicSize,
  targetArea: number,
  options: { minSide?: number; maxSide?: number; grid?: number; chrome?: { width: number; height: number } } = {},
): CanvasIntrinsicSize {
  const { minSide = 96, maxSide = 1400, grid = 2, chrome } = options;
  if (!Number.isFinite(intrinsic.width) || !Number.isFinite(intrinsic.height)
    || intrinsic.width <= 0 || intrinsic.height <= 0 || !Number.isFinite(targetArea) || targetArea <= 0) {
    throw new TypeError("固有尺寸与目标面积必须是正有限数");
  }
  const ratio = intrinsic.width / intrinsic.height;
  let height = Math.sqrt(targetArea / ratio);
  let width = height * ratio;
  // 夹取时**两边同时缩放**，比例不动 —— 只夹一边就是重新引入留白。
  const scale = Math.min(
    1,
    maxSide / Math.max(width, height),
  ) * Math.max(1, minSide / Math.min(width, height));
  width *= scale;
  height *= scale;
  const snap = (value: number) => Math.max(grid, Math.round(value / grid) * grid);
  /*
   * 给了卡壳就把它**加回去**：拟合的是正文框（图片真正画出来的那块），
   * 返回的是整卡尺寸。不加回去就是"整卡等于图片比例、正文框不等于"，letterbox 照旧。
   * 卡壳不参与 snap 之前的比例计算，否则又把它算进比例里去了。
   */
  if (chrome) return { width: snap(width) + chrome.width, height: snap(height) + chrome.height };
  return { width: snap(width), height: snap(height) };
}

/** 卡片尺寸与固有比例的相对偏差；验收判据 3 用它判 ≤1%。 */
export function cardRatioDeviation(card: CanvasIntrinsicSize, intrinsic: CanvasIntrinsicSize): number {
  const want = intrinsic.width / intrinsic.height;
  const got = card.width / card.height;
  return Math.abs(got - want) / want;
}

/**
 * **正文框**与固有比例的相对偏差 —— 业主第 2 条判的是这个，不是整卡。
 *
 * 整卡比例对上了不代表图片没留白：图片画在正文框里，正文框比整卡少一圈卡壳。
 * 判据要钉在"图片实绘区 == 正文框"这件事上，所以先扣掉卡壳再比。
 */
export function cardBodyRatioDeviation(
  card: CanvasIntrinsicSize,
  intrinsic: CanvasIntrinsicSize,
  title: CanvasCardTitleMode,
): number {
  const chrome = canvasCardChrome(title);
  const body = { width: card.width - chrome.width, height: card.height - chrome.height };
  if (body.width <= 0 || body.height <= 0) return 1;
  return cardRatioDeviation(body, intrinsic);
}

export function normalizeCanvasFontSize(value: number, min = 8, max = 72, fallback = 14): number {
  if (!Number.isFinite(value)) return Math.round(Math.max(min, Math.min(max, fallback)));
  return Math.round(Math.max(min, Math.min(max, value)));
}

/**
 * 共用的空间编组操作。宿主只负责构造自己的组节点外观，坐标与 parentId 语义由核心维护。
 */
export function groupCanvasItems<T extends CanvasPositionedItem>(
  items: T[],
  selectedIds: readonly string[],
  createGroup: (bounds: CanvasGroupBounds) => T,
  paddingX = 28,
  paddingTop = 58,
  paddingBottom = 28,
): T[] {
  const selected = new Set(selectedIds);
  const children = items.filter((item) => selected.has(item.id) && item.kind !== "group" && !item.parentId);
  if (children.length < 2) return items;
  const left = Math.min(...children.map((item) => item.x));
  const top = Math.min(...children.map((item) => item.y));
  const right = Math.max(...children.map((item) => item.x + item.width));
  const bottom = Math.max(...children.map((item) => item.y + item.height));
  const bounds = {
    x: left - paddingX,
    y: top - paddingTop,
    width: right - left + paddingX * 2,
    height: bottom - top + paddingTop + paddingBottom,
  };
  const group = createGroup(bounds);
  return [group, ...items.map((item) => selected.has(item.id) && item.kind !== "group" && !item.parentId
    ? { ...item, x: item.x - group.x, y: item.y - group.y, parentId: group.id }
    : item)];
}

/** 共用的取消编组操作；子卡恢复绝对坐标，组节点本身被移除。 */
export function ungroupCanvasItems<T extends CanvasPositionedItem>(items: T[], groupIds: readonly string[]): T[] {
  const wanted = new Set(groupIds);
  const groups = new Map(items
    .filter((item) => wanted.has(item.id) && item.kind === "group")
    .map((item) => [item.id, item]));
  if (!groups.size) return items;
  return items.flatMap((item): T[] => {
    if (groups.has(item.id)) return [];
    const parent = item.parentId ? groups.get(item.parentId) : undefined;
    return parent ? [{ ...item, x: parent.x + item.x, y: parent.y + item.y, parentId: undefined }] : [item];
  });
}

export function groupCanvasCards(
  document: MaterialCanvasDocument,
  selectedIds: readonly string[],
  groupId: string,
  title = "组合",
): MaterialCanvasDocument {
  const cards = groupCanvasItems(document.cards, selectedIds, (bounds): CanvasCardRecord => ({
    id: groupId,
    kind: "group",
    title,
    adapter: "auto",
    ...bounds,
  }));
  return cards === document.cards ? document : { ...document, cards };
}

export function ungroupCanvasCards(document: MaterialCanvasDocument, groupIds: readonly string[]): MaterialCanvasDocument {
  const cards = ungroupCanvasItems(document.cards, groupIds);
  return cards === document.cards ? document : { ...document, cards };
}

export function setCanvasRelationPath(
  document: MaterialCanvasDocument,
  relationIds: readonly string[],
  path: CanvasRelationRecord["path"],
): MaterialCanvasDocument {
  const selected = new Set(relationIds);
  if (!document.relations.some((relation) => selected.has(relation.id) && relation.path !== path)) return document;
  return {
    ...document,
    relations: document.relations.map((relation) => selected.has(relation.id) ? { ...relation, path } : relation),
  };
}

/**
 * 让关系的 handle 与切片保持一致。切片在就以切片为准（handle 是派生缓存）；
 * 切片不在就沿用四边语义，不动。返回同一个对象表示"本来就一致"，便于调用方省掉写入。
 */
export function normalizeCanvasRelationEndpoints(relation: CanvasRelationRecord): CanvasRelationRecord {
  const wantSource = relation.sourceSlice ? canvasSliceHandleId(relation.sourceSlice) : relation.sourceHandle;
  const wantTarget = relation.targetSlice ? canvasSliceHandleId(relation.targetSlice) : relation.targetHandle;
  if (wantSource === relation.sourceHandle && wantTarget === relation.targetHandle) return relation;
  return {
    ...relation,
    ...(wantSource === undefined ? {} : { sourceHandle: wantSource }),
    ...(wantTarget === undefined ? {} : { targetHandle: wantTarget }),
  };
}

/**
 * 端点切片的稳定签名。用途是**判定自动布局有没有偷偷动过端点**：
 * 布局前后取签名对比，不相等即为布局越权（REQ-CANVAS-SLICE-PROTOCOL 判据 2）。
 */
export function canvasRelationSliceSignature(relations: readonly CanvasRelationRecord[]): string {
  const one = (slice: CanvasSliceRef | undefined) => (slice
    ? `${slice.kind}|${slice.representationId ?? ""}|${slice.ref}|${slice.occurrence ?? 0}`
    : "-");
  return [...relations]
    .sort((left, right) => left.id.localeCompare(right.id))
    .map((relation) => `${relation.id}>${one(relation.sourceSlice)}>${one(relation.targetSlice)}`)
    .join("\n");
}

/** 整张画布的关系端点校验；返回问题清单（空 = 通过）。 */
export function validateCanvasRelationSlices(relations: readonly CanvasRelationRecord[]): string[] {
  const problems: string[] = [];
  for (const relation of relations) {
    if (relation.sourceSlice) {
      problems.push(...validateCanvasSlice(relation.sourceSlice, `关系 ${relation.id} 的 sourceSlice`));
      const want = canvasSliceHandleId(relation.sourceSlice);
      if (relation.sourceHandle !== undefined && relation.sourceHandle !== want) {
        problems.push(`关系 ${relation.id}: sourceHandle "${relation.sourceHandle}" 与切片派生值 "${want}" 不一致（切片是权威）`);
      }
    }
    if (relation.targetSlice) {
      problems.push(...validateCanvasSlice(relation.targetSlice, `关系 ${relation.id} 的 targetSlice`));
      const want = canvasSliceHandleId(relation.targetSlice);
      if (relation.targetHandle !== undefined && relation.targetHandle !== want) {
        problems.push(`关系 ${relation.id}: targetHandle "${relation.targetHandle}" 与切片派生值 "${want}" 不一致（切片是权威）`);
      }
    }
  }
  return problems;
}

export function normalizeLink(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!/^https?:\/\//i.test(trimmed) && !trimmed.startsWith("/")) return null;
  try {
    const url = new URL(trimmed, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function adapterForLink(urlText: string): MaterialAdapter {
  const url = new URL(urlText);
  const path = url.pathname.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg|avif)$/.test(path)) return "image";
  if (/\.pdf$/.test(path)) return "pdf";
  if (/\.(mp3|wav|ogg|m4a|flac)$/.test(path)) return "audio";
  if (/\.(mp4|webm|mov|m4v)$/.test(path)) return "video";
  return url.origin === window.location.origin ? "web" : "link";
}

function finite(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function mojibakeScore(value: string): number {
  const markers = ["Ã", "Â", "â", "ð", "�"]
    .reduce((total, marker) => total + value.split(marker).length - 1, 0);
  const controls = [...value].filter((char) => {
    const code = char.charCodeAt(0);
    return code >= 0x80 && code <= 0x9f;
  }).length;
  return markers * 4 + controls * 3;
}

/** Defensive display repair; the server performs the canonical repair on write. */
export function repairDisplayText(value: string): string {
  let current = value;
  for (let pass = 0; pass < 3; pass += 1) {
    let candidate = "";
    try {
      const bytes = Uint8Array.from([...current], (char) => {
        const code = char.charCodeAt(0);
        if (code > 0xff) throw new Error("not latin-1");
        return code;
      });
      candidate = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      break;
    }
    if (candidate === current || mojibakeScore(candidate) >= mojibakeScore(current)) break;
    current = candidate;
  }
  return current;
}

function validSource(value: unknown): value is MaterialSource {
  if (!value || typeof value !== "object") return false;
  const source = value as Record<string, unknown>;
  if (source.kind === "file") {
    return typeof source.path === "string" && typeof source.token === "string";
  }
  if (source.kind === "link") return typeof source.url === "string";
  if (source.kind === "legacy-note") return typeof source.id === "string";
  return false;
}

function validFrozen(value: unknown): CanvasCardRecord["frozen"] {
  if (!value || typeof value !== "object") return undefined;
  const frozen = value as Record<string, unknown>;
  if (!validSource(frozen.from) || typeof frozen.fromAdapter !== "string") return undefined;
  return {
    capturedAt: typeof frozen.capturedAt === "string" ? frozen.capturedAt : "",
    from: frozen.from,
    fromAdapter: frozen.fromAdapter as MaterialAdapter,
  };
}

const SUBMISSION_STATES: SubmissionState[] = ["unreviewed", "seen", "fully-passed", "not-fully-passed"];

function optStr(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function optNum(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function optBool(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function optRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function optArray(value: unknown): unknown[] | undefined {
  return Array.isArray(value) ? value : undefined;
}

function validPushMark(value: unknown): SubmissionPushMark | undefined {
  const mark = optRecord(value);
  if (!mark || typeof mark.toUser !== "boolean") return undefined;
  return { toUser: mark.toUser, reason: optStr(mark.reason), at: optStr(mark.at) };
}

function validSubmission(value: unknown): CardSubmission | undefined {
  // 旧卡无 submission → undefined(未提审); 字段不全的残次 submission 丢棄不阻断整张卡。
  // 三个必填(state/submittedAt/submittedBy)缺一即整体丢弃; 其余字段类型不符只跳过该字段。
  if (!value || typeof value !== "object") return undefined;
  const sub = value as Record<string, unknown>;
  if (typeof sub.state !== "string" || !SUBMISSION_STATES.includes(sub.state as SubmissionState)) return undefined;
  if (typeof sub.submittedAt !== "string" || typeof sub.submittedBy !== "string") return undefined;
  return {
    state: sub.state as SubmissionState,
    submittedAt: sub.submittedAt,
    submittedBy: sub.submittedBy,
    seenAt: optStr(sub.seenAt),
    verdictAt: optStr(sub.verdictAt),
    verdictBy: optStr(sub.verdictBy),
    reason: optStr(sub.reason),
    // 审阅库单一真源字段: 逐字段透传, 不再重建成 6 字段白名单
    // (白名单曾把迁移写入的 tier/track/history/legacy 等静默剥掉, 丰富度全丢)。
    archived: optBool(sub.archived),
    updatedAt: optStr(sub.updatedAt),
    materialKind: optStr(sub.materialKind),
    tier: optStr(sub.tier),
    project: optStr(sub.project),
    track: optStr(sub.track),
    version: optNum(sub.version),
    versionFamily: optStr(sub.versionFamily),
    subjectId: optStr(sub.subjectId),
    subjectType: optStr(sub.subjectType),
    revision: optNum(sub.revision),
    sourcePlanId: optStr(sub.sourcePlanId),
    sourceSubagentId: optStr(sub.sourceSubagentId),
    annotationsAllowed: optBool(sub.annotationsAllowed),
    links: optRecord(sub.links),
    extra: optRecord(sub.extra),
    reviewContext: optRecord(sub.reviewContext),
    pushed: validPushMark(sub.pushed),
    annotations: optArray(sub.annotations),
    comments: optArray(sub.comments),
    history: optArray(sub.history),
    legacy: optRecord(sub.legacy),
  };
}

function validPositionLock(value: unknown): CanvasPositionLock | undefined {
  if (!value || typeof value !== "object") return undefined;
  const lock = value as Record<string, unknown>;
  if (typeof lock.locked !== "boolean") return undefined;
  return {
    locked: lock.locked,
    actor: typeof lock.actor === "string" ? lock.actor : undefined,
    reason: typeof lock.reason === "string" ? lock.reason : undefined,
    updatedAt: typeof lock.updatedAt === "string" ? lock.updatedAt : undefined,
  };
}

function validPositionSource(value: unknown): CanvasPositionSource | undefined {
  if (!value || typeof value !== "object") return undefined;
  const source = value as Record<string, unknown>;
  if (source.kind !== "seed" && source.kind !== "auto-layout" && source.kind !== "user") return undefined;
  return {
    kind: source.kind,
    actor: typeof source.actor === "string" ? source.actor : undefined,
    updatedAt: typeof source.updatedAt === "string" ? source.updatedAt : undefined,
  };
}

function validDisplayOverride(value: unknown): CanvasDisplayOverride | undefined {
  if (!value || typeof value !== "object") return undefined;
  const pluginId = (value as Record<string, unknown>).pluginId;
  return typeof pluginId === "string" && pluginId.trim() ? { pluginId: pluginId.trim() } : undefined;
}

function validWorkbenchHref(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const href = value.trim();
  if (!href || href.length > 2048) return undefined;
  if (/^(?:\/|\.\.?(?:\/|$))/.test(href)) return href;
  try {
    const parsed = new URL(href);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? href : undefined;
  } catch {
    return undefined;
  }
}

function validWorkbenchLink(value: unknown): CanvasWorkbenchLink | undefined {
  const link = optRecord(value);
  if (!link || typeof link.id !== "string" || !link.id.trim() || link.id.length > 80
    || typeof link.label !== "string" || !link.label.trim() || link.label.length > 80) return undefined;
  const href = validWorkbenchHref(link.href);
  if (!href) return undefined;
  const rawQuery = optRecord(link.query);
  const query = rawQuery ? Object.fromEntries(Object.entries(rawQuery)
    .filter(([key, item]) => Boolean(key.trim())
      && (typeof item === "string" || typeof item === "number" || typeof item === "boolean"))
    .slice(0, 32)) as Record<string, string | number | boolean> : undefined;
  const rawDisplay = optRecord(link.display);
  const placement = rawDisplay?.placement === "toolbar" || rawDisplay?.placement === "menu"
    || rawDisplay?.placement === "both" ? rawDisplay.placement : undefined;
  const target = rawDisplay?.target === "same" || rawDisplay?.target === "new" ? rawDisplay.target : undefined;
  const icon = rawDisplay?.icon === "map" || rawDisplay?.icon === "material"
    || rawDisplay?.icon === "external" ? rawDisplay.icon : undefined;
  const tone = rawDisplay?.tone === "primary" || rawDisplay?.tone === "default" ? rawDisplay.tone : undefined;
  const display: CanvasWorkbenchLinkDisplay | undefined = placement || target || icon || tone
    ? { placement, target, icon, tone } : undefined;
  return {
    id: link.id.trim(), label: link.label.trim(), href,
    ...(query && Object.keys(query).length ? { query } : {}),
    ...(display ? { display } : {}),
  };
}

function validCanvasBehavior(value: unknown): CanvasBehavior | undefined {
  const behavior = optRecord(value);
  if (!behavior) return undefined;
  const result: CanvasBehavior = {};
  const performance = optRecord(behavior.performance);
  if (performance) {
    const virtualization = performance.virtualization === "always" || performance.virtualization === "never"
      ? performance.virtualization : performance.virtualization === "auto" ? "auto" : undefined;
    const defaultRenderMode = performance.defaultRenderMode === "overview" || performance.defaultRenderMode === "detail"
      ? performance.defaultRenderMode : performance.defaultRenderMode === "auto" ? "auto" : undefined;
    const virtualizationThreshold = typeof performance.virtualizationThreshold === "number"
      && Number.isInteger(performance.virtualizationThreshold)
      ? Math.max(20, Math.min(10000, performance.virtualizationThreshold)) : undefined;
    const overviewMaxZoom = typeof performance.overviewMaxZoom === "number" && Number.isFinite(performance.overviewMaxZoom)
      ? Math.max(0.05, Math.min(2, performance.overviewMaxZoom)) : undefined;
    const detailMinPixels = typeof performance.detailMinPixels === "number" && Number.isFinite(performance.detailMinPixels)
      ? Math.max(32, Math.min(1200, performance.detailMinPixels)) : undefined;
    const normalized: CanvasPerformanceBehavior = {
      ...(virtualization ? { virtualization } : {}),
      ...(virtualizationThreshold !== undefined ? { virtualizationThreshold } : {}),
      ...(defaultRenderMode ? { defaultRenderMode } : {}),
      ...(overviewMaxZoom !== undefined ? { overviewMaxZoom } : {}),
      ...(detailMinPixels !== undefined ? { detailMinPixels } : {}),
    };
    if (Object.keys(normalized).length) result.performance = normalized;
  }
  if (Array.isArray(behavior.workbenchLinks)) {
    const links = behavior.workbenchLinks.flatMap((link): CanvasWorkbenchLink[] => {
      const normalized = validWorkbenchLink(link);
      return normalized ? [normalized] : [];
    }).slice(0, 16);
    const unique = links.filter((link, index) => links.findIndex((candidate) => candidate.id === link.id) === index);
    if (unique.length) result.workbenchLinks = unique;
  }
  if (Array.isArray(behavior.checks)) {
    const checks = behavior.checks.flatMap((item): CanvasConstraintSpec[] => {
      const check = optRecord(item);
      if (!check || typeof check.checker !== "string" || !check.checker.trim() || check.checker.length > 120) return [];
      return [{
        checker: check.checker.trim(),
        ...(check.severity === "warning" || check.severity === "error" ? { severity: check.severity } : {}),
        ...(optRecord(check.options) ? { options: structuredClone(check.options as Record<string, unknown>) } : {}),
      }];
    }).slice(0, 32);
    if (checks.length) result.checks = checks;
  }
  const folding = optRecord(behavior.folding);
  if (folding) {
    const relationKinds = uniqueStrings(folding.relationKinds, 32);
    if (typeof folding.enabled === "boolean" || relationKinds) result.folding = {
      ...(typeof folding.enabled === "boolean" ? { enabled: folding.enabled } : {}),
      ...(relationKinds ? { relationKinds } : {}),
    };
  }
  return Object.keys(result).length ? result : undefined;
}

function validCardFold(value: unknown): CanvasCardFold | undefined {
  const fold = optRecord(value);
  if (!fold) return undefined;
  const result: CanvasCardFold = {};
  if (typeof fold.defaultCollapsed === "boolean") result.defaultCollapsed = fold.defaultCollapsed;
  if (typeof fold.collapsed === "boolean") result.collapsed = fold.collapsed;
  const memberIds = uniqueStrings(fold.memberIds, 10000);
  if (memberIds) result.memberIds = memberIds;
  return Object.keys(result).length ? result : undefined;
}

function validSamePositionContainer(value: unknown): CanvasSamePositionContainer | undefined {
  const container = optRecord(value);
  if (!container || typeof container.dimension !== "string" || !container.dimension.trim()
    || typeof container.activeVariant !== "string" || !container.activeVariant.trim()
    || !Array.isArray(container.variants) || container.variants.length === 0) return undefined;
  const variants = container.variants.flatMap((item): CanvasSamePositionVariant[] => {
    const variant = optRecord(item);
    if (!variant || typeof variant.key !== "string" || !variant.key.trim()
      || typeof variant.label !== "string" || !variant.label.trim()) return [];
    const out: CanvasSamePositionVariant = { key: variant.key.trim(), label: variant.label.trim() };
    for (const field of ["status", "business", "owner", "created_at"] as const) {
      const text = variant[field];
      if (typeof text === "string" && text.trim() && text.length <= 200) out[field] = text.trim();
    }
    if (Array.isArray(variant.keywords)) {
      const keywords = variant.keywords.filter((item): item is string => typeof item === "string" && item.trim().length > 0).map((item) => item.trim().slice(0, 120)).slice(0, 24);
      if (keywords.length) out.keywords = keywords;
    }
    return [out];
  });
  if (variants.length !== container.variants.length) return undefined;
  const keys = new Set(variants.map((variant) => variant.key));
  if (keys.size !== variants.length || !keys.has(container.activeVariant.trim())) return undefined;
  return { dimension: container.dimension.trim(), variants, activeVariant: container.activeVariant.trim() };
}

function validSamePositionMember(value: unknown): CanvasSamePositionMember | undefined {
  const member = optRecord(value);
  if (!member || typeof member.slotId !== "string" || !member.slotId.trim()
    || typeof member.variantKey !== "string" || !member.variantKey.trim()) return undefined;
  return { slotId: member.slotId.trim(), variantKey: member.variantKey.trim() };
}

/** 间距档：未声明或非法一律 undefined（= 走 comfortable 默认），不猜。 */
function validSpacingProfile(value: unknown): CanvasSpacingProfile | undefined {
  return value === "compact" || value === "comfortable" ? value : undefined;
}

function uniqueStrings(value: unknown, limit = 256): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const strings = [...new Set(value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean))].slice(0, limit);
  return strings.length ? strings : undefined;
}

function validCanvasView(value: unknown): CanvasViewDefinition | undefined {
  if (!value || typeof value !== "object") return undefined;
  const candidate = value as Record<string, unknown>;
  const sourceValue = candidate.source;
  if (!sourceValue || typeof sourceValue !== "object") return undefined;
  const source = sourceValue as Record<string, unknown>;
  if ((source.kind !== "material-graph" && source.kind !== "canvas")
    || typeof source.ref !== "string" || !source.ref.trim()) return undefined;
  const result: CanvasViewDefinition = {
    source: { kind: source.kind, ref: source.ref.trim() },
  };
  if (candidate.selector && typeof candidate.selector === "object") {
    const selector = candidate.selector as Record<string, unknown>;
    const direction = selector.direction === "incoming" || selector.direction === "both" ? selector.direction : "outgoing";
    const maxDepth = typeof selector.maxDepth === "number" && Number.isInteger(selector.maxDepth)
      ? Math.max(0, Math.min(64, selector.maxDepth)) : undefined;
    const nodeLimit = typeof selector.nodeLimit === "number" && Number.isInteger(selector.nodeLimit)
      ? Math.max(1, Math.min(100000, selector.nodeLimit)) : undefined;
    result.selector = {
      ...(uniqueStrings(selector.rootRefs) ? { rootRefs: uniqueStrings(selector.rootRefs) } : {}),
      ...(uniqueStrings(selector.relationKinds, 32) ? { relationKinds: uniqueStrings(selector.relationKinds, 32) } : {}),
      direction,
      ...(maxDepth !== undefined ? { maxDepth } : {}),
      ...(nodeLimit !== undefined ? { nodeLimit } : {}),
      ...(typeof selector.includeFrontier === "boolean" ? { includeFrontier: selector.includeFrontier } : {}),
    };
  }
  if (candidate.narrative && typeof candidate.narrative === "object") {
    const narrative = candidate.narrative as Record<string, unknown>;
    const rawTitles = narrative.titleOverrides;
    const titleOverrides = rawTitles && typeof rawTitles === "object" && !Array.isArray(rawTitles)
      ? Object.fromEntries(Object.entries(rawTitles as Record<string, unknown>)
        .filter(([key, item]) => Boolean(key.trim()) && typeof item === "string" && Boolean(item.trim()))
        .slice(0, 256)
        .map(([key, item]) => [key.trim(), (item as string).trim()]))
      : undefined;
    result.narrative = {
      ...(uniqueStrings(narrative.hiddenCardIds) ? { hiddenCardIds: uniqueStrings(narrative.hiddenCardIds) } : {}),
      ...(uniqueStrings(narrative.cardOrder) ? { cardOrder: uniqueStrings(narrative.cardOrder) } : {}),
      ...(uniqueStrings(narrative.chapterOrder) ? { chapterOrder: uniqueStrings(narrative.chapterOrder) } : {}),
      ...(uniqueStrings(narrative.slideBreakBefore) ? { slideBreakBefore: uniqueStrings(narrative.slideBreakBefore) } : {}),
      ...(titleOverrides && Object.keys(titleOverrides).length ? { titleOverrides } : {}),
    };
  }
  return result;
}

/** 解析持久化里的外观；不合契约就整条丢弃（= 回到 panel + inside），不半信半疑地保留半份。 */
function validPresentation(value: unknown): CanvasCardPresentation | undefined {
  if (value === undefined || validateCanvasCardPresentation(value).length) return undefined;
  const presentation = value as CanvasCardPresentation;
  const out: CanvasCardPresentation = {};
  if (presentation.frame) out.frame = presentation.frame;
  if (presentation.title) out.title = presentation.title;
  if (presentation.emphasis) out.emphasis = presentation.emphasis;
  if (typeof presentation.titleMinZoom === "number") out.titleMinZoom = presentation.titleMinZoom;
  if (presentation.style) out.style = presentation.style.trim();
  if (presentation.state) out.state = presentation.state;
  return Object.keys(out).length ? out : undefined;
}

/** 解析持久化里的切片；任何一项不合契约就整条丢弃，不半信半疑地保留。 */
function validComment(value: unknown): CanvasCardComment | undefined {
  if (value === undefined || value === null) return undefined;
  if (validateCanvasComment(value).length) return undefined;
  const comment = value as CanvasCardComment;
  const subject = validSlice(comment.subject);
  if (!subject) return undefined;
  return {
    subject,
    stance: comment.stance,
    ...(typeof comment.authority === "string" && comment.authority.trim()
      ? { authority: comment.authority.trim() } : {}),
  };
}

/** 评论线程整块过校验；不合契约就整块丢弃。 */
function validCommentThread(value: unknown, selfId?: string): CanvasCommentThread | undefined {
  if (value === undefined || value === null) return undefined;
  if (validateCanvasCommentThread(value, selfId).length) return undefined;
  const thread = value as CanvasCommentThread;
  const targets = [...new Set(thread.targets.map((target) => target.trim()))];
  return {
    targets,
    root: thread.root.trim(),
    ...(thread.replyTo ? { replyTo: thread.replyTo.trim() } : {}),
    ...(thread.semantic ? { semantic: thread.semantic } : {}),
    ...(thread.promotedTo ? { promotedTo: thread.promotedTo.trim() } : {}),
  };
}

/** 溯源两头都得是合格身份；缺一头就整块丢弃。 */
function validProvenance(value: unknown): CanvasCardProvenance | undefined {
  if (!value || typeof value !== "object") return undefined;
  const provenance = value as Partial<CanvasCardProvenance>;
  const created = normalizeWriteIdentity(provenance.created);
  const updated = normalizeWriteIdentity(provenance.updated);
  if (!created || !updated) return undefined;
  return { created, updated };
}

function validDerivedFrom(value: unknown): CanvasCardDerivedFrom | undefined {
  if (!value || typeof value !== "object") return undefined;
  const derived = value as Partial<CanvasCardDerivedFrom>;
  if (!nonEmptyString(derived.card)) return undefined;
  if (typeof derived.revision !== "number" || !Number.isInteger(derived.revision) || derived.revision < 0) return undefined;
  if (!(CANVAS_DERIVED_MODES as readonly string[]).includes(String(derived.mode))) return undefined;
  return { card: derived.card.trim(), revision: derived.revision, mode: derived.mode as CanvasCardDerivedFrom["mode"] };
}

function validSlice(value: unknown): CanvasSliceRef | undefined {
  if (validateCanvasSlice(value).length) return undefined;
  const slice = value as CanvasSliceRef;
  const out: CanvasSliceRef = { kind: slice.kind, ref: slice.ref.trim() };
  if (slice.representationId) out.representationId = slice.representationId.trim();
  if (typeof slice.occurrence === "number" && slice.occurrence > 0) out.occurrence = slice.occurrence;
  if (slice.box) {
    out.box = {
      x: slice.box.x, y: slice.box.y, width: slice.box.width, height: slice.box.height,
    };
  }
  if (typeof slice.label === "string" && slice.label.trim()) out.label = slice.label.trim();
  // 响度不参与身份，但要过取值闭合：非法值丢弃，由 resolveSliceEmphasis 落到 quiet。
  if (isCanvasSliceEmphasis(slice.emphasis)) out.emphasis = slice.emphasis;
  return out;
}

function validLabelPosition(value: unknown): CanvasLabelPosition | undefined {
  if (!value || typeof value !== "object") return undefined;
  const position = value as Record<string, unknown>;
  if (typeof position.t !== "number" || !Number.isFinite(position.t)) return undefined;
  return {
    t: Math.min(1, Math.max(0, position.t)),
    offsetX: typeof position.offsetX === "number" && Number.isFinite(position.offsetX) ? position.offsetX : undefined,
    offsetY: typeof position.offsetY === "number" && Number.isFinite(position.offsetY) ? position.offsetY : undefined,
  };
}

export function parseCanvasDocument(raw: string | null, sessionId: string): MaterialCanvasDocument | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<MaterialCanvasDocument>;
    if (value.version !== 1 || !Array.isArray(value.cards) || !Array.isArray(value.relations)) return null;
    const cards = value.cards.flatMap((candidate): CanvasCardRecord[] => {
      if (!candidate || typeof candidate !== "object" || typeof candidate.id !== "string") return [];
      // group=编组容器卡(无 source); 未知/缺省 kind 回退 text(旧文档没有 group)
      const kind: CardKind = candidate.kind === "material" ? "material"
        : candidate.kind === "group" ? "group"
        : candidate.kind === "comment" ? "comment" : "text";
      if (kind === "material" && !validSource(candidate.source)) return [];
      if (kind === "text" && !validSource(candidate.source)) return [];
      // 评论卡必须说清评的是谁；线程不合契约整张丢弃（未声明即拒绝）。
      const commentThread = kind === "comment" ? validCommentThread(candidate.commentThread, candidate.id) : undefined;
      if (kind === "comment" && !commentThread) return [];
      return [{
        id: candidate.id,
        kind,
        title: typeof candidate.title === "string" ? repairDisplayText(candidate.title) : "未命名卡片",
        text: typeof candidate.text === "string" ? candidate.text : undefined,
        material: candidate.material && typeof candidate.material === "object" && !Array.isArray(candidate.material)
          ? candidate.material as Record<string, unknown> : undefined,
        source: validSource(candidate.source) ? candidate.source : undefined,
        adapter: typeof candidate.adapter === "string" ? candidate.adapter as MaterialAdapter : "auto",
        frozen: validFrozen(candidate.frozen),
        parentId: typeof candidate.parentId === "string" ? candidate.parentId : undefined,
        order: typeof candidate.order === "number" && Number.isFinite(candidate.order) ? candidate.order : undefined,
        submission: validSubmission(candidate.submission),
        positionLock: validPositionLock(candidate.positionLock),
        positionSource: validPositionSource(candidate.positionSource),
        display: validDisplayOverride(candidate.display),
        fold: validCardFold(candidate.fold),
        samePositionContainer: kind === "group" ? validSamePositionContainer(candidate.samePositionContainer) : undefined,
        samePositionMember: validSamePositionMember(candidate.samePositionMember),
        presentation: validPresentation(candidate.presentation),
        // 备注块整块过校验；任何一项不合契约就整块丢弃，不留半份（未声明即拒绝）。
        comment: validComment(candidate.comment),
        ...(commentThread ? { commentThread } : {}),
        provenance: validProvenance(candidate.provenance),
        derivedFrom: validDerivedFrom(candidate.derivedFrom),
        x: finite(candidate.x, 80),
        y: finite(candidate.y, 72),
        // 下限从 260x180 降到 40x40（2026-08-20）：按材料固有比例定尺后，
        // 一张 9:16 界面截图的合理卡宽约 230px，被 260 夹一下比例就从 0.5625 变成 0.637，
        // 偏差 13% —— 业主要的"图片有尺寸就照尺寸、不留缺口"会在每次读盘时被悄悄撤销。
        // 缺值仍回落 440x320；这里只是不再篡改已经写明的小尺寸。
        width: Math.max(40, finite(candidate.width, 440)),
        height: Math.max(40, finite(candidate.height, 320)),
        fontSize: typeof candidate.fontSize === "number"
          ? Math.min(72, Math.max(8, candidate.fontSize)) : undefined,
      }];
    });
    const ids = new Set(cards.map((card) => card.id));
    const relations = value.relations.flatMap((candidate): CanvasRelationRecord[] => {
      if (!candidate || typeof candidate !== "object") return [];
      if (typeof candidate.id !== "string" || typeof candidate.source !== "string" || typeof candidate.target !== "string") return [];
      if (!ids.has(candidate.source) || !ids.has(candidate.target)) return [];
      return [{
        id: candidate.id,
        source: candidate.source,
        target: candidate.target,
        programLabel: typeof candidate.programLabel === "string" ? repairDisplayText(candidate.programLabel) : undefined,
        userLabel: typeof candidate.userLabel === "string" ? repairDisplayText(candidate.userLabel) : undefined,
        // Legacy routed/control-point geometry is intentionally discarded. Relations are
        // rendered from their semantic endpoints with one uniform Bézier implementation.
        programLabelPosition: validLabelPosition(candidate.programLabelPosition),
        userLabelPosition: validLabelPosition(candidate.userLabelPosition),
        label: typeof candidate.label === "string" ? repairDisplayText(candidate.label) : undefined,
        /*
         * kind 取封闭集内的值；transitions-to 缺 trigger 时**丢掉 kind**（回到"未定"），
         * 既不伪造一个 trigger，也不把它偷偷改成 continues —— 那是两种不同的语义，
         * 猜哪一种都是编。丢掉之后 validateCanvasRelationSemantics 也就不会再报它，
         * 真正的把关在写入方（生成器与交互层）。
         */
        kind: isCanvasRelationKind(candidate.kind)
          && (candidate.kind !== "transitions-to"
            || (typeof candidate.trigger === "string" && candidate.trigger.trim().length > 0))
          ? candidate.kind : undefined,
        trigger: typeof candidate.trigger === "string" && candidate.trigger.trim()
          ? candidate.trigger.trim() : undefined,
        // 未声明即缺省(由 kind 派生)，声明了但不在三值内即丢弃 —— 不做兜底猜测。
        layer: isCanvasRelationLayer(candidate.layer) ? candidate.layer : undefined,
        sourceSlice: validSlice(candidate.sourceSlice),
        targetSlice: validSlice(candidate.targetSlice),
        sourceHandle: typeof candidate.sourceHandle === "string" ? candidate.sourceHandle : undefined,
        targetHandle: typeof candidate.targetHandle === "string" ? candidate.targetHandle : undefined,
        path: "curve",
        arrow: candidate.arrow === "open" || candidate.arrow === "none" ? candidate.arrow : "closed",
      }];
    }).map(normalizeCanvasRelationEndpoints);
    return {
      version: 1,
      sessionId,
      title: typeof value.title === "string" && value.title.trim() ? repairDisplayText(value.title) : undefined,
      catalog: value.catalog && typeof value.catalog === "object" ? normalizeCanvasCatalog(value.catalog) : undefined,
      revision: typeof value.revision === "number" && Number.isInteger(value.revision) && value.revision >= 0
        ? value.revision : 0,
      updatedAt: typeof value.updatedAt === "string" ? value.updatedAt : new Date(0).toISOString(),
      lastMutation: value.lastMutation && typeof value.lastMutation === "object"
        && typeof value.lastMutation.id === "string"
        && typeof value.lastMutation.actor === "string"
        && typeof value.lastMutation.sequence === "number"
        && typeof value.lastMutation.at === "string"
        ? value.lastMutation : undefined,
      display: validDisplayOverride(value.display),
      behavior: validCanvasBehavior(value.behavior),
      spacing: validSpacingProfile(value.spacing),
      view: validCanvasView(value.view),
      cards,
      relations,
    };
  } catch {
    return null;
  }
}

/**
 * 稳定序列化：对象键按名排序，数组顺序保留（数组顺序在我们的模型里是有意义的）。
 *
 * 为什么不能直接 JSON.stringify 比：**它对键顺序敏感**。基线那份文档出自
 * `parseCanvasDocument`（键序 = 解析器字面量的顺序），待写那份出自 `persistedDocument`
 * （键序 = 它自己字面量的顺序），两个顺序本来就不一样 —— 于是"内容完全相同"的关系
 * 每次都被判成改过。实测后果：**每打开一次板子就写掉 41 条关系、版本 +2**，
 * 而落库内容一个字段都没变（diff 工具逐字段比过：变化 0/41）。
 * 这种扰动还会放大成 409：两次写抢同一个 base_revision。
 */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
  return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`).join(",")}}`;
}

function equalValue(left: unknown, right: unknown): boolean {
  return stableStringify(left) === stableStringify(right);
}

export function emptyCanvasDocument(sessionId: string): MaterialCanvasDocument {
  return {
    version: 1,
    sessionId,
    revision: 0,
    updatedAt: new Date(0).toISOString(),
    cards: [],
    relations: [],
  };
}

/** Compute an entity-addressed patch; timestamps never participate in the diff. */
export function diffCanvasDocuments(
  base: MaterialCanvasDocument,
  next: MaterialCanvasDocument,
): CanvasMutationPatch {
  const patch: CanvasMutationPatch = {};
  const baseTitle = base.title || "";
  const nextTitle = next.title || "";
  if (baseTitle !== nextTitle) {
    patch.set_title = true;
    patch.title = nextTitle || null;
  }
  if (!equalValue(base.catalog, next.catalog)) {
    patch.set_catalog = true;
    patch.catalog = next.catalog ? normalizeCanvasCatalog(next.catalog) : null;
  }
  if (!equalValue(base.display, next.display)) {
    patch.set_display = true;
    patch.display = next.display ? validDisplayOverride(next.display) || null : null;
  }
  if (base.spacing !== next.spacing) {
    patch.set_spacing = true;
    patch.spacing = validSpacingProfile(next.spacing);
  }
  if (!equalValue(base.behavior, next.behavior)) {
    patch.set_behavior = true;
    patch.behavior = next.behavior ? validCanvasBehavior(next.behavior) || null : null;
  }
  if (!equalValue(base.view, next.view)) {
    patch.set_view = true;
    patch.view = next.view ? validCanvasView(next.view) || null : null;
  }
  const baseCards = new Map(base.cards.map((card) => [card.id, card]));
  const nextCards = new Map(next.cards.map((card) => [card.id, card]));
  const upsertCards = next.cards.filter((card) => !equalValue(baseCards.get(card.id), card));
  const removeCardIds = base.cards.filter((card) => !nextCards.has(card.id)).map((card) => card.id);
  if (upsertCards.length) patch.upsert_cards = upsertCards;
  if (removeCardIds.length) patch.remove_card_ids = removeCardIds;
  const baseRelations = new Map(base.relations.map((relation) => [relation.id, relation]));
  const nextRelations = new Map(next.relations.map((relation) => [relation.id, relation]));
  const upsertRelations = next.relations.filter((relation) => !equalValue(baseRelations.get(relation.id), relation));
  const removeRelationIds = base.relations
    .filter((relation) => !nextRelations.has(relation.id))
    .map((relation) => relation.id);
  if (upsertRelations.length) patch.upsert_relations = upsertRelations;
  if (removeRelationIds.length) patch.remove_relation_ids = removeRelationIds;
  return patch;
}

export function canvasPatchIsEmpty(patch: CanvasMutationPatch): boolean {
  return !patch.set_title
    && !patch.set_catalog
    && !patch.set_display
    && !patch.set_behavior
    && !patch.set_spacing
    && !patch.set_view
    && !(patch.upsert_cards?.length)
    && !(patch.remove_card_ids?.length)
    && !(patch.upsert_relations?.length)
    && !(patch.remove_relation_ids?.length);
}

/** Apply a local pending patch over a newer canonical document after an in-flight save. */
export function applyCanvasMutation(
  document: MaterialCanvasDocument,
  patch: CanvasMutationPatch,
): MaterialCanvasDocument {
  const cards = new Map(document.cards.map((card) => [card.id, card]));
  for (const id of patch.remove_card_ids || []) cards.delete(id);
  for (const card of patch.upsert_cards || []) cards.set(card.id, card);
  const relations = new Map(document.relations.map((relation) => [relation.id, relation]));
  for (const id of patch.remove_relation_ids || []) relations.delete(id);
  for (const relation of patch.upsert_relations || []) {
    const legacy = relation as CanvasRelationRecord & { routePoints?: unknown; curveControl?: unknown };
    const { routePoints: _routePoints, curveControl: _curveControl, ...semanticRelation } = legacy;
    relations.set(relation.id, { ...semanticRelation, path: "curve" });
  }
  for (const [id, relation] of relations) {
    if (!cards.has(relation.source) || !cards.has(relation.target)) relations.delete(id);
  }
  const next = {
    ...document,
    cards: [...cards.values()],
    relations: [...relations.values()],
  };
  if (patch.set_title) {
    if (patch.title) next.title = patch.title;
    else delete next.title;
  }
  if (patch.set_catalog) {
    if (patch.catalog) next.catalog = normalizeCanvasCatalog(patch.catalog);
    else delete next.catalog;
  }
  if (patch.set_display) {
    const display = validDisplayOverride(patch.display);
    if (display) next.display = display;
    else delete next.display;
  }
  if (patch.set_spacing) {
    const spacing = validSpacingProfile(patch.spacing);
    if (spacing) next.spacing = spacing;
    else delete next.spacing;
  }
  if (patch.set_behavior) {
    const behavior = validCanvasBehavior(patch.behavior);
    if (behavior) next.behavior = behavior;
    else delete next.behavior;
  }
  if (patch.set_view) {
    const view = validCanvasView(patch.view);
    if (view) next.view = view;
    else delete next.view;
  }
  // 切片是端点权威：任何一次 patch 落地后都把 handle 对齐回切片，
  // 免得某个调用方只改了 handle 就让"从这个按钮出发"悄悄失配。
  next.relations = next.relations.map(normalizeCanvasRelationEndpoints);
  return next;
}

/* ────────────────────────────────────────────────────────────────────────────
 * 注释卡的落位算子：让每条注释挨着它注释的那个元素
 * ──────────────────────────────────────────────────────────────────────────── */

export interface AnchoredNote {
  id: string;
  height: number;
  /** 它注释的那个元素在**组坐标系**里的位置（不是在自己那张状态卡里的相对位置）。 */
  anchor: { x: number; y: number };
}

export interface NotePlacement {
  id: string;
  side: "left" | "right";
  x: number;
  y: number;
}

export interface NoteColumnLayout {
  placements: NotePlacement[];
  leftWidth: number;
  rightWidth: number;
  /** 两列各自撑出来的高度，供组框定尺。 */
  columnHeight: number;
}

/**
 * **保序回归（PAVA）**：在"不许重叠、且保持给定顺序"的约束下，
 * 让每张卡尽量停在它自己的目标高度上（最小化位移平方和）。
 *
 * 这是分层布图里那一步纵坐标指派的标准解法，不是手调：
 *   目标 minimize Σ (y_i - t_i)²  s.t.  y_{i+1} ≥ y_i + h_i + gap
 *   换元 z_i = y_i − Σ_{k<i}(h_k+gap) 之后约束变成"z 单调不减"，
 *   于是就是等权保序回归，pool-adjacent-violators 一趟扫完，精确最优。
 *
 * 手动摆的做法（从上往下依次堆）等价于"全都顶到最上面"，
 * 与锚点高度无关 —— 那正是业主说的"各个地方错位"。
 */
export function isotonicPositions(
  targets: readonly number[],
  heights: readonly number[],
  gap: number,
  floor: number,
): number[] {
  const offsets: number[] = [];
  let running = 0;
  for (let index = 0; index < targets.length; index += 1) {
    offsets.push(running);
    running += heights[index]! + gap;
  }
  // 换元后的目标值与下界
  const shifted = targets.map((target, index) => target - offsets[index]!);
  const lowest = floor;

  // PAVA：相邻违约就并块，块内取加权平均
  const blocks: { value: number; weight: number }[] = [];
  for (const value of shifted) {
    blocks.push({ value, weight: 1 });
    while (blocks.length > 1 && blocks[blocks.length - 2]!.value > blocks[blocks.length - 1]!.value) {
      const right = blocks.pop()!;
      const left = blocks.pop()!;
      const weight = left.weight + right.weight;
      blocks.push({ value: (left.value * left.weight + right.value * right.weight) / weight, weight });
    }
  }
  const solved: number[] = [];
  for (const block of blocks) {
    for (let repeat = 0; repeat < block.weight; repeat += 1) solved.push(block.value);
  }
  // 换回来，并整体抬到下界之上（保持相对间距，不破坏最优性）
  const lift = Math.max(0, lowest - Math.min(...solved));
  return solved.map((value, index) => value + lift + offsets[index]!);
}

/**
 * 把注释放进**离它锚点最近的那条槽**，并给出纵坐标。
 *
 * 业主原话：「之前的排布其实很不合理，每个注释都没有选择靠近自己所注释的位置的位置，
 * 导致各个地方错位，左边注释右边，右边注释左边，我们需要智能排布算法能管这个，
 * 你自己也能局部应用（各种）智能算法而不是一个个手动输入坐标」。
 *
 * 槽位不是"左右两列"，而是 n 张状态卡切出的 **n+1 条缝**：最左、每两张卡之间、最右。
 * 只有两列外挂时，锚点落在卡内侧的注释无论去哪一列都要横穿整张界面图
 * （实测 18 条里 5 条这样）；开中间那条缝，它就能贴着自己那张卡站。
 *
 * 三步都是有名字的做法，不是拍坐标：
 *   1. **定槽**：锚点离哪条缝最近就进哪条（缝的位置 = 卡与卡的边界）；
 *   2. **定尺**：只有真被用到的缝才占宽度，状态卡按用到的缝依次右移；
 *   3. **定高**：同槽按锚点纵序排（保序＝连线不交叉），再用保序回归求纵坐标，
 *      每张卡尽量停在自己锚点的高度上。
 */
export function placeNotesInGutters(input: {
  notes: readonly AnchoredNote[];
  /** 状态卡，按从左到右的顺序，x/width 用"还没为缝让位"的坐标。 */
  states: readonly { id: string; x: number; width: number }[];
  contentTop: number;
  columnWidth: number;
  gap: number;
  padding: number;
}): {
  placements: NotePlacement[];
  /** 每张状态卡要右移多少（下标与 states 对齐）。 */
  stateShift: number[];
  /** 组的内容宽度（含被用到的缝）。 */
  contentWidth: number;
  columnHeight: number;
} {
  const { notes, states, contentTop, columnWidth, gap, padding } = input;
  // 缝的边界位置：第 i 条缝在 states[i] 的左边；最后一条在最后一张卡的右边
  const boundaries = [
    ...states.map((state) => state.x),
    states.length ? states[states.length - 1]!.x + states[states.length - 1]!.width : padding,
  ];
  const gutterOf = (anchorX: number) => {
    let best = 0;
    for (let index = 1; index < boundaries.length; index += 1) {
      if (Math.abs(boundaries[index]! - anchorX) < Math.abs(boundaries[best]! - anchorX)) best = index;
    }
    return best;
  };

  const byGutter = new Map<number, AnchoredNote[]>();
  for (const note of notes) {
    const gutter = gutterOf(note.anchor.x);
    byGutter.set(gutter, [...(byGutter.get(gutter) ?? []), note]);
  }

  // 第 2 步：从左到右铺一遍 —— 用到的缝占一列宽，然后是那张状态卡
  const gutterX = new Map<number, number>();
  const stateX: number[] = [];
  let cursor = padding;
  for (let index = 0; index <= states.length; index += 1) {
    if (byGutter.has(index)) {
      gutterX.set(index, cursor);
      cursor += columnWidth + gap;
    }
    if (index < states.length) {
      stateX.push(cursor);
      cursor += states[index]!.width + gap;
    }
  }
  const contentWidth = cursor - gap - padding;
  const stateShift = states.map((state, index) => stateX[index]! - state.x);

  // 第 3 步：每条缝内按锚点纵序 + 保序回归
  const placements: NotePlacement[] = [];
  let columnHeight = 0;
  for (const [gutter, list] of byGutter) {
    const ordered = [...list].sort((left, right) => left.anchor.y - right.anchor.y);
    const positions = isotonicPositions(
      ordered.map((note) => note.anchor.y - note.height / 2),
      ordered.map((note) => note.height),
      gap,
      contentTop,
    );
    for (const [index, note] of ordered.entries()) {
      placements.push({
        id: note.id,
        // side 只是留给旧消费方的粗分类：在第一条缝算"左"，其余算"右"
        side: gutter === 0 ? 'left' : 'right',
        x: gutterX.get(gutter)!,
        y: positions[index]!,
      });
      columnHeight = Math.max(columnHeight, positions[index]! + note.height);
    }
  }
  return { placements, stateShift, contentWidth, columnHeight };
}

/**
 * 把注释分到组框左右两列，并给出纵坐标。
 *
 * 业主原话：「之前的排布其实很不合理，每个注释都没有选择靠近自己所注释的位置的位置，
 * 导致各个地方错位，左边注释右边，右边注释左边，我们需要智能排布算法能管这个」。
 *
 * 三步，每步都是有名字的做法，不是拍坐标：
 *   1. **定边**：锚点在组内容区中线的哪一侧，就去哪一列。
 *      关键是用**组坐标**里的 x —— 之前拿"元素在自己那张状态卡里的相对位置"定边，
 *      于是右边那张状态卡上偏左的元素被判成"去左列"，线横穿整个组，就是那句"错位"。
 *   2. **均衡**：一列排不下（比另一列高出一整张卡）时，把**站边最弱**的那张
 *      （锚点离中线最近的）挪去另一列。弱的先让，铁定靠边的那些不动。
 *   3. **定高**：同侧按锚点 y 排序（保序=连线不交叉），再用保序回归求纵坐标，
 *      每张卡尽量停在自己锚点的高度上。
 */
export function placeNotesInColumns(input: {
  notes: readonly AnchoredNote[];
  /** 组内容区（状态卡占的那块）的左右边界与上沿，组坐标。 */
  content: { left: number; right: number; top: number };
  columnWidth: number;
  gap: number;
  padding: number;
}): NoteColumnLayout {
  const { notes, content, columnWidth, gap, padding } = input;
  const centre = (content.left + content.right) / 2;
  const sided = notes.map((note) => ({
    note,
    side: note.anchor.x < centre ? ("left" as const) : ("right" as const),
    strength: Math.abs(note.anchor.x - centre),
  }));

  /*
   * 第 2 步：**不做均衡**。
   *
   * 试过"哪列矮就往哪列塞"，两个门槛都试了（半宽的 35%、更严的）——
   * 只要它有权搬动，就一定会把某些注释搬到锚点的对侧：实测 18 条里 3 条错边。
   * 业主给过优先级：「左边注释右边，右边注释左边」是要修的病；
   * 两列不齐不是病（纵坐标本来就跟着锚点走，本来就不该齐）。
   * 所以这一步删掉，边只由锚点定。要是哪天真需要均衡，它得先保证不改变任何一条的边。
   */

  const placements: NotePlacement[] = [];
  let columnHeight = 0;
  for (const side of ["left", "right"] as const) {
    const column = sided
      .filter((item) => item.side === side)
      .sort((left, right) => left.note.anchor.y - right.note.anchor.y);
    if (!column.length) continue;
    const x = side === "left" ? padding : content.right + gap;
    const positions = isotonicPositions(
      // 目标：卡片**中心**对齐锚点高度，所以目标 y = 锚点 y − 卡高/2
      column.map((item) => item.note.anchor.y - item.note.height / 2),
      column.map((item) => item.note.height),
      gap,
      content.top,
    );
    for (const [index, item] of column.entries()) {
      placements.push({ id: item.note.id, side, x, y: positions[index]! });
      columnHeight = Math.max(columnHeight, positions[index]! + item.note.height);
    }
  }
  const usedLeft = placements.some((item) => item.side === "left");
  const usedRight = placements.some((item) => item.side === "right");
  return {
    placements,
    leftWidth: usedLeft ? columnWidth : 0,
    rightWidth: usedRight ? columnWidth : 0,
    columnHeight,
  };
}

/** 审阅队列共享件（2026-08-14 抽出）：札记画布审阅面与 dashboard 审阅台同源消费，别抄一份。
 *  含：全模态预览缩略 ReviewThumb、二值裁决条 SubmissionVerdictBar、跨画布裁决写回
 *  verdictCardOnCanvas（head 取 revision → 读画布改卡 → mutate；写失败重取重试一次）。 */
import { useEffect, useState } from "react";
import {
  safeRandomId,
  type CardSubmission,
} from "./materialCanvasModel";
import { renderMarkdownSafe } from "../lib/sanitizeHtml";
import "./reviewShared.css";

export interface CanvasHead {
  revision: number;
  updatedAt?: string;
  lastMutation?: { id: string; actor: string; sequence: number; at: string };
}

export interface CanvasMutationResult extends CanvasHead {
  json: string;
  merged: boolean;
  duplicate: boolean;
}

/** notes 桥 invoke 的最小形状(webNotesInvoke / MaterialNotesWorkspace 的 invokeCommand 都满足)。 */
export type NotesInvoke = <T>(
  command: string,
  args?: Record<string, unknown>,
  timeoutMs?: number,
) => Promise<T>;

export function streamUrl(token: string): string {
  // 桥前缀同源(webNotesInvoke bridge_prefix 约定): workshop /bside 等反代宿主下
  // 文件流也必须落回宿主自己的 /lofa 路由, 而不是打到网关根。
  const candidate = new URLSearchParams(typeof location === "undefined" ? "" : location.search).get("bridge_prefix") ?? "";
  const prefix = /^\/(?:[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*)?$/.test(candidate) && !candidate.split("/").includes("..")
    ? candidate.replace(/\/$/, "")
    : "";
  return `${prefix}/lofa/overlay/file/${encodeURIComponent(token)}`;
}

/** 审阅队列项(M1b): 本会话与全局统一形状; canvasKey 区分归属, 外部画布项走新 tab 深链跳回。 */
export interface ReviewItemData {
  key: string;
  cardId: string;
  canvasKey: string;
  sessionId: string;
  sessionLabel: string;
  canvasTitle: string;
  title: string;
  kind: string;
  adapter: string;
  mime: string;
  /** 文件卡读取令牌(image/audio/video/pdf 内联预览用); 文本卡为 null */
  fileToken: string | null;
  /** web 链接卡(source.kind=link)的活 URL; 结构化网页材料本体(如 EP2 网页影片) */
  url: string | null;
  /** 文本卡正文材料 id(内联摘要预取用) */
  editableId: string | null;
  submission: CardSubmission;
  /** 画布所属域(canvas-domain 架构, 2026-08-16): builtin/workshop...; 缺省按 workshop 处理。 */
  domain?: string;
}

/** 全模态内联预览(M1b 定稿): image/audio/video/pdf 直接渲, 文本预取摘要, 其它给类型签。
 *  variant="thumb"(默认, 列表小图) / "full"(2026-08-14: 详情窗口主角位, 全文/全尺寸)。 */
export function ReviewThumb({ item, fetchText, variant = "thumb" }: {
  item: ReviewItemData;
  fetchText: (id: string) => Promise<string>;
  variant?: "thumb" | "full";
}) {
  const full = variant === "full";
  const [snippet, setSnippet] = useState<string | null>(null);
  // 换目标必须重取: 组件实例在列表/详情间复用, snippet!==null 守卫会拦住新目标的取文
  // (2026-08-16 审阅台串台实测) —— 先按目标键清空, 守卫只负责同一目标去重。
  useEffect(() => { setSnippet(null) }, [item.editableId])
  useEffect(() => {
    if (item.kind !== "text" || !item.editableId || snippet !== null) return;
    let live = true;
    void fetchText(item.editableId)
      .then((text) => {
        if (!live) return;
        const trimmed = text.trim();
        setSnippet(full ? (trimmed || "（空）") : (trimmed.slice(0, 160) || "（空）"));
      })
      .catch(() => { if (live) setSnippet("（读取失败）"); });
    return () => { live = false; };
  }, [item.kind, item.editableId, fetchText, snippet, full]);
  if (item.kind === "text") {
    // full(审阅台详情/文档主角位): 正文是 Markdown(omni review submit 的 text 卡 mime=text/markdown),
    // 按画布卡同一条消毒链渲成预览, 不再裸 <span> 出原文 (2026-09-01 用户反馈"审阅台 markdown 没按预览显示")。
    // thumb(列表缩略): 仍用纯文本摘要, 避免列表里渲标题/表格撑高。
    if (full && snippet !== null) {
      return (
        <div
          className="material-review-snippet material-markdown-preview"
          data-variant="full"
          data-material-renderer="material-note-markdown"
          dangerouslySetInnerHTML={{ __html: renderMarkdownSafe(snippet) }}
        />
      );
    }
    return <span className="material-review-snippet" data-variant={variant}>{snippet ?? "读取…"}</span>;
  }
  // web 卡本体有两种: 文件卡(text/html, 走 token 流)与链接卡(link, 走活 URL);
  // 只认 token 会让链接卡预览成"材料"占位——链接卡没有 token, 必须回落到 url。
  const src = item.fileToken ? streamUrl(item.fileToken)
    : (item.adapter === "web" && item.url ? item.url : null);
  if (!src) return <span className="material-review-noimg">材料</span>;
  if (item.adapter === "image") return <img data-variant={variant} src={src} alt={item.title} />;
  if (item.adapter === "audio") return <audio data-variant={variant} src={src} controls />;
  if (item.adapter === "video") return <video data-variant={variant} src={src} controls />;
  if (item.adapter === "web") {
    // web 卡预览的是"结构化网页"材料本体(如 EP2 网页影片审阅索引/链接页), 需要脚本与跳出能力:
    // 旗标与 MaterialViews 的 html 视图同口径(allow-same-origin+allow-scripts+popups 可逃逸沙箱,
    // 弹出的新页不继承沙箱)。sandbox="" 会让目标页脚本被禁照样白屏(2026-08-18 EP2 送审实测)。
    return <iframe data-variant={variant} src={src} title={item.title}
      sandbox="allow-same-origin allow-scripts allow-popups allow-popups-to-escape-sandbox" />;
  }
  if (item.adapter === "pdf") return <iframe data-variant={variant} src={src} title={item.title} sandbox="" />;
  return <span className="material-review-noimg">{item.mime || "材料"}</span>;
}

/** 二值裁决条: 一次点击即裁决完 —— 「完全通过 / 未完全通过」两个终态钮, 没有第二步。
 *  未通过的理由**不由人填**(用户裁决: 理由由伴随 agent 维护, 受控词表见
 *  SUBMISSION_FAIL_REASONS, agent 写进 submission.reason)。已裁决态只读显示结论 + 理由。 */
export function SubmissionVerdictBar({ submission, onVerdict }: {
  submission: CardSubmission;
  onVerdict: (pass: boolean, reason?: string) => void;
}) {
  if (submission.state === "fully-passed" || submission.state === "not-fully-passed") {
    return (
      <div className={`submission-verdict done ${submission.state}`}>
        <span>{submission.state === "fully-passed" ? "✓ 已通过" : "✗ 未通过"}</span>
        {submission.reason ? <small>{submission.reason}</small> : null}
        {submission.verdictAt ? <small>{submission.verdictAt.slice(0, 10)}</small> : null}
      </div>
    );
  }
  // 2026-08-16 用户裁定: 压缩为勾/叉图标钮(title/aria 保语义), 不再占整行文字条。
  return (
    <div className="submission-verdict compact" onPointerDown={(event) => event.stopPropagation()}>
      <button type="button" className="pass" onClick={() => onVerdict(true)} title="完全通过" aria-label="完全通过">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
      </button>
      <button type="button" className="fail" onClick={() => onVerdict(false)} title="未完全通过" aria-label="未完全通过">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>
      </button>
    </div>
  );
}

/** 跨画布裁决写回: 对【该卡所属画布】head 取 revision → 读画布改卡 submission → mutate。
 *  写失败(409 真冲突等)整链重取重试一次; 仍败抛首次错误。 */
export async function verdictCardOnCanvas(
  invoke: NotesInvoke,
  item: ReviewItemData,
  pass: boolean,
  reason: string | undefined,
  actor: string,
): Promise<void> {
  const writeOnce = async () => {
    const head = await invoke<CanvasHead | null>("notes_canvas_head", { id: item.canvasKey });
    const raw = await invoke<string | null>("notes_canvas_get", { id: item.canvasKey });
    const doc = raw ? JSON.parse(raw) as Record<string, unknown> : null;
    const cards = (doc?.cards ?? []) as Record<string, unknown>[];
    const card = cards.find((entry) => entry.id === item.cardId);
    if (!doc || !card || typeof card.submission !== "object" || !card.submission) {
      throw new Error(`目标卡或提审记录不存在: ${item.canvasKey}/${item.cardId}`);
    }
    card.submission = {
      ...(card.submission as Record<string, unknown>),
      state: pass ? "fully-passed" : "not-fully-passed",
      verdictAt: new Date().toISOString(),
      verdictBy: actor,
      ...(reason ? { reason } : {}),
    };
    const sessionId = typeof doc.sessionId === "string" ? doc.sessionId : `external:${item.sessionId}`;
    await invoke("notes_canvas_mutate", {
      id: item.canvasKey,
      session_id: sessionId,
      mutation_id: safeRandomId("web-review-"),
      actor,
      workspace_ref: { kind: "note-session", ref: sessionId },
      actor_ref: { kind: "browser", ref: actor },
      base_revision: head?.revision ?? (typeof doc.revision === "number" ? doc.revision : 0),
      patch: { upsert_cards: [card] },
    });
  };
  try {
    await writeOnce();
  } catch (firstError) {
    try {
      await writeOnce();
    } catch {
      throw firstError;
    }
  }
}

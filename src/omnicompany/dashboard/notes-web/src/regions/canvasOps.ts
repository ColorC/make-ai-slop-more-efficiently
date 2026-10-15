// 活画布 ops 桥 — notes-web 页轮询 cb_pending, 在活 React Flow 画布上做定向 op, cb_respond 回结果。
// 镜像 noteOps.ts(旧 BlockSuite 笔记的 nb 桥), 给 `omni canvas` CLI 用。
// 每条 op 按 canvas id 定向认领(服务端过滤), 多个会话画布页并存互不抢单。
// 只在网页上下文启动(桌面 Tauri 换核未完成, 是另一笔债)。
import { webNotesInvoke } from "../webNotesInvoke";

export type CanvasOpArgs = Record<string, unknown> & { card?: string };

/** 各 op 的实现由 MaterialNotesWorkspace 注册; 返回值成为 cb_respond 的 ok 负载, 抛错变 error。 */
export interface CanvasOpHandlers {
  state: () => unknown;
  update: (args: CanvasOpArgs) => unknown;
  remove: (args: CanvasOpArgs) => unknown;
  viewport: (args: CanvasOpArgs) => unknown;
  focus: (args: CanvasOpArgs) => unknown;
  highlight: (args: CanvasOpArgs) => unknown;
  freeze: (args: CanvasOpArgs) => unknown;
  unfreeze: (args: CanvasOpArgs) => unknown;
  say: (args: CanvasOpArgs) => unknown;
  rewrite: (args: CanvasOpArgs) => unknown;
}

type CanvasOpCommand = { op?: string; args?: CanvasOpArgs };

const handlers = new Map<string, CanvasOpHandlers>();
const pollTimers = new Map<string, number>();

function isWebContext(): boolean {
  return typeof location !== "undefined" && (location.protocol === "http:" || location.protocol === "https:");
}

async function handleCanvasOp(storageId: string, cmd: CanvasOpCommand): Promise<string> {
  const target = handlers.get(storageId);
  if (!target) return JSON.stringify({ ok: false, error: "画布 handler 未注册" });
  const op = String(cmd.op || "");
  const args = cmd.args || {};
  try {
    switch (op) {
      case "state": return JSON.stringify({ ok: true, result: await target.state() });
      case "update": return JSON.stringify({ ok: true, result: await target.update(args) });
      case "remove": return JSON.stringify({ ok: true, result: await target.remove(args) });
      case "viewport": return JSON.stringify({ ok: true, result: await target.viewport(args) });
      case "focus": return JSON.stringify({ ok: true, result: await target.focus(args) });
      case "highlight": return JSON.stringify({ ok: true, result: await target.highlight(args) });
      case "freeze": return JSON.stringify({ ok: true, result: await target.freeze(args) });
      case "unfreeze": return JSON.stringify({ ok: true, result: await target.unfreeze(args) });
      case "say": return JSON.stringify({ ok: true, result: await target.say(args) });
      case "rewrite": return JSON.stringify({ ok: true, result: await target.rewrite(args) });
      default: return JSON.stringify({ ok: false, error: `未知 op ${op}` });
    }
  } catch (error) {
    return JSON.stringify({ ok: false, error: String(error instanceof Error ? error.message : error) });
  }
}

function startPolling(storageId: string): void {
  if (pollTimers.has(storageId) || !isWebContext()) return;
  const tick = async () => {
    let pending: [string, string][] = [];
    try {
      pending = await webNotesInvoke<[string, string][]>("cb_pending", { canvas: storageId });
    } catch {
      return; // 桥暂不可达: 下拍再试, 不刷屏
    }
    for (const [id, body] of pending) {
      let cmd: CanvasOpCommand;
      try {
        cmd = JSON.parse(body) as CanvasOpCommand;
      } catch {
        cmd = { op: "" };
      }
      const result = await handleCanvasOp(storageId, cmd);
      try {
        await webNotesInvoke("cb_respond", { id, body: result });
      } catch {
        /* 回写失败: CLI 侧会超时, 不重试(避免重复执行写 op) */
      }
    }
  };
  pollTimers.set(storageId, window.setInterval(() => { void tick(); }, 1200));
}

/** 注册一个活画布的 op 执行器; 返回反注册函数(组件卸载时调)。 */
export function registerCanvasHandler(storageId: string, target: CanvasOpHandlers): () => void {
  handlers.set(storageId, target);
  startPolling(storageId);
  return () => {
    handlers.delete(storageId);
    const timer = pollTimers.get(storageId);
    if (timer !== undefined) {
      window.clearInterval(timer);
      pollTimers.delete(storageId);
    }
  };
}

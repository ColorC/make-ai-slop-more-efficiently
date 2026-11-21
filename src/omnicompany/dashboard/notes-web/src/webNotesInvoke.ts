/** Browser transport for the Overlay note authority. */

type BridgeResponse = {
  id?: unknown;
  ok?: boolean;
  result?: unknown;
  error?: string;
};

type PendingRequest = {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
};

class SocketUnavailableBeforeSend extends Error {}
class SocketDeliveryUncertain extends Error {}

// ── 传输层观测上报(2026-08-16): WS 握手失败/超时与 HTTP fallback 失败直接报给
// dashboard 观测端点(/api/diagnostics/frontend/events), 远端"画布打不开"不再靠猜。
// 限流 + 静默: 观测不能反噬应用。
let transportReportBudget = 12;
function reportTransport(kind: string, message: string): void {
  if (transportReportBudget <= 0) return;
  transportReportBudget -= 1;
  try {
    void fetch(bridgePath("/api/diagnostics/frontend/events"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        instance: {
          instance_id: "overlay-notes-web",
          page_load_id: String(Date.now().toString(36)),
          origin: typeof location !== "undefined" ? location.origin : "",
          surface: "overlay-notes",
          href: typeof location !== "undefined" ? location.pathname.slice(0, 120) : "",
          ua: typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 200) : "",
        },
        events: [{ t: Date.now() / 1000, kind, level: "error", message }],
      }),
      keepalive: true,
    }).catch(() => { /* 上报静默 */ });
  } catch { /* */ }
}

const pending = new Map<string, PendingRequest>();
let activeSocket: WebSocket | null = null;
let openingSocket: Promise<WebSocket> | null = null;
let nextRequestId = 0;

export function resolveBridgeOptions(search: string, pathname: string): { prefix: string; httpOnly: boolean } {
  const params = new URLSearchParams(search);
  const canvasAt = pathname.indexOf("/canvas/");
  const inferredPrefix = canvasAt > 0 ? pathname.slice(0, canvasAt) : "";
  const explicitPrefix = params.get("bridge_prefix");
  const candidate = explicitPrefix ?? inferredPrefix;
  const safePrefix = /^\/(?:[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*)?$/.test(candidate)
    && !candidate.split("/").includes("..")
    ? candidate.replace(/\/$/, "")
    : "";
  return {
    prefix: safePrefix,
    httpOnly: params.get("notes_transport") === "http",
  };
}

export function resolveBridgePath(search: string, pathname: string, suffix: string): string {
  return `${resolveBridgeOptions(search, pathname).prefix}${suffix}`;
}

function bridgeOptions(): { prefix: string; httpOnly: boolean } {
  if (typeof location === "undefined") return { prefix: "", httpOnly: false };
  return resolveBridgeOptions(location.search, location.pathname);
}

function bridgePath(suffix: string): string {
  if (typeof location === "undefined") return suffix;
  return resolveBridgePath(location.search, location.pathname, suffix);
}

function rejectPending(error: Error): void {
  for (const request of pending.values()) {
    clearTimeout(request.timeout);
    request.reject(error);
  }
  pending.clear();
}

function receiveBridgeMessage(event: MessageEvent): void {
  let payload: BridgeResponse;
  try {
    payload = JSON.parse(String(event.data)) as BridgeResponse;
  } catch {
    return;
  }
  if (typeof payload.id !== "string") return;
  const request = pending.get(payload.id);
  if (!request) return;
  pending.delete(payload.id);
  clearTimeout(request.timeout);
  if (payload.ok === false) {
    request.reject(new Error(payload.error || "notes WebSocket bridge error"));
    return;
  }
  request.resolve(payload.result);
}

function websocketUrl(): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${bridgePath("/lofa/overlay/invoke-ws")}`;
}

function ensureSocket(): Promise<WebSocket> {
  if (typeof WebSocket !== "function" || typeof location === "undefined") {
    return Promise.reject(new SocketUnavailableBeforeSend("WebSocket is unavailable"));
  }
  if (activeSocket?.readyState === WebSocket.OPEN) {
    return Promise.resolve(activeSocket);
  }
  if (openingSocket) return openingSocket;

  const candidate = new WebSocket(websocketUrl());
  const opening = new Promise<WebSocket>((resolve, reject) => {
    let settled = false;
    // 2026-08-16: 1500ms → 8000ms。远端入口(浏览器→8210-LAN 网关→12443→dashboard)是
    // 多跳代理链, 握手 RTT 叠加后轻松超过 1.5s —— 画布在远端"一直加载"的直接根因之一:
    // WS 在建立前就被掐掉, 首个 invoke 全部走超时路径。8s 覆盖三跳 + 认证开销仍有余量,
    // 真不可达时由 HTTP fallback 兜底。
    const connectionTimeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      if (openingSocket === opening) openingSocket = null;
      candidate.close();
      reject(new SocketUnavailableBeforeSend("note bridge WebSocket connection timed out"));
    }, 8000);

    candidate.addEventListener("open", () => {
      if (settled) return;
      settled = true;
      clearTimeout(connectionTimeout);
      activeSocket = candidate;
      if (openingSocket === opening) openingSocket = null;
      resolve(candidate);
    });
    candidate.addEventListener("message", receiveBridgeMessage);
    candidate.addEventListener("error", () => {
      if (settled) return;
      settled = true;
      clearTimeout(connectionTimeout);
      if (openingSocket === opening) openingSocket = null;
      candidate.close();
      reject(new SocketUnavailableBeforeSend("note bridge WebSocket connection failed"));
    });
    candidate.addEventListener("close", () => {
      if (activeSocket === candidate) activeSocket = null;
      if (!settled) {
        settled = true;
        clearTimeout(connectionTimeout);
        if (openingSocket === opening) openingSocket = null;
        reject(new SocketUnavailableBeforeSend("note bridge WebSocket closed before opening"));
      }
      rejectPending(new SocketDeliveryUncertain(
        "note bridge WebSocket closed after a request may have been delivered",
      ));
    });
  });
  openingSocket = opening;
  return opening;
}

async function invokeHttp<T>(
  command: string,
  args: Record<string, unknown>,
): Promise<T> {
  const response = await fetch(bridgePath("/lofa/overlay/invoke"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ cmd: command, args }),
  });
  const payload = await response.json().catch(() => null) as {
    ok?: boolean;
    result?: T;
    error?: string;
  } | null;
  if (!response.ok || !payload || payload.ok === false) {
    throw new Error(payload?.error || `notes bridge error: ${command}`);
  }
  return payload.result as T;
}

async function invokeWebSocket<T>(
  command: string,
  args: Record<string, unknown>,
  timeoutMs: number,
): Promise<T> {
  const socket = await ensureSocket();
  const id = `note-${Date.now().toString(36)}-${(++nextRequestId).toString(36)}`;
  return new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new SocketDeliveryUncertain(
        `note bridge request timed out after it may have been delivered: ${command}`,
      ));
    }, timeoutMs);
    pending.set(id, {
      resolve: (value) => resolve(value as T),
      reject,
      timeout,
    });
    try {
      socket.send(JSON.stringify({ id, cmd: command, args }));
    } catch (error) {
      pending.delete(id);
      clearTimeout(timeout);
      reject(new SocketUnavailableBeforeSend(
        error instanceof Error ? error.message : "note bridge WebSocket send failed",
      ));
    }
  });
}

export async function webNotesInvoke<T>(
  command: string,
  args: Record<string, unknown> = {},
  timeoutMs = 15_000,
): Promise<T> {
  if (bridgeOptions().httpOnly) return invokeHttp<T>(command, args);
  try {
    return await invokeWebSocket<T>(command, args, timeoutMs);
  } catch (error) {
    // Only retry when the command was definitely not sent. Retrying an uncertain
    // write could execute it twice; those errors stay visible to the caller.
    if (error instanceof SocketUnavailableBeforeSend) {
      reportTransport("notes_ws_unavailable", `${command}: ${error.message} → HTTP fallback`);
      try {
        return await invokeHttp<T>(command, args);
      } catch (httpError) {
        reportTransport("notes_http_fallback_failed", `${command}: ${httpError instanceof Error ? httpError.message : String(httpError)}`);
        throw httpError;
      }
    }
    throw error;
  }
}

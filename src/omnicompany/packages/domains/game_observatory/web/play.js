import { ensureGatewaySession } from "./session-bootstrap.js";

/**
 * PTS paced render queue. Timestamps are microseconds; browser clock values
 * are milliseconds. The offset is allowed to move earlier when an initial
 * decoder backlog reveals a smaller arrival-minus-PTS delay.
 */
export function createPtsRenderClock({ delayMs = 50, maxFrames = 6 } = {}) {
  const frames = [];
  let offsetMs = null;
  let renderDropped = 0;
  let clockAdjustments = 0;

  function close(entry) {
    if (entry && entry.frame && typeof entry.frame.close === "function") entry.frame.close();
    renderDropped += 1;
  }
  function recalculate() {
    for (const entry of frames) entry.presentAtMs = entry.ptsUs / 1000 + offsetMs;
  }
  function enqueue(frame, ptsUs, arrivalMs, metadata = {}) {
    const observedOffset = arrivalMs - ptsUs / 1000 + delayMs;
    if (offsetMs === null || observedOffset < offsetMs) {
      offsetMs = observedOffset;
      clockAdjustments += 1;
      recalculate();
    }
    const entry = { ...metadata, frame, ptsUs, presentAtMs: ptsUs / 1000 + offsetMs };
    frames.push(entry);
    frames.sort((left, right) => left.ptsUs - right.ptsUs);
    while (frames.length > maxFrames) close(frames.shift());
    return entry;
  }
  function takeLatestDue(nowMs) {
    let latestDue = -1;
    for (let index = 0; index < frames.length; index += 1) {
      if (frames[index].presentAtMs > nowMs) break;
      latestDue = index;
    }
    if (latestDue < 0) return null;
    while (latestDue > 0) { close(frames.shift()); latestDue -= 1; }
    return frames.shift();
  }
  function release() {
    while (frames.length) close(frames.shift());
    offsetMs = null;
  }
  return {
    enqueue,
    takeLatestDue,
    release,
    get depth() { return frames.length; },
    get offsetMs() { return offsetMs; },
    get renderDropped() { return renderDropped; },
    get clockAdjustments() { return clockAdjustments; },
    get nextLeadMs() { return frames.length ? frames[0].presentAtMs : null; },
  };
}

export function replaySegmentTimeUs(frame, segment) {
  const ptsUs = Number(frame && frame.pts_us);
  const startPtsUs = Number(segment && segment.start_pts_us);
  if (Number.isFinite(ptsUs) && Number.isFinite(startPtsUs)) {
    return Math.max(0, Math.round(ptsUs - startPtsUs));
  }
  const wallNs = Number(frame && frame.wall_ns);
  const startWallNs = Number(segment && segment.start_wall_ns);
  if (Number.isFinite(wallNs) && Number.isFinite(startWallNs)) {
    return Math.max(0, Math.round((wallNs - startWallNs) / 1000));
  }
  return 0;
}

// Low-rate cross-layer timing samples are kept in a bounded ring for console
// diagnostics. Normal frames carry no extra client-side record.
export function createVideoTimingRing({ maxEntries = 128 } = {}) {
  if (!Number.isInteger(maxEntries) || maxEntries < 1) throw new RangeError("invalid timing ring size");
  const entries = [];
  const bySeq = new Map();
  function mark(meta, phase, atMs) {
    const seq = Number(meta && meta.seq);
    if (!Number.isFinite(seq) || !meta || !meta.timing) return null;
    let entry = bySeq.get(seq);
    if (!entry) {
      entry = { seq, server: { ...meta.timing } };
      bySeq.set(seq, entry); entries.push(entry);
      while (entries.length > maxEntries) bySeq.delete(entries.shift().seq);
    }
    if (phase) entry[phase] = atMs;
    return entry;
  }
  function markSeq(seq, phase, atMs) {
    const entry = bySeq.get(Number(seq));
    if (entry && phase) entry[phase] = atMs;
    return entry || null;
  }
  return {
    mark,
    markSeq,
    get(seq) { return bySeq.get(Number(seq)) || null; },
    get entries() { return entries.slice(); },
  };
}

if (typeof document !== "undefined") {
(async () => {
  "use strict";
  const api = "/api/game-observatory/play";
  const canvas = document.querySelector("#screen");
  const replayVideo = document.querySelector("#replay-video");
  const replayLive = document.querySelector("#replay-live");
  const ctx = canvas.getContext("2d", { alpha: false });
  const message = document.querySelector("#screen-message");
  const accessGate = document.querySelector("#access-gate");
  const accessMessage = document.querySelector("#access-message");
  const accessAction = document.querySelector("#access-action");
  const connection = document.querySelector("#connection");
  const latency = document.querySelector("#latency");
  const statusNode = document.querySelector("#status");
  const recordsPanel = document.querySelector("#records-panel");
  const recordsList = document.querySelector("#records-list");
  const recordsState = document.querySelector("#records-state");
  const recordsToggle = document.querySelector("#records-toggle");
  const fullscreenToggle = document.querySelector("#fullscreen-toggle");
  const recordPreview = document.querySelector("#record-preview");
  const recordPreviewImage = document.querySelector("#record-preview-image");
  const recordPreviewClose = document.querySelector("#record-preview-close");
  const layout = document.querySelector(".layout");
  const clientId = `browser-${window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : Date.now()}`;
  const pending = new Map();
  const stats = window.__rrStreamStats = {
    received: 0, decoded: 0, presented: 0, presentedSeq: 0, dropped: 0, lastAck: null,
    decoderDropped: 0, renderDropped: 0,
    renderBufferDepth: 0, lastRenderLeadMs: null, clockOffsetMs: null, clockAdjustments: 0,
    videoConnected: false, inputConnected: false
  };
  let videoSocket = null;
  let inputSocket = null;
  let decoder = null;
  const ptsToSeq = new Map();
  let currentDisplayedSeq = 0;
  let needKeyframe = false;
  let inputReady = false;
  const pointerPositions = new Map();
  let started = false;
  let socketsStarted = false;
  let reconnectTimer = null;
  const maxDecodeQueue = 12;
  const renderDelayMs = 50;
  const maxRenderBuffer = 6;
  const renderClock = createPtsRenderClock({ delayMs: renderDelayMs, maxFrames: maxRenderBuffer });
  const videoTiming = createVideoTimingRing();
  window.__rrVideoTiming = videoTiming;
  let renderScheduled = false;
  let recordsPath = null;
  let recordsLoadedPath = null;
  let recordsSignature = "";
  let recordsInflight = false;
  const recordById = new Map();

  function decoderDrop() { stats.dropped += 1; stats.decoderDropped += 1; }
  function releasePendingFrame() {
    renderClock.release();
    stats.renderDropped = renderClock.renderDropped;
    stats.renderBufferDepth = renderClock.depth;
    stats.clockOffsetMs = null;
    stats.lastRenderLeadMs = null;
  }
  function scheduleRender() {
    if (renderScheduled) return;
    renderScheduled = true;
    requestAnimationFrame(() => {
      renderScheduled = false;
      const now = performance.now();
      stats.lastRenderLeadMs = renderClock.nextLeadMs === null ? null : renderClock.nextLeadMs - now;
      const next = renderClock.takeLatestDue(now);
      if (next) {
        const frame = next.frame;
        videoTiming.markSeq(next.seq, "present_ms", now);
        if (next.seq !== undefined) { currentDisplayedSeq = next.seq; stats.presentedSeq = next.seq; }
        if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
          canvas.width = frame.displayWidth; canvas.height = frame.displayHeight;
        }
        ctx.drawImage(frame, 0, 0);
        frame.close();
        stats.presented += 1;
        if (!stats.videoConnected) { stats.videoConnected = true; setConnection("画面已连接"); }
        setMessage("");
      }
      stats.renderDropped = renderClock.renderDropped;
      stats.renderBufferDepth = renderClock.depth;
      if (renderClock.depth) scheduleRender();
    });
  }

  function setMessage(text) { message.textContent = text; message.hidden = !text; }
  function setConnection(text) { connection.textContent = text; }
  function recordsVisible() {
    return recordsPanel && !recordsPanel.classList.contains("is-collapsed") &&
      (window.matchMedia("(min-width: 851px)").matches || recordsPanel.classList.contains("is-open"));
  }
  function fileUrl(path) {
    return `${api}/files/${String(path).split("/").map(encodeURIComponent).join("/")}`;
  }
  function runPrefixFromFramePath(path) {
    const parts = String(path || "").split("/").filter(Boolean);
    const framesIndex = parts.lastIndexOf("frames");
    if (framesIndex < 1 || !parts[0].startsWith("session-") || framesIndex !== parts.length - 2) return null;
    return parts.slice(0, framesIndex).join("/");
  }
  function friendlyAction(action) {
    const kind = String(action && action.kind || "").toLowerCase();
    const params = action && action.params && typeof action.params === "object" ? action.params : {};
    const x1 = Number(params.x1), y1 = Number(params.y1), x2 = Number(params.x2), y2 = Number(params.y2);
    const distance = [x1, y1, x2, y2].every(Number.isFinite) ? Math.hypot(x2 - x1, y2 - y1) : Infinity;
    if (kind === "wait") return "等待画面";
    if (kind === "drag" && distance > 12) return "拖动";
    if (kind === "drag" || kind === "tap") return "点击";
    return "操作";
  }
  function recordTime(value) {
    const date = typeof value === "string" ? new Date(Date.parse(value)) : (() => {
      const numeric = Number(value);
      return Number.isFinite(numeric) ? new Date(numeric > 1e12 ? numeric : numeric * 1000) : new Date(NaN);
    })();
    if (Number.isNaN(date.getTime())) return "刚刚";
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function recordSortValue(action) {
    const value = action && action.t_wall;
    if (typeof value === "string") return Date.parse(value) || 0;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? (numeric > 1e12 ? numeric : numeric * 1000) : 0;
  }
  function renderRecords() {
    if (!recordsList) return;
    const records = [...recordById.values()].sort((a, b) => recordSortValue(b) - recordSortValue(a)).slice(0, 40);
    recordsList.replaceChildren();
    if (!records.length) {
      recordsList.innerHTML = '<p class="records-empty">等待操作</p>';
      return;
    }
    for (const action of records) {
      const item = document.createElement("article"); item.className = "record";
      const frameId = typeof action.pre_frame_id === "string" ? action.pre_frame_id : "";
      const thumbRoot = action._recordsPath || recordsPath;
      const thumbPath = frameId && thumbRoot ? `${thumbRoot}/frames/${frameId.endsWith(".webp") ? frameId : `${frameId}.webp`}` : "";
      if (thumbPath) {
        const link = document.createElement("a"); link.href = fileUrl(thumbPath);
        link.addEventListener("click", event => {
          if (!recordPreview || typeof recordPreview.showModal !== "function") return;
          event.preventDefault(); recordPreviewImage.src = link.href; recordPreview.showModal();
        });
        const image = document.createElement("img"); image.className = "record-thumb"; image.alt = "操作前画面"; image.loading = "lazy";
        image.src = fileUrl(thumbPath); image.addEventListener("error", () => { link.replaceChildren(); link.className = "record-thumb record-thumb-missing"; link.textContent = "暂无画面"; });
        link.append(image); item.append(link);
      } else {
        const missing = document.createElement("div"); missing.className = "record-thumb record-thumb-missing"; missing.textContent = "暂无画面"; item.append(missing);
      }
      const main = document.createElement("div"); main.className = "record-main";
      const title = document.createElement("p"); title.className = "record-title"; title.textContent = friendlyAction(action);
      const meta = document.createElement("p"); meta.className = "record-meta";
      const source = action.params && action.params.source === "manual" ? "手动" : "自动";
      meta.innerHTML = `${recordTime(action.t_wall)} · <span class="record-source">${source}</span>`;
      main.append(title, meta); item.append(main); recordsList.append(item);
    }
  }
  async function loadRecentRecords() {
    try {
      const recent = await fetchWithTimeout(`${api}/recent-actions?limit=40`, { cache: "no-store" }, 4000);
      if (!recent.ok) return false;
      const body = await recent.json();
      if (!Array.isArray(body && body.actions)) return false;
      const incoming = body.actions.filter(action => action && action.action_id).map(action => ({
        ...action,
        _recordsPath: typeof action.replay_prefix === "string" ? action.replay_prefix.replace(/\/$/, "") : null,
      }));
      if (incoming.length) {
        recordById.clear();
        for (const action of incoming) recordById.set(String(action.action_id), action);
        recordsPath = null;
      }
      recordsLoadedPath = "recent-actions";
      recordsSignature = JSON.stringify(incoming);
      if (recordsState) recordsState.textContent = recordById.size ? `${Math.min(40, recordById.size)} 条` : "等待操作";
      renderRecords();
      return true;
    } catch (_) {
      return false;
    }
  }
  async function refreshRecords(statusValue) {
    if (!recordsVisible() || recordsInflight) return;
    recordsInflight = true;
    try {
      // Newer servers expose a bounded recent-actions view that survives a
      // run/session reconnect and carries each action's replay prefix.
      if (await loadRecentRecords()) return;
      const path = statusValue && statusValue.last_observation && statusValue.last_observation.path;
      const prefix = runPrefixFromFramePath(path);
      if (!prefix) {
        if (recordsState) recordsState.textContent = recordById.size ? `${Math.min(40, recordById.size)} 条` : "等待操作";
        renderRecords();
        return;
      }
      if (recordsLoadedPath && recordsLoadedPath !== prefix) {
        // Keep the previous run visible until the new run exposes an action;
        // loaded rows carry their own replay prefix for thumbnail lookup.
        recordsSignature = "";
        renderRecords();
      }
      recordsPath = prefix;
      const response = await fetchWithTimeout(fileUrl(`${prefix}/actions.ndjson`), { headers: { Range: "bytes=-65536" }, cache: "no-store" }, 4000);
      if (response.status === 404) {
        recordsLoadedPath = prefix;
        if (recordsState) recordsState.textContent = recordById.size ? `${Math.min(40, recordById.size)} 条` : "等待操作";
        renderRecords();
        return;
      }
      if (!response.ok && response.status !== 206) throw new Error("records unavailable");
      const text = await response.text();
      const range = response.headers.get("Content-Range") || "";
      const rangeStart = Number((/^bytes\s+(\d+)-/i.exec(range) || [])[1]);
      const lines = text.split(/\r?\n/);
      const first = Number.isFinite(rangeStart) && rangeStart > 0 ? 1 : 0;
      const partialTail = response.status === 206 && !(text.endsWith("\n") || text.endsWith("\r"));
      const last = partialTail ? lines.length - 1 : lines.length;
      const incoming = [];
      for (let index = first; index < last; index += 1) {
        const line = lines[index];
        if (!line.trim()) continue;
        try { const action = JSON.parse(line); if (action && action.action_id) incoming.push(action); } catch (_) { /* partial range line */ }
      }
      recordsLoadedPath = prefix;
      for (const action of incoming) action._recordsPath = prefix;
      const sorted = [...new Map(incoming.map(action => [String(action.action_id), action])).values()]
        .sort((a, b) => recordSortValue(b) - recordSortValue(a)).slice(0, 40);
      const signature = JSON.stringify(sorted);
      if (signature === recordsSignature && recordsPath === prefix) return;
      recordById.clear(); for (const action of sorted) recordById.set(String(action.action_id), action);
      recordsSignature = signature;
      if (recordsState) recordsState.textContent = recordById.size ? `${Math.min(40, recordById.size)} 条` : "等待操作";
      renderRecords();
    } catch (_) { if (recordsState) recordsState.textContent = "记录暂不可用"; }
    finally { recordsInflight = false; }
  }
  function closeMapForReplay() {
    if (location.hash !== "#play-map") return;
    history.replaceState(null, "", `${location.pathname}${location.search}`);
    window.dispatchEvent(new Event("hashchange"));
  }
  function showLive() {
    closeMapForReplay();
    replayVideo.pause();
    replayVideo.removeAttribute("src");
    replayVideo.load();
    replayVideo.hidden = true;
    replayLive.hidden = true;
    setConnection(stats.videoConnected ? "画面已连接" : "正在连接");
  }
  function renderStatus(value) {
    if (!value || typeof value !== "object") return;
    const rows = [];
    const automatic = value.automatic;
    const autoLabel = value.mode === "auto" && automatic && automatic.needs_attention ? "需要检查" :
      value.mode === "auto" && automatic && automatic.wait_until ? "等待体力" : "自动游玩中";
    const modeLabel = value.mode === "manual" ? (value.owner === clientId ? "你在操作" : "正在观看") :
      value.mode === "auto" ? autoLabel : "已暂停";
    started = Boolean(value.started);
    if (value.mode) rows.push(`<div><dt>模式</dt><dd>${modeLabel}</dd></div>`);
    document.querySelectorAll("[data-mode]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.mode === value.mode &&
        (value.mode !== "manual" || value.owner === clientId)));
    });
    if (value.mode === "auto" && automatic && automatic.progress) {
      const level = automatic.progress.level;
      if (level !== null && level !== undefined) rows.push(`<div><dt>自动进度</dt><dd>等级 ${level}</dd></div>`);
      // 2026-09-21: "自动游玩中" stayed on screen through 9.4 hours of segments
      // that ended cleanly and moved nothing.  Running is not the same as
      // getting anywhere, so say when the account last actually moved.
      const idle = automatic.progress.idle_segments;
      const since = automatic.progress.seconds_since_progress;
      if (typeof since === "number") {
        const minutes = Math.floor(since / 60);
        const label = minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分前`
          : minutes >= 1 ? `${minutes} 分前` : "刚刚";
        const suffix = idle ? `（已空转 ${idle} 段）` : "";
        rows.push(`<div><dt>上次进展</dt><dd>${label}${suffix}</dd></div>`);
      } else if (idle) {
        rows.push(`<div><dt>上次进展</dt><dd>本次启动后还没有（空转 ${idle} 段）</dd></div>`);
      }
    }
    // 2026-09-21: "需要检查" used to be the end of the line for the page.  An
    // episode is held until a child proves progress and no child runs while
    // one is open, so clearing it meant editing attention-state.json and
    // restarting the service -- twice in one day, each time dropping the
    // stream.  The person who just fixed the screen says so here instead.
    if (value.mode === "auto" && automatic && automatic.needs_attention) {
      const recovery = automatic.recovery || {};
      const key = recovery.attention_key ? String(recovery.attention_key).replace(/[<>&]/g, "") : "";
      rows.push(`<div><dt>卡在</dt><dd>${key || "未知界面"}</dd></div>`);
      rows.push('<div><dt>已处理？</dt><dd><button type="button" id="clear-attention">我看过了，继续</button></dd></div>');
    }
    statusNode.innerHTML = rows.join("") || "<div><dt>状态</dt><dd>准备中</dd></div>";
    const clearButton = statusNode.querySelector("#clear-attention");
    if (clearButton) clearButton.addEventListener("click", async () => {
      clearButton.disabled = true;
      try {
        const response = await fetch(`${api}/attention/clear`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actor: clientId, note: "cleared from the play page" }),
        });
        if (response.ok) renderStatus(await response.json());
        else { setConnection("现在无法继续"); clearButton.disabled = false; }
      } catch (_) { setConnection("操作未送达"); clearButton.disabled = false; }
    });
    refreshRecords(value);
    if (started) {
      if (!socketsStarted) startSockets();
      else {
        // A socket can close while gateway preparation reports started=false.
        // The later status transition must recreate only closed sockets;
        // CONNECTING/OPEN sockets and the existing stats interval stay intact.
        if (!videoSocket || videoSocket.readyState === WebSocket.CLOSED) connectVideo();
        if (!inputSocket || inputSocket.readyState === WebSocket.CLOSED) connectInput();
      }
    }
  }
  async function refresh() {
    try {
      const status = await fetch(`${api}/status`, { cache: "no-store" });
      if (status.ok) renderStatus(await status.json());
    } catch (_) { /* the sockets carry the useful live error */ }
  }
  function startSockets() {
    if (socketsStarted) return;
    socketsStarted = true;
    connectVideo(); connectInput(); setInterval(reportViewerStats, 5000);
  }
  function showAccessGate(kind, detail = "") {
    accessGate.hidden = false;
    message.hidden = true;
    if (kind === "login") {
      setConnection("需要登录");
      accessMessage.textContent = "登录后即可游玩";
      accessAction.hidden = false;
      accessAction.textContent = "登录后游玩";
      accessAction.href = "#";
      accessAction.onclick = async event => {
        event.preventDefault();
        accessAction.setAttribute("aria-busy", "true");
        if (await ensureGatewaySession()) window.location.reload();
        else accessMessage.textContent = "登录未完成，请重试";
        accessAction.removeAttribute("aria-busy");
      };
    } else if (kind === "device") {
      setConnection("需要设备批准");
      accessMessage.textContent = "请先批准这台设备";
      accessAction.hidden = false;
      accessAction.textContent = "批准设备";
      accessAction.href = `/device-access?next=${encodeURIComponent(location.pathname + location.search)}`;
      accessAction.onclick = null;
    } else {
      setConnection("连接不可用");
      accessMessage.textContent = detail || "暂时无法连接游玩服务";
      accessAction.hidden = true;
    }
  }
  async function fetchWithTimeout(url, options = {}, timeoutMs = 4000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try { return await fetch(url, { ...options, signal: controller.signal }); }
    finally { clearTimeout(timer); }
  }
  async function probePlayAccess() {
    try {
      const probe = await fetchWithTimeout(`${api}/status`, { cache: "no-store" });
      if (probe.status === 401) { showAccessGate("login"); return false; }
      if (probe.status === 403) { showAccessGate("device"); return false; }
      if (!probe.ok) { showAccessGate("error", `游玩服务返回 ${probe.status}`); return false; }
      try { renderStatus(await probe.clone().json()); } catch (_) {}
      return true;
    } catch (_) {
      showAccessGate("error", "暂时无法连接游玩服务");
      return false;
    }
  }
  async function prepareGateway() {
    const localDirect = new Set(["localhost", "127.0.0.1", "::1"]).has(location.hostname);
    let state;
    try {
      state = await fetchWithTimeout("/api/desktop/session/state", { cache: "no-store" });
    } catch (_) {
      // Localhost direct mode may have no gateway endpoint; the play API is
      // still authoritative in that deployment.
      return probePlayAccess();
    }
    if (state.status === 401) { showAccessGate("login"); return false; }
    if (state.status === 403) { showAccessGate("login"); return false; }
    if (state.ok) {
      let body = {};
      try { body = await state.json(); } catch (_) {}
      if (!body.has_session && !(await Promise.race([
        ensureGatewaySession(), new Promise(resolve => setTimeout(() => resolve(false), 4000))
      ]))) {
        // The remote gateway requires its authenticated bootstrap before the
        // play API can be probed.  Showing the login action here avoids a
        // misleading timeout/error and prevents an unauthorised WS loop.
        if (!localDirect) { showAccessGate("login"); return false; }
        // A direct local service may expose this state route but does not
        // implement gateway sessions; let the play endpoint decide.
        return probePlayAccess();
      }
    } else if (state.status !== 404 && state.status !== 503) {
      showAccessGate("error", `登录服务返回 ${state.status}`);
      return false;
    }
    return probePlayAccess();
  }
  function makeDecoder(meta) {
    if (!window.isSecureContext || typeof VideoDecoder !== "function") {
      setMessage("请在安全连接中使用支持 WebCodecs 的浏览器打开此页面。");
      return null;
    }
    try {
      return new VideoDecoder({
      output(frame) {
        stats.decoded += 1;
        const frameSeq = ptsToSeq.get(frame.timestamp);
        ptsToSeq.delete(frame.timestamp);
        const outputMs = performance.now();
        videoTiming.markSeq(frameSeq, "output_ms", outputMs);
        renderClock.enqueue(frame, frame.timestamp, outputMs, { seq: frameSeq, timing: videoTiming.get(frameSeq) });
          stats.renderDropped = renderClock.renderDropped;
          stats.renderBufferDepth = renderClock.depth;
          stats.clockOffsetMs = renderClock.offsetMs;
          stats.clockAdjustments = renderClock.clockAdjustments;
          scheduleRender();
        },
        error() {
          stats.videoConnected = false; setConnection("正在恢复画面");
          decoderDrop(); needKeyframe = true;
          ptsToSeq.clear(); releasePendingFrame();
          try { decoder.close(); } catch (_) {}
          decoder = null;
          setMessage("视频解码暂时中断，正在等待关键帧。");
        }
      });
    } catch (_) { setMessage("当前浏览器无法启用 WebCodecs 视频解码。"); return null; }
  }
  function decode(meta, bytes) {
    if (needKeyframe && !meta.keyframe) { decoderDrop(); return; }
    if (!decoder) decoder = makeDecoder(meta);
    if (!decoder) return;
    try {
      if (decoder.state === "unconfigured") {
        const config = { codec: meta.codec || "avc1.42c02a", optimizeForLatency: true, hardwareAcceleration: "prefer-hardware" };
        try { decoder.configure(config); }
        catch (_) { decoder.configure({ codec: config.codec, optimizeForLatency: true }); }
      }
      if (decoder.state !== "configured") return;
      if (!meta.keyframe && decoder.decodeQueueSize > maxDecodeQueue) { decoderDrop(); needKeyframe = true; return; }
      const pts = Number(meta.pts_us) || 0;
      ptsToSeq.set(pts, Number(meta.seq) || 0);
      decoder.decode(new EncodedVideoChunk({ type: meta.keyframe ? "key" : "delta", timestamp: pts, data: bytes }));
      if (meta.keyframe) needKeyframe = false;
    } catch (_) { decoderDrop(); needKeyframe = true; ptsToSeq.clear(); releasePendingFrame(); try { decoder.close(); } catch (__) {} decoder = null; }
  }
  async function readPacket(buffer) {
    const bytes = new Uint8Array(buffer);
    if (bytes.byteLength < 4) { decoderDrop(); return; }
    const length = new DataView(bytes.buffer, bytes.byteOffset, 4).getUint32(0);
    if (bytes.byteLength < 4 + length) { decoderDrop(); return; }
    let meta;
    try { meta = JSON.parse(new TextDecoder().decode(bytes.subarray(4, 4 + length))); }
    catch (_) { decoderDrop(); return; }
    stats.received += 1;
    videoTiming.mark(meta, "arrival_ms", performance.now());
    decode(meta, bytes.subarray(4 + length));
  }
  function connectVideo() {
    if (videoSocket && videoSocket.readyState <= WebSocket.OPEN) return;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    videoSocket = new WebSocket(`${scheme}://${location.host}${api}/video`);
    videoSocket.binaryType = "arraybuffer";
    videoSocket.onopen = () => { stats.videoConnected = false; setConnection("正在接收画面"); };
    videoSocket.onmessage = event => event.data instanceof ArrayBuffer ? readPacket(event.data) : (() => { try { renderStatus(JSON.parse(event.data)); } catch (_) {} })();
    videoSocket.onerror = () => setConnection("画面连接异常");
  videoSocket.onclose = async () => {
    stats.videoConnected = false; needKeyframe = true; ptsToSeq.clear(); releasePendingFrame();
    setConnection("正在重连画面");
    try { decoder && decoder.close(); } catch (_) {} decoder = null;
    if (await prepareGateway() && started) { setConnection("正在重连画面"); reconnectTimer = setTimeout(connectVideo, 1200); }
  };
  }
  function sendInput(kind, event) {
    if (!inputSocket || inputSocket.readyState !== WebSocket.OPEN || !inputReady) return;
    const rect = canvas.getBoundingClientRect();
    const sourceW = canvas.width || rect.width, sourceH = canvas.height || rect.height;
    const scale = Math.min(rect.width / sourceW, rect.height / sourceH);
    const shownW = sourceW * scale, shownH = sourceH * scale;
    const left = rect.left + (rect.width - shownW) / 2, top = rect.top + (rect.height - shownH) / 2;
    let nx = (event.clientX - left) / shownW, ny = (event.clientY - top) / shownH;
    const inside = nx >= 0 && nx <= 1 && ny >= 0 && ny <= 1;
    if (!inside && kind !== "up" && kind !== "cancel") return;
    if (!inside) { const previous = pointerPositions.get(event.pointerId); nx = previous ? previous.nx : Math.min(1, Math.max(0, nx)); ny = previous ? previous.ny : Math.min(1, Math.max(0, ny)); }
    nx = Math.min(1, Math.max(0, nx)); ny = Math.min(1, Math.max(0, ny));
    if (kind === "down" || kind === "move") pointerPositions.set(event.pointerId, { nx, ny });
    else pointerPositions.delete(event.pointerId);
    const seq = ++sendInput.seq;
    const stableEventId = `${clientId}-${seq}`;
    pending.set(stableEventId, performance.now());
    inputSocket.send(JSON.stringify({ client_id: clientId, event_id: stableEventId, seq, kind, pointer_id: event.pointerId, nx, ny, client_time_ms: performance.timeOrigin + performance.now(), displayed_seq: currentDisplayedSeq }));
  }
  sendInput.seq = 0;
  function connectInput() {
    if (inputSocket && inputSocket.readyState <= WebSocket.OPEN) return;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    inputSocket = new WebSocket(`${scheme}://${location.host}${api}/input`);
    inputSocket.onopen = () => { inputReady = true; stats.inputConnected = true; };
    inputSocket.onmessage = event => {
      let ack; try { ack = JSON.parse(event.data); } catch (_) { return; }
      const started = pending.get(ack.event_id); if (started !== undefined) { stats.lastAck = performance.now() - started; latency.textContent = `输入延迟 ${Math.round(stats.lastAck)} ms`; pending.delete(ack.event_id); }
      if (ack.ok === false) latency.textContent = `操作未完成：${friendlyInputError(ack.error)}`;
    };
    inputSocket.onclose = async () => {
      inputReady = false; stats.inputConnected = false;
      if (await prepareGateway() && started) { setConnection("控制连接已断开"); setTimeout(connectInput, 1500); }
    };
    inputSocket.onerror = () => { inputReady = false; };
  }
  function friendlyInputError(error) {
    const value = String(error || "");
    if (value.includes("control_handed_over")) return "当前由自动游玩接管";
    if (value.includes("recording_unavailable")) return "画面暂不可用";
    if (value.includes("stale")) return "画面已过期，请稍后重试";
    if (value.includes("forbidden")) return "当前画面不允许操作";
    if (value.includes("outside")) return "操作位置不在画面内";
    return "操作没有送达";
  }
  canvas.addEventListener("pointerdown", event => { if (!inputReady) return; canvas.setPointerCapture(event.pointerId); sendInput("down", event); });
  canvas.addEventListener("pointermove", event => { if (canvas.hasPointerCapture(event.pointerId)) sendInput("move", event); });
  ["pointerup", "pointercancel"].forEach(name => canvas.addEventListener(name, event => { if (name === "pointerup") sendInput("up", event); else sendInput("cancel", event); if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId); }));
  document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", async () => {
    try {
      if (!started) { const start = await fetch(`${api}/start`, { method: "POST" }); if (!start.ok) throw new Error("start"); renderStatus(await start.json()); }
      const response = await fetch(`${api}/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: button.dataset.mode, client_id: clientId }) });
      if (response.ok) renderStatus(await response.json()); else setConnection("当前无法切换模式");
    } catch (_) { setConnection("操作未送达"); }
  }));
  recordsToggle.addEventListener("click", () => {
    const collapsed = recordsPanel.classList.toggle("is-collapsed");
    recordsPanel.classList.toggle("is-open", !collapsed);
    layout.classList.toggle("records-collapsed", collapsed);
    recordsToggle.setAttribute("aria-expanded", String(!collapsed));
    if (!collapsed) refresh();
  });
  fullscreenToggle.addEventListener("click", async () => {
    try {
      if (!document.fullscreenElement) await document.querySelector(".shell").requestFullscreen();
      else await document.exitFullscreen();
    } catch (_) { setConnection("全屏暂不可用"); }
  });
  recordPreviewClose.addEventListener("click", () => recordPreview.close());
  recordPreview.addEventListener("click", event => { if (event.target === recordPreview) recordPreview.close(); });
  document.addEventListener("fullscreenchange", () => { fullscreenToggle.textContent = document.fullscreenElement ? "退出全屏" : "全屏"; });
  async function seekReplay(payload) {
    const evidence = payload && payload.transition && payload.transition.evidence;
    try {
      const targetId = evidence && evidence.post_frame_id;
      let observation = payload.observation;
      if (!observation && targetId) {
        const response = await fetch(`${api}/observation/${encodeURIComponent(targetId)}`, { cache: "no-store" });
        if (!response.ok) throw new Error("observation unavailable");
        observation = await response.json();
      }
      const frame = observation && (observation.frame || observation);
      const seq = Number.isInteger(frame && frame.seq) ? frame.seq : null;
      const wallNs = Number.isFinite(frame && frame.wall_ns) ? frame.wall_ns : null;
      const sessionId = typeof (frame && frame.session_id) === "string" ? frame.session_id : null;
      if (!frame || (seq === null && wallNs === null) || !sessionId) throw new Error("对应回放不存在");
      const manifestPath = `${sessionId}/video-manifest.json`;
      const manifestResponse = await fetch(`${api}/files/${manifestPath.split("/").map(encodeURIComponent).join("/")}`, { cache: "no-store" });
      if (!manifestResponse.ok) throw new Error("对应回放不存在");
      const manifest = await manifestResponse.json();
      const segments = manifest.segments || [];
      const bySeq = seq === null ? [] : segments.filter(item => Number.isInteger(item.first_seq) && Number.isInteger(item.last_seq) && seq >= item.first_seq && seq <= item.last_seq);
      const byWall = wallNs === null ? [] : segments.filter(item => Number.isFinite(item.start_wall_ns) && wallNs >= item.start_wall_ns);
      const segment = bySeq[0] || byWall[byWall.length - 1];
      if (!segment) throw new Error("对应回放不存在");
      const segmentTimeUs = replaySegmentTimeUs(frame, segment);
      replayVideo.src = `${api}/files/${`${sessionId}/${segment.path}`.split("/").map(encodeURIComponent).join("/")}`;
      replayVideo.addEventListener("loadedmetadata", () => { replayVideo.currentTime = segmentTimeUs / 1e6; }, { once: true });
      closeMapForReplay();
      replayVideo.hidden = false;
      replayLive.hidden = false;
      setConnection("正在查看回放");
      replayVideo.play().catch(() => {});
    } catch (_) { setConnection("对应回放暂时无法定位"); }
  }
  replayLive.addEventListener("click", showLive);
  function reportViewerStats() {
    const payload = {
      client_id: clientId,
      received: stats.received,
      decoded: stats.decoded,
      presented: stats.presented,
      decoderDropped: stats.decoderDropped,
      renderDropped: stats.renderDropped,
      client_perf_ms: performance.now(),
      visibilityState: document.visibilityState,
      lastAckRttMs: typeof stats.lastAck === "number" ? stats.lastAck : null
    };
    fetch(`${api}/viewer-stats`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), keepalive: true
    }).catch(() => {});
  }
  window.__rrPlaySeekReplay = seekReplay;
  if (!window.matchMedia("(min-width: 851px)").matches) {
    recordsPanel.classList.add("is-collapsed");
    recordsToggle.setAttribute("aria-expanded", "false");
  }
  layout.classList.toggle("records-collapsed", recordsPanel.classList.contains("is-collapsed"));
  if (!window.isSecureContext || typeof VideoDecoder !== "function") setMessage("请在安全连接中使用支持 WebCodecs 的浏览器打开此页面。");
  if (await prepareGateway()) { refresh(); setInterval(refresh, 3000); if (started) startSockets(); }
})();
}

/**
 * BOSS SIGHT cockpit 的可热换实现层。
 *
 * 全部业务逻辑住这里: 后端守护 (supervisor)、webview html 生成、消息处理。
 * loader (薄壳) 从仓库 out/impl.js 动态 require 本模块; `omni dashboard ext-update`
 * 重编译后 loader 通过 /api/dev/versions 的 ext token 变化自动热换 — 不重启扩展宿主。
 *
 * 约束:
 * - 不在这里注册 vscode 命令/视图 provider (那是 loader 的, 由 package.json 声明锁定)。
 * - 所有需要清理的资源 (定时器/输出通道/状态栏/事件订阅) 必须进 dispose()。
 * - 会话状态读写 slot.state (loader 持有), 热换后不丢。
 */

import * as cp from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as net from 'net';
import * as path from 'path';
import * as vscode from 'vscode';
import type { ImplApi, ImplHost, WebviewSlot, SessionState } from './types';

type BackendPhase = 'idle' | 'checking' | 'starting-daemon' | 'starting-dashboard' | 'ready' | 'error';

type BackendStatus = {
  phase: BackendPhase;
  dashboardReady: boolean;
  daemonReady: boolean;
  message: string;
};

type ChatHostMessage =
  | { type: 'session-state'; sessionId: string | null; state: SessionState }
  | { type: 'session-preview'; sessionId: string | null; preview: string }
  | { type: 'open-file'; path: string; line?: number | null; column?: number | null }
  | { type: 'save-snapshot'; html: string; fileName?: string; sessionId?: string | null }
  | { type: 'copy-to-clipboard'; text: string }
  | { type: 'backend-restart' }
  | { type: 'backend-reload' }
  | { type: 'open-material-native'; materialId: string; title?: string }
  | { type: 'open-omnidashboard'; openType: string; openId: string; facet?: string | null; title?: string }
  | { type: 'open-in-claude-code'; cwd?: string; sessionId?: string }
  | { type: 'open-codex-terminal'; cwd?: string; sessionId?: string }
  | { type: 'restore-region-internal'; region?: string }
  | { type: 'focus-native-view'; viewId: string };

function cfg<T>(key: string, fallback: T): T {
  return vscode.workspace.getConfiguration('omniChat').get<T>(key) ?? fallback;
}

function dashboardPort(): number {
  return cfg<number>('dashboardPort', 8210);
}

function daemonPort(): number {
  return cfg<number>('daemonPort', 8201);
}

function getDashboardUrl(): string {
  const configured = cfg<string>('dashboardUrl', '');
  if (configured) return configured;
  return `http://127.0.0.1:${dashboardPort()}/`;
}

function appendSessionToUrl(baseUrl: string, sessionId: string | null): string {
  if (!sessionId) return baseUrl;
  const [pathAndQuery, hash = ''] = baseUrl.split('#', 2);
  const separator = pathAndQuery.includes('?') ? '&' : '?';
  const next = `${pathAndQuery}${separator}session=${encodeURIComponent(sessionId)}`;
  return hash ? `${next}#${hash}` : next;
}

function appendSurfaceToUrl(baseUrl: string, slot: WebviewSlot): string {
  if (!slot.surface) return baseUrl;
  const [pathAndQuery, hash = ''] = baseUrl.split('#', 2);
  const sep = pathAndQuery.includes('?') ? '&' : '?';
  let next = `${pathAndQuery}${sep}surface=${encodeURIComponent(slot.surface.kind)}`;
  if (slot.surface.id) next += `&id=${encodeURIComponent(slot.surface.id)}`;
  return hash ? `${next}#${hash}` : next;
}

function appendDeeplinkToUrl(baseUrl: string, slot: WebviewSlot): string {
  if (!slot.deeplink) return baseUrl;
  const [pathAndQuery, hash = ''] = baseUrl.split('#', 2);
  const sep = pathAndQuery.includes('?') ? '&' : '?';
  let next = `${pathAndQuery}${sep}open_type=${encodeURIComponent(slot.deeplink.openType)}&open_id=${encodeURIComponent(slot.deeplink.openId)}`;
  if (slot.deeplink.openFacet) next += `&open_facet=${encodeURIComponent(slot.deeplink.openFacet)}`;
  return hash ? `${next}#${hash}` : next;
}

function getDashboardUrlForSlot(slot: WebviewSlot): string {
  // 稳定 URL — 不再每次渲染都打 Date.now() 时间戳。时间戳会让每次开/恢复窗口都拿到全新 URL,
  // 命不中 webview 的 HTTP 缓存, 整个重型 SPA 冷启动一遍(用户「开窗口就刷新、很慢」的主因)。
  // Vite 产物已按内容哈希; 静默更新交给 devReload(产物哈希真变才刷); 这里给稳定 URL 让恢复窗口直接吃缓存。
  return appendDeeplinkToUrl(appendSurfaceToUrl(appendSessionToUrl(getDashboardUrl(), slot.state.sessionId), slot), slot);
}

function findBackendRoot(): string | null {
  const configured = cfg<string>('backendRoot', '').trim();
  if (configured && isBackendRoot(configured)) return configured;

  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    const direct = folder.uri.fsPath;
    if (isBackendRoot(direct)) return direct;
    const nested = path.join(direct, 'omnicompany');
    if (isBackendRoot(nested)) return nested;
  }

  let cur = __dirname;
  for (let i = 0; i < 12; i += 1) {
    if (isBackendRoot(cur)) return cur;
    const next = path.dirname(cur);
    if (next === cur) break;
    cur = next;
  }
  return null;
}

function isBackendRoot(dir: string): boolean {
  return fs.existsSync(path.join(dir, 'src', 'omnicompany', 'dashboard', 'app.py'))
    && fs.existsSync(path.join(dir, 'src', 'omnicompany', 'dashboard', 'ccdaemon', 'main.py'));
}

function httpOk(url: string, timeoutMs = 1500): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(Boolean(res.statusCode && res.statusCode >= 200 && res.statusCode < 500));
    });
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.on('error', () => resolve(false));
  });
}

async function waitForHttp(url: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await httpOk(url)) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function dashboardOk(): Promise<boolean> {
  // 只判 dashboard 页面在不在(它起没起), 不再 && daemon 健康。
  // 原来揉进 daemon 健康: daemon 冷/慢/瞬时抖, 就把健康的"共享 dashboard"判成没就绪 → 被 killPort 重拉
  // → ui token 跳变 → 别的窗口 devReload 全员 reload。这是"开一个窗口全员刷新"的根因之一。
  return httpOk(`http://127.0.0.1:${dashboardPort()}/`);
}

async function waitForDashboard(timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await dashboardOk()) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

function getWebviewHtml(url: string, status: BackendStatus): string {
  const origin = new URL(url).origin;
  const iframe = status.phase === 'ready'
    ? `<iframe id="chat" src="${escapeHtml(url)}" allow="clipboard-read; clipboard-write"></iframe>`
    : '';
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none';
               frame-src ${origin};
               script-src 'unsafe-inline';
               style-src 'unsafe-inline';">
<style>
  html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; background: #0f0f0f; color: #d6deeb; font-family: var(--vscode-font-family, Segoe UI, sans-serif); }
  iframe { display: block; width: 100%; height: 100%; border: 0; }
  .boot { height: 100%; display: grid; place-items: center; padding: 24px; box-sizing: border-box; }
  .panel { width: min(520px, 100%); border: 1px solid #233047; background: #111827; border-radius: 8px; padding: 18px; box-sizing: border-box; }
  .title { font-size: 15px; font-weight: 650; margin-bottom: 8px; }
  .msg { font-size: 13px; color: #9fb0c6; line-height: 1.5; margin-bottom: 14px; }
  .row { display: flex; align-items: center; gap: 8px; font-size: 12px; margin: 7px 0; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #64748b; }
  .ok { background: #22c55e; }
  .bad { background: #ef4444; }
  .spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid #334155; border-top-color: #60a5fa; animation: spin .9s linear infinite; }
  .actions { display: flex; gap: 8px; margin-top: 14px; }
  button { background: #1d4ed8; color: white; border: 0; border-radius: 6px; padding: 7px 10px; cursor: pointer; }
  button.secondary { background: #263244; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
${iframe || `<div class="boot">
  <div class="panel">
    <div class="title">Starting OmniChat backend</div>
    <div class="msg">${escapeHtml(status.message)}</div>
    <div class="row"><span class="dot ${status.dashboardReady ? 'ok' : status.phase === 'error' ? 'bad' : ''}"></span>dashboard :${dashboardPort()}</div>
    <div class="row"><span class="dot ${status.daemonReady ? 'ok' : status.phase === 'error' ? 'bad' : ''}"></span>ccdaemon :${daemonPort()}</div>
    <div class="row"><span class="spin"></span>${escapeHtml(status.phase)}</div>
    <div class="actions">
      <button onclick="window.vscode.postMessage({type:'backend-restart'})">Restart</button>
      <button class="secondary" onclick="window.vscode.postMessage({type:'backend-reload'})">Reload</button>
    </div>
  </div>
</div>`}
<script>
(function(){
  const vscode = acquireVsCodeApi();
  window.vscode = vscode;
  window.addEventListener('message', (ev) => {
    if (ev.data && ev.data.__omnichat === true) vscode.postMessage(ev.data);
  });
})();
</script>
</body>
</html>`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function composeTitle(state: SessionState, preview: string | null): string {
  const raw = (preview || 'new chat').replace(/\s+/g, ' ').trim();
  const base = raw.length > 24 ? `${raw.slice(0, 23)}...` : raw;
  switch (state) {
    case 'processing': return '* ' + base;
    case 'awaiting_permission': return '? ' + base;
    case 'ended': return 'done ' + base;
    default: return base;
  }
}

class BackendSupervisor {
  private status: BackendStatus = { phase: 'idle', dashboardReady: false, daemonReady: false, message: 'Not checked yet.' };
  private readonly output = vscode.window.createOutputChannel('OmniChat Backend');
  private readonly statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 10);
  private starting: Promise<void> | null = null;
  private monitor: NodeJS.Timeout | null = null;
  private disposed = false;

  constructor(private readonly host: ImplHost, private readonly renderAll: () => void) {
    this.statusBar.command = 'omniChat.backendStatus';
    this.statusBar.text = 'OmniChat: starting';
    this.statusBar.show();
  }

  get current(): BackendStatus {
    return this.status;
  }

  startMonitor(): void {
    if (this.monitor) return;
    this.monitor = setInterval(() => {
      void this.refreshStatus(false);
    }, 5000);
  }

  dispose(): void {
    this.disposed = true;
    if (this.monitor) clearInterval(this.monitor);
    this.monitor = null;
    this.output.dispose();
    this.statusBar.dispose();
  }

  async ensureStarted(): Promise<void> {
    if (!cfg<boolean>('autoStartBackend', true)) {
      await this.refreshStatus(true);
      return;
    }
    if (this.starting) return this.starting;
    this.starting = this.ensureStartedInner().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  async restart(): Promise<void> {
    this.update({ phase: 'checking', message: 'Restarting OmniChat backend...', dashboardReady: false, daemonReady: false });
    await this.killPort(dashboardPort());
    await this.killPort(daemonPort());
    await new Promise((resolve) => setTimeout(resolve, 800));
    await this.ensureStartedInner();
  }

  async refreshStatus(showOutput: boolean): Promise<void> {
    if (this.disposed) return;
    const daemonReady = await httpOk(`http://127.0.0.1:${daemonPort()}/cc/chat/health`);
    const dashboardReady = await dashboardOk();
    const phase: BackendPhase = dashboardReady && daemonReady ? 'ready' : 'idle';
    this.update({
      phase,
      dashboardReady,
      daemonReady,
      message: dashboardReady && daemonReady ? 'OmniChat backend is ready.' : 'OmniChat backend is not fully ready.',
    });
    if (showOutput) this.showStatusOutput();
  }

  showStatusOutput(): void {
    if (this.disposed) return;
    this.output.appendLine(`status: ${this.status.phase}`);
    this.output.appendLine(`dashboard :${dashboardPort()} ready=${this.status.dashboardReady}`);
    this.output.appendLine(`ccdaemon  :${daemonPort()} ready=${this.status.daemonReady}`);
    this.output.appendLine(`message: ${this.status.message}`);
    this.output.show(true);
  }

  private async ensureStartedInner(): Promise<void> {
    const root = findBackendRoot();
    if (!root) {
      this.update({
        phase: 'error',
        dashboardReady: false,
        daemonReady: false,
        message: 'Cannot find omnicompany backend root. Set omniChat.backendRoot.',
      });
      return;
    }

    this.update({ phase: 'checking', message: `Using backend root ${root}`, dashboardReady: false, daemonReady: false });

    let daemonReady = await httpOk(`http://127.0.0.1:${daemonPort()}/cc/chat/health`);
    if (!daemonReady) {
      if (await this.isPortListening(daemonPort())) {
        // 别的窗口已起共享 ccdaemon(端口已被监听) — 只等它健康, 绝不 killPort(否则杀掉别窗口的后端 → 全员刷新)。
        this.update({ phase: 'starting-daemon', message: `Waiting for shared ccdaemon on ${daemonPort()}...`, dashboardReady: false, daemonReady: false });
        daemonReady = await waitForHttp(`http://127.0.0.1:${daemonPort()}/cc/chat/health`, 30000);
      } else {
        this.update({ phase: 'starting-daemon', message: `Starting ccdaemon on ${daemonPort()}...`, dashboardReady: false, daemonReady: false });
        await this.killPort(daemonPort());
        this.spawnBackend(root, [
          '-m', 'uvicorn',
          'omnicompany.dashboard.ccdaemon.main:app',
          '--host', '127.0.0.1',
          '--port', String(daemonPort()),
        ], { OMNI_CC_DAEMON_PORT: String(daemonPort()) });
        daemonReady = await waitForHttp(`http://127.0.0.1:${daemonPort()}/cc/chat/health`, 30000);
      }
    }

    let dashboardReady = await dashboardOk();
    if (!dashboardReady) {
      if (await this.isPortListening(dashboardPort())) {
        // 别的窗口已起共享 dashboard(端口已被监听) — 只读 attach, 只等它健康, 绝不 killPort。这正是"开一个窗口全员刷新"的根因。
        this.update({ phase: 'starting-dashboard', message: `Waiting for shared dashboard on ${dashboardPort()}...`, dashboardReady: false, daemonReady });
        dashboardReady = await waitForDashboard(30000);
      } else {
        this.update({ phase: 'starting-dashboard', message: `Starting dashboard on ${dashboardPort()}...`, dashboardReady: false, daemonReady });
        await this.killPort(dashboardPort());
        this.spawnBackend(root, [
          '-m', 'uvicorn',
          'omnicompany.dashboard.app:app',
          '--host', '127.0.0.1',
          '--port', String(dashboardPort()),
          '--log-level', 'info',
        ], {});
        dashboardReady = await waitForDashboard(30000);
      }
    }

    this.update({
      phase: dashboardReady && daemonReady ? 'ready' : 'error',
      dashboardReady,
      daemonReady,
      message: dashboardReady && daemonReady
        ? 'OmniChat backend is ready.'
        : 'Backend did not become ready in time. Check OmniChat Backend output.',
    });
  }

  private spawnBackend(root: string, args: string[], extraEnv: Record<string, string>): void {
    const python = cfg<string>('pythonPath', 'python');
    const env = {
      ...process.env,
      ...extraEnv,
      PYTHONPATH: path.join(root, 'src'),
    };
    if (!this.disposed) this.output.appendLine(`spawn: ${python} ${args.join(' ')}`);
    const child = cp.spawn(python, args, {
      cwd: root,
      env,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    // 热换后旧 supervisor 的 output 已 dispose, 子进程还活着 — 用 disposed 守门
    child.stdout?.on('data', (data: Buffer) => { if (!this.disposed) this.output.append(data.toString()); });
    child.stderr?.on('data', (data: Buffer) => { if (!this.disposed) this.output.append(data.toString()); });
    child.on('exit', (code, signal) => {
      if (this.disposed) return;
      this.output.appendLine(`process exited code=${code} signal=${signal}`);
      void this.refreshStatus(false);
    });
  }

  private async killPort(port: number): Promise<void> {
    if (process.platform !== 'win32') return;
    await new Promise<void>((resolve) => {
      const script = [
        `$conns = Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue`,
        'foreach ($c in $conns) { taskkill /PID $c.OwningProcess /T /F | Out-Null }',
      ].join('; ');
      cp.execFile('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script], { windowsHide: true }, () => resolve());
    });
  }

  // 端口有没有人在监听(TCP connect 探活, 只查不杀)。给"别窗口已起共享后端就别杀、只等健康"用。
  private isPortListening(port: number): Promise<boolean> {
    return new Promise((resolve) => {
      const socket = new net.Socket();
      let settled = false;
      const done = (v: boolean) => {
        if (settled) return;
        settled = true;
        try { socket.destroy(); } catch { /* ignore */ }
        resolve(v);
      };
      socket.setTimeout(1200);
      socket.once('connect', () => done(true));
      socket.once('timeout', () => done(false));
      socket.once('error', () => done(false));
      socket.connect(port, '127.0.0.1');
    });
  }

  private update(next: BackendStatus): void {
    if (this.disposed) return;
    const phaseChanged = this.status.phase !== next.phase;
    this.status = next;
    this.statusBar.text = next.phase === 'ready' ? 'OmniChat: ready' : `OmniChat: ${next.phase}`;
    this.statusBar.tooltip = next.message;
    if (next.phase === 'ready' && !phaseChanged) {
      return;
    }
    this.renderAll();
  }
}

async function openLocalFile(filePath: string, line?: number | null, column?: number | null) {
  if (!filePath) return;
  const match = filePath.match(/^(.*?):(\d+)(?::(\d+))?$/);
  let resolvedPath = match ? match[1] : filePath;
  // 相对路径(如计划的 docs/plans/… folder_path)对仓库根解析 —— 前端不知道绝对根, 这里补。
  if (!path.isAbsolute(resolvedPath) && !/^[a-zA-Z]:[\\/]/.test(resolvedPath)) {
    const root = findBackendRoot();
    if (root) resolvedPath = path.join(root, resolvedPath);
  }
  const resolvedLine = line ?? (match ? Number(match[2]) : null);
  const resolvedColumn = column ?? (match?.[3] ? Number(match[3]) : null);
  try {
    const uri = vscode.Uri.file(resolvedPath);
    // 目录(项目 roots 常在工作区外, revealInExplorer 不可用): 开系统文件管理器
    try {
      const stat = await vscode.workspace.fs.stat(uri);
      if (stat.type & vscode.FileType.Directory) {
        await vscode.env.openExternal(uri);
        return;
      }
    } catch { /* stat 不到就按文件继续, 让 openTextDocument 给出真实错误 */ }
    const doc = await vscode.workspace.openTextDocument(uri);
    const options: vscode.TextDocumentShowOptions = {};
    if (resolvedLine && resolvedLine > 0) {
      const pos = new vscode.Position(resolvedLine - 1, Math.max((resolvedColumn || 1) - 1, 0));
      options.selection = new vscode.Range(pos, pos);
    }
    await vscode.window.showTextDocument(doc, options);
  } catch (err) {
    // 静默失败 = 用户视角"点了没反应"(2026-06-12 剪贴板同类教训), 必须可见
    void vscode.window.showErrorMessage(`OmniChat 打开失败: ${resolvedPath} — ${String((err as Error)?.message || err)}`);
  }
}

function utf8Bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

async function saveSnapshot(context: vscode.ExtensionContext, html: string, fileName?: string) {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
  const baseDir = workspaceRoot || context.globalStorageUri;
  const dir = vscode.Uri.joinPath(baseDir, '_scratch');
  await vscode.workspace.fs.createDirectory(dir);
  const safeName = (fileName || `omnichat_snapshot_${new Date().toISOString()}.html`)
    .replace(/[:<>"/\\|?*]/g, '-');
  const uri = vscode.Uri.joinPath(dir, safeName);
  await vscode.workspace.fs.writeFile(uri, utf8Bytes(html));
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc, { preview: false });
}

export function activateImpl(host: ImplHost): ImplApi {
  const messageBindings = new Map<WebviewSlot, vscode.Disposable>();
  const renderedPhase = new Map<WebviewSlot, BackendPhase>();

  // 2026-06-14 用户: "点一个区别把另俩刷了/老显示启动中"。重设 slot.webview.html = 重载 iframe,
  // 加上 cache-bust 每次都是新 URL, 所以任何 renderSlot 都会闪一次"启动中"。修法: 已经在显示就绪
  // iframe 的 slot, 后续仍是 ready 就别重渲(相位没变); 只有相位真变了(启动中→就绪/出错)或显式
  // 重载(force)才重渲。这样后台健康轮询/新开一个区都不会把已就绪的区刷掉。
  const renderSlot = (slot: WebviewSlot, status: BackendStatus, force = false) => {
    if (!force && status.phase === 'ready' && renderedPhase.get(slot) === 'ready') return;
    slot.webview.html = getWebviewHtml(getDashboardUrlForSlot(slot), status);
    renderedPhase.set(slot, status.phase);
  };

  const supervisor = new BackendSupervisor(host, () => {
    for (const slot of host.listSlots()) renderSlot(slot, supervisor.current);
  });

  const bindMessages = (slot: WebviewSlot): void => {
    messageBindings.get(slot)?.dispose();
    const binding = slot.webview.onDidReceiveMessage((msg: ChatHostMessage) => {
      if (msg.type === 'backend-restart') {
        void supervisor.restart();
        return;
      }
      if (msg.type === 'backend-reload') {
        renderSlot(slot, supervisor.current, true);
        void supervisor.ensureStarted();
        return;
      }
      if (msg.type === 'copy-to-clipboard') {
        // 网页里 navigator.clipboard / execCommand 都被 webview 限制时的最后一级降级
        // (lib/copyText.ts 第 3 级)。宿主侧 vscode.env.clipboard 永远可写。
        void vscode.env.clipboard.writeText(msg.text || '');
        return;
      }
      if (msg.type === 'open-material-native') {
        // 队列点项 / 材料页签"在 VSCode 打开" → 编辑区开一个材料正文页签(surface=material)。
        host.openMaterialPanel(msg.materialId, msg.title || msg.materialId);
        return;
      }
      if (msg.type === 'open-omnidashboard') {
        // 主侧栏 section 点条目 → 完整驾驶舱编辑页签, 深链到该条目。
        host.openOmnidashboardPanel(msg.openType, msg.openId, msg.title || msg.openId, msg.facet || undefined);
        return;
      }
      if (msg.type === 'open-in-claude-code') {
        // 在会话目录起终端跑 Claude Code CLI(VSCode 集成终端里即官方插件); resume 到这条具体对话。
        // claude --resume <session_id> 已实测: 提供真 id 会 resume 该对话(假 id 报 "No conversation found")。
        const sid = (msg.sessionId || '').trim();
        host.openTerminal(msg.cwd || '', sid ? `claude --resume ${sid}` : 'claude', 'Claude Code');
        return;
      }
      if (msg.type === 'open-codex-terminal') {
        // 在会话目录起终端跑 codex resume 到这条具体对话 + yolo(全自动)。已实测 flag/语法:
        // codex resume <session_id> --dangerously-bypass-approvals-and-sandbox (codex 无 --yolo 别名)。
        const sid = (msg.sessionId || '').trim();
        const cmd = sid
          ? `codex resume ${sid} --dangerously-bypass-approvals-and-sandbox`
          : 'codex resume --last --dangerously-bypass-approvals-and-sandbox';
        host.openTerminal(msg.cwd || '', cmd, 'Codex');
        return;
      }
      if (msg.type === 'restore-region-internal') {
        // "回 omnichat": 主侧栏已无完整壳折叠区, 改在编辑区开完整驾驶舱(落总控首页)。
        host.openOmnidashboardPanel('controller', 'main', '总控');
        return;
      }
      if (msg.type === 'focus-native-view') {
        // dashboard 里某区"在 VSCode 打开" → 聚焦对应原生视图(队列/评论)。
        host.focusView(msg.viewId);
        return;
      }
      if ('sessionId' in msg && msg.sessionId) {
        slot.state.sessionId = msg.sessionId;
      }
      if (msg.type === 'session-state') {
        slot.state.state = msg.state;
      } else if (msg.type === 'session-preview') {
        slot.state.preview = (msg.preview || '').trim() || null;
      } else if (msg.type === 'open-file') {
        void openLocalFile(msg.path, msg.line, msg.column);
        return;
      } else if (msg.type === 'save-snapshot') {
        void saveSnapshot(host.context, msg.html, msg.fileName);
        return;
      }
      slot.setTitle(composeTitle(slot.state.state, slot.state.preview));
    });
    messageBindings.set(slot, binding);
  };

  supervisor.startMonitor();
  void supervisor.ensureStarted();

  return {
    attachWebview(slot: WebviewSlot): void {
      bindMessages(slot);
      renderSlot(slot, supervisor.current);
      void supervisor.ensureStarted();
    },

    handleCommand(command: string): void {
      switch (command) {
        case 'omniChat.backendStatus':
          supervisor.showStatusOutput();
          break;
        case 'omniChat.restartBackend':
          void supervisor.restart();
          break;
        case 'omniChat.reloadWebviews':
          for (const slot of host.listSlots()) renderSlot(slot, supervisor.current, true);
          void supervisor.ensureStarted();
          break;
        default:
          host.log(`impl: unknown command ${command}`);
      }
    },

    dispose(): void {
      for (const binding of messageBindings.values()) binding.dispose();
      messageBindings.clear();
      supervisor.dispose();
    },
  };
}

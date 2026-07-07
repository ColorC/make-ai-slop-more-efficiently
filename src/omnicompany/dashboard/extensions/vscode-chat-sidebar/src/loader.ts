/**
 * 永久薄壳 loader — vsix 里装的就是这一层, 目标是"几乎永不再改"。
 *
 * 职责 (固定集, 与 package.json 声明一一对应):
 * - 注册命令 + 侧栏 webview provider, 全部转发给 impl
 * - 管理 webview slot 生命周期 (会话状态挂 slot 上, impl 热换不丢)
 * - 从仓库 out/impl.js 动态 require 实现层 (优先仓库工作树, 兜底 vsix 自带)
 * - 每 5s 轮询 dashboard /api/dev/versions, ext token 变了就热换 impl
 *
 * 热换覆盖不了的: package.json 的 contributes (新命令/新视图/新配置项) 与本文件
 * 自身 — 那种改动走"重发 vsix + 重启扩展宿主"(比重载窗口轻, 终端进程不死)。
 */

import * as fs from 'fs';
import * as http from 'http';
import * as path from 'path';
import * as vscode from 'vscode';
import type { ImplApi, ImplModule, WebviewSlot, SlotSurface } from './types';

const POLL_MS = 5000;

let impl: ImplApi | null = null;
let implFile: string | null = null;
let lastExtToken: string | null = null;
let pollTimer: NodeJS.Timeout | null = null;
let output: vscode.OutputChannel;
const slots = new Set<WebviewSlot>();

function cfg<T>(key: string, fallback: T): T {
  return vscode.workspace.getConfiguration('omniChat').get<T>(key) ?? fallback;
}

function log(message: string): void {
  output?.appendLine(`[${new Date().toISOString()}] ${message}`);
}

function isBackendRoot(dir: string): boolean {
  return fs.existsSync(path.join(dir, 'src', 'omnicompany', 'dashboard', 'app.py'))
    && fs.existsSync(path.join(dir, 'src', 'omnicompany', 'dashboard', 'ccdaemon', 'main.py'));
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

/** impl 寻址: 仓库工作树优先 (热更新的源头), vsix 自带的做兜底。 */
function implCandidates(): string[] {
  const candidates: string[] = [];
  const root = findBackendRoot();
  if (root) {
    candidates.push(path.join(root, 'src', 'omnicompany', 'dashboard',
      'extensions', 'vscode-chat-sidebar', 'out', 'impl.js'));
  }
  candidates.push(path.join(__dirname, 'impl.js'));
  return candidates;
}

function loadImplModule(): { mod: ImplModule; file: string } {
  for (const file of implCandidates()) {
    if (!fs.existsSync(file)) continue;
    const resolved = require.resolve(file);
    delete require.cache[resolved];
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const mod = require(resolved) as ImplModule;
    if (typeof mod.activateImpl !== 'function') {
      throw new Error(`${file} 缺少 activateImpl 导出`);
    }
    return { mod, file };
  }
  throw new Error(`impl.js 不存在, 找过: ${implCandidates().join(' | ')}`);
}

function fallbackHtml(detail: string): string {
  return `<!DOCTYPE html><html><body style="background:#0f0f0f;color:#d6deeb;font-family:sans-serif;padding:24px">
  <h3>OmniChat impl 未加载</h3><p style="color:#9fb0c6;font-size:13px">${detail.replace(/</g, '&lt;')}</p>
  <p style="font-size:12px">修复 impl 后跑 <code>omni dashboard ext-reload</code>, 或命令面板执行 "Omni Chat: Hot Reload Impl"。</p>
  </body></html>`;
}

function attachSlot(slot: WebviewSlot): void {
  if (!impl) {
    slot.webview.html = fallbackHtml(implFile ? `上次加载自 ${implFile}` : '尚未成功加载过 impl');
    return;
  }
  try {
    impl.attachWebview(slot);
  } catch (e) {
    log(`attachWebview failed: ${e}`);
    slot.webview.html = fallbackHtml(String(e));
  }
}

function swapImpl(context: vscode.ExtensionContext, reason: string): void {
  let next: { mod: ImplModule; file: string };
  try {
    next = loadImplModule();
  } catch (e) {
    log(`hot-swap aborted (${reason}): ${e}`);
    return;
  }
  try {
    impl?.dispose();
  } catch (e) {
    log(`old impl dispose error (ignored): ${e}`);
  }
  impl = null;
  try {
    impl = next.mod.activateImpl({
      context,
      listSlots: () => [...slots],
      log,
      openMaterialPanel,
      openOmnidashboardPanel,
      openTerminal,
      focusView,
    });
    implFile = next.file;
    log(`impl loaded (${reason}) from ${next.file}`);
  } catch (e) {
    log(`impl activate failed (${reason}): ${e}`);
  }
  for (const slot of slots) attachSlot(slot);
}

function fetchVersions(): Promise<{ ext?: string } | null> {
  const port = cfg<number>('dashboardPort', 8210);
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/api/dev/versions`, { timeout: 1500 }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch { resolve(null); }
      });
    });
    req.on('timeout', () => { req.destroy(); resolve(null); });
    req.on('error', () => resolve(null));
  });
}

function startVersionPoll(context: vscode.ExtensionContext): void {
  pollTimer = setInterval(async () => {
    const versions = await fetchVersions();
    const token = versions?.ext;
    if (!token) return;
    if (lastExtToken === null) {
      lastExtToken = token;
      return;
    }
    if (token !== lastExtToken) {
      lastExtToken = token;
      swapImpl(context, `ext token changed -> ${token}`);
    }
  }, POLL_MS);
  context.subscriptions.push({ dispose: () => pollTimer && clearInterval(pollTimer) });
}

function makeSlot(webview: vscode.Webview, setTitle: (t: string) => void, surface?: SlotSurface): WebviewSlot {
  return {
    webview,
    setTitle,
    state: { sessionId: null, state: 'idle', preview: null },
    surface,
  };
}

/** 编辑器页签 panel 统一接管 — 新建与重载还原共用同一套 slot 接线。 */
function adoptPanel(panel: vscode.WebviewPanel): void {
  const slot = makeSlot(panel.webview, (t) => { panel.title = t; });
  slots.add(slot);
  panel.onDidDispose(() => slots.delete(slot));
  attachSlot(slot);
}

function openInEditor(context: vscode.ExtensionContext): void {
  const panel = vscode.window.createWebviewPanel(
    'omniChat',
    'new chat',
    vscode.ViewColumn.Active,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
    },
  );
  adoptPanel(panel);
  context.subscriptions.push(panel);
}

/** 单区视图 provider: 同一份前端按 ?surface=<kind> 只渲染一个区(主侧栏各 section / 次级侧栏评论)。 */
class SurfaceViewProvider implements vscode.WebviewViewProvider {
  constructor(private readonly surface: SlotSurface) {}
  resolveWebviewView(webviewView: vscode.WebviewView): void {
    webviewView.webview.options = { enableScripts: true };
    const slot = makeSlot(webviewView.webview, (t) => { webviewView.title = t; }, this.surface);
    slots.add(slot);
    webviewView.onDidDispose(() => slots.delete(slot));
    attachSlot(slot);
  }
}

/** 在编辑区开一个"材料正文"页签(?surface=material&id)。viewType 复用 omniChat;
 * 窗口重载时 serializer 会把它当完整壳还原(丢 surface)—— 临时材料页, 用户从队列再点开即可。 */
function openMaterialPanel(materialId: string, title: string): void {
  const panel = vscode.window.createWebviewPanel(
    'omniChat',
    title || materialId,
    vscode.ViewColumn.Active,
    { enableScripts: true, retainContextWhenHidden: true },
  );
  const slot = makeSlot(panel.webview, (t) => { panel.title = t; }, { kind: 'material', id: materialId });
  panel.title = title || materialId;
  slots.add(slot);
  panel.onDidDispose(() => slots.delete(slot));
  attachSlot(slot);
}

/** 完整驾驶舱编辑页签, 深链到某条目(项目/计划/对话/札记 在 omnidashboard 打开)。 */
function openOmnidashboardPanel(openType: string, openId: string, title: string, facet?: string): void {
  const panel = vscode.window.createWebviewPanel(
    'omniChat',
    title || openId,
    vscode.ViewColumn.Active,
    { enableScripts: true, retainContextWhenHidden: true },
  );
  const slot = makeSlot(panel.webview, (t) => { panel.title = t; });
  slot.deeplink = { openType, openId, openFacet: facet };
  panel.title = title || openId;
  slots.add(slot);
  panel.onDidDispose(() => slots.delete(slot));
  attachSlot(slot);
}

function openTerminal(cwd: string, command: string, name?: string): void {
  const opts: vscode.TerminalOptions = { name: name || 'omni' };
  if (cwd && fs.existsSync(cwd)) opts.cwd = cwd;
  const term = vscode.window.createTerminal(opts);
  term.show();
  if (command) term.sendText(command);
}

function focusView(viewId: string): void {
  void vscode.commands.executeCommand(`${viewId}.focus`);
}

export function activate(context: vscode.ExtensionContext): void {
  output = vscode.window.createOutputChannel('OmniChat Loader');
  context.subscriptions.push(output);

  context.subscriptions.push(
    vscode.commands.registerCommand('omniChat.openInEditor', () => openInEditor(context)),
    vscode.commands.registerCommand('omniChat.focusSidebar', () => {
      void vscode.commands.executeCommand('omniChat.sidebar.focus');
    }),
    vscode.commands.registerCommand('omniChat.backendStatus', () => impl?.handleCommand('omniChat.backendStatus')),
    vscode.commands.registerCommand('omniChat.restartBackend', () => impl?.handleCommand('omniChat.restartBackend')),
    vscode.commands.registerCommand('omniChat.reloadWebviews', () => impl?.handleCommand('omniChat.reloadWebviews')),
    vscode.commands.registerCommand('omniChat.hotReloadImpl', () => swapImpl(context, 'manual command')),
  );

  // 主侧栏改成 5 个 section(项目/计划/对话/审阅材料/札记), 不再有"完整 omnichat 折叠区"
  // (2026-06-14 用户: 去掉那个 omnichat 区)。完整驾驶舱改由 openInEditor / 条目"在 omnidashboard 打开"
  // 在编辑区开。评论(comments)单列, 可拖到次级侧栏。
  const reg = (viewId: string, kind: SlotSurface['kind']) =>
    vscode.window.registerWebviewViewProvider(viewId, new SurfaceViewProvider({ kind }), { webviewOptions: { retainContextWhenHidden: true } });
  context.subscriptions.push(
    reg('omniChat.project', 'project'),
    reg('omniChat.plan', 'plan'),
    reg('omniChat.threads', 'threads'),
    reg('omniChat.multiagent', 'multiagent'),
    reg('omniChat.queue', 'queue'),
    reg('omniChat.reviewOverview', 'review-overview'),
    reg('omniChat.authored', 'authored'),
    reg('omniChat.comments', 'comments'),
  );

  // 窗口重载后还原编辑器页签里的 cockpit panel — 没有 serializer 的 webview panel
  // 会在 reload 时被 VSCode 直接关掉 (2026-06-12 用户实测: 其他窗口都在, 唯独它没了)。
  // 配套 package.json activationEvents: onWebviewPanel:omniChat (还原时唤醒本扩展)。
  context.subscriptions.push(
    vscode.window.registerWebviewPanelSerializer('omniChat', {
      async deserializeWebviewPanel(panel: vscode.WebviewPanel): Promise<void> {
        panel.webview.options = { enableScripts: true };
        adoptPanel(panel);
      },
    }),
  );

  swapImpl(context, 'activate');
  startVersionPoll(context);
}

export function deactivate(): void {
  try {
    impl?.dispose();
  } catch {
    // extension host 正在关, 忽略
  }
  impl = null;
}

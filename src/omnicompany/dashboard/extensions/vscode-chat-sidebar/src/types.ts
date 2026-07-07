/**
 * loader ↔ impl 的契约类型 — 免重启更新的关键缝合面。
 *
 * loader (永久薄壳, 装进 vsix 后几乎不再变) 与 impl (可热换实现层, 从仓库
 * out/impl.js 动态 require) 只通过这里的接口交互。改这个文件 = 改契约 =
 * 需要重发 vsix + 重启扩展宿主, 所以保持最小; 日常功能演进只动 impl.ts。
 *
 * 会话状态 (sessionId/preview) 挂在 loader 拥有的 slot 上, impl 热换后不丢。
 */

import type * as vscode from 'vscode';

export type SessionState = 'idle' | 'processing' | 'awaiting_permission' | 'ended';

export interface SlotSessionBag {
  sessionId: string | null;
  state: SessionState;
  preview: string | null;
}

/** 单区表面: 这个 webview 只渲染哪个语义区。无 = 完整驾驶舱(?surface=full/缺省)。
 * 主侧栏 section: project/plan/threads/queue/authored; 编辑页签: material; 次级侧栏: comments。 */
export interface SlotSurface {
  kind: 'queue' | 'material' | 'comments' | 'project' | 'plan' | 'threads' | 'authored' | 'multiagent' | 'review-overview';
  id?: string;
}

/** 一个活着的 webview 容器 (侧栏视图或编辑器页签), 生命周期由 loader 管理。 */
export interface WebviewSlot {
  webview: vscode.Webview;
  setTitle: (title: string) => void;
  state: SlotSessionBag;
  /** 设了就把前端按单区渲染挂进该表面(主侧栏 section / 编辑页签材料 / 次级侧栏评论)。 */
  surface?: SlotSurface;
  /** 完整驾驶舱编辑页签深链到某条目(?open_type&open_id&open_facet) —— 侧栏点条目"在 omnidashboard 打开"。 */
  deeplink?: { openType: string; openId: string; openFacet?: string };
}

export interface ImplHost {
  context: vscode.ExtensionContext;
  listSlots(): WebviewSlot[];
  log(message: string): void;
  /** 在编辑区开一个"材料正文"页签(?surface=material&id=…)。三区化: 队列点项 → 这里。 */
  openMaterialPanel(materialId: string, title: string): void;
  /** 在编辑区开完整驾驶舱页签并深链到某条目(项目/计划/对话/札记 在 omnidashboard 打开)。 */
  openOmnidashboardPanel(openType: string, openId: string, title: string, facet?: string): void;
  /** 在某工作目录起一个终端并(可选)跑命令 —— claude --resume / codex resume 等。 */
  openTerminal(cwd: string, command: string, name?: string): void;
  /** 聚焦某个原生视图, 供旧"回 omnichat"等用。 */
  focusView(viewId: string): void;
}

export interface ImplApi {
  /** 渲染 webview html 并绑定消息处理 (重复调用要先解绑旧的, 幂等)。 */
  attachWebview(slot: WebviewSlot): void;
  /** package.json 里声明的业务命令转发到这里 (backendStatus/restartBackend/reloadWebviews)。 */
  handleCommand(command: string): void;
  /** 热换前清理: 定时器/输出通道/状态栏/消息绑定。子进程不杀 (由健康检查接管)。 */
  dispose(): void;
}

export interface ImplModule {
  activateImpl(host: ImplHost): ImplApi;
}

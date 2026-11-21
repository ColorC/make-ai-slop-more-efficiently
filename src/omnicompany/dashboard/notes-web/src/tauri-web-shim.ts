// 纯网页端 Tauri 垫片: 让 overlay-shell 笔记栈在浏览器里跑(无 Tauri)。
// @tauri-apps/api 的 invoke 底层读 window.__TAURI_INTERNALS__.invoke —— 这里把它接到 8210 的
// overlay-shell 笔记桥(/lofa/overlay/invoke), notes_* 命令落盘到主机的 overlay-note-store(与桌面 overlay-shell 共用笔记)。
// 文件搜索接 Dashboard → 本机 Overlay Shell 索引；其余高权限文件命令仍降级成安全空值。
//
// 必须在任何 @tauri-apps/api 调用前 import(notes-web.tsx 第一行 import 本模块)。
//
// 2026-08-16 修「画布在 Omnicompany Desktop 里永远卡在加载门」: 桌面端本身是 Tauri 宿主,
// window.__TAURI_INTERNALS__ 已作为只读属性存在(注入的 invoke 指向桌面端自己的 Rust),
// 无脑赋值 mock 直接 TypeError → 整包死在启动门。现在分两条路:
//   无宿主(纯浏览器) → 安装完整 mock(原行为);
//   有宿主(Desktop WebView) → 不能赋值, 只包装已有 internals 的 invoke 字段:
//     overlay 命令(notes_*/search/…)改走 HTTP 桥, 其余透传宿主原实现。
//
// 2026-08-17 修「画布桌面壳 notes_canvas_get not allowed by ACL」: 宿主若冻结了
// __TAURI_INTERNALS__(如 Omnicompany Desktop), 下面的运行时赋值会被 catch 吞掉,
// notes_* 直打宿主 Rust 撞 ACL。真正生效的拦截已挪到构建期 —— 见 tauriCoreBridge.ts +
// vite.web.config.ts 的 resolve.alias(把裸 import "@tauri-apps/api/core" 整体改道)。
// 这里的运行时包装保留作双保险(宿主未冻结时依然生效), 失败时不再静默。
import { installDoubleCtrlForwarder, installWebClipboardCompat } from "./webRuntimeCompat";
import { bridgeInvoke, isBridgeCmd } from "./tauriBridgeCommands";

const warnedUnknownCmds = new Set<string>();

installWebClipboardCompat();
installDoubleCtrlForwarder();

const existing = (window as any).__TAURI_INTERNALS__ as Record<string, any> | undefined;

if (existing && typeof existing.invoke === "function") {
  // 真实 Tauri 宿主(如 Omnicompany Desktop): 只读属性, 包装 invoke 字段。
  const hostInvoke: (cmd: string, args: any) => Promise<any> = existing.invoke.bind(existing);
  try {
    existing.invoke = (cmd: string, args: any) =>
      isBridgeCmd(cmd) ? bridgeInvoke(cmd, args).catch((err) => {
        // 桥里不认识的命令还给宿主兜底, 避免一次性打断启动。
        const msg = String(err && (err as Error).message || err);
        if (msg.startsWith("bridge-unknown:")) return hostInvoke(cmd, args);
        throw err;
      }) : hostInvoke(cmd, args);
  } catch (err) {
    // 宿主冻结了 internals: 无法包装, 运行时这层兜底失效——但构建期的
    // tauriCoreBridge.ts alias 已经把 @tauri-apps/api/core 的 invoke 整体改道,
    // notes_* 等桥命令走的是那条路径, 不会打到宿主。这里只是留痕, 不再静默吞掉。
    console.warn(
      "[tauri-web-shim] 宿主冻结了 __TAURI_INTERNALS__, 运行时包装未生效"
        + "(已由构建期 @tauri-apps/api/core alias 兜底):",
      err,
    );
  }
} else {
  (window as any).__TAURI_INTERNALS__ = {
    invoke: (cmd: string, args: any) =>
      isBridgeCmd(cmd)
        ? bridgeInvoke(cmd, args)
        : (warnedUnknownCmds.has(cmd)
            ? Promise.resolve(null)
            : (warnedUnknownCmds.add(cmd),
              console.warn(`[tauri-web-shim] 未覆盖命令 "${cmd}" 已降级为 null`),
              Promise.resolve(null))),
    transformCallback: (cb: any) => cb,
    registerListener: () => {},
    unregisterListener: () => {},
    // @tauri-apps/api 的 getCurrentWindow/getCurrentWebview 读 metadata; 网页端给个假身份避免崩。
    metadata: {
      currentWindow: { label: "main" },
      currentWebview: { windowLabel: "main", label: "main" },
    },
    convertFileSrc: (p: string) => p,
  };
}

try {
  (window as any).__TAURI_EVENT_PLUGIN_INTERNALS__ = {
    ...(window as any).__TAURI_EVENT_PLUGIN_INTERNALS__,
    unregisterListener: () => {},
  };
} catch {
  /* 宿主只读时跳过(事件反注册失灵不影响画布加载) */
}

export {};

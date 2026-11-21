// 构建期拦截 @tauri-apps/api/core。
//
// 背景: tauri-web-shim.ts 原先只能在运行时给 window.__TAURI_INTERNALS__.invoke 赋值来
// 把 notes_*/search 等桥命令改道 HTTP —— 但真实 Tauri 宿主(Omnicompany Desktop)会冻结
// __TAURI_INTERNALS__, 赋值抛 TypeError, 一旦被 catch 吞掉, notes_* 就直接打到宿主 Rust,
// 撞上 Tauri ACL(notes_canvas_get not allowed by ACL)。运行时可变性不可靠, 改在编译期解决:
// vite.web.config.ts 用 resolve.alias 把裸 import "@tauri-apps/api/core" 整体指到本文件,
// 所有 `import { invoke } from "@tauri-apps/api/core"` 从一开始拿到的就是这里的 invoke,
// 与宿主是否冻结 internals 无关。
//
// 除 invoke 外的其余导出(Channel/transformCallback/convertFileSrc/isTauri/…)原样从包内
// 真实实现按相对路径转发——它们内部都是调用时才读 window.__TAURI_INTERNALS__ 的对应字段,
// 天然透传宿主或 tauri-web-shim.ts 装好的 mock, 不需要在这里重新实现。
//
// 注意: 本文件绝不能 import 裸的 "@tauri-apps/api/core"/"@tauri-apps/api"——那会命中
// vite.web.config.ts 里的同一条 alias, 造成自我递归。所有到真实包的引用一律走相对路径。
import { bridgeInvoke, isBridgeCmd } from "./tauriBridgeCommands";
import type { InvokeArgs, InvokeOptions } from "../node_modules/@tauri-apps/api/core.js";

export {
  Channel,
  PluginListener,
  Resource,
  SERIALIZE_TO_IPC_FN,
  addPluginListener,
  checkPermissions,
  convertFileSrc,
  isTauri,
  requestPermissions,
  transformCallback,
} from "../node_modules/@tauri-apps/api/core.js";
export type { InvokeArgs, InvokeOptions } from "../node_modules/@tauri-apps/api/core.js";

const warnedUnhandledCmds = new Set<string>();

function hostInvoke(): ((cmd: string, args?: InvokeArgs, options?: InvokeOptions) => Promise<unknown>) | undefined {
  const internals = (window as any).__TAURI_INTERNALS__;
  return typeof internals?.invoke === "function" ? internals.invoke.bind(internals) : undefined;
}

export async function invoke<T>(cmd: string, args: InvokeArgs = {}, options?: InvokeOptions): Promise<T> {
  if (isBridgeCmd(cmd)) {
    try {
      return (await bridgeInvoke(cmd, args)) as T;
    } catch (err) {
      const msg = String((err as Error)?.message ?? err);
      if (msg.startsWith("bridge-unknown:")) {
        const host = hostInvoke();
        if (host) return (await host(cmd, args, options)) as T;
      }
      throw err;
    }
  }
  const host = hostInvoke();
  if (host) return (await host(cmd, args, options)) as T;
  if (!warnedUnhandledCmds.has(cmd)) {
    warnedUnhandledCmds.add(cmd);
    console.warn(`[tauriCoreBridge] 无宿主也非桥命令 "${cmd}", 已降级为 null`);
  }
  return null as T;
}

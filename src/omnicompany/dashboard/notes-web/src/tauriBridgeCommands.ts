// overlay 笔记栈自己认的桥命令表: notes_*/search/file_inspect/… 一律走 HTTP 桥
// (webNotesInvoke → 8210 的 /lofa/overlay/invoke)或安全降级, 与 Tauri 宿主是否存在无关。
// 被 tauri-web-shim.ts(运行时包装, 纯浏览器/宽松宿主兜底)与 tauriCoreBridge.ts
// (构建期 alias 拦截 @tauri-apps/api/core, 冻结宿主下的唯一生效路径)共用, 只此一份定义。
import { copyPlainText } from "./webRuntimeCompat";
import { webNotesInvoke } from "./webNotesInvoke";

const BRIDGE_CMDS = new Set([
  "search",
  "file_inspect",
  "search_index_ready",
  "nb_pending",
  "nb_respond",
  "copy_text",
  "read_file_text",
  "read_file_b64",
  "write_file_text",
  "open_path",
  "reveal_path",
  "run_shell",
]);

export function isBridgeCmd(cmd: unknown): boolean {
  if (typeof cmd !== "string") return false;
  return cmd.startsWith("notes_") || BRIDGE_CMDS.has(cmd);
}

export function bridgeInvoke(cmd: string, args: any): Promise<any> {
  if (typeof cmd === "string" && cmd.startsWith("notes_")) return webNotesInvoke(cmd, args);
  switch (cmd) {
    case "search":
    case "file_inspect":
      return webNotesInvoke(cmd, args);
    case "search_index_ready":
      // The browser bridge does not own the desktop process' lazy arena lifecycle.
      return Promise.resolve(true);
    case "nb_pending":
    case "nb_respond":
      return webNotesInvoke(cmd, args);
    case "copy_text":
      return Promise.resolve(copyPlainText(String(args?.text ?? "")));
    case "read_file_text":
      return Promise.resolve("");
    case "read_file_b64":
      return Promise.resolve(null);
    case "write_file_text":
    case "open_path":
    case "reveal_path":
      return Promise.resolve(null);
    case "run_shell":
      return Promise.resolve({ stdout: "", stderr: "", code: 0 });
    default:
      return Promise.reject(new Error(`bridge-unknown:${cmd}`));
  }
}

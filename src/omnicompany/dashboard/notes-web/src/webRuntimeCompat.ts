const DOUBLE_CTRL_QUIET_MS = 340;

type ClipboardItemLike = {
  readonly types: readonly string[];
  getType(type: string): Promise<Blob>;
};

let installedClipboard: Clipboard | null = null;

function notAllowed(message: string): DOMException {
  return new DOMException(message, "NotAllowedError");
}

function ensureClipboardItem(): void {
  if (typeof window.ClipboardItem !== "undefined") return;
  class CompatClipboardItem implements ClipboardItemLike {
    readonly presentationStyle = "unspecified" as const;
    readonly types: readonly string[];
    private readonly data: Record<string, Blob | Promise<Blob>>;

    constructor(data: Record<string, Blob | Promise<Blob>>) {
      this.data = data;
      this.types = Object.keys(data);
    }

    async getType(type: string): Promise<Blob> {
      const value = this.data[type];
      if (!value) throw new DOMException(`Clipboard type not found: ${type}`, "NotFoundError");
      return await value;
    }

    static supports(_type: string): boolean {
      return true;
    }
  }
  Object.defineProperty(window, "ClipboardItem", {
    configurable: true,
    value: CompatClipboardItem,
  });
}

async function legacyCopy(text: string, html = ""): Promise<void> {
  if (typeof document.execCommand !== "function") {
    throw notAllowed("当前浏览器不支持复制命令");
  }
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const selection = window.getSelection();
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange())
    : [];
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none";
  document.body.appendChild(textarea);
  textarea.focus({ preventScroll: true });
  textarea.select();

  let handled = false;
  const onCopy = (event: ClipboardEvent) => {
    if (!event.clipboardData) return;
    handled = true;
    event.preventDefault();
    event.stopImmediatePropagation();
    event.clipboardData.setData("text/plain", text);
    if (html) event.clipboardData.setData("text/html", html);
  };
  document.addEventListener("copy", onCopy, true);
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    document.removeEventListener("copy", onCopy, true);
    textarea.remove();
    previous?.focus({ preventScroll: true });
    if (selection && ranges.length) {
      selection.removeAllRanges();
      for (const range of ranges) selection.addRange(range);
    }
  }
  if (!copied && !handled) throw notAllowed("浏览器拒绝了复制操作");
}

async function legacyWrite(items: ClipboardItem[]): Promise<void> {
  const item = items[0] as ClipboardItemLike | undefined;
  if (!item) return;
  const read = async (type: string) => {
    if (!item.types.includes(type)) return "";
    return (await item.getType(type)).text();
  };
  const [plain, html] = await Promise.all([read("text/plain"), read("text/html")]);
  let text = plain;
  if (!text && html) {
    text = new DOMParser().parseFromString(html, "text/html").body.textContent || "";
  }
  await legacyCopy(text, html);
}

/**
 * HTTP pages do not expose navigator.clipboard in Chromium. BlockSuite checks
 * for the object before registering all copy/paste/cut handlers, so install a
 * compatible wrapper and fall back to the user-gesture-backed legacy copy API.
 */
export function installWebClipboardCompat(): Clipboard {
  if (installedClipboard) return installedClipboard;
  ensureClipboardItem();

  const native = navigator.clipboard;
  const nativeRead = native?.read?.bind(native);
  const nativeReadText = native?.readText?.bind(native);
  const nativeWrite = native?.write?.bind(native);
  const nativeWriteText = native?.writeText?.bind(native);
  const compat = Object.assign(new EventTarget(), {
    read: nativeRead
      ? () => nativeRead()
      : () => Promise.reject(notAllowed("只能通过 Ctrl+V 读取剪贴板")),
    readText: nativeReadText
      ? () => nativeReadText()
      : () => Promise.reject(notAllowed("只能通过 Ctrl+V 读取剪贴板")),
    async write(items: ClipboardItems): Promise<void> {
      if (nativeWrite) {
        try {
          await nativeWrite(items);
          return;
        } catch {
          // Remote HTTP / iframe policy may reject the modern API.
        }
      }
      await legacyWrite(items);
    },
    async writeText(text: string): Promise<void> {
      if (nativeWriteText) {
        try {
          await nativeWriteText(text);
          return;
        } catch {
          // Fall through to execCommand while the keyboard/click gesture is live.
        }
      }
      await legacyCopy(text);
    },
  }) as Clipboard;
  try {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: compat,
    });
  } catch {
    // A secure-context browser can expose a non-configurable native object.
  }
  installedClipboard = compat;
  return compat;
}

export async function copyPlainText(text: string): Promise<void> {
  await installWebClipboardCompat().writeText(text);
}

/** Forward the same clean double-Ctrl gesture to the embedding Dashboard. */
export function installDoubleCtrlForwarder(): () => void {
  let ctrlHeld = false;
  let clean = false;
  let taps = 0;
  let timer: number | undefined;
  const reset = () => {
    ctrlHeld = false;
    clean = false;
    taps = 0;
    if (timer !== undefined) window.clearTimeout(timer);
    timer = undefined;
  };
  const keydown = (event: KeyboardEvent) => {
    if (event.key === "Control") {
      if (!event.repeat && !ctrlHeld) {
        ctrlHeld = true;
        clean = !event.altKey && !event.metaKey && !event.shiftKey;
      }
    } else if (ctrlHeld) {
      clean = false;
    }
  };
  const keyup = (event: KeyboardEvent) => {
    if (event.key !== "Control") {
      if (ctrlHeld) clean = false;
      return;
    }
    const cleanTap = ctrlHeld && clean && !event.altKey && !event.metaKey && !event.shiftKey;
    ctrlHeld = false;
    clean = false;
    if (!cleanTap) return;
    taps += 1;
    if (timer !== undefined) window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      const shouldOpen = taps >= 2;
      taps = 0;
      timer = undefined;
      if (shouldOpen && window.parent !== window) {
        window.parent.postMessage({ type: "omni:double-control" }, window.location.origin);
      }
    }, DOUBLE_CTRL_QUIET_MS);
  };
  window.addEventListener("keydown", keydown, true);
  window.addEventListener("keyup", keyup, true);
  window.addEventListener("blur", reset);
  return () => {
    reset();
    window.removeEventListener("keydown", keydown, true);
    window.removeEventListener("keyup", keyup, true);
    window.removeEventListener("blur", reset);
  };
}

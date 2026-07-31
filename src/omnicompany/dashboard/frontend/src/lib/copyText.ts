// 复制到剪贴板 — 全站唯一抽象(2026-06-12 复查: 项目卡 index 按钮在 VSCode webview iframe 里
// navigator.clipboard 被静默拒绝, 点了没反应)。三级降级:
// 1) navigator.clipboard (普通浏览器/安全上下文)
// 2) textarea + execCommand (受限 iframe 多数可用)
// 3) postMessage 给宿主 VSCode 扩展 → vscode.env.clipboard (extension impl.ts 的
//    'copy-to-clipboard' 消息处理; webview 外壳会把 __omnichat 消息转发给扩展)
// 返回是否成功(第 3 级为乐观成功 — 消息已交宿主)。调用方失败时必须给用户可见反馈。

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* 降级 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    ta.setAttribute('data-omni-capture-ignore', 'true')
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    if (ok) return true
  } catch { /* 降级 */ }
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ __omnichat: true, type: 'copy-to-clipboard', text }, '*')
      return true
    }
  } catch { /* 到底了 */ }
  return false
}

// 在 VSCode 里打开本地文件/目录 — 全站唯一抽象。
// 2026-07-06 用户两连击: "在 VSCode 打开持续失效, 要中转" → "该点了马上打开, 为什么要轮询式"。
// 最终形态 = 最短路优先: 后端与 VSCode 同机, POST /api/dev/open-file 让后端直接执行
// `code --goto 文件:行`(VSCode 官方 CLI) —— 点击即开, 零轮询、零消息桥、零确认框。
// 兜底两级(每级都给页面内可见反馈):
//   ② 后端直开不可用(如 code 不在 PATH) → /api/dev/request-open {kind:'file'} 入队,
//      桌面扩展长轮询领取调 openLocalFile(服务端挂 25s 长轮询, 实际也近即时);
//   ③ 后端整个不可达 → vscode://file 协议链接(顶层导航/新开页, 会弹确认框)。
// webview postMessage 桥不再参与文件打开(桥接失联是慢性病, 材料面板才需要它)。

import { notice } from './surface'

/** 把本地绝对路径拼成 vscode://file 官方链接(Windows 反斜杠归一, 段内编码保留 : 和 /)。 */
export function vscodeFileUrl(path: string, line?: number | null, column?: number | null): string {
  let p = path.trim().replace(/\\/g, '/')
  if (!p.startsWith('/')) p = '/' + p
  let url = 'vscode://file' + encodeURI(p)
  if (line && line > 0) {
    url += `:${line}`
    if (column && column > 0) url += `:${column}`
  }
  return url
}

/** 打开文件/目录。返回是否已发起(发起≠已打开; 每级链路都在页面右下角给可见反馈)。 */
export function openInVscode(path: string, line?: number | null): boolean {
  const p = (path || '').trim()
  if (!p) return false
  const base = p.replace(/\\/g, '/').split('/').filter(Boolean).pop() || p

  const viaProtocol = () => {
    // 最后兜底: vscode:// 协议。webview iframe 里导航多半被沙箱拦, 只在顶层窗口有意义。
    try {
      if (window.top === window) {
        window.location.href = vscodeFileUrl(p, line)
        return
      }
    } catch { /* 读不到 top = 跨源嵌入, 按嵌入处理 */ }
    try { window.open(vscodeFileUrl(p, line), '_blank') } catch { /* 到头了 */ }
  }

  const viaQueue = () => {
    void fetch('/api/dev/request-open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'file', id: line && line > 0 ? `${p}:${line}` : p, title: base }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
      notice(`已转交桌面 VSCode 扩展打开 ${base}(不抢前台焦点)。\n若一直没开: 没有活着的 VSCode 扩展在领取, 把这句话发给 AI。`, 'ok')
    }).catch((e) => {
      notice(`后端不可达: ${String(e).slice(0, 120)}\n改走 vscode:// 协议直开(可能弹确认框)…`, 'err')
      viaProtocol()
    })
  }

  // 最短路: 本机后端直接 `code --goto`(文件, 落最近活跃 VSCode 窗口)/`code 目录`(开文件夹窗口)。
  notice(`正在打开 ${base} …`, 'pending')
  void fetch('/api/dev/open-file', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: p, line: line ?? null }),
  }).then(async (r) => {
    if (!r.ok) throw new Error(`${r.status}`)
    const d = await r.json() as { dir?: boolean }
    notice(d.dir
      ? `已在 VSCode 打开文件夹 ${base} ✓(新窗口, 不抢焦点)`
      : `VSCode 已打开 ${base} ✓(落最近活跃窗口, 不抢焦点)`, 'ok')
  }).catch(() => { viaQueue() })
  return true
}

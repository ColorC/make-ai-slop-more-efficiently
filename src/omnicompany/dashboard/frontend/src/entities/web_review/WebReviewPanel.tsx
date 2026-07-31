import React from 'react'
import { RefreshCw, ExternalLink, Link2, Route as RouteIcon } from 'lucide-react'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { copyText } from '../../lib/copyText'

// 网页审阅面板 = 内嵌 iframe 看外部内容(walker-game / vilo demo 等), 经 dashboard 同源代理。
// 圈选元素 / 页面快照 仍走驾驶舱顶栏那一套(已扩展到能进同源 iframe); 不在面板内重复造圈选控件。
// 面板自有的"外壳 chrome"按 frostpane 重做标准:
//   · 无重复页签名的标题头(页签已标识身份, 内容从顶部直接开始)
//   · root 透明吃全局冷渐变, 玻璃工具条浮其上
//   · 主操作(重载)常显; 低频(新窗打开 / 复制链接·路由)收进共享 KebabMenu(⋯)
//   · iframe 本体保持白底 —— 那是外部内容, 不算面板 chrome, 不套玻璃
// 同源(经代理到各开发服务)是顶栏圈选能读到内容的前提。

export interface WebReviewTarget {
  title: string
  url: string
  route?: string
}

const S: Record<string, React.CSSProperties> = {
  // 面板 root: 透明吃 body 全局统一冷渐变(不铺实底把渐变顶掉)。
  root: {
    display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0,
    background: 'transparent', color: 'var(--fp-text)', boxSizing: 'border-box',
  },
  // 顶部工具条 = 玻璃外壳(磨砂 + 边缘高光), 浮在渐变上, 与下方白底 iframe 分层。
  bar: {
    display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
    padding: '8px 12px',
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    borderBottom: '1px solid var(--fp-border)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)',
  },
  // 当前审阅路由: 最弱微字(12px 等宽弱灰), 占位让出主焦点给内容本身。页签已标识身份, 这里只标"看的是哪条路由"。
  route: {
    flex: 1, minWidth: 0, color: 'var(--fp-text-3)', fontSize: 12,
    fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace",
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  },
  // 主操作: 安静图标钮(重载), hover 才浮一层极淡底 —— 唯一常显操作, 其余收进 ⋯。
  iconBtn: {
    width: 30, height: 30, border: '1px solid transparent', borderRadius: 7,
    background: 'transparent', color: 'var(--fp-text-3)', cursor: 'pointer',
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0,
    transition: 'background 150ms var(--fp-ease, ease), color 150ms var(--fp-ease, ease)',
  },
  // 内容区: 包住 iframe; 玻璃描边只在外框, iframe 本体仍是外部内容的白底。
  frame: { position: 'relative', flex: 1, minHeight: 0 },
  iframe: { display: 'block', width: '100%', height: '100%', border: 'none', background: '#fff' },
}

const WebReviewPanel: React.FC<{ target: WebReviewTarget }> = ({ target }) => {
  // 重载 nonce: 改变它即重新计算 src(追加新的一次性时间戳), 强制 webview 穿透缓存重拉。
  // 主操作"重载"驱动它, 让"改了 demo 立刻能看见"成为一次显式点击, 而非只靠重新挂载。
  const [reloadNonce, setReloadNonce] = React.useState(0)

  // 每次(挂载 / 重载)追加一次性时间戳, 强制重新拉 index.html(绕开 webview 把旧页面缓存死)。
  // 配合代理的 no-store + 引擎 ?v= 注入, 让"改了 demo 立刻能看见"。
  const src = React.useMemo(() => {
    const u = target.url || ''
    if (!u) return u
    return `${u}${u.includes('?') ? '&' : '?'}_cb=${Date.now()}`
    // reloadNonce 进依赖, 点重载就换新戳。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.url, reloadNonce])

  const kebabItems: KebabItem[] = [
    {
      label: '在新标签打开', icon: <ExternalLink size={14} />, testid: 'web-review-open-new',
      onClick: () => { if (target.url) window.open(target.url, '_blank', 'noopener') },
    },
    {
      label: '复制链接', icon: <Link2 size={14} />, testid: 'web-review-copy-url',
      onClick: () => { if (target.url) void copyText(target.url) },
    },
  ]
  if (target.route) {
    kebabItems.push({
      label: '复制路由', icon: <RouteIcon size={14} />, testid: 'web-review-copy-route',
      onClick: () => { void copyText(target.route!) },
    })
  }

  return (
    <div style={S.root} data-testid="web-review-panel">
      {/* 无标题头: 不重复页签名("网页审阅"); 仅一条玻璃工具条, 左侧弱灰标当前路由, 右侧主操作 + ⋯。 */}
      <div style={S.bar}>
        <span style={S.route} title={target.url || target.title}>{target.route || target.url || target.title}</span>
        <button
          type="button"
          style={S.iconBtn}
          title="重载(穿透缓存)"
          data-testid="web-review-reload"
          onClick={() => setReloadNonce((n) => n + 1)}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,.06)'; e.currentTarget.style.color = 'var(--fp-text)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fp-text-3)' }}
        >
          <RefreshCw size={15} />
        </button>
        <KebabMenu items={kebabItems} testid="web-review-actions" iconSize={15} />
      </div>
      {/* iframe 本体: 外部内容, 保持白底, 不套玻璃。 */}
      <div style={S.frame}>
        <iframe
          key={reloadNonce}
          data-testid="web-review-iframe"
          src={src}
          style={S.iframe}
          title={target.title}
        />
      </div>
    </div>
  )
}

export default WebReviewPanel

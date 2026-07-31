/**
 * CollapsibleSessionContext — 给编辑区右侧的"会话上下文 / 札记侧栏"加可收起开关。
 *
 * 用户诉求(2026-06-14): omnichat 编辑区的札记侧栏要能收起; 且检测到(vscode 原生)侧栏
 * 已展开时默认收起 —— 否则编辑区右侧 + 原生侧栏两条栏并存太挤。
 *
 * 实现要点:
 *  - 不改 SessionContextPanel 本体(它由别的 worker 在动), 只在外层包一条 24px 开关 rail。
 *  - 默认收起判据: 前端 iframe 拿不到原生侧栏开合状态, 用视口宽度做代理 —— 原生侧栏一展开
 *    就把编辑区 webview 挤窄, innerWidth 落到阈值下 → 默认收起。
 *  - 用户手动展开/收起后写 localStorage 偏好, 之后不再被宽度自动覆盖(尊重显式选择)。
 */
import React, { useEffect, useState } from 'react'
import { PanelRightOpen, PanelRightClose } from 'lucide-react'
import SessionContextPanel from './SessionContextPanel'

const PREF_KEY = 'omni.editor.ctxCollapsed' // '1'=收起 '0'=展开 缺省=跟随宽度
const NARROW_PX = 1180

function readPref(): 'open' | 'closed' | null {
  try {
    const v = localStorage.getItem(PREF_KEY)
    return v === '1' ? 'closed' : v === '0' ? 'open' : null
  } catch { return null }
}
function writePref(collapsed: boolean) {
  try { localStorage.setItem(PREF_KEY, collapsed ? '1' : '0') } catch { /* privacy mode 兜底 */ }
}

interface Props {
  sessionId: string
  alive: boolean
  kind?: 'cc' | 'native'
}

export default function CollapsibleSessionContext({ sessionId, alive, kind }: Props) {
  const [pref, setPref] = useState<'open' | 'closed' | null>(() => readPref())
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.innerWidth < NARROW_PX)
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < NARROW_PX)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const collapsed = pref === 'closed' ? true : pref === 'open' ? false : narrow
  const setCollapsed = (c: boolean) => { setPref(c ? 'closed' : 'open'); writePref(c) }

  return (
    <div style={{ display: 'flex', flexShrink: 0, height: '100%' }} data-ctx-collapsible data-ctx-collapsed={collapsed ? '1' : '0'}>
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        title={collapsed ? '展开会话上下文 / 札记' : '收起会话上下文 / 札记'}
        data-testid="session-ctx-toggle"
        // 透明吃宿主全局冷渐变(不铺实底把渐变顶掉); 颜色全 var(--fp-*), hover 才浮一层极淡底。
        style={{
          width: 24, flexShrink: 0, border: 'none', borderLeft: '1px solid var(--fp-border)',
          background: 'transparent', color: 'var(--fp-text-3)', cursor: 'pointer',
          display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: 10,
          transition: 'background 150ms var(--fp-ease, ease), color 150ms var(--fp-ease, ease)',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,.06)'; e.currentTarget.style.color = 'var(--fp-text)' }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--fp-text-3)' }}
      >
        {collapsed ? <PanelRightOpen size={16} /> : <PanelRightClose size={16} />}
      </button>
      {!collapsed && <SessionContextPanel sessionId={sessionId} alive={alive} kind={kind} />}
    </div>
  )
}

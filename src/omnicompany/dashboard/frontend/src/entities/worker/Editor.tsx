import React, { useState } from 'react'
import type { WorkerEntity } from './resolver'
import Design from './facets/Design'
import Live from './facets/Live'
import History from './facets/History'

const FACETS = [
  { key: 'design', label: '设计', Component: Design },
  { key: 'live', label: '运行', Component: Live },
  { key: 'history', label: '历史', Component: History },
] as const

const S: Record<string, any> = {
  // 面板 root 透明, 吃 body 全局冷渐变(不铺实底把渐变顶掉)。
  root: { display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent', color: 'var(--fp-text)' },
  // facet 切换条 = shadcn 风分段控件: 圆角胶囊容器(玻璃淡底), 选中页与内容同色无缝, 无底部分割线。
  facetBar: {
    display: 'inline-flex', alignSelf: 'flex-start', gap: 2, margin: '10px 14px 8px',
    padding: 3, borderRadius: 9, background: 'var(--fp-surface)', border: '1px solid var(--fp-border-subtle)',
  },
  facetBtn: (active: boolean): React.CSSProperties => ({
    padding: '4px 14px', borderRadius: 7, border: '1px solid transparent', cursor: 'pointer',
    // 选中: 浮一层玻璃 + 高光描边(与内容同质无缝); 未选: 透明凹陷弱字。
    background: active ? 'var(--fp-glass)' : 'transparent',
    boxShadow: active ? 'inset 0 1px 0 rgba(255,255,255,.08)' : 'none',
    borderColor: active ? 'var(--fp-border)' : 'transparent',
    color: active ? 'var(--fp-text)' : 'var(--fp-text-3)',
    fontSize: 14, fontWeight: active ? 600 : 500,
    transition: 'background 150ms cubic-bezier(0.175,0.885,0.32,1.1), color 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  body: { flex: 1, overflow: 'hidden', minHeight: 0 },
}

export default function WorkerEditor({ entity, facet }: { entity: WorkerEntity; facet?: string }) {
  const [current, setCurrent] = useState<string>(facet || 'design')
  const Active = (FACETS.find((f) => f.key === current) || FACETS[0]).Component

  return (
    <div style={S.root} data-testid="worker-editor">
      {/* 无标题头(Linear 风): dockview 页签已标识 worker 身份, 不重复一条标题栏。
          内部 facet 切换对齐 shadcn 分段控件(选中无缝/无分割线)。 */}
      <div style={S.facetBar} role="tablist" data-testid="worker-facet-bar">
        {FACETS.map((f) => {
          const active = current === f.key
          return (
            <button
              key={f.key}
              type="button"
              role="tab"
              aria-selected={active}
              style={S.facetBtn(active)}
              data-testid={`worker-facet-${f.key}`}
              data-active={active ? '1' : '0'}
              onClick={() => setCurrent(f.key)}
              onMouseEnter={(e) => { if (!active) e.currentTarget.style.color = 'var(--fp-text-2)' }}
              onMouseLeave={(e) => { if (!active) e.currentTarget.style.color = 'var(--fp-text-3)' }}
            >
              {f.label}
            </button>
          )
        })}
      </div>
      <div style={S.body}>
        <Active entity={entity} />
      </div>
    </div>
  )
}

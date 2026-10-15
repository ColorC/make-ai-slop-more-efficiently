// retired 实体壳 — M4 旧界面下线: 注册保留(旧深链不生 404), 打开渲染退役提示+迁移指引。
// 组件代码只换渲染壳不删实现; 旧数据(data/boss_sight/reviewstage/ 等)只读保留。
import React from 'react'

export interface RetiredSurfaceProps {
  title: string
  lines: string[]
  link?: { href: string; label: string }
}

export function RetiredSurface({ title, lines, link }: RetiredSurfaceProps): React.ReactElement {
  return (
    <div style={{
      height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#0d1522', color: '#c9d4e3', padding: 24,
    }}>
      <div style={{
        maxWidth: 560, border: '1px solid #31445b', borderRadius: 12,
        background: 'rgba(18, 30, 46, 0.94)', padding: '28px 32px',
      }}>
        <h2 style={{ margin: '0 0 14px', fontSize: 18, color: '#e5eefb' }}>{title}</h2>
        {lines.map((line) => (
          <p key={line} style={{ margin: '8px 0', fontSize: 13, lineHeight: 1.7 }}>{line}</p>
        ))}
        {link && (
          <p style={{ marginTop: 16 }}>
            <a href={link.href} target="_blank" rel="noopener noreferrer" style={{ color: '#73b8ff' }}>
              {link.label} ↗
            </a>
          </p>
        )}
      </div>
    </div>
  )
}

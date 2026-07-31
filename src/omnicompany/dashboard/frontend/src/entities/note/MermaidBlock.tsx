import React, { useEffect, useRef, useState } from 'react'

let mermaidPromise: Promise<typeof import('mermaid').default> | null = null
let inited = false

function loadMermaid(): Promise<typeof import('mermaid').default> {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((m) => m.default)
  }
  return mermaidPromise
}

let counter = 0

export default function MermaidBlock({ source }: { source: string }) {
  const [svg, setSvg] = useState<string>('')
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const idRef = useRef(`mmd-${++counter}-${Date.now()}`)

  useEffect(() => {
    let cancelled = false
    setSvg(''); setErr(null); setLoading(true)
    loadMermaid().then((mermaid) => {
      if (cancelled) return
      if (!inited) {
        mermaid.initialize({
          startOnLoad: false,
          theme: 'dark',
          securityLevel: 'loose',
          fontFamily: 'Consolas, Menlo, monospace',
        })
        inited = true
      }
      return mermaid.render(idRef.current, source)
    }).then((res) => {
      if (cancelled || !res) return
      setSvg(res.svg)
      setLoading(false)
    }).catch((e) => {
      if (!cancelled) {
        setErr(String(e?.message || e))
        setLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [source])

  if (err) {
    return (
      <div data-mermaid-error="true" style={{ background: '#1a0a0a', border: '1px solid #4a2222', borderRadius: 4, padding: 12, color: '#ef5350', fontSize: 14, fontFamily: 'Consolas, Menlo, monospace' }}>
        Mermaid 解析错误: {err}
        <pre style={{ marginTop: 8, color: '#888', whiteSpace: 'pre-wrap' as const }}>{source}</pre>
      </div>
    )
  }

  return (
    <div
      data-mermaid="true"
      style={{ background: 'var(--fp-bg)', padding: 12, borderRadius: 4, overflow: 'auto', textAlign: 'center' as const }}
      dangerouslySetInnerHTML={{ __html: svg || (loading ? '<div style="color:#666;font-size:13px">加载 mermaid...</div>' : '') }}
    />
  )
}

/**
 * FileViewer — 文件内容查看 + Diff 对比
 *
 * 使用 Monaco Editor（lazy load）。
 * 支持只读查看和 diff 模式。
 */

import React, { Suspense, useState } from 'react'
import { useIDEStore } from '../../stores/ideStore'
import type { FileChange } from '../../api/ideClient'

// Lazy load Monaco to avoid 2MB initial bundle
const MonacoEditor = React.lazy(() =>
  import('@monaco-editor/react').then((m) => ({ default: m.default }))
)
const MonacoDiffEditor = React.lazy(() =>
  import('@monaco-editor/react').then((m) => ({ default: m.DiffEditor }))
)

function detectLanguage(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript',
    js: 'javascript', jsx: 'javascript',
    py: 'python',
    rs: 'rust',
    go: 'go',
    json: 'json',
    yaml: 'yaml', yml: 'yaml',
    md: 'markdown',
    html: 'html',
    css: 'css',
    sh: 'shell', bash: 'shell',
    sql: 'sql',
    lua: 'lua',
    cs: 'csharp',
    xml: 'xml',
    toml: 'ini',
  }
  return map[ext] || 'plaintext'
}

export default function FileViewer() {
  const { fileChanges } = useIDEStore()
  const [activeIdx, setActiveIdx] = useState(0)

  if (fileChanges.length === 0) {
    return (
      <div style={{ padding: 12, color: '#444', fontSize: 14 }}>
        No file changes yet
      </div>
    )
  }

  const file = fileChanges[activeIdx] || fileChanges[0]
  const isDiff = file.action === 'edit' && file.old_text && file.new_text
  const language = detectLanguage(file.path)

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Tab bar */}
      <div
        style={{
          display: 'flex',
          gap: 1,
          background: 'var(--fp-bg)',
          borderBottom: '1px solid #222',
          overflow: 'auto',
          flexShrink: 0,
        }}
      >
        {fileChanges.map((fc, i) => {
          const name = fc.path.split('/').pop() || fc.path
          return (
            <button
              key={i}
              onClick={() => setActiveIdx(i)}
              style={{
                padding: '4px 10px',
                background: i === activeIdx ? '#1a1a2a' : 'transparent',
                border: 'none',
                borderBottom: i === activeIdx ? '2px solid #90caf9' : '2px solid transparent',
                color: i === activeIdx ? '#90caf9' : '#666',
                cursor: 'pointer',
                fontSize: 14,
                fontFamily: 'Consolas, Menlo, monospace',
                whiteSpace: 'nowrap',
              }}
              title={fc.path}
            >
              {fc.action === 'edit' ? '\u0394 ' : fc.action === 'create' ? '+ ' : ''}{name}
            </button>
          )
        })}
      </div>

      {/* File path */}
      <div style={{ padding: '4px 8px', fontSize: 14, color: '#555', fontFamily: 'Consolas, Menlo, monospace' }}>
        {file.path}
      </div>

      {/* Editor */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <Suspense
          fallback={
            <div style={{ padding: 12, color: '#444' }}>Loading editor...</div>
          }
        >
          {isDiff ? (
            <MonacoDiffEditor
              original={file.old_text || ''}
              modified={file.new_text || ''}
              language={language}
              theme="vs-dark"
              options={{
                readOnly: true,
                renderSideBySide: true,
                minimap: { enabled: false },
                fontSize: 14,
                scrollBeyondLastLine: false,
              }}
            />
          ) : (
            <MonacoEditor
              value={file.new_text || file.old_text || '(empty)'}
              language={language}
              theme="vs-dark"
              options={{
                readOnly: true,
                minimap: { enabled: false },
                fontSize: 14,
                scrollBeyondLastLine: false,
              }}
            />
          )}
        </Suspense>
      </div>
    </div>
  )
}

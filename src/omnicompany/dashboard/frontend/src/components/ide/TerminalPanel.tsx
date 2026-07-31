/**
 * TerminalPanel — 终端输出展示
 *
 * 聚合 bash/cmd 工具的命令和输出，只读显示。
 */

import React, { useEffect, useRef } from 'react'
import { useIDEStore } from '../../stores/ideStore'

export default function TerminalPanel() {
  const { terminalLines } = useIDEStore()
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [terminalLines.length])

  return (
    <div
      ref={scrollRef}
      style={{
        height: '100%',
        overflow: 'auto',
        background: 'var(--fp-bg)',
        padding: 8,
        fontFamily: 'Consolas, Menlo, monospace',
        fontSize: 14,
      }}
    >
      {terminalLines.length === 0 && (
        <div style={{ color: '#444', padding: 12 }}>No terminal output yet</div>
      )}
      {terminalLines.map((line, i) => (
        <div key={i} style={{ marginBottom: 8 }}>
          <div style={{ color: '#66bb6a' }}>
            $ {line.command}
          </div>
          <pre
            style={{
              color: line.exitCode !== undefined && line.exitCode !== 0 ? '#ef9a9a' : '#ccc',
              margin: '2px 0 0 0',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 14,
            }}
          >
            {line.output}
          </pre>
          {line.exitCode !== undefined && line.exitCode !== 0 && (
            <div style={{ color: '#ef5350', fontSize: 14 }}>exit code: {line.exitCode}</div>
          )}
        </div>
      ))}
    </div>
  )
}

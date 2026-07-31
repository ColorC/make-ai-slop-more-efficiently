import { describe, expect, it } from 'vitest'
// @ts-expect-error Vitest runs this source-level regression under Node; the
// browser bundle intentionally excludes Node type declarations.
import { readFileSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser.
import { resolve } from 'node:path'

declare const process: { cwd(): string }

const source = readFileSync(resolve(process.cwd(), 'src/entities/controller/index.tsx'), 'utf8')
const viewStore = readFileSync(resolve(process.cwd(), 'src/entities/controller/viewStore.ts'), 'utf8')

describe('recovered Controller surfaces', () => {
  it('keeps the native sessions and CLI surface reachable', () => {
    expect(source).toContain("const ThreadMonitorPanel = lazy(() => import('./ThreadMonitorPanel'))")
    expect(source).toContain("key: 'sessions', label: '会话 / CLI'")
    expect(source).toContain("view === 'sessions'")
    expect(source).toContain('<ThreadMonitorPanel />')
    expect(viewStore).toContain("'sessions'")
  })

  it('uses the authoritative shared view store and real controller chat', () => {
    expect(source).toContain("import { useControllerView } from './viewStore'")
    expect(source).toContain("const ControllerChat = lazy(() => import('./ControllerChat'))")
    expect(source).not.toContain("import { create } from 'zustand'")
    expect(source).not.toContain('ChatuiHandoff')
  })

  it('keeps heavy controller subviews lazy and the blueprint control classes intact', () => {
    expect(source).toContain("import React, { Suspense, lazy } from 'react'")
    expect(source).toContain('className="ct-bar"')
    expect(source).toContain('className="v2-seg"')
    expect(source).toContain('className="ct-matbtn"')
  })
})

import { describe, expect, it } from 'vitest'
import type { CcSessionMeta } from '../../api/ccClient'
import { providerForPty, resolvePtyMetaById } from './index'

function session(id: string): CcSessionMeta {
  return {
    id,
    cmd: ['codex'],
    cwd: 'C:/workspace/',
    cols: 80,
    rows: 24,
    started_at: 1,
    alive: true,
  }
}

describe('PTY deep-link id resolution', () => {
  it('resolves a unique displayed short id to the daemon full id', () => {
    const full = session('862bb496844d41ee')
    expect(resolvePtyMetaById([full], '862bb496')).toBe(full)
  })

  it('does not guess when a prefix is ambiguous', () => {
    const first = session('862bb496844d41ee')
    const second = session('862bb496abcdef00')
    expect(resolvePtyMetaById([first, second], '862bb496')).toBeUndefined()
  })
})

describe('PTY provider identity fallback', () => {
  it('recognizes ZCode and other CLIs from persisted commands', () => {
    expect(providerForPty({ ...session('z'), provider: 'shell', cmd: ['zcode.CMD', '--mode', 'yolo'] })).toBe('zcode')
    expect(providerForPty({ ...session('d'), provider: 'shell', cmd: ['python', 'dsh-pty.py', '--omni-dsh-pty'] })).toBe('dsh')
    expect(providerForPty({ ...session('k'), provider: 'shell', cmd: ['kiro-cli', 'chat'] })).toBe('kiro')
  })

  it('does not let a stale generic provider hide a concrete command', () => {
    expect(providerForPty({ ...session('z'), provider: 'claude_code', cmd: ['zcode'] })).toBe('zcode')
  })
})

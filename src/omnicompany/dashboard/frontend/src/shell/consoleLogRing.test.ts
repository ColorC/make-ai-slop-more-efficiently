import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CONSOLE_LOG_RING_CAPACITY,
  clearConsoleLogRing,
  formatConsoleLogEntries,
  getConsoleLogEntries,
  installConsoleLogRing,
  latestConsoleError,
  pushConsoleLogEntry,
  subscribeConsoleLogRing,
} from './consoleLogRing'

// P1.2 薄顶栏警告徽标 + 快照附带日志的数据源测试。
describe('consoleLogRing', () => {
  beforeAll(() => {
    // 先静音 console 再安装 patch: wrapper 会包住这个 mock(原始链保持安静), 记录仍生效。
    // 注意: 本文件不 restore — restore 会把 patch 一并撤掉(install 是幂等的一次性安装)。
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    installConsoleLogRing()
  })

  beforeEach(() => {
    clearConsoleLogRing()
  })

  it('installs idempotently and records patched console.error / console.warn', () => {
    installConsoleLogRing() // 二次安装不重复 patch
    console.error('boom', 42)
    console.warn('careful')
    const entries = getConsoleLogEntries()
    expect(entries).toHaveLength(2)
    expect(entries[0]).toMatchObject({ level: 'error', text: 'boom 42' })
    expect(entries[1]).toMatchObject({ level: 'warn', text: 'careful' })
    expect(entries[0].time).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/)
  })

  it('stringifies Error / objects in console args', () => {
    console.error(new Error('exploded'))
    console.warn({ code: 'E1', detail: { x: 1 } })
    const entries = getConsoleLogEntries()
    expect(entries[0].text).toContain('exploded')
    expect(entries[1].text).toContain('"code":"E1"')
  })

  it('keeps only the most recent CAPACITY entries (ring truncation)', () => {
    for (let i = 0; i < CONSOLE_LOG_RING_CAPACITY + 10; i += 1) pushConsoleLogEntry('error', `e${i}`)
    const entries = getConsoleLogEntries()
    expect(CONSOLE_LOG_RING_CAPACITY).toBe(50)
    expect(entries).toHaveLength(50)
    expect(entries[0].text).toBe('e10')
    expect(entries[49].text).toBe('e59')
  })

  it('records window error events', () => {
    window.dispatchEvent(new ErrorEvent('error', { message: 'script exploded' }))
    const entries = getConsoleLogEntries()
    expect(entries).toHaveLength(1)
    expect(entries[0].level).toBe('error')
    expect(entries[0].text).toContain('script exploded')
  })

  it('records unhandledrejection reasons', () => {
    window.dispatchEvent(Object.assign(new Event('unhandledrejection'), { reason: new Error('rejected') }))
    const [entry] = getConsoleLogEntries()
    expect(entry.level).toBe('error')
    expect(entry.text).toContain('unhandledrejection')
    expect(entry.text).toContain('rejected')
  })

  it('records omni-notes-error canvas failures and ignores other messages', () => {
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'omni-notes-error', error: '画布水合失败' } }))
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'omni-notes-ready' } }))
    const entries = getConsoleLogEntries()
    expect(entries).toHaveLength(1)
    expect(entries[0]).toMatchObject({ level: 'error', text: 'omni-notes-error: 画布水合失败' })
  })

  it('skips empty texts', () => {
    pushConsoleLogEntry('error', '   ')
    pushConsoleLogEntry('warn', '')
    expect(getConsoleLogEntries()).toHaveLength(0)
  })

  it('latestConsoleError returns the newest error, skipping warns', () => {
    pushConsoleLogEntry('error', 'first')
    pushConsoleLogEntry('warn', 'middle')
    pushConsoleLogEntry('error', 'second')
    expect(latestConsoleError()?.text).toBe('second')
  })

  it('latestConsoleError is null when only warns exist', () => {
    pushConsoleLogEntry('warn', 'only warn')
    expect(latestConsoleError()).toBeNull()
  })

  it('formats all entries as "time [level] text" lines', () => {
    pushConsoleLogEntry('error', 'a')
    pushConsoleLogEntry('warn', 'b')
    const lines = formatConsoleLogEntries().split('\n')
    expect(lines).toHaveLength(2)
    expect(lines[0]).toMatch(/ \[error\] a$/)
    expect(lines[1]).toMatch(/ \[warn\] b$/)
  })

  it('notifies subscribers on push and clear, and stops after unsubscribe', () => {
    const seen: number[] = []
    const off = subscribeConsoleLogRing(() => seen.push(seen.length))
    pushConsoleLogEntry('error', 'x')
    clearConsoleLogRing()
    off()
    pushConsoleLogEntry('error', 'y')
    expect(seen).toHaveLength(2)
    expect(getConsoleLogEntries()).toHaveLength(1)
  })
})

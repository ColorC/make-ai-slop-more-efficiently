import { beforeEach, describe, expect, it, vi } from 'vitest'
import { copyTextToClipboard } from './clipboard'

describe('copyTextToClipboard', () => {
  beforeEach(() => {
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn().mockReturnValue(false) })
  })

  it('在浏览器支持读回时只把完全一致的内容算作成功', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined), readText: vi.fn().mockResolvedValue('被截断') },
    })
    expect(await copyTextToClipboard('稳定链接全文')).toBe(false)
  })

  it('在安全剪贴板接口不可用时验证传统复制返回值', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    vi.mocked(document.execCommand).mockReturnValue(true)
    expect(await copyTextToClipboard('完整引用')).toBe(true)
    expect(document.execCommand).toHaveBeenCalledWith('copy')
  })
})

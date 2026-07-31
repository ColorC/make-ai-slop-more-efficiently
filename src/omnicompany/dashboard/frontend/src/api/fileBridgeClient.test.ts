import { afterEach, describe, expect, it, vi } from 'vitest'
import { fileBridgeApi, fileBridgeContentUrl } from './fileBridgeClient'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fileBridgeApi', () => {
  it('adds the same-origin bridge marker to directory requests', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        root_id: 'workspace',
        root_path: 'C:/workspace/',
        path: 'C:/workspace/',
        relative_path: '',
        items: [],
        truncated: false,
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await fileBridgeApi.browse('workspace', 'omnicompany')

    expect(fetchMock).toHaveBeenCalledWith(
      '/lofa/file-bridge/browse',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'X-Omni-File-Bridge': '1',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({ root_id: 'workspace', path: 'omnicompany' }),
      }),
    )
  })

  it('builds short-lived content URLs without exposing raw paths', () => {
    expect(fileBridgeContentUrl('signed.token', true)).toBe(
      '/lofa/file-bridge/content/signed.token?inline=true',
    )
  })

  it('queries the durable upload ledger through the single history endpoint', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        items: [],
        history_path: 'C:/workspace/omnicompany\\data\\runtime\\file_bridge_uploads.jsonl',
        staging_path: 'C:/workspace/temp\\omni-file-bridge',
        query_command: 'omni dashboard uploads --limit 12',
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await fileBridgeApi.history(12)

    expect(fetchMock).toHaveBeenCalledWith(
      '/lofa/file-bridge/history?limit=12',
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-Omni-File-Bridge': '1' }),
      }),
    )
  })

  it('reports a useful error when a dev proxy returns the SPA document', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => { throw new SyntaxError('Unexpected token <') },
    })))

    await expect(fileBridgeApi.roots()).rejects.toThrow('读取文件桥根目录失败：服务返回了非 JSON 响应')
  })
})

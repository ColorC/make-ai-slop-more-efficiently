import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  inspectOverlayFile,
  overlayFileContentUrl,
  searchOverlayFiles,
} from './overlayClient'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('searchOverlayFiles', () => {
  it('通过同源桥发送搜索并返回有效文件命中', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        result: [
          {
            kind: 'file',
            name: 'PROJECT_INDEX.md',
            path: 'C:/workspace/PROJECT_INDEX.md',
            score: 9,
            tags: [],
            pinned: false,
            open_token: 'signed-file-token',
          },
          { malformed: true },
        ],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(searchOverlayFiles('  PROJECT_INDEX  ', 999)).resolves.toEqual([
      {
        kind: 'file',
        name: 'PROJECT_INDEX.md',
        path: 'C:/workspace/PROJECT_INDEX.md',
        score: 9,
        tags: [],
        pinned: false,
        open_token: 'signed-file-token',
      },
    ])
    expect(fetchMock).toHaveBeenCalledWith('/lofa/overlay/invoke', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ cmd: 'search', args: { query: 'PROJECT_INDEX', limit: 100 } }),
    }))
  })

  it('把桥接错误暴露给界面', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ ok: false, error: 'Overlay Shell 文件索引未连接' }),
    }))
    await expect(searchOverlayFiles('notes')).rejects.toThrow('文件索引未连接')
  })
})

describe('inspectOverlayFile', () => {
  it('通过签名令牌读取网页预览数据', async () => {
    const detail = {
      kind: 'file',
      name: 'README.md',
      path: 'E:\\README.md',
      size: 12,
      modified_at: '2026-07-24T10:00:00',
      mime: 'text/markdown',
      open_token: 'signed-token',
      preview: 'text',
      content: '# hello',
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, result: detail }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(inspectOverlayFile('signed-token')).resolves.toEqual(detail)
    expect(fetchMock).toHaveBeenCalledWith('/lofa/overlay/invoke', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ cmd: 'file_inspect', args: { token: 'signed-token' } }),
    }))
    expect(overlayFileContentUrl('a/b')).toBe('/lofa/overlay/file/a%2Fb')
  })
})

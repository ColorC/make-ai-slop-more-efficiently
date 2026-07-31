import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fileBridgeApi } from '../../api/fileBridgeClient'
import { copyText } from '../../lib/copyText'
import GlobalFileDrop, { FILE_BRIDGE_UPLOAD_EVENT } from './GlobalFileDrop'

vi.mock('../../api/fileBridgeClient', () => ({
  fileBridgeApi: {
    upload: vi.fn(),
  },
}))

vi.mock('../../lib/copyText', () => ({
  copyText: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
})

function fileEvent(type: 'dragenter' | 'drop' | 'paste', files: File[]): Event {
  const event = new Event(type, { bubbles: true, cancelable: true })
  if (type === 'paste') {
    Object.defineProperty(event, 'clipboardData', {
      value: { files },
    })
  } else {
    Object.defineProperty(event, 'dataTransfer', {
      value: { files, types: ['Files'], dropEffect: 'none' },
    })
  }
  return event
}

function uploadResult(name: string) {
  return {
    batch_id: '20260731-170000-abc123',
    uploaded_at: '2026-07-31T17:00:00+08:00',
    batch_path: 'E:\\WindowsWorkspace\\temp\\omni-file-bridge\\20260731-170000-abc123',
    root_id: 'staging' as const,
    total_bytes: 5,
    items: [{
      name,
      path: `E:\\WindowsWorkspace\\temp\\omni-file-bridge\\20260731-170000-abc123\\${name}`,
      relative_path: `20260731-170000-abc123/${name}`,
      kind: 'file' as const,
      size: 5,
      modified_at: '2026-07-31T17:00:00+08:00',
      mime: 'text/plain',
      preview: 'text' as const,
      accessible: true,
    }],
  }
}

describe('GlobalFileDrop', () => {
  it('uploads a file dropped anywhere and copies its Agent path', async () => {
    const file = new File(['hello'], 'phone.txt', { type: 'text/plain' })
    const result = uploadResult(file.name)
    vi.mocked(fileBridgeApi.upload).mockResolvedValue(result)
    vi.mocked(copyText).mockResolvedValue(true)
    const uploaded = vi.fn()
    window.addEventListener(FILE_BRIDGE_UPLOAD_EVENT, uploaded)
    render(<GlobalFileDrop surface="full" />)

    fireEvent(window, fileEvent('dragenter', [file]))
    expect(screen.getByTestId('global-file-drop-overlay')).toBeTruthy()
    fireEvent(window, fileEvent('drop', [file]))

    await waitFor(() => expect(fileBridgeApi.upload).toHaveBeenCalledWith([file]))
    await waitFor(() => expect(copyText).toHaveBeenCalledWith(result.items[0].path))
    expect(uploaded).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('global-file-drop-notice').textContent).toContain('复制远端绝对路径')
    window.removeEventListener(FILE_BRIDGE_UPLOAD_EVENT, uploaded)
  })

  it('uploads file clipboard contents instead of swallowing ordinary text paste', async () => {
    const file = new File(['image'], 'clipboard.png', { type: 'image/png' })
    vi.mocked(fileBridgeApi.upload).mockResolvedValue(uploadResult(file.name))
    vi.mocked(copyText).mockResolvedValue(true)
    render(<GlobalFileDrop surface="full" />)

    const textPaste = fileEvent('paste', [])
    fireEvent(window, textPaste)
    expect(textPaste.defaultPrevented).toBe(false)
    expect(fileBridgeApi.upload).not.toHaveBeenCalled()

    const filePaste = fileEvent('paste', [file])
    fireEvent(window, filePaste)
    expect(filePaste.defaultPrevented).toBe(true)
    await waitFor(() => expect(fileBridgeApi.upload).toHaveBeenCalledWith([file]))
  })

  it('does not duplicate a drop already handled by the dedicated staging area', () => {
    const file = new File(['hello'], 'once.txt', { type: 'text/plain' })
    render(<GlobalFileDrop surface="full" />)
    const drop = fileEvent('drop', [file])
    drop.preventDefault()

    fireEvent(window, drop)

    expect(fileBridgeApi.upload).not.toHaveBeenCalled()
  })
})

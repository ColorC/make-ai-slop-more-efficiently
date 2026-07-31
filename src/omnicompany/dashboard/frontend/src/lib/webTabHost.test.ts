import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanels } from '../stores/panelsStore'
import {
  decodeWebPageId,
  encodeWebPageId,
  isInternalWebUrl,
  installWebTabHost,
  normalizeWebTabUrl,
  openWebTab,
  WEB_TAB_MESSAGE,
} from './webTabHost'

describe('webTabHost', () => {
  beforeEach(() => {
    usePanels.setState({ tabs: [], activeId: null })
  })

  it('encodes a restorable web page id', () => {
    const url = 'https://example.com/path?a=1#x'
    expect(decodeWebPageId(encodeWebPageId(url))).toBe(url)
  })

  it('rewrites localhost to the reachable Dashboard host', () => {
    expect(normalizeWebTabUrl('http://localhost:7348/chat', 'http://10.3.43.246:8210/?lofaRemoteWeb=1'))
      .toBe('http://10.3.43.246:7348/chat')
  })

  it('opens one Dashboard tab for an internal service URL and reuses it', () => {
    const first = openWebTab('http://10.3.43.246:7348/a', 'Chat', 'http://10.3.43.246:8210/')
    const second = openWebTab('http://10.3.43.246:7348/a', 'Chat again', 'http://10.3.43.246:8210/')
    expect(first).toBe(second)
    expect(usePanels.getState().tabs).toHaveLength(1)
    expect(usePanels.getState().tabs[0].ref.type).toBe('web_review')
  })

  it('routes a same-origin material surface into the native review material tab', () => {
    const opened = openWebTab(
      '/?surface=material&id=mat+candidate',
      'Review candidate',
      'http://10.3.43.246:8210/?lofaRemoteWeb=1',
    )

    expect(opened).toBe('review_material:mat candidate')
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      id: 'review_material:mat candidate',
      ref: { type: 'review_material', id: 'mat candidate' },
      title: 'Review candidate',
    })])
  })

  it('leaves an external material-looking URL to the browser', () => {
    const opened = openWebTab(
      'https://example.com/?surface=material&id=mat_candidate',
      'External',
      'http://10.3.43.246:8210/?lofaRemoteWeb=1',
    )

    expect(opened).toBeNull()
    expect(usePanels.getState().tabs).toHaveLength(0)
  })

  it('classifies same-host and private service URLs as internal', () => {
    expect(isInternalWebUrl('http://10.3.43.246:7348/chat', 'http://10.3.43.246:8210/')).toBe(true)
    expect(isInternalWebUrl('http://192.168.1.20/app', 'http://10.3.43.246:8210/')).toBe(true)
    expect(isInternalWebUrl('https://example.com/', 'http://10.3.43.246:8210/')).toBe(false)
  })

  it('unwraps a Dashboard root into the native project tab', () => {
    const opened = openWebTab(
      'http://10.3.43.246:8210/?lofaRemoteWeb=1',
      '',
      'http://10.3.43.246:8210/?lofaRemoteWeb=1',
    )
    expect(opened).toBe('project_board:main')
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      id: 'project_board:main',
      ref: { type: 'project_board', id: 'main' },
    })])
  })

  it('leaves an explicitly linked Multiagent surface to a real browser window', () => {
    const opened = openWebTab(
      '/?surface=multiagent&ma_link=pair-1',
      '',
      'http://10.3.43.246:8210/?lofaRemoteWeb=1',
    )
    expect(opened).toBeNull()
    expect(usePanels.getState().tabs).toHaveLength(0)
  })

  it('lets native window.open create the linked Multiagent browser tab', () => {
    const originalOpen = window.open
    const child = { opener: window } as unknown as Window
    const nativeOpen = vi.fn(() => child)
    window.open = nativeOpen
    const uninstall = installWebTabHost(window)
    try {
      const opened = window.open('/?surface=multiagent&ma_link=pair-1', '_blank')
      expect(opened).toBe(child)
      expect(nativeOpen).toHaveBeenCalledWith('/?surface=multiagent&ma_link=pair-1', '_blank', undefined)
      expect(usePanels.getState().tabs).toHaveLength(0)
    } finally {
      uninstall()
      window.open = originalOpen
    }
  })

  it('routes a plain Multiagent surface into the native Multiagent tab', () => {
    const opened = openWebTab(
      '/?surface=multiagent',
      'Multiagent',
      'http://10.3.43.246:8210/?lofaRemoteWeb=1',
    )
    expect(opened).toBe('multiagent:main')
    expect(usePanels.getState().tabs).toEqual([expect.objectContaining({
      id: 'multiagent:main',
      ref: { type: 'multiagent', id: 'main' },
    })])
  })

  it('routes raw review material files to the native review tab', () => {
    const opened = openWebTab(
      '/api/boss-sight/reviewstage/mat_file/file',
      '',
      'http://10.3.43.246:8210/',
    )
    expect(opened).toBe('review_material:mat_file')
  })

  it('exports the LOFA bridge message contract', () => {
    expect(WEB_TAB_MESSAGE).toBe('omni:open-web-tab')
  })
})

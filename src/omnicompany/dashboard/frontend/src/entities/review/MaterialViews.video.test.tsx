import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { VideoMaterialView } from './MaterialViews'
import type { Material } from '../../api/reviewstageClient'

function mat(over: Partial<Material> = {}): Material {
  return {
    id: 'mat_1',
    kind: 'video',
    tier: 'processual',
    title: 'demo clip',
    status: 'pending',
    source_subagent_id: null,
    source_plan_id: null,
    file_relpath: null,
    inline_content: null,
    annotations: [],
    comments: [],
    annotations_allowed: true,
    created_at: '',
    updated_at: '',
    history: [],
    pushed_to_user: false,
    pushed_reason: null,
    pushed_at: null,
    extra: {},
    ...over,
  }
}

describe('VideoMaterialView', () => {
  it('embeds a YouTube link as an iframe', () => {
    render(<VideoMaterialView m={mat({ extra: { video_url: 'https://youtu.be/abcdefghijk' } })} />)
    const el = screen.getByTestId('material-video')
    expect(el.tagName).toBe('IFRAME')
    expect(el.getAttribute('src')).toContain('youtube.com/embed/abcdefghijk')
  })

  it('renders a local file as a <video> element with typed <source>', () => {
    render(<VideoMaterialView m={mat({ file_relpath: 'clip.mp4' })} />)
    const el = screen.getByTestId('material-video')
    expect(el.tagName).toBe('VIDEO')
    const source = el.querySelector('source')
    expect(source?.getAttribute('src')).toBeTruthy()
    expect(source?.getAttribute('type')).toBe('video/mp4')
  })

  it('renders a direct video URL as <video>', () => {
    render(<VideoMaterialView m={mat({ extra: { video_url: 'https://cdn.example.com/a.mp4' } })} />)
    const el = screen.getByTestId('material-video')
    expect(el.tagName).toBe('VIDEO')
  })

  // 2026-07-07 视频审阅整改: 播放器之外必须有常驻保底动作条(浏览器直开/下载/复制链接)
  it('always renders the action bar (open-in-browser / download / copy) for local files', () => {
    render(<VideoMaterialView m={mat({ file_relpath: 'clip.webm' })} />)
    const actions = screen.getByTestId('material-video-actions')
    expect(actions.textContent).toContain('在浏览器打开')
    expect(actions.textContent).toContain('下载')
    expect(actions.textContent).toContain('复制链接')
    const open = actions.querySelector('a[target="_blank"]') as HTMLAnchorElement
    expect(open?.href).toContain('/api/boss-sight/reviewstage/mat_1/file')
  })

  it('sizes the player with an explicit aspect-ratio box (no indefinite-height collapse)', () => {
    render(<VideoMaterialView m={mat({ file_relpath: 'clip.webm' })} />)
    const el = screen.getByTestId('material-video')
    const box = el.parentElement as HTMLElement
    expect(box.style.aspectRatio).toBe('16 / 9')
    expect(box.style.width).toBe('100%')
  })

  it('shows a placeholder when there is no source', () => {
    render(<VideoMaterialView m={mat()} />)
    expect(screen.getByTestId('material-video').textContent).toContain('无视频源')
  })
})

/**
 * 网页型材料的两种呈现模式 + 刷新。
 *
 * 用户 2026-08-07 报障:「同时不能刷新，应当多种模式都支持」。
 * 活页面(extra.live_url)的内容会随产物重生成而变, 但 iframe 自己不会重取 ——
 * 没有刷新按钮时, 用户看到的永远是第一次打开那一刻。
 * 而落盘快照与活页面本是互补的两份(现况 vs 提交那刻), 原来只挑一个显示。
 */
import { describe, it, expect } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { HtmlMaterialView } from './MaterialViews'
import type { Material } from '../../api/reviewstageClient'

function mat(over: Partial<Material> = {}): Material {
  return {
    id: 'mat_web',
    kind: 'demo',
    tier: 'important',
    title: 'web material',
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
  } as Material
}

const iframeSrc = () => (screen.getByTestId('material-html') as HTMLIFrameElement).getAttribute('src') || ''

describe('HtmlMaterialView 呈现模式', () => {
  it('刷新会换掉 src, 逼 iframe 重取', () => {
    render(<HtmlMaterialView m={mat({ extra: { live_url: '/bside/' } })} />)
    const before = iframeSrc()
    expect(before.startsWith('/bside/')).toBe(true)

    fireEvent.click(screen.getByTestId('material-web-reload'))
    const after = iframeSrc()
    expect(after).not.toBe(before)
    expect(after.startsWith('/bside/')).toBe(true)
  })

  it('同时有活页面和快照时两种模式都能选', () => {
    render(<HtmlMaterialView m={mat({ extra: { live_url: '/bside/' }, file_relpath: 'files/sha-abc.html' })} />)
    expect(iframeSrc().startsWith('/bside/')).toBe(true)

    fireEvent.click(screen.getByTestId('material-mode-snapshot'))
    const snap = iframeSrc()
    expect(snap.startsWith('/bside/')).toBe(false)
    expect(snap).toContain('mat_web')

    fireEvent.click(screen.getByTestId('material-mode-live'))
    expect(iframeSrc().startsWith('/bside/')).toBe(true)
  })

  it('只有一种来源时不出模式切换, 但仍可刷新', () => {
    render(<HtmlMaterialView m={mat({ file_relpath: 'files/sha-abc.html' })} />)
    expect(screen.queryByTestId('material-mode-live')).toBeNull()
    expect(screen.getByTestId('material-web-reload')).toBeTruthy()
  })

  it('既无 live_url 也无落盘文件时整条模式栏不出现', () => {
    render(<HtmlMaterialView m={mat({ inline_content: '<!doctype html><html></html>' })} />)
    expect(screen.queryByTestId('material-web-modes')).toBeNull()
  })

  it('浏览器里跨源 live_url 自动退出 iframe 并给新窗口入口', () => {
    delete window.__OMNICOMPANY_HOST__
    render(<HtmlMaterialView m={mat({
      extra: { live_url: 'https://192.0.2.10:12444/app/maintainer-workboard/' },
    })} />)

    expect(screen.queryByTestId('material-html')).toBeNull()
    expect(screen.getByTestId('web-page-external-fallback')).not.toBeNull()
    expect(screen.getByRole('button', { name: '在新窗口打开' })).not.toBeNull()
  })
})

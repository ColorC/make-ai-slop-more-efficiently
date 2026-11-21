import React from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/projectsClient', () => ({
  projectsApi: {
    list: vi.fn(async () => ({ projects: [{ id: 'vilo', name: 'Vilo', roots: [] }] })),
    plans: vi.fn(async () => ({ items: [], plan_ids: [] })),
    index: vi.fn(async () => ({ ok: true, content: '# Vilo' })),
    views: vi.fn(async () => ({ apps: [] })),
  },
}))

vi.mock('../../api/ccClient', () => ({ ccApi: { list: vi.fn(async () => []) } }))
vi.mock('../../api/reviewstageClient', () => ({
  reviewstageApi: {
    domainTree: vi.fn(async () => ({ domains: [] })),
    list: vi.fn(async () => ({ items: [] })),
  },
}))
vi.mock('../../api/bossSightClient', () => ({ bossSightApi: { activeBindings: vi.fn(async () => ({ by_project: {} })) } }))
vi.mock('../../api/materialWikiClient', () => ({
  materialWikiApi: {
    profile: vi.fn(async () => ({ taxonomy: [] })),
    get: vi.fn(),
  },
}))
vi.mock('./loadProjectWikiMaterials', () => ({ loadProjectWikiMaterials: vi.fn(async () => []) }))
vi.mock('./ProjectWikiHome', () => ({ default: () => <div data-testid="project-wiki-home" /> }))
vi.mock('./DomainTreeBar', () => ({ default: () => null }))
vi.mock('./ProjectSkills', () => ({ default: () => null }))
vi.mock('./ProjectFileTree', () => ({ default: () => null }))
vi.mock('../review-canvas', () => ({ ReviewCanvas: () => null }))
vi.mock('../studio_reader/WorkshopSurface', () => ({ WorkshopSurface: () => null }))
vi.mock('../authored/NotesForTarget', () => ({ default: () => null }))
vi.mock('./AiLearningObservatoryPanel', () => ({ default: () => null }))
vi.mock('../../shared/view/ui/ActiveConvoBadge', () => ({ default: () => null }))

import ProjectDetail from './ProjectDetail'
import { materialWikiApi } from '../../api/materialWikiClient'
import { loadProjectWikiMaterials } from './loadProjectWikiMaterials'

const mockWikiGet = vi.mocked(materialWikiApi.get)
const mockLoadWiki = vi.mocked(loadProjectWikiMaterials)

function wikiEntry(body: string) {
  return {
    material_id: 'vilo.intro',
    project_id: 'vilo',
    title: '介绍',
    head_version: 'v1',
    current_version: 'v1',
    versions: [{ version: 'v1', status: 'current', body }],
  } as never
}

describe('ProjectDetail Wiki navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fully removes the left rail and keeps a visible button that restores it', () => {
    render(<ProjectDetail entity={{ id: 'vilo' }} wikiOnly />)

    expect(screen.getByTestId('pd-rail')).toBeTruthy()
    expect(screen.queryByTestId('pd-entry-canvas')).toBeNull()

    fireEvent.click(screen.getByTestId('pd-toggle-rail'))

    expect(screen.queryByTestId('pd-rail')).toBeNull()
    const reopen = screen.getByTestId('pd-toggle-rail')
    expect(reopen.getAttribute('aria-expanded')).toBe('false')
    expect(reopen.getAttribute('aria-label')).toBe('展开项目 Wiki 导航')

    fireEvent.click(reopen)

    expect(screen.getByTestId('pd-rail')).toBeTruthy()
    expect(screen.getByTestId('pd-toggle-rail').getAttribute('aria-expanded')).toBe('true')
  })
})

describe('ProjectDetail wiki entry reader', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the entry markdown body directly, never a workshop-surface iframe', async () => {
    mockWikiGet.mockResolvedValue(wikiEntry('# 介绍\n\n这是条目正文。'))
    const { container } = render(<ProjectDetail entity={{ id: 'vilo' }} facet="wiki:vilo.intro" wikiOnly />)

    expect(await screen.findByText('这是条目正文。')).toBeTruthy()
    expect(screen.getByTestId('pd-wiki-entry')).toBeTruthy()
    expect(screen.queryByTestId('pd-material-surface')).toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
  })

  it('renders inline SVG from the entry body', async () => {
    mockWikiGet.mockResolvedValue(wikiEntry(
      '正文 <svg data-testid="inline-svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg> 结尾',
    ))
    render(<ProjectDetail entity={{ id: 'vilo' }} facet="wiki:vilo.intro" wikiOnly />)

    const svg = await screen.findByTestId('inline-svg')
    expect(svg.tagName.toLowerCase()).toBe('svg')
    expect(screen.getByTestId('pd-wiki-entry').contains(svg)).toBe(true)
  })

  it('shows a loading state while fetching and a failure state on error', async () => {
    mockWikiGet.mockReturnValue(new Promise(() => undefined))
    const view = render(<ProjectDetail entity={{ id: 'vilo' }} facet="wiki:vilo.intro" wikiOnly />)
    expect(screen.getByText('加载中…')).toBeTruthy()
    view.unmount()

    mockWikiGet.mockRejectedValue(new Error('Material Wiki 404'))
    render(<ProjectDetail entity={{ id: 'vilo' }} facet="wiki:vilo.intro" wikiOnly />)
    expect((await screen.findByTestId('pd-wiki-entry-error')).textContent).toContain('条目加载失败')
  })

  it('renders fenced ```html demo blocks as live sandboxed pages, not source code', async () => {
    const demo = '<!doctype html>\n<html><head><meta charset="utf-8"></head>\n' +
      '<body><svg width="960" height="360" viewBox="0 0 960 360"><rect width="10" height="10"/></svg></body></html>'
    mockWikiGet.mockResolvedValue(wikiEntry(
      `## 结构化网页范例\n\n演示说明文字。\n\n\`\`\`html\n${demo}\n\`\`\`\n\n## 适用情况\n\n后续正文。`,
    ))
    render(<ProjectDetail entity={{ id: 'vilo' }} facet="wiki:vilo.intro" wikiOnly />)

    const iframe = (await screen.findByTestId('pd-live-demo')) as HTMLIFrameElement
    expect(iframe.tagName.toLowerCase()).toBe('iframe')
    expect(iframe.getAttribute('sandbox')).toBe('')
    expect(iframe.srcdoc).toContain('<svg width="960" height="360"')
    // 注入缩放样式, SVG 随栏宽等比缩放; 纵横比按 svg 实尺
    expect(iframe.srcdoc).toContain('max-width:100%')
    expect(iframe.style.aspectRatio.replace(' / 1', '')).toBe(String(960 / 360))
    // 围栏前后正文仍按 markdown 渲染, html 源码不泄漏成文本
    expect(await screen.findByText('演示说明文字。')).toBeTruthy()
    expect(await screen.findByText('后续正文。')).toBeTruthy()
    expect(screen.getByTestId('pd-wiki-entry').textContent).not.toContain('<!doctype html>')
  })

  it('renders a live-material marker entry as a full-bleed page, not a pointer document', async () => {
    mockWikiGet.mockResolvedValue(wikiEntry(
      '<!-- omni-live-material {"url":"/bilibili-video/ep02-publishing-pipelines/","card":"c#review-1"} -->\n\n' +
      '# EP2 本体\n\n> live 网页材料指针。\n',
    ))
    render(<ProjectDetail entity={{ id: 'vilo' }} facet="wiki:vilo.intro" wikiOnly />)

    const host = await screen.findByTestId('pd-wiki-entry-live')
    const iframe = host.querySelector('iframe.pd-live-material') as HTMLIFrameElement
    expect(iframe).toBeTruthy()
    expect(iframe.getAttribute('src')).toBe('/bilibili-video/ep02-publishing-pipelines/')
    // 指针文字不渲染, 整页只有本体
    expect(host.textContent || '').not.toContain('live 网页材料指针')
    expect(screen.queryByTestId('pd-wiki-entry')).toBeNull()
  })
})

describe('ProjectDetail wiki 未裁决分组', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function listedEntry(materialId: string, title: string, path: string[], reviewState?: string) {
    return {
      material_id: materialId,
      project_id: 'vilo',
      profile_id: 'p',
      title,
      placement_path: path,
      head_version: 'v1',
      current_version: 'v1',
      links: { canonical_ref: '', api: '', studio: '' },
      review_state: reviewState ?? null,
    } as never
  }

  it('unreviewed/seen 收进置顶「未裁决」组, 已裁决与无卡条目留在原分类且不重复', async () => {
    mockLoadWiki.mockResolvedValue([
      listedEntry('vilo.pending-a', '待审A', ['plans', 'pending-a'], 'unreviewed'),
      listedEntry('vilo.pending-b', '待审B', ['plans', 'pending-b'], 'seen'),
      listedEntry('vilo.done-c', '已裁决C', ['plans', 'done-c'], 'fully-passed'),
      listedEntry('vilo.rejected-d', '已裁决D', ['plans', 'rejected-d'], 'not-fully-passed'),
      listedEntry('vilo.plain-e', '普通E', ['notes', 'plain-e']),
    ])
    const { container } = render(<ProjectDetail entity={{ id: 'vilo' }} wikiOnly />)

    expect(await screen.findByText('待审A')).toBeTruthy()

    // 「未裁决」虚拟组置顶, 真实分类 plans/notes 仍在
    const groupLabels = [...container.querySelectorAll('.pd-group')].map((el) => el.textContent || '')
    expect(groupLabels[0]).toContain('未裁决')
    expect(groupLabels).toContain('plans')
    expect(groupLabels).toContain('notes')

    // 条目从原分类移动进组, 不重复显示
    expect(screen.getAllByText('待审A')).toHaveLength(1)
    expect(screen.getAllByText('待审B')).toHaveLength(1)
    expect(screen.getAllByText('已裁决C')).toHaveLength(1)

    // 展平顺序: 未裁决组及其条目在最前; 已裁决/无卡条目仍在原分类 plans 组下
    const rows = [...container.querySelectorAll('.pd-entry')].map((el) => el.textContent || '')
    const at = (text: string) => rows.findIndex((row) => row.includes(text))
    expect(at('未裁决')).toBeGreaterThanOrEqual(0)
    expect(at('待审A')).toBeGreaterThan(at('未裁决'))
    expect(at('待审B')).toBeGreaterThan(at('未裁决'))
    expect(at('plans')).toBeGreaterThan(at('待审B'))
    expect(at('已裁决C')).toBeGreaterThan(at('plans'))
    expect(at('已裁决D')).toBeGreaterThan(at('plans'))
  })

  it('窗口重新获得焦点时 refetch 材料清单(裁决状态在画布卡上, SSE 不覆盖, 靠焦点刷新)', async () => {
    mockLoadWiki.mockResolvedValue([])
    render(<ProjectDetail entity={{ id: 'vilo' }} wikiOnly />)

    await waitFor(() => expect(mockLoadWiki).toHaveBeenCalledTimes(1))
    act(() => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(mockLoadWiki).toHaveBeenCalledTimes(2))
  })
})

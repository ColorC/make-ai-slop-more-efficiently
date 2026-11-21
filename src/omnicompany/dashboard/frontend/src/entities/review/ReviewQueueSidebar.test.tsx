/**
 * ReviewQueueSidebar 卡片「更多」菜单测试(件二 DEC-2026-07-06-082/083)。
 * 覆盖: cardKebab 含「在项目工作台打开」, 点击 → openTab({type:'studio_reader', id:<project>}, ..., <material id>)。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { Material } from '../../api/reviewstageClient'
import type { Mock } from 'vitest'

// list/stats 走夹具; 其余保留。streamStore 的 acquire 需存在但不真连。
vi.mock('../../api/reviewstageClient', async (orig) => {
  const mod = await orig<typeof import('../../api/reviewstageClient')>()
  return { ...mod, reviewstageApi: { ...mod.reviewstageApi, list: vi.fn(), stats: vi.fn() } }
})
vi.mock('./streamStore', () => ({
  useReviewStream: Object.assign(
    (sel: (s: { version: number }) => unknown) => sel({ version: 0 }),
    { getState: () => ({ acquire: () => () => {} }) },
  ),
}))

import { ReviewQueueSidebar } from './ReviewQueueSidebar'
import { reviewstageApi } from '../../api/reviewstageClient'
import { usePanels } from '../../stores/panelsStore'

const mockedList = reviewstageApi.list as unknown as Mock
const mockedStats = reviewstageApi.stats as unknown as Mock

const MAT: Material = {
  id: 'mat_ov1', kind: 'markdown', tier: 'important', title: '一条叙事材料', status: 'pending',
  source_subagent_id: null, source_plan_id: null, file_relpath: null,
  inline_content: 'x', annotations: [], comments: [], annotations_allowed: true,
  created_at: '', updated_at: '', history: [], pushed_to_user: false, pushed_reason: null, pushed_at: null,
  extra: {}, project: 'vilo',
} as unknown as Material

beforeEach(() => {
  mockedList.mockResolvedValue({ items: [MAT] })
  mockedStats.mockResolvedValue({ mandatory_unaccepted: 0 })
  usePanels.setState({ tabs: [], activeId: null })
})
afterEach(() => { vi.restoreAllMocks() })

describe('ReviewQueueSidebar 聚合折叠(同项目同类≥3条收叠层卡, 2026-07-07)', () => {
  it('4条同项目同类默认折叠为一张叠层卡, 点击展开出成员卡', async () => {
    const mats = [1, 2, 3, 4].map((i) => ({
      ...MAT, id: `mat_c${i}`, title: `发布草稿 ${i}`, project: 'publish',
    })) as unknown as Material[]
    mockedList.mockResolvedValue({ items: mats })
    render(<ReviewQueueSidebar onOpenMaterial={() => {}} />)
    // 叠层卡出现(锚在首条 id 上), 成员卡默认不渲染
    const cluster = await screen.findByTestId('material-cluster-mat_c1')
    expect(cluster.textContent).toContain('4 条')
    expect(screen.queryByTestId('material-card-mat_c1')).toBeNull()
    expect(screen.queryByTestId('material-card-mat_c4')).toBeNull()
    // 点击展开 → 4 张成员卡全部出现
    fireEvent.click(cluster)
    await waitFor(() => {
      expect(screen.getByTestId('material-card-mat_c1')).toBeTruthy()
      expect(screen.getByTestId('material-card-mat_c4')).toBeTruthy()
    })
    // 再点收起
    fireEvent.click(screen.getByTestId('material-cluster-mat_c1'))
    await waitFor(() => expect(screen.queryByTestId('material-card-mat_c1')).toBeNull())
  })

  it('不足3条不折叠, 原样平铺', async () => {
    const mats = [1, 2].map((i) => ({
      ...MAT, id: `mat_s${i}`, title: `零散 ${i}`, project: 'publish',
    })) as unknown as Material[]
    mockedList.mockResolvedValue({ items: mats })
    render(<ReviewQueueSidebar onOpenMaterial={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('material-card-mat_s1')).toBeTruthy())
    expect(screen.queryByTestId('material-cluster-mat_s1')).toBeNull()
  })
})

describe('ReviewQueueSidebar 卡片「在项目工作台打开」(件二)', () => {
  it('cardKebab 含新入口, 点击 → openTab(studio_reader, project=vilo, facet=材料 id)', async () => {
    render(<ReviewQueueSidebar onOpenMaterial={() => {}} />)
    // 卡片入列。
    await waitFor(() => expect(screen.getByTestId(`material-card-${MAT.id}`)).toBeTruthy())
    // 打开卡片 kebab。
    fireEvent.click(screen.getByTestId(`material-card-more-${MAT.id}`))
    const item = await screen.findByTestId(`material-card-open-studio-${MAT.id}`)
    expect(item.textContent).toContain('在项目工作台打开')
    fireEvent.click(item)
    // openTab 落一个 studio_reader:<project> tab, facet=材料 id。
    await waitFor(() => {
      const tab = usePanels.getState().tabs.find((t) => t.ref.type === 'studio_reader' && t.ref.id === 'vilo')
      expect(tab).toBeTruthy()
      expect(tab?.facet).toBe(MAT.id)
    })
  })
})

import { describe, it, expect, vi, afterEach } from 'vitest'
import { reviewMaterialRegistration, materialTabTitle } from './index'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'

function mat(id: string, title: string): Material {
  return {
    id,
    kind: 'markdown',
    tier: 'mandatory',
    title,
    status: 'pending',
    source_subagent_id: null,
    source_plan_id: null,
    file_relpath: null,
    inline_content: 'hello',
    annotations: [],
    comments: [],
    annotations_allowed: true,
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    history: [],
    pushed_to_user: false,
    pushed_reason: null,
    pushed_at: null,
    extra: {},
  }
}

describe('review_material entity (R3 驾驶舱审阅材料页签)', () => {
  afterEach(() => vi.restoreAllMocks())

  it('resolver 与 renderer 同 type 注册', () => {
    expect(reviewMaterialRegistration.resolver.type).toBe('review_material')
    expect(reviewMaterialRegistration.renderer.type).toBe('review_material')
  })

  it('fetch 走后端单条接口 reviewstageApi.get(id), 不靠 list 过滤', async () => {
    const spy = vi.spyOn(reviewstageApi, 'get').mockResolvedValue(mat('mat_1', 'walker 战棋首屏'))
    const e = await reviewMaterialRegistration.resolver.fetch('mat_1')
    expect(spy).toHaveBeenCalledWith('mat_1')
    expect(e.type).toBe('review_material')
    expect(e.id).toBe('mat_1')
    expect(e.title).toBe('walker 战棋首屏')
    expect(e.tags).toEqual(['mandatory', 'pending'])
  })

  it('list 把材料映射成实体', async () => {
    vi.spyOn(reviewstageApi, 'list').mockResolvedValue({
      count: 2,
      items: [mat('a', 'A'), mat('b', 'B')],
    })
    const list = await reviewMaterialRegistration.resolver.list()
    expect(list.map((e) => e.id)).toEqual(['a', 'b'])
    expect(list.every((e) => e.type === 'review_material')).toBe(true)
  })

  it('materialTabTitle 截断长标题', () => {
    expect(materialTabTitle('短标题')).toBe('短标题')
    const long = 'x'.repeat(40)
    expect(materialTabTitle(long)).toHaveLength(24)
    expect(materialTabTitle(long).endsWith('…')).toBe(true)
    expect(materialTabTitle('   ')).toBe('(untitled)')
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { MaterialWikiManageBar, canSubmitWikiWrite, headWikiBody } from './MaterialWikiManageBar'
import { StudioReaderPanel } from './index'

const profile = {
  project_id: 'walker',
  default_status: 'draft',
  statuses: [{ id: 'draft', label: '草稿', current: true }],
  taxonomy: [{ id: 'mechanics', label: '机制', children: [{ id: 'cards', label: '卡牌', children: [] }] }],
}

const listed = {
  material_id: 'walker.card.demo',
  title: '演示卡',
  head_version: 'v1',
  current_version: 'v1',
  placement_path: ['mechanics', 'cards'],
  status: 'draft',
  preview: '摘要不是正文',
}
const full = {
  ...listed,
  versions: [{ version: 'v1', status: 'draft', body: '现场同一正文。', created_at: '2026-08-15T00:00:00Z', sources: [], relations: {} }],
}

vi.mock('../../api/materialWikiClient', () => ({
  materialWikiApi: {
    profile: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    revise: vi.fn(),
    archive: vi.fn(),
    restore: vi.fn(),
    link: vi.fn(),
  },
}))

vi.mock('./WorkshopSurface', () => ({
  WorkshopSurface: ({ project, materialId }: { project?: string; materialId?: string }) => (
    <div data-testid="workshop-surface-materials">{project}:{materialId ?? ''}</div>
  ),
}))

import { materialWikiApi } from '../../api/materialWikiClient'

describe('MaterialWikiManageBar live write path', () => {
  beforeEach(() => {
    vi.mocked(materialWikiApi.profile).mockResolvedValue(profile as never)
    vi.mocked(materialWikiApi.list).mockResolvedValue({ materials: [listed], total: 1, count: 1, offset: 0, limit: 20, has_more: false, project_id: 'walker' } as never)
    vi.mocked(materialWikiApi.get).mockResolvedValue(full as never)
    vi.mocked(materialWikiApi.create).mockResolvedValue({})
    vi.mocked(materialWikiApi.revise).mockResolvedValue({})
    vi.mocked(materialWikiApi.archive).mockResolvedValue({})
    vi.mocked(materialWikiApi.link).mockResolvedValue({})
  })

  it('is mounted next to the one Material Core surface without a second reader', () => {
    const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'index.tsx'), 'utf8')
    expect(source).toContain('MaterialWikiManageBar')
    expect(source).toContain('WorkshopSurface')
    expect(source).not.toMatch(/<MaterialWikiWorkspace/)
    expect(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'MaterialWikiManageBar.tsx'), 'utf8'))
      .not.toMatch(/MarkdownRenderer/)
  })

  it('create calls the shipped materialWikiApi.create', async () => {
    render(<MaterialWikiManageBar project="walker" />)
    await waitFor(() => expect(materialWikiApi.profile).toHaveBeenCalledWith('walker'))
    fireEvent.click(screen.getByRole('button', { name: /新建/ }))
    const dialog = await screen.findByTestId('material-wiki-write-dialog')
    fireEvent.change(dialog.querySelector('input')!, { target: { value: '新卡' } })
    fireEvent.click(screen.getByRole('button', { name: '写入材料本体' }))
    await waitFor(() => expect(materialWikiApi.create).toHaveBeenCalled())
    const payload = vi.mocked(materialWikiApi.create).mock.calls[0]![1] as { title: string; body: string }
    expect(payload.title).toBe('新卡')
  })

  it('archive calls the shipped materialWikiApi.archive', async () => {
    window.confirm = vi.fn(() => true)
    render(<MaterialWikiManageBar project="walker" materialId="walker.card.demo" />)
    await waitFor(() => expect(materialWikiApi.list).toHaveBeenCalledWith('walker', expect.objectContaining({ query: 'walker.card.demo' })))
    fireEvent.click(await screen.findByRole('button', { name: /归档/ }))
    await waitFor(() => expect(materialWikiApi.archive).toHaveBeenCalledWith(
      'walker',
      'walker.card.demo',
      expect.objectContaining({ expected_version: 'v1' }),
    ))
  })

  it('does not get() on mount; revise prefills head body and refuses empty submit', async () => {
    expect(canSubmitWikiWrite('revise', { body: '', loaded: false })).toBe(false)
    expect(canSubmitWikiWrite('revise', { body: '', loaded: true })).toBe(false)
    expect(canSubmitWikiWrite('revise', { body: '现场同一正文。', loaded: true })).toBe(true)
    expect(headWikiBody(listed as never)).toBeNull()
    expect(headWikiBody(full as never)).toBe('现场同一正文。')

    render(<MaterialWikiManageBar project="walker" materialId="walker.card.demo" />)
    await waitFor(() => expect(materialWikiApi.list).toHaveBeenCalled())
    expect(materialWikiApi.get).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: /修订/ }))
    await waitFor(() => expect(materialWikiApi.get).toHaveBeenCalledWith('walker', 'walker.card.demo'))
    const textarea = await screen.findByTestId('wiki-write-body') as HTMLTextAreaElement
    await waitFor(() => expect(textarea.value).toBe('现场同一正文。'))
    fireEvent.change(textarea, { target: { value: '' } })
    expect((screen.getByRole('button', { name: '写入材料本体' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '写入材料本体' }))
    expect(materialWikiApi.revise).not.toHaveBeenCalled()
    fireEvent.change(textarea, { target: { value: '现场同一正文。' } })
    fireEvent.click(screen.getByRole('button', { name: '写入材料本体' }))
    await waitFor(() => expect(materialWikiApi.revise).toHaveBeenCalled())
    const payload = vi.mocked(materialWikiApi.revise).mock.calls[0]![2] as { body: string }
    expect(payload.body).toBe('现场同一正文。')
  })

  it('StudioReaderPanel hosts the manage bar and the Workshop wiki iframe', async () => {
    render(<StudioReaderPanel project="walker" initialMaterialId="walker.card.demo" />)
    expect(await screen.findByTestId('material-wiki-manage-bar')).toBeTruthy()
    expect(screen.getByTestId('workshop-surface-materials').textContent).toContain('walker')
  })
})

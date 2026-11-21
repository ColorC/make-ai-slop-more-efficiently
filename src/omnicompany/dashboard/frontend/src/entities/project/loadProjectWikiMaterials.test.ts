import { describe, expect, it, vi } from 'vitest'
import type { MaterialWikiEntry, MaterialWikiListResponse } from '../../api/materialWikiClient'
import { loadProjectWikiMaterials } from './loadProjectWikiMaterials'

function entry(id: string): MaterialWikiEntry {
  return {
    material_id: id,
    project_id: 'vilo',
    profile_id: 'vilo.materials',
    title: id,
    placement_path: [],
    head_version: 'v1',
    links: { canonical_ref: '', api: '', studio: '' },
  }
}

function page(
  offset: number,
  materials: MaterialWikiEntry[],
  hasMore: boolean,
  nextOffset: number | null = null,
): MaterialWikiListResponse {
  return {
    project_id: 'vilo',
    count: materials.length,
    total: 2,
    offset,
    limit: 100,
    has_more: hasMore,
    next_offset: nextOffset,
    materials,
  }
}

describe('loadProjectWikiMaterials', () => {
  it('respects the API limit and joins every page for the project tree', async () => {
    const loadPage = vi.fn()
      .mockResolvedValueOnce(page(0, [entry('vilo.story')], true, 1))
      .mockResolvedValueOnce(page(1, [entry('vilo.character')], false))

    await expect(loadProjectWikiMaterials('vilo', loadPage)).resolves.toEqual([
      expect.objectContaining({ material_id: 'vilo.story' }),
      expect.objectContaining({ material_id: 'vilo.character' }),
    ])
    expect(loadPage).toHaveBeenNthCalledWith(1, 'vilo', { offset: 0, limit: 100, includeTerminal: false })
    expect(loadPage).toHaveBeenNthCalledWith(2, 'vilo', { offset: 1, limit: 100, includeTerminal: false })
  })

  it('fails instead of looping forever when the server does not advance', async () => {
    const loadPage = vi.fn().mockResolvedValue(page(0, [], true, 0))

    await expect(loadProjectWikiMaterials('vilo', loadPage)).rejects.toThrow('did not advance')
  })
})

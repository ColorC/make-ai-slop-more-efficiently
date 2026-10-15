import {
  materialWikiApi,
  type MaterialWikiEntry,
  type MaterialWikiListResponse,
} from '../../api/materialWikiClient'

const MATERIAL_WIKI_PAGE_SIZE = 100

type WikiPageLoader = (
  project: string,
  options: { offset: number; limit: number; includeTerminal: boolean },
) => Promise<MaterialWikiListResponse>

export async function loadProjectWikiMaterials(
  project: string,
  loadPage: WikiPageLoader = materialWikiApi.list,
): Promise<MaterialWikiEntry[]> {
  const materials: MaterialWikiEntry[] = []
  let offset = 0

  while (true) {
    const page = await loadPage(project, {
      offset,
      limit: MATERIAL_WIKI_PAGE_SIZE,
      // 终态(rejected/superseded)条目不进侧栏树(2026-08-21 用户反馈: 垃圾条目毫无用处);
      // 审计走 CLI/API include_terminal=true。
      includeTerminal: false,
    })
    materials.push(...(page.materials || []))
    if (!page.has_more) return materials

    const nextOffset = page.next_offset ?? page.offset + page.count
    if (!Number.isFinite(nextOffset) || nextOffset <= offset) {
      throw new Error(`Material Wiki pagination did not advance for ${project}`)
    }
    offset = nextOffset
  }
}

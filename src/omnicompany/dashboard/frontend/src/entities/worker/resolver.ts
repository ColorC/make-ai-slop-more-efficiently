import type { Entity } from '../types'
import type { EntityResolver } from '../registry'

export interface WorkerEntity extends Entity {
  type: 'worker'
  package: string
  file_path: string
  has_design_md: boolean
}

export interface WorkerDetail extends WorkerEntity {
  design_md_path: string | null
  design_md: string | null
  source: string
}

let _listCache: WorkerEntity[] | null = null

async function fetchList(): Promise<WorkerEntity[]> {
  if (_listCache) return _listCache
  const r = await fetch('/api/workers')
  if (!r.ok) throw new Error(`list workers: ${r.status}`)
  const data = await r.json() as { items: any[] }
  _listCache = data.items.map((it) => ({
    type: 'worker' as const,
    id: it.id,
    title: it.name,
    package: it.package,
    file_path: it.file_path,
    has_design_md: !!it.has_design_md,
    tags: [it.package.split('/')[0]],
  }))
  return _listCache!
}

export async function fetchWorkerDetail(id: string): Promise<WorkerDetail> {
  const r = await fetch(`/api/workers/${id}`)
  if (!r.ok) throw new Error(`get worker: ${r.status}`)
  const it = await r.json()
  return {
    type: 'worker',
    id: it.id,
    title: it.name,
    package: it.package,
    file_path: it.file_path,
    has_design_md: !!it.design_md,
    design_md_path: it.design_md_path,
    design_md: it.design_md,
    source: it.source,
  }
}

export const workerResolver: EntityResolver<WorkerEntity> = {
  type: 'worker',
  async fetch(id) {
    const list = await fetchList()
    const found = list.find((w) => w.id === id)
    if (found) return found
    const detail = await fetchWorkerDetail(id)
    return detail
  },
  async list() {
    return fetchList()
  },
  async search(q) {
    const list = await fetchList()
    const ql = q.toLowerCase()
    return list.filter((w) =>
      w.id.toLowerCase().includes(ql) || w.title.toLowerCase().includes(ql),
    )
  },
}

export function invalidateWorkerCache(): void {
  _listCache = null
}

import React, { useEffect, useState } from 'react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import CodeFileEditor, { type CodeFileDetail } from '../../shell/CodeFileEditor'
import EmptyState from '../../shell/EmptyState'
import { CodeFileSidebar } from '../team/sidebar'

export interface MaterialEntity extends Entity {
  type: 'material'
  package: string
  file_path: string
  has_design_md: boolean
}

let _cache: MaterialEntity[] | null = null

async function fetchList(): Promise<MaterialEntity[]> {
  if (_cache) return _cache
  const r = await fetch('/api/materials')
  if (!r.ok) throw new Error(`list materials: ${r.status}`)
  const d = await r.json() as { items: any[] }
  _cache = d.items.map((it) => ({
    type: 'material' as const,
    id: it.id,
    title: it.name === 'materials' || it.name === 'formats'
      ? `${it.package.split('/').pop() || it.package}.${it.name}`
      : it.name,
    package: it.package,
    file_path: it.file_path,
    has_design_md: !!it.has_design_md,
    tags: [it.package.split('/')[0], it.name],
  }))
  return _cache!
}

async function fetchDetail(id: string): Promise<CodeFileDetail> {
  const r = await fetch(`/api/materials/${id}`)
  if (!r.ok) throw new Error(`get material: ${r.status}`)
  return r.json()
}

const resolver: EntityResolver<MaterialEntity> = {
  type: 'material',
  async fetch(id) {
    const list = await fetchList()
    const found = list.find((m) => m.id === id)
    if (found) return found
    throw new Error(`material not found: ${id}`)
  },
  async list() { return fetchList() },
  async search(q) {
    const all = await fetchList()
    const ql = q.toLowerCase()
    return all.filter((m) => m.id.toLowerCase().includes(ql) || m.title.toLowerCase().includes(ql))
  },
}

const Editor: React.FC<{ entity: MaterialEntity }> = ({ entity }) => {
  const [detail, setDetail] = useState<CodeFileDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setDetail(null); setError(null)
    fetchDetail(entity.id).then(setDetail).catch((e) => setError(String(e)))
  }, [entity.id])
  if (error) return <EmptyState text={`加载失败: ${error}`} />
  if (!detail) return <EmptyState text="加载中..." />
  return <CodeFileEditor detail={detail} defaultView="source" />
}

export const materialRegistration: EntityRegistration<MaterialEntity> = {
  resolver,
  renderer: { type: 'material', Editor, SidebarView: (props) => <CodeFileSidebar entityType="material" fetchList={fetchList} {...props} /> },
  label: '材料',
  icon: '◆',
}

export function invalidateMaterialCache(): void { _cache = null }

import { useCallback, useEffect, useState } from 'react'
import { ArchiveRestore, Link2, Plus, Save } from 'lucide-react'
import {
  materialWikiApi,
  type MaterialWikiEntry,
  type MaterialWikiProfile,
} from '../../api/materialWikiClient'
// 管理条弹窗(mw-editor/mw-modal-backdrop)样式; 2026-08-16 面板删除时 import 被连带拆掉,
// styleEntrypoints 闸门抓到断链, 恢复同目录共置。
import './materialWiki.css'

type Mode = 'create' | 'revise' | 'link' | null

function operationKey(prefix: string): string {
  const uuid = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
  return `material-wiki-manage:${prefix}:${uuid}`
}

function source(project: string) {
  return [{ kind: 'studio_reader', ref: `studio-reader:${project}` }]
}

function stableIdPart(value: string): string {
  return value.normalize('NFKC').replace(/[^A-Za-z\p{Script=Han}\p{N}]+/gu, '').slice(0, 36) || '未命名'
}

function canonicalMaterialId(profile: MaterialWikiProfile | null, title: string, placement: string[]): string {
  return ['Vilo', '材料', ...placement, title].map(stableIdPart).join('.')
}

export function headWikiBody(entry: MaterialWikiEntry | null | undefined): string | null {
  if (!entry?.versions?.length) return null
  const head = entry.versions.find((item) => item.version === entry.head_version) ?? entry.versions[0]
  return typeof head?.body === 'string' ? head.body : null
}

export function canSubmitWikiWrite(mode: Exclude<Mode, null>, input: { body: string; loaded: boolean }): boolean {
  if (mode === 'link') return true
  if (mode === 'create') return true
  return input.loaded && input.body.length > 0
}

export function MaterialWikiManageBar({
  project,
  materialId,
  onMaterialChange,
}: {
  project: string
  materialId?: string
  onMaterialChange?: (id: string) => void
}) {
  const [profile, setProfile] = useState<MaterialWikiProfile | null>(null)
  const [entry, setEntry] = useState<MaterialWikiEntry | null>(null)
  const [mode, setMode] = useState<Mode>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  const selectedId = materialId?.replace(/^material-wiki:/, '') ?? ''

  useEffect(() => {
    let live = true
    materialWikiApi.profile(project).then((next) => { if (live) setProfile(next) }).catch(() => { if (live) setProfile(null) })
    return () => { live = false }
  }, [project])

  useEffect(() => {
    if (!selectedId) { setEntry(null); return }
    let live = true
    materialWikiApi.list(project, { query: selectedId, limit: 20, includeTerminal: true })
      .then((listing) => {
        if (!live) return
        setEntry(listing.materials.find((item) => item.material_id === selectedId) ?? listing.materials[0] ?? null)
      })
      .catch(() => { if (live) setEntry(null) })
    return () => { live = false }
  }, [project, selectedId])

  const run = useCallback(async (action: () => Promise<unknown>, message: string, id?: string) => {
    setBusy(true)
    setNotice('')
    try {
      await action()
      setNotice(message)
      setMode(null)
      if (id) onMaterialChange?.(id)
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }, [onMaterialChange])

  const headVersion = entry?.head_version ?? entry?.current_version ?? 'v1'
  const rejected = entry?.status === 'rejected' || Boolean(entry?.terminal)
  const canWrite = Boolean(profile)

  return (
    <div className="mw-manage-bar" data-testid="material-wiki-manage-bar">
      <button type="button" disabled={!canWrite || busy} onClick={() => setMode('create')}><Plus size={13} /> 新建</button>
      <button type="button" disabled={!entry || busy} onClick={() => setMode('revise')}><Save size={13} /> 修订</button>
      <button type="button" disabled={!entry || busy} onClick={() => setMode('link')}><Link2 size={13} /> 关系</button>
      {rejected
        ? (
            <button
              type="button"
              disabled={!entry || busy}
              onClick={() => void run(() => materialWikiApi.restore(project, entry!.material_id, {
                target_version: headVersion,
                expected_version: headVersion,
                status: profile?.default_status || 'draft',
                sources: source(project),
                idempotency_key: operationKey(`restore:${entry!.material_id}`),
              }), '已恢复为当前材料', entry!.material_id)}
            >
              <ArchiveRestore size={13} /> 恢复
            </button>
          )
        : (
            <button
              type="button"
              disabled={!entry || busy}
              onClick={() => {
                if (!entry || !window.confirm(`归档“${entry.title}”？该操作可恢复。`)) return
                void run(() => materialWikiApi.archive(project, entry.material_id, {
                  target_version: headVersion,
                  expected_version: headVersion,
                  sources: source(project),
                  idempotency_key: operationKey(`archive:${entry.material_id}`),
                }), '已可恢复归档', entry.material_id)
              }}
            >
              <ArchiveRestore size={13} /> 归档
            </button>
          )}
      {notice && <span data-testid="material-wiki-manage-notice">{notice}</span>}
      {mode && (
        <WikiWriteDialog
          mode={mode}
          project={project}
          profile={profile}
          entry={entry}
          busy={busy}
          onClose={() => setMode(null)}
          onSubmit={(action, message, id) => void run(action, message, id)}
        />
      )}
    </div>
  )
}

function WikiWriteDialog({
  mode, project, profile, entry, busy, onClose, onSubmit,
}: {
  mode: Exclude<Mode, null>
  project: string
  profile: MaterialWikiProfile | null
  entry: MaterialWikiEntry | null
  busy: boolean
  onClose: () => void
  onSubmit: (action: () => Promise<unknown>, message: string, id?: string) => void
}) {
  const headVersion = entry?.head_version ?? 'v1'
  const [title, setTitle] = useState(entry?.title ?? '')
  const [rootId, setRootId] = useState(profile?.taxonomy[0]?.id ?? '')
  const root = profile?.taxonomy.find((item) => item.id === rootId)
  const [typeId, setTypeId] = useState(root?.children[0]?.id ?? '')
  const [subpath, setSubpath] = useState(entry?.placement_path.slice(2).join('/') ?? '')
  const [status, setStatus] = useState(entry?.status === 'rejected' ? profile?.default_status ?? 'draft' : entry?.status ?? profile?.default_status ?? 'draft')
  const [body, setBody] = useState('')
  const [bodyLoaded, setBodyLoaded] = useState(mode !== 'revise')
  const [loadError, setLoadError] = useState('')
  const [relation, setRelation] = useState('related')
  const [relatedId, setRelatedId] = useState('')
  const [relatedVersion, setRelatedVersion] = useState('版本 1')
  const currentStatuses = profile?.statuses.filter((item) => item.current) ?? []

  useEffect(() => {
    if (mode !== 'revise' || !entry) return
    let live = true
    setBodyLoaded(false)
    setLoadError('')
    materialWikiApi.get(project, entry.material_id)
      .then((full) => {
        if (!live) return
        const next = headWikiBody(full)
        if (next == null) {
          setLoadError('当前材料没有可修订正文')
          setBody('')
          setBodyLoaded(false)
          return
        }
        setBody(next)
        setBodyLoaded(true)
      })
      .catch((reason) => {
        if (!live) return
        setLoadError(reason instanceof Error ? reason.message : String(reason))
        setBody('')
        setBodyLoaded(false)
      })
    return () => { live = false }
  }, [mode, project, entry])

  function submit(): void {
    if (mode === 'revise' && !canSubmitWikiWrite('revise', { body, loaded: bodyLoaded })) return
    if (mode === 'create') {
      const placement = [rootId, typeId, ...subpath.split('/').map((item) => item.trim()).filter(Boolean)]
      const materialId = canonicalMaterialId(profile, title, placement)
      onSubmit(() => materialWikiApi.create(project, {
        title, placement_path: placement, body, status, sources: source(project),
        idempotency_key: operationKey('create'), material_id: materialId,
      }), '材料条目已建立', materialId)
    } else if (mode === 'revise' && entry) {
      onSubmit(() => materialWikiApi.revise(project, entry.material_id, {
        body, status, sources: source(project), expected_version: headVersion,
        idempotency_key: operationKey(`revise:${entry.material_id}`),
      }), '新版本已写入材料本体', entry.material_id)
    } else if (mode === 'link' && entry) {
      onSubmit(() => materialWikiApi.link(project, entry.material_id, {
        target_version: headVersion, expected_version: headVersion, relation,
        related_material_id: relatedId, related_version: relatedVersion.replace(/^版本\s*/, 'v'),
        sources: source(project), idempotency_key: operationKey(`link:${entry.material_id}`),
      }), '关系已写入材料本体', entry.material_id)
    }
  }

  return (
    <div className="mw-modal-backdrop" role="presentation">
      <section className="mw-editor" role="dialog" aria-modal="true" data-testid="material-wiki-write-dialog">
        <header>
          <h2>{mode === 'create' ? '新建材料' : mode === 'revise' ? '修订材料' : '建立关系'}</h2>
          <button type="button" aria-label="关闭编辑器" onClick={onClose}>关闭</button>
        </header>
        {mode === 'create' && (
          <>
            <label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <label>层
              <select value={rootId} onChange={(event) => {
                setRootId(event.target.value)
                setTypeId(profile?.taxonomy.find((item) => item.id === event.target.value)?.children[0]?.id ?? '')
              }}
              >
                {profile?.taxonomy.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
            <label>一级类型
              <select value={typeId} onChange={(event) => setTypeId(event.target.value)}>
                {root?.children.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
          </>
        )}
        {mode !== 'link' && (
          <>
            <label>状态
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                {currentStatuses.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
            <label>正文<textarea rows={12} value={body} onChange={(event) => setBody(event.target.value)} data-testid="wiki-write-body" /></label>
            {mode === 'revise' && !bodyLoaded && <p data-testid="wiki-write-body-wait">{loadError || '正在读取当前正文…'}</p>}
          </>
        )}
        {mode === 'link' && (
          <>
            <label>关系
              <select value={relation} onChange={(event) => setRelation(event.target.value)}>
                <option value="related">一般关系</option>
                <option value="derived_from">推导自</option>
                <option value="supersedes">覆写</option>
              </select>
            </label>
            <label>目标材料标识<input value={relatedId} onChange={(event) => setRelatedId(event.target.value)} /></label>
          </>
        )}
        <footer>
          <button type="button" onClick={onClose}>取消</button>
          <button
            type="button"
            className="primary"
            disabled={busy || (mode === 'create' && (!title || !rootId || !typeId)) || (mode === 'link' && !relatedId) || (mode === 'revise' && !canSubmitWikiWrite('revise', { body, loaded: bodyLoaded }))}
            onClick={submit}
          >
            {busy ? '提交中…' : '写入材料本体'}
          </button>
        </footer>
      </section>
    </div>
  )
}

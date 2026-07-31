import { Fragment, useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Check, GitCompare, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'

const API = '/narrative-studio/api'

type ReviewStatus = 'draft' | 'pending' | 'accepted' | 'rejected'

interface Beat {
  id: string
  parent?: string | null
  title?: string | null
  function?: string | null
  summary?: { sentence?: string | null; paragraph?: string | null; full?: string | null }
  position?: number
  status?: string
  lane?: string | null
  authority?: string | null
  review_status?: ReviewStatus
  review_note?: string | null
  reviewed_at?: string | null
}

interface StoryLine {
  id: string
  title: string
  color?: string | null
  character_id?: string | null
  review_status?: ReviewStatus
  review_note?: string | null
  reviewed_at?: string | null
}

interface Project {
  beats: Beat[]
  storylines: StoryLine[]
}

interface VersionInfo {
  name: string
  created_at?: string | null
  note?: string | null
  review_status: ReviewStatus
  review_note?: string | null
  reviewed_at?: string | null
}

interface DiffCarrier {
  added: string[]
  removed: string[]
  changed: Array<{ id: string; fields: string[] }>
  a_count: number
  b_count: number
}

interface ProjectDiff {
  carriers: Record<string, DiffCarrier>
  premise_changed?: boolean
}

const REVIEW_LABEL: Record<ReviewStatus, string> = {
  draft: '草稿', pending: '待审', accepted: '已通过', rejected: '已否决',
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail || `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export function NarrativeOutlineWorkspace() {
  const [project, setProject] = useState<Project | null>(null)
  const [versions, setVersions] = useState<VersionInfo[]>([])
  const [selected, setSelected] = useState<{ carrier: 'beats' | 'storylines'; id: string } | null>(null)
  const [form, setForm] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [versionName, setVersionName] = useState('')
  const [versionNote, setVersionNote] = useState('')
  const [compareA, setCompareA] = useState('_working')
  const [compareB, setCompareB] = useState('')
  const [diff, setDiff] = useState<ProjectDiff | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [nextProject, nextVersions] = await Promise.all([
        jsonRequest<Project>('/project'),
        jsonRequest<VersionInfo[]>('/versions/details'),
      ])
      setProject(nextProject)
      setVersions(nextVersions)
      if (!compareB && nextVersions.length) setCompareB(nextVersions[nextVersions.length - 1].name)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [compareB])

  useEffect(() => { void load() }, [load])

  const stages = useMemo(
    () => (project?.beats || []).filter((beat) => !beat.parent).sort((a, b) => (a.position || 0) - (b.position || 0)),
    [project],
  )
  const lines = project?.storylines || []

  const selectedEntity = useMemo(() => {
    if (!project || !selected) return null
    return selected.carrier === 'beats'
      ? project.beats.find((beat) => beat.id === selected.id) || null
      : project.storylines.find((line) => line.id === selected.id) || null
  }, [project, selected])

  useEffect(() => {
    if (!selectedEntity) { setForm({}); return }
    const isBeat = 'parent' in selectedEntity
    setForm({
      title: selectedEntity.title || '',
      summary: isBeat ? selectedEntity.summary?.sentence || '' : '',
      function: isBeat ? selectedEntity.function || '' : '',
      status: isBeat ? selectedEntity.status || 'todo' : '',
      color: 'color' in selectedEntity ? selectedEntity.color || '#8b94a3' : '',
      review_status: selectedEntity.review_status || 'pending',
      review_note: selectedEntity.review_note || '',
    })
  }, [selectedEntity])

  const mutate = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await work()
      setMessage(success)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const saveEntity = async () => {
    if (!selected || !selectedEntity) return
    const patch = selected.carrier === 'beats'
      ? {
          title: form.title,
          summary: { ...(('summary' in selectedEntity && selectedEntity.summary) || {}), sentence: form.summary || null },
          function: form.function || null,
          status: form.status,
          review_status: form.review_status,
          review_note: form.review_note || null,
          reviewed_at: new Date().toISOString(),
        }
      : {
          title: form.title,
          color: form.color || null,
          review_status: form.review_status,
          review_note: form.review_note || null,
          reviewed_at: new Date().toISOString(),
        }
    await mutate(
      () => jsonRequest(`/entity/${selected.carrier}/${encodeURIComponent(selected.id)}`, {
        method: 'PATCH', body: JSON.stringify(patch),
      }),
      '已保存到创作台',
    )
  }

  const createLine = async () => {
    const title = window.prompt('新剧情线名称')?.trim()
    if (!title) return
    const id = `sl-${Date.now().toString(36)}`
    await mutate(
      () => jsonRequest('/entity/storylines', {
        method: 'POST', body: JSON.stringify({
          id, title, color: '#8b94a3', review_status: 'draft', review_note: null, reviewed_at: null,
        }),
      }),
      '已新增剧情线',
    )
    setSelected({ carrier: 'storylines', id })
  }

  const createBeat = async (parent: string, lane: string) => {
    const title = window.prompt('新卡片标题')?.trim()
    if (!title || !project) return
    const id = `b-${Date.now().toString(36)}`
    const position = project.beats.filter((beat) => beat.parent === parent && beat.lane === lane).length
    await mutate(
      () => jsonRequest('/entity/beats', {
        method: 'POST', body: JSON.stringify({
          id, parent, lane, position, title, summary: {}, status: 'todo', edges: [],
          authority: null, review_status: 'draft', review_note: null, reviewed_at: null,
        }),
      }),
      '已新增大纲卡片',
    )
    setSelected({ carrier: 'beats', id })
  }

  const removeSelected = async () => {
    if (!selected || !project) return
    if (selected.carrier === 'storylines' && project.beats.some((beat) => beat.lane === selected.id)) {
      setError('这条剧情线仍有卡片，先移动或删除卡片。')
      return
    }
    if (!window.confirm('确认删除当前内容？自动历史快照仍可恢复。')) return
    const old = selected
    setSelected(null)
    await mutate(
      () => jsonRequest(`/entity/${old.carrier}/${encodeURIComponent(old.id)}`, { method: 'DELETE' }),
      '已删除',
    )
  }

  const saveVersion = async () => {
    if (!versionName.trim()) { setError('请填写版本名。'); return }
    await mutate(
      () => jsonRequest('/versions/save', {
        method: 'POST', body: JSON.stringify({ name: versionName.trim(), note: versionNote.trim() || null }),
      }),
      '已保存具名版本，状态为待审',
    )
    setVersionName(''); setVersionNote('')
  }

  const reviewVersion = async (version: VersionInfo, review_status: ReviewStatus) => {
    const review_note = window.prompt('审阅意见（可留空）', version.review_note || '')
    if (review_note === null) return
    await mutate(
      () => jsonRequest('/versions/review', {
        method: 'POST', body: JSON.stringify({ name: version.name, review_status, review_note }),
      }),
      `版本“${version.name}”已标为${REVIEW_LABEL[review_status]}`,
    )
  }

  const compare = async () => {
    if (!compareB) { setError('请选择要比较的版本。'); return }
    setBusy(true); setError(null)
    try {
      setDiff(await jsonRequest<ProjectDiff>(`/diff?a=${encodeURIComponent(compareA)}&b=${encodeURIComponent(compareB)}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const activate = async (version: VersionInfo) => {
    if (!window.confirm(`把“${version.name}”切换为当前工作版本？切换前会自动留历史快照。`)) return
    await mutate(
      () => jsonRequest('/versions/activate', { method: 'POST', body: JSON.stringify({ name: version.name }) }),
      `已切换到版本“${version.name}”`,
    )
  }

  const cellBeats = (stage: string, lane: string) => (project?.beats || [])
    .filter((beat) => beat.parent === stage && beat.lane === lane)
    .sort((a, b) => (a.position || 0) - (b.position || 0))

  return (
    <div data-testid="narrative-outline" style={S.root}>
      <div style={S.topbar}>
        <button type="button" style={S.button} data-testid="narrative-workspace-retry" onClick={() => void load()} disabled={busy}><RefreshCw size={13} />刷新</button>
        <button type="button" style={S.button} onClick={() => void createLine()} disabled={busy}><Plus size={13} />新增剧情线</button>
        <span style={S.hint}>直接编辑后即保存为当前工作内容；不需要再同步另一份大纲数据。</span>
      </div>
      {(error || message) && <div data-testid={error ? 'narrative-workspace-error' : 'narrative-workspace-message'} style={{ ...S.notice, color: error ? 'var(--fp-danger)' : 'var(--fp-ok)' }}>{error || message}</div>}

      <div style={S.content}>
        <div style={S.boardWrap}>
          {!project && !error && <div style={S.empty}>载入创作台数据…</div>}
          {project && (
            <div style={{ ...S.board, gridTemplateColumns: `190px repeat(${stages.length}, minmax(220px, 280px))` }}>
              <div style={S.corner}>剧情线 / 大纲阶段</div>
              {stages.map((stage) => (
                <button key={stage.id} type="button" data-testid={`narrative-stage-${stage.id}`} style={{ ...S.card, ...S.stage }}
                  onClick={() => setSelected({ carrier: 'beats', id: stage.id })}>
                  <ReviewPill value={stage.review_status || 'pending'} />
                  <strong>{stage.title || stage.id}</strong>
                  <span style={S.summary}>{stage.summary?.sentence}</span>
                </button>
              ))}
              {lines.map((line) => (
                <Fragment key={line.id}>
                  <button type="button" style={{ ...S.line, borderLeftColor: line.color || '#8b94a3' }}
                    onClick={() => setSelected({ carrier: 'storylines', id: line.id })}>
                    <ReviewPill value={line.review_status || 'pending'} />
                    <strong>{line.title}</strong>
                  </button>
                  {stages.map((stage) => (
                    <div key={`${line.id}-${stage.id}`} style={S.cell}>
                      {cellBeats(stage.id, line.id).map((beat) => (
                        <button key={beat.id} type="button" data-testid={`narrative-beat-${beat.id}`} style={S.card}
                          onClick={() => setSelected({ carrier: 'beats', id: beat.id })}>
                          <ReviewPill value={beat.review_status || 'pending'} />
                          <strong>{beat.title || beat.id}</strong>
                          {beat.summary?.sentence && <span style={S.summary}>{beat.summary.sentence}</span>}
                        </button>
                      ))}
                      <button type="button" style={S.addCard} onClick={() => void createBeat(stage.id, line.id)}>
                        <Plus size={12} />卡片
                      </button>
                    </div>
                  ))}
                </Fragment>
              ))}
            </div>
          )}
        </div>

        <aside style={S.side}>
          <section style={S.panel}>
            <h3 style={S.heading}>当前内容</h3>
            {!selectedEntity && <div style={S.empty}>选择阶段、剧情线或卡片后直接修改。</div>}
            {selectedEntity && (
              <>
                <Field label="名称" value={form.title || ''} onChange={(value) => setForm({ ...form, title: value })} />
                {'parent' in selectedEntity && (
                  <>
                    <Field label="大纲说明" value={form.summary || ''} multiline onChange={(value) => setForm({ ...form, summary: value })} />
                    <Field label="作用（可空）" value={form.function || ''} multiline onChange={(value) => setForm({ ...form, function: value })} />
                    <label style={S.label}>制作进度
                      <select style={S.input} value={form.status || 'todo'} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                        <option value="todo">未开始</option><option value="tocomplete">待补全</option><option value="done">完成</option>
                      </select>
                    </label>
                  </>
                )}
                {'color' in selectedEntity && <Field label="颜色" value={form.color || ''} onChange={(value) => setForm({ ...form, color: value })} />}
                <label style={S.label}>审阅状态
                  <select style={S.input} value={form.review_status || 'pending'} onChange={(e) => setForm({ ...form, review_status: e.target.value })}>
                    {Object.entries(REVIEW_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <Field label="审阅意见" value={form.review_note || ''} multiline onChange={(value) => setForm({ ...form, review_note: value })} />
                <div style={S.row}>
                  <button type="button" style={S.primary} onClick={() => void saveEntity()} disabled={busy}><Save size={13} />保存</button>
                  {('parent' in selectedEntity ? Boolean(selectedEntity.parent) : selectedEntity.id !== 'sl-common') && (
                    <button type="button" style={S.danger} onClick={() => void removeSelected()} disabled={busy}><Trash2 size={13} />删除</button>
                  )}
                </div>
              </>
            )}
          </section>

          <section style={S.panel}>
            <h3 style={S.heading}>保存具名版本</h3>
            <Field label="版本名" value={versionName} onChange={setVersionName} />
            <Field label="说明" value={versionNote} onChange={setVersionNote} />
            <button type="button" style={S.primary} onClick={() => void saveVersion()} disabled={busy}><Save size={13} />保存为待审版本</button>
          </section>

          <section style={S.panel}>
            <h3 style={S.heading}>版本审阅</h3>
            {versions.length === 0 && <div style={S.empty}>还没有具名版本。</div>}
            {versions.map((version) => (
              <div key={version.name} style={S.version}>
                <div style={S.row}><strong>{version.name}</strong><ReviewPill value={version.review_status} /></div>
                {version.note && <div style={S.summary}>{version.note}</div>}
                {version.review_note && <div style={S.reviewNote}>审阅：{version.review_note}</div>}
                <div style={S.row}>
                  <button type="button" style={S.tiny} onClick={() => void reviewVersion(version, 'accepted')}><Check size={12} />通过</button>
                  <button type="button" style={S.tiny} onClick={() => void reviewVersion(version, 'rejected')}>否决</button>
                  <button type="button" style={S.tiny} onClick={() => void activate(version)}>切换到此版本</button>
                </div>
              </div>
            ))}
          </section>

          <section style={S.panel}>
            <h3 style={S.heading}>比较版本</h3>
            <div style={S.row}>
              <VersionSelect value={compareA} versions={versions} onChange={setCompareA} />
              <span>→</span>
              <VersionSelect value={compareB} versions={versions} onChange={setCompareB} />
            </div>
            <button type="button" style={S.button} onClick={() => void compare()} disabled={busy}><GitCompare size={13} />比较</button>
            {diff && <DiffView diff={diff} />}
          </section>
        </aside>
      </div>
    </div>
  )
}

function ReviewPill({ value }: { value: ReviewStatus }) {
  const color = value === 'accepted' ? 'var(--fp-ok)' : value === 'rejected' ? 'var(--fp-danger)' : value === 'pending' ? 'var(--fp-warn)' : 'var(--fp-text-3)'
  return <span data-testid={`narrative-review-${value}`} style={{ ...S.pill, color, borderColor: color }}>{REVIEW_LABEL[value]}</span>
}

function Field({ label, value, onChange, multiline = false }: {
  label: string; value: string; onChange: (value: string) => void; multiline?: boolean
}) {
  return (
    <label style={S.label}>{label}
      {multiline
        ? <textarea style={{ ...S.input, minHeight: 76, resize: 'vertical' }} value={value} onChange={(e) => onChange(e.target.value)} />
        : <input style={S.input} value={value} onChange={(e) => onChange(e.target.value)} />}
    </label>
  )
}

function VersionSelect({ value, versions, onChange }: { value: string; versions: VersionInfo[]; onChange: (value: string) => void }) {
  return (
    <select style={{ ...S.input, flex: 1 }} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="_working">当前工作内容</option>
      {versions.map((version) => <option key={version.name} value={version.name}>{version.name}</option>)}
    </select>
  )
}

function DiffView({ diff }: { diff: ProjectDiff }) {
  const entries = Object.entries(diff.carriers)
  if (!entries.length && !diff.premise_changed) return <div style={S.empty}>没有差异。</div>
  return (
    <div style={S.diff}>
      {entries.map(([carrier, value]) => (
        <div key={carrier}>
          <strong>{carrier}</strong>
          {value.added.length > 0 && <div>新增：{value.added.join('、')}</div>}
          {value.removed.length > 0 && <div>删除：{value.removed.join('、')}</div>}
          {value.changed.map((item) => <div key={item.id}>修改：{item.id}（{item.fields.join('、')}）</div>)}
        </div>
      ))}
      {diff.premise_changed && <div>立意有变化</div>}
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  root: { height: '100%', display: 'flex', flexDirection: 'column', color: 'var(--fp-text-1)', background: 'var(--fp-bg)' },
  topbar: { display: 'flex', alignItems: 'center', gap: 8, padding: '9px 14px', borderBottom: '1px solid var(--fp-border)' },
  content: { minHeight: 0, flex: 1, display: 'flex' },
  boardWrap: { minWidth: 0, flex: 1, overflow: 'auto', padding: 14 },
  board: { display: 'grid', gap: 8, alignItems: 'start' },
  side: { width: 340, flexShrink: 0, overflow: 'auto', padding: 12, borderLeft: '1px solid var(--fp-border)', background: 'var(--fp-surface)' },
  panel: { display: 'flex', flexDirection: 'column', gap: 9, padding: 12, marginBottom: 12, border: '1px solid var(--fp-border)', borderRadius: 8, background: 'var(--fp-bg)' },
  heading: { margin: 0, fontSize: 13.5 },
  corner: { padding: 10, fontSize: 12, color: 'var(--fp-text-3)' },
  line: { minHeight: 74, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 7, padding: 10, textAlign: 'left', color: 'inherit', background: 'var(--fp-surface)', border: '1px solid var(--fp-border)', borderLeftWidth: 4, borderRadius: 7, cursor: 'pointer' },
  cell: { minHeight: 74, display: 'flex', flexDirection: 'column', gap: 6, padding: 6, border: '1px dashed var(--fp-border)', borderRadius: 7 },
  card: { width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 6, padding: 9, color: 'inherit', textAlign: 'left', background: 'var(--fp-surface)', border: '1px solid var(--fp-border)', borderRadius: 7, cursor: 'pointer' },
  stage: { minHeight: 96, borderTop: '3px solid var(--fp-accent)' },
  summary: { display: 'block', fontSize: 11.5, lineHeight: 1.55, color: 'var(--fp-text-2)' },
  reviewNote: { fontSize: 11.5, lineHeight: 1.5, color: 'var(--fp-text-2)', padding: 6, borderRadius: 5, background: 'var(--fp-surface)' },
  pill: { flexShrink: 0, padding: '1px 5px', border: '1px solid', borderRadius: 999, fontSize: 10.5, lineHeight: 1.3 },
  label: { display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11.5, color: 'var(--fp-text-2)' },
  input: { boxSizing: 'border-box', width: '100%', padding: '7px 8px', color: 'var(--fp-text-1)', background: 'var(--fp-surface)', border: '1px solid var(--fp-border)', borderRadius: 5, font: 'inherit' },
  row: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  button: { display: 'inline-flex', alignItems: 'center', gap: 5, padding: '6px 9px', color: 'var(--fp-text-1)', background: 'var(--fp-surface)', border: '1px solid var(--fp-border)', borderRadius: 5, cursor: 'pointer' },
  primary: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 5, padding: '7px 10px', color: 'white', background: 'var(--fp-accent)', border: 0, borderRadius: 5, cursor: 'pointer' },
  danger: { display: 'inline-flex', alignItems: 'center', gap: 5, padding: '7px 10px', color: 'var(--fp-danger)', background: 'transparent', border: '1px solid var(--fp-danger)', borderRadius: 5, cursor: 'pointer' },
  tiny: { display: 'inline-flex', alignItems: 'center', gap: 3, padding: '4px 6px', color: 'var(--fp-text-2)', background: 'transparent', border: '1px solid var(--fp-border)', borderRadius: 4, cursor: 'pointer', fontSize: 11 },
  addCard: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 3, padding: 4, color: 'var(--fp-text-3)', background: 'transparent', border: 0, cursor: 'pointer', fontSize: 11 },
  hint: { fontSize: 11.5, color: 'var(--fp-text-3)' },
  notice: { padding: '7px 14px', fontSize: 12, borderBottom: '1px solid var(--fp-border)' },
  empty: { padding: 8, color: 'var(--fp-text-3)', fontSize: 12 },
  version: { display: 'flex', flexDirection: 'column', gap: 6, padding: 8, border: '1px solid var(--fp-border)', borderRadius: 6 },
  diff: { display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4, padding: 8, fontSize: 11.5, lineHeight: 1.55, background: 'var(--fp-surface)', borderRadius: 5 },
}

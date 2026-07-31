/**
 * entities/review/businesses/narrative — 叙事业务的专属类型渲染器(统一设计工作室 v2 N1/N2)。
 *
 * 租户纪律(DEC-2026-07-05-030):
 * - 数据一律来自叙事内容引擎 API 投影(/narrative-studio/api/*, dashboard 同源反代,
 *   引擎未跑时反代会自动拉起); **禁止依赖工作室 store(useStudio)** —— 静态检查项。
 * - 画法从工作室视图"提取"(大纲=段×线散卡矩阵, 取自 StructureView.Board), 不搬应用壳。
 * - 大纲直接在创作台编辑；多条剧情线、具名版本与审阅状态均由内容引擎保存。
 * - 认可状态(作者/拟)直接长在卡上(台账纪律); 关联裁决经域层级映射(domain-tree)可见,
 *   一跳到决策历程(轨迹画布) —— 补"叙事工作室里看不见轨迹和决策设施"。
 */
import { Fragment, useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { RefreshCw, ArrowRight } from 'lucide-react'
import { type Material } from '../../../api/reviewstageClient'
import { NarrativeOutlineWorkspace } from './narrativeOutlineWorkspace'

const API = '/narrative-studio/api'
const MONO = "'Berkeley Mono','Consolas','Menlo',monospace"

// ── 引擎投影的最小契约镜像(字段名与 narrative_studio/models.py 对齐) ──
interface NBeat {
  id: string; parent?: string | null; title?: string | null; function?: string | null
  summary?: { sentence?: string | null } | null; position?: number; status?: string | null
  lane?: string | null; authority?: string | null
}
interface NLine { id: string; title?: string | null; color?: string | null }
interface NPremise { proposition?: string | null; controlling_ideas?: string[]; stance?: string | null; locked?: boolean }
interface NCharacter {
  id: string; name: string; importance?: string; color?: string | null
  arc?: { want?: string | null; need?: string | null; wound?: string | null; lie?: string | null }
  secret?: string | null; status?: string | null
}
interface NRelationship { id: string; a: string; b: string; nature?: string | null; label?: string | null }
interface NGameText { id: string; text_type?: string; title?: string | null; category?: string | null; body?: string | null; is_draft?: boolean; status?: string | null }
interface NScene {
  id: string; title?: string | null; intent_summary?: string | null; status?: string | null
  objective_events?: string[]; value_shift?: { from?: string | null; to?: string | null }
  links?: { characters?: string[]; lines?: string[] }
}
interface NWorldNode { id: string; name: string; description?: string | null; children?: NWorldNode[] }
interface NAudience { segments?: Array<{ name: string; note?: string | null }>; stance?: string | null; expectations?: string[]; resonance_targets?: string[] }
interface NBackground { thinking?: string | null; world_notes?: string | null; open_questions?: string[] }
interface NRevealLayer { id: string; order?: number | string; title?: string | null; rewrites?: string | null }
interface NRegister { id: string; rule?: string | null }
interface NVoice { id: string }
interface NStyleCell { emotion?: string | null; scene_type?: string | null; register_id?: string | null }
interface NNote { id: string; body?: string | null; text?: string | null; content?: string | null }
interface NProject {
  meta?: { name?: string; version?: string }
  premise?: NPremise
  beats?: NBeat[]
  storylines?: NLine[]
  characters?: NCharacter[]
  relationships?: NRelationship[]
  game_texts?: NGameText[]
  scenes?: NScene[]
  world?: NWorldNode[]
  audience?: NAudience
  background?: NBackground
  reveal_layers?: NRevealLayer[]
  registers?: NRegister[]
  voices?: NVoice[]
  style_matrix?: NStyleCell[]
  notes?: NNote[]
  nodes?: Array<{ id: string }>
  connections?: Array<{ id: string }>
  endings?: Array<{ node_id: string; name?: string | null }>
  variables?: Array<{ namespace?: string; name: string }>
}

// ── 引擎项目投影拉取(带显式降级态, 不拖垮整页) ──
function useNarrativeProject(): { proj: NProject | null; error: string | null; loading: boolean; retry: () => void } {
  const [proj, setProj] = useState<NProject | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    fetch(`${API}/project`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        const ct = r.headers.get('content-type') || ''
        if (!ct.includes('json')) throw new Error('引擎返回非 JSON(可能仍在启动)')
        return r.json() as Promise<NProject>
      })
      .then((d) => { if (alive) { setProj(d); setLoading(false) } })
      .catch((e) => { if (alive) { setError(String(e instanceof Error ? e.message : e)); setLoading(false) } })
    return () => { alive = false }
  }, [nonce])
  const retry = useCallback(() => setNonce((n) => n + 1), [])
  return { proj, error, loading, retry }
}

function EngineDown({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div data-testid="narrative-engine-down" style={ST.down}>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>叙事内容引擎不可达</div>
      <div style={{ fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.6 }}>
        {error} — 驾驶舱反代会自动拉起引擎, 稍候重试; 反复失败时看托管中心 narrative-studio 的启动日志。
      </div>
      <button type="button" style={ST.retryBtn} data-testid="narrative-engine-retry" onClick={onRetry}>
        <RefreshCw size={13} /> 重试
      </button>
    </div>
  )
}

// 认可状态徽标: 作者=绿(已认可), 拟=黄(待作者认可)。状态直接长在内容上(台账纪律)。
function AuthBadge({ authority }: { authority?: string | null }) {
  if (!authority) return null
  const isAuthor = authority === 'author'
  return (
    <span
      data-testid={isAuthor ? 'narrative-badge-author' : 'narrative-badge-draft'}
      style={{
        flexShrink: 0, fontSize: 11, fontWeight: 700, padding: '0 6px', height: 17, lineHeight: '17px', borderRadius: 5,
        color: isAuthor ? 'var(--fp-ok)' : 'var(--fp-warn)',
        background: `color-mix(in srgb, ${isAuthor ? 'var(--fp-ok)' : 'var(--fp-warn)'} 12%, transparent)`,
        border: `1px solid color-mix(in srgb, ${isAuthor ? 'var(--fp-ok)' : 'var(--fp-warn)'} 38%, transparent)`,
      }}
    >{isAuthor ? '作者' : '拟'}</span>
  )
}

function StatusDot({ status }: { status?: string | null }) {
  const c = status === 'done' ? 'var(--fp-ok)' : status === 'doing' || status === 'tocomplete' ? 'var(--fp-warn)' : 'var(--fp-text-3)'
  return <span style={{ flexShrink: 0, width: 7, height: 7, borderRadius: '50%', background: c }} />
}

// ── 业务顶栏(件一 DEC-2026-07-06-082/083): 并入审阅顶栏, 不再在正文自画第二条工具条 ──
// 顶栏工厂(NarrativeRulings / narrativeToolbar / 九个 *Toolbar 导出)已剥离到
// ./narrativeToolbars(2026-07 首屏拆包: 工厂轻量静态挂, 渲染器本体保持 lazy)。

// ═══ N1 大纲 ═══
export function NarrativeOutlineView({ m: _m }: { m: Material }) {
  return <NarrativeOutlineWorkspace />
}

function LegacyNarrativeOutlineView({ m: _m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const beats = proj?.beats ?? []
  const lines = proj?.storylines ?? []

  const stages = useMemo(
    () => beats.filter((b) => !b.parent).sort((a, b) => (a.position ?? 0) - (b.position ?? 0)),
    [beats],
  )
  const laneRows = useMemo(() => {
    const known = new Set(lines.map((l) => l.id))
    const hasUnlaned = beats.some((b) => b.parent && (!b.lane || !known.has(b.lane)))
    return [...lines, ...(hasUnlaned ? [{ id: '', title: '未分线', color: null } as NLine] : [])]
  }, [beats, lines])

  const cellBeats = (stageId: string, laneId: string): NBeat[] =>
    beats
      .filter((b) => b.parent === stageId && ((b.lane || '') === laneId || (laneId === '' && !b.lane)))
      .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))

  return (
    <div data-testid="narrative-outline" style={ST.root}>
      {/* 顶栏已并入审阅顶栏(件一): 渲染器不再自画第二条工具条; 见 narrativeOutlineToolbar 工厂。 */}
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && stages.length === 0 && <div style={ST.dim}>暂无大纲段。</div>}
      {!loading && !error && stages.length > 0 && (
        <div style={{ overflow: 'auto', padding: '4px 16px 24px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: `120px repeat(${stages.length}, minmax(230px, 290px))`, gap: 8, alignItems: 'start' }}>
            {/* 列头: 段卡(主链) */}
            <div style={ST.laneLabel}>段 <ArrowRight size={13} aria-hidden style={{ verticalAlign: -2 }} /></div>
            {stages.map((st) => (
              <div key={st.id} style={{ ...ST.card, ...ST.stageCard }} data-testid={`narrative-stage-${st.id}`}>
                <div style={ST.cardHead}>
                  <StatusDot status={st.status} />
                  <span style={{ flex: 1, minWidth: 0, fontWeight: 650, fontSize: 13.5 }}>{st.title || st.id}</span>
                  <AuthBadge authority={st.authority} />
                </div>
                {st.summary?.sentence && <div style={ST.cardSub}>{st.summary.sentence}</div>}
                {st.function && <div style={ST.cardFn}>{st.function}</div>}
              </div>
            ))}
            {/* 行: 故事线 × 段 */}
            {laneRows.map((ln) => (
              <Fragment key={ln.id || '_none'}>
                <div style={{ ...ST.laneLabel, borderLeft: `3px solid ${ln.color || 'var(--fp-border)'}` }}>
                  {ln.title || ln.id || '未分线'}
                </div>
                {stages.map((st) => (
                  <div key={`${st.id}-${ln.id || '_none'}`} style={ST.cell}>
                    {cellBeats(st.id, ln.id).map((b) => (
                      <div key={b.id} style={ST.card} data-testid={`narrative-beat-${b.id}`}>
                        <div style={ST.cardHead}>
                          <StatusDot status={b.status} />
                          <span style={{ flex: 1, minWidth: 0, fontSize: 13 }}>{b.title || b.id}</span>
                          <AuthBadge authority={b.authority} />
                        </div>
                        {b.summary?.sentence && <div style={ST.cardSub}>{b.summary.sentence}</div>}
                      </div>
                    ))}
                  </div>
                ))}
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ═══ N2 立意(唯一权威=vilo wiki/10 洁净版, 引擎 premise 为誊抄投影) ═══
export function NarrativePremiseView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const p = proj?.premise
  return (
    <div data-testid="narrative-premise" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && (
        <div style={{ maxWidth: 880, margin: '0 auto', padding: '18px 24px 60px', width: '100%', boxSizing: 'border-box' }}>
          <div style={{ ...ST.card, padding: 20, borderLeft: '3px solid var(--fp-accent)' }}>
            <div style={{ fontSize: 12, color: 'var(--fp-text-3)', marginBottom: 8 }}>
              命题 {p?.locked ? '· 已锁定' : ''}
            </div>
            <div style={{ fontSize: 17, fontWeight: 600, lineHeight: 1.6 }}>{p?.proposition || '(命题空)'}</div>
            {p?.stance && <div style={{ marginTop: 10, fontSize: 13, color: 'var(--fp-text-2)', lineHeight: 1.6 }}>立场: {p.stance}</div>}
          </div>
          {(p?.controlling_ideas ?? []).length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, color: 'var(--fp-text-3)', margin: '0 0 8px 2px' }}>主控思想</div>
              {(p?.controlling_ideas ?? []).map((idea, i) => (
                <div key={i} style={{ ...ST.card, padding: '12px 16px', marginBottom: 8, fontSize: 13.5, lineHeight: 1.6 }}>{idea}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ═══ N2 角色卡 ═══
export function NarrativeCharactersView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const chars = proj?.characters ?? []
  const rels = proj?.relationships ?? []
  const arcRow = (label: string, v?: string | null) => v
    ? <div style={{ display: 'flex', gap: 8, fontSize: 12.5, lineHeight: 1.55 }}><span style={{ flexShrink: 0, color: 'var(--fp-text-3)' }}>{label}</span><span style={{ color: 'var(--fp-text-2)' }}>{v}</span></div>
    : null
  return (
    <div data-testid="narrative-characters" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && (
        <div style={{ overflow: 'auto', padding: '4px 20px 40px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
            {chars.map((c) => (
              <div key={c.id} style={{ ...ST.card, padding: 14 }} data-testid={`narrative-character-${c.id}`}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ width: 10, height: 10, borderRadius: '50%', flexShrink: 0, background: c.color || 'var(--fp-accent)' }} />
                  <span style={{ fontSize: 14.5, fontWeight: 650, flex: 1, minWidth: 0 }}>{c.name}</span>
                  {c.importance && <span style={ST.impChip}>{c.importance}</span>}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {arcRow('想要', c.arc?.want)}
                  {arcRow('需要', c.arc?.need)}
                  {arcRow('创伤', c.arc?.wound)}
                  {arcRow('谎言', c.arc?.lie)}
                </div>
                {c.secret && (
                  <details style={{ marginTop: 10 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--fp-text-3)' }}>秘密 ›</summary>
                    <div style={{ fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.55, paddingTop: 6 }}>{c.secret}</div>
                  </details>
                )}
              </div>
            ))}
          </div>
          {rels.length > 0 && (
            <div style={{ marginTop: 20 }}>
              <div style={{ fontSize: 12, color: 'var(--fp-text-3)', margin: '0 0 8px 2px' }}>关系</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {rels.map((r) => (
                  <span key={r.id} style={ST.relChip}>{r.a} ↔ {r.b}{r.nature ? ` · ${r.nature}` : ''}{r.label ? `(${r.label})` : ''}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ═══ N2 草稿看板(game_texts 中 is_draft, 三列; 只读 —— 转正/编辑走内容引擎) ═══
export function NarrativeDraftsView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const drafts = (proj?.game_texts ?? []).filter((g) => g.is_draft)
  const cols: Array<{ key: string; label: string }> = [
    { key: 'todo', label: '待写' }, { key: 'tocomplete', label: '待完善' }, { key: 'done', label: '已就绪' },
  ]
  const norm = (s?: string | null) => (s === 'done' || s === 'tocomplete' ? s : 'todo')
  const summarize = (body?: string | null) => {
    const t = (body ?? '').trim().replace(/\s+/g, ' ')
    return t.length > 90 ? `${t.slice(0, 90)}…` : t
  }
  return (
    <div data-testid="narrative-drafts" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && (
        <div style={{ overflow: 'auto', padding: '4px 20px 40px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(220px, 1fr))', gap: 12 }}>
            {cols.map((col) => (
              <div key={col.key}>
                <div style={{ fontSize: 12, color: 'var(--fp-text-3)', margin: '0 0 8px 2px' }}>
                  {col.label} · {drafts.filter((d) => norm(d.status) === col.key).length}
                </div>
                {drafts.filter((d) => norm(d.status) === col.key).map((d) => (
                  <div key={d.id} style={{ ...ST.card, padding: 12, marginBottom: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0 }}>{d.title || d.id}</span>
                      {d.text_type && <span style={{ fontSize: 11, color: 'var(--fp-text-3)', fontFamily: MONO }}>{d.text_type}</span>}
                    </div>
                    {d.body && <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.5 }}>{summarize(d.body)}</div>}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ═══ 情节(叙事事实层: 场景客观事实, 画法取自 ScenesView 的列表语义) ═══
export function NarrativeScenesView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const scenes = proj?.scenes ?? []
  return (
    <div data-testid="narrative-scenes" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && scenes.length === 0 && <div style={ST.dim}>暂无场景。</div>}
      {!loading && !error && scenes.length > 0 && (
        <div style={{ overflow: 'auto', padding: '4px 20px 40px' }}>
          {scenes.map((sc) => (
            <div key={sc.id} style={{ ...ST.card, padding: 14, marginBottom: 10 }} data-testid={`narrative-scene-${sc.id}`}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <StatusDot status={sc.status} />
                <span style={{ fontSize: 14, fontWeight: 650, flex: 1, minWidth: 0 }}>{sc.title || sc.id}</span>
                {sc.value_shift && (sc.value_shift.from || sc.value_shift.to) && (
                  <span style={{ fontSize: 12, color: 'var(--fp-text-2)', flexShrink: 0 }}>
                    {sc.value_shift.from ?? '?'} → {sc.value_shift.to ?? '?'}
                  </span>
                )}
              </div>
              {sc.intent_summary && <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.55 }}>{sc.intent_summary}</div>}
              {(sc.objective_events ?? []).length > 0 && (
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.6 }}>
                  {(sc.objective_events ?? []).map((ev, i) => <li key={i}>{ev}</li>)}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ═══ 设定 · 世界与关系(叙事事实层; 人设在角色卡渲染器) ═══
export function NarrativeSettingView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const world = proj?.world ?? []
  const rels = proj?.relationships ?? []
  const renderNode = (n: NWorldNode, depth: number): ReactNode => (
    <div key={n.id} style={{ marginLeft: depth * 16 }}>
      <div style={{ ...ST.card, padding: '10px 14px', marginBottom: 6 }}>
        <span style={{ fontSize: 13.5, fontWeight: 600 }}>{n.name}</span>
        {n.description && <div style={{ marginTop: 4, fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.55 }}>{n.description}</div>}
      </div>
      {(n.children ?? []).map((c) => renderNode(c, depth + 1))}
    </div>
  )
  return (
    <div data-testid="narrative-setting" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && (
        <div style={{ overflow: 'auto', padding: '4px 20px 40px' }}>
          {world.length === 0 && <div style={ST.dim}>暂无世界设定词条。</div>}
          {world.map((n) => renderNode(n, 0))}
          {rels.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 12, color: 'var(--fp-text-3)', margin: '0 0 8px 2px' }}>关系</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {rels.map((r) => (
                  <span key={r.id} style={ST.relChip}>{r.a} ↔ {r.b}{r.nature ? ` · ${r.nature}` : ''}{r.label ? `(${r.label})` : ''}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ═══ 指导补充: 背景·思考 / 受众与预期管理 / 揭示层(叙事指导层) ═══
export function NarrativeGuidanceView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const bg = proj?.background
  const au = proj?.audience
  const reveals = proj?.reveal_layers ?? []
  const section = (label: string, body: ReactNode) => (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontSize: 12, color: 'var(--fp-text-3)', margin: '0 0 8px 2px' }}>{label}</div>
      {body}
    </div>
  )
  return (
    <div data-testid="narrative-guidance" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && (
        <div style={{ maxWidth: 880, margin: '0 auto', padding: '18px 24px 60px', width: '100%', boxSizing: 'border-box' }}>
          {section('背景 · 思考', (
            <div style={{ ...ST.card, padding: 16, fontSize: 13, lineHeight: 1.65, color: 'var(--fp-text-2)' }}>
              {bg?.thinking || bg?.world_notes ? (
                <>
                  {bg?.thinking && <div style={{ whiteSpace: 'pre-wrap' }}>{bg.thinking}</div>}
                  {bg?.world_notes && <div style={{ whiteSpace: 'pre-wrap', marginTop: 8 }}>{bg.world_notes}</div>}
                  {(bg?.open_questions ?? []).length > 0 && (
                    <ul style={{ margin: '10px 0 0', paddingLeft: 18 }}>
                      {(bg?.open_questions ?? []).map((q, i) => <li key={i}>未决: {q}</li>)}
                    </ul>
                  )}
                </>
              ) : '(空)'}
            </div>
          ))}
          {section('受众与预期管理', (
            <div style={{ ...ST.card, padding: 16, fontSize: 13, lineHeight: 1.65, color: 'var(--fp-text-2)' }}>
              {au && ((au.segments ?? []).length > 0 || au.stance || (au.expectations ?? []).length > 0) ? (
                <>
                  {(au.segments ?? []).length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                      {(au.segments ?? []).map((s, i) => <span key={i} style={ST.relChip} title={s.note ?? ''}>{s.name}</span>)}
                    </div>
                  )}
                  {au.stance && <div>立场: {au.stance}</div>}
                  {(au.expectations ?? []).map((e, i) => <div key={i}>预期: {e}</div>)}
                  {(au.resonance_targets ?? []).map((e, i) => <div key={i}>共鸣目标: {e}</div>)}
                </>
              ) : '(空)'}
            </div>
          ))}
          {section(`揭示层 · ${reveals.length}`, reveals.length === 0
            ? <div style={{ ...ST.card, padding: 16, fontSize: 13, color: 'var(--fp-text-2)' }}>(空)</div>
            : (
              <div>
                {reveals.map((r) => (
                  <div key={r.id} style={{ ...ST.card, padding: '12px 16px', marginBottom: 8 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 600 }}>{r.title || r.id}</span>
                    <span style={{ fontSize: 12, color: 'var(--fp-text-3)', marginLeft: 8 }}>{String(r.order ?? '')}</span>
                    {r.rewrites && <div style={{ marginTop: 4, fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.55 }}>重写: {r.rewrites}</div>}
                  </div>
                ))}
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

// ═══ 文风矩阵与演算引擎(落地指导层) ═══
export function NarrativeStyleEngineView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const registers = proj?.registers ?? []
  const voices = proj?.voices ?? []
  const matrix = proj?.style_matrix ?? []
  const principle = (proj?.notes ?? []).find((n) => n.id === 'note-writing-principle')
  const counts = [
    ['路线节点', (proj?.nodes ?? []).length], ['连线', (proj?.connections ?? []).length],
    ['结局', (proj?.endings ?? []).length], ['数值/状态', (proj?.variables ?? []).length],
  ] as const
  return (
    <div data-testid="narrative-style-engine" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && (
        <div style={{ maxWidth: 880, margin: '0 auto', padding: '18px 24px 60px', width: '100%', boxSizing: 'border-box' }}>
          {principle && (
            <div style={{ ...ST.card, padding: 16, marginBottom: 14, borderLeft: '3px solid var(--fp-accent)', fontSize: 13, lineHeight: 1.6, color: 'var(--fp-text-2)', whiteSpace: 'pre-wrap' }}>
              {principle.body || principle.text || principle.content}
            </div>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
            <span style={ST.relChip}>语域 {registers.length}</span>
            <span style={ST.relChip}>声道 {voices.length}</span>
            <span style={ST.relChip}>矩阵格 {matrix.length}</span>
            {counts.map(([label, n]) => <span key={label} style={ST.relChip}>{label} {n}</span>)}
          </div>
          {registers.length === 0 && matrix.length === 0 && (
            <div style={ST.dim}>文风矩阵暂无认可版本(见否决案归档);语域/声道/矩阵有数据后在此陈列。</div>
          )}
          {registers.map((r) => (
            <div key={r.id} style={{ ...ST.card, padding: '10px 14px', marginBottom: 6, fontSize: 13 }}>
              <span style={{ fontFamily: MONO, fontSize: 12, color: 'var(--fp-text-3)', marginRight: 8 }}>{r.id}</span>{r.rule || ''}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ═══ 游戏内文本(落地层, 非草稿; 草稿在草稿看板) ═══
export function NarrativeGameTextView({ m }: { m: Material }) {
  const { proj, error, loading, retry } = useNarrativeProject()
  const texts = (proj?.game_texts ?? []).filter((g) => !g.is_draft)
  const byType = new Map<string, NGameText[]>()
  for (const g of texts) {
    const k = g.text_type || 'other'
    byType.set(k, [...(byType.get(k) ?? []), g])
  }
  return (
    <div data-testid="narrative-gametext" style={ST.root}>
      {loading && <div style={ST.dim}>载入引擎投影…</div>}
      {error && <EngineDown error={error} onRetry={retry} />}
      {!loading && !error && texts.length === 0 && <div style={ST.dim}>暂无正式游戏内文本(草稿在草稿看板)。</div>}
      {!loading && !error && texts.length > 0 && (
        <div style={{ overflow: 'auto', padding: '4px 20px 40px' }}>
          {[...byType.entries()].map(([type, list]) => (
            <div key={type} style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: 'var(--fp-text-3)', margin: '0 0 8px 2px' }}>{type} · {list.length}</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                {list.map((g) => (
                  <div key={g.id} style={{ ...ST.card, padding: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0 }}>{g.title || g.id}</span>
                      {g.category && <span style={{ fontSize: 11, color: 'var(--fp-text-3)' }}>{g.category}</span>}
                    </div>
                    {g.body && <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>{g.body.length > 160 ? `${g.body.slice(0, 160)}…` : g.body}</div>}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── frostpane 样式(与驾驶舱同一 token 体系, 场景庚"重整外观") ──
const ST: Record<string, CSSProperties> = {
  root: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: 'transparent', color: 'var(--fp-text)', overflow: 'auto' },
  dim: { padding: 24, color: 'var(--fp-text-3)', fontSize: 13 },
  down: {
    margin: 24, padding: '16px 18px', maxWidth: 560, borderRadius: 11,
    background: 'color-mix(in srgb, var(--fp-warn) 8%, transparent)',
    border: '1px solid color-mix(in srgb, var(--fp-warn) 30%, transparent)', color: 'var(--fp-text)',
  },
  retryBtn: {
    marginTop: 12, display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 7,
    background: 'var(--fp-card)', color: 'var(--fp-text)', border: '1px solid var(--fp-border)', cursor: 'pointer', fontSize: 13,
  },
  // "适用裁决"chip 与展开浮层(rul*)已随顶栏工厂迁到 ./narrativeToolbars。
  laneLabel: {
    fontSize: 12, color: 'var(--fp-text-3)', padding: '8px 8px', position: 'sticky', left: 0,
    display: 'flex', alignItems: 'center',
  },
  cell: {
    minHeight: 40, display: 'flex', flexDirection: 'column', gap: 6, padding: 4,
    borderRadius: 9, background: 'color-mix(in srgb, var(--fp-surface) 55%, transparent)',
  },
  card: {
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: '1px solid var(--fp-border)', borderRadius: 10, padding: 10,
    boxShadow: '0 2px 10px rgba(0,0,0,.22), inset 0 1px 0 rgba(255,255,255,.06)',
  },
  stageCard: { borderTop: '3px solid var(--fp-accent-2)' },
  cardHead: { display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 },
  cardSub: { marginTop: 6, fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.5 },
  cardFn: { marginTop: 4, fontSize: 11.5, color: 'var(--fp-text-3)', lineHeight: 1.45 },
  impChip: {
    flexShrink: 0, fontSize: 11, padding: '0 7px', height: 17, lineHeight: '17px', borderRadius: 999,
    color: 'var(--fp-text-2)', background: 'var(--fp-surface)', border: '1px solid var(--fp-border)',
  },
  relChip: {
    fontSize: 12, padding: '3px 10px', borderRadius: 999, color: 'var(--fp-text-2)',
    background: 'var(--fp-surface)', border: '1px solid var(--fp-border)',
  },
}

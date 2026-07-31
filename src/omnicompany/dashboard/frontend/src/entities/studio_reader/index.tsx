/**
 * entities/studio_reader — 「阅读视图」生产页面(材料主体视图)。
 *
 * 一个项目一个阅读页签(tab id = studio_reader:<project>)。左侧窄列表切换材料/决策过程,
 * 主屏留给材料本体。列表数据 = reviewstageApi.canvas(project)(tracks/families/versions 现成);
 * 决策数据 = DecisionPanel 自拉 material-graph 投影。
 * 主区选中材料 → 复用 review_material 的 ReviewMaterialPanel(embedded, 富渲染/圈选批注/
 * 评论/✓✕裁决/更多菜单/调级全链路, 绝不另写渲染器); 选中「决策过程」「待你裁决」→ DecisionPanel。
 *
 * 统一设计工作室 v2 F2(DEC-2026-07-05-030): 本页即"业务展示区"的宿主 ——
 * 按 displayProfiles 三项配置渲染: ①结构排布(domain-tree=域层级注册投影, 材料按业务流水线
 * 顺序分区; 回落按 track) ②版本策略三型(并存/仅最新+历史折叠/最新为主+选择性, 纯展示层,
 * 底层 version_family 链不动) ③业务类型渲染器(经 rendererRegistry, 主区自动生效)。
 *
 * 2026-07-19 阶段四第四波 · 蓝图 G 重置(合同=demo/MAPPING.md;demo 未覆盖本页, 按 MAPPING 推导):
 *   · 版本 pill 单选(F1 一排等权小按钮病灶): span+cursor → 真 button + role=radio;
 *     ≤3 内联, >3 强制收拢成行尾「vN ▾」TraceMenu(底层版本一条不丢)。
 *   · 视图切换(材料/轨迹) = v2-seg 双段(替代只显示去程的 accent 描边按钮)。
 *   · 材料行/入口行 = 整行可点(role=button + aria-current + 键盘), 类型交给 KindIcon;
 *     裸色点无图例退役;待裁决计数 = 朱红热徽章(v2-count.hot)。
 *   · 左栏 = chrome/scene 区铺格纸(玻璃 rail 退役), 栏题空心描边, 分区头 mono 大写 + ⊢N⊣ 计数。
 * 2026-07-20 内容运营层级修正: subject/revision 不再横排成标签和原生 select；收拢为一个
 *   蓝图档案定位器，弹层内按 subject → revision 树状展开。登记仍是五层，展示不再把层级摊平。
 *
 * 打开约定(接线在主线): openTab({type:'studio_reader', id:<project>}, ...)。
 */
import type React from 'react'
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import { ChevronLeft, ChevronRight, ChevronDown, Check, Flag, GitBranch, History } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { usePanels } from '../../stores/panelsStore'
import { reviewstageApi, type CanvasResponse, type CanvasMaterial, type CanvasTrack, type DomainTreeStep, type MaterialKind } from '../../api/reviewstageClient'
import { getDisplayProfile, versionPolicyFor } from '../review/displayProfiles'
import { ReviewMaterialPanel } from '../review_material'
import DecisionPanel from './DecisionPanel'
import { KindIcon } from '../review/kindIcons'
import { TraceMenu } from '../review/TraceMenu'
import { DimText } from '../../components/Segmented'
import './studioReader.css'

export interface StudioReaderEntity extends Entity {
  type: 'studio_reader'
}

const resolver: EntityResolver<StudioReaderEntity> = {
  type: 'studio_reader',
  async fetch(id) {
    return { type: 'studio_reader', id, title: `${id} 阅读` }
  },
  async list() {
    return []
  },
}

// 选中态: 材料(某 material id)| 决策过程 | 单条待裁决(仅高亮列表, 主区同为 DecisionPanel)。
type Sel =
  | { kind: 'material'; id: string }
  | { kind: 'decisions' }

function flattenMaterials(data: CanvasResponse | null): CanvasMaterial[] {
  const out: CanvasMaterial[] = []
  for (const t of data?.tracks ?? []) for (const f of t.families) for (const m of f.materials) out.push(m)
  return out
}

// family 内代表版本 = 最新(后端 families 内按 version 升序, 取末位)。
function repOf(materials: CanvasMaterial[]): CanvasMaterial {
  return materials[materials.length - 1]
}

// 展示区分区: 结构排布=domain-tree 时按域层级步骤有序分区(层级名==track), 装不进层级的
// track 保序附在后面; 结构=track 或域层级不可达时, 即画布 track 顺序(显式降级, 不白屏)。
interface Section {
  name: string
  order?: number      // 域层级序号(结构排布=domain-tree 时有)
  desc?: string       // 该层做什么(人话, 域层级注册来的)
  track?: CanvasTrack // 对应画布轨(无材料的层级为 undefined)
}

function buildSections(data: CanvasResponse | null, steps: DomainTreeStep[] | null): Section[] {
  const tracks = data?.tracks ?? []
  if (!steps || steps.length === 0) {
    return tracks.map((t) => ({ name: t.track, track: t }))
  }
  const byName = new Map(tracks.map((t) => [t.track, t]))
  const used = new Set<string>()
  const out: Section[] = []
  for (const st of [...steps].sort((a, b) => a.order - b.order)) {
    out.push({ name: st.name, order: st.order, desc: st.desc, track: byName.get(st.name) })
    used.add(st.name)
  }
  for (const t of tracks) if (!used.has(t.track)) out.push({ name: t.track, track: t })
  return out
}

function filterSections(
  sections: Section[],
  predicate: (material: CanvasMaterial) => boolean,
): Section[] {
  return sections
    .map((section) => {
      if (!section.track) return section
      const families = section.track.families
        .map((family) => ({ ...family, materials: family.materials.filter(predicate) }))
        .filter((family) => family.materials.length > 0)
      return { ...section, track: { ...section.track, families } }
    })
    // 正式域层级即使为空也保留，便于看清完整生产管线；不在域层级里的历史轨道
    // 只有当前主体/整期版本真有材料时才出现，避免“决策候选 0”“乱码轨道 0”尾随每一期。
    .filter((section) => section.order != null || (section.track?.families.length ?? 0) > 0)
}

function preferredMaterial(materials: CanvasMaterial[]): CanvasMaterial | undefined {
  const ordered = [...materials].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
  return ordered.find((m) => Boolean(m.extra?.primary_in_revision))
    ?? [...ordered].reverse().find((m) => m.status === 'pending' && m.tier === 'mandatory')
    ?? [...ordered].reverse().find((m) => m.kind === 'markdown')
    ?? ordered[ordered.length - 1]
}

// 待你裁决计数(与 DecisionPanel 同端点同口径, 只取真工作提议)。
async function fetchPendingCount(project: string): Promise<number> {
  try {
    const r = await fetch(`/api/v2/material-graph?project=${encodeURIComponent(project)}&status=adopted,proposed&include_deleted=false`)
    if (!r.ok) return 0
    const d = await r.json() as { nodes?: Array<{ record_kind?: string; status?: string; anchor?: { ref?: string } }> }
    return (d.nodes ?? []).filter((n) => n.record_kind === 'decision' && n.status === 'proposed'
      && !String(n.anchor?.ref || '').startsWith('session:')).length
  } catch { return 0 }
}

/** 行点击键盘等价(Enter/Space)。 */
function rowKeyDown(fn: () => void) {
  return (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn() } }
}

export function StudioReaderPanel({ project, initialMaterialId }: { project: string; initialMaterialId?: string }) {
  const [data, setData] = useState<CanvasResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [sel, setSel] = useState<Sel | null>(null)
  // version_family → 用户手挑的 material id(不挑则默认代表版本)。
  const [versionPick, setVersionPick] = useState<Record<string, string>>({})
  // version_family → 历史抽屉展开(latest-collapse / latest-selective 两型用)。
  const [historyOpen, setHistoryOpen] = useState<Record<string, boolean>>({})
  const [subjectId, setSubjectId] = useState('')
  const [revision, setRevision] = useState<number | null>(null)
  const [subjectHistoryOpen, setSubjectHistoryOpen] = useState(false)
  // 域层级步骤(结构排布=domain-tree 时拉取); null=不适用或不可达(降级按 track)。
  const [steps, setSteps] = useState<DomainTreeStep[] | null>(null)
  const [structureFallback, setStructureFallback] = useState(false)
  const [pendingCount, setPendingCount] = useState<number | null>(null)
  const openTab = usePanels((s) => s.openTab)

  const profile = useMemo(() => getDisplayProfile(project), [project])

  useEffect(() => {
    let alive = true
    setData(null); setError(null)
    reviewstageApi.canvas(project, {
      includeArchived: Boolean(profile.subjectHierarchy?.includeArchivedHistory),
    })
      .then((d) => { if (alive) setData(d) })
      .catch((e) => { if (alive) setError(String(e instanceof Error ? e.message : e)) })
    return () => { alive = false }
  }, [project, profile.subjectHierarchy?.includeArchivedHistory])

  useEffect(() => {
    let alive = true
    setSteps(null); setStructureFallback(false)
    if (profile.structure !== 'domain-tree') return
    reviewstageApi.domainTree(project)
      .then((d) => {
        if (!alive) return
        const flat = d.domains.flatMap((dom) => dom.steps)
        if (flat.length === 0) setStructureFallback(true)
        else setSteps(flat)
      })
      .catch(() => { if (alive) setStructureFallback(true) })
    return () => { alive = false }
  }, [project, profile.structure])

  useEffect(() => {
    let alive = true
    setPendingCount(null)
    fetchPendingCount(project).then((n) => { if (alive) setPendingCount(n) })
    return () => { alive = false }
  }, [project])

  const all = useMemo(() => flattenMaterials(data), [data])
  const sections = useMemo(() => buildSections(data, steps), [data, steps])
  const subjects = useMemo(() => {
    const subjectType = profile.subjectHierarchy?.subjectType
    return (data?.subjects ?? []).filter((subject) => !subjectType || subject.subject_type === subjectType)
  }, [data, profile.subjectHierarchy?.subjectType])
  const selectedSubject = subjects.find((subject) => subject.subject_id === subjectId)
  const selectedRevision = selectedSubject?.revisions.find((item) => item.revision === revision)
  const visibleAll = useMemo(() => {
    if (!profile.subjectHierarchy || !subjectId || revision == null) return all
    return all.filter((material) => material.subject_id === subjectId && material.revision === revision)
  }, [all, profile.subjectHierarchy, subjectId, revision])
  const visibleSections = useMemo(() => {
    if (!profile.subjectHierarchy || !subjectId || revision == null) return sections
    return filterSections(
      sections,
      (material) => material.subject_id === subjectId && material.revision === revision,
    )
  }, [sections, profile.subjectHierarchy, subjectId, revision])

  // 带主体层级的业务先定位 subject/revision。深链材料优先；否则默认第一个主体的最新整期版本。
  useEffect(() => {
    if (!profile.subjectHierarchy || !subjects.length || subjectId) return
    const deepLinked = initialMaterialId
      ? all.find((material) => material.id === initialMaterialId)
      : undefined
    const initialSubject = subjects.find((subject) => subject.subject_id === deepLinked?.subject_id) ?? subjects[0]
    const initialRevision = deepLinked?.revision
      ?? initialSubject.revisions[0]?.revision
      ?? null
    setSubjectId(initialSubject.subject_id)
    setRevision(initialRevision)
    if (deepLinked) setSel({ kind: 'material', id: deepLinked.id })
  }, [all, initialMaterialId, profile.subjectHierarchy, subjectId, subjects])

  // 进来默认选中: 深链带来的 initialMaterialId 优先(存在于列表时), 否则最新一条设计评审文档(有则),
  // 再否则任意最新材料 —— 主屏一进来就是材料本身。
  useEffect(() => {
    if (!all.length) return
    if (profile.subjectHierarchy) {
      if (!subjectId || revision == null || !visibleAll.length) return
      if (sel?.kind === 'material' && visibleAll.some((material) => material.id === sel.id)) return
      const preferred = preferredMaterial(visibleAll)
      if (preferred) setSel({ kind: 'material', id: preferred.id })
      return
    }
    if (sel) return
    const deepLinked = initialMaterialId && all.find((m) => m.id === initialMaterialId)
    if (deepLinked) { setSel({ kind: 'material', id: deepLinked.id }); return }
    const md = [...all].reverse().find((m) => m.kind === 'markdown')
    setSel({ kind: 'material', id: (md ?? all[all.length - 1]).id })
  }, [all, sel, initialMaterialId, profile.subjectHierarchy, revision, subjectId, visibleAll])

  const gotoCanvas = useCallback(() => {
    // 不带 facet: 画布本就是项目页默认 tab, facet 会进 tab id 造成第二个项目页签
    openTab({ type: 'project', id: project }, project)
  }, [openTab, project])

  const selMaterialId = sel?.kind === 'material' ? sel.id : null
  const railWidth = profile.subjectHierarchy ? 360 : 300

  const chooseSubject = (nextSubjectId: string) => {
    const next = subjects.find((subject) => subject.subject_id === nextSubjectId)
    setSubjectId(nextSubjectId)
    setRevision(next?.revisions[0]?.revision ?? null)
    setSubjectHistoryOpen(false)
    setSel(null)
  }

  return (
    <div style={S.app} data-testid="studio-reader">
      {/* ── 左侧窄列表(chrome 格纸壳) ── */}
      <aside className="sr-rail" style={{ ...S.rail, flexBasis: collapsed ? 52 : railWidth, width: collapsed ? 52 : railWidth }} data-testid="studio-reader-rail">
        {collapsed ? (
          <div style={S.strip}>
            <button type="button" className="v2-iconbtn" title="展开列表"
              data-testid="reader-expand" onClick={() => setCollapsed(false)}>
              <ChevronRight size={15} aria-hidden />
            </button>
          </div>
        ) : (
          <div style={S.railFull}>
            <div style={S.railHead}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="sr-brand" title={project}>材料 · {project}</div>
              </div>
              <button type="button" className="v2-iconbtn" title="收起列表"
                data-testid="reader-collapse" onClick={() => setCollapsed(true)}>
                <ChevronLeft size={15} aria-hidden />
              </button>
            </div>

            {/* 视图双段(材料/轨迹): 去程回程同层可见, 替代单向 accent 按钮 */}
            <div style={S.railActions}>
              <span className="v2-seg sr-viewseg" role="radiogroup" aria-label="视图">
                <button type="button" className="seg-i" role="radio" aria-checked="true">材料</button>
                <button type="button" className="seg-i" role="radio" aria-checked="false"
                  data-testid="reader-goto-canvas" onClick={gotoCanvas}>
                  <GitBranch size={12} aria-hidden />轨迹
                </button>
              </span>
            </div>

            {profile.subjectHierarchy && subjects.length > 0 && (
              <div className="sr-scope" data-testid="reader-subject-hierarchy">
                <div className="sr-locator-menu">
                  <TraceMenu
                    label={`${profile.subjectHierarchy.label}档案`}
                    minWidth={324}
                    onOpenChange={(open) => { if (!open) setSubjectHistoryOpen(false) }}
                    trigger={(open, toggle) => (
                      <button
                        type="button"
                        className="sr-locator"
                        aria-expanded={open}
                        aria-haspopup="tree"
                        data-testid="reader-subject-locator"
                        onClick={toggle}
                      >
                        <span className="sr-locator-mark" aria-hidden>{selectedSubject?.subject_id ?? '—'}</span>
                        <span className="sr-locator-copy">
                          <strong>{selectedSubject?.title ?? '选择视频'}</strong>
                          <small data-testid="reader-revision-summary">
                            {revision == null
                              ? '尚未登记版本'
                              : `v${revision} · ${selectedRevision?.stages.join(' · ') || '尚未登记阶段材料'}`}
                          </small>
                        </span>
                        <ChevronDown size={14} aria-hidden />
                      </button>
                    )}
                  >
                    {(close) => (
                      <div className="sr-archive-tree" role="tree" aria-label={`${profile.subjectHierarchy?.label ?? '视频'}与版本`}>
                        <div className="sr-archive-head">
                          <span>视频档案</span>
                          <DimText>{subjects.length} 部</DimText>
                        </div>
                        {subjects.map((subject) => {
                          const current = subject.subject_id === subjectId
                          return (
                            <div className="sr-archive-node" key={subject.subject_id} role="treeitem" aria-expanded={current}>
                              <button
                                type="button"
                                className="sr-archive-subject"
                                aria-current={current ? 'true' : undefined}
                                title={subject.title}
                                data-testid={`reader-subject-${subject.subject_id}`}
                                onClick={() => chooseSubject(subject.subject_id)}
                              >
                                <span className="sr-tree-elbow" aria-hidden>{current ? '└' : '├'}</span>
                                <span className="sr-archive-id">{subject.subject_id}</span>
                                <span className="sr-archive-title">{subject.title}</span>
                                <ChevronRight className="sr-archive-arrow" size={12} aria-hidden />
                              </button>
                              {current && (
                                <div className="sr-archive-revisions" role="group" aria-label={`${subject.subject_id} 版本历史`}>
                                  {subject.revisions.filter((item) => item.revision === revision).map((item) => (
                                    <button
                                      key={item.revision}
                                      type="button"
                                      className="sr-archive-revision"
                                      role="radio"
                                      aria-checked={item.revision === revision}
                                      data-testid={`reader-revision-${item.revision}`}
                                      onClick={() => { setRevision(item.revision); setSel(null); close() }}
                                    >
                                      <span className="sr-rev-node" aria-hidden />
                                      <strong>v{item.revision}</strong>
                                      <span>{item.stages.join(' · ') || '尚无阶段'}</span>
                                      {item.archived_count > 0 && <small>{item.archived_count} 已归档</small>}
                                    </button>
                                  ))}
                                  {subject.revisions.length > 1 && (
                                    <button
                                      type="button"
                                      className="sr-archive-history"
                                      aria-expanded={subjectHistoryOpen}
                                      data-testid="reader-revision-history-toggle"
                                      onClick={() => setSubjectHistoryOpen((open) => !open)}
                                    >
                                      <History size={11} aria-hidden />
                                      {subjectHistoryOpen ? '收起历史版本' : `${subject.revisions.length - 1} 个历史版本`}
                                      <ChevronDown size={11} aria-hidden />
                                    </button>
                                  )}
                                  {subjectHistoryOpen && subject.revisions
                                    .filter((item) => item.revision !== revision)
                                    .map((item) => (
                                      <button
                                        key={item.revision}
                                        type="button"
                                        className="sr-archive-revision"
                                        role="radio"
                                        aria-checked="false"
                                        data-testid={`reader-revision-${item.revision}`}
                                        onClick={() => { setRevision(item.revision); setSel(null); close() }}
                                      >
                                        <span className="sr-rev-node" aria-hidden />
                                        <strong>v{item.revision}</strong>
                                        <span>{item.stages.join(' · ') || '尚无阶段'}</span>
                                        {item.archived_count > 0 && <small>{item.archived_count} 已归档</small>}
                                      </button>
                                    ))}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </TraceMenu>
                </div>
              </div>
            )}

            <div style={S.railScroll}>
              {error && <div style={S.railError} data-testid="studio-reader-error">载入材料出错: {error}</div>}
              {!error && !data && <div style={S.railDim}>载入中…</div>}
              {data && all.length === 0 && (
                <div style={S.railDim} data-testid="studio-reader-empty">该项目还没有带 track/version 的材料。</div>
              )}

              {structureFallback && all.length > 0 && (
                <div className="sr-fallback" data-testid="reader-structure-fallback">
                  域层级注册不可达或为空, 暂按 track 陈列。
                </div>
              )}

              {visibleSections.map((sec) => {
                const families = sec.track?.families ?? []
                const policy = versionPolicyFor(profile, sec.name)
                return (
                  <div key={sec.name} style={{ margin: '6px 4px 4px' }} data-testid={`reader-section-${sec.name}`}>
                    <div className="sr-sechead" title={sec.desc || undefined}>
                      {sec.order != null && <span className="seq">{sec.order}</span>}
                      <span className="nm">{sec.name}</span>
                      <DimText>{families.length}</DimText>
                      <span className="ln" aria-hidden="true" />
                    </div>
                    {families.length === 0 && sec.order != null && (
                      <div className="sr-laneempty">该层暂无材料</div>
                    )}
                    {families.map((fam) => {
                      const mats = fam.materials
                      const latest = repOf(mats)
                      const picked = versionPick[fam.family]
                      const rep = (picked && mats.find((m) => m.id === picked)) || latest
                      const active = selMaterialId != null && mats.some((m) => m.id === selMaterialId)
                      const activeInFam = active ? selMaterialId : rep.id
                      // 版本策略(纯展示层): 决定哪些版本片可见, 其余折叠进历史抽屉。
                      const expanded = !!historyOpen[fam.family]
                      let visible: CanvasMaterial[]
                      if (mats.length <= 1) visible = []
                      else if (policy === 'coexist' || expanded) visible = mats
                      else if (policy === 'latest-collapse') visible = []
                      else { // latest-selective: 最新 + 被钉住的版本
                        const pinned = mats.filter((m) => m !== latest && Boolean((m.extra as Record<string, unknown> | undefined)?.display_pinned))
                        visible = pinned.length ? [...pinned, latest] : []
                      }
                      const hiddenCount = mats.length > 1 && policy !== 'coexist'
                        ? mats.length - (visible.length || 1)
                        : 0
                      const pickVersion = (id: string) => {
                        setVersionPick((p) => ({ ...p, [fam.family]: id }))
                        setSel({ kind: 'material', id })
                      }
                      return (
                        <div
                          key={fam.family}
                          className="sr-row"
                          role="button"
                          tabIndex={0}
                          aria-current={active}
                          data-testid={`reader-material-${latest.id}`}
                          onClick={() => setSel({ kind: 'material', id: rep.id })}
                          onKeyDown={rowKeyDown(() => setSel({ kind: 'material', id: rep.id }))}
                        >
                          <span className="ic"><KindIcon kind={rep.kind as MaterialKind} size={14} /></span>
                          <span className="t" title={rep.title}>{rep.title}</span>
                          {/* 版本单选: ≤3 内联 radio pill;>3 强制收拢「vN ▾」弹层 */}
                          {visible.length > 1 && (visible.length <= 3 ? (
                            <span className="sr-ver" role="radiogroup" aria-label="版本">
                              {visible.map((m) => (
                                <button
                                  key={m.id}
                                  type="button"
                                  className="sr-verpill"
                                  role="radio"
                                  aria-checked={m.id === activeInFam}
                                  data-testid={`reader-version-${m.id}`}
                                  onClick={(e) => { e.stopPropagation(); pickVersion(m.id) }}
                                >v{m.version ?? '?'}</button>
                              ))}
                            </span>
                          ) : (
                            <span className="sr-ver" onClick={(e) => e.stopPropagation()}>
                              <TraceMenu
                                label="版本"
                                trigger={(open, toggle) => (
                                  <button type="button" className="sr-vermore" aria-expanded={open} aria-haspopup="true" onClick={toggle}>
                                    v{mats.find((m) => m.id === activeInFam)?.version ?? rep.version ?? '?'}
                                    <ChevronDown size={10} aria-hidden />
                                  </button>
                                )}
                              >
                                {(close) => visible.map((m) => (
                                  <button
                                    key={m.id}
                                    type="button"
                                    className="v2-checkrow"
                                    role="radio"
                                    aria-checked={m.id === activeInFam}
                                    data-testid={`reader-version-${m.id}`}
                                    onClick={() => { pickVersion(m.id); close() }}
                                  >
                                    <span className="cb" aria-hidden><Check size={11} strokeWidth={3} /></span>
                                    <span className="cr-t">v{m.version ?? '?'} · {m.title}</span>
                                  </button>
                                ))}
                              </TraceMenu>
                            </span>
                          ))}
                          {(hiddenCount > 0 || (expanded && policy !== 'coexist' && mats.length > 1)) && (
                            <button
                              type="button"
                              className="sr-hist"
                              aria-expanded={expanded}
                              data-testid={`reader-history-toggle-${fam.family}`}
                              title={expanded ? '收起历史版本' : `${hiddenCount} 个历史版本折叠中(底层版本一条不丢)`}
                              onClick={(e) => {
                                e.stopPropagation()
                                setHistoryOpen((h) => ({ ...h, [fam.family]: !h[fam.family] }))
                              }}
                            >
                              <History size={11} aria-hidden />{expanded ? '收起' : hiddenCount}
                            </button>
                          )}
                          <span className="st">{rep.archived
                            ? <History size={13} color="var(--fp-text-3)" aria-label="已归档" />
                            : rep.status === 'accepted'
                              ? <Check size={14} color="var(--fp-ok)" aria-label="已通过" />
                              : null}</span>
                        </div>
                      )
                    })}
                  </div>
                )
              })}

              {/* ── 底部两个入口 ── */}
              <div style={{ margin: '14px 4px 4px' }}>
                <div
                  className="sr-row"
                  role="button"
                  tabIndex={0}
                  aria-current={sel?.kind === 'decisions'}
                  data-testid="reader-goto-decisions"
                  onClick={() => setSel({ kind: 'decisions' })}
                  onKeyDown={rowKeyDown(() => setSel({ kind: 'decisions' }))}
                >
                  <span className="ic"><Flag size={14} color="var(--fp-bp-brass-hi)" aria-hidden /></span>
                  <span className="t">决策过程</span>
                </div>
                <div
                  className="sr-row"
                  role="button"
                  tabIndex={0}
                  aria-current={sel?.kind === 'decisions'}
                  data-testid="reader-goto-pending"
                  onClick={() => setSel({ kind: 'decisions' })}
                  onKeyDown={rowKeyDown(() => setSel({ kind: 'decisions' }))}
                >
                  <span className="ic"><span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--fp-warn)', display: 'inline-block' }} aria-hidden /></span>
                  <span className="t">待你裁决</span>
                  {pendingCount != null && pendingCount > 0 && (
                    <span className="v2-count hot" data-testid="reader-pending-count">{pendingCount}</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </aside>

      {/* ── 主区(绝对主角) ── */}
      <section style={S.stage} data-testid="studio-reader-stage">
        {sel?.kind === 'material' && (
          // 富渲染/圈选批注/评论/✓✕裁决/调级全链路复用 review_material; embedded=顶栏不塞「返回源」。
          <ReviewMaterialPanel key={sel.id} id={sel.id} embedded />
        )}
        {sel?.kind === 'decisions' && (
          <div style={S.decWrap}><DecisionPanel project={project} /></div>
        )}
      </section>
    </div>
  )
}

const S: Record<string, CSSProperties> = {
  app: { display: 'flex', height: '100%', minHeight: 0, background: 'transparent', color: 'var(--fp-text)', fontFamily: 'var(--fp-font-sans)' },

  rail: {
    flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0,
    transition: 'flex-basis .26s cubic-bezier(.4,.8,.3,1), width .26s cubic-bezier(.4,.8,.3,1)',
    position: 'relative', zIndex: 5,
  },
  railFull: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 },
  strip: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '12px 0', height: '100%' },

  railHead: { display: 'flex', alignItems: 'center', gap: 9, padding: '10px 12px', borderBottom: '1px solid var(--fp-border-subtle)', flexShrink: 0 },
  railActions: { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: '1px solid var(--fp-border-subtle)', flexShrink: 0 },

  railScroll: { overflow: 'auto', flex: 1, minHeight: 0, padding: '8px 8px 24px' },
  railError: { color: 'var(--fp-err)', fontSize: 12.5, padding: '10px 8px', lineHeight: 1.6 },
  railDim: { color: 'var(--fp-text-3)', fontSize: 12.5, padding: '10px 8px', lineHeight: 1.6 },

  stage: { flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', position: 'relative' },
  decWrap: { flex: 1, minHeight: 0, overflow: 'auto' },
}

// facet=材料 id(深链定位): 由审阅列表「在项目工作台打开」经 openTab(..., m.id) 传入,
// 阅读页优先选中该材料(件二 DEC-2026-07-06-082/083)。
const Editor: React.ComponentType<{ entity: StudioReaderEntity; facet?: string }> = ({ entity, facet }) => (
  <StudioReaderPanel project={entity.id} initialMaterialId={facet} />
)

export const studioReaderRegistration: EntityRegistration<StudioReaderEntity> = {
  resolver,
  renderer: { type: 'studio_reader', Editor },
  label: '阅读视图',
  icon: 'book-open',
}

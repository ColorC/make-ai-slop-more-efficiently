// 项目详情页 — 从首页项目卡点开。上半: 项目头(背景/名称/标签/roots/index 路径);
// 下半: 材料轨迹 / 计划 / 对话 / 管线 / 技能 / 文件 / 审阅 / 札记 内页签, 把散在各处的
// 项目相关物聚到一个入口(计划=docs/plans 关联类目; 对话=cc sessions 的 active_plan 归属;
// 审阅=reviewstage 按 plan 过滤; 技能=atlas+omni run 注册表; 文件=真目录树懒加载)。
//
// 2026-07-06 用户裁决: 低频内容不再占版面 —— 常用工作选项区块整体删除(quick_actions 数据仍在
// PROJECT_INDEX.md/omni project show, 只是网页不渲染); 顶部"新建计划书/写草稿"删除(札记页签内
// NotesForTarget 自带写草稿入口); 快速行为不再走网页点击, 全部内容经过 AI(技能页签只做集合与
// 复制调用词)。
//
// 2026-06-29 深度重建: 各内页签从拥挤的等宽列表行 → 磨砂玻璃卡片网格(ItemCard 抽到 cards.tsx)。

import React, { useEffect, useMemo, useState } from 'react'
import { Copy, Check, ExternalLink, FileText, MessageSquare, GitBranch, ClipboardCheck, Play } from 'lucide-react'
import { projectsApi, type ProjectItem } from '../../api/projectsClient'
import { ccApi } from '../../api/ccClient'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import DomainTreeBar from './DomainTreeBar'
import { usePanels } from '../../stores/panelsStore'
import { copyText } from '../../lib/copyText'
import { openInVscode } from '../../lib/openInVscode'
import { relTimeZh as relTime } from '../../lib/time'
import NotesForTarget from '../authored/NotesForTarget'
import { GLASS, MONO, EASE, ItemCard } from './cards'
import ProjectSkills from './ProjectSkills'
import ProjectFileTree from './ProjectFileTree'
import { ReviewCanvas } from '../review-canvas'
import type { CanvasMaterial } from '../../api/reviewstageClient'
import { bossSightApi, type BossSightBindingBucket } from '../../api/bossSightClient'
import ActiveConvoBadge from '../../shared/view/ui/ActiveConvoBadge'

// 决策树标签页已删除(2026-07-04 用户裁决 DEC-2026-07-04-240:该外观=在图里画散落列表,绝对不行;
// 「下次怎么做」的决策树将以具象化管线形态回归,入口=管线栏,DEC-2026-07-04-239)。
type TabKey = 'canvas' | 'plans' | 'convos' | 'teams' | 'skills' | 'files' | 'reviews' | 'authored'

// 项目→内置网页 demo(web_review 实体目标 id)。有 demo 的项目在工作台直达, 消除"项目页与 demo 割裂"。
const DEMO_BY_PROJECT: Record<string, string> = { vilo: 'vilo-demo', walker: 'walker-game' }

// 注册表 id → 决策库 project 的映射已统一到后端(narrative.canon_project),前端直接传 entity.id。

const S: Record<string, any> = {
  // 外壳透明, 让背后冷色渐变透出来; 滚动内容区自带安静近实色表面
  root: { height: '100%', overflow: 'auto', background: 'transparent', color: 'var(--fp-text)', boxSizing: 'border-box' },
  // 项目头 = 沉浸图背景 + 压暗层, 是页面唯一主焦点
  hero: { position: 'relative', minHeight: 148, borderBottom: '1px solid var(--fp-border)', display: 'flex', alignItems: 'flex-end' },
  heroBg: { position: 'absolute', inset: 0 },
  // 压暗层(2026-06-12 用户: 图片背景撞色压字)
  heroOverlay: { position: 'absolute', inset: 0, background: 'linear-gradient(to top, rgba(4,7,10,.95) 6%, rgba(4,7,10,.62) 56%, rgba(4,7,10,.24) 100%)' },
  heroInner: { position: 'relative', padding: '20px 24px 16px', width: '100%', boxSizing: 'border-box' },
  // 标题层级靠字号拉开: 名称 18/650 醒目
  name: { fontSize: 18, fontWeight: 650, letterSpacing: '-0.01em', color: '#fff', textShadow: '0 1px 4px rgba(0,0,0,.95)' },
  desc: { color: 'rgba(238,241,246,.86)', fontSize: 13, marginTop: 6, lineHeight: 1.5, textShadow: '0 1px 3px rgba(0,0,0,.9)' },
  metaRow: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12, alignItems: 'center' },
  // 头部 chip = 胶囊玻璃, 浮在沉浸背景上
  chip: { display: 'inline-flex', alignItems: 'center', gap: 5, border: '1px solid rgba(255,255,255,.18)', borderRadius: 999, padding: '3px 10px', fontSize: 12, color: 'var(--fp-text)', background: 'rgba(11,15,23,.42)', backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)' },
  body: { padding: '20px 24px 36px' },
  // 区标题 13/650, 弱灰大写感, 不再 15/700 抢戏
  secTitle: { color: 'var(--fp-text-2)', fontSize: 13, fontWeight: 650, letterSpacing: '.02em', margin: '24px 0 12px' },
  // 统一卡片网格: 异构图文卡铺成自适应列, 4px 栅格放宽呼吸
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 },

  // 内页签 = shadcn 分段控件: 圆角胶囊跑道, 无底部分割线; 选中页与内容无缝(同 surface 浮起),
  // 未选中弱底凹陷。整条 tabs 自身是一道极淡凹槽, 选中片浮在槽上。
  tabs: { display: 'inline-flex', alignItems: 'center', gap: 4, flexWrap: 'wrap', padding: 4, borderRadius: 10, background: 'var(--fp-surface)', border: '1px solid var(--fp-border-subtle)', margin: '24px 0 16px' },
  tab: (active: boolean): React.CSSProperties => ({
    border: '1px solid ' + (active ? 'var(--fp-border)' : 'transparent'),
    borderRadius: 7,
    background: active ? 'var(--fp-glass-2)' : 'transparent',
    color: active ? 'var(--fp-text)' : 'var(--fp-text-3)',
    padding: '6px 13px', cursor: 'pointer', fontSize: 13, fontWeight: active ? 600 : 500,
    boxShadow: active ? 'inset 0 1px 0 rgba(255,255,255,.07)' : 'none',
    transition: `color 150ms ${EASE}, background 150ms ${EASE}`,
  }),
  tabCount: (active: boolean): React.CSSProperties => ({ marginLeft: 5, fontSize: 12, fontWeight: 500, color: active ? 'var(--fp-text-3)' : 'var(--fp-border-strong)' }),
  dim: { color: 'var(--fp-text-3)', fontSize: 13, padding: 24, textAlign: 'center' as const },
  warn: { color: 'var(--fp-warn)', fontSize: 13, padding: '8px 2px' },

  // 打开 Demo(仅 vilo/walker 有): 保留为唯一顶部操作 —— 新建计划书/写草稿按钮已删
  // (2026-07-06 用户: 与札记内新建入口重复)。
  demoBtn: {
    display: 'inline-flex', alignItems: 'center', gap: 6,
    background: 'var(--fp-accent-weak)', color: 'var(--fp-accent)',
    border: '1px solid var(--fp-border)', borderRadius: 7, padding: '7px 14px',
    fontSize: 13, fontWeight: 600, cursor: 'pointer', transition: `filter 150ms ${EASE}`,
  } as React.CSSProperties,
}

function CopyBtn({ text, label = '复制' }: { text: string; label?: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'fail'>('idle')
  const style = { height: 24, border: '1px solid var(--fp-border)', background: 'var(--fp-surface)', color: 'var(--fp-text-2)', borderRadius: 7, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5, padding: '0 9px', fontSize: 12, transition: `all 150ms ${EASE}` }
  return (
    <button type="button" style={style} title={text}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border-strong)'; e.currentTarget.style.color = 'var(--fp-text)' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border)'; e.currentTarget.style.color = 'var(--fp-text-2)' }}
      onClick={() => {
      void copyText(text).then((ok) => {
        setState(ok ? 'done' : 'fail')
        window.setTimeout(() => setState('idle'), 1400)
      })
    }}>
      {state === 'done' ? <Check size={11} /> : <Copy size={11} />}
      {state === 'done' ? '已复制' : state === 'fail' ? '复制失败' : label}
    </button>
  )
}

/** 在 VSCode 打开文件/目录 — webview 里走 open-file 消息桥, 浏览器里走 vscode:// 协议。 */
function OpenBtn({ path, label = '打开' }: { path: string; label?: string }) {
  const style = { height: 24, border: '1px solid var(--fp-border)', background: 'var(--fp-surface)', color: 'var(--fp-text-2)', borderRadius: 7, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5, padding: '0 9px', fontSize: 12, transition: `all 150ms ${EASE}` }
  return (
    <button type="button" style={style} data-testid="open-in-vscode" title={`在 VSCode 打开\n${path}`}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border-strong)'; e.currentTarget.style.color = 'var(--fp-text)' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border)'; e.currentTarget.style.color = 'var(--fp-text-2)' }}
      onClick={(e) => {
      e.stopPropagation()
      openInVscode(path)
    }}>
      <ExternalLink size={11} />{label}
    </button>
  )
}

function heroBackground(p?: ProjectItem | null): string {
  const bg = (p?.bg || '').trim()
  if (bg && (/^(https?:|data:|\/|\.\/)/.test(bg) || /\.(png|jpe?g|webp)(\?|$)/i.test(bg))) {
    return `center/cover no-repeat url("${bg.replace(/"/g, '%22')}")`
  }
  return bg || 'linear-gradient(120deg, #16344c 0%, var(--fp-bg) 95%)'
}

export default function ProjectDetail({ entity, facet }: { entity: { id: string }; facet?: string }) {
  const [proj, setProj] = useState<ProjectItem | null>(null)
  const [plans, setPlans] = useState<{ id: string; title: string; date?: string; archived?: boolean }[] | null>(null)
  const [planIds, setPlanIds] = useState<Set<string> | null>(null)
  const [convos, setConvos] = useState<{ id: string; title: string; ts?: string | null }[] | null>(null)
  const [reviews, setReviews] = useState<Material[] | null>(null)
  const [teams, setTeams] = useState<any[] | null>(null)  // 管线(team*.py), 按项目 roots 归属
  const [hasDomain, setHasDomain] = useState(false)       // 项目是否属注册域(决策树管线栏/开工入口据此出现)
  const [tab, setTab] = useState<TabKey>((facet as TabKey) || 'canvas')   // 首位默认=材料轨迹(场景一)
  const [binding, setBinding] = useState<BossSightBindingBucket | undefined>(undefined)
  const openTab = usePanels((s) => s.openTab)

  useEffect(() => {
    projectsApi.list().then((b) => setProj(b.projects.find((p) => p.id === entity.id) || null)).catch(() => setProj(null))
    // 本项目的活跃对话聚合(chat+PTY 全覆盖, SESSION-SELF-BINDING 4.5)。
    bossSightApi.activeBindings().then((d) => setBinding(d.by_project?.[entity.id])).catch(() => setBinding(undefined))
    // 计划归属由服务端唯一判定(治理覆盖表优先, core.resolve_project_plans) —
    // 2026-06-12 用户: 各项目计划列表全错; 前端不再自带一份前缀匹配逻辑。
    projectsApi.plans(entity.id).then((d) => {
      setPlans(d.items.map((p) => ({
        id: p.id,
        title: `${p.date ? p.date + ' ' : ''}${p.title_zh || p.topic}${p.archived ? ' (已归档)' : ''}`,
        date: p.date,
        archived: p.archived,
      })))
      setPlanIds(new Set(d.plan_ids))
    }).catch(() => { setPlans([]); setPlanIds(new Set()) })
    // 管线(team*.py): 取全量, 下面按本项目 roots 归属(roots 已校准到 packages/ 包根)
    fetch('/api/teams').then((r) => (r.ok ? r.json() : { items: [] })).then((d) => setTeams((d.items as any[]) || [])).catch(() => setTeams([]))
    // 决策树=具象管线: 项目属注册域才亮出管线栏步骤卡。
    reviewstageApi.domainTree(entity.id).then((d) => setHasDomain(d.domains.length > 0)).catch(() => setHasDomain(false))
  }, [entity.id])

  useEffect(() => {
    if (!planIds) return
    const inProject = (planId?: string | null) => !!planId && planIds.has(planId)
    ccApi.list().then((ss: any[]) => {
      setConvos(ss
        .filter((s) => inProject(s.active_plan))
        .map((s) => ({ id: s.id, title: s.title || s.preview || s.id, ts: s.updated_at || s.created_at })))
    }).catch(() => setConvos([]))
    reviewstageApi.list({}).then((full) => {
      setReviews(full.items.filter((m) => inProject(m.source_plan_id)))
    }).catch(() => setReviews([]))
  }, [planIds])

  // 本项目的管线: team 源文件落在项目 roots 下(roots 已校准到 packages/ 包根, 引用级源文件归属)
  const projTeams = useMemo(() => {
    if (!teams || !proj) return [] as any[]
    const norm = (p?: string) => (p || '').replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '')
    const roots = (proj.roots || []).map(norm).filter(Boolean)
    return teams.filter((t) => {
      const f = norm(t.file_path)
      return !!f && roots.some((r) => f === r || f.startsWith(r + '/'))
    })
  }, [teams, proj])

  // 待办的唯一面=全局审阅台(汇聚所有项目按时间/类型排;D4 降级指它)。项目页这栏保持原样的
  // "审阅"平列(全状态),不另设项目级待办(2026-07-04 用户纠正:别在项目里新建待办)。

  return (
    <div style={S.root} data-testid="project-detail">
      <div style={S.hero}>
        <div style={{ ...S.heroBg, background: heroBackground(proj) }} />
        <div style={S.heroOverlay} />
        <div style={S.heroInner}>
          <div style={S.name}>{proj?.name || entity.id}</div>
          {proj?.desc && <div style={S.desc}>{proj.desc}</div>}
          <div style={S.metaRow}>
            {(proj?.tags || []).map((t) => <span key={t} style={S.chip}>{t}</span>)}
            <span style={S.chip}>活跃 {relTime(proj?.last_active)}</span>
            {/* N 活跃对话在推进本项目(chat+PTY 全覆盖) */}
            {binding && <ActiveConvoBadge active={binding.active} total={binding.total} sessions={binding.sessions} size="md" />}
            {proj?.index_path && (
              <span style={S.chip} data-testid="project-detail-index-path">
                index {proj.index_ok === false ? '⚠' : ''}
                <CopyBtn text={proj.index_path} label="复制路径" />
                <OpenBtn path={proj.index_path} />
              </span>
            )}
            {(proj?.links || []).map((l, i) => (
              <a key={i} href={l.url} target="_blank" rel="noreferrer" style={{ ...S.chip, textDecoration: 'none' }}>
                <ExternalLink size={11} />{l.label}
              </a>
            ))}
          </div>
        </div>
      </div>

      <div style={S.body}>
        {/* 顶部只留"打开 Demo"(有内置 demo 的项目)。新建计划书/写草稿已删(2026-07-06 用户:
            与札记页签内的新建入口重复); 常用工作选项区块已删(低频占版面, 技能集合移入「技能」页签)。 */}
        {proj?.index_ok === false && <div style={S.warn}>index 文件校验未通过: {proj.index_error}</div>}
        {DEMO_BY_PROJECT[entity.id] && (
          <button
            type="button" data-testid="project-open-demo" style={S.demoBtn}
            onClick={() => openTab({ type: 'web_review', id: DEMO_BY_PROJECT[entity.id] }, `${proj?.name || entity.id} Demo`)}
            onMouseEnter={(e) => { e.currentTarget.style.filter = 'brightness(1.15)' }}
            onMouseLeave={(e) => { e.currentTarget.style.filter = 'none' }}
          ><Play size={14} />打开 Demo</button>
        )}

        <div style={S.tabs} role="tablist">
          {([
            ['canvas', '材料轨迹', undefined],
            ['plans', '计划', plans ? plans.length : undefined],
            ['convos', '对话', convos ? convos.length : undefined],
            ['teams', '管线', teams ? projTeams.length : undefined],
            ['skills', '技能', undefined],
            ['files', '文件', undefined],
            ['reviews', '审阅', reviews ? reviews.length : undefined],
            ['authored', '札记', undefined],
          ] as [TabKey, string, number | undefined][]).map(([k, label, count]) => {
            const active = tab === k
            return (
              <button key={k} type="button" role="tab" aria-selected={active} style={S.tab(active)} data-testid={`project-tab-${k}`}
                onClick={() => setTab(k)}
                onMouseEnter={(e) => { if (!active) e.currentTarget.style.color = 'var(--fp-text-2)' }}
                onMouseLeave={(e) => { if (!active) e.currentTarget.style.color = 'var(--fp-text-3)' }}
              >
                {label}{count != null && <span style={S.tabCount(active)}>{count}</span>}
              </button>
            )
          })}
        </div>

        {tab === 'canvas' && (
          <div data-testid="project-canvas"
            style={{ height: '74vh', background: 'var(--fp-solid)', border: '1px solid var(--fp-border)', borderRadius: 11, overflow: 'hidden', marginTop: 4 }}>
            <ReviewCanvas
              project={entity.id}
              onOpenMaterial={(m: CanvasMaterial) => openTab({ type: 'review_material', id: m.id }, m.title)}
              onOpenReader={() => openTab({ type: 'studio_reader', id: entity.id }, `${proj?.name || entity.id} 阅读`)}
            />
          </div>
        )}
        {tab === 'plans' && (
          <div data-testid="project-plans">
            {plans === null && <div style={S.dim}>加载中…</div>}
            {plans?.length === 0 && <div style={S.dim}>无关联计划</div>}
            {plans && plans.length > 0 && (
              <div style={S.grid}>
                {plans.map((p) => (
                  <ItemCard
                    key={p.id}
                    icon={<FileText size={13} />}
                    badge="计划"
                    title={p.title}
                    meta={<>#{p.id}</>}
                    onOpen={() => openTab({ type: 'plan', id: p.id }, p.title)}
                    openLabel="打开计划"
                    kebab={[{ label: '复制计划 id', icon: <Copy size={15} />, onClick: () => { void copyText(p.id) } }]}
                  />
                ))}
              </div>
            )}
          </div>
        )}
        {tab === 'convos' && (
          <div data-testid="project-convos">
            {convos === null && <div style={S.dim}>加载中…</div>}
            {convos?.length === 0 && <div style={S.dim}>没有归属本项目计划的对话</div>}
            {convos && convos.length > 0 && (
              <div style={S.grid}>
                {convos.map((s) => (
                  <ItemCard
                    key={s.id}
                    icon={<MessageSquare size={13} />}
                    badge="对话"
                    title={s.title}
                    meta={relTime(s.ts)}
                    onOpen={() => openTab({ type: 'cc_session', id: s.id }, s.title)}
                    openLabel="打开对话"
                    kebab={[{ label: '复制会话 id', icon: <Copy size={15} />, onClick: () => { void copyText(s.id) } }]}
                  />
                ))}
              </div>
            )}
          </div>
        )}
        {tab === 'teams' && (
          <div data-testid="project-teams">
            {/* 决策树=具象管线的步骤卡横排(项目无所属域时整条隐藏); 下方保留原 team*.py 列表 */}
            <DomainTreeBar
              project={entity.id}
              onOpenMaterial={(id, title) => openTab({ type: 'review_material', id }, title)}
            />
            {hasDomain && projTeams.length > 0 && <div style={{ ...S.secTitle, marginTop: 20 }}>域内 team 管线</div>}
            {teams === null && <div style={S.dim}>加载中…</div>}
            {teams && projTeams.length === 0 && !hasDomain && <div style={S.dim}>本项目 roots 下没有管线 (team)</div>}
            {projTeams.length > 0 && (
              <div style={S.grid}>
                {projTeams.map((t) => {
                  const name = (t.package || '').split('/').filter(Boolean).pop() || t.name || t.id
                  return (
                    <ItemCard
                      key={t.id}
                      icon={<GitBranch size={13} />}
                      badge="管线"
                      title={name}
                      titleAttr={t.package}
                      meta={t.package}
                      onOpen={() => openTab({ type: 'team', id: t.id }, name)}
                      openLabel="打开管线"
                      kebab={[{ label: '复制管线 id', icon: <Copy size={15} />, onClick: () => { void copyText(t.id) } }]}
                    />
                  )
                })}
              </div>
            )}
          </div>
        )}
        {tab === 'skills' && (
          <div data-testid="project-skills">
            <ProjectSkills projectRoots={proj?.roots || []} />
          </div>
        )}
        {tab === 'files' && (
          <div data-testid="project-files">
            <ProjectFileTree
              projectId={entity.id}
              onOpenMaterial={(id, title) => openTab({ type: 'review_material', id }, title)}
            />
          </div>
        )}
        {tab === 'reviews' && (
          <div data-testid="project-reviews">
            {reviews === null && <div style={S.dim}>加载中…</div>}
            {reviews?.length === 0 && <div style={S.dim}>本项目还没有审阅材料</div>}
            {reviews && reviews.length > 0 && (
              <div style={S.grid}>
                {reviews.map((m) => {
                  const title = (m as any).title || m.id
                  const status = (m as any).status || ''
                  return (
                    <ItemCard
                      key={m.id}
                      icon={<ClipboardCheck size={13} />}
                      badge="审阅"
                      title={title}
                      onOpen={() => openTab({ type: 'review_material', id: m.id }, title)}
                      openLabel="打开材料"
                      meta={<>{m.source_plan_id || '—'}{status ? ` · ${status}` : ''}</>}
                      kebab={[{ label: '复制材料 id', icon: <Copy size={15} />, onClick: () => { void copyText(m.id) } }]}
                    />
                  )
                })}
              </div>
            )}
          </div>
        )}
        {tab === 'authored' && (
          <div data-testid="project-authored">
            <NotesForTarget
              kind="project"
              id={entity.id}
              title={proj?.name || entity.id}
              heading="本项目的札记(评论/草稿)"
            />
          </div>
        )}
      </div>
    </div>
  )
}

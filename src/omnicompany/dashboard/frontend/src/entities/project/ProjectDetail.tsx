// 项目页 — bside 式 wiki 阅读器(2026-08-12 用户裁决: 大部分页签内容不需要, 初始=简洁 wiki 界面;
// 管线/报告等退化为材料/条目的一种形式)。
// v1.1(同日二轮): 左栏=钉住的项目首页 + 材料嵌套分类树(placement_path) + 游戏入口(专门分类) + 域入口,
// 各区可滚动而首页始终钉在顶上; 阅读面玻璃化不透网格; docbar=真实路径面包屑; 字号调节件不进本阅读器。
//
// 历史裁决保留: 2026-07-06 低频内容不占版面(quick_actions 不渲染); 决策树标签页删除
// (DEC-2026-07-04-240), 决策树将以具象化管线形态回归。

import React, { useEffect, useMemo, useState } from 'react'
import {
  Archive, BadgeCheck, BookOpen, Boxes, Check, ChevronDown, ChevronRight, ClipboardCheck, Copy,
  ExternalLink, FileText, Folder, FolderOpen, FolderTree, Gamepad2, GitBranch, Home,
  KanbanSquare, MessageSquare, NotebookPen, Play, Sparkles,
} from 'lucide-react'
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
import AiLearningObservatoryPanel from './AiLearningObservatoryPanel'
import { materialWikiApi, type MaterialWikiEntry } from '../../api/materialWikiClient'
import { WorkshopSurface } from '../studio_reader/WorkshopSurface'
import MaterialCoreWikiMarkdown from '../../shared/view/wiki/MaterialCoreWikiMarkdown'
import { loadProjectWikiMaterials } from './loadProjectWikiMaterials'
import ProjectWikiHome from './ProjectWikiHome'
import { SidebarToggleButton } from '../../shell/TabSidecar'
import './projectDetail.css'

type DomainKey = 'canvas' | 'plans' | 'convos' | 'teams' | 'skills' | 'files' | 'reviews' | 'authored' | 'learning'
type Sel = 'home' | 'material-core' | DomainKey | `wiki:${string}`

// 项目→内置网页 demo(web_review 实体目标 id)。进「游戏入口」分类的第一条。
const DEMO_BY_PROJECT: Record<string, string> = { vilo: 'vilo-demo', walker: 'walker-game' }

const DOMAINS: [DomainKey, string, React.ReactNode][] = [
  ['canvas', '材料轨迹', <KanbanSquare size={13} />],
  ['plans', '计划', <FileText size={13} />],
  ['convos', '对话', <MessageSquare size={13} />],
  ['teams', '管线', <GitBranch size={13} />],
  ['skills', '技能', <Sparkles size={13} />],
  ['files', '文件', <FolderTree size={13} />],
  ['reviews', '审阅', <ClipboardCheck size={13} />],
  ['authored', '画布', <NotebookPen size={13} />],
]

interface GameEntry {
  id: string
  label: string
  icon?: string
  url: string
}

function CopyBtn({ text, label = '复制' }: { text: string; label?: string }) {
  const [state, setState] = useState<'idle' | 'done' | 'fail'>('idle')
  return (
    <button type="button" className="pd-chip-btn" title={text}
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
  return (
    <button type="button" className="pd-chip-btn" data-testid="open-in-vscode" title={`在 VSCode 打开\n${path}`}
      onClick={(e) => {
        e.stopPropagation()
        openInVscode(path)
      }}>
      <ExternalLink size={11} />{label}
    </button>
  )
}

/** 材料分类树节点: placement_path 中间层是组, 叶子是材料。 */
const HAS_CJK = /[一-鿿]/

// 「未裁决」置顶虚拟组的保留 key: 双下划线前缀防与真实 taxonomy 分类 id 冲突。
export const UNREVIEWED_WIKI_GROUP_KEY = '__unreviewed'
const UNREVIEWED_WIKI_GROUP_LABEL = '未裁决'
// 画布四态里这两档算「未裁决」: 未看(unreviewed)与已看未裁决(seen)。
const PENDING_REVIEW_STATES = new Set(['unreviewed', 'seen'])

interface WikiNode {
  key: string
  label: string
  children: WikiNode[]
  entry?: MaterialWikiEntry
}

function buildWikiTree(materials: MaterialWikiEntry[], labelOf: (id: string) => string): WikiNode[] {
  const roots: WikiNode[] = []
  const groups = new Map<string, WikiNode>()
  // 未裁决条目从原分类移进置顶虚拟组(原分类不再重复显示); 组置顶由末尾 unshift 保证。
  const pendingGroup: WikiNode = { key: UNREVIEWED_WIKI_GROUP_KEY, label: UNREVIEWED_WIKI_GROUP_LABEL, children: [] }
  for (const entry of materials) {
    const slug = entry.material_id.split('.').pop() || ''
    let path = (entry.placement_path || []).filter(Boolean)
    // 路径末段就是材料自己的 slug 时摘掉(它不是分组, 是叶子本身)
    if (path.length && path[path.length - 1] === slug) path = path.slice(0, -1)
    const leaf: WikiNode = { key: entry.material_id, label: entry.title, children: [], entry }
    if (entry.review_state && PENDING_REVIEW_STATES.has(entry.review_state)) {
      pendingGroup.children.push(leaf)
      continue
    }
    let siblings = roots
    let prefix = ''
    for (const seg of path) {
      prefix = prefix ? `${prefix}/${seg}` : seg
      let group = groups.get(prefix)
      if (!group) {
        group = { key: prefix, label: labelOf(seg), children: [] }
        groups.set(prefix, group)
        siblings.push(group)
      }
      siblings = group.children
    }
    siblings.push(leaf)
  }
  if (pendingGroup.children.length) roots.unshift(pendingGroup)
  return roots
}

function materialIcon(entry: MaterialWikiEntry): React.ReactNode {
  if (entry.terminal) return <Archive size={13} />
  if (entry.approved) return <BadgeCheck size={13} />
  return <FileText size={13} />
}

/**
 * 单个 wiki 条目阅读面 — 直接渲染条目 markdown 正文。
 * 2026-08-21 用户死命令: wiki 里绝对不许再套一层 wiki 应用(原先这里 iframe 整个
 * /bside/materials, 自带树和材料头)。正文里的 material embed 仍走
 * MaterialCoreWikiMarkdown 的富嵌件; 整份材料 wiki 应用只保留在顶层 material-core 入口。
 *
 * 2026-08-21 二轮: ```html 范例块必须渲染成本体页面, 不是源码 —— 用户只看样例/
 * 本体(变体)/实例长什么样, 大段文字是给 agent 看的。fenced html 拆出来走沙箱 iframe,
 * 其余仍走 markdown 渲染。
 */
type WikiPart = { kind: 'md' | 'html'; text: string }

function splitHtmlDemoBlocks(body: string): WikiPart[] {
  const parts: WikiPart[] = []
  const fence = /```html\s*\n([\s\S]*?)```/g
  let last = 0
  for (let m = fence.exec(body); m; m = fence.exec(body)) {
    if (m.index > last) parts.push({ kind: 'md', text: body.slice(last, m.index) })
    parts.push({ kind: 'html', text: m[1] })
    last = m.index + m[0].length
  }
  if (last < body.length) parts.push({ kind: 'md', text: body.slice(last) })
  return parts.filter((p) => p.kind === 'html' || p.text.trim())
}

/** 取范例里第一个 <svg> 的宽高定 iframe 纵横比(配合注入样式 SVG 随栏宽缩放)。 */
function svgAspect(code: string): number | null {
  const tag = code.match(/<svg\b[^>]*>/i)?.[0]
  if (!tag) return null
  const w = Number(tag.match(/\bwidth="([\d.]+)"/i)?.[1])
  const h = Number(tag.match(/\bheight="([\d.]+)"/i)?.[1])
  if (w > 0 && h > 0) return w / h
  const vb = tag.match(/\bviewBox="([\d.\s-]+)"/i)?.[1]?.trim().split(/\s+/).map(Number)
  if (vb && vb.length === 4 && vb[2] > 0 && vb[3] > 0) return vb[2] / vb[3]
  return null
}

// 范例 SVG 是定宽像素(如 960px), Companion 栏更窄; 注入缩放样式 + iframe 纵横比
// 让图随栏宽等比缩放且不出现滚动条。sandbox 不给 allow-scripts: 样板几何是纯静态 SVG。
const DEMO_FIT_STYLE =
  '<style>html,body{margin:0;padding:0}svg{max-width:100%;height:auto;display:block}</style>'

function LiveHtmlDemo({ code, order }: { code: string; order: number }) {
  const aspect = svgAspect(code)
  const srcDoc = /<\/head>/i.test(code)
    ? code.replace(/<\/head>/i, `${DEMO_FIT_STYLE}</head>`)
    : DEMO_FIT_STYLE + code
  return (
    <iframe
      className="pd-live-demo"
      title={`结构化网页范例 ${order}`}
      sandbox=""
      srcDoc={srcDoc}
      style={aspect ? { aspectRatio: String(aspect) } : { height: 420 }}
      data-testid="pd-live-demo"
    />
  )
}

/** web/live 材料的机读标记(review submit --kind web 写入 wiki 本体首行):
 * 命中即把条目渲染成整页 live 网页, 与审阅台同一本体, 不再是指针文档。 */
const LIVE_MATERIAL_RE = /<!--\s*omni-live-material\s+(\{[\s\S]*?\})\s*-->/

function parseLiveMaterial(body: string): { url: string } | null {
  const m = LIVE_MATERIAL_RE.exec(body)
  if (!m) return null
  try {
    const data = JSON.parse(m[1])
    return typeof data?.url === 'string' && data.url ? { url: data.url } : null
  } catch {
    return null
  }
}

function ProjectWikiEntry({ project, materialId }: { project: string; materialId: string }) {
  const [body, setBody] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setBody(null)
    setError(null)
    materialWikiApi.get(project, materialId)
      .then((entry) => {
        if (cancelled) return
        const versions = entry.versions || []
        const version = versions.find((v) => v.version === entry.current_version)
          || versions.find((v) => v.version === entry.head_version)
        setBody(version?.body ?? '')
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => { cancelled = true }
  }, [project, materialId])

  const parts = useMemo(() => (body === null ? [] : splitHtmlDemoBlocks(body)), [body])
  const live = useMemo(() => (body === null ? null : parseLiveMaterial(body)), [body])

  if (error) return <div className="pd-dim-center" data-testid="pd-wiki-entry-error">条目加载失败: {error}</div>
  if (body === null) return <div className="pd-dim-center">加载中…</div>
  if (live) {
    return (
      <div className="pd-wiki-entry pd-wiki-entry--live" data-testid="pd-wiki-entry-live">
        <iframe
          className="pd-live-material"
          title="live 材料本体"
          src={live.url}
          allow="autoplay; fullscreen"
        />
      </div>
    )
  }
  let demoOrder = 0
  return (
    <div className="pd-wiki-entry" data-testid="pd-wiki-entry">
      {parts.map((part, i) => (part.kind === 'html'
        ? <LiveHtmlDemo key={i} code={part.text} order={(demoOrder += 1)} />
        : <MaterialCoreWikiMarkdown key={i} source={part.text} project={project} />))}
    </div>
  )
}

export default function ProjectDetail({
  entity,
  facet,
  wikiOnly = false,
}: {
  entity: { id: string }
  facet?: string
  /** Companion 概览复用同一项目 Wiki，只隐藏项目页的非 Wiki 域入口。 */
  wikiOnly?: boolean
}) {
  const [proj, setProj] = useState<ProjectItem | null>(null)
  const [plans, setPlans] = useState<{ id: string; title: string; date?: string; archived?: boolean }[] | null>(null)
  const [planIds, setPlanIds] = useState<Set<string> | null>(null)
  const [convos, setConvos] = useState<{ id: string; title: string; ts?: string | null }[] | null>(null)
  const [reviews, setReviews] = useState<Material[] | null>(null)
  const [teams, setTeams] = useState<any[] | null>(null)  // 管线(team*.py), 按项目 roots 归属
  const [hasDomain, setHasDomain] = useState(false)       // 项目是否属注册域(决策树管线栏/开工入口据此出现)
  const [binding, setBinding] = useState<BossSightBindingBucket | undefined>(undefined)
  const [sel, setSel] = useState<Sel>((facet as Sel) || (entity.id === 'bside' ? 'material-core' : 'home'))
  const [indexContent, setIndexContent] = useState<string | null>(null)
  const [wikiMaterials, setWikiMaterials] = useState<MaterialWikiEntry[] | null>(null)
  const [wikiLabels, setWikiLabels] = useState<Record<string, string>>({})
  const [wikiFilter, setWikiFilter] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [railOpen, setRailOpen] = useState(true)
  const [gameEntries, setGameEntries] = useState<GameEntry[]>([])
  const openTab = usePanels((s) => s.openTab)

  useEffect(() => {
    projectsApi.list().then((b) => setProj(b.projects.find((p) => p.id === entity.id) || null)).catch(() => setProj(null))
    bossSightApi.activeBindings().then((d) => setBinding(d.by_project?.[entity.id])).catch(() => setBinding(undefined))
    projectsApi.plans(entity.id).then((d) => {
      setPlans(d.items.map((p) => ({
        id: p.id,
        title: `${p.date ? p.date + ' ' : ''}${p.title_zh || p.topic}${p.archived ? ' (已归档)' : ''}`,
        date: p.date,
        archived: p.archived,
      })))
      setPlanIds(new Set(d.plan_ids))
    }).catch(() => { setPlans([]); setPlanIds(new Set()) })
    fetch('/api/teams').then((r) => (r.ok ? r.json() : { items: [] })).then((d) => setTeams((d.items as any[]) || [])).catch(() => setTeams([]))
    reviewstageApi.domainTree(entity.id).then((d) => setHasDomain(d.domains.length > 0)).catch(() => setHasDomain(false))
    // 项目首页正文(PROJECT_INDEX.md, 剥 frontmatter); 材料清单走下面独立 effect(可 refetch)
    projectsApi.index(entity.id)
      .then((d) => setIndexContent(d.ok && d.content ? d.content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "") : null))
      .catch(() => setIndexContent(null))
    materialWikiApi.profile(entity.id)
      .then((p) => {
        const map: Record<string, string> = {}
        const walk = (nodes: { id: string; label: string; children?: any[] }[]) => {
          for (const n of nodes) {
            map[n.id] = n.label
            if (n.children) walk(n.children)
          }
        }
        walk(p.taxonomy || [])
        setWikiLabels(map)
      })
      .catch(() => setWikiLabels({}))
    // 游戏入口: 应用视图启动器(config/project_views.yaml)里属于本项目的条目
    projectsApi.views()
      .then((d) => setGameEntries((d.apps || []).filter((a: any) => String(a.id).startsWith(`${entity.id}-`)) as GameEntry[]))
      .catch(() => setGameEntries([]))
  }, [entity.id])

  useEffect(() => {
    setCollapsed(new Set())
    setWikiFilter('')
    setRailOpen(true)
  }, [entity.id])

  // 材料清单: 挂载时加载 + focus/可见性 refetch。
  // 取舍说明: 「未裁决」分组依赖 review_state, 而裁决状态存在画布卡上而非 wiki 目录
  // 文件, SSE(/events)只在 wiki 文件签名变化时触发 —— 用户在审阅台裁决后本概览
  // 不会收到 changed, 不自动刷新。这里不做轮询, 窗口重新获得焦点或从隐藏切回可见时
  // refetch 一次: 用户在审阅台裁决后切回项目页即看到分组更新。
  useEffect(() => {
    let cancelled = false
    const reload = () => {
      loadProjectWikiMaterials(entity.id)
        .then((list) => { if (!cancelled) setWikiMaterials(list) })
        .catch(() => { if (!cancelled) setWikiMaterials(null) })  // 无 profile 的项目: 材料区不显示
    }
    reload()
    const onFocus = () => reload()
    const onVisible = () => { if (document.visibilityState === 'visible') reload() }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      cancelled = true
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisible)
    }
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


  const projTeams = useMemo(() => {
    if (!teams || !proj) return [] as any[]
    const norm = (p?: string) => (p || '').replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '')
    const roots = (proj.roots || []).map(norm).filter(Boolean)
    return teams.filter((t) => {
      const f = norm(t.file_path)
      return !!f && roots.some((r) => f === r || f.startsWith(r + '/'))
    })
  }, [teams, proj])

  const filteredWiki = useMemo(() => {
    if (!wikiMaterials) return null
    const q = wikiFilter.trim().toLowerCase()
    if (!q) return wikiMaterials
    return wikiMaterials.filter((m) => `${m.title} ${m.preview ?? ''}`.toLowerCase().includes(q))
  }, [wikiMaterials, wikiFilter])

  const wikiTree = useMemo(
    () => (filteredWiki ? buildWikiTree(filteredWiki, (id) => wikiLabels[id] || id) : null),
    [filteredWiki, wikiLabels],
  )

  // 分组默认全折叠(用户裁决 2026-08-12): 树一变就整组收起
  const domainCount = (key: DomainKey): number | undefined => {
    if (key === 'plans') return plans ? plans.length : undefined
    if (key === 'convos') return convos ? convos.length : undefined
    if (key === 'teams') return teams ? projTeams.length : undefined
    if (key === 'reviews') return reviews ? reviews.length : undefined
    return undefined
  }

  const toggleGroup = (key: string) => {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const renderWikiNodes = (nodes: WikiNode[], depth: number): React.ReactNode => nodes.map((node) => {
    if (node.entry) {
      const entrySel = `wiki:${node.entry.material_id}` as Sel
      const cjk = HAS_CJK.test(node.label)
      return (
        <button key={node.key} type="button"
          className={`pd-entry${sel === entrySel ? ' active' : ''}${cjk ? '' : ' pd-entry-code'}`}
          style={{ paddingLeft: 8 + depth * 14 }}
          title={`${node.entry.material_id}${node.entry.preview ? `\n${node.entry.preview.slice(0, 80)}` : ''}`}
          data-testid={`pd-wiki-entry`}
          onClick={() => setSel(entrySel)}>
          {materialIcon(node.entry)}<span>{node.label}</span>
        </button>
      )
    }
    const folded = collapsed.has(node.key)
    return (
      <React.Fragment key={node.key}>
        <button type="button" className="pd-entry pd-group"
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={() => toggleGroup(node.key)}>
          {folded ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          {folded ? <Folder size={13} /> : <FolderOpen size={13} />}
          <span>{node.label}</span>
        </button>
        {!folded && renderWikiNodes(node.children, depth + 1)}
      </React.Fragment>
    )
  })

  // docbar 路径面包屑: 项目 / 分区 / …路径 / 条目
  const crumbs: string[] = [proj?.name || entity.id]
  if (sel === 'home') crumbs.push('项目首页')
  else if (sel.startsWith('wiki:')) {
    const wikiId = sel.slice(5)
    const listed = wikiMaterials?.find((item) => item.material_id === wikiId)
    crumbs.push('材料', ...(listed?.placement_path || []), listed?.title || wikiId)
  }
  else if (sel === 'material-core') crumbs.push('共享 Material View')
  else crumbs.push(DOMAINS.find(([k]) => k === sel)?.[1] || sel)

  const demoTarget = DEMO_BY_PROJECT[entity.id]

  return (
    <div
      className="pd-root"
      data-testid="project-detail"
      data-wiki-only={wikiOnly ? '1' : undefined}
      data-rail-collapsed={railOpen ? '0' : '1'}
    >
      {railOpen && <aside className="pd-rail" data-testid="pd-rail">
        {/* 首页钉住不滚 */}
        <div className="pd-rail-pin">
          <SidebarToggleButton
            side="left"
            open={railOpen}
            onToggle={() => setRailOpen(false)}
            label="项目 Wiki 导航"
            testId="pd-toggle-rail-mobile"
            className="pd-rail-toggle"
          />
          <button type="button" className={`pd-entry${sel === 'home' ? ' active' : ''}`} data-testid="pd-entry-home"
            onClick={() => setSel('home')}>
            <Home size={13} /><span>项目首页</span>
          </button>
          {entity.id === 'bside' && (
            <button type="button" className={`pd-entry${sel === 'material-core' ? ' active' : ''}`}
              data-testid="pd-entry-material-core" onClick={() => setSel('material-core')}>
              <Boxes size={13} /><span>共享 Material View</span>
            </button>
          )}
        </div>

        <div className="pd-rail-scroll">
          {wikiTree && wikiTree.length > 0 && (
            <div className="pd-rail-sec" data-testid="pd-wiki-sec">
              <div className="pd-rail-title">材料</div>
              <input
                className="pd-filter"
                placeholder="过滤…"
                value={wikiFilter}
                onChange={(e) => setWikiFilter(e.target.value)}
              />
              {renderWikiNodes(wikiTree, 0)}
            </div>
          )}

          {!wikiOnly && (demoTarget || gameEntries.length > 0) && (
            <div className="pd-rail-sec" data-testid="pd-game-sec">
              <div className="pd-rail-title">游戏入口</div>
              {demoTarget && (
                <button type="button" className="pd-entry" data-testid="project-open-demo"
                  onClick={() => openTab({ type: 'web_review', id: demoTarget }, `${proj?.name || entity.id} Demo`)}>
                  <Play size={13} /><span>试玩 demo</span>
                </button>
              )}
              {gameEntries.map((g) => (
                <button key={g.id} type="button" className="pd-entry" title={g.url}
                  onClick={() => window.open(g.url, '_blank', 'noopener')}>
                  {g.icon ? <span className="pd-emoji">{g.icon}</span> : <Gamepad2 size={13} />}
                  <span>{g.label}</span>
                </button>
              ))}
            </div>
          )}

          {!wikiOnly && <div className="pd-rail-sec">
            <div className="pd-rail-title">域</div>
            {DOMAINS.map(([key, label, icon]) => {
              const count = domainCount(key)
              return (
                <button key={key} type="button" className={`pd-entry${sel === key ? ' active' : ''}`}
                  data-testid={`pd-entry-${key}`}
                  title={count != null ? `${label} ${count}` : label}
                  onClick={() => setSel(key)}>
                  {icon}<span>{label}</span>
                </button>
              )
            })}
            {entity.id === 'ai-learning-observatory' && (
              <button type="button" className={`pd-entry${sel === 'learning' ? ' active' : ''}`}
                data-testid="pd-entry-learning" onClick={() => setSel('learning')}>
                <Sparkles size={13} /><span>持续学习</span>
              </button>
            )}
          </div>}
        </div>
      </aside>}

      <div className="pd-main">
        <div className="pd-docbar" data-testid="pd-docbar">
          <SidebarToggleButton
            side="left"
            open={railOpen}
            onToggle={() => setRailOpen((open) => !open)}
            label="项目 Wiki 导航"
            testId="pd-toggle-rail"
          />
          {crumbs.map((c, i) => (
            <React.Fragment key={i}>
              {i > 0 && <span className="pd-crumb-sep">/</span>}
              <span className={i === crumbs.length - 1 ? 'pd-crumb-current' : 'pd-crumb'}>{c}</span>
            </React.Fragment>
          ))}
          <span className="pd-docbar-meta">活跃 {relTime(proj?.last_active)}</span>
          {binding && <ActiveConvoBadge active={binding.active} total={binding.total} sessions={binding.sessions} size="md" />}
          <span className="pd-docbar-spacer" />
          {proj?.index_path && (
            <span className="pd-docbar-ops" data-testid="project-detail-index-path">
              index {proj.index_ok === false ? '⚠' : ''}
              <CopyBtn text={proj.index_path} label="复制路径" />
              <OpenBtn path={proj.index_path} />
            </span>
          )}
          {(proj?.links || []).map((l, i) => (
            <a key={i} href={l.url} target="_blank" rel="noreferrer" className="pd-chip-btn pd-chip-link">
              <ExternalLink size={11} />{l.label}
            </a>
          ))}
        </div>

        {proj?.index_ok === false && <div className="pd-warn">index 文件校验未通过: {proj.index_error}</div>}

        <div className="pd-body">
          {sel.startsWith('wiki:') && (
            <ProjectWikiEntry key={sel} project={entity.id} materialId={sel.slice(5)} />
          )}
          {sel === 'home' && (
            <ProjectWikiHome projectId={entity.id} project={proj} indexContent={indexContent} />
          )}

          {sel === 'material-core' && (
            <div className="pd-material-core" data-testid="pd-material-core">
              <WorkshopSurface kind="materials" project={entity.id} />
            </div>
          )}

          {sel === 'canvas' && (
            <div data-testid="project-canvas"
              style={{ height: '74vh', background: 'var(--fp-solid)', border: '1px solid var(--fp-border)', borderRadius: 3, overflow: 'hidden', marginTop: 4 }}>
              <ReviewCanvas
                project={entity.id}
                onOpenMaterial={(m: CanvasMaterial) => openTab({ type: 'review_material', id: m.id }, m.title)}
                onOpenReader={() => setSel('home')}
              />
            </div>
          )}
          {sel === 'plans' && (
            <div data-testid="project-plans">
              {plans === null && <div className="pd-dim-center">加载中…</div>}
              {plans?.length === 0 && <div className="pd-dim-center">无关联计划</div>}
              {plans && plans.length > 0 && (
                <div className="pd-grid">
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
          {sel === 'convos' && (
            <div data-testid="project-convos">
              {convos === null && <div className="pd-dim-center">加载中…</div>}
              {convos?.length === 0 && <div className="pd-dim-center">没有归属本项目计划的对话</div>}
              {convos && convos.length > 0 && (
                <div className="pd-grid">
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
          {sel === 'teams' && (
            <div data-testid="project-teams">
              {/* 决策树=具象管线的步骤卡横排(项目无所属域时整条隐藏); 下方保留原 team*.py 列表 */}
              <DomainTreeBar
                project={entity.id}
                onOpenMaterial={(id, title) => openTab({ type: 'review_material', id }, title)}
              />
              {hasDomain && projTeams.length > 0 && <div className="pd-sec-title">域内 team 管线</div>}
              {teams === null && <div className="pd-dim-center">加载中…</div>}
              {teams && projTeams.length === 0 && !hasDomain && <div className="pd-dim-center">本项目 roots 下没有管线 (team)</div>}
              {projTeams.length > 0 && (
                <div className="pd-grid">
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
          {sel === 'skills' && (
            <div data-testid="project-skills">
              <ProjectSkills projectRoots={proj?.roots || []} />
            </div>
          )}
          {sel === 'files' && (
            <div data-testid="project-files">
              <ProjectFileTree
                projectId={entity.id}
                onOpenMaterial={(id, title) => openTab({ type: 'review_material', id }, title)}
              />
            </div>
          )}
          {sel === 'reviews' && (
            <div data-testid="project-reviews">
              {reviews === null && <div className="pd-dim-center">加载中…</div>}
              {reviews?.length === 0 && <div className="pd-dim-center">本项目还没有审阅材料</div>}
              {reviews && reviews.length > 0 && (
                <div className="pd-grid">
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
          {sel === 'authored' && (
            <div data-testid="project-authored" style={{ height: '74vh' }}>
              <WorkshopSurface kind="canvas" sessionId={entity.id} />
            </div>
          )}
          {sel === 'learning' && entity.id === 'ai-learning-observatory' && (
            <AiLearningObservatoryPanel />
          )}
        </div>
      </div>
    </div>
  )
}

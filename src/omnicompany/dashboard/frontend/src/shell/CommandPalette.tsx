import React, { useEffect, useState } from 'react'
import {
  KBarProvider, KBarPortal, KBarPositioner, KBarAnimator, KBarSearch,
  useMatches, KBarResults, useRegisterActions, useKBar, VisualState, type Action,
} from 'kbar'
import { registry } from '../entities/registry'
import { usePanels } from '../stores/panelsStore'
import { bossSightApi } from '../api/bossSightClient'
import { searchOverlayFiles } from '../api/overlayClient'
import type { Entity, EntityType } from '../entities/types'
import {
  openOverlayFileInDashboard,
  type OverlayFileOpenTarget,
} from '../lib/overlayFileNavigation'
import WebFileContextMenu, {
  type WebFileMenuState,
} from '../shared/view/ui/WebFileContextMenu'
import { installDoubleCtrlShortcut } from './doubleCtrlShortcut'

// ⌘K 命令面板(壳层 A 收编全局搜索于此;2026-07-19 蓝图 G 化:深色描图纸 + 虚线边 + mono 栈)。
// 开合路径:①kbar 自带 ⌘K/Ctrl+K;②薄条 ⌘K 提示钮 → window 事件 'omni:toggle-command-palette'
// (PaletteToggleBridge 收听;面板懒加载,钮的 hover 会预热 chunk)。
const POSITIONER: React.CSSProperties = {
  position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
  display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
  paddingTop: 80, background: 'rgba(4,12,28,.5)', zIndex: 9999,
}
const ANIMATOR: React.CSSProperties = {
  width: 600, maxWidth: '90vw',
  background: 'var(--fp-glass-2)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
  border: '1px dashed var(--fp-border-strong)', borderRadius: 3, overflow: 'hidden',
  boxShadow: 'var(--fp-shadow-pop)', fontFamily: 'var(--fp-font-mono)',
}
const SEARCH: React.CSSProperties = {
  flex: 1, minWidth: 0, padding: '12px 16px', background: 'transparent',
  border: 'none', outline: 'none', color: 'var(--fp-text)', fontSize: 15,
  fontFamily: 'var(--fp-font-sans)',
}
const SEARCH_BAR: React.CSSProperties = {
  display: 'flex', alignItems: 'center',
  borderBottom: '1px solid var(--fp-border)',
}
const SCOPE_SELECT: React.CSSProperties = {
  marginRight: 10, padding: '5px 24px 5px 8px',
  color: 'var(--fp-text-2)', background: 'var(--fp-bg-2)',
  border: '1px solid var(--fp-border-strong)', borderRadius: 2,
  outline: 'none', fontSize: 12, fontFamily: 'var(--fp-font-mono)',
}

type SearchScope = 'all' | 'omni' | 'files'

function ResultRow({
  active,
  item,
  onFileContextMenu,
}: {
  active: boolean
  item: any
  onFileContextMenu: (event: React.MouseEvent, target: OverlayFileOpenTarget) => void
}) {
  const target = item.overlayFileTarget as OverlayFileOpenTarget | undefined
  return (
    <div
      data-testid={target ? 'overlay-file-search-result' : undefined}
      onContextMenu={target ? (event) => onFileContextMenu(event, target) : undefined}
      style={{
        padding: '8px 16px', cursor: 'pointer', display: 'flex', gap: 8, alignItems: 'baseline',
        background: active ? 'var(--fp-bp-hatch)' : 'transparent',
        boxShadow: active ? 'inset 0 0 0 1px var(--fp-border-strong)' : 'none',
        color: active ? 'var(--fp-text)' : 'var(--fp-text-2)', fontSize: 14,
      }}
    >
      <span style={{ color: 'var(--fp-bp-brass-hi)', width: 64, flexShrink: 0, fontSize: 12, fontFamily: 'var(--fp-font-mono)', textTransform: 'uppercase', letterSpacing: '.08em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.section || ''}</span>
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</span>
      <span
        title={item.subtitle || ''}
        style={{
          color: 'var(--fp-text-3)', fontSize: 12, fontFamily: 'var(--fp-font-mono)',
          maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}
      >
        {item.subtitle || ''}
      </span>
    </div>
  )
}

function ResultsRender({
  onFileContextMenu,
}: {
  onFileContextMenu: (event: React.MouseEvent, target: OverlayFileOpenTarget) => void
}) {
  const { results } = useMatches()
  return (
    <KBarResults
      items={results}
      onRender={({ item, active }) =>
        typeof item === 'string' ? (
          <div style={{ padding: '4px 16px', color: 'var(--fp-text-3)', fontSize: 12, fontFamily: 'var(--fp-font-mono)', textTransform: 'uppercase', letterSpacing: '.1em' }}>{item}</div>
        ) : (
          <ResultRow active={active} item={item} onFileContextMenu={onFileContextMenu} />
        )
      }
    />
  )
}

// 薄条 ⌘K 钮 → window 事件桥(面板自包含 KBarProvider,壳层拿不到它的 context,走事件最干净)。
function PaletteToggleBridge() {
  const { query } = useKBar()
  useEffect(() => {
    const on = () => query.toggle()
    const onMessage = (event: MessageEvent) => {
      if (event.origin === window.location.origin && event.data?.type === 'omni:double-control') on()
    }
    const disposeDoubleCtrl = installDoubleCtrlShortcut(window, on)
    window.addEventListener('omni:toggle-command-palette', on)
    window.addEventListener('message', onMessage)
    return () => {
      disposeDoubleCtrl()
      window.removeEventListener('omni:toggle-command-palette', on)
      window.removeEventListener('message', onMessage)
    }
  }, [query])
  return null
}

// 静态动作:原顶栏全局搜索的特例入口(KB 关系图谱 / 可达性审计),保持 ⌘K 迁移后可达性不丢。
const STATIC_ACTIONS: Action[] = [
  {
    id: 'open:graph:main',
    name: 'KB 关系图谱',
    subtitle: 'graph:main',
    section: '知识库',
    keywords: '图 graph 关系 kb 链接 link 知识库',
    perform: () => usePanels.getState().openTab({ type: 'graph', id: 'main' }, 'KB 关系图谱'),
  },
  {
    id: 'open:nav_audit:main',
    name: '可达性审计',
    subtitle: 'nav_audit:main',
    section: '导航',
    keywords: '可达性 审计 导航 孤岛 入口 audit',
    perform: () => usePanels.getState().openTab({ type: 'nav_audit', id: 'main' }, '可达性审计'),
  },
]

function DynamicActions({ enabled }: { enabled: boolean }) {
  const openTab = usePanels((s) => s.openTab)
  const [actions, setActions] = useState<Action[]>([])

  useEffect(() => {
    let dead = false
    const types = registry.types() as EntityType[]
    Promise.all(
      types.map(async (t) => {
        const reg = registry.get(t)
        if (!reg) return [] as Entity[]
        try { return await reg.resolver.list() } catch { return [] }
      }),
    ).then(async (groups) => {
      if (dead) return
      const acts: Action[] = []
      groups.flat().forEach((e) => {
        const reg = registry.get(e.type)
        acts.push({
          id: `open:${e.type}:${e.id}`,
          name: e.title,
          subtitle: e.id,
          section: reg?.label || e.type,
          keywords: `${e.id} ${(e.tags || []).join(' ')}`,
          perform: () => openTab(e, e.title),
        })
      })
      // 原顶栏全局搜索的材料登记覆盖(审阅材料/计划/笔记/guard 等单条材料)并入面板:
      // resolver.list() 只给实体入口,材料条目走登记端点补一批(最近 250 条,客户端模糊)。
      try {
        const mr = await bossSightApi.getMaterialRegistry({ limit: 250 })
        if (dead) return
        for (const item of mr.items || []) {
          const ref = item.open_ref
          if (!ref?.type || !ref?.id) continue
          acts.push({
            id: `open:mat:${item.uri || item.id}`,
            name: item.title || item.id,
            subtitle: `${item.kind}${item.role ? ` / ${item.role}` : ''}`,
            section: '材料',
            keywords: `${item.id} ${(item.tags || []).join(' ')}`,
            perform: () => openTab({ type: ref.type as EntityType, id: String(ref.id) }, item.title || item.id, ref.facet),
          })
        }
      } catch { /* 登记端点失败不挡实体动作 */ }
      setActions(acts)
    })
    return () => { dead = true }
  }, [openTab])

  return <ActionLoader actions={enabled ? actions : []} />
}

function ActionLoader({ actions }: { actions: Action[] }) {
  useRegisterActions(actions, [actions])
  return null
}

const FILE_KIND_LABEL: Record<string, string> = {
  app: '应用',
  exe: '程序',
  folder: '文件夹',
  file: '文件',
}

function FileActions({ enabled, query: rawQuery }: { enabled: boolean; query: string }) {
  const [actions, setActions] = useState<Action[]>([])

  useEffect(() => {
    const query = rawQuery.trim()
    if (!enabled || !query) {
      setActions([])
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void searchOverlayFiles(query, 40, controller.signal).then((hits) => {
        if (controller.signal.aborted) return
        setActions(hits.map((hit, index) => ({
          id: `overlay-file:${hit.path}`,
          name: hit.name,
          subtitle: hit.path,
          section: FILE_KIND_LABEL[hit.kind] || '本机文件',
          overlayFileTarget: hit,
          // kbar 会对后端结果再过滤一次。把原查询并入 keywords，保留拼音/首字母等
          // 由 Overlay Shell 命中的结果，同时仍允许路径和标签参与前端过滤。
          keywords: `${query} ${hit.path} ${(hit.tags || []).join(' ')}`,
          priority: Math.max(1, 1000 - index),
          perform: () => openOverlayFileInDashboard(hit),
        })))
      }).catch((error) => {
        if (controller.signal.aborted) return
        setActions([{
          id: `overlay-file-error:${query}`,
          name: 'Overlay Shell 文件索引未连接',
          subtitle: error instanceof Error ? error.message : String(error),
          section: '本机文件',
          keywords: query,
        }])
      })
    }, 110)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [enabled, rawQuery])

  return <ActionLoader actions={enabled ? actions : []} />
}

// 启动洪峰治理(2026-07 首屏拆包): DynamicActions 一挂载就对 ~24 个注册类型全发 list(~10 个
// 端点), 是启动洪峰最大的单一请求源。推迟到两个触发点先到先触发: ①面板首次打开(kbar
// visualState 变 showing)②浏览器空闲(requestIdleCallback; 不支持的浏览器 8s 后兜底)——
// 空闲预热让首次打开基本"开箱即有数据", 又不再和首屏关键请求抢同源连接。
function DynamicActionsGate({ enabled }: { enabled: boolean }) {
  const { visualState } = useKBar((s) => ({ visualState: s.visualState }))
  const [load, setLoad] = useState(false)
  useEffect(() => {
    if (visualState === VisualState.showing) setLoad(true)
  }, [visualState])
  useEffect(() => {
    if (load) return
    if (typeof window.requestIdleCallback === 'function') {
      const id = window.requestIdleCallback(() => setLoad(true), { timeout: 8000 })
      return () => window.cancelIdleCallback(id)
    }
    const t = window.setTimeout(() => setLoad(true), 8000)
    return () => window.clearTimeout(t)
  }, [load])
  return load ? <DynamicActions enabled={enabled} /> : null
}

function CommandPaletteBody() {
  const { query: kbarQuery } = useKBar()
  const [scope, setScope] = useState<SearchScope>('all')
  const [query, setQuery] = useState('')
  const [fileMenu, setFileMenu] = useState<WebFileMenuState | null>(null)
  const includeOmni = scope !== 'files'
  const includeFiles = scope !== 'omni'
  return (
    <>
      <PaletteToggleBridge />
      <ActionLoader actions={includeOmni ? STATIC_ACTIONS : []} />
      <DynamicActionsGate enabled={includeOmni} />
      <FileActions enabled={includeFiles} query={query} />
      <KBarPortal>
        <KBarPositioner style={POSITIONER}>
          <KBarAnimator style={ANIMATOR}>
            <div style={SEARCH_BAR}>
              <KBarSearch
                style={SEARCH}
                defaultPlaceholder="搜索 / 命令: 实体、材料或本机文件…"
                onChange={(event) => setQuery(event.target.value)}
              />
              <select
                aria-label="搜索范围"
                title="搜索范围"
                value={scope}
                style={SCOPE_SELECT}
                onChange={(event) => setScope(event.target.value as SearchScope)}
                onKeyDown={(event) => event.stopPropagation()}
              >
                <option value="all">全部</option>
                <option value="omni">Omni 实体</option>
                <option value="files">本机文件</option>
              </select>
            </div>
            <ResultsRender
              onFileContextMenu={(event, target) => {
                event.preventDefault()
                event.stopPropagation()
                setFileMenu({ x: event.clientX, y: event.clientY, target })
              }}
            />
          </KBarAnimator>
        </KBarPositioner>
      </KBarPortal>
      <WebFileContextMenu
        menu={fileMenu}
        onClose={() => setFileMenu(null)}
        onOpen={(target) => {
          openOverlayFileInDashboard(target)
          // Portal 菜单的 mousedown 会先触发 KBar 的 outer-click 关闭逻辑。此处若 toggle，
          // 会把 animatingOut 反转成 animatingIn，导致文件已打开但搜索层残留；显式 hidden
          // 才能覆盖两种事件顺序。
          kbarQuery.setVisualState(VisualState.hidden)
        }}
      />
    </>
  )
}

// 自包含命令面板(2026-07 首屏拆包): 不再以 Provider 包住整棵树 —— 全工程无其他 kbar context
// 消费方, KBarProvider 只服务面板自身(快捷键注册/portal/动作注册都在内部)。App 用
// React.lazy + Suspense 把它作为兄弟节点后挂, kbar chunk 退出首屏静态图。
export function CommandPalette() {
  return (
    <KBarProvider actions={[]} options={{ enableHistory: false }}>
      <CommandPaletteBody />
    </KBarProvider>
  )
}

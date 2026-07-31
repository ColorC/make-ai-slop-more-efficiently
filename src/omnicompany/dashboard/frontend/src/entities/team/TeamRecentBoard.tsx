// 「管线」看板 — 管线 == team(TeamSpec 是 2026-04-21 从 PipelineSpec 改名来的同一模型)。
// 用户 2026-06-19: "我怎么知道一个 project 属下的管线有哪些, 在哪里？" 项目↔管线唯一的链接是
// 项目 root(包路径), 此前没任何地方展示归属。这里按项目分组: 每个 project 一组, 列它 root 路径下的
// 管线(team*.py), 显示包路径/状态/最近修改/复制源码路径; 点卡片打开既有 team 拓扑视图(结构图/源码/设计)。
// 数据: /api/teams(catalogue.py) + /api/projects(projects_registry, 取 roots 做归属匹配)。
//
// 呈现层 (重做思维, 2026-06-30 按 frostpane REBUILD-STANDARD):
//   ① 无标题头 —— 撤掉重复页签名的「管线·按项目」标题栏/副标(页签已标识身份), 内容从顶部直接开始,
//      只留一条右对齐的玻璃控件条(搜索框 + ⋯ 收纳刷新), 照 ThreadMonitorPanel。
//   ② root 透明吃 body 全局冷渐变; ③ 卡片磨砂玻璃(var(--fp-glass)+blur+rim 高光+radius11);
//   ④ 信息层级靠字号(分区名/标题 15 / 包路径·时间 12 弱灰等宽); ⑤ 主操作「打开拓扑」显眼,
//      低频「复制 id / 复制源码路径」收进共享 KebabMenu ⋯; ⑥ 项目分区 + auto-fill 卡片网格;
//      ⑦ 颜色全 var(--fp-*) 无裸 hex; ⑧ 保留所有数据接线与 data-testid。
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Copy, RefreshCw, FileCode2, Link2 } from 'lucide-react'
import { usePanels } from '../../stores/panelsStore'
import { useRefreshBus } from '../../stores/refreshBus'
import { openProps } from '../../utils/middleClick'
import { copyText } from '../../lib/copyText'
import { relTimeEn } from '../../lib/time'
import { ProjectIcon } from '../../lib/projectIcon'
import { projectsApi, type ProjectItem } from '../../api/projectsClient'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'

interface TeamItem {
  id: string
  name: string
  package: string
  file_path?: string
  size?: number
  has_design_md?: boolean
  registered_via?: string
  mtime?: number
}

const UNATTR = '__unattributed__'

function teamLabel(pkg: string): string {
  const parts = (pkg || '').split('/').filter(Boolean)
  return parts[parts.length - 1] || pkg || '?'
}
function isRegistered(via?: string): boolean {
  return !!via && via !== 'file_glob_only'
}
function norm(p?: string): string {
  return (p || '').replace(/\\/g, '/').toLowerCase().replace(/\/+$/, '')
}
/** 把一条管线按 file_path 归到 root 最长前缀匹配的项目; 没匹配返回 null。 */
function attributeProject(filePath: string | undefined, projects: ProjectItem[]): ProjectItem | null {
  const f = norm(filePath)
  if (!f) return null
  let best: ProjectItem | null = null
  let bestLen = 0
  for (const p of projects) {
    for (const r of p.roots || []) {
      const rn = norm(r)
      if (rn && (f === rn || f.startsWith(rn + '/')) && rn.length > bestLen) {
        best = p
        bestLen = rn.length
      }
    }
  }
  return best
}

const GLASS_BG = 'var(--fp-glass)'
const GLASS_BLUR = 'var(--fp-blur)'
const HOVER_BORDER = 'var(--fp-border-strong)'

const S: Record<string, any> = {
  // ② root 透明, 吃 body 全局冷渐变, 不铺实底。
  root: { height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'transparent', color: 'var(--fp-text)' },
  // ① 无标题头: 仅留右对齐玻璃控件条(搜索 + ⋯)。内容紧随其下从顶部开始。
  bar: { flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px' },
  search: { flex: 1, height: 32, border: '1px solid var(--fp-border)', background: 'var(--fp-card)', color: 'var(--fp-text)', borderRadius: 7, padding: '0 12px', fontSize: 13, minWidth: 0, outline: 'none' },
  iconBtn: { width: 32, height: 32, border: '1px solid var(--fp-border)', borderRadius: 7, background: 'var(--fp-glass)', backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR, color: 'var(--fp-text-2)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0, transition: 'all 150ms cubic-bezier(0.175,0.885,0.32,1.1)' },
  // 大滚动区: 放宽呼吸 (4px 栅格)。
  scroll: { flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 16px 28px', display: 'flex', flexDirection: 'column', gap: 24 },
  // 项目分区
  group: { display: 'flex', flexDirection: 'column', gap: 12 },
  groupHead: { display: 'flex', alignItems: 'center', gap: 10, paddingBottom: 2 },
  // ④ 分区名 15 建层级
  groupName: { color: 'var(--fp-text)', fontSize: 15, fontWeight: 650, letterSpacing: '-0.01em' },
  // 计数 = 胶囊 chip, 弱化
  groupCount: { color: 'var(--fp-text-2)', fontSize: 12, fontWeight: 600, borderRadius: 999, padding: '1px 10px', background: 'rgba(255,255,255,.06)', border: '1px solid var(--fp-border)' },
  // ⑥ 卡片网格 (auto-fill, minmax) — 照 ThreadMonitorPanel
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 },
  // ③ 磨砂玻璃卡片解剖
  card: { display: 'flex', flexDirection: 'column', minWidth: 0, background: GLASS_BG, backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR, border: '1px solid var(--fp-border)', borderRadius: 11, padding: 14, cursor: 'pointer', boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)', transition: 'border-color 150ms cubic-bezier(0.175,0.885,0.32,1.1), transform 150ms cubic-bezier(0.175,0.885,0.32,1.1)' },
  cardTop: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 },
  // ④ 标题 flex1 醒目 (15/650), 链图标 + 名字
  cardTitle: { flex: 1, display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, color: 'var(--fp-text)', fontSize: 15, fontWeight: 650, letterSpacing: '-0.01em', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  chainIcon: { width: 20, height: 20, borderRadius: 5, flexShrink: 0, background: 'var(--fp-accent-weak)', border: '1px solid var(--fp-border)', color: 'var(--fp-link)', fontSize: 13, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' },
  // 状态徽章
  badge: (on: boolean): React.CSSProperties => ({ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, fontWeight: 600, borderRadius: 4, padding: '1px 7px', whiteSpace: 'nowrap', color: on ? 'var(--fp-ok)' : 'var(--fp-text-3)', background: on ? 'color-mix(in srgb, var(--fp-ok) 12%, transparent)' : 'rgba(255,255,255,.05)', border: `1px solid ${on ? 'color-mix(in srgb, var(--fp-ok) 38%, transparent)' : 'var(--fp-border)'}` }),
  // ④ 包路径 = 弱灰等宽微字 (12)
  pkg: { color: 'var(--fp-text-3)', fontSize: 12, fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace", margin: '10px 0 0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 },
  // ④ 时间 = 弱灰等宽微字
  time: { color: 'var(--fp-text-3)', fontSize: 12, fontFamily: "'Berkeley Mono','Consolas','Menlo',monospace", marginTop: 4 },
  // ⑤ 底部整宽主操作按钮
  foot: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 },
  openBtn: { flex: 1, border: '1px solid var(--fp-accent)', background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', borderRadius: 7, padding: '7px 0', cursor: 'pointer', fontSize: 13, fontWeight: 600, fontFamily: 'inherit', transition: 'filter 150ms cubic-bezier(0.175,0.885,0.32,1.1)' },
  empty: { color: 'var(--fp-text-3)', padding: 32, fontSize: 14, textAlign: 'center' as const },
  err: { color: 'var(--fp-err)', fontSize: 14, padding: '10px 16px' },
}

interface Group { key: string; label: string; projId?: string; teams: TeamItem[] }

export default function TeamRecentBoard() {
  const [items, setItems] = useState<TeamItem[]>([])
  const [projects, setProjects] = useState<ProjectItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const openTab = usePanels((s) => s.openTab)
  const openTabBg = usePanels((s) => s.openTabBackground)
  const refreshNonce = useRefreshBus((s) => s.nonce)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      fetch('/api/teams').then((r) => (r.ok ? r.json() : Promise.reject(new Error(`teams ${r.status}`)))),
      projectsApi.list().catch(() => ({ projects: [] as ProjectItem[] })),
    ])
      .then(([t, b]) => { setItems((t?.items as TeamItem[]) || []); setProjects(((b as any)?.projects as ProjectItem[]) || []); setError(null) })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load, refreshNonce])

  const open = (t: TeamItem, bg = false) =>
    (bg ? openTabBg : openTab)({ type: 'team', id: t.id }, teamLabel(t.package))

  // 按项目分组: 每条管线归到 root 最长前缀匹配的项目, 项目内按最近修改排, 组间按管线数降序。
  const groups = useMemo<Group[]>(() => {
    const s = q.trim().toLowerCase()
    const filtered = s ? items.filter((t) => `${t.id} ${t.package} ${t.name}`.toLowerCase().includes(s)) : items
    const byProj = new Map<string, Group>()
    for (const t of filtered) {
      const p = attributeProject(t.file_path, projects)
      const key = p ? p.id : UNATTR
      if (!byProj.has(key)) byProj.set(key, { key, label: p ? (p.name || p.id) : '未归属到项目', projId: p?.id, teams: [] })
      byProj.get(key)!.teams.push(t)
    }
    const arr = [...byProj.values()]
    for (const g of arr) g.teams.sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
    arr.sort((a, b) => {
      if ((a.key === UNATTR) !== (b.key === UNATTR)) return a.key === UNATTR ? 1 : -1
      return b.teams.length - a.teams.length
    })
    return arr
  }, [items, projects, q])

  const totalShown = groups.reduce((n, g) => n + g.teams.length, 0)

  return (
    <div style={S.root} data-testid="team-recent-board">
      {/* ① 无标题头(Linear 风): 不放重复页签名的面板标题。仅留右对齐控件条: 搜索 + 刷新。 */}
      <div style={S.bar}>
        <input style={S.search} placeholder="搜管线名 / 包路径 / id…" value={q} onChange={(e) => setQ(e.target.value)} data-testid="team-board-search" />
        <button
          type="button"
          style={S.iconBtn}
          title="刷新"
          data-testid="team-board-refresh"
          onClick={() => load()}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = HOVER_BORDER; (e.currentTarget as HTMLButtonElement).style.color = 'var(--fp-text)' }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--fp-border)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--fp-text-2)' }}
        ><RefreshCw size={14} /></button>
      </div>
      {error && <div style={S.err}>加载失败: {error}</div>}
      <div style={S.scroll} data-testid="team-board-scroll">
        {!loading && totalShown === 0 && <div style={S.empty}>{q ? '没有匹配的管线' : '暂无管线'}</div>}
        {groups.map((g) => (
          <div key={g.key} style={S.group} data-testid="team-project-group" data-project={g.projId || 'none'}>
            <div style={S.groupHead}>
              {g.projId ? <ProjectIcon id={g.projId} size={20} /> : <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--fp-bg-overlay)', border: '1px solid var(--fp-border)', display: 'inline-block' }} />}
              <span style={S.groupName}>{g.label}</span>
              <span style={S.groupCount}>{g.teams.length}</span>
            </div>
            <div style={S.grid}>
              {g.teams.map((t) => {
                const registered = isRegistered(t.registered_via)
                const menu: KebabItem[] = [
                  { label: '复制管线 id', icon: <Copy size={14} />, testid: 'team-kebab-copy-id', onClick: () => { void copyText(t.id) } },
                ]
                if (t.file_path) menu.push({ label: '复制源码路径', icon: <FileCode2 size={14} />, testid: 'team-kebab-copy-path', onClick: () => { void copyText(t.file_path!) } })
                return (
                  <div
                    key={t.id}
                    style={S.card}
                    data-testid="team-recent-row"
                    title={`${t.id} · 左键打开 / 中键后台开`}
                    {...openProps(() => open(t), () => open(t, true))}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = HOVER_BORDER; (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-1px)' }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--fp-border)'; (e.currentTarget as HTMLDivElement).style.transform = 'none' }}
                  >
                    <div style={S.cardTop}>
                      <span style={S.badge(registered)}>{registered ? '已注册' : '未进G2'}</span>
                      {t.has_design_md && <span style={S.badge(true)}>设计</span>}
                      <div style={{ flex: 1 }} />
                      <div data-omni-capture-ignore="true">
                        <KebabMenu items={menu} testid="team-recent-kebab" iconSize={14} />
                      </div>
                    </div>
                    <div style={{ ...S.cardTitle, marginTop: 10 }}>
                      <span style={S.chainIcon}>⛓</span>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{teamLabel(t.package)}</span>
                    </div>
                    <div style={S.pkg} title={t.file_path || t.package}>
                      <Link2 size={12} style={{ flexShrink: 0, opacity: 0.7 }} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.package}</span>
                    </div>
                    <div style={S.time}>{t.mtime ? relTimeEn(t.mtime) : '—'}</div>
                    <div style={S.foot}>
                      <button
                        type="button"
                        style={S.openBtn}
                        onClick={(e) => { e.stopPropagation(); open(t) }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.filter = 'brightness(1.08)' }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.filter = 'none' }}
                      >打开拓扑</button>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

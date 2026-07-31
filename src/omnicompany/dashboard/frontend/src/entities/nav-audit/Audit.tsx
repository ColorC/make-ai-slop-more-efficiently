import { useMemo } from 'react'
import { registry } from '../registry'
import { usePanels } from '../../stores/panelsStore'

// 已知"从主页可达"的入口清单(单一真源:新增功能要在这里登记入口,否则审计标它为孤岛)。
// 返回主页:dashboard 任何页签都能点左上 rail 第一个图标(项目)回到项目主页,故 returnable 普遍成立。
const REACHABILITY: Record<string, string> = {
  project_board: 'rail 首页(项目)',
  project: '项目板点项目卡 / 全局搜索项目名',
  authored: 'rail 草稿箱 / 项目详情·札记 / 全局搜索',
  review_queue: 'rail 审阅 / 全局搜索',
  review_material: '全局搜索 / 项目详情·审阅 / 评论铃铛',
  controller: 'rail 总控 / 全局搜索',
  settings: 'rail 设置 / 全局搜索',
  material_graph: '(已摘除注册:裸DAG决策树外观封禁 DEC-2026-07-04-240;新形态=具象化管线待建)',
  web_review: '项目详情·Demo / 全局搜索',
  plan: '项目详情·计划 / 全局搜索',
  cc_session: '项目详情·对话 / 总控',
  team: '项目详情·管线',
  team_board: '全局搜索',
  graph: '全局搜索「图/关系/kb」',
  worker: '全局搜索 / 总控节点',
  material: '全局搜索(材料登记)',
  plan_audit: '计划·三点菜单「跑 audit」',
  nav_audit: '全局搜索「可达性/审计」(本视图)',
}

export default function NavAudit() {
  const openTab = usePanels((s) => s.openTab)
  const rows = useMemo(() => {
    return registry.all()
      .map((reg) => {
        const type = reg.resolver.type
        const via = REACHABILITY[type]
        return { type, label: reg.label || type, via: via || '', island: !via }
      })
      .sort((a, b) => Number(a.island) - Number(b.island) || a.type.localeCompare(b.type))
      .reverse()  // 孤岛排前面(醒目)
  }, [])
  const islands = rows.filter((r) => r.island)

  return (
    <div style={{ height: '100%', overflow: 'auto', background: 'transparent', color: '#adbac7', padding: 16, fontFamily: 'system-ui' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <strong style={{ fontSize: 16, color: 'var(--fp-text)' }}>🧭 可达性审计</strong>
        <span style={{ fontSize: 13, color: 'var(--fp-text-3)' }}>
          共 {rows.length} 个功能 · <span style={{ color: islands.length ? 'var(--fp-err)' : 'var(--fp-ok)' }}>{islands.length} 个疑似孤岛</span>
        </span>
        <button type="button" onClick={() => openTab({ type: 'project_board', id: 'main' }, '项目')}
          style={{ marginLeft: 'auto', fontSize: 12, padding: '4px 12px', cursor: 'pointer', border: '1px solid var(--fp-border)', borderRadius: 6, background: 'var(--fp-accent)', color: '#fff' }}>
          ← 返回项目主页
        </button>
      </div>
      <div style={{ fontSize: 12, color: 'var(--fp-text-3)', marginBottom: 12, lineHeight: 1.6 }}>
        全量列出 dashboard 注册的功能(实体类型),标出每个「从主页怎么到达」。
        <span style={{ color: 'var(--fp-err)' }}>红色 = 孤岛</span>:没登记任何从主页可达的入口,只能靠输网址或他处钻入 —— 需要补一个从主页可达的入口。
        所有功能都能用左上 rail 第一个图标(项目)返回主页。
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: 'left', color: 'var(--fp-text-3)', borderBottom: '1px solid var(--fp-border)' }}>
            <th style={{ padding: '6px 8px' }}>功能</th>
            <th style={{ padding: '6px 8px' }}>类型</th>
            <th style={{ padding: '6px 8px' }}>从主页怎么到达</th>
            <th style={{ padding: '6px 8px' }}>返回主页</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.type} style={{ borderBottom: '1px solid var(--fp-solid)', background: r.island ? '#2a161788' : 'transparent' }}>
              <td style={{ padding: '6px 8px', color: r.island ? 'var(--fp-err)' : 'var(--fp-text)' }}>
                {r.island ? '⚠ ' : ''}{r.label}
              </td>
              <td style={{ padding: '6px 8px', color: 'var(--fp-text-3)', fontFamily: 'Consolas, monospace', fontSize: 12 }}>{r.type}</td>
              <td style={{ padding: '6px 8px', color: r.island ? 'var(--fp-err)' : '#adbac7' }}>
                {r.island ? '未登记入口(疑似孤岛)— 需补一个从主页可达的入口' : r.via}
              </td>
              <td style={{ padding: '6px 8px', color: 'var(--fp-ok)' }}>rail 首页 ✓</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

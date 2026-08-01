/**
 * entities/review/businesses/narrativeToolbar — 叙事业务顶栏工厂(件一 DEC-2026-07-06-082/083)。
 *
 * 与叙事渲染器(narrative.tsx)分文件的用意: 渲染器本体(九视图 + 引擎投影)仍走 React.lazy 拆包,
 * 只在查看叙事材料时下载; 而顶栏工厂需在 registerReviewBusinessRenderers() 启动期就挂到
 * RendererEntry.toolbar(供 MaterialDetail 同步解析), 故把这份"轻量顶栏件"独立出来, 别把
 * 重渲染器 chunk 拽进启动包。
 *
 * 协议: 每个渲染器一个工厂 → 返回 BusinessToolbarSpec(icon/title/sub + 适用裁决 aux + 决策历程 action)。
 * "适用裁决"需异步取域层级 + 自带展开态, 封装为自洽活体组件 NarrativeRulings, 经 spec.aux 挂进合并栏。
 * 决策历程按钮的跳转行为原样保留(经 usePanels.getState().openTab)。
 */
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { Flag, ScrollText, Users, LayoutGrid, Columns3 } from 'lucide-react'
import { reviewstageApi, type Material, type DomainTreeStep } from '../../../api/reviewstageClient'
import { usePanels } from '../../../stores/panelsStore'
import type { BusinessToolbarSpec } from '../rendererRegistry'

const MONO = "'Berkeley Mono','Consolas','Menlo',monospace"

/** 适用裁决 chip + 可展开原话面板(自洽活体件: 异步取域层级映射, 自带展开态)。 */
function NarrativeRulings({ m }: { m: Material }) {
  const [step, setStep] = useState<DomainTreeStep | null>(null)
  const [showRulings, setShowRulings] = useState(false)
  const project = m.project || 'vilo'
  useEffect(() => {
    let alive = true
    reviewstageApi.domainTree(project)
      .then((d) => {
        if (!alive) return
        const steps = d.domains.flatMap((dom) => dom.steps)
        setStep(steps.find((s) => s.name === m.track) ?? null)
      })
      .catch(() => { /* 域层级不可达 → 只少一个裁决 chip, 不影响本体 */ })
    return () => { alive = false }
  }, [project, m.track])

  if (!step || step.adopted_rulings.length === 0) return null
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}>
      <button type="button" style={ST.rulChip} data-testid="narrative-rulings-chip"
        onClick={() => setShowRulings((v) => !v)}
        title={`本层(${step.name})适用的已拍板裁决`}>
        适用裁决 {step.adopted_rulings.length}
      </button>
      {showRulings && (
        <div style={ST.rulPanelFloat} data-testid="narrative-rulings-panel">
          {step.adopted_rulings.map((r) => (
            <div key={r.id} style={ST.rulItem}>
              <span style={{ fontSize: 11.5, color: 'var(--fp-text-3)', fontFamily: MONO }}>{r.id}</span>
              <div style={{ fontSize: 12.5, lineHeight: 1.5 }}>{r.statement}</div>
              {r.anchor && r.anchor !== r.statement && (
                <div style={ST.rulQuote}>“{r.anchor}”</div>
              )}
            </div>
          ))}
        </div>
      )}
    </span>
  )
}

/** 叙事业务顶栏工厂: 生成并入审阅顶栏的 BusinessToolbarSpec(icon/title/sub + 适用裁决 aux + 决策历程 action)。 */
function narrativeToolbar(icon: ReactNode, title: string, sub?: string): (m: Material) => BusinessToolbarSpec {
  return (m: Material) => {
    const project = m.project || 'vilo'
    return {
      icon,
      title,
      sub,
      aux: <NarrativeRulings m={m} />,
      actions: [{
        label: '决策历程',
        icon: <Flag size={13} />,
        title: '打开决策历程(材料轨迹画布)',
        // 跳转行为原样保留: 复用 panels store 的 openTab(工厂在 React 外, 走 getState())。
        onClick: () => usePanels.getState().openTab({ type: 'project', id: project }, project),
      }],
    }
  }
}

// 九个渲染器各自的顶栏工厂(businesses/index.ts 挂到对应 RendererEntry.toolbar)。
export const narrativeOutlineToolbar = narrativeToolbar(<LayoutGrid size={15} />, '大纲 · 段×线结构图')
export const narrativePremiseToolbar = narrativeToolbar(<ScrollText size={15} />, '立意', '唯一权威=vilo wiki/10 洁净版; 引擎内为誊抄投影')
export const narrativeCharactersToolbar = narrativeToolbar(<Users size={15} />, '角色卡')
export const narrativeDraftsToolbar = narrativeToolbar(<Columns3 size={15} />, '草稿看板', '只读陈列 · 转正/编辑走内容引擎(agent)')
export const narrativeScenesToolbar = narrativeToolbar(<LayoutGrid size={15} />, '情节(场景客观事实)')
export const narrativeSettingToolbar = narrativeToolbar(<Users size={15} />, '设定 · 世界与关系')
export const narrativeGuidanceToolbar = narrativeToolbar(<ScrollText size={15} />, '背景 / 受众 / 揭示层', '叙事指导层的补充载体')
export const narrativeStyleEngineToolbar = narrativeToolbar(<Columns3 size={15} />, '文风矩阵 · 演算引擎', '先语义后文风;引擎细图后续按需接投影')
export const narrativeGameTextToolbar = narrativeToolbar(<ScrollText size={15} />, '游戏内文本')

// ── 顶栏局部样式(仅"适用裁决"chip 与其展开浮层; 顶栏其余件由 MaterialDetail 合并栏统管) ──
const ST: Record<string, CSSProperties> = {
  rulChip: {
    display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 999, cursor: 'pointer',
    fontSize: 12, color: 'var(--fp-violet)', background: 'color-mix(in srgb, var(--fp-violet) 10%, transparent)',
    border: '1px solid color-mix(in srgb, var(--fp-violet) 32%, transparent)',
  },
  // 展开面板: 合并栏里挂在 chip 下方的浮层(绝对定位, 不撑开顶栏行高)。
  rulPanelFloat: {
    position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 20, width: 340, maxHeight: 320, overflow: 'auto',
    padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 10, borderRadius: 10,
    background: 'var(--fp-surface)', border: '1px solid var(--fp-border)',
    boxShadow: '0 8px 24px rgba(0,0,0,.32), inset 0 1px 0 rgba(255,255,255,.06)',
  },
  rulItem: { display: 'flex', flexDirection: 'column', gap: 3 },
  rulQuote: {
    fontSize: 12, color: 'var(--fp-text-2)', lineHeight: 1.55, fontStyle: 'italic',
    borderLeft: '2px solid color-mix(in srgb, var(--fp-violet) 55%, transparent)',
    padding: '4px 10px', background: 'color-mix(in srgb, var(--fp-violet) 6%, transparent)', borderRadius: '0 6px 6px 0',
  },
}

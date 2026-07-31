// 决策树=具象管线 · 项目页管线栏步骤卡横排。
// 一条域级作业管线单程向右: 每张步骤卡 = 层级名 + 人话说明 + 门禁执法器 chip + 样例数/适用裁决数。
// 点卡展开参考来源面板(样例材料可点开 → onOpenMaterial 由 ProjectDetail 接 openTab; 裁决带原话)。
// 「按这一步开工」= 组装开工上下文包文本(参照 ReviewCanvas nextStepPackage 包结构)复制到剪贴板。
// 视觉走 frostpane 玻璃 token; 四步冷色由 --fp-accent-2(青)→--fp-accent(蓝) color-mix 渐变, 门禁紫=--fp-violet。

import React, { useEffect, useMemo, useState } from 'react'
import { ArrowRight, ShieldCheck, Layers, Flag, Play, ChevronRight, Copy, Check, Info } from 'lucide-react'
import { reviewstageApi, type DomainTreeStep, type DomainTreeDomain, type DomainTreeSample } from '../../api/reviewstageClient'
import { copyText } from '../../lib/copyText'

const EASE = 'cubic-bezier(0.175,0.885,0.32,1.1)'
const MONO = "'Berkeley Mono','Consolas','Menlo',monospace"

// 四步冷色: 序 1→N 在 accent-2(青)与 accent(蓝)之间 color-mix。单步时退回 accent。
function stepColor(order: number, total: number): string {
  if (total <= 1) return 'var(--fp-accent)'
  const t = Math.round(((order - 1) / (total - 1)) * 100)
  return `color-mix(in srgb, var(--fp-accent) ${t}%, var(--fp-accent-2))`
}

const S: Record<string, any> = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 14 },
  lead: { display: 'flex', alignItems: 'center', gap: 10 },
  leadCap: {
    width: 30, height: 30, borderRadius: 8, flex: '0 0 auto',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--fp-accent-weak)', border: '1px solid var(--fp-border)', color: 'var(--fp-link)',
  },
  leadTitle: { fontSize: 13, fontWeight: 650, color: 'var(--fp-text)', letterSpacing: '.01em' },
  leadSub: { fontSize: 12, color: 'var(--fp-text-3)', marginTop: 1 },
  // 横向滚动跑道: 步骤卡 + 箭头单程向右
  rail: { display: 'flex', alignItems: 'stretch', gap: 0, overflowX: 'auto', paddingBottom: 4 },
  card: (color: string, active: boolean): React.CSSProperties => ({
    width: 268, flex: '0 0 268px', display: 'flex', flexDirection: 'column',
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: `1px solid ${active ? color : 'var(--fp-border)'}`, borderTop: `3px solid ${color}`,
    borderRadius: 11, cursor: 'pointer', boxShadow: active ? '0 8px 26px rgba(0,0,0,.42)' : 'var(--fp-shadow-sm)',
    transition: `border-color 150ms ${EASE}, box-shadow 150ms ${EASE}`,
  }),
  cardHead: { display: 'flex', alignItems: 'center', gap: 10, padding: '13px 14px 10px' },
  seq: (color: string): React.CSSProperties => ({
    flex: '0 0 auto', width: 26, height: 26, borderRadius: 7,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 14, fontWeight: 700, color: 'var(--fp-bg)', background: color,
    fontVariantNumeric: 'tabular-nums',
  }),
  layerName: { fontSize: 15, fontWeight: 650, color: 'var(--fp-text)', letterSpacing: '.01em' },
  cardBody: { padding: '0 14px 13px', display: 'flex', flexDirection: 'column', gap: 10, flex: '1 1 auto' },
  plain: { fontSize: 12.5, color: 'var(--fp-text-2)', lineHeight: 1.55 },
  gateRow: {
    display: 'flex', alignItems: 'center', gap: 7, padding: '7px 9px', borderRadius: 8,
    background: 'color-mix(in srgb, var(--fp-violet) 8%, transparent)',
    border: '1px solid color-mix(in srgb, var(--fp-violet) 30%, transparent)',
  },
  gateTxt: { fontSize: 11.5, color: 'var(--fp-violet)', fontFamily: MONO, wordBreak: 'break-all', lineHeight: 1.4 },
  counts: { display: 'flex', gap: 8 },
  chip: (zero: boolean): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', gap: 6, flex: 1, padding: '5px 8px', borderRadius: 7,
    background: 'var(--fp-surface)', border: `1px ${zero ? 'dashed' : 'solid'} var(--fp-border)`,
  }),
  chipLabel: { fontSize: 11.5, color: 'var(--fp-text-3)' },
  chipNum: (zero: boolean): React.CSSProperties => ({
    marginLeft: 'auto', fontSize: 13, fontWeight: 700, fontVariantNumeric: 'tabular-nums',
    color: zero ? 'var(--fp-text-3)' : 'var(--fp-text)',
  }),
  sink: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--fp-text-3)', marginTop: 'auto' },
  cardFoot: (color: string): React.CSSProperties => ({
    padding: '9px 14px', borderTop: '1px solid var(--fp-border-subtle)',
    display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color,
  }),
  arrow: { flex: '0 0 40px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fp-border-strong)' },

  // 展开的参考来源面板
  panel: {
    background: 'var(--fp-solid)', border: '1px solid var(--fp-border)', borderRadius: 11,
    padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 14,
  },
  panelHead: { display: 'flex', alignItems: 'center', gap: 10 },
  panelSeq: (color: string): React.CSSProperties => ({
    width: 28, height: 28, borderRadius: 8, flex: '0 0 auto',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 14, fontWeight: 700, color: 'var(--fp-bg)', background: color, fontVariantNumeric: 'tabular-nums',
  }),
  panelTitle: { fontSize: 15, fontWeight: 650, color: 'var(--fp-text)' },
  panelTag: { fontSize: 11.5, color: 'var(--fp-text-3)' },
  panelStartBtn: (color: string): React.CSSProperties => ({
    marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6,
    border: `1px solid ${color}`, background: 'color-mix(in srgb, var(--fp-accent) 12%, transparent)',
    color: 'var(--fp-link)', borderRadius: 8, padding: '7px 13px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer',
    transition: `filter 150ms ${EASE}`,
  }),
  secTitle: { fontSize: 11.5, fontWeight: 650, color: 'var(--fp-text-3)', letterSpacing: '.03em', display: 'flex', alignItems: 'center', gap: 8 },
  secCount: { fontVariantNumeric: 'tabular-nums', color: 'var(--fp-text-2)', background: 'var(--fp-surface)', border: '1px solid var(--fp-border)', borderRadius: 5, padding: '0 6px', fontSize: 11.5 },
  samp: {
    background: 'var(--fp-surface)', border: '1px solid var(--fp-border)', borderRadius: 9,
    padding: '10px 12px', cursor: 'pointer', transition: `border-color 150ms ${EASE}`,
  },
  sampTitle: { fontSize: 12.5, color: 'var(--fp-text)', lineHeight: 1.45 },
  sampMeta: { display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, flexWrap: 'wrap' },
  badge: { display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11.5, padding: '1px 7px', borderRadius: 5, border: '1px solid var(--fp-border)', color: 'var(--fp-text-2)', background: 'var(--fp-glass-2)', fontVariantNumeric: 'tabular-nums' },
  badgeOk: { color: 'var(--fp-ok)', borderColor: 'color-mix(in srgb, var(--fp-ok) 35%, transparent)', background: 'color-mix(in srgb, var(--fp-ok) 10%, transparent)' },
  empty: {
    display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12, color: 'var(--fp-text-3)', lineHeight: 1.5,
    padding: '11px 12px', border: '1px dashed var(--fp-border)', borderRadius: 9, background: 'var(--fp-surface)',
  },
  rul: { border: '1px solid var(--fp-border)', borderRadius: 8, background: 'var(--fp-surface)', overflow: 'hidden' },
  rulHead: { display: 'flex', alignItems: 'flex-start', gap: 8, padding: '9px 11px', cursor: 'pointer' },
  rid: { fontSize: 11.5, color: 'var(--fp-text-3)', fontFamily: MONO, flex: '0 0 auto', paddingTop: 1 },
  rst: { fontSize: 12.5, color: 'var(--fp-text)', lineHeight: 1.45, flex: 1 },
  quote: {
    fontSize: 12, color: 'var(--fp-text-2)', lineHeight: 1.55,
    borderLeft: '2px solid color-mix(in srgb, var(--fp-violet) 55%, transparent)',
    padding: '5px 10px', margin: '0 11px 11px', background: 'color-mix(in srgb, var(--fp-violet) 6%, transparent)',
    borderRadius: '0 6px 6px 0',
  },
  gateBlock: {
    background: 'color-mix(in srgb, var(--fp-violet) 6%, transparent)',
    border: '1px solid color-mix(in srgb, var(--fp-violet) 28%, transparent)', borderRadius: 9, padding: '11px 13px',
    display: 'flex', flexDirection: 'column', gap: 6,
  },
  gateName: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--fp-violet)', fontFamily: MONO, wordBreak: 'break-all' },
}

const STATUS_LABEL: Record<string, string> = { accepted: '已采纳', pending: '待裁决', rejected: '已驳回', blocked: '已阻断' }

// 开工上下文包文本(参照 ReviewCanvas nextStepPackage 包结构): 该做哪一层/形态期望/上轮样例路径/
// 适用裁决/门禁/产出去向。空 samples 如实标"暂无样例", 不造假。
function buildStartPackage(project: string, domain: string, step: DomainTreeStep): string {
  const lines: string[] = []
  lines.push(`# 按决策树开工 · ${domain} 域「${step.name}」`)
  lines.push('')
  lines.push(`- 项目: ${project}`)
  lines.push(`- 层级(track): ${step.name}(第 ${step.order} 步)`)
  lines.push(`- 形态期望: ${step.expected_kinds.join(' / ') || '(未定)'}`)
  if (step.next) lines.push(`- 下一层: ${step.next}`)
  lines.push('')
  lines.push('## 这一步做什么')
  lines.push(step.desc)
  lines.push('')
  lines.push(`## 上一轮样例引用(${step.samples.length})`)
  if (step.samples.length === 0) lines.push('(本域该层暂无样例, 首轮产出即成为下一轮引用。)')
  else for (const s of step.samples) {
    const path = s.file_relpath ? ` · ${s.file_relpath}` : ''
    lines.push(`- ${s.id}(v${s.version ?? '?'}, ${STATUS_LABEL[s.status] || s.status}): ${s.title}${path}`)
  }
  lines.push('')
  lines.push(`## 适用裁决(${step.adopted_rulings.length})`)
  if (step.adopted_rulings.length === 0) lines.push('(本步无适用的已拍板裁决。)')
  else for (const r of step.adopted_rulings) lines.push(`- ${r.id}: ${r.statement}`)
  lines.push('')
  lines.push('## 门禁(须过才算这步完成)')
  lines.push(`- 执法器: ${step.gate.enforcer}`)
  lines.push('')
  lines.push('## 产出去向')
  lines.push(`${step.name} 层 · ${step.expected_kinds.join(' / ') || '材料'}`)
  return lines.join('\n')
}

function SampleCard({ sample, color, onOpen }: { sample: DomainTreeSample; color: string; onOpen: () => void }) {
  return (
    <div
      style={{ ...S.samp, borderLeft: `3px solid ${color}` }} data-testid="domain-tree-sample"
      onClick={onOpen} role="button" tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen() } }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border-strong)' }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--fp-border)' }}
    >
      <div style={S.sampTitle}>{sample.title}</div>
      <div style={S.sampMeta}>
        <span style={S.badge}>v{sample.version ?? '?'}</span>
        <span style={sample.status === 'accepted' ? { ...S.badge, ...S.badgeOk } : S.badge}>
          {sample.status === 'accepted' && <Check size={11} />}{STATUS_LABEL[sample.status] || sample.status}
        </span>
        <span style={{ ...S.badge, fontFamily: MONO }}>{sample.id.replace('mat_', 'mat·').slice(0, 12)}</span>
      </div>
    </div>
  )
}

function RulingRow({ ruling }: { ruling: { id: string; statement: string; anchor: string } }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={S.rul} data-testid="domain-tree-ruling">
      <div style={S.rulHead} onClick={() => setOpen((v) => !v)}
        role="button" tabIndex={0} aria-expanded={open}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((v) => !v) } }}>
        <span style={S.rid}>{ruling.id.replace('DEC-2026-07-04-', '#').replace('DEC-2026-07-05-', '#')}</span>
        <span style={S.rst}>{ruling.statement}</span>
        <ChevronRight size={14} style={{ flex: '0 0 auto', color: 'var(--fp-text-3)', marginTop: 2, transform: open ? 'rotate(90deg)' : 'none', transition: `transform 200ms ${EASE}` }} />
      </div>
      {open && ruling.anchor && <div style={S.quote}>{ruling.anchor}</div>}
    </div>
  )
}

function StepPanel({ project, domain, step, color, onOpenMaterial }: {
  project: string; domain: string; step: DomainTreeStep; color: string
  onOpenMaterial: (id: string, title: string) => void
}) {
  const [copied, setCopied] = useState(false)
  const onStart = () => {
    void copyText(buildStartPackage(project, domain, step)).then((ok) => {
      setCopied(ok)
      window.setTimeout(() => setCopied(false), 1600)
    })
  }
  return (
    <div style={S.panel} data-testid="domain-tree-panel">
      <div style={S.panelHead}>
        <div style={S.panelSeq(color)}>{step.order}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={S.panelTag}>第 {step.order} 步 · {domain} 域</div>
          <div style={S.panelTitle}>{step.name}</div>
        </div>
        <button type="button" style={S.panelStartBtn(color)} data-testid="domain-tree-start"
          onClick={onStart}
          onMouseEnter={(e) => { e.currentTarget.style.filter = 'brightness(1.08)' }}
          onMouseLeave={(e) => { e.currentTarget.style.filter = 'none' }}>
          {copied ? <Check size={14} /> : <Play size={14} />}{copied ? '已复制' : '按这一步开工'}
        </button>
      </div>

      <div style={S.plain}>{step.desc}</div>

      <div>
        <div style={S.secTitle}><Layers size={13} />上一轮样例<span style={S.secCount}>{step.samples.length}</span></div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          {step.samples.length > 0
            ? step.samples.map((s) => (
              <SampleCard key={s.id} sample={s} color={color} onOpen={() => onOpenMaterial(s.id, s.title)} />
            ))
            : <div style={S.empty}><Info size={15} style={{ flex: '0 0 auto', marginTop: 1 }} /><span>本域该层暂无样例,首轮产出后会自动出现在这里。</span></div>}
        </div>
      </div>

      <div>
        <div style={S.secTitle}><Flag size={13} />适用裁决<span style={S.secCount}>{step.adopted_rulings.length}</span></div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          {step.adopted_rulings.length > 0
            ? step.adopted_rulings.map((r) => <RulingRow key={r.id} ruling={r} />)
            : <div style={S.empty}><Info size={15} style={{ flex: '0 0 auto', marginTop: 1 }} /><span>本步暂无适用的已拍板裁决。</span></div>}
        </div>
      </div>

      <div>
        <div style={S.secTitle}><ShieldCheck size={13} />门禁</div>
        <div style={{ ...S.gateBlock, marginTop: 8 }}>
          <div style={S.gateName}><ShieldCheck size={15} style={{ flex: '0 0 auto' }} />{step.gate.enforcer}</div>
        </div>
      </div>
    </div>
  )
}

/** 决策树=具象管线的管线栏。项目无所属域时返回 null(由 ProjectDetail 整条隐藏)。 */
export default function DomainTreeBar({ project, onOpenMaterial }: {
  project: string
  onOpenMaterial: (id: string, title: string) => void
}) {
  const [domains, setDomains] = useState<DomainTreeDomain[] | null>(null)
  const [openKey, setOpenKey] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    reviewstageApi.domainTree(project)
      .then((d) => { if (alive) setDomains(d.domains) })
      .catch(() => { if (alive) setDomains([]) })
    return () => { alive = false }
  }, [project])

  const allSteps = useMemo(
    () => (domains || []).flatMap((d) => d.steps.map((s) => ({ domain: d.domain, step: s }))),
    [domains],
  )

  if (domains === null) return null
  if (domains.length === 0) return null

  return (
    <div style={S.wrap} data-testid="domain-tree-bar">
      {domains.map((d) => {
        const total = d.steps.length
        return (
          <div key={d.domain} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={S.lead}>
              <span style={S.leadCap}><ArrowRight size={16} /></span>
              <div>
                <div style={S.leadTitle}>{d.domain} 域作业管线 · 单程向右</div>
                <div style={S.leadSub}>一条域级具象化管线,点任一步看该怎么做(参考来源 + 门禁 + 按这一步开工)。</div>
              </div>
            </div>
            <div style={S.rail}>
              {d.steps.map((step, i) => {
                const color = stepColor(step.order, total)
                const key = `${d.domain}/${step.name}`
                const active = openKey === key
                return (
                  <React.Fragment key={key}>
                    <div
                      style={S.card(color, active)} data-testid="domain-tree-step"
                      onClick={() => setOpenKey(active ? null : key)}
                      role="button" tabIndex={0} aria-expanded={active}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpenKey(active ? null : key) } }}
                      onMouseEnter={(e) => { if (!active) e.currentTarget.style.borderColor = 'var(--fp-border-strong)' }}
                      onMouseLeave={(e) => { if (!active) e.currentTarget.style.borderColor = 'var(--fp-border)' }}
                    >
                      <div style={S.cardHead}>
                        <div style={S.seq(color)}>{step.order}</div>
                        <div style={S.layerName}>{step.name}</div>
                      </div>
                      <div style={S.cardBody}>
                        <div style={S.plain}>{step.desc}</div>
                        <div style={S.gateRow}>
                          <ShieldCheck size={14} style={{ flex: '0 0 auto', color: 'var(--fp-violet)' }} />
                          <span style={S.gateTxt}>{step.gate.enforcer}</span>
                        </div>
                        <div style={S.counts}>
                          <span style={S.chip(step.samples.length === 0)}>
                            <Layers size={12} style={{ color: 'var(--fp-text-3)', flex: '0 0 auto' }} />
                            <span style={S.chipLabel}>样例</span>
                            <span style={S.chipNum(step.samples.length === 0)}>{step.samples.length}</span>
                          </span>
                          <span style={S.chip(step.adopted_rulings.length === 0)}>
                            <Flag size={12} style={{ color: 'var(--fp-text-3)', flex: '0 0 auto' }} />
                            <span style={S.chipLabel}>裁决</span>
                            <span style={S.chipNum(step.adopted_rulings.length === 0)}>{step.adopted_rulings.length}</span>
                          </span>
                        </div>
                        <div style={S.sink}>产出去向: {step.name} 层 · {step.expected_kinds.join(' / ') || '材料'}</div>
                      </div>
                      <div style={S.cardFoot(color)}><ChevronRight size={13} />点开看参考来源 · 按这一步开工</div>
                    </div>
                    {i < d.steps.length - 1 && (
                      <div style={S.arrow}><ArrowRight size={22} strokeWidth={2.4} /></div>
                    )}
                  </React.Fragment>
                )
              })}
            </div>
          </div>
        )
      })}

      {openKey && (() => {
        const found = allSteps.find((x) => `${x.domain}/${x.step.name}` === openKey)
        if (!found) return null
        return (
          <StepPanel
            project={project} domain={found.domain} step={found.step}
            color={stepColor(found.step.order, (domains.find((d) => d.domain === found.domain)?.steps.length) || 1)}
            onOpenMaterial={onOpenMaterial}
          />
        )
      })()}
    </div>
  )
}

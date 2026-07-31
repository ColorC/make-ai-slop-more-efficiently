// plan_audit 实体 — 三点菜单「跑 plan audit」打开的审计报告视图。
// audit 是分钟级 LLM 循环, 后端 POST /api/plan-audit 起后台 job, 这里按 job_id 轮询
// GET /api/plan-audit/{job_id} 直到 done/error, 渲染人读报告(指示清单+状态+证据+未落地汇总)。
import React, { useEffect, useRef, useState } from 'react'
import { Copy, Hash } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration } from '../registry'
import { copyText } from '../../lib/copyText'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'

interface AuditJob {
  status: 'running' | 'done' | 'error'
  against?: string
  target?: string
  elapsed_s?: number
  report_md?: string
  error?: string
  result?: any
}

// 状态语义色: 全走 token, 不写死 hex。运行=链接蓝 / 完成=绿 / 出错=红。
const TONE: Record<string, string> = { running: 'var(--fp-link)', done: 'var(--fp-ok)', error: 'var(--fp-err)' }
const STATUS_LABEL: Record<string, string> = { running: '审计中', done: '已完成', error: '失败' }

const S: Record<string, any> = {
  // 面板 root 透明吃全局冷渐变(不铺实底); 内容从顶部直接开始, 无重复页签名的标题头。
  root: { height: '100%', overflow: 'auto', background: 'transparent', color: 'var(--fp-text)', padding: '14px 20px 40px', boxSizing: 'border-box' },
  // 顶行: 状态药丸(主焦点) + 元信息 + ⋯ 收纳低频操作(复制报告 / 复制 job id)。
  head: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, flexWrap: 'wrap' },
  // 状态药丸: 14px, 语义色描边 + 同色淡底(token color-mix)。
  pill: (tone: string): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 14, fontWeight: 600, borderRadius: 999, padding: '3px 11px',
    color: tone, background: `color-mix(in srgb, ${tone} 14%, transparent)`, border: `1px solid color-mix(in srgb, ${tone} 38%, transparent)`,
  }),
  dot: (tone: string): React.CSSProperties => ({ width: 6, height: 6, borderRadius: '50%', background: tone }),
  // 元信息: 次级 13px。
  meta: { color: 'var(--fp-text-2)', fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  // 最弱微字: 12px 等宽弱灰(禁 11px)。
  micro: { color: 'var(--fp-text-3)', fontSize: 12, fontFamily: 'var(--fp-font-mono)' },
  // 运行态玻璃卡: 磨砂 + rim 高光 + token 描边。
  card: {
    background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)',
    border: '1px solid var(--fp-border)', borderRadius: 11, boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
  },
  running: { display: 'flex', alignItems: 'center', gap: 10, color: 'var(--fp-link)', fontSize: 14, padding: '16px 18px' },
  spinner: { width: 14, height: 14, border: '2px solid var(--fp-border)', borderTopColor: 'var(--fp-link)', borderRadius: '50%', display: 'inline-block', animation: 'omni-spin 0.8s linear infinite', flexShrink: 0 },
  // 长 mono 报告: 阅读区保持安静 —— 极淡 surface 不盖死渐变, token 描边, 13px 行高 1.65。
  report: { whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'var(--fp-font-mono)', fontSize: 13, lineHeight: 1.65, color: 'var(--fp-text)', background: 'var(--fp-surface)', border: '1px solid var(--fp-border-subtle)', borderRadius: 11, padding: '16px 18px' },
  // 错误态: 语义红, color-mix 淡底替代写死 hex。
  err: { color: 'var(--fp-err)', fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap', background: 'color-mix(in srgb, var(--fp-err) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--fp-err) 34%, transparent)', borderRadius: 11, padding: '14px 16px' },
}

function PlanAuditView({ entity }: { entity: Entity }) {
  const jobId = entity.id
  const [job, setJob] = useState<AuditJob | null>(null)
  const [pollErr, setPollErr] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    let alive = true
    let netRetries = 0
    const poll = async () => {
      try {
        const r = await fetch(`/api/plan-audit/${encodeURIComponent(jobId)}`)
        if (r.status === 404) {
          // job 不存在/已过期(dashboard 重启会清空进行中的内存 job) → 停止轮询, 别无限 404
          if (alive) setJob({ status: 'error', error: 'audit job 不存在或已过期(dashboard 重启会清空进行中的 job)。请重新发起审计。' })
          return
        }
        if (!r.ok) {
          if (alive && netRetries++ < 5) { setPollErr(`轮询失败: ${r.status}, 重试中…`); timer.current = window.setTimeout(poll, 4000) }
          else if (alive) setJob({ status: 'error', error: `轮询多次失败: ${r.status}` })
          return
        }
        netRetries = 0
        const d = (await r.json()) as AuditJob
        if (!alive) return
        setJob(d); setPollErr(null)
        if (d.status === 'running') timer.current = window.setTimeout(poll, 3000)
      } catch (e: any) {
        if (alive && netRetries++ < 5) { setPollErr(String(e?.message || e)); timer.current = window.setTimeout(poll, 4000) }
        else if (alive) setJob({ status: 'error', error: `轮询失败: ${e?.message || e}` })
      }
    }
    void poll()
    return () => { alive = false; if (timer.current) window.clearTimeout(timer.current) }
  }, [jobId])

  const status = job?.status || 'running'
  const tone = TONE[status] || 'var(--fp-link)'

  // 低频操作收进共享 ⋯ 菜单: 复制报告(done 时可用) / 复制 job id。无一排等权按钮。
  const kebabItems: KebabItem[] = [
    {
      label: '复制报告', icon: <Copy size={14} />, testid: 'plan-audit-copy-report',
      disabled: !job?.report_md, onClick: () => { if (job?.report_md) void copyText(job.report_md) },
    },
    { label: '复制 job id', icon: <Hash size={14} />, testid: 'plan-audit-copy-jobid', onClick: () => { void copyText(jobId) } },
  ]

  return (
    <div style={S.root} data-testid="plan-audit-view">
      <style>{'@keyframes omni-spin{to{transform:rotate(360deg)}}'}</style>
      <div style={S.head}>
        <span style={S.pill(tone)} data-testid="plan-audit-status">
          <span style={S.dot(tone)} />{STATUS_LABEL[status] || status}
        </span>
        {job?.against && <span style={S.meta}>{job.against === 'plan' ? 'plan' : '对话'} · {job?.target}</span>}
        {typeof job?.elapsed_s === 'number' && <span style={S.micro}>{job.elapsed_s}s</span>}
        <span style={{ flex: 1 }} />
        <KebabMenu testid="plan-audit-actions" items={kebabItems} />
      </div>

      {status === 'running' && (
        <div style={{ ...S.card, ...S.running }}>
          <span style={S.spinner} />
          审计中… agent 正在读{job?.against === 'plan' ? '相关对话与 plan' : '对话'}、逐条用 grep/read/git 核查落地，分钟级，请稍候。
        </div>
      )}

      {status === 'error' && <div style={S.err} data-testid="plan-audit-error">审计失败: {job?.error || pollErr || '未知错误'}</div>}

      {status === 'done' && (
        <div style={S.report} data-testid="plan-audit-report">{job?.report_md || '(无报告内容)'}</div>
      )}

      {pollErr && status === 'running' && <div style={{ ...S.micro, marginTop: 10 }}>（{pollErr}，重试中…）</div>}
    </div>
  )
}

const auditEntity = (id: string): Entity => ({ type: 'plan_audit', id, title: '审计报告' })

export const planAuditRegistration: EntityRegistration = {
  label: '落地审计',
  icon: 'shield-check',
  resolver: {
    type: 'plan_audit',
    fetch: async (id: string) => auditEntity(id),
    list: async () => [],
  },
  renderer: { type: 'plan_audit', Editor: PlanAuditView as any },
}

// 定时任务管理视图 — 控制器顶层第 4 个视图(与 最近访问/项目/总控对话 并列)。
// 列 omni cron 体系的全部任务(治理/卫生/资源): 名/档期/用不用 LLM/详细说明/上次跑/到期,
// 带「立即跑」(POST /api/cron/run/{name}) + 「历史」(GET /api/cron/history/{name}) + 心跳状态。
import React, { useCallback, useEffect, useState } from 'react'
import { relTimeZh as relTime } from '../../lib/time'

interface CronTask {
  name: string
  schedule: string
  kind: string
  command: string
  description: string
  detail: string
  last_run_at: string | null
  due: boolean
  uses_llm: boolean
}
interface CronData {
  tasks: CronTask[]
  total: number
  last_tick: { checked_at?: string; due_count?: number } | null
  trigger_installed: boolean
}
interface CronRun {
  ts: string
  trigger: string
  ok: boolean
  returncode?: number
  preview?: string
  log?: string
}

const SCHED_ZH: Record<string, string> = { '@daily': '每天', '@weekly': '每周', '@monthly': '每月', '@hourly': '每小时', '@yearly': '每年' }

const S: Record<string, React.CSSProperties> = {
  root: { height: '100%', overflow: 'auto', background: 'transparent', color: 'var(--fp-text)', boxSizing: 'border-box', padding: '14px 18px 30px' },
  state: { padding: 16, fontSize: 15, color: 'var(--fp-text-3)' },
  head: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 12 },
  title: { fontSize: 18, fontWeight: 750, color: '#fff' },
  sub: { color: 'var(--fp-text-3)', fontSize: 13, marginLeft: 8 },
  hb: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#9aa7b4' },
  hbOn: { color: 'var(--fp-ok)', fontWeight: 600 },
  hbOff: { color: 'var(--fp-err)', fontWeight: 600 },
  refresh: { border: '1px solid var(--fp-border)', background: 'var(--fp-card)', color: 'var(--fp-text-2)', borderRadius: 5, padding: '3px 10px', fontSize: 13, cursor: 'pointer' },
  list: { display: 'flex', flexDirection: 'column', gap: 6 },
  row: { border: '1px solid #18222d', borderRadius: 7, padding: '9px 11px', background: '#0d1318' },
  rowMain: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  name: { color: 'var(--fp-text)', fontSize: 14.5, fontWeight: 650 },
  sched: { border: '1px solid var(--fp-border)', color: '#9aa7b4', borderRadius: 4, padding: '0 6px', fontSize: 12.5, background: 'var(--fp-card)' },
  llm: { border: '1px solid #5a4a18', color: 'var(--fp-warn)', borderRadius: 4, padding: '0 6px', fontSize: 12.5, background: '#211a07' },
  noLlm: { border: '1px solid var(--fp-border)', color: '#6e8aa3', borderRadius: 4, padding: '0 6px', fontSize: 12.5, background: '#0c151d' },
  due: { color: 'var(--fp-link)', fontSize: 12.5, fontWeight: 600 },
  last: { color: 'var(--fp-text-3)', fontSize: 12.5 },
  btn: { border: '1px solid var(--fp-border)', background: 'var(--fp-card)', color: 'var(--fp-text-2)', borderRadius: 5, padding: '3px 10px', fontSize: 13, cursor: 'pointer' },
  runBtn: { border: '1px solid var(--fp-accent)', background: 'var(--fp-accent-weak)', color: 'var(--fp-link)', borderRadius: 5, padding: '3px 12px', fontSize: 13, cursor: 'pointer', fontWeight: 600 },
  detail: { color: '#b3bdc7', fontSize: 13.5, marginTop: 6, lineHeight: 1.5 },
  cmd: { color: '#6e7681', fontSize: 12.5, fontFamily: 'Consolas, monospace', marginTop: 4 },
  histBox: { marginTop: 8, borderTop: '1px solid #18222d', paddingTop: 7 },
  histTitle: { color: 'var(--fp-text-3)', fontSize: 12.5, marginBottom: 4 },
  histRow: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, padding: '2px 0' },
  histOk: { color: 'var(--fp-ok)', width: 16 },
  histFail: { color: 'var(--fp-err)', width: 16 },
  histTime: { color: '#c2cdd8', minWidth: 96 },
  histTrig: { color: '#6e7681', minWidth: 40 },
  histTail: { color: '#6e7681', fontFamily: 'Consolas, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 },
  logBtn: { border: '1px solid var(--fp-border)', background: 'var(--fp-card)', color: 'var(--fp-link)', borderRadius: 4, padding: '0 7px', fontSize: 12, cursor: 'pointer', flexShrink: 0 },
  logPre: { margin: '4px 0 6px 24px', padding: '8px 10px', background: '#06090d', border: '1px solid #18222d', borderRadius: 5, color: '#a8b0ba', fontSize: 12, fontFamily: 'Consolas, monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 280, overflow: 'auto' },
}

export default function CronView() {
  const [data, setData] = useState<CronData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState<string>('')
  const [histOpen, setHistOpen] = useState<string>('')
  const [hist, setHist] = useState<Record<string, CronRun[] | 'loading'>>({})
  const [logOpen, setLogOpen] = useState<string>('')
  const [logContent, setLogContent] = useState<Record<string, string>>({})

  const load = useCallback(() => {
    fetch('/api/cron')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => { setData(d); setError(null) })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(() => { load() }, [load])

  const loadHistory = useCallback(async (name: string) => {
    setHist((h) => ({ ...h, [name]: 'loading' }))
    try {
      const r = await fetch(`/api/cron/history/${encodeURIComponent(name)}`)
      const d = await r.json()
      setHist((h) => ({ ...h, [name]: (d.runs as CronRun[]) || [] }))
    } catch {
      setHist((h) => ({ ...h, [name]: [] }))
    }
  }, [])

  const toggleHistory = (name: string) => {
    if (histOpen === name) { setHistOpen(''); return }
    setHistOpen(name)
    if (!hist[name]) void loadHistory(name)
  }

  const viewLog = async (logPath: string) => {
    if (logOpen === logPath) { setLogOpen(''); return }
    setLogOpen(logPath)
    if (logContent[logPath] === undefined) {
      try {
        const r = await fetch(`/api/cron/log?path=${encodeURIComponent(logPath)}`)
        const d = await r.json()
        setLogContent((m) => ({ ...m, [logPath]: d.content ?? '(空)' }))
      } catch {
        setLogContent((m) => ({ ...m, [logPath]: '读取失败' }))
      }
    }
  }

  const runNow = async (name: string) => {
    if (running) return
    setRunning(name)
    try {
      await fetch(`/api/cron/run/${encodeURIComponent(name)}`, { method: 'POST' })
    } catch { /* 触发失败也照常刷新 */ }
    // 任务是 detached 跑的, 稍等再刷新看 last_run / 历史 更新
    window.setTimeout(() => {
      load()
      if (histOpen === name) void loadHistory(name)
      setRunning('')
    }, 3000)
  }

  if (error) return <div style={S.state}>定时任务加载失败: {error}</div>
  if (!data) return <div style={S.state}>加载中…</div>

  const llmCount = data.tasks.filter((t) => t.uses_llm).length
  const lt = data.last_tick
  return (
    <div style={S.root} data-testid="cron-view">
      {/* 无标题头(Linear 风): 删"定时任务"标题+计数; 仅留心跳状态(右对齐)。 */}
      <div style={{ ...S.head, justifyContent: 'flex-end' }}>
        <div style={S.hb}>
          <span>心跳</span>
          {data.trigger_installed
            ? <span style={S.hbOn}>✓ 已装(每 10 分钟自动跑到期的)</span>
            : <span style={S.hbOff}>✗ 未装</span>}
          {lt?.checked_at && <span style={S.sub}>上次 tick {relTime(lt.checked_at)}</span>}
          <button type="button" style={S.refresh} onClick={load}>刷新</button>
        </div>
      </div>
      <div style={S.list}>
        {data.tasks.map((t) => {
          const open = histOpen === t.name
          const runs = hist[t.name]
          return (
            <div key={t.name} style={S.row} data-testid={`cron-task-${t.name}`}>
              <div style={S.rowMain}>
                <span style={S.name}>{t.name}</span>
                <span style={S.sched}>{SCHED_ZH[t.schedule] || t.schedule}</span>
                {t.uses_llm
                  ? <span style={S.llm} title="会调用 AI 模型(便宜模型), 有 token 成本">用 LLM</span>
                  : <span style={S.noLlm} title="纯代码扫描, 不调 AI, 不花钱">无 LLM</span>}
                {t.due && <span style={S.due}>▶ 到期</span>}
                <span style={{ flex: 1 }} />
                <span style={S.last}>{t.last_run_at ? `上次 ${relTime(t.last_run_at)}` : '从未跑'}</span>
                <button type="button" style={S.btn} data-testid={`cron-history-${t.name}`} onClick={() => toggleHistory(t.name)}>
                  {open ? '收起历史' : '历史'}
                </button>
                <button
                  type="button" style={S.runBtn}
                  disabled={running === t.name}
                  data-testid={`cron-run-${t.name}`}
                  onClick={() => runNow(t.name)}
                >{running === t.name ? '已触发…' : '立即跑'}</button>
              </div>
              <div style={S.detail}>{t.detail || t.description}</div>
              <div style={S.cmd}>{t.command}</div>
              {open && (
                <div style={S.histBox} data-testid={`cron-history-box-${t.name}`}>
                  <div style={S.histTitle}>运行历史(最近在前)</div>
                  {runs === 'loading' && <div style={S.last}>加载中…</div>}
                  {Array.isArray(runs) && runs.length === 0 && <div style={S.last}>还没有运行记录。</div>}
                  {Array.isArray(runs) && runs.map((r, i) => (
                    <div key={i}>
                      <div style={S.histRow}>
                        <span style={r.ok ? S.histOk : S.histFail}>{r.ok ? '✓' : '✗'}</span>
                        <span style={S.histTime}>{relTime(r.ts)}</span>
                        <span style={S.histTrig}>{r.trigger === 'manual' ? '手动' : '定时'}</span>
                        {typeof r.returncode === 'number' && <span style={S.histTrig}>rc={r.returncode}</span>}
                        <span style={S.histTail} title={r.preview || ''}>{(r.preview || '').replace(/\s+/g, ' ').slice(0, 100)}</span>
                        {r.log && <button type="button" style={S.logBtn} onClick={() => viewLog(r.log!)}>{logOpen === r.log ? '收起' : '全文'}</button>}
                      </div>
                      {r.log && logOpen === r.log && <pre style={S.logPre}>{logContent[r.log] ?? '加载中…'}</pre>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

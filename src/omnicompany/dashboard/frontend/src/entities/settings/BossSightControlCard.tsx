import React, { useEffect, useMemo, useState } from 'react'
import {
  bossSightApi,
  type BossSightControlItem,
  type BossSightControlResponse,
  type BossSightObservabilityDimension,
  type BossSightObservabilitySettings,
  type BossSightObservationEvent,
  type BossSightUserPrefs,
} from '../../api/bossSightClient'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { RefreshCw } from 'lucide-react'

const CONTROL_ORDER = [
  'controller.auto_wake',
  'reviewstage.push_to_user',
  'spawn.hard_block',
  'observability.enabled',
]

const CONTROL_COPY: Record<string, { title: string; body: string }> = {
  'controller.auto_wake': { title: '总控自动唤起', body: '评论、阻断、完成事件可以唤起 controller。' },
  'reviewstage.push_to_user': { title: '审阅推送', body: '重要审阅材料可以推到用户视野。' },
  'spawn.hard_block': { title: '硬阻断', body: '高风险 subagent 动作继续走硬阻断。' },
  'observability.enabled': { title: '观测总开关', body: '允许记录界面行为给 controller 读取。' },
}

const DIMENSIONS: BossSightObservabilityDimension[] = ['click', 'selection', 'toggle_change', 'view_dwell']

const DIM_COPY: Record<BossSightObservabilityDimension, { title: string; body: string }> = {
  click: { title: '点击', body: '记录用户点击过的界面目标。' },
  selection: { title: '圈选', body: '记录用户选中的文字或元素线索。' },
  toggle_change: { title: '开关变更', body: '记录设置和开关变化。' },
  view_dwell: { title: '视图停留', body: '记录用户停留在哪个视图。' },
}

const glassCard: React.CSSProperties = {
  background: 'var(--fp-glass)',
  backdropFilter: 'var(--fp-blur)',
  WebkitBackdropFilter: 'var(--fp-blur)',
  border: '1px solid var(--fp-border)',
  borderRadius: 11,
  boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
}

const S: Record<string, React.CSSProperties> = {
  // root 透明: 吃 body 全局冷渐变, 不再铺实底。
  root: { display: 'grid', gap: 14, background: 'transparent', color: 'var(--fp-text)' },
  // 分组玻璃卡
  group: { ...glassCard, padding: 14, display: 'grid', gap: 10 },
  groupHead: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 },
  groupTitle: { color: 'var(--fp-text)', fontSize: 15, fontWeight: 650, letterSpacing: '-0.01em' },
  groupHint: { color: 'var(--fp-text-3)', fontSize: 12 },
  // 开关行(玻璃组内的安静实色行)
  toggleRow: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
    border: '1px solid var(--fp-border-subtle)', borderRadius: 8, padding: '10px 12px', background: 'var(--fp-surface)',
  },
  toggleMain: { minWidth: 0, display: 'grid', gap: 3 },
  toggleTitleRow: { display: 'flex', alignItems: 'center', gap: 8 },
  toggleTitle: { color: 'var(--fp-text)', fontSize: 14, fontWeight: 600 },
  toggleBody: { color: 'var(--fp-text-2)', fontSize: 13, lineHeight: 1.45 },
  toggleMeta: { color: 'var(--fp-text-3)', fontSize: 12, fontFamily: 'var(--fp-font-mono)' },
  stateOn: { color: 'var(--fp-ok)', fontSize: 12, fontWeight: 600 },
  stateOff: { color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 600 },
  switch: { width: 18, height: 18, accentColor: 'var(--fp-accent)', flexShrink: 0, cursor: 'pointer' },
  // 表单
  form: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, alignItems: 'end' },
  inputWrap: { display: 'grid', gap: 4, minWidth: 0 },
  label: { color: 'var(--fp-text-3)', fontSize: 12, fontWeight: 600, letterSpacing: '.02em' },
  input: { background: 'var(--fp-card)', border: '1px solid var(--fp-border)', borderRadius: 6, color: 'var(--fp-text)', padding: '7px 9px', minWidth: 0, fontSize: 14 },
  submitWrap: { display: 'flex', alignItems: 'flex-end' },
  button: { background: 'var(--fp-accent)', border: '1px solid var(--fp-accent)', borderRadius: 6, color: 'var(--fp-accent-fg)', padding: '8px 14px', cursor: 'pointer', fontSize: 14, fontWeight: 600, whiteSpace: 'nowrap' },
  // 列表(只读 mono 行)
  rows: { display: 'grid', gap: 6 },
  row: {
    display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center',
    border: '1px solid var(--fp-border-subtle)', borderRadius: 6, padding: '7px 10px',
    color: 'var(--fp-text-2)', fontSize: 13, fontFamily: 'var(--fp-font-mono)',
  },
  muted: { color: 'var(--fp-text-3)', fontSize: 13 },
  ok: { color: 'var(--fp-ok)', fontSize: 13 },
  error: { color: 'var(--fp-err)', fontSize: 13 },
  empty: { color: 'var(--fp-text-3)', fontSize: 13, padding: '8px 2px' },
}

function updateControl(prev: BossSightControlResponse | null, item: BossSightControlItem): BossSightControlResponse | null {
  if (!prev) return prev
  return {
    ...prev,
    items: prev.items.map((existing) => existing.key === item.key ? item : existing),
    by_key: { ...prev.by_key, [item.key]: item },
  }
}

function safeTime(value?: string) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function formatEvent(event: BossSightObservationEvent) {
  const target = event.target ? ` · ${event.target}` : ''
  return `${event.dimension}${target}`
}

export default function BossSightControlCard() {
  const [controls, setControls] = useState<BossSightControlResponse | null>(null)
  const [settings, setSettings] = useState<BossSightObservabilitySettings | null>(null)
  const [prefs, setPrefs] = useState<BossSightUserPrefs | null>(null)
  const [recent, setRecent] = useState<BossSightObservationEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [allowForm, setAllowForm] = useState({ scope: 'user', tool: '', pattern: '', reason: '' })

  useEffect(() => {
    let alive = true
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const [controlRes, settingsRes, prefsRes, recentRes] = await Promise.all([
          bossSightApi.getControl(),
          bossSightApi.getObservabilitySettings(),
          bossSightApi.getUserPrefs(),
          bossSightApi.recentObservations(8),
        ])
        if (!alive) return
        setControls(controlRes)
        setSettings(settingsRes)
        setPrefs(prefsRes)
        setRecent(recentRes.items)
      } catch (e) {
        if (alive) setError(String(e))
      } finally {
        if (alive) setLoading(false)
      }
    }
    load()
    return () => { alive = false }
  }, [])

  const controlItems = useMemo(() => {
    if (!controls) return []
    return CONTROL_ORDER.map((key) => controls.by_key[key]).filter(Boolean)
  }, [controls])

  function refreshRecent() {
    bossSightApi.recentObservations(8).then((r) => setRecent(r.items)).catch((e) => setMessage(String(e)))
  }

  async function recordSettingEvent(target: string, value: unknown) {
    try {
      await bossSightApi.recordObservation({
        dimension: 'toggle_change',
        surface: 'settings',
        target,
        value,
        meta: { source: 'BossSightControlCard' },
      })
    } catch {
      // Observation must not block settings changes.
    }
  }

  async function toggleControl(item: BossSightControlItem) {
    const next = !item.value
    setBusyKey(item.key)
    setMessage(null)
    try {
      const updated = await bossSightApi.setControl(item.key, next, 'human', 'settings panel toggle')
      setControls((prev) => updateControl(prev, updated))
      await recordSettingEvent(`control:${item.key}`, next)
    } catch (e) {
      setMessage(String(e))
    } finally {
      setBusyKey(null)
    }
  }

  async function toggleDimension(dimension: BossSightObservabilityDimension) {
    if (!settings) return
    const next = !settings.dimensions[dimension]
    setBusyKey(`obs:${dimension}`)
    setMessage(null)
    try {
      const updated = await bossSightApi.setObservabilitySettings(
        { [dimension]: next },
        'human',
        'settings panel toggle',
      )
      setSettings(updated)
      await recordSettingEvent(`observability:${dimension}`, next)
    } catch (e) {
      setMessage(String(e))
    } finally {
      setBusyKey(null)
    }
  }

  async function submitPermanentAllow(e: React.FormEvent) {
    e.preventDefault()
    if (!allowForm.tool.trim()) {
      setMessage('tool 必填')
      return
    }
    setBusyKey('permanent_allow')
    setMessage(null)
    try {
      const entry = await bossSightApi.addPermanentAllow({
        scope: allowForm.scope,
        tool: allowForm.tool,
        pattern: allowForm.pattern,
        reason: allowForm.reason,
      })
      setPrefs((prev) => ({
        ...(prev || { version: 1, permanent_allow: [] }),
        permanent_allow: [...(prev?.permanent_allow || []), entry],
      }))
      setAllowForm({ scope: 'user', tool: '', pattern: '', reason: '' })
      setMessage('已写入 user_prefs.json')
      await recordSettingEvent('user_prefs:permanent_allow', { tool: entry.tool, scope: entry.scope })
    } catch (err) {
      setMessage(String(err))
    } finally {
      setBusyKey(null)
    }
  }

  // 无标题头(index 的「BOSS SIGHT」节标题已标识身份)。低频「刷新」收进 ⋯。
  return (
    <div style={S.root} data-testid="boss-sight-control-card">
      {loading && <div style={S.muted}>加载中...</div>}
      {error && <div style={S.error}>{error}</div>}

      {!loading && !error && (
        <>
          <section style={S.group}>
            <div style={S.groupHead}>
              <span style={S.groupTitle}>双控开关</span>
              <span style={S.groupHint}>永久允许只进用户偏好</span>
            </div>
            {controlItems.map((item) => {
              const copy = CONTROL_COPY[item.key] || { title: item.label || item.key, body: item.description || '' }
              return (
                <label key={item.key} style={S.toggleRow} data-testid={`control-${item.key}`}>
                  <span style={S.toggleMain}>
                    <span style={S.toggleTitleRow}>
                      <span style={S.toggleTitle}>{copy.title}</span>
                      <span style={item.value ? S.stateOn : S.stateOff}>{item.value ? '开启' : '关闭'}</span>
                    </span>
                    <span style={S.toggleBody}>{copy.body}</span>
                    <span style={S.toggleMeta}>{item.updated_by} · {safeTime(item.updated_at)}</span>
                  </span>
                  <input
                    aria-label={copy.title}
                    type="checkbox"
                    checked={item.value}
                    disabled={busyKey === item.key}
                    style={S.switch}
                    onChange={() => toggleControl(item)}
                  />
                </label>
              )
            })}
          </section>

          <section style={S.group}>
            <div style={S.groupHead}>
              <span style={S.groupTitle}>观测维度</span>
            </div>
            {DIMENSIONS.map((dimension) => {
              const copy = DIM_COPY[dimension]
              const checked = settings?.dimensions[dimension] ?? true
              return (
                <label key={dimension} style={S.toggleRow} data-testid={`observability-${dimension}`}>
                  <span style={S.toggleMain}>
                    <span style={S.toggleTitleRow}>
                      <span style={S.toggleTitle}>{copy.title}</span>
                      <span style={checked ? S.stateOn : S.stateOff}>{checked ? '记录' : '关闭'}</span>
                    </span>
                    <span style={S.toggleBody}>{copy.body}</span>
                  </span>
                  <input
                    aria-label={copy.title}
                    type="checkbox"
                    checked={checked}
                    disabled={busyKey === `obs:${dimension}`}
                    style={S.switch}
                    onChange={() => toggleDimension(dimension)}
                  />
                </label>
              )
            })}
          </section>

          <section style={S.group}>
            <div style={S.groupHead}>
              <span style={S.groupTitle}>永久允许偏好</span>
            </div>
            <form style={S.form} onSubmit={submitPermanentAllow} data-testid="permanent-allow-form">
              <label style={S.inputWrap}>
                <span style={S.label}>scope</span>
                <input
                  style={S.input}
                  value={allowForm.scope}
                  onChange={(e) => setAllowForm((f) => ({ ...f, scope: e.target.value }))}
                />
              </label>
              <label style={S.inputWrap}>
                <span style={S.label}>tool</span>
                <input
                  style={S.input}
                  value={allowForm.tool}
                  onChange={(e) => setAllowForm((f) => ({ ...f, tool: e.target.value }))}
                />
              </label>
              <label style={S.inputWrap}>
                <span style={S.label}>pattern</span>
                <input
                  style={S.input}
                  value={allowForm.pattern}
                  onChange={(e) => setAllowForm((f) => ({ ...f, pattern: e.target.value }))}
                />
              </label>
              <label style={S.inputWrap}>
                <span style={S.label}>reason</span>
                <input
                  style={S.input}
                  value={allowForm.reason}
                  onChange={(e) => setAllowForm((f) => ({ ...f, reason: e.target.value }))}
                />
              </label>
              <span style={S.submitWrap}>
                <button type="submit" style={S.button} disabled={busyKey === 'permanent_allow'}>
                  写入偏好
                </button>
              </span>
            </form>
            {message && <div style={message.includes('Error') ? S.error : S.ok}>{message}</div>}
            <div style={S.rows} data-testid="permanent-allow-list">
              {(prefs?.permanent_allow || []).length === 0 && <div style={S.empty}>暂无永久允许偏好</div>}
              {(prefs?.permanent_allow || []).slice(-4).reverse().map((entry) => (
                <div key={entry.id} style={S.row}>
                  <span>{entry.tool}{entry.pattern ? ` · ${entry.pattern}` : ''}</span>
                  <span style={S.muted}>{entry.scope}</span>
                </div>
              ))}
            </div>
          </section>

          <section style={S.group}>
            <div style={S.groupHead}>
              <span style={S.groupTitle}>最近观测</span>
              <KebabMenu testid="boss-sight-actions" items={[
                { label: '刷新观测', icon: <RefreshCw size={15} />, testid: 'boss-sight-refresh', onClick: () => refreshRecent() },
              ] as KebabItem[]} />
            </div>
            <div style={S.rows} data-testid="recent-observations">
              {recent.length === 0 && <div style={S.empty}>暂无观测事件</div>}
              {recent.map((event) => (
                <div key={event.id} style={S.row}>
                  <span>{formatEvent(event)}</span>
                  <span style={S.muted}>{safeTime(event.recorded_at)}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}

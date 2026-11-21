import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ExternalLink, RefreshCw, TerminalSquare } from 'lucide-react'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'

export interface SubscriptionWindow {
  id: string
  label: string
  used_pct?: number | null
  remaining_pct?: number | null
  remaining?: number | null
  capacity?: number | null
  unit?: string
  resets_at?: string | null
  reset_in_sec?: number | null
  observed_at?: string | null
  estimated?: boolean
  note?: string | null
}

export interface SubscriptionProvider {
  id: string
  display_name: string
  available?: boolean
  method?: string
  query_cost?: string
  reason?: string | null
  stale?: boolean
  needs_calibration?: boolean
  usage_url?: string | null
  usage_command?: string | null
  runtime_provider?: string | null
  windows: SubscriptionWindow[]
  next_reset_at?: string | null
  next_reset_in_sec?: number | null
  observed_at?: string | null
  checked_at?: string | null
  refreshing?: boolean
  refresh_error?: string | null
  stale_reason?: string | null
  account_home?: string | null
  account_fingerprint?: string | null
}

export interface SubscriptionsPayload {
  generated_at?: string
  note?: string
  sort?: string
  providers: SubscriptionProvider[]
  refreshing?: boolean
}

// Temporarily keep the quota page focused on the active subscriptions.
const VISIBLE_SUBSCRIPTION_IDS = new Set(['claude', 'claude-official-2', 'codex', 'codex-official-2'])

const CLI_ICONS: Record<string, string> = {
  claude: '/assets/cli/claudecode-color.svg',
  claude_code: '/assets/cli/claudecode-color.svg',
  claude_2: '/assets/cli/claudecode-color.svg',
  'claude-official-2': '/assets/cli/claudecode-color.svg',
  codex: '/assets/cli/codex-color.svg',
  'codex-official-2': '/assets/cli/codex-color.svg',
  'codex_the_company': '/assets/cli/the_company-color.png',
  'the_company-api': '/assets/cli/the_company-color.png',
  codebuddy: '/assets/cli/codebuddy-color.svg',
  kimi: '/assets/cli/kimi-color.svg',
  opencode: '/assets/cli/opencode.svg',
  'opencode-go': '/assets/cli/opencode.svg',
  kiro: '/assets/cli/kiro-color.svg',
  qoder: '/assets/cli/qoder-color.svg',
  'qwen-token-plan': '/assets/cli/qoder-color.svg',
  grok: '/assets/cli/grok-color.png',
}

export function providerIconSrc(id: string, runtime?: string | null): string | null {
  return CLI_ICONS[id] || (runtime ? CLI_ICONS[runtime] : null) || null
}

export function fmtReset(sec?: number | null): string {
  if (sec == null) return '\u672a\u62a5\u544a\u91cd\u7f6e\u65f6\u95f4'
  if (sec <= 0) return '即将重置'
  const hours = Math.floor(sec / 3600)
  const minutes = Math.floor((sec % 3600) / 60)
  if (hours >= 24) return `${Math.floor(hours / 24)}天${hours % 24}小时`
  if (hours >= 1) return `${hours}小时${minutes}分`
  return `${minutes}分`
}

export function usedPct(window: SubscriptionWindow): number | null {
  if (window.used_pct != null) return Math.max(0, Math.min(100, window.used_pct))
  if (window.remaining_pct != null) return Math.max(0, Math.min(100, 100 - window.remaining_pct))
  if (window.remaining != null && window.capacity) {
    return Math.max(0, Math.min(100, 100 - (100 * window.remaining) / window.capacity))
  }
  return null
}

export function remainingPct(window: SubscriptionWindow): number | null {
  if (window.remaining_pct != null) return Math.max(0, Math.min(100, window.remaining_pct))
  const used = usedPct(window)
  return used == null ? null : Math.max(0, Math.min(100, 100 - used))
}

export function usageTone(pct?: number | null): string {
  if (pct == null) return 'var(--fp-text-3)'
  if (pct >= 85) return 'var(--fp-err)'
  if (pct >= 60) return 'var(--fp-warn)'
  return 'var(--fp-ok)'
}

export function sortProviders(
  rows: SubscriptionProvider[],
  sort: 'reset_at' | 'observed_at' | 'remaining',
): SubscriptionProvider[] {
  return [...rows].sort((left, right) => {
    if (sort === 'remaining') {
      const a = left.windows.map((item) => item.remaining_pct).find((item) => item != null)
      const b = right.windows.map((item) => item.remaining_pct).find((item) => item != null)
      if (a == null && b == null) return left.id.localeCompare(right.id)
      if (a == null) return 1
      if (b == null) return -1
      return a - b
    }
    if (sort === 'observed_at') {
      const a = left.observed_at || ''
      const b = right.observed_at || ''
      if (!a && !b) return left.id.localeCompare(right.id)
      if (!a) return 1
      if (!b) return -1
      return a < b ? 1 : a > b ? -1 : 0
    }
    const a = left.next_reset_in_sec
    const b = right.next_reset_in_sec
    if (a == null && b == null) return left.id.localeCompare(right.id)
    if (a == null) return 1
    if (b == null) return -1
    return a - b
  })
}

function windowValue(window: SubscriptionWindow): string {
  if (window.unit === 'usd') {
    const spent = window.remaining ?? 0
    return `$${spent.toFixed(2)}`
  }
  const remaining = remainingPct(window)
  if (remaining != null) return `剩余 ${Math.round(remaining)}%`
  if (window.remaining != null && window.capacity != null) return `剩余 ${window.remaining}/${window.capacity}`
  if (window.remaining != null) return `剩余 ${window.remaining}${window.unit ? ` ${window.unit}` : ''}`
  return '—'
}

export default function SubscriptionsTab() {
  const [data, setData] = useState<SubscriptionsPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pollPaused, setPollPaused] = useState(false)
  const [sort, setSort] = useState<'reset_at' | 'observed_at' | 'remaining'>('reset_at')
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const active = useRef(false)
  const pending = useRef(false)
  const pollDeadline = useRef(0)

  const load = useCallback((force = false) => {
    if (pending.current) return
    if (force || !pollDeadline.current) {
      pollDeadline.current = Date.now() + 45_000
      setPollPaused(false)
    }
    clearTimeout(timer.current)
    pending.current = true
    setBusy(true)
    fetch(`/api/boss-sight/subscriptions${force ? '?force=true' : ''}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<SubscriptionsPayload>
      })
      .then((payload) => {
        if (!active.current) return
        setData(payload)
        setError(null)
        // One bounded poll at a time while individual accounts are updating.
        // Completed rows remain visible throughout a slow account's refresh.
        if (payload.refreshing && Date.now() < pollDeadline.current) {
          timer.current = setTimeout(() => load(false), 750)
        } else {
          setPollPaused(Boolean(payload.refreshing))
        }
      })
      .catch((reason) => { if (active.current) setError(String(reason)) })
      .finally(() => { pending.current = false; if (active.current) setBusy(false) })
  }, [])

  useEffect(() => {
    active.current = true
    load(false)
    return () => { active.current = false; clearTimeout(timer.current) }
  }, [load])

  const providers = useMemo(
    () => sortProviders((data?.providers || []).filter((provider) => VISIBLE_SUBSCRIPTION_IDS.has(provider.id)), sort),
    [data, sort],
  )

  const postJson = async (path: string, body: Record<string, unknown>) => {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return response.json() as Promise<Record<string, unknown>>
  }

  const menuFor = (provider: SubscriptionProvider): KebabItem[] => {
    const items: KebabItem[] = []
    if (provider.usage_url) {
      items.push({
        label: '打开用量页',
        icon: <ExternalLink size={14} />,
        onClick: () => { window.open(provider.usage_url || '', '_blank', 'noreferrer') },
      })
      items.push({
        label: '登录窗打开',
        icon: <ExternalLink size={14} />,
        onClick: () => {
          void postJson('/api/boss-sight/subscriptions/browser/open', { provider: provider.id })
            .catch((cause) => setError(`打开登录窗失败 ${cause instanceof Error ? cause.message : String(cause)}`))
        },
      })
    }
    if (provider.id === 'kiro' || provider.id === 'grok' || provider.id === 'kimi') {
      items.push({
        label: '拉 /usage',
        icon: <TerminalSquare size={14} />,
        onClick: () => {
          void postJson('/api/boss-sight/subscriptions/slash', { provider: provider.id })
            .then((payload) => {
              if (payload.ok === false) setError(String(payload.reason || '/usage 没读到'))
              load(true)
            })
            .catch((cause) => setError(`/usage 失败 ${cause instanceof Error ? cause.message : String(cause)}`))
        },
      })
    }
    return items
  }

  return (
    <div className="sub-page" data-testid="settings-subscriptions">
      <div className="sub-toolbar">
        <span className="v2-seg" role="radiogroup" aria-label="订阅排序">
          {([
            ['reset_at', '更新'],
            ['remaining', '剩余'],
            ['observed_at', '最近'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={sort === value}
              className={`seg-i${sort === value ? ' on' : ''}`}
              onClick={() => setSort(value)}
            >
              {label}
            </button>
          ))}
        </span>
        <button
          type="button"
          className="st-btn"
          data-testid="subscriptions-refresh"
          disabled={busy}
          onClick={() => load(true)}
        >
          <RefreshCw size={14} aria-hidden="true" />
          {busy ? '刷新中' : '刷新'}
        </button>
      </div>
      {error && <div className="st-err" data-testid="subscriptions-error">{error}</div>}
      {pollPaused && <div role="status" data-testid="subscriptions-poll-paused">部分账号仍在后台读取，已暂停自动更新。已有读数保留，可稍后刷新。</div>}
      {!data && !error && <div className="sub-empty">正在读取…</div>}
      <div className="sub-grid">
        {providers.map((provider) => {
          const icon = providerIconSrc(provider.id, provider.runtime_provider)
          return (
            <article key={provider.id} className="sub-card" data-testid={`subscription-card-${provider.id}`}>
              <header className="sub-card-head">
                <span className="sub-ico" aria-hidden="true">
                  {icon
                    ? <img src={icon} alt="" width={22} height={22} draggable={false} />
                    : <span className="sub-ico-fallback">{provider.display_name.slice(0, 1)}</span>}
                </span>
                <span className="sub-name">{provider.display_name}</span>
                <KebabMenu items={menuFor(provider)} testid={`subscription-more-${provider.id}`} />
              </header>
              {provider.account_home && <small className="sub-win-reset">{provider.account_home} · {provider.account_fingerprint}</small>}
              <div className="sub-bars">
                {provider.windows.length === 0 && (
                  <div className="sub-win">
                    <div className="sub-win-meta">
                      <span className="sub-win-val">—</span>
                    </div>
                    <div className="sub-track" aria-hidden="true"><i style={{ width: '0%' }} /></div>
                  </div>
                )}
                {provider.windows.map((window) => {
                  const pct = usedPct(window)
                  const tone = usageTone(pct)
                  return (
                    <div key={window.id} className="sub-win">
                      <div className="sub-win-meta">
                        <span>{window.label}</span>
                        <span className="sub-win-val" style={{ color: tone }}>{windowValue(window)}</span>
                        <span className="sub-win-reset">↻ {fmtReset(window.reset_in_sec)}</span>
                      </div>
                      <div className="sub-track" aria-label={`${window.label} 已用 ${pct ?? 0}%`}>
                        <i style={{ width: `${pct ?? 0}%`, background: tone }} />
                      </div>
                    </div>
                  )
                })}
              </div>
              <div className="sub-win-reset" data-testid={`subscription-status-${provider.id}`} role="status">
                {provider.refreshing ? '正在刷新；' : ''}
                {provider.observed_at
                  ? `${provider.stale ? '上次读数' : '读取于'} ${new Date(provider.observed_at).toLocaleString()}`
                  : '尚无成功读数'}
                {(provider.refresh_error || provider.stale_reason || (!provider.available && provider.reason)) && (
                  <div>{provider.refresh_error || provider.stale_reason || provider.reason}</div>
                )}
              </div>
            </article>
          )
        })}
      </div>
    </div>
  )
}

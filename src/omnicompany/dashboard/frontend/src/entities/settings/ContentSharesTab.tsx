import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Clipboard, RefreshCw, RotateCcw } from 'lucide-react'
import { copyText } from '../../lib/copyText'

type ShareStatus = 'reserved' | 'capturing' | 'ready' | 'failed' | 'unavailable' | 'revoked' | 'expired'

interface ContentShare {
  share_id: string
  title: string
  type: string
  status: ShareStatus
  canonical_url: string
  updated_at: string
  producer: {
    product: string
    adapter?: string
  }
  aliases?: string[]
  error?: { message?: string } | null
}

interface ContentShareListResponse {
  items: ContentShare[]
  count: number
}

const API_ROOT = '/api/content-share/v1/shares'

const STATUS_LABELS: Record<ShareStatus, string> = {
  reserved: '已预留',
  capturing: '生成中',
  ready: '可用',
  failed: '失败',
  unavailable: '暂不可用',
  revoked: '已撤销',
  expired: '已过期',
}

function formatUpdatedAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value || '未知'
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

async function responseMessage(response: Response, action: string): Promise<string> {
  try {
    const payload = await response.json() as { detail?: { message?: string } | string }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message
    if (detail) return `${action}失败：${detail}`
  } catch { /* 使用稳定的 HTTP 回执 */ }
  return `${action}失败（HTTP ${response.status}）`
}

export default function ContentSharesTab() {
  const [shares, setShares] = useState<ContentShare[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [copyNotice, setCopyNotice] = useState<{ shareId: string; ok: boolean } | null>(null)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [ageFilter, setAgeFilter] = useState('all')

  const loadShares = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(API_ROOT)
      if (!response.ok) throw new Error(await responseMessage(response, '读取'))
      const payload = await response.json() as ContentShareListResponse
      const items = Array.isArray(payload.items) ? payload.items : []
      setShares(items)
      setTotal(Number.isFinite(payload.count) ? payload.count : items.length)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取内容分享失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadShares()
  }, [loadShares])

  const filteredShares = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const now = Date.now()
    const maxAge = ageFilter === '1d' ? 86_400_000 : ageFilter === '7d' ? 604_800_000 : ageFilter === '30d' ? 2_592_000_000 : null
    return shares.filter((share) => {
      if (typeFilter !== 'all' && share.type !== typeFilter) return false
      if (statusFilter !== 'all' && share.status !== statusFilter) return false
      if (maxAge !== null) {
        const updatedAt = new Date(share.updated_at).getTime()
        if (!Number.isFinite(updatedAt) || now - updatedAt > maxAge) return false
      }
      if (!needle) return true
      return [share.share_id, share.title, share.producer?.product, share.producer?.adapter, ...(share.aliases ?? [])]
        .some((value) => String(value ?? '').toLowerCase().includes(needle))
    })
  }, [ageFilter, query, shares, statusFilter, typeFilter])

  const copyUrl = async (share: ContentShare) => {
    const ok = await copyText(share.canonical_url)
    setCopyNotice({ shareId: share.share_id, ok })
  }

  const revoke = async (share: ContentShare) => {
    setRevoking(share.share_id)
    setError(null)
    try {
      const response = await fetch(`${API_ROOT}/${encodeURIComponent(share.share_id)}/revoke`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error(await responseMessage(response, '撤销'))
      await loadShares()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '撤销内容分享失败')
    } finally {
      setRevoking(null)
    }
  }

  return (
    <div data-testid="content-shares-tab" style={{ display: 'grid', gap: 14 }}>
      <div className="st-sec">内容分享</div>
      <section className="st-group">
        <div className="st-gr">
          <span className="st-row-t">
            <span className="t1">统一分享记录</span>
            <span className="t2" data-testid="content-shares-total">
              {loading && shares.length === 0 ? '正在读取…' : `显示 ${filteredShares.length.toLocaleString()} / 共 ${total.toLocaleString()} 项`}
            </span>
          </span>
          <button
            type="button"
            className="st-btn"
            data-testid="content-shares-refresh"
            disabled={loading}
            onClick={() => { void loadShares() }}
          >
            <RefreshCw size={14} aria-hidden="true" />
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>
      </section>

      <section className="st-group">
        <div className="st-gr" style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) repeat(3, minmax(120px, auto))', gap: 8 }}>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索标题、Share ID、产品或别名"
            aria-label="搜索内容分享"
            style={{ minWidth: 0, border: '1px solid var(--fp-border)', borderRadius: 6, background: 'var(--fp-solid)', color: 'var(--fp-text)', padding: '7px 9px' }}
          />
          <select aria-label="按类型筛选" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="all">全部类型</option>
            {['ui', 'pg', 'tm', 'sn', 'cd', 'cv', 'wt', 'mt'].map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <select aria-label="按状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">全部状态</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select aria-label="按更新时间筛选" value={ageFilter} onChange={(event) => setAgeFilter(event.target.value)}>
            <option value="all">全部时间</option>
            <option value="1d">最近 24 小时</option>
            <option value="7d">最近 7 天</option>
            <option value="30d">最近 30 天</option>
          </select>
        </div>
      </section>

      {error && (
        <div className="st-node-message error" role="alert" data-testid="content-shares-error">
          {error}
        </div>
      )}

      {!loading && filteredShares.length === 0 && !error && (
        <section className="st-group" data-testid="content-shares-empty">
          <div className="st-gr">
            <span className="st-row-t">
              <span className="t1">{shares.length === 0 ? '还没有内容分享' : '没有符合筛选条件的分享'}</span>
              <span className="t2">{shares.length === 0 ? '产品创建分享后，会在这里统一显示和管理。' : '调整搜索词或筛选条件后再试。'}</span>
            </span>
          </div>
        </section>
      )}

      {filteredShares.map((share) => {
        const copyState = copyNotice?.shareId === share.share_id ? copyNotice : null
        const isRevoking = revoking === share.share_id
        return (
          <section className="st-group" key={share.share_id} data-testid={`content-share-${share.share_id}`}>
            <div className="st-gr" style={{ alignItems: 'flex-start' }}>
              <span className="st-row-t" style={{ gap: 9 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span className="t1">{share.title}</span>
                  <span className={`st-status ${share.status === 'ready' ? 'ready' : 'planned'}`}>
                    {STATUS_LABELS[share.status] ?? share.status}
                  </span>
                </span>
                <span
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                    gap: '7px 16px',
                    color: 'var(--fp-text-2)',
                    fontFamily: 'var(--fp-font-mono)',
                    fontSize: 'var(--fp-fs-4)',
                  }}
                >
                  <span><b style={{ color: 'var(--fp-text-3)', fontWeight: 500 }}>Share ID</b><br />{share.share_id}</span>
                  <span><b style={{ color: 'var(--fp-text-3)', fontWeight: 500 }}>产品</b><br />{share.producer?.product || '未知'}</span>
                  <span><b style={{ color: 'var(--fp-text-3)', fontWeight: 500 }}>出口</b><br />{share.producer?.adapter || '未知'}</span>
                  <span><b style={{ color: 'var(--fp-text-3)', fontWeight: 500 }}>类型</b><br />{share.type}</span>
                  <span><b style={{ color: 'var(--fp-text-3)', fontWeight: 500 }}>更新时间</b><br />{formatUpdatedAt(share.updated_at)}</span>
                </span>
                {!!share.aliases?.length && <span className="t2">旧标识：{share.aliases.join(' · ')}</span>}
                {!!share.error?.message && <span className="st-err">{share.error.message}</span>}
                <a
                  href={share.canonical_url}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: 'var(--fp-accent)', fontFamily: 'var(--fp-font-mono)', fontSize: 'var(--fp-fs-4)', overflowWrap: 'anywhere' }}
                >
                  {share.canonical_url}
                </a>
                {copyState && (
                  <span className={copyState.ok ? 'st-ok' : 'st-err'} role="status">
                    {copyState.ok ? '链接已复制' : '复制失败，请手动选择链接'}
                  </span>
                )}
              </span>
              <span className="st-node-actions" style={{ flex: '0 0 auto' }}>
                <button type="button" className="st-btn" onClick={() => { void copyUrl(share) }}>
                  <Clipboard size={14} aria-hidden="true" />
                  复制链接
                </button>
                {share.status !== 'revoked' && (
                  <button
                    type="button"
                    className="st-btn danger"
                    disabled={isRevoking}
                    onClick={() => { void revoke(share) }}
                  >
                    <RotateCcw size={14} aria-hidden="true" />
                    {isRevoking ? '撤销中…' : '撤销'}
                  </button>
                )}
              </span>
            </div>
          </section>
        )
      })}
    </div>
  )
}

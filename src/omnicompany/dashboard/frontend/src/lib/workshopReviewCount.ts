// 左侧「审阅」角标的数字来源 —— Workshop 的审阅收件箱。
// 审阅归 Workshop 管, 驾驶舱不再自己维护待审队列, 只读这一个数。
import { useEffect, useState } from 'react'

const INBOX_URL = '/bside/api/review/inbox'
const REFRESH_MS = 60_000

interface ReviewInbox {
  unread?: unknown[]
  own_pending?: unknown[]
}

/** 等我处理的条数 = 指派给我的待审 + 未读提审。读不到(未登录、Workshop 没起)按 0 算。 */
export async function fetchWorkshopReviewCount(fetchImpl: typeof fetch = fetch): Promise<number> {
  try {
    const response = await fetchImpl(INBOX_URL, { credentials: 'same-origin', headers: { accept: 'application/json' } })
    if (!response.ok) return 0
    const inbox = await response.json() as ReviewInbox
    const count = (list: unknown) => (Array.isArray(list) ? list.length : 0)
    return count(inbox.unread) + count(inbox.own_pending)
  } catch {
    return 0
  }
}

export function useWorkshopReviewCount(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let alive = true
    const refresh = () => {
      if (document.visibilityState === 'hidden') return
      void fetchWorkshopReviewCount().then((next) => { if (alive) setCount(next) })
    }
    refresh()
    const timer = window.setInterval(refresh, REFRESH_MS)
    // 从 Workshop 页签处理完切回来时立刻刷新, 不等下一轮。
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    return () => {
      alive = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
    }
  }, [])
  return count
}

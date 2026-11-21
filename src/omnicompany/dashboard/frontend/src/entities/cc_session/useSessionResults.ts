// 会话产物数据: 挂上时取一次, 工作中每 8 秒刷新(会话一边干一边出的东西也能马上看到), 一轮结束再取一次。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchTurnResults, type TurnResults } from '../../lib/turnResults'
import { useCcTabRunState } from './tabStatus'
import { buildResultItems, type ResultItem } from './resultItems'

const WORKING_POLL_MS = 8_000

export function useSessionResults(sessionId: string, scope: 'turn' | 'session' = 'session') {
  const runState = useCcTabRunState(sessionId, 'pty')
  const [results, setResults] = useState<TurnResults | null>(null)
  const [loading, setLoading] = useState(false)
  const inflight = useRef<AbortController | null>(null)

  const refresh = useCallback(async () => {
    if (!sessionId) return
    inflight.current?.abort()
    const controller = new AbortController()
    inflight.current = controller
    setLoading(true)
    try {
      setResults(await fetchTurnResults(sessionId, controller.signal, scope))
    } catch {
      // 读不到就保持上一次的结果
    } finally {
      if (inflight.current === controller) setLoading(false)
    }
  }, [scope, sessionId])

  useEffect(() => {
    setResults(null)
    void refresh()
    return () => inflight.current?.abort()
  }, [refresh])

  const previous = useRef(runState)
  useEffect(() => {
    const before = previous.current
    previous.current = runState
    if (before === 'working' && runState !== 'working') void refresh()
  }, [refresh, runState])

  useEffect(() => {
    if (runState !== 'working') return undefined
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, WORKING_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh, runState])

  const items: ResultItem[] = useMemo(() => buildResultItems(results), [results])
  return { results, items, loading, refresh }
}

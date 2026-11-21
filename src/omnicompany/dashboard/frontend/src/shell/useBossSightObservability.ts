import { useEffect, useRef } from 'react'
import { bossSightApi, type BossSightObservabilityDimension } from '../api/bossSightClient'

function targetLabel(target: EventTarget | null): string {
  if (!(target instanceof Element)) return 'unknown'
  const explicit = target.getAttribute('data-testid') || target.getAttribute('aria-label') || target.id
  if (explicit) return explicit.slice(0, 160)
  const role = target.getAttribute('role')
  const text = (target.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80)
  const tag = target.tagName.toLowerCase()
  return [tag, role, text].filter(Boolean).join(':').slice(0, 160) || tag
}

function changedValue(target: EventTarget | null): unknown {
  if (target instanceof HTMLInputElement) {
    if (target.type === 'checkbox' || target.type === 'radio') return target.checked
    return target.value.slice(0, 160)
  }
  if (target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement) {
    return target.value.slice(0, 160)
  }
  return null
}

function sendObservation(
  dimension: BossSightObservabilityDimension,
  surface: string,
  target: string | null,
  value: unknown,
  meta: Record<string, unknown> = {},
) {
  bossSightApi.recordObservation({ dimension, surface, target, value, meta }).catch(() => {
    // Observability is diagnostic only and must not affect the UI.
  })
}

export function useBossSightObservability(surface: string) {
  const mountedAt = useRef(Date.now())

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      sendObservation('click', surface, targetLabel(event.target), null, {
        path: window.location.pathname,
      })
    }

    const onChange = (event: Event) => {
      sendObservation('toggle_change', surface, targetLabel(event.target), changedValue(event.target), {
        path: window.location.pathname,
      })
    }

    const onSelection = () => {
      const text = window.getSelection?.()?.toString().trim()
      if (!text || text.length < 2) return
      sendObservation('selection', surface, document.activeElement ? targetLabel(document.activeElement) : null, text.slice(0, 500), {
        path: window.location.pathname,
      })
    }

    const sendDwell = (reason: string) => {
      sendObservation('view_dwell', surface, window.location.pathname, {
        ms: Date.now() - mountedAt.current,
        visibility: document.visibilityState,
        reason,
      })
    }

    const onVisibility = () => {
      if (document.visibilityState === 'hidden') sendDwell('hidden')
    }

    window.addEventListener('click', onClick, true)
    window.addEventListener('change', onChange, true)
    window.addEventListener('mouseup', onSelection, true)
    document.addEventListener('selectionchange', onSelection)
    document.addEventListener('visibilitychange', onVisibility)
    const interval = window.setInterval(() => sendDwell('interval'), 30000)

    return () => {
      window.removeEventListener('click', onClick, true)
      window.removeEventListener('change', onChange, true)
      window.removeEventListener('mouseup', onSelection, true)
      document.removeEventListener('selectionchange', onSelection)
      document.removeEventListener('visibilitychange', onVisibility)
      window.clearInterval(interval)
      sendDwell('unmount')
    }
  }, [surface])
}

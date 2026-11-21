// useDraft —— 把「写了一半不许丢」接到一个正在编辑的表单上。
//
// 契约(与 draftStore 的三条纪律对应):
//   · 只要内容和服务端那份不一致, 就 debounce 落本地, 不等保存;
//   · 只有调用方明确 markSynced() 才清本地 —— 保存失败、页面被关都不清;
//   · 重开时若本地那份和当前不一样, **摆出来让人选**, 绝不自动覆盖当前内容。
//
// 自动覆盖为什么不行: 你可能在别处已经把这条改好了, 这时候拿一份旧草稿盖上去,
// 是把"丢一半"换成"丢全部", 更糟。

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  clearUnsynced, deleteDraft, getDraft, markUnsynced, putDraft, putDraftSync,
} from './draftStore'

export interface UseDraftOptions<T> {
  /** 稳定标识; null = 本次不启用(还没选中东西时)。 */
  key: string | null
  /** 人读名字, 出现在恢复提示里。 */
  label: string
  /** 当前正在编辑的值。 */
  value: T
  /** 与服务端那份一致时为 true —— 干净就不该留草稿。 */
  clean: boolean
  /** 最坏丢失窗口。硬杀进程没有任何钩子能跑, 这个值就是上限。 */
  debounceMs?: number
}

export interface DraftHandle<T> {
  /** 挂载时发现的、与当前不同的本地草稿。null = 没有可恢复的。 */
  recovered: T | null
  /** 调用方已经把值 set 回去了 —— 收起提示。 */
  acceptRecovered: () => void
  /** 丢弃本地草稿并收起提示。 */
  discardRecovered: () => void
  /** 服务端确认写入成功。**唯一会清本地的入口。** */
  markSynced: () => void
  /** 最近一次落本地的时间, 给"已保存草稿"的轻提示用。 */
  savedAt: number | null
}

function serialize(value: unknown): string {
  try {
    return JSON.stringify(value) ?? ''
  } catch {
    return ''
  }
}

export function useDraft<T>(options: UseDraftOptions<T>): DraftHandle<T> {
  const { key, label, value, clean, debounceMs = 600 } = options
  const [recovered, setRecovered] = useState<T | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  // 探过本地之前一律不落盘也不清盘。少了这道闸, 一个"干净"的表单刚挂载就会把
  // 崩溃前留下的草稿当成多余的删掉 —— 恰恰是最该保住的那一份(实测抓到过)。
  const [probed, setProbed] = useState(false)

  const serialized = serialize(value)
  // unload 那一刻要写的是"最新的值", 不是闭包里那个 —— 用 ref 兜住。
  const latest = useRef({ key, label, value, clean, serialized })
  latest.current = { key, label, value, clean, serialized }

  // 换 key = 换编辑对象: 重新探一次本地有没有更新的那份。
  useEffect(() => {
    setProbed(false)
    setRecovered(null)
    if (!key) { setProbed(true); return }
    let alive = true
    void getDraft<T>(key).then((rec) => {
      if (!alive) return
      // 和当前一样就没什么可"恢复"的, 别拿一模一样的内容去打扰人。
      if (rec && serialize(rec.value) !== latest.current.serialized) setRecovered(rec.value)
      setProbed(true)
    })
    return () => { alive = false }
  }, [key])

  // 落盘。clean 时反过来清 —— 服务端已经有了, 本地再留一份只会在下次误报可恢复。
  useEffect(() => {
    if (!key || !probed) return
    if (clean) {
      // 还没决定要不要恢复时不许删 —— 用户正看着那个提示。
      if (recovered != null) return
      clearUnsynced(key)
      void deleteDraft(key)
      setSavedAt(null)
      return
    }
    markUnsynced(key, label)
    const timer = window.setTimeout(() => {
      void putDraft(key, label, value).then((rec) => setSavedAt(rec.updatedAt))
    }, debounceMs)
    return () => window.clearTimeout(timer)
  }, [key, label, clean, serialized, debounceMs, probed, recovered])

  // 关页面 / 切后台时同步刷一次: IndexedDB 是异步的, unload 阶段不保证写得完。
  useEffect(() => {
    if (typeof window === 'undefined') return
    const flush = () => {
      const cur = latest.current
      if (!cur.key || cur.clean) return
      putDraftSync(cur.key, cur.label, cur.value)
    }
    const onHide = () => { if (document.visibilityState === 'hidden') flush() }
    window.addEventListener('pagehide', flush)
    document.addEventListener('visibilitychange', onHide)
    return () => {
      window.removeEventListener('pagehide', flush)
      document.removeEventListener('visibilitychange', onHide)
    }
  }, [])

  // 卸载只注销 unload 拦截, **不删草稿** —— 关掉抽屉不等于放弃内容。
  useEffect(() => () => { if (key) clearUnsynced(key) }, [key])

  const acceptRecovered = useCallback(() => setRecovered(null), [])

  const discardRecovered = useCallback(() => {
    setRecovered(null)
    if (key) { void deleteDraft(key); clearUnsynced(key) }
  }, [key])

  const markSynced = useCallback(() => {
    if (!key) return
    clearUnsynced(key)
    void deleteDraft(key)
    setSavedAt(null)
    setRecovered(null)
  }, [key])

  return { recovered, acceptRecovered, discardRecovered, markSynced, savedAt }
}

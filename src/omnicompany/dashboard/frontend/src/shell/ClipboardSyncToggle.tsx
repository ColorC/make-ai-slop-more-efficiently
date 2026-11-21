import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ClipboardCopy, ClipboardCheck, ClipboardX } from 'lucide-react'
import { fetchLatestWorkstationClip } from '../api/overlayClient'
import { desktopHostBridge } from '../lib/workspaceNavigation'
import { copyTextToClipboard } from '../utils/clipboard'

/**
 * 远程剪切板同步开关（状态栏）。
 *
 * 开着的时候每 1.5s 轮询工作站剪贴板"最新一条"；只同步**开关打开之后**新增的条目——
 * 打开瞬间的存量不搬运（历史里几乎必然有密码/token，这是刻意的安全边界，别改成回放历史）。
 * 桌面壳（0.1.29+ 有 writeClipboard 桥）自动写入本机剪贴板；纯网页处于 8210 非安全上下文，
 * navigator.clipboard 不存在，只能靠真实点击手势走 execCommand——所以网页版是"提示 + 点一下复制"。
 */

const POLL_MS = 1500
const STORAGE_KEY = 'omni.clipboardSync.enabled'

type SyncState =
  | { phase: 'idle' }
  | { phase: 'synced'; at: number }
  | { phase: 'pending'; text: string; preview: string }
  | { phase: 'error'; message: string }

function preview(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > 24 ? `${flat.slice(0, 24)}…` : flat
}

export default function ClipboardSyncToggle() {
  const [enabled, setEnabled] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1'
    } catch {
      return false
    }
  })
  const [state, setState] = useState<SyncState>({ phase: 'idle' })
  // 打开开关后第一次成功轮询只记 id 不搬运，之后 id 变了才是"新增"。
  const lastIdRef = useRef<string | null>(null)
  const primedRef = useRef(false)

  const applyText = useCallback(async (text: string) => {
    const bridge = desktopHostBridge()
    if (bridge?.writeClipboard) {
      await bridge.writeClipboard(text)
      setState({ phase: 'synced', at: Date.now() })
      return
    }
    // 网页版：非安全上下文写剪贴板需要真实用户手势，挂到按钮上等用户点。
    setState({ phase: 'pending', text, preview: preview(text) })
  }, [])

  useEffect(() => {
    if (!enabled) {
      lastIdRef.current = null
      primedRef.current = false
      setState({ phase: 'idle' })
      return
    }
    let stopped = false
    const controller = new AbortController()
    const tick = async () => {
      try {
        const clip = await fetchLatestWorkstationClip(controller.signal)
        if (stopped || !clip) return
        if (!primedRef.current) {
          primedRef.current = true
          lastIdRef.current = clip.id
          return
        }
        if (clip.id === lastIdRef.current) return
        lastIdRef.current = clip.id
        if (clip.kind === 'text' && clip.text) {
          await applyText(clip.text)
        }
      } catch (error) {
        if (stopped) return
        setState({ phase: 'error', message: error instanceof Error ? error.message : String(error) })
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), POLL_MS)
    return () => {
      stopped = true
      controller.abort()
      window.clearInterval(timer)
    }
  }, [enabled, applyText])

  const toggle = () => {
    const next = !enabled
    setEnabled(next)
    try {
      localStorage.setItem(STORAGE_KEY, next ? '1' : '0')
    } catch {
      // localStorage 不可用时开关只在本页生效，也够用。
    }
  }

  const confirmPending = async () => {
    if (state.phase !== 'pending') return
    const copied = await copyTextToClipboard(state.text)
    setState(copied ? { phase: 'synced', at: Date.now() } : { phase: 'error', message: '复制失败，请重试' })
  }

  const Icon = state.phase === 'error' ? ClipboardX : state.phase === 'synced' ? ClipboardCheck : ClipboardCopy
  const label =
    state.phase === 'error' ? '剪切板同步异常'
      : state.phase === 'synced' ? '剪切板已同步'
        : enabled ? '剪切板同步中' : '剪切板同步'

  const buttonStyle: React.CSSProperties = {
    height: 18,
    border: 'none',
    borderRadius: 3,
    background: enabled ? 'rgba(144,202,249,.16)' : 'transparent',
    color: state.phase === 'error' ? 'var(--fp-danger, #e57373)' : enabled ? 'var(--fp-link)' : 'var(--fp-text-2)',
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '0 6px',
    fontSize: 14,
    fontFamily: 'Consolas, Menlo, monospace',
  }

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      {state.phase === 'pending' && (
        <button
          type="button"
          title={`工作站有新复制内容：${state.preview}（点击写入本机剪贴板）`}
          style={{ ...buttonStyle, background: 'rgba(255,183,77,.18)', color: 'var(--fp-warn, #ffb74d)' }}
          onClick={() => void confirmPending()}
        >
          <ClipboardCopy size={13} strokeWidth={1.8} />
          <span>复制：{state.preview}</span>
        </button>
      )}
      <button
        type="button"
        title={
          enabled
            ? '远程剪切板同步已开启：工作站复制的文本会同步到这台机器（点击关闭）'
            : '开启远程剪切板同步：工作站上复制的文本自动出现在这台机器的剪贴板（仅同步开启后的新增）'
        }
        aria-label={label}
        aria-pressed={enabled}
        style={buttonStyle}
        onClick={toggle}
      >
        <Icon size={13} strokeWidth={1.8} />
        <span>{label}</span>
      </button>
    </span>
  )
}

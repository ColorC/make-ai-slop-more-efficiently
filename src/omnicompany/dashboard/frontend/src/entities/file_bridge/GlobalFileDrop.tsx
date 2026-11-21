import React, { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, HardDrive, LoaderCircle, UploadCloud } from 'lucide-react'
import { fileBridgeApi, type FileBridgeUploadResult } from '../../api/fileBridgeClient'
import { copyText } from '../../lib/copyText'
import type { Surface } from '../../lib/surface'
import { usePanels } from '../../stores/panelsStore'
import './globalFileDrop.css'

export const FILE_BRIDGE_UPLOAD_EVENT = 'omni:file-bridge-uploaded'

type Notice = {
  kind: 'ok' | 'error'
  text: string
} | null

function transferHasFiles(transfer: DataTransfer | null): boolean {
  return !!transfer && Array.from(transfer.types || []).includes('Files')
}

export function pastedFiles(clipboard: DataTransfer | null): File[] {
  if (!clipboard) return []
  return Array.from(clipboard.files || [])
}

function fullDashboardFileBridgeUrl(): string {
  const url = new URL(window.location.href)
  url.search = ''
  url.hash = ''
  url.searchParams.set('open', 'file_bridge')
  return url.toString()
}

export function openFileBridgeTool(surface: Surface): void {
  if (surface === 'full') {
    usePanels.getState().openTab({ type: 'file_bridge', id: 'main' }, 'Agent 暂存区')
    return
  }
  window.open(fullDashboardFileBridgeUrl(), '_blank', 'noopener')
}

export default function GlobalFileDrop({ surface }: { surface: Surface }) {
  const dragDepth = useRef(0)
  const busyRef = useRef(false)
  const noticeTimer = useRef<number | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState<Notice>(null)

  const showNotice = useCallback((next: Notice) => {
    if (noticeTimer.current != null) window.clearTimeout(noticeTimer.current)
    setNotice(next)
    if (next) {
      noticeTimer.current = window.setTimeout(() => {
        setNotice(null)
        noticeTimer.current = null
      }, next.kind === 'error' ? 12000 : 8000)
    }
  }, [])

  const receive = useCallback(async (files: File[]) => {
    if (files.length === 0 || busyRef.current) return
    busyRef.current = true
    setUploading(true)
    showNotice(null)
    try {
      const result = await fileBridgeApi.upload(files)
      const paths = result.items.map((item) => item.path)
      const copied = await copyText(paths.join('\n'))
      window.dispatchEvent(new CustomEvent<FileBridgeUploadResult>(
        FILE_BRIDGE_UPLOAD_EVENT,
        { detail: result },
      ))
      showNotice({
        kind: 'ok',
        text: copied
          ? `已上传 ${paths.length} 个文件，并复制远端绝对路径`
          : `已上传 ${paths.length} 个文件；浏览器拒绝自动复制，请在暂存区复制路径`,
      })
    } catch (reason) {
      showNotice({
        kind: 'error',
        text: reason instanceof Error ? reason.message : String(reason),
      })
    } finally {
      busyRef.current = false
      setUploading(false)
    }
  }, [showNotice])

  useEffect(() => {
    const resetDrag = () => {
      dragDepth.current = 0
      setDragging(false)
    }
    const onDragEnter = (event: DragEvent) => {
      if (!transferHasFiles(event.dataTransfer)) return
      event.preventDefault()
      dragDepth.current += 1
      setDragging(true)
    }
    const onDragOver = (event: DragEvent) => {
      if (!transferHasFiles(event.dataTransfer)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    }
    const onDragLeave = (event: DragEvent) => {
      if (!transferHasFiles(event.dataTransfer)) return
      event.preventDefault()
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (dragDepth.current === 0) setDragging(false)
    }
    const onDrop = (event: DragEvent) => {
      if (!transferHasFiles(event.dataTransfer)) return
      // The dedicated picker area has its own drop handler. Respect it so a
      // file dropped there is not uploaded once locally and once globally.
      if (event.defaultPrevented) {
        resetDrag()
        return
      }
      event.preventDefault()
      const files = Array.from(event.dataTransfer?.files || [])
      resetDrag()
      void receive(files)
    }
    const onPaste = (event: ClipboardEvent) => {
      const files = pastedFiles(event.clipboardData)
      if (files.length === 0) return
      event.preventDefault()
      void receive(files)
    }

    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('drop', onDrop)
    window.addEventListener('paste', onPaste)
    window.addEventListener('blur', resetDrag)
    return () => {
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('drop', onDrop)
      window.removeEventListener('paste', onPaste)
      window.removeEventListener('blur', resetDrag)
      if (noticeTimer.current != null) window.clearTimeout(noticeTimer.current)
    }
  }, [receive])

  return (
    <>
      {(dragging || uploading) && (
        <div
          className={`gfd-overlay${uploading ? ' is-uploading' : ''}`}
          data-testid="global-file-drop-overlay"
          data-omni-capture-ignore="true"
          role="status"
          aria-live="polite"
        >
          <div className="gfd-target">
            {uploading
              ? <LoaderCircle size={28} className="gfd-spin" />
              : <UploadCloud size={28} />}
            <strong>{uploading ? '正在写入 Agent 暂存区' : '松开即可上传'}</strong>
            <span>任何 Dashboard 页面都可以投递；完成后自动复制远端绝对路径</span>
          </div>
        </div>
      )}
      {notice && (
        <aside
          className={`gfd-notice is-${notice.kind}`}
          data-testid="global-file-drop-notice"
          data-omni-capture-ignore="true"
          role="status"
        >
          {notice.kind === 'ok' ? <Check size={17} /> : <AlertTriangle size={17} />}
          <span>{notice.text}</span>
          <button type="button" onClick={() => openFileBridgeTool(surface)}>
            <HardDrive size={14} />暂存区
          </button>
        </aside>
      )}
    </>
  )
}

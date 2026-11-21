// L3 详情抽屉 —— 旧界面把编辑器插在列表中间, 列表被顶跳; 这里改成停靠/覆盖两形态。
//
// 容器 ≥781px: 右侧停靠列, 列表不动。
// 容器 <781px: 底部覆盖抽屉 + 遮罩, Esc / 点遮罩关闭。
// 两形态同一组件, 只由 task.css 的容器查询切换。

import React, { useEffect, useMemo, useState } from 'react'
import { Archive, Link2, X } from 'lucide-react'
import { BlueprintSelect } from '../../components/BlueprintSelect'
import { useDraft } from '../../lib/useDraft'
import { StateMark } from './TaskItem'
import {
  type BusTask, type TaskDetail, STATE_LABEL, UNGROUPED, UNRATED, dueLabel, stateLabel,
  taskProfile, tierOf, tiers,
} from './taskBus'

const STATE_OPTIONS = Object.keys(STATE_LABEL).map((value) => ({ value, label: STATE_LABEL[value] }))

/** 阶梯词表在服务端（task-core §10 个人附加层），前端不留第二份。 */
function priorityOptions() {
  return [
    { value: '', label: taskProfile()?.unratedLabel || UNRATED },
    // 用短标: 长名是判据, 判据在下面那行单独写, 不塞进选项。
    ...tiers().map((t) => ({ value: String(t.priority), label: t.short || t.name })),
  ]
}

function toDateInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
}

function fromDateInput(value: string): string | null {
  if (!value) return null
  const [y, m, d] = value.split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d, 23, 59, 0, 0).toISOString()
}

export interface SheetPatch {
  title: string
  description?: string
  priority?: number | null
  dueAt?: string | null
  projectId?: string | null
}

export interface TaskSheetProps {
  task: BusTask
  detail: TaskDetail | null
  projects: { id: string; label: string }[]
  sessionId?: string
  busy: boolean
  error: string | null
  onClose: () => void
  onDirtyChange: (dirty: boolean) => void
  /** 只带真正改过的字段 —— 把没动过的一并发出去，会让旧宿主因为不认识某个字段
   *  而把整条更新拒掉（实证：带上 projectId 时连改标题都会失败）。 */
  onSave: (patch: SheetPatch) => Promise<boolean>
  onStatus: (stateKey: string) => void
  onArchive: () => void
  onBindConversation: () => void
}

export function TaskSheet(props: TaskSheetProps) {
  const { task, detail, sessionId, busy, error } = props
  const [title, setTitle] = useState(task.title)
  const [description, setDescription] = useState(task.description || '')
  const [priority, setPriority] = useState(task.priority == null ? '' : String(task.priority))
  const [due, setDue] = useState(toDateInput(task.dueAt))
  const [projectId, setProjectId] = useState(task.projectId ?? UNGROUPED)

  // 换一条任务 = 重置草稿(不跨任务串草稿)。
  useEffect(() => {
    setTitle(task.title)
    setDescription(task.description || '')
    setPriority(task.priority == null ? '' : String(task.priority))
    setDue(toDateInput(task.dueAt))
    setProjectId(task.projectId ?? UNGROUPED)
  }, [task.id, task.revision, task.title, task.description, task.priority, task.dueAt, task.projectId])

  const dirty = useMemo(() => (
    title !== task.title
    || description !== (task.description || '')
    || priority !== (task.priority == null ? '' : String(task.priority))
    || due !== toDateInput(task.dueAt)
    || projectId !== (task.projectId ?? UNGROUPED)
  ), [title, description, priority, due, projectId, task])

  useEffect(() => { props.onDirtyChange(dirty) }, [dirty])
  useEffect(() => () => props.onDirtyChange(false), [])

  // 写了一半不许丢: 只要和宿主那份不一致就 debounce 落本地, 保存成功才清。
  // 关抽屉、刷新、崩溃都不清 —— 下次打开这条任务会把它摆出来让人选。
  const draft = useDraft({
    key: `task-sheet:${task.id}`,
    label: task.title,
    value: { title, description, priority, due, projectId },
    clean: !dirty,
  })

  const save = () => {
    // 桥要求 title 必填（它靠 title 判断这是不是一条合法的 update），其余一律只发改动项。
    const patch: SheetPatch = { title: title.trim() || task.title }
    if (description !== (task.description || '')) patch.description = description
    if (priority !== (task.priority == null ? '' : String(task.priority))) {
      patch.priority = priority === '' ? null : Number(priority)
    }
    if (due !== toDateInput(task.dueAt)) patch.dueAt = fromDateInput(due)
    if (projectId !== (task.projectId ?? UNGROUPED)) {
      patch.projectId = projectId === UNGROUPED ? null : projectId
    }
    return props.onSave(patch).then((ok) => {
      // 只有宿主确认写进去了才清本地草稿。失败时本地那份必须留着。
      if (ok) draft.markSynced()
      return ok
    })
  }

  const convLinks = (detail?.links || task.locationLinks || []).filter((l) => l.locationKind === 'conversation' && l.active)
  const planFiles = (detail?.links || task.locationLinks || []).filter((l) => l.locationKind === 'plan-file' && l.active)
  const boundHere = Boolean(sessionId && convLinks.some((l) => l.locationId === sessionId))

  return (
    <aside className="tk-sheet bp-corners" data-testid="tk-sheet" aria-label="任务详情">
      <header className="tk-sheet-head">
        <StateMark state={task.stateKey} />
        <span className="tk-sheet-id" title={task.id}>{task.id}</span>
        <button type="button" className="v2-iconbtn" aria-label="关闭详情" onClick={props.onClose}>
          <X size={15} />
        </button>
      </header>

      <div className="tk-sheet-body">
        {draft.recovered && (
          <div className="tk-draft" role="status" data-testid="tk-sheet-draft">
            <span className="tk-draft-t">有一份未提交的草稿</span>
            <button
              type="button"
              className="v2-btn primary sm"
              data-testid="tk-sheet-draft-restore"
              onClick={() => {
                const d = draft.recovered as {
                  title: string; description: string; priority: string; due: string; projectId: string
                }
                setTitle(d.title)
                setDescription(d.description)
                setPriority(d.priority)
                setDue(d.due)
                setProjectId(d.projectId)
                draft.acceptRecovered()
              }}
            >
              恢复
            </button>
            <button
              type="button"
              className="v2-btn ghost sm"
              data-testid="tk-sheet-draft-discard"
              onClick={draft.discardRecovered}
            >
              丢弃
            </button>
          </div>
        )}
        <input
          className="bp-input tk-sheet-title"
          value={title}
          aria-label="任务标题"
          data-testid="tk-sheet-title"
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void save() }}
        />

        <div className="tk-sheet-grid">
          <label className="tk-field">
            <span className="tk-field-k">项目</span>
            <BlueprintSelect
              value={projectId}
              options={[
                ...props.projects.filter((p) => p.id !== UNGROUPED).map((p) => ({ value: p.id, label: p.label })),
                { value: UNGROUPED, label: UNGROUPED },
              ]}
              label="项目"
              data-testid="tk-sheet-project"
              onChange={setProjectId}
            />
          </label>
          <label className="tk-field">
            <span className="tk-field-k">状态</span>
            <BlueprintSelect
              value={task.stateKey}
              options={STATE_OPTIONS}
              label="状态"
              data-testid="tk-sheet-state"
              onChange={props.onStatus}
            />
          </label>
          <label className="tk-field">
            <span className="tk-field-k">优先级</span>
            <BlueprintSelect
              value={priority}
              options={priorityOptions()}
              label="优先级"
              onChange={setPriority}
            />
          </label>
          <label className="tk-field">
            <span className="tk-field-k">截止</span>
            <input
              type="date"
              className="bp-input tk-date"
              value={due}
              aria-label="截止日期"
              onChange={(e) => setDue(e.target.value)}
            />
          </label>
        </div>

        {tierOf(priority === '' ? null : Number(priority))?.criterion && (
          <p className="tk-tier-why" data-testid="tk-tier-why">
            {tierOf(Number(priority))?.criterion}
          </p>
        )}

        <label className="tk-field tk-field-wide">
          <span className="tk-field-k">描述</span>
          <textarea
            className="bp-input tk-textarea"
            value={description}
            rows={4}
            placeholder="补充上下文…"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <div className="tk-sheet-actions">
          <button
            type="button"
            className="v2-btn primary sm"
            disabled={!dirty || busy}
            data-testid="tk-sheet-save"
            onClick={() => void save()}
          >
            保存
          </button>
          <button
            type="button"
            className="v2-btn ghost sm"
            disabled={busy || !sessionId || boundHere}
            title={sessionId ? (boundHere ? '本会话已绑定' : `绑到当前会话 ${sessionId}`) : '当前没有会话'}
            onClick={props.onBindConversation}
          >
            <Link2 size={12} aria-hidden /> {boundHere ? '已绑本会话' : '绑当前会话'}
          </button>
          <button
            type="button"
            className="v2-btn danger sm tk-sheet-archive"
            disabled={busy}
            title="放弃并归档（总线无硬删，审计留痕）"
            onClick={props.onArchive}
          >
            <Archive size={12} aria-hidden /> 归档
          </button>
        </div>

        {error && <div className="tk-sheet-err" role="status">{error}</div>}
        {draft.savedAt != null && !draft.recovered && (
          <div className="tk-draft-hint" data-testid="tk-sheet-draft-saved">草稿已存本地 · 保存成功后自动清除</div>
        )}

        <section className="tk-sec">
          <h3 className="bp-sechead">位置</h3>
          <ul className="tk-links">
            {convLinks.map((l) => (
              <li key={l.id}><span className="tk-chip" data-kind="conv">会话</span><code>{l.locationId}</code></li>
            ))}
            {planFiles.map((l) => (
              <li key={l.id}><span className="tk-chip" data-kind="plan">计划</span><code>{l.locationId}</code></li>
            ))}
            {(detail?.bindings || []).map((b) => (
              <li key={b.id}>
                <span className="tk-chip" data-kind="ext">{b.provider}</span>
                <code>{b.title || `${b.remoteKind} ${b.remoteId}`}</code>
                <span className="tk-links-state">{b.cacheState}</span>
              </li>
            ))}
            {convLinks.length + planFiles.length + (detail?.bindings.length || 0) === 0 && (
              <li className="tk-links-empty">未绑定任何位置</li>
            )}
          </ul>
        </section>

        <section className="tk-sec">
          <h3 className="bp-sechead">时间线</h3>
          {detail === null && <div className="tk-links-empty">读取中…</div>}
          {detail !== null && detail.notes.length === 0 && (
            <div className="tk-links-empty">无进展与评论</div>
          )}
          <ol className="tk-notes">
            {(detail?.notes || []).slice(-12).reverse().map((note) => (
              <li key={note.id} data-kind={note.kind}>
                <span className="tk-note-k">{note.kind === 'progress' ? '进展' : '评论'}</span>
                <span className="tk-note-b">{note.body}</span>
              </li>
            ))}
          </ol>
        </section>

        <div className="tk-sheet-foot">
          <span>{stateLabel(task.stateKey)}</span>
          {task.dueAt && <span>截止 {dueLabel(task.dueAt)}</span>}
          <span>rev {task.revision}</span>
        </div>
      </div>
    </aside>
  )
}

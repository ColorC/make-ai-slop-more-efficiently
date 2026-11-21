// 就地新建 —— 每个面板一个 `+`，点开在面板内展开一个真正的编辑区。
//
// 上一版只有一行输入 + `#项目 !优先级 @截止` 语法：语法是给熟手的加速器，不该是
// 唯一入口（用户原话：「为什么新建的时候还要语法而不是一个多输入框编辑区域」）。
// 现在对齐 Plane / Linear 的 inline create：标题 + 描述 + 一排属性件（项目/状态/
// 优先级/截止），全部可点可选，预填该面板的 seed；语法仍然认，且覆盖属性件的值。
//
// 回车（或 ⌘/Ctrl+Enter，描述框里用后者）提交并留在原地继续记；Esc 收起。

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { BlueprintSelect } from '../../components/BlueprintSelect'
import { useDraft } from '../../lib/useDraft'
import {
  type GroupSeed, STATE_LABEL, UNGROUPED, UNRATED, dueLabel, parseQuickAdd, taskProfile, tiers,
} from './taskBus'

const STATE_OPTIONS = Object.keys(STATE_LABEL).map((value) => ({ value, label: STATE_LABEL[value] }))

/** 阶梯词表在服务端（task-core §10），这里现取现用，不缓存成模块常量。 */
function priorityOptions() {
  return [
    { value: '', label: taskProfile()?.unratedLabel || UNRATED },
    // 用短标: 「长远升级·重兴趣」那种是**判据**不是档位名, 摆在选项里又长又吵。
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

export interface ComposerInput {
  title: string
  description: string
  projectId: string | null
  stateKey?: string
  parentTaskId?: string | null
  priority: number | null
  dueAt: string | null
}

export interface TaskComposerProps {
  seed: GroupSeed
  projects: { id: string; label: string }[]
  busy: boolean
  onCreate: (input: ComposerInput) => Promise<boolean>
  onClose: () => void
}

export function TaskComposer({ seed, projects, busy, onCreate, onClose }: TaskComposerProps) {
  // 提单子模式: 只要标题。定级/指派/归属都是分诊时的事 ——
  // 捕获阶段每多一个必填格子, 就少记下一条反馈（spec §2.1）。
  const intake = seed.stateKey === 'inbox'
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  // 提单子不预设项目: 归属是分诊时定的, 默认塞进第一个项目会造出一堆错归属的单子。
  const [projectId, setProjectId] = useState(
    seed.projectId ?? (seed.stateKey === 'inbox' ? UNGROUPED : projects[0]?.id ?? UNGROUPED),
  )
  const [stateKey, setStateKey] = useState(seed.stateKey ?? 'todo')
  const [priority, setPriority] = useState(seed.priority == null ? '' : String(seed.priority))
  const [due, setDue] = useState(toDateInput(seed.dueAt))
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLInputElement>(null)

  useEffect(() => { ref.current?.focus() }, [])

  // 语法仍然认，但只是加速器：解析出什么就把对应的属性件跟着改，用户看得见结果。
  const parsed = useMemo(() => parseQuickAdd(title, projects), [title, projects])
  useEffect(() => {
    if (parsed.projectId) setProjectId(parsed.projectId)
    if (parsed.priority != null) setPriority(String(parsed.priority))
    if (parsed.dueAt) setDue(toDateInput(parsed.dueAt))
  }, [parsed.projectId, parsed.priority, parsed.dueAt])

  const finalTitle = parsed.title.trim()

  // 就地新建也会写到一半: Esc 收起、切页签、刷新都不该把打的字弄没。
  // key 跟着落点走 —— 不同面板的半成品互不覆盖。
  const draft = useDraft({
    key: `tk-composer:${seed.stateKey ?? ''}:${seed.projectId ?? ''}:${seed.parentTaskId ?? ''}`,
    label: seed.stateKey === 'inbox' ? '未记完的单子' : '未建完的任务',
    value: { title, description },
    clean: !title.trim() && !description.trim(),
  })

  const submit = async () => {
    if (!finalTitle || busy) return
    setError(null)
    const ok = await onCreate({
      title: finalTitle,
      description: description.trim(),
      projectId,
      stateKey,
      parentTaskId: seed.parentTaskId ?? null,
      priority: priority === '' ? null : Number(priority),
      dueAt: fromDateInput(due),
    })
    if (!ok) { setError('没建上 · 看总线是否在线'); return }
    // 建上了才清草稿。没建上的话打的字必须留着。
    draft.markSynced()
    setTitle('')
    setDescription('')
    ref.current?.focus()
  }

  const projectOptions = [
    ...projects.map((p) => ({ value: p.id, label: p.label })),
    ...(projects.some((p) => p.id === UNGROUPED) ? [] : [{ value: UNGROUPED, label: UNGROUPED }]),
  ]

  return (
    <div
      className="tk-composer"
      data-testid="tk-composer"
      onKeyDown={(e) => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose() }
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() }
      }}
    >
      {draft.recovered && (
        <div className="tk-draft" role="status" data-testid="tk-composer-draft">
          <span className="tk-draft-t">上次没记完</span>
          <button
            type="button"
            className="v2-btn primary sm"
            data-testid="tk-composer-draft-restore"
            onClick={() => {
              const d = draft.recovered as { title: string; description: string }
              setTitle(d.title)
              setDescription(d.description)
              draft.acceptRecovered()
            }}
          >
            接着写
          </button>
          <button type="button" className="v2-btn ghost sm" onClick={draft.discardRecovered}>丢弃</button>
        </div>
      )}
      <input
        ref={ref}
        className="bp-input tk-composer-title"
        value={title}
        placeholder="标题"
        aria-label="标题"
        data-testid="tk-composer-input"
        disabled={busy}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.metaKey && !e.ctrlKey) { e.preventDefault(); void submit() } }}
      />
      {!intake && (
      <textarea
        className="bp-input tk-composer-desc"
        value={description}
        rows={2}
        placeholder="描述（可选）"
        aria-label="描述"
        data-testid="tk-composer-desc"
        disabled={busy}
        onChange={(e) => setDescription(e.target.value)}
      />
      )}

      {!intake && (
      <div className="tk-composer-props">
        {seed.parentTaskId
          ? <span className="tk-echo">子任务</span>
          : (
            <BlueprintSelect
              value={projectId}
              options={projectOptions}
              label="项目"
              aria-label="项目"
              data-testid="tk-composer-project"
              onChange={setProjectId}
            />
          )}
        <BlueprintSelect
          value={stateKey}
          options={STATE_OPTIONS}
          label="状态"
          aria-label="状态"
          data-testid="tk-composer-state"
          onChange={setStateKey}
        />
        <BlueprintSelect
          value={priority}
          options={priorityOptions()}
          label="优先级"
          aria-label="优先级"
          data-testid="tk-composer-priority"
          onChange={setPriority}
        />
        <input
          type="date"
          className="bp-input tk-composer-due"
          value={due}
          aria-label="截止日期"
          title={due ? `截止 ${dueLabel(fromDateInput(due))}` : '截止日期'}
          onChange={(e) => setDue(e.target.value)}
        />
      </div>
      )}

      <div className="tk-composer-foot">
        {parsed.unmatchedProject && (
          <span className="tk-echo" data-kind="warn">#{parsed.unmatchedProject} 未匹配 · 留在标题里</span>
        )}
        {error && <span className="tk-echo" data-kind="warn">{error}</span>}
        <span className="tk-composer-tip">
          {intake ? '回车存下继续记 · 不定级不指派 · Esc 收起' : '回车新建并继续记 · Esc 收起'}
        </span>
        <button type="button" className="v2-btn ghost sm" onClick={onClose}>
          <X size={12} aria-hidden /> 取消
        </button>
        <button
          type="button"
          className="v2-btn primary sm"
          disabled={busy || !finalTitle}
          data-testid="tk-composer-submit"
          onClick={() => void submit()}
        >
          {intake ? '记下' : '新建'}
        </button>
      </div>
    </div>
  )
}

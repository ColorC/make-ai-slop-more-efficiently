import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, X } from 'lucide-react'
import {
  formalDomainActionLabel, listFormalDomains,
  type FormalDomainSummary,
} from './formalDomainsClient'
import type { NoteCanvasNode } from './noteCanvasModel'

type Props = {
  open: boolean
  noteId: string
  notePath: string
  noteRevision: string
  node: NoteCanvasNode | null
  onClose: () => void
}

function shortRef(value: string): string {
  return value.replace(/^omni-product-family\.domain\./, '')
}

export default function FormalDomainDialog({ open, noteId, notePath, noteRevision, node, onClose }: Props) {
  const [domains, setDomains] = useState<FormalDomainSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setError('')
    listFormalDomains().then((items) => {
      setDomains(items)
      setSelectedId((current) => current && items.some((item) => item.material_id === current)
        ? current : items[0]?.material_id || '')
    }).catch((reason) => setError(String(reason)))
  }, [open])

  const selected = useMemo(
    () => domains.find((domain) => domain.material_id === selectedId) || null,
    [domains, selectedId],
  )
  if (!open || !node) return null

  const inputRef = `notes:${noteId}#${node.id}`
  const exactRevision = `mtime-ns:${noteRevision}`
  const adapterReady = false

  return (
    <div className="formal-domain-dialog__backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="formal-domain-dialog" role="dialog" aria-modal="true" aria-label="落入正式域">
        <header>
          <div><strong>落入正式域</strong><small>{node.label || '未命名卡片'}</small></div>
          <button onClick={onClose} aria-label="关闭"><X size={17} /></button>
        </header>
        <div className="formal-domain-dialog__body">
          <nav aria-label="目标正式域">
            {domains.map((domain) => (
              <button key={domain.material_id} className={domain.material_id === selectedId ? 'is-active' : ''}
                onClick={() => setSelectedId(domain.material_id)}>
                <strong>{shortRef(domain.material_id)}</strong>
                <small>{domain.formal_domain.product_ref.ref}</small>
              </button>
            ))}
            {!domains.length && !error && <p>正在读取正式域…</p>}
          </nav>
          <main>
            {error && <div className="formal-domain-dialog__warning"><AlertTriangle size={16} />{error}</div>}
            {selected && <>
              <div className="formal-domain-dialog__action">
                <span>{inputRef}</span><ArrowRight size={16} /><strong>{formalDomainActionLabel(selected)}</strong>
              </div>
              <p>{selected.formal_domain.semantic_scope}</p>
              <dl>
                <dt>输入版本</dt><dd><code>{notePath}</code><code>{exactRevision}</code></dd>
                <dt>真实产物</dt><dd>{selected.formal_domain.formal_artifact_kinds.join('、')}</dd>
                <dt>责任</dt><dd>{selected.formal_domain.responsibility_model.required_roles.join('、')}</dd>
                <dt>权威范围</dt><dd>{selected.formal_domain.authority_scopes.map((scope) => (
                  <code key={`${scope.object}:${scope.purpose}:${scope.scope}`}>{scope.object} · {scope.purpose} · {scope.scope}</code>
                ))}</dd>
                <dt>域版本</dt><dd><code>{selected.domain_ref.revision_ref.revision}</code></dd>
              </dl>
              <div className="formal-domain-dialog__warning">
                <AlertTriangle size={16} />
                <span>当前卡片尚未由目标域适配器生成真实产物，因此预检查停在这里；不会写入正式化记录或当前权威。</span>
              </div>
            </>}
          </main>
        </div>
        <footer>
          <span>{adapterReady && <><CheckCircle2 size={14} /> 可执行</>}</span>
          <button onClick={onClose}>关闭</button>
          <button className="is-primary" disabled={!adapterReady}>执行产品动作</button>
        </footer>
      </section>
    </div>
  )
}

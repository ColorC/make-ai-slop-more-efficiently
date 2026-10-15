import React, { useEffect, useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import {
  aiLearningObservatoryApi,
  type ObservatoryOverview,
  type ObservatoryProjectDetail,
  type ObservatoryProjectSummary,
  type EvaluationProfileSummary,
  type EvaluationDimensionId,
} from '../../api/aiLearningObservatoryClient'
import { relTimeZh } from '../../lib/time'
import { openInVscode } from '../../lib/openInVscode'
import {
  humanTextZh,
  projectScopeNoteZh,
  selectionReasonLabelZh,
  sourceCategoryLabelZh,
  statusLabelZh,
  workTypeLabelZh,
} from './aiLearningObservatoryLabels'

const panel: React.CSSProperties = {
  border: '1px solid var(--fp-border)', borderRadius: 3, background: 'var(--fp-solid)', padding: 16,
}
const grid: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12,
}
const muted: React.CSSProperties = { color: 'var(--fp-text-3)', fontSize: 12, lineHeight: 1.55 }
const chip: React.CSSProperties = {
  display: 'inline-flex', padding: '3px 8px', borderRadius: 999, border: '1px solid var(--fp-border)',
  color: 'var(--fp-text-2)', fontSize: 12, marginRight: 6,
}
const DIMENSION_LABELS: Record<EvaluationDimensionId, string> = {
  time: '时间',
  token_cost: 'token',
  user_expectation: '用户预期',
  objective_outcome: '客观结果',
  human_attention: '用户精力',
}

function ProjectCard({
  row,
  selection,
  profile,
}: {
  row: ObservatoryProjectSummary
  selection?: { name?: string; selection_reasons?: string[]; work_type?: string }
  profile?: EvaluationProfileSummary
}) {
  const coverage = row.coverage
  const result = row.assessment
  const [detail, setDetail] = useState<ObservatoryProjectDetail | null>(null)
  const [detailError, setDetailError] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const resolved = coverage ? coverage.available : 0
  const queried = coverage ? coverage.queried : 0
  const usage = result?.evaluator?.usage
  const dimensionStatuses = result?.dimension_statuses || {}
  const scopeNote = projectScopeNoteZh(row.project_id)
  const loadDetail = () => {
    if (detail) { setDetail(null); return }
    setDetailLoading(true)
    setDetailError('')
    aiLearningObservatoryApi.project(row.project_id)
      .then(setDetail)
      .catch((reason: unknown) => setDetailError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setDetailLoading(false))
  }
  return (
    <article style={panel} data-testid={`learning-project-${row.project_id}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <strong style={{ fontSize: 14 }}>{selection?.name || row.project_id}</strong>
        <span style={chip}>{statusLabelZh(result?.evaluation_state, '尚未评价')}</span>
      </div>
      <div style={{ margin: '12px 0 8px', fontSize: 22, fontVariantNumeric: 'tabular-nums' }}>
        {resolved}<span style={{ ...muted, marginLeft: 4 }}>/ {queried} 类证据可用</span>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        <span style={chip}>正确性：{statusLabelZh(result?.correctness_status || result?.absolute_status, '尚无法判断')}</span>
        <span style={chip}>比较资格：{statusLabelZh(result?.comparison_status || result?.trajectory_status, '还没有可比较的前后样本')}</span>
        <span style={chip}>评测档案：{statusLabelZh(profile?.status, '尚未接通')}</span>
        <span style={chip}>空 {coverage?.empty ?? 0}</span>
        <span style={chip}>不可用 {coverage?.unavailable ?? 0}</span>
        <span style={chip}>冲突 {coverage?.conflict ?? 0}</span>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        {(Object.keys(DIMENSION_LABELS) as EvaluationDimensionId[]).map((dimension) => (
          <span key={dimension} style={chip}>
            {DIMENSION_LABELS[dimension]}：{statusLabelZh(dimensionStatuses[dimension], '尚无法判断')}
          </span>
        ))}
      </div>
      <div style={{ color: 'var(--fp-text-2)', fontSize: 13, lineHeight: 1.55 }}>
        {humanTextZh(result?.summary) || '尚无评价；这不代表工作通过或失败。'}
      </div>
      {scopeNote && <div style={{ ...muted, marginTop: 7 }}>{scopeNote}</div>}
      <div style={{ ...muted, marginTop: 10 }}>
        采集 {relTimeZh(row.collected_at)} · {row.assessment_id || row.snapshot_id || '无记录'}
      </div>
      <div style={{ ...muted, marginTop: 4 }}>
        {workTypeLabelZh(selection?.work_type)} · {(selection?.selection_reasons || []).map(selectionReasonLabelZh).join(' · ') || '已有运行记录'}
      </div>
      <div style={{ ...muted, marginTop: 4 }}>
        token {usage ? `输入 ${usage.input_tokens || 0} / 输出 ${usage.output_tokens || 0}` : '未知'}
        {' · '}估算成本 {result?.evaluator?.estimated_cost_usd == null ? '未知' : `$${result.evaluator.estimated_cost_usd.toFixed(6)}`}
      </div>
      <button type="button" onClick={loadDetail} style={{
        marginTop: 12, border: '1px solid var(--fp-border)', borderRadius: 2, background: 'var(--fp-surface)',
        color: 'var(--fp-text-2)', padding: '6px 9px', cursor: 'pointer', fontSize: 12,
      }}>{detailLoading ? '读取中' : detail ? '收起证据' : '下钻证据与评测档案'}</button>
      {detailError && <div style={{ ...muted, color: 'var(--fp-danger)', marginTop: 8 }}>{detailError}</div>}
      {detail && (
        <div style={{ borderTop: '1px solid var(--fp-border-subtle)', marginTop: 12, paddingTop: 12 }}>
          {detail.evaluation_profile && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 5 }}>项目评测档案</div>
              <div style={{ color: 'var(--fp-text-2)', fontSize: 12, lineHeight: 1.55 }}>
                {detail.evaluation_profile.summary}
              </div>
              <div style={{ ...muted, marginTop: 5 }}>
                正确性门：{detail.evaluation_profile.correctness_gate.primary_question}
              </div>
              {detail.evaluation_profile.user_concerns.map((concern) => (
                <div key={concern.id} style={{ ...muted, marginTop: 4 }}>
                  用户关心 · {concern.label}：{concern.user_question}
                </div>
              ))}
              {detail.evaluation_profile.task_classes.map((taskClass) => (
                <div key={taskClass.id} style={{ ...muted, marginTop: 4 }}>
                  可比任务 · {taskClass.label}：{taskClass.unit_of_work}
                </div>
              ))}
            </div>
          )}
          {(detail.snapshot?.work_units?.businesses || []).map((business) => (
            <div key={business.business_id} style={{ fontSize: 12, lineHeight: 1.55, marginBottom: 5 }}>
              <b>Business：</b>{business.business_id}
              {(detail.snapshot?.work_units?.tasks || [])
                .filter((task) => task.business_id === business.business_id)
                .map((task) => <div key={task.task_id} style={{ ...muted, paddingLeft: 10 }}>Task：{task.task_id} · {statusLabelZh(task.status)}</div>)}
            </div>
          ))}
          <div style={{ ...muted, marginBottom: 8 }}>
            评价版本：{detail.assessment?.assessment_id || '无'}
            {detail.assessment?.supersedes ? ` ← ${detail.assessment.supersedes}` : ''}
          </div>
          <div style={{ ...muted, marginBottom: 8 }}>
            {Object.entries(detail.snapshot?.sources || {}).map(([name, source]) => (
              <div key={name} style={{ marginBottom: 5 }}>
                <div>{sourceCategoryLabelZh(name)}：{statusLabelZh(source.status)}（{source.items.length}）</div>
                {source.items.slice(0, 3).map((item) => {
                  const localPath = /^[A-Za-z]:[\\/]/.test(item.source_ref)
                  return (
                    <div key={item.id} style={{ paddingLeft: 10 }}>
                      {item.id} · {humanTextZh(item.summary)} · {item.source_ref}
                      {localPath && (
                        <button type="button" onClick={() => openInVscode(item.source_ref)} style={{
                          marginLeft: 6, border: 0, background: 'transparent', color: 'var(--fp-accent)', cursor: 'pointer',
                        }}>打开</button>
                      )}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
          {detail.assessment?.semantic?.correctness_gate && (
            <div style={{ fontSize: 12, lineHeight: 1.55, marginTop: 8 }}>
              <b>正确性门：</b>{statusLabelZh(detail.assessment.semantic.correctness_gate.status)} · {detail.assessment.semantic.correctness_gate.reason}
            </div>
          )}
          {(detail.assessment?.semantic?.dimensions || []).map((dimension) => (
            <div key={dimension.dimension} style={{ fontSize: 12, lineHeight: 1.55, marginTop: 8 }}>
              <b>{DIMENSION_LABELS[dimension.dimension]}：</b>{statusLabelZh(dimension.status)} · {dimension.reason}
              <div style={muted}>{dimension.measurement_scope} · {dimension.evidence_refs.join(' · ') || '当前无可引用数值'}</div>
            </div>
          ))}
          {(detail.assessment?.semantic?.unknowns || detail.assessment?.semantic?.evidence_gaps || []).map((gap, index) => (
            <div key={`gap-${index}`} style={{ ...muted, marginTop: 5 }}>未知：{gap}</div>
          ))}
          {(detail.assessment?.semantic?.next_observations || []).map((observation, index) => (
            <div key={`observation-${index}`} style={{ ...muted, marginTop: 5 }}>
              下一观测问题：{observation.question} · {observation.why}
            </div>
          ))}
          {detail.assessment?.calibration && (
            <div style={{ ...muted, marginTop: 8 }}>
              评价校准：{statusLabelZh(detail.assessment.calibration.status)} · {humanTextZh(detail.assessment.calibration.note)}
            </div>
          )}
        </div>
      )}
    </article>
  )
}

export default function AiLearningObservatoryPanel() {
  const [data, setData] = useState<ObservatoryOverview | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const load = () => {
    setLoading(true)
    setError('')
    aiLearningObservatoryApi.overview()
      .then(setData)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  const totals = useMemo(() => {
    const rows = data?.projects || []
    return {
      projects: rows.length,
      available: rows.reduce((sum, row) => sum + (row.coverage?.available || 0), 0),
      queried: rows.reduce((sum, row) => sum + (row.coverage?.queried || 0), 0),
      semanticOk: rows.filter((row) => row.assessment?.evaluator?.status === 'ok').length,
      profiles: Object.values(data?.evaluation_profiles || {}).filter((row) => row.status === 'connected').length,
    }
  }, [data])
  const selectionByProject = useMemo(() => new Map(
    (data?.selection?.projects || []).map((row) => [row.project_id, row]),
  ), [data])

  return (
    <section data-testid="ai-learning-observatory-panel">
      <div style={{ ...panel, marginBottom: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 500 }}>工作是否做对，并在可比尺度上怎样变化？</div>
            <div style={{ ...muted, marginTop: 6 }}>
              正确性与智能度是第一门槛；时间、token、用户预期、客观结果和用户精力分别显示，不生成总分。“未知”与“不可比”都是正常观测状态，不是目标项目待办。
            </div>
          </div>
          <button type="button" onClick={load} disabled={loading} style={{
            border: '1px solid var(--fp-border)', borderRadius: 2, background: 'var(--fp-surface)',
            color: 'var(--fp-text-2)', padding: '7px 10px', cursor: 'pointer', display: 'inline-flex', gap: 6,
          }}><RefreshCw size={13} />{loading ? '读取中' : '刷新'}</button>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
          <span style={chip}>项目 {totals.projects}</span>
          <span style={chip}>证据类 {totals.available}/{totals.queried}</span>
          <span style={chip}>Terra 成功 {totals.semanticOk}</span>
          <span style={chip}>项目评测档案 {totals.profiles}/{data?.selection?.projects?.length || 0}</span>
          <span style={chip}>无综合分</span>
          <span style={chip}>{data?.config.provider || 'the_company'} / {data?.config.model || 'gpt-5.6-terra'}</span>
          <span style={chip}>最近运行 {data?.latest_run_id || '尚无'}</span>
        </div>
      </div>
      {error && <div style={{ ...panel, color: 'var(--fp-danger)', marginBottom: 12 }}>读取失败：{error}</div>}
      {!error && !loading && data?.projects.length === 0 && (
        <div style={{ ...panel, ...muted }}>尚无运行记录。设施不会把“没采到数据”展示成改善。</div>
      )}
      <div style={grid}>{data?.projects.map((row) => (
        <ProjectCard key={row.project_id} row={row} selection={selectionByProject.get(row.project_id)} profile={data?.evaluation_profiles?.[row.project_id]} />
      ))}</div>
    </section>
  )
}

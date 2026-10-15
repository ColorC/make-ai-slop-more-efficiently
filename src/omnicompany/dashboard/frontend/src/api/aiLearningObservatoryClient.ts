export interface ObservatoryCoverage {
  available: number
  empty: number
  unavailable: number
  stale: number
  conflict: number
  excluded: number
  queried: number
}

export interface ObservatoryAssessmentSummary {
  // 以下是存储/API 兼容枚举；界面必须通过中文映射显示，不能直接渲染原值。
  evaluation_state?: 'provisional' | 'unknown' | 'inconclusive'
  absolute_status?: 'pass' | 'fail' | 'mixed' | 'unknown'
  trajectory_status?: 'improving' | 'regressing' | 'mixed' | 'unknown' | 'not_comparable'
  correctness_status?: 'met' | 'not_met' | 'mixed' | 'unknown'
  comparison_status?: 'comparable' | 'partially_comparable' | 'not_comparable' | 'unknown'
  dimension_statuses?: Partial<Record<EvaluationDimensionId, EvaluationDimensionStatus>>
  summary?: string
  evaluated_at?: string
  evaluator?: {
    provider?: string
    model?: string
    status?: string
    usage?: Record<string, number>
    estimated_cost_usd?: number | null
  }
}

export type EvaluationDimensionId = 'time' | 'token_cost' | 'user_expectation' | 'objective_outcome' | 'human_attention'
export type EvaluationDimensionStatus = 'better' | 'worse' | 'stable' | 'mixed' | 'unknown' | 'not_comparable'

export interface EvaluationProfileSummary {
  status: 'connected' | 'missing' | 'invalid'
  profile_path?: string | null
  task_class_count: number
  user_concern_count: number
  dimensions: EvaluationDimensionId[]
  no_composite_score?: boolean
  error?: string
}

export interface ProjectEvaluationProfile {
  project_id: string
  project_name: string
  work_type: string
  summary: string
  user_concerns: Array<{ id: string; label: string; user_question: string; semantic_acceptance: string }>
  task_classes: Array<{ id: string; label: string; unit_of_work: string; absolute_quality_bar: string[] }>
  correctness_gate: { primary_question: string; must_hold: string[] }
  current_baseline: { facts: string[]; inferences: string[]; unknowns: string[]; next_connections: string[] }
}

export interface ObservatoryProjectDetail {
  project_id: string
  evaluation_profile?: ProjectEvaluationProfile | null
  snapshot?: {
    snapshot_id: string
    coverage: ObservatoryCoverage
    sources: Record<string, { status: string; items: Array<{ id: string; source_ref: string; summary: string }> }>
    work_units?: {
      businesses?: Array<{ business_id: string; parent_business_id?: string; label?: string }>
      tasks?: Array<{ task_id: string; business_id: string; status?: string; source_ref?: string }>
    }
  } | null
  assessment?: {
    assessment_id: string
    supersedes?: string | null
    evaluated_at?: string
    evaluator?: ObservatoryAssessmentSummary['evaluator']
    semantic?: {
      summary?: string
      correctness_gate?: { status?: string; reason?: string; evidence_refs?: string[] }
      comparison?: { status?: string; matched_task_class?: string; scale_handling?: string; confounders?: string[] }
      dimensions?: Array<{ dimension: EvaluationDimensionId; status: EvaluationDimensionStatus; reason: string; measurement_scope: string; evidence_refs: string[] }>
      facts?: Array<{ claim: string; evidence_refs: string[] }>
      inferences?: Array<{ claim: string; confidence: string; evidence_refs: string[]; disconfirming_evidence: string }>
      unknowns?: string[]
      next_observations?: Array<{ question: string; why: string; existing_source_candidates: string[] }>
      // Immutable v1-v3 assessment compatibility.
      absolute_assessment?: { status?: string; findings?: Array<{ claim: string; evidence_refs: string[] }> }
      trajectory_assessment?: { status?: string; findings?: Array<{ claim: string; evidence_refs: string[] }> }
      evidence_gaps?: string[]
      cause_hypotheses?: Array<{ hypothesis: string; confidence: string; evidence_refs: string[] }>
      improvement_candidates?: Array<{ change: string; target: string; expected_signal: string; evidence_refs: string[] }>
    } | null
    observation_followup?: {
      previous_assessment_id?: string
      new_snapshot_available?: boolean
      status?: string
      note?: string
    }
    improvement_followup?: {
      previous_assessment_id?: string
      verified_candidate_count?: number
      candidates?: Array<{ target: string; status: string; current_evidence_refs: string[]; note: string }>
    }
    calibration?: { previous_assessment_id?: string; revision_triggered?: boolean; status?: string; note?: string }
  } | null
}

export interface ObservatoryProjectSummary {
  project_id: string
  snapshot_id?: string
  assessment_id?: string
  collected_at?: string
  coverage?: ObservatoryCoverage
  assessment?: ObservatoryAssessmentSummary | null
}

export interface ObservatoryOverview {
  schema_version: number
  data_root: string
  updated_at?: string
  latest_run_id?: string
  config: {
    provider: string
    model: string
    source_categories: string[]
    minimum_projects: number
    maximum_projects: number
    correctness_is_gate?: boolean
    no_composite_score?: boolean
    dimensions?: EvaluationDimensionId[]
  }
  evaluation_profiles?: Record<string, EvaluationProfileSummary>
  selection?: {
    observed_at?: string
    gate?: { ok?: boolean }
    projects?: Array<{ project_id: string; name?: string; selection_reasons?: string[]; work_type?: string }>
  }
  projects: ObservatoryProjectSummary[]
}

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return response.json() as Promise<T>
}

export const aiLearningObservatoryApi = {
  overview: () => fetch('/api/ai-learning-observatory/overview').then(jsonOrThrow<ObservatoryOverview>),
  project: (projectId: string) => fetch(`/api/ai-learning-observatory/projects/${encodeURIComponent(projectId)}`)
    .then(jsonOrThrow<ObservatoryProjectDetail>),
}

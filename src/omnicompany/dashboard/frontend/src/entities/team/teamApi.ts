// team 取数层(2026-08-06 从 index.tsx 拆出): 列表/详情/图/运行/体检 + 各类 builder 报告的
// latest 快照读取, 以及 EntityResolver。列表结果进程内缓存一次(_cache), 由 invalidateTeamCache 清。
// 拆分缘由见 teamTypes.ts 头注。
import type { EntityResolver } from '../registry'
import type { CodeFileDetail } from '../../shell/CodeFileEditor'
import type {
  MaterialAttributionReport,
  TeamBuilderClosureStatus,
  TeamBuilderHighStandardAudit,
  TeamBuilderLlmReplayPlan,
  TeamBuilderLlmReplayResult,
  TeamBuilderMaterialGapValidation,
  TeamBuilderMaterialization,
  TeamBuilderProviderCoverageAudit,
  TeamBuilderProviderSameInputTrialPlan,
  TeamBuilderReadClueResolutionPlan,
  TeamBuilderRepairApplyExecutionReport,
  TeamBuilderRepairApplyGateReport,
  TeamBuilderRepairApplyPreviewReport,
  TeamBuilderRepairApprovalReport,
  TeamBuilderRepairClosureRollupReport,
  TeamBuilderRepairDryRunReport,
  TeamBuilderRepairExecutionReadinessReport,
  TeamBuilderRepairGeneralizationTrialReport,
  TeamBuilderRepairOutcomeReconciliationReport,
  TeamBuilderRepairPatchCandidatesReport,
  TeamBuilderRepairPatchDiffProposalReport,
  TeamBuilderRepairPlan,
  TeamBuilderRepairPostApplyVerificationReport,
  TeamBuilderRepairProbeReport,
  TeamBuilderRepairRealGeneratedFileSetTrialReport,
  TeamBuilderRepairRealRunApplyExecutionReport,
  TeamBuilderRepairRealRunApplyGateReport,
  TeamBuilderRepairRealRunApplyPreviewReport,
  TeamBuilderRepairRealRunCandidateScanReport,
  TeamBuilderRepairRealRunClosureRollupReport,
  TeamBuilderRepairRealRunDiffPreviewReport,
  TeamBuilderRepairRealRunDiffReviewReport,
  TeamBuilderRepairRealRunOutcomeReconciliationReport,
  TeamBuilderRepairRealRunPostApplyVerificationReport,
  TeamBuilderRepairRealRunReplayPlanReport,
  TeamBuilderRepairRealRunRollbackExecutionReport,
  TeamBuilderRepairRealRunRollbackPostVerificationReport,
  TeamBuilderRepairRealRunRollbackReadinessReport,
  TeamBuilderRepairRollbackExecutionReport,
  TeamBuilderRepairRollbackPostVerificationReport,
  TeamBuilderRepairRollbackReadinessReport,
  TeamBuilderRepairSafetyPolicy,
  TeamBuilderTestReport,
  TeamDoctorHealth,
  TeamEntity,
  TeamGraphData,
  TeamRunDetail,
  TeamRunSummary,
} from './teamTypes'

let _cache: TeamEntity[] | null = null

export async function fetchList(): Promise<TeamEntity[]> {
  if (_cache) return _cache
  const r = await fetch('/api/teams')
  if (!r.ok) throw new Error(`读取 team 列表失败：${r.status}`)
  const d = await r.json() as { items: any[] }
  _cache = d.items.map((it) => ({
    type: 'team' as const,
    id: it.id,
    title: it.name === 'team' ? it.package.split('/').pop() || it.package : it.name,
    package: it.package,
    file_path: it.file_path,
    has_design_md: !!it.has_design_md,
    tags: [it.package.split('/')[0]],
  }))
  return _cache!
}

export async function fetchDetail(id: string): Promise<CodeFileDetail> {
  const r = await fetch(`/api/teams/${id}`)
  if (!r.ok) throw new Error(`读取 team 详情失败：${r.status}`)
  return r.json()
}

export async function fetchGraph(id: string, builder: string | null): Promise<TeamGraphData> {
  const suffix = builder ? `?builder=${encodeURIComponent(builder)}` : ''
  const r = await fetch(`/api/team-graph/${id}${suffix}`)
  if (!r.ok) throw new Error(`读取 team 结构图失败：${r.status}`)
  return r.json()
}

export async function fetchRuns(id: string, builder: string | null): Promise<TeamRunSummary[]> {
  const params = new URLSearchParams({ limit: '12' })
  if (builder) params.set('builder', builder)
  const r = await fetch(`/api/team-runs/${id}?${params}`)
  if (!r.ok) throw new Error(`读取 team 运行轨迹失败：${r.status}`)
  const d = await r.json() as { items: TeamRunSummary[] }
  return d.items
}

export async function fetchRunDetail(id: string, traceId: string, builder: string | null): Promise<TeamRunDetail> {
  const params = new URLSearchParams({ trace_id: traceId })
  if (builder) params.set('builder', builder)
  const r = await fetch(`/api/team-run-detail/${id}?${params}`)
  if (!r.ok) throw new Error(`读取 team 单次运行详情失败：${r.status}`)
  return r.json()
}

export async function fetchDoctorHealth(id: string, builder: string | null): Promise<TeamDoctorHealth> {
  const params = new URLSearchParams()
  if (builder) params.set('builder', builder)
  const suffix = params.toString() ? `?${params}` : ''
  const r = await fetch(`/api/team-doctor/${id}${suffix}`)
  if (!r.ok) throw new Error(`读取 team 健康诊断失败：${r.status}`)
  return r.json()
}

export async function fetchMaterializationLatest(): Promise<TeamBuilderMaterialization> {
  const r = await fetch('/api/team-builder-materialization/latest')
  if (!r.ok) throw new Error(`读取 material 化实战结果失败：${r.status}`)
  return r.json()
}

export async function fetchMaterialAttributionReportLatest(): Promise<MaterialAttributionReport> {
  const r = await fetch('/api/team-builder-materialization/report/latest')
  if (!r.ok) throw new Error(`读取 material 归因报告失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderTestReportLatest(): Promise<TeamBuilderTestReport> {
  const r = await fetch('/api/team-builder-materialization/test-report/latest')
  if (!r.ok) throw new Error(`读取生成包测试报告失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairPlanLatest(): Promise<TeamBuilderRepairPlan> {
  const r = await fetch('/api/team-builder-materialization/repair-plan/latest')
  if (!r.ok) throw new Error(`读取修复准备计划失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairProbeLatest(): Promise<TeamBuilderRepairProbeReport> {
  const r = await fetch('/api/team-builder-materialization/repair-probe/latest')
  if (!r.ok) throw new Error(`读取故障修复探针失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairDryRunLatest(): Promise<TeamBuilderRepairDryRunReport> {
  const r = await fetch('/api/team-builder-materialization/repair-dry-run/latest')
  if (!r.ok) throw new Error(`读取修复干跑探针失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairPatchCandidatesLatest(): Promise<TeamBuilderRepairPatchCandidatesReport> {
  const r = await fetch('/api/team-builder-materialization/repair-patch-candidates/latest')
  if (!r.ok) throw new Error(`读取候选补丁计划失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairApplyGateLatest(): Promise<TeamBuilderRepairApplyGateReport> {
  const r = await fetch('/api/team-builder-materialization/repair-apply-gate/latest')
  if (!r.ok) throw new Error(`读取修复应用门失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairPatchDiffProposalLatest(): Promise<TeamBuilderRepairPatchDiffProposalReport> {
  const r = await fetch('/api/team-builder-materialization/repair-patch-diff-proposal/latest')
  if (!r.ok) throw new Error(`读取补丁 diff proposal 失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairApprovalLatest(): Promise<TeamBuilderRepairApprovalReport> {
  const r = await fetch('/api/team-builder-materialization/repair-approval/latest')
  if (!r.ok) throw new Error(`读取修复批准记录失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairExecutionReadinessLatest(): Promise<TeamBuilderRepairExecutionReadinessReport> {
  const r = await fetch('/api/team-builder-materialization/repair-execution-readiness/latest')
  if (!r.ok) throw new Error(`读取修复执行就绪失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairApplyPreviewLatest(): Promise<TeamBuilderRepairApplyPreviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-apply-preview/latest')
  if (!r.ok) throw new Error(`读取修复应用预览失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairApplyExecutionLatest(): Promise<TeamBuilderRepairApplyExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-apply-execution/latest')
  if (!r.ok) throw new Error(`读取修复真实应用记录失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairPostApplyVerificationLatest(): Promise<TeamBuilderRepairPostApplyVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-post-apply-verification/latest')
  if (!r.ok) throw new Error(`读取修复应用后验证失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairOutcomeReconciliationLatest(): Promise<TeamBuilderRepairOutcomeReconciliationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-outcome-reconciliation/latest')
  if (!r.ok) throw new Error(`读取修复前后对账失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRollbackReadinessLatest(): Promise<TeamBuilderRepairRollbackReadinessReport> {
  const r = await fetch('/api/team-builder-materialization/repair-rollback-readiness/latest')
  if (!r.ok) throw new Error(`读取修复回滚就绪失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRollbackExecutionLatest(): Promise<TeamBuilderRepairRollbackExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-rollback-execution/latest')
  if (!r.ok) throw new Error(`读取修复回滚执行记录失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRollbackPostVerificationLatest(): Promise<TeamBuilderRepairRollbackPostVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-rollback-post-verification/latest')
  if (!r.ok) throw new Error(`读取修复回滚后验证失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairClosureRollupLatest(): Promise<TeamBuilderRepairClosureRollupReport> {
  const r = await fetch('/api/team-builder-materialization/repair-closure-rollup/latest')
  if (!r.ok) throw new Error(`读取修复闭环总览失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairGeneralizationTrialLatest(): Promise<TeamBuilderRepairGeneralizationTrialReport> {
  const r = await fetch('/api/team-builder-materialization/repair-generalization-trial/latest')
  if (!r.ok) throw new Error(`读取修复泛化试验失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealGeneratedFileSetTrialLatest(): Promise<TeamBuilderRepairRealGeneratedFileSetTrialReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-generated-file-set-trial/latest')
  if (!r.ok) throw new Error(`读取真实 generated worker 文件集试验失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunCandidateScanLatest(): Promise<TeamBuilderRepairRealRunCandidateScanReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-candidate-scan/latest')
  if (!r.ok) throw new Error(`读取真实 run 候选扫描失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunReplayPlanLatest(): Promise<TeamBuilderRepairRealRunReplayPlanReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-replay-plan/latest')
  if (!r.ok) throw new Error(`读取真实 run 消解计划失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunDiffPreviewLatest(): Promise<TeamBuilderRepairRealRunDiffPreviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-diff-preview/latest')
  if (!r.ok) throw new Error(`读取真实 run diff 预览失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunDiffReviewLatest(): Promise<TeamBuilderRepairRealRunDiffReviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-diff-review/latest')
  if (!r.ok) throw new Error(`读取真实 run diff 审阅门失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunApplyGateLatest(): Promise<TeamBuilderRepairRealRunApplyGateReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-apply-gate/latest')
  if (!r.ok) throw new Error(`读取真实 run 显式应用门失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunApplyPreviewLatest(): Promise<TeamBuilderRepairRealRunApplyPreviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-apply-preview/latest')
  if (!r.ok) throw new Error(`读取真实 run 文件集应用预览失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunApplyExecutionLatest(): Promise<TeamBuilderRepairRealRunApplyExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-apply-execution/latest')
  if (!r.ok) throw new Error(`读取真实 run 显式应用执行失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunPostApplyVerificationLatest(): Promise<TeamBuilderRepairRealRunPostApplyVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-post-apply-verification/latest')
  if (!r.ok) throw new Error(`读取真实 run 应用后回放验证失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunOutcomeReconciliationLatest(): Promise<TeamBuilderRepairRealRunOutcomeReconciliationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest')
  if (!r.ok) throw new Error(`读取真实 run 修复结果对账失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunRollbackReadinessLatest(): Promise<TeamBuilderRepairRealRunRollbackReadinessReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-rollback-readiness/latest')
  if (!r.ok) throw new Error(`读取真实 run 回滚就绪失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunRollbackExecutionLatest(): Promise<TeamBuilderRepairRealRunRollbackExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-rollback-execution/latest')
  if (!r.ok) throw new Error(`读取真实 run 回滚执行失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunRollbackPostVerificationLatest(): Promise<TeamBuilderRepairRealRunRollbackPostVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-rollback-post-verification/latest')
  if (!r.ok) throw new Error(`读取真实 run 回滚后验证失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairRealRunClosureRollupLatest(): Promise<TeamBuilderRepairRealRunClosureRollupReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-closure-rollup/latest')
  if (!r.ok) throw new Error(`读取真实 run 闭环总览失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderRepairSafetyPolicyLatest(): Promise<TeamBuilderRepairSafetyPolicy> {
  const r = await fetch('/api/team-builder-materialization/repair-safety-policy/latest')
  if (!r.ok) throw new Error(`读取修复安全策略失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderReadClueResolutionLatest(): Promise<TeamBuilderReadClueResolutionPlan> {
  const r = await fetch('/api/team-builder-materialization/read-clue-resolution/latest')
  if (!r.ok) throw new Error(`读取读取线索消解计划失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderMaterialGapValidationLatest(): Promise<TeamBuilderMaterialGapValidation> {
  const r = await fetch('/api/team-builder-materialization/material-gap-validation/latest')
  if (!r.ok) throw new Error(`读取 material 缺口验证失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderClosureStatusLatest(): Promise<TeamBuilderClosureStatus> {
  const r = await fetch('/api/team-builder-materialization/closure/latest')
  if (!r.ok) throw new Error(`读取闭环状态失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderHighStandardAuditLatest(): Promise<TeamBuilderHighStandardAudit> {
  const r = await fetch('/api/team-builder-materialization/high-standard-audit/latest')
  if (!r.ok) throw new Error(`读取高标准目标审计失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderProviderCoverageAuditLatest(): Promise<TeamBuilderProviderCoverageAudit> {
  const r = await fetch('/api/team-builder-materialization/provider-coverage/latest')
  if (!r.ok) throw new Error(`读取 provider 覆盖审计失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderProviderSameInputTrialPlanLatest(): Promise<TeamBuilderProviderSameInputTrialPlan> {
  const r = await fetch('/api/team-builder-materialization/provider-same-input-trial/latest')
  if (!r.ok) throw new Error(`读取 provider 同口径试验计划失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderLlmReplayPlanLatest(): Promise<TeamBuilderLlmReplayPlan> {
  const r = await fetch('/api/team-builder-materialization/llm-replay-plan/latest')
  if (!r.ok) throw new Error(`读取 LLM 回放计划失败：${r.status}`)
  return r.json()
}

export async function fetchTeamBuilderLlmReplayResultLatest(): Promise<TeamBuilderLlmReplayResult> {
  const r = await fetch('/api/team-builder-materialization/llm-replay-result/latest')
  if (!r.ok) throw new Error(`读取 LLM 回放结果失败：${r.status}`)
  return r.json()
}

export const resolver: EntityResolver<TeamEntity> = {
  type: 'team',
  async fetch(id) {
    const list = await fetchList()
    const found = list.find((t) => t.id === id)
    if (found) return found
    throw new Error(`找不到 team：${id}`)
  },
  async list() { return fetchList() },
  async search(q) {
    const all = await fetchList()
    const ql = q.toLowerCase()
    return all.filter((t) => t.id.toLowerCase().includes(ql) || t.title.toLowerCase().includes(ql))
  },
}

export function invalidateTeamCache(): void { _cache = null }

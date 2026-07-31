// team 图编辑器主体(2026-07 首屏拆包, 从 index.tsx 拆出): reactflow/dagre/CodeFileEditor
// 全部住这里, 经 index.tsx 的 React.lazy 按需加载; 本文件不挂注册, 只默认导出 Editor。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
// team 图布局用成熟库 dagre(@dagrejs/dagre): 主线程同步跑、无 Web Worker, 自带去环/分层/减交叉,
// 并直接给出每条边的走线折点。是 ReactFlow 官方推荐的分层布局方案。
// (放弃 elkjs: 它必须靠 Web Worker, 而那个 worker 在本项目 Vite 产物里握手不通、elk.layout 永远不返回;
//  已排除 EDR/CSP——普通 blob worker 能跑。elkjs 没有同步模式, 故改用同算法但无 worker 的 dagre。)
import dagre from '@dagrejs/dagre'
import ReactFlow, { BaseEdge, Background, Controls, MarkerType, MiniMap, Panel, Position, useNodesState, type Edge as FlowEdge, type Node as FlowNode, type NodeChange as FlowNodeChange } from 'reactflow'
import 'reactflow/dist/style.css'
import { ExternalLink, PanelRightClose, PanelRightOpen } from 'lucide-react'
import type { EntityType } from '../types'
import type { TeamEntity } from './index'
import CodeFileEditor, { type CodeFileDetail } from '../../shell/CodeFileEditor'
import EmptyState from '../../shell/EmptyState'
import { colors, fonts } from '../../shell/tokens'
import { Segmented } from '../../components/Segmented'
import { usePanels } from '../../stores/panelsStore'

interface TeamBuilderSummary {
  name: string
  spec_id: string
  nodes: number
  edges: number
  entry: string
}

interface TeamDefinitionRef {
  entity_type: EntityType
  entity_id: string
  file_path: string
  design_md_path: string | null
  has_design_md: boolean
  label: string
  symbol: string | null
  line_start: number | null
  line_end: number | null
  summary: string | null
  source_excerpt: string | null
  material: {
    id: string
    name: string
    description: string
    parent: string | null
    kind: string | null
    tags: string[]
    fields: Array<{ name: string; type: string | null; description: string; required: boolean }>
    required: string[]
  } | null
  worker: {
    class_name: string
    description: string
    format_in: string | string[] | null
    format_in_mode: string | null
    format_out: string | null
    /** 固定 prompt(NODE_PROMPT 等);动态拼接段后端已用 {…} 占位。无则 null */
    prompt: { attr: string; text: string } | null
  } | null
}

interface TeamGraphNode {
  id: string
  label: string
  kind: string
  maturity: string
  format_in: string[]
  format_out: string | null
  validator_kind: string | null
  validator_id: string | null
  method: string | null
  description: string
  is_entry: boolean
  definition: TeamDefinitionRef | null
}

interface TeamGraphEdge {
  id: string
  source: string
  target: string
  condition: string | null
  feedback: boolean
  material_id: string | null
  source_format: string | null
  target_format: string[]
}

interface TeamGraphMaterial {
  id: string
  producers: string[]
  consumers: string[]
  is_external_input: boolean
  is_terminal_output: boolean
  definition: TeamDefinitionRef | null
}

interface TeamGraphData {
  team_id: string
  source_path: string
  definition: TeamDefinitionRef | null
  spec_id: string
  name: string
  description: string
  purpose: string
  entry: string
  tags: string[]
  builders: TeamBuilderSummary[]
  selected_builder: string
  nodes: TeamGraphNode[]
  edges: TeamGraphEdge[]
  materials: TeamGraphMaterial[]
  health: {
    warnings: string[]
    builder_errors: Record<string, string>
    soft_nodes: number
    hard_nodes: number
    feedback_edges: number
    external_inputs: number
    terminal_outputs: number
  }
}

interface TeamRunSummary {
  trace_id: string
  task_desc: string | null
  source: string
  domains: string[]
  started_at: string | null
  ended_at: string | null
  event_count: number
  matched_event_count: number
  matched_nodes: string[]
  total_nodes: number
  tool_calls: number
  llm_calls: number
  agent_turns: number
  status: 'running' | 'finished' | 'error' | 'missing'
  verdict_counts: Record<string, number>
  last_event: string | null
}

interface TeamRunNodeStatus {
  node_id: string
  event_count: number
  first_at: string | null
  last_at: string | null
  event_types: Record<string, number>
  verdict_counts: Record<string, number>
}

interface TeamRunTimelineEvent {
  id: string
  timestamp: string | null
  event_type: string
  source: string
  node_ids: string[]
  description: string
  verdict: string | null
  format_in: string[]
  format_out: string[]
  input_signal: string
  output_signal: string
  diagnosis: string
  tool_calls: any[] | null
}

interface TeamRunDetail {
  team_id: string
  spec_id: string
  selected_builder: string
  trace_id: string
  summary: TeamRunSummary
  active_nodes: string[]
  inactive_nodes: string[]
  node_statuses: TeamRunNodeStatus[]
  material_observations: Array<{
    node_id: string
    event_type: string
    timestamp: string | null
    inputs: string[]
    outputs: string[]
    verdict: string | null
  }>
  timeline: TeamRunTimelineEvent[]
}

type ResourceKind = 'workspace' | 'database' | 'external'

interface TeamResourceHint {
  id: ResourceKind
  label: string
  description: string
  workers: string[]
  evidence: string[]
  confidence: '演示推断' | '运行推断'
}

interface TeamDoctorFinding {
  id: string
  check_id: string
  level: 'blocking' | 'degrading' | 'advisory' | 'info' | string
  severity: string
  location: string
  target_kind: 'node' | 'edge' | 'material' | 'team' | 'unknown' | string
  target_id: string
  node_ids: string[]
  edge_ids: string[]
  material_ids: string[]
  observation: string
  implication: string
  cross_refs: string[]
}

interface TeamDoctorHealth {
  team_id: string
  spec_id: string
  selected_builder: string
  source_path: string
  status: 'healthy' | 'degraded' | 'unhealthy' | string
  passed: boolean
  counts: {
    blocking: number
    degrading: number
    advisory: number
    info: number
    total: number
  }
  checks: Array<{ id: string; description: string; default_on: boolean }>
  findings: TeamDoctorFinding[]
}

interface TeamMaterializationLink {
  material_id: string
  direction: string
  confidence: string
  basis: string
  registration_status: string
  resource_kind: string
  target: string
  target_key: string
  normalized_target: string
  candidate_kind: string
  candidate_reason: string
  promotion_hint: string
  candidate_material_id: string
  matched_material_ids: string[]
  human_title: string
  human_summary: string
  evidence_summary: string
  target_title: string
  target_summary: string
  target_excerpt: string
  target_exists: boolean
  rel_path: string
  content_kind: string
  bytes: number | null
  evidence: string[]
}

interface TeamMaterializationLinkGroup {
  id: string
  linkKind: 'resource' | 'inferred'
  worker_id: string
  label: string
  summary: string
  evidence_summary: string
  count: number
  links: TeamMaterializationLink[]
  sample_targets: string[]
  matched_material_ids: string[]
}

interface TeamMaterializationWorkerRun {
  worker_id: string
  status: string
  parse_status: string
  provider: string
  run_id: string
  rel_path: string
  changed_files: string[]
  observed_read_targets: string[]
  material_io_links: TeamMaterializationLink[]
  produced_content_materials: TeamMaterializationLink[]
  resource_material_links: TeamMaterializationLink[]
  inferred_material_read_links: TeamMaterializationLink[]
  static_field_access: {
    input_field_reads: Record<string, string[]>
    missing_input_required: Record<string, string[]>
    missing_output_required: string[]
    output_field_writes: string[]
  }
}

interface TeamMaterializationReviewIssue {
  worker_id: string
  severity: string
  category: string
  issue: string
  fix_hint: string
  format_in: string[]
  required_not_read: string[]
}

interface TeamBuilderMaterialization {
  available: boolean
  reason?: string
  run_id: string
  summary_path: string
  provider: string
  started_at_local: string
  team_name: string
  review: {
    kind: string
    verdict: string
    critical_count: number
    warning_count: number
    diagnosis: string
    issues: TeamMaterializationReviewIssue[]
  }
  counts: {
    worker_success_count: number
    worker_fail_count: number
    compile_fail_count: number
    declared_material_links: number
    generated_candidates: number
    resource_candidates: number
    workers_with_missing_required: number
  }
  worker_runs: TeamMaterializationWorkerRun[]
}

interface MaterialAttributionReportLink {
  kind: string
  kind_label: string
  title: string
  material_id: string
  direction: string
  confidence: string
  registration_status: string
  resource_kind: string
  target: string
  rel_path: string
  summary: string
  evidence_summary: string
  target_summary: string
  target_excerpt: string
  promotion_hint: string
  matched_material_ids?: string[]
  declared_material_ids?: string[]
  evidence: string[]
  source_filter: {
    worker: string
    material: string
    target: string
  }
}

interface MaterialAttributionQualityGate {
  id: string
  name: string
  status: 'pass' | 'warning' | 'fail' | string
  summary: string
  evidence: string[]
}

interface MaterialAttributionWorkerReport {
  worker_id: string
  worker_name: string
  status: 'pass' | 'warning' | 'fail' | string
  summary: string
  declared_io: MaterialAttributionReportLink[]
  generated_artifacts: MaterialAttributionReportLink[]
  read_clues: MaterialAttributionReportLink[]
  confirmed_reads: MaterialAttributionReportLink[]
  field_contract: {
    status: string
    summary: string
    input_field_reads: Record<string, string[]>
    missing_input_required: Record<string, string[]>
    missing_output_required: string[]
    output_field_writes: string[]
  }
  risks: string[]
  next_actions: string[]
}

interface MaterialAttributionReadGroup {
  id: string
  worker_id: string
  group_kind: 'tool_clues' | 'unconfirmed' | 'confirmed' | string
  status: 'evidence' | 'candidate' | 'confirmed' | string
  title: string
  summary: string
  decision: string
  next_action: string
  count: number
  material_count: number
  sample_targets: string[]
  sample_material_ids: string[]
  evidence: string[]
  source_filter: {
    worker: string
    material: string
    target: string
  }
}

interface MaterialAttributionReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  provider: string
  started_at_local: string
  summary: string
  verdict: 'pass' | 'warning' | 'fail' | string
  quality_gates: MaterialAttributionQualityGate[]
  counts: {
    workers: number
    declared_io: number
    generated_artifacts: number
    read_clues: number
    confirmed_reads: number
    read_groups?: number
    unconfirmed_read_clues?: number
    field_contract_failures: number
    review_issues: number
  }
  worker_reports: MaterialAttributionWorkerReport[]
  read_groups?: MaterialAttributionReadGroup[]
  open_questions: Array<{ worker_id: string; summary: string; next_action: string }>
  source: {
    summary_path: string
    materialization_endpoint: string
  }
}

interface TeamBuilderTestReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  summary: string
  verdict: 'pass' | 'warning' | 'fail' | string
  quality_gates: MaterialAttributionQualityGate[]
  counts: {
    files: number
    python_files: number
    worker_files: number
    syntax_failures: number
    nodes: number
    bindings: number
    executed_workers?: number
    stubbed_workers?: number
    skipped_workers?: number
    failed_workers?: number
    doctor_findings?: number
  }
  smoke: {
    team_id: string
    entry: string
    nodes: string[]
    binding_keys: string[]
    missing_bindings: string[]
    error: string
  }
  worker_run_smoke?: {
    status: 'pass' | 'warning' | 'fail' | string
    executed_workers: Array<{
      worker_id: string
      kind: string
      input_materials?: string[]
      output_material?: string
      diagnosis?: string
      output_summary?: unknown
    }>
    stubbed_workers?: Array<{
      worker_id: string
      kind: string
      input_materials?: string[]
      output_material?: string
      diagnosis?: string
      output_summary?: unknown
      stub?: string
      llm_stub_calls?: TeamBuilderLlmStubCall[]
    }>
    skipped_workers: Array<{
      worker_id: string
      reason: string
      summary?: string
      missing_inputs?: string[]
    }>
    failed_workers: Array<{
      worker_id: string
      kind?: string
      diagnosis?: string
      input_materials?: string[]
      output_material?: string
    }>
    seed_materials: string[]
    produced_materials: string[]
    llm_stub_calls?: TeamBuilderLlmStubCall[]
    error: string
  }
  doctor_findings?: TeamDoctorFinding[]
  contract_coverage?: {
    available: boolean
    verdict: 'pass' | 'warning' | 'fail' | string
    status: string
    summary: string
    counts: {
      available_contracts: number
      matching_contracts: number
      executed_contracts: number
      missing_contracts: number
    }
    quality_gates: MaterialAttributionQualityGate[]
    matching_contracts: Array<{
      slug: string
      pipeline_name?: string
      path: string
      mode?: string
      status?: string
    }>
    available_contracts: Array<{
      slug: string
      pipeline_name?: string
      path: string
      mode?: string
      status?: string
    }>
    latest_execution?: {
      available?: boolean
      verdict?: 'pass' | 'warning' | 'fail' | string
      status?: string
      summary?: string
      counts?: {
        matching_contracts?: number
        executed_contracts?: number
        passed_contracts?: number
        failed_contracts?: number
      }
      contracts?: Array<{
        slug?: string
        pipeline_name?: string
        path?: string
        status?: string
        returncode?: number
        command?: string
        stdout_tail?: string
        stderr_tail?: string
      }>
    }
    next_action: string
    source?: {
      contract_root?: string
      contract_coverage_material?: string
      contract_execution_material?: string
    }
  }
  source: {
    code_package_files: string
    test_package_dir: string
    report_material: string
    doctor_findings_material?: string
    contract_coverage_material?: string
    contract_execution_material?: string
  }
}

interface TeamBuilderLlmStubCall {
  model?: string
  max_tokens?: number
  system_chars?: number
  user_chars?: number
  system_preview?: string
  user_preview?: string
  expected_output_keys?: string[]
  stub_response_keys?: string[]
  has_json_instruction?: boolean
  has_chinese_instruction?: boolean
}

interface TeamBuilderRepairPlan {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'validation_gap' | 'repair_required' | 'unavailable' | string
  summary: string
  counts: {
    actions: number
    repair_required: number
    validation_gap: number
    observe_only: number
    auto_safe: number
  }
  actions: Array<{
    id: string
    finding_id: string
    check_id: string
    level: string
    location: string
    category: string
    auto_safe: boolean
    observation: string
    rationale: string
    next_action: string
    validation_actions?: Array<{
      id: string
      title: string
      summary: string
      action_kind: string
      endpoint?: string
      command?: string
      expected_result?: string
      safety?: string
    }>
    node_ids: string[]
    material_ids: string[]
  }>
  source: {
    repair_plan_material?: string
    repair_safety_policy_endpoint?: string
  }
}

interface TeamBuilderRepairSafetyPolicy {
  available: boolean
  run_id: string
  version: string
  summary: string
  counts: {
    rules: number
    auto_safe_rules: number
    patch_plan_only_rules: number
    manual_or_none_rules: number
  }
  rules: Array<{
    id: string
    name: string
    category: string
    automation_level: string
    auto_safe: boolean
    next_action: string
    rationale: string
  }>
  source: {
    repair_safety_policy_material?: string
  }
}

interface TeamBuilderRepairProbeReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'pass' | 'warning' | 'fail' | string
  summary: string
  counts: {
    captured_failures: number
    doctor_findings: number
    repair_required: number
    validation_gap: number
    auto_safe: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  worker_run_smoke?: {
    status: string
    failed_workers: Array<{
      worker_id: string
      kind: string
      diagnosis: string
      input_materials?: string[]
      output_material?: string
    }>
    executed_workers?: Array<{ worker_id: string; kind: string; diagnosis: string }>
    error?: string
  }
  doctor_findings: TeamDoctorFinding[]
  repair_plan: {
    verdict: string
    summary: string
    counts: {
      actions: number
      repair_required: number
      validation_gap: number
      auto_safe: number
    }
    actions: TeamBuilderRepairPlan['actions']
  }
  source: {
    probe_package_dir?: string
    repair_probe_material?: string
  }
}

interface TeamBuilderRepairDryRunReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'pass' | 'warning' | 'fail' | string
  summary: string
  counts: {
    before_failures: number
    before_findings: number
    repair_required: number
    patch_files: number
    after_failures: number
    after_findings: number
    fixed_workers: number
    auto_safe: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  patch_plan: {
    id?: string
    title?: string
    summary?: string
    finding_ids?: string[]
    policy_rule_ids?: string[]
    changed_files?: string[]
    dry_run_applied?: boolean
    scope?: string
    auto_safe?: boolean
    rationale?: string
    verification_commands?: string[]
    diff?: string
  }
  before?: {
    worker_run_smoke?: {
      status?: string
      failed_workers?: Array<{ worker_id: string; kind: string; diagnosis: string }>
      doctor_findings?: TeamDoctorFinding[]
    }
    repair_actions?: TeamBuilderRepairPlan['actions']
  }
  after?: {
    worker_run_smoke?: {
      status?: string
      executed_workers?: Array<{ worker_id: string; kind: string; diagnosis: string }>
      failed_workers?: Array<{ worker_id: string; kind: string; diagnosis: string }>
      doctor_findings?: TeamDoctorFinding[]
    }
  }
  source: {
    probe_package_dir?: string
    patched_file?: string
    repair_dry_run_material?: string
  }
}

interface TeamBuilderRepairPatchCandidatesReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'ready_for_manual_patch' | 'needs_locator_or_dry_run' | 'unavailable' | string
  summary: string
  counts: {
    actions: number
    candidates: number
    source_located: number
    source_missing?: number
    dry_run_verified: number
    auto_safe: number
    manual_required?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  candidates: Array<{
    id: string
    status: 'source_located' | 'needs_source_locator' | 'not_applicable' | string
    finding_id: string
    check_id: string
    worker_id: string
    category: string
    policy_rule_id: string
    automation_level: string
    auto_safe: boolean
    summary: string
    observation: string
    next_action: string
    source_candidates: Array<{
      path: string
      exists: boolean
      material_ids?: string[]
      excerpt?: string
    }>
    contract_sources?: Array<{
      path: string
      exists: boolean
      material_ids?: string[]
      excerpt?: string
    }>
    proposed_patch: {
      mode: string
      scope: string
      changed_files: string[]
      diff?: string
      reason?: string
    }
    verification_commands: string[]
    safety: {
      dry_run_first: boolean
      requires_human_confirmation: boolean
      auto_apply_allowed: boolean
      reason: string
    }
  }>
  dry_run_reference?: {
    verdict?: string
    summary?: string
    counts?: Record<string, any>
    source?: Record<string, any>
  }
  source: {
    repair_patch_candidates_material?: string
    repair_plan_material?: string
    test_report_endpoint?: string
    repair_dry_run_endpoint?: string
  }
}

interface TeamBuilderRepairApplyGateReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'ready_for_human_review' | 'blocked' | 'unavailable' | string
  summary: string
  counts: {
    candidates: number
    source_located: number
    dry_run_verified: number
    manual_required: number
    auto_apply_allowed: number
    review_items: number
    apply_ready: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  review_items: Array<{
    id: string
    candidate_id: string
    status: 'ready_for_human_review' | 'blocked' | string
    check_id?: string
    worker_id: string
    finding_id: string
    policy_rule_id: string
    changed_files: string[]
    source_files: string[]
    contract_files?: string[]
    required_confirmations: string[]
    verification_commands: string[]
    apply_modes: Array<{
      id: string
      name: string
      allowed: boolean
      summary: string
    }>
    blocked_reasons: string[]
    safety: {
      auto_apply_allowed: boolean
      requires_human_confirmation: boolean
      reason: string
    }
  }>
  source: {
    repair_apply_gate_material?: string
    repair_patch_candidates_endpoint?: string
    repair_patch_candidates_material?: string
  }
}

interface TeamBuilderRepairPatchDiffProposalReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'diff_ready' | 'needs_ai_or_human_diff' | 'blocked' | 'unavailable' | string
  summary: string
  counts: {
    candidates: number
    diff_ready: number
    needs_ai_or_human_diff: number
    blocked: number
    unsafe_targets: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  proposals: Array<{
    id: string
    candidate_id: string
    status: 'diff_ready' | 'needs_ai_or_human_diff' | 'blocked' | string
    check_id: string
    worker_id: string
    finding_id: string
    changed_files: string[]
    diff?: string
    diff_source?: string
    reason: string
    missing_requirements: string[]
    patch_request: {
      summary: string
      context_files: string[]
      verification_commands: string[]
    }
    safety: {
      writes_files: boolean
      applies_to_real_code: boolean
      requires_human_confirmation: boolean
      reason: string
    }
  }>
  source: {
    repair_patch_diff_proposal_material?: string
    repair_patch_candidates_endpoint?: string
    repair_apply_gate_endpoint?: string
  }
}

interface TeamBuilderRepairApprovalReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'approved' | 'awaiting_approval' | 'stale_or_mismatch' | 'unavailable' | string
  summary: string
  counts: {
    proposals: number
    approvable: number
    approved: number
    awaiting_approval: number
    stale_or_mismatch: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  approval_items: Array<{
    candidate_id: string
    proposal_id: string
    status: 'approved' | 'awaiting_approval' | 'stale_or_mismatch' | 'not_approvable' | string
    approval_valid: boolean
    approvable: boolean
    diff_sha256: string
    approved_by?: string
    approved_at?: string
    summary: string
    changed_files: string[]
    stale_records: number
  }>
  source: {
    repair_patch_diff_proposal_endpoint?: string
    repair_approval_records_material?: string
    repair_approval_report_material?: string
  }
}

interface TeamBuilderRepairExecutionReadinessReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'waiting_for_patch_diff' | 'awaiting_explicit_approval' | 'ready_for_explicit_apply' | 'blocked' | 'unavailable' | string
  summary: string
  counts: {
    candidates: number
    review_ready: number
    diff_ready: number
    approval_recorded: number
    execution_ready: number
    blocked: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  execution_items: Array<{
    id: string
    candidate_id: string
    status: 'waiting_for_patch_diff' | 'awaiting_explicit_approval' | 'ready_for_explicit_apply' | 'blocked' | string
    check_id: string
    worker_id: string
    finding_id: string
    changed_files: string[]
    contract_files?: string[]
    review_item_status: string
    has_diff: boolean
    approval_recorded: boolean
    missing_requirements: string[]
    verification_commands: string[]
    safety: {
      auto_apply_allowed: boolean
      requires_explicit_approval: boolean
      reason: string
    }
  }>
  source: {
    repair_execution_readiness_material?: string
    repair_apply_gate_endpoint?: string
    repair_patch_candidates_endpoint?: string
  }
}

interface TeamBuilderRepairApplyPreviewReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'preview_ready' | 'blocked' | 'unavailable' | string
  summary: string
  counts: {
    items: number
    preview_ready: number
    blocked: number
    files_written: number
    files_previewed?: number
    multi_file_preview_ready?: number
    real_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  preview_items: Array<{
    id: string
    candidate_id: string
    status: 'preview_ready' | 'blocked' | string
    changed_files: string[]
    file_count?: number
    multi_file?: boolean
    before_preview_files: string[]
    after_preview_files: string[]
    file_previews?: Array<{
      changed_file: string
      before_preview_file: string
      after_preview_file: string
      before_sha256: string
      after_sha256: string
      diff_sha256: string
    }>
    blocked_reasons: string[]
    diff_sha256: string
    safety: {
      scope: string
      writes_real_files: boolean
      requires_final_apply_confirmation: boolean
      reason: string
    }
  }>
  source: {
    repair_apply_preview_material?: string
    repair_execution_readiness_endpoint?: string
  }
}

interface TeamBuilderRepairApplyExecutionReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'ready_for_explicit_apply' | 'applied' | 'blocked' | 'stale_or_mismatch' | 'unavailable' | string
  summary: string
  counts: {
    items: number
    preview_ready: number
    applied: number
    blocked: number
    stale_or_mismatch: number
    real_writes: number
    file_set_ready?: number
    file_set_applied?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  apply_items: Array<{
    id: string
    candidate_id: string
    status: 'ready_for_explicit_apply' | 'applied' | 'blocked' | 'stale_or_mismatch' | string
    summary: string
    changed_files: string[]
    preview_status: string
    diff_sha256: string
    applied_at?: string
    applied_by?: string
    target_current_sha256?: string
    applied_after_sha256?: string
    file_set?: boolean
    file_count?: number
    file_records?: TeamBuilderRepairFileRecord[]
    real_writes: number
    blocked_reasons: string[]
  }>
  records: Array<{
    id: string
    candidate_id: string
    applied: boolean
    applied_by: string
    applied_at: string
    reason: string
    diff_sha256: string
    changed_file: string
    changed_files?: string[]
    before_sha256: string
    after_sha256: string
    after_preview_file: string
    file_set?: boolean
    file_count?: number
    file_records?: TeamBuilderRepairFileRecord[]
    real_writes: number
  }>
  source: {
    repair_apply_preview_endpoint?: string
    repair_apply_execution_records_material?: string
    repair_apply_execution_report_material?: string
  }
}

interface TeamBuilderRepairPostApplyVerificationReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'awaiting_verification' | 'pass' | 'fail' | 'unavailable' | string
  summary: string
  counts: {
    applied: number
    verified: number
    pending: number
    failed: number
    contract_failed?: number
    doctor_findings?: number
    repair_required?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  verification_items: Array<{
    id: string
    candidate_id: string
    status: 'pending_verification' | 'pass' | 'fail' | string
    summary: string
    changed_files: string[]
    required_commands: string[]
  }>
  source: {
    repair_apply_execution_endpoint?: string
    repair_post_apply_verification_material?: string
  }
}

interface TeamBuilderRepairOutcomeReconciliationReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'pass' | 'awaiting_verification' | 'missing_baseline' | 'partial' | 'regression' | 'unavailable' | string
  summary: string
  counts: {
    applied: number
    reconciled: number
    missing_baseline: number
    resolved_findings: number
    introduced_findings: number
    persistent_findings: number
    pending_verification: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  reconciliation_items: Array<{
    id: string
    candidate_id: string
    status: 'reconciled' | 'pending_verification' | 'missing_baseline' | 'partial' | 'regression' | string
    summary: string
    changed_file: string
    changed_files?: string[]
    file_set?: boolean
    file_count?: number
    diff_sha256: string
    before: {
      doctor_verdict: string
      doctor_findings: number
      repair_verdict: string
      repair_required: number
      closure_verdict: string
    }
    after: {
      doctor_verdict: string
      doctor_findings: number
      repair_verdict: string
      repair_required: number
      closure_verdict: string
    }
    resolved_findings: Array<{ key: string; check_id: string; observation: string }>
    introduced_findings: Array<{ key: string; check_id: string; observation: string }>
    persistent_findings: Array<{ key: string; check_id: string; observation: string }>
  }>
  source: {
    repair_apply_execution_endpoint?: string
    repair_post_apply_verification_endpoint?: string
    repair_outcome_reconciliation_material?: string
  }
}

interface TeamBuilderRepairRollbackReadinessReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'ready_for_explicit_rollback' | 'stale_or_mismatch' | 'missing_before_snapshot' | 'blocked' | 'unavailable' | string
  summary: string
  counts: {
    applied: number
    rollback_ready: number
    blocked: number
    stale_or_mismatch: number
    missing_before_snapshot: number
    real_writes: number
    file_set_ready?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  rollback_items: Array<{
    id: string
    candidate_id: string
    status: 'ready_for_explicit_rollback' | 'stale_or_mismatch' | 'missing_before_snapshot' | 'blocked' | string
    summary: string
    changed_file: string
    changed_files?: string[]
    file_set?: boolean
    file_count?: number
    file_records?: TeamBuilderRepairFileRecord[]
    diff_sha256: string
    applied_at?: string
    applied_by?: string
    before_sha256: string
    after_sha256: string
    current_sha256: string
    before_preview_file: string
    before_snapshot_sha256: string
    target_scope_safe: boolean
    current_matches_after: boolean
    before_snapshot_valid: boolean
    blocked_reasons: string[]
  }>
  source: {
    repair_apply_execution_endpoint?: string
    repair_rollback_readiness_material?: string
  }
}

interface TeamBuilderRepairRollbackExecutionReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'ready_for_explicit_rollback' | 'rolled_back' | 'blocked' | 'stale_or_mismatch' | 'unavailable' | string
  summary: string
  counts: {
    items: number
    ready: number
    rolled_back: number
    blocked: number
    stale_or_mismatch: number
    real_writes: number
    file_set_ready?: number
    file_set_rolled_back?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  rollback_items: Array<{
    id: string
    candidate_id: string
    status: 'ready_for_explicit_rollback' | 'rolled_back' | 'blocked' | 'stale_or_mismatch' | string
    summary: string
    changed_file: string
    changed_files?: string[]
    file_set?: boolean
    file_count?: number
    file_records?: TeamBuilderRepairFileRecord[]
    before_sha256: string
    after_sha256: string
    current_sha256: string
    rolled_back_at?: string
    rolled_back_by?: string
    rollback_from_sha256: string
    rollback_to_sha256: string
    real_writes: number
    blocked_reasons: string[]
  }>
  records: Array<{
    id: string
    candidate_id: string
    rolled_back: boolean
    rolled_back_by: string
    rolled_back_at: string
    reason: string
    changed_file: string
    changed_files?: string[]
    file_set?: boolean
    file_count?: number
    file_records?: TeamBuilderRepairFileRecord[]
    rollback_from_sha256: string
    rollback_to_sha256: string
    real_writes: number
  }>
  source: {
    repair_rollback_readiness_endpoint?: string
    repair_rollback_execution_records_material?: string
    repair_rollback_execution_report_material?: string
  }
}

interface TeamBuilderRepairRollbackPostVerificationReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'awaiting_verification' | 'pass' | 'fail' | 'unavailable' | string
  summary: string
  counts: {
    rolled_back: number
    verified: number
    pending: number
    failed: number
    doctor_findings?: number
    repair_required?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  verification_items: Array<{
    id: string
    candidate_id: string
    status: 'pending_verification' | 'pass' | 'fail' | string
    summary: string
    changed_files: string[]
    required_commands: string[]
  }>
  source: {
    repair_rollback_execution_endpoint?: string
    repair_rollback_post_verification_material?: string
  }
}

interface TeamBuilderRepairClosureRollupReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'action_required' | 'blocked' | 'unavailable' | string
  summary: string
  counts: {
    stages: number
    pending_stages: number
    failed_stages: number
    repair_required: number
    validation_gap: number
    candidates: number
    review_items: number
    diff_ready: number
    approved: number
    execution_ready: number
    preview_ready: number
    applied: number
    apply_real_writes: number
    post_apply_pending: number
    post_apply_failed: number
    reconciled: number
    rollback_ready: number
    rolled_back: number
    rollback_real_writes: number
    rollback_post_pending: number
    rollback_post_failed: number
    multi_candidate_count?: number
    multi_file_candidate_count?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  stages: Array<{
    id: string
    name: string
    status: string
    summary: string
    endpoint: string
    counts: Record<string, number>
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  generalization?: {
    summary: string
    candidate_count: number
    multi_file_candidate_count: number
    single_file_execution_limit: boolean
    blockers: string[]
    next_validation: string
  }
  source: {
    repair_closure_rollup_material?: string
  }
}

interface TeamBuilderRepairFileRecord {
  changed_file: string
  before_sha256?: string
  after_sha256?: string
  current_sha256?: string
  before_preview_file?: string
  after_preview_file?: string
  before_snapshot_sha256?: string
  rollback_from_sha256?: string
  rollback_to_sha256?: string
  after_apply_sha256?: string
  after_rollback_sha256?: string
  diff_sha256?: string
  target_scope_safe?: boolean
  current_matches_after?: boolean
  before_snapshot_valid?: boolean
}

interface TeamBuilderRepairGeneralizationTrialReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'guarded_trial_ready' | 'unavailable' | string
  summary: string
  counts: {
    candidate_count: number
    multi_file_candidate_count: number
    contract_target_count: number
    blocked_for_real_apply: number
    scratch_preview_required: number
    real_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  trial_cases: Array<{
    id: string
    name: string
    status: string
    summary: string
    evidence: string[]
  }>
  controlled_candidates: Array<{
    id: string
    priority: number
    title: string
    summary: string
    changed_files: string[]
    risk: string
    expected_handling: string
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
    post_endpoint?: string
    required_confirmations?: string[]
    approval_requirements?: string[]
    safety_note?: string
  }>
  source: {
    repair_generalization_trial_material?: string
  }
}

interface TeamBuilderRepairRealGeneratedFileSetTrialReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'pass' | 'fail' | 'unavailable' | string
  summary: string
  counts: {
    changed_files: number
    files_previewed: number
    files_applied: number
    files_rolled_back: number
    before_failures: number
    post_apply_passed: number
    rollback_restored: number
    scratch_generated_writes: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  changed_files: string[]
  file_records: TeamBuilderRepairFileRecord[]
  smoke: {
    before_worker?: { status?: string; failed_workers?: unknown[] }
    after_apply_worker?: { status?: string; failed_workers?: unknown[]; executed_workers?: unknown[] }
    after_rollback_worker?: { status?: string; failed_workers?: unknown[] }
  }
  source: {
    trial_package_dir?: string
    repair_real_generated_file_set_trial_material?: string
  }
}

interface TeamBuilderRepairRealRunCandidate {
  run_id: string
  team_name: string
  classification: string
  summary: string
  counts: {
    critical: number
    warnings: number
    failed_workers: number
    doctor_findings: number
    repair_required: number
    validation_gap: number
    patch_candidates: number
    source_files: number
  }
  source_ready: boolean
  doctor_ready: boolean
  candidate_ready: boolean
  evidence: string[]
  source_files: string[]
  materials: Array<{
    label: string
    path: string
    available: boolean
  }>
}

interface TeamBuilderRepairRealRunCandidateScanReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'candidate_ready' | 'failure_candidate_needs_doctor' | 'validation_gap_only' | 'no_real_failure_candidate' | 'unavailable' | string
  summary: string
  counts: {
    runs_scanned: number
    failure_candidates: number
    repair_ready_candidates: number
    validation_gap_runs: number
    clean_runs: number
    source_ready_candidates: number
    doctor_ready_candidates: number
    patch_candidate_sets: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  candidates: TeamBuilderRepairRealRunCandidate[]
  run_summaries: TeamBuilderRepairRealRunCandidate[]
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
    post_endpoint?: string
    required_confirmations?: string[]
    approval_requirements?: string[]
    safety_note?: string
  }>
  source: {
    scan_material?: string
    materialization_root?: string
    latest_run?: string
  }
}

interface TeamBuilderRepairRealRunReplayPlanReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'repair_plan_ready' | 'no_failed_candidate' | 'no_repair_action' | 'unavailable' | string
  summary: string
  counts: {
    code_review_issues: number
    repair_required: number
    source_located: number
    source_missing: number
    diffs_generated: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  findings: Array<{
    id: string
    check_id: string
    level: string
    severity: string
    target_kind: string
    target_id: string
    location: string
    category: string
    observation: string
    implication: string
    source_file: string
    required_not_read: string[]
    format_in: string[]
    evidence: string[]
  }>
  repair_actions: Array<{
    id: string
    finding_id: string
    category: string
    automation_level: string
    auto_safe: boolean
    worker_id: string
    changed_files: string[]
    required_input_fields: string[]
    proposed_change: string
    verification: string[]
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    candidate_scan_endpoint?: string
    code_review_report?: string
    candidate_run_dir?: string
    replay_plan_material?: string
  }
}

interface TeamBuilderRepairRealRunDiffRecord {
  id: string
  action_id: string
  worker_id: string
  changed_file: string
  required_input_fields: string[]
  change_summary: string[]
  diff: string
  diff_sha256: string
  before_sha256: string
  after_sha256: string
  before_preview_file: string
  after_preview_file: string
}

interface TeamBuilderRepairRealRunDiffPreviewReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'diff_preview_ready' | 'blocked' | 'no_repair_actions' | string
  summary: string
  counts: {
    repair_actions: number
    diff_ready: number
    files_previewed: number
    blocked: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  diff_records: TeamBuilderRepairRealRunDiffRecord[]
  blocked_items: Array<{
    action_id?: string
    changed_file?: string
    reason: string
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    replay_plan_endpoint?: string
    candidate_run_dir?: string
    diff_preview_material?: string
    preview_root?: string
  }
}

interface TeamBuilderRepairRealRunDiffReviewItem {
  id: string
  record_id: string
  worker_id: string
  status: 'ready_for_explicit_review' | 'blocked' | string
  summary: string
  changed_file: string
  change_summary: string[]
  required_input_fields: string[]
  target_scope_safe: boolean
  source_matches_before: boolean
  current_source_sha256: string
  before_sha256: string
  after_sha256: string
  diff_sha256: string
  before_preview_file: string
  after_preview_file: string
  risk_notes: string[]
  review_questions: string[]
  evidence_links: string[]
  blocked_reasons: string[]
}

interface TeamBuilderRepairRealRunDiffReviewReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'review_ready' | 'blocked' | 'no_diff_preview' | string
  summary: string
  counts: {
    diff_records: number
    ready_for_review: number
    blocked: number
    requires_explicit_approval: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  review_items: TeamBuilderRepairRealRunDiffReviewItem[]
  blocked_items: Array<{
    record_id?: string
    changed_file?: string
    reasons?: string[]
    reason?: string
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    diff_preview_endpoint?: string
    diff_preview_material?: string
    diff_review_material?: string
  }
}

interface TeamBuilderRepairRealRunApplyGateItem {
  id: string
  review_item_id: string
  record_id: string
  worker_id: string
  status: 'ready_for_explicit_apply_preview' | 'blocked' | string
  summary: string
  changed_file: string
  required_input_fields: string[]
  diff_sha256: string
  before_sha256: string
  after_sha256: string
  current_source_sha256: string
  before_preview_file: string
  after_preview_file: string
  required_confirmations: string[]
  post_apply_verification: string[]
  rollback_requirement: string
  blocked_reasons: string[]
}

interface TeamBuilderRepairRealRunApplyGateReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'ready_for_explicit_apply_preview' | 'blocked' | 'no_review_ready_diff' | string
  summary: string
  counts: {
    review_items: number
    apply_preview_ready: number
    blocked: number
    required_confirmation_tokens: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  apply_items: TeamBuilderRepairRealRunApplyGateItem[]
  blocked_items: Array<{
    review_item_id?: string
    changed_file?: string
    reasons: string[]
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    diff_review_endpoint?: string
    diff_review_material?: string
    apply_gate_material?: string
  }
}

interface TeamBuilderRepairRealRunApplyPreviewItem {
  id: string
  apply_item_id: string
  worker_id: string
  status: 'preview_ready' | 'blocked' | string
  summary: string
  changed_files: string[]
  file_set: boolean
  file_count: number
  before_preview_files: string[]
  after_preview_files: string[]
  file_records: TeamBuilderRepairFileRecord[]
  required_confirmations: string[]
  post_apply_verification: string[]
  rollback_requirement: string
  blocked_reasons: string[]
  safety: {
    scope: string
    writes_real_files: boolean
    requires_final_apply_confirmation: boolean
    reason: string
  }
}

interface TeamBuilderRepairRealRunApplyPreviewReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'preview_ready' | 'blocked' | 'no_apply_gate_ready_item' | string
  summary: string
  counts: {
    apply_items: number
    preview_ready: number
    files_previewed: number
    blocked: number
    required_confirmation_tokens: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  preview_items: TeamBuilderRepairRealRunApplyPreviewItem[]
  blocked_items: Array<{
    apply_item_id?: string
    changed_file?: string
    reasons: string[]
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    apply_gate_endpoint?: string
    apply_gate_material?: string
    apply_preview_material?: string
    preview_root?: string
  }
}

interface TeamBuilderRepairRealRunApplyExecutionItem {
  id: string
  apply_item_id: string
  worker_id: string
  status: 'ready_for_explicit_apply' | 'applied' | 'blocked' | 'stale_or_mismatch' | string
  summary: string
  changed_files: string[]
  file_set: boolean
  file_count: number
  file_records: TeamBuilderRepairFileRecord[]
  required_confirmations: string[]
  applied_at?: string
  applied_by?: string
  real_writes: number
  blocked_reasons: string[]
}

interface TeamBuilderRepairRealRunApplyExecutionReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'ready_for_explicit_apply' | 'applied' | 'blocked' | 'stale_or_mismatch' | string
  summary: string
  counts: {
    items: number
    ready: number
    applied: number
    blocked: number
    stale_or_mismatch: number
    real_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  apply_items: TeamBuilderRepairRealRunApplyExecutionItem[]
  records: Array<{
    id: string
    apply_item_id: string
    applied: boolean
    applied_by: string
    applied_at: string
    real_writes: number
    file_records: TeamBuilderRepairFileRecord[]
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    apply_preview_endpoint?: string
    apply_execution_records_material?: string
    apply_execution_report_material?: string
  }
}

interface TeamBuilderRepairRealRunPostApplyVerificationItem {
  id: string
  apply_item_id: string
  worker_id: string
  status: 'pending_verification' | 'verified' | 'failed' | string
  summary: string
  changed_files: string[]
  required_fields_checked?: number
  missing_required_fields?: string[]
  required_commands?: string[]
}

interface TeamBuilderRepairRealRunPostApplyVerificationReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'awaiting_apply' | 'awaiting_replay_verification' | 'pass' | 'warning' | 'fail' | string
  summary: string
  counts: {
    applied: number
    verified: number
    pending: number
    failed: number
    warnings: number
    ready: number
    real_repo_writes: number
    fields_checked?: number
    missing_required_fields?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  verification_items: TeamBuilderRepairRealRunPostApplyVerificationItem[]
  field_checks?: Array<{
    changed_file: string
    required_fields: string[]
    missing_fields: string[]
    status: string
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    apply_execution_endpoint?: string
    post_apply_verification_material?: string
    code_package_files?: string
  }
}

interface TeamBuilderRepairRealRunOutcomeReconciliationReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'awaiting_apply' | 'awaiting_verification' | 'pass' | 'warning' | 'partial' | 'regression' | 'missing_baseline' | string
  summary: string
  counts: {
    applied: number
    reconciled: number
    missing_baseline: number
    resolved_findings: number
    introduced_findings: number
    persistent_findings: number
    pending_verification: number
    warnings: number
    ready: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  reconciliation_items: Array<{
    id: string
    apply_item_id: string
    worker_id: string
    status: 'reconciled' | 'reconciled_with_warnings' | 'pending_verification' | 'missing_baseline' | 'partial' | 'regression' | string
    summary: string
    changed_files: string[]
    file_set: boolean
    file_count: number
    before: {
      baseline_findings: number
      repair_required: number
      replay_plan_verdict: string
    }
    after: {
      verification_verdict: string
      missing_required_fields: number
      failed_gates: number
      warning_gates: number
    }
    resolved_findings: Array<{ key: string; check_id: string; observation: string }>
    introduced_findings: Array<{ key: string; check_id: string; observation: string }>
    persistent_findings: Array<{ key: string; check_id: string; observation: string }>
    warnings?: Array<{ key: string; summary: string }>
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    apply_execution_endpoint?: string
    post_apply_verification_endpoint?: string
    replay_plan_endpoint?: string
    outcome_reconciliation_material?: string
  }
}

interface TeamBuilderRepairRealRunRollbackReadinessReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'awaiting_apply' | 'ready_for_explicit_rollback' | 'blocked' | 'stale_or_mismatch' | 'missing_before_snapshot' | string
  summary: string
  counts: {
    applied: number
    rollback_ready: number
    blocked: number
    stale_or_mismatch: number
    missing_before_snapshot: number
    ready: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  rollback_items: Array<{
    id: string
    apply_item_id: string
    worker_id: string
    status: 'ready_for_explicit_rollback' | 'blocked' | 'stale_or_mismatch' | 'missing_before_snapshot' | string
    summary: string
    changed_files: string[]
    file_set: boolean
    file_count: number
    file_records: TeamBuilderRepairFileRecord[]
    applied_at: string
    applied_by: string
    real_writes: number
    blocked_reasons: string[]
  }>
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    apply_execution_endpoint?: string
    outcome_reconciliation_endpoint?: string
    real_run_rollback_readiness_material?: string
  }
}

interface TeamBuilderRepairRealRunRollbackExecutionReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'awaiting_apply' | 'ready_for_explicit_rollback' | 'rolled_back' | 'blocked' | 'stale_or_mismatch' | string
  summary: string
  counts: {
    items: number
    ready: number
    rolled_back: number
    blocked: number
    stale_or_mismatch: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  rollback_items: Array<{
    id: string
    apply_item_id: string
    worker_id: string
    status: 'ready_for_explicit_rollback' | 'rolled_back' | 'blocked' | 'stale_or_mismatch' | string
    summary: string
    changed_files: string[]
    file_set: boolean
    file_count: number
    file_records: TeamBuilderRepairFileRecord[]
    rolled_back_at: string
    rolled_back_by: string
    real_writes: number
    blocked_reasons: string[]
  }>
  records: Array<{
    id: string
    apply_item_id: string
    rolled_back: boolean
    rolled_back_by: string
    rolled_back_at: string
    real_writes: number
    file_records: TeamBuilderRepairFileRecord[]
  }>
  source: {
    rollback_readiness_endpoint?: string
    rollback_execution_records_material?: string
    rollback_execution_report_material?: string
  }
}

interface TeamBuilderRepairRealRunRollbackPostVerificationReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'awaiting_apply' | 'awaiting_verification' | 'pass' | 'fail' | string
  summary: string
  counts: {
    rolled_back: number
    verified: number
    pending: number
    failed: number
    real_repo_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  verification_items: Array<{
    id: string
    apply_item_id: string
    worker_id: string
    status: 'pending_verification' | 'pass' | 'fail' | string
    summary: string
    changed_files: string[]
    required_commands?: string[]
    file_checks?: Array<{
      changed_file: string
      current_sha256: string
      expected_before_sha256: string
      matches_before: boolean
    }>
  }>
  source: {
    rollback_execution_endpoint?: string
    rollback_post_verification_material?: string
  }
}

interface TeamBuilderRepairRealRunClosureRollupReport {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'clean' | 'action_required' | 'blocked' | string
  summary: string
  counts: {
    stages: number
    pending_stages: number
    failed_stages: number
    failure_candidates: number
    repair_required: number
    ready_to_apply: number
    applied: number
    apply_real_writes: number
    verified: number
    reconciled: number
    rollback_ready: number
    rolled_back: number
    rollback_verified: number
    rollback_real_writes: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  stages: Array<{
    id: string
    name: string
    status: string
    summary: string
    endpoint: string
    counts: Record<string, number>
  }>
  approval_packet?: {
    available: boolean
    status: string
    title: string
    summary: string
    post_endpoint: string
    approval_requirements: string[]
    required_confirmations: string[]
    payload_template?: Record<string, unknown>
    decision_dossier?: {
      title: string
      decision_question: string
      why_now: string
      write_scope: string
      expected_effect: string
      do_not_use_as_completion: string
      post_approval_sequence: string[]
      human_review_focus: string[]
    }
    post_preflight?: {
      available: boolean
      status: 'ready_to_post' | 'blocked' | 'not_ready' | string
      summary: string
      conditions: Array<{
        id: string
        name: string
        status: 'pass' | 'warning' | 'fail' | string
        summary: string
        evidence: string[]
      }>
      blockers: string[]
    }
    auto_apply_policy?: {
      available: boolean
      verdict: string
      eligible: boolean
      summary: string
      counts: {
        candidate_items: number
        eligible_items: number
        blocked_items: number
        total_changed_files: number
        max_apply_items: number
        max_changed_files: number
        required_field_checks: number
        missing_required_fields: number
        real_repo_writes: number
      }
      blockers: string[]
      warnings: string[]
      execute_endpoint: string
      required_confirmation: string
    }
    apply_rehearsal?: {
      available: boolean
      verdict: string
      summary: string
      counts: {
        ready: number
        passed: number
        blocked: number
        scratch_writes: number
        real_repo_writes: number
        required_field_checks?: number
        missing_required_fields?: number
        files_without_required_contract?: number
      }
      material: string
      rehearsal_root: string
    }
    execution_playbook?: {
      available: boolean
      status: string
      title: string
      summary: string
      safety_note: string
      steps: Array<{
        id: string
        order: number
        method: 'GET' | 'POST' | string
        endpoint: string
        title: string
        summary: string
        writes_target_files: boolean
        can_execute_now: boolean
        required_confirmations: string[]
        payload_template?: Record<string, unknown>
        expected_next_verdict: string
      }>
    }
    items: Array<{
      apply_item_id: string
      worker_id: string
      status: string
      summary: string
      changed_files: string[]
      file_count: number
      file_records: Array<{
        changed_file: string
        before_sha256: string
        after_sha256: string
        current_sha256: string
        before_preview_file?: string
        after_preview_file?: string
      }>
      required_confirmations: string[]
      required_input_fields?: string[]
      problem_statement?: string
      impact_summary?: string
      intended_change?: string
      change_summary?: string[]
      review_questions?: string[]
      risk_notes?: string[]
      evidence_links?: Array<{
        label: string
        kind: 'endpoint' | 'material' | 'file' | string
        target: string
        summary: string
      }>
      post_apply_verification: string[]
      rollback_requirement: string
    }>
    safety_checks: string[]
    safety_note: string
  }
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
    post_endpoint?: string
    required_confirmations?: string[]
    approval_requirements?: string[]
    safety_note?: string
  }>
  source: {
    real_run_closure_rollup_material?: string
  }
}

interface TeamBuilderReadClueResolutionPlan {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'pass' | 'warning' | 'fail' | 'unavailable' | string
  summary: string
  counts: {
    read_clues: number
    confirmed: number
    confirmed_read_edges?: number
    unresolved: number
    candidate_materialized?: number
    candidate_materials?: number
    unexpanded?: number
    tool_scope_confirmed?: number
    tool_read_confirmed_materials?: number
    content_mention_path_clues?: number
    content_mention_path_materials?: number
    auto_expandable: number
    trace_replay_required?: number
    manual_review?: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  actions: TeamBuilderReadClueResolutionAction[]
  content_mention_actions?: TeamBuilderReadClueResolutionAction[]
  source: {
    material_report_endpoint?: string
    read_clue_resolution_plan_material?: string
  }
}

interface TeamBuilderReadClueResolutionAction {
    id: string
    worker_id: string
    title: string
    target: string
    category: string
    automation_level: string
    status: string
    evidence_summary: string
    reason: string
    next_action: string
    review_target?: string
    review_summary?: string
    review_examples?: Array<{
      path: string
      kind?: string
      line?: number
      excerpt?: string
      material_ids?: string[]
    }>
    material_id_hits?: string[]
    candidate_materials?: Array<{
      id: string
      worker_id?: string
      material_id: string
      path?: string
      line?: number
      kind?: string
      confidence?: string
      status?: string
      basis?: string
      excerpt?: string
      needs_confirmation?: boolean
    }>
    tool_confirmation?: {
      status?: string
      summary?: string
      matching_events?: Array<{
        index?: number
        tool?: string
        targets?: string[]
      }>
      confirmed_materials?: Array<{
        material_id: string
        path?: string
        kind?: string
        tool?: string
        event_index?: number
        evidence_kind?: string
        basis?: string
      }>
    }
    raw_evidence?: string[]
}

interface TeamBuilderMaterialGapValidation {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'pass' | 'warning' | 'fail' | 'unavailable' | string
  summary: string
  counts: {
    groups: number
    targets: number
    resolved_targets: number
    relocated_targets?: number
    material_id_hits: number
    missing_targets: number
  }
  groups: Array<{
    id: string
    worker_id: string
    title: string
    status: string
    summary: string
    targets: Array<{
      target: string
      status: string
      resolution_kind?: string
      resolution_note?: string
      resolved_paths: string[]
      material_ids: string[]
      decision: string
      examples?: Array<{
        path: string
        kind?: string
        line?: number
        excerpt?: string
        material_ids?: string[]
      }>
    }>
  }>
  source: {
    material_report_endpoint?: string
    material_gap_validation_material?: string
  }
}

interface TeamBuilderClosureStatus {
  available: boolean
  run_id: string
  team_name: string
  verdict: 'pass' | 'warning' | 'fail' | string
  summary: string
  stages: MaterialAttributionQualityGate[]
  missing: string[]
  source: {
    closure_status_material?: string
  }
}

interface TeamBuilderHighStandardAudit {
  available: boolean
  run_id: string
  team_name: string
  verdict: 'complete' | 'in_progress' | string
  completion_ready: boolean
  summary: string
  deliverables: Array<{
    id: string
    name: string
    status: 'pass' | 'warning' | 'fail' | string
    summary: string
    evidence: string[]
    endpoint: string
    next_action?: string
  }>
  quality_gates: MaterialAttributionQualityGate[]
  prompt_to_artifact_checklist?: {
    objective: string
    completion_rule: string
    status: 'complete' | 'not_complete' | string
    items: Array<{
      id: string
      requirement: string
      artifact: string
      status: 'pass' | 'warning' | 'fail' | string
      evidence: string[]
      covered_by_tests: string[]
      conclusion: string
      gap: string
    }>
    uncovered_or_incomplete: string[]
  }
  missing: string[]
  boundary_notes?: string[]
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    high_standard_audit_material?: string
  }
}

interface TeamBuilderProviderCoverageAudit {
  available: boolean
  run_id: string
  verdict: 'comparison_ready' | 'needs_more_evidence' | string
  comparison_ready: boolean
  summary: string
  counts: {
    runs_scanned: number
    same_input_trials_scanned?: number
    tracked_external_providers: number
    external_providers_with_real_runs: number
    external_providers_with_evidence?: number
    internal_model_records: number
    team_types_seen: number
    provider_team_type_counts?: Record<string, number>
  }
  providers: Array<{
    provider: string
    label: string
    role: string
    status: 'pass' | 'warning' | 'fail' | 'missing' | string
    summary: string
    runs: number
    successful_workers: number
    failed_workers: number
    compile_failures: number
    critical_reviews: number
    same_input_trials?: number
    trial_successful_workers?: number
    trial_failed_workers?: number
    trial_parse_failures?: number
    passing_evidence?: number
    latest_run_id: string
    latest_trial_id?: string
    team_type_count?: number
    team_names?: string[]
  }>
  internal_models: Array<{
    provider: string
    label: string
    role: string
    status: 'pass' | 'warning' | 'fail' | 'missing' | string
    summary: string
    runs: number
  }>
  missing: string[]
  boundary_notes?: string[]
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
  }>
  source: {
    provider_coverage_material?: string
  }
}

interface TeamBuilderProviderSameInputTrialPlan {
  available: boolean
  run_id: string
  title?: string
  verdict: 'ready_for_explicit_trial' | 'blocked' | string
  ready: boolean
  baseline_run_id: string
  team_name: string
  baseline_provider: string
  target_provider: string
  permission: string
  model_policy: string
  timeout_s: number
  summary: string
  counts: {
    workers: number
    materials: number
    baseline_external_runs: number
    missing: number
  }
  workers: Array<{
    worker_id: string
    cn_name: string
    impl_type: string
    format_in: string | string[]
    format_out: string
    baseline_provider: string
    baseline_status: string
    baseline_prompt_chars: number
    baseline_rel_path: string
  }>
  missing: string[]
  safety_gates: Array<{
    id: string
    name?: string
    status: 'pass' | 'warning' | 'fail' | string
    summary: string
    evidence?: string[]
  }>
  command: string
  next_actions: Array<{
    id: string
    title: string
    summary: string
    endpoint: string
    command?: string
  }>
  source: {
    same_input_trial_plan_material?: string
    baseline_summary?: string
    trial_root?: string
  }
}

interface TeamBuilderLlmReplayPlan {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'ready_for_controlled_replay' | 'blocked' | 'no_llm_call' | 'unavailable' | string
  summary: string
  counts: {
    calls: number
    ready: number
    blocked: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  actions: Array<{
    id: string
    worker_id: string
    call_index: number
    model: string
    max_tokens?: number
    status: 'ready' | 'blocked' | string
    missing_contract: string[]
    expected_output_keys: string[]
    stub_response_keys: string[]
    system_chars: number
    user_chars: number
    system_preview: string
    user_preview: string
    human_summary: string
    next_action: string
  }>
  execution_preflight?: {
    status: string
    enabled: boolean
    can_execute: boolean
    has_the_company_api_key: boolean
    models: string[]
    summary: string
    next_action: string
  }
  source: {
    llm_replay_plan_material?: string
  }
}

interface TeamBuilderLlmReplayResult {
  available: boolean
  reason?: string
  run_id: string
  team_name: string
  verdict: 'pass' | 'warning' | 'fail' | 'blocked_by_switch' | 'not_run' | 'unavailable' | string
  summary: string
  counts: {
    planned_calls: number
    executed_workers: number
    executed_llm_workers: Array<{ worker_id: string; kind?: string }>
    failed_workers: number
    contract_failures: number
  }
  quality_gates: MaterialAttributionQualityGate[]
  executed_workers?: Array<{
    worker_id: string
    kind: string
    is_llm_worker?: boolean
    diagnosis?: string
    output_material?: string
    output_summary?: Record<string, any>
  }>
  failed_workers?: Array<{
    worker_id: string
    kind: string
    diagnosis?: string
  }>
  contract_failures?: string[]
  source: {
    llm_replay_result_material?: string
  }
}

async function fetchDetail(id: string): Promise<CodeFileDetail> {
  const r = await fetch(`/api/teams/${id}`)
  if (!r.ok) throw new Error(`读取 team 详情失败：${r.status}`)
  return r.json()
}

async function fetchGraph(id: string, builder: string | null): Promise<TeamGraphData> {
  const suffix = builder ? `?builder=${encodeURIComponent(builder)}` : ''
  const r = await fetch(`/api/team-graph/${id}${suffix}`)
  if (!r.ok) throw new Error(`读取 team 结构图失败：${r.status}`)
  return r.json()
}

async function fetchRuns(id: string, builder: string | null): Promise<TeamRunSummary[]> {
  const params = new URLSearchParams({ limit: '12' })
  if (builder) params.set('builder', builder)
  const r = await fetch(`/api/team-runs/${id}?${params}`)
  if (!r.ok) throw new Error(`读取 team 运行轨迹失败：${r.status}`)
  const d = await r.json() as { items: TeamRunSummary[] }
  return d.items
}

async function fetchRunDetail(id: string, traceId: string, builder: string | null): Promise<TeamRunDetail> {
  const params = new URLSearchParams({ trace_id: traceId })
  if (builder) params.set('builder', builder)
  const r = await fetch(`/api/team-run-detail/${id}?${params}`)
  if (!r.ok) throw new Error(`读取 team 单次运行详情失败：${r.status}`)
  return r.json()
}

async function fetchDoctorHealth(id: string, builder: string | null): Promise<TeamDoctorHealth> {
  const params = new URLSearchParams()
  if (builder) params.set('builder', builder)
  const suffix = params.toString() ? `?${params}` : ''
  const r = await fetch(`/api/team-doctor/${id}${suffix}`)
  if (!r.ok) throw new Error(`读取 team 健康诊断失败：${r.status}`)
  return r.json()
}

async function fetchMaterializationLatest(): Promise<TeamBuilderMaterialization> {
  const r = await fetch('/api/team-builder-materialization/latest')
  if (!r.ok) throw new Error(`读取 material 化实战结果失败：${r.status}`)
  return r.json()
}

async function fetchMaterialAttributionReportLatest(): Promise<MaterialAttributionReport> {
  const r = await fetch('/api/team-builder-materialization/report/latest')
  if (!r.ok) throw new Error(`读取 material 归因报告失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderTestReportLatest(): Promise<TeamBuilderTestReport> {
  const r = await fetch('/api/team-builder-materialization/test-report/latest')
  if (!r.ok) throw new Error(`读取生成包测试报告失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairPlanLatest(): Promise<TeamBuilderRepairPlan> {
  const r = await fetch('/api/team-builder-materialization/repair-plan/latest')
  if (!r.ok) throw new Error(`读取修复准备计划失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairProbeLatest(): Promise<TeamBuilderRepairProbeReport> {
  const r = await fetch('/api/team-builder-materialization/repair-probe/latest')
  if (!r.ok) throw new Error(`读取故障修复探针失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairDryRunLatest(): Promise<TeamBuilderRepairDryRunReport> {
  const r = await fetch('/api/team-builder-materialization/repair-dry-run/latest')
  if (!r.ok) throw new Error(`读取修复干跑探针失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairPatchCandidatesLatest(): Promise<TeamBuilderRepairPatchCandidatesReport> {
  const r = await fetch('/api/team-builder-materialization/repair-patch-candidates/latest')
  if (!r.ok) throw new Error(`读取候选补丁计划失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairApplyGateLatest(): Promise<TeamBuilderRepairApplyGateReport> {
  const r = await fetch('/api/team-builder-materialization/repair-apply-gate/latest')
  if (!r.ok) throw new Error(`读取修复应用门失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairPatchDiffProposalLatest(): Promise<TeamBuilderRepairPatchDiffProposalReport> {
  const r = await fetch('/api/team-builder-materialization/repair-patch-diff-proposal/latest')
  if (!r.ok) throw new Error(`读取补丁 diff proposal 失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairApprovalLatest(): Promise<TeamBuilderRepairApprovalReport> {
  const r = await fetch('/api/team-builder-materialization/repair-approval/latest')
  if (!r.ok) throw new Error(`读取修复批准记录失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairExecutionReadinessLatest(): Promise<TeamBuilderRepairExecutionReadinessReport> {
  const r = await fetch('/api/team-builder-materialization/repair-execution-readiness/latest')
  if (!r.ok) throw new Error(`读取修复执行就绪失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairApplyPreviewLatest(): Promise<TeamBuilderRepairApplyPreviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-apply-preview/latest')
  if (!r.ok) throw new Error(`读取修复应用预览失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairApplyExecutionLatest(): Promise<TeamBuilderRepairApplyExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-apply-execution/latest')
  if (!r.ok) throw new Error(`读取修复真实应用记录失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairPostApplyVerificationLatest(): Promise<TeamBuilderRepairPostApplyVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-post-apply-verification/latest')
  if (!r.ok) throw new Error(`读取修复应用后验证失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairOutcomeReconciliationLatest(): Promise<TeamBuilderRepairOutcomeReconciliationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-outcome-reconciliation/latest')
  if (!r.ok) throw new Error(`读取修复前后对账失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRollbackReadinessLatest(): Promise<TeamBuilderRepairRollbackReadinessReport> {
  const r = await fetch('/api/team-builder-materialization/repair-rollback-readiness/latest')
  if (!r.ok) throw new Error(`读取修复回滚就绪失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRollbackExecutionLatest(): Promise<TeamBuilderRepairRollbackExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-rollback-execution/latest')
  if (!r.ok) throw new Error(`读取修复回滚执行记录失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRollbackPostVerificationLatest(): Promise<TeamBuilderRepairRollbackPostVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-rollback-post-verification/latest')
  if (!r.ok) throw new Error(`读取修复回滚后验证失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairClosureRollupLatest(): Promise<TeamBuilderRepairClosureRollupReport> {
  const r = await fetch('/api/team-builder-materialization/repair-closure-rollup/latest')
  if (!r.ok) throw new Error(`读取修复闭环总览失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairGeneralizationTrialLatest(): Promise<TeamBuilderRepairGeneralizationTrialReport> {
  const r = await fetch('/api/team-builder-materialization/repair-generalization-trial/latest')
  if (!r.ok) throw new Error(`读取修复泛化试验失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealGeneratedFileSetTrialLatest(): Promise<TeamBuilderRepairRealGeneratedFileSetTrialReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-generated-file-set-trial/latest')
  if (!r.ok) throw new Error(`读取真实 generated worker 文件集试验失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunCandidateScanLatest(): Promise<TeamBuilderRepairRealRunCandidateScanReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-candidate-scan/latest')
  if (!r.ok) throw new Error(`读取真实 run 候选扫描失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunReplayPlanLatest(): Promise<TeamBuilderRepairRealRunReplayPlanReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-replay-plan/latest')
  if (!r.ok) throw new Error(`读取真实 run 消解计划失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunDiffPreviewLatest(): Promise<TeamBuilderRepairRealRunDiffPreviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-diff-preview/latest')
  if (!r.ok) throw new Error(`读取真实 run diff 预览失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunDiffReviewLatest(): Promise<TeamBuilderRepairRealRunDiffReviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-diff-review/latest')
  if (!r.ok) throw new Error(`读取真实 run diff 审阅门失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunApplyGateLatest(): Promise<TeamBuilderRepairRealRunApplyGateReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-apply-gate/latest')
  if (!r.ok) throw new Error(`读取真实 run 显式应用门失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunApplyPreviewLatest(): Promise<TeamBuilderRepairRealRunApplyPreviewReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-apply-preview/latest')
  if (!r.ok) throw new Error(`读取真实 run 文件集应用预览失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunApplyExecutionLatest(): Promise<TeamBuilderRepairRealRunApplyExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-apply-execution/latest')
  if (!r.ok) throw new Error(`读取真实 run 显式应用执行失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunPostApplyVerificationLatest(): Promise<TeamBuilderRepairRealRunPostApplyVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-post-apply-verification/latest')
  if (!r.ok) throw new Error(`读取真实 run 应用后回放验证失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunOutcomeReconciliationLatest(): Promise<TeamBuilderRepairRealRunOutcomeReconciliationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest')
  if (!r.ok) throw new Error(`读取真实 run 修复结果对账失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunRollbackReadinessLatest(): Promise<TeamBuilderRepairRealRunRollbackReadinessReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-rollback-readiness/latest')
  if (!r.ok) throw new Error(`读取真实 run 回滚就绪失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunRollbackExecutionLatest(): Promise<TeamBuilderRepairRealRunRollbackExecutionReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-rollback-execution/latest')
  if (!r.ok) throw new Error(`读取真实 run 回滚执行失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunRollbackPostVerificationLatest(): Promise<TeamBuilderRepairRealRunRollbackPostVerificationReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-rollback-post-verification/latest')
  if (!r.ok) throw new Error(`读取真实 run 回滚后验证失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairRealRunClosureRollupLatest(): Promise<TeamBuilderRepairRealRunClosureRollupReport> {
  const r = await fetch('/api/team-builder-materialization/repair-real-run-closure-rollup/latest')
  if (!r.ok) throw new Error(`读取真实 run 闭环总览失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderRepairSafetyPolicyLatest(): Promise<TeamBuilderRepairSafetyPolicy> {
  const r = await fetch('/api/team-builder-materialization/repair-safety-policy/latest')
  if (!r.ok) throw new Error(`读取修复安全策略失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderReadClueResolutionLatest(): Promise<TeamBuilderReadClueResolutionPlan> {
  const r = await fetch('/api/team-builder-materialization/read-clue-resolution/latest')
  if (!r.ok) throw new Error(`读取读取线索消解计划失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderMaterialGapValidationLatest(): Promise<TeamBuilderMaterialGapValidation> {
  const r = await fetch('/api/team-builder-materialization/material-gap-validation/latest')
  if (!r.ok) throw new Error(`读取 material 缺口验证失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderClosureStatusLatest(): Promise<TeamBuilderClosureStatus> {
  const r = await fetch('/api/team-builder-materialization/closure/latest')
  if (!r.ok) throw new Error(`读取闭环状态失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderHighStandardAuditLatest(): Promise<TeamBuilderHighStandardAudit> {
  const r = await fetch('/api/team-builder-materialization/high-standard-audit/latest')
  if (!r.ok) throw new Error(`读取高标准目标审计失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderProviderCoverageAuditLatest(): Promise<TeamBuilderProviderCoverageAudit> {
  const r = await fetch('/api/team-builder-materialization/provider-coverage/latest')
  if (!r.ok) throw new Error(`读取 provider 覆盖审计失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderProviderSameInputTrialPlanLatest(): Promise<TeamBuilderProviderSameInputTrialPlan> {
  const r = await fetch('/api/team-builder-materialization/provider-same-input-trial/latest')
  if (!r.ok) throw new Error(`读取 provider 同口径试验计划失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderLlmReplayPlanLatest(): Promise<TeamBuilderLlmReplayPlan> {
  const r = await fetch('/api/team-builder-materialization/llm-replay-plan/latest')
  if (!r.ok) throw new Error(`读取 LLM 回放计划失败：${r.status}`)
  return r.json()
}

async function fetchTeamBuilderLlmReplayResultLatest(): Promise<TeamBuilderLlmReplayResult> {
  const r = await fetch('/api/team-builder-materialization/llm-replay-result/latest')
  if (!r.ok) throw new Error(`读取 LLM 回放结果失败：${r.status}`)
  return r.json()
}

// ── frostpane 呈现层 (重建, 2026-06-29) ───────────────────────────────────
// 准则: 外壳(顶栏/右侧栏/卡片/浮层)= 磨砂玻璃; 大滚动区(图布局画布/源码摘录)= 安静近实色 var(--fp-solid),
//   不上玻璃护性能; 层级靠字号拉开(标题 16/650 · 区组标签 12 弱灰大写 · 正文 13 · 度量值 18/650 · 微字 12),
//   不再全平 16; 圆角卡片/浮层用 11; 4px 栅格放宽呼吸; 冷色全走 colors token。
const GLASS_BG = 'var(--fp-glass)'
const GLASS_BLUR = 'var(--fp-blur)'
const GLASS_SHADOW = '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)'
const QUIET_SURFACE = colors.bgPanel // var(--fp-solid) — 大滚动/阅读区安静近实色
const S = {
  // 外壳透明, 让背后冷色渐变透出来; 内部各表面自带分层材质
  root: {
    display: 'flex',
    flexDirection: 'column' as const,
    height: '100%',
    background: 'transparent',
    color: colors.text,
    fontFamily: fonts.ui,
    minWidth: 0,
  },
  // 顶部工具条 = 玻璃外壳 (磨砂 + 边缘高光), 与下方画布/侧栏分层
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    padding: '8px 16px',
    borderBottom: `1px solid ${colors.border}`,
    background: 'transparent', // G.7:私有格纸清除,透出 #bp-grid 统一连续网格
    backgroundAttachment: 'local',
    flexShrink: 0,
  },
  // 标题 = 最醒目层: 16px / 650, 主文本色
  title: {
    fontSize: 14,
    fontWeight: 650,
    letterSpacing: '-0.01em',
    fontFamily: 'var(--fp-bp-font-display)',
    color: colors.text,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  // 副标题 = 最次层: 压成弱灰等宽微字
  subtitle: {
    marginTop: 3,
    fontFamily: fonts.mono,
    fontSize: 12,
    color: colors.textFaint,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  tabs: {
    display: 'flex',
    gap: 4,
    flexShrink: 0,
  },
  // 分段控件: 选中态填强调弱底 + 边, 未选中安静近无形
  tab: (active: boolean): React.CSSProperties => ({
    border: `1px solid ${active ? colors.accent : 'transparent'}`,
    background: active ? colors.accentBg : 'rgba(255,255,255,.04)',
    color: active ? colors.text : colors.textMuted,
    height: 28,
    minWidth: 70,
    padding: '0 12px',
    borderRadius: 7,
    cursor: 'pointer',
    fontFamily: fonts.ui,
    fontSize: 13,
    fontWeight: active ? 600 : 500,
    transition: 'all 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  body: {
    flex: 1,
    minHeight: 0,
    display: 'flex',
  },
  // 图布局画布 = 大面积滚动/平移区: 安静近实色, 绝不上玻璃 (frostpane 性能铁律)
  graphWrap: {
    flex: 1,
    minWidth: 0,
    minHeight: 0,
    position: 'relative' as const,
    background: 'transparent', // G.7:私有格纸清除,透出 #bp-grid 统一连续网格
    backgroundAttachment: 'local',
  },
  // 右侧栏 = 玻璃外壳: 磨砂 + 左缘描边 + 内高光; 放宽内边距呼吸
  side: {
    width: 390,
    minWidth: 360,
    maxWidth: '42%',
    overflow: 'auto',
    borderLeft: '2px solid var(--fp-border)',
    background: 'var(--fp-surface)',
    padding: 16,
  },
  sideTopBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    marginBottom: 12,
  },
  // 浮回侧栏按钮 = 玻璃小浮层
  restoreSideButton: {
    position: 'absolute' as const,
    top: 12,
    right: 12,
    zIndex: 20,
    height: 30,
    border: '1px dashed var(--fp-border-strong)',
    borderRadius: 3,
    background: 'var(--fp-bp-tracing)',
    backdropFilter: 'var(--fp-bp-tracing-blur)',
    WebkitBackdropFilter: 'var(--fp-bp-tracing-blur)',
    boxShadow: 'var(--fp-bp-shadow-sm)',
    color: colors.textSecondary,
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '0 10px',
    fontFamily: fonts.ui,
    fontSize: 13,
  },
  // 区块分隔: 组间 16 呼吸 (4px 栅格)
  section: {
    borderTop: `1px solid ${colors.border}`,
    paddingTop: 16,
    marginTop: 16,
  },
  // 区组标签 = 弱灰大写微字, 安静地领起一组 (字号建层级)
  label: {
    fontSize: 12,
    color: colors.textFaint,
    textTransform: 'uppercase' as const,
    letterSpacing: '.05em',
    marginBottom: 10,
    fontWeight: 600,
  },
  metricRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
    gap: 8,
  },
  // 度量卡 = 安静近实色小卡, 圆角 7
  metric: {
    border: `1px solid ${colors.border}`,
    borderRadius: 7,
    padding: '10px 12px',
    background: QUIET_SURFACE,
    minWidth: 0,
  },
  // 度量值 = 该卡最重信息: 18 / 650 醒目
  metricValue: {
    fontSize: 18,
    fontWeight: 650,
    letterSpacing: '-0.01em',
    color: colors.text,
  },
  // 度量名 = 弱灰微字
  metricName: {
    marginTop: 3,
    fontSize: 12,
    color: colors.textMuted,
  },
  select: {
    width: '100%',
    height: 32,
    background: colors.bgCard,
    color: colors.text,
    border: `1px solid ${colors.border}`,
    borderRadius: 7,
    padding: '0 10px',
    fontFamily: fonts.mono,
    fontSize: 13,
    cursor: 'pointer',
  },
  // 标签胶囊: 安静近实色 + 等宽微字
  pill: {
    display: 'inline-flex',
    alignItems: 'center',
    minHeight: 20,
    maxWidth: '100%',
    padding: '3px 9px',
    border: `1px solid ${colors.border}`,
    borderRadius: 999,
    background: 'rgba(255,255,255,.04)',
    color: colors.textSecondary,
    fontFamily: fonts.mono,
    fontSize: 12,
    overflowWrap: 'anywhere' as const,
    whiteSpace: 'normal' as const,
  },
  // 运行项按钮: 选中态填语义弱底, 圆角 7
  runButton: (active: boolean): React.CSSProperties => ({
    width: '100%',
    textAlign: 'left' as const,
    border: `1px solid ${active ? '#36c275' : colors.border}`,
    borderRadius: 7,
    padding: 12,
    background: active ? '#0f2a1a' : QUIET_SURFACE,
    color: colors.textSecondary,
    cursor: 'pointer',
    fontFamily: fonts.ui,
    fontSize: 13,
    transition: 'all 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  }),
  // 材料卡 = 磨砂玻璃外壳卡, 圆角 11 + 内高光
  materialCard: {
    border: `1px solid ${colors.border}`,
    borderRadius: 3,
    padding: 12,
    background: 'var(--fp-bp-paper-2)',
    boxShadow: 'var(--fp-bp-shadow-sm)',
    display: 'grid',
    gap: 8,
  },
  materialId: {
    fontFamily: fonts.mono,
    fontSize: 12,
    lineHeight: 1.5,
    color: colors.text,
    overflowWrap: 'anywhere' as const,
    wordBreak: 'break-word' as const,
  },
  // 资源卡 = 暖色虚线标注 (区别于材料), 玻璃外壳
  resourceCard: {
    border: `1px dashed #b98b42`,
    borderRadius: 11,
    padding: 12,
    background: 'rgba(36,28,12,0.5)',
    backdropFilter: GLASS_BLUR,
    WebkitBackdropFilter: GLASS_BLUR,
    boxShadow: GLASS_SHADOW,
    display: 'grid',
    gap: 8,
  },
  // 定义卡 = 磨砂玻璃外壳卡
  definitionCard: {
    border: `1px solid ${colors.border}`,
    borderRadius: 3,
    padding: 12,
    background: 'var(--fp-bp-paper-2)',
    boxShadow: 'var(--fp-bp-shadow-sm)',
    display: 'grid',
    gap: 8,
  },
  // 源码/长文摘录 = 阅读滚动区: 安静近实色, 不上玻璃; 等宽微字
  sourceExcerpt: {
    maxHeight: 190,
    overflow: 'auto',
    margin: 0,
    padding: 10,
    border: `1px solid ${colors.border}`,
    borderRadius: 7,
    background: colors.bg,
    color: colors.textSecondary,
    fontFamily: fonts.mono,
    fontSize: 12,
    lineHeight: 1.5,
    whiteSpace: 'pre-wrap' as const,
    overflowWrap: 'anywhere' as const,
  },
  // 选中详情浮层 = 大浮层玻璃: 圆角 11 + 强阴影 + 内高光
  floatingPanel: {
    width: 400,
    maxWidth: 'min(400px, calc(100vw - 32px))',
    maxHeight: 'min(520px, calc(100vh - 260px))',
    overflow: 'auto',
    padding: 16,
    border: `1px solid ${colors.border}`,
    borderRadius: 11,
    background: 'var(--fp-glass)',
    backdropFilter: GLASS_BLUR,
    WebkitBackdropFilter: GLASS_BLUR,
    boxShadow: '0 14px 44px rgba(0,0,0,.5), inset 0 1px 0 rgba(255,255,255,.10)',
    fontFamily: fonts.ui,
    color: colors.text,
    pointerEvents: 'auto' as const,
  },
  // 浮层标题 = 醒目层: 15 / 650
  floatingTitle: {
    minWidth: 0,
    color: colors.text,
    fontSize: 15,
    fontWeight: 650,
    letterSpacing: '-0.01em',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  iconButton: {
    width: 26,
    height: 26,
    border: `1px solid ${colors.border}`,
    borderRadius: 7,
    background: 'rgba(255,255,255,.04)',
    color: colors.textMuted,
    cursor: 'pointer',
    fontSize: 13,
    lineHeight: '20px',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 150ms cubic-bezier(0.175,0.885,0.32,1.1)',
  },
}

function shortFormat(value: string | null | undefined): string {
  if (!value) return ''
  const parts = value.split('.')
  return parts.length > 2 ? parts.slice(-2).join('.') : value
}

function shortTrace(value: string): string {
  return value.length > 18 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value
}

function compactTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function zhValidatorKind(value: string | null | undefined): string {
  if (!value) return '未声明'
  const map: Record<string, string> = {
    hard: '硬规则',
    soft: '模型判断',
    router: '路由',
    transformer: '转换器',
    validator: '校验器',
    worker: '工作节点',
  }
  return map[value] ? `${map[value]}（${value}）` : value
}

function zhDoctorStatus(value: string | null | undefined): string {
  const map: Record<string, string> = {
    healthy: '健康',
    degraded: '有风险',
    unhealthy: '不健康',
  }
  return value ? (map[value] ? `${map[value]}（${value}）` : value) : '未知'
}

function zhDoctorLevel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    blocking: '阻断',
    degrading: '降级',
    advisory: '建议',
    info: '信息',
  }
  return value ? (map[value] ? `${map[value]}（${value}）` : value) : '未知'
}

function zhRunStatus(value: string | null | undefined): string {
  const map: Record<string, string> = {
    running: '运行中',
    finished: '已结束',
    error: '错误',
    missing: '缺失',
  }
  return value ? (map[value] ? `${map[value]}（${value}）` : value) : '未知'
}

function zhVerdict(value: string | null | undefined): string {
  const map: Record<string, string> = {
    pass: '通过',
    partial: '部分通过',
    fail: '失败',
    failed: '失败',
    error: '错误',
  }
  return value ? (map[value] ? `${map[value]}（${value}）` : value) : ''
}

function zhEventType(value: string | null | undefined): string {
  if (!value) return '事件'
  const lower = value.toLowerCase()
  if (lower.includes('tool')) return `工具调用（${value}）`
  if (lower.includes('llm')) return `模型调用（${value}）`
  if (lower.includes('verdict')) return `判定（${value}）`
  if (lower.includes('node')) return `节点事件（${value}）`
  if (lower.includes('agent')) return `代理事件（${value}）`
  return value
}

const ZH_TOKEN: Record<string, string> = {
  agent: '代理',
  aggregator: '聚合器',
  analyzer: '分析器',
  architect: '架构师',
  assembler: '组装器',
  assessment: '评估',
  assessor: '评估器',
  audit: '审计',
  auditor: '审计器',
  bundle: '文件包',
  builder: '构建器',
  checker: '检查器',
  code: '代码',
  collector: '收集器',
  composite: '组合',
  config: '配置',
  context: '上下文',
  contract: '契约',
  decomposition: '拆分',
  design: '设计',
  designer: '设计器',
  detailed: '明细',
  diagnosis: '诊断',
  doctor: '诊断',
  doc: '文档',
  edge: '连接',
  error: '错误',
  eval: '评估',
  evidence: '证据',
  example: '示例',
  exec: '执行',
  executor: '执行器',
  extractor: '提取器',
  file: '文件',
  finalizer: '收尾器',
  fixer: '修复器',
  format: '格式',
  formats: '格式',
  generator: '生成器',
  health: '健康',
  hypothesis: '假设',
  init: '初始化',
  input: '输入',
  intent: '意图',
  inventory: '清单',
  loader: '装载器',
  material: '材料',
  module: '模块',
  origin: '原始',
  output: '输出',
  package: '包',
  pattern: '模式',
  pipeline: '流水线',
  pkg: '包',
  planner: '规划器',
  probe: '试探',
  reader: '读取器',
  references: '参考资料',
  registrar: '注册器',
  registration: '注册',
  regression: '回归',
  report: '报告',
  request: '请求',
  reviewer: '评审器',
  rtr: 'Worker',
  run: '运行',
  scanner: '扫描器',
  scout: '侦察器',
  selected: '已选',
  selector: '选择器',
  signature: '签名',
  sink: '最终输出',
  source: '来源',
  spec: '规格',
  structural: '结构',
  syntax: '语法',
  tag: '标签',
  team: '团队',
  tester: '测试器',
  topology: '拓扑',
  topo: '拓扑',
  trigger: '触发',
  validation: '验证',
  validator: '校验器',
  worker: 'Worker',
  workspace: '工作区',
  writer: '写入器',
  yaml: '配置',
  py: 'Python',
  md: 'Markdown',
}

function tokenizeIdentifier(value: string | null | undefined): string[] {
  if (!value) return []
  const leaf = String(value).split('/').pop() || String(value)
  const expanded = leaf
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^0-9A-Za-z]+/g, '_')
    .toLowerCase()
  return expanded.split('_').filter(Boolean)
}

function zhAlias(value: string | null | undefined, fallback: string): string {
  const tokens = tokenizeIdentifier(value).filter((token) => !['material', 'materials'].includes(token))
  const parts = tokens.map((token) => ZH_TOKEN[token] || token)
  const alias = parts.join('')
  return alias || fallback
}

function zhTeamName(graph: TeamGraphData): string {
  const base = graph.name || graph.spec_id || graph.team_id
  const alias = zhAlias(base, 'Team')
  return alias.includes('团队') ? alias : `${alias}团队`
}

function zhMaterialName(materialId: string | null | undefined): string {
  if (!materialId) return '未声明材料'
  const meaningful = materialId.split('.').slice(-1)[0] || materialId
  return zhAlias(meaningful, '材料')
}

function zhWorkerName(nodeOrId: TeamGraphNode | string): string {
  if (typeof nodeOrId === 'string') return zhAlias(nodeOrId, 'Worker')
  return zhAlias(nodeOrId.label || nodeOrId.id, 'Worker')
}

function trimText(value: string | null | undefined, limit = 96): string {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}

function lineClamp(lines: number): React.CSSProperties {
  return {
    display: '-webkit-box',
    WebkitLineClamp: lines,
    WebkitBoxOrient: 'vertical',
    overflow: 'hidden',
  } as React.CSSProperties
}

function workerZhDescription(node: TeamGraphNode): string {
  const inputs = node.format_in.length ? node.format_in.map(zhMaterialName).join('、') : '外部输入'
  const output = node.format_out ? zhMaterialName(node.format_out) : '最终输出'
  const base = `${zhValidatorKind(node.validator_kind || node.kind)}，读取 ${inputs}，产出 ${output}。`
  const raw = trimText(node.description, 120)
  return raw ? `${base}${raw}` : base
}

function materialZhDescription(material: TeamGraphMaterial, graph: TeamGraphData): string {
  const producers = material.producers.length ? material.producers.map(zhWorkerName).join('、') : '外部输入'
  const consumers = material.consumers.length ? material.consumers.map(zhWorkerName).join('、') : '最终输出'
  const scope = graph.name || graph.team_id
  return `${zhMaterialName(material.id)} 属于 ${scope}，由 ${producers} 产出，供 ${consumers} 使用。`
}

function teamZhDescription(graph: TeamGraphData): string {
  const base = `${graph.selected_builder} 当前构建器，包含 ${graph.nodes.length} 个 Worker、${graph.materials.length} 个 Material、${graph.edges.length} 条路由连接。`
  const raw = trimText(graph.description || graph.purpose, 140)
  return raw ? `${base}${raw}` : base
}

const RESOURCE_META: Record<ResourceKind, { label: string; description: string; tone: string; background: string }> = {
  workspace: {
    label: '工作区文件',
    description: '聚合展示 worker 在实战或静态线索中接触过的工作区文件、目录或命令；这只是线索，不等于已确认 material。',
    tone: '#d09a45',
    background: '#171208',
  },
  database: {
    label: '数据库/事件库',
    description: '聚合展示 worker 可能通过 SQL、SQLite、事件库或查询接口读取到的数据资料。',
    tone: '#55b8d8',
    background: '#08141a',
  },
  external: {
    label: '外部代理/接口',
    description: '表示 worker 可能调用 Claude Code、Codex、HTTP API 或其他外部资源。',
    tone: '#b2a7ff',
    background: '#111025',
  },
}

function addResourceHint(
  hints: Map<ResourceKind, TeamResourceHint>,
  kind: ResourceKind,
  workerId: string,
  evidence: string,
  confidence: TeamResourceHint['confidence'],
) {
  const meta = RESOURCE_META[kind]
  const current = hints.get(kind) || {
    id: kind,
    label: meta.label,
    description: meta.description,
    workers: [],
    evidence: [],
    confidence,
  }
  if (!current.workers.includes(workerId)) current.workers.push(workerId)
  if (!current.evidence.includes(evidence) && current.evidence.length < 5) current.evidence.push(evidence)
  if (confidence === '运行推断') current.confidence = '运行推断'
  hints.set(kind, current)
}

function workerSearchText(node: TeamGraphNode): string {
  return [
    node.id,
    node.label,
    node.kind,
    node.method,
    node.description,
    node.definition?.worker?.class_name,
    node.definition?.worker?.description,
    node.definition?.summary,
  ].filter(Boolean).join(' ').toLowerCase()
}

function resourceKindFromLink(link: TeamMaterializationLink): ResourceKind {
  if (link.resource_kind === 'database') return 'database'
  if (link.resource_kind === 'external') return 'external'
  return 'workspace'
}

function inferResourceHints(graph: TeamGraphData, runDetail: TeamRunDetail | null, materialization: TeamBuilderMaterialization | null = null): TeamResourceHint[] {
  const hints = new Map<ResourceKind, TeamResourceHint>()
  graph.nodes.forEach((node) => {
    const text = workerSearchText(node)
    if (/(repo|repository|file|path|workspace|grep|bash|shell|scanner|loader|snapshot|code|compile|import|diff)/i.test(text)) {
      addResourceHint(hints, 'workspace', node.id, `${node.id} 的名称或说明命中工作区访问词`, '演示推断')
    }
    if (/(database|sqlite|sql|query|event\.db|ide_events|db\b)/i.test(text)) {
      addResourceHint(hints, 'database', node.id, `${node.id} 的名称或说明命中数据库访问词`, '演示推断')
    }
    if (/(agent|claude|codex|api|http|browser|web|external)/i.test(text)) {
      addResourceHint(hints, 'external', node.id, `${node.id} 的名称或说明命中外部资源词`, '演示推断')
    }
  })

  ;(runDetail?.timeline || []).forEach((event) => {
    const toolText = JSON.stringify(event.tool_calls || []).toLowerCase()
    if (!toolText || toolText === '[]') return
    event.node_ids.forEach((nodeId) => {
      if (/(grep|bash|shell|powershell|read|write|glob|find|ls|file|path)/i.test(toolText)) {
        addResourceHint(hints, 'workspace', nodeId, `${zhEventType(event.event_type)} 工具参数疑似访问文件或命令行`, '运行推断')
      }
      if (/(database|sqlite|sql|query|db\b|event\.db|ide_events)/i.test(toolText)) {
        addResourceHint(hints, 'database', nodeId, `${zhEventType(event.event_type)} 工具参数疑似访问数据库`, '运行推断')
      }
      if (/(http|https|api|browser|web|curl|claude|codex|external)/i.test(toolText)) {
        addResourceHint(hints, 'external', nodeId, `${zhEventType(event.event_type)} 工具参数疑似访问外部资源`, '运行推断')
      }
    })
  })

  ;(materialization?.worker_runs || []).forEach((run) => {
    run.resource_material_links.forEach((link) => {
      const title = materializationHumanTitle(link)
      const target = link.normalized_target || link.target || link.rel_path || link.evidence[0] || ''
      const evidence = `${zhWorkerName(run.worker_id)} 实战读取线索：${title}${target ? `（${trimText(target, 90)}）` : ''}`
      addResourceHint(hints, resourceKindFromLink(link), run.worker_id, evidence, '运行推断')
    })
  })

  return Array.from(hints.values())
    .map((hint) => ({
      ...hint,
      workers: hint.workers.filter((workerId) => graph.nodes.some((node) => node.id === workerId)).slice(0, 12),
    }))
    .filter((hint) => hint.workers.length > 0)
}

function pickDefaultRun(runs: TeamRunSummary[]): string | null {
  if (!runs.length) return null
  const ranked = [...runs].sort((a, b) => {
    const coverage = b.matched_nodes.length - a.matched_nodes.length
    if (coverage) return coverage
    return String(b.ended_at || b.started_at || '').localeCompare(String(a.ended_at || a.started_at || ''))
  })
  return ranked[0].trace_id
}

function hasFailedVerdict(status: TeamRunNodeStatus | undefined): boolean {
  if (!status) return false
  return Object.keys(status.verdict_counts || {}).some((key) => ['fail', 'failed', 'error'].includes(key.toLowerCase()))
}

function doctorTone(level: string | undefined): { border: string; background: string; label: string } | null {
  if (level === 'blocking') return { border: '#e05252', background: '#2a1111', label: '阻断' }
  if (level === 'degrading') return { border: '#ff9f43', background: '#2a1d0d', label: '降级' }
  if (level === 'advisory') return { border: 'var(--fp-accent)', background: '#242610', label: '建议' }
  return null
}

function highestDoctorFinding(findings: TeamDoctorFinding[]): TeamDoctorFinding | undefined {
  const order: Record<string, number> = { blocking: 0, degrading: 1, advisory: 2, info: 3 }
  return [...findings].sort((a, b) => (order[a.level] ?? 9) - (order[b.level] ?? 9))[0]
}

function zhDirection(value: string | null | undefined): string {
  const map: Record<string, string> = {
    read: '读取',
    write: '写入',
  }
  return value ? (map[value] || value) : '未声明'
}

function zhRegistrationStatus(value: string | null | undefined): string {
  const map: Record<string, string> = {
    candidate: '待确认线索',
    'generated-candidate': '生成产物候选',
    registered: '已注册',
    declared: '已声明',
    'declared-in-file': '文件声明命中',
  }
  return value ? (map[value] || value) : '已声明事实'
}

function zhConfidence(value: string | null | undefined): string {
  const map: Record<string, string> = {
    high: '高置信',
    medium: '中置信',
    low: '低置信',
  }
  return value ? (map[value] || value) : '未标注'
}

function zhGenerationStatus(value: string | null | undefined): string {
  const map: Record<string, string> = {
    succeeded: '生成成功',
    failed: '生成失败',
    error: '生成错误',
    skipped: '未执行',
  }
  return value ? (map[value] || value) : '未知'
}

function materializationRawHref(params: Record<string, string | null | undefined> = {}): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value)
  })
  const suffix = query.toString()
  return `/api/team-builder-materialization/latest${suffix ? `?${suffix}` : ''}`
}

function materializationLinkHref(link: TeamMaterializationLink, workerId?: string): string {
  if (link.material_id) return materializationRawHref({ worker: workerId, material: link.material_id })
  return materializationRawHref({ worker: workerId, target: link.target || link.rel_path || link.evidence[0] })
}

function linkTone(link: TeamMaterializationLink): string {
  if (link.registration_status === 'generated-candidate') return '#9db8ff'
  if (link.registration_status === 'candidate') return '#d09a45'
  if (link.direction === 'write') return '#78d98b'
  return colors.accent
}

function linkKindLabel(link: TeamMaterializationLink): string {
  if (link.registration_status === 'generated-candidate') return '生成产物'
  if (link.registration_status === 'candidate') return '读取线索'
  if (link.registration_status === 'declared-in-file') return '文件声明命中'
  if (link.direction === 'write') return '声明输出'
  return '声明输入'
}

function linkDisplayName(link: TeamMaterializationLink): string {
  return materializationHumanTitle(link)
}

function SourceDataLink({ href, label = '原始记录' }: { href: string; label?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      title="调试入口：打开这条记录的原始 JSON。普通审阅优先看当前小窗里的中文判断。"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        minHeight: 22,
        padding: '2px 6px',
        border: `1px solid ${colors.border}`,
        borderRadius: 4,
        background: '#10151b',
        color: colors.textSecondary,
        fontSize: 13,
        textDecoration: 'none',
        whiteSpace: 'nowrap',
      }}
    >
      <ExternalLink size={12} strokeWidth={1.8} />
      {label}
    </a>
  )
}

function materializationRunForWorker(materialization: TeamBuilderMaterialization | null, workerId: string): TeamMaterializationWorkerRun | null {
  return materialization?.worker_runs.find((run) => run.worker_id === workerId) || null
}

function reviewIssuesForWorker(materialization: TeamBuilderMaterialization | null, workerId: string): TeamMaterializationReviewIssue[] {
  return (materialization?.review.issues || []).filter((issue) => issue.worker_id === workerId)
}

function linkMatchesMaterial(link: TeamMaterializationLink, materialId: string): boolean {
  return link.material_id === materialId || link.evidence.includes(materialId) || link.target.includes(materialId)
}

function MaterializationLinks({ title, links, emptyText, limit = 6, workerId }: {
  title: string
  links: TeamMaterializationLink[]
  emptyText: string
  limit?: number
  workerId?: string
}) {
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ ...S.label, marginBottom: 6 }}>{title}</div>
      {links.length === 0 ? (
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{emptyText}</div>
      ) : (
        <div style={{ display: 'grid', gap: 7 }}>
          {links.slice(0, limit).map((link, index) => {
            const name = link.material_id || link.target || link.rel_path || `link-${index}`
            const tone = linkTone(link)
            const title = materializationHumanTitle(link)
            const summary = materializationHumanSummary(link)
            return (
              <div key={`${name}-${index}`} style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 8, background: colors.bg }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
                  <span style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>{title}</span>
                  <span style={{ color: tone, fontSize: 13, whiteSpace: 'nowrap' }}>{linkKindLabel(link)}</span>
                </div>
                <div style={{ marginTop: 5, color: colors.textSecondary, fontSize: 13, lineHeight: 1.45, overflowWrap: 'anywhere' }}>{summary}</div>
                <div style={{ ...S.materialId, marginTop: 5 }}>{name}</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                  <span style={S.pill}>{zhRegistrationStatus(link.registration_status)}</span>
                  <span style={S.pill}>{zhConfidence(link.confidence)}</span>
                  {link.candidate_kind && <span style={S.pill}>{zhCandidateKind(link.candidate_kind)}</span>}
                  {link.content_kind && <span style={S.pill}>{link.content_kind}</span>}
                  {typeof link.bytes === 'number' && <span style={S.pill}>{link.bytes} 字节</span>}
                  <SourceDataLink href={materializationLinkHref(link, workerId)} label="原始记录" />
                </div>
                {(link.evidence_summary || link.candidate_reason) && (
                  <div style={{ marginTop: 6, color: colors.textSecondary, fontSize: 13, lineHeight: 1.45, overflowWrap: 'anywhere' }}>
                    判断依据：{materializationEvidenceSummary(link)}
                  </div>
                )}
                {(link.normalized_target || link.target) && (
                  <div style={{ marginTop: 6, color: colors.textMuted, fontSize: 13, overflowWrap: 'anywhere' }}>
                    具体目标：{link.normalized_target || link.target}
                  </div>
                )}
                {(link.matched_material_ids || []).length > 0 && (
                  <div style={{ marginTop: 6, color: '#78d98b', fontSize: 13, overflowWrap: 'anywhere' }}>
                    命中 material：{(link.matched_material_ids || []).join('、')}
                  </div>
                )}
                {link.basis && (
                  <div style={{ marginTop: 5, color: colors.textFaint, fontSize: 13, overflowWrap: 'anywhere' }}>
                    依据：{link.basis}
                  </div>
                )}
              </div>
            )
          })}
          {links.length > limit && <div style={{ color: colors.textMuted, fontSize: 13 }}>还有 {links.length - limit} 条未展开。</div>}
        </div>
      )}
    </div>
  )
}

function FieldAccessBlock({ run }: { run: TeamMaterializationWorkerRun }) {
  const missingInput = Object.entries(run.static_field_access.missing_input_required || {})
    .filter(([, fields]) => fields.length > 0)
  const missingOutput = run.static_field_access.missing_output_required || []
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ ...S.label, marginBottom: 6 }}>字段覆盖</div>
      {Object.entries(run.static_field_access.input_field_reads || {}).map(([materialId, fields]) => (
        <Kv
          key={materialId}
          name={zhMaterialName(materialId)}
          value={fields.length ? fields.map((field) => <span key={field} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{field}</span>) : '没有静态读取'}
        />
      ))}
      <Kv
        name="输出字段"
        value={run.static_field_access.output_field_writes.length
          ? run.static_field_access.output_field_writes.map((field) => <span key={field} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{field}</span>)
          : '没有静态写入'}
      />
      {(missingInput.length > 0 || missingOutput.length > 0) ? (
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.5, overflowWrap: 'anywhere' }}>
          {missingInput.map(([materialId, fields]) => (
            <div key={materialId}>{zhMaterialName(materialId)} 缺少读取字段：{fields.join('、')}</div>
          ))}
          {missingOutput.length > 0 && <div>输出缺少字段：{missingOutput.join('、')}</div>}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13 }}>必需字段读取/写入没有发现缺口。</div>
      )}
    </div>
  )
}

function MaterialLinkChips({ label, description, links, limit = 3, workerId }: {
  label: string
  description: string
  links: TeamMaterializationLink[]
  limit?: number
  workerId?: string
}) {
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{label}</div>
      <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4 }}>{description}</div>
      {links.length === 0 ? (
        <div style={{ color: colors.textFaint, fontSize: 13 }}>无</div>
      ) : (
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          {links.slice(0, limit).map((link, index) => {
            const name = link.material_id || link.target || link.rel_path || `link-${index}`
            const tone = linkTone(link)
            return (
              <a
                key={`${name}-${index}`}
                href={materializationLinkHref(link, workerId)}
                target="_blank"
                rel="noreferrer"
                title={`${name} · 调试用原始记录；普通审阅优先点击图中节点看小窗`}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  minHeight: 24,
                  maxWidth: '100%',
                  padding: '2px 7px',
                  border: `1px solid ${tone}`,
                  borderRadius: 4,
                  background: colors.bg,
                  color: colors.textSecondary,
                  fontFamily: fonts.mono,
                  fontSize: 13,
                  lineHeight: 1.3,
                  overflowWrap: 'anywhere',
                  textDecoration: 'none',
                }}
              >
                <span style={{ color: tone, fontFamily: fonts.ui, fontWeight: 700 }}>{linkKindLabel(link)}</span>
                <span>{linkDisplayName(link).replace(`${zhDirection(link.direction)} `, '')}</span>
                <ExternalLink size={11} strokeWidth={1.8} />
              </a>
            )
          })}
          {links.length > limit && <span style={S.pill}>+{links.length - limit}</span>}
        </div>
      )}
    </div>
  )
}

function MaterializationExplanation() {
  const items = [
    ['声明输入/输出', 'Team 里写死的输入和输出, 是最稳定的事实。'],
    ['生成产物', '这次外部代理实际生成的源码文件, 先作为可追踪产物记录。'],
    ['实战读取线索', '运行中接触过的文件、目录、命令或查询。它回答“为什么怀疑读过资料”, 但不等于已经确认 material。'],
  ]
  return (
    <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', display: 'grid', gap: 7, marginTop: 8 }}>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.45 }}>
        这是 TeamBuilder 的审阅视图: 用最近一次生成审查回答「worker 声明要读写什么、实际产出了什么、运行时接触过哪些资料线索、为什么这么判断」。
      </div>
      {items.map(([name, desc]) => (
        <div key={name} style={{ display: 'grid', gridTemplateColumns: '92px minmax(0, 1fr)', gap: 8, alignItems: 'start' }}>
          <span style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>{name}</span>
          <span style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{desc}</span>
        </div>
      ))}
    </div>
  )
}

function attributionStatusTone(status: string): string {
  if (status === 'pass') return '#78d98b'
  if (status === 'warning') return '#ffb74d'
  if (status === 'fail') return '#ff7b7b'
  return colors.textMuted
}

function zhAttributionStatus(status: string): string {
  if (status === 'pass') return '通过'
  if (status === 'warning') return '需确认'
  if (status === 'fail') return '未通过'
  return status || '未知'
}

function readGroupTone(status: string): string {
  if (status === 'confirmed') return '#78d98b'
  if (status === 'candidate') return '#ffb74d'
  if (status === 'evidence') return '#9db8ff'
  if (status === 'explanatory') return '#8cc7c1'
  return colors.textMuted
}

function zhReadGroupStatus(status: string): string {
  if (status === 'confirmed') return '事实读取'
  if (status === 'candidate') return '待确认'
  if (status === 'evidence') return '工具证据'
  if (status === 'explanatory') return '内容提及'
  return status || '未知'
}

function repairPlanTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'validation_gap') return '#ffb74d'
  if (verdict === 'repair_required') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairPlanVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需修复'
  if (verdict === 'validation_gap') return '验证缺口'
  if (verdict === 'repair_required') return '需要修复'
  if (verdict === 'unavailable') return '暂无计划'
  return verdict || '未知'
}

function zhRepairActionCategory(category: string): string {
  if (category === 'validation_gap') return '验证缺口'
  if (category === 'repair_required') return '修复准备'
  if (category === 'observe_only') return '观察项'
  return category || '未知'
}

function repairPatchCandidateTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'ready_for_manual_patch') return '#ffb74d'
  if (verdict === 'needs_locator_or_dry_run') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairPatchVerdict(verdict: string): string {
  if (verdict === 'clean') return '暂无候选'
  if (verdict === 'ready_for_manual_patch') return '可人工审阅'
  if (verdict === 'needs_locator_or_dry_run') return '需补定位'
  if (verdict === 'unavailable') return '暂无候选'
  return verdict || '未知'
}

function zhRepairPatchCandidateStatus(status: string): string {
  if (status === 'source_located') return '源码已定位'
  if (status === 'needs_source_locator') return '需定位源码'
  if (status === 'not_applicable') return '不适用'
  return status || '未知'
}

function repairApplyGateTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'ready_for_human_review') return '#ffb74d'
  if (verdict === 'blocked') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairApplyGateVerdict(verdict: string): string {
  if (verdict === 'clean') return '未开启'
  if (verdict === 'ready_for_human_review') return '待人工审阅'
  if (verdict === 'blocked') return '阻断'
  if (verdict === 'unavailable') return '暂无应用门'
  return verdict || '未知'
}

function repairPatchDiffProposalTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'diff_ready' || verdict === 'needs_ai_or_human_diff') return '#ffb74d'
  if (verdict === 'blocked') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairPatchDiffProposalVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需 diff'
  if (verdict === 'diff_ready') return '已有 diff'
  if (verdict === 'needs_ai_or_human_diff') return '需生成 diff'
  if (verdict === 'blocked') return '阻断'
  if (verdict === 'unavailable') return '暂无 proposal'
  return verdict || '未知'
}

function zhRepairPatchDiffProposalStatus(status: string): string {
  if (status === 'diff_ready') return '已有 diff'
  if (status === 'needs_ai_or_human_diff') return '需 AI/人工'
  if (status === 'blocked') return '阻断'
  return status || '未知'
}

function repairApprovalTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'approved') return '#ffb74d'
  if (verdict === 'awaiting_approval') return '#ffb74d'
  if (verdict === 'stale_or_mismatch') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairApprovalVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需批准'
  if (verdict === 'approved') return '已批准'
  if (verdict === 'awaiting_approval') return '等待批准'
  if (verdict === 'stale_or_mismatch') return '批准失效'
  if (verdict === 'unavailable') return '暂无批准记录'
  return verdict || '未知'
}

function zhRepairApprovalStatus(status: string): string {
  if (status === 'approved') return '已批准'
  if (status === 'awaiting_approval') return '等待批准'
  if (status === 'stale_or_mismatch') return '批准失效'
  if (status === 'not_approvable') return '不可批准'
  return status || '未知'
}

function repairExecutionReadinessTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'ready_for_explicit_apply') return '#ffb74d'
  if (verdict === 'waiting_for_patch_diff' || verdict === 'awaiting_explicit_approval') return '#ffb74d'
  if (verdict === 'blocked') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairExecutionReadinessVerdict(verdict: string): string {
  if (verdict === 'clean') return '未开启'
  if (verdict === 'waiting_for_patch_diff') return '等待补丁'
  if (verdict === 'awaiting_explicit_approval') return '等待批准'
  if (verdict === 'ready_for_explicit_apply') return '可显式执行'
  if (verdict === 'blocked') return '阻断'
  if (verdict === 'unavailable') return '暂无就绪检查'
  return verdict || '未知'
}

function zhRepairExecutionItemStatus(status: string): string {
  if (status === 'waiting_for_patch_diff') return '等待补丁 diff'
  if (status === 'awaiting_explicit_approval') return '等待人工批准'
  if (status === 'ready_for_explicit_apply') return '可显式执行'
  if (status === 'blocked') return '阻断'
  return status || '未知'
}

function repairApplyPreviewTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'preview_ready') return '#ffb74d'
  if (verdict === 'blocked') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairApplyPreviewVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需预览'
  if (verdict === 'preview_ready') return '预览已生成'
  if (verdict === 'blocked') return '预览阻断'
  if (verdict === 'unavailable') return '暂无预览'
  return verdict || '未知'
}

function zhRepairApplyPreviewStatus(status: string): string {
  if (status === 'preview_ready') return '预览已生成'
  if (status === 'blocked') return '阻断'
  return status || '未知'
}

function repairApplyExecutionTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'ready_for_explicit_apply' || verdict === 'applied') return '#ffb74d'
  if (verdict === 'blocked' || verdict === 'stale_or_mismatch') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairApplyExecutionVerdict(verdict: string): string {
  if (verdict === 'clean') return '未开启'
  if (verdict === 'ready_for_explicit_apply') return '可显式应用'
  if (verdict === 'applied') return '已应用'
  if (verdict === 'blocked') return '阻断'
  if (verdict === 'stale_or_mismatch') return '记录不匹配'
  if (verdict === 'unavailable') return '暂无应用记录'
  return verdict || '未知'
}

function zhRepairApplyExecutionStatus(status: string): string {
  if (status === 'ready_for_explicit_apply') return '等待显式应用'
  if (status === 'applied') return '已应用'
  if (status === 'blocked') return '阻断'
  if (status === 'stale_or_mismatch') return '记录不匹配'
  return status || '未知'
}

function repairPostApplyVerificationTone(verdict: string): string {
  if (verdict === 'clean' || verdict === 'pass') return '#78d98b'
  if (verdict === 'awaiting_verification') return '#ffb74d'
  if (verdict === 'fail') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairPostApplyVerificationVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需验证'
  if (verdict === 'awaiting_verification') return '等待验证'
  if (verdict === 'pass') return '验证通过'
  if (verdict === 'fail') return '验证失败'
  if (verdict === 'unavailable') return '暂无验证'
  return verdict || '未知'
}

function zhRepairPostApplyVerificationStatus(status: string): string {
  if (status === 'pending_verification') return '等待重跑'
  if (status === 'pass') return '通过'
  if (status === 'fail') return '失败'
  return status || '未知'
}

function repairOutcomeReconciliationTone(verdict: string): string {
  if (verdict === 'clean' || verdict === 'pass') return '#78d98b'
  if (verdict === 'awaiting_verification' || verdict === 'missing_baseline' || verdict === 'partial') return '#ffb74d'
  if (verdict === 'regression') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairOutcomeReconciliationVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需对账'
  if (verdict === 'pass') return '对账通过'
  if (verdict === 'awaiting_verification') return '等待验证'
  if (verdict === 'missing_baseline') return '缺少基线'
  if (verdict === 'partial') return '部分修复'
  if (verdict === 'regression') return '出现回归'
  if (verdict === 'unavailable') return '暂无对账'
  return verdict || '未知'
}

function zhRepairOutcomeReconciliationStatus(status: string): string {
  if (status === 'reconciled') return '已对账'
  if (status === 'pending_verification') return '等待验证'
  if (status === 'missing_baseline') return '缺少基线'
  if (status === 'partial') return '部分修复'
  if (status === 'regression') return '出现回归'
  return status || '未知'
}

function repairRollbackReadinessTone(verdict: string): string {
  if (verdict === 'clean' || verdict === 'ready_for_explicit_rollback') return '#78d98b'
  if (verdict === 'missing_before_snapshot' || verdict === 'blocked') return '#ffb74d'
  if (verdict === 'stale_or_mismatch') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairRollbackReadinessVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需回滚'
  if (verdict === 'ready_for_explicit_rollback') return '可显式回滚'
  if (verdict === 'stale_or_mismatch') return '当前文件已变化'
  if (verdict === 'missing_before_snapshot') return '缺少回滚快照'
  if (verdict === 'blocked') return '回滚阻断'
  if (verdict === 'unavailable') return '暂无回滚检查'
  return verdict || '未知'
}

function zhRepairRollbackReadinessStatus(status: string): string {
  if (status === 'ready_for_explicit_rollback') return '可显式回滚'
  if (status === 'stale_or_mismatch') return '当前文件已变化'
  if (status === 'missing_before_snapshot') return '缺少 before 快照'
  if (status === 'blocked') return '阻断'
  return status || '未知'
}

function repairRollbackExecutionTone(verdict: string): string {
  if (verdict === 'clean' || verdict === 'rolled_back') return '#78d98b'
  if (verdict === 'ready_for_explicit_rollback' || verdict === 'blocked') return '#ffb74d'
  if (verdict === 'stale_or_mismatch') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairRollbackExecutionVerdict(verdict: string): string {
  if (verdict === 'clean') return '未开启'
  if (verdict === 'ready_for_explicit_rollback') return '可显式回滚'
  if (verdict === 'rolled_back') return '已回滚'
  if (verdict === 'blocked') return '回滚阻断'
  if (verdict === 'stale_or_mismatch') return '记录不匹配'
  if (verdict === 'unavailable') return '暂无回滚记录'
  return verdict || '未知'
}

function zhRepairRollbackExecutionStatus(status: string): string {
  if (status === 'ready_for_explicit_rollback') return '等待显式回滚'
  if (status === 'rolled_back') return '已回滚'
  if (status === 'blocked') return '阻断'
  if (status === 'stale_or_mismatch') return '记录不匹配'
  return status || '未知'
}

function repairRollbackPostVerificationTone(verdict: string): string {
  if (verdict === 'clean' || verdict === 'pass') return '#78d98b'
  if (verdict === 'awaiting_verification') return '#ffb74d'
  if (verdict === 'fail') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairRollbackPostVerificationVerdict(verdict: string): string {
  if (verdict === 'clean') return '无需验证'
  if (verdict === 'awaiting_verification') return '等待验证'
  if (verdict === 'pass') return '验证通过'
  if (verdict === 'fail') return '验证失败'
  if (verdict === 'unavailable') return '暂无验证'
  return verdict || '未知'
}

function zhRepairRollbackPostVerificationStatus(status: string): string {
  if (status === 'pending_verification') return '等待重跑'
  if (status === 'pass') return '通过'
  if (status === 'fail') return '失败'
  return status || '未知'
}

function repairClosureRollupTone(verdict: string): string {
  if (verdict === 'clean') return '#78d98b'
  if (verdict === 'action_required') return '#ffb74d'
  if (verdict === 'blocked') return '#ff7b7b'
  return colors.textMuted
}

function zhRepairClosureRollupVerdict(verdict: string): string {
  if (verdict === 'clean') return '当前闭合'
  if (verdict === 'action_required') return '需要处理'
  if (verdict === 'blocked') return '存在阻断'
  if (verdict === 'unavailable') return '暂无总览'
  return verdict || '未知'
}

function zhRepairClosureStageStatus(status: string): string {
  if (status === 'clean') return '无待处理'
  if (status === 'no_failure') return '暂无失败'
  if (status === 'failure_candidate') return '发现失败候选'
  if (status === 'repair_required') return '需要修复'
  if (status === 'validation_gap') return '验证缺口'
  if (status === 'no_candidate') return '暂无候选'
  if (status === 'review_required') return '等待审阅'
  if (status === 'review_or_preview_pending') return '审阅或预览待完成'
  if (status === 'reviewed_and_previewed') return '审阅和预览就绪'
  if (status === 'candidate_ready') return '候选就绪'
  if (status === 'no_patch') return '暂无补丁'
  if (status === 'approval_required') return '等待批准'
  if (status === 'approved') return '已批准'
  if (status === 'not_started') return '未开始'
  if (status === 'ready') return '就绪'
  if (status === 'ready_for_explicit_apply') return '等待显式应用'
  if (status === 'awaiting_apply') return '等待应用'
  if (status === 'applied') return '已应用'
  if (status === 'verified') return '已验证'
  if (status === 'pending_verification') return '等待验证'
  if (status === 'regression_or_persistent') return '回归/残留'
  if (status === 'not_needed') return '无需处理'
  if (status === 'ready_for_explicit_rollback') return '可显式回滚'
  if (status === 'stale_or_failed') return '失效/失败'
  if (status === 'blocked') return '阻断'
  return status || '未知'
}

function repairGeneralizationTrialTone(verdict: string): string {
  if (verdict === 'guarded_trial_ready') return '#ffb74d'
  return colors.textMuted
}

function zhRepairGeneralizationTrialVerdict(verdict: string): string {
  if (verdict === 'guarded_trial_ready') return '受控试验就绪'
  if (verdict === 'unavailable') return '暂无试验'
  return verdict || '未知'
}

function zhReadClueCategory(category: string): string {
  if (category === 'expand_pattern') return '展开匹配'
  if (category === 'expand_directory') return '展开目录'
  if (category === 'tool_trace_replay') return '工具回放'
  if (category === 'workspace_review') return '工作区复核'
  if (category === 'manual_review') return '人工确认'
  return category || '未知'
}

function zhReadClueAutomation(level: string): string {
  if (level === 'auto_expand_then_review') return '自动展开后复核'
  if (level === 'trace_replay_required') return '需要回放'
  if (level === 'manual_or_header_scan') return '文件头/人工'
  if (level === 'manual_review') return '人工确认'
  return level || '未知'
}

function llmReplayPlanTone(verdict: string): string {
  if (verdict === 'ready_for_controlled_replay' || verdict === 'no_llm_call') return '#78d98b'
  if (verdict === 'blocked') return '#ff7b7b'
  return '#ffb74d'
}

function zhLlmReplayVerdict(verdict: string): string {
  if (verdict === 'ready_for_controlled_replay') return '可受控回放'
  if (verdict === 'blocked') return '契约阻断'
  if (verdict === 'no_llm_call') return '无 LLM 调用'
  if (verdict === 'unavailable') return '暂无计划'
  return verdict || '未知'
}

function llmReplayResultTone(verdict: string): string {
  if (verdict === 'pass') return '#78d98b'
  if (verdict === 'fail') return '#ff7b7b'
  return '#ffb74d'
}

function zhLlmReplayResultVerdict(verdict: string): string {
  if (verdict === 'pass') return '已通过'
  if (verdict === 'fail') return '失败'
  if (verdict === 'blocked_by_switch') return '开关阻断'
  if (verdict === 'not_run') return '未执行'
  if (verdict === 'unavailable') return '暂无结果'
  return verdict || '未知'
}

function materializationReportForWorker(report: MaterialAttributionReport | null, workerId: string): MaterialAttributionWorkerReport | null {
  return report?.worker_reports.find((item) => item.worker_id === workerId) || null
}

function MaterialAttributionReportPanel({ report, error, loading }: {
  report: MaterialAttributionReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>material 归因报告读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在整理 material 归因报告...</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无 material 归因报告。'}</div>
      </div>
    )
  }
  const tone = attributionStatusTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-material-attribution-report>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>material 归因报告</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhAttributionStatus(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>worker {report.counts.workers}</span>
        <span style={S.pill}>生成 {report.counts.generated_artifacts}</span>
        <span style={S.pill}>线索 {report.counts.read_clues}</span>
        <span style={S.pill}>确认读取 {report.counts.confirmed_reads}</span>
        <span style={S.pill}>分组 {report.counts.read_groups ?? report.read_groups?.length ?? 0}</span>
        <span style={S.pill}>待确认 {report.counts.unconfirmed_read_clues ?? Math.max(report.counts.read_clues - report.counts.confirmed_reads, 0)}</span>
      </div>
      <div style={{ display: 'grid', gap: 6 }}>
        {report.quality_gates.slice(0, 6).map((gate) => (
          <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
              <span style={{ color: attributionStatusTone(gate.status), fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{gate.summary}</div>
          </div>
        ))}
      </div>
      {(report.read_groups || []).length > 0 && (
        <div style={{ display: 'grid', gap: 7 }} data-material-attribution-read-groups>
          <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>读取归因分组</div>
          {(report.read_groups || []).slice(0, 8).map((group) => {
            const groupTone = readGroupTone(group.status)
            return (
              <div key={group.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px solid ${groupTone}`, borderRadius: 5, background: colors.bg }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
                  <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650, overflowWrap: 'anywhere' }}>{group.title}</span>
                  <span style={{ color: groupTone, fontSize: 13, whiteSpace: 'nowrap' }}>{zhReadGroupStatus(group.status)}</span>
                </div>
                <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>{group.summary}</div>
                <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.45 }}>{group.decision}</div>
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                  <span style={S.pill}>线索 {group.count}</span>
                  <span style={S.pill}>material {group.material_count}</span>
                  <span style={S.pill}>{zhWorkerName(group.worker_id)}</span>
                </div>
                {group.sample_targets.length > 0 && (
                  <div style={{ display: 'grid', gap: 2 }}>
                    {group.sample_targets.slice(0, 3).map((target) => (
                      <div key={`${group.id}-target-${target}`} style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                        资料样例：{target}
                      </div>
                    ))}
                  </div>
                )}
                {group.sample_material_ids.length > 0 && (
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {group.sample_material_ids.slice(0, 4).map((materialId) => (
                      <span key={`${group.id}-material-${materialId}`} style={{ ...S.pill, whiteSpace: 'normal' }}>{zhMaterialName(materialId)} / {materialId}</span>
                    ))}
                  </div>
                )}
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4 }}>{group.next_action}</div>
              </div>
            )
          })}
        </div>
      )}
      {report.open_questions.length > 0 && (
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>
          仍需确认：{report.open_questions[0].summary}
        </div>
      )}
    </div>
  )
}

function TeamBuilderReadClueResolutionPanel({ plan, error, loading }: {
  plan: TeamBuilderReadClueResolutionPlan | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>读取线索消解计划读取失败：{error}</div>
      </div>
    )
  }
  if (!plan) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在整理读取线索消解计划...</div>
      </div>
    )
  }
  if (!plan.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{plan.reason || '暂无读取线索消解计划。'}</div>
      </div>
    )
  }
  const tone = attributionStatusTone(plan.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-read-clue-resolution>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>读取线索消解</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhAttributionStatus(plan.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{plan.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>线索 {plan.counts.read_clues}</span>
        <span style={S.pill}>已确认 {plan.counts.confirmed}</span>
        <span style={S.pill}>事实边 {plan.counts.confirmed_read_edges ?? 0}</span>
        <span style={S.pill}>候选仍需 {plan.counts.unresolved}</span>
        <span style={S.pill}>候选 material {plan.counts.candidate_materials ?? 0}</span>
        <span style={S.pill}>未展开 {plan.counts.unexpanded ?? 0}</span>
        <span style={S.pill}>工具动作 {plan.counts.tool_scope_confirmed ?? 0}</span>
        <span style={S.pill}>工具命中/Read {plan.counts.tool_read_confirmed_materials ?? 0}</span>
        <span style={S.pill}>内容提及线索 {plan.counts.content_mention_path_clues ?? 0}</span>
        <span style={S.pill}>内容提及 material {plan.counts.content_mention_path_materials ?? 0}</span>
        <span style={S.pill}>可展开 {plan.counts.auto_expandable}</span>
        <span style={S.pill}>需回放 {plan.counts.trace_replay_required ?? 0}</span>
        <span style={S.pill}>人工/文件头 {plan.counts.manual_review ?? 0}</span>
      </div>
      {(plan.content_mention_actions || []).length > 0 && (
        <div style={{ display: 'grid', gap: 6 }}>
          <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>内容提及解释层</div>
          {(plan.content_mention_actions || []).slice(0, 4).map((action) => (
            <div key={action.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid #8cc7c1`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650, overflowWrap: 'anywhere' }}>{action.worker_id} / {action.title || action.target}</span>
                <span style={{ color: '#8cc7c1', fontSize: 13, whiteSpace: 'nowrap' }}>不作为缺口</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.reason}</div>
              {action.review_target && (
                <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  提及目标：{action.review_target}
                </div>
              )}
              {(action.tool_confirmation?.confirmed_materials || []).length > 0 && (
                <div style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
                  {(action.tool_confirmation?.confirmed_materials || []).slice(0, 3).map((candidate) => (
                    <div key={`${action.id}-${candidate.material_id}`} style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                      内容提及：{zhMaterialName(candidate.material_id)} / {candidate.material_id}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4 }}>{action.next_action}</div>
            </div>
          ))}
        </div>
      )}
      {plan.actions.length > 0 ? (
        <div style={{ display: 'grid', gap: 6 }}>
          {plan.actions.slice(0, 4).map((action) => (
            <div key={action.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650, overflowWrap: 'anywhere' }}>{action.worker_id} / {action.title || action.target}</span>
                <span style={{ color: '#ffb74d', fontSize: 13, whiteSpace: 'nowrap' }}>{zhReadClueCategory(action.category)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.reason}</div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.next_action}</div>
              {action.review_target && (
                <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  真实目标：{action.review_target}
                </div>
              )}
              {action.review_summary && (
                <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.review_summary}</div>
              )}
              {(action.material_id_hits || []).length > 0 && (
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {(action.material_id_hits || []).slice(0, 4).map((materialId) => (
                    <span key={materialId} style={{ ...S.pill, whiteSpace: 'normal' }}>{zhMaterialName(materialId)} / {materialId}</span>
                  ))}
                </div>
              )}
              {(action.candidate_materials || []).length > 0 && (
                <div style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
                  <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>候选 material（等待真实工具事件确认）</div>
                  {(action.candidate_materials || []).slice(0, 4).map((candidate) => (
                    <div key={candidate.id || `${action.id}-${candidate.material_id}-${candidate.path || ''}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                      {zhMaterialName(candidate.material_id)} / {candidate.material_id}
                      {candidate.path ? ` / ${candidate.path}${candidate.line ? `:${candidate.line}` : ''}` : ''}
                      {candidate.basis ? ` / ${candidate.basis}` : ''}
                    </div>
                  ))}
                </div>
              )}
              {action.tool_confirmation && (
                <div style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: 'var(--fp-solid)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>工具事件确认</span>
                    <span style={{ color: action.tool_confirmation.status === 'scope_and_read_confirmed' ? '#78d98b' : '#ffb74d', fontSize: 13 }}>
                      {action.tool_confirmation.status || '未知'}
                    </span>
                  </div>
                  {action.tool_confirmation.summary && (
                    <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.tool_confirmation.summary}</div>
                  )}
                  {(action.tool_confirmation.confirmed_materials || []).slice(0, 3).map((candidate) => (
                    <div key={`${action.id}-tool-${candidate.material_id}-${candidate.path || ''}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                      {candidate.evidence_kind === 'content_mention_path' ? '内容提及路径' : candidate.evidence_kind === 'tool_result_path' ? '工具输出路径' : 'Read'}：{zhMaterialName(candidate.material_id)} / {candidate.material_id}
                      {candidate.path ? ` / ${candidate.path}` : ''}
                      {candidate.basis ? ` / ${candidate.basis}` : ''}
                    </div>
                  ))}
                  {(action.tool_confirmation.matching_events || []).slice(0, 2).map((event) => (
                    <div key={`${action.id}-event-${event.index ?? event.tool}`} style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                      事件：{event.tool || 'tool'}#{event.index ?? '?'} / {(event.targets || []).join(' / ')}
                    </div>
                  ))}
                </div>
              )}
              {(action.review_examples || []).length > 0 && (
                <div style={{ display: 'grid', gap: 3 }}>
                  {(action.review_examples || []).slice(0, 3).map((example) => (
                    <div key={`${action.id}-${example.path}-${example.line || 0}`} style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                      示例：{example.path}{example.line ? `:${example.line}` : ''}
                      {example.material_ids && example.material_ids.length ? ` / material_id ${example.material_ids.slice(0, 2).join(', ')}` : ''}
                      {example.excerpt ? ` / ${example.excerpt}` : ''}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                {zhReadClueAutomation(action.automation_level)} / {action.evidence_summary || action.target}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13 }}>当前没有待消解候选读取线索。</div>
      )}
      {plan.source.read_clue_resolution_plan_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          消解计划 material：{plan.source.read_clue_resolution_plan_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderMaterialGapValidationPanel({ report, error, loading }: {
  report: TeamBuilderMaterialGapValidation | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>material 缺口验证读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在验证待确认 material 线索...' : '暂无 material 缺口验证报告。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无 material 缺口验证报告。'}</div>
      </div>
    )
  }
  const tone = attributionStatusTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-material-gap-validation>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>material 缺口验证</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhAttributionStatus(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>目标 {report.counts.targets}</span>
        <span style={S.pill}>已定位 {report.counts.resolved_targets}</span>
        <span style={S.pill}>迁移映射 {report.counts.relocated_targets ?? 0}</span>
        <span style={S.pill}>material_id {report.counts.material_id_hits}</span>
        <span style={S.pill}>缺失 {report.counts.missing_targets}</span>
      </div>
      {report.groups.slice(0, 3).map((group) => (
        <div key={group.id} style={{ display: 'grid', gap: 6, padding: '7px 8px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'start' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650, overflowWrap: 'anywhere' }}>{zhWorkerName(group.worker_id)} / {group.title}</span>
            <span style={{ color: group.status === 'partial' ? '#ffb74d' : colors.textMuted, fontSize: 13 }}>{group.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{group.summary}</div>
          {group.targets.slice(0, 4).map((target) => (
            <div key={`${group.id}-${target.target}`} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                {target.target}
              </div>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                <span style={S.pill}>{target.status}</span>
                {target.resolution_kind && <span style={S.pill}>{target.resolution_kind}</span>}
              </div>
              {target.resolution_note && (
                <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{target.resolution_note}</div>
              )}
              {target.resolved_paths.length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                  当前文件：{target.resolved_paths.join(' / ')}
                </div>
              )}
              {target.material_ids.length > 0 && (
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {target.material_ids.slice(0, 3).map((materialId) => (
                    <span key={`${target.target}-${materialId}`} style={{ ...S.pill, whiteSpace: 'normal' }}>{zhMaterialName(materialId)} / {materialId}</span>
                  ))}
                </div>
              )}
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{target.decision}</div>
            </div>
          ))}
        </div>
      ))}
      {report.source.material_gap_validation_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          验证报告 material：{report.source.material_gap_validation_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderTestReportPanel({ report, error, loading }: {
  report: TeamBuilderTestReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>生成包测试报告读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在整理生成包测试报告...</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无生成包测试报告。'}</div>
      </div>
    )
  }
  const tone = attributionStatusTone(report.verdict)
  const runSmoke = report.worker_run_smoke
  const runTone = attributionStatusTone(runSmoke?.status || 'warning')
  const doctorFindings = report.doctor_findings || []
  const contractCoverage = report.contract_coverage
  const contractTone = attributionStatusTone(contractCoverage?.verdict || 'warning')
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-test-report>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>生成包测试报告</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhAttributionStatus(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>文件 {report.counts.files}</span>
        <span style={S.pill}>Python {report.counts.python_files}</span>
        <span style={S.pill}>worker {report.counts.worker_files}</span>
        <span style={S.pill}>节点 {report.counts.nodes}</span>
        <span style={S.pill}>绑定 {report.counts.bindings}</span>
        <span style={S.pill}>执行业务 {report.counts.executed_workers ?? 0}</span>
        <span style={S.pill}>模型桩 {report.counts.stubbed_workers ?? 0}</span>
        <span style={S.pill}>跳过 {report.counts.skipped_workers ?? 0}</span>
        <span style={S.pill}>doctor finding {report.counts.doctor_findings ?? 0}</span>
      </div>
      {runSmoke && (
        <div style={{ border: `1px solid ${runTone}`, borderRadius: 5, padding: 8, display: 'grid', gap: 7, background: colors.bg }} data-worker-run-smoke>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>worker 业务运行</span>
            <span style={{ color: runTone, fontSize: 13 }}>{zhAttributionStatus(runSmoke.status)}</span>
          </div>
          {runSmoke.executed_workers.length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              {runSmoke.executed_workers.slice(0, 4).map((worker) => (
                <div key={`executed-${worker.worker_id}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  已执行：<span style={{ color: colors.textSecondary }}>{worker.worker_id}</span> / {worker.kind || '未知结果'}{worker.output_material ? ` / 输出 ${worker.output_material}` : ''}
                </div>
              ))}
            </div>
          )}
          {(runSmoke.stubbed_workers || []).length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              {(runSmoke.stubbed_workers || []).slice(0, 4).map((worker) => (
                <div key={`stubbed-${worker.worker_id}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  模型桩验证：<span style={{ color: colors.textSecondary }}>{worker.worker_id}</span> / {worker.kind || '未知结果'}{worker.output_material ? ` / 输出 ${worker.output_material}` : ''}
                </div>
              ))}
            </div>
          )}
          {(runSmoke.llm_stub_calls || []).length > 0 && (
            <div style={{ display: 'grid', gap: 4 }} data-llm-stub-evidence>
              {(runSmoke.llm_stub_calls || []).slice(0, 2).map((call, index) => (
                <div key={`llm-stub-${index}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  模型桩证据：模型 {call.model || '未声明'} / system {call.system_chars ?? 0} 字 / user {call.user_chars ?? 0} 字 / 输出键 {(call.expected_output_keys || []).join(', ') || '未声明'}
                  {call.has_json_instruction ? ' / 要求 JSON' : ''}
                  {call.has_chinese_instruction ? ' / 要求中文' : ''}
                </div>
              ))}
            </div>
          )}
          {runSmoke.skipped_workers.length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              {runSmoke.skipped_workers.slice(0, 4).map((worker) => (
                <div key={`skipped-${worker.worker_id}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  跳过：<span style={{ color: colors.textSecondary }}>{worker.worker_id}</span> / {worker.reason}{worker.summary ? ` / ${worker.summary}` : ''}
                </div>
              ))}
            </div>
          )}
          {runSmoke.failed_workers.length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              {runSmoke.failed_workers.slice(0, 3).map((worker) => (
                <div key={`failed-${worker.worker_id}`} style={{ color: '#ff8a80', fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  失败：<span>{worker.worker_id}</span> / {worker.diagnosis || worker.kind || '未知错误'}
                </div>
              ))}
            </div>
          )}
          {(runSmoke.seed_materials.length > 0 || runSmoke.produced_materials.length > 0) && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
              样例输入：{runSmoke.seed_materials.join(', ') || '无'}；本次产出：{runSmoke.produced_materials.join(', ') || '无'}
            </div>
          )}
        </div>
      )}
      {contractCoverage?.available && (
        <div style={{ border: `1px solid ${contractTone}`, borderRadius: 5, padding: 8, display: 'grid', gap: 7, background: colors.bg }} data-team-builder-contract-coverage>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>contract 覆盖</span>
            <span style={{ color: contractTone, fontSize: 13 }}>{zhAttributionStatus(contractCoverage.verdict)}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>{contractCoverage.summary}</div>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            <span style={S.pill}>已登记 {contractCoverage.counts.available_contracts}</span>
            <span style={S.pill}>同名匹配 {contractCoverage.counts.matching_contracts}</span>
            <span style={S.pill}>已执行 {contractCoverage.counts.executed_contracts}</span>
            <span style={S.pill}>缺口 {contractCoverage.counts.missing_contracts}</span>
          </div>
          {(contractCoverage.matching_contracts.length > 0 ? contractCoverage.matching_contracts : contractCoverage.available_contracts.slice(0, 3)).length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              {(contractCoverage.matching_contracts.length > 0 ? contractCoverage.matching_contracts : contractCoverage.available_contracts.slice(0, 3)).map((item) => (
                <div key={`${item.slug}-${item.path}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  {contractCoverage.matching_contracts.length > 0 ? '匹配 contract' : '可参考 contract'}：
                  <span style={{ color: colors.textSecondary }}>{item.slug}</span>
                  {item.pipeline_name ? ` / pipeline ${item.pipeline_name}` : ''}
                  {item.path ? ` / ${item.path}` : ''}
                </div>
              ))}
            </div>
          )}
          {contractCoverage.quality_gates.length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              {contractCoverage.quality_gates.slice(0, 3).map((gate) => (
                <div key={gate.id} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>
                  <span style={{ color: attributionStatusTone(gate.status) }}>{zhAttributionStatus(gate.status)}</span>
                  <span style={{ color: colors.textSecondary }}> / {gate.name}</span> / {gate.summary}
                </div>
              ))}
            </div>
          )}
          <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.45, overflowWrap: 'anywhere' }}>
            下一步：{contractCoverage.next_action}
          </div>
          {contractCoverage.latest_execution?.available && (
            <div style={{ border: `1px solid ${colors.border}`, borderRadius: 5, padding: 7, display: 'grid', gap: 5, background: '#05070a' }} data-team-builder-contract-execution>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>contract 执行结果</span>
                <span style={{ color: attributionStatusTone(contractCoverage.latest_execution.verdict || 'warning'), fontSize: 13 }}>
                  {zhAttributionStatus(contractCoverage.latest_execution.verdict || 'warning')}
                </span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{contractCoverage.latest_execution.summary || '已找到 contract 执行结果。'}</div>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                <span style={S.pill}>执行 {contractCoverage.latest_execution.counts?.executed_contracts ?? 0}</span>
                <span style={S.pill}>通过 {contractCoverage.latest_execution.counts?.passed_contracts ?? 0}</span>
                <span style={S.pill}>失败 {contractCoverage.latest_execution.counts?.failed_contracts ?? 0}</span>
              </div>
              {(contractCoverage.latest_execution.contracts || []).slice(0, 3).map((item) => (
                <div key={`${item.slug}-${item.path}-${item.status}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                  <span style={{ color: attributionStatusTone(item.status || 'warning') }}>{zhAttributionStatus(item.status || 'warning')}</span>
                  <span style={{ color: colors.textSecondary }}> / {item.slug || item.path}</span>
                  {item.returncode !== undefined ? ` / exit ${item.returncode}` : ''}
                  {item.stdout_tail ? ` / ${item.stdout_tail.split('\n').slice(-1)[0]}` : ''}
                </div>
              ))}
            </div>
          )}
          {(contractCoverage.source?.contract_coverage_material || report.source.contract_coverage_material) && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
              contract 覆盖 material：{contractCoverage.source?.contract_coverage_material || report.source.contract_coverage_material}
              {(contractCoverage.source?.contract_execution_material || report.source.contract_execution_material) ? `；contract 执行 material：${contractCoverage.source?.contract_execution_material || report.source.contract_execution_material}` : ''}
            </div>
          )}
        </div>
      )}
      {doctorFindings.length > 0 && (
        <div style={{ border: `1px solid ${colors.border}`, borderRadius: 5, padding: 8, display: 'grid', gap: 6, background: colors.bg }} data-team-builder-test-findings>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>doctor 发现</span>
            <span style={{ color: colors.textMuted, fontSize: 13 }}>{doctorFindings.length} 条</span>
          </div>
          {doctorFindings.slice(0, 3).map((finding) => (
            <div key={finding.id || `${finding.check_id}-${finding.location}`} style={{ display: 'grid', gap: 2, color: colors.textMuted, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
              <div>
                <span style={{ color: doctorTone(finding.level)?.border || colors.textSecondary }}>{zhDoctorLevel(finding.level)}</span>
                <span style={{ color: colors.textSecondary }}> / {finding.check_id}</span>
                <span> / {finding.location}</span>
              </div>
              <div>{finding.observation}</div>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'grid', gap: 6 }}>
        {report.quality_gates.map((gate) => (
          <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
              <span style={{ color: attributionStatusTone(gate.status), fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{gate.summary}</div>
          </div>
        ))}
      </div>
      {report.source.report_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          测试报告 material：{report.source.report_material}
          {report.source.doctor_findings_material ? `；doctor findings：${report.source.doctor_findings_material}` : ''}
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairPlanPanel({ plan, error, loading }: {
  plan: TeamBuilderRepairPlan | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复准备计划读取失败：{error}</div>
      </div>
    )
  }
  if (!plan) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在整理修复准备计划...</div>
      </div>
    )
  }
  if (!plan.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{plan.reason || '暂无修复准备计划。'}</div>
      </div>
    )
  }
  const tone = repairPlanTone(plan.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-plan>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复准备计划</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairPlanVerdict(plan.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{plan.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>动作 {plan.counts.actions}</span>
        <span style={S.pill}>需修复 {plan.counts.repair_required}</span>
        <span style={S.pill}>验证缺口 {plan.counts.validation_gap}</span>
        <span style={S.pill}>自动安全 {plan.counts.auto_safe}</span>
      </div>
      {plan.actions.slice(0, 3).map((action) => (
        <div key={action.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{zhRepairActionCategory(action.category)} / {action.location}</span>
            <span style={{ color: action.auto_safe ? '#78d98b' : colors.textMuted, fontSize: 13 }}>{action.auto_safe ? '可自动' : '不自动'}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.next_action}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4 }}>{action.rationale}</div>
          {(action.validation_actions || []).length > 0 && (
            <div style={{ display: 'grid', gap: 4, marginTop: 3 }} data-team-builder-validation-actions>
              {(action.validation_actions || []).slice(0, 3).map((validation) => (
                <div key={validation.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{validation.title}</span>
                    <span style={{ color: colors.textFaint, fontSize: 13, whiteSpace: 'nowrap' }}>{validation.action_kind}</span>
                  </div>
                  <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{validation.summary}</div>
                  {validation.endpoint && <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>接口：{validation.endpoint}</div>}
                  {validation.expected_result && <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{validation.expected_result}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      {plan.source.repair_plan_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          修复计划 material：{plan.source.repair_plan_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairProbePanel({ report, error, loading }: {
  report: TeamBuilderRepairProbeReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>故障修复探针读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在执行故障修复探针...' : '暂无故障修复探针结果。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无故障修复探针结果。'}</div>
      </div>
    )
  }
  const tone = attributionStatusTone(report.verdict)
  const failedWorkers = report.worker_run_smoke?.failed_workers || []
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-probe>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>故障修复探针</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhAttributionStatus(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>捕获失败 {report.counts.captured_failures}</span>
        <span style={S.pill}>诊断 {report.counts.doctor_findings}</span>
        <span style={S.pill}>需修复 {report.counts.repair_required}</span>
        <span style={S.pill}>自动安全 {report.counts.auto_safe}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 3).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {failedWorkers.slice(0, 2).map((worker) => (
        <div key={worker.worker_id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{zhWorkerName(worker.worker_id)} / {worker.worker_id}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{worker.diagnosis}</div>
          {worker.output_material && <div style={{ color: colors.textFaint, fontSize: 13, overflowWrap: 'anywhere' }}>输出 material：{worker.output_material}</div>}
        </div>
      ))}
      <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
        repair 结论：{zhRepairPlanVerdict(report.repair_plan.verdict)}。{report.repair_plan.summary}
      </div>
      {report.source.repair_probe_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          探针 material：{report.source.repair_probe_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairDryRunPanel({ report, error, loading }: {
  report: TeamBuilderRepairDryRunReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复干跑探针读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在执行修复干跑探针...' : '暂无修复干跑探针结果。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复干跑探针结果。'}</div>
      </div>
    )
  }
  const tone = attributionStatusTone(report.verdict)
  const diffText = report.patch_plan.diff || ''
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-dry-run>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复干跑探针</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhAttributionStatus(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
        <div style={S.metric}><div style={S.metricValue}>{report.counts.before_failures}</div><div style={S.metricName}>修复前失败</div></div>
        <div style={S.metric}><div style={S.metricValue}>{report.counts.after_failures}</div><div style={S.metricName}>修复后失败</div></div>
        <div style={S.metric}><div style={S.metricValue}>{report.counts.before_findings}</div><div style={S.metricName}>前诊断</div></div>
        <div style={S.metric}><div style={S.metricValue}>{report.counts.after_findings}</div><div style={S.metricName}>后诊断</div></div>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>补丁文件 {report.counts.patch_files}</span>
        <span style={S.pill}>修复 worker {report.counts.fixed_workers}</span>
        <span style={S.pill}>自动安全 {report.counts.auto_safe}</span>
        <span style={S.pill}>范围 {report.patch_plan.scope || '-'}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 5).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
        <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{report.patch_plan.title || '补丁计划'}</div>
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.patch_plan.summary || '-'}</div>
        <div style={{ color: colors.textFaint, fontSize: 13, overflowWrap: 'anywhere' }}>
          文件：{(report.patch_plan.changed_files || []).join('、') || '-'}
        </div>
        {(report.patch_plan.policy_rule_ids || []).length > 0 && (
          <div style={{ color: colors.textFaint, fontSize: 13, overflowWrap: 'anywhere' }}>
            策略：{(report.patch_plan.policy_rule_ids || []).join('、')}
          </div>
        )}
        {diffText && (
          <pre style={{ margin: 0, padding: 7, borderRadius: 4, background: '#050708', color: colors.textMuted, fontSize: 13, lineHeight: 1.35, maxHeight: 150, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
            {diffText}
          </pre>
        )}
      </div>
      {report.source.repair_dry_run_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          干跑 material：{report.source.repair_dry_run_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairPatchCandidatesPanel({ report, error, loading }: {
  report: TeamBuilderRepairPatchCandidatesReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>候选补丁计划读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在整理候选补丁计划...' : '暂无候选补丁计划。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无候选补丁计划。'}</div>
      </div>
    )
  }
  const tone = repairPatchCandidateTone(report.verdict)
  const dryCounts = report.dry_run_reference?.counts || {}
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-patch-candidates>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>候选补丁计划</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairPatchVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>候选 {report.counts.candidates}</span>
        <span style={S.pill}>源码已定位 {report.counts.source_located}</span>
        <span style={S.pill}>缺源码 {report.counts.source_missing || 0}</span>
        <span style={S.pill}>需人工 {report.counts.manual_required || 0}</span>
        <span style={S.pill}>自动安全 {report.counts.auto_safe}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.candidates.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.candidates.slice(0, 3).map((candidate) => {
            const candidateTitle = candidate.worker_id
              ? `${zhWorkerName(candidate.worker_id)} / ${candidate.worker_id}`
              : 'team 级 contract 失败'
            return (
              <div key={candidate.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }} data-team-builder-repair-patch-candidate>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{candidateTitle}</span>
                  <span style={{ color: candidate.status === 'source_located' ? '#78d98b' : '#ffb74d', fontSize: 13 }}>{zhRepairPatchCandidateStatus(candidate.status)}</span>
                </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{candidate.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                finding：{candidate.finding_id}
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                策略：{candidate.policy_rule_id || '-'}；模式：{candidate.proposed_patch.mode}；范围：{candidate.proposed_patch.scope}
              </div>
              {candidate.source_candidates.length > 0 && (
                <div style={{ display: 'grid', gap: 4 }}>
                  {candidate.source_candidates.slice(0, 4).map((source) => (
                    <div key={source.path} style={{ display: 'grid', gap: 2 }}>
                      <div style={{ color: source.exists ? colors.textSecondary : colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                        {source.exists ? '可审阅源码' : '候选路径'}：{source.path}
                        {(source.material_ids || []).length > 0 ? `；material ${source.material_ids?.join('、')}` : ''}
                      </div>
                      {source.excerpt && (
                        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                          源码摘要：{source.excerpt}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {(candidate.contract_sources || []).length > 0 && (
                <div style={{ display: 'grid', gap: 4, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
                  <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>失败 contract 定义</div>
                  {(candidate.contract_sources || []).slice(0, 3).map((source) => (
                    <div key={source.path} style={{ display: 'grid', gap: 2 }}>
                      <div style={{ color: source.exists ? colors.textSecondary : colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                        {source.exists ? '验收定义' : '候选定义'}：{source.path}
                      </div>
                      {source.excerpt && (
                        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                          定义摘要：{source.excerpt}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{candidate.next_action}</div>
              <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>
                安全边界：{candidate.safety.reason}
              </div>
              {candidate.verification_commands.length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                  验证：{candidate.verification_commands.join('；')}
                </div>
              )}
            </div>
            )
          })}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前真实 run 没有需要生成代码补丁的 repair_required action；下面的干跑参考只证明修复链路设施可用。
        </div>
      )}
      {report.dry_run_reference && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>受控干跑参考：{zhAttributionStatus(report.dry_run_reference.verdict || '')}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{report.dry_run_reference.summary || '-'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
            修复前失败 {dryCounts.before_failures ?? '-'}；补丁文件 {dryCounts.patch_files ?? '-'}；修复后失败 {dryCounts.after_failures ?? '-'}；修复后诊断 {dryCounts.after_findings ?? '-'}
          </div>
        </div>
      )}
      {report.source.repair_patch_candidates_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>候选补丁 material：{report.source.repair_patch_candidates_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-patch-candidates/latest" label="候选报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairApplyGatePanel({ report, error, loading }: {
  report: TeamBuilderRepairApplyGateReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复应用门读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在整理修复应用门...' : '暂无修复应用门。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复应用门。'}</div>
      </div>
    )
  }
  const tone = repairApplyGateTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-apply-gate>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复应用门</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairApplyGateVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>候选 {report.counts.candidates}</span>
        <span style={S.pill}>可审阅 {report.counts.apply_ready}</span>
        <span style={S.pill}>源码定位 {report.counts.source_located}</span>
        <span style={S.pill}>人工确认 {report.counts.manual_required}</span>
        <span style={S.pill}>自动应用 {report.counts.auto_apply_allowed}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 5).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.review_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.review_items.slice(0, 3).map((item) => {
            const itemTitle = item.worker_id
              ? `${zhWorkerName(item.worker_id)} / ${item.worker_id}`
              : 'team 级 contract 失败'
            return (
              <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{itemTitle}</span>
                  <span style={{ color: item.status === 'ready_for_human_review' ? '#ffb74d' : '#ff7b7b', fontSize: 13 }}>{item.status === 'ready_for_human_review' ? '待人工审阅' : '阻断'}</span>
                </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>finding：{item.finding_id}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{item.changed_files.join('、') || '-'}</div>
              {item.source_files.length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>源码：{item.source_files.join('、')}</div>
              )}
              {(item.contract_files || []).length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>失败 contract：{(item.contract_files || []).join('、')}</div>
              )}
              <div style={{ display: 'grid', gap: 3 }}>
                {item.required_confirmations.slice(0, 5).map((confirmation) => (
                  <div key={confirmation} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>确认项：{confirmation}</div>
                ))}
              </div>
              {item.blocked_reasons.length > 0 && (
                <div style={{ color: '#ff7b7b', fontSize: 13, lineHeight: 1.35 }}>
                  阻断原因：{item.blocked_reasons.join('；')}
                </div>
              )}
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                回查：{item.verification_commands.join('；') || '-'}
              </div>
              <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>{item.safety.reason}</div>
            </div>
            )
          })}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有 repair_required 候选，因此真实补丁应用门没有开启。
        </div>
      )}
      {report.source.repair_apply_gate_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>应用门 material：{report.source.repair_apply_gate_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-apply-gate/latest" label="应用门报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairPatchDiffProposalPanel({ report, error, loading }: {
  report: TeamBuilderRepairPatchDiffProposalReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>补丁 diff proposal 读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在整理补丁 diff proposal...' : '暂无补丁 diff proposal。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无补丁 diff proposal。'}</div>
      </div>
    )
  }
  const tone = repairPatchDiffProposalTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-patch-diff-proposal>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>补丁 diff proposal</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairPatchDiffProposalVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>候选 {report.counts.candidates}</span>
        <span style={S.pill}>已有 diff {report.counts.diff_ready}</span>
        <span style={S.pill}>需生成 {report.counts.needs_ai_or_human_diff}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>越界 {report.counts.unsafe_targets}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.proposals.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.proposals.slice(0, 3).map((proposal) => {
            const title = proposal.worker_id
              ? `${zhWorkerName(proposal.worker_id)} / ${proposal.worker_id}`
              : 'team 级 contract 失败'
            return (
              <div key={proposal.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{title}</span>
                  <span style={{ color: proposal.status === 'blocked' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairPatchDiffProposalStatus(proposal.status)}</span>
                </div>
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{proposal.changed_files.join('、') || '-'}</div>
                <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{proposal.reason}</div>
                {proposal.missing_requirements.length > 0 && (
                  <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>还缺：{proposal.missing_requirements.join('；')}</div>
                )}
                {proposal.diff && (
                  <pre style={{ margin: 0, maxHeight: 140, overflow: 'auto', whiteSpace: 'pre-wrap', color: colors.textSecondary, background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 5, padding: 7, fontSize: 13, lineHeight: 1.35 }}>
                    {proposal.diff}
                  </pre>
                )}
                {proposal.patch_request?.context_files?.length > 0 && (
                  <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>上下文：{proposal.patch_request.context_files.join('、')}</div>
                )}
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>{proposal.safety.reason}</div>
              </div>
            )
          })}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有 repair_required 候选，因此无需生成补丁 diff。
        </div>
      )}
      {report.source.repair_patch_diff_proposal_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>diff proposal material：{report.source.repair_patch_diff_proposal_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-patch-diff-proposal/latest" label="diff 报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairApprovalPanel({ report, error, loading }: {
  report: TeamBuilderRepairApprovalReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复批准记录读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在读取修复批准记录...' : '暂无修复批准记录。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复批准记录。'}</div>
      </div>
    )
  }
  const tone = repairApprovalTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-approval>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复批准记录</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairApprovalVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>proposal {report.counts.proposals}</span>
        <span style={S.pill}>可批准 {report.counts.approvable}</span>
        <span style={S.pill}>已批准 {report.counts.approved}</span>
        <span style={S.pill}>等待 {report.counts.awaiting_approval}</span>
        <span style={S.pill}>失效 {report.counts.stale_or_mismatch}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.approval_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 6 }}>
          {report.approval_items.slice(0, 3).map((item) => (
            <div key={item.candidate_id} style={{ display: 'grid', gap: 4, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'stale_or_mismatch' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairApprovalStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>diff sha256：{item.diff_sha256 || '-'}</div>
              {item.approved_by && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>批准：{item.approved_by} / {item.approved_at || '-'}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有可批准的补丁 diff。
        </div>
      )}
      {(report.source.repair_approval_report_material || report.source.repair_approval_records_material) && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>批准报告 material：{report.source.repair_approval_report_material || '-'}</span>
          <span>记录 material：{report.source.repair_approval_records_material || '-'}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-approval/latest" label="批准报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairExecutionReadinessPanel({ report, error, loading }: {
  report: TeamBuilderRepairExecutionReadinessReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复执行就绪读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查修复执行就绪状态...' : '暂无修复执行就绪检查。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复执行就绪检查。'}</div>
      </div>
    )
  }
  const tone = repairExecutionReadinessTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-execution-readiness>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复执行就绪</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairExecutionReadinessVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>候选 {report.counts.candidates}</span>
        <span style={S.pill}>审阅通过 {report.counts.review_ready}</span>
        <span style={S.pill}>已有 diff {report.counts.diff_ready}</span>
        <span style={S.pill}>已批准 {report.counts.approval_recorded}</span>
        <span style={S.pill}>可执行 {report.counts.execution_ready}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 5).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.execution_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.execution_items.slice(0, 3).map((item) => {
            const itemTitle = item.worker_id
              ? `${zhWorkerName(item.worker_id)} / ${item.worker_id}`
              : 'team 级 contract 失败'
            return (
              <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{itemTitle}</span>
                  <span style={{ color: item.status === 'blocked' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairExecutionItemStatus(item.status)}</span>
                </div>
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>finding：{item.finding_id}</div>
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{item.changed_files.join('、') || '-'}</div>
                {(item.contract_files || []).length > 0 && (
                  <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>失败 contract：{(item.contract_files || []).join('、')}</div>
                )}
                {item.missing_requirements.length > 0 && (
                  <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>
                    还缺：{item.missing_requirements.join('；')}
                  </div>
                )}
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                  回查：{item.verification_commands.join('；') || '-'}
                </div>
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>{item.safety.reason}</div>
              </div>
            )
          })}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有 repair_required 候选，因此真实修复执行没有开启。
        </div>
      )}
      {report.source.repair_execution_readiness_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>执行就绪 material：{report.source.repair_execution_readiness_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-execution-readiness/latest" label="就绪报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairApplyPreviewPanel({ report, error, loading }: {
  report: TeamBuilderRepairApplyPreviewReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复应用预览读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在生成修复应用预览...' : '暂无修复应用预览。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复应用预览。'}</div>
      </div>
    )
  }
  const tone = repairApplyPreviewTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-apply-preview>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复应用预览</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairApplyPreviewVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>候选 {report.counts.items}</span>
        <span style={S.pill}>预览 {report.counts.preview_ready}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>scratch 文件 {report.counts.files_written}</span>
        {typeof report.counts.files_previewed === 'number' && (
          <span style={S.pill}>逐文件 {report.counts.files_previewed}</span>
        )}
        {typeof report.counts.multi_file_preview_ready === 'number' && (
          <span style={S.pill}>文件集预览 {report.counts.multi_file_preview_ready}</span>
        )}
        <span style={S.pill}>真实写入 {report.counts.real_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.preview_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.preview_items.slice(0, 3).map((item) => (
            <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'blocked' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairApplyPreviewStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{item.changed_files.join('、') || '-'}</div>
              {item.multi_file && (
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>多文件候选：当前只生成 scratch 文件集预览，真实应用仍需单独扩展。</div>
              )}
              {item.before_preview_files.length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>预览前：{item.before_preview_files.join('、')}</div>
              )}
              {item.after_preview_files.length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>预览后：{item.after_preview_files.join('、')}</div>
              )}
              {(item.file_previews || []).length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                  逐文件 diff：{(item.file_previews || []).map((file) => `${file.changed_file} ${file.diff_sha256.slice(0, 10)}`).join('；')}
                </div>
              )}
              {item.blocked_reasons.length > 0 && (
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>阻断原因：{item.blocked_reasons.join('；')}</div>
              )}
              <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>{item.safety.reason}</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有可执行候选，因此无需生成应用预览；这一层只会写 _scratch，不写真实文件。
        </div>
      )}
      {report.source.repair_apply_preview_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>应用预览 material：{report.source.repair_apply_preview_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-apply-preview/latest" label="预览报告" />
        </div>
      )}
    </div>
  )
}

function repairShortSha(value?: string) {
  return value ? value.slice(0, 12) : '-'
}

function repairBoolLabel(value?: boolean) {
  if (value === true) return '是'
  if (value === false) return '否'
  return '未知'
}

function TeamBuilderRepairFileRecordList({ records, mode }: {
  records?: TeamBuilderRepairFileRecord[]
  mode: 'apply' | 'rollback'
}) {
  const visibleRecords = (records || []).filter((record) => record.changed_file).slice(0, 4)
  if (visibleRecords.length === 0) return null
  const total = records?.length || visibleRecords.length
  return (
    <div style={{ display: 'grid', gap: 4 }}>
      {visibleRecords.map((record, index) => (
        <div key={`${record.changed_file}:${index}`} style={{ display: 'grid', gap: 2, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650, overflowWrap: 'anywhere' }}>{record.changed_file}</div>
          {mode === 'apply' ? (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
              before {repairShortSha(record.before_sha256)} → after {repairShortSha(record.after_sha256)}
              {record.current_sha256 ? `；当前 ${repairShortSha(record.current_sha256)}` : ''}
            </div>
          ) : (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
              当前 {repairShortSha(record.current_sha256)}；回滚 {repairShortSha(record.rollback_from_sha256 || record.after_sha256)} → {repairShortSha(record.rollback_to_sha256 || record.before_sha256)}
            </div>
          )}
          {(record.before_preview_file || record.after_preview_file) && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
              预览：{record.before_preview_file || '-'}{record.after_preview_file ? ` / ${record.after_preview_file}` : ''}
            </div>
          )}
          {mode === 'rollback' && (record.target_scope_safe !== undefined || record.current_matches_after !== undefined || record.before_snapshot_valid !== undefined) && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
              范围安全 {repairBoolLabel(record.target_scope_safe)}；当前匹配 {repairBoolLabel(record.current_matches_after)}；快照可校验 {repairBoolLabel(record.before_snapshot_valid)}
            </div>
          )}
        </div>
      ))}
      {total > visibleRecords.length && (
        <div style={{ color: colors.textMuted, fontSize: 13 }}>还有 {total - visibleRecords.length} 个文件记录，完整内容见 material。</div>
      )}
    </div>
  )
}

function TeamBuilderRepairApplyExecutionPanel({ report, error, loading }: {
  report: TeamBuilderRepairApplyExecutionReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复真实应用记录读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查修复真实应用记录...' : '暂无修复真实应用记录。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复真实应用记录。'}</div>
      </div>
    )
  }
  const tone = repairApplyExecutionTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-apply-execution>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复真实应用</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairApplyExecutionVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>候选 {report.counts.items}</span>
        <span style={S.pill}>待应用 {report.counts.preview_ready}</span>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>真实写入 {report.counts.real_writes}</span>
        {report.counts.file_set_ready !== undefined && <span style={S.pill}>文件集待应用 {report.counts.file_set_ready}</span>}
        {report.counts.file_set_applied !== undefined && <span style={S.pill}>文件集已应用 {report.counts.file_set_applied}</span>}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.apply_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.apply_items.slice(0, 3).map((item) => (
            <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'blocked' || item.status === 'stale_or_mismatch' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairApplyExecutionStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{item.changed_files.join('、') || '-'}</div>
              {(item.file_set || (item.file_count || 0) > 1) && (
                <div style={{ color: '#78d98b', fontSize: 13, lineHeight: 1.35 }}>
                  文件集修复：共 {item.file_count || item.changed_files.length} 个文件；真实应用必须额外携带 confirm_file_set_write。
                </div>
              )}
              <TeamBuilderRepairFileRecordList records={item.file_records} mode="apply" />
              {item.applied_by && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>执行人：{item.applied_by} {item.applied_at ? ` / ${item.applied_at}` : ''}</div>
              )}
              {item.blocked_reasons.length > 0 && (
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>阻断原因：{item.blocked_reasons.join('；')}</div>
              )}
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>diff sha：{item.diff_sha256 || '-'}</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有可执行候选；GET 只展示记录，不会写真实文件。真实应用必须走显式 POST execute 并携带确认 token。
        </div>
      )}
      {report.source.repair_apply_execution_report_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>真实应用 material：{report.source.repair_apply_execution_report_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-apply-execution/latest" label="应用记录" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairPostApplyVerificationPanel({ report, error, loading }: {
  report: TeamBuilderRepairPostApplyVerificationReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>应用后验证读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查应用后验证...' : '暂无应用后验证。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无应用后验证。'}</div>
      </div>
    )
  }
  const tone = repairPostApplyVerificationTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-post-apply-verification>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>应用后验证</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairPostApplyVerificationVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>已验证 {report.counts.verified}</span>
        <span style={S.pill}>等待 {report.counts.pending}</span>
        <span style={S.pill}>失败 {report.counts.failed}</span>
        <span style={S.pill}>doctor {report.counts.doctor_findings ?? 0}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.verification_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.verification_items.slice(0, 3).map((item) => (
            <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'fail' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairPostApplyVerificationStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{item.changed_files.join('、') || '-'}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>回查：{item.required_commands.join('；') || '-'}</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有已应用补丁需要验证；GET 只读，应用后重跑必须走显式 POST execute。
        </div>
      )}
      {report.source.repair_post_apply_verification_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>应用后验证 material：{report.source.repair_post_apply_verification_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-post-apply-verification/latest" label="验证报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairOutcomeReconciliationPanel({ report, error, loading }: {
  report: TeamBuilderRepairOutcomeReconciliationReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>补丁前后对账读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查补丁前后对账...' : '暂无补丁前后对账。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无补丁前后对账。'}</div>
      </div>
    )
  }
  const tone = repairOutcomeReconciliationTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-outcome-reconciliation>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>补丁前后对账</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairOutcomeReconciliationVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>已对账 {report.counts.reconciled}</span>
        <span style={S.pill}>已消除 {report.counts.resolved_findings}</span>
        <span style={S.pill}>新增 {report.counts.introduced_findings}</span>
        <span style={S.pill}>残留 {report.counts.persistent_findings}</span>
        <span style={S.pill}>缺基线 {report.counts.missing_baseline}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.reconciliation_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.reconciliation_items.slice(0, 3).map((item) => (
            <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'regression' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairOutcomeReconciliationStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{(item.changed_files && item.changed_files.length > 0 ? item.changed_files : [item.changed_file]).filter(Boolean).join('、') || '-'}</div>
              {(item.file_set || (item.file_count || 0) > 1) && (
                <div style={{ color: '#78d98b', fontSize: 13, lineHeight: 1.35 }}>文件集对账：共 {item.file_count || item.changed_files?.length || 0} 个文件。</div>
              )}
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
                doctor：{item.before.doctor_findings} → {item.after.doctor_findings}；repair required：{item.before.repair_required} → {item.after.repair_required}
              </div>
              {item.resolved_findings.length > 0 && (
                <div style={{ color: '#78d98b', fontSize: 13, lineHeight: 1.35 }}>已消除：{item.resolved_findings.map((finding) => finding.check_id).join('、')}</div>
              )}
              {item.introduced_findings.length > 0 && (
                <div style={{ color: '#ff7b7b', fontSize: 13, lineHeight: 1.35 }}>新增：{item.introduced_findings.map((finding) => finding.check_id).join('、')}</div>
              )}
              {item.persistent_findings.length > 0 && (
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>残留：{item.persistent_findings.map((finding) => finding.check_id).join('、')}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有已应用补丁需要对账。
        </div>
      )}
      {report.source.repair_outcome_reconciliation_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>对账 material：{report.source.repair_outcome_reconciliation_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-outcome-reconciliation/latest" label="对账报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRollbackReadinessPanel({ report, error, loading }: {
  report: TeamBuilderRepairRollbackReadinessReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>回滚就绪读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查回滚就绪...' : '暂无回滚就绪检查。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无回滚就绪检查。'}</div>
      </div>
    )
  }
  const tone = repairRollbackReadinessTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-rollback-readiness>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>回滚就绪检查</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairRollbackReadinessVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>可回滚 {report.counts.rollback_ready}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>已变化 {report.counts.stale_or_mismatch}</span>
        <span style={S.pill}>缺快照 {report.counts.missing_before_snapshot}</span>
        <span style={S.pill}>真实写入 {report.counts.real_writes}</span>
        {report.counts.file_set_ready !== undefined && <span style={S.pill}>文件集可回滚 {report.counts.file_set_ready}</span>}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.rollback_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.rollback_items.slice(0, 3).map((item) => (
            <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'stale_or_mismatch' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairRollbackReadinessStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{(item.changed_files && item.changed_files.length > 0 ? item.changed_files : [item.changed_file]).filter(Boolean).join('、') || '-'}</div>
              {(item.file_set || (item.file_count || 0) > 1) && (
                <div style={{ color: '#78d98b', fontSize: 13, lineHeight: 1.35 }}>
                  文件集回滚前置检查：共 {item.file_count || item.changed_files?.length || 0} 个文件；真实回滚必须额外携带 confirm_file_set_rollback。
                </div>
              )}
              <TeamBuilderRepairFileRecordList records={item.file_records} mode="rollback" />
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>before 快照：{item.before_preview_file || '-'}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
                当前匹配：{item.current_matches_after ? '是' : '否'}；before 可校验：{item.before_snapshot_valid ? '是' : '否'}；范围安全：{item.target_scope_safe ? '是' : '否'}
              </div>
              {item.blocked_reasons.length > 0 && (
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>阻断原因：{item.blocked_reasons.join('；')}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有已应用补丁；无需准备回滚。
        </div>
      )}
      {report.source.repair_rollback_readiness_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>回滚就绪 material：{report.source.repair_rollback_readiness_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-rollback-readiness/latest" label="回滚报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRollbackExecutionPanel({ report, error, loading }: {
  report: TeamBuilderRepairRollbackExecutionReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>回滚执行记录读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查回滚执行记录...' : '暂无回滚执行记录。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无回滚执行记录。'}</div>
      </div>
    )
  }
  const tone = repairRollbackExecutionTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-rollback-execution>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>回滚执行记录</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairRollbackExecutionVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>条目 {report.counts.items}</span>
        <span style={S.pill}>待回滚 {report.counts.ready}</span>
        <span style={S.pill}>已回滚 {report.counts.rolled_back}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>不匹配 {report.counts.stale_or_mismatch}</span>
        <span style={S.pill}>真实写入 {report.counts.real_writes}</span>
        {report.counts.file_set_ready !== undefined && <span style={S.pill}>文件集待回滚 {report.counts.file_set_ready}</span>}
        {report.counts.file_set_rolled_back !== undefined && <span style={S.pill}>文件集已回滚 {report.counts.file_set_rolled_back}</span>}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 3).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.rollback_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.rollback_items.slice(0, 3).map((item) => (
            <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'stale_or_mismatch' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairRollbackExecutionStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{(item.changed_files && item.changed_files.length > 0 ? item.changed_files : [item.changed_file]).filter(Boolean).join('、') || '-'}</div>
              {(item.file_set || (item.file_count || 0) > 1) && (
                <div style={{ color: '#78d98b', fontSize: 13, lineHeight: 1.35 }}>
                  文件集回滚：共 {item.file_count || item.changed_files?.length || 0} 个文件；执行记录按文件保存 before/after 和回滚 sha。
                </div>
              )}
              <TeamBuilderRepairFileRecordList records={item.file_records} mode="rollback" />
              {item.rolled_back_by && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>执行人：{item.rolled_back_by} {item.rolled_back_at ? ` / ${item.rolled_back_at}` : ''}</div>
              )}
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>回滚：{item.rollback_from_sha256 || '-'} → {item.rollback_to_sha256 || '-'}</div>
              {item.blocked_reasons.length > 0 && (
                <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>阻断原因：{item.blocked_reasons.join('；')}</div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有可回滚条目；GET 只展示记录，真实回滚必须走显式 POST execute 和确认 token。
        </div>
      )}
      {report.source.repair_rollback_execution_report_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>回滚执行 material：{report.source.repair_rollback_execution_report_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-rollback-execution/latest" label="回滚记录" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRollbackPostVerificationPanel({ report, error, loading }: {
  report: TeamBuilderRepairRollbackPostVerificationReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>回滚后验证读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查回滚后验证...' : '暂无回滚后验证。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无回滚后验证。'}</div>
      </div>
    )
  }
  const tone = repairRollbackPostVerificationTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-rollback-post-verification>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>回滚后验证</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairRollbackPostVerificationVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已回滚 {report.counts.rolled_back}</span>
        <span style={S.pill}>已验证 {report.counts.verified}</span>
        <span style={S.pill}>等待 {report.counts.pending}</span>
        <span style={S.pill}>失败 {report.counts.failed}</span>
        <span style={S.pill}>doctor {report.counts.doctor_findings ?? 0}</span>
        <span style={S.pill}>需修复 {report.counts.repair_required ?? 0}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.verification_items.length > 0 ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {report.verification_items.slice(0, 3).map((item) => (
            <div key={item.id} style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.candidate_id}</span>
                <span style={{ color: item.status === 'fail' ? '#ff7b7b' : '#ffb74d', fontSize: 13 }}>{zhRepairRollbackPostVerificationStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{item.changed_files.join('、') || '-'}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>回查：{item.required_commands.join('；') || '-'}</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
          当前没有已回滚补丁需要验证；GET 只读，回滚后重跑必须走显式 POST execute。
        </div>
      )}
      {report.source.repair_rollback_post_verification_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>回滚后验证 material：{report.source.repair_rollback_post_verification_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-rollback-post-verification/latest" label="验证报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairClosureRollupPanel({ report, error, loading }: {
  report: TeamBuilderRepairClosureRollupReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复闭环总览读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在整理修复闭环总览...' : '暂无修复闭环总览。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复闭环总览。'}</div>
      </div>
    )
  }
  const tone = repairClosureRollupTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-closure-rollup>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复闭环总览</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairClosureRollupVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>阶段 {report.counts.stages}</span>
        <span style={S.pill}>待处理 {report.counts.pending_stages}</span>
        <span style={S.pill}>阻断 {report.counts.failed_stages}</span>
        <span style={S.pill}>候选 {report.counts.candidates}</span>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>已回滚 {report.counts.rolled_back}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'grid', gap: 5 }}>
        {report.stages.slice(0, 6).map((item) => {
          const stageTone = item.status === 'blocked' || item.status === 'regression_or_persistent' || item.status === 'stale_or_failed'
            ? '#ff7b7b'
            : item.status === 'clean' || item.status === 'no_candidate' || item.status === 'no_patch' || item.status === 'not_needed' || item.status === 'verified'
              ? '#78d98b'
              : '#ffb74d'
          return (
            <div key={item.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.name}</span>
                <span style={{ color: stageTone, fontSize: 13 }}>{zhRepairClosureStageStatus(item.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
            </div>
          )
        })}
      </div>
      {report.generalization && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }} data-team-builder-repair-generalization>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>泛化面</div>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            <span style={S.pill}>候选 {report.generalization.candidate_count}</span>
            <span style={S.pill}>多文件候选 {report.generalization.multi_file_candidate_count}</span>
            <span style={S.pill}>写入模型 {report.generalization.single_file_execution_limit ? '单文件' : '文件集'}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.generalization.summary}</div>
          {report.generalization.blockers.length > 0 && (
            <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.4 }}>
              {report.generalization.blockers.slice(0, 3).join('；')}
            </div>
          )}
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4 }}>{report.generalization.next_validation}</div>
        </div>
      )}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>入口：{report.next_actions[0].endpoint}</div>
        </div>
      )}
      {report.source.repair_closure_rollup_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>修复闭环总览 material：{report.source.repair_closure_rollup_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-closure-rollup/latest" label="总览报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairGeneralizationTrialPanel({ report, error, loading }: {
  report: TeamBuilderRepairGeneralizationTrialReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复泛化试验读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在整理修复泛化试验...' : '暂无修复泛化试验。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无修复泛化试验。'}</div>
      </div>
    )
  }
  const tone = repairGeneralizationTrialTone(report.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-generalization-trial>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复泛化试验</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRepairGeneralizationTrialVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>候选 {report.counts.candidate_count}</span>
        <span style={S.pill}>多文件 {report.counts.multi_file_candidate_count}</span>
        <span style={S.pill}>contract 目标 {report.counts.contract_target_count}</span>
        <span style={S.pill}>真实写入 {report.counts.real_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'grid', gap: 5 }}>
        {report.controlled_candidates.slice(0, 3).map((candidate) => (
          <div key={candidate.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{candidate.priority}. {candidate.title}</span>
              <span style={{ color: '#ffb74d', fontSize: 13 }}>{candidate.risk}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{candidate.summary}</div>
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{candidate.changed_files.join('；')}</div>
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{candidate.expected_handling}</div>
          </div>
        ))}
      </div>
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
          {report.next_actions[0].required_confirmations && report.next_actions[0].required_confirmations.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认 token：{report.next_actions[0].required_confirmations.join('、')}</div>
          )}
          {report.next_actions[0].approval_requirements && report.next_actions[0].approval_requirements.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>审批字段：{report.next_actions[0].approval_requirements.join('、')}</div>
          )}
          {report.next_actions[0].safety_note && (
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{report.next_actions[0].safety_note}</div>
          )}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <SourceDataLink href={report.next_actions[0].endpoint} label="执行状态" />
            {report.next_actions[0].post_endpoint && <SourceDataLink href={report.next_actions[0].post_endpoint} label="显式 POST 入口" />}
          </div>
        </div>
      )}
      {report.source.repair_generalization_trial_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>泛化试验 material：{report.source.repair_generalization_trial_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-generalization-trial/latest" label="试验报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealGeneratedFileSetTrialPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealGeneratedFileSetTrialReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 generated worker 文件集试验读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在执行真实 generated worker 文件集试验...' : '暂无真实 generated worker 文件集试验。'}</div>
      </div>
    )
  }
  if (!report.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{report.reason || '暂无真实 generated worker 文件集试验。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'pass' ? '#78d98b' : report.verdict === 'fail' ? '#ff7b7b' : '#ffb74d'
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-generated-file-set-trial>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实 generated worker 文件集试验</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{report.verdict === 'pass' ? '通过' : report.verdict === 'fail' ? '失败' : report.verdict}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>目标文件 {report.counts.changed_files}</span>
        <span style={S.pill}>预览 {report.counts.files_previewed}</span>
        <span style={S.pill}>已应用 {report.counts.files_applied}</span>
        <span style={S.pill}>已回滚 {report.counts.files_rolled_back}</span>
        <span style={S.pill}>应用后通过 {report.counts.post_apply_passed}</span>
        <span style={S.pill}>回滚恢复 {report.counts.rollback_restored}</span>
        <span style={S.pill}>真实仓库写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 6).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
        smoke：修复前 {report.smoke.before_worker?.status || '-'}；应用后 {report.smoke.after_apply_worker?.status || '-'}；回滚后 {report.smoke.after_rollback_worker?.status || '-'}
      </div>
      <TeamBuilderRepairFileRecordList records={report.file_records} mode="rollback" />
      {report.source.trial_package_dir && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>试验包：{report.source.trial_package_dir}</div>
      )}
      {report.source.repair_real_generated_file_set_trial_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>文件集试验 material：{report.source.repair_real_generated_file_set_trial_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-generated-file-set-trial/latest" label="试验报告" />
        </div>
      )}
    </div>
  )
}

function zhRealRunCandidateScanVerdict(verdict: string): string {
  if (verdict === 'candidate_ready') return '候选就绪'
  if (verdict === 'failure_candidate_needs_doctor') return '失败待消解'
  if (verdict === 'validation_gap_only') return '仅验证缺口'
  if (verdict === 'no_real_failure_candidate') return '暂无真实失败'
  if (verdict === 'unavailable') return '不可用'
  return verdict
}

function zhRealRunCandidateClass(value: string): string {
  if (value === 'repair_ready') return '可进入修复候选'
  if (value === 'failure_without_repair_plan') return '真实失败待 doctor 消解'
  if (value === 'validation_gap_only') return '验证缺口'
  if (value === 'clean') return '已闭合'
  return value
}

function TeamBuilderRepairRealRunCandidateScanPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunCandidateScanReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 候选扫描读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在扫描真实 TeamBuilder run...' : '暂无真实 run 候选扫描。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'candidate_ready'
    ? '#78d98b'
    : report.verdict === 'failure_candidate_needs_doctor'
      ? '#ffb74d'
      : report.verdict === 'validation_gap_only'
        ? '#64b5f6'
        : colors.border
  const rows = report.candidates.length > 0 ? report.candidates : report.run_summaries.slice(0, 3)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-candidate-scan>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实 run 修复候选扫描</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhRealRunCandidateScanVerdict(report.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已扫 run {report.counts.runs_scanned}</span>
        <span style={S.pill}>真实失败 {report.counts.failure_candidates}</span>
        <span style={S.pill}>修复候选 {report.counts.repair_ready_candidates}</span>
        <span style={S.pill}>验证缺口 {report.counts.validation_gap_runs}</span>
        <span style={S.pill}>源码可见 {report.counts.source_ready_candidates}</span>
        <span style={S.pill}>真实仓库写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 5).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'grid', gap: 5 }}>
        {rows.map((candidate) => (
          <div key={candidate.run_id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{candidate.run_id}</span>
              <span style={{ color: candidate.candidate_ready ? '#78d98b' : '#ffb74d', fontSize: 13 }}>{zhRealRunCandidateClass(candidate.classification)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{candidate.summary}</div>
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
              <span style={S.pill}>critical {candidate.counts.critical}</span>
              <span style={S.pill}>失败 worker {candidate.counts.failed_workers}</span>
              <span style={S.pill}>doctor {candidate.counts.doctor_findings}</span>
              <span style={S.pill}>repair_required {candidate.counts.repair_required}</span>
              <span style={S.pill}>源码 {candidate.counts.source_files}</span>
            </div>
            {candidate.source_files.length > 0 && (
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                源码入口：{candidate.source_files.slice(0, 4).join('；')}
              </div>
            )}
            {candidate.evidence.length > 0 && (
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                判断依据：{candidate.evidence.slice(0, 4).join('；')}
              </div>
            )}
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
              资料：{candidate.materials.filter((item) => item.available).map((item) => `${item.label} ${item.path}`).join('；') || '暂无可读报告'}
            </div>
          </div>
        ))}
      </div>
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
          {report.next_actions[0].required_confirmations && report.next_actions[0].required_confirmations.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认 token：{report.next_actions[0].required_confirmations.join('、')}</div>
          )}
          {report.next_actions[0].approval_requirements && report.next_actions[0].approval_requirements.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>审批字段：{report.next_actions[0].approval_requirements.join('、')}</div>
          )}
          {report.next_actions[0].safety_note && (
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{report.next_actions[0].safety_note}</div>
          )}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <SourceDataLink href={report.next_actions[0].endpoint} label="执行状态" />
            {report.next_actions[0].post_endpoint && <SourceDataLink href={report.next_actions[0].post_endpoint} label="显式 POST 入口" />}
          </div>
        </div>
      )}
      {report.source.scan_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>候选扫描 material：{report.source.scan_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-candidate-scan/latest" label="扫描报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunReplayPlanPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunReplayPlanReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 消解计划读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在消解真实失败 run...' : '暂无真实 run 消解计划。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'repair_plan_ready' ? '#78d98b' : report.verdict === 'no_repair_action' ? '#ffb74d' : colors.border
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-replay-plan>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 消解计划</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{report.verdict === 'repair_plan_ready' ? '修复计划就绪' : report.verdict}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>审查问题 {report.counts.code_review_issues}</span>
        <span style={S.pill}>repair_required {report.counts.repair_required}</span>
        <span style={S.pill}>源码定位 {report.counts.source_located}</span>
        <span style={S.pill}>diff 已生成 {report.counts.diffs_generated}</span>
        <span style={S.pill}>真实仓库写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.findings.slice(0, 2).map((finding) => (
        <div key={finding.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px dashed ${colors.border}`, borderRadius: 5, background: '#0b1015' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{finding.target_id || finding.location}</span>
            <span style={{ color: '#ffb74d', fontSize: 13 }}>{finding.category}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{finding.implication}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>缺失字段：{finding.required_not_read.join('、') || '-'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标源码：{finding.source_file || '-'}</div>
        </div>
      ))}
      {report.repair_actions.slice(0, 2).map((action) => (
        <div key={action.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>修复动作：{action.worker_id}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{action.proposed_change}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>文件：{action.changed_files.join('；') || '-'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>安全级别：{action.automation_level} / 自动安全 {action.auto_safe ? '是' : '否'}</div>
        </div>
      ))}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.replay_plan_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>消解计划 material：{report.source.replay_plan_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-replay-plan/latest" label="消解报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunDiffPreviewPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunDiffPreviewReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run diff 预览读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在生成真实失败候选 diff 预览...' : '暂无真实 run diff 预览。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'diff_preview_ready' ? '#78d98b' : report.verdict === 'blocked' ? '#ff7b7b' : colors.border
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-diff-preview>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run diff 预览</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{report.verdict === 'diff_preview_ready' ? 'diff 可审阅' : report.verdict}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>修复动作 {report.counts.repair_actions}</span>
        <span style={S.pill}>diff 就绪 {report.counts.diff_ready}</span>
        <span style={S.pill}>预览文件 {report.counts.files_previewed}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>真实仓库写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.diff_records.slice(0, 2).map((record) => (
        <div key={record.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{record.worker_id}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{record.change_summary.join('；')}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>文件：{record.changed_file}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>预览：{record.before_preview_file} 到 {record.after_preview_file}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>diff sha：{record.diff_sha256}</div>
        </div>
      ))}
      {report.blocked_items.length > 0 && (
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
          阻断：{report.blocked_items.slice(0, 3).map((item) => item.reason).join('；')}
        </div>
      )}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.diff_preview_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>diff 预览 material：{report.source.diff_preview_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-diff-preview/latest" label="diff 预览报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunDiffReviewPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunDiffReviewReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run diff 审阅门读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败候选 diff 审阅门...' : '暂无真实 run diff 审阅门。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'review_ready' ? '#78d98b' : report.verdict === 'blocked' ? '#ff7b7b' : colors.border
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-diff-review>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run diff 审阅门</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{report.verdict === 'review_ready' ? '可进入显式审阅' : report.verdict}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>diff 记录 {report.counts.diff_records}</span>
        <span style={S.pill}>可审阅 {report.counts.ready_for_review}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>需显式批准 {report.counts.requires_explicit_approval}</span>
        <span style={S.pill}>真实仓库写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.review_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id}</span>
            <span style={{ color: item.status === 'ready_for_explicit_review' ? '#78d98b' : '#ffb74d', fontSize: 13 }}>{item.status === 'ready_for_explicit_review' ? '审阅条件已满足' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>文件：{item.changed_file}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>字段：{item.required_input_fields.join('、') || '无'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>范围安全：{item.target_scope_safe ? '是' : '否'}；源码等于 before：{item.source_matches_before ? '是' : '否'}</div>
          {item.risk_notes.slice(0, 2).map((note, index) => (
            <div key={`${item.id}-risk-${index}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{note}</div>
          ))}
          {item.review_questions.length > 0 && (
            <div style={{ color: '#ffcc80', fontSize: 13, lineHeight: 1.35 }}>审阅问题：{item.review_questions.slice(0, 2).join('；')}</div>
          )}
          {item.blocked_reasons.length > 0 && (
            <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>阻断：{item.blocked_reasons.join('；')}</div>
          )}
        </div>
      ))}
      {report.blocked_items.length > 0 && (
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
          审阅阻断：{report.blocked_items.slice(0, 3).map((item) => (item.reasons || [item.reason || '未知阻断']).join('、')).join('；')}
        </div>
      )}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.diff_review_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>diff 审阅 material：{report.source.diff_review_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-diff-review/latest" label="diff 审阅报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunApplyGatePanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunApplyGateReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 显式应用门读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败 run 显式应用门...' : '暂无真实 run 显式应用门。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'ready_for_explicit_apply_preview' ? '#78d98b' : report.verdict === 'blocked' ? '#ff7b7b' : colors.border
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-apply-gate>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 显式应用门</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{report.verdict === 'ready_for_explicit_apply_preview' ? '可生成应用预览' : report.verdict}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>审阅项 {report.counts.review_items}</span>
        <span style={S.pill}>应用预览就绪 {report.counts.apply_preview_ready}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>确认 token {report.counts.required_confirmation_tokens}</span>
        <span style={S.pill}>真实仓库写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.apply_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id}</span>
            <span style={{ color: item.status === 'ready_for_explicit_apply_preview' ? '#78d98b' : '#ffb74d', fontSize: 13 }}>{item.status === 'ready_for_explicit_apply_preview' ? '应用预览前置通过' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>文件：{item.changed_file}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认：{item.required_confirmations.join('、')}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>回滚要求：{item.rollback_requirement}</div>
          {item.post_apply_verification.slice(0, 2).map((step, index) => (
            <div key={`${item.id}-verify-${index}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{step}</div>
          ))}
          {item.blocked_reasons.length > 0 && (
            <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>阻断：{item.blocked_reasons.join('；')}</div>
          )}
        </div>
      ))}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.apply_gate_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>应用门 material：{report.source.apply_gate_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-apply-gate/latest" label="应用门报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunApplyPreviewPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunApplyPreviewReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 文件集应用预览读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在生成真实失败 run 文件集应用预览...' : '暂无真实 run 文件集应用预览。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'preview_ready' ? '#78d98b' : report.verdict === 'blocked' ? '#ff7b7b' : colors.border
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-apply-preview>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 文件集应用预览</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{report.verdict === 'preview_ready' ? '预览已生成' : report.verdict}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>应用项 {report.counts.apply_items}</span>
        <span style={S.pill}>预览 {report.counts.preview_ready}</span>
        <span style={S.pill}>文件 {report.counts.files_previewed}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>确认 token {report.counts.required_confirmation_tokens}</span>
        <span style={S.pill}>真实仓库写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.preview_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id}</span>
            <span style={{ color: item.status === 'preview_ready' ? '#78d98b' : '#ffb74d', fontSize: 13 }}>{item.status === 'preview_ready' ? '文件集预览就绪' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{item.changed_files.join('、') || '-'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>预览后：{item.after_preview_files.join('、') || '-'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认：{item.required_confirmations.join('、')}</div>
          {item.file_records.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
              文件记录：{item.file_records.map((record) => `${record.changed_file} ${repairShortSha(record.after_sha256)}`).join('；')}
            </div>
          )}
          {item.post_apply_verification.slice(0, 2).map((step, index) => (
            <div key={`${item.id}-verify-${index}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{step}</div>
          ))}
          <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>{item.safety.reason}</div>
          {item.blocked_reasons.length > 0 && (
            <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>阻断：{item.blocked_reasons.join('；')}</div>
          )}
        </div>
      ))}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.apply_preview_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>真实应用预览 material：{report.source.apply_preview_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-apply-preview/latest" label="真实应用预览报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunApplyExecutionPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunApplyExecutionReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 显式应用执行读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败 run 显式应用执行...' : '暂无真实 run 显式应用执行。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'applied' ? '#78d98b' : report.verdict === 'ready_for_explicit_apply' ? '#ffcc80' : report.verdict === 'stale_or_mismatch' || report.verdict === 'blocked' ? '#ff7b7b' : colors.border
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-apply-execution>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 显式应用执行</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{report.verdict === 'ready_for_explicit_apply' ? '等待显式执行' : report.verdict === 'applied' ? '已显式应用' : report.verdict}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>应用项 {report.counts.items}</span>
        <span style={S.pill}>待执行 {report.counts.ready}</span>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>不匹配 {report.counts.stale_or_mismatch}</span>
        <span style={S.pill}>真实写入 {report.counts.real_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 3).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.apply_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id}</span>
            <span style={{ color: item.status === 'applied' ? '#78d98b' : item.status === 'ready_for_explicit_apply' ? '#ffcc80' : '#ffb74d', fontSize: 13 }}>{item.status === 'ready_for_explicit_apply' ? '等待 POST execute' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{item.changed_files.join('、') || '-'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>确认：{item.required_confirmations.join('、') || '-'}</div>
          {item.file_records.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
              当前文件：{item.file_records.map((record) => `${record.changed_file} ${repairShortSha(record.current_sha256 || record.after_sha256)}`).join('；')}
            </div>
          )}
        </div>
      ))}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.apply_execution_report_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>应用执行 material：{report.source.apply_execution_report_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-apply-execution/latest" label="应用执行报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunPostApplyVerificationPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunPostApplyVerificationReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 应用后回放验证读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败 run 应用后回放验证...' : '暂无真实 run 应用后回放验证。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'pass' ? '#78d98b' : report.verdict === 'warning' || report.verdict === 'awaiting_apply' || report.verdict === 'awaiting_replay_verification' ? '#ffcc80' : report.verdict === 'fail' ? '#ff7b7b' : colors.border
  const label = report.verdict === 'awaiting_apply' ? '等待应用' : report.verdict === 'awaiting_replay_verification' ? '等待回放验证' : report.verdict === 'pass' ? '已通过' : report.verdict === 'warning' ? '有警告' : report.verdict
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-post-apply-verification>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 应用后回放验证</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{label}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>已验证 {report.counts.verified}</span>
        <span style={S.pill}>待验证 {report.counts.pending}</span>
        <span style={S.pill}>警告 {report.counts.warnings}</span>
        <span style={S.pill}>真实写入 {report.counts.real_repo_writes}</span>
        {typeof report.counts.fields_checked === 'number' && <span style={S.pill}>字段 {report.counts.fields_checked}</span>}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.verification_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id || item.apply_item_id}</span>
            <span style={{ color: item.status === 'verified' ? '#78d98b' : item.status === 'failed' ? '#ff7b7b' : '#ffcc80', fontSize: 13 }}>{item.status === 'pending_verification' ? '等待验证' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{item.changed_files.join('、') || '-'}</div>
          {item.missing_required_fields && item.missing_required_fields.length > 0 && (
            <div style={{ color: '#ff7b7b', fontSize: 13, lineHeight: 1.35 }}>仍缺字段：{item.missing_required_fields.join('、')}</div>
          )}
        </div>
      ))}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.post_apply_verification_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>应用后验证 material：{report.source.post_apply_verification_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-post-apply-verification/latest" label="应用后验证报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunOutcomeReconciliationPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunOutcomeReconciliationReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 修复结果对账读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败 run 修复结果对账...' : '暂无真实 run 修复结果对账。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'pass' ? '#78d98b' : report.verdict === 'warning' || report.verdict === 'awaiting_apply' || report.verdict === 'awaiting_verification' || report.verdict === 'partial' || report.verdict === 'missing_baseline' ? '#ffcc80' : report.verdict === 'regression' ? '#ff7b7b' : colors.border
  const label = report.verdict === 'awaiting_apply' ? '等待应用' : report.verdict === 'awaiting_verification' ? '等待验证' : report.verdict === 'pass' ? '对账通过' : report.verdict === 'warning' ? '有警告' : report.verdict === 'partial' ? '部分消解' : report.verdict === 'regression' ? '新增失败' : report.verdict
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-outcome-reconciliation>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 修复结果对账</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{label}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>已对账 {report.counts.reconciled}</span>
        <span style={S.pill}>已消解 {report.counts.resolved_findings}</span>
        <span style={S.pill}>新增 {report.counts.introduced_findings}</span>
        <span style={S.pill}>残留 {report.counts.persistent_findings}</span>
        <span style={S.pill}>真实写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 5).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.reconciliation_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id || item.apply_item_id}</span>
            <span style={{ color: item.status === 'regression' ? '#ff7b7b' : item.status === 'reconciled' ? '#78d98b' : '#ffcc80', fontSize: 13 }}>{item.status === 'reconciled_with_warnings' ? '已消解有警告' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{item.changed_files.join('、') || '-'}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
            finding：{item.before.baseline_findings} → 残留 {item.after.missing_required_fields}，验证 {item.after.verification_verdict || '-'}
          </div>
        </div>
      ))}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.outcome_reconciliation_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>结果对账 material：{report.source.outcome_reconciliation_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest" label="结果对账报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunRollbackReadinessPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunRollbackReadinessReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 回滚就绪读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败 run 回滚就绪...' : '暂无真实失败 run 回滚就绪报告。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'clean' || report.verdict === 'ready_for_explicit_rollback' ? '#78d98b' : report.verdict === 'awaiting_apply' || report.verdict === 'missing_before_snapshot' ? '#ffcc80' : report.verdict === 'blocked' || report.verdict === 'stale_or_mismatch' ? '#ff7b7b' : colors.border
  const label = report.verdict === 'awaiting_apply' ? '等待应用' : report.verdict === 'ready_for_explicit_rollback' ? '可显式回滚' : report.verdict === 'clean' ? '无需回滚' : report.verdict === 'stale_or_mismatch' ? '记录不匹配' : report.verdict === 'missing_before_snapshot' ? '缺少快照' : report.verdict === 'blocked' ? '阻断' : report.verdict
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-rollback-readiness>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 回滚就绪</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{label}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>可回滚 {report.counts.rollback_ready}</span>
        <span style={S.pill}>不匹配 {report.counts.stale_or_mismatch}</span>
        <span style={S.pill}>缺快照 {report.counts.missing_before_snapshot}</span>
        <span style={S.pill}>真实写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 5).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.rollback_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id || item.apply_item_id}</span>
            <span style={{ color: item.status === 'ready_for_explicit_rollback' ? '#78d98b' : '#ffcc80', fontSize: 13 }}>{item.status === 'ready_for_explicit_rollback' ? '可显式回滚' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{item.changed_files.join('、') || '-'}</div>
          {item.blocked_reasons.length > 0 && (
            <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.35 }}>{item.blocked_reasons[0]}</div>
          )}
        </div>
      ))}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
        </div>
      )}
      {report.source.real_run_rollback_readiness_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>回滚就绪 material：{report.source.real_run_rollback_readiness_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-rollback-readiness/latest" label="回滚就绪报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunRollbackExecutionPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunRollbackExecutionReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 回滚执行读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败 run 回滚执行...' : '暂无真实失败 run 回滚执行报告。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'clean' || report.verdict === 'rolled_back' ? '#78d98b' : report.verdict === 'awaiting_apply' || report.verdict === 'ready_for_explicit_rollback' ? '#ffcc80' : report.verdict === 'blocked' || report.verdict === 'stale_or_mismatch' ? '#ff7b7b' : colors.border
  const label = report.verdict === 'awaiting_apply' ? '等待应用' : report.verdict === 'ready_for_explicit_rollback' ? '等待显式回滚' : report.verdict === 'rolled_back' ? '已回滚' : report.verdict === 'clean' ? '未开启' : report.verdict === 'stale_or_mismatch' ? '记录不匹配' : report.verdict === 'blocked' ? '阻断' : report.verdict
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-rollback-execution>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 显式回滚执行</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{label}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>待回滚 {report.counts.ready}</span>
        <span style={S.pill}>已回滚 {report.counts.rolled_back}</span>
        <span style={S.pill}>阻断 {report.counts.blocked}</span>
        <span style={S.pill}>真实写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 3).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.rollback_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id || item.apply_item_id}</span>
            <span style={{ color: item.status === 'rolled_back' ? '#78d98b' : '#ffcc80', fontSize: 13 }}>{item.status === 'ready_for_explicit_rollback' ? '等待显式回滚' : item.status === 'rolled_back' ? '已回滚' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{item.changed_files.join('、') || '-'}</div>
        </div>
      ))}
      {report.source.rollback_execution_report_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>回滚执行 material：{report.source.rollback_execution_report_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-rollback-execution/latest" label="回滚执行报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunRollbackPostVerificationPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunRollbackPostVerificationReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 回滚后验证读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在检查真实失败 run 回滚后验证...' : '暂无真实失败 run 回滚后验证报告。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'clean' || report.verdict === 'pass' ? '#78d98b' : report.verdict === 'awaiting_apply' || report.verdict === 'awaiting_verification' ? '#ffcc80' : report.verdict === 'fail' ? '#ff7b7b' : colors.border
  const label = report.verdict === 'awaiting_apply' ? '等待应用' : report.verdict === 'awaiting_verification' ? '等待验证' : report.verdict === 'pass' ? '验证通过' : report.verdict === 'clean' ? '无需验证' : report.verdict === 'fail' ? '验证失败' : report.verdict
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-rollback-post-verification>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 回滚后验证</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{label}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已回滚 {report.counts.rolled_back}</span>
        <span style={S.pill}>已验证 {report.counts.verified}</span>
        <span style={S.pill}>待验证 {report.counts.pending}</span>
        <span style={S.pill}>失败 {report.counts.failed}</span>
        <span style={S.pill}>真实写入 {report.counts.real_repo_writes}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 3).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      {report.verification_items.slice(0, 2).map((item) => (
        <div key={item.id} style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id || item.apply_item_id}</span>
            <span style={{ color: item.status === 'pass' ? '#78d98b' : item.status === 'fail' ? '#ff7b7b' : '#ffcc80', fontSize: 13 }}>{item.status === 'pending_verification' ? '等待验证' : item.status === 'pass' ? '通过' : item.status}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标：{item.changed_files.join('、') || '-'}</div>
        </div>
      ))}
      {report.source.rollback_post_verification_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>回滚后验证 material：{report.source.rollback_post_verification_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-rollback-post-verification/latest" label="回滚后验证报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairRealRunClosureRollupPanel({ report, error, loading }: {
  report: TeamBuilderRepairRealRunClosureRollupReport | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>真实 run 闭环总览读取失败：{error}</div>
      </div>
    )
  }
  if (!report) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在汇总真实失败 run 修复闭环...' : '暂无真实失败 run 闭环总览。'}</div>
      </div>
    )
  }
  const tone = report.verdict === 'clean' ? '#78d98b' : report.verdict === 'action_required' ? '#ffcc80' : report.verdict === 'blocked' ? '#ff7b7b' : colors.border
  const label = report.verdict === 'clean' ? '当前闭合' : report.verdict === 'action_required' ? '需要动作' : report.verdict === 'blocked' ? '阻断' : report.verdict
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-real-run-closure-rollup>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实失败 run 修复闭环总览</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{label}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{report.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>待处理阶段 {report.counts.pending_stages}</span>
        <span style={S.pill}>失败阶段 {report.counts.failed_stages}</span>
        <span style={S.pill}>待应用 {report.counts.ready_to_apply}</span>
        <span style={S.pill}>已应用 {report.counts.applied}</span>
        <span style={S.pill}>已对账 {report.counts.reconciled}</span>
        <span style={S.pill}>已回滚 {report.counts.rolled_back}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.quality_gates.slice(0, 4).map((gate) => {
          const gateTone = attributionStatusTone(gate.status)
          return (
            <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
                <span style={{ color: gateTone, fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {report.stages.slice(0, 7).map((stage) => (
          <div key={stage.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{stage.name}</span>
              <span style={{ color: '#ffcc80', fontSize: 13 }}>{zhRepairClosureStageStatus(stage.status)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{stage.summary}</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <SourceDataLink href={stage.endpoint} label="阶段报告" />
            </div>
          </div>
        ))}
      </div>
      {report.approval_packet?.available && (
        <div style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{report.approval_packet.title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.approval_packet.summary}</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span style={S.pill}>审批项 {report.approval_packet.items.length}</span>
            <span style={S.pill}>确认 token {report.approval_packet.required_confirmations.length}</span>
            <span style={S.pill}>审批字段 {report.approval_packet.approval_requirements.length}</span>
          </div>
          {report.approval_packet.decision_dossier && (
            <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: 'var(--fp-bg)' }}>
              <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{report.approval_packet.decision_dossier.title}</div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.approval_packet.decision_dossier.decision_question}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{report.approval_packet.decision_dossier.why_now}</div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>预期效果：{report.approval_packet.decision_dossier.expected_effect}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>写入范围：{report.approval_packet.decision_dossier.write_scope}</div>
              <div style={{ color: '#ffcc80', fontSize: 13, lineHeight: 1.35 }}>{report.approval_packet.decision_dossier.do_not_use_as_completion}</div>
              {report.approval_packet.decision_dossier.human_review_focus.length > 0 && (
                <div style={{ display: 'grid', gap: 2 }}>
                  {report.approval_packet.decision_dossier.human_review_focus.slice(0, 3).map((item) => (
                    <div key={item} style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>审阅重点：{item}</div>
                  ))}
                </div>
              )}
              {report.approval_packet.decision_dossier.post_approval_sequence.length > 0 && (
                <div style={{ display: 'grid', gap: 2 }}>
                  {report.approval_packet.decision_dossier.post_approval_sequence.slice(0, 4).map((item, index) => (
                    <div key={item} style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>批准后 {index + 1}：{item}</div>
                  ))}
                </div>
              )}
            </div>
          )}
          {report.approval_packet.post_preflight?.available && (
            <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${report.approval_packet.post_preflight.status === 'ready_to_post' ? '#78d98b' : '#ffcc80'}`, borderRadius: 5, background: 'var(--fp-bg)' }} data-team-builder-repair-real-run-post-preflight>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>POST 前置检查</span>
                <span style={{ color: report.approval_packet.post_preflight.status === 'ready_to_post' ? '#78d98b' : '#ffcc80', fontSize: 13 }}>
                  {report.approval_packet.post_preflight.status === 'ready_to_post' ? '可提交审批' : '有阻断'}
                </span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.approval_packet.post_preflight.summary}</div>
              <div style={{ display: 'grid', gap: 3 }}>
                {report.approval_packet.post_preflight.conditions.slice(0, 10).map((condition) => (
                  <div key={condition.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
                    <span style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{condition.name}</span>
                    <span style={{ color: attributionStatusTone(condition.status), fontSize: 13, whiteSpace: 'nowrap' }}>{zhAttributionStatus(condition.status)}</span>
                  </div>
                ))}
              </div>
              {report.approval_packet.post_preflight.blockers.length > 0 && (
                <div style={{ color: '#ffcc80', fontSize: 13, lineHeight: 1.35 }}>
                  阻断：{report.approval_packet.post_preflight.blockers.slice(0, 2).join('；')}
                </div>
              )}
            </div>
          )}
          {report.approval_packet.auto_apply_policy?.available && (
            <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${report.approval_packet.auto_apply_policy.eligible ? '#78d98b' : '#ffcc80'}`, borderRadius: 5, background: 'var(--fp-bg)' }} data-team-builder-repair-real-run-auto-apply-policy>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>低风险自动 apply 策略</span>
                <span style={{ color: report.approval_packet.auto_apply_policy.eligible ? '#78d98b' : '#ffcc80', fontSize: 13 }}>
                  {report.approval_packet.auto_apply_policy.eligible ? '可自动 apply' : report.approval_packet.auto_apply_policy.verdict}
                </span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.approval_packet.auto_apply_policy.summary}</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <span style={S.pill}>候选 {report.approval_packet.auto_apply_policy.counts.candidate_items}</span>
                <span style={S.pill}>可自动 {report.approval_packet.auto_apply_policy.counts.eligible_items}</span>
                <span style={S.pill}>文件 {report.approval_packet.auto_apply_policy.counts.total_changed_files}/{report.approval_packet.auto_apply_policy.counts.max_changed_files}</span>
                <span style={S.pill}>必读字段 {report.approval_packet.auto_apply_policy.counts.required_field_checks}</span>
                <span style={S.pill}>字段缺失 {report.approval_packet.auto_apply_policy.counts.missing_required_fields}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>POST 入口：{report.approval_packet.auto_apply_policy.execute_endpoint}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认 token：{report.approval_packet.auto_apply_policy.required_confirmation}</div>
              {report.approval_packet.auto_apply_policy.blockers.length > 0 && (
                <div style={{ color: '#ffcc80', fontSize: 13, lineHeight: 1.35 }}>
                  阻断：{report.approval_packet.auto_apply_policy.blockers.slice(0, 2).join('；')}
                </div>
              )}
            </div>
          )}
          {report.approval_packet.apply_rehearsal?.available && (
            <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${report.approval_packet.apply_rehearsal.verdict === 'pass' ? '#78d98b' : '#ffcc80'}`, borderRadius: 5, background: 'var(--fp-bg)' }} data-team-builder-repair-real-run-apply-rehearsal>
              <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>应用前演练：{report.approval_packet.apply_rehearsal.verdict === 'pass' ? '通过' : report.approval_packet.apply_rehearsal.verdict}</div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.approval_packet.apply_rehearsal.summary}</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <span style={S.pill}>待应用 {report.approval_packet.apply_rehearsal.counts.ready}</span>
                <span style={S.pill}>演练通过 {report.approval_packet.apply_rehearsal.counts.passed}</span>
                <span style={S.pill}>阻断 {report.approval_packet.apply_rehearsal.counts.blocked}</span>
                <span style={S.pill}>scratch 写入 {report.approval_packet.apply_rehearsal.counts.scratch_writes}</span>
                <span style={S.pill}>真实写入 {report.approval_packet.apply_rehearsal.counts.real_repo_writes}</span>
                <span style={S.pill}>必读字段 {report.approval_packet.apply_rehearsal.counts.required_field_checks ?? 0}</span>
                <span style={S.pill}>字段缺失 {report.approval_packet.apply_rehearsal.counts.missing_required_fields ?? 0}</span>
              </div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>演练目录：{report.approval_packet.apply_rehearsal.rehearsal_root}</div>
              {report.approval_packet.apply_rehearsal.material && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span>演练 material：{report.approval_packet.apply_rehearsal.material}</span>
                  <SourceDataLink href="/api/team-builder-materialization/repair-real-run-apply-rehearsal/latest" label="演练报告" />
                </div>
              )}
            </div>
          )}
          {report.approval_packet.execution_playbook?.available && (
            <div style={{ display: 'grid', gap: 5, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: 'var(--fp-bg)' }} data-team-builder-repair-real-run-execution-playbook>
              <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{report.approval_packet.execution_playbook.title}</div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.approval_packet.execution_playbook.summary}</div>
              <div style={{ color: '#ffcc80', fontSize: 13, lineHeight: 1.35 }}>{report.approval_packet.execution_playbook.safety_note}</div>
              <div style={{ display: 'grid', gap: 4 }}>
                {report.approval_packet.execution_playbook.steps.slice(0, 5).map((step) => (
                  <div key={step.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{step.order}. {step.title}</span>
                      <span style={{ color: step.writes_target_files ? '#ffcc80' : '#78d98b', fontSize: 13 }}>{step.method}{step.writes_target_files ? ' 写目标文件' : ' 只读/验证'}</span>
                    </div>
                    <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{step.summary}</div>
                    <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>入口：{step.endpoint}</div>
                    {step.required_confirmations.length > 0 && (
                      <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认 token：{step.required_confirmations.join('、')}</div>
                    )}
                    <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>当前可执行：{step.can_execute_now ? '是' : '否'}；预期结果：{step.expected_next_verdict}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {report.approval_packet.items.slice(0, 2).map((item) => (
            <div key={item.apply_item_id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: 'var(--fp-bg)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.worker_id || item.apply_item_id}</span>
                <span style={{ color: '#ffcc80', fontSize: 13 }}>等待批准</span>
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
              {item.problem_statement && <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>问题：{item.problem_statement}</div>}
              {item.impact_summary && <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>影响：{item.impact_summary}</div>}
              {item.intended_change && <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>预期改动：{item.intended_change}</div>}
              {item.required_input_fields && item.required_input_fields.length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>必读字段：{item.required_input_fields.join('、')}</div>
              )}
              {item.change_summary && item.change_summary.length > 0 && (
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>diff 摘要：{item.change_summary.slice(0, 2).join('；')}</div>
              )}
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>apply_item：{item.apply_item_id}</div>
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>目标文件：{item.changed_files.slice(0, 3).join('、') || '-'}</div>
              {item.file_records.slice(0, 2).map((record) => (
                <div key={`${item.apply_item_id}:${record.changed_file}`} style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>
                  sha：当前 {record.current_sha256.slice(0, 10)} / before {record.before_sha256.slice(0, 10)} / after {record.after_sha256.slice(0, 10)}
                </div>
              ))}
              {item.review_questions && item.review_questions.length > 0 && (
                <div style={{ display: 'grid', gap: 2 }}>
                  {item.review_questions.slice(0, 2).map((question) => (
                    <div key={question} style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>审阅问题：{question}</div>
                  ))}
                </div>
              )}
              {item.risk_notes && item.risk_notes.length > 0 && (
                <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>风险说明：{item.risk_notes[0]}</div>
              )}
              {item.evidence_links && item.evidence_links.length > 0 && (
                <div style={{ display: 'grid', gap: 3 }}>
                  {item.evidence_links.slice(0, 5).map((link) => (
                    <div key={`${item.apply_item_id}:${link.label}:${link.target}`} style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
                      <span>依据：{link.label} - {link.summary}</span>
                      {link.kind === 'endpoint' && link.target ? <SourceDataLink href={link.target} label={link.label} /> : <span style={{ overflowWrap: 'anywhere' }}>{link.target}</span>}
                    </div>
                  ))}
                </div>
              )}
              {item.post_apply_verification.length > 0 && <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>应用后验证：{item.post_apply_verification[0]}</div>}
              {item.rollback_requirement && <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>回滚要求：{item.rollback_requirement}</div>}
            </div>
          ))}
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认 token：{report.approval_packet.required_confirmations.join('、')}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>审批字段：{report.approval_packet.approval_requirements.join('、')}</div>
          {report.approval_packet.payload_template && Object.keys(report.approval_packet.payload_template).length > 0 && (
            <pre style={{ margin: 0, padding: '7px 8px', border: `1px solid ${colors.border}`, borderRadius: 5, background: '#06090d', color: colors.textSecondary, fontSize: 13, lineHeight: 1.45, overflowX: 'auto', whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(report.approval_packet.payload_template, null, 2)}
            </pre>
          )}
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{report.approval_packet.safety_note}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{report.approval_packet.safety_checks[0]}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>显式 POST 入口：{report.approval_packet.post_endpoint}</div>
        </div>
      )}
      {report.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{report.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{report.next_actions[0].summary}</div>
          {report.next_actions[0].required_confirmations && report.next_actions[0].required_confirmations.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>确认 token：{report.next_actions[0].required_confirmations.join('、')}</div>
          )}
          {report.next_actions[0].approval_requirements && report.next_actions[0].approval_requirements.length > 0 && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>审批字段：{report.next_actions[0].approval_requirements.join('、')}</div>
          )}
          {report.next_actions[0].safety_note && (
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{report.next_actions[0].safety_note}</div>
          )}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <SourceDataLink href={report.next_actions[0].endpoint} label="执行状态" />
          </div>
          {report.next_actions[0].post_endpoint && (
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>显式 POST 入口：{report.next_actions[0].post_endpoint}</div>
          )}
        </div>
      )}
      {report.source.real_run_closure_rollup_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>真实 run 闭环总览 material：{report.source.real_run_closure_rollup_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/repair-real-run-closure-rollup/latest" label="真实 run 总览报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderRepairSafetyPolicyPanel({ policy, error, loading }: {
  policy: TeamBuilderRepairSafetyPolicy | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>修复安全策略读取失败：{error}</div>
      </div>
    )
  }
  if (!policy) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在整理修复安全策略...</div>
      </div>
    )
  }
  return (
    <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-repair-safety-policy>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>修复安全策略</div>
        <span style={S.pill}>{policy.version}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{policy.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>规则 {policy.counts.rules}</span>
        <span style={S.pill}>自动安全 {policy.counts.auto_safe_rules}</span>
        <span style={S.pill}>补丁计划 {policy.counts.patch_plan_only_rules}</span>
        <span style={S.pill}>人工/不动 {policy.counts.manual_or_none_rules}</span>
      </div>
      {policy.rules.slice(0, 4).map((rule) => (
        <div key={rule.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{rule.name}</span>
            <span style={{ color: rule.auto_safe ? '#78d98b' : colors.textMuted, fontSize: 13 }}>{rule.automation_level}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{rule.next_action}</div>
        </div>
      ))}
      {policy.source.repair_safety_policy_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          策略 material：{policy.source.repair_safety_policy_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderHighStandardAuditPanel({ audit, error, loading }: {
  audit: TeamBuilderHighStandardAudit | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-high-standard-audit>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>高标准目标审计读取失败：{error}</div>
      </div>
    )
  }
  if (!audit) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-high-standard-audit>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在审计 TeamBuilder 高标准闭环...' : '暂无高标准目标审计。'}</div>
      </div>
    )
  }
  const tone = audit.completion_ready ? '#78d98b' : '#ffcc80'
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-high-standard-audit>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>TeamBuilder 高标准目标审计</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{audit.completion_ready ? '可完成' : '仍在推进'}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{audit.summary}</div>
      <div style={{ display: 'grid', gap: 4 }}>
        {audit.deliverables.slice(0, 7).map((item) => (
          <div key={item.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.name}</span>
              <span style={{ color: attributionStatusTone(item.status), fontSize: 13 }}>{zhAttributionStatus(item.status)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.summary}</div>
            {item.next_action && <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{item.next_action}</div>}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <SourceDataLink href={item.endpoint} label="证据报告" />
            </div>
          </div>
        ))}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {audit.quality_gates.map((gate) => (
          <div key={gate.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{gate.name}</span>
              <span style={{ color: attributionStatusTone(gate.status), fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{gate.summary}</div>
          </div>
        ))}
      </div>
      {audit.prompt_to_artifact_checklist && (
        <div style={{ display: 'grid', gap: 5, padding: '7px 8px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }} data-team-builder-prompt-artifact-checklist>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>目标到产物检查表</span>
            <span style={{ color: audit.prompt_to_artifact_checklist.status === 'complete' ? '#78d98b' : '#ffcc80', fontSize: 13 }}>
              {audit.prompt_to_artifact_checklist.status === 'complete' ? '已覆盖' : '未完成'}
            </span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{audit.prompt_to_artifact_checklist.objective}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{audit.prompt_to_artifact_checklist.completion_rule}</div>
          <div style={{ display: 'grid', gap: 4 }}>
            {audit.prompt_to_artifact_checklist.items.slice(0, 8).map((item) => (
              <div key={item.id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: 'var(--fp-bg)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{item.requirement}</span>
                  <span style={{ color: attributionStatusTone(item.status), fontSize: 13 }}>{zhAttributionStatus(item.status)}</span>
                </div>
                <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{item.conclusion}</div>
                {item.gap && <div style={{ color: '#ffcc80', fontSize: 13, lineHeight: 1.35 }}>缺口：{item.gap}</div>}
                <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>产物：{item.artifact}</div>
                {item.covered_by_tests.length > 0 && (
                  <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere' }}>验证：{item.covered_by_tests.slice(0, 3).join('、')}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {(audit.boundary_notes || []).length > 0 && (
        <div style={{ display: 'grid', gap: 3 }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>边界说明</div>
          {(audit.boundary_notes || []).slice(0, 3).map((item, index) => (
            <div key={`${index}:${item}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>- {item}</div>
          ))}
        </div>
      )}
      {audit.missing.length > 0 && (
        <div style={{ display: 'grid', gap: 3 }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>仍未完成</div>
          {audit.missing.slice(0, 4).map((item, index) => (
            <div key={`${index}:${item}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>- {item}</div>
          ))}
        </div>
      )}
      {audit.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{audit.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{audit.next_actions[0].summary}</div>
          <SourceDataLink href={audit.next_actions[0].endpoint} label="下一步报告" />
        </div>
      )}
      {audit.source.high_standard_audit_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>高标准审计 material：{audit.source.high_standard_audit_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/high-standard-audit/latest" label="审计报告" />
        </div>
      )}
    </div>
  )
}

function zhProviderCoverageStatus(status: string) {
  if (status === 'missing') return '缺样本'
  return zhAttributionStatus(status)
}

function TeamBuilderProviderCoverageAuditPanel({ audit, error, loading }: {
  audit: TeamBuilderProviderCoverageAudit | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-provider-coverage-audit>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>Provider 覆盖审计读取失败：{error}</div>
      </div>
    )
  }
  if (!audit) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-provider-coverage-audit>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在审计 provider 覆盖...' : '暂无 provider 覆盖审计。'}</div>
      </div>
    )
  }
  const tone = audit.comparison_ready ? '#78d98b' : '#ffcc80'
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-provider-coverage-audit>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>Provider 覆盖审计</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{audit.comparison_ready ? '对比就绪' : '证据不足'}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{audit.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>已扫 run {audit.counts.runs_scanned}</span>
        <span style={S.pill}>外部 provider 样本 {audit.counts.external_providers_with_real_runs}/{audit.counts.tracked_external_providers}</span>
        <span style={S.pill}>内部模型记录 {audit.counts.internal_model_records}</span>
        <span style={S.pill}>同口径试验 {audit.counts.same_input_trials_scanned || 0}</span>
        <span style={S.pill}>team 类型 {audit.counts.team_types_seen}</span>
        <span style={S.pill}>provider 证据 {audit.counts.external_providers_with_evidence ?? audit.counts.external_providers_with_real_runs}/{audit.counts.tracked_external_providers}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {audit.providers.map((provider) => (
          <div key={provider.provider} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{provider.label}</span>
              <span style={{ color: attributionStatusTone(provider.status), fontSize: 13 }}>{zhProviderCoverageStatus(provider.status)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{provider.role}；{provider.summary}</div>
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>run {provider.runs}，成功 worker {provider.successful_workers}，team 类型 {provider.team_type_count || 0}，最新 {provider.latest_run_id || '无'}</div>
            {(provider.same_input_trials || 0) > 0 && (
              <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>
                同口径试验 {provider.same_input_trials}，成功 {provider.trial_successful_workers || 0}，失败 {provider.trial_failed_workers || 0}，源码解析失败 {provider.trial_parse_failures || 0}，最新 {provider.latest_trial_id || '-'}
              </div>
            )}
          </div>
        ))}
      </div>
      {audit.internal_models.map((model) => (
        <div key={model.provider} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{model.label}</span>
            <span style={{ color: attributionStatusTone(model.status), fontSize: 13 }}>{zhProviderCoverageStatus(model.status)}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{model.role}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>{model.summary}</div>
        </div>
      ))}
      {(audit.boundary_notes || []).length > 0 && (
        <div style={{ display: 'grid', gap: 3 }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>边界说明</div>
          {(audit.boundary_notes || []).slice(0, 3).map((item: string, index: number) => (
            <div key={`${index}:${item}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>- {item}</div>
          ))}
        </div>
      )}
      {audit.missing.length > 0 && (
        <div style={{ display: 'grid', gap: 3 }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>仍缺证据</div>
          {audit.missing.slice(0, 4).map((item, index) => (
            <div key={`${index}:${item}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>- {item}</div>
          ))}
        </div>
      )}
      {audit.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{audit.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{audit.next_actions[0].summary}</div>
          <SourceDataLink href={audit.next_actions[0].endpoint} label="下一步报告" />
        </div>
      )}
      {audit.source.provider_coverage_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>provider 覆盖 material：{audit.source.provider_coverage_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/provider-coverage/latest" label="覆盖报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderProviderSameInputTrialPlanPanel({ plan, error, loading }: {
  plan: TeamBuilderProviderSameInputTrialPlan | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-provider-same-input-trial>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>Provider 同口径试验计划读取失败：{error}</div>
      </div>
    )
  }
  if (!plan) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-provider-same-input-trial>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{loading ? '正在整理 Codex 同口径试验计划...' : '暂无 provider 同口径试验计划。'}</div>
      </div>
    )
  }
  const tone = plan.ready ? '#78d98b' : '#ffb74d'
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-provider-same-input-trial>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>{plan.title || 'Codex 同口径 TeamBuilder 试验计划'}</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{plan.ready ? '可显式执行' : '被阻断'}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{plan.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>基线 {plan.baseline_provider || '-'}</span>
        <span style={S.pill}>目标 {plan.target_provider || '-'}</span>
        <span style={S.pill}>{plan.permission}</span>
        <span style={S.pill}>worker {plan.counts.workers}</span>
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {plan.workers.slice(0, 4).map((worker) => (
          <div key={worker.worker_id} style={{ display: 'grid', gap: 3, padding: '5px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{worker.cn_name || worker.worker_id}</span>
              <span style={{ color: colors.textFaint, fontSize: 13 }}>{worker.baseline_status || '-'}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>{worker.worker_id}：{String(worker.format_in)} {'->'} {worker.format_out}</div>
            <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.35 }}>baseline prompt {worker.baseline_prompt_chars} chars；{worker.baseline_rel_path || '-'}</div>
          </div>
        ))}
      </div>
      <div style={{ display: 'grid', gap: 4 }}>
        {plan.safety_gates.map((gate) => (
          <div key={gate.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '4px 6px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <span style={{ color: colors.textMuted, fontSize: 13 }}>{gate.name || gate.id}</span>
            <span style={{ color: attributionStatusTone(gate.status), fontSize: 13 }}>{zhAttributionStatus(gate.status)}</span>
          </div>
        ))}
      </div>
      {plan.command && (
        <div style={{ display: 'grid', gap: 3 }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>显式执行命令</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35, overflowWrap: 'anywhere', padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>{plan.command}</div>
        </div>
      )}
      {plan.missing.length > 0 && (
        <div style={{ display: 'grid', gap: 3 }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>阻断项</div>
          {plan.missing.slice(0, 4).map((item, index) => (
            <div key={`${index}:${item}`} style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.35 }}>- {item}</div>
          ))}
        </div>
      )}
      {plan.next_actions.length > 0 && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>下一步：{plan.next_actions[0].title}</div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{plan.next_actions[0].summary}</div>
          <SourceDataLink href={plan.next_actions[0].endpoint} label="复查报告" />
        </div>
      )}
      {plan.source.same_input_trial_plan_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span>同口径试验 material：{plan.source.same_input_trial_plan_material}</span>
          <SourceDataLink href="/api/team-builder-materialization/provider-same-input-trial/latest" label="计划报告" />
        </div>
      )}
    </div>
  )
}

function TeamBuilderClosureStatusPanel({ status, error, loading }: {
  status: TeamBuilderClosureStatus | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>闭环状态读取失败：{error}</div>
      </div>
    )
  }
  if (!status) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在整理闭环状态...</div>
      </div>
    )
  }
  const tone = attributionStatusTone(status.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-closure-status>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>TeamBuilder 闭环状态</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhAttributionStatus(status.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{status.summary}</div>
      <div style={{ display: 'grid', gap: 6 }}>
        {status.stages.map((stage) => (
          <div key={stage.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{stage.name}</span>
              <span style={{ color: attributionStatusTone(stage.status), fontSize: 13 }}>{zhAttributionStatus(stage.status)}</span>
            </div>
            <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{stage.summary}</div>
          </div>
        ))}
      </div>
      {status.missing.length > 0 && (
        <div style={{ display: 'grid', gap: 4 }}>
          {status.missing.slice(0, 4).map((item) => (
            <div key={item} style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>缺口：{item}</div>
          ))}
        </div>
      )}
      {status.source.closure_status_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          闭环状态 material：{status.source.closure_status_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderLlmReplayPlanPanel({ plan, error, loading }: {
  plan: TeamBuilderLlmReplayPlan | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>LLM 回放计划读取失败：{error}</div>
      </div>
    )
  }
  if (!plan) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在整理 LLM 回放计划...</div>
      </div>
    )
  }
  if (!plan.available) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }}>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{plan.reason || '暂无 LLM 回放计划。'}</div>
      </div>
    )
  }
  const tone = llmReplayPlanTone(plan.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-llm-replay-plan>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>LLM 回放计划</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhLlmReplayVerdict(plan.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{plan.summary}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>调用 {plan.counts.calls}</span>
        <span style={S.pill}>可回放 {plan.counts.ready}</span>
        <span style={S.pill}>阻断 {plan.counts.blocked}</span>
      </div>
      {plan.execution_preflight && (
        <div style={{ display: 'grid', gap: 4, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>执行前置条件</span>
            <span style={{ color: plan.execution_preflight.can_execute ? '#78d98b' : '#ffb74d', fontSize: 13 }}>
              {plan.execution_preflight.can_execute ? '可执行' : '未满足'}
            </span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{plan.execution_preflight.summary}</div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            <span style={S.pill}>开关 {plan.execution_preflight.enabled ? '已开' : '未开'}</span>
            <span style={S.pill}>THE_COMPANY_KEY {plan.execution_preflight.has_the_company_api_key ? '存在' : '缺少'}</span>
            <span style={S.pill}>模型 {plan.execution_preflight.models.join(', ') || '未声明'}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{plan.execution_preflight.next_action}</div>
        </div>
      )}
      {plan.actions.slice(0, 3).map((action) => (
        <div key={action.id} style={{ display: 'grid', gap: 3, padding: '6px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{action.worker_id} / {action.model || '未声明模型'}</span>
            <span style={{ color: action.status === 'ready' ? '#78d98b' : '#ff7b7b', fontSize: 13 }}>{action.status === 'ready' ? '可回放' : '阻断'}</span>
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.human_summary}</div>
          <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
            system {action.system_chars} 字 / user {action.user_chars} 字 / 输出键 {action.expected_output_keys.join(', ') || '未声明'}
          </div>
          <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>{action.next_action}</div>
        </div>
      ))}
      {plan.source.llm_replay_plan_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          回放计划 material：{plan.source.llm_replay_plan_material}
        </div>
      )}
    </div>
  )
}

function TeamBuilderLlmReplayResultPanel({ result, error, loading }: {
  result: TeamBuilderLlmReplayResult | null
  error: string | null
  loading: boolean
}) {
  if (error) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-llm-replay-result>
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>LLM 回放结果读取失败：{error}</div>
      </div>
    )
  }
  if (loading && !result) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-llm-replay-result>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在读取 LLM 回放结果...</div>
      </div>
    )
  }
  if (!result) {
    return (
      <div style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 9, background: 'var(--fp-bg)', marginTop: 8 }} data-team-builder-llm-replay-result>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>暂无 LLM 回放结果。</div>
      </div>
    )
  }
  const tone = llmReplayResultTone(result.verdict)
  return (
    <div style={{ border: `1px solid ${tone}`, borderRadius: 6, padding: 10, background: 'var(--fp-bg)', display: 'grid', gap: 8, marginTop: 8 }} data-team-builder-llm-replay-result>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
        <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>真实 LLM 回放结果</div>
        <span style={{ ...S.pill, color: tone, borderColor: tone }}>{zhLlmReplayResultVerdict(result.verdict)}</span>
      </div>
      <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.5 }}>{result.summary || result.reason || '暂无结果说明。'}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span style={S.pill}>计划调用 {result.counts.planned_calls ?? 0}</span>
        <span style={S.pill}>执行 worker {result.counts.executed_workers ?? 0}</span>
        <span style={S.pill}>LLM worker {result.counts.executed_llm_workers?.length ?? 0}</span>
        <span style={S.pill}>失败 {result.counts.failed_workers ?? 0}</span>
        <span style={S.pill}>契约问题 {result.counts.contract_failures ?? 0}</span>
      </div>
      {result.counts.executed_llm_workers?.length > 0 && (
        <div style={{ display: 'grid', gap: 4 }}>
          {result.counts.executed_llm_workers.slice(0, 4).map((worker) => (
            <div key={`${worker.worker_id}:${worker.kind || ''}`} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '5px 7px', border: `1px solid ${colors.border}`, borderRadius: 5, background: colors.bg }}>
              <span style={{ color: colors.textSecondary, fontSize: 15, fontWeight: 650 }}>{worker.worker_id}</span>
              <span style={{ color: worker.kind === 'pass' ? '#78d98b' : '#ffb74d', fontSize: 13 }}>{worker.kind || '未知'}</span>
            </div>
          ))}
        </div>
      )}
      {result.failed_workers && result.failed_workers.length > 0 && (
        <div style={{ display: 'grid', gap: 4 }}>
          {result.failed_workers.slice(0, 3).map((worker) => (
            <div key={`${worker.worker_id}:${worker.diagnosis || ''}`} style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
              {worker.worker_id}：{worker.diagnosis || worker.kind}
            </div>
          ))}
        </div>
      )}
      {result.contract_failures && result.contract_failures.length > 0 && (
        <div style={{ color: '#ffb74d', fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          {result.contract_failures.slice(0, 4).join('；')}
        </div>
      )}
      {result.source.llm_replay_result_material && (
        <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          回放结果 material：{result.source.llm_replay_result_material}
        </div>
      )}
    </div>
  )
}

function TeamMaterializationSection({ selectedNode, selectedMaterial, materialization, materializationError, materializationLoading, materialReport, materialReportError, materialReportLoading, readClueResolutionPlan, readClueResolutionError, readClueResolutionLoading, materialGapValidation, materialGapValidationError, materialGapValidationLoading, testReport, testReportError, testReportLoading, repairPlan, repairPlanError, repairPlanLoading, repairProbe, repairProbeError, repairProbeLoading, repairDryRun, repairDryRunError, repairDryRunLoading, repairPatchCandidates, repairPatchCandidatesError, repairPatchCandidatesLoading, repairApplyGate, repairApplyGateError, repairApplyGateLoading, repairPatchDiffProposal, repairPatchDiffProposalError, repairPatchDiffProposalLoading, repairApproval, repairApprovalError, repairApprovalLoading, repairExecutionReadiness, repairExecutionReadinessError, repairExecutionReadinessLoading, repairApplyPreview, repairApplyPreviewError, repairApplyPreviewLoading, repairApplyExecution, repairApplyExecutionError, repairApplyExecutionLoading, repairPostApplyVerification, repairPostApplyVerificationError, repairPostApplyVerificationLoading, repairOutcomeReconciliation, repairOutcomeReconciliationError, repairOutcomeReconciliationLoading, repairRollbackReadiness, repairRollbackReadinessError, repairRollbackReadinessLoading, repairRollbackExecution, repairRollbackExecutionError, repairRollbackExecutionLoading, repairRollbackPostVerification, repairRollbackPostVerificationError, repairRollbackPostVerificationLoading, repairClosureRollup, repairClosureRollupError, repairClosureRollupLoading, repairGeneralizationTrial, repairGeneralizationTrialError, repairGeneralizationTrialLoading, repairRealGeneratedFileSetTrial, repairRealGeneratedFileSetTrialError, repairRealGeneratedFileSetTrialLoading, repairRealRunCandidateScan, repairRealRunCandidateScanError, repairRealRunCandidateScanLoading, repairRealRunReplayPlan, repairRealRunReplayPlanError, repairRealRunReplayPlanLoading, repairRealRunDiffPreview, repairRealRunDiffPreviewError, repairRealRunDiffPreviewLoading, repairRealRunDiffReview, repairRealRunDiffReviewError, repairRealRunDiffReviewLoading, repairRealRunApplyGate, repairRealRunApplyGateError, repairRealRunApplyGateLoading, repairRealRunApplyPreview, repairRealRunApplyPreviewError, repairRealRunApplyPreviewLoading, repairRealRunApplyExecution, repairRealRunApplyExecutionError, repairRealRunApplyExecutionLoading, repairRealRunPostApplyVerification, repairRealRunPostApplyVerificationError, repairRealRunPostApplyVerificationLoading, repairRealRunOutcomeReconciliation, repairRealRunOutcomeReconciliationError, repairRealRunOutcomeReconciliationLoading, repairRealRunRollbackReadiness, repairRealRunRollbackReadinessError, repairRealRunRollbackReadinessLoading, repairRealRunRollbackExecution, repairRealRunRollbackExecutionError, repairRealRunRollbackExecutionLoading, repairRealRunRollbackPostVerification, repairRealRunRollbackPostVerificationError, repairRealRunRollbackPostVerificationLoading, repairRealRunClosureRollup, repairRealRunClosureRollupError, repairRealRunClosureRollupLoading, repairSafetyPolicy, repairSafetyPolicyError, repairSafetyPolicyLoading, closureStatus, closureStatusError, closureStatusLoading, llmReplayPlan, llmReplayPlanError, llmReplayPlanLoading, llmReplayResult, llmReplayResultError, llmReplayResultLoading }: {
  selectedNode: TeamGraphNode | null
  selectedMaterial: TeamGraphMaterial | null
  materialization: TeamBuilderMaterialization | null
  materializationError: string | null
  materializationLoading: boolean
  materialReport: MaterialAttributionReport | null
  materialReportError: string | null
  materialReportLoading: boolean
  readClueResolutionPlan: TeamBuilderReadClueResolutionPlan | null
  readClueResolutionError: string | null
  readClueResolutionLoading: boolean
  materialGapValidation: TeamBuilderMaterialGapValidation | null
  materialGapValidationError: string | null
  materialGapValidationLoading: boolean
  testReport: TeamBuilderTestReport | null
  testReportError: string | null
  testReportLoading: boolean
  repairPlan: TeamBuilderRepairPlan | null
  repairPlanError: string | null
  repairPlanLoading: boolean
  repairProbe: TeamBuilderRepairProbeReport | null
  repairProbeError: string | null
  repairProbeLoading: boolean
  repairDryRun: TeamBuilderRepairDryRunReport | null
  repairDryRunError: string | null
  repairDryRunLoading: boolean
  repairPatchCandidates: TeamBuilderRepairPatchCandidatesReport | null
  repairPatchCandidatesError: string | null
  repairPatchCandidatesLoading: boolean
  repairApplyGate: TeamBuilderRepairApplyGateReport | null
  repairApplyGateError: string | null
  repairApplyGateLoading: boolean
  repairPatchDiffProposal: TeamBuilderRepairPatchDiffProposalReport | null
  repairPatchDiffProposalError: string | null
  repairPatchDiffProposalLoading: boolean
  repairApproval: TeamBuilderRepairApprovalReport | null
  repairApprovalError: string | null
  repairApprovalLoading: boolean
  repairExecutionReadiness: TeamBuilderRepairExecutionReadinessReport | null
  repairExecutionReadinessError: string | null
  repairExecutionReadinessLoading: boolean
  repairApplyPreview: TeamBuilderRepairApplyPreviewReport | null
  repairApplyPreviewError: string | null
  repairApplyPreviewLoading: boolean
  repairApplyExecution: TeamBuilderRepairApplyExecutionReport | null
  repairApplyExecutionError: string | null
  repairApplyExecutionLoading: boolean
  repairPostApplyVerification: TeamBuilderRepairPostApplyVerificationReport | null
  repairPostApplyVerificationError: string | null
  repairPostApplyVerificationLoading: boolean
  repairOutcomeReconciliation: TeamBuilderRepairOutcomeReconciliationReport | null
  repairOutcomeReconciliationError: string | null
  repairOutcomeReconciliationLoading: boolean
  repairRollbackReadiness: TeamBuilderRepairRollbackReadinessReport | null
  repairRollbackReadinessError: string | null
  repairRollbackReadinessLoading: boolean
  repairRollbackExecution: TeamBuilderRepairRollbackExecutionReport | null
  repairRollbackExecutionError: string | null
  repairRollbackExecutionLoading: boolean
  repairRollbackPostVerification: TeamBuilderRepairRollbackPostVerificationReport | null
  repairRollbackPostVerificationError: string | null
  repairRollbackPostVerificationLoading: boolean
  repairClosureRollup: TeamBuilderRepairClosureRollupReport | null
  repairClosureRollupError: string | null
  repairClosureRollupLoading: boolean
  repairGeneralizationTrial: TeamBuilderRepairGeneralizationTrialReport | null
  repairGeneralizationTrialError: string | null
  repairGeneralizationTrialLoading: boolean
  repairRealGeneratedFileSetTrial: TeamBuilderRepairRealGeneratedFileSetTrialReport | null
  repairRealGeneratedFileSetTrialError: string | null
  repairRealGeneratedFileSetTrialLoading: boolean
  repairRealRunCandidateScan: TeamBuilderRepairRealRunCandidateScanReport | null
  repairRealRunCandidateScanError: string | null
  repairRealRunCandidateScanLoading: boolean
  repairRealRunReplayPlan: TeamBuilderRepairRealRunReplayPlanReport | null
  repairRealRunReplayPlanError: string | null
  repairRealRunReplayPlanLoading: boolean
  repairRealRunDiffPreview: TeamBuilderRepairRealRunDiffPreviewReport | null
  repairRealRunDiffPreviewError: string | null
  repairRealRunDiffPreviewLoading: boolean
  repairRealRunDiffReview: TeamBuilderRepairRealRunDiffReviewReport | null
  repairRealRunDiffReviewError: string | null
  repairRealRunDiffReviewLoading: boolean
  repairRealRunApplyGate: TeamBuilderRepairRealRunApplyGateReport | null
  repairRealRunApplyGateError: string | null
  repairRealRunApplyGateLoading: boolean
  repairRealRunApplyPreview: TeamBuilderRepairRealRunApplyPreviewReport | null
  repairRealRunApplyPreviewError: string | null
  repairRealRunApplyPreviewLoading: boolean
  repairRealRunApplyExecution: TeamBuilderRepairRealRunApplyExecutionReport | null
  repairRealRunApplyExecutionError: string | null
  repairRealRunApplyExecutionLoading: boolean
  repairRealRunPostApplyVerification: TeamBuilderRepairRealRunPostApplyVerificationReport | null
  repairRealRunPostApplyVerificationError: string | null
  repairRealRunPostApplyVerificationLoading: boolean
  repairRealRunOutcomeReconciliation: TeamBuilderRepairRealRunOutcomeReconciliationReport | null
  repairRealRunOutcomeReconciliationError: string | null
  repairRealRunOutcomeReconciliationLoading: boolean
  repairRealRunRollbackReadiness: TeamBuilderRepairRealRunRollbackReadinessReport | null
  repairRealRunRollbackReadinessError: string | null
  repairRealRunRollbackReadinessLoading: boolean
  repairRealRunRollbackExecution: TeamBuilderRepairRealRunRollbackExecutionReport | null
  repairRealRunRollbackExecutionError: string | null
  repairRealRunRollbackExecutionLoading: boolean
  repairRealRunRollbackPostVerification: TeamBuilderRepairRealRunRollbackPostVerificationReport | null
  repairRealRunRollbackPostVerificationError: string | null
  repairRealRunRollbackPostVerificationLoading: boolean
  repairRealRunClosureRollup: TeamBuilderRepairRealRunClosureRollupReport | null
  repairRealRunClosureRollupError: string | null
  repairRealRunClosureRollupLoading: boolean
  repairSafetyPolicy: TeamBuilderRepairSafetyPolicy | null
  repairSafetyPolicyError: string | null
  repairSafetyPolicyLoading: boolean
  closureStatus: TeamBuilderClosureStatus | null
  closureStatusError: string | null
  closureStatusLoading: boolean
  llmReplayPlan: TeamBuilderLlmReplayPlan | null
  llmReplayPlanError: string | null
  llmReplayPlanLoading: boolean
  llmReplayResult: TeamBuilderLlmReplayResult | null
  llmReplayResultError: string | null
  llmReplayResultLoading: boolean
}) {
  const [highStandardAudit, setHighStandardAudit] = useState<TeamBuilderHighStandardAudit | null>(null)
  const [highStandardAuditError, setHighStandardAuditError] = useState<string | null>(null)
  const [highStandardAuditLoading, setHighStandardAuditLoading] = useState(false)
  const [providerCoverageAudit, setProviderCoverageAudit] = useState<TeamBuilderProviderCoverageAudit | null>(null)
  const [providerCoverageAuditError, setProviderCoverageAuditError] = useState<string | null>(null)
  const [providerCoverageAuditLoading, setProviderCoverageAuditLoading] = useState(false)
  const [providerSameInputTrialPlan, setProviderSameInputTrialPlan] = useState<TeamBuilderProviderSameInputTrialPlan | null>(null)
  const [providerSameInputTrialPlanError, setProviderSameInputTrialPlanError] = useState<string | null>(null)
  const [providerSameInputTrialPlanLoading, setProviderSameInputTrialPlanLoading] = useState(false)
  useEffect(() => {
    let cancelled = false
    setHighStandardAuditLoading(true)
    fetchTeamBuilderHighStandardAuditLatest()
      .then((data) => {
        if (!cancelled) {
          setHighStandardAudit(data)
          setHighStandardAuditError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) setHighStandardAuditError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setHighStandardAuditLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])
  useEffect(() => {
    let cancelled = false
    setProviderCoverageAuditLoading(true)
    fetchTeamBuilderProviderCoverageAuditLatest()
      .then((data) => {
        if (!cancelled) {
          setProviderCoverageAudit(data)
          setProviderCoverageAuditError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) setProviderCoverageAuditError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setProviderCoverageAuditLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])
  useEffect(() => {
    let cancelled = false
    setProviderSameInputTrialPlanLoading(true)
    fetchTeamBuilderProviderSameInputTrialPlanLatest()
      .then((data) => {
        if (!cancelled) {
          setProviderSameInputTrialPlan(data)
          setProviderSameInputTrialPlanError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) setProviderSameInputTrialPlanError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setProviderSameInputTrialPlanLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])
  if (materializationError) {
    return (
      <div style={S.section} data-team-materialization>
        <div style={S.label}>最近一次生成审查</div>
        <div style={{ color: '#ffb74d', fontSize: 13, overflowWrap: 'anywhere' }}>{materializationError}</div>
      </div>
    )
  }
  if (!materialization) {
    return (
      <div style={S.section} data-team-materialization>
        <div style={S.label}>最近一次生成审查</div>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>正在读取最新实战结果...</div>
      </div>
    )
  }
  if (!materialization.available) {
    return (
      <div style={S.section} data-team-materialization>
        <div style={S.label}>最近一次生成审查</div>
        <div style={{ color: colors.textMuted, fontSize: 13 }}>{materialization.reason || '暂无实战结果。'}</div>
      </div>
    )
  }

  const selectedRun = selectedNode ? materializationRunForWorker(materialization, selectedNode.id) : null
  const materialLinks = selectedMaterial
    ? materialization.worker_runs.flatMap((run) => [
      ...run.material_io_links,
      ...run.produced_content_materials,
      ...run.resource_material_links,
      ...run.inferred_material_read_links,
    ].filter((link) => linkMatchesMaterial(link, selectedMaterial.id)))
    : []
  const reviewTone = materialization.review.kind === 'fail' ? '#ff7b7b' : '#78d98b'
  const runsToShow = selectedRun ? [selectedRun] : materialization.worker_runs

  return (
    <div style={S.section} data-team-materialization>
      <div style={S.label}>最近一次生成审查</div>
      <MaterializationExplanation />
      <TeamBuilderHighStandardAuditPanel audit={highStandardAudit} error={highStandardAuditError} loading={highStandardAuditLoading} />
      <TeamBuilderProviderCoverageAuditPanel audit={providerCoverageAudit} error={providerCoverageAuditError} loading={providerCoverageAuditLoading} />
      <TeamBuilderProviderSameInputTrialPlanPanel plan={providerSameInputTrialPlan} error={providerSameInputTrialPlanError} loading={providerSameInputTrialPlanLoading} />
      <TeamBuilderClosureStatusPanel status={closureStatus} error={closureStatusError} loading={closureStatusLoading} />
      <MaterialAttributionReportPanel report={materialReport} error={materialReportError} loading={materialReportLoading} />
      <TeamBuilderReadClueResolutionPanel plan={readClueResolutionPlan} error={readClueResolutionError} loading={readClueResolutionLoading} />
      <TeamBuilderMaterialGapValidationPanel report={materialGapValidation} error={materialGapValidationError} loading={materialGapValidationLoading} />
      <TeamBuilderTestReportPanel report={testReport} error={testReportError} loading={testReportLoading} />
      <TeamBuilderLlmReplayPlanPanel plan={llmReplayPlan} error={llmReplayPlanError} loading={llmReplayPlanLoading} />
      <TeamBuilderLlmReplayResultPanel result={llmReplayResult} error={llmReplayResultError} loading={llmReplayResultLoading} />
      <TeamBuilderRepairClosureRollupPanel report={repairClosureRollup} error={repairClosureRollupError} loading={repairClosureRollupLoading} />
      <TeamBuilderRepairGeneralizationTrialPanel report={repairGeneralizationTrial} error={repairGeneralizationTrialError} loading={repairGeneralizationTrialLoading} />
      <TeamBuilderRepairRealGeneratedFileSetTrialPanel report={repairRealGeneratedFileSetTrial} error={repairRealGeneratedFileSetTrialError} loading={repairRealGeneratedFileSetTrialLoading} />
      <TeamBuilderRepairRealRunClosureRollupPanel report={repairRealRunClosureRollup} error={repairRealRunClosureRollupError} loading={repairRealRunClosureRollupLoading} />
      <TeamBuilderRepairRealRunCandidateScanPanel report={repairRealRunCandidateScan} error={repairRealRunCandidateScanError} loading={repairRealRunCandidateScanLoading} />
      <TeamBuilderRepairRealRunReplayPlanPanel report={repairRealRunReplayPlan} error={repairRealRunReplayPlanError} loading={repairRealRunReplayPlanLoading} />
      <TeamBuilderRepairRealRunDiffPreviewPanel report={repairRealRunDiffPreview} error={repairRealRunDiffPreviewError} loading={repairRealRunDiffPreviewLoading} />
      <TeamBuilderRepairRealRunDiffReviewPanel report={repairRealRunDiffReview} error={repairRealRunDiffReviewError} loading={repairRealRunDiffReviewLoading} />
      <TeamBuilderRepairRealRunApplyGatePanel report={repairRealRunApplyGate} error={repairRealRunApplyGateError} loading={repairRealRunApplyGateLoading} />
      <TeamBuilderRepairRealRunApplyPreviewPanel report={repairRealRunApplyPreview} error={repairRealRunApplyPreviewError} loading={repairRealRunApplyPreviewLoading} />
      <TeamBuilderRepairRealRunApplyExecutionPanel report={repairRealRunApplyExecution} error={repairRealRunApplyExecutionError} loading={repairRealRunApplyExecutionLoading} />
      <TeamBuilderRepairRealRunPostApplyVerificationPanel report={repairRealRunPostApplyVerification} error={repairRealRunPostApplyVerificationError} loading={repairRealRunPostApplyVerificationLoading} />
      <TeamBuilderRepairRealRunOutcomeReconciliationPanel report={repairRealRunOutcomeReconciliation} error={repairRealRunOutcomeReconciliationError} loading={repairRealRunOutcomeReconciliationLoading} />
      <TeamBuilderRepairRealRunRollbackReadinessPanel report={repairRealRunRollbackReadiness} error={repairRealRunRollbackReadinessError} loading={repairRealRunRollbackReadinessLoading} />
      <TeamBuilderRepairRealRunRollbackExecutionPanel report={repairRealRunRollbackExecution} error={repairRealRunRollbackExecutionError} loading={repairRealRunRollbackExecutionLoading} />
      <TeamBuilderRepairRealRunRollbackPostVerificationPanel report={repairRealRunRollbackPostVerification} error={repairRealRunRollbackPostVerificationError} loading={repairRealRunRollbackPostVerificationLoading} />
      <TeamBuilderRepairPlanPanel plan={repairPlan} error={repairPlanError} loading={repairPlanLoading} />
      <TeamBuilderRepairProbePanel report={repairProbe} error={repairProbeError} loading={repairProbeLoading} />
      <TeamBuilderRepairDryRunPanel report={repairDryRun} error={repairDryRunError} loading={repairDryRunLoading} />
      <TeamBuilderRepairPatchCandidatesPanel report={repairPatchCandidates} error={repairPatchCandidatesError} loading={repairPatchCandidatesLoading} />
      <TeamBuilderRepairApplyGatePanel report={repairApplyGate} error={repairApplyGateError} loading={repairApplyGateLoading} />
      <TeamBuilderRepairPatchDiffProposalPanel report={repairPatchDiffProposal} error={repairPatchDiffProposalError} loading={repairPatchDiffProposalLoading} />
      <TeamBuilderRepairApprovalPanel report={repairApproval} error={repairApprovalError} loading={repairApprovalLoading} />
      <TeamBuilderRepairExecutionReadinessPanel report={repairExecutionReadiness} error={repairExecutionReadinessError} loading={repairExecutionReadinessLoading} />
      <TeamBuilderRepairApplyPreviewPanel report={repairApplyPreview} error={repairApplyPreviewError} loading={repairApplyPreviewLoading} />
      <TeamBuilderRepairApplyExecutionPanel report={repairApplyExecution} error={repairApplyExecutionError} loading={repairApplyExecutionLoading} />
      <TeamBuilderRepairPostApplyVerificationPanel report={repairPostApplyVerification} error={repairPostApplyVerificationError} loading={repairPostApplyVerificationLoading} />
      <TeamBuilderRepairOutcomeReconciliationPanel report={repairOutcomeReconciliation} error={repairOutcomeReconciliationError} loading={repairOutcomeReconciliationLoading} />
      <TeamBuilderRepairRollbackReadinessPanel report={repairRollbackReadiness} error={repairRollbackReadinessError} loading={repairRollbackReadinessLoading} />
      <TeamBuilderRepairRollbackExecutionPanel report={repairRollbackExecution} error={repairRollbackExecutionError} loading={repairRollbackExecutionLoading} />
      <TeamBuilderRepairRollbackPostVerificationPanel report={repairRollbackPostVerification} error={repairRollbackPostVerificationError} loading={repairRollbackPostVerificationLoading} />
      <TeamBuilderRepairSafetyPolicyPanel policy={repairSafetyPolicy} error={repairSafetyPolicyError} loading={repairSafetyPolicyLoading} />
      <div style={{ ...S.metricRow, marginTop: 10 }}>
        <div style={S.metric}><div style={S.metricValue}>{materialization.counts.generated_candidates}</div><div style={S.metricName}>生成文件</div></div>
        <div style={S.metric}><div style={S.metricValue}>{materialization.counts.resource_candidates}</div><div style={S.metricName}>读取线索</div></div>
        <div style={S.metric}><div style={S.metricValue}>{materialization.counts.workers_with_missing_required}</div><div style={S.metricName}>需处理</div></div>
      </div>
      <Kv
        name="检查编号"
        value={<span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}><span style={S.pill}>{materialization.run_id}</span><SourceDataLink href={materializationRawHref()} label="原始汇总" /></span>}
      />
      <Kv name="审查对象" value={materialization.team_name || '-'} />
      <Kv name="执行方式" value={`${materialization.provider || '-'} / ${materialization.started_at_local || '-'}`} />
      <Kv name="代码生成" value={`${materialization.counts.worker_success_count} 成功 / ${materialization.counts.worker_fail_count} 失败 / ${materialization.counts.compile_fail_count} 编译失败`} />
      <Kv
        name="生成审查"
        value={<span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}><span style={{ color: reviewTone }}>{zhVerdict(materialization.review.kind || materialization.review.verdict)}</span><SourceDataLink href={materializationRawHref()} label="原始审查" /></span>}
      />
      {materialization.review.diagnosis && (
        <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45, overflowWrap: 'anywhere' }}>
          {materialization.review.diagnosis}
        </div>
      )}

      {selectedMaterial ? (
        <MaterializationLinks
          title={`选中 Material 的实战命中：${zhMaterialName(selectedMaterial.id)}`}
          links={materialLinks}
          emptyText="最新实战 run 没有直接命中这个 material。"
          limit={8}
        />
      ) : (
        <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
          {runsToShow.map((run) => {
            const issues = reviewIssuesForWorker(materialization, run.worker_id)
            const reportForWorker = materializationReportForWorker(materialReport, run.worker_id)
            return (
              <div key={run.worker_id} style={{ border: `1px solid ${colors.border}`, borderRadius: 6, padding: 10, background: colors.bg }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>{zhWorkerName(run.worker_id)}</div>
                    <div style={{ marginTop: 3, color: colors.textMuted, fontFamily: fonts.mono, fontSize: 13, overflowWrap: 'anywhere' }}>{run.worker_id}</div>
                  </div>
                  <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
                    <span style={{ color: run.status === 'succeeded' ? '#78d98b' : '#ff7b7b', fontSize: 13 }}>{zhGenerationStatus(run.status)}</span>
                    <SourceDataLink href={materializationRawHref({ worker: run.worker_id })} label="原始记录" />
                  </span>
                </div>
                <div style={{ color: colors.textMuted, fontSize: 13, marginTop: 6 }}>
                  声明读写 {run.material_io_links.length} 条，生成产物 {run.produced_content_materials.length} 个，实战读取线索 {run.resource_material_links.length} 条，确认读取 {run.inferred_material_read_links.length} 条。
                </div>
                {reportForWorker && (
                  <div style={{ marginTop: 7, display: 'grid', gap: 5, color: colors.textSecondary, fontSize: 13, lineHeight: 1.45 }}>
                    <div>{reportForWorker.summary}</div>
                    <div style={{ color: attributionStatusTone(reportForWorker.status) }}>
                      字段契约：{reportForWorker.field_contract.summary}
                    </div>
                    {reportForWorker.risks.slice(0, 2).map((risk) => (
                      <div key={risk} style={{ color: '#ffb74d' }}>风险：{risk}</div>
                    ))}
                    {reportForWorker.next_actions[0] && (
                      <div style={{ color: colors.textMuted }}>下一步：{reportForWorker.next_actions[0]}</div>
                    )}
                  </div>
                )}
                {!selectedRun && (
                  <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                    <MaterialLinkChips
                      label="声明输入/输出"
                      description="来自 Team 输入/输出声明, 可视为当前稳定事实。"
                      links={run.material_io_links}
                      workerId={run.worker_id}
                    />
                    <MaterialLinkChips
                      label="生成产物"
                      description="外部代理这次返回的源码文件, 先作为生成产物候选。"
                      links={run.produced_content_materials}
                      limit={2}
                      workerId={run.worker_id}
                    />
                    <MaterialLinkChips
                      label="实战读取线索"
                      description="来自文件、目录、命令或查询记录, 用来解释为什么怀疑 worker 接触过某份资料。"
                      links={run.resource_material_links}
                      limit={2}
                      workerId={run.worker_id}
                    />
                    <MaterialLinkChips
                      label="已确认读取"
                      description="已经从文件头 material_id 或明确命中升级为正式读取关系。"
                      links={run.inferred_material_read_links}
                      limit={2}
                      workerId={run.worker_id}
                    />
                  </div>
                )}
                {issues.length > 0 && (
                  <div style={{ marginTop: 8, color: '#ffb74d', fontSize: 13, lineHeight: 1.45 }}>
                    {issues.map((issue) => (
                      <div key={`${issue.category}-${issue.issue}`} style={{ display: 'grid', gap: 5 }}>
                        <div>{issue.issue}</div>
                        <SourceDataLink href={materializationRawHref({ worker: run.worker_id })} label="原始来源" />
                      </div>
                    ))}
                  </div>
                )}
                {selectedRun && (
                  <>
                    <FieldAccessBlock run={run} />
                    <MaterializationLinks title="声明输入/输出 material" links={run.material_io_links} emptyText="没有声明读写。" workerId={run.worker_id}/>
                    <MaterializationLinks title="生成产物" links={run.produced_content_materials} emptyText="没有生成内容。" workerId={run.worker_id}/>
                    <MaterializationLinks title="实战读取线索" links={run.resource_material_links} emptyText="没有读取线索。" limit={8} workerId={run.worker_id}/>
                    <MaterializationLinks title="已确认读取" links={run.inferred_material_read_links} emptyText="还没有确认读取边。" limit={8} workerId={run.worker_id}/>
                  </>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function nodeColor(node: TeamGraphNode): { background: string; border: string } {
  if (node.is_entry) return { background: '#132033', border: colors.accent }
  if (node.validator_kind === 'hard') return { background: '#11251a', border: '#2f8f4e' }
  if (node.validator_kind === 'soft') return { background: '#211c34', border: '#7761d8' }
  if (node.kind === 'transformer') return { background: '#102a30', border: '#02b8cc' }
  return { background: colors.bgCard, border: colors.border }
}

const GRAPH_NODE_WIDTH = 300
const GRAPH_NODE_HEIGHT = 218
const MATERIAL_NODE_WIDTH = 260
const MATERIAL_NODE_HEIGHT = 128
const RESOURCE_NODE_WIDTH = 260
const RESOURCE_NODE_HEIGHT = 122
const MATERIALIZATION_WORKER_NODE_WIDTH = 286
const MATERIALIZATION_WORKER_NODE_HEIGHT = 148
const MATERIALIZATION_LINK_NODE_WIDTH = 286
const MATERIALIZATION_LINK_NODE_HEIGHT = 146
const MATERIALIZATION_LINK_GROUP_NODE_WIDTH = 310
const MATERIALIZATION_LINK_GROUP_NODE_HEIGHT = 154
const MATERIALIZATION_LINK_GROUP_THRESHOLD = 8
const GRAPH_COLUMN_GAP = 330
const GRAPH_ROW_GAP = 258

type LayoutPoint = { x: number; y: number }
type MaterializationLinkKind = 'declared' | 'generated' | 'resource' | 'inferred'
type GraphItemKind = 'worker' | 'material' | 'resource' | 'materialization-worker' | 'materialization-link' | 'materialization-link-group'
type TeamVisualNode =
  | { id: string; kind: 'worker'; worker: TeamGraphNode; width: number; height: number }
  | { id: string; kind: 'material'; material: TeamGraphMaterial; width: number; height: number }
  | { id: string; kind: 'resource'; resource: TeamResourceHint; width: number; height: number }
  | { id: string; kind: 'materialization-worker'; run: TeamMaterializationWorkerRun; ownerNodeId: string | null; width: number; height: number }
  | { id: string; kind: 'materialization-link'; run: TeamMaterializationWorkerRun; link: TeamMaterializationLink; linkKind: MaterializationLinkKind; ownerNodeId: string | null; width: number; height: number }
  | { id: string; kind: 'materialization-link-group'; run: TeamMaterializationWorkerRun; group: TeamMaterializationLinkGroup; ownerNodeId: string | null; width: number; height: number }

interface TeamVisualEdge {
  id: string
  source: string
  target: string
  material_id: string | null
  resource_id?: ResourceKind
  materialization_link?: TeamMaterializationLink
  materialization_group?: TeamMaterializationLinkGroup
  kind: 'producer' | 'consumer' | 'resource' | 'materialization-owner' | 'materialization-declared' | 'materialization-produced' | 'materialization-resource' | 'materialization-inferred'
}

function materialNodeId(materialId: string): string {
  return `material:${materialId}`
}

function resourceNodeId(kind: ResourceKind): string {
  return `resource:${kind}`
}

function materializationWorkerNodeId(workerId: string): string {
  return `mworker:${encodeURIComponent(workerId)}`
}

function materializationLinkNodeId(run: TeamMaterializationWorkerRun, linkKind: MaterializationLinkKind, index: number, link: TeamMaterializationLink): string {
  const key = link.material_id || link.target || link.rel_path || link.evidence[0] || String(index)
  return `mlink:${encodeURIComponent(`${run.worker_id}:${linkKind}:${index}:${key}`)}`
}

function materializationLinkGroupNodeId(run: TeamMaterializationWorkerRun, linkKind: 'resource' | 'inferred'): string {
  return `mgroup:${encodeURIComponent(`${run.worker_id}:${linkKind}`)}`
}

function parseGraphItemId(id: string): { kind: GraphItemKind; id: string } {
  if (id.startsWith('material:')) return { kind: 'material', id: id.slice('material:'.length) }
  if (id.startsWith('resource:')) return { kind: 'resource', id: id.slice('resource:'.length) }
  if (id.startsWith('mworker:')) return { kind: 'materialization-worker', id: decodeURIComponent(id.slice('mworker:'.length)) }
  if (id.startsWith('mlink:')) return { kind: 'materialization-link', id: decodeURIComponent(id.slice('mlink:'.length)) }
  if (id.startsWith('mgroup:')) return { kind: 'materialization-link-group', id: decodeURIComponent(id.slice('mgroup:'.length)) }
  return { kind: 'worker', id }
}

function materializationLayerVisible(graph: TeamGraphData, materialization: TeamBuilderMaterialization | null): boolean {
  if (!materialization?.available || materialization.worker_runs.length === 0) return false
  const workerIds = new Set(graph.nodes.map((node) => node.id))
  if (materialization.worker_runs.some((run) => workerIds.has(run.worker_id))) return true
  return graph.team_id.includes('team_builder') || graph.name.includes('team_builder') || workerIds.has('worker_code_orchestrator')
}

function resolveMaterializationOwnerNodeId(graph: TeamGraphData, run: TeamMaterializationWorkerRun): string | null {
  const workerIds = new Set(graph.nodes.map((node) => node.id))
  if (workerIds.has(run.worker_id)) return run.worker_id
  const candidates = ['worker_code_orchestrator', 'code_aggregator', 'code_reviewer', graph.entry]
  return candidates.find((candidate) => candidate && workerIds.has(candidate)) || null
}

function materializationTargetText(link: TeamMaterializationLink): string {
  return link.material_id || link.target || link.rel_path || link.evidence[0] || '未命名证据'
}

function zhCandidateKind(value: string | null | undefined): string {
  const map: Record<string, string> = {
    file: '文件',
    directory: '目录',
    glob: '文件匹配',
    pattern: '路径模式',
    command: '命令',
    cmd: '命令',
    argv: '命令参数',
    query: '查询',
    resource: '资源',
  }
  return value ? (map[value] || value) : '资源'
}

function materializationHumanTitle(link: TeamMaterializationLink): string {
  if (link.human_title) return link.human_title
  if (link.registration_status === 'generated-candidate') {
    const value = link.rel_path || link.normalized_target || link.material_id
    return value ? `生成产物：${fileLeaf(value)}` : '生成产物'
  }
  if (link.registration_status === 'candidate') {
    const value = link.normalized_target || link.target || link.rel_path || link.evidence[0] || link.material_id
    if (link.candidate_kind === 'command' || link.candidate_kind === 'cmd' || link.candidate_kind === 'argv') return '命令读取线索'
    if (link.candidate_kind === 'glob' || link.candidate_kind === 'pattern') return `文件匹配：${trimText(value, 42)}`
    return `工作区文件：${fileLeaf(value)}`
  }
  if (link.material_id) return zhMaterialName(link.material_id)
  return trimText(link.normalized_target || link.target || link.rel_path || link.evidence[0] || '读取线索', 42)
}

function materializationHumanSummary(link: TeamMaterializationLink): string {
  if (link.human_summary) return link.human_summary
  if (link.target_summary) return link.target_summary
  if (link.evidence_summary) return link.evidence_summary
  if (link.registration_status === 'candidate') {
    const target = link.normalized_target || link.target || link.rel_path || '未记录具体目标'
    return `实战记录显示 worker 接触过 ${target}；现在只作为待确认读取线索展示。`
  }
  if (link.registration_status === 'generated-candidate') return '外部代理这次生成了这个文件，因此先作为生成产物候选展示。'
  return '来自 Team/Worker 定义或 material 归因结果的读写关系。'
}

function materializationEvidenceSummary(link: TeamMaterializationLink): string {
  if (link.evidence_summary) return link.evidence_summary
  if (link.candidate_reason) return link.candidate_reason
  if (link.basis) return link.basis
  return '暂无更细的判断说明。'
}

function materializationShortTarget(link: TeamMaterializationLink): string {
  return trimText(materializationHumanTitle(link), 42)
}

function materializationLinkKindLabel(linkKind: MaterializationLinkKind, link: TeamMaterializationLink): string {
  if (linkKind === 'generated') return '生成产物'
  if (linkKind === 'resource') return '实战读取线索'
  if (linkKind === 'inferred') return '已推断读取'
  return link.direction === 'write' ? '声明输出' : '声明输入'
}

function uniqueNonEmpty(values: Array<string | null | undefined>, limit = 8): string[] {
  const result: string[] = []
  const seen = new Set<string>()
  values.forEach((value) => {
    const text = trimText(value, 220)
    if (!text || seen.has(text) || result.length >= limit) return
    seen.add(text)
    result.push(text)
  })
  return result
}

function materializationGroupLinkIds(links: TeamMaterializationLink[], limit = 12): string[] {
  return uniqueNonEmpty(
    links.flatMap((link) => [
      link.material_id,
      ...(link.matched_material_ids || []),
      link.candidate_material_id,
    ]),
    limit,
  )
}

function materializationGroupTargets(links: TeamMaterializationLink[], limit = 6): string[] {
  return uniqueNonEmpty(
    links.map((link) => link.rel_path || link.normalized_target || link.target || link.target_title || link.evidence[0]),
    limit,
  )
}

function materializationLinkGroupFromLinks(run: TeamMaterializationWorkerRun, linkKind: 'resource' | 'inferred', links: TeamMaterializationLink[]): TeamMaterializationLinkGroup {
  const samples = materializationGroupTargets(links)
  const materialIds = materializationGroupLinkIds(links)
  const first = links[0]
  const kindLabel = linkKind === 'resource' ? '工具检索命中文件组' : '确认读取材料组'
  const evidenceLabel = linkKind === 'resource'
    ? '这些线索来自同一 worker 的工具调用、结果路径或工作区文件读取，先合并成一组审阅入口。'
    : '这些项目已经从工具读取或文件 material_id 中推断成读取关系，先合并展示，避免画布出现大量平行线。'
  const sampleText = samples.length ? `样例：${samples.slice(0, 3).join('、')}` : '暂无可展示样例。'
  return {
    id: `${run.worker_id}:${linkKind}`,
    linkKind,
    worker_id: run.worker_id,
    label: `${kindLabel}（${links.length}）`,
    summary: `${zhWorkerName(run.worker_id)} 有 ${links.length} 条${linkKind === 'resource' ? '待确认读取线索' : '已推断读取关系'}。${sampleText}`,
    evidence_summary: first?.evidence_summary || first?.candidate_reason || first?.basis || evidenceLabel,
    count: links.length,
    links,
    sample_targets: samples,
    matched_material_ids: materialIds,
  }
}

function materializationEdgeLabel(edge: TeamVisualEdge): string | undefined {
  if (edge.materialization_group) return edge.materialization_group.linkKind === 'resource' ? `读取线索 ${edge.materialization_group.count}` : `确认读取 ${edge.materialization_group.count}`
  if (edge.kind === 'materialization-owner') return '实战生成'
  if (edge.kind === 'materialization-produced') return '生成文件'
  if (edge.kind === 'materialization-resource') return '读取线索'
  if (edge.kind === 'materialization-inferred') return '确认读取'
  if (edge.kind === 'materialization-declared') {
    return edge.materialization_link?.direction === 'write' ? '声明输出' : '声明输入'
  }
  return undefined
}

function buildMaterializationVisualLayer(graph: TeamGraphData, materialization: TeamBuilderMaterialization | null): {
  nodes: Extract<TeamVisualNode, { kind: 'materialization-worker' | 'materialization-link' | 'materialization-link-group' }>[]
  edges: TeamVisualEdge[]
} {
  if (!materializationLayerVisible(graph, materialization)) return { nodes: [], edges: [] }
  const activeMaterialization = materialization
  if (!activeMaterialization) return { nodes: [], edges: [] }

  const materialIds = new Set(graph.materials.map((material) => material.id))
  const nodes: Extract<TeamVisualNode, { kind: 'materialization-worker' | 'materialization-link' | 'materialization-link-group' }>[] = []
  const edges: TeamVisualEdge[] = []
  const seenNodes = new Set<string>()
  const seenEdges = new Set<string>()
  const pushNode = (node: Extract<TeamVisualNode, { kind: 'materialization-worker' | 'materialization-link' | 'materialization-link-group' }>) => {
    if (seenNodes.has(node.id)) return
    seenNodes.add(node.id)
    nodes.push(node)
  }
  const pushEdge = (edge: TeamVisualEdge) => {
    if (seenEdges.has(edge.id)) return
    seenEdges.add(edge.id)
    edges.push(edge)
  }

  const addLink = (
    run: TeamMaterializationWorkerRun,
    link: TeamMaterializationLink,
    linkKind: MaterializationLinkKind,
    index: number,
    source: string,
    target: string,
    ownerNodeId: string | null,
  ) => {
    pushEdge({
      id: `mflow:${encodeURIComponent(`${source}->${target}:${linkKind}:${index}`)}`,
      source,
      target,
      material_id: link.material_id || null,
      materialization_link: link,
      kind: linkKind === 'generated'
        ? 'materialization-produced'
        : linkKind === 'resource'
          ? 'materialization-resource'
          : linkKind === 'inferred'
            ? 'materialization-inferred'
            : 'materialization-declared',
    })
  }

  const addLinkGroup = (
    run: TeamMaterializationWorkerRun,
    linkKind: 'resource' | 'inferred',
    links: TeamMaterializationLink[],
    ownerNodeId: string | null,
  ) => {
    if (!links.length) return
    const group = materializationLinkGroupFromLinks(run, linkKind, links)
    const groupNodeId = materializationLinkGroupNodeId(run, linkKind)
    pushNode({
      id: groupNodeId,
      kind: 'materialization-link-group',
      run,
      group,
      ownerNodeId,
      width: MATERIALIZATION_LINK_GROUP_NODE_WIDTH,
      height: MATERIALIZATION_LINK_GROUP_NODE_HEIGHT,
    })
    pushEdge({
      id: `mgroup:${encodeURIComponent(`${groupNodeId}->${run.worker_id}:${linkKind}`)}`,
      source: groupNodeId,
      target: materializationWorkerNodeId(run.worker_id),
      material_id: null,
      materialization_group: group,
      kind: linkKind === 'resource' ? 'materialization-resource' : 'materialization-inferred',
    })
  }

  activeMaterialization.worker_runs.forEach((run) => {
    const ownerNodeId = resolveMaterializationOwnerNodeId(graph, run)
    const runNodeId = materializationWorkerNodeId(run.worker_id)
    pushNode({
      id: runNodeId,
      kind: 'materialization-worker',
      run,
      ownerNodeId,
      width: MATERIALIZATION_WORKER_NODE_WIDTH,
      height: MATERIALIZATION_WORKER_NODE_HEIGHT,
    })
    if (ownerNodeId) {
      pushEdge({
        id: `mowner:${ownerNodeId}->${runNodeId}`,
        source: ownerNodeId,
        target: runNodeId,
        material_id: null,
        kind: 'materialization-owner',
      })
    }

    run.material_io_links.forEach((link, index) => {
      if (link.material_id && materialIds.has(link.material_id)) {
        const materialId = materialNodeId(link.material_id)
        const source = link.direction === 'write' ? runNodeId : materialId
        const target = link.direction === 'write' ? materialId : runNodeId
        addLink(run, link, 'declared', index, source, target, ownerNodeId)
        return
      }
      const linkNodeId = materializationLinkNodeId(run, 'declared', index, link)
      pushNode({
        id: linkNodeId,
        kind: 'materialization-link',
        run,
        link,
        linkKind: 'declared',
        ownerNodeId,
        width: MATERIALIZATION_LINK_NODE_WIDTH,
        height: MATERIALIZATION_LINK_NODE_HEIGHT,
      })
      const source = link.direction === 'write' ? runNodeId : linkNodeId
      const target = link.direction === 'write' ? linkNodeId : runNodeId
      addLink(run, link, 'declared', index, source, target, ownerNodeId)
    })

    run.produced_content_materials.forEach((link, index) => {
      const linkNodeId = materializationLinkNodeId(run, 'generated', index, link)
      pushNode({
        id: linkNodeId,
        kind: 'materialization-link',
        run,
        link,
        linkKind: 'generated',
        ownerNodeId,
        width: MATERIALIZATION_LINK_NODE_WIDTH,
        height: MATERIALIZATION_LINK_NODE_HEIGHT,
      })
      addLink(run, link, 'generated', index, runNodeId, linkNodeId, ownerNodeId)
    })

    if (run.resource_material_links.length > MATERIALIZATION_LINK_GROUP_THRESHOLD) {
      addLinkGroup(run, 'resource', run.resource_material_links, ownerNodeId)
    } else {
      run.resource_material_links.forEach((link, index) => {
        const linkNodeId = materializationLinkNodeId(run, 'resource', index, link)
        pushNode({
          id: linkNodeId,
          kind: 'materialization-link',
          run,
          link,
          linkKind: 'resource',
          ownerNodeId,
          width: MATERIALIZATION_LINK_NODE_WIDTH,
          height: MATERIALIZATION_LINK_NODE_HEIGHT,
        })
        addLink(run, link, 'resource', index, linkNodeId, runNodeId, ownerNodeId)
      })
    }

    if (run.inferred_material_read_links.length > MATERIALIZATION_LINK_GROUP_THRESHOLD) {
      addLinkGroup(run, 'inferred', run.inferred_material_read_links, ownerNodeId)
    } else {
      run.inferred_material_read_links.forEach((link, index) => {
        if (link.material_id && materialIds.has(link.material_id)) {
          addLink(run, link, 'inferred', index, materialNodeId(link.material_id), runNodeId, ownerNodeId)
          return
        }
        const linkNodeId = materializationLinkNodeId(run, 'inferred', index, link)
        pushNode({
          id: linkNodeId,
          kind: 'materialization-link',
          run,
          link,
          linkKind: 'inferred',
          ownerNodeId,
          width: MATERIALIZATION_LINK_NODE_WIDTH,
          height: MATERIALIZATION_LINK_NODE_HEIGHT,
        })
        addLink(run, link, 'inferred', index, linkNodeId, runNodeId, ownerNodeId)
      })
    }
  })

  return { nodes, edges }
}

function buildMaterialVisualGraph(graph: TeamGraphData, resourceHints: TeamResourceHint[] = [], materialization: TeamBuilderMaterialization | null = null): { nodes: TeamVisualNode[]; edges: TeamVisualEdge[] } {
  const workerIds = new Set(graph.nodes.map((node) => node.id))
  const materializationLayer = buildMaterializationVisualLayer(graph, materialization)
  const nodes: TeamVisualNode[] = [
    ...graph.nodes.map((worker) => ({
      id: worker.id,
      kind: 'worker' as const,
      worker,
      width: GRAPH_NODE_WIDTH,
      height: GRAPH_NODE_HEIGHT,
    })),
    ...graph.materials.map((material) => ({
      id: materialNodeId(material.id),
      kind: 'material' as const,
      material,
      width: MATERIAL_NODE_WIDTH,
      height: MATERIAL_NODE_HEIGHT,
    })),
    ...resourceHints.map((resource) => ({
      id: resourceNodeId(resource.id),
      kind: 'resource' as const,
      resource,
      width: RESOURCE_NODE_WIDTH,
      height: RESOURCE_NODE_HEIGHT,
    })),
    ...materializationLayer.nodes,
  ]
  const edges: TeamVisualEdge[] = []
  const seen = new Set<string>()
  const pushEdge = (edge: TeamVisualEdge) => {
    if (seen.has(edge.id)) return
    seen.add(edge.id)
    edges.push(edge)
  }

  graph.materials.forEach((material) => {
    const materialId = materialNodeId(material.id)
    material.producers.filter((producer) => workerIds.has(producer)).forEach((producer) => {
      pushEdge({
        id: `flow:${producer}->${material.id}`,
        source: producer,
        target: materialId,
        material_id: material.id,
        kind: 'producer',
      })
    })
    material.consumers.filter((consumer) => workerIds.has(consumer)).forEach((consumer) => {
      pushEdge({
        id: `flow:${material.id}->${consumer}`,
        source: materialId,
        target: consumer,
        material_id: material.id,
        kind: 'consumer',
      })
    })
  })

  resourceHints.forEach((resource) => {
    const resourceId = resourceNodeId(resource.id)
    resource.workers.filter((workerId) => workerIds.has(workerId)).forEach((workerId) => {
      pushEdge({
        id: `resource:${resource.id}->${workerId}`,
        source: resourceId,
        target: workerId,
        material_id: null,
        resource_id: resource.id,
        kind: 'resource',
      })
    })
  })

  materializationLayer.edges.forEach(pushEdge)

  return { nodes, edges }
}

function computeSimplePositions(visualNodes: TeamVisualNode[], visualEdges: TeamVisualEdge[]): Map<string, LayoutPoint> {
  const levels = new Map<string, number>()
  const incoming = new Map<string, number>()
  const nodeIds = new Set(visualNodes.map((node) => node.id))

  // ── 先用迭代 DFS 找回边(反馈环), 分层时跳过它们。否则环会把节点 level 越推越大、甩到画外
  //    (mechanics_evolver 的 compile_verify→code_patcher 反馈边曾把节点扔到 x≈10000, 整张图看着空了)。
  const SEP = ' '
  const adj = new Map<string, string[]>()
  visualNodes.forEach((n) => adj.set(n.id, []))
  visualEdges.forEach((e) => { if (nodeIds.has(e.source) && nodeIds.has(e.target)) adj.get(e.source)!.push(e.target) })
  const dfsState = new Map<string, number>()  // 1=在栈中, 2=完成
  const backEdges = new Set<string>()
  const visit = (start: string) => {
    const stack: Array<{ id: string; i: number }> = [{ id: start, i: 0 }]
    dfsState.set(start, 1)
    while (stack.length) {
      const frame = stack[stack.length - 1]
      const kids = adj.get(frame.id) || []
      if (frame.i < kids.length) {
        const v = kids[frame.i]
        frame.i += 1
        const st = dfsState.get(v) || 0
        if (st === 1) backEdges.add(frame.id + SEP + v)
        else if (st === 0) { dfsState.set(v, 1); stack.push({ id: v, i: 0 }) }
      } else { dfsState.set(frame.id, 2); stack.pop() }
    }
  }
  visualNodes.forEach((n) => { if ((dfsState.get(n.id) || 0) === 0) visit(n.id) })
  const forwardEdges = visualEdges.filter((e) =>
    nodeIds.has(e.source) && nodeIds.has(e.target) && !backEdges.has(e.source + SEP + e.target))

  visualNodes.forEach((node) => incoming.set(node.id, 0))
  forwardEdges.forEach((edge) => incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1))
  visualNodes
    .filter((node) => (incoming.get(node.id) || 0) === 0)
    .forEach((node) => levels.set(node.id, 0))

  for (let i = 0; i < visualNodes.length; i += 1) {
    let changed = false
    forwardEdges.forEach((edge) => {
      const sourceLevel = levels.get(edge.source)
      if (sourceLevel === undefined) return
      const next = sourceLevel + 1
      const current = levels.get(edge.target)
      if (current === undefined || next > current) {
        levels.set(edge.target, next)
        changed = true
      }
    })
    if (!changed) break
  }

  // 去环后仍没分到层的(只被回边指到的孤点)归到第 0 层, 别再各自甩一列。
  visualNodes.forEach((node) => { if (!levels.has(node.id)) levels.set(node.id, 0) })

  // ── 层内排序: barycenter 交叉最小化(Sugiyama 第3步)。之前按 id 字母序排, 完全不管连线 → 大量交叉。
  const succ = new Map<string, string[]>()
  const pred = new Map<string, string[]>()
  visualNodes.forEach((n) => { succ.set(n.id, []); pred.set(n.id, []) })
  visualEdges.forEach((e) => {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) return
    if (levels.get(e.source) === levels.get(e.target)) return  // 同层边不参与跨层排序
    succ.get(e.source)!.push(e.target)
    pred.get(e.target)!.push(e.source)
  })

  const maxLevel = Math.max(0, ...Array.from(levels.values()))
  const order: string[][] = Array.from({ length: maxLevel + 1 }, () => [])
  ;[...visualNodes]
    .sort((a, b) => a.id.localeCompare(b.id))
    .forEach((n) => order[levels.get(n.id) || 0].push(n.id))
  const indexIn = (layer: string[]) => { const m = new Map<string, number>(); layer.forEach((id, i) => m.set(id, i)); return m }

  for (let iter = 0; iter < 6; iter += 1) {
    const downward = iter % 2 === 0  // 偶数轮向下扫(看上层 pred), 奇数轮向上扫(看下层 succ)
    const seq = downward ? [...Array(maxLevel + 1).keys()] : [...Array(maxLevel + 1).keys()].reverse()
    for (const lv of seq) {
      const adjLv = downward ? lv - 1 : lv + 1
      if (adjLv < 0 || adjLv > maxLevel) continue
      const adjIndex = indexIn(order[adjLv])
      const neighbors = downward ? pred : succ
      const bary = new Map<string, number>()
      order[lv].forEach((id, i) => {
        const ns = neighbors.get(id) || []
        const vals = ns.map((m) => adjIndex.get(m)).filter((v): v is number => v !== undefined)
        bary.set(id, vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : i)  // 无邻居保持原位
      })
      order[lv] = [...order[lv]].sort((a, b) => (bary.get(a)! - bary.get(b)!) || 0)
    }
  }

  // ── 落坐标: 每层垂直居中, 看着更平衡 ──
  const positions = new Map<string, LayoutPoint>()
  order.forEach((layer, lv) => {
    const offset = -((layer.length - 1) / 2) * GRAPH_ROW_GAP
    layer.forEach((id, row) => {
      positions.set(id, { x: lv * GRAPH_COLUMN_GAP, y: offset + row * GRAPH_ROW_GAP })
    })
  })
  return positions
}

interface ElkLayoutResult {
  positions: Map<string, LayoutPoint>
  /** edgeId -> elk 算好的正交走线折点(已归一化到节点同一坐标系)。喂给自定义边按拐点画, 避免穿插/撞节点。 */
  routes: Map<string, LayoutPoint[]>
}

async function computeElkPositions(graph: TeamGraphData, resourceHints: TeamResourceHint[], materialization: TeamBuilderMaterialization | null): Promise<ElkLayoutResult> {
  const visual = buildMaterialVisualGraph(graph, resourceHints, materialization)
  const fallback = (): ElkLayoutResult => ({ positions: computeSimplePositions(visual.nodes, visual.edges), routes: new Map<string, LayoutPoint[]>() })
  try {
    const g = new dagre.graphlib.Graph({ multigraph: true })
    g.setGraph({ rankdir: 'LR', nodesep: 72, ranksep: 140, edgesep: 28, marginx: 24, marginy: 24 })
    g.setDefaultEdgeLabel(() => ({}))
    const nodeIds = new Set(visual.nodes.map((n) => n.id))
    visual.nodes.forEach((n) => g.setNode(n.id, { width: n.width, height: n.height }))
    const valid = visual.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
    valid.forEach((e) => g.setEdge(e.source, e.target, {}, e.id))
    dagre.layout(g)
    const positions = new Map<string, LayoutPoint>()
    visual.nodes.forEach((n) => {
      const dn = g.node(n.id) as { x?: number; y?: number } | undefined
      if (dn && typeof dn.x === 'number' && typeof dn.y === 'number') {
        positions.set(n.id, { x: dn.x - n.width / 2, y: dn.y - n.height / 2 })  // dagre 给中心坐标, ReactFlow 要左上角
      }
    })
    if (positions.size === 0) return fallback()
    // dagre 同时算好了每条边绕节点的折线(长边自动穿 rank 间空隙)。坐标系与节点中心一致, 直接给自定义边用。
    const routes = new Map<string, LayoutPoint[]>()
    valid.forEach((e) => {
      const de = g.edge(e.source, e.target, e.id) as { points?: Array<{ x: number; y: number }> } | undefined
      const pts = de?.points
      if (pts && pts.length >= 2) routes.set(e.id, pts.map((p) => ({ x: p.x, y: p.y })))
    })
    return { positions, routes }
  } catch (error) {
    console.warn('team graph: dagre 布局失败, 退回简单分层', error)
    return fallback()
  }
}

// ── 自定义边: 按 elk 算好的正交折线画(端点吸附到 ReactFlow 句柄, 拐角微圆角更成熟) ──
const ROUTE_CORNER = 5

function _segLen(a: LayoutPoint, b: LayoutPoint): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function _toward(from: LayoutPoint, to: LayoutPoint, d: number): LayoutPoint {
  const len = _segLen(from, to) || 1
  return { x: from.x + (to.x - from.x) * (d / len), y: from.y + (to.y - from.y) * (d / len) }
}

/** 折线转 SVG path, 每个拐点加小圆角(二次贝塞尔), 看着更顺。 */
function roundedRoutePath(pts: LayoutPoint[]): string {
  if (pts.length < 2) return ''
  if (pts.length === 2) return `M ${pts[0].x},${pts[0].y} L ${pts[1].x},${pts[1].y}`
  let d = `M ${pts[0].x},${pts[0].y}`
  for (let i = 1; i < pts.length - 1; i++) {
    const prev = pts[i - 1], cur = pts[i], next = pts[i + 1]
    const a = _toward(cur, prev, Math.min(ROUTE_CORNER, _segLen(prev, cur) / 2))
    const b = _toward(cur, next, Math.min(ROUTE_CORNER, _segLen(next, cur) / 2))
    d += ` L ${a.x},${a.y} Q ${cur.x},${cur.y} ${b.x},${b.y}`
  }
  const last = pts[pts.length - 1]
  d += ` L ${last.x},${last.y}`
  return d
}

function ElkRoutedEdge({ sourceX, sourceY, targetX, targetY, data, style, markerEnd, label, labelStyle, labelBgStyle }: any) {
  const routePts: LayoutPoint[] = (data?.points as LayoutPoint[]) || []
  // 有 dagre 走线点就用它(带拐点 + 避重叠, 静态最好看)。没有 = 该边的节点被拖动过, 边构建时
  // 已清掉 points, 这里用实时句柄坐标直连跟手。"是否被拖动"由上层 draggedIds 状态判定,
  // 不靠坐标比对(dagre 端点和 ReactFlow 句柄坐标系不一致, 比对会全判成已拖动 → 全直线)。
  const pts: LayoutPoint[] = routePts.length >= 2 ? routePts : [{ x: sourceX, y: sourceY }, { x: targetX, y: targetY }]
  const path = roundedRoutePath(pts)
  const mid = pts[Math.floor(pts.length / 2)] || { x: (sourceX + targetX) / 2, y: (sourceY + targetY) / 2 }
  const labelX = mid.x
  const labelY = mid.y
  return (
    <BaseEdge
      path={path}
      style={style}
      markerEnd={markerEnd}
      label={label}
      labelX={labelX}
      labelY={labelY}
      labelStyle={labelStyle}
      labelBgStyle={labelBgStyle}
      labelBgPadding={[4, 2]}
      labelBgBorderRadius={3}
    />
  )
}

const TEAM_EDGE_TYPES = { elk: ElkRoutedEdge }

function DefinitionButton({ definition, title, compact = false }: { definition: TeamDefinitionRef | null; title: string; compact?: boolean }) {
  const openTab = usePanels((state) => state.openTab)
  if (!definition) {
    // 节点卡(compact)不再每卡一句占位话 —— 缩为一枚空心点(缺入口才显示,title/aria 兜底);
    // 侧栏键值行保留文字形态(它是详情语境)。
    return compact
      ? <span title="暂无定义入口" aria-label="暂无定义入口" style={{ width: 8, height: 8, borderRadius: '50%', border: `1.5px solid ${colors.border}`, display: 'inline-block', flexShrink: 0 }} />
      : <span style={{ color: colors.textFaint, fontSize: 13 }}>暂无定义入口</span>
  }
  return (
    <button
      type="button"
      title={`${definition.label}: ${definition.file_path}`}
      onClick={(event) => {
        event.stopPropagation()
        openTab(
          { type: definition.entity_type, id: definition.entity_id },
          title,
          definition.entity_type === 'worker' ? 'design' : undefined,
        )
      }}
      style={{
        border: `1px solid ${colors.border}`,
        background: 'var(--fp-bp-paper-2)',
        color: colors.textSecondary,
        height: compact ? 22 : 26,
        padding: compact ? '0 7px' : '0 9px',
        borderRadius: 3,
        fontSize: 12,
        cursor: 'pointer',
        fontFamily: fonts.mono,
        flexShrink: 0,
      }}
    >
      定义
    </button>
  )
}

function TeamGraphCanvas({ graph, selectedItemId, onSelectItem, runDetail, doctorHealth, materialization }: {
  graph: TeamGraphData
  selectedItemId: string | null
  onSelectItem: (itemId: string | null) => void
  runDetail: TeamRunDetail | null
  doctorHealth: TeamDoctorHealth | null
  materialization: TeamBuilderMaterialization | null
}) {
  const resourceHints = useMemo(() => inferResourceHints(graph, runDetail, materialization), [graph, runDetail, materialization])
  const resourceKey = resourceHints.map((hint) => `${hint.id}:${hint.workers.join(',')}:${hint.confidence}`).join('|')
  const materializationForGraph = materializationLayerVisible(graph, materialization) ? materialization : null
  const materializationKey = materializationForGraph ? `${materializationForGraph.run_id}:${materializationForGraph.counts.generated_candidates}:${materializationForGraph.counts.resource_candidates}` : 'no-materialization'
  const layoutKey = `${graph.team_id}:${graph.selected_builder}:${runDetail?.trace_id || 'static'}:${resourceKey}:${materializationKey}`
  const [elkLayout, setElkLayout] = useState<{ key: string; positions: Map<string, LayoutPoint>; routes: Map<string, LayoutPoint[]> } | null>(null)
  // 被用户拖动过的节点 id 集合。这些节点的边丢掉 dagre 走线点 → 改直连跟手; 其余边保持 dagre 走线。
  const [draggedIds, setDraggedIds] = useState<Set<string>>(() => new Set())
  useEffect(() => {
    let cancelled = false
    computeElkPositions(graph, resourceHints, materializationForGraph)
      .then((result) => {
        if (!cancelled && result.positions.size > 0) setElkLayout({ key: layoutKey, positions: result.positions, routes: result.routes })
      })
      .catch((error) => {
        console.warn('team graph ELK layout failed, fallback to simple layout', error)
        if (!cancelled) setElkLayout(null)
      })
    return () => {
      cancelled = true
    }
  }, [graph, resourceHints, materializationForGraph, layoutKey])

  const elkPositions = elkLayout?.key === layoutKey ? elkLayout.positions : null
  const elkRoutes = elkLayout?.key === layoutKey ? elkLayout.routes : null
  const layoutResult = useMemo((): { nodes: FlowNode[]; edges: FlowEdge[] } => {
    const visual = buildMaterialVisualGraph(graph, resourceHints, materializationForGraph)
    const positions = elkPositions || computeSimplePositions(visual.nodes, visual.edges)
    const activeNodeIds = new Set(runDetail?.active_nodes || [])
    const activeMaterialIds = new Set<string>()
    ;(runDetail?.material_observations || []).forEach((observation) => {
      observation.inputs.forEach((materialId) => activeMaterialIds.add(materialId))
      observation.outputs.forEach((materialId) => activeMaterialIds.add(materialId))
    })
    const statuses = new Map((runDetail?.node_statuses || []).map((status) => [status.node_id, status]))
    const hasRunOverlay = !!runDetail && runDetail.summary.event_count > 0
    const doctorByNode = new Map<string, TeamDoctorFinding[]>()
    const doctorByMaterial = new Map<string, TeamDoctorFinding[]>()
    ;(doctorHealth?.findings || []).forEach((finding) => {
      finding.node_ids.forEach((nodeId) => {
        if (!doctorByNode.has(nodeId)) doctorByNode.set(nodeId, [])
        doctorByNode.get(nodeId)!.push(finding)
      })
      finding.material_ids.forEach((materialId) => {
        if (!doctorByMaterial.has(materialId)) doctorByMaterial.set(materialId, [])
        doctorByMaterial.get(materialId)!.push(finding)
      })
    })
    const ordered = [...visual.nodes].sort((a, b) => {
      const aPos = positions.get(a.id) || { x: 0, y: 0 }
      const bPos = positions.get(b.id) || { x: 0, y: 0 }
      return aPos.x - bPos.x || aPos.y - bPos.y || a.id.localeCompare(b.id)
    })

    const nodes: FlowNode[] = ordered.map((visualNode) => {
      const position = positions.get(visualNode.id) || { x: 0, y: 0 }
      const selected = selectedItemId === visualNode.id

      if (visualNode.kind === 'materialization-worker') {
        const run = visualNode.run
        const borderColor = selected ? 'var(--fp-accent)' : run.status === 'succeeded' ? '#9db8ff' : '#ff7b7b'
        return {
          id: visualNode.id,
          position,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: {
            label: (
              <div data-team-materialization-node data-team-materialization-worker-node style={{ width: '100%', maxWidth: '100%', height: '100%', minWidth: 0, overflow: 'hidden', fontFamily: fonts.ui }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                  <div title={`${zhWorkerName(run.worker_id)} / ${run.worker_id}`} style={{ fontSize: 15, fontWeight: 650, color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {zhWorkerName(run.worker_id)}
                  </div>
                  <span style={{ fontSize: 13, color: borderColor, whiteSpace: 'nowrap' }}>{zhGenerationStatus(run.status)}</span>
                </div>
                <div style={{ marginTop: 4, fontFamily: fonts.mono, fontSize: 13, color: colors.textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {run.worker_id}
                </div>
                <div style={{ marginTop: 7, color: colors.textSecondary, fontSize: 13, lineHeight: 1.38, ...lineClamp(2) }}>
                  外部代理实战生成记录，连接到 TeamBuilder 的代码生成入口。
                </div>
                <div style={{ marginTop: 9, display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                  <span style={S.pill}>声明 {run.material_io_links.length}</span>
                  <span style={S.pill}>生成 {run.produced_content_materials.length}</span>
                  <span style={S.pill}>线索 {run.resource_material_links.length}</span>
                </div>
              </div>
            ),
          },
          style: {
            width: MATERIALIZATION_WORKER_NODE_WIDTH,
            height: MATERIALIZATION_WORKER_NODE_HEIGHT,
            borderRadius: 7,
            border: `${selected ? 2 : 1}px solid ${borderColor}`,
            background: '#10182a',
            color: colors.text,
            boxShadow: selected ? '0 0 0 1px rgba(228,242,34,.35)' : 'none',
            overflow: 'hidden',
            padding: 10,
            boxSizing: 'border-box',
          },
        }
      }

      if (visualNode.kind === 'materialization-link') {
        const link = visualNode.link
        const tone = linkTone(link)
        const target = materializationTargetText(link)
        const title = materializationShortTarget(link)
        const summary = materializationHumanSummary(link)
        const linkBackground = visualNode.linkKind === 'resource'
          ? '#18130a'
          : visualNode.linkKind === 'generated'
            ? '#101526'
            : visualNode.linkKind === 'inferred'
              ? '#0d1f16'
              : '#11151a'
        return {
          id: visualNode.id,
          position,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: {
            label: (
              <div data-team-materialization-node style={{ width: '100%', maxWidth: '100%', height: '100%', minWidth: 0, overflow: 'hidden', fontFamily: fonts.ui }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                  <div style={{ fontSize: 13, color: tone, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {materializationLinkKindLabel(visualNode.linkKind, link)}
                  </div>
                  <span style={{ fontSize: 13, color: colors.textMuted, whiteSpace: 'nowrap' }}>{zhConfidence(link.confidence)}</span>
                </div>
                <div title={`${title} / ${target}`} style={{ marginTop: 5, fontSize: 15, fontWeight: 650, color: colors.text, ...lineClamp(2) }}>
                  {title}
                </div>
                <div style={{ marginTop: 5, fontSize: 13, color: colors.textSecondary, lineHeight: 1.35, ...lineClamp(2) }}>
                  {summary}
                </div>
                <div style={{ marginTop: 8, display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                  <span style={S.pill}>{zhRegistrationStatus(link.registration_status)}</span>
                  {link.candidate_kind && <span style={S.pill}>{zhCandidateKind(link.candidate_kind)}</span>}
                  {link.resource_kind && <span style={S.pill}>{link.resource_kind}</span>}
                  {typeof link.bytes === 'number' && <span style={S.pill}>{link.bytes} 字节</span>}
                </div>
              </div>
            ),
          },
          style: {
            width: MATERIALIZATION_LINK_NODE_WIDTH,
            height: MATERIALIZATION_LINK_NODE_HEIGHT,
            borderRadius: 7,
            border: `${selected ? 2 : 1}px ${link.registration_status === 'generated-candidate' ? 'solid' : 'dashed'} ${selected ? 'var(--fp-accent)' : tone}`,
            background: linkBackground,
            color: colors.text,
            boxShadow: selected ? '0 0 0 1px rgba(228,242,34,.35)' : 'none',
            overflow: 'hidden',
            padding: 10,
            boxSizing: 'border-box',
          },
        }
      }

      if (visualNode.kind === 'materialization-link-group') {
        const group = visualNode.group
        const tone = group.linkKind === 'resource' ? '#d09a45' : '#78d98b'
        const background = group.linkKind === 'resource' ? '#18130a' : '#0d1f16'
        return {
          id: visualNode.id,
          position,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: {
            label: (
              <div data-team-materialization-node data-team-materialization-group-node style={{ width: '100%', maxWidth: '100%', height: '100%', minWidth: 0, overflow: 'hidden', fontFamily: fonts.ui }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                  <div style={{ fontSize: 13, color: tone, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {group.linkKind === 'resource' ? '读取线索组' : '确认读取组'}
                  </div>
                  <span style={{ fontSize: 13, color: colors.textMuted, whiteSpace: 'nowrap' }}>{group.count} 条</span>
                </div>
                <div title={group.label} style={{ marginTop: 5, fontSize: 15, fontWeight: 650, color: colors.text, ...lineClamp(2) }}>
                  {group.label}
                </div>
                <div style={{ marginTop: 5, fontSize: 13, color: colors.textSecondary, lineHeight: 1.35, ...lineClamp(2) }}>
                  {group.summary}
                </div>
                <div style={{ marginTop: 8, display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                  <span style={S.pill}>{group.linkKind === 'resource' ? '待确认' : '已推断'}</span>
                  <span style={S.pill}>样例 {group.sample_targets.length}</span>
                  <span style={S.pill}>material {group.matched_material_ids.length}</span>
                </div>
              </div>
            ),
          },
          style: {
            width: MATERIALIZATION_LINK_GROUP_NODE_WIDTH,
            height: MATERIALIZATION_LINK_GROUP_NODE_HEIGHT,
            borderRadius: 7,
            border: `${selected ? 2 : 1}px dashed ${selected ? 'var(--fp-accent)' : tone}`,
            background,
            color: colors.text,
            boxShadow: selected ? '0 0 0 1px rgba(228,242,34,.35)' : 'none',
            overflow: 'hidden',
            padding: 10,
            boxSizing: 'border-box',
          },
        }
      }

      if (visualNode.kind === 'resource') {
        const resource = visualNode.resource
        const meta = RESOURCE_META[resource.id]
        const activeInRun = resource.workers.some((workerId) => activeNodeIds.has(workerId))
        const opacity = hasRunOverlay && !activeInRun ? 0.58 : 1
        return {
          id: visualNode.id,
          position,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: {
            label: (
              <div data-team-resource-node style={{ width: '100%', maxWidth: '100%', height: '100%', minWidth: 0, overflow: 'hidden', fontFamily: fonts.ui }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                  <div style={{ fontSize: 15, fontWeight: 650, color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {resource.label}
                  </div>
                  <span style={{ fontSize: 13, color: meta.tone, whiteSpace: 'nowrap' }}>{resource.confidence}</span>
                </div>
                <div style={{ marginTop: 6, color: colors.textSecondary, fontSize: 13, lineHeight: 1.4, ...lineClamp(2) }}>
                  {resource.description}
                </div>
                <div style={{ marginTop: 8, color: colors.textMuted, fontSize: 13, lineHeight: 1.4 }}>
                  关联 {resource.workers.length} 个 worker · {resource.evidence.length} 条可查看线索
                </div>
                {resource.evidence[0] && (
                  <div style={{ marginTop: 4, color: colors.textFaint, fontSize: 13, lineHeight: 1.35, ...lineClamp(2) }}>
                    例：{resource.evidence[0]}
                  </div>
                )}
              </div>
            ),
          },
          style: {
            width: RESOURCE_NODE_WIDTH,
            height: RESOURCE_NODE_HEIGHT,
            borderRadius: 7,
            border: `1px dashed ${meta.tone}`,
            background: meta.background,
            color: colors.text,
            opacity,
            overflow: 'hidden',
            padding: 10,
            boxSizing: 'border-box',
          },
        }
      }

      if (visualNode.kind === 'material') {
        const material = visualNode.material
        const activeInRun = activeMaterialIds.has(material.id)
        const doctorFinding = highestDoctorFinding(doctorByMaterial.get(material.id) || [])
        const doctor = doctorTone(doctorFinding?.level)
        const borderColor = selected ? 'var(--fp-accent)' : doctor ? doctor.border : activeInRun ? '#36c275' : '#7b6b43'
        const background = doctor ? doctor.background : activeInRun ? '#102416' : '#16130d'
        const opacity = hasRunOverlay && !activeInRun && !doctor ? 0.5 : 1
        const title = zhMaterialName(material.id)

        return {
          id: visualNode.id,
          position,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: {
            label: (
            <div data-team-node-content style={{ width: '100%', maxWidth: '100%', height: '100%', minWidth: 0, overflow: 'hidden', fontFamily: fonts.ui }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                  <div style={{ fontSize: 15, fontWeight: 650, color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {title}
                  </div>
                  <DefinitionButton definition={material.definition} title={title} compact />
                </div>
                <div style={{ marginTop: 4, fontFamily: fonts.mono, fontSize: 13, color: colors.textMuted, overflowWrap: 'anywhere' }}>
                  {material.id}
                </div>
                <div style={{ marginTop: 8, color: colors.textSecondary, fontSize: 13, lineHeight: 1.45 }}>
                  {material.producers.length || '外部'} 个生产者 / {material.consumers.length || '最终'} 个消费者
                </div>
              </div>
            ),
          },
          style: {
            width: MATERIAL_NODE_WIDTH,
            height: MATERIAL_NODE_HEIGHT,
            borderRadius: 7,
            border: `${selected || activeInRun ? 2 : 1}px solid ${borderColor}`,
            background,
            color: colors.text,
            boxShadow: selected ? '0 0 0 1px rgba(228,242,34,.35)' : 'none',
            opacity,
            overflow: 'hidden',
            padding: 10,
            boxSizing: 'border-box',
          },
        }
      }

      const node = visualNode.worker
      const tone = nodeColor(node)
      const nodeStatus = statuses.get(node.id)
      const activeInRun = activeNodeIds.has(node.id)
      const failedInRun = hasFailedVerdict(nodeStatus)
      const doctorFinding = highestDoctorFinding(doctorByNode.get(node.id) || [])
      const doctor = doctorTone(doctorFinding?.level)
      const borderColor = selected ? 'var(--fp-accent)' : doctor ? doctor.border : failedInRun ? '#e05252' : activeInRun ? '#36c275' : tone.border
      const background = doctor ? doctor.background : failedInRun ? '#2a1111' : activeInRun ? '#0f2a1a' : tone.background
      const opacity = hasRunOverlay && !activeInRun && !doctor ? 0.42 : 1
      const title = zhWorkerName(node)
      const inputText = node.format_in.map(zhMaterialName).join('、') || '-'
      const outputText = node.format_out ? zhMaterialName(node.format_out) : '-'
      const summaryText = workerZhDescription(node)

      return {
        id: node.id,
        position,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        data: {
          label: (
            <div data-team-node-content style={{ width: '100%', maxWidth: '100%', height: '100%', minWidth: 0, overflow: 'hidden', fontFamily: fonts.ui }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                <div title={`${title} / ${node.id}`} style={{ fontSize: 15, fontWeight: 650, color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {title}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
                  <span style={{ fontSize: 13, color: node.validator_kind === 'hard' ? '#72d68a' : '#b2a7ff', whiteSpace: 'nowrap' }}>
                    {doctor ? doctor.label : zhValidatorKind(node.validator_kind || node.kind)}
                  </span>
                  <DefinitionButton definition={node.definition} title={title} compact />
                </div>
              </div>
              <div style={{ marginTop: 4, fontFamily: fonts.mono, fontSize: 13, color: colors.textMuted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {node.label && node.label !== node.id ? `${node.id} / ${node.label}` : node.id}
              </div>
              <div title={summaryText} style={{ marginTop: 6, color: colors.textSecondary, fontSize: 13, lineHeight: 1.38, ...lineClamp(1) }}>
                {summaryText}
              </div>
              <div style={{ marginTop: 9, display: 'grid', gridTemplateColumns: '38px minmax(0, 1fr)', columnGap: 7, rowGap: 6, alignItems: 'start' }}>
                <div style={{ fontSize: 13, color: colors.textFaint }}>输入</div>
                <div title={(node.format_in || []).join(', ')} style={{ fontFamily: fonts.mono, fontSize: 13, lineHeight: 1.38, color: colors.textSecondary, ...lineClamp(2) }}>
                  {inputText}
                </div>
                <div style={{ fontSize: 13, color: colors.textFaint }}>输出</div>
                <div title={node.format_out || ''} style={{ fontFamily: fonts.mono, fontSize: 13, lineHeight: 1.38, color: colors.textSecondary, ...lineClamp(2) }}>
                  {outputText}
                </div>
              </div>
            </div>
          ),
        },
        style: {
          width: GRAPH_NODE_WIDTH,
          height: GRAPH_NODE_HEIGHT,
          borderRadius: 8,
          border: `${selected || activeInRun || failedInRun ? 2 : 1}px solid ${borderColor}`,
          background,
          color: colors.text,
          boxShadow: selected ? '0 0 0 1px rgba(228,242,34,.35)' : 'none',
          opacity,
          overflow: 'hidden',
          padding: 10,
          boxSizing: 'border-box',
        },
      }
    })

    const edges: FlowEdge[] = visual.edges.map((edge) => {
      const sourceItem = parseGraphItemId(edge.source)
      const targetItem = parseGraphItemId(edge.target)
      const resourceMeta = edge.resource_id ? RESOURCE_META[edge.resource_id] : null
      const materializationEdge = edge.kind.startsWith('materialization-')
      const activeEdge = hasRunOverlay && (
        (edge.material_id ? activeMaterialIds.has(edge.material_id) : false)
        || (sourceItem.kind === 'worker' && activeNodeIds.has(sourceItem.id))
        || (targetItem.kind === 'worker' && activeNodeIds.has(targetItem.id))
      )
      const selectedEdge = selectedItemId === edge.source || selectedItemId === edge.target
      const doctorFinding = edge.material_id ? highestDoctorFinding(doctorByMaterial.get(edge.material_id) || []) : undefined
      const doctor = doctorTone(doctorFinding?.level)
      const stroke = materializationEdge
        ? edge.kind === 'materialization-resource'
          ? '#d09a45'
          : edge.kind === 'materialization-produced'
            ? '#9db8ff'
            : edge.kind === 'materialization-owner'
              ? '#b2a7ff'
              : '#78d98b'
        : resourceMeta ? resourceMeta.tone : doctor ? doctor.border : activeEdge ? '#36c275' : edge.kind === 'producer' ? '#7b6b43' : '#5e6ad2'
      // 被拖动节点的边: 丢掉 dagre 走线点, 让 ElkRoutedEdge 用实时句柄直连跟手; 其余边保留 dagre 走线。
      const edgeDragging = draggedIds.has(edge.source) || draggedIds.has(edge.target)
      const routePts = edgeDragging ? undefined : elkRoutes?.get(edge.id)
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        // elk 算好的避让折线优先(自定义 'elk' 边按拐点画); 没有则退回 smoothstep。
        type: routePts && routePts.length >= 2 ? 'elk' : 'smoothstep',
        data: routePts ? { points: routePts } : undefined,
        animated: activeEdge,
        label: materializationEdge ? materializationEdgeLabel(edge) : edge.kind === 'resource' ? '资源线索' : edge.kind === 'producer' ? undefined : zhMaterialName(edge.material_id),
        labelStyle: { fill: colors.textMuted, fontSize: 13, fontFamily: fonts.mono },
        labelBgStyle: { fill: '#090a0b', fillOpacity: 0.86 },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
        style: {
          stroke,
          strokeDasharray: edge.kind === 'resource' || materializationEdge ? '6 5' : undefined,
          opacity: materializationEdge ? 0.92 : selectedEdge ? 1 : hasRunOverlay && !activeEdge && !doctor ? 0.3 : 0.86,
          strokeWidth: selectedEdge || doctor || materializationEdge ? 2.2 : activeEdge ? 2 : 1.1,
        },
      }
    })

    return { nodes, edges }
  }, [graph, resourceHints, materializationForGraph, selectedItemId, runDetail, doctorHealth, elkPositions, elkRoutes, draggedIds])
  const { nodes: layoutNodes, edges } = layoutResult
  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const layoutKeyRef = useRef(layoutKey)
  const userAdjustedLayoutRef = useRef(false)
  const rfRef = useRef<any>(null)            // ReactFlow 实例(onInit 拿到), 给 elk 异步布局后 refit 用
  const fittedKeyRef = useRef<string | null>(null)

  useEffect(() => {
    setNodes((currentNodes) => {
      if (layoutKeyRef.current !== layoutKey) {
        layoutKeyRef.current = layoutKey
        userAdjustedLayoutRef.current = false
        return layoutNodes
      }
      if (!userAdjustedLayoutRef.current) return layoutNodes
      const currentPositions = new Map(currentNodes.map((node) => [node.id, node.position]))
      return layoutNodes.map((node) => ({
        ...node,
        position: currentPositions.get(node.id) || node.position,
      }))
    })
  }, [layoutKey, layoutNodes, setNodes])
  const handleNodesChange = useCallback((changes: FlowNodeChange[]) => {
    const movedIds = changes.filter((c) => c.type === 'position' && (c as any).id).map((c) => (c as any).id as string)
    if (movedIds.length) {
      userAdjustedLayoutRef.current = true
      setDraggedIds((prev) => {
        let changed = false
        const next = new Set(prev)
        movedIds.forEach((id) => { if (!next.has(id)) { next.add(id); changed = true } })
        return changed ? next : prev
      })
    }
    onNodesChange(changes)
  }, [onNodesChange])

  // 切换 team / builder 时清空拖动记录(回到全 dagre 走线)。不挂 layoutKey, 避免异步数据(运行态/物化)
  // 一到就把用户的拖动重置掉。
  useEffect(() => {
    setDraggedIds(new Set())
  }, [graph.team_id, graph.selected_builder])

  // elkjs 是异步布局: <ReactFlow fitView> 只在 init 跑一次(那时节点还在画布外), 节点到位后没再 fit,
  // 导致节点多的 team 一打开节点全在视口外 = 画布看着是空的(用户: "team 里面怎么没有东西")。
  // 关键: 必须等 elk 布局真正算完并应用(elkPositions 就绪)后再 fit; 否则会 fit 到布局前的临时坐标,
  // 然后节点弹到画外却因 fittedKeyRef 已置位不再回中(就是 restored tab 打开还是空白的根因)。
  useEffect(() => {
    if (userAdjustedLayoutRef.current || nodes.length === 0 || fittedKeyRef.current === layoutKey) return
    if (!elkPositions) return  // elk 还没算完, 等它好了本 effect 会因 elkPositions 变化再跑
    const id = window.setTimeout(() => {
      try { rfRef.current?.fitView({ padding: 0.18, duration: 200 }); fittedKeyRef.current = layoutKey } catch { /* */ }
    }, 80)
    return () => window.clearTimeout(id)
  }, [layoutKey, nodes, elkPositions])

  // 「重新整理」: 拖乱了想恢复 dagre 干净布局时点一下 —— 清拖动记录(边回到带拐点走线)+ 节点回到
  // dagre 算的位置 + 重新 fit。用的是同一套成熟 dagre 布局, 不是手搓。
  const relayout = useCallback(() => {
    userAdjustedLayoutRef.current = false
    fittedKeyRef.current = null
    setDraggedIds(new Set())
    setNodes(layoutNodes)
    window.setTimeout(() => { try { rfRef.current?.fitView({ padding: 0.18, duration: 200 }) } catch { /* */ } }, 60)
  }, [layoutNodes, setNodes])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      edgeTypes={TEAM_EDGE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.18 }}
      onInit={(inst) => { rfRef.current = inst }}
      nodesDraggable
      nodesConnectable={false}
      onNodesChange={handleNodesChange}
      onNodeClick={(_, node) => {
        onSelectItem(node.id)
      }}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="rgba(190,220,255,.16)" gap={26} />
      <MiniMap
        pannable
        zoomable
        nodeStrokeWidth={2}
        nodeColor={(node) => String(node.style?.background || '#15181d')}
        nodeStrokeColor={(node) => String(node.style?.borderColor || '#343944')}
        maskColor="rgba(9,26,62,.66)"
        style={{
          width: 132,
          height: 92,
          right: 12,
          bottom: 46,
          background: '#0d2450',
          border: `1px solid ${colors.border}`,
          borderRadius: 3,
        }}
      />
      <Controls showInteractive={false} />
      <Panel position="top-left" style={{ margin: 12 }}>
        <button
          type="button"
          onClick={relayout}
          title="重新按 dagre 整理布局(恢复带拐点走线、节点归位)"
          style={{
            border: `1px solid ${colors.border}`,
            background: 'rgba(7, 9, 11, 0.94)',
            color: colors.textSecondary,
            borderRadius: 6,
            padding: '5px 11px',
            fontSize: 13,
            cursor: 'pointer',
            fontFamily: fonts.ui,
            boxShadow: '0 8px 22px rgba(0,0,0,.32)',
          }}
        >
          重新整理
        </button>
      </Panel>
      {materializationForGraph && (
        <Panel position="top-right" style={{ margin: 14 }}>
          <div
            data-team-materialization-panel
            style={{
              width: 260,
              padding: 10,
              border: `1px solid ${colors.border}`,
              borderRadius: 7,
              background: 'rgba(7, 9, 11, 0.94)',
              color: colors.text,
              fontFamily: fonts.ui,
              boxShadow: '0 14px 36px rgba(0,0,0,.36)',
            }}
          >
            <div style={{ fontSize: 15, fontWeight: 650, color: colors.text }}>实战归因层</div>
            <div style={{ marginTop: 5, fontFamily: fonts.mono, fontSize: 13, color: colors.textMuted, overflowWrap: 'anywhere' }}>
              {materializationForGraph.run_id}
            </div>
            <div style={{ marginTop: 7, display: 'flex', gap: 5, flexWrap: 'wrap' }}>
              <span style={S.pill}>生成 {materializationForGraph.counts.generated_candidates}</span>
              <span style={S.pill}>线索 {materializationForGraph.counts.resource_candidates}</span>
              <span style={S.pill}>待处理 {materializationForGraph.counts.workers_with_missing_required}</span>
            </div>
          </div>
        </Panel>
      )}
      <FloatingSelectionDetail
        graph={graph}
        selectedItemId={selectedItemId}
        onClose={() => onSelectItem(null)}
        materialization={materializationForGraph}
        resourceHints={resourceHints}
      />
    </ReactFlow>
  )
}

function Kv({ name, value }: { name: string; value: React.ReactNode }) {
  // frostpane: 字段名压成弱灰微字, 值用可读正文 — 靠字号/对比拉开主次, 不再全平 16
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '96px minmax(0, 1fr)', gap: 8, alignItems: 'start', marginTop: 8 }}>
      <div style={{ color: colors.textFaint, fontSize: 12 }}>{name}</div>
      <div style={{ color: colors.text, fontSize: 13, minWidth: 0, overflowWrap: 'anywhere' }}>{value}</div>
    </div>
  )
}

function fileLeaf(path: string | null | undefined): string {
  if (!path) return '-'
  return path.split(/[\\/]/).pop() || path
}

function formatContractValue(value: string | string[] | null | undefined): React.ReactNode {
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => <span key={item} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhMaterialName(item)} / {item}</span>) : '-'
  }
  return value ? <span style={S.pill}>{zhMaterialName(value)} / {value}</span> : '-'
}

/** worker 的固定 prompt 块: 可复制 + 长文折叠。动态拼接段后端已用 {…} 占位。 */
function WorkerPromptBlock({ prompt }: { prompt: { attr: string; text: string } }) {
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const lines = prompt.text.split('\n')
  const long = lines.length > 8 || prompt.text.length > 600
  const shown = expanded || !long ? prompt.text : lines.slice(0, 8).join('\n')
  const copy = (event: React.MouseEvent) => {
    event.stopPropagation()
    try { void navigator.clipboard.writeText(prompt.text) } catch { /* 剪贴板不可用时静默 */ }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div style={{ display: 'grid', gap: 6, marginTop: 10, paddingTop: 10, borderTop: `1px solid ${colors.border}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span style={S.label}>提示词 · {prompt.attr}</span>
        <button
          type="button"
          onClick={copy}
          style={{ border: `1px solid ${colors.border}`, background: '#11151a', color: colors.textSecondary, height: 22, padding: '0 8px', borderRadius: 5, cursor: 'pointer', fontSize: 15 }}
        >{copied ? '已复制' : '复制'}</button>
      </div>
      <pre style={S.sourceExcerpt}>{shown}{long && !expanded ? '\n…' : ''}</pre>
      {long && (
        <button
          type="button"
          onClick={(event) => { event.stopPropagation(); setExpanded((value) => !value) }}
          style={{ justifySelf: 'start', border: 'none', background: 'transparent', color: colors.accent || '#6cb6ff', cursor: 'pointer', fontSize: 15, padding: 0 }}
        >{expanded ? '收起' : `展开全部(${lines.length} 行)`}</button>
      )}
    </div>
  )
}

function DefinitionDetail({ definition, kind, presentation = 'card' }: {
  definition: TeamDefinitionRef | null
  kind: 'worker' | 'material' | 'team'
  presentation?: 'card' | 'inline'
}) {
  if (!definition) {
    return <div style={{ color: colors.textMuted, fontSize: 13 }}>暂无定义入口。</div>
  }
  const location = definition.line_start
    ? `${fileLeaf(definition.file_path)}:${definition.line_start}${definition.line_end && definition.line_end !== definition.line_start ? `-${definition.line_end}` : ''}`
    : fileLeaf(definition.file_path)
  const containerStyle: React.CSSProperties = presentation === 'inline'
    ? {
      display: 'grid',
      gap: 6,
      marginTop: 10,
      paddingTop: 10,
      borderTop: `1px solid ${colors.border}`,
    }
    : S.definitionCard
  return (
    <div style={containerStyle}>
      <Kv name="定义文件" value={location} />
      {definition.symbol && <Kv name="定义符号" value={<span style={S.pill}>{definition.symbol}</span>} />}
      {kind === 'worker' && definition.worker && (
        <>
          <Kv name="Worker 类" value={<span style={S.pill}>{definition.worker.class_name}</span>} />
          <Kv name="输入声明" value={formatContractValue(definition.worker.format_in)} />
          <Kv name="输出声明" value={formatContractValue(definition.worker.format_out)} />
          {definition.worker.format_in_mode && <Kv name="输入模式" value={definition.worker.format_in_mode} />}
          {definition.worker.description && <Kv name="定义说明" value={definition.worker.description} />}
          {definition.worker.prompt && <WorkerPromptBlock prompt={definition.worker.prompt} />}
        </>
      )}
      {kind === 'material' && definition.material && (
        <>
          <Kv name="英文 ID" value={<span style={S.pill}>{definition.material.id}</span>} />
          <Kv name="Material 名" value={definition.material.name || '-'} />
          <Kv name="父级" value={definition.material.parent || '-'} />
          <Kv name="类别" value={definition.material.kind ? `${definition.material.kind}` : '-'} />
          {definition.material.description && <Kv name="定义说明" value={definition.material.description} />}
          {definition.material.fields.length > 0 && (
            <div style={{ display: 'grid', gap: 6, marginTop: 4 }}>
              <div style={S.label}>字段</div>
              {definition.material.fields.slice(0, 10).map((field) => (
                <div key={field.name} style={{ border: `1px solid ${colors.border}`, borderRadius: 4, padding: 7, background: colors.bg }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontFamily: fonts.mono, color: colors.text, fontSize: 13 }}>{field.name}</span>
                    <span style={{ color: field.required ? '#ffb74d' : colors.textMuted, fontSize: 13 }}>{field.required ? '必填' : '可选'}{field.type ? ` · ${field.type}` : ''}</span>
                  </div>
                  {field.description && <div style={{ marginTop: 4, color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>{field.description}</div>}
                </div>
              ))}
              {definition.material.fields.length > 10 && (
                <div style={{ color: colors.textMuted, fontSize: 13 }}>还有 {definition.material.fields.length - 10} 个字段，打开源码查看完整定义。</div>
              )}
            </div>
          )}
        </>
      )}
      {!definition.worker && !definition.material && definition.summary && <Kv name="定义说明" value={definition.summary} />}
      {presentation === 'card' && definition.source_excerpt && (
        <div>
          <div style={{ ...S.label, marginBottom: 6 }}>源码片段</div>
          <pre style={S.sourceExcerpt}>{definition.source_excerpt}</pre>
        </div>
      )}
      <Kv name="打开" value={<DefinitionButton definition={definition} title={definition.symbol || fileLeaf(definition.file_path)} />} />
    </div>
  )
}

function findMaterializationVisualNode(graph: TeamGraphData, materialization: TeamBuilderMaterialization | null, selectedItemId: string | null): Extract<TeamVisualNode, { kind: 'materialization-worker' | 'materialization-link' | 'materialization-link-group' }> | null {
  if (!selectedItemId) return null
  return buildMaterializationVisualLayer(graph, materialization).nodes.find((node) => node.id === selectedItemId) || null
}

function MaterializationEvidenceDetail({ visualNode }: {
  visualNode: Extract<TeamVisualNode, { kind: 'materialization-worker' | 'materialization-link' | 'materialization-link-group' }>
}) {
  if (visualNode.kind === 'materialization-worker') {
    const run = visualNode.run
    return (
      <>
        <Kv name="证据类型" value="外部代理 worker 实战记录" />
        <Kv name="生成状态" value={zhGenerationStatus(run.status)} />
        <Kv name="运行编号" value={<span style={S.pill}>{run.run_id}</span>} />
        <Kv name="执行器" value={`${run.provider || '-'} / ${run.parse_status || '-'}`} />
        <Kv name="关联入口" value={visualNode.ownerNodeId ? <span style={S.pill}>{zhWorkerName(visualNode.ownerNodeId)} / {visualNode.ownerNodeId}</span> : '未挂接到当前图'} />
        <Kv name="代码文件" value={run.rel_path || run.changed_files.join('、') || '-'} />
        <Kv name="声明读写" value={run.material_io_links.length} />
        <Kv name="生成文件" value={run.produced_content_materials.length} />
        <Kv name="读取线索" value={run.resource_material_links.length} />
        <Kv name="原始记录" value={<SourceDataLink href={materializationRawHref({ worker: run.worker_id })} label="原始 JSON" />} />
        <FieldAccessBlock run={run} />
      </>
    )
  }

  if (visualNode.kind === 'materialization-link-group') {
    const group = visualNode.group
    return (
      <>
        <Kv name="证据类型" value={group.linkKind === 'resource' ? '聚合后的待确认读取线索' : '聚合后的已推断读取关系'} />
        <Kv name="人读判断" value={group.summary} />
        <Kv name="判断依据" value={group.evidence_summary} />
        <Kv name="所属 worker" value={<span style={S.pill}>{zhWorkerName(visualNode.run.worker_id)} / {visualNode.run.worker_id}</span>} />
        <Kv name="合并数量" value={`${group.count} 条`} />
        {group.matched_material_ids.length > 0 && (
          <Kv
            name="命中 material"
            value={group.matched_material_ids.slice(0, 8).map((materialId) => (
              <span key={materialId} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhMaterialName(materialId)} / {materialId}</span>
            ))}
          />
        )}
        {group.sample_targets.length > 0 && (
          <Kv
            name="资料样例"
            value={group.sample_targets.slice(0, 8).map((target) => (
              <div key={target} style={{ marginBottom: 5, overflowWrap: 'anywhere' }}>{target}</div>
            ))}
          />
        )}
        <Kv
          name="怎么审阅"
          value={group.linkKind === 'resource'
            ? '这组只是工具检索或工作区读取线索。先看样例是否像真实资料，再进入原始 JSON 或消解计划查看它为什么还没有升级为事实 material 读边。'
            : '这组已经从工具读取和文件声明推断出 material 读关系。这里合并展示，避免画布被大量平行线覆盖；需要抽查时看样例和原始 JSON。'}
        />
        <Kv name="原始记录" value={<SourceDataLink href={materializationRawHref({ worker: visualNode.run.worker_id })} label="worker JSON" />} />
      </>
    )
  }

  const link = visualNode.link
  const target = materializationTargetText(link)
  return (
    <>
      <Kv name="证据类型" value={materializationLinkKindLabel(visualNode.linkKind, link)} />
      <Kv name="人读判断" value={materializationHumanSummary(link)} />
      <Kv name="判断依据" value={materializationEvidenceSummary(link)} />
      <Kv name="英文 ID" value={<span style={S.pill}>{link.material_id || target}</span>} />
      <Kv name="所属 worker" value={<span style={S.pill}>{zhWorkerName(visualNode.run.worker_id)} / {visualNode.run.worker_id}</span>} />
      <Kv name="读写方向" value={zhDirection(link.direction)} />
      <Kv name="登记状态" value={zhRegistrationStatus(link.registration_status)} />
      <Kv name="置信度" value={zhConfidence(link.confidence)} />
      {link.resource_kind && <Kv name="资源空间" value={link.resource_kind} />}
      {link.candidate_kind && <Kv name="线索类型" value={zhCandidateKind(link.candidate_kind)} />}
      {link.target_key && <Kv name="目标来源" value={link.target_key} />}
      {link.content_kind && <Kv name="内容类型" value={link.content_kind} />}
      {typeof link.bytes === 'number' && <Kv name="大小" value={`${link.bytes} 字节`} />}
      {link.target && <Kv name="目标" value={link.target} />}
      {link.normalized_target && <Kv name="规范目标" value={link.normalized_target} />}
      {link.target_summary && <Kv name="资料摘要" value={link.target_summary} />}
      {link.target_excerpt && <Kv name="内容线索" value={<pre style={{ ...S.sourceExcerpt, maxHeight: 90 }}>{link.target_excerpt}</pre>} />}
      {link.candidate_material_id && <Kv name="候选来源" value={<span style={S.pill}>{link.candidate_material_id}</span>} />}
      {(link.matched_material_ids || []).length > 0 && (
        <Kv
          name="命中 material"
          value={(link.matched_material_ids || []).map((materialId) => (
            <span key={materialId} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhMaterialName(materialId)} / {materialId}</span>
          ))}
        />
      )}
      {link.rel_path && <Kv name="相对路径" value={link.rel_path} />}
      {link.basis && <Kv name="底层依据" value={link.basis} />}
      {link.candidate_reason && <Kv name="底层归因" value={link.candidate_reason} />}
      {link.promotion_hint && <Kv name="升级提示" value={link.promotion_hint} />}
      {link.evidence.length > 0 && (
        <Kv
          name="证据片段"
          value={link.evidence.slice(0, 4).map((item) => (
            <div key={item} style={{ marginBottom: 4 }}>{item}</div>
          ))}
        />
      )}
      <Kv name="原始记录" value={<SourceDataLink href={materializationLinkHref(link, visualNode.run.worker_id)} label="原始 JSON" />} />
    </>
  )
}

function FloatingSelectionDetail({ graph, selectedItemId, onClose, materialization, resourceHints }: {
  graph: TeamGraphData
  selectedItemId: string | null
  onClose: () => void
  materialization: TeamBuilderMaterialization | null
  resourceHints: TeamResourceHint[]
}) {
  if (!selectedItemId) return null

  const selectedItem = parseGraphItemId(selectedItemId)
  const selectedNode = selectedItem.kind === 'worker'
    ? graph.nodes.find((node) => node.id === selectedItem.id) || null
    : null
  const selectedMaterial = selectedItem.kind === 'material'
    ? graph.materials.find((material) => material.id === selectedItem.id) || null
    : null
  const selectedMaterialization = findMaterializationVisualNode(graph, materialization, selectedItemId)
  const selectedResource = selectedItem.kind === 'resource'
    ? resourceHints.find((resource) => resource.id === selectedItem.id) || null
    : null
  if (!selectedNode && !selectedMaterial && !selectedMaterialization && !selectedResource) return null

  const title = selectedNode
    ? zhWorkerName(selectedNode)
    : selectedMaterial
      ? zhMaterialName(selectedMaterial.id)
      : selectedMaterialization?.kind === 'materialization-worker'
        ? zhWorkerName(selectedMaterialization.run.worker_id)
        : selectedMaterialization?.kind === 'materialization-link-group'
          ? selectedMaterialization.group.label
        : selectedMaterialization
          ? materializationShortTarget(selectedMaterialization.link)
          : selectedResource
            ? selectedResource.label
          : ''
  const subtitle = selectedNode
    ? selectedNode.id
    : selectedMaterial
      ? selectedMaterial.id
      : selectedMaterialization?.kind === 'materialization-worker'
        ? selectedMaterialization.run.worker_id
        : selectedMaterialization?.kind === 'materialization-link-group'
          ? `${selectedMaterialization.group.worker_id} / ${selectedMaterialization.group.count} 条`
        : selectedMaterialization
          ? materializationTargetText(selectedMaterialization.link)
          : selectedResource
            ? `资源空间 / ${selectedResource.id}`
          : ''

  return (
    <Panel position="top-left" style={{ margin: 14 }}>
      <div
        data-team-floating-detail
        style={S.floatingPanel}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        <div style={{ display: 'flex', alignItems: 'start', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
          <div style={{ minWidth: 0 }}>
            <div style={S.floatingTitle}>{title}</div>
            <div style={{ marginTop: 3, fontFamily: fonts.mono, color: colors.textMuted, fontSize: 13, overflowWrap: 'anywhere' }}>{subtitle}</div>
          </div>
          <button type="button" aria-label="关闭详情" style={S.iconButton} onClick={onClose}>×</button>
        </div>

        {selectedMaterialization ? (
          <>
            <Kv name="中文说明" value="这是最新 TeamBuilder 实战生成审查里的证据节点，用来解释 worker、material、workspace 读取之间的关系。" />
            <MaterializationEvidenceDetail visualNode={selectedMaterialization} />
          </>
        ) : selectedNode ? (
          <>
            <Kv name="中文说明" value={workerZhDescription(selectedNode)} />
            <Kv name="类型" value={zhValidatorKind(selectedNode.validator_kind || selectedNode.kind)} />
            <Kv name="输入材料" value={selectedNode.format_in.length ? selectedNode.format_in.map((fmt) => <span key={fmt} title={fmt} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhMaterialName(fmt)} / {fmt}</span>) : '-'} />
            <Kv name="输出材料" value={selectedNode.format_out ? <span title={selectedNode.format_out} style={S.pill}>{zhMaterialName(selectedNode.format_out)} / {selectedNode.format_out}</span> : '-'} />
            <DefinitionDetail definition={selectedNode.definition} kind="worker" presentation="inline" />
          </>
        ) : selectedMaterial ? (
          <>
            <Kv name="中文说明" value={materialZhDescription(selectedMaterial, graph)} />
            <Kv name="生产者" value={selectedMaterial.producers.length ? selectedMaterial.producers.map((producer) => <span key={producer} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhWorkerName(producer)} / {producer}</span>) : '外部输入'} />
            <Kv name="消费者" value={selectedMaterial.consumers.length ? selectedMaterial.consumers.map((consumer) => <span key={consumer} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhWorkerName(consumer)} / {consumer}</span>) : '最终输出'} />
            <DefinitionDetail definition={selectedMaterial.definition} kind="material" presentation="inline" />
          </>
        ) : selectedResource ? (
          <>
            <Kv name="中文说明" value={selectedResource.description} />
            <Kv name="判断状态" value={selectedResource.confidence === '运行推断' ? '来自运行或实战记录的线索，仍需结合具体读取节点确认。' : '来自 worker 名称或说明的粗略提示。'} />
            <Kv
              name="关联 worker"
              value={selectedResource.workers.map((workerId) => (
                <span key={workerId} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhWorkerName(workerId)} / {workerId}</span>
              ))}
            />
            <Kv
              name="具体线索"
              value={selectedResource.evidence.slice(0, 6).map((item) => (
                <div key={item} style={{ marginBottom: 6, color: colors.textSecondary }}>{item}</div>
              ))}
            />
            <Kv name="怎么审阅" value="先把它当作资料入口提示；需要判断具体 material 时，继续点击图上的“实战读取线索”节点，看具体目标、资料摘要和判断依据。" />
          </>
        ) : null}
      </div>
    </Panel>
  )
}

function GraphSidePanel({ graph, selectedNode, selectedMaterial, builderValue, onBuilderChange, onClose, isSmoke, runs, selectedRunId, onSelectRun, runDetail, runsError, runLoading, doctorHealth, doctorError, doctorLoading, showTeamBuilderMaterialization, materialization, materializationError, materializationLoading, materialReport, materialReportError, materialReportLoading, readClueResolutionPlan, readClueResolutionError, readClueResolutionLoading, materialGapValidation, materialGapValidationError, materialGapValidationLoading, testReport, testReportError, testReportLoading, repairPlan, repairPlanError, repairPlanLoading, repairProbe, repairProbeError, repairProbeLoading, repairDryRun, repairDryRunError, repairDryRunLoading, repairPatchCandidates, repairPatchCandidatesError, repairPatchCandidatesLoading, repairApplyGate, repairApplyGateError, repairApplyGateLoading, repairPatchDiffProposal, repairPatchDiffProposalError, repairPatchDiffProposalLoading, repairApproval, repairApprovalError, repairApprovalLoading, repairExecutionReadiness, repairExecutionReadinessError, repairExecutionReadinessLoading, repairApplyPreview, repairApplyPreviewError, repairApplyPreviewLoading, repairApplyExecution, repairApplyExecutionError, repairApplyExecutionLoading, repairPostApplyVerification, repairPostApplyVerificationError, repairPostApplyVerificationLoading, repairOutcomeReconciliation, repairOutcomeReconciliationError, repairOutcomeReconciliationLoading, repairRollbackReadiness, repairRollbackReadinessError, repairRollbackReadinessLoading, repairRollbackExecution, repairRollbackExecutionError, repairRollbackExecutionLoading, repairRollbackPostVerification, repairRollbackPostVerificationError, repairRollbackPostVerificationLoading, repairClosureRollup, repairClosureRollupError, repairClosureRollupLoading, repairGeneralizationTrial, repairGeneralizationTrialError, repairGeneralizationTrialLoading, repairRealGeneratedFileSetTrial, repairRealGeneratedFileSetTrialError, repairRealGeneratedFileSetTrialLoading, repairRealRunClosureRollup, repairRealRunClosureRollupError, repairRealRunClosureRollupLoading, repairRealRunCandidateScan, repairRealRunCandidateScanError, repairRealRunCandidateScanLoading, repairRealRunReplayPlan, repairRealRunReplayPlanError, repairRealRunReplayPlanLoading, repairRealRunDiffPreview, repairRealRunDiffPreviewError, repairRealRunDiffPreviewLoading, repairRealRunDiffReview, repairRealRunDiffReviewError, repairRealRunDiffReviewLoading, repairRealRunApplyGate, repairRealRunApplyGateError, repairRealRunApplyGateLoading, repairRealRunApplyPreview, repairRealRunApplyPreviewError, repairRealRunApplyPreviewLoading, repairRealRunApplyExecution, repairRealRunApplyExecutionError, repairRealRunApplyExecutionLoading, repairRealRunPostApplyVerification, repairRealRunPostApplyVerificationError, repairRealRunPostApplyVerificationLoading, repairRealRunOutcomeReconciliation, repairRealRunOutcomeReconciliationError, repairRealRunOutcomeReconciliationLoading, repairRealRunRollbackReadiness, repairRealRunRollbackReadinessError, repairRealRunRollbackReadinessLoading, repairRealRunRollbackExecution, repairRealRunRollbackExecutionError, repairRealRunRollbackExecutionLoading, repairRealRunRollbackPostVerification, repairRealRunRollbackPostVerificationError, repairRealRunRollbackPostVerificationLoading, repairSafetyPolicy, repairSafetyPolicyError, repairSafetyPolicyLoading, closureStatus, closureStatusError, closureStatusLoading, llmReplayPlan, llmReplayPlanError, llmReplayPlanLoading, llmReplayResult, llmReplayResultError, llmReplayResultLoading }: {
  graph: TeamGraphData
  selectedNode: TeamGraphNode | null
  selectedMaterial: TeamGraphMaterial | null
  builderValue: string
  onBuilderChange: (value: string) => void
  onClose: () => void
  isSmoke: boolean
  runs: TeamRunSummary[]
  selectedRunId: string | null
  onSelectRun: (traceId: string) => void
  runDetail: TeamRunDetail | null
  runsError: string | null
  runLoading: boolean
  doctorHealth: TeamDoctorHealth | null
  doctorError: string | null
  doctorLoading: boolean
  showTeamBuilderMaterialization: boolean
  materialization: TeamBuilderMaterialization | null
  materializationError: string | null
  materializationLoading: boolean
  materialReport: MaterialAttributionReport | null
  materialReportError: string | null
  materialReportLoading: boolean
  readClueResolutionPlan: TeamBuilderReadClueResolutionPlan | null
  readClueResolutionError: string | null
  readClueResolutionLoading: boolean
  materialGapValidation: TeamBuilderMaterialGapValidation | null
  materialGapValidationError: string | null
  materialGapValidationLoading: boolean
  testReport: TeamBuilderTestReport | null
  testReportError: string | null
  testReportLoading: boolean
  repairPlan: TeamBuilderRepairPlan | null
  repairPlanError: string | null
  repairPlanLoading: boolean
  repairProbe: TeamBuilderRepairProbeReport | null
  repairProbeError: string | null
  repairProbeLoading: boolean
  repairDryRun: TeamBuilderRepairDryRunReport | null
  repairDryRunError: string | null
  repairDryRunLoading: boolean
  repairPatchCandidates: TeamBuilderRepairPatchCandidatesReport | null
  repairPatchCandidatesError: string | null
  repairPatchCandidatesLoading: boolean
  repairApplyGate: TeamBuilderRepairApplyGateReport | null
  repairApplyGateError: string | null
  repairApplyGateLoading: boolean
  repairPatchDiffProposal: TeamBuilderRepairPatchDiffProposalReport | null
  repairPatchDiffProposalError: string | null
  repairPatchDiffProposalLoading: boolean
  repairApproval: TeamBuilderRepairApprovalReport | null
  repairApprovalError: string | null
  repairApprovalLoading: boolean
  repairExecutionReadiness: TeamBuilderRepairExecutionReadinessReport | null
  repairExecutionReadinessError: string | null
  repairExecutionReadinessLoading: boolean
  repairApplyPreview: TeamBuilderRepairApplyPreviewReport | null
  repairApplyPreviewError: string | null
  repairApplyPreviewLoading: boolean
  repairApplyExecution: TeamBuilderRepairApplyExecutionReport | null
  repairApplyExecutionError: string | null
  repairApplyExecutionLoading: boolean
  repairPostApplyVerification: TeamBuilderRepairPostApplyVerificationReport | null
  repairPostApplyVerificationError: string | null
  repairPostApplyVerificationLoading: boolean
  repairOutcomeReconciliation: TeamBuilderRepairOutcomeReconciliationReport | null
  repairOutcomeReconciliationError: string | null
  repairOutcomeReconciliationLoading: boolean
  repairRollbackReadiness: TeamBuilderRepairRollbackReadinessReport | null
  repairRollbackReadinessError: string | null
  repairRollbackReadinessLoading: boolean
  repairRollbackExecution: TeamBuilderRepairRollbackExecutionReport | null
  repairRollbackExecutionError: string | null
  repairRollbackExecutionLoading: boolean
  repairRollbackPostVerification: TeamBuilderRepairRollbackPostVerificationReport | null
  repairRollbackPostVerificationError: string | null
  repairRollbackPostVerificationLoading: boolean
  repairClosureRollup: TeamBuilderRepairClosureRollupReport | null
  repairClosureRollupError: string | null
  repairClosureRollupLoading: boolean
  repairGeneralizationTrial: TeamBuilderRepairGeneralizationTrialReport | null
  repairGeneralizationTrialError: string | null
  repairGeneralizationTrialLoading: boolean
  repairRealGeneratedFileSetTrial: TeamBuilderRepairRealGeneratedFileSetTrialReport | null
  repairRealGeneratedFileSetTrialError: string | null
  repairRealGeneratedFileSetTrialLoading: boolean
  repairRealRunClosureRollup: TeamBuilderRepairRealRunClosureRollupReport | null
  repairRealRunClosureRollupError: string | null
  repairRealRunClosureRollupLoading: boolean
  repairRealRunCandidateScan: TeamBuilderRepairRealRunCandidateScanReport | null
  repairRealRunCandidateScanError: string | null
  repairRealRunCandidateScanLoading: boolean
  repairRealRunReplayPlan: TeamBuilderRepairRealRunReplayPlanReport | null
  repairRealRunReplayPlanError: string | null
  repairRealRunReplayPlanLoading: boolean
  repairRealRunDiffPreview: TeamBuilderRepairRealRunDiffPreviewReport | null
  repairRealRunDiffPreviewError: string | null
  repairRealRunDiffPreviewLoading: boolean
  repairRealRunDiffReview: TeamBuilderRepairRealRunDiffReviewReport | null
  repairRealRunDiffReviewError: string | null
  repairRealRunDiffReviewLoading: boolean
  repairRealRunApplyGate: TeamBuilderRepairRealRunApplyGateReport | null
  repairRealRunApplyGateError: string | null
  repairRealRunApplyGateLoading: boolean
  repairRealRunApplyPreview: TeamBuilderRepairRealRunApplyPreviewReport | null
  repairRealRunApplyPreviewError: string | null
  repairRealRunApplyPreviewLoading: boolean
  repairRealRunApplyExecution: TeamBuilderRepairRealRunApplyExecutionReport | null
  repairRealRunApplyExecutionError: string | null
  repairRealRunApplyExecutionLoading: boolean
  repairRealRunPostApplyVerification: TeamBuilderRepairRealRunPostApplyVerificationReport | null
  repairRealRunPostApplyVerificationError: string | null
  repairRealRunPostApplyVerificationLoading: boolean
  repairRealRunOutcomeReconciliation: TeamBuilderRepairRealRunOutcomeReconciliationReport | null
  repairRealRunOutcomeReconciliationError: string | null
  repairRealRunOutcomeReconciliationLoading: boolean
  repairRealRunRollbackReadiness: TeamBuilderRepairRealRunRollbackReadinessReport | null
  repairRealRunRollbackReadinessError: string | null
  repairRealRunRollbackReadinessLoading: boolean
  repairRealRunRollbackExecution: TeamBuilderRepairRealRunRollbackExecutionReport | null
  repairRealRunRollbackExecutionError: string | null
  repairRealRunRollbackExecutionLoading: boolean
  repairRealRunRollbackPostVerification: TeamBuilderRepairRealRunRollbackPostVerificationReport | null
  repairRealRunRollbackPostVerificationError: string | null
  repairRealRunRollbackPostVerificationLoading: boolean
  repairSafetyPolicy: TeamBuilderRepairSafetyPolicy | null
  repairSafetyPolicyError: string | null
  repairSafetyPolicyLoading: boolean
  closureStatus: TeamBuilderClosureStatus | null
  closureStatusError: string | null
  closureStatusLoading: boolean
  llmReplayPlan: TeamBuilderLlmReplayPlan | null
  llmReplayPlanError: string | null
  llmReplayPlanLoading: boolean
  llmReplayResult: TeamBuilderLlmReplayResult | null
  llmReplayResultError: string | null
  llmReplayResultLoading: boolean
}) {
  const warnings = graph.health.warnings
  // 面板节渐进披露(2026-07-19 G): 运行轨迹/资源空间线索/材料清单默认折叠,
  // 节头整行可点(aria-expanded + 计数),把面板信息密度降到"扫一眼"级。
  const [closedSections, setClosedSections] = useState<Record<string, boolean>>({ runs: true, resources: true, materials: true })
  const toggleSection = (k: string) => setClosedSections((p) => ({ ...p, [k]: !p[k] }))
  const materials = graph.materials.slice(0, 12)
  const selectedNodeStatus = selectedNode
    ? runDetail?.node_statuses.find((status) => status.node_id === selectedNode.id)
    : null
  const selectedDoctorFindings = selectedNode
    ? (doctorHealth?.findings || []).filter((finding) => finding.node_ids.includes(selectedNode.id))
    : []
  const selectedNodeEvents = selectedNode
    ? (runDetail?.timeline || []).filter((event) => event.node_ids.includes(selectedNode.id)).slice(-4)
    : []
  const selectedMaterialFindings = selectedMaterial
    ? (doctorHealth?.findings || []).filter((finding) => finding.material_ids.includes(selectedMaterial.id))
    : []
  const selectedMaterialObservations = selectedMaterial
    ? (runDetail?.material_observations || []).filter((observation) => observation.inputs.includes(selectedMaterial.id) || observation.outputs.includes(selectedMaterial.id))
    : []
  const resourceHints = useMemo(() => inferResourceHints(graph, runDetail, materialization), [graph, runDetail, materialization])
  return (
    <aside style={S.side}>
      <div style={S.sideTopBar}>
        <div style={{ ...S.label, marginBottom: 0 }}>构建器</div>
        <button type="button" title="关闭右侧栏" aria-label="关闭右侧栏" data-team-side-close style={S.iconButton} onClick={onClose}>
          <PanelRightClose size={15} strokeWidth={1.8} />
        </button>
      </div>
      {graph.builders.length > 1 ? (
        <select value={builderValue} onChange={(e) => onBuilderChange(e.target.value)} style={S.select}>
          {graph.builders.map((builder) => (
            <option key={builder.name} value={builder.name}>
              {builder.name}（{builder.nodes} 节点 / {builder.edges} 连接）
            </option>
          ))}
        </select>
      ) : (
        // 单构建器时下拉是死交互 —— 降级为静态标题 + ⊢N·M⊣ 计数(虚线测量件)。
        <div data-team-builder-static style={{ display: 'flex', alignItems: 'center', gap: 8, border: '1px dashed var(--fp-border)', borderRadius: 3, padding: '6px 10px' }}>
          <span style={{ flex: 1, minWidth: 0, fontFamily: fonts.mono, fontSize: 12, color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{builderValue}</span>
          <span className="v2-count" style={{ flex: 'none' }}>{graph.nodes.length}·{graph.edges.length}</span>
        </div>
      )}

      {isSmoke && (
        <div style={{ ...S.section, color: '#ffb74d', fontSize: 13 }}>
          这个 team 只保留为冒烟测试样本，不能代表真实 TeamBuilder 的能力。
        </div>
      )}

      <div style={S.section}>
        <div style={S.label}>结构概览</div>
        <Kv name="中文代称" value={zhTeamName(graph)} />
        <Kv name="中文说明" value={<span title={teamZhDescription(graph)} style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{teamZhDescription(graph)}</span>} />
        <Kv name="定义入口" value={<DefinitionButton definition={graph.definition} title={zhTeamName(graph)} />} />
        <div style={S.metricRow}>
          <div style={S.metric}><div style={S.metricValue}>{graph.nodes.length}</div><div style={S.metricName}>节点</div></div>
          <div style={S.metric}><div style={S.metricValue}>{graph.edges.length}</div><div style={S.metricName}>连接</div></div>
          <div style={S.metric}><div style={S.metricValue}>{graph.materials.length}</div><div style={S.metricName}>材料</div></div>
        </div>
        {/* 边/节点构成 chips:零值类目自动隐藏(0 是噪音),有值类目才占版面 */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {([
            ['硬规则', graph.health.hard_nodes],
            ['模型判断', graph.health.soft_nodes],
            ['反馈边', graph.health.feedback_edges],
            ['外部输入', graph.health.external_inputs],
            ['最终输出', graph.health.terminal_outputs],
          ] as [string, number][]).filter(([, n]) => n > 0).map(([k, n]) => (
            <span key={k} style={S.pill}>{k} {n}</span>
          ))}
        </div>
      </div>

      <div style={S.section}>
        <div style={S.label}>健康诊断</div>
        {doctorError ? (
          <div style={{ color: '#ffb74d', fontSize: 13, overflowWrap: 'anywhere' }}>{doctorError}</div>
        ) : doctorHealth ? (
          doctorHealth.counts.total === 0 ? (
            // 全绿 = 一行状态徽标;有问题才展开统计卡+findings 明细。
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }} data-doctor-summary>
              <span className="v2-status st-ok"><i className="led" aria-hidden />{zhDoctorStatus(doctorHealth.status)}</span>
              <span style={{ color: colors.textFaint, fontSize: 12, fontFamily: fonts.mono }}>检查项 {doctorHealth.checks.filter((check) => check.default_on).length} · 问题 0</span>
            </div>
          ) : (
          <>
            <div style={S.metricRow} data-doctor-summary>
              <div style={S.metric}><div style={S.metricValue}>{doctorHealth.counts.total}</div><div style={S.metricName}>问题</div></div>
              <div style={S.metric}><div style={S.metricValue}>{doctorHealth.counts.blocking}</div><div style={S.metricName}>阻断</div></div>
              <div style={S.metric}><div style={S.metricValue}>{doctorHealth.counts.degrading}</div><div style={S.metricName}>降级</div></div>
            </div>
            <Kv name="状态" value={zhDoctorStatus(doctorHealth.status)} />
            <Kv name="检查项" value={doctorHealth.checks.filter((check) => check.default_on).length} />
            {doctorHealth.findings.length > 0 ? (
              <div style={{ display: 'grid', gap: 6, marginTop: 8 }} data-doctor-findings>
                {doctorHealth.findings.slice(0, 5).map((finding) => {
                  const tone = doctorTone(finding.level)
                  return (
                    <div key={finding.id} style={{ border: `1px solid ${tone?.border || colors.border}`, borderRadius: 6, padding: 8, background: colors.bg }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                        <span style={{ fontFamily: fonts.mono, fontSize: 13, color: colors.text }}>{finding.check_id}</span>
                        <span style={{ fontSize: 13, color: tone?.border || colors.textMuted }}>{zhDoctorLevel(finding.level)}</span>
                      </div>
                      <div style={{ marginTop: 4, color: colors.textMuted, fontSize: 13, overflowWrap: 'anywhere' }}>
                        {finding.location}
                      </div>
                      <div style={{ marginTop: 4, color: colors.textSecondary, fontSize: 13, overflowWrap: 'anywhere' }}>
                        {finding.observation}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div style={{ marginTop: 8, color: colors.textMuted, fontSize: 13 }}>当前构建器没有诊断问题。</div>
            )}
          </>
          )
        ) : (
          <div style={{ color: colors.textMuted, fontSize: 13 }}>{doctorLoading ? '正在加载诊断结果...' : '暂无诊断数据。'}</div>
        )}
      </div>

      {showTeamBuilderMaterialization && (
        <TeamMaterializationSection
          selectedNode={selectedNode}
          selectedMaterial={selectedMaterial}
          materialization={materialization}
          materializationError={materializationError}
          materializationLoading={materializationLoading}
          materialReport={materialReport}
          materialReportError={materialReportError}
          materialReportLoading={materialReportLoading}
          readClueResolutionPlan={readClueResolutionPlan}
          readClueResolutionError={readClueResolutionError}
          readClueResolutionLoading={readClueResolutionLoading}
          materialGapValidation={materialGapValidation}
          materialGapValidationError={materialGapValidationError}
          materialGapValidationLoading={materialGapValidationLoading}
          testReport={testReport}
          testReportError={testReportError}
          testReportLoading={testReportLoading}
          repairPlan={repairPlan}
          repairPlanError={repairPlanError}
          repairPlanLoading={repairPlanLoading}
          repairProbe={repairProbe}
          repairProbeError={repairProbeError}
          repairProbeLoading={repairProbeLoading}
          repairDryRun={repairDryRun}
          repairDryRunError={repairDryRunError}
          repairDryRunLoading={repairDryRunLoading}
          repairPatchCandidates={repairPatchCandidates}
          repairPatchCandidatesError={repairPatchCandidatesError}
          repairPatchCandidatesLoading={repairPatchCandidatesLoading}
          repairApplyGate={repairApplyGate}
          repairApplyGateError={repairApplyGateError}
          repairApplyGateLoading={repairApplyGateLoading}
          repairPatchDiffProposal={repairPatchDiffProposal}
          repairPatchDiffProposalError={repairPatchDiffProposalError}
          repairPatchDiffProposalLoading={repairPatchDiffProposalLoading}
          repairApproval={repairApproval}
          repairApprovalError={repairApprovalError}
          repairApprovalLoading={repairApprovalLoading}
          repairExecutionReadiness={repairExecutionReadiness}
          repairExecutionReadinessError={repairExecutionReadinessError}
          repairExecutionReadinessLoading={repairExecutionReadinessLoading}
          repairApplyPreview={repairApplyPreview}
          repairApplyPreviewError={repairApplyPreviewError}
          repairApplyPreviewLoading={repairApplyPreviewLoading}
          repairApplyExecution={repairApplyExecution}
          repairApplyExecutionError={repairApplyExecutionError}
          repairApplyExecutionLoading={repairApplyExecutionLoading}
          repairPostApplyVerification={repairPostApplyVerification}
          repairPostApplyVerificationError={repairPostApplyVerificationError}
          repairPostApplyVerificationLoading={repairPostApplyVerificationLoading}
          repairOutcomeReconciliation={repairOutcomeReconciliation}
          repairOutcomeReconciliationError={repairOutcomeReconciliationError}
          repairOutcomeReconciliationLoading={repairOutcomeReconciliationLoading}
          repairRollbackReadiness={repairRollbackReadiness}
          repairRollbackReadinessError={repairRollbackReadinessError}
          repairRollbackReadinessLoading={repairRollbackReadinessLoading}
          repairRollbackExecution={repairRollbackExecution}
          repairRollbackExecutionError={repairRollbackExecutionError}
          repairRollbackExecutionLoading={repairRollbackExecutionLoading}
          repairRollbackPostVerification={repairRollbackPostVerification}
          repairRollbackPostVerificationError={repairRollbackPostVerificationError}
          repairRollbackPostVerificationLoading={repairRollbackPostVerificationLoading}
          repairClosureRollup={repairClosureRollup}
          repairClosureRollupError={repairClosureRollupError}
          repairClosureRollupLoading={repairClosureRollupLoading}
          repairGeneralizationTrial={repairGeneralizationTrial}
          repairGeneralizationTrialError={repairGeneralizationTrialError}
          repairGeneralizationTrialLoading={repairGeneralizationTrialLoading}
          repairRealGeneratedFileSetTrial={repairRealGeneratedFileSetTrial}
          repairRealGeneratedFileSetTrialError={repairRealGeneratedFileSetTrialError}
          repairRealGeneratedFileSetTrialLoading={repairRealGeneratedFileSetTrialLoading}
          repairRealRunClosureRollup={repairRealRunClosureRollup}
          repairRealRunClosureRollupError={repairRealRunClosureRollupError}
          repairRealRunClosureRollupLoading={repairRealRunClosureRollupLoading}
          repairRealRunCandidateScan={repairRealRunCandidateScan}
          repairRealRunCandidateScanError={repairRealRunCandidateScanError}
          repairRealRunCandidateScanLoading={repairRealRunCandidateScanLoading}
          repairRealRunReplayPlan={repairRealRunReplayPlan}
          repairRealRunReplayPlanError={repairRealRunReplayPlanError}
          repairRealRunReplayPlanLoading={repairRealRunReplayPlanLoading}
          repairRealRunDiffPreview={repairRealRunDiffPreview}
          repairRealRunDiffPreviewError={repairRealRunDiffPreviewError}
          repairRealRunDiffPreviewLoading={repairRealRunDiffPreviewLoading}
          repairRealRunDiffReview={repairRealRunDiffReview}
          repairRealRunDiffReviewError={repairRealRunDiffReviewError}
          repairRealRunDiffReviewLoading={repairRealRunDiffReviewLoading}
          repairRealRunApplyGate={repairRealRunApplyGate}
          repairRealRunApplyGateError={repairRealRunApplyGateError}
          repairRealRunApplyGateLoading={repairRealRunApplyGateLoading}
          repairRealRunApplyPreview={repairRealRunApplyPreview}
          repairRealRunApplyPreviewError={repairRealRunApplyPreviewError}
          repairRealRunApplyPreviewLoading={repairRealRunApplyPreviewLoading}
          repairRealRunApplyExecution={repairRealRunApplyExecution}
          repairRealRunApplyExecutionError={repairRealRunApplyExecutionError}
          repairRealRunApplyExecutionLoading={repairRealRunApplyExecutionLoading}
          repairRealRunPostApplyVerification={repairRealRunPostApplyVerification}
          repairRealRunPostApplyVerificationError={repairRealRunPostApplyVerificationError}
          repairRealRunPostApplyVerificationLoading={repairRealRunPostApplyVerificationLoading}
          repairRealRunOutcomeReconciliation={repairRealRunOutcomeReconciliation}
          repairRealRunOutcomeReconciliationError={repairRealRunOutcomeReconciliationError}
          repairRealRunOutcomeReconciliationLoading={repairRealRunOutcomeReconciliationLoading}
          repairRealRunRollbackReadiness={repairRealRunRollbackReadiness}
          repairRealRunRollbackReadinessError={repairRealRunRollbackReadinessError}
          repairRealRunRollbackReadinessLoading={repairRealRunRollbackReadinessLoading}
          repairRealRunRollbackExecution={repairRealRunRollbackExecution}
          repairRealRunRollbackExecutionError={repairRealRunRollbackExecutionError}
          repairRealRunRollbackExecutionLoading={repairRealRunRollbackExecutionLoading}
          repairRealRunRollbackPostVerification={repairRealRunRollbackPostVerification}
          repairRealRunRollbackPostVerificationError={repairRealRunRollbackPostVerificationError}
          repairRealRunRollbackPostVerificationLoading={repairRealRunRollbackPostVerificationLoading}
          repairSafetyPolicy={repairSafetyPolicy}
          repairSafetyPolicyError={repairSafetyPolicyError}
          repairSafetyPolicyLoading={repairSafetyPolicyLoading}
          closureStatus={closureStatus}
          closureStatusError={closureStatusError}
          closureStatusLoading={closureStatusLoading}
          llmReplayPlan={llmReplayPlan}
          llmReplayPlanError={llmReplayPlanError}
          llmReplayPlanLoading={llmReplayPlanLoading}
          llmReplayResult={llmReplayResult}
          llmReplayResultError={llmReplayResultError}
          llmReplayResultLoading={llmReplayResultLoading}
        />
      )}

      <div style={S.section}>
        <button type="button" aria-expanded={!closedSections.runs} data-team-section-toggle="runs" onClick={() => toggleSection('runs')}
          style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', background: 'transparent', border: 0, padding: 0, cursor: 'pointer', font: 'inherit', color: 'inherit', textAlign: 'left' }}>
          <span style={{ ...S.label, marginBottom: 0, flex: 1 }}>运行轨迹</span>
          {runs.length > 0 && <span style={{ fontFamily: fonts.mono, fontSize: 12, color: colors.textFaint }}>{runs.length} 条</span>}
          <span style={{ color: colors.textFaint, fontSize: 12 }} aria-hidden>{closedSections.runs ? '▸' : '▾'}</span>
        </button>
        {!closedSections.runs && (<>
        {runsError ? (
          <div style={{ color: '#ffb74d', fontSize: 13, overflowWrap: 'anywhere' }}>{runsError}</div>
        ) : runs.length === 0 ? (
          <div style={{ color: colors.textMuted, fontSize: 13 }}>事件库里没有找到匹配的运行记录。</div>
        ) : (
          <div style={{ display: 'grid', gap: 6 }} data-team-runs>
            {runs.slice(0, 6).map((run) => (
              <button
                key={run.trace_id}
                type="button"
                title={`trace ${run.trace_id}`}
                data-team-run-row={run.trace_id}
                onClick={() => onSelectRun(run.trace_id)}
                style={S.runButton(selectedRunId === run.trace_id)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                  <span style={{ fontFamily: fonts.mono, fontSize: 13, color: colors.text }}>{shortTrace(run.trace_id)}</span>
                  <span style={{ fontSize: 13, color: run.status === 'error' ? '#ff7b7b' : '#78d98b' }}>{zhRunStatus(run.status)}</span>
                </div>
                <div style={{ marginTop: 4, fontSize: 13, color: colors.textMuted }}>
                  {run.matched_nodes.length}/{run.total_nodes} 节点，{run.tool_calls} 工具，{run.llm_calls} 模型调用，{run.agent_turns} 轮次
                </div>
                <div style={{ marginTop: 3, fontSize: 13, color: colors.textFaint }}>
                  {compactTime(run.started_at)} / {run.source || run.domains.join(', ')}
                </div>
              </button>
            ))}
          </div>
        )}

        {runDetail && (
          <div style={{ marginTop: 10 }} data-run-summary>
            <div style={S.metricRow}>
              <div style={S.metric}><div style={S.metricValue}>{runDetail.active_nodes.length}</div><div style={S.metricName}>活跃</div></div>
              <div style={S.metric}><div style={S.metricValue}>{runDetail.summary.tool_calls}</div><div style={S.metricName}>工具</div></div>
              <div style={S.metric}><div style={S.metricValue}>{runDetail.summary.llm_calls}</div><div style={S.metricName}>模型</div></div>
            </div>
            <Kv name="轨迹" value={<span style={S.pill}>{shortTrace(runDetail.trace_id)}</span>} />
            <Kv name="事件" value={`${runDetail.summary.matched_event_count}/${runDetail.summary.event_count} 已匹配`} />
            <Kv name="材料观察" value={runDetail.material_observations.length} />
            <Kv name="最后时间" value={compactTime(runDetail.summary.ended_at)} />
          </div>
        )}
        {runLoading && <div style={{ marginTop: 8, color: colors.textMuted, fontSize: 13 }}>正在加载运行叠加层...</div>}
        </>)}
      </div>

      <div style={S.section} data-team-resource-hints>
        <button type="button" aria-expanded={!closedSections.resources} data-team-section-toggle="resources" onClick={() => toggleSection('resources')}
          style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', background: 'transparent', border: 0, padding: 0, cursor: 'pointer', font: 'inherit', color: 'inherit', textAlign: 'left' }}>
          <span style={{ ...S.label, marginBottom: 0, flex: 1 }}>资源空间线索</span>
          {resourceHints.length > 0 && <span style={{ fontFamily: fonts.mono, fontSize: 12, color: colors.textFaint }}>{resourceHints.length} 条</span>}
          <span style={{ color: colors.textFaint, fontSize: 12 }} aria-hidden>{closedSections.resources ? '▸' : '▾'}</span>
        </button>
        {!closedSections.resources && (<>
        {resourceHints.length === 0 ? (
          <div style={{ color: colors.textMuted, fontSize: 13 }}>暂未从名称、说明或工具参数里推断出额外资源空间。</div>
        ) : (
          <div style={{ display: 'grid', gap: 9 }}>
            {resourceHints.map((hint) => {
              const meta = RESOURCE_META[hint.id]
              return (
                <div key={hint.id} style={{ ...S.resourceCard, borderColor: meta.tone, background: meta.background }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>{hint.label}</span>
                    <span style={{ color: meta.tone, fontSize: 13 }}>{hint.confidence}</span>
                  </div>
                  <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.45 }}>{hint.description}</div>
                  <div style={{ color: colors.textMuted, fontSize: 13, lineHeight: 1.45 }}>
                    关联 worker：{hint.workers.map((workerId) => zhWorkerName(workerId)).join('、')}
                  </div>
                  <div style={{ color: colors.textFaint, fontSize: 13, lineHeight: 1.45 }}>
                    {hint.evidence.slice(0, 3).map((item) => (
                      <div key={item}>例：{item}</div>
                    ))}
                    <div>这些只是资料入口线索，不是已确认 material 边。</div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        </>)}
      </div>

      <div style={{ ...S.section, display: 'none' }} aria-hidden="true">
        <div style={S.label}>当前选中对象</div>
        {selectedNode ? (
          <>
            <Kv name="中文代称" value={zhWorkerName(selectedNode)} />
            <Kv name="中文说明" value={workerZhDescription(selectedNode)} />
            <Kv name="定义入口" value={<DefinitionButton definition={selectedNode.definition} title={zhWorkerName(selectedNode)} />} />
            <Kv name="英文 ID" value={<span style={S.pill}>{selectedNode.id}</span>} />
            <Kv name="类型" value={zhValidatorKind(selectedNode.validator_kind || selectedNode.kind)} />
            <Kv name="成熟度" value={selectedNode.maturity || '-'} />
            <Kv name="输入材料" value={selectedNode.format_in.length ? selectedNode.format_in.map((fmt) => <span key={fmt} title={fmt} style={{ ...S.pill, margin: '0 4px 4px 0', whiteSpace: 'normal' }}>{zhMaterialName(fmt)} / {fmt}</span>) : '-'} />
            <Kv name="输出材料" value={selectedNode.format_out ? <span title={selectedNode.format_out} style={{ ...S.pill, whiteSpace: 'normal' }}>{zhMaterialName(selectedNode.format_out)} / {selectedNode.format_out}</span> : '-'} />
            <Kv name="校验器" value={selectedNode.validator_id || '-'} />
            <Kv name="说明" value={selectedNode.description || '-'} />
            <Kv name="定义详情" value="已移到图内悬浮窗；点上方定义入口可打开源码。" />
            {runDetail && (
              <>
                <Kv name="运行事件" value={selectedNodeStatus ? selectedNodeStatus.event_count : 0} />
                <Kv name="最后出现" value={selectedNodeStatus ? compactTime(selectedNodeStatus.last_at) : '-'} />
                <Kv name="判定" value={selectedNodeStatus && Object.keys(selectedNodeStatus.verdict_counts).length
                  ? Object.entries(selectedNodeStatus.verdict_counts).map(([name, count]) => `${zhVerdict(name)}:${count}`).join(', ')
                  : '-'}
                />
                {selectedNodeEvents.length > 0 && (
                  <Kv
                    name="时间线"
                    value={selectedNodeEvents.map((event) => (
                      <div key={event.id} style={{ marginBottom: 4 }}>
                        <span>{zhEventType(event.event_type)}</span>
                        {event.verdict ? ` / ${zhVerdict(event.verdict)}` : ''}
                      </div>
                    ))}
                  />
                )}
              </>
            )}
            {doctorHealth && (
              <>
                <Kv name="诊断问题" value={selectedDoctorFindings.length} />
                {selectedDoctorFindings.length > 0 && (
                  <Kv
                    name="问题"
                    value={selectedDoctorFindings.slice(0, 3).map((finding) => (
                      <div key={finding.id} style={{ marginBottom: 5 }}>
                        <span style={{ fontFamily: fonts.mono }}>{finding.check_id}</span>
                        {` / ${zhDoctorLevel(finding.level)}`}
                      </div>
                    ))}
                  />
                )}
              </>
            )}
          </>
        ) : selectedMaterial ? (
          <>
            <Kv name="中文代称" value={zhMaterialName(selectedMaterial.id)} />
            <Kv name="中文说明" value={materialZhDescription(selectedMaterial, graph)} />
            <Kv name="定义入口" value={<DefinitionButton definition={selectedMaterial.definition} title={zhMaterialName(selectedMaterial.id)} />} />
            <Kv name="英文 ID" value={<span style={S.pill}>{selectedMaterial.id}</span>} />
            <Kv name="定义详情" value="已移到图内悬浮窗；点上方定义入口可打开源码。" />
            <Kv name="生产者" value={selectedMaterial.producers.length ? selectedMaterial.producers.map((producer) => <span key={producer} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhWorkerName(producer)} / {producer}</span>) : '外部输入'} />
            <Kv name="消费者" value={selectedMaterial.consumers.length ? selectedMaterial.consumers.map((consumer) => <span key={consumer} style={{ ...S.pill, margin: '0 4px 4px 0' }}>{zhWorkerName(consumer)} / {consumer}</span>) : '最终输出'} />
            <Kv name="运行观察" value={selectedMaterialObservations.length} />
            <Kv name="诊断问题" value={selectedMaterialFindings.length} />
            {selectedMaterialFindings.length > 0 && (
              <Kv
                name="问题"
                value={selectedMaterialFindings.slice(0, 3).map((finding) => (
                  <div key={finding.id} style={{ marginBottom: 5 }}>
                    <span style={{ fontFamily: fonts.mono }}>{finding.check_id}</span>
                    {` / ${zhDoctorLevel(finding.level)}`}
                  </div>
                ))}
              />
            )}
          </>
        ) : (
          <div style={{ color: colors.textMuted, fontSize: 13 }}>请在图中选择一个 Worker 或 Material。</div>
        )}
      </div>

      <div style={S.section}>
        <button type="button" aria-expanded={!closedSections.materials} data-team-section-toggle="materials" onClick={() => toggleSection('materials')}
          style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', background: 'transparent', border: 0, padding: 0, cursor: 'pointer', font: 'inherit', color: 'inherit', textAlign: 'left' }}>
          <span style={{ ...S.label, marginBottom: 0, flex: 1 }}>材料清单</span>
          {materials.length > 0 && <span style={{ fontFamily: fonts.mono, fontSize: 12, color: colors.textFaint }}>{materials.length} 项</span>}
          <span style={{ color: colors.textFaint, fontSize: 12 }} aria-hidden>{closedSections.materials ? '▸' : '▾'}</span>
        </button>
        {!closedSections.materials && (
        <div style={{ display: 'grid', gap: 10 }}>
          {materials.map((material) => (
            <div key={material.id} style={S.materialCard}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                <div style={{ color: colors.text, fontSize: 15, fontWeight: 650 }}>{zhMaterialName(material.id)}</div>
                <DefinitionButton definition={material.definition} title={zhMaterialName(material.id)} compact />
              </div>
              <div style={S.materialId}>{material.id}</div>
              <div style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 1.45 }}>
                {materialZhDescription(material, graph)}
              </div>
              <div style={{ color: colors.textMuted, fontSize: 13 }}>
                {material.producers.length || 0} 个生产者 / {material.consumers.length || 0} 个消费者
              </div>
              {(material.is_external_input || material.is_terminal_output) && (
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {material.is_external_input && <span style={S.pill}>外部输入</span>}
                  {material.is_terminal_output && <span style={S.pill}>最终输出</span>}
                </div>
              )}
            </div>
          ))}
        </div>
        )}
      </div>

      {(warnings.length > 0 || Object.keys(graph.health.builder_errors || {}).length > 0) && (
        <div style={S.section}>
          <div style={S.label}>告警</div>
          <div style={{ display: 'grid', gap: 6 }}>
            {warnings.map((warning) => (
              <div key={warning} style={{ color: '#ffb74d', fontSize: 13, overflowWrap: 'anywhere' }}>{warning}</div>
            ))}
            {Object.entries(graph.health.builder_errors || {}).map(([name, error]) => (
              <div key={name} style={{ color: '#ffb74d', fontSize: 13, overflowWrap: 'anywhere' }}>{name}: {error}</div>
            ))}
          </div>
        </div>
      )}
    </aside>
  )
}

function TeamGraphView({ graph, entity, builderValue, onBuilderChange }: {
  graph: TeamGraphData
  entity: TeamEntity
  builderValue: string
  onBuilderChange: (value: string) => void
}) {
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null)
  const [runs, setRuns] = useState<TeamRunSummary[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [runDetail, setRunDetail] = useState<TeamRunDetail | null>(null)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [runsLoading, setRunsLoading] = useState(false)
  const [runLoading, setRunLoading] = useState(false)
  const [doctorHealth, setDoctorHealth] = useState<TeamDoctorHealth | null>(null)
  const [doctorError, setDoctorError] = useState<string | null>(null)
  const [doctorLoading, setDoctorLoading] = useState(false)
  const [materialization, setMaterialization] = useState<TeamBuilderMaterialization | null>(null)
  const [materializationError, setMaterializationError] = useState<string | null>(null)
  const [materializationLoading, setMaterializationLoading] = useState(false)
  const [materialReport, setMaterialReport] = useState<MaterialAttributionReport | null>(null)
  const [materialReportError, setMaterialReportError] = useState<string | null>(null)
  const [materialReportLoading, setMaterialReportLoading] = useState(false)
  const [readClueResolutionPlan, setReadClueResolutionPlan] = useState<TeamBuilderReadClueResolutionPlan | null>(null)
  const [readClueResolutionError, setReadClueResolutionError] = useState<string | null>(null)
  const [readClueResolutionLoading, setReadClueResolutionLoading] = useState(false)
  const [materialGapValidation, setMaterialGapValidation] = useState<TeamBuilderMaterialGapValidation | null>(null)
  const [materialGapValidationError, setMaterialGapValidationError] = useState<string | null>(null)
  const [materialGapValidationLoading, setMaterialGapValidationLoading] = useState(false)
  const [testReport, setTestReport] = useState<TeamBuilderTestReport | null>(null)
  const [testReportError, setTestReportError] = useState<string | null>(null)
  const [testReportLoading, setTestReportLoading] = useState(false)
  const [repairPlan, setRepairPlan] = useState<TeamBuilderRepairPlan | null>(null)
  const [repairPlanError, setRepairPlanError] = useState<string | null>(null)
  const [repairPlanLoading, setRepairPlanLoading] = useState(false)
  const [repairProbe, setRepairProbe] = useState<TeamBuilderRepairProbeReport | null>(null)
  const [repairProbeError, setRepairProbeError] = useState<string | null>(null)
  const [repairProbeLoading, setRepairProbeLoading] = useState(false)
  const [repairDryRun, setRepairDryRun] = useState<TeamBuilderRepairDryRunReport | null>(null)
  const [repairDryRunError, setRepairDryRunError] = useState<string | null>(null)
  const [repairDryRunLoading, setRepairDryRunLoading] = useState(false)
  const [repairPatchCandidates, setRepairPatchCandidates] = useState<TeamBuilderRepairPatchCandidatesReport | null>(null)
  const [repairPatchCandidatesError, setRepairPatchCandidatesError] = useState<string | null>(null)
  const [repairPatchCandidatesLoading, setRepairPatchCandidatesLoading] = useState(false)
  const [repairApplyGate, setRepairApplyGate] = useState<TeamBuilderRepairApplyGateReport | null>(null)
  const [repairApplyGateError, setRepairApplyGateError] = useState<string | null>(null)
  const [repairApplyGateLoading, setRepairApplyGateLoading] = useState(false)
  const [repairPatchDiffProposal, setRepairPatchDiffProposal] = useState<TeamBuilderRepairPatchDiffProposalReport | null>(null)
  const [repairPatchDiffProposalError, setRepairPatchDiffProposalError] = useState<string | null>(null)
  const [repairPatchDiffProposalLoading, setRepairPatchDiffProposalLoading] = useState(false)
  const [repairApproval, setRepairApproval] = useState<TeamBuilderRepairApprovalReport | null>(null)
  const [repairApprovalError, setRepairApprovalError] = useState<string | null>(null)
  const [repairApprovalLoading, setRepairApprovalLoading] = useState(false)
  const [repairExecutionReadiness, setRepairExecutionReadiness] = useState<TeamBuilderRepairExecutionReadinessReport | null>(null)
  const [repairExecutionReadinessError, setRepairExecutionReadinessError] = useState<string | null>(null)
  const [repairExecutionReadinessLoading, setRepairExecutionReadinessLoading] = useState(false)
  const [repairApplyPreview, setRepairApplyPreview] = useState<TeamBuilderRepairApplyPreviewReport | null>(null)
  const [repairApplyPreviewError, setRepairApplyPreviewError] = useState<string | null>(null)
  const [repairApplyPreviewLoading, setRepairApplyPreviewLoading] = useState(false)
  const [repairApplyExecution, setRepairApplyExecution] = useState<TeamBuilderRepairApplyExecutionReport | null>(null)
  const [repairApplyExecutionError, setRepairApplyExecutionError] = useState<string | null>(null)
  const [repairApplyExecutionLoading, setRepairApplyExecutionLoading] = useState(false)
  const [repairPostApplyVerification, setRepairPostApplyVerification] = useState<TeamBuilderRepairPostApplyVerificationReport | null>(null)
  const [repairPostApplyVerificationError, setRepairPostApplyVerificationError] = useState<string | null>(null)
  const [repairPostApplyVerificationLoading, setRepairPostApplyVerificationLoading] = useState(false)
  const [repairOutcomeReconciliation, setRepairOutcomeReconciliation] = useState<TeamBuilderRepairOutcomeReconciliationReport | null>(null)
  const [repairOutcomeReconciliationError, setRepairOutcomeReconciliationError] = useState<string | null>(null)
  const [repairOutcomeReconciliationLoading, setRepairOutcomeReconciliationLoading] = useState(false)
  const [repairRollbackReadiness, setRepairRollbackReadiness] = useState<TeamBuilderRepairRollbackReadinessReport | null>(null)
  const [repairRollbackReadinessError, setRepairRollbackReadinessError] = useState<string | null>(null)
  const [repairRollbackReadinessLoading, setRepairRollbackReadinessLoading] = useState(false)
  const [repairRollbackExecution, setRepairRollbackExecution] = useState<TeamBuilderRepairRollbackExecutionReport | null>(null)
  const [repairRollbackExecutionError, setRepairRollbackExecutionError] = useState<string | null>(null)
  const [repairRollbackExecutionLoading, setRepairRollbackExecutionLoading] = useState(false)
  const [repairRollbackPostVerification, setRepairRollbackPostVerification] = useState<TeamBuilderRepairRollbackPostVerificationReport | null>(null)
  const [repairRollbackPostVerificationError, setRepairRollbackPostVerificationError] = useState<string | null>(null)
  const [repairRollbackPostVerificationLoading, setRepairRollbackPostVerificationLoading] = useState(false)
  const [repairClosureRollup, setRepairClosureRollup] = useState<TeamBuilderRepairClosureRollupReport | null>(null)
  const [repairClosureRollupError, setRepairClosureRollupError] = useState<string | null>(null)
  const [repairClosureRollupLoading, setRepairClosureRollupLoading] = useState(false)
  const [repairGeneralizationTrial, setRepairGeneralizationTrial] = useState<TeamBuilderRepairGeneralizationTrialReport | null>(null)
  const [repairGeneralizationTrialError, setRepairGeneralizationTrialError] = useState<string | null>(null)
  const [repairGeneralizationTrialLoading, setRepairGeneralizationTrialLoading] = useState(false)
  const [repairRealGeneratedFileSetTrial, setRepairRealGeneratedFileSetTrial] = useState<TeamBuilderRepairRealGeneratedFileSetTrialReport | null>(null)
  const [repairRealGeneratedFileSetTrialError, setRepairRealGeneratedFileSetTrialError] = useState<string | null>(null)
  const [repairRealGeneratedFileSetTrialLoading, setRepairRealGeneratedFileSetTrialLoading] = useState(false)
  const [repairRealRunClosureRollup, setRepairRealRunClosureRollup] = useState<TeamBuilderRepairRealRunClosureRollupReport | null>(null)
  const [repairRealRunClosureRollupError, setRepairRealRunClosureRollupError] = useState<string | null>(null)
  const [repairRealRunClosureRollupLoading, setRepairRealRunClosureRollupLoading] = useState(false)
  const [repairRealRunCandidateScan, setRepairRealRunCandidateScan] = useState<TeamBuilderRepairRealRunCandidateScanReport | null>(null)
  const [repairRealRunCandidateScanError, setRepairRealRunCandidateScanError] = useState<string | null>(null)
  const [repairRealRunCandidateScanLoading, setRepairRealRunCandidateScanLoading] = useState(false)
  const [repairRealRunReplayPlan, setRepairRealRunReplayPlan] = useState<TeamBuilderRepairRealRunReplayPlanReport | null>(null)
  const [repairRealRunReplayPlanError, setRepairRealRunReplayPlanError] = useState<string | null>(null)
  const [repairRealRunReplayPlanLoading, setRepairRealRunReplayPlanLoading] = useState(false)
  const [repairRealRunDiffPreview, setRepairRealRunDiffPreview] = useState<TeamBuilderRepairRealRunDiffPreviewReport | null>(null)
  const [repairRealRunDiffPreviewError, setRepairRealRunDiffPreviewError] = useState<string | null>(null)
  const [repairRealRunDiffPreviewLoading, setRepairRealRunDiffPreviewLoading] = useState(false)
  const [repairRealRunDiffReview, setRepairRealRunDiffReview] = useState<TeamBuilderRepairRealRunDiffReviewReport | null>(null)
  const [repairRealRunDiffReviewError, setRepairRealRunDiffReviewError] = useState<string | null>(null)
  const [repairRealRunDiffReviewLoading, setRepairRealRunDiffReviewLoading] = useState(false)
  const [repairRealRunApplyGate, setRepairRealRunApplyGate] = useState<TeamBuilderRepairRealRunApplyGateReport | null>(null)
  const [repairRealRunApplyGateError, setRepairRealRunApplyGateError] = useState<string | null>(null)
  const [repairRealRunApplyGateLoading, setRepairRealRunApplyGateLoading] = useState(false)
  const [repairRealRunApplyPreview, setRepairRealRunApplyPreview] = useState<TeamBuilderRepairRealRunApplyPreviewReport | null>(null)
  const [repairRealRunApplyPreviewError, setRepairRealRunApplyPreviewError] = useState<string | null>(null)
  const [repairRealRunApplyPreviewLoading, setRepairRealRunApplyPreviewLoading] = useState(false)
  const [repairRealRunApplyExecution, setRepairRealRunApplyExecution] = useState<TeamBuilderRepairRealRunApplyExecutionReport | null>(null)
  const [repairRealRunApplyExecutionError, setRepairRealRunApplyExecutionError] = useState<string | null>(null)
  const [repairRealRunApplyExecutionLoading, setRepairRealRunApplyExecutionLoading] = useState(false)
  const [repairRealRunPostApplyVerification, setRepairRealRunPostApplyVerification] = useState<TeamBuilderRepairRealRunPostApplyVerificationReport | null>(null)
  const [repairRealRunPostApplyVerificationError, setRepairRealRunPostApplyVerificationError] = useState<string | null>(null)
  const [repairRealRunPostApplyVerificationLoading, setRepairRealRunPostApplyVerificationLoading] = useState(false)
  const [repairRealRunOutcomeReconciliation, setRepairRealRunOutcomeReconciliation] = useState<TeamBuilderRepairRealRunOutcomeReconciliationReport | null>(null)
  const [repairRealRunOutcomeReconciliationError, setRepairRealRunOutcomeReconciliationError] = useState<string | null>(null)
  const [repairRealRunOutcomeReconciliationLoading, setRepairRealRunOutcomeReconciliationLoading] = useState(false)
  const [repairRealRunRollbackReadiness, setRepairRealRunRollbackReadiness] = useState<TeamBuilderRepairRealRunRollbackReadinessReport | null>(null)
  const [repairRealRunRollbackReadinessError, setRepairRealRunRollbackReadinessError] = useState<string | null>(null)
  const [repairRealRunRollbackReadinessLoading, setRepairRealRunRollbackReadinessLoading] = useState(false)
  const [repairRealRunRollbackExecution, setRepairRealRunRollbackExecution] = useState<TeamBuilderRepairRealRunRollbackExecutionReport | null>(null)
  const [repairRealRunRollbackExecutionError, setRepairRealRunRollbackExecutionError] = useState<string | null>(null)
  const [repairRealRunRollbackExecutionLoading, setRepairRealRunRollbackExecutionLoading] = useState(false)
  const [repairRealRunRollbackPostVerification, setRepairRealRunRollbackPostVerification] = useState<TeamBuilderRepairRealRunRollbackPostVerificationReport | null>(null)
  const [repairRealRunRollbackPostVerificationError, setRepairRealRunRollbackPostVerificationError] = useState<string | null>(null)
  const [repairRealRunRollbackPostVerificationLoading, setRepairRealRunRollbackPostVerificationLoading] = useState(false)
  const [repairSafetyPolicy, setRepairSafetyPolicy] = useState<TeamBuilderRepairSafetyPolicy | null>(null)
  const [repairSafetyPolicyError, setRepairSafetyPolicyError] = useState<string | null>(null)
  const [repairSafetyPolicyLoading, setRepairSafetyPolicyLoading] = useState(false)
  const [closureStatus, setClosureStatus] = useState<TeamBuilderClosureStatus | null>(null)
  const [closureStatusError, setClosureStatusError] = useState<string | null>(null)
  const [closureStatusLoading, setClosureStatusLoading] = useState(false)
  const [llmReplayPlan, setLlmReplayPlan] = useState<TeamBuilderLlmReplayPlan | null>(null)
  const [llmReplayPlanError, setLlmReplayPlanError] = useState<string | null>(null)
  const [llmReplayPlanLoading, setLlmReplayPlanLoading] = useState(false)
  const [llmReplayResult, setLlmReplayResult] = useState<TeamBuilderLlmReplayResult | null>(null)
  const [llmReplayResultError, setLlmReplayResultError] = useState<string | null>(null)
  const [llmReplayResultLoading, setLlmReplayResultLoading] = useState(false)
  const showTeamBuilderMaterialization = entity.id === 'services/_core/team_builder/team'
  // 窄屏(≤1024)默认收起右栏成「右侧栏」浮钮,画布全宽(analysis 形态 Top1:
  // 面板定宽 390 吃掉 834 视口 43%,图退化为缩略区);desktop(>1024)维持 V1 默认展开。
  const [sidePanelVisible, setSidePanelVisible] = useState(() => typeof window === 'undefined' || window.matchMedia('(min-width: 1025px)').matches)
  useEffect(() => {
    setSelectedItemId(null)
  }, [graph.team_id, graph.selected_builder, graph.entry])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setMaterialization(null)
      setMaterializationError(null)
      setMaterializationLoading(false)
      return () => { cancelled = true }
    }
    setMaterialization(null)
    setMaterializationError(null)
    setMaterializationLoading(true)
    fetchMaterializationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterialization(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterializationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterializationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setMaterialReport(null)
      setMaterialReportError(null)
      setMaterialReportLoading(false)
      return () => { cancelled = true }
    }
    setMaterialReport(null)
    setMaterialReportError(null)
    setMaterialReportLoading(true)
    fetchMaterialAttributionReportLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterialReport(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterialReportError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterialReportLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setReadClueResolutionPlan(null)
      setReadClueResolutionError(null)
      setReadClueResolutionLoading(false)
      return () => { cancelled = true }
    }
    setReadClueResolutionPlan(null)
    setReadClueResolutionError(null)
    setReadClueResolutionLoading(true)
    fetchTeamBuilderReadClueResolutionLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setReadClueResolutionPlan(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setReadClueResolutionError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setReadClueResolutionLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setMaterialGapValidation(null)
      setMaterialGapValidationError(null)
      setMaterialGapValidationLoading(false)
      return () => { cancelled = true }
    }
    setMaterialGapValidation(null)
    setMaterialGapValidationError(null)
    setMaterialGapValidationLoading(true)
    fetchTeamBuilderMaterialGapValidationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterialGapValidation(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterialGapValidationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setMaterialGapValidationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setTestReport(null)
      setTestReportError(null)
      setTestReportLoading(false)
      return () => { cancelled = true }
    }
    setTestReport(null)
    setTestReportError(null)
    setTestReportLoading(true)
    fetchTeamBuilderTestReportLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setTestReport(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setTestReportError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setTestReportLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairPlan(null)
      setRepairPlanError(null)
      setRepairPlanLoading(false)
      return () => { cancelled = true }
    }
    setRepairPlan(null)
    setRepairPlanError(null)
    setRepairPlanLoading(true)
    fetchTeamBuilderRepairPlanLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPlan(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPlanError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPlanLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairProbe(null)
      setRepairProbeError(null)
      setRepairProbeLoading(false)
      return () => { cancelled = true }
    }
    setRepairProbe(null)
    setRepairProbeError(null)
    setRepairProbeLoading(true)
    fetchTeamBuilderRepairProbeLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairProbe(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairProbeError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairProbeLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairDryRun(null)
      setRepairDryRunError(null)
      setRepairDryRunLoading(false)
      return () => { cancelled = true }
    }
    setRepairDryRun(null)
    setRepairDryRunError(null)
    setRepairDryRunLoading(true)
    fetchTeamBuilderRepairDryRunLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairDryRun(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairDryRunError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairDryRunLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairPatchCandidates(null)
      setRepairPatchCandidatesError(null)
      setRepairPatchCandidatesLoading(false)
      return () => { cancelled = true }
    }
    setRepairPatchCandidates(null)
    setRepairPatchCandidatesError(null)
    setRepairPatchCandidatesLoading(true)
    fetchTeamBuilderRepairPatchCandidatesLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPatchCandidates(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPatchCandidatesError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPatchCandidatesLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairApplyGate(null)
      setRepairApplyGateError(null)
      setRepairApplyGateLoading(false)
      return () => { cancelled = true }
    }
    setRepairApplyGate(null)
    setRepairApplyGateError(null)
    setRepairApplyGateLoading(true)
    fetchTeamBuilderRepairApplyGateLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyGate(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyGateError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyGateLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairPatchDiffProposal(null)
      setRepairPatchDiffProposalError(null)
      setRepairPatchDiffProposalLoading(false)
      return () => { cancelled = true }
    }
    setRepairPatchDiffProposal(null)
    setRepairPatchDiffProposalError(null)
    setRepairPatchDiffProposalLoading(true)
    fetchTeamBuilderRepairPatchDiffProposalLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPatchDiffProposal(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPatchDiffProposalError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPatchDiffProposalLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairApproval(null)
      setRepairApprovalError(null)
      setRepairApprovalLoading(false)
      return () => { cancelled = true }
    }
    setRepairApproval(null)
    setRepairApprovalError(null)
    setRepairApprovalLoading(true)
    fetchTeamBuilderRepairApprovalLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApproval(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApprovalError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApprovalLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairExecutionReadiness(null)
      setRepairExecutionReadinessError(null)
      setRepairExecutionReadinessLoading(false)
      return () => { cancelled = true }
    }
    setRepairExecutionReadiness(null)
    setRepairExecutionReadinessError(null)
    setRepairExecutionReadinessLoading(true)
    fetchTeamBuilderRepairExecutionReadinessLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairExecutionReadiness(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairExecutionReadinessError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairExecutionReadinessLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairApplyPreview(null)
      setRepairApplyPreviewError(null)
      setRepairApplyPreviewLoading(false)
      return () => { cancelled = true }
    }
    setRepairApplyPreview(null)
    setRepairApplyPreviewError(null)
    setRepairApplyPreviewLoading(true)
    fetchTeamBuilderRepairApplyPreviewLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyPreview(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyPreviewError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyPreviewLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairApplyExecution(null)
      setRepairApplyExecutionError(null)
      setRepairApplyExecutionLoading(false)
      return () => { cancelled = true }
    }
    setRepairApplyExecution(null)
    setRepairApplyExecutionError(null)
    setRepairApplyExecutionLoading(true)
    fetchTeamBuilderRepairApplyExecutionLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyExecution(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyExecutionError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairApplyExecutionLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairPostApplyVerification(null)
      setRepairPostApplyVerificationError(null)
      setRepairPostApplyVerificationLoading(false)
      return () => { cancelled = true }
    }
    setRepairPostApplyVerification(null)
    setRepairPostApplyVerificationError(null)
    setRepairPostApplyVerificationLoading(true)
    fetchTeamBuilderRepairPostApplyVerificationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPostApplyVerification(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPostApplyVerificationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairPostApplyVerificationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairOutcomeReconciliation(null)
      setRepairOutcomeReconciliationError(null)
      setRepairOutcomeReconciliationLoading(false)
      return () => { cancelled = true }
    }
    setRepairOutcomeReconciliation(null)
    setRepairOutcomeReconciliationError(null)
    setRepairOutcomeReconciliationLoading(true)
    fetchTeamBuilderRepairOutcomeReconciliationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairOutcomeReconciliation(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairOutcomeReconciliationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairOutcomeReconciliationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRollbackReadiness(null)
      setRepairRollbackReadinessError(null)
      setRepairRollbackReadinessLoading(false)
      return () => { cancelled = true }
    }
    setRepairRollbackReadiness(null)
    setRepairRollbackReadinessError(null)
    setRepairRollbackReadinessLoading(true)
    fetchTeamBuilderRepairRollbackReadinessLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackReadiness(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackReadinessError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackReadinessLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRollbackExecution(null)
      setRepairRollbackExecutionError(null)
      setRepairRollbackExecutionLoading(false)
      return () => { cancelled = true }
    }
    setRepairRollbackExecution(null)
    setRepairRollbackExecutionError(null)
    setRepairRollbackExecutionLoading(true)
    fetchTeamBuilderRepairRollbackExecutionLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackExecution(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackExecutionError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackExecutionLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRollbackPostVerification(null)
      setRepairRollbackPostVerificationError(null)
      setRepairRollbackPostVerificationLoading(false)
      return () => { cancelled = true }
    }
    setRepairRollbackPostVerification(null)
    setRepairRollbackPostVerificationError(null)
    setRepairRollbackPostVerificationLoading(true)
    fetchTeamBuilderRepairRollbackPostVerificationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackPostVerification(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackPostVerificationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRollbackPostVerificationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairClosureRollup(null)
      setRepairClosureRollupError(null)
      setRepairClosureRollupLoading(false)
      return () => { cancelled = true }
    }
    setRepairClosureRollup(null)
    setRepairClosureRollupError(null)
    setRepairClosureRollupLoading(true)
    fetchTeamBuilderRepairClosureRollupLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairClosureRollup(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairClosureRollupError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairClosureRollupLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairGeneralizationTrial(null)
      setRepairGeneralizationTrialError(null)
      setRepairGeneralizationTrialLoading(false)
      return () => { cancelled = true }
    }
    setRepairGeneralizationTrial(null)
    setRepairGeneralizationTrialError(null)
    setRepairGeneralizationTrialLoading(true)
    fetchTeamBuilderRepairGeneralizationTrialLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairGeneralizationTrial(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairGeneralizationTrialError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairGeneralizationTrialLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealGeneratedFileSetTrial(null)
      setRepairRealGeneratedFileSetTrialError(null)
      setRepairRealGeneratedFileSetTrialLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealGeneratedFileSetTrial(null)
    setRepairRealGeneratedFileSetTrialError(null)
    setRepairRealGeneratedFileSetTrialLoading(true)
    fetchTeamBuilderRepairRealGeneratedFileSetTrialLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealGeneratedFileSetTrial(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealGeneratedFileSetTrialError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealGeneratedFileSetTrialLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunClosureRollup(null)
      setRepairRealRunClosureRollupError(null)
      setRepairRealRunClosureRollupLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunClosureRollup(null)
    setRepairRealRunClosureRollupError(null)
    setRepairRealRunClosureRollupLoading(true)
    fetchTeamBuilderRepairRealRunClosureRollupLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunClosureRollup(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunClosureRollupError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunClosureRollupLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunCandidateScan(null)
      setRepairRealRunCandidateScanError(null)
      setRepairRealRunCandidateScanLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunCandidateScan(null)
    setRepairRealRunCandidateScanError(null)
    setRepairRealRunCandidateScanLoading(true)
    fetchTeamBuilderRepairRealRunCandidateScanLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunCandidateScan(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunCandidateScanError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunCandidateScanLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunReplayPlan(null)
      setRepairRealRunReplayPlanError(null)
      setRepairRealRunReplayPlanLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunReplayPlan(null)
    setRepairRealRunReplayPlanError(null)
    setRepairRealRunReplayPlanLoading(true)
    fetchTeamBuilderRepairRealRunReplayPlanLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunReplayPlan(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunReplayPlanError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunReplayPlanLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunDiffPreview(null)
      setRepairRealRunDiffPreviewError(null)
      setRepairRealRunDiffPreviewLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunDiffPreview(null)
    setRepairRealRunDiffPreviewError(null)
    setRepairRealRunDiffPreviewLoading(true)
    fetchTeamBuilderRepairRealRunDiffPreviewLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunDiffPreview(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunDiffPreviewError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunDiffPreviewLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunDiffReview(null)
      setRepairRealRunDiffReviewError(null)
      setRepairRealRunDiffReviewLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunDiffReview(null)
    setRepairRealRunDiffReviewError(null)
    setRepairRealRunDiffReviewLoading(true)
    fetchTeamBuilderRepairRealRunDiffReviewLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunDiffReview(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunDiffReviewError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunDiffReviewLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunApplyGate(null)
      setRepairRealRunApplyGateError(null)
      setRepairRealRunApplyGateLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunApplyGate(null)
    setRepairRealRunApplyGateError(null)
    setRepairRealRunApplyGateLoading(true)
    fetchTeamBuilderRepairRealRunApplyGateLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyGate(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyGateError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyGateLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunApplyPreview(null)
      setRepairRealRunApplyPreviewError(null)
      setRepairRealRunApplyPreviewLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunApplyPreview(null)
    setRepairRealRunApplyPreviewError(null)
    setRepairRealRunApplyPreviewLoading(true)
    fetchTeamBuilderRepairRealRunApplyPreviewLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyPreview(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyPreviewError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyPreviewLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunApplyExecution(null)
      setRepairRealRunApplyExecutionError(null)
      setRepairRealRunApplyExecutionLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunApplyExecution(null)
    setRepairRealRunApplyExecutionError(null)
    setRepairRealRunApplyExecutionLoading(true)
    fetchTeamBuilderRepairRealRunApplyExecutionLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyExecution(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyExecutionError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunApplyExecutionLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunPostApplyVerification(null)
      setRepairRealRunPostApplyVerificationError(null)
      setRepairRealRunPostApplyVerificationLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunPostApplyVerification(null)
    setRepairRealRunPostApplyVerificationError(null)
    setRepairRealRunPostApplyVerificationLoading(true)
    fetchTeamBuilderRepairRealRunPostApplyVerificationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunPostApplyVerification(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunPostApplyVerificationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunPostApplyVerificationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunOutcomeReconciliation(null)
      setRepairRealRunOutcomeReconciliationError(null)
      setRepairRealRunOutcomeReconciliationLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunOutcomeReconciliation(null)
    setRepairRealRunOutcomeReconciliationError(null)
    setRepairRealRunOutcomeReconciliationLoading(true)
    fetchTeamBuilderRepairRealRunOutcomeReconciliationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunOutcomeReconciliation(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunOutcomeReconciliationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunOutcomeReconciliationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunRollbackReadiness(null)
      setRepairRealRunRollbackReadinessError(null)
      setRepairRealRunRollbackReadinessLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunRollbackReadiness(null)
    setRepairRealRunRollbackReadinessError(null)
    setRepairRealRunRollbackReadinessLoading(true)
    fetchTeamBuilderRepairRealRunRollbackReadinessLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackReadiness(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackReadinessError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackReadinessLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunRollbackExecution(null)
      setRepairRealRunRollbackExecutionError(null)
      setRepairRealRunRollbackExecutionLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunRollbackExecution(null)
    setRepairRealRunRollbackExecutionError(null)
    setRepairRealRunRollbackExecutionLoading(true)
    fetchTeamBuilderRepairRealRunRollbackExecutionLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackExecution(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackExecutionError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackExecutionLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairRealRunRollbackPostVerification(null)
      setRepairRealRunRollbackPostVerificationError(null)
      setRepairRealRunRollbackPostVerificationLoading(false)
      return () => { cancelled = true }
    }
    setRepairRealRunRollbackPostVerification(null)
    setRepairRealRunRollbackPostVerificationError(null)
    setRepairRealRunRollbackPostVerificationLoading(true)
    fetchTeamBuilderRepairRealRunRollbackPostVerificationLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackPostVerification(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackPostVerificationError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairRealRunRollbackPostVerificationLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setRepairSafetyPolicy(null)
      setRepairSafetyPolicyError(null)
      setRepairSafetyPolicyLoading(false)
      return () => { cancelled = true }
    }
    setRepairSafetyPolicy(null)
    setRepairSafetyPolicyError(null)
    setRepairSafetyPolicyLoading(true)
    fetchTeamBuilderRepairSafetyPolicyLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairSafetyPolicy(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairSafetyPolicyError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setRepairSafetyPolicyLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setClosureStatus(null)
      setClosureStatusError(null)
      setClosureStatusLoading(false)
      return () => { cancelled = true }
    }
    setClosureStatus(null)
    setClosureStatusError(null)
    setClosureStatusLoading(true)
    fetchTeamBuilderClosureStatusLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setClosureStatus(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setClosureStatusError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setClosureStatusLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setLlmReplayPlan(null)
      setLlmReplayPlanError(null)
      setLlmReplayPlanLoading(false)
      return () => { cancelled = true }
    }
    setLlmReplayPlan(null)
    setLlmReplayPlanError(null)
    setLlmReplayPlanLoading(true)
    fetchTeamBuilderLlmReplayPlanLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setLlmReplayPlan(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setLlmReplayPlanError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setLlmReplayPlanLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    if (!showTeamBuilderMaterialization) {
      setLlmReplayResult(null)
      setLlmReplayResultError(null)
      setLlmReplayResultLoading(false)
      return () => { cancelled = true }
    }
    setLlmReplayResult(null)
    setLlmReplayResultError(null)
    setLlmReplayResultLoading(true)
    fetchTeamBuilderLlmReplayResultLatest()
      .then((data) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setLlmReplayResult(data)
      })
      .catch((e) => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setLlmReplayResultError(String(e))
      })
      .finally(() => {
        if (cancelled && !showTeamBuilderMaterialization) return
        setLlmReplayResultLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, showTeamBuilderMaterialization])

  useEffect(() => {
    let cancelled = false
    setDoctorHealth(null)
    setDoctorError(null)
    setDoctorLoading(true)
    fetchDoctorHealth(entity.id, graph.selected_builder)
      .then((data) => {
        if (!cancelled) setDoctorHealth(data)
      })
      .catch((e) => {
        if (!cancelled) setDoctorError(String(e))
      })
      .finally(() => {
        if (!cancelled) setDoctorLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder])

  useEffect(() => {
    let cancelled = false
    setRuns([])
    setRunDetail(null)
    setRunsError(null)
    setRunsLoading(true)
    fetchRuns(entity.id, graph.selected_builder)
      .then((items) => {
        if (cancelled) return
        setRuns(items)
        setSelectedRunId((prev) => {
          if (prev && items.some((item) => item.trace_id === prev)) return prev
          return pickDefaultRun(items)
        })
      })
      .catch((e) => {
        if (cancelled) return
        setRunsError(String(e))
        setSelectedRunId(null)
      })
      .finally(() => {
        if (!cancelled) setRunsLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder])

  useEffect(() => {
    let cancelled = false
    if (!selectedRunId) {
      setRunDetail(null)
      return () => { cancelled = true }
    }
    setRunLoading(true)
    fetchRunDetail(entity.id, selectedRunId, graph.selected_builder)
      .then((data) => {
        if (!cancelled) setRunDetail(data)
      })
      .catch((e) => {
        if (!cancelled) {
          setRunsError(String(e))
          setRunDetail(null)
        }
      })
      .finally(() => {
        if (!cancelled) setRunLoading(false)
      })
    return () => { cancelled = true }
  }, [entity.id, graph.selected_builder, selectedRunId])

  const selectedItem = selectedItemId ? parseGraphItemId(selectedItemId) : null
  const selectedNode = selectedItem?.kind === 'worker'
    ? graph.nodes.find((node) => node.id === selectedItem.id) || null
    : null
  const selectedMaterial = selectedItem?.kind === 'material'
    ? graph.materials.find((material) => material.id === selectedItem.id) || null
    : null
  const isSmoke = entity.id.includes('csv_to_md')

  return (
    <div style={S.body}>
      <div style={S.graphWrap}>
        <TeamGraphCanvas
          graph={graph}
          selectedItemId={selectedItemId}
          onSelectItem={setSelectedItemId}
          runDetail={runDetail}
          doctorHealth={doctorHealth}
          materialization={showTeamBuilderMaterialization ? materialization : null}
        />
        {!sidePanelVisible && (
          <button
            type="button"
            title="打开右侧栏"
            aria-label="打开右侧栏"
            data-team-side-open
            style={S.restoreSideButton}
            onClick={() => setSidePanelVisible(true)}
          >
            <PanelRightOpen size={15} strokeWidth={1.8} />
            <span>右侧栏</span>
          </button>
        )}
      </div>
      {sidePanelVisible && (
        <GraphSidePanel
          graph={graph}
          selectedNode={selectedNode}
          selectedMaterial={selectedMaterial}
          builderValue={builderValue}
          onBuilderChange={onBuilderChange}
          onClose={() => setSidePanelVisible(false)}
          isSmoke={isSmoke}
          runs={runs}
          selectedRunId={selectedRunId}
          onSelectRun={setSelectedRunId}
          runDetail={runDetail}
          runsError={runsError}
          runLoading={runsLoading || runLoading}
          doctorHealth={doctorHealth}
          doctorError={doctorError}
          doctorLoading={doctorLoading}
          showTeamBuilderMaterialization={showTeamBuilderMaterialization}
          materialization={materialization}
          materializationError={materializationError}
          materializationLoading={materializationLoading}
          materialReport={materialReport}
          materialReportError={materialReportError}
          materialReportLoading={materialReportLoading}
          readClueResolutionPlan={readClueResolutionPlan}
          readClueResolutionError={readClueResolutionError}
          readClueResolutionLoading={readClueResolutionLoading}
          materialGapValidation={materialGapValidation}
          materialGapValidationError={materialGapValidationError}
          materialGapValidationLoading={materialGapValidationLoading}
          testReport={testReport}
          testReportError={testReportError}
          testReportLoading={testReportLoading}
          repairPlan={repairPlan}
          repairPlanError={repairPlanError}
          repairPlanLoading={repairPlanLoading}
          repairProbe={repairProbe}
          repairProbeError={repairProbeError}
          repairProbeLoading={repairProbeLoading}
          repairDryRun={repairDryRun}
          repairDryRunError={repairDryRunError}
          repairDryRunLoading={repairDryRunLoading}
          repairPatchCandidates={repairPatchCandidates}
          repairPatchCandidatesError={repairPatchCandidatesError}
          repairPatchCandidatesLoading={repairPatchCandidatesLoading}
          repairApplyGate={repairApplyGate}
          repairApplyGateError={repairApplyGateError}
          repairApplyGateLoading={repairApplyGateLoading}
          repairPatchDiffProposal={repairPatchDiffProposal}
          repairPatchDiffProposalError={repairPatchDiffProposalError}
          repairPatchDiffProposalLoading={repairPatchDiffProposalLoading}
          repairApproval={repairApproval}
          repairApprovalError={repairApprovalError}
          repairApprovalLoading={repairApprovalLoading}
          repairExecutionReadiness={repairExecutionReadiness}
          repairExecutionReadinessError={repairExecutionReadinessError}
          repairExecutionReadinessLoading={repairExecutionReadinessLoading}
          repairApplyPreview={repairApplyPreview}
          repairApplyPreviewError={repairApplyPreviewError}
          repairApplyPreviewLoading={repairApplyPreviewLoading}
          repairApplyExecution={repairApplyExecution}
          repairApplyExecutionError={repairApplyExecutionError}
          repairApplyExecutionLoading={repairApplyExecutionLoading}
          repairPostApplyVerification={repairPostApplyVerification}
          repairPostApplyVerificationError={repairPostApplyVerificationError}
          repairPostApplyVerificationLoading={repairPostApplyVerificationLoading}
          repairOutcomeReconciliation={repairOutcomeReconciliation}
          repairOutcomeReconciliationError={repairOutcomeReconciliationError}
          repairOutcomeReconciliationLoading={repairOutcomeReconciliationLoading}
          repairRollbackReadiness={repairRollbackReadiness}
          repairRollbackReadinessError={repairRollbackReadinessError}
          repairRollbackReadinessLoading={repairRollbackReadinessLoading}
          repairRollbackExecution={repairRollbackExecution}
          repairRollbackExecutionError={repairRollbackExecutionError}
          repairRollbackExecutionLoading={repairRollbackExecutionLoading}
          repairRollbackPostVerification={repairRollbackPostVerification}
          repairRollbackPostVerificationError={repairRollbackPostVerificationError}
          repairRollbackPostVerificationLoading={repairRollbackPostVerificationLoading}
          repairClosureRollup={repairClosureRollup}
          repairClosureRollupError={repairClosureRollupError}
          repairClosureRollupLoading={repairClosureRollupLoading}
          repairGeneralizationTrial={repairGeneralizationTrial}
          repairGeneralizationTrialError={repairGeneralizationTrialError}
          repairGeneralizationTrialLoading={repairGeneralizationTrialLoading}
          repairRealGeneratedFileSetTrial={repairRealGeneratedFileSetTrial}
          repairRealGeneratedFileSetTrialError={repairRealGeneratedFileSetTrialError}
          repairRealGeneratedFileSetTrialLoading={repairRealGeneratedFileSetTrialLoading}
          repairRealRunClosureRollup={repairRealRunClosureRollup}
          repairRealRunClosureRollupError={repairRealRunClosureRollupError}
          repairRealRunClosureRollupLoading={repairRealRunClosureRollupLoading}
          repairRealRunCandidateScan={repairRealRunCandidateScan}
          repairRealRunCandidateScanError={repairRealRunCandidateScanError}
          repairRealRunCandidateScanLoading={repairRealRunCandidateScanLoading}
          repairRealRunReplayPlan={repairRealRunReplayPlan}
          repairRealRunReplayPlanError={repairRealRunReplayPlanError}
          repairRealRunReplayPlanLoading={repairRealRunReplayPlanLoading}
          repairRealRunDiffPreview={repairRealRunDiffPreview}
          repairRealRunDiffPreviewError={repairRealRunDiffPreviewError}
          repairRealRunDiffPreviewLoading={repairRealRunDiffPreviewLoading}
          repairRealRunDiffReview={repairRealRunDiffReview}
          repairRealRunDiffReviewError={repairRealRunDiffReviewError}
          repairRealRunDiffReviewLoading={repairRealRunDiffReviewLoading}
          repairRealRunApplyGate={repairRealRunApplyGate}
          repairRealRunApplyGateError={repairRealRunApplyGateError}
          repairRealRunApplyGateLoading={repairRealRunApplyGateLoading}
          repairRealRunApplyPreview={repairRealRunApplyPreview}
          repairRealRunApplyPreviewError={repairRealRunApplyPreviewError}
          repairRealRunApplyPreviewLoading={repairRealRunApplyPreviewLoading}
          repairRealRunApplyExecution={repairRealRunApplyExecution}
          repairRealRunApplyExecutionError={repairRealRunApplyExecutionError}
          repairRealRunApplyExecutionLoading={repairRealRunApplyExecutionLoading}
          repairRealRunPostApplyVerification={repairRealRunPostApplyVerification}
          repairRealRunPostApplyVerificationError={repairRealRunPostApplyVerificationError}
          repairRealRunPostApplyVerificationLoading={repairRealRunPostApplyVerificationLoading}
          repairRealRunOutcomeReconciliation={repairRealRunOutcomeReconciliation}
          repairRealRunOutcomeReconciliationError={repairRealRunOutcomeReconciliationError}
          repairRealRunOutcomeReconciliationLoading={repairRealRunOutcomeReconciliationLoading}
          repairRealRunRollbackReadiness={repairRealRunRollbackReadiness}
          repairRealRunRollbackReadinessError={repairRealRunRollbackReadinessError}
          repairRealRunRollbackReadinessLoading={repairRealRunRollbackReadinessLoading}
          repairRealRunRollbackExecution={repairRealRunRollbackExecution}
          repairRealRunRollbackExecutionError={repairRealRunRollbackExecutionError}
          repairRealRunRollbackExecutionLoading={repairRealRunRollbackExecutionLoading}
          repairRealRunRollbackPostVerification={repairRealRunRollbackPostVerification}
          repairRealRunRollbackPostVerificationError={repairRealRunRollbackPostVerificationError}
          repairRealRunRollbackPostVerificationLoading={repairRealRunRollbackPostVerificationLoading}
          repairSafetyPolicy={repairSafetyPolicy}
          repairSafetyPolicyError={repairSafetyPolicyError}
          repairSafetyPolicyLoading={repairSafetyPolicyLoading}
          closureStatus={closureStatus}
          closureStatusError={closureStatusError}
          closureStatusLoading={closureStatusLoading}
          llmReplayPlan={llmReplayPlan}
          llmReplayPlanError={llmReplayPlanError}
          llmReplayPlanLoading={llmReplayPlanLoading}
          llmReplayResult={llmReplayResult}
          llmReplayResultError={llmReplayResultError}
          llmReplayResultLoading={llmReplayResultLoading}
        />
      )}
    </div>
  )
}

const Editor: React.FC<{ entity: TeamEntity }> = ({ entity }) => {
  const [detail, setDetail] = useState<CodeFileDetail | null>(null)
  const [graph, setGraph] = useState<TeamGraphData | null>(null)
  const [view, setView] = useState<'graph' | 'design' | 'source'>('graph')
  const [builder, setBuilder] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDetail(null)
    setGraph(null)
    setBuilder(null)
    setError(null)
    fetchDetail(entity.id).then(setDetail).catch((e) => setError(String(e)))
  }, [entity.id])

  useEffect(() => {
    let cancelled = false
    setGraph(null)
    setError(null)
    fetchGraph(entity.id, builder)
      .then((data) => { if (!cancelled) setGraph(data) })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [entity.id, builder])

  if (error) return <EmptyState text={`加载失败：${error}`} />
  if (!detail) return <EmptyState text="正在加载 team..." />

  const title = graph?.name || detail.name
  const subtitle = graph
    ? `${graph.spec_id} / ${graph.selected_builder} / ${detail.package}`
    : detail.package
  const builderValue = builder || graph?.selected_builder || ''

  return (
    <div style={S.root}>
      <div style={S.header}>
        <div style={{ minWidth: 0 }}>
          <div style={S.title}>{title}</div>
          <div style={S.subtitle}>{subtitle}</div>
        </div>
        <Segmented
          label="视图"
          current={view}
          onChange={(v) => setView(v as 'graph' | 'design' | 'source')}
          items={[
            { value: 'graph', label: '结构图' },
            { value: 'design', label: '设计说明' },
            { value: 'source', label: '源码' },
          ]}
        />
      </div>

      {view === 'graph' ? (
        graph ? (
          <TeamGraphView
            graph={graph}
            entity={entity}
            builderValue={builderValue}
            onBuilderChange={(value) => setBuilder(value)}
          />
        ) : (
          <EmptyState text="正在加载 team 结构图..." />
        )
      ) : (
        <CodeFileEditor key={`${detail.id}:${view}`} detail={detail} defaultView={view} />
      )}
    </div>
  )
}

export default Editor

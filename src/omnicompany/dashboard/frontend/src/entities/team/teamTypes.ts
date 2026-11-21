// team 域的数据契约(2026-08-06 从 index.tsx 拆出)。
// 背景: index.tsx 曾是一个 1.28 万行的单文件, 里面 类型 / 取数 / 呈现 / 注册 四层揉在一起;
// 2026-07 想拆首屏包, 只把整份复制成 TeamEditor.tsx 就停在半路 —— 那份副本无人 import,
// 却和本体同步长了 18 天的分叉, 全站 742 处旧圆角有 543 处堆在这两个文件里。
// 这次按四层真正拆开: teamTypes(纯类型, 零运行时) / teamApi(取数 + resolver) /
// TeamEditor(呈现, 经 index 懒加载) / index(注册壳)。
import type { Entity, EntityType } from '../types'

export interface TeamEntity extends Entity {
  type: 'team'
  package: string
  file_path: string
  has_design_md: boolean
}

export interface TeamBuilderSummary {
  name: string
  spec_id: string
  nodes: number
  edges: number
  entry: string
}

export interface TeamDefinitionRef {
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

export interface TeamGraphNode {
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

export interface TeamGraphEdge {
  id: string
  source: string
  target: string
  condition: string | null
  feedback: boolean
  material_id: string | null
  source_format: string | null
  target_format: string[]
}

export interface TeamGraphMaterial {
  id: string
  producers: string[]
  consumers: string[]
  is_external_input: boolean
  is_terminal_output: boolean
  definition: TeamDefinitionRef | null
}

export interface TeamGraphData {
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

export interface TeamRunSummary {
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

export interface TeamRunNodeStatus {
  node_id: string
  event_count: number
  first_at: string | null
  last_at: string | null
  event_types: Record<string, number>
  verdict_counts: Record<string, number>
}

export interface TeamRunTimelineEvent {
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

export interface TeamRunDetail {
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

export type ResourceKind = 'workspace' | 'database' | 'external'

export interface TeamResourceHint {
  id: ResourceKind
  label: string
  description: string
  workers: string[]
  evidence: string[]
  confidence: '演示推断' | '运行推断'
}

export interface TeamDoctorFinding {
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

export interface TeamDoctorHealth {
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

export interface TeamMaterializationLink {
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

export interface TeamMaterializationLinkGroup {
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

export interface TeamMaterializationWorkerRun {
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

export interface TeamMaterializationReviewIssue {
  worker_id: string
  severity: string
  category: string
  issue: string
  fix_hint: string
  format_in: string[]
  required_not_read: string[]
}

export interface TeamBuilderMaterialization {
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

export interface MaterialAttributionReportLink {
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

export interface MaterialAttributionQualityGate {
  id: string
  name: string
  status: 'pass' | 'warning' | 'fail' | string
  summary: string
  evidence: string[]
}

export interface MaterialAttributionWorkerReport {
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

export interface MaterialAttributionReadGroup {
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

export interface MaterialAttributionReport {
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

export interface TeamBuilderTestReport {
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

export interface TeamBuilderLlmStubCall {
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

export interface TeamBuilderRepairPlan {
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

export interface TeamBuilderRepairSafetyPolicy {
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

export interface TeamBuilderRepairProbeReport {
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

export interface TeamBuilderRepairDryRunReport {
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

export interface TeamBuilderRepairPatchCandidatesReport {
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

export interface TeamBuilderRepairApplyGateReport {
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

export interface TeamBuilderRepairPatchDiffProposalReport {
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

export interface TeamBuilderRepairApprovalReport {
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

export interface TeamBuilderRepairExecutionReadinessReport {
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

export interface TeamBuilderRepairApplyPreviewReport {
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

export interface TeamBuilderRepairApplyExecutionReport {
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

export interface TeamBuilderRepairPostApplyVerificationReport {
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

export interface TeamBuilderRepairOutcomeReconciliationReport {
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

export interface TeamBuilderRepairRollbackReadinessReport {
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

export interface TeamBuilderRepairRollbackExecutionReport {
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

export interface TeamBuilderRepairRollbackPostVerificationReport {
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

export interface TeamBuilderRepairClosureRollupReport {
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

export interface TeamBuilderRepairFileRecord {
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

export interface TeamBuilderRepairGeneralizationTrialReport {
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

export interface TeamBuilderRepairRealGeneratedFileSetTrialReport {
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

export interface TeamBuilderRepairRealRunCandidate {
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

export interface TeamBuilderRepairRealRunCandidateScanReport {
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

export interface TeamBuilderRepairRealRunReplayPlanReport {
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

export interface TeamBuilderRepairRealRunDiffRecord {
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

export interface TeamBuilderRepairRealRunDiffPreviewReport {
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

export interface TeamBuilderRepairRealRunDiffReviewItem {
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

export interface TeamBuilderRepairRealRunDiffReviewReport {
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

export interface TeamBuilderRepairRealRunApplyGateItem {
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

export interface TeamBuilderRepairRealRunApplyGateReport {
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

export interface TeamBuilderRepairRealRunApplyPreviewItem {
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

export interface TeamBuilderRepairRealRunApplyPreviewReport {
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

export interface TeamBuilderRepairRealRunApplyExecutionItem {
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

export interface TeamBuilderRepairRealRunApplyExecutionReport {
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

export interface TeamBuilderRepairRealRunPostApplyVerificationItem {
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

export interface TeamBuilderRepairRealRunPostApplyVerificationReport {
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

export interface TeamBuilderRepairRealRunOutcomeReconciliationReport {
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

export interface TeamBuilderRepairRealRunRollbackReadinessReport {
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

export interface TeamBuilderRepairRealRunRollbackExecutionReport {
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

export interface TeamBuilderRepairRealRunRollbackPostVerificationReport {
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

export interface TeamBuilderRepairRealRunClosureRollupReport {
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

export interface TeamBuilderReadClueResolutionPlan {
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

export interface TeamBuilderReadClueResolutionAction {
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

export interface TeamBuilderMaterialGapValidation {
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

export interface TeamBuilderClosureStatus {
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

export interface TeamBuilderHighStandardAudit {
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

export interface TeamBuilderProviderCoverageAudit {
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

export interface TeamBuilderProviderSameInputTrialPlan {
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

export interface TeamBuilderLlmReplayPlan {
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

export interface TeamBuilderLlmReplayResult {
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

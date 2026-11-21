import { test, expect, type Page } from '@playwright/test'
import { setupErrorLogging, waitAppReady } from './helpers'

async function openSystemModule(page: Page): Promise<void> {
  await waitAppReady(page)
  await page.locator('button[title]').nth(3).click()
}

async function openTeam(page: Page, teamId: string): Promise<void> {
  await openSystemModule(page)
  await page.locator('input[placeholder]').fill(teamId.split('/').slice(-3, -1).join('/'))
  await page.locator(`div[data-tree="team"] div[title="${teamId}"]`).click()
}

async function expectBusinessNodeCount(page: Page, expected: number): Promise<void> {
  await expect.poll(async () => page.locator('.react-flow__node').evaluateAll((nodes) => (
    nodes.filter((node) => !node.querySelector('[data-team-resource-node]') && !node.querySelector('[data-team-materialization-node]')).length
  )), { timeout: 15000 }).toBe(expected)
}

async function mockTeamBuilderMaterialization(page: Page): Promise<void> {
  await page.route(/\/api\/team-builder-materialization\/test-report\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        summary: '生成包测试 warning: 文件 10 个, Python 7 个, worker 3 个；业务 smoke 执行 2 个，模型桩 1 个，跳过真实调用 1 个。',
        verdict: 'warning',
        quality_gates: [
          { id: 'package_manifest', name: '生成包文件清单完整', status: 'pass', summary: '发现 10 个文件、3 个 worker 文件。', evidence: [] },
          { id: 'syntax_compile', name: 'Python 语法编译通过', status: 'pass', summary: '7 个 Python 文件语法检查通过。', evidence: [] },
          { id: 'import_build_team', name: '隔离导入和 build_team 通过', status: 'pass', summary: 'build_team 返回 team_observer_material_trial, 节点 3, 边 2。', evidence: [] },
          { id: 'build_bindings', name: 'build_bindings 覆盖 team 节点', status: 'pass', summary: 'bindings 覆盖 3 个节点。', evidence: [] },
          { id: 'worker_run_smoke', name: 'worker 业务 run smoke', status: 'warning', summary: '已执行 2 个 worker，模型桩验证 1 个，跳过真实调用 1 个，失败 0 个。', evidence: ['health_report_writer 模型桩=pass', 'health_report_writer 跳过: requires_llm'] },
        ],
        counts: {
          files: 10,
          python_files: 7,
          worker_files: 3,
          syntax_failures: 0,
          nodes: 3,
          bindings: 3,
          executed_workers: 2,
          stubbed_workers: 1,
          skipped_workers: 1,
          failed_workers: 0,
          doctor_findings: 2,
        },
        smoke: {
          team_id: 'team_observer_material_trial',
          entry: 'run_artifact_collector',
          nodes: ['run_artifact_collector', 'material_usage_mapper', 'health_report_writer'],
          binding_keys: ['run_artifact_collector', 'material_usage_mapper', 'health_report_writer'],
          missing_bindings: [],
          error: '',
        },
        worker_run_smoke: {
          status: 'warning',
          executed_workers: [
            { worker_id: 'run_artifact_collector', kind: 'pass', input_materials: ['team_observer.input.observation_request'], output_material: 'team_observer.material.run_artifact_bundle', diagnosis: 'team=team_observer_material_trial' },
            { worker_id: 'material_usage_mapper', kind: 'pass', input_materials: ['team_observer.material.run_artifact_bundle'], output_material: 'team_observer.material.material_lineage_graph', diagnosis: 'lineage graph built' },
          ],
          stubbed_workers: [
            {
              worker_id: 'health_report_writer',
              kind: 'pass',
              input_materials: ['team_observer.input.observation_request', 'team_observer.material.material_lineage_graph'],
              output_material: 'team_observer.material.health_report',
              diagnosis: '健康报告生成完成',
              stub: 'call_llm_json',
              llm_stub_calls: [
                {
                  model: 'qwen-3.6-plus',
                  max_tokens: 4096,
                  system_chars: 42,
                  user_chars: 512,
                  system_preview: '你是 team 运行健康观察助手',
                  user_preview: '请结合观察请求和物料血缘图输出 summary_cn、risks、next_checks。',
                  expected_output_keys: ['summary_cn', 'risks', 'next_checks'],
                  stub_response_keys: ['summary_cn', 'risks', 'next_checks'],
                  has_json_instruction: true,
                  has_chinese_instruction: true,
                },
              ],
            },
          ],
          skipped_workers: [
            { worker_id: 'health_report_writer', reason: 'requires_llm', summary: 'worker 会调用 LLM，当前 smoke 不进行真实模型调用。', missing_inputs: [] },
          ],
          failed_workers: [],
          seed_materials: ['team_observer.input.observation_request'],
          produced_materials: ['team_observer.material.run_artifact_bundle', 'team_observer.material.material_lineage_graph', 'team_observer.material.health_report'],
          llm_stub_calls: [
            {
              model: 'qwen-3.6-plus',
              max_tokens: 4096,
              system_chars: 42,
              user_chars: 512,
              system_preview: '你是 team 运行健康观察助手',
              user_preview: '请结合观察请求和物料血缘图输出 summary_cn、risks、next_checks。',
              expected_output_keys: ['summary_cn', 'risks', 'next_checks'],
              stub_response_keys: ['summary_cn', 'risks', 'next_checks'],
              has_json_instruction: true,
              has_chinese_instruction: true,
            },
          ],
          error: '',
        },
        doctor_findings: [
          {
            id: 'team_builder.worker_run_smoke.requires_llm:health_report_writer',
            check_id: 'team_builder.worker_run_smoke.requires_llm',
            level: 'advisory',
            severity: 'MEDIUM',
            location: 'node:health_report_writer',
            target_kind: 'node',
            target_id: 'health_report_writer',
            node_ids: ['health_report_writer'],
            edge_ids: [],
            material_ids: [],
            observation: '业务 run smoke 跳过：这个 worker 需要调用 LLM。',
            implication: '完整端到端通过还需要受控 LLM smoke 或模型调用 stub。',
            cross_refs: [],
          },
        ],
        contract_coverage: {
          available: true,
          run_id: 'e2e-materialization-001',
          team_name: 'team_observer_material_trial',
          verdict: 'warning',
          status: 'missing_contract',
          summary: '当前 generated team `team_observer_material_trial` 没有同名 tests/teams contract；不能把 smoke test 等同于 acceptance。',
          counts: {
            available_contracts: 2,
            matching_contracts: 0,
            executed_contracts: 0,
            missing_contracts: 1,
          },
          quality_gates: [
            { id: 'contract_registry_visible', name: 'contract 注册表可见', status: 'pass', summary: '发现 2 个 tests/teams contract。', evidence: ['tests/teams/csv_to_md/test_contract.py', 'tests/teams/repo_absorption/test_contract.py'] },
            { id: 'generated_team_contract_configured', name: '当前 generated team 有 contract', status: 'warning', summary: 'team_observer_material_trial 还没有同名 contract；不能把 smoke test 等同于 acceptance。', evidence: [] },
            { id: 'contract_execution_explicit', name: 'contract 执行需要显式触发', status: 'pass', summary: '覆盖报告只识别 contract，不在页面刷新时自动执行 pytest 或真实 pipeline。', evidence: ['pytest --team-mode=programmatic tests/teams/<team>/test_contract.py'] },
          ],
          matching_contracts: [],
          available_contracts: [
            { slug: 'csv_to_md', pipeline_name: 'csv-to-md', path: 'tests/teams/csv_to_md/test_contract.py', mode: 'pytest_team_contract', status: 'available' },
            { slug: 'repo_absorption', pipeline_name: 'repo-absorption', path: 'tests/teams/repo_absorption/test_contract.py', mode: 'pytest_team_contract', status: 'available' },
          ],
          next_action: '为 team_observer_material_trial 新增 tests/teams/team_observer_material_trial/test_contract.py，定义输入样例、期望输出和失败样例。',
          source: {
            contract_root: 'tests/teams',
            contract_coverage_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_contract_coverage.json',
          },
        },
        source: {
          code_package_files: '_scratch/team_builder_real_material_validation/e2e/code_package_files',
          test_package_dir: '_scratch/team_builder_test_reports/e2e/team_observer_material_trial',
          report_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_test_report.json',
          doctor_findings_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_doctor_findings.json',
          contract_coverage_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_contract_coverage.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-plan\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'validation_gap',
        summary: '当前主要是 2 条验证缺口；不应直接修改生成代码。',
        counts: {
          actions: 2,
          repair_required: 0,
          validation_gap: 2,
          observe_only: 0,
          auto_safe: 0,
        },
        actions: [
          {
            id: 'repair_action:0',
            finding_id: 'team_builder.worker_run_smoke.requires_llm:health_report_writer',
            check_id: 'team_builder.worker_run_smoke.requires_llm',
            level: 'advisory',
            location: 'node:health_report_writer',
            category: 'validation_gap',
            auto_safe: false,
            observation: '业务 run smoke 跳过：这个 worker 需要调用 LLM。',
            rationale: 'finding 表示真实模型调用尚未验证，不代表 worker 代码需要修复。',
            next_action: '补受控 LLM smoke 或真实模型回放；当前不改生成代码。',
            validation_actions: [
              {
                id: 'validate_llm_gap:inspect_replay_plan',
                title: '查看受控 LLM 回放计划',
                summary: '检查 LLM 桩调用是否具备模型、JSON 输出键、中文约束和执行前置条件。',
                action_kind: 'api_probe',
                endpoint: '/api/team-builder-materialization/llm-replay-plan/latest',
                expected_result: '回放计划应为 ready_for_controlled_replay。',
                safety: '只生成计划，不调用真实模型。',
              },
            ],
            node_ids: ['health_report_writer'],
            material_ids: [],
          },
        ],
        source: {
          repair_plan_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_plan.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-probe\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001-repair-probe',
        team_name: 'team_builder_repair_probe',
        verdict: 'pass',
        summary: '故障修复探针 pass: 实际执行 1 个受控失败 worker，捕获失败 1 个，doctor finding 1 条，repair_required 1 条，auto_safe 0 条。',
        counts: {
          captured_failures: 1,
          doctor_findings: 1,
          repair_required: 1,
          validation_gap: 0,
          auto_safe: 0,
        },
        quality_gates: [
          { id: 'probe_package_imports', name: '故障探针包可导入', status: 'pass', summary: 'build_team 返回 team_builder_repair_probe, 节点 1。', evidence: ['failure_probe_worker'] },
          { id: 'worker_failure_captured', name: 'worker 失败被 smoke 捕获', status: 'pass', summary: '捕获 1 个失败 worker，并生成 doctor finding。', evidence: ['failure_probe_worker: controlled failure'] },
          { id: 'repair_classified_as_plan_only', name: 'repair 进入补丁计划而非自动改码', status: 'pass', summary: 'repair_required=1, auto_safe=0；策略要求只生成补丁计划。', evidence: ['runtime_failure_patch_plan_only'] },
        ],
        worker_run_smoke: {
          status: 'fail',
          failed_workers: [
            {
              worker_id: 'failure_probe_worker',
              kind: 'fail',
              diagnosis: 'controlled failure: repair probe worker returned FAIL on purpose',
              input_materials: ['team_builder_probe.input.observation_request'],
              output_material: 'team_builder_probe.material.failed_report',
            },
          ],
          executed_workers: [],
          error: '',
        },
        doctor_findings: [
          {
            id: 'team_builder.worker_run_smoke.failed:failure_probe_worker',
            check_id: 'team_builder.worker_run_smoke.failed',
            level: 'blocking',
            severity: 'CRITICAL',
            location: 'node:failure_probe_worker',
            target_kind: 'node',
            target_id: 'failure_probe_worker',
            node_ids: ['failure_probe_worker'],
            edge_ids: [],
            material_ids: ['team_builder_probe.input.observation_request', 'team_builder_probe.material.failed_report'],
            observation: '业务 run smoke 执行失败：controlled failure',
            implication: '生成 team 的真实业务链路已经出现可复现失败，应进入 doctor/repair 阶段。',
            cross_refs: [],
          },
        ],
        repair_plan: {
          verdict: 'repair_required',
          summary: '受控失败已被归类为需要修复准备；当前安全策略只允许生成补丁计划，不允许自动修改生成代码。',
          counts: { actions: 1, repair_required: 1, validation_gap: 0, auto_safe: 0 },
          actions: [
            {
              id: 'repair_action:0',
              finding_id: 'team_builder.worker_run_smoke.failed:failure_probe_worker',
              check_id: 'team_builder.worker_run_smoke.failed',
              level: 'blocking',
              location: 'node:failure_probe_worker',
              category: 'repair_required',
              auto_safe: false,
              observation: '业务 run smoke 执行失败：controlled failure',
              rationale: '运行时失败可能需要业务语义判断，不能只凭异常文本自动修改生成代码。',
              next_action: '定位源码、输入 material 和失败诊断，生成补丁计划；必须人工确认后才能改代码。',
              validation_actions: [],
              node_ids: ['failure_probe_worker'],
              material_ids: ['team_builder_probe.input.observation_request', 'team_builder_probe.material.failed_report'],
            },
          ],
        },
        source: {
          probe_package_dir: '_scratch/team_builder_repair_probe/e2e/team_builder_repair_probe',
          repair_probe_material: '_scratch/team_builder_repair_probe/e2e/materials/team_repair_probe_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-dry-run\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001-repair-dry-run',
        team_name: 'team_builder_repair_probe',
        verdict: 'pass',
        summary: '修复干跑探针 pass: 修复前失败 1 个，doctor finding 1 条；应用 scratch 补丁 1 个文件后，失败 0 个，doctor finding 0 条。',
        counts: {
          before_failures: 1,
          before_findings: 1,
          repair_required: 1,
          patch_files: 1,
          after_failures: 0,
          after_findings: 0,
          fixed_workers: 1,
          auto_safe: 0,
        },
        quality_gates: [
          { id: 'before_failure_captured', name: '修复前失败可复现', status: 'pass', summary: '修复前捕获失败 1 个，doctor finding 1 条。', evidence: ['controlled failure'] },
          { id: 'patch_plan_generated', name: '最小补丁计划已生成', status: 'pass', summary: '补丁计划修改 1 个文件，repair_required=1。', evidence: ['workers/failure_probe_worker.py'] },
          { id: 'patch_scope_confined', name: '补丁限制在 scratch 探针包', status: 'pass', summary: '补丁只触碰 scratch 中的 workers/failure_probe_worker.py。', evidence: ['_scratch/team_builder_repair_dry_run/e2e/team_builder_repair_probe/workers/failure_probe_worker.py'] },
          { id: 'after_worker_smoke_passed', name: '修复后 worker smoke 通过', status: 'pass', summary: '修复后执行 1 个 worker，失败 0 个。', evidence: ['failure_probe_worker=pass'] },
          { id: 'after_doctor_clean', name: '修复后 doctor finding 清零', status: 'pass', summary: '修复后 doctor finding 0 条，worker status=pass。', evidence: [] },
        ],
        patch_plan: {
          id: 'repair_patch_plan:failure_probe_worker:dry_run',
          title: '把受控失败 worker 改为通过返回',
          summary: '这是 scratch 内干跑补丁：只修改探针包的 failure_probe_worker.py，把可复现失败改成 PASS 返回，用于验证 repair 后重跑链路。',
          finding_ids: ['team_builder.worker_run_smoke.failed:failure_probe_worker'],
          policy_rule_ids: ['runtime_failure_patch_plan_only'],
          changed_files: ['workers/failure_probe_worker.py'],
          dry_run_applied: true,
          scope: 'scratch_only',
          auto_safe: false,
          rationale: '运行失败能自动生成最小补丁计划，但真实 generated code 仍需人工确认。',
          verification_commands: ['GET /api/team-builder-materialization/repair-dry-run/latest'],
          diff: '-            kind=VerdictKind.FAIL\\n+            kind=VerdictKind.PASS\\n',
        },
        before: {
          worker_run_smoke: {
            status: 'fail',
            failed_workers: [{ worker_id: 'failure_probe_worker', kind: 'fail', diagnosis: 'controlled failure' }],
            doctor_findings: [],
          },
          repair_actions: [],
        },
        after: {
          worker_run_smoke: {
            status: 'pass',
            executed_workers: [{ worker_id: 'failure_probe_worker', kind: 'pass', diagnosis: 'repair dry-run success' }],
            failed_workers: [],
            doctor_findings: [],
          },
        },
        source: {
          probe_package_dir: '_scratch/team_builder_repair_dry_run/e2e/team_builder_repair_probe',
          patched_file: '_scratch/team_builder_repair_dry_run/e2e/team_builder_repair_probe/workers/failure_probe_worker.py',
          repair_dry_run_material: '_scratch/team_builder_repair_dry_run/e2e/materials/team_repair_dry_run_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-patch-candidates\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'ready_for_manual_patch',
        summary: '修复候选补丁计划 ready_for_manual_patch: repair action 1 条，repair_required 候选 1 条，已定位源码 1 条，受控干跑参考已通过。',
        counts: {
          actions: 1,
          candidates: 1,
          source_located: 1,
          source_missing: 0,
          dry_run_verified: 1,
          auto_safe: 0,
          manual_required: 1,
        },
        quality_gates: [
          { id: 'repair_required_inputs', name: 'repair_required 输入明确', status: 'pass', summary: '1 条 repair_required action 进入候选补丁计划。', evidence: ['team_builder.worker_run_smoke.failed:failure_probe_worker'] },
          { id: 'source_locator', name: 'worker 源文件可定位', status: 'pass', summary: '1/1 条候选已定位 worker 源文件。', evidence: [] },
          { id: 'dry_run_reference', name: '受控干跑参考通过', status: 'pass', summary: '受控可修复故障已在 scratch 内完成补丁干跑并清零 finding。', evidence: ['repair_dry_run=pass'] },
          { id: 'auto_apply_blocked', name: '自动改码仍然阻断', status: 'pass', summary: '候选补丁计划只允许人工确认或 scratch 干跑，不允许自动修改真实 generated code。', evidence: ['auto_safe=0'] },
        ],
        candidates: [
          {
            id: 'repair_patch_candidate:0',
            status: 'source_located',
            finding_id: 'team_builder.worker_run_smoke.failed:failure_probe_worker',
            check_id: 'team_builder.worker_run_smoke.failed',
            worker_id: 'failure_probe_worker',
            category: 'repair_required',
            policy_rule_id: 'runtime_failure_patch_plan_only',
            automation_level: 'patch_plan_only',
            auto_safe: false,
            summary: '已定位 failure_probe_worker 的 worker 源文件，可进入人工确认或 AI 补丁生成。',
            observation: '业务 run smoke 执行失败：controlled failure',
            next_action: '先审阅定位到的 worker 源码和输入输出 material；生成最小补丁计划后，只能在 scratch 或人工确认路径应用并重跑验证。',
            source_candidates: [
              {
                path: '_scratch/team_builder_test_reports/e2e/team_observer_material_trial/workers/failure_probe_worker.py',
                exists: true,
                material_ids: ['material:team_builder_probe.failure_probe_worker.py'],
                excerpt: 'class FailureProbeWorker',
              },
            ],
            proposed_patch: {
              mode: 'manual_or_ai_generated',
              scope: 'generated_worker_only',
              changed_files: ['_scratch/team_builder_test_reports/e2e/team_observer_material_trial/workers/failure_probe_worker.py'],
              diff: '',
              reason: '真实 generated worker 的补丁需要基于源码和业务语义生成；当前层只产出候选补丁计划，不直接改代码。',
            },
            verification_commands: [
              'GET /api/team-builder-materialization/test-report/latest',
              'GET /api/team-builder-materialization/doctor-findings/latest',
              'GET /api/team-builder-materialization/closure/latest',
            ],
            safety: {
              dry_run_first: true,
              requires_human_confirmation: true,
              auto_apply_allowed: false,
              reason: 'repair_required 表示确有运行失败，但仍不能绕过人工确认直接修改真实 generated code。',
            },
          },
        ],
        dry_run_reference: {
          verdict: 'pass',
          summary: '修复干跑探针 pass: 修复前失败 1 个，doctor finding 1 条；应用 scratch 补丁 1 个文件后，失败 0 个，doctor finding 0 条。',
          counts: { before_failures: 1, patch_files: 1, after_failures: 0, after_findings: 0 },
          source: { repair_dry_run_material: '_scratch/team_builder_repair_dry_run/e2e/materials/team_repair_dry_run_report.json' },
        },
        source: {
          repair_patch_candidates_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_patch_candidates.json',
          repair_plan_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_plan.json',
          test_report_endpoint: '/api/team-builder-materialization/test-report/latest',
          repair_dry_run_endpoint: '/api/team-builder-materialization/repair-dry-run/latest',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-apply-gate\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'ready_for_human_review',
        summary: '1 条候选已满足人工审阅前置条件；仍不允许自动应用真实补丁。',
        counts: {
          candidates: 1,
          source_located: 1,
          dry_run_verified: 1,
          manual_required: 1,
          auto_apply_allowed: 0,
          review_items: 1,
          apply_ready: 1,
        },
        quality_gates: [
          { id: 'candidate_review_scope', name: '候选范围可审阅', status: 'pass', summary: '1/1 条候选已定位源码。', evidence: [] },
          { id: 'dry_run_reference_required', name: '受控干跑是前置条件', status: 'pass', summary: '受控 repair dry-run 已通过，可以作为真实修复前置参考。', evidence: ['dry_run_verified=1'] },
          { id: 'manual_review_required', name: '人工确认仍然必需', status: 'pass', summary: '1/1 条候选要求人工确认。', evidence: ['manual_required=1'] },
          { id: 'auto_apply_blocked', name: '真实自动应用被阻断', status: 'pass', summary: '当前应用门不允许自动修改真实 generated code。', evidence: ['auto_apply_allowed=0'] },
          { id: 'verification_commands_present', name: '回查命令完整', status: 'pass', summary: '候选补丁均列出 test-report、doctor-findings 和 closure 等回查入口。', evidence: [] },
        ],
        review_items: [
          {
            id: 'repair_apply_gate:0',
            candidate_id: 'repair_patch_candidate:0',
            status: 'ready_for_human_review',
            worker_id: 'failure_probe_worker',
            finding_id: 'team_builder.worker_run_smoke.failed:failure_probe_worker',
            policy_rule_id: 'runtime_failure_patch_plan_only',
            changed_files: ['_scratch/team_builder_test_reports/e2e/team_observer_material_trial/workers/failure_probe_worker.py'],
            source_files: ['_scratch/team_builder_test_reports/e2e/team_observer_material_trial/workers/failure_probe_worker.py'],
            required_confirmations: [
              '确认 finding 仍然可复现，且确实指向该 worker。',
              '确认源码文件、输入 material、输出 material 与候选补丁范围一致。',
              '确认补丁 diff 只触碰候选 generated worker 文件。',
              '确认回查命令至少覆盖 test-report、doctor-findings 和 closure。',
              '确认人工批准后才允许进入真实补丁生成或应用流程。',
            ],
            verification_commands: [
              'GET /api/team-builder-materialization/test-report/latest',
              'GET /api/team-builder-materialization/doctor-findings/latest',
              'GET /api/team-builder-materialization/closure/latest',
            ],
            apply_modes: [
              { id: 'scratch_preview', name: 'scratch 预览', allowed: true, summary: '允许在 scratch 或临时副本中生成补丁 diff 并重跑验证。' },
              { id: 'manual_patch', name: '人工确认后应用', allowed: false, summary: '当前接口不执行真实改码；需要后续人工确认协议或显式执行接口。' },
            ],
            blocked_reasons: [],
            safety: {
              auto_apply_allowed: false,
              requires_human_confirmation: true,
              reason: '修复应用门只负责审阅放行条件，不会自动修改真实 generated code。',
            },
          },
        ],
        source: {
          repair_apply_gate_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_apply_gate.json',
          repair_patch_candidates_endpoint: '/api/team-builder-materialization/repair-patch-candidates/latest',
          repair_patch_candidates_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_patch_candidates.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-patch-diff-proposal\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有 repair_required 候选；无需生成补丁 diff。',
        counts: {
          candidates: 0,
          diff_ready: 0,
          needs_ai_or_human_diff: 0,
          blocked: 0,
          unsafe_targets: 0,
        },
        quality_gates: [
          { id: 'candidate_scope_ready', name: '候选范围可用于生成 diff', status: 'pass', summary: '候选均已通过基础审阅范围检查。', evidence: [] },
          { id: 'target_scope_safe', name: '补丁目标范围安全', status: 'pass', summary: '没有候选把 contract 文件列为 diff 目标。', evidence: [] },
          { id: 'diff_available', name: '可审阅 diff 已生成', status: 'pass', summary: '当前没有候选需要 diff。', evidence: [] },
          { id: 'no_real_write', name: '不写真实文件', status: 'pass', summary: 'diff proposal 只写 material 报告，不修改 generated package。', evidence: ['writes_files=false'] },
        ],
        proposals: [],
        source: {
          repair_patch_diff_proposal_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_patch_diff_proposal.json',
          repair_patch_candidates_endpoint: '/api/team-builder-materialization/repair-patch-candidates/latest',
          repair_apply_gate_endpoint: '/api/team-builder-materialization/repair-apply-gate/latest',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-approval\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有可批准的补丁 diff。',
        counts: {
          proposals: 0,
          approvable: 0,
          approved: 0,
          awaiting_approval: 0,
          stale_or_mismatch: 0,
        },
        quality_gates: [
          { id: 'diff_is_approvable', name: '存在可批准 diff', status: 'pass', summary: '当前没有 proposal 需要批准。', evidence: [] },
          { id: 'approval_matches_diff', name: '批准绑定当前 diff', status: 'pass', summary: '没有发现 diff sha256 不匹配的批准记录。', evidence: [] },
          { id: 'explicit_approval_recorded', name: '显式人工批准已记录', status: 'pass', summary: '当前没有可批准 diff。', evidence: [] },
          { id: 'no_apply_side_effect', name: '批准记录不应用补丁', status: 'pass', summary: 'approval 只写入批准 material，不修改 generated code。', evidence: ['writes_files=false'] },
        ],
        approval_items: [],
        source: {
          repair_patch_diff_proposal_endpoint: '/api/team-builder-materialization/repair-patch-diff-proposal/latest',
          repair_approval_records_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_approval_records.json',
          repair_approval_report_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_approval_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-execution-readiness\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有 repair_required 候选；真实修复执行未开启。',
        counts: {
          candidates: 0,
          review_ready: 0,
          diff_ready: 0,
          approval_recorded: 0,
          execution_ready: 0,
          blocked: 0,
        },
        quality_gates: [
          { id: 'apply_gate_review_ready', name: '应用门已允许审阅', status: 'pass', summary: '当前没有 repair_required 候选。', evidence: [] },
          { id: 'patch_diff_present', name: '实际 diff 已生成', status: 'pass', summary: '当前没有需要生成 diff 的候选。', evidence: [] },
          { id: 'target_scope_safe', name: '补丁目标范围安全', status: 'pass', summary: '没有候选把 contract 文件列为修复目标。', evidence: [] },
          { id: 'explicit_approval_recorded', name: '显式人工批准', status: 'pass', summary: '当前没有候选需要人工批准。', evidence: [] },
          { id: 'auto_apply_blocked', name: '自动应用仍被阻断', status: 'pass', summary: '执行就绪检查只报告条件，不会自动修改真实 generated code。', evidence: ['auto_apply_allowed=0'] },
        ],
        execution_items: [],
        source: {
          repair_execution_readiness_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_execution_readiness.json',
          repair_apply_gate_endpoint: '/api/team-builder-materialization/repair-apply-gate/latest',
          repair_patch_candidates_endpoint: '/api/team-builder-materialization/repair-patch-candidates/latest',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-apply-preview\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有可执行候选；无需生成应用预览。',
        counts: {
          items: 0,
          preview_ready: 0,
          blocked: 0,
          files_written: 0,
          real_writes: 0,
        },
        quality_gates: [
          { id: 'execution_ready_required', name: '执行就绪已放行', status: 'pass', summary: '当前没有候选需要预览。', evidence: [] },
          { id: 'scratch_only', name: '只写 scratch 副本', status: 'pass', summary: '应用预览只写 _scratch before/after 文件，不写真实 generated code。', evidence: ['files_written=0', 'real_writes=0'] },
          { id: 'preview_files_created', name: '预览文件已生成', status: 'pass', summary: '当前没有候选需要预览。', evidence: [] },
        ],
        preview_items: [],
        source: {
          repair_apply_preview_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_apply_preview.json',
          repair_execution_readiness_endpoint: '/api/team-builder-materialization/repair-execution-readiness/latest',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-apply-execution\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有可执行候选；真实应用未开启。',
        counts: {
          items: 0,
          preview_ready: 0,
          applied: 0,
          blocked: 0,
          stale_or_mismatch: 0,
          real_writes: 0,
          file_set_ready: 0,
          file_set_applied: 0,
        },
        quality_gates: [
          { id: 'preview_required', name: '必须先通过应用预览', status: 'pass', summary: '当前没有候选需要真实应用。', evidence: [] },
          { id: 'explicit_execute_only', name: '只允许显式执行', status: 'pass', summary: 'GET 报告接口不会写真实文件；只有 POST execute 且确认 token 齐全时才写入目标文件。', evidence: ['get_writes_files=false', 'post_requires=confirm_real_file_write'] },
          { id: 'apply_record_matches_current', name: '应用记录匹配当前文件', status: 'pass', summary: '没有发现已应用记录与当前目标文件不匹配。', evidence: [] },
        ],
        apply_items: [],
        records: [],
        source: {
          repair_apply_preview_endpoint: '/api/team-builder-materialization/repair-apply-preview/latest',
          repair_apply_execution_records_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_apply_execution_records.json',
          repair_apply_execution_report_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_apply_execution_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-post-apply-verification\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有已应用补丁；无需执行应用后验证。',
        counts: {
          applied: 0,
          verified: 0,
          pending: 0,
          failed: 0,
          contract_failed: 0,
          doctor_findings: 0,
          repair_required: 0,
        },
        quality_gates: [
          { id: 'applied_records_present', name: '存在真实应用记录', status: 'pass', summary: '当前没有已应用补丁。', evidence: [] },
          { id: 'post_apply_verification_executed', name: '应用后验证已执行', status: 'pass', summary: '当前没有已应用补丁需要验证。', evidence: [] },
        ],
        verification_items: [],
        source: {
          repair_apply_execution_endpoint: '/api/team-builder-materialization/repair-apply-execution/latest',
          repair_post_apply_verification_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_post_apply_verification_result.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-outcome-reconciliation\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有已应用补丁；无需做补丁前后对账。',
        counts: {
          applied: 0,
          reconciled: 0,
          missing_baseline: 0,
          resolved_findings: 0,
          introduced_findings: 0,
          persistent_findings: 0,
          pending_verification: 0,
        },
        quality_gates: [
          { id: 'baseline_available', name: '应用前快照存在', status: 'pass', summary: '当前没有已应用补丁。', evidence: [] },
          { id: 'post_apply_verification_available', name: '应用后验证可用', status: 'pass', summary: '当前没有已应用补丁。', evidence: [] },
          { id: 'no_new_findings', name: '没有新增 finding', status: 'pass', summary: '应用后没有新增 doctor finding。', evidence: [] },
          { id: 'original_findings_resolved', name: '原 finding 已消除', status: 'pass', summary: '当前没有原 finding 需要消除。', evidence: [] },
        ],
        reconciliation_items: [],
        source: {
          repair_apply_execution_endpoint: '/api/team-builder-materialization/repair-apply-execution/latest',
          repair_post_apply_verification_endpoint: '/api/team-builder-materialization/repair-post-apply-verification/latest',
          repair_outcome_reconciliation_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_outcome_reconciliation.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-rollback-readiness\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有已应用补丁；无需准备回滚。',
        counts: {
          applied: 0,
          rollback_ready: 0,
          blocked: 0,
          stale_or_mismatch: 0,
          missing_before_snapshot: 0,
          real_writes: 0,
          file_set_ready: 0,
        },
        quality_gates: [
          { id: 'explicit_rollback_only', name: '只允许显式回滚', status: 'pass', summary: 'GET 报告接口不会写真实文件；后续真实回滚必须走 POST execute 和确认 token。', evidence: ['get_writes_files=false', 'post_requires=confirm_real_file_rollback'] },
          { id: 'current_file_matches_applied_after', name: '当前文件等于应用后内容', status: 'pass', summary: '所有已应用记录的当前文件 sha 都等于 after sha。', evidence: [] },
          { id: 'before_snapshot_available', name: '应用前快照可用', status: 'pass', summary: '所有已应用记录都有可校验 before 快照。', evidence: [] },
          { id: 'rollback_target_scope_safe', name: '回滚目标范围安全', status: 'pass', summary: '没有发现越界或缺失目标文件。', evidence: [] },
        ],
        rollback_items: [],
        source: {
          repair_apply_execution_endpoint: '/api/team-builder-materialization/repair-apply-execution/latest',
          repair_rollback_readiness_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_rollback_readiness.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-rollback-execution\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有已应用补丁；真实回滚未开启。',
        counts: {
          items: 0,
          ready: 0,
          rolled_back: 0,
          blocked: 0,
          stale_or_mismatch: 0,
          real_writes: 0,
          file_set_ready: 0,
          file_set_rolled_back: 0,
        },
        quality_gates: [
          { id: 'rollback_readiness_required', name: '必须先通过回滚就绪检查', status: 'pass', summary: '所有回滚项都已通过就绪检查或已有回滚记录。', evidence: [] },
          { id: 'explicit_rollback_execute_only', name: '只允许显式执行回滚', status: 'pass', summary: 'GET 报告接口不会写真实文件；只有 POST execute 且确认 token 齐全时才写入目标文件。', evidence: ['get_writes_files=false', 'post_requires=confirm_real_file_rollback'] },
          { id: 'rollback_record_matches_current', name: '回滚记录匹配当前文件', status: 'pass', summary: '没有发现回滚记录与当前目标文件不匹配。', evidence: [] },
        ],
        rollback_items: [],
        records: [],
        source: {
          repair_rollback_readiness_endpoint: '/api/team-builder-materialization/repair-rollback-readiness/latest',
          repair_rollback_execution_records_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_rollback_execution_records.json',
          repair_rollback_execution_report_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_rollback_execution_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-rollback-post-verification\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前没有已回滚补丁；无需执行回滚后验证。',
        counts: {
          rolled_back: 0,
          verified: 0,
          pending: 0,
          failed: 0,
          doctor_findings: 0,
          repair_required: 0,
        },
        quality_gates: [
          { id: 'rolled_back_records_present', name: '存在真实回滚记录', status: 'pass', summary: '当前没有已回滚补丁。', evidence: [] },
          { id: 'post_rollback_verification_executed', name: '回滚后验证已执行', status: 'pass', summary: '当前没有已回滚补丁需要验证。', evidence: [] },
        ],
        verification_items: [],
        source: {
          repair_rollback_execution_endpoint: '/api/team-builder-materialization/repair-rollback-execution/latest',
          repair_rollback_post_verification_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_rollback_post_verification_result.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-closure-rollup\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'clean',
        summary: '当前修复闭环没有待处理补丁、待验证应用或待验证回滚；可以进入多文件、多候选和真实 worker 泛化。',
        counts: {
          stages: 6,
          pending_stages: 0,
          failed_stages: 0,
          repair_required: 0,
          validation_gap: 0,
          candidates: 0,
          review_items: 0,
          diff_ready: 0,
          approved: 0,
          execution_ready: 0,
          preview_ready: 0,
          applied: 0,
          apply_real_writes: 0,
          post_apply_pending: 0,
          post_apply_failed: 0,
          reconciled: 0,
          rollback_ready: 0,
          rolled_back: 0,
          rollback_real_writes: 0,
          rollback_post_pending: 0,
          rollback_post_failed: 0,
          multi_candidate_count: 0,
          multi_file_candidate_count: 0,
        },
        quality_gates: [
          { id: 'repair_subreports_available', name: '修复子报告可读取', status: 'pass', summary: '总览已读取候选、应用、验证、对账、回滚和回滚后验证报告。', evidence: [] },
          { id: 'real_writes_are_explicit', name: '真实写入只来自显式执行', status: 'pass', summary: '总览只读；真实应用和真实回滚仍分别由 explicit apply/rollback POST 记录证明。', evidence: ['apply_real_writes=0', 'rollback_real_writes=0'] },
          { id: 'post_apply_closed', name: '应用后验证闭合', status: 'pass', summary: '没有已应用补丁，或已应用补丁已完成应用后验证和 finding 对账。', evidence: [] },
          { id: 'rollback_closed', name: '回滚后验证闭合', status: 'pass', summary: '没有已回滚补丁，或已回滚补丁已完成回滚后验证。', evidence: [] },
        ],
        stages: [
          { id: 'diagnosis_to_plan', name: '诊断到修复计划', status: 'clean', summary: '当前没有需要修复的 finding。', endpoint: '/api/team-builder-materialization/repair-plan/latest', counts: { repair_required: 0, validation_gap: 0 } },
          { id: 'candidate_review', name: '候选补丁审阅', status: 'no_candidate', summary: '当前真实 run 没有候选补丁。', endpoint: '/api/team-builder-materialization/repair-patch-candidates/latest', counts: { candidates: 0 } },
          { id: 'diff_and_approval', name: 'diff 与显式批准', status: 'no_patch', summary: '当前没有待批准 diff。', endpoint: '/api/team-builder-materialization/repair-approval/latest', counts: { diff_ready: 0 } },
          { id: 'execution_preflight', name: '执行前置与预览', status: 'not_started', summary: '当前没有待执行补丁。', endpoint: '/api/team-builder-materialization/repair-execution-readiness/latest', counts: { execution_ready: 0 } },
          { id: 'apply_and_verify', name: '应用后验证与对账', status: 'not_started', summary: '当前没有已应用补丁需要验证。', endpoint: '/api/team-builder-materialization/repair-outcome-reconciliation/latest', counts: { applied: 0 } },
          { id: 'rollback_and_verify', name: '回滚与回滚后验证', status: 'not_needed', summary: '当前没有已回滚补丁需要验证。', endpoint: '/api/team-builder-materialization/repair-rollback-post-verification/latest', counts: { rolled_back: 0 } },
        ],
        next_actions: [
          {
            id: 'scan_real_run_repair_candidates',
            title: '扫描真实 TeamBuilder 失败 run 候选',
            summary: '文件集应用、验证和回滚样本已经具备；下一步从已有真实 run 里分清失败、验证缺口和可修复候选。',
            endpoint: '/api/team-builder-materialization/repair-real-run-candidate-scan/latest',
          },
        ],
        generalization: {
          summary: '当前没有候选补丁；下一阶段应构造多文件、多候选样本来验证排序、审阅、预览、应用、回滚和对账。',
          candidate_count: 0,
          multi_file_candidate_count: 0,
          single_file_execution_limit: false,
          blockers: [],
          next_validation: '构造真实 generated worker 的多文件、多候选候选集，验证审阅排序、scratch 文件集预览、显式文件集应用、回滚和前后对账。',
        },
        source: {
          repair_closure_rollup_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_closure_rollup.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/high-standard-audit\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'in_progress',
        completion_ready: false,
        summary: 'TeamBuilder 高标准闭环仍在推进中：3 个缺口阻止宣称完整完成。',
        deliverables: [
          { id: 'build_team', name: '建立 team', status: 'pass', summary: '生成包结构通过。', evidence: ['files=10', 'worker_files=3'], endpoint: '/api/team-builder-materialization/test-report/latest' },
          { id: 'materialize_team', name: '全内容 material 化', status: 'warning', summary: '仍有读取线索待确认。', evidence: ['confirmed_reads=10'], endpoint: '/api/team-builder-materialization/report/latest', next_action: '继续把未确认读取线索升级、保留为候选或明确作废。' },
          { id: 'test_team', name: '测试 team', status: 'pass', summary: '测试和 LLM 回放通过。', evidence: ['executed_workers=3'], endpoint: '/api/team-builder-materialization/closure/latest' },
          { id: 'diagnose_team', name: '诊断分析 team', status: 'pass', summary: 'doctor finding 0 条。', evidence: ['doctor_total=0'], endpoint: '/api/team-builder-materialization/doctor-findings/latest' },
          { id: 'repair_team_preparation', name: '修复准备与安全网', status: 'pass', summary: '普通 repair plan clean。', evidence: ['repair_required=0'], endpoint: '/api/team-builder-materialization/repair-closure-rollup/latest' },
          { id: 'repair_real_failed_run', name: '真实失败 run 修复闭环', status: 'warning', summary: '真实失败 run 修复闭环等待显式应用。', evidence: ['ready_to_apply=1', 'applied=0'], endpoint: '/api/team-builder-materialization/repair-real-run-closure-rollup/latest', next_action: '等待明确批准后执行一次真实失败 run 显式 apply，再做应用后回放、对账和回滚验收。' },
          { id: 'generalization', name: '泛用性与文件集试验', status: 'warning', summary: '已有受控泛化和文件集试验，但真实失败 run 样本仍少。', evidence: ['candidate_count=3'], endpoint: '/api/team-builder-materialization/repair-generalization-trial/latest', next_action: '继续扩大真实失败 run 样本，并对不同 provider 的生成质量做同口径比较。' },
        ],
        quality_gates: [
          { id: 'genericity', name: '泛用性', status: 'warning', summary: '真实失败 run 样本仍少，尚未覆盖任意 generated team。', evidence: ['candidate_count=3'] },
          { id: 'observability', name: '观测面', status: 'pass', summary: 'dashboard 已提供主要审计卡片。', evidence: [] },
          { id: 'facilities', name: '设施', status: 'pass', summary: '主要报告都有固定 API、material 路径和回归测试。', evidence: [] },
          { id: 'robustness', name: '鲁棒性', status: 'warning', summary: '真实 apply 之前不会伪装成已修复。', evidence: ['real_ready_to_apply=1', 'real_applied=0'] },
        ],
        prompt_to_artifact_checklist: {
          objective: '完整建立 team、测试 team、诊断分析 team，并修复 team；每阶段同时满足泛用性、观测面、设施和鲁棒性。',
          completion_rule: '只有全部 checklist status 为 pass，且 missing 为空，且 quality_gates 全部为 pass，才允许标记 complete。',
          status: 'not_complete',
          items: [
            { id: 'objective_build_team', requirement: 'TeamBuilder 可以建立 team，且生成包结构可被 dashboard 和测试报告读取。', artifact: '/api/team-builder-materialization/test-report/latest', status: 'pass', evidence: ['files=10'], covered_by_tests: ['tests/dashboard/test_catalogue_material_attribution.py'], conclusion: '生成包结构通过。', gap: '' },
            { id: 'objective_test_team', requirement: 'TeamBuilder 可以测试 team，包括 contract、worker smoke、LLM 回放边界和 closure。', artifact: '/api/team-builder-materialization/closure/latest', status: 'pass', evidence: ['executed_workers=3'], covered_by_tests: ['tests/e2e/team_graph.spec.ts'], conclusion: '测试和 LLM 回放通过。', gap: '' },
            { id: 'objective_diagnose_team', requirement: 'TeamBuilder 可以诊断分析 team，把 finding 定位到 worker/material/边/运行事件。', artifact: '/api/team-builder-materialization/doctor-findings/latest', status: 'pass', evidence: ['doctor_total=0'], covered_by_tests: ['tests/dashboard/test_catalogue_material_attribution.py'], conclusion: 'doctor finding 0 条。', gap: '' },
            { id: 'objective_repair_team', requirement: 'TeamBuilder 可以修复 team：finding 驱动、显式应用、应用后验证、结果对账、回滚和回滚后验证。', artifact: '/api/team-builder-materialization/repair-real-run-closure-rollup/latest', status: 'warning', evidence: ['ready_to_apply=1', 'applied=0'], covered_by_tests: ['tests/dashboard/test_catalogue_material_attribution.py'], conclusion: '真实失败 run 修复闭环等待显式应用。', gap: '真实 apply 前必须保持 in_progress；应用前演练通过也不能替代 POST apply、回放验证、对账和回滚验收。' },
            { id: 'quality_genericity', requirement: '每阶段必须具备泛用性，不只在单一样本上成立。', artifact: '/api/team-builder-materialization/provider-coverage/latest', status: 'warning', evidence: ['candidate_count=3'], covered_by_tests: ['tests/team_builder/test_provider_baseline_from_snapshot.py'], conclusion: '真实失败 run 样本仍少，尚未覆盖任意 generated team。', gap: '还需要补齐 provider 同口径覆盖和更多真实失败 run 样本。' },
            { id: 'quality_observability', requirement: '每阶段必须有可观测面，dashboard 能解释结构、健康、证据和下一步。', artifact: 'dashboard TeamBuilder 页面', status: 'pass', evidence: ['dashboard_cards=true'], covered_by_tests: ['tests/e2e/team_graph.spec.ts'], conclusion: 'dashboard 已提供主要审计卡片。', gap: '' },
            { id: 'quality_facilities', requirement: '每阶段必须有设施支撑，包括固定 API、material、测试和显式安全门。', artifact: 'catalogue API + materials + pytest/playwright', status: 'pass', evidence: ['pytest'], covered_by_tests: ['python -m pytest'], conclusion: '主要报告都有固定 API、material 路径和回归测试。', gap: '' },
            { id: 'quality_robustness', requirement: '每阶段必须有鲁棒性，不能把演练、预览或普通 closure 误判为完整修复。', artifact: '/api/team-builder-materialization/high-standard-audit/latest', status: 'warning', evidence: ['real_ready_to_apply=1', 'real_applied=0'], covered_by_tests: ['tests/dashboard/test_catalogue_material_attribution.py'], conclusion: '真实 apply 之前不会伪装成已修复。', gap: '真实 apply 前必须保持 in_progress；应用前演练通过也不能替代 POST apply、回放验证、对账和回滚验收。' },
          ],
          uncovered_or_incomplete: [
            '真实失败 run 修复仍停在显式 apply 审批前；尚未完成应用后回放验证、结果对账和回滚验收。',
          ],
        },
        missing: [
          '真实失败 run 修复仍停在显式 apply 审批前；尚未完成应用后回放验证、结果对账和回滚验收。',
          'material 读写归因仍有 warning；未确认读取线索不能当成事实 material 读边。',
          '当前泛化证据仍集中在 team_observer_material_trial 和受控试验；还没有多 provider、多类型 team 的高标准对比。',
        ],
        next_actions: [
          {
            id: 'review_real_run_apply_decision',
            title: '审阅真实失败 run 显式应用审批包',
            summary: '先由人审阅目标文件、确认 token、应用后验证和回滚要求，再决定是否执行 POST apply。',
            endpoint: '/api/team-builder-materialization/repair-real-run-closure-rollup/latest',
          },
        ],
        source: {
          high_standard_audit_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_builder_high_standard_audit.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/provider-coverage\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        verdict: 'needs_more_evidence',
        comparison_ready: false,
        summary: 'Provider 覆盖仍不足：3 个缺口阻止宣称泛用。',
        counts: {
          runs_scanned: 5,
          tracked_external_providers: 2,
          external_providers_with_real_runs: 1,
          internal_model_records: 1,
          team_types_seen: 1,
        },
        providers: [
          {
            provider: 'claude-code',
            label: 'Claude Code',
            role: '外部 codegen provider',
            status: 'pass',
            summary: '已有 5 个 TeamBuilder 实战 run，成功 worker 15 个。',
            runs: 5,
            successful_workers: 15,
            failed_workers: 0,
            compile_failures: 0,
            critical_reviews: 0,
            latest_run_id: '20260517-010000-failed',
          },
          {
            provider: 'codex',
            label: 'Codex',
            role: '外部 codegen provider',
            status: 'missing',
            summary: '当前没有同口径 TeamBuilder codegen 实战 run。',
            runs: 0,
            successful_workers: 0,
            failed_workers: 0,
            compile_failures: 0,
            critical_reviews: 0,
            latest_run_id: '',
          },
        ],
        internal_models: [
          {
            provider: 'qwen-3.6-plus',
            label: 'Qwen 3.6 Plus',
            role: '内部 LLM 默认模型/role，不是 WorkerCodeOrchestrator external provider',
            status: 'warning',
            summary: '已有受控 LLM 回放桩证明内部 LLM worker 的调用形状；仍缺与 Claude Code/Codex 同一输入下的 codegen provider 对比。',
            runs: 1,
          },
        ],
        missing: [
          '尚未形成 Claude Code 与 Codex 在同一 TeamBuilder 输入、同一权限、同一验证命令下的 codegen 质量对比。',
          'qwen-3.6-plus 当前只作为内部 LLM/role 或受控回放证据出现，还不是 WorkerCodeOrchestrator external provider 的同口径 codegen 实战证据。',
          '真实样本仍集中在少数 team 类型；还缺多类型 generated team 的 provider 覆盖。',
        ],
        next_actions: [
          {
            id: 'run_codex_same_input_trial',
            title: '补一条 Codex 同口径 TeamBuilder 样本',
            summary: '用同一观察型 team 需求、同一 readonly/workspace 策略和同一测试/doctor/repair 验证命令跑 Codex，再与 Claude Code 样本对比。',
            endpoint: '/api/team-builder-materialization/provider-coverage/latest',
          },
        ],
        source: {
          provider_coverage_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_builder_provider_coverage_audit.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/provider-same-input-trial\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        title: 'Codex 同口径 TeamBuilder 试验计划',
        verdict: 'ready_for_explicit_trial',
        ready: true,
        baseline_run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        baseline_provider: 'claude-code',
        target_provider: 'codex',
        permission: 'readonly',
        model_policy: 'cheap',
        timeout_s: 900,
        summary: 'Codex 同口径试验已具备只读执行计划。',
        counts: { workers: 3, materials: 4, baseline_external_runs: 3, missing: 0 },
        workers: [
          {
            worker_id: 'run_artifact_collector',
            cn_name: '运行证据收集器',
            impl_type: 'HARD',
            format_in: 'team_observer.input.observation_request',
            format_out: 'team_observer.material.run_artifact_bundle',
            baseline_provider: 'claude-code',
            baseline_status: 'succeeded',
            baseline_prompt_chars: 5585,
            baseline_rel_path: 'workers/run_artifact_collector.py',
          },
        ],
        missing: [],
        safety_gates: [
          { id: 'readonly', status: 'pass', summary: '默认只读运行 provider。' },
          { id: 'same_input_materials', status: 'pass', summary: '输入来自同一 baseline summary。' },
          { id: 'scratch_output', status: 'pass', summary: '试验结果只写 scratch。' },
        ],
        command: 'python -m omnicompany.packages.services._core.team_builder.scripts.provider_same_input_trial --baseline-run 20260517-010000-failed --provider codex --permission readonly --model-policy cheap --timeout 900',
        next_actions: [
          {
            id: 'execute_codex_same_input_trial',
            title: '执行只读 Codex 同口径试验',
            summary: '人工在终端执行计划命令；结果只写 _scratch/team_builder_provider_trials。',
            endpoint: '/api/team-builder-materialization/provider-coverage/latest',
          },
        ],
        source: {
          baseline_summary: '_scratch/team_builder_real_material_validation/20260517-010000-failed/summary.json',
          trial_root: '_scratch/team_builder_provider_trials',
          same_input_trial_plan_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_builder_provider_same_input_trial_plan.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-generalization-trial\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'guarded_trial_ready',
        summary: '多候选/多文件泛化试验已建立：候选排序、文件集显式安全门、contract 目标拒绝和只读边界均可审阅；下一步应使用真实 generated worker 多文件候选验证完整文件集修复闭环。',
        counts: {
          candidate_count: 3,
          multi_file_candidate_count: 1,
          contract_target_count: 1,
          blocked_for_real_apply: 1,
          scratch_preview_required: 3,
          real_writes: 0,
        },
        quality_gates: [
          {
            id: 'candidate_ordering_visible',
            name: '候选排序可见',
            status: 'pass',
            summary: '受控样本包含多个候选，并显式展示 priority。',
            evidence: ['generalization_candidate:multi_file_worker_contract'],
          },
          {
            id: 'multi_file_real_apply_guarded',
            name: '多文件真实应用受文件集门保护',
            status: 'pass',
            summary: '真实多文件应用不再按单文件模型偷跑；必须先有逐文件预览，再同时确认 confirm_real_file_write 和 confirm_file_set_write。',
            evidence: ['multi_file_candidate_count=1'],
          },
          {
            id: 'contract_target_rejected',
            name: 'contract 不作为修复目标',
            status: 'pass',
            summary: 'contract 文件只能作为验收定义，不进入真实修复写入目标。',
            evidence: ['contract_target_count=1'],
          },
          {
            id: 'trial_is_read_only',
            name: '试验只读',
            status: 'pass',
            summary: 'GET 报告只写 material 产物，不执行真实 apply/rollback。',
            evidence: ['real_writes=0'],
          },
        ],
        trial_cases: [
          {
            id: 'candidate_ordering',
            name: '多候选排序必须可见',
            status: 'pass',
            summary: '受控样本包含 3 个候选，并按 priority 展示。',
            evidence: [],
          },
          {
            id: 'multi_file_preview_guard',
            name: '多文件补丁必须走文件集安全门',
            status: 'pass',
            summary: '多文件候选必须先进入 scratch 文件集预览；真实 apply 还要同时满足 confirm_real_file_write 和 confirm_file_set_write。',
            evidence: [],
          },
        ],
        controlled_candidates: [
          {
            id: 'generalization_candidate:multi_file_worker_contract',
            priority: 1,
            title: '多文件 worker 修复候选',
            summary: '模拟一个真实 generated worker 需要同时修改 worker 源码和格式/辅助文件的情况。',
            changed_files: ['src/generated/workers/report_writer.py', 'src/generated/formats.py'],
            risk: 'multi_file_real_write_requires_file_set_gate',
            expected_handling: '只能在逐文件预览、文件集确认 token、逐文件记录和回滚前置检查都满足后显式放行。',
          },
          {
            id: 'generalization_candidate:single_file_alternative',
            priority: 2,
            title: '单文件备选修复候选',
            summary: '模拟另一个只修改单个 worker 的备选方案。',
            changed_files: ['src/generated/workers/material_mapper.py'],
            risk: 'needs_candidate_ranking_and_isolated_verification',
            expected_handling: '必须和多文件候选分开生成 diff、预览、批准、应用和验证记录。',
          },
          {
            id: 'generalization_candidate:contract_target_rejected',
            priority: 3,
            title: '错误 contract 目标候选',
            summary: '模拟候选误把 tests/teams contract 当成修复写入目标的情况。',
            changed_files: ['tests/teams/team_observer_material_trial/test_contract.py'],
            risk: 'contract_target_must_be_rejected',
            expected_handling: 'contract 只能作为验收定义和诊断证据，不能被自动修复当成写入目标。',
          },
        ],
        next_actions: [
          {
            id: 'validate_real_generated_file_set_trial',
            title: '验证真实 generated worker 文件集修复',
            summary: '文件集 apply/rollback 记录模型已具备显式确认和逐文件记录；下一步要用真实 generated worker 多文件候选跑完应用、验证、对账、回滚和回滚后验证。',
            endpoint: '/api/team-builder-materialization/repair-closure-rollup/latest',
          },
        ],
        source: {
          repair_generalization_trial_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_generalization_trial.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-generated-file-set-trial\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001-file-set-trial',
        team_name: 'team_builder_file_set_trial',
        verdict: 'pass',
        summary: '真实 generated worker 文件集试验通过：修复前失败、两文件补丁应用后通过、文件集回滚后恢复。',
        counts: {
          changed_files: 2,
          files_previewed: 2,
          files_applied: 2,
          files_rolled_back: 2,
          before_failures: 1,
          post_apply_passed: 1,
          rollback_restored: 1,
          scratch_generated_writes: 4,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'generated_package_imports', name: '生成包可导入', status: 'pass', summary: '生成包 build_team/build_bindings 可运行。', evidence: [] },
          { id: 'failure_reproduced', name: '修复前失败可复现', status: 'pass', summary: '修复前 worker smoke 返回 fail，具备真实 repair 输入。', evidence: ['before_status=fail'] },
          { id: 'file_set_preview_ready', name: '文件集预览已展开', status: 'pass', summary: '两份目标文件都生成 before/after 预览和逐文件 diff sha。', evidence: [] },
          { id: 'file_set_apply_verified', name: '文件集应用后验证通过', status: 'pass', summary: '两份目标文件写入 after 内容后，worker smoke 通过。', evidence: ['after_apply_status=pass'] },
          { id: 'file_set_rollback_verified', name: '文件集回滚后恢复', status: 'pass', summary: '两份目标文件回滚到 before 内容，worker smoke 恢复到修复前失败状态。', evidence: ['after_rollback_status=fail'] },
          { id: 'scratch_only_scope', name: '只写 scratch generated 包', status: 'pass', summary: '本试验只写 _scratch 下的 generated package、预览和 material，不修改仓库业务源码。', evidence: ['real_repo_writes=0'] },
        ],
        changed_files: [
          '_scratch/team_builder_real_generated_file_set_trial/e2e/team_builder_file_set_trial/formats.py',
          '_scratch/team_builder_real_generated_file_set_trial/e2e/team_builder_file_set_trial/workers/file_set_report_worker.py',
        ],
        file_records: [
          {
            changed_file: '_scratch/team_builder_real_generated_file_set_trial/e2e/team_builder_file_set_trial/formats.py',
            before_sha256: 'before-format-sha',
            after_sha256: 'after-format-sha',
            after_apply_sha256: 'after-format-sha',
            after_rollback_sha256: 'before-format-sha',
            before_preview_file: '_scratch/team_builder_repair_apply_preview/e2e/before/formats.py',
            after_preview_file: '_scratch/team_builder_repair_apply_preview/e2e/after/formats.py',
          },
          {
            changed_file: '_scratch/team_builder_real_generated_file_set_trial/e2e/team_builder_file_set_trial/workers/file_set_report_worker.py',
            before_sha256: 'before-worker-sha',
            after_sha256: 'after-worker-sha',
            after_apply_sha256: 'after-worker-sha',
            after_rollback_sha256: 'before-worker-sha',
            before_preview_file: '_scratch/team_builder_repair_apply_preview/e2e/before/file_set_report_worker.py',
            after_preview_file: '_scratch/team_builder_repair_apply_preview/e2e/after/file_set_report_worker.py',
          },
        ],
        smoke: {
          before_worker: { status: 'fail', failed_workers: [{}] },
          after_apply_worker: { status: 'pass', executed_workers: [{}] },
          after_rollback_worker: { status: 'fail', failed_workers: [{}] },
        },
        source: {
          trial_package_dir: '_scratch/team_builder_real_generated_file_set_trial/e2e/team_builder_file_set_trial',
          repair_real_generated_file_set_trial_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_real_generated_file_set_trial.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-closure-rollup\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'action_required',
        summary: '真实失败 run 修复闭环有 3 个阶段需要审阅、显式执行或补验证。',
        counts: {
          stages: 7,
          pending_stages: 3,
          failed_stages: 0,
          failure_candidates: 1,
          repair_required: 1,
          ready_to_apply: 1,
          apply_rehearsal_passed: 1,
          apply_rehearsal_blocked: 0,
          apply_rehearsal_required_fields: 1,
          apply_rehearsal_missing_required_fields: 0,
          applied: 0,
          apply_real_writes: 0,
          verified: 0,
          reconciled: 0,
          rollback_ready: 0,
          rolled_back: 0,
          rollback_verified: 0,
          rollback_real_writes: 0,
        },
        quality_gates: [
          { id: 'real_run_repair_subreports_available', name: '真实 run 修复子报告可读取', status: 'pass', summary: '总览已读取真实失败 run 的候选、消解、diff、应用、验证、对账、回滚和回滚后验证报告。', evidence: [] },
          { id: 'real_run_apply_rehearsal_passed', name: '应用前演练通过', status: 'pass', summary: '待应用项已在独立 scratch 副本中完成 apply 和 rollback 演练。', evidence: ['ready=1', 'rehearsal_passed=1', 'rehearsal_blocked=0'] },
          { id: 'real_run_writes_are_explicit', name: '真实写入只来自显式执行', status: 'pass', summary: '总览只读；真实应用和真实回滚分别只能由 explicit apply/rollback POST 记录证明。', evidence: ['apply_real_writes=0', 'rollback_real_writes=0'] },
          { id: 'real_run_post_apply_closed', name: '应用后验证与对账闭合', status: 'pass', summary: '尚未应用，或已应用记录完成应用后验证和结果对账。', evidence: [] },
          { id: 'real_run_rollback_closed', name: '回滚后验证闭合', status: 'pass', summary: '尚未回滚，或已回滚记录完成回滚后验证。', evidence: [] },
        ],
        stages: [
          { id: 'real_run_candidate', name: '真实失败 run 候选', status: 'failure_candidate', summary: '扫描到 1 个真实失败候选。', endpoint: '/api/team-builder-materialization/repair-real-run-candidate-scan/latest', counts: { failure_candidates: 1 } },
          { id: 'real_run_replay_plan', name: '失败 run 消解计划', status: 'repair_required', summary: 'code review finding 已转成 1 条 repair_required action。', endpoint: '/api/team-builder-materialization/repair-real-run-replay-plan/latest', counts: { repair_required: 1 } },
          { id: 'real_run_diff_review', name: 'diff 审阅与应用预览', status: 'reviewed_and_previewed', summary: 'diff、审阅门和文件集应用预览都已就绪。', endpoint: '/api/team-builder-materialization/repair-real-run-diff-review/latest', counts: { diff_ready: 1, ready_for_review: 1, apply_ready: 1, preview_ready: 1 } },
          { id: 'real_run_apply', name: '显式应用', status: 'ready_for_explicit_apply', summary: '1 条真实失败 run 应用项等待 POST execute。', endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/latest', counts: { ready: 1, applied: 0, real_writes: 0 } },
          { id: 'real_run_post_apply', name: '应用后回放验证', status: 'awaiting_apply', summary: '尚未应用，不能执行应用后回放验证。', endpoint: '/api/team-builder-materialization/repair-real-run-post-apply-verification/latest', counts: { verified: 0, pending: 0, failed: 0 } },
          { id: 'real_run_outcome', name: '修复结果对账', status: 'awaiting_apply', summary: '尚未应用，结果对账停在等待应用。', endpoint: '/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest', counts: { reconciled: 0, introduced_findings: 0, persistent_findings: 0 } },
          { id: 'real_run_rollback', name: '回滚与回滚后验证', status: 'awaiting_apply', summary: '尚未应用，回滚闭环未开启。', endpoint: '/api/team-builder-materialization/repair-real-run-rollback-post-verification/latest', counts: { rollback_ready: 0, rolled_back: 0, verified: 0, real_writes: 0 } },
        ],
        approval_packet: {
          available: true,
          status: 'ready_for_decision',
          title: '真实失败 run 显式应用审批包',
          summary: '1 个真实失败 run 应用项已具备显式审批条件；审批后才允许 POST 写目标文件。',
          post_endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/execute',
          approval_requirements: ['apply=true', 'apply_item_id', 'applied_by', 'reason'],
          required_confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'],
          payload_template: {
            apply: true,
            apply_item_id: 'real_run_apply_gate:0',
            applied_by: '<审批人或执行代理>',
            reason: '<批准这次真实失败 run 修复的原因>',
            confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'],
          },
          decision_dossier: {
            title: '审批前决策说明',
            decision_question: '是否批准把 material_usage_mapper 的真实失败 run 修复写入 scratch generated package？',
            why_now: '前置链路已经完成候选扫描、code review finding 消解、diff 预览、审阅门、应用门和 before/after 文件集预览；剩余动作必须由显式 POST 才能继续。',
            write_scope: '仅限 _scratch/team_builder_real_material_validation/.../code_package_files 下的真实 TeamBuilder run 生成包。',
            expected_effect: '应用后应让 input_key_not_read / required_not_read 类 finding 消解。',
            do_not_use_as_completion: '审批包只是 apply 前证据；没有 POST apply、应用后回放、结果对账和回滚后验证前，不能宣称完整闭环完成。',
            post_approval_sequence: ['POST 显式 apply，并记录 before/after sha。', 'POST 应用后回放验证。', '读取修复结果对账。', '按需要进入显式 rollback。'],
            human_review_focus: ['required_input_fields 是否确实代表该 worker 必须读取的 material 字段。', 'after 预览是否只补充读取和血缘记录。'],
          },
          post_preflight: {
            available: true,
            status: 'ready_to_post',
            summary: 'POST apply 前置检查通过；仍需人工明确批准并携带 required confirmations。',
            conditions: [
              { id: 'semantic_rehearsal_passed', name: '必读字段演练已回放', status: 'pass', summary: 'after 预览已经在演练中确认会读取 required 字段。', evidence: ['required_field_checks=1', 'missing_required_fields=0'] },
              { id: 'ready_item_present', name: '存在待应用项', status: 'pass', summary: '当前 ready_for_explicit_apply 项 1 个。', evidence: ['ready_to_apply=1'] },
              { id: 'not_already_applied', name: '尚未应用', status: 'pass', summary: '没有已应用记录，可以进入审批决策。', evidence: ['applied=0'] },
              { id: 'target_scope_safe', name: '目标范围安全', status: 'pass', summary: '所有目标都限制在真实 run 的 scratch generated package 范围内。', evidence: [] },
              { id: 'current_matches_before', name: '当前内容等于 before', status: 'pass', summary: '当前目标文件 sha 仍等于 before sha，可以应用当前 after 预览。', evidence: [] },
              { id: 'after_preview_verified', name: 'after 预览可校验', status: 'pass', summary: 'after 预览文件存在且 sha 与记录一致。', evidence: [] },
              { id: 'rollback_snapshot_verified', name: '回滚快照可校验', status: 'pass', summary: 'before 预览文件存在且 sha 与记录一致，后续可回滚。', evidence: [] },
              { id: 'required_confirmations_declared', name: '确认 token 已声明', status: 'pass', summary: 'apply 所需三个确认 token 已在待应用项中声明。', evidence: [] },
              { id: 'apply_rehearsal_passed', name: '应用前演练通过', status: 'pass', summary: '独立 scratch 副本已完成 apply 与 rollback 演练。', evidence: [] },
            ],
            blockers: [],
          },
          auto_apply_policy: {
            available: true,
            verdict: 'eligible',
            eligible: true,
            summary: '1 个真实失败 run 修复项满足低风险自动 apply 策略；执行后会自动做应用后验证和结果对账。',
            counts: {
              candidate_items: 1,
              eligible_items: 1,
              blocked_items: 0,
              total_changed_files: 1,
              max_apply_items: 1,
              max_changed_files: 3,
              required_field_checks: 1,
              missing_required_fields: 0,
              real_repo_writes: 0,
            },
            blockers: [],
            warnings: [],
            execute_endpoint: '/api/team-builder-materialization/repair-real-run-auto-apply-execution/execute',
            required_confirmation: 'confirm_team_builder_low_risk_auto_apply',
          },
          apply_rehearsal: {
            available: true,
            verdict: 'pass',
            summary: '1 个待应用项已在独立 scratch 副本中完成 apply 与 rollback 演练；真实目标文件未修改。',
            counts: { ready: 1, passed: 1, blocked: 0, scratch_writes: 3, real_repo_writes: 0, required_field_checks: 1, missing_required_fields: 0, files_without_required_contract: 0 },
            material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_apply_rehearsal.json',
            rehearsal_root: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_rehearsal',
          },
          execution_playbook: {
            available: true,
            status: 'awaiting_explicit_approval',
            title: '批准后的执行剧本',
            summary: '这是一张只读顺序表；它说明批准后每一步该调用哪个端点、是否写目标文件、预期看什么结果。',
            safety_note: '只有 apply 和 rollback 两个 POST 会写目标文件；验证、对账和总览 GET/POST 验证步骤不得写目标文件。',
            steps: [
              { id: 'apply_real_run_patch', order: 1, method: 'POST', endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/execute', title: '显式应用真实失败 run 修复', summary: '写入 after 预览内容，并记录 before/after sha、执行人、理由和确认 token。', writes_target_files: true, can_execute_now: true, required_confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'], payload_template: {}, expected_next_verdict: 'applied' },
              { id: 'verify_after_apply', order: 2, method: 'POST', endpoint: '/api/team-builder-materialization/repair-real-run-post-apply-verification/execute', title: '应用后回放验证', summary: '复查 after sha、required 字段读取、语法、导入和 worker smoke。', writes_target_files: false, can_execute_now: false, required_confirmations: ['confirm_real_run_post_apply_replay'], payload_template: {}, expected_next_verdict: 'pass_or_warning' },
              { id: 'review_reconciliation', order: 3, method: 'GET', endpoint: '/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest', title: '读取修复结果对账', summary: '确认原始 finding 是否消解、是否新增失败、是否还有残留 finding。', writes_target_files: false, can_execute_now: false, required_confirmations: [], payload_template: {}, expected_next_verdict: 'pass_or_warning' },
              { id: 'rollback_if_needed', order: 4, method: 'POST', endpoint: '/api/team-builder-materialization/repair-real-run-rollback-execution/execute', title: '按需显式回滚', summary: '只有在需要撤回或验证回滚链路时执行；会把目标文件恢复到 before 内容。', writes_target_files: true, can_execute_now: false, required_confirmations: ['confirm_real_run_file_rollback'], payload_template: {}, expected_next_verdict: 'rolled_back' },
              { id: 'verify_after_rollback', order: 5, method: 'POST', endpoint: '/api/team-builder-materialization/repair-real-run-rollback-post-verification/execute', title: '回滚后验证', summary: '只验证当前目标文件是否回到 before sha，不执行 apply 或 rollback。', writes_target_files: false, can_execute_now: false, required_confirmations: ['confirm_real_run_post_rollback_verification'], payload_template: {}, expected_next_verdict: 'pass' },
            ],
          },
          items: [
            {
              apply_item_id: 'real_run_apply_gate:0',
              worker_id: 'material_usage_mapper',
              status: 'ready_for_explicit_apply',
              summary: '文件集应用预览已通过，等待 POST execute 显式写入。',
              changed_files: ['_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py'],
              file_count: 1,
              file_records: [
                {
                  changed_file: '_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py',
                  before_sha256: 'before-sha',
                  after_sha256: 'after-sha',
                  current_sha256: 'before-sha',
                  before_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_preview/before/workers/material_usage_mapper.py',
                  after_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_preview/after/workers/material_usage_mapper.py',
                },
              ],
              required_confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'],
              post_apply_verification: ['重新执行 generated package code review，确认 input_key_not_read 清零。'],
              rollback_requirement: '应用前必须保存 before sha 和 before 预览，应用后必须能按 before sha 回滚。',
            },
          ],
          safety_checks: [
            '目标文件必须限制在真实失败 run 的 _scratch/team_builder_real_material_validation/.../code_package_files 范围内。',
          ],
          safety_note: '审批包和总览只读；只有显式 POST execute 才可能写目标文件。',
        },
        next_actions: [
          {
            id: 'execute_real_run_apply_with_confirmations',
            title: '等待你批准真实失败 run 显式应用',
            summary: 'diff、审阅门和应用预览已经就绪；真正写目标文件必须由 POST execute 和确认 token 触发。',
            endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/latest',
            post_endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/execute',
            required_confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'],
            approval_requirements: ['apply=true', 'apply_item_id', 'applied_by', 'reason'],
            safety_note: '只能由显式 POST 写入真实失败 run 的 scratch generated package；总览和 dashboard 页面刷新都不会写目标文件。',
          },
        ],
        source: {
          real_run_closure_rollup_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_closure_rollup.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-candidate-scan\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_builder',
        verdict: 'failure_candidate_needs_doctor',
        summary: '扫描 2 个真实 TeamBuilder run，发现 1 个真实失败候选，但还缺 doctor/repair plan 消解。',
        counts: {
          runs_scanned: 2,
          failure_candidates: 1,
          repair_ready_candidates: 0,
          validation_gap_runs: 1,
          clean_runs: 0,
          source_ready_candidates: 1,
          doctor_ready_candidates: 1,
          patch_candidate_sets: 0,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'real_runs_scanned', name: '真实 run 已扫描', status: 'pass', summary: '已扫描 2 个带 summary.json 的 TeamBuilder 实战 run。', evidence: ['20260517-010000-failed'] },
          { id: 'failure_and_validation_gap_separated', name: '失败与验证缺口已分开', status: 'pass', summary: '报告把 code review/worker 失败、repair_required 和 validation_gap 分开计数。', evidence: ['failure_candidates=1', 'validation_gap_runs=1'] },
          { id: 'repair_requires_explicit_candidate', name: '真实修复必须有 repair_required 候选', status: 'pass', summary: '没有 repair_required、源码和诊断输入三者同时满足时，不进入真实 diff/apply。', evidence: ['repair_ready_candidates=0'] },
          { id: 'source_package_visible', name: '候选源码入口可见', status: 'pass', summary: '所有真实失败候选都能看到 generated source 入口。', evidence: ['source_missing=0'] },
          { id: 'scan_is_read_only', name: '扫描只读', status: 'pass', summary: '本接口只读取已有 run/material 并写扫描 material，不执行 apply/rollback。', evidence: ['real_repo_writes=0'] },
        ],
        candidates: [
          {
            run_id: '20260517-010000-failed',
            team_name: 'team_observer_material_trial',
            classification: 'failure_without_repair_plan',
            summary: '真实失败已经出现，但还没有形成可直接执行的 repair_required 修复候选。',
            counts: {
              critical: 1,
              warnings: 0,
              failed_workers: 0,
              doctor_findings: 0,
              repair_required: 0,
              validation_gap: 0,
              patch_candidates: 0,
              source_files: 10,
            },
            source_ready: true,
            doctor_ready: false,
            candidate_ready: false,
            evidence: ['code_review=fail, critical=1, warning=0', 'source_files=10'],
            source_files: ['workers/material_usage_mapper.py', 'formats.py'],
            materials: [
              { label: '代码审查报告', path: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/code_review_report.json', available: true },
              { label: '修复计划', path: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_plan.json', available: false },
            ],
          },
        ],
        run_summaries: [
          {
            run_id: '20260517-020000-validation-gap',
            team_name: 'team_observer_material_trial',
            classification: 'validation_gap_only',
            summary: '这是验证覆盖缺口，不应直接改生成代码。',
            counts: {
              critical: 0,
              warnings: 0,
              failed_workers: 0,
              doctor_findings: 1,
              repair_required: 0,
              validation_gap: 1,
              patch_candidates: 0,
              source_files: 0,
            },
            source_ready: false,
            doctor_ready: true,
            candidate_ready: false,
            evidence: ['doctor_findings=1', 'repair_required=0, validation_gap=1'],
            source_files: [],
            materials: [],
          },
        ],
        next_actions: [
          {
            id: 'replay_failed_run_to_repair_plan',
            title: '把真实失败 run 消解成 doctor/repair plan',
            summary: '已有真实失败证据，但还没有 repair_required 候选；下一步要回放该 run 的测试、代码审查和源码包。',
            endpoint: '/api/team-builder-materialization/repair-real-run-replay-plan/latest',
          },
        ],
        source: {
          scan_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_real_run_candidate_scan.json',
          materialization_root: '_scratch/team_builder_real_material_validation',
          latest_run: 'e2e-materialization-001',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-replay-plan\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'repair_plan_ready',
        summary: '真实失败 run 20260517-010000-failed 已消解出 1 条 repair_required 修复计划；仍然只生成计划，不生成 diff、不应用。',
        counts: {
          code_review_issues: 1,
          repair_required: 1,
          source_located: 1,
          source_missing: 0,
          diffs_generated: 0,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'failed_candidate_selected', name: '已选择真实失败候选', status: 'pass', summary: '候选来自 run 20260517-010000-failed。', evidence: ['20260517-010000-failed'] },
          { id: 'code_review_issue_consumed', name: '代码审查问题已消费', status: 'pass', summary: '读取 code_review issues 1 条。', evidence: ['code_review_report.json'] },
          { id: 'source_target_located', name: '修复目标源码已定位', status: 'pass', summary: 'repair_required action 均已定位到 generated worker 源码。', evidence: ['workers/material_usage_mapper.py'] },
          { id: 'replay_plan_is_read_only', name: '消解报告只读', status: 'pass', summary: '本接口只写 replay plan material，不生成 diff、不执行 apply/rollback。', evidence: ['diffs_generated=0', 'real_repo_writes=0'] },
        ],
        findings: [
          {
            id: 'team_builder.real_run.code_review:20260517-010000-failed:0',
            check_id: 'team_builder.real_run.code_review',
            level: 'error',
            severity: 'critical',
            target_kind: 'worker',
            target_id: 'material_usage_mapper',
            location: 'node:material_usage_mapper',
            category: 'repair_required',
            observation: 'Worker did not read required field files',
            implication: 'worker 没有读取输入 material 的必需字段，真实 generated team 的数据血缘图可能丢失输入文件信息。',
            source_file: '_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py',
            required_not_read: ['files'],
            format_in: ['team_observer.material.run_artifact_bundle'],
            evidence: ['code_review_report.json', 'workers/material_usage_mapper.py'],
          },
        ],
        repair_actions: [
          {
            id: 'repair_action:0',
            finding_id: 'team_builder.real_run.code_review:20260517-010000-failed:0',
            category: 'repair_required',
            automation_level: 'patch_plan_only',
            auto_safe: false,
            worker_id: 'material_usage_mapper',
            changed_files: ['_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py'],
            required_input_fields: ['files'],
            proposed_change: '在 worker 的 run() 中显式读取并使用输入 material 的必需字段；本候选是 diff 生成输入，不在此接口直接改文件。',
            verification: ['重新执行 generated package code review，确认 input_key_not_read 清零。'],
          },
        ],
        next_actions: [
          {
            id: 'generate_reviewable_real_run_diff',
            title: '为真实失败候选生成可审阅 diff',
            summary: 'repair plan 已明确目标 worker、必需字段和验证方式；下一步才能生成 diff 预览。',
            endpoint: '/api/team-builder-materialization/repair-patch-diff-proposal/latest',
          },
        ],
        source: {
          candidate_scan_endpoint: '/api/team-builder-materialization/repair-real-run-candidate-scan/latest',
          code_review_report: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/code_review_report.json',
          candidate_run_dir: '_scratch/team_builder_real_material_validation/20260517-010000-failed',
          replay_plan_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_replay_plan.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-diff-preview\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'diff_preview_ready',
        summary: '真实失败候选已生成 1 个可审阅 diff 预览；仍然没有写回候选源码。',
        counts: {
          repair_actions: 1,
          diff_ready: 1,
          files_previewed: 1,
          blocked: 0,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'repair_plan_consumed', name: '已消费真实失败修复计划', status: 'pass', summary: '读取 repair actions 1 条。', evidence: ['20260517-010000-failed'] },
          { id: 'target_scope_is_scratch_candidate', name: '目标限制在候选 generated 包', status: 'pass', summary: 'diff 预览只指向真实 run 的 scratch generated package，不触碰业务源码。', evidence: [] },
          { id: 'diff_replay_verified', name: 'diff 可回放', status: 'pass', summary: '1 个 diff 已在内存中回放到 after 预览。', evidence: ['workers/material_usage_mapper.py'] },
          { id: 'preview_is_read_only', name: '预览只读', status: 'pass', summary: '本接口只写 before/after 预览和 material，不改候选源码。', evidence: ['real_repo_writes=0'] },
        ],
        diff_records: [
          {
            id: 'real_run_diff_preview:0',
            action_id: 'repair_action:0',
            worker_id: 'material_usage_mapper',
            changed_file: '_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py',
            required_input_fields: ['files'],
            change_summary: ['读取输入 material 的 files 字段', '把 files 内容写入 lineage graph 节点与证据说明'],
            diff: '--- a/workers/material_usage_mapper.py\n+++ b/workers/material_usage_mapper.py\n+        files: list[dict] = bundle.get("files", [])\n+                    confidence_notes.append(f"declared_file: {file_label}")\n',
            diff_sha256: 'diff-sha',
            before_sha256: 'before-sha',
            after_sha256: 'after-sha',
            before_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_diff_preview/before/workers/material_usage_mapper.py',
            after_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_diff_preview/after/workers/material_usage_mapper.py',
          },
        ],
        blocked_items: [],
        next_actions: [
          {
            id: 'review_real_run_diff_preview',
            title: '审阅真实失败候选 diff 预览',
            summary: 'diff 已生成 before/after 预览；下一步应做人工审阅和显式批准。',
            endpoint: '/api/team-builder-materialization/repair-approval/latest',
          },
        ],
        source: {
          replay_plan_endpoint: '/api/team-builder-materialization/repair-real-run-replay-plan/latest',
          candidate_run_dir: '_scratch/team_builder_real_material_validation/20260517-010000-failed',
          diff_preview_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_diff_preview.json',
          preview_root: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_diff_preview',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-diff-review\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'review_ready',
        summary: '真实失败候选已有 1 个 diff 具备审阅条件；仍需要人工或 AI 明确批准后才能进入应用预览。',
        counts: {
          diff_records: 1,
          ready_for_review: 1,
          blocked: 0,
          requires_explicit_approval: 1,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'diff_preview_consumed', name: '已消费 diff 预览', status: 'pass', summary: '读取 diff records 1 条。', evidence: ['20260517-010000-failed'] },
          { id: 'target_scope_checked', name: '目标范围已检查', status: 'pass', summary: '所有待审 diff 都限制在真实 run 的 scratch generated package。', evidence: ['workers/material_usage_mapper.py'] },
          { id: 'sha_evidence_checked', name: 'sha 证据已检查', status: 'pass', summary: '目标源码当前 sha 与 before 预览一致，diff/before/after sha 完整。', evidence: ['source_matches_before=true'] },
          { id: 'explicit_approval_required', name: '仍需显式批准', status: 'pass', summary: '审阅门只生成结论和问题清单，不批准、不 apply、不 rollback。', evidence: ['real_repo_writes=0'] },
        ],
        review_items: [
          {
            id: 'real_run_diff_review:0',
            record_id: 'real_run_diff_preview:0',
            worker_id: 'material_usage_mapper',
            status: 'ready_for_explicit_review',
            summary: 'diff 证据完整、目标范围受限，已具备进入人工或 AI 审阅的条件；仍未批准、未应用。',
            changed_file: '_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py',
            change_summary: ['读取输入 material 的 files 字段', '把 files 内容写入 lineage graph 节点与证据说明'],
            required_input_fields: ['files'],
            target_scope_safe: true,
            source_matches_before: true,
            current_source_sha256: 'before-sha',
            before_sha256: 'before-sha',
            after_sha256: 'after-sha',
            diff_sha256: 'diff-sha',
            before_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_diff_preview/before/workers/material_usage_mapper.py',
            after_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_diff_preview/after/workers/material_usage_mapper.py',
            risk_notes: [
              '当前目标限制在 _scratch 里的真实 TeamBuilder generated package，不触碰业务源码。',
              '该 diff 只补充输入 material 的 files 字段读取和 declared_file 血缘证据，不应改变 worker 的外部接口。',
            ],
            review_questions: ['是否接受把输入 bundle.files 注册为 workspace 文件节点和 declared_file 证据？'],
            evidence_links: ['workers/material_usage_mapper.py'],
            blocked_reasons: [],
          },
        ],
        blocked_items: [],
        next_actions: [
          {
            id: 'build_real_run_explicit_apply_gate',
            title: '建立真实失败 run 显式应用门',
            summary: '审阅通过后仍需显式批准 token、目标 sha 复查、文件集预览和应用后回放验证。',
            endpoint: '/api/team-builder-materialization/repair-real-run-diff-review/latest',
          },
        ],
        source: {
          diff_preview_endpoint: '/api/team-builder-materialization/repair-real-run-diff-preview/latest',
          diff_preview_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_diff_preview.json',
          diff_review_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_diff_review.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-apply-gate\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'ready_for_explicit_apply_preview',
        summary: '真实失败候选已有 1 个 diff 通过应用门前置检查；下一步只能生成显式应用预览，不能自动写文件。',
        counts: {
          review_items: 1,
          apply_preview_ready: 1,
          blocked: 0,
          required_confirmation_tokens: 3,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'diff_review_consumed', name: '已消费 diff 审阅门', status: 'pass', summary: '读取 review items 1 条。', evidence: ['20260517-010000-failed'] },
          { id: 'source_sha_preflight', name: '源码 sha 预检通过', status: 'pass', summary: '所有待应用项当前源码仍等于 before sha。', evidence: ['material_usage_mapper.py:before-sha'] },
          { id: 'confirmation_tokens_required', name: '确认 token 已列出', status: 'pass', summary: '真实失败 run 应用必须显式携带 diff 审阅、文件集写入和应用后回放确认。', evidence: ['confirm_real_run_diff_review'] },
          { id: 'get_apply_gate_is_read_only', name: 'GET 应用门只读', status: 'pass', summary: '本接口只生成应用门 material，不批准、不应用、不回滚。', evidence: ['real_repo_writes=0'] },
        ],
        apply_items: [
          {
            id: 'real_run_apply_gate:0',
            review_item_id: 'real_run_diff_review:0',
            record_id: 'real_run_diff_preview:0',
            worker_id: 'material_usage_mapper',
            status: 'ready_for_explicit_apply_preview',
            summary: '已具备进入显式应用预览的前置条件；仍必须携带确认 token，并在应用后回放验证。',
            changed_file: '_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py',
            required_input_fields: ['files'],
            diff_sha256: 'diff-sha',
            before_sha256: 'before-sha',
            after_sha256: 'after-sha',
            current_source_sha256: 'before-sha',
            before_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_diff_preview/before/workers/material_usage_mapper.py',
            after_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_diff_preview/after/workers/material_usage_mapper.py',
            required_confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'],
            post_apply_verification: ['重新执行 generated package code review，确认 input_key_not_read 清零。'],
            rollback_requirement: '应用前必须保存 before sha 和 before 预览，应用后必须能按 before sha 回滚。',
            blocked_reasons: [],
          },
        ],
        blocked_items: [],
        next_actions: [
          {
            id: 'generate_real_run_apply_preview',
            title: '生成真实失败 run 显式应用预览',
            summary: '下一步应在不写真实文件的前提下，把审阅通过项展开为文件集 before/after 预览、确认 token 和应用后回放计划。',
            endpoint: '/api/team-builder-materialization/repair-real-run-apply-gate/latest',
          },
        ],
        source: {
          diff_review_endpoint: '/api/team-builder-materialization/repair-real-run-diff-review/latest',
          diff_review_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_diff_review.json',
          apply_gate_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_apply_gate.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-apply-preview\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'preview_ready',
        summary: '真实失败 run 已生成 1 个文件集应用预览；真实目标文件未修改。',
        counts: {
          apply_items: 1,
          preview_ready: 1,
          files_previewed: 1,
          blocked: 0,
          required_confirmation_tokens: 3,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'apply_gate_consumed', name: '已消费显式应用门', status: 'pass', summary: '读取 apply items 1 条。', evidence: ['20260517-010000-failed'] },
          { id: 'source_sha_rechecked', name: '源码 sha 已复查', status: 'pass', summary: '所有预览项当前源码仍等于 before 预览。', evidence: ['workers/material_usage_mapper.py'] },
          { id: 'file_set_preview_created', name: '文件集预览已生成', status: 'pass', summary: '生成 1 个 before/after 文件预览。', evidence: ['after/material_usage_mapper.py'] },
          { id: 'real_run_preview_is_read_only', name: '真实 run 预览只读', status: 'pass', summary: '本接口只写 scratch 应用预览和 material，不批准、不应用、不回滚。', evidence: ['real_repo_writes=0'] },
        ],
        preview_items: [
          {
            id: 'real_run_apply_preview:0',
            apply_item_id: 'real_run_apply_gate:0',
            worker_id: 'material_usage_mapper',
            status: 'preview_ready',
            summary: '真实失败 run 文件集应用预览已生成；仍未写目标文件。',
            changed_files: ['_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py'],
            file_set: true,
            file_count: 1,
            before_preview_files: ['_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_preview/before/workers/material_usage_mapper.py'],
            after_preview_files: ['_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_preview/after/workers/material_usage_mapper.py'],
            file_records: [
              {
                changed_file: '_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py',
                before_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_preview/before/workers/material_usage_mapper.py',
                after_preview_file: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_preview/after/workers/material_usage_mapper.py',
                before_sha256: 'before-sha',
                after_sha256: 'after-sha',
                diff_sha256: 'diff-sha',
              },
            ],
            required_confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'],
            post_apply_verification: ['重新执行 generated package code review，确认 input_key_not_read 清零。'],
            rollback_requirement: '应用前必须保存 before sha 和 before 预览，应用后必须能按 before sha 回滚。',
            blocked_reasons: [],
            safety: {
              scope: 'scratch_only_preview',
              writes_real_files: false,
              requires_final_apply_confirmation: true,
              reason: '该接口只写 _scratch 应用预览和 material，不修改真实 generated package。',
            },
          },
        ],
        blocked_items: [],
        next_actions: [
          {
            id: 'execute_real_run_apply_with_confirmations',
            title: '显式应用真实失败 run 修复',
            summary: '只有在审阅者确认 token、目标 sha 仍匹配且准备好应用后回放时，才能执行真实写入。',
            endpoint: '/api/team-builder-materialization/repair-real-run-apply-preview/latest',
          },
        ],
        source: {
          apply_gate_endpoint: '/api/team-builder-materialization/repair-real-run-apply-gate/latest',
          apply_gate_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_apply_gate.json',
          apply_preview_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_apply_preview.json',
          preview_root: '_scratch/team_builder_repair_apply_preview/20260517-010000-failed/real_run_apply_preview',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-apply-execution\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'ready_for_explicit_apply',
        summary: '1 条真实失败 run 应用项等待显式执行。',
        counts: {
          items: 1,
          ready: 1,
          applied: 0,
          blocked: 0,
          stale_or_mismatch: 0,
          real_writes: 0,
        },
        quality_gates: [
          { id: 'apply_preview_consumed', name: '已消费文件集应用预览', status: 'pass', summary: '读取 preview items 1 条。', evidence: ['20260517-010000-failed'] },
          { id: 'explicit_execute_required', name: '必须显式执行', status: 'pass', summary: 'GET 只展示状态；POST execute 需要确认 token、理由、执行人和 before/after sha 匹配。', evidence: ['confirm_real_run_file_set_write'] },
          { id: 'apply_records_match_current', name: '应用记录匹配当前文件', status: 'pass', summary: '没有发现应用记录或预览与当前文件不匹配。', evidence: [] },
        ],
        apply_items: [
          {
            id: 'real_run_apply_execution:0',
            apply_item_id: 'real_run_apply_gate:0',
            worker_id: 'material_usage_mapper',
            status: 'ready_for_explicit_apply',
            summary: '文件集应用预览已通过，等待 POST execute 显式写入。',
            changed_files: ['_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py'],
            file_set: true,
            file_count: 1,
            file_records: [
              {
                changed_file: '_scratch/team_builder_real_material_validation/20260517-010000-failed/code_package_files/workers/material_usage_mapper.py',
                before_sha256: 'before-sha',
                after_sha256: 'after-sha',
                current_sha256: 'before-sha',
              },
            ],
            required_confirmations: ['confirm_real_run_diff_review', 'confirm_real_run_file_set_write', 'confirm_post_apply_replay_required'],
            real_writes: 0,
            blocked_reasons: [],
          },
        ],
        records: [],
        next_actions: [
          {
            id: 'post_real_run_apply_execute',
            title: 'POST 显式应用真实失败 run 修复',
            summary: '仅当审阅者提供所有确认 token 且当前 sha 匹配时才会写目标文件。',
            endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/execute',
          },
        ],
        source: {
          apply_preview_endpoint: '/api/team-builder-materialization/repair-real-run-apply-preview/latest',
          apply_execution_records_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_apply_execution_records.json',
          apply_execution_report_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_apply_execution_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-post-apply-verification\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'awaiting_apply',
        summary: '1 条真实失败 run 修复已准备显式应用；应用后回放验证等待 POST apply 执行。',
        counts: {
          applied: 0,
          verified: 0,
          pending: 0,
          failed: 0,
          warnings: 0,
          ready: 1,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'real_run_apply_required', name: '先完成显式应用', status: 'warning', summary: '应用后验证只消费已应用记录；不会主动写入目标文件。', evidence: ['ready=1', 'applied=0'] },
          { id: 'post_apply_replay_is_explicit', name: '回放验证必须显式触发', status: 'pass', summary: 'GET 只展示状态；POST execute 需要确认 token、执行人和理由。', evidence: ['confirm_real_run_post_apply_replay'] },
        ],
        verification_items: [],
        next_actions: [
          {
            id: 'post_real_run_apply_execute',
            title: '先显式应用真实失败 run 修复',
            summary: '尚无已应用记录时，不能做应用后回放验证。',
            endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/execute',
          },
        ],
        source: {
          apply_execution_endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/latest',
          post_apply_verification_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_post_apply_verification_result.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-outcome-reconciliation\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'awaiting_apply',
        summary: '1 条真实失败 run 修复等待显式应用；结果对账尚不能开始。',
        counts: {
          applied: 0,
          reconciled: 0,
          missing_baseline: 0,
          resolved_findings: 0,
          introduced_findings: 0,
          persistent_findings: 0,
          pending_verification: 0,
          warnings: 0,
          ready: 1,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'real_run_baseline_available', name: '应用前 finding 基线存在', status: 'pass', summary: 'replay plan/code review finding 可用于前后对账。', evidence: ['baseline_findings=1'] },
          { id: 'real_run_post_apply_verified', name: '应用后回放验证可用', status: 'warning', summary: '尚未执行真实 apply，不能做应用后回放验证。', evidence: ['awaiting_apply'] },
          { id: 'real_run_reconciliation_is_read_only', name: '对账只读', status: 'pass', summary: '本接口只写对账 material，不执行 apply/rollback，也不改目标文件。', evidence: ['real_repo_writes=0'] },
        ],
        reconciliation_items: [],
        next_actions: [
          {
            id: 'post_real_run_apply_execute',
            title: '先显式应用真实失败 run 修复',
            summary: '尚无已应用记录时，对账只能停在等待应用。',
            endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/execute',
          },
        ],
        source: {
          apply_execution_endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/latest',
          post_apply_verification_endpoint: '/api/team-builder-materialization/repair-real-run-post-apply-verification/latest',
          replay_plan_endpoint: '/api/team-builder-materialization/repair-real-run-replay-plan/latest',
          outcome_reconciliation_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_outcome_reconciliation.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-rollback-readiness\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'awaiting_apply',
        summary: '1 条真实失败 run 修复等待显式应用；回滚就绪只能等待应用记录。',
        counts: {
          applied: 0,
          rollback_ready: 0,
          blocked: 0,
          stale_or_mismatch: 0,
          missing_before_snapshot: 0,
          ready: 1,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'real_run_explicit_rollback_only', name: '只允许显式回滚', status: 'pass', summary: 'GET 报告接口只检查回滚就绪，不写目标文件；后续真实回滚必须另走显式执行门。', evidence: ['get_writes_files=false'] },
          { id: 'real_run_current_file_matches_after', name: '当前文件等于应用后内容', status: 'warning', summary: '尚未执行真实 apply，不能判断回滚文件状态。', evidence: [] },
          { id: 'real_run_before_snapshot_available', name: '应用前快照可用', status: 'warning', summary: '尚未执行真实 apply，不能判断回滚文件状态。', evidence: [] },
        ],
        rollback_items: [],
        next_actions: [
          {
            id: 'post_real_run_apply_execute',
            title: '先显式应用真实失败 run 修复',
            summary: '没有已应用记录时，回滚就绪只能停在等待应用。',
            endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/execute',
          },
        ],
        source: {
          apply_execution_endpoint: '/api/team-builder-materialization/repair-real-run-apply-execution/latest',
          outcome_reconciliation_endpoint: '/api/team-builder-materialization/repair-real-run-outcome-reconciliation/latest',
          real_run_rollback_readiness_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_rollback_readiness.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-rollback-execution\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'awaiting_apply',
        summary: '真实失败 run 修复尚未应用，回滚执行未开启。',
        counts: {
          items: 0,
          ready: 0,
          rolled_back: 0,
          blocked: 0,
          stale_or_mismatch: 0,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'real_run_rollback_readiness_required', name: '必须先通过回滚就绪检查', status: 'warning', summary: '真实失败 run 修复尚未应用，回滚执行必须等待回滚就绪检查。', evidence: [] },
          { id: 'real_run_explicit_rollback_execute_only', name: '只允许显式执行回滚', status: 'pass', summary: 'GET 报告接口不会写目标文件；只有 POST execute 且确认 token 齐全时才写入目标文件。', evidence: ['get_writes_files=false'] },
        ],
        rollback_items: [],
        records: [],
        source: {
          rollback_readiness_endpoint: '/api/team-builder-materialization/repair-real-run-rollback-readiness/latest',
          rollback_execution_records_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_rollback_execution_records.json',
          rollback_execution_report_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_rollback_execution_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-real-run-rollback-post-verification\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: '20260517-010000-failed',
        team_name: 'team_observer_material_trial',
        verdict: 'awaiting_apply',
        summary: '真实失败 run 修复尚未应用，回滚后验证未开启。',
        counts: {
          rolled_back: 0,
          verified: 0,
          pending: 0,
          failed: 0,
          real_repo_writes: 0,
        },
        quality_gates: [
          { id: 'real_run_rolled_back_records_present', name: '存在真实失败 run 回滚记录', status: 'warning', summary: '真实失败 run 修复尚未应用，回滚后验证未开启。', evidence: [] },
          { id: 'real_run_post_rollback_verification_executed', name: '回滚后验证已执行', status: 'warning', summary: '尚未应用时没有回滚后验证可执行。', evidence: [] },
          { id: 'real_run_post_rollback_is_read_only', name: '回滚后验证只读', status: 'pass', summary: 'GET 和 POST 验证只写验证 material，不执行 apply/rollback，也不改目标文件。', evidence: ['real_repo_writes=0'] },
        ],
        verification_items: [],
        source: {
          rollback_execution_endpoint: '/api/team-builder-materialization/repair-real-run-rollback-execution/latest',
          rollback_post_verification_material: '_scratch/team_builder_real_material_validation/20260517-010000-failed/materials/team_repair_real_run_rollback_post_verification_result.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/repair-safety-policy\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        version: '2026-05-17.v1',
        summary: '当前策略允许自动分类和补丁计划生成，但不允许在没有人工确认的情况下直接修改生成代码。',
        counts: {
          rules: 4,
          auto_safe_rules: 0,
          patch_plan_only_rules: 2,
          manual_or_none_rules: 2,
        },
        rules: [
          {
            id: 'validation_gap_no_code_change',
            name: '验证缺口不改代码',
            category: 'validation_gap',
            automation_level: 'none',
            auto_safe: false,
            next_action: '补受控 LLM 回放或测试样例，不改生成代码。',
            rationale: '这类 finding 说明验证没有覆盖完整链路，不证明生成代码错误。',
          },
          {
            id: 'runtime_failure_patch_plan_only',
            name: '运行失败只生成补丁计划',
            category: 'repair_required',
            automation_level: 'patch_plan_only',
            auto_safe: false,
            next_action: '定位源码、输入 material 和失败诊断，生成补丁计划。',
            rationale: '运行时失败可能需要业务语义判断。',
          },
        ],
        source: {
          repair_safety_policy_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_repair_safety_policy.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/read-clue-resolution\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'warning',
        summary: '读取线索消解计划: 1 条候选仍需确认，1 条已展开为候选 material，共 1 个候选；1 条能对上真实工具事件，1 个候选有明确 Read 证据；0 条可自动展开后复核，1 条需要工具回放，0 条需要人工或文件头复核。',
        counts: {
          read_clues: 1,
          confirmed: 0,
          confirmed_read_edges: 0,
          unresolved: 1,
          candidate_materialized: 1,
          candidate_materials: 1,
          unexpanded: 0,
          tool_scope_confirmed: 1,
          tool_read_confirmed_materials: 1,
          auto_expandable: 0,
          trace_replay_required: 1,
          manual_review: 0,
        },
        quality_gates: [
          { id: 'read_clues_visible', name: '候选读取线索可见', status: 'pass', summary: '共有 1 条实战读取线索。', evidence: ['read_clues=1'] },
          { id: 'candidate_material_expansion', name: '候选 material 已展开', status: 'pass', summary: '1/1 条未确认线索已展开为候选 material，共 1 个候选。', evidence: ['candidate_materials=1', 'unexpanded=0'] },
          { id: 'unresolved_not_promoted', name: '未确认线索不伪装', status: 'warning', summary: '1 条线索仍未确认，继续保持候选状态。', evidence: ['工作区 grep 线索'] },
          { id: 'resolution_next_action', name: '消解动作已给出', status: 'pass', summary: '每条未确认线索都有下一步确认动作。', evidence: [] },
        ],
        actions: [
          {
            id: 'read_clue_resolution:0',
            worker_id: 'material_usage_mapper',
            title: '工作区 grep 线索',
            target: 'command=grep material_id src/omnicompany/packages/services/_core/team_builder',
            category: 'tool_trace_replay',
            automation_level: 'trace_replay_required',
            status: 'candidate_materialized',
            evidence_summary: '来自 grep 命令，需要回放命中文件再确认 material_id。',
            reason: '没有 matched_material_ids 或 declared_material_ids，不能作为确认 material 读边。',
            next_action: '回放工具命令摘要，提取真实命中文件或 material_id；不能只凭命令文本升级。',
            review_target: 'pattern material_id',
            review_summary: '按 pattern 在 TeamBuilder 相关源码范围试展开，扫描 12 个文件，命中 1 个示例；示例仍需和真实工具输出交叉确认。',
            review_examples: [
              {
                path: 'src/omnicompany/packages/services/_core/team_builder/workers/code_gen_soft.py',
                kind: 'py',
                line: 12,
                excerpt: '# [OMNI] material_id="material:core.team_builder.soft_worker.py"',
                material_ids: ['material:core.team_builder.soft_worker.py'],
              },
            ],
            material_id_hits: ['material:core.team_builder.soft_worker.py'],
            candidate_materials: [
              {
                id: 'read_clue_resolution:0:candidate:0',
                worker_id: 'material_usage_mapper',
                material_id: 'material:core.team_builder.soft_worker.py',
                path: 'src/omnicompany/packages/services/_core/team_builder/workers/code_gen_soft.py',
                line: 12,
                kind: 'py',
                confidence: 'low',
                status: 'candidate_material',
                basis: '模式静态展开命中 material_id，但仍需和真实 grep/read 输出交叉确认。',
                needs_confirmation: true,
              },
            ],
            tool_confirmation: {
              status: 'scope_and_read_confirmed',
              summary: '真实工具事件确认 material_usage_mapper 执行过这条搜索动作，并且同一 worker 明确 Read 了 1 个可 material 化文件；仍需 grep 命中输出确认是否还有其他命中文件。',
              matching_events: [
                {
                  index: 0,
                  tool: 'Grep',
                  targets: ['command=grep material_id src/omnicompany/packages/services/_core/team_builder'],
                },
              ],
              confirmed_materials: [
                {
                  material_id: 'material:core.team_builder.soft_worker.py',
                  path: 'src/omnicompany/packages/services/_core/team_builder/workers/code_gen_soft.py',
                  kind: 'py',
                  tool: 'Read',
                  event_index: 1,
                  basis: '同一 worker 的真实工具事件明确 Read 了这个文件，且文件头声明了 material_id。',
                },
              ],
            },
            raw_evidence: ['grep material_id src/omnicompany/packages/services/_core/team_builder'],
          },
        ],
        source: {
          material_report_endpoint: '/api/team-builder-materialization/report/latest',
          read_clue_resolution_plan_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_read_clue_resolution_plan.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/material-gap-validation\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'pass',
        summary: 'material 缺口验证: 3 个样例目标都能定位到当前仓库文件并找到 material_id。',
        counts: {
          groups: 1,
          targets: 3,
          resolved_targets: 3,
          relocated_targets: 1,
          material_id_hits: 3,
          missing_targets: 0,
        },
        groups: [
          {
            id: 'read_group:health_report_writer:unconfirmed',
            worker_id: 'health_report_writer',
            title: 'health_report_writer 的待确认读取线索',
            status: 'partial',
            summary: '复核 3 个样例目标，3 个找到 material_id，0 个当前路径缺失。',
            targets: [
              {
                target: 'file_path=src/omnicompany/packages/services/workflow_factory/routers.py',
                status: 'material_id_found',
                resolution_kind: 'renamed_alias',
                resolution_note: '旧 workflow_factory 路径已改名并迁移到 team_builder；当前仓库通过兼容 shim 保留 import 路径，本验证映射到当前 team_builder 实现文件。',
                resolved_paths: ['src/omnicompany/packages/services/_core/team_builder/routers.py'],
                material_ids: ['material:core.team_builder.router_compatibility.shim.py'],
                examples: [
                  {
                    path: 'src/omnicompany/packages/services/_core/team_builder/routers.py',
                    kind: 'py',
                    material_ids: ['material:core.team_builder.router_compatibility.shim.py'],
                    excerpt: '文件头声明 material_id: material:core.team_builder.router_compatibility.shim.py',
                  },
                ],
                decision: '当前文件能解析且有 material_id；仍需真实工具输出证明该文件确实被本次 worker 命中。',
              },
            ],
          },
        ],
        source: {
          material_report_endpoint: '/api/team-builder-materialization/report/latest',
          material_gap_validation_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_material_gap_validation_report.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/closure\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'warning',
        summary: 'TeamBuilder 闭环状态 warning: 1/5 个阶段通过，3 个缺口待处理。',
        stages: [
          { id: '建立 team', name: '建立 team', status: 'pass', summary: '生成包结构门通过。', evidence: [] },
          { id: 'material 归因', name: 'material 归因', status: 'warning', summary: '仍有候选读取线索。', evidence: [] },
          { id: '测试 team', name: '测试 team', status: 'warning', summary: '真实 LLM 调用尚未回放；contract 覆盖同名 0 个，已执行 0 个。', evidence: ['contract_matching=0', 'contract_executed=0'] },
          { id: '诊断分析', name: '诊断分析', status: 'warning', summary: 'doctor finding 2 条。', evidence: [] },
          { id: '修复准备', name: '修复准备', status: 'warning', summary: '当前是验证缺口。', evidence: [] },
        ],
        missing: [
          '读取线索消解计划已生成；1 条候选仍需展开、回放或人工确认。',
          '真实 LLM 调用尚未回放；当前只有本地模型桩验证。',
          '当前 generated team 还没有同名 tests/teams contract；不能把 smoke test 等同于 acceptance。',
          '修复安全策略已定义；当前 finding 不满足自动改代码条件。',
        ],
        source: {
          closure_status_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_closure_status.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/llm-replay-plan\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'ready_for_controlled_replay',
        summary: 'LLM 回放计划: 1 个调用可进入受控真实回放，0 个契约不完整；当前未调用真实模型。',
        counts: { calls: 1, ready: 1, blocked: 0 },
        execution_preflight: {
          status: 'blocked_by_switch',
          enabled: false,
          can_execute: false,
          has_the_company_api_key: false,
          models: ['qwen-3.6-plus'],
          summary: '真实 LLM 回放开关未打开，dashboard 不会因刷新页面而产生模型调用。',
          next_action: '确认要产生真实模型调用后，设置 OMNI_ALLOW_TEAM_BUILDER_LLM_REPLAY=1，再执行受控回放。',
        },
        quality_gates: [
          { id: 'stub_evidence_present', name: '模型桩证据存在', status: 'pass', summary: '发现 1 个 LLM 桩调用证据。', evidence: [] },
          { id: 'prompt_contract_visible', name: 'prompt 契约可审阅', status: 'pass', summary: '1 个调用具备模型名、输出键、JSON/中文约束。', evidence: [] },
          { id: 'real_replay_not_run', name: '真实模型回放未执行', status: 'warning', summary: '本计划只定义受控回放门槛，尚未调用真实模型。', evidence: [] },
        ],
        actions: [
          {
            id: 'llm_replay:health_report_writer:0',
            worker_id: 'health_report_writer',
            call_index: 0,
            model: 'qwen-3.6-plus',
            max_tokens: 4096,
            status: 'ready',
            missing_contract: [],
            expected_output_keys: ['summary_cn', 'risks', 'next_checks'],
            stub_response_keys: ['summary_cn', 'risks', 'next_checks'],
            system_chars: 42,
            user_chars: 512,
            system_preview: '你是 team 运行健康观察助手',
            user_preview: '请结合观察请求和物料血缘图输出 summary_cn、risks、next_checks。',
            human_summary: 'health_report_writer 已具备受控回放契约: 模型 qwen-3.6-plus, 输出键 summary_cn, risks, next_checks。',
            next_action: '允许在受控开关下执行真实 LLM 回放，并校验输出键、JSON 结构、中文摘要和 doctor finding 变化。',
          },
        ],
        source: {
          llm_replay_plan_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_llm_replay_plan.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/llm-replay-result\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        verdict: 'blocked_by_switch',
        summary: '真实 LLM 回放开关未打开，dashboard 不会因刷新页面而产生模型调用。',
        counts: {
          planned_calls: 1,
          executed_workers: 0,
          executed_llm_workers: [],
          failed_workers: 0,
          contract_failures: 0,
        },
        quality_gates: [
          { id: 'execution_preflight', name: '执行前置条件', status: 'warning', summary: '真实 LLM 回放开关未打开。', evidence: [] },
        ],
        executed_workers: [],
        failed_workers: [],
        contract_failures: [],
        source: {
          llm_replay_result_material: '_scratch/team_builder_real_material_validation/e2e/materials/team_llm_replay_result.json',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/report\/latest(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        team_name: 'team_observer_material_trial',
        provider: 'claude-code',
        started_at_local: '2026-05-17 15:30:00',
        summary: '本次 TeamBuilder run 覆盖 1 个 worker，生成 1 个产物，记录 1 条实战读取线索。结论为 warning。',
        verdict: 'warning',
        quality_gates: [
          { id: 'declared_io', name: '声明输入输出可见', status: 'pass', summary: '所有 worker 都有声明读写。', evidence: ['declared_io=1'] },
          { id: 'materialized_writes', name: '生成产物已 material 化', status: 'pass', summary: '所有 worker 的生成文件都进入生成产物候选。', evidence: ['generated_artifacts=1'] },
          { id: 'runtime_read_clues', name: '实战读取线索可解释', status: 'pass', summary: '读取线索都有标题和判断依据。', evidence: ['read_clues=1'] },
          { id: 'confirmed_reads', name: '正式读取边不伪装', status: 'warning', summary: '本次没有确认读取边；候选线索保持为线索，没有伪装成事实。', evidence: ['confirmed_reads=0'] },
        ],
        counts: {
          workers: 1,
          declared_io: 1,
          generated_artifacts: 1,
          read_clues: 1,
          confirmed_reads: 0,
          read_groups: 1,
          field_contract_failures: 0,
          review_issues: 0,
        },
        worker_reports: [
          {
            worker_id: 'material_usage_mapper',
            worker_name: 'material_usage_mapper',
            status: 'warning',
            summary: '该 worker 成功完成，生成 1 个产物，留下 1 条读取线索。',
            declared_io: [],
            generated_artifacts: [],
            read_clues: [],
            confirmed_reads: [],
            field_contract: {
              status: 'pass',
              summary: 'required 字段读写通过：读取 2 个输入字段，写出 2 个输出字段。',
              input_field_reads: { 'team_observer.material.run_artifact_bundle': ['team_id', 'files'] },
              missing_input_required: {},
              missing_output_required: [],
              output_field_writes: ['nodes', 'edges'],
            },
            risks: [],
            next_actions: ['继续确认读取线索是否能升级为正式 material 边。'],
          },
        ],
        read_groups: [
          {
            id: 'read_group:material_usage_mapper:tool_clues',
            worker_id: 'material_usage_mapper',
            group_kind: 'tool_clues',
            status: 'evidence',
            title: 'material_usage_mapper 的工具读取线索组',
            summary: 'material_usage_mapper 有 1 条工具读取、搜索结果或工作区接触线索。',
            decision: '这是工具接触证据汇总；是否已经成为事实读边，要看确认读取组和待确认线索。',
            next_action: '用它快速抽查工具到底接触了哪些资料；不要仅凭本组就判定正式读取关系。',
            count: 1,
            material_count: 0,
            sample_targets: ['src/omnicompany/packages/services/_core/team_builder/team.py'],
            sample_material_ids: [],
            evidence: ['来自 Read 工具目标。'],
            source_filter: { worker: 'material_usage_mapper', material: '', target: 'src/omnicompany/packages/services/_core/team_builder/team.py' },
          },
        ],
        open_questions: [
          {
            worker_id: 'material_usage_mapper',
            summary: 'material_usage_mapper 有 1 条实战读取线索，但还没有升级为正式 material 读取边。',
            next_action: '继续解析文件 header。',
          },
        ],
        source: {
          summary_path: '_scratch/team_builder_real_material_validation/e2e/summary.json',
          materialization_endpoint: '/api/team-builder-materialization/latest',
        },
      }),
    })
  })
  await page.route(/\/api\/team-builder-materialization\/latest(?:\?.*)?$/, async (route) => {
    const resourceLinks = Array.from({ length: 9 }, (_, index) => ({
      material_id: '',
      direction: 'read',
      confidence: 'medium',
      basis: 'Read/Grep tool target',
      registration_status: 'candidate',
      resource_kind: 'workspace',
      target: `src/omnicompany/packages/services/_core/team_builder/workers/team_fixture_${index}.py`,
      rel_path: `src/omnicompany/packages/services/_core/team_builder/workers/team_fixture_${index}.py`,
      content_kind: 'source',
      bytes: null,
      evidence: [`Grep result team_fixture_${index}.py`],
      human_title: `工作区文件：team_fixture_${index}.py`,
      human_summary: `工具结果命中了 team_fixture_${index}.py，仍需确认是否可升级为 material。`,
      evidence_summary: '来自工具结果路径，先作为待确认读取线索。',
      matched_material_ids: [],
    }))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        run_id: 'e2e-materialization-001',
        summary_path: '_scratch/team_builder_real_material_validation/e2e/summary.json',
        provider: 'claude-code',
        started_at_local: '2026-05-17 15:30:00',
        team_name: 'team_observer_material_trial',
        review: {
          kind: 'pass',
          verdict: 'pass',
          critical_count: 0,
          warning_count: 0,
          diagnosis: 'e2e fixture: TeamBuilder materialization provenance is available.',
          issues: [],
        },
        counts: {
          worker_success_count: 1,
          worker_fail_count: 0,
          compile_fail_count: 0,
          declared_material_links: 1,
          generated_candidates: 1,
          resource_candidates: resourceLinks.length,
          workers_with_missing_required: 0,
        },
        worker_runs: [
          {
            worker_id: 'material_usage_mapper',
            status: 'succeeded',
            parse_status: 'parsed',
            provider: 'claude-code',
            run_id: 'e2e-materialization-001',
            rel_path: 'workers/material_usage_mapper.py',
            changed_files: ['workers/material_usage_mapper.py'],
            observed_read_targets: ['src/omnicompany/packages/services/_core/team_builder/team.py'],
            material_io_links: [
              {
                material_id: 'team_observer.material.run_artifact_bundle',
                direction: 'read',
                confidence: 'high',
                basis: 'FORMAT_IN',
                registration_status: 'declared',
                resource_kind: '',
                target: '',
                rel_path: '',
                content_kind: 'schema',
                bytes: null,
                evidence: ['FORMAT_IN'],
              },
            ],
            produced_content_materials: [
              {
                material_id: 'team_builder.generated_file.team_observer_material_trial.workers.material_usage_mapper.py',
                direction: 'write',
                confidence: 'high',
                basis: 'external agent generated worker file',
                registration_status: 'generated-candidate',
                resource_kind: 'workspace',
                target: 'src/omnicompany/packages/services/team_observer_material_trial/workers/material_usage_mapper.py',
                rel_path: 'workers/material_usage_mapper.py',
                content_kind: 'python',
                bytes: 1234,
                evidence: ['workers/material_usage_mapper.py'],
              },
            ],
            resource_material_links: resourceLinks,
            inferred_material_read_links: [],
            static_field_access: {
              input_field_reads: { 'team_observer.material.run_artifact_bundle': ['team_id', 'files'] },
              missing_input_required: {},
              missing_output_required: [],
              output_field_writes: ['nodes', 'edges'],
            },
          },
        ],
      }),
    })
  })
}

test.beforeEach(async ({ page }) => {
  setupErrorLogging(page)
})

test('TeamBuilder graph defaults to the agent-first TeamSpec and exposes materials', async ({ page }) => {
  await mockTeamBuilderMaterialization(page)
  await page.goto('/')
  await openTeam(page, 'services/_core/team_builder/team')

  await expect(page.getByText('team-builder').first()).toBeVisible({ timeout: 15000 })
  await expect(page.locator('select')).toHaveValue('build_team_agent_first')
  await expectBusinessNodeCount(page, 45)
  await expect(page.locator('[data-team-materialization-panel]')).toContainText('实战归因层', { timeout: 15000 })
  await expect(page.locator('[data-team-materialization-panel]')).toContainText('e2e-materialization-001')
  await expect(page.locator('[data-material-attribution-report]')).toContainText('material 归因报告', { timeout: 15000 })
  await expect(page.locator('[data-material-attribution-report]')).toContainText('正式读取边不伪装')
  await expect(page.locator('[data-material-attribution-read-groups]')).toContainText('读取归因分组')
  await expect(page.locator('[data-material-attribution-read-groups]')).toContainText('工具读取线索组')
  await expect(page.locator('[data-material-attribution-read-groups]')).toContainText('src/omnicompany/packages/services/_core/team_builder/team.py')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('读取线索消解', { timeout: 15000 })
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('候选仍需')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('候选 material')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('工具事件确认')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('工具命中/Read')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('需要回放')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('真实目标')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('code_gen_soft.py')
  await expect(page.locator('[data-team-builder-read-clue-resolution]')).toContainText('material:core.team_builder.soft_worker.py')
  await expect(page.locator('[data-team-builder-material-gap-validation]')).toContainText('material 缺口验证', { timeout: 15000 })
  await expect(page.locator('[data-team-builder-material-gap-validation]')).toContainText('renamed_alias')
  await expect(page.locator('[data-team-builder-material-gap-validation]')).toContainText('src/omnicompany/packages/services/_core/team_builder/routers.py')
  await expect(page.locator('[data-team-builder-material-gap-validation]')).toContainText('material:core.team_builder.router_compatibility.shim.py')
  await expect(page.locator('[data-team-builder-test-report]')).toContainText('生成包测试报告', { timeout: 15000 })
  await expect(page.locator('[data-team-builder-closure-status]')).toContainText('TeamBuilder 闭环状态', { timeout: 15000 })
  await expect(page.locator('[data-team-builder-closure-status]')).toContainText('真实 LLM 调用尚未回放')
  await expect(page.locator('[data-team-builder-closure-status]')).toContainText('不能把 smoke test 等同于 acceptance')
  await expect(page.locator('[data-team-builder-test-report]')).toContainText('build_bindings 覆盖 team 节点')
  await expect(page.locator('[data-worker-run-smoke]')).toContainText('worker 业务运行')
  await expect(page.locator('[data-worker-run-smoke]')).toContainText('已执行：run_artifact_collector')
  await expect(page.locator('[data-worker-run-smoke]')).toContainText('模型桩验证：health_report_writer')
  await expect(page.locator('[data-llm-stub-evidence]')).toContainText('qwen-3.6-plus')
  await expect(page.locator('[data-llm-stub-evidence]')).toContainText('summary_cn')
  await expect(page.locator('[data-worker-run-smoke]')).toContainText('跳过：health_report_writer')
  await expect(page.locator('[data-team-builder-test-findings]')).toContainText('doctor 发现')
  await expect(page.locator('[data-team-builder-test-findings]')).toContainText('team_builder.worker_run_smoke.requires_llm')
  await expect(page.locator('[data-team-builder-contract-coverage]')).toContainText('contract 覆盖')
  await expect(page.locator('[data-team-builder-contract-coverage]')).toContainText('不能把 smoke test 等同于 acceptance')
  await expect(page.locator('[data-team-builder-contract-coverage]')).toContainText('repo_absorption')
  await expect(page.locator('[data-team-builder-llm-replay-plan]')).toContainText('LLM 回放计划')
  await expect(page.locator('[data-team-builder-llm-replay-plan]')).toContainText('可受控回放')
  await expect(page.locator('[data-team-builder-llm-replay-plan]')).toContainText('qwen-3.6-plus')
  await expect(page.locator('[data-team-builder-llm-replay-plan]')).toContainText('执行前置条件')
  await expect(page.locator('[data-team-builder-llm-replay-plan]')).toContainText('开关 未开')
  await expect(page.locator('[data-team-builder-llm-replay-result]')).toContainText('真实 LLM 回放结果')
  await expect(page.locator('[data-team-builder-llm-replay-result]')).toContainText('开关阻断')
  await expect(page.locator('[data-team-builder-llm-replay-result]')).toContainText('计划调用 1')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('修复闭环总览')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('当前闭合')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('真实写入只来自显式执行')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('应用后验证与对账')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('泛化面')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('多文件候选 0')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('写入模型 文件集')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('下一步：扫描真实 TeamBuilder 失败 run 候选')
  await expect(page.locator('[data-team-builder-repair-closure-rollup]')).toContainText('修复闭环总览 material')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('修复泛化试验')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('受控试验就绪')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('候选 3')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('多文件 1')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('contract 目标 1')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('多文件真实应用受文件集门保护')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('多文件 worker 修复候选')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('验证真实 generated worker 文件集修复')
  await expect(page.locator('[data-team-builder-repair-generalization-trial]')).toContainText('泛化试验 material')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('真实 generated worker 文件集试验')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('通过')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('目标文件 2')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('应用后通过 1')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('回滚恢复 1')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('只写 scratch generated 包')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('file_set_report_worker.py')
  await expect(page.locator('[data-team-builder-repair-real-generated-file-set-trial]')).toContainText('文件集试验 material')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('真实失败 run 修复闭环总览')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('需要动作')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('待应用 1')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('等待显式应用')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('阶段报告')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('真实写入只来自显式执行')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('真实失败 run 显式应用审批包')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('审批项 1')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('审批前决策说明')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('不能宣称完整闭环完成')
  await expect(page.locator('[data-team-builder-repair-real-run-post-preflight]')).toContainText('POST 前置检查')
  await expect(page.locator('[data-team-builder-repair-real-run-post-preflight]')).toContainText('可提交审批')
  await expect(page.locator('[data-team-builder-repair-real-run-post-preflight]')).toContainText('当前内容等于 before')
  await expect(page.locator('[data-team-builder-repair-real-run-post-preflight]')).toContainText('回滚快照可校验')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-rehearsal]')).toContainText('应用前演练：通过')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-rehearsal]')).toContainText('演练通过 1')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-rehearsal]')).toContainText('真实写入 0')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-rehearsal]')).toContainText('演练报告')
  await expect(page.locator('[data-team-builder-repair-real-run-execution-playbook]')).toContainText('批准后的执行剧本')
  await expect(page.locator('[data-team-builder-repair-real-run-execution-playbook]')).toContainText('显式应用真实失败 run 修复')
  await expect(page.locator('[data-team-builder-repair-real-run-execution-playbook]')).toContainText('POST 写目标文件')
  await expect(page.locator('[data-team-builder-repair-real-run-execution-playbook]')).toContainText('应用后回放验证')
  await expect(page.locator('[data-team-builder-repair-real-run-execution-playbook]')).toContainText('回滚后验证')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('apply_item：real_run_apply_gate:0')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('应用后验证')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('回滚要求')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('确认 token')
  await expect(page.locator('[data-team-builder-repair-real-run-post-preflight]')).toContainText('必读字段演练已回放')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-rehearsal]')).toContainText('必读字段 1')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-rehearsal]')).toContainText('字段缺失 0')
  await expect(page.locator('[data-team-builder-repair-real-run-auto-apply-policy]')).toContainText('低风险自动 apply 策略')
  await expect(page.locator('[data-team-builder-repair-real-run-auto-apply-policy]')).toContainText('可自动 apply')
  await expect(page.locator('[data-team-builder-repair-real-run-auto-apply-policy]')).toContainText('confirm_team_builder_low_risk_auto_apply')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('confirm_real_run_file_set_write')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('显式 POST 入口')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('/api/team-builder-materialization/repair-real-run-apply-execution/execute')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('页面刷新都不会写目标文件')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('下一步：等待你批准真实失败 run 显式应用')
  await expect(page.locator('[data-team-builder-repair-real-run-closure-rollup]')).toContainText('真实 run 闭环总览 material')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('TeamBuilder 高标准目标审计')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('仍在推进')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('真实失败 run 修复闭环')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('显式 apply 审批前')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('泛用性')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('鲁棒性')
  await expect(page.locator('[data-team-builder-prompt-artifact-checklist]')).toContainText('目标到产物检查表')
  await expect(page.locator('[data-team-builder-prompt-artifact-checklist]')).toContainText('未完成')
  await expect(page.locator('[data-team-builder-prompt-artifact-checklist]')).toContainText('TeamBuilder 可以修复 team')
  await expect(page.locator('[data-team-builder-prompt-artifact-checklist]')).toContainText('应用前演练通过也不能替代 POST apply')
  await expect(page.locator('[data-team-builder-prompt-artifact-checklist]')).toContainText('tests/dashboard/test_catalogue_material_attribution.py')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('下一步：审阅真实失败 run 显式应用审批包')
  await expect(page.locator('[data-team-builder-high-standard-audit]')).toContainText('高标准审计 material')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('Provider 覆盖审计')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('证据不足')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('Claude Code')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('Codex')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('缺样本')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('Qwen 3.6 Plus')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('不是 WorkerCodeOrchestrator external provider')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('下一步：补一条 Codex 同口径 TeamBuilder 样本')
  await expect(page.locator('[data-team-builder-provider-coverage-audit]')).toContainText('provider 覆盖 material')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('Codex 同口径 TeamBuilder 试验计划')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('可显式执行')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('基线 claude-code')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('目标 codex')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('运行证据收集器')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('显式执行命令')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('provider_same_input_trial')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('下一步：执行只读 Codex 同口径试验')
  await expect(page.locator('[data-team-builder-provider-same-input-trial]')).toContainText('同口径试验 material')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('真实 run 修复候选扫描')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('失败待消解')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('已扫 run 2')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('真实失败 1')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('验证缺口 1')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('失败与验证缺口已分开')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('20260517-010000-failed')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('workers/material_usage_mapper.py')
  await expect(page.locator('[data-team-builder-repair-real-run-candidate-scan]')).toContainText('候选扫描 material')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('真实失败 run 消解计划')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('修复计划就绪')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('审查问题 1')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('repair_required 1')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('修复目标源码已定位')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('material_usage_mapper')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('缺失字段：files')
  await expect(page.locator('[data-team-builder-repair-real-run-replay-plan]')).toContainText('消解计划 material')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('真实失败 run diff 预览')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('diff 可审阅')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('修复动作 1')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('diff 就绪 1')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('diff 可回放')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('material_usage_mapper')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('读取输入 material 的 files 字段')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-preview]')).toContainText('diff 预览 material')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('真实失败 run diff 审阅门')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('可进入显式审阅')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('可审阅 1')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('需显式批准 1')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('目标范围已检查')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('sha 证据已检查')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('源码等于 before：是')
  await expect(page.locator('[data-team-builder-repair-real-run-diff-review]')).toContainText('diff 审阅 material')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-gate]')).toContainText('真实失败 run 显式应用门')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-gate]')).toContainText('可生成应用预览')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-gate]')).toContainText('应用预览就绪 1')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-gate]')).toContainText('确认 token 3')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-gate]')).toContainText('源码 sha 预检通过')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-gate]')).toContainText('confirm_real_run_file_set_write')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-gate]')).toContainText('应用门 material')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-preview]')).toContainText('真实失败 run 文件集应用预览')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-preview]')).toContainText('预览已生成')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-preview]')).toContainText('文件 1')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-preview]')).toContainText('源码 sha 已复查')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-preview]')).toContainText('文件集预览已生成')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-preview]')).toContainText('confirm_post_apply_replay_required')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-preview]')).toContainText('真实应用预览 material')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-execution]')).toContainText('真实失败 run 显式应用执行')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-execution]')).toContainText('等待显式执行')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-execution]')).toContainText('待执行 1')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-execution]')).toContainText('必须显式执行')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-execution]')).toContainText('等待 POST execute')
  await expect(page.locator('[data-team-builder-repair-real-run-apply-execution]')).toContainText('应用执行 material')
  await expect(page.locator('[data-team-builder-repair-plan]')).toContainText('修复准备计划')
  await expect(page.locator('[data-team-builder-repair-plan]')).toContainText('验证缺口')
  await expect(page.locator('[data-team-builder-repair-plan]')).toContainText('当前不改生成代码')
  await expect(page.locator('[data-team-builder-validation-actions]')).toContainText('查看受控 LLM 回放计划')
  await expect(page.locator('[data-team-builder-repair-real-run-post-apply-verification]')).toContainText('真实失败 run 应用后回放验证')
  await expect(page.locator('[data-team-builder-repair-real-run-post-apply-verification]')).toContainText('等待应用')
  await expect(page.locator('[data-team-builder-repair-real-run-post-apply-verification]')).toContainText('已应用 0')
  await expect(page.locator('[data-team-builder-repair-real-run-post-apply-verification]')).toContainText('回放验证必须显式触发')
  await expect(page.locator('[data-team-builder-repair-real-run-post-apply-verification]')).toContainText('应用后验证 material')
  await expect(page.locator('[data-team-builder-repair-real-run-outcome-reconciliation]')).toContainText('真实失败 run 修复结果对账')
  await expect(page.locator('[data-team-builder-repair-real-run-outcome-reconciliation]')).toContainText('等待应用')
  await expect(page.locator('[data-team-builder-repair-real-run-outcome-reconciliation]')).toContainText('已对账 0')
  await expect(page.locator('[data-team-builder-repair-real-run-outcome-reconciliation]')).toContainText('对账只读')
  await expect(page.locator('[data-team-builder-repair-real-run-outcome-reconciliation]')).toContainText('结果对账 material')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-readiness]')).toContainText('真实失败 run 回滚就绪')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-readiness]')).toContainText('等待应用')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-readiness]')).toContainText('可回滚 0')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-readiness]')).toContainText('只允许显式回滚')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-readiness]')).toContainText('应用前快照可用')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-readiness]')).toContainText('回滚就绪 material')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-execution]')).toContainText('真实失败 run 显式回滚执行')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-execution]')).toContainText('等待应用')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-execution]')).toContainText('待回滚 0')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-execution]')).toContainText('只允许显式执行回滚')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-execution]')).toContainText('回滚执行 material')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-post-verification]')).toContainText('真实失败 run 回滚后验证')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-post-verification]')).toContainText('等待应用')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-post-verification]')).toContainText('已验证 0')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-post-verification]')).toContainText('回滚后验证只读')
  await expect(page.locator('[data-team-builder-repair-real-run-rollback-post-verification]')).toContainText('回滚后验证 material')
  await expect(page.locator('[data-team-builder-validation-actions]')).toContainText('/api/team-builder-materialization/llm-replay-plan/latest')
  await expect(page.locator('[data-team-builder-repair-probe]')).toContainText('故障修复探针')
  await expect(page.locator('[data-team-builder-repair-probe]')).toContainText('worker 失败被 smoke 捕获')
  await expect(page.locator('[data-team-builder-repair-probe]')).toContainText('repair 进入补丁计划')
  await expect(page.locator('[data-team-builder-repair-probe]')).toContainText('自动安全 0')
  await expect(page.locator('[data-team-builder-repair-dry-run]')).toContainText('修复干跑探针')
  await expect(page.locator('[data-team-builder-repair-dry-run]')).toContainText('最小补丁计划已生成')
  await expect(page.locator('[data-team-builder-repair-dry-run]')).toContainText('修复后 doctor finding 清零')
  await expect(page.locator('[data-team-builder-repair-dry-run]')).toContainText('scratch_only')
  await expect(page.locator('[data-team-builder-repair-dry-run]')).toContainText('VerdictKind.PASS')
  await expect(page.locator('[data-team-builder-repair-patch-candidates]')).toContainText('候选补丁计划')
  await expect(page.locator('[data-team-builder-repair-patch-candidates]')).toContainText('可人工审阅')
  await expect(page.locator('[data-team-builder-repair-patch-candidates]')).toContainText('源码已定位')
  await expect(page.locator('[data-team-builder-repair-patch-candidates]')).toContainText('自动改码仍然阻断')
  await expect(page.locator('[data-team-builder-repair-patch-candidates]')).toContainText('class FailureProbeWorker')
  await expect(page.locator('[data-team-builder-repair-patch-candidates]')).toContainText('GET /api/team-builder-materialization/doctor-findings/latest')
  await expect(page.locator('[data-team-builder-repair-apply-gate]')).toContainText('修复应用门')
  await expect(page.locator('[data-team-builder-repair-apply-gate]')).toContainText('待人工审阅')
  await expect(page.locator('[data-team-builder-repair-apply-gate]')).toContainText('真实自动应用被阻断')
  await expect(page.locator('[data-team-builder-repair-apply-gate]')).toContainText('确认人工批准后才允许进入真实补丁生成或应用流程')
  await expect(page.locator('[data-team-builder-repair-apply-gate]')).toContainText('GET /api/team-builder-materialization/closure/latest')
  await expect(page.locator('[data-team-builder-repair-patch-diff-proposal]')).toContainText('补丁 diff proposal')
  await expect(page.locator('[data-team-builder-repair-patch-diff-proposal]')).toContainText('无需 diff')
  await expect(page.locator('[data-team-builder-repair-patch-diff-proposal]')).toContainText('不写真实文件')
  await expect(page.locator('[data-team-builder-repair-patch-diff-proposal]')).toContainText('diff proposal material')
  await expect(page.locator('[data-team-builder-repair-approval]')).toContainText('修复批准记录')
  await expect(page.locator('[data-team-builder-repair-approval]')).toContainText('无需批准')
  await expect(page.locator('[data-team-builder-repair-approval]')).toContainText('批准记录不应用补丁')
  await expect(page.locator('[data-team-builder-repair-approval]')).toContainText('批准报告 material')
  await expect(page.locator('[data-team-builder-repair-execution-readiness]')).toContainText('修复执行就绪')
  await expect(page.locator('[data-team-builder-repair-execution-readiness]')).toContainText('未开启')
  await expect(page.locator('[data-team-builder-repair-execution-readiness]')).toContainText('实际 diff 已生成')
  await expect(page.locator('[data-team-builder-repair-execution-readiness]')).toContainText('执行就绪 material')
  await expect(page.locator('[data-team-builder-repair-apply-preview]')).toContainText('修复应用预览')
  await expect(page.locator('[data-team-builder-repair-apply-preview]')).toContainText('无需预览')
  await expect(page.locator('[data-team-builder-repair-apply-preview]')).toContainText('只写 scratch 副本')
  await expect(page.locator('[data-team-builder-repair-apply-preview]')).toContainText('不写真实文件')
  await expect(page.locator('[data-team-builder-repair-apply-preview]')).toContainText('应用预览 material')
  await expect(page.locator('[data-team-builder-repair-apply-execution]')).toContainText('修复真实应用')
  await expect(page.locator('[data-team-builder-repair-apply-execution]')).toContainText('未开启')
  await expect(page.locator('[data-team-builder-repair-apply-execution]')).toContainText('只允许显式执行')
  await expect(page.locator('[data-team-builder-repair-apply-execution]')).toContainText('确认 token')
  await expect(page.locator('[data-team-builder-repair-apply-execution]')).toContainText('文件集待应用 0')
  await expect(page.locator('[data-team-builder-repair-apply-execution]')).toContainText('文件集已应用 0')
  await expect(page.locator('[data-team-builder-repair-apply-execution]')).toContainText('真实应用 material')
  await expect(page.locator('[data-team-builder-repair-post-apply-verification]')).toContainText('应用后验证')
  await expect(page.locator('[data-team-builder-repair-post-apply-verification]')).toContainText('无需验证')
  await expect(page.locator('[data-team-builder-repair-post-apply-verification]')).toContainText('应用后验证已执行')
  await expect(page.locator('[data-team-builder-repair-post-apply-verification]')).toContainText('POST execute')
  await expect(page.locator('[data-team-builder-repair-post-apply-verification]')).toContainText('应用后验证 material')
  await expect(page.locator('[data-team-builder-repair-outcome-reconciliation]')).toContainText('补丁前后对账')
  await expect(page.locator('[data-team-builder-repair-outcome-reconciliation]')).toContainText('无需对账')
  await expect(page.locator('[data-team-builder-repair-outcome-reconciliation]')).toContainText('应用前快照存在')
  await expect(page.locator('[data-team-builder-repair-outcome-reconciliation]')).toContainText('没有新增 finding')
  await expect(page.locator('[data-team-builder-repair-outcome-reconciliation]')).toContainText('当前没有已应用补丁需要对账')
  await expect(page.locator('[data-team-builder-repair-outcome-reconciliation]')).toContainText('对账 material')
  await expect(page.locator('[data-team-builder-repair-rollback-readiness]')).toContainText('回滚就绪检查')
  await expect(page.locator('[data-team-builder-repair-rollback-readiness]')).toContainText('无需回滚')
  await expect(page.locator('[data-team-builder-repair-rollback-readiness]')).toContainText('只允许显式回滚')
  await expect(page.locator('[data-team-builder-repair-rollback-readiness]')).toContainText('应用前快照可用')
  await expect(page.locator('[data-team-builder-repair-rollback-readiness]')).toContainText('文件集可回滚 0')
  await expect(page.locator('[data-team-builder-repair-rollback-readiness]')).toContainText('当前没有已应用补丁；无需准备回滚')
  await expect(page.locator('[data-team-builder-repair-rollback-readiness]')).toContainText('回滚就绪 material')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('回滚执行记录')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('未开启')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('必须先通过回滚就绪检查')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('只允许显式执行回滚')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('真实回滚必须走显式 POST execute')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('文件集待回滚 0')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('文件集已回滚 0')
  await expect(page.locator('[data-team-builder-repair-rollback-execution]')).toContainText('回滚执行 material')
  await expect(page.locator('[data-team-builder-repair-rollback-post-verification]')).toContainText('回滚后验证')
  await expect(page.locator('[data-team-builder-repair-rollback-post-verification]')).toContainText('无需验证')
  await expect(page.locator('[data-team-builder-repair-rollback-post-verification]')).toContainText('回滚后验证已执行')
  await expect(page.locator('[data-team-builder-repair-rollback-post-verification]')).toContainText('回滚后重跑必须走显式 POST execute')
  await expect(page.locator('[data-team-builder-repair-rollback-post-verification]')).toContainText('回滚后验证 material')
  await expect(page.locator('[data-team-builder-repair-safety-policy]')).toContainText('修复安全策略')
  await expect(page.locator('[data-team-builder-repair-safety-policy]')).toContainText('验证缺口不改代码')
  await expect(page.locator('[data-team-materialization-worker-node]').filter({ hasText: 'material_usage_mapper' }).first()).toBeVisible()
  const groupedReadNode = page.locator('[data-team-materialization-group-node]').filter({ hasText: '读取线索组' }).first()
  await expect(groupedReadNode).toBeVisible()
  await groupedReadNode.click()
  await expect(page.locator('[data-team-floating-detail]')).toContainText('聚合后的待确认读取线索')
  await expect(page.locator('[data-team-floating-detail]')).toContainText('team_fixture_0.py')
  const generatedCandidate = page.locator('[data-team-materialization-node]').filter({ hasText: '生成产物' }).first()
  await expect(generatedCandidate).toBeVisible()
  await generatedCandidate.click()
  await expect(page.locator('[data-team-floating-detail]')).toContainText('证据类型')
  await expect(page.locator('[data-team-floating-detail]')).toContainText('生成产物')
  await expect(page.locator('[data-team-resource-node]').filter({ hasText: '工作区文件' }).first()).toBeVisible()
  await expect(page.locator('[data-team-resource-hints]')).toContainText('不是已确认 material 边')
  await expect(page.locator('.react-flow__node[data-id="worker_code_orchestrator"]')).toBeVisible()
  await expect(page.locator('aside')).toContainText('材料')
  await expect(page.locator('aside')).toContainText('23')

  await page.locator('.react-flow__node[data-id="worker_code_orchestrator"]').click()
  await expect(page.locator('aside')).toContainText('worker_code_orchestrator')
  const workerNode = page.locator('.react-flow__node[data-id="worker_code_orchestrator"]')
  const beforeBox = await workerNode.boundingBox()
  if (!beforeBox) throw new Error('worker_code_orchestrator node has no bounding box')
  await page.mouse.move(beforeBox.x + beforeBox.width / 2, beforeBox.y + beforeBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(beforeBox.x + beforeBox.width / 2 + 90, beforeBox.y + beforeBox.height / 2 + 30, { steps: 8 })
  await page.mouse.up()
  await expect.poll(async () => {
    const afterBox = await workerNode.boundingBox()
    return afterBox ? Math.round(afterBox.x - beforeBox.x) : 0
  }).toBeGreaterThan(20)

  await page.locator('select').selectOption('build_team')
  await expect(page.getByText('workflow-factory').first()).toBeVisible({ timeout: 15000 })
  await expectBusinessNodeCount(page, 21)
})

test('real team samples render as graph, while csv_to_md is labelled smoke only', async ({ page }) => {
  await page.goto('/')

  await openTeam(page, 'services/_learning/repo/absorption/team')
  await expect(page.getByText('repo_absorption').first()).toBeVisible({ timeout: 15000 })
  await expectBusinessNodeCount(page, 11)
  await expect(page.locator('[data-team-resource-node]').filter({ hasText: '工作区文件' }).first()).toBeVisible()
  await expect(page.locator('.react-flow__node').filter({ hasText: 'PatternExtractorWorker' })).toBeVisible()
  await expect(page.locator('[data-team-runs]')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('[data-team-run-row]').first()).toBeVisible()
  await expect(page.locator('[data-run-summary]')).toContainText('已匹配')
  await expect(page.locator('[data-run-summary]')).toContainText('5')
  await expect(page.locator('[data-doctor-summary]')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('aside')).toContainText('健康')
  await page.locator('.react-flow__node').filter({ hasText: 'ReportAssemblerWorker' }).click()
  await expect(page.locator('aside')).toContainText('运行事件')

  await openTeam(page, 'domains/software_engineering/debugger/team')
  await expect(page.getByText('Hypothesis-Driven Debugger').first()).toBeVisible({ timeout: 15000 })
  await expect(page.locator('[data-doctor-summary]')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('[data-doctor-findings]')).toContainText('cycle')
  await expect(page.locator('[data-doctor-findings]')).toContainText('blocking')

  await openTeam(page, 'services/_diagnosis/doctor/team')
  await expect(page.getByText('doctor-material-diagnosis').first()).toBeVisible({ timeout: 15000 })
  await expectBusinessNodeCount(page, 19)
  await expect(page.locator('.react-flow__node').filter({ hasText: 'format_extractor' })).toBeVisible()
  await expect(page.locator('[data-doctor-summary]')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('aside')).toContainText('健康')

  await openTeam(page, 'services/_utility/csv_to_md/team')
  await expect(page.getByText('csv_to_md').first()).toBeVisible({ timeout: 15000 })
  await expectBusinessNodeCount(page, 5)
  await expect(page.locator('.react-flow__node[data-id="CsvReaderWorker"]')).toBeVisible()
  await expect(page.getByText('冒烟测试样本')).toBeVisible()
})

test('team catalogue stays clean and all listed teams have graph plus doctor data', async ({ page }) => {
  await page.goto('/')
  await waitAppReady(page)

  const audit = await page.evaluate(async () => {
    const listResponse = await fetch('/api/teams')
    if (!listResponse.ok) throw new Error(`teams ${listResponse.status}`)
    const list = await listResponse.json() as { items: Array<{ id: string }> }
    const failures: Array<{ id: string; stage: string; status: number; detail: string }> = []
    const noise = list.items
      .map((item) => item.id)
      .filter((id) => id.includes('/_archive/') || id.includes('/workers/') || id.startsWith('pipeline:'))

    for (const item of list.items) {
      const graphResponse = await fetch(`/api/team-graph/${item.id}`)
      if (!graphResponse.ok) {
        failures.push({ id: item.id, stage: 'graph', status: graphResponse.status, detail: await graphResponse.text() })
        continue
      }
      const graph = await graphResponse.json() as { selected_builder: string }
      const doctorResponse = await fetch(`/api/team-doctor/${item.id}?builder=${encodeURIComponent(graph.selected_builder)}`)
      if (!doctorResponse.ok) {
        failures.push({ id: item.id, stage: 'doctor', status: doctorResponse.status, detail: await doctorResponse.text() })
      }
    }

    return { total: list.items.length, failures, noise }
  })

  expect(audit.total).toBeGreaterThanOrEqual(35)
  expect(audit.noise).toEqual([])
  expect(audit.failures).toEqual([])
})

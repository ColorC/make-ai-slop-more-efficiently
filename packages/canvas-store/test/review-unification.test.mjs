// 审阅库单一真源的防回退闸门（2026-08-20 两套审阅体系融合）。
//
// 这两条测试各钉住一个真实事故：
//   ① submission 字段白名单：validSubmission 曾把 state/submittedAt/submittedBy/verdict*/reason
//      之外的字段全部重建丢弃。融合迁移把 1569 条旧材料的 tier/track/history/legacy 写进去，
//      落盘后读回只剩 6 个字段，丰富度静默归零。谁再把它改回白名单，本测试红。
//   ② readBody 分块解码：`raw += chunk` 让每个 TCP chunk 各自 toString('utf8')，
//      1.3MB 的画布 mutate 里「真」被劈成「\uFFFD\uFFFD」。这条用超过单块大小的正文过一遍
//      HTTP 服务，断言多字节字符零损坏。
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { CanvasFileStore } from '../store.ts'
import { canvasStorageId } from '../../material-core/note.ts'

function tmpRoot() { return mkdtempSync(join(tmpdir(), 'review-unify-')) }

test('submission 承载审阅库全部一等字段（防白名单复辟）', () => {
  const root = tmpRoot()
  try {
    const store = new CanvasFileStore(root, { productRef: 'omnidashboard', workspaceCheck: 'off' })
    const docId = canvasStorageId('external:unify-test')
    const submission = {
      state: 'unreviewed',
      submittedAt: '2026-08-02T12:21:09.846459+00:00',
      submittedBy: 'legacy:reviewstage',
      reason: '内容错误/需迭代',
      archived: true,
      updatedAt: '2026-08-13T05:54:26.992717+00:00',
      materialKind: 'agent-workflow-report',
      tier: 'mandatory',
      project: 'ai-learning-observatory',
      track: '工作报告',
      version: 3,
      versionFamily: 'Plan 完成报告: X',
      subjectId: 'EP02',
      subjectType: 'episode',
      revision: 7,
      sourcePlanId: 'plan/x',
      sourceSubagentId: 'work-report',
      annotationsAllowed: false,
      links: { supersedes: ['mat_old'] },
      extra: { live_url: '/demo/' },
      reviewContext: { profile_id: 'generic-web' },
      pushed: { toUser: true, reason: '需要裁决', at: '2026-08-14T00:00:00+00:00' },
      annotations: [{ id: 'a1', kind: 'user', content: '这里不对' }],
      comments: [{ id: 'c1', content: '改一下', author: 'user' }],
      history: [{ event: 'created', at: '2026-08-02T12:21:09.846459+00:00' }],
      legacy: { matId: 'mat_abc', status: 'pending' },
    }
    const res = store.mutate({
      document_id: docId,
      workspace_ref: { kind: 'workspace', ref: 'external:unify-test' },
      actor_ref: { kind: 'agent', ref: 'test' },
      base_revision: 0,
      idempotency_key: 'unify-1',
      patch: {
        upsert_cards: [{
          id: 'mat_abc', kind: 'material', title: '审阅库卡', adapter: 'text',
          source: { kind: 'file', path: 'mat_abc.md', token: 't' },
          x: 0, y: 0, width: 380, height: 300,
          submission,
        }],
      },
    })
    const back = res.document.cards[0].submission
    for (const key of Object.keys(submission)) {
      assert.deepEqual(back[key], submission[key], `submission.${key} 被剥离或改写`)
    }
    // 落盘再读一遍：磁盘往返也不能丢
    const reread = new CanvasFileStore(root, { productRef: 'omnidashboard', workspaceCheck: 'off' })
      .get(docId).cards[0].submission
    assert.equal(reread.archived, true)
    assert.equal(reread.tier, 'mandatory')
    assert.equal(reread.history.length, 1)
    assert.deepEqual(reread.legacy, { matId: 'mat_abc', status: 'pending' })
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('大 payload 的多字节字符零损坏（防 readBody 分块解码回退）', async () => {
  const { spawn } = await import('node:child_process')
  const root = tmpRoot()
  const port = 18212 + (process.pid % 200)
  const proc = spawn(process.execPath, [
    '--experimental-strip-types',
    join(import.meta.dirname, '..', 'server.ts'),
    '--root', root, '--product', 'omnidashboard',
    '--text-dir', 'materials', '--workspace-check', 'off',
    '--port', String(port),
  ], { stdio: 'ignore' })
  try {
    for (let i = 0; i < 60; i += 1) {
      try {
        const probe = await fetch(`http://127.0.0.1:${port}/api/health`)
        if (probe.ok) break
      } catch { /* 还没起来 */ }
      await new Promise((r) => setTimeout(r, 100))
    }
    // 撑到远超单个 TCP 块（~64KB）；关键字里的汉字必须能落在任意块边界上
    const filler = '真实办公室依赖检验字符'.repeat(40_000)
    const title = `编码闸门 · 真实办公室 依赖 3D`
    const docId = canvasStorageId('external:chunk-test')
    const res = await fetch(`http://127.0.0.1:${port}/api/canvases/${encodeURIComponent(docId)}/mutations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        workspace_ref: { kind: 'workspace', ref: 'external:chunk-test' },
        actor_ref: { kind: 'agent', ref: 'test' },
        base_revision: 0,
        idempotency_key: 'chunk-1',
        patch: {
          upsert_cards: [{
            id: 'c-chunk', kind: 'material', title, adapter: 'text',
            source: { kind: 'file', path: 'c.md', token: 't' },
            x: 0, y: 0, width: 380, height: 300,
            submission: {
              state: 'unreviewed', submittedAt: '2026-08-20T00:00:00+00:00',
              submittedBy: 'test', track: '真实轨道', legacy: { filler },
            },
          }],
        },
      }),
    })
    assert.equal(res.status, 200, `mutate 应成功: ${res.status}`)
    const payload = await res.json()
    const card = payload.document.cards[0]
    assert.equal(card.title, title, '标题里的汉字被块边界劈开')
    assert.equal(card.submission.track, '真实轨道')
    assert.equal(card.submission.legacy.filler, filler, '大正文里的汉字被块边界劈开')
    assert.ok(!JSON.stringify(payload).includes('\uFFFD'), '出现了 U+FFFD 替换字符')
  } finally {
    proc.kill()
    rmSync(root, { recursive: true, force: true })
  }
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  parseFlatYaml, readMachineIdentity, resolveWriteIdentity, resolveSession, locateSessionFile, ledgerRecord,
} from '../index.ts'

function scratch() { return mkdtempSync(join(tmpdir(), 'write-identity-')) }

test('flat yaml: comments, quotes and blank lines', () => {
  assert.deepEqual(parseFlatYaml('# 注释\nid: abc-1  # 行尾\nlabel: "host-01"\n\nowner: \'maintainer\'\nbad line\n'), { id: 'abc-1', label: 'host-01', owner: 'maintainer' })
})

test('machine identity: configured file wins, owner bound; missing file falls back to a machine id without owner', () => {
  const root = scratch()
  try {
    const file = join(root, 'machine-identity.yaml')
    writeFileSync(file, 'id: 53df1732-cda5\nlabel: host-01\nowner: maintainer\nos: windows\n', 'utf8')
    const configured = readMachineIdentity({ OMNI_MACHINE_IDENTITY_FILE: file })
    assert.deepEqual(configured, { id: '53df1732-cda5', label: 'host-01', owner: 'maintainer', os: 'windows' })
    const fallback = readMachineIdentity({ OMNI_MACHINE_IDENTITY_FILE: join(root, 'missing.yaml') })
    assert.ok(fallback.id.length > 0)
    assert.equal(fallback.owner, undefined)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('session: vendor-native id from env, file located under the claude config dir, ledger fills pty-only shells', () => {
  const root = scratch()
  try {
    const claudeDir = join(root, '.claude-x')
    mkdirSync(join(claudeDir, 'projects', 'C--workspace'), { recursive: true })
    writeFileSync(join(claudeDir, 'projects', 'C--workspace', '073260ab-53ae.jsonl'), '{}\n', 'utf8')
    const dataDir = join(root, 'data')
    mkdirSync(dataDir, { recursive: true })
    writeFileSync(join(dataDir, 'cc_session_bindings.json'), JSON.stringify({
      '6dd90a98f43a4ded': { trace_id: '6dd90a98f43a4ded', pty_id: '6dd90a98f43a4ded', provider: 'claude_2', model: 'fable', session_id: '073260ab-53ae' },
    }), 'utf8')
    const file = join(root, 'machine-identity.yaml')
    writeFileSync(file, 'id: m-1\nlabel: box\nowner: maintainer\n', 'utf8')

    const envDirect = { CLAUDE_CODE_SESSION_ID: '073260ab-53ae', CLAUDE_CONFIG_DIR: claudeDir, OMNI_AGENT_PROVIDER: 'claude_2', OMNI_CC_PTY_ID: '6dd90a98f43a4ded', OMNI_DATA_DIR: dataDir, OMNI_MACHINE_IDENTITY_FILE: file }
    assert.equal(locateSessionFile('claude_2', '073260ab-53ae', envDirect), join(claudeDir, 'projects', 'C--workspace', '073260ab-53ae.jsonl'))
    const identity = resolveWriteIdentity({ env: envDirect, purpose: '加评论', at: new Date('2026-09-12T12:00:00Z') })
    assert.equal(identity.principal, 'agent')
    assert.equal(identity.runtime.provider, 'claude_2')
    assert.equal(identity.runtime.model, 'fable')
    assert.equal(identity.session.native_id, '073260ab-53ae')
    assert.ok(identity.session.file.endsWith('073260ab-53ae.jsonl'))
    assert.equal(identity.session.pty_id, '6dd90a98f43a4ded')
    assert.equal(identity.machine.owner, 'maintainer')
    assert.equal(identity.purpose, '加评论')
    assert.equal(identity.captured_by, 'cli')

    // 只有 pty（比如 node 脚本从 PTY 里被拉起但没继承原生 id）：从台账补出原生 id 与模型
    const envPty = { OMNI_CC_PTY_ID: '6dd90a98f43a4ded', OMNI_DATA_DIR: dataDir, OMNI_AGENT_PROVIDER: 'claude_2', CLAUDE_CONFIG_DIR: claudeDir, OMNI_MACHINE_IDENTITY_FILE: file }
    assert.equal(ledgerRecord(envPty).session_id, '073260ab-53ae')
    assert.equal(resolveSession(envPty).native_id, '073260ab-53ae')

    // 没有任何运行时线索 = system，不伪造 agent
    const bare = resolveWriteIdentity({ env: { OMNI_MACHINE_IDENTITY_FILE: file, OMNI_DATA_DIR: dataDir, CLAUDE_CONFIG_DIR: claudeDir } })
    assert.equal(bare.principal, 'system')
    assert.equal(bare.runtime, undefined)
    assert.equal(bare.session, undefined)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

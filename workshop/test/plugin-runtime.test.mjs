// Smoke tests for the published Workshop plugin runtime (script runner + QuickJS sandbox).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { runScript } from '../server/src/plugins/runner-node.ts';
import { runTransform } from '../server/src/plugins/sandbox-quickjs.ts';

function tempDir(prefix) {
    return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), prefix)));
}

test('script plugin runs under the Node permission model and writes only to its scratch dir', async () => {
    const pluginDir = tempDir('ws-plugin-');
    const scratch = tempDir('ws-scratch-');
    const entry = path.join(pluginDir, 'main.mjs');
    fs.writeFileSync(entry, [
        'export default async function (input, ctx) {',
        "  ctx.writeText(ctx.dirs.scratch + '/out.txt', String(input.value * 2));",
        "  ctx.log('wrote');",
        '  return { doubled: input.value * 2 };',
        '}',
    ].join('\n'));
    const result = await runScript({
        entry,
        readDirs: [pluginDir],
        writeDirs: [scratch],
        input: { value: 21 },
        budget: { wallMs: 20_000, cpuMs: 10_000, memoryMb: 128 },
        dirs: { scratch },
    });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.output, { doubled: 42 });
    assert.equal(fs.readFileSync(path.join(scratch, 'out.txt'), 'utf8'), '42');
});

test('script plugin cannot read outside the directories its manifest grants', async () => {
    const pluginDir = tempDir('ws-plugin-');
    const secretDir = tempDir('ws-secret-');
    fs.writeFileSync(path.join(secretDir, 'secret.txt'), 'nope');
    const entry = path.join(pluginDir, 'main.mjs');
    fs.writeFileSync(entry, `export default (input, ctx) => ctx.readText(${JSON.stringify(path.join(secretDir, 'secret.txt'))});\n`);
    const result = await runScript({
        entry,
        readDirs: [pluginDir],
        writeDirs: [],
        input: null,
        budget: { wallMs: 20_000, cpuMs: 10_000, memoryMb: 128 },
        dirs: {},
    });
    assert.equal(result.ok, false);
    assert.equal(result.error, 'permission_denied', JSON.stringify(result));
});

test('transform plugin runs inside the QuickJS sandbox with only the injected host API', async () => {
    const calls = [];
    const host = {
        async api(method, apiPath, body) { calls.push([method, apiPath, body]); return { echoed: body }; },
        log() {},
    };
    const code = "export default function (input) { const r = workshop.api('POST', '/api/echo', { n: input.n }); return { n: r.echoed.n + 1, hasProcess: typeof process !== 'undefined' }; }";
    const result = await runTransform(code, { n: 1 }, host, { wallMs: 5_000, memoryMb: 32 });
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.deepEqual(result.output, { n: 2, hasProcess: false });
    assert.deepEqual(calls, [['POST', '/api/echo', { n: 1 }]]);
});

test('transform plugin that never returns is stopped by the wall-clock budget', async () => {
    const host = { async api() { return null; }, log() {} };
    const result = await runTransform('export default function () { for (;;) {} }', null, host, { wallMs: 300, memoryMb: 32 });
    assert.equal(result.ok, false);
    assert.equal(result.budgetExceeded, true, JSON.stringify(result));
});

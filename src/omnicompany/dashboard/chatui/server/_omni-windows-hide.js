// [OMNI] origin=ai-ide ts=2026-06-23 type=infra fork-patch=omni/chat-backend
// Windows 前台窗口硬约束补丁 —— 给所有 child_process spawn 族注入 windowsHide:true,
// 防止 CCUI 起子进程(cursor/gemini/opencode CLI、git、@anthropic-ai/claude-agent-sdk
// 与 @openai/codex-sdk 内部的裸 child_process.spawn)在本机弹出前台控制台窗口。
//
// 这是 omnicompany ccdaemon/_subprocess_hide.py(Python 侧 patch anyio.open_process)的
// Node 镜像。道路: omnicompany docs/plans/dashboard/[2026-06-23]聊天后端迁上游CCUI/plan.md §2 雷一 / D5。
//
// **必须作为 server/index.js 的第一个 import** —— 早于 load-env 与任何 SDK/provider 模块,
// 才能在它们(尤其 SDK)首次评估、绑定/调用 spawn 之前完成 patch。
// CCUI 升级合并时这是需重打的 fork patch 之一(D3)。
import { createRequire } from 'module';

if (process.platform === 'win32') {
  const require = createRequire(import.meta.url);
  const cp = require('child_process');

  // 通用 options 注入: spawn 族签名是 (file, args?, options?), exec 族是 (cmd, options?, cb?),
  // fork 是 (modulePath, args?, options?)。统一策略: 找到第一个"普通对象"参数当 options 合并;
  // 找不到就在回调函数之前(或参数末尾)插一个 {windowsHide:true}。
  const injectHide = (argList) => {
    const optIdx = argList.findIndex(
      (a) => a !== null && typeof a === 'object' && !Array.isArray(a),
    );
    if (optIdx === -1) {
      const opts = { windowsHide: true };
      const cbIdx = argList.findIndex((a) => typeof a === 'function');
      if (cbIdx === -1) argList.push(opts);
      else argList.splice(cbIdx, 0, opts);
    } else if (argList[optIdx].windowsHide === undefined) {
      argList[optIdx] = { ...argList[optIdx], windowsHide: true };
    }
    return argList;
  };

  let patched = 0;
  for (const name of [
    'spawn', 'spawnSync', 'execFile', 'execFileSync', 'exec', 'execSync', 'fork',
  ]) {
    const orig = cp[name];
    if (typeof orig !== 'function') continue;
    cp[name] = function (...args) {
      return orig.apply(this, injectHide(args));
    };
    // 保留原函数引用与名字, 降低对依赖内省的破坏
    try {
      Object.defineProperty(cp[name], 'name', { value: name });
    } catch (_) { /* noop */ }
    patched += 1;
  }

  // 一行 stderr 注记, 便于 Phase 0.5 验证 patch 已生效(不进 stdout)。
  process.stderr.write(`[omni-windows-hide] patched ${patched} child_process fns with windowsHide\n`);
}

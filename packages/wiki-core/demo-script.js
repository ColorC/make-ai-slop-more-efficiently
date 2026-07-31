// 引导演示脚本核：TourScript 的校验 + 评论锚点 + 共享 action 执行器。
// isomorphic：浏览器(覆盖层 demo.js)与录制器(经页面内 window.__demo)都用同一份语义，
// 杜绝"演示步在两处各写一遍"。类型权威见 demo-script.d.ts。

/** 结构校验（仅警告，对齐后端 validate_material_structure 的宽容风格）。 */
export function validateTour(tour) {
  if (!tour || typeof tour !== "object") return ["tour 不是对象"];
  const warnings = [];
  if (!tour.id) warnings.push("tour.id 缺失");
  if (!Array.isArray(tour.steps) || tour.steps.length === 0) warnings.push("tour.steps 为空");
  const seen = new Set();
  for (const [i, s] of (tour.steps || []).entries()) {
    if (!s.id) warnings.push(`steps[${i}].id 缺失`);
    else if (seen.has(s.id)) warnings.push(`steps[${i}].id 重复: ${s.id}`);
    else seen.add(s.id);
    if (!s.narration) warnings.push(`steps[${i}](${s.id || "?"}) narration 缺失`);
  }
  return warnings;
}

/** 评论 target（覆盖层与录制器统一用它，保证 target 形状一致）。 */
export function stepAnchor(tour, step, index) {
  return {
    kind: "demo_step",
    tour_id: tour && tour.id,
    step_id: step.id,
    step_index: typeof index === "number" ? index : (tour && tour.steps ? tour.steps.indexOf(step) : -1),
    title: step.title || step.id,
  };
}

/**
 * 浏览器 action 执行器。到达某步状态时执行声明式动作。
 * ctx = { appRoot=document, hooks }；hooks: 名→async(action) 提供 app 专属动作(如 clickCell)。
 */
export async function executeAction(action, ctx = {}) {
  if (!action) return;
  const root = ctx.appRoot || (typeof document !== "undefined" ? document : null);
  const hooks = ctx.hooks || {};
  switch (action.type) {
    case "waitMs":
      await sleep(action.ms || 0);
      return;
    case "waitFor":
      await waitForSelector(root, action.target, action.timeoutMs || 4000);
      return;
    case "click": {
      // 先等目标出现再点(异步渲染的界面里,元素常晚于进步到来;静默不点=叙述与画面脱节)。
      await waitForSelector(root, action.target, action.timeoutMs || 4000);
      const el = root && root.querySelector ? root.querySelector(action.target) : null;
      if (el && el.click) el.click();
      return;
    }
    case "clickCell":
      if (hooks.clickCell) await hooks.clickCell(action.q, action.r);
      return;
    case "eval":
      if (hooks[action.ref]) await hooks[action.ref](action);
      return;
    default:
      return;
  }
}

function sleep(ms) {
  return new Promise((res) => setTimeout(res, ms));
}

async function waitForSelector(root, sel, timeoutMs) {
  if (!root || !sel || !root.querySelector) return;
  const start = nowMs();
  while (nowMs() - start < timeoutMs) {
    if (root.querySelector(sel)) return;
    await sleep(60);
  }
}

function nowMs() {
  return typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
}

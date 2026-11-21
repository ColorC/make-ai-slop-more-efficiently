// 引导演示覆盖层：在 app 真实 UI 之上叠加逐步引导 + 高亮 + 每步评论。
// mountDemoTour(rootEl, { tour, appRoot, comments, hooks, autoplay, reducedMotion, onStep })
// 覆盖层 root 用 pointer-events:none，卡片/面板单独 auto，底下真实 UI 保持可交互。
import { executeAction, stepAnchor } from "./demo-script.js";

export function mountDemoTour(rootEl, opts = {}) {
  const tour = opts.tour || { id: "", steps: [] };
  const appRoot = opts.appRoot || document;
  const comments = opts.comments || null;
  const hooks = opts.hooks || {};
  const steps = tour.steps || [];
  let index = -1;
  let playing = false;
  let destroyed = false;
  let timer = null;
  let commentPanel = null;

  rootEl.classList.add("demo-tour");
  const spotlight = document.createElement("div");
  spotlight.className = "demo-spotlight";
  spotlight.style.display = "none";
  const card = document.createElement("div");
  card.className = "demo-card";
  card.setAttribute("data-testid", "demo-card");
  rootEl.appendChild(spotlight);
  rootEl.appendChild(card);

  let cardPos = null; // 一旦拖动就固定 {left, top}
  let minimized = false;

  function applyCardLayout() {
    if (cardPos) {
      card.style.left = `${cardPos.left}px`;
      card.style.top = `${cardPos.top}px`;
      card.style.bottom = "auto";
      card.style.transform = "none";
    }
    card.classList.toggle("is-min", minimized);
  }

  function startDrag(e) {
    if (e.target.closest("button")) return; // 头部按钮不触发拖动
    e.preventDefault();
    const rect = card.getBoundingClientRect();
    const offX = e.clientX - rect.left;
    const offY = e.clientY - rect.top;
    const onMove = (ev) => { cardPos = { left: ev.clientX - offX, top: ev.clientY - offY }; applyCardLayout(); };
    const onUp = () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  function renderCard(step) {
    const headHtml = `
      <div class="demo-card-head demo-drag">
        <span class="demo-step-counter" data-testid="demo-step-counter">${index + 1}/${steps.length}</span>
        <strong class="demo-step-title">${esc(step.title || "")}</strong>
        <button type="button" class="demo-mini" data-testid="${minimized ? "demo-restore" : "demo-minimize"}" title="${minimized ? "展开" : "最小化"}">${minimized ? "▢" : "—"}</button>
      </div>`;
    card.innerHTML = minimized ? headHtml : headHtml + `
      <div class="demo-narration" data-testid="demo-narration">${esc(step.narration || "")}</div>
      <div class="demo-card-actions">
        <button type="button" class="demo-btn demo-back" data-testid="demo-back" ${index <= 0 ? "disabled" : ""}>上一步</button>
        <button type="button" class="demo-btn demo-play" data-testid="demo-play">${playing ? "暂停" : "播放"}</button>
        <button type="button" class="demo-btn demo-next" data-testid="demo-next" ${index >= steps.length - 1 ? "disabled" : ""}>下一步</button>
        ${comments ? '<button type="button" class="demo-btn demo-comment" data-testid="demo-comment">评论</button>' : ""}
      </div>`;
    card.querySelector(".demo-card-head")?.addEventListener("mousedown", startDrag);
    card.querySelector(".demo-mini")?.addEventListener("click", () => { minimized = !minimized; renderCard(step); });
    if (!minimized) {
      card.querySelector(".demo-back")?.addEventListener("click", () => { pause(); void back(); });
      card.querySelector(".demo-next")?.addEventListener("click", () => { pause(); void next(); });
      card.querySelector(".demo-play")?.addEventListener("click", () => (playing ? pause() : play()));
      card.querySelector(".demo-comment")?.addEventListener("click", () => openComment(step));
    }
    applyCardLayout();
  }

  // 聚光灯持续跟踪:目标可能晚于进步出现(异步加载),也会因滚动/重排移动——
  // 一次性定位会把高亮留在旧坐标上(实测=叙述与画面脱节的主因)。改为每步一个跟踪循环:
  // 出现前隐藏,首次出现滚动到可视区一次,此后每 300ms 重新对位,换步/销毁时停。
  let hlTimer = null;
  let hlScrolled = false;

  function positionSpotlight(el) {
    const r = el.getBoundingClientRect();
    spotlight.style.display = "";
    spotlight.style.left = `${r.left - 4}px`;
    spotlight.style.top = `${r.top - 4}px`;
    spotlight.style.width = `${r.width + 8}px`;
    spotlight.style.height = `${r.height + 8}px`;
  }

  function highlight(step) {
    if (hlTimer) { clearInterval(hlTimer); hlTimer = null; }
    hlScrolled = false;
    spotlight.style.display = "none";
    if (!step.target || !appRoot.querySelector) return;
    const tick = () => {
      const el = appRoot.querySelector(step.target);
      if (!el || !el.getBoundingClientRect) { spotlight.style.display = "none"; return; }
      if (!hlScrolled) {
        hlScrolled = true;
        if (!opts.reducedMotion && el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      positionSpotlight(el);
    };
    tick();
    hlTimer = setInterval(tick, 300);
  }

  async function goTo(i) {
    if (destroyed || i < 0 || i >= steps.length) return;
    index = i;
    const step = steps[i];
    renderCard(step);
    await executeAction(step.action, { appRoot, hooks });
    highlight(step);
    opts.onStep?.({ index: i, step });
  }

  async function next() {
    if (index < steps.length - 1) await goTo(index + 1);
    else pause();
  }
  async function back() {
    if (index > 0) await goTo(index - 1);
  }

  function pause() {
    playing = false;
    if (timer) { clearTimeout(timer); timer = null; }
    if (index >= 0) renderCard(steps[index]);
  }
  function play() {
    if (destroyed) return;
    playing = true;
    if (index < 0) { void goTo(0).then(scheduleNext); return; }
    renderCard(steps[index]);
    scheduleNext();
  }
  function scheduleNext() {
    if (!playing) return;
    const dwell = (steps[index] && steps[index].autoplayMs) || 1800;
    timer = setTimeout(async () => {
      if (!playing) return;
      if (index >= steps.length - 1) { pause(); return; }
      await next();
      scheduleNext();
    }, dwell);
  }

  function openComment(step) {
    if (!comments) return;
    closeComment();
    const i = index;
    const panel = document.createElement("div");
    panel.className = "demo-comment-panel";
    panel.setAttribute("data-testid", "demo-comment-panel");
    panel.innerHTML = `
      <div class="dcp-head"><strong>第 ${i + 1} 步 · ${esc(step.title || "")}</strong><button type="button" class="dcp-close">✕</button></div>
      <textarea class="dcp-input" data-testid="demo-comment-input" placeholder="对这一步写点什么…"></textarea>
      <div class="dcp-actions"><button type="button" class="dcp-submit" data-testid="demo-comment-submit">提交</button></div>`;
    rootEl.appendChild(panel);
    commentPanel = panel;
    panel.querySelector(".dcp-close").addEventListener("click", closeComment);
    panel.querySelector(".dcp-submit").addEventListener("click", async () => {
      const input = panel.querySelector(".dcp-input");
      const content = (input.value || "").trim();
      if (!content) return;
      try {
        await comments.add({ target: stepAnchor(tour, step, i), content });
        closeComment();
      } catch (e) {
        input.placeholder = `提交失败: ${e.message}`;
      }
    });
    panel.querySelector(".dcp-input").focus();
  }
  function closeComment() {
    commentPanel?.remove();
    commentPanel = null;
  }

  void goTo(0);
  if (opts.autoplay) play();

  return {
    goTo: (i) => void goTo(i),
    next: () => next(),
    back: () => back(),
    play,
    pause,
    getCurrent: () => (index >= 0 ? { index, step: steps[index] } : null),
    stepCount: () => steps.length,
    destroy: () => {
      if (destroyed) return;
      destroyed = true;
      pause();
      if (hlTimer) { clearInterval(hlTimer); hlTimer = null; }
      closeComment();
      rootEl.innerHTML = "";
      rootEl.classList.remove("demo-tour");
    },
  };
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

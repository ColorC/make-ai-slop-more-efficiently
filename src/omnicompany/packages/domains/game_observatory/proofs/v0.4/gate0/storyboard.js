const imageRoot = "../../../artifacts/";

const steps = [
  {
    index: "步骤 01",
    title: "进入英雄厅",
    beforeName: "主世界",
    beforeImage: `${imageRoot}art.afk.live.world.png`,
    afterName: "英雄厅",
    afterImage: `${imageRoot}art.afk.live.hero_hall.png`,
    action: "点击底部导航“英雄厅堂”",
    actionGap: "实际点击点未保留",
    targetClass: "world-target",
    explanation: "玩家从常驻底栏进入英雄成长总览。",
    result: "结果画面显示赛季共鸣等级、核心共鸣位、英雄卡池与快速升级入口。",
    gaps: ["有动作前后状态截图", "没有实际点击坐标", "没有动作与截图的精确时间对齐", "没有连续视频片段"]
  },
  {
    index: "步骤 02",
    title: "选择英雄，触发礼包打断",
    beforeName: "英雄厅",
    beforeImage: `${imageRoot}art.afk.live.hero_hall.png`,
    afterName: "限时礼包",
    afterImage: `${imageRoot}art.afk.live.monetization_interrupt.png`,
    action: "点击一张英雄卡片",
    actionGap: "只保留候选卡片区域",
    targetClass: "hall-target",
    explanation: "英雄卡选择这条导航边被全屏商业化模态条件性打断。",
    result: "礼包覆盖原界面，提供 ¥45 购买与后续免费奖励；本轮没有购买或领取。",
    gaps: ["有打断前后状态截图", "没有实际点击坐标", "不知道礼包内部触发条件", "没有连续视频片段"]
  },
  {
    index: "步骤 03",
    title: "退出礼包，恢复英雄厅",
    beforeName: "限时礼包",
    beforeImage: `${imageRoot}art.afk.live.monetization_interrupt.png`,
    afterName: "英雄厅",
    afterImage: `${imageRoot}art.afk.live.hero_hall.png`,
    action: "退出全屏礼包",
    actionGap: "manifest 未保存退出方式",
    targetClass: "offer-target",
    explanation: "玩家离开商业化模态后回到英雄厅，没有被强制带入购买路径。",
    result: "恢复到英雄列表后，需要再次选择目标英雄才能继续进入详情。",
    gaps: ["有退出前后状态截图", "退出方式与坐标缺失", "恢复耗时缺失", "没有连续视频片段"]
  },
  {
    index: "步骤 04",
    title: "打开英雄详情并读取升级预览",
    beforeName: "英雄厅",
    beforeImage: `${imageRoot}art.afk.live.hero_hall.png`,
    afterName: "罗万 · 英雄详情",
    afterImage: `${imageRoot}art.afk.live.hero_detail.png`,
    action: "再次选择目标英雄",
    actionGap: "实际点击点未保留",
    targetClass: "hall-target",
    explanation: "玩家进入单英雄详情，并在确认前同时看到永久等级、赛季等级、战力和两项升级成本。",
    result: "当前样本库存高于可见成本，但本轮停在确认前，因此没有升级结果证据。",
    gaps: ["有详情到达画面", "动作前后时间未对齐", "没有点击升级", "没有结果帧与反馈视频"]
  }
];

function switchSurface(name, stepIndex) {
  document.querySelectorAll("[data-surface]").forEach((surface) => {
    surface.classList.toggle("is-active", surface.dataset.surface === name);
  });
  document.querySelectorAll(".primary-nav [data-nav]").forEach((button) => {
    if (button.dataset.nav === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  history.replaceState(null, "", `#${name}`);
  if (name === "evidence" && Number.isInteger(stepIndex)) selectStep(stepIndex);
  window.scrollTo({ top: 0, behavior: "instant" });
}

function selectStep(index) {
  const step = steps[index];
  if (!step) return;

  document.querySelectorAll("[data-evidence-step]").forEach((button) => {
    button.classList.toggle("is-active", Number(button.dataset.evidenceStep) === index);
  });

  document.getElementById("step-index").textContent = step.index;
  document.getElementById("step-title").textContent = step.title;
  document.getElementById("before-name").textContent = step.beforeName;
  document.getElementById("before-image").src = step.beforeImage;
  document.getElementById("after-name").textContent = step.afterName;
  document.getElementById("after-image").src = step.afterImage;
  document.getElementById("action-name").textContent = step.action;
  document.getElementById("action-gap").textContent = step.actionGap;
  document.getElementById("step-explanation").textContent = step.explanation;
  document.getElementById("step-result").textContent = step.result;

  const target = document.getElementById("target-region");
  target.className = `target-region ${step.targetClass}`;
  target.innerHTML = `<span>${index === 2 ? "可退出背景；实际方式未知" : "目标区域推定"}</span>`;

  const gaps = document.getElementById("step-gaps");
  gaps.replaceChildren(...step.gaps.map((text, gapIndex) => {
    const li = document.createElement("li");
    li.textContent = text;
    if (gapIndex > 0) li.className = "missing";
    return li;
  }));
}

document.querySelectorAll("[data-nav]").forEach((control) => {
  control.addEventListener("click", (event) => {
    event.preventDefault();
    const step = control.dataset.step === undefined ? undefined : Number(control.dataset.step);
    switchSurface(control.dataset.nav, step);
  });
});

document.querySelectorAll("[data-evidence-step]").forEach((button) => {
  button.addEventListener("click", () => selectStep(Number(button.dataset.evidenceStep)));
});

document.querySelectorAll("[data-overlay]").forEach((button) => {
  button.addEventListener("click", () => {
    const mode = button.dataset.overlay;
    document.querySelectorAll("[data-overlay]").forEach((candidate) => {
      candidate.classList.toggle("is-active", candidate === button);
    });
    document.querySelectorAll("[data-layer]").forEach((layer) => {
      layer.classList.toggle("is-visible", layer.dataset.layer === mode);
    });
  });
});

const initialSurface = ["library", "design-case", "evidence", "console"].includes(location.hash.slice(1))
  ? location.hash.slice(1)
  : "library";
switchSurface(initialSurface);
selectStep(0);
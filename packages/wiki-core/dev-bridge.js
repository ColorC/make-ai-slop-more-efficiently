// 通用开发者模式桥（自 vilo creator-dev 机制泛化）：
// 元素带 data-wiki-ref="<页面名或路径>"；dev 模式开启时点击被截获 → onOpen(ref)。
// capture 阶段拦截，吞掉游戏自身的点击行为，避免误触发游戏命令。

export function wikiRefAttr(page) {
  return `data-wiki-ref="${String(page).replace(/"/g, "&quot;")}"`;
}

export function installWikiDevMode({ isEnabled, onOpen, root = document } = {}) {
  const findRef = (target) =>
    target instanceof Element ? target.closest("[data-wiki-ref]") : null;

  const onPointerDown = (event) => {
    if (!isEnabled()) return;
    if (findRef(event.target)) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  const onClick = (event) => {
    if (!isEnabled()) return;
    const el = findRef(event.target);
    if (!el) return;
    event.preventDefault();
    event.stopPropagation();
    onOpen(el.getAttribute("data-wiki-ref"), el);
  };

  root.addEventListener("pointerdown", onPointerDown, true);
  root.addEventListener("click", onClick, true);
  const syncClass = () => {
    document.documentElement.classList.toggle("wiki-dev-mode", !!isEnabled());
  };
  syncClass();
  return {
    sync: syncClass,
    uninstall: () => {
      root.removeEventListener("pointerdown", onPointerDown, true);
      root.removeEventListener("click", onClick, true);
      document.documentElement.classList.remove("wiki-dev-mode");
    },
  };
}

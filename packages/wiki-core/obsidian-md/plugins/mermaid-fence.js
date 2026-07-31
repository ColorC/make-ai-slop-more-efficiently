/**
 * mermaid-fence: re-emit ```mermaid fenced blocks as <pre class="mermaid"> for
 * client-side mermaid.js to pick up via mermaid.run({ querySelector: '.mermaid' }).
 */
export function mermaidFence(md) {
  const defaultFence = md.renderer.rules.fence?.bind(md.renderer);

  md.renderer.rules.fence = function (tokens, idx, opts, env, slf) {
    const token = tokens[idx];
    const info = (token.info || '').trim();

    if (info === 'mermaid') {
      return `<pre class="mermaid">${escapeHtml(token.content)}</pre>\n`;
    }

    if (defaultFence) {
      return defaultFence(tokens, idx, opts, env, slf);
    }
    return slf.renderToken(tokens, idx, opts);
  };
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

import MarkdownIt from 'markdown-it';
import markdownItFootnote from 'markdown-it-footnote';
import markdownItAnchor from 'markdown-it-anchor';
import markdownItTaskLists from 'markdown-it-task-lists';
import markdownItDeflist from 'markdown-it-deflist';
import markdownItKatex from '@vscode/markdown-it-katex';
import { obsidianSyntax } from './plugins/obsidian-syntax.js';
import { mermaidFence } from './plugins/mermaid-fence.js';

/**
 * Create a markdown-it instance configured for obsidian-flavored markdown.
 *
 * options:
 *   resolveLink(name, anchor, alias) -> { href, text, broken }
 *   resolveAsset(path) -> { href }     // for image/video/audio/pdf embeds
 *   resolveEmbed(name) -> { href, targetSlug, broken }  // for note transclusion ![[...]]
 *   resolveTag(tag) -> { href }
 *   anchorPermalink: bool (default false)
 */
export function createRenderer(options = {}) {
  const md = new MarkdownIt({
    html: true,
    // linkify off: it auto-treats `CLAUDE.md` `foo.org` etc. as URLs, false-positives are common
    // in tech wikis. Authors who want a real link can write `[text](url)` or `<https://...>`.
    linkify: false,
    breaks: false,
    typographer: false,
  });

  md.use(markdownItFootnote);
  md.use(markdownItAnchor, {
    permalink: options.anchorPermalink
      ? markdownItAnchor.permalink.headerLink()
      : false,
    slugify: (s) => slugify(s),
  });
  md.use(markdownItTaskLists, { enabled: true });
  md.use(markdownItDeflist);

  // KaTeX: $...$ and $$...$$
  // The package exports default in CJS interop.
  const katexPlugin = markdownItKatex.default ?? markdownItKatex;
  md.use(katexPlugin, { throwOnError: false });

  md.use(obsidianSyntax, options);
  md.use(mermaidFence);

  // External links open in new tab so SPA state isn't blown away
  const defaultLinkOpen =
    md.renderer.rules.link_open ??
    function (tokens, idx, opts, _env, slf) {
      return slf.renderToken(tokens, idx, opts);
    };
  md.renderer.rules.link_open = function (tokens, idx, opts, env, slf) {
    const token = tokens[idx];
    const href = token.attrGet('href') ?? '';
    if (/^(https?:|mailto:|ftp:)/i.test(href)) {
      token.attrSet('target', '_blank');
      token.attrSet('rel', 'noopener noreferrer');
    }
    return defaultLinkOpen(tokens, idx, opts, env, slf);
  };

  return md;
}

/**
 * Slugify heading text to anchor id. Keep CJK characters since they don't break URLs in modern browsers.
 */
function slugify(s) {
  return String(s)
    .trim()
    .toLowerCase()
    .replace(/[\s]+/g, '-')
    .replace(/[​-‍﻿]/g, '');
}

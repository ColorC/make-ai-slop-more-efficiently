/**
 * index-builder: walk a vault directory, extract metadata from every .md file,
 * and assemble a JSON index that the browser can consume to power
 * navigation / backlinks / tags / search / graph.
 *
 * Public API:
 *   buildIndex({ roots, exclude }) -> { vaults: [vaultIndex, ...] }
 *
 *   roots:    [{ name, path }]    — list of vaults to scan
 *   exclude:  [glob-like patterns] — directory or file names to skip (e.g. '_audit', '.obsidian')
 *
 * Per-vault output:
 *   {
 *     name, path,
 *     files: [{ path, slug, title, frontmatter, headings, outgoing, tags, mtime, size }],
 *     assets: [{ path, basename }],
 *     byBasename: { basename: [path] },
 *     tags: { tag: [path] },
 *     backlinks: { path: [path] },
 *     stats: { mdCount, assetCount, totalLinks, brokenLinks }
 *   }
 *
 * Runs in Node (uses fs/promises). Not for browser.
 */
import { promises as fs } from 'node:fs';
import { spawn } from 'node:child_process';
import path from 'node:path';
import matter from 'gray-matter';
import fg from 'fast-glob';

const MD_EXTS = new Set(['.md']);
const ASSET_EXTS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.ico',
  '.mp4', '.webm', '.ogv', '.mov', '.mkv',
  '.mp3', '.wav', '.m4a', '.ogg', '.flac', '.3gp',
  '.pdf',
]);

const DEFAULT_EXCLUDE = new Set([
  '.git', '.obsidian', '.vscode', '.idea',
  'node_modules', '__pycache__',
  '_audit',
  '.trash',  // wiki-viewer soft-delete bucket
]);

const WIKILINK_RE = /(!?)\[\[([^\[\]\|\#\\\n]+)?(#+[^\[\]\|\#\\\n]+)?(\\?\|[^\[\]\#\n]*)?\]\]/g;
const TAG_RE = /(?<=^|[\s(])#([\p{L}\p{N}_][\p{L}\p{N}_\-/]*)/gu;
const HEADING_RE = /^(#{1,6})\s+(.+?)\s*$/gm;
const BLOCK_REF_RE = /(?<=\S)[ \t]+\^([a-zA-Z0-9_-]+)[ \t]*$/gm;
const FENCE_RE = /^([ \t]*)(```|~~~)[^\n]*\n[\s\S]*?\n\1\2[ \t]*$/gm;

// Module-level parseMd cache: skip readFile+regex when (path, mtime, size) unchanged.
// 不限大小 (典型 vault < 5000 文件, 每条 meta 几 KB, 内存可控). 文件删了 cache 留着旧条目无害.
const parsedCache = new Map();

// Concurrency limit for parallel parseMd. 32 是 Node 默认 IO 并发的甜点 — 太大会触发 EMFILE.
const PARSE_CONCURRENCY = 32;
async function mapWithConcurrency(items, mapper, limit = PARSE_CONCURRENCY) {
  const results = new Array(items.length);
  let i = 0;
  async function worker() {
    while (true) {
      const idx = i++;
      if (idx >= items.length) return;
      results[idx] = await mapper(items[idx], idx);
    }
  }
  const workers = Array.from({ length: Math.min(limit, items.length) }, () => worker());
  await Promise.all(workers);
  return results;
}

export async function buildIndex({ roots, exclude }) {
  const globalExcludes = [...DEFAULT_EXCLUDE, ...(exclude ?? [])];

  // Vault 之间互不依赖, 走 Promise.all 并行扫盘. 大头是 fast-glob/walk + fs.stat,
  // 多个 vault 并行时磁盘 IO 仍能 overlap (SSD 上 ~2x 加速).
  const vaults = await Promise.all(roots.map((root) => {
    const perVaultExcludes = new Set([...globalExcludes, ...(root.exclude ?? [])]);
    // root carries: name, path, exclude, glob (optional, for huge-tree vaults that only want
    // a subset matching a pattern, e.g. all SKILL.md across the P4 workspace)
    return buildVault(root, perVaultExcludes);
  }));

  return { vaults, builtAt: new Date().toISOString() };
}

async function buildVault({ name, path: rootPath, glob }, excludeSet) {
  const stat = await fs.stat(rootPath).catch(() => null);
  if (!stat || !stat.isDirectory()) {
    return emptyVault(name, rootPath, `path not found or not a directory: ${rootPath}`);
  }

  const allEntries = [];
  if (glob) {
    // Special: glob mode uses ripgrep to list files matching pattern across a huge tree (e.g. all SKILL.md
    // under D:/P4/main). Way faster than walking + filtering — rg's default 0.5s for 118 hits across
    // the whole P4 workspace. Falls back to recursive walk if rg isn't on PATH.
    const matched = await findFilesByGlob(rootPath, glob, [...excludeSet]);
    for (const full of matched) {
      const rel = full
        .replace(/\\/g, '/')
        .replace(rootPath.replace(/\\/g, '/').replace(/\/$/, '') + '/', '');
      const st = await fs.stat(full).catch(() => null);
      allEntries.push({
        relPath: rel,
        name: rel.split('/').pop(),
        ext: '.' + (rel.split('.').pop() || '').toLowerCase(),
        mtime: st ? st.mtimeMs : 0,
        size: st ? st.size : 0,
      });
    }
  } else {
    await walk(rootPath, '', excludeSet, allEntries);
  }

  const files = [];
  const assets = [];

  // 把 .md 跟 asset 分流, .md 走并发 parse (64 并发 IO + mtime cache 命中直接 reuse).
  const mdEntries = [];
  for (const entry of allEntries) {
    if (MD_EXTS.has(entry.ext)) mdEntries.push(entry);
    else if (ASSET_EXTS.has(entry.ext)) assets.push({ path: entry.relPath, basename: entry.name });
  }
  const mdMetas = await mapWithConcurrency(mdEntries, (entry) => parseMd(entry, rootPath));
  for (const meta of mdMetas) {
    if (meta) files.push(meta);
  }

  files.sort((a, b) => a.path.localeCompare(b.path));
  assets.sort((a, b) => a.path.localeCompare(b.path));

  // Indexes
  const byBasename = {};
  const register = (key, val) => {
    if (!byBasename[key]) byBasename[key] = [];
    if (!byBasename[key].includes(val)) byBasename[key].push(val);
  };
  for (const f of files) {
    register(f.slug, f.path);
    // Also register frontmatter aliases — obsidian uses these for [[X]] resolution
    const aliases = f.frontmatter?.aliases;
    if (Array.isArray(aliases)) {
      for (const alias of aliases) {
        const key = String(alias).trim();
        if (key) register(key, f.path);
      }
    } else if (typeof aliases === 'string' && aliases.trim()) {
      register(aliases.trim(), f.path);
    }
  }
  for (const a of assets) {
    register(a.basename, a.path);
  }

  // Tags
  const tags = {};
  for (const f of files) {
    for (const t of f.tags) {
      if (!tags[t]) tags[t] = [];
      if (!tags[t].includes(f.path)) tags[t].push(f.path);
    }
  }

  // Backlinks: invert outgoing edges (resolved against byBasename)
  const backlinks = {};
  let totalLinks = 0;
  let brokenLinks = 0;

  for (const f of files) {
    for (const out of f.outgoing) {
      totalLinks++;
      const target = resolveLinkTarget(out.name, f.path, byBasename);
      if (!target) {
        brokenLinks++;
        continue;
      }
      if (!backlinks[target]) backlinks[target] = [];
      if (!backlinks[target].includes(f.path)) backlinks[target].push(f.path);
    }
  }

  return {
    name,
    path: rootPath,
    files,
    assets,
    byBasename,
    tags,
    backlinks,
    stats: {
      mdCount: files.length,
      assetCount: assets.length,
      totalLinks,
      brokenLinks,
    },
  };
}

/**
 * List files matching a glob pattern under root. Uses fast-glob (cross-platform, no external
 * deps, ~1-2s for 100k-file trees). The exclude list is treated as a list of dir BASENAMES
 * to skip — fast-glob's `ignore` patterns do the work.
 */
async function findFilesByGlob(rootPath, glob, excludes) {
  // 把目录 basename 列表转 fast-glob 的 ignore 模式: '**/<name>/**'
  // 这样匹配任意层下的同名目录, 但不误伤 .claude/, .agents/ 这类我们要保留的.
  const ignore = excludes.map((e) => `**/${e}/**`);
  try {
    const found = await fg(glob, {
      cwd: rootPath.replace(/\\/g, '/'),
      absolute: true,
      onlyFiles: true,
      dot: true,           // 看 .claude / .agents 下面
      followSymbolicLinks: false,
      suppressErrors: true, // 跳过 EACCES / EPERM 不打断
      ignore,
    });
    return found.map((p) => p.replace(/\\/g, '/'));
  } catch (e) {
    console.warn('[obsidian-md] fast-glob failed, falling back to walk:', e.message);
    return walkFallback(rootPath, glob, excludes);
  }
}

async function walkFallback(rootPath, glob, excludes) {
  // Slow path. Tail-match only (e.g. '**/SKILL.md'). Mostly here as last resort.
  const tail = glob.replace(/^\*\*\//, '');
  const out = [];
  const excludeSet = new Set(excludes);
  async function rec(rel) {
    const dir = rel ? path.join(rootPath, rel) : rootPath;
    let entries;
    try { entries = await fs.readdir(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (excludeSet.has(e.name)) continue;
      const childRel = rel ? rel + '/' + e.name : e.name;
      if (e.isDirectory()) await rec(childRel);
      else if (e.isFile() && e.name === tail) out.push(path.join(rootPath, childRel));
    }
  }
  await rec('');
  return out;
}

async function walk(rootPath, relDir, excludeSet, out) {
  const dir = path.join(rootPath, relDir);
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (excludeSet.has(entry.name)) continue;
    const relPath = relDir ? `${relDir}/${entry.name}` : entry.name;
    const full = path.join(rootPath, relPath);

    if (entry.isDirectory()) {
      await walk(rootPath, relPath, excludeSet, out);
    } else if (entry.isFile()) {
      const ext = path.extname(entry.name).toLowerCase();
      const stat = await fs.stat(full).catch(() => null);
      out.push({
        relPath: relPath.replace(/\\/g, '/'),
        name: entry.name,
        ext,
        mtime: stat ? stat.mtimeMs : 0,
        size: stat ? stat.size : 0,
      });
    }
  }
}

async function parseMd(entry, rootPath) {
  const full = path.join(rootPath, entry.relPath);
  // mtime+size 缓存: 同一文件 stat 跟上次相同 → 跳过 readFile / matter / regex.
  // 缓存只存"路径无关"的解析结果 (frontmatter / headings / outgoing / tags / blockRefs),
  // path/slug/mtime/size 用当前 entry 现拼, 这样同一文件被两个 vault 以不同 rel path 看见时都对.
  const cacheKey = full;
  const cached = parsedCache.get(cacheKey);
  if (cached && cached.mtime === entry.mtime && cached.size === entry.size) {
    const slug = entry.name.replace(/\.md$/i, '');
    const title = cached.parsed.frontmatter.title || (cached.parsed.headings[0]?.text) || slug;
    return {
      path: entry.relPath,
      slug,
      title,
      ...cached.parsed,
      mtime: entry.mtime,
      size: entry.size,
    };
  }
  let raw;
  try {
    raw = await fs.readFile(full, 'utf8');
  } catch {
    return null;
  }

  let parsed;
  try {
    parsed = matter(raw);
  } catch {
    parsed = { data: {}, content: raw };
  }

  const frontmatter = parsed.data ?? {};
  const content = parsed.content ?? '';

  // Strip code fences before extracting links/tags/headings to avoid false positives.
  const stripped = content.replace(FENCE_RE, '');

  const headings = [];
  for (const m of stripped.matchAll(HEADING_RE)) {
    headings.push({
      level: m[1].length,
      text: m[2].trim(),
      anchor: slugifyAnchor(m[2]),
    });
  }

  const outgoing = [];
  for (const m of stripped.matchAll(WIKILINK_RE)) {
    const [, bang, rawFp, rawHeader, rawAlias] = m;
    if (!rawFp && !rawHeader) continue;
    outgoing.push({
      name: (rawFp ?? '').trim(),
      anchor: rawHeader ? rawHeader.replace(/^#+/, '').replace(/^\^/, 'block-') : '',
      alias: rawAlias ? rawAlias.replace(/^\\?\|/, '').trim() : null,
      embed: bang === '!',
    });
  }

  const tagSet = new Set();
  for (const m of stripped.matchAll(TAG_RE)) {
    if (/^[\d/]+$/.test(m[1])) continue;
    // CSS hex color (#abc / #abcd / #aabbcc / #aabbccdd) — drop, not a tag
    if ([3, 4, 6, 8].includes(m[1].length) && /^[0-9a-fA-F]+$/.test(m[1])) continue;
    tagSet.add(m[1]);
  }
  if (Array.isArray(frontmatter.tags)) {
    for (const t of frontmatter.tags) tagSet.add(String(t));
  } else if (typeof frontmatter.tags === 'string') {
    for (const t of frontmatter.tags.split(/[,\s]+/)) {
      if (t) tagSet.add(t);
    }
  }

  const blockRefs = [];
  for (const m of stripped.matchAll(BLOCK_REF_RE)) {
    blockRefs.push(m[1]);
  }

  const slug = entry.name.replace(/\.md$/i, '');
  const title = frontmatter.title || (headings[0]?.text) || slug;

  // 路径无关部分进缓存; path/slug/title/mtime/size 是 entry 上下文派生出来的.
  const parseOutput = { frontmatter, headings, outgoing, tags: [...tagSet], blockRefs };
  parsedCache.set(cacheKey, { mtime: entry.mtime, size: entry.size, parsed: parseOutput });

  return {
    path: entry.relPath,
    slug,
    title,
    ...parseOutput,
    mtime: entry.mtime,
    size: entry.size,
  };
}

function resolveLinkTarget(name, sourcePath, byBasename) {
  if (!name) return null;

  // 1. Direct basename / alias match
  let candidates = byBasename[name] ?? [];

  // 2-3. <name> · <X> fallback — tolerate optional spaces around · (different files use different conventions)
  if (candidates.length === 0) {
    const prefixRe = new RegExp('^' + escapeRegex(name) + '\\s*·\\s*');
    const prefixed = Object.keys(byBasename).filter((k) => prefixRe.test(k));
    if (prefixed.length) {
      // Prefer 总览 / overview / 主页 endings
      const overview = prefixed.find((k) => /·\s*总览$/.test(k))
        ?? prefixed.find((k) => /·\s*overview$/i.test(k))
        ?? prefixed[0];
      candidates = byBasename[overview] ?? [];
    }
  }

  // 4. Path-style link [[folder/file]]
  if (candidates.length === 0 && name.includes('/')) {
    const norm = name.replace(/\\/g, '/');
    const withMd = norm.endsWith('.md') ? norm : norm + '.md';
    const baseName = withMd.split('/').pop().replace(/\.md$/, '');
    const all = byBasename[baseName] ?? [];
    const exact = all.find((p) => p === withMd);
    if (exact) return exact;
  }

  if (candidates.length === 0) return null;
  if (candidates.length === 1) return candidates[0];

  // Tiebreak: prefer same directory
  const sourceDir = path.dirname(sourcePath);
  const sameDir = candidates.find((c) => path.dirname(c) === sourceDir);
  return sameDir ?? candidates[0];
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function slugifyAnchor(s) {
  return String(s)
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^\p{L}\p{N}\-_]/gu, '');
}

function emptyVault(name, rootPath, error) {
  return {
    name,
    path: rootPath,
    files: [],
    assets: [],
    byBasename: {},
    tags: {},
    backlinks: {},
    stats: { mdCount: 0, assetCount: 0, totalLinks: 0, brokenLinks: 0 },
    error,
  };
}

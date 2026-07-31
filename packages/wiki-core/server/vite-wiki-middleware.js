// Vite 插件：给宿主 dev server 挂 wiki 文件 API（模式照抄 AIWorkSpace wiki-viewer 的 middleware）。
//   GET /api/wiki/index          → { pages: [{ path, title, tags }] }
//   GET /api/wiki/file?path=x.md → { path, content }
//   PUT /api/wiki/file?path=x.md → 写回（body 为新全文）
// 路径经规范化校验，禁止越出 vault 根。URL 用包含匹配（兼容 --base /walker-game/ 前缀）。
import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { resolve, join, relative, sep } from "node:path";

function listMarkdown(root) {
  const out = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name.startsWith(".") || name === "node_modules") continue;
        walk(full);
      } else if (name.endsWith(".md")) {
        out.push(relative(root, full).split(sep).join("/"));
      }
    }
  };
  walk(root);
  return out;
}

function firstHeading(content) {
  const m = content.match(/^#\s+(.+)$/m);
  return m ? m[1].trim() : null;
}

function fmTags(content) {
  const m = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return [];
  return [...m[1].matchAll(/^\s*-\s*(.+)$/gm)].map((x) => x[1].trim());
}

function safeResolve(root, rel) {
  const full = resolve(root, rel);
  if (!full.startsWith(resolve(root) + sep) && full !== resolve(root)) return null;
  return full;
}

function readBody(req) {
  return new Promise((res, rej) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => res(Buffer.concat(chunks).toString("utf8")));
    req.on("error", rej);
  });
}

export function wikiMiddleware({ root, readOnly = false } = {}) {
  const vaultRoot = resolve(root);
  return {
    name: "wiki-core-middleware",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = req.url || "";
        const marker = url.indexOf("/api/wiki/");
        if (marker === -1) return next();
        const sub = url.slice(marker + "/api/wiki/".length);
        const [route, query] = sub.split("?");
        const params = new URLSearchParams(query || "");
        const json = (code, obj) => {
          res.statusCode = code;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify(obj));
        };
        try {
          if (route === "index" && req.method === "GET") {
            const pages = listMarkdown(vaultRoot).map((p) => {
              const content = readFileSync(join(vaultRoot, p), "utf8");
              return { path: p, title: firstHeading(content) || p.replace(/\.md$/, ""), tags: fmTags(content) };
            });
            return json(200, { pages });
          }
          if (route === "file") {
            const rel = params.get("path") || "";
            const full = safeResolve(vaultRoot, rel);
            if (!full || !rel.endsWith(".md")) return json(400, { error: "bad path" });
            if (req.method === "GET") {
              if (!existsSync(full)) return json(404, { error: "not found" });
              return json(200, { path: rel, content: readFileSync(full, "utf8") });
            }
            if (req.method === "PUT") {
              if (readOnly) return json(403, { error: "read-only" });
              const body = await readBody(req);
              writeFileSync(full, body);
              return json(200, { ok: true, path: rel });
            }
          }
          return json(404, { error: "unknown wiki route" });
        } catch (error) {
          return json(500, { error: String(error && error.message) });
        }
      });
    },
  };
}

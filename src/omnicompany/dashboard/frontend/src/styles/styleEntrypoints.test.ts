// @ts-expect-error Vitest executes this source-level regression under Node;
// the browser bundle intentionally does not include Node type declarations.
import { existsSync, readFileSync, readdirSync } from 'node:fs'
// @ts-expect-error This import is test-only and never reaches the browser.
import { dirname, extname, join, relative, resolve } from 'node:path'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

declare const process: { cwd(): string }

const SOURCE_ROOT = resolve(process.cwd(), 'src')
const SOURCE_EXTENSIONS = ['.ts', '.tsx', '.js', '.jsx'] as const
const CSS_CLASS = /\.([A-Za-z_][A-Za-z0-9_-]*)/g
const CLASS_TOKEN = /[A-Za-z_][A-Za-z0-9_-]*/g

function resolveLocalImport(importer: string, specifier: string): string | null {
  if (!specifier.startsWith('.')) return null
  const base = resolve(dirname(importer), specifier)
  if (extname(base)) return existsSync(base) ? base : null
  for (const extension of SOURCE_EXTENSIONS) {
    if (existsSync(base + extension)) return base + extension
  }
  for (const extension of SOURCE_EXTENSIONS) {
    const indexFile = join(base, `index${extension}`)
    if (existsSync(indexFile)) return indexFile
  }
  return null
}

function scriptKind(file: string): ts.ScriptKind {
  if (file.endsWith('.tsx')) return ts.ScriptKind.TSX
  if (file.endsWith('.jsx')) return ts.ScriptKind.JSX
  if (file.endsWith('.js')) return ts.ScriptKind.JS
  return ts.ScriptKind.TS
}

function parse(file: string): ts.SourceFile {
  return ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    scriptKind(file),
  )
}

function runtimeGraph(entry: string): { sourceFiles: Set<string>; styleFiles: Set<string> } {
  const sourceFiles = new Set<string>()
  const styleFiles = new Set<string>()
  const pending = [entry]

  while (pending.length > 0) {
    const file = pending.pop()
    if (!file || sourceFiles.has(file)) continue
    sourceFiles.add(file)

    const visit = (node: ts.Node): void => {
      let specifier: string | null = null
      if (
        (ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier
        && ts.isStringLiteral(node.moduleSpecifier)
      ) {
        specifier = node.moduleSpecifier.text
      } else if (
        ts.isCallExpression(node)
        && node.expression.kind === ts.SyntaxKind.ImportKeyword
        && node.arguments.length === 1
        && ts.isStringLiteral(node.arguments[0])
      ) {
        specifier = node.arguments[0].text
      }

      if (specifier) {
        const imported = resolveLocalImport(file, specifier)
        if (imported?.endsWith('.css')) styleFiles.add(imported)
        else if (imported) pending.push(imported)
      }
      ts.forEachChild(node, visit)
    }
    visit(parse(file))
  }

  return { sourceFiles, styleFiles }
}

function jsxClassTokens(files: Iterable<string>): Set<string> {
  const tokens = new Set<string>()
  for (const file of files) {
    if (!file.endsWith('.tsx') && !file.endsWith('.jsx')) continue
    const collectStrings = (node: ts.Node): void => {
      if (
        ts.isStringLiteral(node)
        || ts.isNoSubstitutionTemplateLiteral(node)
        || ts.isTemplateHead(node)
        || ts.isTemplateMiddle(node)
        || ts.isTemplateTail(node)
      ) {
        for (const match of node.text.matchAll(CLASS_TOKEN)) tokens.add(match[0])
      }
      ts.forEachChild(node, collectStrings)
    }
    const visit = (node: ts.Node): void => {
      if (
        ts.isJsxAttribute(node)
        && ts.isIdentifier(node.name)
        && node.name.text === 'className'
        && node.initializer
      ) {
        collectStrings(node.initializer)
      } else {
        ts.forEachChild(node, visit)
      }
    }
    visit(parse(file))
  }
  return tokens
}

function cssClasses(file: string): Set<string> {
  return new Set([...readFileSync(file, 'utf8').matchAll(CSS_CLASS)].map((match) => match[1]))
}

function localStyles(root: string): string[] {
  const files: string[] = []
  const visit = (directory: string): void => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const file = join(directory, entry.name)
      if (entry.isDirectory()) visit(file)
      else if (file.endsWith('.css')) files.push(file)
    }
  }
  visit(root)
  return files
}

describe('Dashboard stylesheet entrypoints', () => {
  it('keeps the recovered deterministic production bundler and Vilo OS proxy', () => {
    const config = readFileSync(resolve(process.cwd(), 'vite.config.ts'), 'utf8')
    expect(config).toContain("minify: 'terser'")
    expect(config).toContain("'/vilo-os':")
    expect(config).toContain('manualChunks(id: string)')
    expect(config).toContain('chunkFileNames: stableChunkFileName')
    expect(config).toContain("'/lofa/file-bridge':")
  })

  it('loads every local stylesheet that uniquely styles a reachable JSX class', () => {
    const graph = runtimeGraph(join(SOURCE_ROOT, 'main.tsx'))
    const usedClasses = jsxClassTokens(graph.sourceFiles)
    const loadedClasses = new Set(
      [...graph.styleFiles].flatMap((file) => [...cssClasses(file)]),
    )

    const disconnected = localStyles(SOURCE_ROOT)
      .filter((file) => !graph.styleFiles.has(file))
      .map((file) => ({
        file: relative(SOURCE_ROOT, file).replaceAll('\\', '/'),
        classes: [...cssClasses(file)]
          // Hyphenated selectors avoid treating generic legacy helpers such as `.row`
          // as proof that an otherwise dormant stylesheet belongs in the runtime.
          .filter((name) => name.includes('-') && usedClasses.has(name) && !loadedClasses.has(name))
          .sort(),
      }))
      .filter(({ classes }) => classes.length > 0)

    expect(disconnected).toEqual([])
  })

  it('keeps the current controller card styles on the runtime import graph', () => {
    const graph = runtimeGraph(join(SOURCE_ROOT, 'main.tsx'))
    const controllerStyle = join(SOURCE_ROOT, 'entities', 'controller', 'controller.css')
    expect(graph.styleFiles.has(controllerStyle)).toBe(true)
    expect(jsxClassTokens(graph.sourceFiles)).toContain('ct-page')
    expect(cssClasses(controllerStyle)).toContain('ct-page')
  })
})

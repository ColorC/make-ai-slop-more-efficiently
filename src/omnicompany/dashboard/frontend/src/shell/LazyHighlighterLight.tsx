// 被 shell/MarkdownRenderer.tsx 用 React.lazy 动态引入。PrismLight + 按需注册的语言集 ——
// 与 chat 的完整 Prism 同属 react-syntax-highlighter, 两处都改成动态引入后, 整个 syntax-highlighter
// chunk 才能真正变成「用到代码块才下载」, 不再随首屏 bundle 一起加载。
import React from 'react'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx'
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css'
import lua from 'react-syntax-highlighter/dist/esm/languages/prism/lua'
import diff from 'react-syntax-highlighter/dist/esm/languages/prism/diff'

SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('typescript', typescript)
SyntaxHighlighter.registerLanguage('ts', typescript)
SyntaxHighlighter.registerLanguage('javascript', javascript)
SyntaxHighlighter.registerLanguage('js', javascript)
SyntaxHighlighter.registerLanguage('tsx', tsx)
SyntaxHighlighter.registerLanguage('jsx', jsx)
SyntaxHighlighter.registerLanguage('json', json)
SyntaxHighlighter.registerLanguage('yaml', yaml)
SyntaxHighlighter.registerLanguage('yml', yaml)
SyntaxHighlighter.registerLanguage('bash', bash)
SyntaxHighlighter.registerLanguage('sh', bash)
SyntaxHighlighter.registerLanguage('shell', bash)
SyntaxHighlighter.registerLanguage('markdown', markdown)
SyntaxHighlighter.registerLanguage('md', markdown)
SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('css', css)
SyntaxHighlighter.registerLanguage('lua', lua)
SyntaxHighlighter.registerLanguage('diff', diff)

const LazyHighlighterLight: React.FC<{ language: string; value: string }> = ({ language, value }) => (
  <SyntaxHighlighter
    style={vscDarkPlus as any}
    language={language}
    PreTag="div"
    customStyle={{ margin: '8px 0', borderRadius: 6, fontSize: 14, background: 'var(--fp-bg)' }}
  >
    {value}
  </SyntaxHighlighter>
)

export default LazyHighlighterLight

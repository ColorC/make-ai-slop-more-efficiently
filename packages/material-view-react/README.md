# @omni/material-view-react

A React reader for Material Core View graphs. The host application owns the theme, shell, routing, and representation openers; this package does not embed a product theme or domain profile.

```jsx
import { MaterialWorkspace } from '@omni/material-view-react'
import '@omni/material-view-react/styles.css'

export function Reader({ graph }) {
  return <MaterialWorkspace graph={graph} theme="my-product" title="My materials" />
}
```

The component accepts either a validated `graph` or a product-neutral
`MaterialProvider`. A provider can load the graph and representation bodies
without the component knowing a product URL.

Peer requirements are React 18+, Material Core 0.2+ and Material Client 0.1+.
The host may pass
`renderMarkdown(source, material, representation)` to reuse its Markdown
renderer; without it the reader shows Markdown source losslessly as
preformatted text. Domain displays use `renderers`: the registry matches exact
`renderer_hint`, exact/wildcard media type, labels and an optional predicate in
that order. Unknown representations keep a traceable generic fallback. The
package does not depend on a private Wiki runtime.

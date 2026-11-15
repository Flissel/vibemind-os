// Rows of the Base table from the file tree (moved out of components/bases-view for fast refresh).
// VibeMind: HTML concepts under knowledge/ are rows too (name without extension, no front matter).
import { konzept } from '@x/shared'

type FileTreeNode = {
  path: string
  name: string
  kind: 'file' | 'dir'
  children?: FileTreeNode[]
  stat?: { mtimeMs: number }
}

export function collectFiles(nodes: FileTreeNode[]): { path: string; name: string; mtimeMs: number }[] {
  return nodes.flatMap((n) =>
    n.kind === 'file' && (n.name.endsWith('.md') || konzept.isKnowledgeHtmlPath(n.path))
      ? [{ path: n.path, name: n.name.replace(/\.(md|html?|xhtml)$/i, ''), mtimeMs: n.stat?.mtimeMs ?? 0 }]
      : n.children
        ? collectFiles(n.children)
        : [],
  )
}

/** Rows whose front matter is read: notes only, never HTML concepts. */
export function frontmatterPaths(paths: string[]): string[] {
  return paths.filter((p) => !konzept.isHtmlPath(p))
}

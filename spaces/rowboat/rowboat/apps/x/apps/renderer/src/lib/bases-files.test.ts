import { describe, expect, it } from 'vitest'
import { collectFiles, frontmatterPaths } from './bases-files'

describe('BasesView: Konzepte als Zeilen', () => {
  const baum = [
    {
      path: 'knowledge/Konzepte',
      name: 'Konzepte',
      kind: 'dir' as const,
      children: [
        { path: 'knowledge/Konzepte/Sommer.html', name: 'Sommer.html', kind: 'file' as const, stat: { size: 1, mtimeMs: 5 } },
        { path: 'knowledge/Konzepte/bild.png', name: 'bild.png', kind: 'file' as const },
      ],
    },
    { path: 'knowledge/Notiz.md', name: 'Notiz.md', kind: 'file' as const },
    { path: 'bases/x.html', name: 'x.html', kind: 'file' as const },
  ]

  it('nimmt HTML-Konzepte unter knowledge/ mit Namen ohne Endung auf', () => {
    expect(collectFiles(baum)).toEqual([
      { path: 'knowledge/Konzepte/Sommer.html', name: 'Sommer', mtimeMs: 5 },
      { path: 'knowledge/Notiz.md', name: 'Notiz', mtimeMs: 0 },
    ])
  })

  it('liest Frontmatter nur fuer Notizen', () => {
    expect(frontmatterPaths(['knowledge/Konzepte/Sommer.html', 'knowledge/Notiz.md'])).toEqual(['knowledge/Notiz.md'])
  })
})

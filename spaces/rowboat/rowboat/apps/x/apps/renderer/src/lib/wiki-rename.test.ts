import { describe, expect, it } from 'vitest'
import { renamedFileName, rewriteOpenDocumentForRename, rewriteWikiLinksForRenamedFileInMarkdown } from './wiki-rename'

describe('rewriteWikiLinksForRenamedFileInMarkdown', () => {
  it('Notiz: Verweise auf sich selbst ziehen mit, wie bisher', () => {
    expect(
      rewriteWikiLinksForRenamedFileInMarkdown('[[Alt]] [[Ordner/Alt.md|x]] [[Anderes]]', 'knowledge/Ordner/Alt.md', 'knowledge/Ordner/Neu.md'),
    ).toBe('[[Neu]] [[Ordner/Neu.md|x]] [[Anderes]]')
  })
  it('Konzept umbenannt: Verweise mit Endung ziehen mit, Alias bleibt', () => {
    expect(
      rewriteWikiLinksForRenamedFileInMarkdown('[[Konzepte/k.html|Konzept]] [[Konzepte/K.HTML]]', 'knowledge/Konzepte/k.html', 'knowledge/Konzepte/n.html'),
    ).toBe('[[Konzepte/n.html|Konzept]] [[Konzepte/n.html]]')
  })
  it('Verweis auf eine Notiz bleibt von einem Konzept-Umbenennen unberührt', () => {
    expect(rewriteWikiLinksForRenamedFileInMarkdown('[[Konzepte/k]]', 'knowledge/Konzepte/k.html', 'knowledge/Konzepte/n.html')).toBe('[[Konzepte/k]]')
  })
  it('Pfade ausserhalb von knowledge/ oder andere Endungen: unverändert', () => {
    expect(rewriteWikiLinksForRenamedFileInMarkdown('[[a]]', 'x/a.md', 'x/b.md')).toBe('[[a]]')
    expect(rewriteWikiLinksForRenamedFileInMarkdown('[[a]]', 'knowledge/a.md', 'knowledge/b.txt')).toBe('[[a]]')
  })
})

describe('renamedFileName', () => {
  it('Notizen bekommen .md, Ordner nicht', () => {
    expect(renamedFileName('knowledge/a.md', 'b', false)).toBe('b.md')
    expect(renamedFileName('knowledge/a.md', 'b.md', false)).toBe('b.md')
    expect(renamedFileName('knowledge/Ordner', 'b', true)).toBe('b')
  })
  it('HTML-Konzepte behalten ihre Endung', () => {
    expect(renamedFileName('knowledge/K/a.html', 'neu', false)).toBe('neu.html')
    expect(renamedFileName('knowledge/K/a.htm', 'neu', false)).toBe('neu.htm')
    expect(renamedFileName('knowledge/K/a.html', 'neu.html', false)).toBe('neu.html')
  })
})

describe('rewriteOpenDocumentForRename', () => {
  it('Notiz: Zwischenspeicher wird umgeschrieben', () => {
    expect(rewriteOpenDocumentForRename('[[a]]', 'knowledge/a.md', 'knowledge/b.md', false)).toBe('[[b]]')
  })
  it('HTML-Konzept oder Ordner: Zwischenspeicher bleibt unberührt (gehört zur zuletzt geöffneten Notiz)', () => {
    expect(rewriteOpenDocumentForRename('[[Konzepte/k.html]] [[a]]', 'knowledge/Konzepte/k.html', 'knowledge/Konzepte/n.html', false)).toBe(
      '[[Konzepte/k.html]] [[a]]',
    )
    expect(rewriteOpenDocumentForRename('[[a]]', 'knowledge/a', 'knowledge/b', true)).toBe('[[a]]')
  })
})
